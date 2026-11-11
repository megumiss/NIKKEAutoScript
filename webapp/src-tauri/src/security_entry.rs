use anyhow::{Context, Result};
use std::fs;
use std::net::{IpAddr, UdpSocket};

use crate::{backend, config::DesktopConfig};

fn local_host(host: &str) -> bool {
    if host == "localhost" {
        return true;
    }
    host.parse::<IpAddr>()
        .map(|address| address.is_loopback() || UdpSocket::bind((address, 0)).is_ok())
        .unwrap_or(false)
}

fn valid_key(value: &str) -> bool {
    value.len() == 43
        && value.bytes().all(|ch| ch.is_ascii_alphanumeric() || ch == b'_' || ch == b'-')
}

pub fn key(config: &DesktopConfig) -> Result<Option<String>> {
    // Never forward a local installation's key to a configured remote server.
    if !local_host(&config.host) {
        return Ok(None);
    }
    let yaml = fs::read_to_string(config.root.join("config/deploy.yaml"))
        .context("Unable to read local deployment settings")?;
    let settings: serde_yaml::Value = serde_yaml::from_str(&yaml)
        .map_err(|_| anyhow::anyhow!("Invalid local deployment settings"))?;
    if settings["Deploy"]["Webui"]["SecurityEntryEnabled"].as_bool() != Some(true) {
        return Ok(None);
    }
    let value = fs::read_to_string(config.root.join("config/.security/entry.key"))
        .context("Unable to read the local security entry")?;
    let value = value.trim();
    anyhow::ensure!(valid_key(value), "Invalid local security entry");
    Ok(Some(value.to_owned()))
}

pub fn page(config: &DesktopConfig) -> Result<tauri::Url> {
    let path = key(config)?.map(|key| format!("/entry/{key}")).unwrap_or_else(|| "/app/".into());
    backend::url(&config.host, config.port, &path).parse().context("Invalid local WebUI address")
}

pub fn authorize(request: reqwest::blocking::RequestBuilder, config: &DesktopConfig) -> Result<reqwest::blocking::RequestBuilder> {
    Ok(match key(config)? {
        Some(key) => request.bearer_auth(key),
        None => request,
    })
}
