# 剧情地图重扫与 Wiki 标注进度

更新时间：2026-09-30 10:47，Asia/Hong_Kong。此文档是本轮跟进的精简检查点；早期原型记录仍见 [执行记录](minimap-autopush-execution.md)。

## 目标与顺序

最新用户要求：**先下载所有章节的 Wiki 收集品数据。** 此步骤立即执行，不等待地图修复；仅下载攻略、条目和原图，标注仍在新地图验收后进行。

1. 跟进当前目标 `codex://threads/01a0ef9c-49e8-7a30-9f44-0c8899991728`（hostId：`local`），核对分层重建方案及执行结果。用户已将目标切换到此恢复任务，旧聊天不再作为当前监控对象。
2. 确认方案和必要验证通过后，先备份当前地图，再全新采集普通剧情第 41 至第 1 章，共 41 章。使用独立新目录，不能因旧包已存在而跳过本次重扫。
3. 所有目标章的新扫描及正式地图包验收后，执行 Wiki 普通／困难收集品标注。来源难度分别保存，不猜测低置信度坐标，不复制旧底图坐标到新图。
4. 汇总各章扫描、地图质量、标注通过／待复核／失败数量、证据与恢复命令；不得将扫描结束或测试通过当作全部任务完成。

本轮未获 Git 推送授权。保留共享工作区原有未提交改动，不与原任务并行控制游戏或修改其正在处理的源码。

## 当前检查点

**Wiki 全量下载与验收已完成。** 当前公布的普通／困难均为第 1–48 章，共 96 篇、877 个条目、1,159 张原图；失败 0、缺失 0、未关联条目的原文图片 0。普通 608 项／835 图，困难 269 项／324 图。原图共 1,328,673,841 字节，包含 1,157 个不同来源 URL（两张图片在不同文章中重复引用）。所有图片重新解码成功，1,447 个文章／清单／图片文件已记录大小及 SHA-256。

资料根目录：`data/wiki_collectibles/`；离线入口 [README](../data/wiki_collectibles/README.md)，总数据 [collectibles.json](../data/wiki_collectibles/collectibles.json)，核对报告 [download_audit.json](../data/wiki_collectibles/download_audit.json)，文件清单 [download_inventory.json](../data/wiki_collectibles/download_inventory.json)。每章保留 `detail.json`、`article.json`、`items.json` 和 `images/`；总数据包含名称、编号、难度、作者、来源 URL、更新时间、图片角色与相对路径。目录已在线刷新，原有 4 篇正文也重新获取，回执分别为 `catalog_refresh.json`、`existing_metadata_refresh.json`。条目数只描述 Wiki 当前资料，未推断游戏内缺失内容或地图坐标。

下载命令：`.venv\Scripts\python.exe -X utf8 -u dev_tools/wiki_collectibles.py --download-only --difficulty both`。10:45:59 正常结束，退出码 0，原会话 `66924`／Python PID `68988`、`88604` 已结束，无需恢复下载。日志 `tmp/map_followup/wiki_download_20260930.log`，同名 `.err` 为空，`.exit` 为 0。核对命令：`.venv\Scripts\python.exe -X utf8 tmp/map_followup/audit_wiki_download.py`；语法检查与实际全量核对均通过，日志 `tmp/map_followup/wiki_download_audit_20260930.log`。没有修改地图、现有标注或应用源码；数据与临时日志受 Git 忽略，保留本机供后续标注使用。重扫／标注与原监控自动化仍保持以下状态。

本轮重扫目标仍为 `blocked`。当前目标聊天于 09:29:22 因模型 Token 速率限制失败，实时接口已确认 `systemError / failed`，并非观测超时。尚无通过验收的扫描方案。自动跟进按需用户介入规则设为 `PAUSED`，待用户恢复目标任务和跟进。

当前目标内的用户补充：**主要关注小队移动，Wiki 完全匹配不上也可以手工标注。** 后续验收以可用于小队定位、移动的地图几何为重点；不要把自动 Wiki 圆环检测／配准全通过设成重扫前置条件。标注阶段仍需可靠来源和新底图坐标绑定，自动匹配不可靠时转人工复核／标注。

| 阶段 | 状态 | 证据与下一步 |
| --- | --- | --- |
| 当前目标任务 | 速率限制失败，需恢复 | 09:29:22 因 `rate limit exceeded` 结束；39 章仍有一批约 16px 误差，尚不能用于移动，未交付最终方案 |
| 旧方案 | 不适用于全部目标章 | 40 章高低道路在同次拖动中相差约 31.5 地图像素，已有三次扫描均配准失败 |
| 当前数据备份 | 已验证 | 1,300 个文件、563,936,089 字节；源和副本逐项 SHA-256 一致，末尾重新核对源文件集及所有哈希；启动重扫前核对新增／变更数据 |
| 新扫描 | 未启动，0/41 | 等待修复验收；不重复运行已确认不适用的旧方案 |
| Wiki 资料下载 | 已完成并全量核对 | 普通／困难各 1–48 章；96 篇、877 项、1,159 图，失败与缺失均为 0 |
| Wiki 标注 | 未开始 | 待 41→1 全部新地图完成，使用本轮完整 Wiki 缓存并按新地图哈希重新配准或人工标注 |

现有 `data/chapter_maps/current/` 包括 27、28、34、38、39、40、41 章目录，但目录存在不代表合格。38 章旧包含人工核对标注；40 章目前仅有失败诊断预览。原始记录含历史失败和重复尝试，汇总需按章取本轮最终验收状态。

旧阶段离线诊断：`tmp/ch40_elevation_20260930/analysis.md`、`evidence.json`、`evidence.png`。这些本机缓存受 Git 忽略。

最新核对：`wait_threads` 和 `read_thread` 均确认当前回合 `01a0efa4-8898-7713-95e7-12692ded8149` 失败，错误为 `rate limit exceeded: Your requests to gpt-6-astra for gpt-6-astra in eastus2 have exceeded token rate limit.`。不可变失败回执：`tmp/map_followup/source_failure_20260930_resumed.json`；最近详情：`tmp/map_followup/latest_read_thread.json`。09:34 新扫描目录仍不存在，且未发现章节采集、Wiki 批次或 surface-fit 活动 Python 进程；无需停止或重启任何扫描进程。

旧任务 `01a0ee2a-df22-7033-8a8e-96a7454276e1` 于 04:23:41 因 `400 / invalid_encrypted_content` 失败；历史回执为 `tmp/map_followup/source_failure_20260930.json`。此错误不代表新目标失败，也不能据此推断错误原因是上下文大小。已有诊断工具、测试及数据由新目标继续处理。

待新目标更新的已有验收证据摘要：

- 39／40 章联合拟合的求解器成功不等于地图合格；每条轨迹留出末两次观测的误差第 95 百分位仍约 17.06／6.43px。输入哈希、计数与限制见 `tmp/map_followup/model_audit.json`。这不是全局独立帧、稠密道路或整图验证。
- `minimap_surface_fit` 的退出码 0 仅表示模型比较完成，不导出正式地图包，也不验证跨层连通和场景点击。生产采集与 Wiki 坐标消费路径仍须实现并验收。
- 偏平面章评估必须使用最终修复投影，避免把旧标定误差误判为高低差。
- 原任务最新 Wiki 兼容验证：39／40 章普通截图各有 4/14 未识别小队圆环，正在补救并保护已有成功样本。这不计作本轮扫描后的批量标注。

备份根目录：`data/chapter_maps/history/before_full_rescan_20260930_020453_509746/`，内含原目录层级下的 `data/chapter_maps/current/` 和 `data/campaign_prototype/`，以及逐文件清单 `backup_manifest.json`。核对结果：`tmp/map_followup/backup_result.json`。备份复制过程的源文件没有变化；原集合仍留在原处供原任务读取。此备份不包括无需改动的历史集合和 Wiki 缓存。

备份内已有正式包为 27、34、38、41 章，均记录 `joint_grid_road / roads_exhausted`，但不计入本次新扫描。34 章保存 12 个标注、38 章保存 20 个标注。28、39、40 章无正式包。基线清单：`tmp/map_followup/baseline_inventory.json`。本轮仅新增跟进文档与本机工具；备份脚本语法检查、实际复制与完整双侧哈希复核、Node 语法检查及 `git diff --check` 通过。

后续新采集拟使用独立 `data/chapter_maps/rescan_20260930/`；尚未创建或启动。若前序修复改变命令或数据格式，先依实际接口更新再运行。游戏实际章节尚未重新核实，不能直接从预定 41 章盲跑。

原生 heartbeat 自动化 ID：`wiki`；执行目标为本跟进聊天 `01a0ee4e-6d13-7860-bcbe-95a8e84ce853`，周期仍为每 5 分钟，现为 `PAUSED`。已通过原生 `automation_update(update)` 暂停，并用 `view` 及保存字段核对；名称、任务正文、周期及执行聊天均保留。被监控聊天仍为本文件第一条的新任务；没有发送跨聊天消息。

恢复顺序：用户在当前目标聊天重试／恢复失败回合，再在本聊天要求继续跟进。更新既有自动化 `wiki` 为 `ACTIVE`，不要重复创建；检查任务实际状态及最终结果，再按验收条件决定是否启动扫描。启动前核对备份与数据变化；不能把实验结果、单元测试通过或任务恢复成功当成扫描方案已验收。

## 验收门槛

- 平面章节旧行为与标注兼容；39／40 章需有分层／局部模型的独立帧、重访和跨方向证据。
- 新地图包的图像哈希、坐标变换、道路数据和层信息一致，导航消费路径经过验证；Wiki 可转人工标注，不要求自动配准全部通过，人工标注也必须正确绑定新图的坐标与哈希。
- 不降低匹配门槛来凑齐结果；分层道路交叉不能直接当作可通行连接。
- `roads_exhausted` 仅表示道路前沿遍历完成；`whole_camera_domain_verified` 单独报告。游戏内完整覆盖未验证时保留限制。
- 批量采集前核实实际章节、窗口权限、焦点、游戏页面和无其他输入控制进程；页面无法确认时受控停止。
- 重扫后的 Wiki 标注需统计实际保存点数、待复核项及来源，不能仅统计已下载文章数。

## 继续工作入口

- 工作目录：`D:\PCR\NIKKEAutoScript`；Python：`.venv\Scripts\python.exe`。
- 监控临时工具：`tmp/map_followup/app_call.mjs`，保留实际当前聊天身份；新 App Tools 需 `callerSource: codex`。
- 即时状态：`node tmp/map_followup/app_call.mjs wait_threads tmp/map_followup/snapshot.json`；工具自动更新 `afterCursor`，仅输出新增消息和精简状态，并保存完整回执。
- 必要时读取原任务最终结果：`node tmp/map_followup/app_call.mjs read_thread tmp/map_followup/read.json`。只输出用户消息和进展／结论，避免展开历史工具输出。
- 现有批次入口：`dev_tools/minimap_chapters.py`；Wiki 入口：`dev_tools/wiki_collectibles.py`；参数与质量限制见 [批量说明](wiki-collectibles-batch.md)。原任务改动后再核对接口，不能提前假设新格式已兼容。
- 每次只保留本节检查点、实际运行命令、进程标识、恢复章节和最新证据路径；完整日志写入本机文件。上下文压缩后先读本文件和最新进度 JSON，避免重复载入历史。
