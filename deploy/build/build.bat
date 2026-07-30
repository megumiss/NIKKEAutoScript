@echo off
setlocal enabledelayedexpansion

REM ============================================
REM 构建 NIKKEAutoScript 环境脚本
REM 作者: Master Megumi
REM 说明:
REM  1. 自动克隆主项目与构建项目
REM  2. 从构建项目复制 toolkit 到主项目
REM  3. 将 Tauri Release 产物复制到项目根目录
REM ============================================

echo ==================================================
echo NIKKEAutoScript Build Script
echo ==================================================

REM =============================
REM Step 0：删除旧目录
REM =============================
echo Step 0/8: Removing existing directories...
if exist NIKKEAutoScript (
    echo Found existing NIKKEAutoScript directory, deleting...
    rd /s /q NIKKEAutoScript
    if exist NIKKEAutoScript (
        echo Error: Failed to delete NIKKEAutoScript directory
        pause
        goto :end
    )
    echo Old NIKKEAutoScript directory removed successfully
)

if exist NIKKEAutoScriptBuild (
    echo Found existing NIKKEAutoScriptBuild directory, deleting...
    rd /s /q NIKKEAutoScriptBuild
    if exist NIKKEAutoScriptBuild (
        echo Error: Failed to delete NIKKEAutoScriptBuild directory
        pause
        goto :end
    )
    echo Old NIKKEAutoScriptBuild directory removed successfully
)

REM =============================
REM Step 1：克隆仓库
REM =============================
echo Step 1/8: Cloning repositories...

echo Cloning main repository...
git clone --depth 1 https://github.com/megumiss/NIKKEAutoScript.git
if not exist NIKKEAutoScript (
    echo Error: Git clone main failed
    pause
    goto :end
)

echo Cloning build repository...
git clone --depth 1 https://github.com/megumiss/NIKKEAutoScriptBuild.git
if not exist NIKKEAutoScriptBuild (
    echo Error: Git clone build failed
    pause
    goto :end
)

if exist NIKKEAutoScript\.git (
    echo Removing .git folder from main repository...
    rd /s /q NIKKEAutoScript\.git
) else (
    echo Warning: .git folder not found in NIKKEAutoScript
)

REM =============================
REM Step 2：构建 WebUI 和 Tauri 桌面壳
REM =============================
echo Step 2/8: Building WebUI and Tauri shell...
where node >nul 2>nul || (echo Error: Node.js 20+ is required & goto :end)
where yarn >nul 2>nul || (echo Error: Yarn is required & goto :end)
where cargo >nul 2>nul || if exist "%USERPROFILE%\.cargo\bin\cargo.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
where rustc >nul 2>nul || (echo Error: stable Rust is required from https://rustup.rs & goto :end)
where cargo >nul 2>nul || (echo Error: Cargo is required & goto :end)
where rustup >nul 2>nul || (echo Error: rustup with the stable MSVC toolchain is required & goto :end)

set "NODE_MAJOR="
for /f "delims=" %%v in ('node -p "process.versions.node.split('.')[0]"') do set "NODE_MAJOR=%%v"
if not defined NODE_MAJOR (echo Error: Unable to determine Node.js version & goto :end)
if !NODE_MAJOR! LSS 20 (echo Error: Node.js 20+ is required, found major version !NODE_MAJOR! & goto :end)

set "RUST_TOOLCHAIN="
for /f "tokens=1" %%v in ('rustup show active-toolchain 2^>nul') do set "RUST_TOOLCHAIN=%%v"
if not defined RUST_TOOLCHAIN (echo Error: Unable to determine the active Rust toolchain & goto :end)
echo !RUST_TOOLCHAIN! | findstr /b /i "stable-" >nul
if errorlevel 1 (echo Error: The active Rust toolchain must be stable, found !RUST_TOOLCHAIN! & goto :end)

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "VSINSTALL="
if exist "!VSWHERE!" for /f "usebackq delims=" %%v in (`"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%v"
if not defined VSINSTALL if exist "%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" set "VSINSTALL=%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools"
if not defined VSINSTALL if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" set "VSINSTALL=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools"
if not defined VSINSTALL (echo Error: Windows C++ Build Tools were not found & goto :end)
if not exist "!VSINSTALL!\VC\Auxiliary\Build\vcvars64.bat" (echo Error: vcvars64.bat was not found & goto :end)
call "!VSINSTALL!\VC\Auxiliary\Build\vcvars64.bat" >nul
where cl.exe >nul 2>nul || (echo Error: cl.exe was not configured by the C++ Build Tools & goto :end)
where link.exe >nul 2>nul || (echo Error: link.exe was not configured by the C++ Build Tools & goto :end)
set "SDKLIBROOT=%ProgramFiles(x86)%\Windows Kits\10\Lib"
if not exist "!SDKLIBROOT!" set "SDKLIBROOT=%ProgramFiles%\Windows Kits\10\Lib"
set "KERNEL32LIB="
if exist "!SDKLIBROOT!" for /r "!SDKLIBROOT!" %%v in (kernel32.lib) do set "KERNEL32LIB=%%v"
if not defined KERNEL32LIB (echo Error: Windows 10/11 SDK libraries were not found; kernel32.lib is required & goto :end)

cd NIKKEAutoScript\webui
call yarn install --frozen-lockfile
if errorlevel 1 (echo Error: Failed to install WebUI dependencies & goto :end)
call yarn run build
if errorlevel 1 (echo Error: Failed to build WebUI & goto :end)
if not exist dist\index.html (echo Error: WebUI build output is missing & goto :end)

cd ..\webapp

echo Installing Node.js dependencies...
call yarn install --frozen-lockfile
if errorlevel 1 (
    echo Error: Failed to install Node.js dependencies
    echo Please check Node.js and Yarn installation
    pause
    goto :end
)

echo Testing Tauri shell...
call yarn test
if errorlevel 1 (echo Error: Tauri tests failed & goto :end)

echo Checking Tauri shell...
call yarn run check
if errorlevel 1 (echo Error: Tauri cargo check failed & goto :end)

echo Building Tauri shell with Yarn...
call yarn run compile
if errorlevel 1 (
    echo Error: Yarn run compile failed
    echo Possible causes:
    echo   1. Missing Node.js dependencies
    echo   2. Build script errors in package.json
    pause
    goto :end
)

if not exist src-tauri\target\release\nkas.exe (
    echo Error: Build output not found at webapp\src-tauri\target\release\nkas.exe
    pause
    goto :end
)

echo Copying build output to root directory...
copy /y "src-tauri\target\release\nkas.exe" "..\nkas.exe" >nul
cd ..
if exist nkas.exe (
    echo Tauri executable copied to the project root
) else (
    echo Error: Failed to copy nkas.exe to the project root
    pause
    goto :end
)

REM =============================
REM Step 3：验证轻量桌面壳
REM =============================
echo Step 3/8: Verifying Tauri shell...
if not exist nkas.exe (echo Error: root nkas.exe is missing & goto :end)

REM =============================
REM Step 4：清理 webapp artifacts
REM =============================
echo Step 4/8: Cleaning webapp artifacts...
cd webapp
if exist node_modules (
    rd /s /q node_modules
    echo node_modules removed
) else (
    echo node_modules not found - skipping
)

if exist src-tauri\target rd /s /q src-tauri\target
cd ../..

REM =============================
REM Step 5：从构建仓库复制 toolkit
REM =============================
echo Step 5/8: Copying toolkit from NIKKEAutoScriptBuild...
if exist "NIKKEAutoScriptBuild\toolkit" (
    xcopy /e /y /q "NIKKEAutoScriptBuild\toolkit" "NIKKEAutoScript\toolkit\" >nul
    echo Toolkit copied successfully.
) else (
    echo Error: toolkit folder not found in NIKKEAutoScriptBuild
    pause
    goto :end
)

REM =============================
REM Step 6：验证根目录 nkas.exe
REM =============================
echo Step 6/8: Verifying root nkas.exe...
if not exist "NIKKEAutoScript\nkas.exe" (
    echo Error: root nkas.exe was not produced by the Tauri build
    pause
    goto :end
)
for %%I in (nkas.exe) do if %%~zI GTR 31457280 (
    echo Error: root nkas.exe exceeds 30 MiB
    goto :end
)

REM =============================
REM Step 7：安装 Python 依赖
REM =============================
echo Step 7/8: Installing Python dependencies...
cd NIKKEAutoScript
if exist "toolkit\python.exe" (
    echo Installing requirements.txt...
    toolkit\python.exe -m pip install -r deploy\requirements.txt -i https://pypi.org/simple
    echo Python dependencies installed
) else (
    echo Error: Python.exe not found in toolkit
    pause
    goto :end
)

REM =============================
REM Step 8：复制配置文件模板
REM =============================
echo Step 8/8: Creating deploy.yaml from template...
cd config
if exist deploy.template.yaml (
    if not exist deploy.yaml (
        copy deploy.template.yaml deploy.yaml >nul
        echo Created deploy.yaml from template
    ) else (
        echo deploy.yaml already exists - skipping copy
    )
) else (
    echo Warning: deploy.template.yaml not found in config directory
)
cd ..

echo ==================================================
echo Build completed successfully!
echo ==================================================
timeout /t 5 >nul

:end
echo Script finished. Press any key to exit...
pause
endlocal
