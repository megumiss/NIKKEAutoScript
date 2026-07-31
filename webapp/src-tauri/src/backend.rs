use crate::config::DesktopConfig;
use anyhow::{Context, Result};
use encoding_rs::GBK;
use serde::Deserialize;
use std::io::{BufRead, BufReader, Read};
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
use windows::core::PCWSTR;
#[cfg(windows)]
use windows::Win32::Foundation::{CloseHandle, HANDLE};
#[cfg(windows)]
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
#[cfg(windows)]
use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

#[derive(Debug, Deserialize)]
struct HealthStatus {
    api_version: u32,
    capabilities: Capabilities,
}

#[derive(Debug, Deserialize)]
struct Capabilities {
    spa: bool,
}

pub type LogSink = Arc<dyn Fn(String) + Send + Sync>;

pub struct Backend {
    child: Option<Child>,
    #[cfg(windows)]
    job: Option<HANDLE>,
}

unsafe impl Send for Backend {}

impl Backend {
    pub fn external() -> Self {
        Self {
            child: None,
            #[cfg(windows)]
            job: None,
        }
    }

    pub fn shutdown(&mut self) {
        #[cfg(windows)]
        if let Some(job) = self.job.take() {
            unsafe {
                let _ = CloseHandle(job);
            }
        }
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.shutdown();
    }
}

pub fn url(port: u16, path: &str) -> String {
    format!("http://127.0.0.1:{port}{path}")
}

pub fn health(port: u16) -> bool {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(600))
        .build()
        .and_then(|client| client.get(url(port, "/api/system/status")).send())
        .and_then(|response| response.error_for_status())
        .and_then(|response| response.json::<HealthStatus>())
        .map(|status| status.api_version == 2 && status.capabilities.spa)
        .unwrap_or(false)
}

pub fn start_and_wait(config: &DesktopConfig, log: LogSink) -> Result<Backend> {
    log(format!(
        "Checking for an existing backend at {}",
        url(config.port, "/api/system/status")
    ));
    if health(config.port) {
        log("Connected to an existing NKAS backend; startup update is skipped.".into());
        return Ok(Backend::external());
    }
    if !config.python.is_file() {
        anyhow::bail!("Python executable not found: {}", config.python.display());
    }
    log("Running project update and dependency checks...".into());
    run_prepare(config, log.clone())?;
    if health(config.port) {
        log("A compatible backend became available during preparation.".into());
        return Ok(Backend::external());
    }
    let port = config.port.to_string();
    log(format!(
        "Starting Python backend with {}",
        config.python.display()
    ));
    let mut command = Command::new(&config.python);
    command
        .current_dir(&config.root)
        .args(["gui.py", "--port", port.as_str(), "--electron"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_python_output(&mut command);
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    let mut child = command
        .spawn()
        .with_context(|| format!("Unable to start {}", config.python.display()))?;
    let process_id = child.id();
    if let Some(stdout) = child.stdout.take() {
        let _ = spawn_log_reader(stdout, log.clone());
    }
    if let Some(stderr) = child.stderr.take() {
        let _ = spawn_log_reader(stderr, log.clone());
    }
    log(format!(
        "Python backend process started (PID {process_id})."
    ));
    let mut backend = Backend {
        child: Some(child),
        #[cfg(windows)]
        job: None,
    };
    #[cfg(windows)]
    {
        backend.job = Some(assign_kill_job(backend.child.as_ref().unwrap().id())?);
    }

    let started = Instant::now();
    let mut next_notice = Duration::from_secs(2);
    while started.elapsed() < Duration::from_secs(60) {
        if health(config.port) {
            log("Backend health check passed.".into());
            return Ok(backend);
        }
        if let Some(status) = backend
            .child
            .as_mut()
            .and_then(|child| child.try_wait().ok())
            .flatten()
        {
            anyhow::bail!("Python backend exited before startup completed ({status})");
        }
        if started.elapsed() >= next_notice {
            log(format!(
                "Waiting for the backend to become ready... {}s",
                started.elapsed().as_secs()
            ));
            next_notice += Duration::from_secs(2);
        }
        thread::sleep(Duration::from_millis(250));
    }
    anyhow::bail!(
        "Timed out waiting 60 seconds for {}",
        url(config.port, "/api/system/status")
    )
}

fn run_prepare(config: &DesktopConfig, log: LogSink) -> Result<()> {
    let desktop_pid = std::process::id().to_string();
    let mut command = Command::new(&config.python);
    command
        .current_dir(&config.root)
        .args([
            "-m",
            "deploy.starter",
            "--prepare",
            "--desktop-pid",
            desktop_pid.as_str(),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_python_output(&mut command);
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    let mut child = command.spawn().with_context(|| {
        format!(
            "Unable to run startup update with {}",
            config.python.display()
        )
    })?;
    let stdout_reader = child
        .stdout
        .take()
        .map(|stdout| spawn_log_reader(stdout, log.clone()));
    let stderr_reader = child
        .stderr
        .take()
        .map(|stderr| spawn_log_reader(stderr, log));
    let status = child.wait().context("Unable to wait for startup update")?;
    let stdout = join_log_reader(stdout_reader);
    let stderr = join_log_reader(stderr_reader);
    if status.success() {
        return Ok(());
    }
    let details = if !stderr.trim().is_empty() {
        stderr.trim()
    } else {
        stdout.trim()
    };
    anyhow::bail!(
        "Startup update failed ({}){}{}",
        status,
        if details.is_empty() { "" } else { ": " },
        details
    )
}

fn spawn_log_reader<R>(reader: R, log: LogSink) -> thread::JoinHandle<String>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut captured = String::new();
        let mut reader = BufReader::new(reader);
        let mut buffer = Vec::new();
        loop {
            buffer.clear();
            match reader.read_until(b'\n', &mut buffer) {
                Ok(0) => break,
                Ok(_) => {
                    let line = decode_output_line(&buffer);
                    if !line.trim().is_empty() {
                        log(line.clone());
                        captured.push_str(&line);
                        captured.push('\n');
                    }
                }
                Err(error) => {
                    log(format!("Unable to read process output: {error}"));
                    break;
                }
            }
        }
        captured
    })
}

fn configure_python_output(command: &mut Command) {
    command
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8:replace")
        .env("PYTHONUNBUFFERED", "1");
}

fn decode_output_line(buffer: &[u8]) -> String {
    let end = buffer
        .iter()
        .rposition(|value| !matches!(value, b'\r' | b'\n'))
        .map_or(0, |index| index + 1);
    let line = &buffer[..end];
    match std::str::from_utf8(line) {
        Ok(line) => line.to_string(),
        Err(_) => {
            let (decoded, _, _) = GBK.decode(line);
            decoded.into_owned()
        }
    }
}

fn join_log_reader(reader: Option<thread::JoinHandle<String>>) -> String {
    reader
        .and_then(|reader| reader.join().ok())
        .unwrap_or_default()
}

#[cfg(windows)]
fn assign_kill_job(process_id: u32) -> Result<HANDLE> {
    unsafe {
        let job = CreateJobObjectW(None, PCWSTR::null())
            .context("Unable to create Windows Job Object")?;
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if let Err(error) = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            std::mem::size_of_val(&info) as u32,
        ) {
            let _ = CloseHandle(job);
            return Err(error).context("Unable to configure Windows Job Object");
        }
        let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false, process_id)
            .context("Unable to open Python process for Job Object assignment")?;
        let assigned = AssignProcessToJobObject(job, process);
        let _ = CloseHandle(process);
        if let Err(error) = assigned {
            let _ = CloseHandle(job);
            return Err(error).context("Unable to assign Python process to Windows Job Object");
        }
        Ok(job)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use std::sync::Mutex;

    #[test]
    fn backend_urls_are_loopback_only() {
        assert_eq!(url(12271, "/app/"), "http://127.0.0.1:12271/app/");
    }

    #[test]
    fn process_output_is_streamed_with_lossy_decoding() {
        let messages = Arc::new(Mutex::new(Vec::new()));
        let received = messages.clone();
        let log: LogSink = Arc::new(move |message| received.lock().unwrap().push(message));
        let reader = spawn_log_reader(Cursor::new(b"first\ninvalid: \xff\n".to_vec()), log);

        let captured = reader.join().unwrap();
        assert!(captured.contains("first"));
        assert!(captured.contains("invalid:"));
        assert_eq!(messages.lock().unwrap().len(), 2);
    }

    #[test]
    fn gbk_process_output_is_decoded() {
        assert_eq!(decode_output_line(&[0xc6, 0xf4, 0xb6, 0xaf, b'\n']), "启动");
        assert_eq!(decode_output_line("正常 UTF-8\n".as_bytes()), "正常 UTF-8");
    }
}
