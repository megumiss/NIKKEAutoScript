@echo off
setlocal enabledelayedexpansion

echo ==================================================
echo NIKKEAutoScript Build Script
echo ==================================================

REM 步骤0：如果存在旧目录则删除
echo Step 0/6: Removing existing directory...
if exist NIKKEAutoScript (
    echo Found existing NIKKEAutoScript directory, deleting...
    rd /s /q NIKKEAutoScript
    if exist NIKKEAutoScript (
        echo Error: Failed to delete NIKKEAutoScript directory
        pause
        exit /b 1
    )
    echo Old directory removed successfully
)

REM 步骤1：克隆仓库并删除.git文件夹
echo Step 1/6: Cloning repository...
git clone --depth 1 https://github.com/megumiss/NIKKEAutoScript.git
if not exist NIKKEAutoScript (
    echo Error: Git clone failed
    pause
    exit /b 1
)

if exist NIKKEAutoScript\.git (
    echo Removing .git folder...
    rd /s /q NIKKEAutoScript\.git
) else (
    echo Warning: .git folder not found
)

REM 步骤2：构建webapp并处理输出
echo Step 2/6: Building webapp...
cd NIKKEAutoScript\webapp

echo Installing Node.js dependencies...
call yarn
if errorlevel 1 (
    echo Error: Failed to install Node.js dependencies
    echo Please check Node.js and Yarn installation
    pause
    exit /b 1
)

echo Building webapp with Yarn...
call yarn build
if errorlevel 1 (
    echo Error: Yarn build failed
    echo Possible causes:
    echo   1. Missing Node.js dependencies
    echo   2. Build script errors in package.json
    pause
    exit /b 1
)

if not exist output\app\win-unpacked (
    echo Error: Build output not found at webapp\output\app\win-unpacked
    pause
    exit /b 1
)

echo Moving build output to root directory...
move /y "output\app\win-unpacked" "..\app" >nul
cd ..
if exist app (
    echo Build output moved to root directory
) else (
    echo Error: Failed to move build output
    pause
    exit /b 1
)

REM 步骤2.5：删除不需要的语言文件和无用文件
echo Step 2.5: Cleaning unnecessary files...

REM 删除除 zh-CN、en、ja 之外的 locales 目录
if exist app\locales (
    echo Cleaning locales folder...
    pushd app\locales
    for /d %%i in (*) do (
        if /I not "%%i"=="zh-CN.pak" if /I not "%%i"=="zh-TW.pak" if /I not "%%i"=="ja.pak" if /I not "%%i"=="en-US.pak" if /I not "%%i"=="en-GB.pak"(
            echo Deleting locale %%i
            rd /s /q "%%i"
        )
    )
    popd
) else (
    echo locales folder not found - skipping
)

REM 删除指定 DLL 和 License 文件
echo Deleting unnecessary DLL and license files...
del /f /q "app\vulkan-1.dll" 2>nul
del /f /q "app\vk_swiftshader_icd.json" 2>nul
del /f /q "app\vk_swiftshader.dll" 2>nul
del /f /q "app\LICENSES.chromium.html" 2>nul
del /f /q "app\LICENSE.electron.txt" 2>nul

echo Clean up completed.

REM 步骤3：清理webapp目录
echo Step 3/6: Cleaning webapp artifacts...
cd webapp
if exist node_modules (
    rd /s /q node_modules
    echo node_modules removed
) else (
    echo node_modules not found - skipping
)

if exist output (
    rd /s /q output
    echo output directory removed
) else (
    echo output directory not found - skipping
)
cd ..

REM 步骤4：复制toolkit目录
echo Step 4/6: Copying toolkit...
if exist "..\toolkit" (
    xcopy /e /y /q "..\toolkit" "toolkit\"
    echo Toolkit copied successfully
) else (
    echo Error: Toolkit folder not found in parent directory
    echo Please ensure toolkit is in same directory as build.bat
    pause
    exit /b 1
)

REM 步骤5：安装Python依赖
echo Step 5/6: Installing Python dependencies...
if exist "toolkit\python.exe" (
    echo Installing requirements.txt...
    toolkit\python.exe -m pip install -r deploy\requirements.txt
    echo Python dependencies installed
) else (
    echo Error: Python.exe not found in toolkit
    pause
    exit /b 1
)

REM 步骤6：复制配置文件模板
echo Step 6/6: Creating deploy.yaml from template...
cd config
if exist deploy-template.yaml (
    if not exist deploy.yaml (
        copy deploy-template.yaml deploy.yaml >nul
        echo Created deploy.yaml from template
    ) else (
        echo deploy.yaml already exists - skipping copy
    )
) else (
    echo Warning: deploy-template.yaml not found in config directory
)
cd ..

echo ==================================================
echo Build completed successfully!
echo ==================================================
timeout /t 5 >nul
endlocal