@echo off
setlocal enabledelayedexpansion

echo ==================================================
echo NIKKEAutoScript Build Script
echo ==================================================

REM =============================
REM Step 0：删除旧目录
REM =============================
echo Step 0/6: Removing existing directory...
if exist NIKKEAutoScript (
    echo Found existing NIKKEAutoScript directory, deleting...
    rd /s /q NIKKEAutoScript
    if exist NIKKEAutoScript (
        echo Error: Failed to delete NIKKEAutoScript directory
        pause
        goto :end
    )
    echo Old directory removed successfully
)

REM =============================
REM Step 1：克隆仓库
REM =============================
echo Step 1/6: Cloning repository...
git clone --depth 1 https://github.com/megumiss/NIKKEAutoScript.git
if not exist NIKKEAutoScript (
    echo Error: Git clone failed
    pause
    goto :end
)

if exist NIKKEAutoScript\.git (
    echo Removing .git folder...
    rd /s /q NIKKEAutoScript\.git
) else (
    echo Warning: .git folder not found
)

REM =============================
REM Step 2：构建 webapp 并移动输出
REM =============================
echo Step 2/6: Building webapp...
cd NIKKEAutoScript\webapp

echo Installing Node.js dependencies...
call yarn
if errorlevel 1 (
    echo Error: Failed to install Node.js dependencies
    echo Please check Node.js and Yarn installation
    pause
    goto :end
)

echo Building webapp with Yarn...
call yarn run compile
if errorlevel 1 (
    echo Error: Yarn run compile failed
    echo Possible causes:
    echo   1. Missing Node.js dependencies
    echo   2. Build script errors in package.json
    pause
    goto :end
)

if not exist dist\win-unpacked (
    echo Error: Build output not found at webapp\dist\win-unpacked
    pause
    goto :end
)

echo Moving build output to root directory...
move /y "dist\win-unpacked" "..\app" >nul
cd ..
if exist app (
    echo Build output moved to root directory
) else (
    echo Error: Failed to move build output
    pause
    goto :end
)

REM =============================
REM Step 2.5：删除不必要的语言文件和 DLL/License
REM =============================
echo Step 2.5: Cleaning unnecessary files...

REM 删除除 zh-CN、en、ja 之外的 locales
if exist app\locales (
    pushd app\locales
    for %%f in (*.pak) do (
        if /I not "%%f"=="zh-CN.pak" if /I not "%%f"=="zh-TW.pak" if /I not "%%f"=="ja.pak" if /I not "%%f"=="en-US.pak" if /I not "%%f"=="en-GB.pak" (
            echo Deleting locale %%f
            del /f /q "%%f"
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

REM =============================
REM Step 3：清理 webapp artifacts
REM =============================
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

if exist dist (
    rd /s /q dist
    echo dist directory removed
) else (
    echo dist directory not found - skipping
)
cd ..

REM =============================
REM Step 4：复制 toolkit 目录
REM =============================
echo Step 4/6: Copying toolkit...
if exist "..\toolkit" (
    xcopy /e /y /q "..\toolkit" "toolkit\"
    echo Toolkit copied successfully
) else (
    echo Error: Toolkit folder not found in parent directory
    pause
    goto :end
)

REM =============================
REM Step 5：安装 Python 依赖
REM =============================
echo Step 5/6: Installing Python dependencies...
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
REM Step 6：复制配置文件模板
REM =============================
echo Step 6/6: Creating deploy.yaml from template...
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
