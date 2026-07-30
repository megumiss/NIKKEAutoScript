use crate::config::DesktopConfig;
use anyhow::{Context, Result};
use serde::Deserialize;
use std::process::{Child, Command, Stdio};
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

pub fn start_and_wait(config: &DesktopConfig) -> Result<Backend> {
    if health(config.port) {
        return Ok(Backend::external());
    }
    if !config.python.is_file() {
        anyhow::bail!("Python executable not found: {}", config.python.display());
    }
    run_prepare(config)?;
    if health(config.port) {
        return Ok(Backend::external());
    }
    let port = config.port.to_string();
    let mut command = Command::new(&config.python);
    command
        .current_dir(&config.root)
        .args(["gui.py", "--port", port.as_str(), "--electron"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    let child = command
        .spawn()
        .with_context(|| format!("Unable to start {}", config.python.display()))?;
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
    while started.elapsed() < Duration::from_secs(60) {
        if health(config.port) {
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
        thread::sleep(Duration::from_millis(250));
    }
    anyhow::bail!(
        "Timed out waiting 60 seconds for {}",
        url(config.port, "/api/system/status")
    )
}

fn run_prepare(config: &DesktopConfig) -> Result<()> {
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
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    let output = command.output().with_context(|| {
        format!(
            "Unable to run startup update with {}",
            config.python.display()
        )
    })?;
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let details = if !stderr.is_empty() { stderr } else { stdout };
    anyhow::bail!(
        "Startup update failed ({}){}{}",
        output.status,
        if details.is_empty() { "" } else { ": " },
        details
    )
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
    #[test]
    fn backend_urls_are_loopback_only() {
        assert_eq!(url(12271, "/app/"), "http://127.0.0.1:12271/app/");
    }
}
