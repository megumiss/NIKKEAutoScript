<#
.SYNOPSIS
Install or uninstall the bundled virtual mouse HID driver (driver_hid_virtual).

.DESCRIPTION
- The caller is expected to already be elevated: NKAS runs as administrator and
  invokes this script from that process, so the script never requests elevation
  itself (no UAC prompt, no re-launch).
- install: copies the bundled depot into
  %ProgramData%\LGHUB\depots\<DepotId>\driver_hid_virtual and then runs
  virtual_driver_manager.exe --install from that directory. The depot path is
  fixed by the driver package layout, not a preference.
- uninstall: runs virtual_driver_manager.exe --uninstall, preferring the copy
  inside the installed directory and falling back to the bundled one.
- With -Json, machine-readable JSON lines are printed for the NKAS backend.

.EXAMPLE
powershell.exe -ExecutionPolicy Bypass -File .\virtual-mouse-driver-manager.ps1 -Action install -Json -Silent
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('install', 'uninstall')]
    [string]$Action,

    [switch]$Json,
    [switch]$Silent
)

$ErrorActionPreference = 'Stop'
$DepotId = '869589'
$SourceDir = Join-Path $PSScriptRoot 'driver_hid_virtual'
$TargetDir = Join-Path $env:ProgramData "LGHUB\depots\$DepotId\driver_hid_virtual"

function Write-Record {
    param([hashtable]$Record)
    if ($Json) {
        ($Record | ConvertTo-Json -Compress) | Write-Output
    } else {
        Write-Host $Record.message
    }
}

#----------------------------------------------------------------------
# ADMINISTRATOR PRIVILEGES
# NKAS runs as administrator, so this script is always called from an elevated
# process and never elevates itself. Report clearly if that precondition is
# broken (e.g. the script invoked by hand from a normal console).
#----------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]'Administrator')
if (-not $isAdmin) {
    Write-Record @{ status = 'error'; action = $Action; message = 'Administrator privileges are required. Restart NKAS as administrator.' }
    exit 1
}

#----------------------------------------------------------------------
# ACTION
#----------------------------------------------------------------------
# The install/uninstall sequence must be serialized: concurrent runs (two NKAS
# instances, retried browser requests) would interleave installer runs against
# the same driver stack. A named mutex serializes callers across processes.
$mutex = New-Object System.Threading.Mutex($false, 'NKASVirtualMouseDriverManager')
$mutexHeld = $false
try {
    try {
        $mutexHeld = $mutex.WaitOne([TimeSpan]::FromMinutes(3))
    } catch [System.Threading.AbandonedMutexException] {
        # A previous holder died without releasing; the wait still owns the mutex.
        $mutexHeld = $true
    }
    if (-not $mutexHeld) {
        Write-Record @{ status = 'error'; action = $Action; message = 'Another driver install/uninstall is still running. Retry after it finishes.' }
        exit 1
    }
    try {
        $bundled = Join-Path $SourceDir 'virtual_driver_manager.exe'
        if (-not (Test-Path $bundled)) {
            throw "Bundled driver installer not found: $bundled"
        }
        if ($Action -eq 'install') {
            New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
            Copy-Item -Path (Join-Path $SourceDir '*') -Destination $TargetDir -Force
        }
        # Uninstall uses the copy inside the installed directory (its manifest lives
        # next to it), falling back to the bundled copy when that directory is gone.
        $installer = Join-Path $TargetDir 'virtual_driver_manager.exe'
        if (-not (Test-Path $installer)) {
            $installer = $bundled
        }

        $argument = "--$Action"
        # The manager is a console program: redirect its output explicitly, otherwise the
        # child inherits this console and its stdout never reaches the caller's pipe
        # (Python runs us with capture_output), leaving only an exit code to report.
        # Names carry this process id so concurrent runs can never share redirection targets.
        $stdoutFile = Join-Path $env:TEMP "nkas-virtual-mouse-$Action-$PID.out.txt"
        $stderrFile = Join-Path $env:TEMP "nkas-virtual-mouse-$Action-$PID.err.txt"
        Remove-Item $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
        $proc = Start-Process -FilePath $installer -ArgumentList $argument `
            -WorkingDirectory (Split-Path $installer) -Wait -PassThru `
            -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile
        $log = @()
        foreach ($file in @($stdoutFile, $stderrFile)) {
            if (Test-Path $file) {
                $log += Get-Content -Path $file -Encoding utf8 | Where-Object { $_.Trim() -ne '' }
            }
        }
        Remove-Item $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
        # Only the tail is reported: the log only needs to show why the run failed.
        $tail = (($log | Select-Object -Last 20) -join "`n").Trim()
        # The manager reports a deferred driver swap as "NEED_REBOOT: <value>" (DiInstallDriverW
        # returning ERROR_SUCCESS_REBOOT_REQUIRED). Without parsing it, a successful install that
        # only takes effect after a restart is indistinguishable from a plain success, and the
        # device stays absent until the reboot - which reads as "install failed".
        $rebootRequired = $false
        foreach ($line in $log) {
            if ($line -match 'NEED_REBOOT:\s*(\S+)') {
                $flag = $Matches[1].Trim().TrimEnd([char]'.').ToLower()
                $rebootRequired = @('false', 'no', '0', 'off') -notcontains $flag
            }
        }
        if ($proc.ExitCode -ne 0) {
            $detail = if ($tail) { ": $tail" } else { '' }
            throw "virtual_driver_manager.exe $argument exited with code $($proc.ExitCode)$detail"
        }
        $message = if ($Action -eq 'install') {
            if ($rebootRequired) { 'Virtual mouse driver installed. A reboot is required for it to take effect.' }
            else { 'Virtual mouse driver installed.' }
        } else {
            'Virtual mouse driver uninstalled.'
        }
        Write-Record @{ status = 'success'; action = $Action; message = $message; reboot_required = $rebootRequired; log = $tail }
        exit 0
    } catch {
        Write-Record @{ status = 'error'; action = $Action; message = $_.Exception.Message }
        exit 1
    }
} finally {
    if ($mutexHeld) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
