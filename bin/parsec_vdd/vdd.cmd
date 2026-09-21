@echo off
setlocal

set app_exe=%~dp0ParsecDisplay.exe
set parent_exe=%~dp0..\ParsecDisplay.exe

if exist "%app_exe%" (
    start /b /wait "" "%app_exe%" -cli %*
) else if exist "%parent_exe%" (
    start /b /wait "" "%parent_exe%" -cli %*
) else (
    echo ParsecDisplay.exe does not exist.
)

endlocal
