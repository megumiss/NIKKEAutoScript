# Mirror 酱接入工作清单

更新渠道由 `MirrorChyanEnabled` 决定：开启后源码和 EXE 只使用 Mirror 酱，任何请求、下载或校验错误直接报错并停止本次更新；用户手动关闭后才使用 Git / VPS / GitHub。

## 上线前待办

- [ ] 联系 [Mirror 酱技术支持](https://github.com/MirrorChyan/docs#联系我们)，申请源码资源 `NKAS_repo` 和桌面资源 `NKAS`。资源 ID 已写入客户端，但尚未确认开通。
- [ ] 确认源码版本采用完整 commit SHA、桌面版本采用独立 SemVer；客户端当前使用 `stable` 通道，源码不传系统/架构，桌面传 `os=windows`、`arch=amd64`，与服务端配置一致后再发布。
- [ ] 获取上传 Token，配置 GitHub Actions Secret `MirrorChyanUploadToken`；获取联调用有效 CDK。CDK 不写入工作流或普通部署配置。
- [ ] 确认源码 ZIP 根目录直接包含程序文件，桌面 ZIP 根目录包含 `nkas.exe` 和 `nkas-desktop.json`；与服务方核对文件级增量包的 `changes.json`、`update_type`、大小和 SHA-256 字段。
- [ ] 配置现有 VPS 凭据和历史文件目录。历史公开地址为 `https://nkas.megumiss.top/releases/history/<完整 SHA>.json`；工作流目录可通过 `VPS_HISTORY_PATH` 指定。
- [ ] 在服务端开通后实际上传两个版本，联调无 CDK 检查、有效/失效 CDK、相邻及跨版本增量、全量包、临时链接失效、上传重试和并发发布顺序。
- [ ] 核对正式发布时的 `config/notices.yaml` 公告日期，并验证公告显示和已读状态。
- [ ] 先通过现有 Git / VPS / GitHub 渠道发布新更新器。桌面用户先升级到包含此次修改的启动器（当前为 `1.1.4`），再启用 Mirror 酱；旧 EXE 不识别新开关。两类资源以后独立发版。
- [ ] 核对整合包、GitHub Release、VPS 和 Mirror 酱中同版本 EXE 的哈希一致，以及源码包 SHA 与触发构建的提交一致。GitHub 草稿 Release 不作为 Mirror 酱下载源。

## 已完成的本地接入

- [x] 增加默认关闭的启用开关；关闭不请求 Mirror 酱，开启失败不自动切换渠道，也不修改开关。
- [x] CDK 可留空检查版本；已是最新且没有 URL 正常结束，有更新却没有 URL 报错。CDK 使用账号信息相同的加密方式，单独保存在 `config/.security/mirrorchyan.acc`，接口不回显，界面支持保存、掩码和清除；修改后下次检查生效。
- [x] 更新页、定时更新、启动准备和 `updater.bat` 共用源码更新逻辑；校验并处理全量/增量包，保留用户配置和凭据，清理发行清单中失效的文件。
- [x] 暂存下载、大小/哈希和路径校验、备份恢复、依赖失败拦截、安装互斥；开发 worktree 拒绝覆盖安装，手动切回 Git 后重新对齐发行文件及版本记录。
- [x] 桌面更新调用同一套 Python 请求和校验逻辑，保留 Rust EXE 替换、重启及恢复流程，桌面版本更新到 `1.1.4`。
- [x] 打包 `app-version.json`、`release-files.json` 和最近 50 条 `update-history.json`；更新页展示真实安装 SHA、提交链接和历史缓存状态。
- [x] 新增源码发布工作流 `.github/workflows/mirrorchyan-source.yml`，按固定 master SHA 打包；桌面工作流从同次构建 artifact 上传 ZIP，整合包不再拉取另一个 master 提交。
- [x] 添加部署页 CDK 控件、多语言文案、具体错误展示和公告，更新 `webui/dist`。

## 本地验证

模拟使用本地 HTTP 服务、临时 Git 仓库及安装目录，不上传资源，不修改真实安装。

- Python：`python -m unittest discover -s tests -p test_mirrorchyan.py -v`，覆盖无 CDK、缺少 URL、错误码、网络失败、不回退、全量/增量、删除保护、哈希/大小、依赖恢复、Git 渠道切换、版本/历史、CDK 和桌面包。
- 现有回归：`test_starter_process_filter.py`、`test_process_manager_race.py`、`test_security_entry.py`。
- 桌面：在 `webapp/` 执行 `yarn test`、`yarn run compile`，包含 Mirror 酱错误不回退及 Python / Rust 安装锁互斥测试。
- SPA：在 `webui/` 执行 `yarn run typecheck`、`yarn run build`。
- 浏览器：使用项目 Python 启动 `tests/mirrorchyan_fixture.py --port 18772`，再运行 `node tests/mirrorchyan_browser.cjs`；验证真实部署/CDK 接口和构建后页面的开关、掩码、清除、保存后生效及缺少 URL 报错。
- 对改动的 Python 文件执行语法编译，核对 YAML 和 `git diff --check`。

服务端资源申请、真实上传及有效 CDK 下载尚未验证，不能用本地模拟结果代替上线联调。
