# Stop NKAS process that launched this script.
$nkasPid = $env:NKAS_PID
if ($nkasPid) {
    try {
        Stop-Process -Id $nkasPid -Force
        exit 0
    } catch {
        # Fallback below
    }
}

# Fallback: walk up the parent chain and try to find a python process running gui.py/main.py
$current = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID")
while ($null -ne $current) {
    $ppid = $current.ParentProcessId
    if (-not $ppid -or $ppid -le 0) { break }
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$ppid"
    if ($null -eq $parent) { break }
    if ($parent.Name -match "python" -and $parent.CommandLine -match "gui\\.py|main\\.py") {
        Stop-Process -Id $parent.ProcessId -Force
        exit 0
    }
    $current = $parent
}
