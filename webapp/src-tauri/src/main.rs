#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod config;
mod desktop_update;
mod security_entry;

use anyhow::{Context, Result};
use backend::Backend;
use config::DesktopConfig;
use std::collections::HashMap;
use std::env;
use std::fs;
use std::io;
use std::path::{Component, Path};
use std::str::FromStr;
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::{NewWindowResponse, PageLoadEvent};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

#[cfg(windows)]
use windows::core::HSTRING;
#[cfg(windows)]
use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};

struct BackendState(Mutex<Option<Backend>>);

#[derive(Clone, Copy)]
enum StartupMessageKind {
    Log,
    Output,
    Stage,
    Error,
    Ready,
}

struct StartupMessage {
    kind: StartupMessageKind,
    text: String,
}

#[derive(Default)]
struct StartupReporterState {
    window: Option<WebviewWindow>,
    ready: bool,
    pending: Vec<StartupMessage>,
}

#[derive(Clone, Default)]
struct StartupReporter(Arc<Mutex<StartupReporterState>>);

impl StartupReporter {
    fn log(&self, message: impl Into<String>) {
        self.send(StartupMessageKind::Log, message.into());
    }

    /// Raw stdout/stderr lines from the Python processes; the startup page
    /// folds them into log records the same way the history log viewer does.
    fn output(&self, message: impl Into<String>) {
        self.send(StartupMessageKind::Output, message.into());
    }

    fn stage(&self, message: impl Into<String>) {
        self.send(StartupMessageKind::Stage, message.into());
    }

    fn error(&self, message: impl Into<String>) {
        self.send(StartupMessageKind::Error, message.into());
    }

    fn complete(&self, message: impl Into<String>) {
        self.send(StartupMessageKind::Ready, message.into());
    }

    fn send(&self, kind: StartupMessageKind, text: String) {
        let message = StartupMessage { kind, text };
        let window = {
            let Ok(mut state) = self.0.lock() else {
                return;
            };
            if !state.ready {
                state.pending.push(message);
                return;
            }
            state.window.clone()
        };
        if let Some(window) = window {
            let _ = window.eval(startup_script(&message));
        }
    }

    fn page_ready(&self, window: WebviewWindow) {
        let pending = {
            let Ok(mut state) = self.0.lock() else {
                return;
            };
            state.window = Some(window.clone());
            state.ready = true;
            std::mem::take(&mut state.pending)
        };
        for message in pending {
            let _ = window.eval(startup_script(&message));
        }
    }
}

fn startup_script(message: &StartupMessage) -> String {
    let method = match message.kind {
        StartupMessageKind::Log => "log",
        StartupMessageKind::Output => "output",
        StartupMessageKind::Stage => "stage",
        StartupMessageKind::Error => "error",
        StartupMessageKind::Ready => "ready",
    };
    let text = serde_json::to_string(&message.text).unwrap_or_else(|_| "\"\"".into());
    format!("window.nkasStartup?.{method}({text});")
}

fn show_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn hide_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
}

fn show_native_message(title: &str, message: &str, error: bool) {
    #[cfg(windows)]
    unsafe {
        use windows::Win32::UI::WindowsAndMessaging::MB_ICONWARNING;
        let icon = if error { MB_ICONERROR } else { MB_ICONWARNING };
        let _ = MessageBoxW(
            None,
            &HSTRING::from(message),
            &HSTRING::from(title),
            MB_OK | icon,
        );
    }
    #[cfg(not(windows))]
    eprintln!("{title}: {message}");
}

fn is_local_webui_url(url: &Url, host: &str, port: u16) -> bool {
    // Url::host_str keeps the brackets of IPv6 literals ([::1]) while
    // DesktopConfig.host is stored without them, so compare unbracketed.
    url.scheme() == "http"
        && url
            .host_str()
            .map(|value| value.trim_matches(|c| c == '[' || c == ']'))
            == Some(host)
        && url.port_or_known_default() == Some(port)
}

fn is_startup_url(url: &Url) -> bool {
    url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost")
}

fn is_valid_export_filename(filename: &str) -> bool {
    let path = Path::new(filename);
    !filename.is_empty()
        && path.components().count() == 1
        && matches!(path.components().next(), Some(Component::Normal(_)))
        && path.file_name().and_then(|name| name.to_str()) == Some(filename)
}

fn available_export_path(directory: &Path, filename: &str) -> std::path::PathBuf {
    let path = Path::new(filename);
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or(filename);
    let extension = path.extension().and_then(|value| value.to_str());
    let original = directory.join(filename);
    if !original.exists() {
        return original;
    }
    for index in 1.. {
        let candidate = match extension {
            Some(extension) => directory.join(format!("{stem} ({index}).{extension}")),
            None => directory.join(format!("{stem} ({index})")),
        };
        if !candidate.exists() {
            return candidate;
        }
    }
    unreachable!()
}

#[tauri::command]
async fn refresh_security_entry(window: WebviewWindow, config: tauri::State<'_, DesktopConfig>) -> Result<(), String> {
    let current = window.url().map_err(|_| "Unable to read application URL")?;
    if !is_local_webui_url(&current, &config.host, config.port) {
        return Err("Security entry is restricted to the configured backend".into());
    }
    let config = config.inner().clone();
    let page = tauri::async_runtime::spawn_blocking(move || security_entry::page(&config))
        .await.map_err(|_| "Unable to read local security entry")?
        .map_err(|_| "Unable to read local security entry")?;
    if !page.path().starts_with("/entry/") {
        return Err("No local security entry is available".into());
    }
    window.navigate(page).map_err(|_| "Unable to open local security entry".into())
}

#[tauri::command]
async fn save_export_file(
    window: WebviewWindow,
    config: tauri::State<'_, DesktopConfig>,
    url_path: String,
    filename: String,
) -> Result<String, String> {
    if !is_valid_export_filename(&filename) {
        return Err("Invalid export filename".into());
    }
    if !url_path.starts_with("/api/") || url_path.starts_with("//") {
        return Err("Invalid export URL".into());
    }

    let current_url = window
        .url()
        .map_err(|error| format!("Unable to read the application URL: {error}"))?;
    if !is_local_webui_url(&current_url, &config.host, config.port) {
        return Err("Exports are restricted to the configured backend".into());
    }
    let config = config.inner().clone();
    let export_url = current_url
        .join(&url_path)
        .map_err(|error| format!("Invalid export URL: {error}"))?;
    if current_url.origin() != export_url.origin() || !export_url.path().starts_with("/api/") {
        return Err("Invalid export URL".into());
    }
    let downloads = window
        .app_handle()
        .path()
        .download_dir()
        .map_err(|error| format!("Unable to locate the Downloads directory: {error}"))?;
    tauri::async_runtime::spawn_blocking(move || {
        fs::create_dir_all(&downloads)
            .map_err(|error| format!("Unable to create Downloads directory: {error}"))?;
        let destination = available_export_path(&downloads, &filename);
        let client = reqwest::blocking::Client::builder()
            .redirect(reqwest::redirect::Policy::none()).build().map_err(|_| "Unable to create export client")?;
        let mut response = security_entry::authorize(client.get(export_url), &config)
            .map_err(|_| "Unable to read local security entry")?
            .send()
            .and_then(|response| response.error_for_status())
            .map_err(|error| format!("Unable to download export: {error}"))?;
        let mut file = fs::File::create(&destination)
            .map_err(|error| format!("Unable to create export file: {error}"))?;
        if let Err(error) = io::copy(&mut response, &mut file) {
            let _ = fs::remove_file(&destination);
            return Err(format!("Unable to save export: {error}"));
        }
        Ok(destination.to_string_lossy().into_owned())
    })
    .await
    .map_err(|error| format!("Export task failed: {error}"))?
}

fn create_window(
    app: &AppHandle,
    config: &DesktopConfig,
    reporter: StartupReporter,
) -> Result<WebviewWindow> {
    let allowed_host = config.host.clone();
    let allowed_port = config.port;
    let load_reporter = reporter.clone();
    // Apply the configured theme before first paint so the startup screen
    // matches the WebUI instead of always flashing the dark palette.  The
    // initialization script runs at document creation where
    // document.documentElement may not exist yet, so pass the value through
    // a global and let each page apply it itself.
    let theme_script = format!(
        "window.__nkasTheme={};",
        serde_json::to_string(&config.theme).unwrap_or_else(|_| "\"light\"".into())
    );
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("NKAS")
        .inner_size(1440.0, 900.0)
        .min_inner_size(1000.0, 640.0)
        // Native chrome is replaced by the in-page titlebar (webui App.vue and
        // shell/index.html); keep the window shadow the OS drops for
        // undecorated windows.
        .decorations(false)
        .shadow(true)
        .visible(true)
        .initialization_script(&theme_script)
        .on_page_load(move |window, payload| {
            if payload.event() == PageLoadEvent::Finished && is_startup_url(payload.url()) {
                load_reporter.page_ready(window);
            }
        })
        .on_navigation(move |url| {
            let target = url.as_str();
            let local = is_local_webui_url(url, &allowed_host, allowed_port);
            let startup = is_startup_url(url);
            if !local && !startup && matches!(url.scheme(), "http" | "https") {
                let _ = open::that_detached(target);
            }
            local || startup
        })
        .on_new_window(|url, _features| {
            if matches!(url.scheme(), "http" | "https") {
                let _ = open::that_detached(url.as_str());
            }
            NewWindowResponse::Deny
        })
        .build()
        .context("Unable to create the NKAS window")
}

fn store_backend(app: &AppHandle, backend: Backend) -> Result<()> {
    let state = app
        .try_state::<BackendState>()
        .context("Backend state is unavailable")?;
    let mut current = state
        .0
        .lock()
        .map_err(|_| anyhow::anyhow!("Backend state lock is poisoned"))?;
    *current = Some(backend);
    Ok(())
}

fn start_application(
    app: AppHandle,
    window: WebviewWindow,
    config: DesktopConfig,
    reporter: StartupReporter,
    cleanup_helper: Option<std::path::PathBuf>,
) {
    if let Err(error) = start_application_inner(&app, &window, &config, &reporter, cleanup_helper) {
        let message = format!("{error:#}");
        reporter.error(&message);
        show_native_message("NKAS startup failed", &message, true);
    }
}

fn start_application_inner(
    app: &AppHandle,
    window: &WebviewWindow,
    config: &DesktopConfig,
    reporter: &StartupReporter,
    cleanup_helper: Option<std::path::PathBuf>,
) -> Result<()> {
    reporter.log(format!(
        "NKAS desktop version {}",
        env!("CARGO_PKG_VERSION")
    ));

    reporter.stage("Preparing the NKAS backend");
    let backend_reporter = reporter.clone();
    let log: backend::LogSink = Arc::new(move |message| backend_reporter.log(message));
    let output_reporter = reporter.clone();
    let output: backend::LogSink = Arc::new(move |message| output_reporter.output(message));
    let backend = backend::start_and_wait(config, log, output)?;
    store_backend(app, backend)?;
    desktop_update::cleanup_after_success(&config.root, cleanup_helper);

    reporter.stage("Opening the application");
    reporter.complete("Startup complete. Opening NKAS...");
    thread::sleep(std::time::Duration::from_millis(150));
    let page = security_entry::page(config)?;
    window.navigate(page)?;
    window.show()?;
    window.set_focus()?;
    // Check for desktop shell updates once in the background after startup;
    // applying an update is done manually from the WebUI.
    if let Some(manager) = app.try_state::<Arc<desktop_update::DesktopUpdateManager>>() {
        manager.start_check();
    }
    Ok(())
}

fn install_tray(app: &AppHandle) -> Result<()> {
    let show = MenuItem::with_id(app, "show", "Show", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "Hide", true, None::<&str>)?;
    let exit = MenuItem::with_id(app, "exit", "Exit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &hide, &exit])?;
    let icon = app
        .default_window_icon()
        .cloned()
        .context("Application icon is missing")?;
    TrayIconBuilder::new()
        .icon(icon)
        .tooltip("NKAS")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => show_window(app),
            "hide" => hide_window(app),
            "exit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if let Some(window) = app.get_webview_window("main") {
                    if window.is_visible().unwrap_or(false) {
                        hide_window(app);
                    } else {
                        show_window(app);
                    }
                }
            }
        })
        .build(app)?;
    Ok(())
}

fn post(config: DesktopConfig, path: &'static str) {
    thread::spawn(move || {
        let Ok(client) = reqwest::blocking::Client::builder().redirect(reqwest::redirect::Policy::none()).build() else { return; };
        if let Ok(request) = security_entry::authorize(client.post(backend::url(&config.host, config.port, path)), &config) {
            let _ = request.send();
        }
    });
}

fn install_shortcuts(app: &AppHandle, config: &DesktopConfig) -> Result<()> {
    if !config.shortcuts_enabled {
        return Ok(());
    }
    let api_paths = HashMap::from([
        ("UPDATE", "/api/update"),
        ("START", "/api/all/start"),
        ("STOP", "/api/all/stop"),
        ("RESTART", "/api/restart"),
        ("ROTATE", "/api/rotate"),
    ]);
    for (key, path) in api_paths {
        let Some(value) = config.shortcuts.get(key) else {
            continue;
        };
        let Ok(shortcut) = Shortcut::from_str(value) else {
            continue;
        };
        let request_config = config.clone();
        let _ = app
            .global_shortcut()
            .on_shortcut(shortcut, move |_app, _shortcut, event| {
                if event.state() == ShortcutState::Pressed {
                    post(request_config.clone(), path);
                }
            });
    }
    for key in ["DEV_TOOLS", "REFRESH", "HARD_REFRESH"] {
        let Some(value) = config.shortcuts.get(key) else {
            continue;
        };
        let Ok(shortcut) = Shortcut::from_str(value) else {
            continue;
        };
        let _ = app
            .global_shortcut()
            .on_shortcut(shortcut, move |app, _shortcut, event| {
                if event.state() != ShortcutState::Pressed {
                    return;
                }
                let Some(window) = app.get_webview_window("main") else {
                    return;
                };
                if !window.is_focused().unwrap_or(false) {
                    return;
                }
                match key {
                    "DEV_TOOLS" => {
                        if window.is_devtools_open() {
                            window.close_devtools();
                        } else {
                            window.open_devtools();
                        }
                    }
                    _ => {
                        let _ = window.reload();
                    }
                }
            });
    }
    Ok(())
}

fn run(cleanup_helper: Option<std::path::PathBuf>) -> Result<()> {
    let cwd = env::current_dir().context("Unable to read current directory")?;
    let executable = env::current_exe().context("Unable to locate nkas.exe")?;
    let root = config::locate_root(&cwd, &executable)?;
    let desktop = config::load(root)?;
    if let Some(arguments) = config::webview_arguments(
        &desktop,
        env::var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS")
            .ok()
            .as_deref(),
    ) {
        env::set_var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", arguments);
    }
    let app_config = desktop.clone();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_window(app)
        }))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            desktop_update::desktop_update_status,
            desktop_update::desktop_update_check,
            desktop_update::desktop_update_apply,
            save_export_file,
            refresh_security_entry,
        ])
        .on_window_event(|window, event| {
            if window.label() == "main"
                && matches!(event, tauri::WindowEvent::CloseRequested { .. })
            {
                window.app_handle().exit(0);
            }
        })
        .setup(move |app| {
            app.manage(app_config.clone());
            app.manage(BackendState(Mutex::new(None)));
            app.manage(Arc::new(desktop_update::DesktopUpdateManager::new(
                app_config.clone(),
            )));
            install_tray(app.handle())?;
            install_shortcuts(app.handle(), &app_config)?;
            let reporter = StartupReporter::default();
            let window = create_window(app.handle(), &app_config, reporter.clone())?;
            window.show()?;
            window.set_focus()?;
            let app_handle = app.handle().clone();
            thread::spawn(move || {
                start_application(app_handle, window, app_config, reporter, cleanup_helper)
            });
            Ok(())
        })
        .build(tauri::generate_context!())?;

    app.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = app.try_state::<BackendState>() {
                if let Ok(mut backend) = state.0.lock() {
                    if let Some(mut backend) = backend.take() {
                        backend.shutdown();
                    }
                }
            }
            app.global_shortcut().unregister_all().ok();
        }
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_navigation_requires_exact_origin() {
        let local = Url::parse("http://127.0.0.1:12271/app/").unwrap();
        let userinfo_bypass = Url::parse("http://127.0.0.1:12271@evil.example/app/").unwrap();
        let wrong_port = Url::parse("http://127.0.0.1:12272/app/").unwrap();
        let wrong_host = Url::parse("http://192.168.1.5:12271/app/").unwrap();

        assert!(is_local_webui_url(&local, "127.0.0.1", 12271));
        assert!(!is_local_webui_url(&userinfo_bypass, "127.0.0.1", 12271));
        assert!(!is_local_webui_url(&wrong_port, "127.0.0.1", 12271));
        assert!(!is_local_webui_url(&wrong_host, "127.0.0.1", 12271));
    }

    #[test]
    fn local_navigation_accepts_the_configured_host() {
        let lan = Url::parse("http://192.168.1.5:12271/app/").unwrap();
        let ipv6 = Url::parse("http://[::1]:12271/app/").unwrap();

        assert!(is_local_webui_url(&lan, "192.168.1.5", 12271));
        assert!(is_local_webui_url(&ipv6, "::1", 12271));
        assert!(!is_local_webui_url(&lan, "::1", 12271));
    }

    #[test]
    fn startup_page_allows_only_the_tauri_asset_origin() {
        let startup = Url::parse("http://tauri.localhost/index.html").unwrap();
        let external = Url::parse("https://example.com/index.html").unwrap();
        assert!(is_startup_url(&startup));
        assert!(!is_startup_url(&external));
    }

    #[test]
    fn startup_messages_are_json_escaped() {
        let script = startup_script(&StartupMessage {
            kind: StartupMessageKind::Log,
            text: "line \"one\"\nline two".into(),
        });
        assert_eq!(
            script,
            "window.nkasStartup?.log(\"line \\\"one\\\"\\nline two\");"
        );
    }

    #[test]
    fn export_filename_rejects_paths() {
        assert!(is_valid_export_filename("2026-09-14_nkas.txt"));
        assert!(is_valid_export_filename("nkas.json"));
        assert!(!is_valid_export_filename(""));
        assert!(!is_valid_export_filename("../nkas.json"));
        assert!(!is_valid_export_filename("folder/nkas.json"));
        assert!(!is_valid_export_filename(r"folder\nkas.json"));
    }

    #[test]
    fn export_path_adds_a_suffix_when_the_name_exists() {
        let directory = env::temp_dir().join(format!(
            "nkas-export-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&directory).unwrap();
        fs::write(directory.join("nkas.json"), "first").unwrap();
        fs::write(directory.join("nkas (1).json"), "second").unwrap();
        assert_eq!(
            available_export_path(&directory, "nkas.json"),
            directory.join("nkas (2).json")
        );
        fs::remove_dir_all(directory).unwrap();
    }
}

fn main() {
    let mode = match desktop_update::parse_internal_mode() {
        Ok(mode) => mode,
        Err(error) => {
            show_native_message("NKAS startup failed", &format!("{error:#}"), true);
            return;
        }
    };
    if let desktop_update::InternalMode::Replace(args) = mode {
        if let Err(error) = desktop_update::run_replace(args) {
            show_native_message("NKAS desktop update failed", &format!("{error:#}"), true);
        }
        return;
    }
    let desktop_update::InternalMode::Normal { cleanup_helper } = mode else {
        unreachable!();
    };
    if let Err(error) = run(cleanup_helper) {
        let message = format!("{error:#}");
        show_native_message("NKAS startup failed", &message, true);
    }
}
