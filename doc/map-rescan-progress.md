# 剧情地图重扫与 Wiki 标注进度

更新时间：2026-09-30，Asia/Hong_Kong。最新结果见下节；后文保留此前任务的检查点，早期原型记录见 [执行记录](minimap-autopush-execution.md)。

## 最新：按原始道路／高度区域投影，扩大重绘覆盖

用户要求优化 `redraw_coverage.png` 绿色区域过少的问题，最终输出：
`data/chapter_maps/road_region_projection_20260930/chapter_40/`。
保持去噪分层基准的画布、相机、投影和原点；移除对旧 map 可信像素数量、像素深度残差和道路召回的门槛。
改为从原始帧的道路与局部高度标签提取区域，直接投影原图道路和空白，不做 map 补面。

每个来源区域须经另外两个相机位置复核：道路 IoU≥90%、边界双向距离 P90≤2 个原始 ROI 像素，
对应高度差中位数≤0.025、P80≤0.05；最终共同绘制范围再复核轮廓。
大片单色道路即使 IoU 高、没有足够可检查边界，也不能仅凭同色覆盖通过。
重叠区域按来源整体质量选择，道路像素不享有优先权，因此原始空白可擦除错误碎片。

| 项目 | 结果 |
| --- | ---: |
| 原固定方块版本重绘像素 | 169,369 |
| 新版重绘像素（含道路与空白） | 537,760，约 3.18 倍 |
| 来源候选区域 | 1,095 |
| 已写入的来源记录 / 最终仍贡献像素 | 195 / 189 |
| 相对去噪分层基准补回背景像素 | 18,351 |
| 相对去噪分层基准擦除路面／不确定像素 | 64,018 |
| ≤256 像素空洞（去噪基准 → 新版） | 2,025 → 549 |

旧算法统计确认 2,459 次局部平面候选因不贴合旧 map 被拒绝，228 块因可信锚点不足未尝试，
126 个候选块最终无合格帧对；这些统计分属不同层级，不应相加当作区块数。
旧流程回执在 `frame_projection_diagnostic_20260930/chapter_40/redraw_report.json`。
新版仍有 891 个来源候选未获得两个合格检查位置，另有 2 个最终共同区域轮廓未过关、7 个被更优来源完全覆盖。
各帧检查拒绝事件见新版 `rejection_events`；同一区域会检查多帧，也不是互斥的区域数量。

`redraw_coverage.png` 绿色仍表示实际重绘像素；`coverage_comparison.jpg` 左旧右新，
`comparison.jpg` 为两版 map 对照。`regions/` 保存原帧有效绘制域，`redraw_report.json` 保存每个来源及两帧验证矩阵。
16 项相关回归、语法检查和全部来源复核通过；真实帧复核最低 IoU=0.90109、最大边界 P90=2.0，
每个重绘像素与原始投影一致，坐标往返误差小于 0.000044 像素。
原始帧、深度缓存、旧图及全局坐标未变；上方交叠区域仍有缺损，未宣称整图或导航验收完成。
复核脚本为 `tmp/ch40_stroke120_20260930/verify_region_redraw.py`，复现命令见
[按来源区域重绘](minimap-layered.md#按原始道路和高度区域重绘)。

## 上一版：回到原始帧投影后二次绘制

已按用户要求接入手工道路修订：现有 `map_annotator` 支持画笔补路、橡皮擦、多边形补路／擦除、
撤销重做、保存重开和独立图片导出。可直接打开下述 `frame_projection_20260930/chapter_40/`，
兼容该包的 `rectified_grid_pixel` 标签，原始图片与元数据保持不变。
修订记录存入 `annotations.json` 的 `terrain_edits`；导出在 `manual_exports/<版本>/` 保存新图、
人工覆盖掩码和绑定回执。16 项后端测试与完整浏览器回归通过，未替用户修改真实道路。
操作说明见 [手动修订道路](map-annotation.md#手动修订道路)。

用户指出连续面推断把错误碎片也并入道路，停止推进下节的面约束输出。
本次恢复 `stroke120_fusion_fix_20260930/chapter_40/` 为父版本，保留其坐标，
直接把原始帧道路与空白投影到 map，而非对错误融合结果补面。
局部验证包：`data/chapter_maps/frame_projection_20260930/chapter_40/`。

分层缓存中的仿射视差平面可以严格展开为局部单应变换；同一块道路用同一个变换绘制道路、空白和 RGB。
`module/campaign_prototype/local_projection.py` 提供 `frame_plane_to_map` 及 `compose_registration`，
后者用于组合「Wiki 小地图 → 原始帧 → map」。这证明坐标链可共用，不代表真实 Wiki 配准已通过。
同一截图含多个高度时仍需要局部变换，不能对整张第 40 章截图只套一个单应。

168 个有可信锚点的 64×64 候选块中，42 个通过原坐标残差及两帧道路 IoU≥0.9 检查，
实际重绘 169,369 个画布像素；相对父版本补回 2,102 个背景像素、擦除 7,784 个旧道路／不确定像素，
共 17,016 个像素颜色发生变化。另 126 块未通过配准／一致性要求，原样保留，**没有完成整图修复**。
剩余主要问题是原始帧间的局部对应与高度估计；不通过降低补面门槛掩盖它。

每块保存来源帧、检查帧、ROI→map 单应、局部表面编号和画布范围。
`projection_examples.jpg` 为变化最大的三个块：左原始帧（黄色框为取用区域）、中旧 map、右原帧重绘；
`redraw_coverage.png` 标绿已绘制区域，`comparison.jpg` 为整图前后对比。
19 项相关测试、语法检查和真实缓存运行通过；全部重绘点的道路／空白和 RGB 均与来源投影逐点一致，
回投误差小于 0.000026 像素，未重绘区域、原始帧、深度缓存及全局坐标完全不变。

`surface_data.npz` 明确保留父版本，新增 `redraw_data.npz` 记录本次图像的来源和路面掩码，
`map_render_mode=projected_raw_patches`。本版是局部重绘验证包，尚未接入正式 Wiki 或小队移动消费路径，
不能把旧缓存的概率当作新图的完整数据模型；仍为 `navigation_ready=false`。
脚本及复现说明见 [原始帧投影重绘](minimap-layered.md#原始帧投影后二次绘制)。

## 已停止推进：道路面与边界联合修复

用户指出逐像素补洞过于保守，要求利用完整道路面和连续边缘判断补齐／删除。
本次最终输出为 `data/chapter_maps/surface_repair_final_20260930/chapter_40/`，
父版本为下节的 `raw_frame_repair_20260930/chapter_40/`。原始帧显示路面基本连续，
逐点选择不同高度／来源后再逐点筛选会破坏这种连续性，图标遮挡还会形成错误空白反证。

新工具先排除彩色图标及白字遮挡，再用局部多高度候选回投原始帧；
对同层相邻点联合最小化观测代价与边界长度，明确空白仍禁止补齐，高度突变降低跨点约束。
偶数帧选择高度、奇数帧复核固定候选；两组共同参与最终面筛选，不称为最终结果的独立留出验收。

| 对比上一版 | 数量 |
| --- | ---: |
| 新补背景像素 | 40,338 |
| 剔除道路／不确定像素 | 41,560 |
| 其中原有蓝色道路像素 | 718 |
| ≤256 像素的小碎片 | 58 → 5（剩 15 像素） |
| ≤256 像素的小空洞 | 1,283 → 71（剩 433 像素） |
| 最终路面 | 474,002 像素 |
| 依赖连续性推断的路面 | 84,288 像素 |

这次 **map 使用 `surface_road_mask` 渲染**；`terrain_probability` 仍保存原始逐帧支持比例，
没有把推断像素的观测概率伪造为 1。`surface_inferred_mask` 标记两组较低支持比例未达 85% 的路面，
可看 `inference_overlay.png` 的黄色区域。新增像素中 1,873 个原始支持不足一半，依赖邻面约束；
这些点不能直接当作已验证的移动目标。后续消费本版地图须显式采用面掩码和推断标记，
旧平面概率阈值流程尚未接入这一格式。

28 项相关回归、语法检查、104 帧实际处理通过。独立矩阵计算复核全部新增点及 10,000 个旧路面点，
共 50,338 点的原始帧概率／次数完全一致；全部路面来源颜色一致、回投误差小于 0.000031 像素。
原始文件、旧包及全局坐标参数不变；剩余真实／未决间隙保留，没有进行游戏内移动验证。
地图变得连续不代表跨层连接已确认，仍为 `navigation_ready=false`。

对比图：`comparison.jpg`、`detail_comparison.png`（左旧右新）；修改掩码可视图 `changes.png`（绿补、红删）。
证据与回执：`surface_repair_evidence.npz`、`surface_repair_report.json`、`verification.json`。
复核脚本：`tmp/ch40_stroke120_20260930/verify_surface_repair.py`。
复现说明见 [道路面修复](minimap-layered.md#道路面与边界联合修复)。

## 上一版：使用原始帧修复分层 map 的局部空洞与碎片

按用户要求继续优化 map，输出到 `data/chapter_maps/raw_frame_repair_20260930/chapter_40/`。
以去噪后的 `stroke120_fusion_fix_20260930/chapter_40/` 为父版本，保留 1396×1149 画布和全部相机、
投影、旋转、原点参数；旧包、104 张原始帧及逐帧深度缓存均未改写。

- 用邻近可信道路提出局部高度，52 帧选候选，另 52 帧复核；补回 13,213 个背景像素，
  将 8,011 个不确定像素提升为可信道路，隐藏 5,596 个没有充分多视角支持的孤立碎片像素。
- 面积不超过 256 像素的孤立分量从 674 个降为 58 个；同口径小空洞从 2,025 个降为 1,283 个。
  碎片仅在整块没有可靠核心时隐藏，具有支持的小路面保留。
- 同步更新 `surface_data.npz` 的概率、帧数、局部高度及来源坐标；`reference.png` 仅更新修补点的来源颜色，
  并未修好整张 reference。保留原始 coverage/raw_probability 和逐帧深度作为父版本证据。
- 22 项相关回归、语法检查及真实缓存运行通过；重新逐帧计算全部补点的道路概率一致，来源颜色完全一致，
  回投最大误差 0.000025 像素，地图变化精确对应修补／隐藏掩码。

`comparison.jpg` 为完整前后对照，`detail_comparison.png` 左旧右新；`repair_evidence.npz`、
`repair_report.json` 和 `verification.json` 保留修改证据。复核脚本为
`tmp/ch40_stroke120_20260930/verify_repair.py`，复现命令见 [分层重建说明](minimap-layered.md#原始帧局部修复)。
复杂层间边界、宽接缝和没有足够观测的区域仍有缺损；没有强行填平或重新拟合整体几何。
保留 `navigation_ready=false`，本次没有进行游戏内移动验证。

## 当前基线：恢复采用消除融合噪点后的分层重建

用户选择继续使用 `stroke120_fusion_fix_20260930/chapter_40/` 的 `local_parallax` 方案，
认为该版 `map.png` 基本符合地图，当前问题集中在 `reference.png`。
对应可供现有标注器打开的资料包为 `chapter40_package_20260930/chapter_40/`；
两个目录的 `map.png` SHA-256 均为 `ddfa3f44bd506fee890e12edaf5021da2b3b11da1f73ca3a01098ab2b3dce5ef`。
后续以此为基线，不再推进实拍优先或全局表面俯视实验；保留实验代码和数据供复核，未覆盖旧包。

已核对实际消费路径：人工标注默认显示 `map.png`，可选 `reference.png` 仅作视觉对照，
保存坐标始终绑定 `map.png`。现有平面 Wiki 自动匹配和移动定位／寻路读取
`source/map_data.npz` 的 `terrain_probability` 及投影数据，不读取 `reference.png`；
场景点击另外依赖实际落点标定。分层基线的等价证据在 `surface_data.npz`，尚未接入这些平面入口，
不能把文件改名或解除章节门槛当作已经支持第 40 章。

分层 `reference` 在最终全帧一致性检查之前生成；`map` 使用检查后的道路概率和帧数筛选，
所以实拍图仍可能显示底图已否决的道路候选。优化优先级是保留基线地图坐标，
明确可通行道路与不确定道路的数据口径、接入分层定位和目标换算，再让实拍图与同一可信几何对齐。
不以修好 `reference.png` 为自动移动的前置条件，也不因用户视觉认可而标记几何或移动验收通过。

## 第 40 章分层俯视与共用坐标

用户明确要求与普通章节相同的俯视产出，并实际支持小队移动与 Wiki 标注；单纯清晰的拼图不算完成。
本轮产出在 `data/chapter_maps/orthographic_package_20260930/chapter_40/`：730×1395，
104 帧缓存、6 个候选表面、同坐标实拍／道路图、原始证据及 20 项 Wiki 资料。
`surface_geometry` 统一了带表面身份的地图／截图转换，`SurfaceLocalizer` 为小队与 Wiki 共用定位入口；
Wiki 导入保留表面编号，移动落点辅助接口要求目标表面和自身标定对应。

**总体目标仍未完成。** 97,470 个像素被多个候选表面解释，真实叠层和错误重复仍未区分；
留出帧 4 的小队定位因表面配准不一致失败，抽查 3 张 Wiki 图也未获得足够对应点。
既有移动会话仍只支持已标定的第 38 章；没有第 40 章落点标定或已验证的跨层路线。
因此保留 `navigation_ready=false`，没有修改章节门槛或发送游戏点击来伪造移动支持。

新包可在既有标注器打开并进行二维人工标注，20 个 Wiki 点仍待确认。
文件、哈希、矩阵与标注器读取检查通过；实际定位失败证据和继续入口见
[分层俯视说明](minimap-orthographic.md)。旧完整资料包及原始扫描均保留。

## 第 40 章实拍优先实验

按「先拼好 reference，再从它生成 map」导出到 `data/chapter_maps/reference_first_20260930/chapter_40/`。
使用现有 104 帧缓存及拟合相机；84 帧用于拼图，20 帧留作未取色回投检查。
整帧投到共同参考平面，视野中心权重选择完整 RGB 内容，背景和空白参与覆盖；
`map.png` 直接从 `reference.png` 提取道路，二者均为 **1382×1019**。
实拍纹理和道路间隙明显恢复；`comparison.jpg` 为旧／新实拍对照，`preview.jpg` 为新实拍／道路并排图。

- 107 个输入哈希、3 个代码哈希、图片和数组一致性通过；每个实拍像素都有来源帧／ROI 坐标，来源颜色完全一致。
- 未取色的 20 帧贡献像素为 0；双向轮廓距离中位数 4.12px、P95 29.73px，道路 IoU 中位数 0.672、最低 0.362。统计只覆盖共同可见区域，覆盖率中位数 88.8%、最低 32.9%。
- 留出帧仍参与此前缓存几何拟合，不能视作独立几何验收。共同平面仍有高度视差与接缝错位，保持 `navigation_ready=false`，未替换完整资料包或接入小队移动。
- 新脚本及测试语法检查、21 项相关回归、真实缓存运行和逐像素导出检查通过。没有启动新采集；运行进程已结束。

复现命令、文件含义及限制见 [实拍参考图实验](minimap-layered.md#整帧实拍参考图实验)。
本机校验脚本：`tmp/ch40_stroke120_20260930/verify_reference_first.py`；回执：输出目录 `verification.json`。

## 第 40 章完整扫描资料包

按章节扫描输出结构归集到 `data/chapter_maps/chapter40_package_20260930/chapter_40/`，
同级 `chapter_40.zip` 及 `chapter_40.zip.sha256` 为可移交副本。包内 [README](../data/chapter_maps/chapter40_package_20260930/chapter_40/README.md)
包含本章信息、文件用途、完整 Wiki 条目及标注器打开命令；`chapter.json` 为结构化总览。

- 共 265 个文件，约 171MB；包含 104 帧采集、全部分层模型／逐帧深度、底图、参考图、扫描摘要、校验与日志。
- 普通 14 项、困难 6 项 Wiki 条目及 20 张原图均归集，来源原文保留。`annotation_tasks.json` 列出 20 项待办，未知坐标及表面身份为 null；`annotations.json` 初始化为空，不虚构收集品或机关位置。
- 图像显示坐标规范化为 `pixel`，分层几何保留 `schema_version=2`、`local_parallax` 及单独的 `surface_coordinates`。现有标注器实际浏览器加载、底图／参考图切换、普通／困难切换均通过；没有启动移动或修改真实标注。
- 132 张图片解码、地图与几何数据核对通过；目录与 ZIP 均逐项通过 264 个清单条目的大小／哈希核对，清单自身不递归包含。原重建目录及旧 `current/chapter_40` 未改写。
- 本包可用于二维人工标注和资料复核；20 项坐标、跨层连接、独立整图几何与本章移动标定仍待完成，保持 `navigation_ready=false`。未生成平面导航缓存或把分层重建伪装成 `joint_grid_road`。

本机生成／验证工具：`tmp/ch40_stroke120_20260930/package_chapter.py`、`check_package.cjs`；
生成器拒绝覆盖已有包。浏览器回执 `package_browser_verification.json`，截图 `package_editor.jpg`。

## 第 40 章 120px 重扫

用户本次要求先提交当前工作区，再把扫描步长改为 120 并重新扫描第 40 章。原工作区已保存为本地提交 `f0ac1bdb`，分支 `auto_push`，未推送。步长及本节更新属于该提交之后的改动。

- 单章和批量入口的默认探索拖动均改为 120px；最初两次标定保持 240px，关键帧间距保持 80px，高频采样间隔保持 0.03 秒。
- 现场核对普通第 40 章后，12:11–12:12 全新采集，正常退出。共 **104 帧、88 次拖动**：2 次标定、86 次探索；逐条核对探索行程均为 120px。状态 `roads_exhausted`，未解决道路前沿 0；最终保持紧凑小地图，鼠标控制已释放。
- 新数据位于 `data/chapter_maps/stroke120_20260930/chapter_40/source/`。104 张原始帧和章节确认图均解码通过；同章 `capture_verification.json` 保存计数、参数及 106 个文件的 SHA-256，`capture_preview.jpg` 为抽帧预览。旧地图和标注未覆盖。
- 本次为独立单章补采，不代表此前 41→1 批次恢复，也不把 `roads_exhausted` 当作完整相机域或分层几何验收。
- 原工作区验证：35 个 Python 文件语法检查、129 项回归及标注器浏览器流程通过；步长调整后，2 个 Python 文件语法检查及采集／章节 57 项回归通过。

运行入口：`.venv\Scripts\python.exe -X utf8 -u tmp/ch40_stroke120_20260930/capture.py`，该入口拒绝覆盖已有目录；日志与退出码见同目录 `capture.log`、`capture.exit`（0）。只读核对：`.venv\Scripts\python.exe -X utf8 tmp/ch40_stroke120_20260930/verify_capture.py`。

新帧的分层预览使用：`.venv\Scripts\python.exe -X utf8 -u -m dev_tools.minimap_layered --source data/chapter_maps/stroke120_20260930/chapter_40/source --output data/chapter_maps/stroke120_layered_20260930/chapter_40`。命令正常结束，导出 **1396×1149** 预览及 104 份深度数据；105 个源文件及副本哈希一致，地图哈希、图片尺寸、数组维度与投影／相机元数据检查通过，回执为输出目录中的 `export_verification.json`。

目视预览仍有重影、缺口和破碎道路，保持 `needs_geometry_review`、`navigation_ready: false`；没有替换正式地图。1137 条轨迹的留出误差中位数约 2.30px、P95 约 8.89px，只是局部轨迹指标；本次没有重新进行独立整图几何或真实移动验证，不能据此宣布缩短步长已经解决高低差重建。

随后修复了融合阶段浮点精度不一致导致的深度／来源漏写，并固定重复命中像素的并列选择。
使用相同 104 帧及深度缓存导出 `data/chapter_maps/stroke120_fusion_fix_20260930/chapter_40/`：
缺少深度／来源的初步道路像素 **50,217 → 0**，道路内部 1–9px 背景小孔 **12,217 → 899**。
相机、深度模型、初步道路支持率和判定阈值不变；原始数据及逐像素来源校验通过，15 项回归通过。
大范围几何错误仍保留，未升级为导航地图。对照图、校验口径和复现入口见
[分层重建说明](minimap-layered.md#第-40-章-120px-缓存的融合针孔修复)。

## 此前全量任务：目标与顺序

最新用户要求：**先下载所有章节的 Wiki 收集品数据。** 此步骤立即执行，不等待地图修复；仅下载攻略、条目和原图，标注仍在新地图验收后进行。

1. 跟进当前目标 `codex://threads/01a0ef9c-49e8-7a30-9f44-0c8899991728`（hostId：`local`），核对分层重建方案及执行结果。用户已将目标切换到此恢复任务，旧聊天不再作为当前监控对象。
2. 确认方案和必要验证通过后，先备份当前地图，再全新采集普通剧情第 41 至第 1 章，共 41 章。使用独立新目录，不能因旧包已存在而跳过本次重扫。
3. 所有目标章的新扫描及正式地图包验收后，执行 Wiki 普通／困难收集品标注。来源难度分别保存，不猜测低置信度坐标，不复制旧底图坐标到新图。
4. 汇总各章扫描、地图质量、标注通过／待复核／失败数量、证据与恢复命令；不得将扫描结束或测试通过当作全部任务完成。

本轮未获 Git 推送授权。保留共享工作区原有未提交改动，不与原任务并行控制游戏或修改其正在处理的源码。

## 此前全量任务：10:47 检查点

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

## 2026-09-30 正式单章扫描入口更新

`dev_tools/minimap_chapters.py --process-3d` 已接入分层重建与原始道路区域投影重绘；
关闭选项继续原平面重建。独立标注编辑器新增章节、3D 处理、120 步长、扫描／停止／打开结果按钮，
每次扫描写入编辑器根目录的独立 `scans/<任务 ID>/chapter_XX/`。
扫描与小队移动在同一服务中互斥，中间阶段不进入地图列表，失败保留原始帧。
这是入口集成更新，不代表此前计划的 41→1 批次已执行，也不替代下述导航验收门槛。

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
