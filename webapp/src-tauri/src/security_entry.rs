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

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn local_entry_is_reread_after_rotation_and_never_sent_to_remote_hosts() {
        let unique = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let root = std::env::temp_dir().join(format!("nkas-entry-{}-{unique}", std::process::id()));
        fs::create_dir_all(root.join("config/.security")).unwrap();
        let config_file = root.join("config/deploy.yaml");
        let key_file = root.join("config/.security/entry.key");
        fs::write(&config_file, "Deploy:\n  Python:\n    PythonExecutable: python\n  Webui:\n    SecurityEntryEnabled: false\n").unwrap();
        let mut config = crate::config::load(root.clone()).unwrap();
        assert_eq!(page(&config).unwrap().path(), "/app/");
        assert!(key(&config).unwrap().is_none());
        fs::write(&config_file, "Deploy:\n  Webui:\n    SecurityEntryEnabled: true\n").unwrap();
        fs::write(&key_file, "a".repeat(43)).unwrap();
        assert_eq!(page(&config).unwrap().path(), format!("/entry/{}", "a".repeat(43)));
        fs::write(&key_file, "b".repeat(43)).unwrap();
        let request = authorize(reqwest::blocking::Client::new().get("http://127.0.0.1/api/test"), &config)
            .unwrap().build().unwrap();
        assert_eq!(request.headers()["authorization"], format!("Bearer {}", "b".repeat(43)));
        config.host = "remote.example".into();
        fs::remove_file(&key_file).unwrap();
        assert!(key(&config).unwrap().is_none());
        assert_eq!(page(&config).unwrap().path(), "/app/");
        config.host = "127.0.0.1".into();
        assert!(key(&config).is_err());
        fs::write(&config_file, "Deploy:\n  Webui:\n    SecurityEntryEnabled: false\n").unwrap();
        assert!(key(&config).unwrap().is_none());
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn local_credentials_reject_remote_names_and_malformed_keys() {
        assert!(local_host("127.0.0.1"));
        assert!(local_host("::1"));
        assert!(!local_host("remote.example"));
        assert!(!local_host("127.0.0.1.evil.example"));
        assert!(valid_key(&"a".repeat(43)));
        assert!(!valid_key(&"a".repeat(42)));
        assert!(!valid_key(&format!("{}\n", "a".repeat(42))));
    }
}
