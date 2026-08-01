#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod config;
mod desktop_update;

use anyhow::{Context, Result};
use backend::Backend;
use config::DesktopConfig;
use std::collections::HashMap;
use std::env;
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
    Warning,
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

    fn warning(&self, message: impl Into<String>) {
        self.send(StartupMessageKind::Warning, message.into());
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
        StartupMessageKind::Warning => "warning",
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

fn is_local_webui_url(url: &Url, port: u16) -> bool {
    url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port_or_known_default() == Some(port)
}

fn is_startup_url(url: &Url) -> bool {
    url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost")
}

fn create_window(
    app: &AppHandle,
    config: &DesktopConfig,
    reporter: StartupReporter,
) -> Result<WebviewWindow> {
    let allowed_port = config.port;
    let load_reporter = reporter.clone();
    // Apply the configured theme before first paint so the startup screen
    // matches the WebUI instead of always flashing the dark palette.  The
    // initialization script runs at document creation where
    // document.documentElement may not exist yet, so pass the value through
    // a global and let each page apply it itself.
    let theme_script = format!(
        "window.__nkasTheme={};",
        serde_json::to_string(&config.theme).unwrap_or_else(|_| "\"dark\"".into())
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
            let local = is_local_webui_url(url, allowed_port);
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
    reporter.stage("Checking desktop shell updates");
    reporter.log(format!(
        "NKAS desktop version {}",
        env!("CARGO_PKG_VERSION")
    ));
    match desktop_update::check_and_launch(config) {
        desktop_update::UpdateOutcome::Restarting => {
            reporter.complete("Desktop update installed. Restarting NKAS...");
            thread::sleep(std::time::Duration::from_millis(500));
            app.exit(0);
            return Ok(());
        }
        desktop_update::UpdateOutcome::Warning(message) => {
            reporter.warning(&message);
            show_native_message("NKAS desktop update", &message, false);
        }
        desktop_update::UpdateOutcome::NoUpdate => {
            reporter.log("Desktop shell is ready.");
        }
    }

    reporter.stage("Preparing the NKAS backend");
    let backend_reporter = reporter.clone();
    let log: backend::LogSink = Arc::new(move |message| backend_reporter.log(message));
    let backend = backend::start_and_wait(config, log)?;
    store_backend(app, backend)?;
    desktop_update::cleanup_after_success(&config.root, cleanup_helper);

    reporter.stage("Opening the application");
    reporter.complete("Startup complete. Opening NKAS...");
    thread::sleep(std::time::Duration::from_millis(350));
    let page: Url = backend::url(config.port, "/app/")
        .parse()
        .context("Invalid WebUI URL")?;
    window.navigate(page)?;
    window.show()?;
    window.set_focus()?;
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

fn post(port: u16, path: &'static str) {
    thread::spawn(move || {
        let _ = reqwest::blocking::Client::new()
            .post(backend::url(port, path))
            .send();
    });
}

fn install_shortcuts(app: &AppHandle, config: &DesktopConfig) -> Result<()> {
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
        let port = config.port;
        let _ = app
            .global_shortcut()
            .on_shortcut(shortcut, move |_app, _shortcut, event| {
                if event.state() == ShortcutState::Pressed {
                    post(port, path);
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
        .on_window_event(|window, event| {
            if window.label() == "main"
                && matches!(event, tauri::WindowEvent::CloseRequested { .. })
            {
                window.app_handle().exit(0);
            }
        })
        .setup(move |app| {
            app.manage(BackendState(Mutex::new(None)));
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

        assert!(is_local_webui_url(&local, 12271));
        assert!(!is_local_webui_url(&userinfo_bypass, 12271));
        assert!(!is_local_webui_url(&wrong_port, 12271));
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
