# 可选安全入口

安全入口默认关闭，不改变已有连接方式。在“部署 → Webui → SecurityEntryEnabled”中手动开启后即时生效，无需重启后端。该开关按 `deploy/template` 与部署 schema 动态生成，使用与其他字段相同的布局；开启后在字段下方显示完整入口与复制、重新生成操作，不额外创建独立卡片。它是一把共享访问密钥，不是多用户账号或角色权限系统；持有完整入口的客户端拥有原有后端访问权限。

## 地址怎么填

| 场景 | 地址或操作 |
| --- | --- |
| 未开启保护 | 浏览器访问 `http://服务器:12271`；App 填同一普通后端地址 |
| 开启后，浏览器或远程 App | 从部署页复制 `http://服务器:12271/entry/<完整随机密钥>` |
| 本机 exe | 正常启动，无需填写入口；桌面壳读取同一安装目录的本机凭据 |
| 本机 Android＋Termux | 使用 App 的本机部署模式；设置页可点击“使用本机 Termux 部署”恢复 |

`<完整随机密钥>` 是部署页生成的 43 字符内容，不要自行填写示例文字。若在本机页面复制出的地址以 `127.0.0.1` 开头，远程访问时将主机和端口改为实际可达的服务器地址，保留完整 `/entry/...` 路径。支持 HTTPS 和 IPv6，例如 `https://[IPv6地址]:端口/entry/<完整随机密钥>`。

浏览器打开正确入口后取得 HttpOnly、SameSite=Strict Cookie，并跳转到不含密钥的 `/app/`；HTTPS 下 Cookie 同时带 Secure。不要把 `/app/` 地址当作首次授权入口。Cookie 按浏览器、站点保存，换浏览器或从 IP 改为域名需要重新打开入口。

App 将完整地址拆为“后端根地址＋密钥”，HTTP 请求和原生 WebSocket 自动携带 Bearer。普通偏好只保存根地址，Android 密钥使用加密存储，iOS 使用仅本设备可用的 Keychain。外部图片地址不附带后端凭据；携带凭据的 API 请求不自动跟随重定向。应用中打开 Web UI 也会使用完整入口。

## Android＋Termux 的完整链路

1. App 按现有初始化流程部署 Termux，后端依然默认不启用入口。现有 STAR 验证、Termux `RUN_COMMAND` 授权与 ADB 配对流程不变。
2. `nkas-service.sh` 在 proot 容器中运行 Python 后端，并把 Termux 的 `$HOME/NIKKEAutoScript` 绑定到容器的 `/app/NIKKEAutoScript`。两侧看到的是同一份配置和密钥文件。
3. 在 App 或 Web 的部署页开启入口。后端将开关保存到 `config/deploy.yaml`，随机密钥独立保存到 `config/.security/entry.key`；当前客户端接收新凭据并继续使用。
4. 本机模式首次连接或凭据失效时，经 Android 平台桥调用 Termux，读取上述私有文件，再给 HTTP/WS 加 Bearer。命令输出不会写入普通日志；读取失败会提示检查本机部署和权限。
5. 其他设备没有读取 Termux 私有文件的通道。远程 App 在“设置 → 后端地址”粘贴完整入口，浏览器打开同一入口即可。

本机模式与手填远程地址严格分开：即使手填 `http://127.0.0.1:端口`（例如 SSH 隧道），也不会自动读取或复用本机 Termux 密钥。切回本机请使用专门的“使用本机 Termux 部署”按钮。

安全入口不会修改监听地址或开放端口。Termux 服务默认监听回环，启动参数来自 `$HOME/.nkas/settings.env` 的 `NKAS_WEBUI_HOST` / `NKAS_WEBUI_PORT`，通过 `gui.py --host ... --port ...` 传入；这些命令行参数优先于部署页的监听配置。远程设备能否连接仍取决于你已有的监听、隧道、反向代理及防火墙配置。ADB/scrcpy 控制地址与后端地址相互独立。

## 开关、轮换与恢复

- 开启：保护 HTTP API、页面、静态资源、截图、导出/日志下载以及 WebSocket；匿名仅保留最小 `/api/system/status` 健康信息。健康接口返回 HTTP 200 不代表已取得访问权限。
- 重新生成：立即撤销旧入口、旧 Cookie/Bearer 和活动 WebSocket。执行操作的当前客户端保存新凭据并重连；其他远程客户端需粘贴新入口。本机 exe 与本机 Termux 模式自动重新读取密钥。
- 关闭：恢复普通地址访问，保留密钥。重新开启沿用原入口，因此关闭再开启不等于撤销旧凭据；需要撤销时使用“重新生成”。
- “还原默认”：保留安全入口开关和密钥，避免重置部署配置时意外解除保护。
- 开关或轮换只变更访问权限，不调用自动化任务的启动、停止或重启操作。

密钥单独存放在配置目录的 `.security/entry.key`，不进入普通部署 schema、Git 或普通访问日志；应用自己的 Uvicorn 日志会将入口路径脱敏。备份部署时应私下备份该文件，并限制配置目录的本地文件权限。

忘记入口时，优先通过同安装目录的本机 exe 或 App 本机 Termux 模式进入部署页重新复制。如果私有文件损坏，可先停止后端并恢复密钥文件备份；需要全新入口时，将损坏文件移到安全备份位置，再启动后端生成新文件。不要在仍公开可达的部署上直接关闭保护来恢复访问。

## 网络安全边界

入口本身等同凭据，请勿公开分享、提交截图或粘贴到公共日志。远程访问优先使用 HTTPS 或可信加密隧道；HTTP 下 Cookie/Bearer 仍可能被网络监听。入口跳转带 `no-store` 和 `no-referrer`，但反向代理、浏览器历史或外部监控仍可能记录原 URL，请另行对 `/entry/` 日志脱敏。

不根据 `localhost`、来源 IP 或转发头豁免鉴权，避免 SSH/反代回环绕过。反向代理应保留实际客户端使用的 Host，正确配置 HTTPS 转发，并支持 WebSocket；跨站写请求及开启保护后的跨站 WS 会被拒绝。

独立 ADB、ws-scrcpy 端口和其他服务不受这层 NKAS HTTP/WS 中间件保护。Web 控制台的启用开关和 Host 限制仍然保留；它们不替代入口鉴权。首次开启前仍属于无鉴权部署，应先在受控网络内完成设置。

## 本次验证（2026-09-14）

- Python：8 项安全入口回归通过，包含真实生产路由工厂、HTTPS Cookie 属性，以及实际 TCP HTTP/WS 的开启、轮换、关闭、重新开启与四类连接撤销；相关 Python 文件语法检查通过。
- Vue：`yarn run build` 与 `yarn run typecheck` 通过；构建保留已有大 chunk / 动静态混用提示。
- 桌面壳：`yarn test` 36 项通过，`yarn run compile` 成功生成 exe。没有启动真实安装目录的 exe，避免触发实际后端或更新流程。
- Flutter：`flutter analyze` 无问题，`flutter test` 56 项通过；包含真实 Dart WebSocket Bearer 握手、手填地址隔离、凭据轮换竞态和 375px 深色动态部署字段。确认安全入口只随 schema 出现，并在原分组内展开操作；取消关闭或保存失败均保留原开关状态。
- 实际浏览器：在临时配置的 `127.0.0.1:18771` / `localhost:18771` 两个 Cookie 隔离站点验证默认关闭、启用、匿名拦截、错误入口、正确入口跳转、显示/隐藏、复制成功提示、关闭/轮换确认的取消、外部轮换后两个旧页面失效、旧入口不可用、关闭恢复普通访问、重新开启及新入口恢复访问。

- 动态部署页浏览器复测：在隔离的 `127.0.0.1:18772` 验证开关位于 `Webui` 原分组，顺序与样式遵循部署 schema；开启后内联展开操作。浅色/深色显示、取消关闭后保留开启状态、复制成功提示均正常。

浏览器测试的轮换和关闭由隔离后端 HTTP 请求驱动，再观察真实页面状态；没有修改真实部署。实际 Uvicorn 入口访问日志仅显示 `/entry/[redacted]`。复制按钮的成功提示已验证，系统剪贴板内容未独立核验。浏览器测试未使用真实游戏任务，不能代替真机/生产链路验收。

Android 环境缺少 SDK，`flutter build apk --debug --config-only` 报 `No Android SDK found`，未完成 Kotlin JVM 回归、APK 构建及真机 Termux 读取验证；Windows 环境也未运行 iOS 构建/Keychain 真机验证。

可复现的后端回归（项目根目录，Python 环境需具备 httpx、websockets 及项目依赖）：

```powershell
python -m unittest discover -s tests -p 'test_security_entry*.py' -v
python tests/security_entry_fixture.py --port 18771
```

第二条用于手动浏览器测试，使用临时配置和构建后的真实 Vue 页面，业务数据为只读 fixture，不启动游戏任务或更新；正常退出时清理临时配置，强制终止进程可能留下需要手动清理的临时目录。
