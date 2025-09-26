@echo off
REM 设置默认端口
set "port=12271"

REM 如果传入了第一个参数（端口），则使用该参数覆盖默认值
if not "%~1"=="" set "port=%~1"

REM 获取第二个参数作为 config_name
set "config_name=%~2"

REM 切换到 exe 所在目录
cd /d "%~dp0..\.."

REM 启动 exe
start "" "nkas.exe"

REM 等待 10 秒，确保程序启动
echo Waiting for the program to start...
timeout /t 10 /nobreak >nul

REM 检查是否传入了 config_name
if not "%config_name%"=="" (
    REM --- 如果传入了 config_name，则调用 API ---
    echo Starting task via API for config '%config_name%' on port %port%...
    powershell -ExecutionPolicy Bypass -Command "Invoke-RestMethod -Method POST -Uri http://127.0.0.1:%port%/api/%config_name%/start"
) else (
    REM --- 如果未传入 config_name，则发送 F9 快捷键 ---
    echo Activating window and sending F9 key...
    powershell -ExecutionPolicy Bypass -Command "$wshell = New-Object -ComObject wscript.shell; if ($wshell.AppActivate('NIKKEAutoScript')) { Start-Sleep -Milliseconds 500; $wshell.SendKeys('{F9}') }"
)

echo Script finished.