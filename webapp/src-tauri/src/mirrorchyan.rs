use anyhow::{Context, Result};
use serde::Deserialize;
use std::fs;
use std::path::Path;
use std::time::Duration;

#[derive(Deserialize)]
struct Response {
    code: i64,
    data: Option<serde_json::Value>,
}

#[derive(Deserialize)]
pub struct Release {
    pub version_name: String,
    #[serde(default)]
    pub release_note: String,
    pub url: Option<String>,
    pub sha256: Option<String>,
    pub filesize: Option<u64>,
}

pub fn latest(root: &Path) -> Result<Release> {
    if !cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        anyhow::bail!("MirrorChyan desktop updates only support Windows x64");
    }
    let path = root.join("config/.security/mirrorchyan.cdk");
    let cdk = match fs::read_to_string(path) {
        Ok(value) => value.trim().to_owned(),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(_) => anyhow::bail!("Unable to read the private MirrorChyan CDK file"),
    };
    if cdk.len() > 256
        || !cdk.bytes().all(|value| value.is_ascii_alphanumeric() || matches!(value, b'_' | b'-'))
    {
        anyhow::bail!("Invalid MirrorChyan CDK file; save it again on the Deploy page");
    }
    let client = reqwest::blocking::Client::builder()
        .user_agent(concat!("NKAS/", env!("CARGO_PKG_VERSION")))
        .build()?;
    let mut query = vec![("os", "win"), ("arch", "x64"), ("channel", "stable"), ("user_agent", "nkas")];
    if !cdk.is_empty() {
        query.push(("cdk", &cdk));
    }
    let response = client
        .get("https://mirrorchyan.com/api/resources/NIKKEAutoScript/latest")
        .query(&query)
        .timeout(Duration::from_secs(20))
        .send()
        .map_err(|error| error.without_url())
        .context("MirrorChyan version request failed")?;
    let status = response.status();
    // Non-2xx responses can still contain the CDK-specific business error.
    let body: Response = response
        .json()
        .map_err(|error| error.without_url())
        .context("Invalid MirrorChyan version response")?;
    if body.code != 0 || !status.is_success() {
        anyhow::bail!("{}", error_message(body.code, status));
    }
    let release: Release = serde_json::from_value(body.data.context("MirrorChyan response is missing release data")?)
        .map_err(|_| anyhow::anyhow!("Invalid MirrorChyan release data"))?;
    anyhow::ensure!(!release.version_name.is_empty(), "MirrorChyan response is missing a version");
    Ok(release)
}

fn error_message(code: i64, status: reqwest::StatusCode) -> String {
    let message = match code {
        7001 => "Mirror酱 CDK 已过期，请更换或续费。",
        7002 => "Mirror酱 CDK 无效，请检查输入。",
        7003 => "Mirror酱 CDK 今日下载次数已达上限，请明日再试或切换更新源。",
        7004 => "Mirror酱 CDK 类型与本资源不匹配。",
        7005 => "Mirror酱 CDK 已被封禁，请联系 Mirror酱售后。",
        8001 => "Mirror酱当前平台或通道暂无可用资源，请切换更新源。",
        8009 => "此 Mirror酱 CDK 仅支持流程测试，不支持实际下载。",
        _ => "Mirror酱服务错误，请重新检查更新或切换更新源。",
    };
    format!("{message} (HTTP {status}, code {code})")
}

pub fn download_error(response: reqwest::blocking::Response) -> anyhow::Error {
    let status = response.status();
    let code = response.json::<serde_json::Value>().ok()
        .and_then(|data| data.get("code").and_then(serde_json::Value::as_i64));
    anyhow::anyhow!("{}", error_message(code.unwrap_or(-1), status))
}
