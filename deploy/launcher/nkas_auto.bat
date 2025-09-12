@echo off
REM 切换到 exe 所在目录
cd /d "%~dp0..\.."

REM 启动 exe
start "" "nkas.exe"

REM 等待 10 秒，确保程序启动
timeout /t 10 /nobreak >nul

REM 用 PowerShell 激活窗口并发送 F9
powershell -ExecutionPolicy Bypass -Command "$wshell = New-Object -ComObject wscript.shell; $wshell.AppActivate('NIKKEAutoScript'); Start-Sleep -Milliseconds 500; $wshell.SendKeys('{F9}')"
