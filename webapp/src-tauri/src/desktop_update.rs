use crate::config::DesktopConfig;
use anyhow::{Context, Result};
use semver::Version;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::env;
use std::ffi::OsString;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
use windows::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
#[cfg(windows)]
use windows::Win32::System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE};

const CURRENT_VERSION: &str = env!("CARGO_PKG_VERSION");
const TARGET: &str = "x86_64-pc-windows-msvc";
const MAX_DOWNLOAD_SIZE: u64 = 30 * 1024 * 1024;
const MAX_LOG_SIZE: u64 = 512 * 1024;

// Append-only log for the whole update chain: check, apply, download, the
// detached replace helper and the post-update cleanup all write here, so a
// failed update can be reconstructed afterwards.  Files follow the
// module/logger convention (log/<date>_desktop.txt, local time, levelled
// lines) so the webui logs page picks them up as the "desktop" source.
// Logging must never break the update itself, hence every error is swallowed.
fn update_log(root: &Path, message: impl AsRef<str>) {
    update_log_levelled(root, "INFO", message);
}

fn update_log_levelled(root: &Path, level: &str, message: impl AsRef<str>) {
    let now = LocalTimestamp::now();
    let path = root
        .join("log")
        .join(format!("{}_desktop.txt", now.date));
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    // Keep the file bounded; the chain within a single update matters, not
    // ancient history.
    if fs::metadata(&path).is_ok_and(|meta| meta.len() > MAX_LOG_SIZE) {
        let _ = fs::remove_file(&path);
    }
    let line = format!("{} | {} | {}\n", now.line, level, message.as_ref());
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = file.write_all(line.as_bytes());
    }
}

// Local wall-clock time rendered the way module/logger's file_formatter does,
// so log files stay mergeable with the Python side.
struct LocalTimestamp {
    date: String, // YYYY-MM-DD, used in the file name
    line: String, // YYYY-MM-DD HH:MM:SS.mmm, used at the start of each line
}

#[cfg(windows)]
impl LocalTimestamp {
    fn now() -> Self {
        use windows::Win32::System::SystemInformation::GetLocalTime;
        let time = unsafe { GetLocalTime() };
        let date = format!("{:04}-{:02}-{:02}", time.wYear, time.wMonth, time.wDay);
        let line = format!(
            "{} {:02}:{:02}:{:02}.{:03}",
            date, time.wHour, time.wMinute, time.wSecond, time.wMilliseconds
        );
        Self { date, line }
    }
}

// Fallback for non-Windows builds (the updater itself only runs on Windows):
// UTC from the system clock without pulling in chrono/time.
#[cfg(not(windows))]
impl LocalTimestamp {
    fn now() -> Self {
        let secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_secs())
            .unwrap_or(0);
        let days = (secs / 86_400) as i64;
        let secs_of_day = secs % 86_400;
        let (year, month, day) = civil_from_days(days);
        let millis = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.subsec_millis())
            .unwrap_or(0);
        let date = format!("{year:04}-{month:02}-{day:02}");
        let line = format!(
            "{} {:02}:{:02}:{:02}.{:03}",
            date,
            secs_of_day / 3600,
            secs_of_day % 3600 / 60,
            secs_of_day % 60,
            millis
        );
        Self { date, line }
    }
}

// Howard Hinnant's civil-from-days algorithm.
#[cfg(not(windows))]
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

#[derive(Debug, Deserialize)]
struct DesktopManifest {
    schema: u32,
    #[serde(rename = "project_version")]
    _project_version: String,
    desktop_version: String,
    target: String,
    url: String,
    sha256: String,
    size: u64,
}

pub enum InternalMode {
    Normal { cleanup_helper: Option<PathBuf> },
    Replace(ReplaceArgs),
}

pub struct ReplaceArgs {
    wait_pid: u32,
    target: PathBuf,
    root: PathBuf,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopUpdateStatus {
    pub current_version: String,
    pub remote_version: Option<String>,
    pub checked: bool,
    pub checking: bool,
    pub update_available: bool,
    pub applying: bool,
    pub error: Option<String>,
}

pub struct DesktopUpdateManager {
    config: DesktopConfig,
    status: Mutex<DesktopUpdateStatus>,
}

impl DesktopUpdateManager {
    pub fn new(config: DesktopConfig) -> Self {
        Self {
            config,
            status: Mutex::new(DesktopUpdateStatus {
                current_version: CURRENT_VERSION.to_string(),
                remote_version: None,
                checked: false,
                checking: false,
                update_available: false,
                applying: false,
                error: None,
            }),
        }
    }

    pub fn status(&self) -> DesktopUpdateStatus {
        self.lock_status().clone()
    }

    // Runs a single manifest check on a background thread; repeated calls
    // while a check is running are ignored.
    pub fn start_check(self: &Arc<Self>) {
        if self.config.desktop_update_manifest.trim().is_empty() {
            return;
        }
        {
            let mut status = self.lock_status();
            if status.checking {
                return;
            }
            status.checking = true;
        }
        let manager = Arc::clone(self);
        thread::spawn(move || manager.check());
    }

    fn check(&self) {
        update_log(
            &self.config.root,
            format!(
                "checking for desktop updates: {}",
                self.config.desktop_update_manifest
            ),
        );
        let result = fetch_manifest(&self.config.desktop_update_manifest)
            .and_then(|manifest| validate_manifest(&manifest));
        let current = Version::parse(CURRENT_VERSION);
        let mut status = self.lock_status();
        status.checking = false;
        status.checked = true;
        match (result, current) {
            (Ok(remote), Ok(current)) => {
                let available = is_newer_version(&remote, &current);
                update_log(
                    &self.config.root,
                    format!("check finished: current {current}, remote {remote}, update available: {available}"),
                );
                status.remote_version = Some(remote.to_string());
                status.update_available = available;
                status.error = None;
            }
            (Ok(remote), Err(error)) => {
                update_log_levelled(
                    &self.config.root,
                    "ERROR",
                    format!("check finished: remote {remote}, but the current version is invalid: {error}"),
                );
                status.remote_version = Some(remote.to_string());
                status.update_available = false;
                status.error = Some(error.to_string());
            }
            (Err(error), _) => {
                update_log_levelled(&self.config.root, "ERROR", format!("check failed: {error:#}"));
                status.update_available = false;
                status.error = Some(format!("{error:#}"));
            }
        }
    }

    // Downloads the update and launches the replace helper. The caller is
    // expected to exit the process after this returns Ok.
    pub fn apply(self: &Arc<Self>) -> Result<()> {
        let executable = env::current_exe().context("Unable to locate nkas.exe")?;
        if !is_root_executable(&self.config.root, &executable) {
            anyhow::bail!("Desktop updates are only supported in the installed build");
        }
        {
            let mut status = self.lock_status();
            if status.applying {
                anyhow::bail!("A desktop update is already being applied");
            }
            if !status.update_available {
                anyhow::bail!("No desktop update is available");
            }
            status.applying = true;
            status.error = None;
        }
        // Fetch the manifest again instead of trusting the cached check.
        update_log(&self.config.root, "applying desktop update");
        let result = fetch_manifest(&self.config.desktop_update_manifest)
            .and_then(|manifest| {
                let remote = validate_manifest(&manifest)?;
                let current =
                    Version::parse(CURRENT_VERSION).context("Invalid current desktop version")?;
                if !is_newer_version(&remote, &current) {
                    anyhow::bail!("The desktop shell is already up to date");
                }
                stage_and_launch(&self.config, &manifest)
            });
        let mut status = self.lock_status();
        match result {
            Ok(()) => {
                update_log(
                    &self.config.root,
                    "update staged and replace helper launched; waiting for restart",
                );
                status.error = None;
                Ok(())
            }
            Err(error) => {
                update_log_levelled(&self.config.root, "ERROR", format!("apply failed: {error:#}"));
                status.applying = false;
                status.error = Some(format!("{error:#}"));
                Err(error)
            }
        }
    }

    fn lock_status(&self) -> MutexGuard<'_, DesktopUpdateStatus> {
        self.status
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
    }
}

#[tauri::command]
pub fn desktop_update_status(
    manager: tauri::State<Arc<DesktopUpdateManager>>,
) -> DesktopUpdateStatus {
    manager.status()
}

#[tauri::command]
pub fn desktop_update_check(
    manager: tauri::State<Arc<DesktopUpdateManager>>,
) -> DesktopUpdateStatus {
    manager.start_check();
    manager.status()
}

#[tauri::command]
pub async fn desktop_update_apply(
    app: tauri::AppHandle,
    manager: tauri::State<'_, Arc<DesktopUpdateManager>>,
) -> Result<(), String> {
    let manager = manager.inner().clone();
    // The download may take up to 120 seconds, so it must not run on the
    // main thread; awaiting the blocking task keeps this command non-blocking.
    tauri::async_runtime::spawn_blocking(move || -> Result<(), String> {
        manager.apply().map_err(|error| format!("{error:#}"))?;
        // Give the staged helper a moment to start waiting on this process;
        // it replaces nkas.exe and restarts it after the exit.
        thread::sleep(Duration::from_millis(500));
        app.exit(0);
        Ok(())
    })
    .await
    .map_err(|error| error.to_string())?
}

pub fn parse_internal_mode() -> Result<InternalMode> {
    parse_internal_mode_from(env::args_os().skip(1).collect())
}

fn parse_internal_mode_from(args: Vec<OsString>) -> Result<InternalMode> {
    if args.first().and_then(|value| value.to_str()) == Some("--desktop-replace") {
        let wait_pid = argument(&args, "--wait-pid")?
            .parse::<u32>()
            .context("Invalid --wait-pid")?;
        let target = PathBuf::from(argument(&args, "--target")?);
        let root = PathBuf::from(argument(&args, "--root")?);
        return Ok(InternalMode::Replace(ReplaceArgs {
            wait_pid,
            target,
            root,
        }));
    }
    let cleanup_helper =
        if args.first().and_then(|value| value.to_str()) == Some("--desktop-updated") {
            Some(PathBuf::from(argument(&args, "--cleanup-helper")?))
        } else {
            None
        };
    Ok(InternalMode::Normal { cleanup_helper })
}

fn argument(args: &[OsString], name: &str) -> Result<String> {
    let index = args
        .iter()
        .position(|value| value == name)
        .with_context(|| format!("Missing {name}"))?;
    args.get(index + 1)
        .and_then(|value| value.to_str())
        .map(str::to_string)
        .with_context(|| format!("Missing value for {name}"))
}

fn is_newer_version(remote: &Version, current: &Version) -> bool {
    remote > current
}

fn fetch_manifest(url: &str) -> Result<DesktopManifest> {
    let client = reqwest::blocking::Client::builder()
        .user_agent(format!("NKAS/{CURRENT_VERSION}"))
        .build()?;
    // Bypass CDN edge caches: the manifest is tiny, must always be fresh, and
    // Cloudflare keys its cache on the full query string.
    let mut url = reqwest::Url::parse(url)?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    url.query_pairs_mut().append_pair("_", &stamp.to_string());
    client
        .get(url)
        .timeout(Duration::from_secs(15))
        .send()?
        .error_for_status()?
        .json()
        .context("Invalid desktop update manifest")
}

fn validate_manifest(manifest: &DesktopManifest) -> Result<Version> {
    if manifest.schema != 1 {
        anyhow::bail!("Unsupported manifest schema {}", manifest.schema);
    }
    if manifest.target != TARGET {
        anyhow::bail!("Unsupported desktop target {}", manifest.target);
    }
    if manifest.size == 0 || manifest.size > MAX_DOWNLOAD_SIZE {
        anyhow::bail!("Invalid desktop update size {}", manifest.size);
    }
    if manifest.sha256.len() != 64
        || !manifest
            .sha256
            .bytes()
            .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value))
    {
        anyhow::bail!("Invalid desktop update SHA-256");
    }
    if !matches!(
        reqwest::Url::parse(&manifest.url)?.scheme(),
        "http" | "https"
    ) {
        anyhow::bail!("Desktop update URL must use HTTP or HTTPS");
    }
    Version::parse(&manifest.desktop_version).context("Invalid desktop version")
}

fn stage_and_launch(config: &DesktopConfig, manifest: &DesktopManifest) -> Result<()> {
    let update_dir = update_dir(&config.root);
    fs::create_dir_all(&update_dir)?;
    let staged = update_dir.join(format!("nkas-update-{}.exe", manifest.desktop_version));
    let part = staged.with_extension("exe.part");
    let client = reqwest::blocking::Client::builder()
        .user_agent(format!("NKAS/{CURRENT_VERSION}"))
        .build()?;
    let mut response = client
        .get(&manifest.url)
        .timeout(Duration::from_secs(120))
        .send()?
        .error_for_status()?;
    if response
        .content_length()
        .is_some_and(|size| size != manifest.size)
    {
        anyhow::bail!("Desktop update size does not match the manifest");
    }
    let _ = fs::remove_file(&part);
    let mut file = fs::File::create(&part)?;
    update_log(
        &config.root,
        format!("downloading {} ({} bytes)", manifest.url, manifest.size),
    );
    write_verified_download(&mut response, &mut file, manifest.size, &manifest.sha256)?;
    file.sync_all()?;
    // Close the handle before renaming and executing the staged file; leaving
    // it open serves no purpose and only complicates sharing on Windows.
    drop(file);
    let _ = fs::remove_file(&staged);
    fs::rename(&part, &staged)?;
    update_log(
        &config.root,
        format!(
            "download verified (size and SHA-256 match), staged at {}",
            staged.display()
        ),
    );

    let target = config.root.join("nkas.exe");
    let mut command = Command::new(&staged);
    command
        .current_dir(&config.root)
        .args([
            "--desktop-replace",
            "--wait-pid",
            std::process::id().to_string().as_str(),
            "--target",
            target.to_string_lossy().as_ref(),
            "--root",
            config.root.to_string_lossy().as_ref(),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    // A freshly written unsigned exe is often still being scanned by the
    // antivirus when we spawn it, which fails transiently with
    // ERROR_ACCESS_DENIED or ERROR_SHARING_VIOLATION; retry briefly before
    // giving up.
    let mut attempts = 0;
    let child = loop {
        attempts += 1;
        match command.spawn() {
            Ok(child) => break child,
            Err(error) => {
                let retryable = matches!(error.raw_os_error(), Some(5) | Some(32));
                if !retryable || attempts >= 10 {
                    return Err(error).context("Unable to start the desktop update helper");
                }
                update_log(
                    &config.root,
                    format!(
                        "helper spawn attempt {attempts} failed (os error {}), retrying",
                        error.raw_os_error().unwrap_or(-1)
                    ),
                );
                thread::sleep(Duration::from_millis(500));
            }
        }
    };
    update_log(
        &config.root,
        format!(
            "replace helper started: pid {}, waiting to replace {}",
            child.id(),
            target.display()
        ),
    );
    Ok(())
}

fn write_verified_download<R: Read, W: Write>(
    reader: &mut R,
    writer: &mut W,
    expected_size: u64,
    expected_sha256: &str,
) -> Result<()> {
    if expected_size == 0 || expected_size > MAX_DOWNLOAD_SIZE {
        anyhow::bail!("Invalid desktop update size {expected_size}");
    }
    let mut total = 0_u64;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        total = total
            .checked_add(read as u64)
            .context("Desktop update size overflow")?;
        if total > expected_size || total > MAX_DOWNLOAD_SIZE {
            anyhow::bail!("Desktop update exceeds the manifest size");
        }
        writer.write_all(&buffer[..read])?;
        hasher.update(&buffer[..read]);
    }
    if total != expected_size {
        anyhow::bail!("Desktop update size does not match the manifest");
    }
    let digest = format!("{:x}", hasher.finalize());
    if digest != expected_sha256 {
        anyhow::bail!("Desktop update SHA-256 does not match the manifest");
    }
    Ok(())
}

pub fn run_replace(args: ReplaceArgs) -> Result<()> {
    update_log(
        &args.root,
        format!(
            "replace helper running: wait-pid {}, target {}",
            args.wait_pid,
            args.target.display()
        ),
    );
    let root = args.root.clone();
    let result = run_replace_inner(args);
    if let Err(error) = &result {
        update_log_levelled(&root, "ERROR", format!("replace failed: {error:#}"));
    }
    result
}

fn run_replace_inner(args: ReplaceArgs) -> Result<()> {
    verify_replace_paths(&args)?;
    wait_for_process(args.wait_pid)?;
    update_log(&args.root, "old process exited; replacing nkas.exe");
    let helper = env::current_exe()?;
    let update_dir = update_dir(&args.root);
    fs::create_dir_all(&update_dir)?;
    let backup = update_dir.join("nkas-current.exe.old");
    replace_and_launch(&helper, &args.target, &backup, |target| {
        let mut command = Command::new(target);
        command
            .current_dir(&args.root)
            .args([
                "--desktop-updated",
                "--cleanup-helper",
                helper.to_string_lossy().as_ref(),
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        command
            .spawn()
            .context("Unable to restart the updated nkas.exe")?;
        Ok(())
    })?;
    update_log(
        &args.root,
        "nkas.exe replaced; restarted with --desktop-updated",
    );
    Ok(())
}

fn replace_and_launch<F>(helper: &Path, target: &Path, backup: &Path, launch: F) -> Result<()>
where
    F: FnOnce(&Path) -> Result<()>,
{
    let _ = fs::remove_file(backup);
    fs::rename(target, backup).context("Unable to back up the current nkas.exe")?;
    if let Err(error) = fs::copy(helper, target) {
        return rollback_after_failure(target, backup, error.into(), "install the new nkas.exe");
    }
    if let Err(error) = launch(target) {
        return rollback_after_failure(target, backup, error, "restart the updated nkas.exe");
    }
    Ok(())
}

fn rollback_after_failure(
    target: &Path,
    backup: &Path,
    original: anyhow::Error,
    operation: &str,
) -> Result<()> {
    let _ = fs::remove_file(target);
    match fs::rename(backup, target) {
        Ok(()) => Err(original).with_context(|| format!("Unable to {operation}")),
        Err(rollback) => anyhow::bail!(
            "Unable to {operation}: {original:#}; restoring nkas-current.exe.old also failed: {rollback}"
        ),
    }
}

fn verify_replace_paths(args: &ReplaceArgs) -> Result<()> {
    let expected = args.root.join("nkas.exe");
    if normalized_path(&expected) != normalized_path(&args.target) {
        anyhow::bail!("Desktop update target is outside the NKAS root");
    }
    let helper = env::current_exe()?;
    if normalized_path(helper.parent().unwrap_or(Path::new("")))
        != normalized_path(&update_dir(&args.root))
    {
        anyhow::bail!("Desktop update helper is outside tmp/desktop-update");
    }
    Ok(())
}

fn normalized_path(path: &Path) -> String {
    fs::canonicalize(path)
        .unwrap_or_else(|_| path.to_path_buf())
        .to_string_lossy()
        .replace('/', "\\")
        .to_lowercase()
}

fn is_root_executable(root: &Path, executable: &Path) -> bool {
    normalized_path(executable) == normalized_path(&root.join("nkas.exe"))
}

fn update_dir(root: &Path) -> PathBuf {
    root.join("tmp/desktop-update")
}

#[cfg(windows)]
fn wait_for_process(process_id: u32) -> Result<()> {
    let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, false, process_id) };
    let Ok(handle) = handle else {
        return Ok(());
    };
    let result = unsafe { WaitForSingleObject(handle, 60_000) };
    unsafe {
        let _ = CloseHandle(handle);
    }
    if result != WAIT_OBJECT_0 {
        anyhow::bail!("Timed out waiting for the current desktop process to exit");
    }
    Ok(())
}

#[cfg(not(windows))]
fn wait_for_process(_: u32) -> Result<()> {
    Ok(())
}

pub fn cleanup_after_success(root: &Path, cleanup_helper: Option<PathBuf>) {
    let root = root.to_path_buf();
    let cleanup_helper = cleanup_helper.filter(|path| is_update_executable(&root, path));
    thread::spawn(move || {
        update_log(&root, "running post-update cleanup");
        let directory = update_dir(&root);
        for _ in 0..30 {
            let mut pending = false;
            if let Some(helper) = cleanup_helper.as_ref() {
                if helper.exists() && fs::remove_file(helper).is_err() {
                    pending = true;
                }
            }
            if let Ok(entries) = fs::read_dir(&directory) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    let name = path
                        .file_name()
                        .and_then(|value| value.to_str())
                        .unwrap_or("");
                    let stale_update = name.starts_with("nkas-update-") && name.ends_with(".exe");
                    if (name == "nkas-current.exe.old" || name.ends_with(".part") || stale_update)
                        && fs::remove_file(&path).is_err()
                    {
                        pending = true;
                    }
                }
            }
            let _ = fs::remove_dir(&directory);
            if !pending {
                update_log(&root, "post-update cleanup finished");
                return;
            }
            thread::sleep(Duration::from_millis(250));
        }
        update_log_levelled(
            &root,
            "WARNING",
            "post-update cleanup gave up; leftover files remain in tmp/desktop-update",
        );
    });
}

fn is_update_executable(root: &Path, path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("");
    name.starts_with("nkas-update-")
        && name.ends_with(".exe")
        && path
            .parent()
            .is_some_and(|parent| normalized_path(parent) == normalized_path(&update_dir(root)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn temporary_directory(name: &str) -> PathBuf {
        env::temp_dir().join(format!("nkas-{name}-{}", std::process::id()))
    }

    fn manifest(version: &str) -> DesktopManifest {
        DesktopManifest {
            schema: 1,
            _project_version: "v1".into(),
            desktop_version: version.into(),
            target: TARGET.into(),
            url: "https://example.com/nkas.exe".into(),
            sha256: "0".repeat(64),
            size: 1024,
        }
    }

    #[test]
    fn desktop_version_is_independent_from_project_version() {
        let value = manifest(CURRENT_VERSION);
        assert_eq!(
            validate_manifest(&value).unwrap(),
            Version::parse(CURRENT_VERSION).unwrap()
        );
        assert_eq!(value._project_version, "v1");
    }

    #[test]
    fn only_higher_desktop_versions_update() {
        let current = Version::parse("0.2.0").unwrap();
        assert!(is_newer_version(
            &Version::parse("0.2.1").unwrap(),
            &current
        ));
        assert!(!is_newer_version(
            &Version::parse("0.2.0").unwrap(),
            &current
        ));
        assert!(!is_newer_version(
            &Version::parse("0.1.9").unwrap(),
            &current
        ));
    }

    #[test]
    fn oversized_update_is_rejected() {
        let mut value = manifest("99.0.0");
        value.size = MAX_DOWNLOAD_SIZE + 1;
        assert!(validate_manifest(&value).is_err());
    }

    #[test]
    fn uppercase_sha256_is_rejected() {
        let mut value = manifest("99.0.0");
        value.sha256 = "A".repeat(64);
        assert!(validate_manifest(&value).is_err());
    }

    #[test]
    fn verified_download_checks_size_and_sha256() {
        let bytes = b"desktop executable";
        let digest = format!("{:x}", Sha256::digest(bytes));
        let mut output = Vec::new();
        write_verified_download(
            &mut Cursor::new(bytes),
            &mut output,
            bytes.len() as u64,
            &digest,
        )
        .unwrap();
        assert_eq!(output, bytes);

        assert!(write_verified_download(
            &mut Cursor::new(bytes),
            &mut Vec::new(),
            (bytes.len() - 1) as u64,
            &digest,
        )
        .is_err());
        assert!(write_verified_download(
            &mut Cursor::new(bytes),
            &mut Vec::new(),
            bytes.len() as u64,
            &"0".repeat(64),
        )
        .is_err());
    }

    #[test]
    fn update_directory_is_inside_project_tmp() {
        assert_eq!(
            update_dir(Path::new("C:/NKAS")),
            PathBuf::from("C:/NKAS/tmp/desktop-update")
        );
    }

    #[test]
    fn desktop_update_only_runs_from_root_executable() {
        let root = Path::new("C:/NKAS");
        assert!(is_root_executable(root, Path::new("c:/nkas/NKAS.EXE")));
        assert!(!is_root_executable(
            root,
            Path::new("C:/NKAS/webapp/src-tauri/target/release/nkas.exe")
        ));
    }

    fn test_config(manifest: &str) -> DesktopConfig {
        DesktopConfig {
            root: PathBuf::from("C:/NKAS"),
            python: PathBuf::from("python.exe"),
            host: "127.0.0.1".into(),
            port: 12271,
            desktop_update_manifest: manifest.into(),
            dpi_scaling: true,
            hardware_acceleration: false,
            theme: "dark".into(),
            shortcuts: Default::default(),
        }
    }

    #[test]
    fn update_status_starts_unchecked() {
        let manager = DesktopUpdateManager::new(test_config("https://example.com/nkas.json"));
        let status = manager.status();
        assert_eq!(status.current_version, CURRENT_VERSION);
        assert!(!status.checked);
        assert!(!status.checking);
        assert!(!status.update_available);
        assert!(!status.applying);
        assert!(status.remote_version.is_none());
        assert!(status.error.is_none());
    }

    #[test]
    fn update_status_serializes_with_camel_case_fields() {
        let status = DesktopUpdateManager::new(test_config("https://example.com/nkas.json")).status();
        let json = serde_json::to_value(&status).unwrap();
        assert_eq!(json["currentVersion"], CURRENT_VERSION);
        assert!(json.get("checked").is_some());
        assert!(json.get("checking").is_some());
        assert!(json.get("updateAvailable").is_some());
        assert!(json.get("applying").is_some());
        assert!(json.get("remoteVersion").is_some());
        assert!(json.get("current_version").is_none());
    }

    #[test]
    fn empty_manifest_skips_the_background_check() {
        let manager = Arc::new(DesktopUpdateManager::new(test_config("  ")));
        manager.start_check();
        let status = manager.status();
        assert!(!status.checked);
        assert!(!status.checking);
    }

    #[test]
    fn apply_only_runs_from_root_executable() {
        let manager = Arc::new(DesktopUpdateManager::new(test_config("https://example.com/nkas.json")));
        // The test binary is never the root nkas.exe, so the guard must
        // reject the apply before any network access happens.
        let error = manager.apply().unwrap_err();
        assert!(format!("{error:#}").contains("installed build"));
        assert!(!manager.status().applying);
    }

    #[test]
    fn internal_replace_arguments_are_parsed() {
        let mode = parse_internal_mode_from(
            [
                "--desktop-replace",
                "--wait-pid",
                "123",
                "--target",
                "C:/NKAS/nkas.exe",
                "--root",
                "C:/NKAS",
            ]
            .into_iter()
            .map(OsString::from)
            .collect(),
        )
        .unwrap();
        let InternalMode::Replace(args) = mode else {
            panic!("expected replace mode");
        };
        assert_eq!(args.wait_pid, 123);
        assert_eq!(args.target, PathBuf::from("C:/NKAS/nkas.exe"));
        assert_eq!(args.root, PathBuf::from("C:/NKAS"));
    }

    #[test]
    fn failed_restart_restores_previous_executable() {
        let directory = temporary_directory("rollback");
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).unwrap();
        let helper = directory.join("new.exe");
        let target = directory.join("nkas.exe");
        let backup = directory.join("nkas-current.exe.old");
        fs::write(&helper, b"new").unwrap();
        fs::write(&target, b"old").unwrap();

        let result = replace_and_launch(&helper, &target, &backup, |_| {
            anyhow::bail!("simulated launch failure")
        });
        assert!(result.is_err());
        assert_eq!(fs::read(&target).unwrap(), b"old");
        assert!(!backup.exists());
        let _ = fs::remove_dir_all(directory);
    }

    #[test]
    fn cleanup_helper_must_be_inside_update_directory() {
        let root = Path::new("C:/NKAS");
        assert!(is_update_executable(
            root,
            Path::new("C:/NKAS/tmp/desktop-update/nkas-update-0.3.0.exe")
        ));
        assert!(!is_update_executable(
            root,
            Path::new("C:/NKAS/nkas-update-0.3.0.exe")
        ));
    }

    #[test]
    fn manifest_schema_and_target_are_enforced() {
        let mut value = manifest("0.3.0");
        value.schema = 2;
        assert!(validate_manifest(&value).is_err());
        value.schema = 1;
        value.target = "aarch64-pc-windows-msvc".into();
        assert!(validate_manifest(&value).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn waiting_for_a_missing_pid_finishes_immediately() {
        assert!(wait_for_process(u32::MAX).is_ok());
    }
}
