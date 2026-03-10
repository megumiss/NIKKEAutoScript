# Stop the parent process that launched this script (typically NKAS).
$parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
if ($null -ne $parent -and $parent -gt 0) {
    Stop-Process -Id $parent -Force
}
