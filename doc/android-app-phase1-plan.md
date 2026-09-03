# NKAS Android APK 一期方案

## 1. 目标

为 Android 真机提供一个单独的 NKAS 启动器 APK，覆盖从安装到使用的完整流程：

- 仅支持 Android 11 及以上、ARM64-v8a 设备；
- 自动准备 Termux 运行环境；
- 自动安装并启动 NKAS 容器；
- 不要求用户手动输入 Termux 命令；
- 不要求用户再打开 Chrome、Edge 等外部浏览器；
- 现有 NKAS Python 任务逻辑、ADB 设备层和 Vue Web UI 保持复用。

APK 的定位是安装向导、运行编排器和 WebView 容器，不负责重写 NKAS 后端。

## 2. 运行架构

```text
nkas.apk
  ├─ 环境检查
  ├─ Termux 安装与授权引导
  ├─ NKAS 初始化与服务管理
  └─ WebView
       └─ http://127.0.0.1:12271/app/

Termux
  ├─ git
  ├─ proot-distro
  ├─ ~/NIKKEAutoScript
  └─ megumiss/nkas:latest
       └─ /usr/local/bin/python gui.py
```

Termux 中使用的是 `proot-distro` 管理的 NKAS Linux 容器镜像，不要求 Android 运行 Docker daemon。

安装和启动基线以 Wiki 为准：

```bash
pkg update
pkg upgrade -y
pkg install -y git proot-distro
git clone https://github.com/megumiss/NIKKEAutoScript.git
cd ~/NIKKEAutoScript
cp config/deploy.template-docker-cn.yaml config/deploy.yaml
proot-distro install megumiss/nkas:latest --name nkas
proot-distro run \
  -b "$HOME/NIKKEAutoScript:/app/NIKKEAutoScript" \
  -w /app/NIKKEAutoScript \
  nkas -- /usr/local/bin/python gui.py
```

## 3. 支持范围

### 支持

- Android 11 及以上；
- ARM64-v8a；
- 无 root 真机；
- Android 无线调试；
- Termux 官方 GitHub 版本；
- `megumiss/nkas:latest` 容器镜像；
- 现有真机虚拟屏幕和 ADB 控制方案。

### 不支持

- Android 10 及以下；
- x86/x86_64 Android 设备；
- iOS；
- APK 内纯原生重写 Python 后端；
- 去除 Termux 或 `proot-distro`；
- 绕过 Android 外部 APK 安装确认；
- 绕过无线调试或其他系统权限确认；
- 云端或远程 NKAS 运行。

Android 10 及以下在环境检查页直接提示“不支持当前 Android 版本”，不提供 `adb tcpip` 兼容分支。

## 4. APK 页面

一期只保留 3 个安装向导页面。NKAS 主界面由现有 Web UI 提供，不计入安装向导页面。

### 4.1 环境检查

检查以下项目：

- Android API Level 是否大于等于 30；
- CPU 是否为 ARM64-v8a；
- 可用存储空间是否满足要求；
- Termux 是否已安装；
- Termux 是否允许外部应用执行命令；
- 无线调试是否已准备；
- APK 是否被系统限制后台运行。

页面只提供必要的系统设置跳转，例如安装 Termux、打开无线调试和关闭电池优化限制。

### 4.2 Termux 安装

仅在 Termux 不存在或未完成授权时显示：

- 下载官方 GitHub 版 Termux；
- 调用 Android 系统安装器；
- 检查安装结果；
- 引导开启 Termux 的外部命令权限；
- 检查完成后进入 NKAS 初始化页。

Termux 安装必须使用固定来源，避免 GitHub 版和 F-Droid 版因签名不同无法互相升级。

### 4.3 NKAS 初始化

通过 Termux `RUN_COMMAND` Intent 自动执行 bootstrap 脚本，页面显示阶段、日志摘要和重试按钮。

初始化阶段：

1. 检查并更新 Termux 软件包；
2. 安装 `git` 和 `proot-distro`；
3. 下载或更新 NKAS 项目；
4. 创建 `config/deploy.yaml`；
5. 安装 `megumiss/nkas:latest`；
6. 设置 Android 真机默认配置；
7. 执行 ADB 无线调试连接；
8. 启动 `gui.py`；
9. 轮询 `/api/system/status`，确认 Web UI 已就绪。

完成后自动跳转到 APK 内 WebView，不显示独立的“真机连接”或“NKAS 控制台”页面。

## 5. 真机配置策略

真机配置继续使用现有 NKAS Web UI。APK 只在初始化阶段写入默认值：

```text
NKAS.Client.Platform                  = adb
Emulator.Emulator.ScreenshotMethod    = ADB
Emulator.Emulator.ControlMethod       = MaaTouch
Emulator.PhysicalDevice.Enable        = true
Emulator.PhysicalDevice.VirtualDisplay = true
NKAS.Optimization.OcrThreads         = 1
Deploy.WebuiHost                      = 127.0.0.1
```

无线调试采用 Android 11+ 标准流程：

1. APK 打开无线调试设置；
2. 用户完成系统授权；
3. 用户在 APK 中填写配对地址、端口和配对码；
4. APK 通过 Termux 执行 `adb pair`；
5. APK 执行 `adb connect` 和 `adb devices`；
6. 将成功连接的 Serial 写入 NKAS 配置。

无线调试配对确认属于 Android 系统权限，APK 不绕过该步骤。

## 6. Bootstrap 脚本要求

新增 `deploy/android/bootstrap.sh`，要求：

- 幂等执行，重复运行不会破坏已有配置；
- 每个阶段写入状态标记；
- 支持中断后继续；
- 命令失败时返回明确错误码；
- 记录完整日志；
- 不覆盖用户已有账号、任务和通知配置；
- 支持配置 Git 镜像地址；
- 镜像已存在时跳过重复安装；
- NKAS 代码更新与容器初始化分开处理。

建议状态值：

```text
checking
installing-termux-tools
cloning-nkas
creating-config
installing-container
connecting-device
starting-nkas
ready
failed
```

## 7. 服务管理

新增 `deploy/android/nkas-service.sh`，提供：

```text
start
stop
restart
status
```

服务启动命令固定使用：

```bash
proot-distro run \
  -b "$HOME/NIKKEAutoScript:/app/NIKKEAutoScript" \
  -w /app/NIKKEAutoScript \
  nkas -- /usr/local/bin/python gui.py
```

APK 通过 `/api/system/status` 判断服务是否在线。APK 被关闭后不主动杀掉 Termux 进程；重新打开 APK 时先检查现有服务，再决定是否启动。

## 8. Android 工程

建议新增独立 `android/` Kotlin 工程，包名使用 `com.megumiss.nkas`，最低版本设置为：

```text
minSdk = 30
targetSdk = 当前 Android SDK
abiFilters = arm64-v8a
```

主要组件：

- `SetupActivity`：页面流程和环境检查；
- `TermuxBridge`：封装 `RUN_COMMAND` Intent；
- `BootstrapService`：执行和监控初始化；
- `BackendService`：启动、停止和检查 NKAS；
- `MainActivity`：承载 WebView；
- `StateStore`：保存安装状态、Serial 和最近错误。

WebView 只允许访问本地 NKAS 服务；外部链接按系统浏览器 Intent 打开。

## 9. 错误恢复

必须覆盖以下情况：

- Termux 未安装或版本不匹配；
- Termux 未允许外部命令；
- `pkg` 或镜像下载失败；
- `proot-distro` 镜像安装中断；
- NKAS 仓库已存在但更新失败；
- ADB 配对失败；
- ADB 设备离线；
- NKAS 端口未启动；
- Termux 被系统杀死；
- WebView 加载超时。

所有错误都应显示可读原因，并提供“重试当前阶段”操作，不要求用户重新安装 APK。

## 10. 验收标准

在一台全新的 Android 13 或 Android 15 ARM64 真机上：

- 安装 APK 后不需要手动输入 Termux 命令；
- 按系统提示完成授权后，可以自动安装 Termux 工具；
- 可以自动安装 `megumiss/nkas:latest`；
- 可以自动启动 NKAS 后端；
- APK 内 WebView 可以打开完整 NKAS Web UI；
- 可以完成 Android 11+ 无线调试连接；
- 可以读取真机截图并执行点击、滑动；
- 可以启动一个简单任务；
- 关闭 APK 后重新打开可以恢复服务状态；
- 不需要用户手动打开 Chrome 或 Edge；
- 初始化中断后可以继续，不需要从头开始。

## 11. 一期交付物

- Android APK 工程和构建脚本；
- `deploy/android/bootstrap.sh`；
- `deploy/android/nkas-service.sh`；
- Android 默认部署配置；
- Termux 调用和状态协议；
- Android 11+ ARM64 真机回归记录；
- 安装、权限和故障恢复文档。

## 12. APK 编译方式

一期不要求开发者安装 Android Studio。

### 12.1 默认方式：GitHub Actions

推荐使用 VSCode 编写代码，推送分支后由 GitHub Actions 编译测试 APK：

```text
VSCode 修改代码
  -> 提交并推送分支
  -> GitHub Actions 执行 assembleDebug
  -> 下载 app-debug.apk Artifact
```

工作流需要完成以下操作：

- 配置 JDK 17；
- 配置 Android SDK；
- 使用项目内的 Gradle Wrapper；
- 执行 `./gradlew assembleDebug`；
- 上传 `app-debug.apk` 作为构建产物。

本机不需要安装 Android Studio、全局 Gradle 或 NDK。

### 12.2 可选方式：Android SDK 命令行

需要本地编译时，只安装以下组件即可：

- JDK 17；
- Android Command-line Tools；
- Android SDK Platform 35；
- Android SDK Build-Tools；
- Android SDK Platform-Tools。

安装 SDK 后，在 VSCode 终端执行：

```powershell
sdkmanager "platform-tools" "platforms;android-35" "build-tools;35.0.0"
.\gradlew.bat assembleDebug
```

Debug APK 默认输出到：

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

### 12.3 编译与运行测试的区别

编译 APK 不需要 Termux、Python、NKAS 容器或真机无线调试。测试完整安装流程时，仍需要一台 Android 11+ ARM64 真机，并按页面提示完成 Termux、无线调试和电池优化授权。
