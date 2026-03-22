# Stop NKAS process.
# Strategy:
# 1. Use NKAS_PID environment variable if available.
# 2. Search for nkas.exe or python processes running from the NKAS installation directory.

$ErrorActionPreference = "SilentlyContinue"

# Get NKAS root directory (assuming scripts/windows/stop_nkas.ps1)
$scriptPath = $MyInvocation.MyCommand.Definition
$nkasRoot = (Get-Item $scriptPath).Directory.Parent.Parent.FullName
# Escape special characters for regex
$nkasRootPattern = [regex]::Escape($nkasRoot)

# 1. Try NKAS_PID environment variable
if ($env:NKAS_PID) {
    Stop-Process -Id $env:NKAS_PID -Force
}

# 2. Find and kill nkas.exe or python running nkas from the installation folder
$processes = Get-CimInstance Win32_Process -Filter "Name = 'nkas.exe' OR Name like 'python%'"

foreach ($p in $processes) {
    # Check if nkas.exe is running from the root folder
    if ($p.Name -eq "nkas.exe" -and $p.ExecutablePath -match "^$nkasRootPattern") {
        Stop-Process -Id $p.ProcessId -Force
    }
    # Check if python is running gui.py or main.py from the root folder
    elseif ($p.Name -match "^python" -and $p.CommandLine -match "gui\.py|main\.py" -and $p.CommandLine -match $nkasRootPattern) {
        Stop-Process -Id $p.ProcessId -Force
    }
}
