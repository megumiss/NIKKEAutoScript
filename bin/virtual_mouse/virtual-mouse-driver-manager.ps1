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
    $proc = Start-Process -FilePath $installer -ArgumentList $argument -WorkingDirectory (Split-Path $installer) -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "virtual_driver_manager.exe $argument exited with code $($proc.ExitCode)"
    }
    $message = if ($Action -eq 'install') { 'Virtual mouse driver installed.' } else { 'Virtual mouse driver uninstalled.' }
    Write-Record @{ status = 'success'; action = $Action; message = $message }
    exit 0
} catch {
    Write-Record @{ status = 'error'; action = $Action; message = $_.Exception.Message }
    exit 1
}
