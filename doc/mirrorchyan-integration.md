# MirrorChyan 接入

## 范围

接入现有安装的源码更新与 Windows x64 启动器 EXE 自更新。默认仍使用 Git 源码更新和原官方启动器 manifest，不强制用户使用 Mirror酱。
完整运行包不进入这两条更新链；首次安装仍保留 Git 初始化流程，Python / toolkit 升级或不可恢复的安装需使用完整运行包。

依据：[项目 PR #349](https://github.com/megumiss/NIKKEAutoScript/pull/349)、[Git Pack 文档 PR #13](https://github.com/MirrorChyan/docs/pull/13)、[客户端集成文档](https://github.com/MirrorChyan/docs/blob/main/Integration.md)。
PR #349 提供服务端分发工作流；本分支实现客户端，不重复复制工作流。发版前仍需确认官方上传工作流和资源持续可用。

| 更新对象 | RID | 请求参数 | 版本规则 |
| --- | --- | --- | --- |
| 源码与仓库内资源，包括 SPA dist | `NIKKEAutoScript-Source` | `channel=stable`；不传 os / arch；传当前 UTC 版本 | HEAD committer 时间转 UTC，格式 `YYYYMMDDHHmmss`；与服务端版本不相等即有更新，允许官方分支回退 |
| 启动器 EXE | `NIKKEAutoScript` | `os=win&arch=x64&channel=stable`；current_version 留空 | 去掉 v 前缀后比较 SemVer；只升级到更高版本 |

平台值使用项目交付的 `win / x64`，不是通用示例中的 `windows / amd64`。
源码 pack 是 Git 对象 ZIP 容器，不直接覆盖工作区，也不使用 changes.json。
启动器资源版本必须对应所分发 EXE 的版本；不能把完整运行包（arch 留空）交给 EXE 替换器。

## 使用

在侧边栏进入「部署」页配置：

- `UpdateSource`：界面选项为「Git源 / Mirror酱」，默认使用「Git源」。
  配置字段为 `Update.UpdateSource`，对应值 `git` / `mirrorchyan`。
  Git源使用 Git 组配置的仓库和 `Update.DesktopUpdateManifest`；Mirror酱提供源码和启动器下载。
- `Update`：统一来源、启动自动更新、周期检查、定时重启和启动器 manifest。
- `Git`：仓库、分支、Git 可执行文件、代理和 SSL 设置。
- `MirrorChyan`：Mirror酱专属的 CDK。
- `MirrorChyan.MirrorChyanCDK`：可留空；掩码输入，清空输入框并保存可删除 CDK。实际凭据保存在 `config/.security/mirrorchyan.cdk`，不写入部署 YAML。

设置在下一次检查 / 应用时生效，不需要重启。两条链共用更新源选择与 CDK；包型、版本规则和恢复流程仍分别处理。
无 CDK 可以检查版本，有更新但无下载资格时显示提示，不会为尝试下载而先停止任务。
Mirror 失败不静默切到其他来源；用户可以修正 CDK 或显式切回原来源。
普通浏览器可更新源码；启动器更新仅在 Tauri 桌面壳中可用。

Mirror源码分发的是官方 master 分支，不限制配置中的 Repository 地址或本地分支名称。
检查更新读取当前 HEAD；实际应用时保护未提交修改、未跟踪文件冲突，并核对更新基准。
请使用干净的官方安装验证源码更新，不在本分支的开发工作区点击实际覆盖更新。

## 实现

### 源码

[GitManager](../deploy/git.py) 为启动准备与 Web UI 复用更新源选择。
[Mirror 客户端](../deploy/mirrorchyan.py) 负责版本 API、私有 CDK、下载 SHA-256 / 大小校验和业务错误映射。
[Git pack 更新器](../deploy/git_pack.py) 执行：

1. 校验安装、分支和工作区，读取完整旧 HEAD，用 cat-file 计算 UTC committer 版本。
2. 下载并校验 ZIP；只接受 pack、idx、meta.json，拒绝路径越界、符号链接、重复条目与超大展开量。
3. 核对 meta.base 与 HEAD，基准失配时不带 current_version 重取完整快照；下载后再次核对 HEAD。
4. 使用 index-pack --fsck-objects --stdin 导入并重建索引，验证目标对象和提交时间；不要求完整父历史。
5. 检查目标路径、当前 Python 编译兼容性，记录恢复信息，再维护 shallow、更新 origin/master、检出目标。
6. 失效依赖缓存并按原设置同步依赖，检查 HEAD / 工作区一致性，再进入重载和实例恢复。

恢复记录位于 `tmp/mirror-source/transaction.json`，保存旧引用、HEAD、shallow 和按需记录的 pip 包版本。
依赖 / 检出失败恢复旧源码和依赖；保留导入快照必要的 shallow 边界，避免恢复后 fsck 报缺失父提交。
成功重载清除记录；尚未确认的失败启动在后续启动中恢复。恢复失败或发现新用户修改时停止自动恢复，不启动混合版本任务。
依赖恢复需要可用 pip 和原版本包来源；恢复失败需使用完整运行包，不把单独恢复旧 SHA 当作成功。

Mirror 检查在 GitOverCdn / fetch 之前分流；手动、周期、定时、启动前更新共用同一配置。
Mirror 返回的源码 / 启动器说明与可读取的 Git 历史统一显示在更新记录中；
源码说明中的提交号用于去重，已在本地历史中的提交不重复显示。
没有记录时显示空状态，不展示空卡片，也不为 Mirror 单独增加说明卡片或设置入口。
API 的检查错误和应用错误分开返回，以便提供正确的重试操作。

### 启动器

[Rust Mirror 适配器](../webapp/src-tauri/src/mirrorchyan.rs) 查询版本和下载元数据，
[桌面更新器](../webapp/src-tauri/src/desktop_update.rs) 在每次检查和应用前重读设置 / CDK。
无 CDK 的版本响应不要求下载字段；应用阶段重新获取元数据，再复用既有大小、SHA-256 校验和替换 helper。
保持 30 MiB EXE 上限、旧 EXE 备份、安装 / 启动失败回滚、重启后清理。
旧源码快照没有启动确认 hook 时，桌面壳在新启动的后端健康检查通过后确认源码事务。

[共享更新锁](../deploy/update_lock.py) 使用操作系统锁；Windows Rust 壳与 Python 更新器共用 `tmp/update.lock`。
锁随进程退出释放，不根据遗留 PID 文本误判存活。源码等待 / 更新期间和 EXE 更新期间禁止并行更新、切换更新源、修改 CDK 或重置部署配置。

## 隐私与错误

- 私有文件目录已被 gitignore 排除；读取部署 schema 和保存结果只返回掩码及是否配置，不回传真实 CDK。
- 私有文件不是加密保险箱；Windows 沿用配置目录访问权限。分享安装目录、备份和自定义导出时必须排除 config/.security。
- CDK 只通过编码后的 HTTPS query 传给 Mirror；上传 Token 只属于 CI，不进入客户端。
- 不输出下载签名 URL、HTTP 请求 URL或服务端任意 msg。该项目异常格式器会抓取局部变量，因此 Mirror 检查失败不输出带凭据帧的完整 traceback。
- 同时检查 HTTP 和 JSON code；非 JSON、超时、错误 CDK 不能显示为「已是最新」。下载阶段也处理可用 JSON 错误码。
- 7001 / 7002 / 7003 / 7004 / 7005 分别提示过期、无效、次数耗尽、资源不匹配、封禁；8001 提示资源不可用，8009 提示仅支持流程测试。
- 错误 / 无下载资格不自动反复请求，也不静默降级来源。损坏包不安装，过期链接通过重新检查获取。

## 验证

见 [验证记录](./mirrorchyan-validation.md)。
构建产物 webui/dist 随源码保留；验证脚本和隔离运行环境位于临时目录，不纳入仓库测试代码。
