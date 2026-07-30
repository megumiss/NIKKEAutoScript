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
use std::sync::Mutex;
use std::thread;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

#[cfg(windows)]
use windows::core::HSTRING;
#[cfg(windows)]
use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};

struct BackendState(Mutex<Backend>);

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

fn create_window(app: &AppHandle, config: &DesktopConfig) -> Result<WebviewWindow> {
    let page = backend::url(config.port, "/app/");
    let allowed_port = config.port;
    WebviewWindowBuilder::new(
        app,
        "main",
        WebviewUrl::External(page.parse().context("Invalid WebUI URL")?),
    )
    .title("NKAS")
    .inner_size(1280.0, 880.0)
    .min_inner_size(900.0, 640.0)
    .visible(false)
    .on_navigation(move |url| {
        let target = url.as_str();
        let local = is_local_webui_url(url, allowed_port);
        if !local && matches!(url.scheme(), "http" | "https") {
            let _ = open::that_detached(target);
        }
        local
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
            match desktop_update::check_and_launch(&app_config) {
                desktop_update::UpdateOutcome::Restarting => {
                    app.handle().exit(0);
                    return Ok(());
                }
                desktop_update::UpdateOutcome::Warning(message) => {
                    show_native_message("NKAS desktop update", &message, false);
                }
                desktop_update::UpdateOutcome::NoUpdate => {}
            }
            let backend = backend::start_and_wait(&app_config)?;
            app.manage(BackendState(Mutex::new(backend)));
            install_tray(app.handle())?;
            install_shortcuts(app.handle(), &app_config)?;
            let window = create_window(app.handle(), &app_config)?;
            window.show()?;
            window.set_focus()?;
            desktop_update::cleanup_after_success(&app_config.root, cleanup_helper.clone());
            Ok(())
        })
        .build(tauri::generate_context!())?;

    app.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = app.try_state::<BackendState>() {
                if let Ok(mut backend) = state.0.lock() {
                    backend.shutdown();
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
