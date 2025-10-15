@echo off
@REM REM 设置默认端口
@REM set "port=12271"

@REM REM 如果传入了第一个参数（端口），则使用该参数覆盖默认值
@REM if not "%~1"=="" set "port=%~1"

@REM REM 获取第二个参数作为 config_name
@REM set "config_name=%~2"

REM 切换到 exe 所在目录
cd /d "%~dp0..\.."

REM 启动 exe
start "" "nkas.exe"

REM 等待程序启动，最多重试 3 次，每次等待 30 秒
set "retry=0"
set "max_retry=3"

:WAIT_LOOP
set /a retry+=1
echo Waiting for the program to start... (Attempt %retry% of %max_retry%)
timeout /t 30 /nobreak >nul

REM 检查程序是否已启动（用 tasklist 判断）
tasklist | find /i "nkas.exe" >nul
if %errorlevel%==0 (
    echo Program started successfully!
    goto :CONTINUE
)

if %retry% lss %max_retry% (
    echo Program not detected, retrying...
    goto :WAIT_LOOP
) else (
    echo Failed to detect nkas.exe after %max_retry% attempts.
    exit /b 1
)

@REM REM 等待 10 秒，确保程序启动
@REM echo Waiting for the program to start...
@REM timeout /t 10 /nobreak >nul

@REM REM 检查是否传入了 config_name
@REM if not "%config_name%"=="" (
@REM     REM --- 如果传入了 config_name，则调用 API ---
@REM     echo Starting task via API for config '%config_name%' on port %port%...
@REM     powershell -ExecutionPolicy Bypass -Command "Invoke-RestMethod -Method POST -Uri http://127.0.0.1:%port%/api/%config_name%/start"
@REM ) else (
@REM     REM --- 如果未传入 config_name，则发送 F9 快捷键 ---
@REM     echo Activating window and sending F9 key...
@REM     powershell -ExecutionPolicy Bypass -Command "$wshell = New-Object -ComObject wscript.shell; $wshell.AppActivate('NIKKEAutoScript'); Start-Sleep -Milliseconds 500; $wshell.SendKeys('{F9}')"
@REM )

:CONTINUE
echo Script finished.