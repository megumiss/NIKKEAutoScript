@echo off
setlocal enabledelayedexpansion

REM ============================================
REM 构建 NIKKEAutoScript 环境脚本
REM 作者: Master Megumi
REM 说明:
REM  1. 自动克隆主项目与构建项目
REM  2. 从构建项目复制 toolkit 到主项目
REM  3. 复制 deploy\build\nkas.exe 到主项目根目录
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
REM Step 2：构建 webapp 并移动输出
REM =============================
echo Step 2/8: Building webapp...
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
REM Step 3：删除不必要的语言文件和 DLL/License
REM =============================
echo Step 3/8: Cleaning unnecessary files...

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
REM Step 6：复制 nkas.exe
REM =============================
echo Step 6/8: Copying nkas.exe to NIKKEAutoScript root...
if exist "NIKKEAutoScript\deploy\build\nkas.exe" (
    copy /y "NIKKEAutoScript\deploy\build\nkas.exe" "NIKKEAutoScript\nkas.exe" >nul
    echo nkas.exe copied successfully.
) else (
    echo Error: nkas.exe not found in build repository
    pause
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
