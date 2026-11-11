use anyhow::{Context, Result};
use serde::Deserialize;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

fn default_webui_host() -> String {
    // Mirrors deploy/config.py; resolved to a connectable address by load().
    "0.0.0.0".into()
}
fn default_webui_port() -> u16 {
    12271
}
fn default_dpi_scaling() -> bool {
    true
}
fn default_theme() -> String {
    "light".into()
}
fn default_desktop_update_manifest() -> String {
    // Desktop updates are fetched from the official VPS by default.
    // Override Deploy.Update.DesktopUpdateManifest in config/deploy.yaml to use another source.
    "https://nkas.megumiss.top/releases/latest/nkas-desktop.json".into()
}

#[derive(Debug, Deserialize)]
struct RootConfig {
    #[serde(rename = "Deploy")]
    deploy: DeployConfig,
}

#[derive(Debug, Deserialize)]
struct DeployConfig {
    #[serde(rename = "Python")]
    python: PythonConfig,
    #[serde(rename = "Update", default)]
    update: UpdateConfig,
    #[serde(rename = "Webui", default)]
    webui: WebuiConfig,
}

#[derive(Debug, Deserialize)]
struct UpdateConfig {
    #[serde(
        rename = "DesktopUpdateManifest",
        default = "default_desktop_update_manifest"
    )]
    desktop_update_manifest: String,
}

impl Default for UpdateConfig {
    fn default() -> Self {
        Self {
            desktop_update_manifest: default_desktop_update_manifest(),
        }
    }
}

#[derive(Debug, Deserialize)]
struct PythonConfig {
    #[serde(rename = "PythonExecutable")]
    executable: PathBuf,
}

#[derive(Debug, Default, Deserialize)]
struct WebuiConfig {
    #[serde(rename = "WebuiHost", default = "default_webui_host")]
    host: String,
    #[serde(rename = "WebuiPort", default = "default_webui_port")]
    port: u16,
    #[serde(rename = "DpiScaling", default = "default_dpi_scaling")]
    dpi_scaling: bool,
    #[serde(rename = "HardwareAcceleration", default)]
    hardware_acceleration: bool,
    #[serde(rename = "Theme", default = "default_theme")]
    theme: String,
}

#[derive(Debug, Clone)]
pub struct DesktopConfig {
    pub root: PathBuf,
    pub python: PathBuf,
    /// Address the shell uses to reach the backend, derived from WebuiHost.
    /// Stored without IPv6 brackets; backend::url adds them when needed.
    pub host: String,
    pub port: u16,
    pub desktop_update_manifest: String,
    pub dpi_scaling: bool,
    pub hardware_acceleration: bool,
    pub theme: String,
    pub shortcuts_enabled: bool,
    pub shortcuts: HashMap<String, String>,
}

pub fn locate_root(current_dir: &Path, executable: &Path) -> Result<PathBuf> {
    let candidates = current_dir.ancestors().take(5).chain(
        executable
            .parent()
            .into_iter()
            .flat_map(|path| path.ancestors().take(6)),
    );
    for candidate in candidates {
        if candidate.join("gui.py").is_file() && candidate.join("config/deploy.yaml").is_file() {
            // Keep the path spelling used to launch the app. On Windows,
            // canonicalize() converts mapped drives into UNC paths, which
            // the Python deployment layer cannot safely normalize.
            return Ok(candidate.to_path_buf());
        }
    }
    anyhow::bail!("Unable to locate the NKAS root containing gui.py and config/deploy.yaml")
}

/// Resolve WebuiHost (the bind address) into an address the shell can
/// connect to.  Wildcard binds are reachable via loopback; anything else is
/// a local interface address and used as-is.  Mirrors the mapping in
/// module/webui/remote_access.py.
pub fn resolve_connect_host(host: &str) -> String {
    let host = host.trim().trim_matches(|c| c == '[' || c == ']');
    match host {
        "" | "0.0.0.0" => "127.0.0.1".into(),
        "::" | "::0" | "0:0:0:0:0:0:0:0" => "::1".into(),
        _ => host.to_string(),
    }
}

pub fn load(root: PathBuf) -> Result<DesktopConfig> {
    let config_path = root.join("config/deploy.yaml");
    let content = fs::read_to_string(&config_path)
        .with_context(|| format!("Unable to read {}", config_path.display()))?;
    let raw: RootConfig = serde_yaml::from_str(&content)
        .with_context(|| format!("Unable to parse {}", config_path.display()))?;
    let python = if raw.deploy.python.executable.is_absolute() {
        raw.deploy.python.executable
    } else {
        root.join(raw.deploy.python.executable)
    };
    let (shortcuts_enabled, shortcuts) = load_shortcuts(&root);
    Ok(DesktopConfig {
        shortcuts_enabled,
        shortcuts,
        root,
        python,
        host: resolve_connect_host(&raw.deploy.webui.host),
        port: raw.deploy.webui.port,
        desktop_update_manifest: raw.deploy.update.desktop_update_manifest,
        dpi_scaling: raw.deploy.webui.dpi_scaling,
        hardware_acceleration: raw.deploy.webui.hardware_acceleration,
        theme: raw.deploy.webui.theme,
    })
}

fn load_shortcuts(root: &Path) -> (bool, HashMap<String, String>) {
    let mut shortcuts = HashMap::from([
        ("UPDATE".into(), "F8".into()),
        ("START".into(), "F9".into()),
        ("STOP".into(), "F10".into()),
        ("RESTART".into(), "F11".into()),
        ("ROTATE".into(), "Ctrl+F12".into()),
        ("DEV_TOOLS".into(), "Ctrl+Shift+I".into()),
        ("REFRESH".into(), "Ctrl+R".into()),
        ("HARD_REFRESH".into(), "Ctrl+Shift+R".into()),
    ]);
    let Ok(content) = fs::read_to_string(root.join("config/shortcuts.yaml")) else {
        return (true, shortcuts);
    };
    let mut enabled = true;
    // ENABLED 是布尔值，不能直接按 HashMap<String, String> 解析
    if let Ok(values) = serde_yaml::from_str::<HashMap<String, serde_yaml::Value>>(&content) {
        for (key, value) in values {
            if key == "ENABLED" {
                enabled = value.as_bool().unwrap_or(true);
                continue;
            }
            if let Some(value) = value.as_str() {
                if shortcuts.contains_key(&key) && !value.trim().is_empty() {
                    shortcuts.insert(key, value.to_string());
                }
            }
        }
    }
    (enabled, shortcuts)
}

/// 交给 WebView2 的 `--disable-features` 列表。
///
/// 前三项是 wry 的默认值。wry 只在没收到 `additional_browser_args` 时才用它自己的默认串，
/// 一旦我们传值就整串替换掉（Tauri 的 `additional_browser_args` 文档同样提示了这点），
/// 所以这三项必须自己补齐，否则迷你菜单 / PDF 工具条 / SmartScreen 会回来。
///
/// 第四项是本项目要关的：Windows 上 Chromium 的原生窗口遮挡检测会把被其他窗口盖住的
/// WebView 判成隐藏并停止合成，表现为画面预览有新帧、屏幕却不动，要点一下窗口才刷新。
/// 它只能写在这个列表里，没有别的开关能替代。
pub const DISABLE_FEATURES: &str = concat!(
    "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection,",
    "CalculateNativeWinOcclusion"
);

fn merge_disable_features(inherited: &str) -> String {
    let mut arguments = Vec::new();
    let mut features = Vec::new();
    let mut seen_features = HashSet::new();
    let mut position = None;
    for argument in inherited.split_whitespace() {
        if let Some(value) = argument.strip_prefix("--disable-features=") {
            if position.is_none() {
                position = Some(arguments.len());
            }
            for feature in value
                .split(',')
                .map(str::trim)
                .filter(|feature| !feature.is_empty())
            {
                if seen_features.insert(feature.to_string()) {
                    features.push(feature.to_string());
                }
            }
        } else {
            arguments.push(argument.to_string());
        }
    }
    for feature in DISABLE_FEATURES
        .trim_start_matches("--disable-features=")
        .split(',')
        .map(str::trim)
        .filter(|feature| !feature.is_empty())
    {
        if seen_features.insert(feature.to_string()) {
            features.push(feature.to_string());
        }
    }
    arguments.insert(
        position.unwrap_or(arguments.len()),
        format!("--disable-features={}", features.join(",")),
    );
    arguments.join(" ")
}

pub fn webview_arguments(config: &DesktopConfig, inherited: Option<&str>) -> Option<String> {
    let mut args = merge_disable_features(inherited.unwrap_or_default().trim());
    let mut append = |value: &str| {
        if !args.split_whitespace().any(|item| item == value) {
            if !args.is_empty() {
                args.push(' ');
            }
            args.push_str(value);
        }
    };
    if !config.hardware_acceleration {
        append("--disable-gpu");
    }
    if !config.dpi_scaling {
        append("--force-device-scale-factor=1");
    }
    (!args.is_empty()).then_some(args)
}
