# 战役地图标定脚本与测试步骤

本文整理普通平面章节的小地图／主场景位移标定入口、复测步骤与验收条件。
采集器由最近四个会话使用的脚本整理而来；数据与现场结果核对日期为 2026-10-01。

## 适用范围与结果含义

- 按普通章节 48→1 递减执行；39、40 显式跳过，声明了 `processing_3d` 或非空 `coordinate_model` 的地图也跳过。
- 输入为 `data/chapter_maps/current/chapter_NN/` 中已有的正式地图，不重新扫描地图。
- 中文 Windows 客户区固定为 `1776×999`；展开小地图 ROI 为 `(644, 280, 1130, 742)`，尺寸 `486×462`。
- 每章采集 6 个训练样本和 3 个独立验证样本，得到采样道路附近的局部位移标定。
- 新结果写入该地图包的 `movement_calibration/normal/flat_calibration.json`。
  它与分层地图的 `movement_calibration/normal/calibration.json` 是不同产物。
- 当前平面移动仍使用 `module/campaign_prototype/assets/calibration.json` 的共享标定。
  新采集文件带有 `runtime_auto_loaded=false`、`whole_chapter_verified=false`，不会自动接入移动执行器。
  管理界面显示 `shared`、地图扫描完整、到达某一点，都不能证明逐章标定已通过。

## 脚本索引

| 入口／模块 | 用途与边界 |
| --- | --- |
| [dev_tools/campaign_calibrate.py](../dev_tools/campaign_calibrate.py) | 本次整理的采集入口；指定起止章节和输出目录，保存进度、截图、样本及通过的标定。会移动小队并切换章节。 |
| [dev_tools/minimap_chapters.py](../dev_tools/minimap_chapters.py) | 提供章节 OCR、切章等待、进度写入；其独立命令用于地图扫描，不是点击位移标定入口。 |
| [prepare_package.py](../module/campaign_prototype/prepare_package.py) | 从静态地图导出独立实验包，绑定共享标定与文件哈希；只处理本地数据。 |
| [check_controls.py](../module/campaign_prototype/check_controls.py) | `--cycles 0` 截图检查；`1..10` 次展开／收回检查，不移动小队。仍会取得游戏输入控制。 |
| [manual_move.py](../module/campaign_prototype/manual_move.py) | `prepare` 和 `GameSession` 负责快照、身份检查、定位、点击及停稳；其 `action=calibrate` 用于分层地图，平面批量标定走上面的新入口。 |
| [arrow_anchor.py](../module/campaign_prototype/arrow_anchor.py)、[movement_feedback.py](../module/campaign_prototype/movement_feedback.py) | 箭头周期采样与地面锚点恢复；本采集器传入 `allow_scene=False`，必须获得箭头锚点。 |
| [camera_navigation.py](../module/campaign_prototype/camera_navigation.py) | 场景投影、有效落点与紧凑小地图停稳检测。 |
| [surface_motion.py](../module/campaign_prototype/surface_motion.py) | `fit_calibration` 执行两轴拟合、独立样本验证和支持域检查，可离线运行。 |

历史入口保留在本机，不作为后续修改入口：

- `log/chapter_calibration_20261001/calibrate_chapters.py`：原批次。
- `log/chapter_calibration_restart_20261001/calibrate_chapters.py`：最新重启批次，本次迁移来源。
  来源文件 SHA-256：`1adfece5a7b23c2e124783a56f70009df0bff0ce1da469adca8f8f570bffa03a`。

整理后的脚本只调整仓库根目录解析、命令说明及 `--output`，沿用来源脚本的采样和验收流程。
历史 `log/`、本地 `data/` 和 `tmp/` 不随 Git 分发；新克隆仓库需另行准备地图和模型。

## 测试顺序

以下 PowerShell 命令从仓库根目录执行。使用已有 `.venv`，无需重复安装依赖。
现场步骤需要可用的项目输入驱动，以及本地 OCR 模型 `bin/paddleocr/PP-OCRv5_mobile_rec_infer`。
先退出其他移动／扫描任务，进入与 `--start` 对应的普通章节道路页面，保持游戏前台且客户区完整可见。
采样期间不要拖动窗口或抢占输入；道路应有两轴活动空间，小队箭头不被收集品提示、角色或弹窗遮挡。

### 1. 离线入口与核心回归

```powershell
.venv\Scripts\python.exe -m py_compile dev_tools/campaign_calibrate.py
.venv\Scripts\python.exe -X utf8 dev_tools/campaign_calibrate.py --help
.venv\Scripts\python.exe -m unittest discover -s tests -p test_surface_motion.py
.venv\Scripts\python.exe -m unittest discover -s tests -p test_arrow_anchor.py
.venv\Scripts\python.exe -m unittest discover -s tests -p test_camera_navigation.py
.venv\Scripts\python.exe -m unittest discover -s tests -p test_movement_feedback.py
```

| 回归文件 | 需要保障的行为 |
| --- | --- |
| `tests/test_surface_motion.py` | 留出坏样本、单轴样本、支持域外验证点和不同表面混用应拒绝。 |
| `tests/test_arrow_anchor.py` | 箭头颜色／尺寸／位置、动画周期、漏检与歧义处理正确。 |
| `tests/test_camera_navigation.py` | 镜头规划与小队位置变化处理、停稳判断等公共导航行为。 |
| `tests/test_movement_feedback.py` | 小地图与静态地面证据、箭头恢复和目标提示处理。 |

这些回归不会取得游戏控制。相关输入没有变化且检查已通过时无需重复运行。
本地截图回放与数学拟合通过，仍需下面的现场采样才能证明该章节的点击映射有效。

### 2. 地图包与控件预检查

以第 48 章为例，选择尚未存在的预检查目录；重测时改目录名和标签：

```powershell
.venv\Scripts\python.exe -m module.campaign_prototype.prepare_package --source data/chapter_maps/current/chapter_48 --destination log/chapter_calibration_preflight_01/map --chapter 48
.venv\Scripts\python.exe -m module.campaign_prototype.check_controls --package log/chapter_calibration_preflight_01/map --chapter 48 --cycles 0 --tag snapshot_01 --output log/chapter_calibration_preflight_01 --stop-file log/chapter_calibration_preflight_01/STOP
.venv\Scripts\python.exe -m module.campaign_prototype.check_controls --package log/chapter_calibration_preflight_01/map --chapter 48 --cycles 3 --tag controls_01 --output log/chapter_calibration_preflight_01 --stop-file log/chapter_calibration_preflight_01/STOP
```

检查初始截图对应正确章节与尺寸；展开图应为 `486×462`，收回后显示紧凑小地图；
控件回执的 `cycles_completed` 应为 3。该步骤只验证包和控件，不产生新位移标定。

### 3. 单章采样

先对当前章节完整跑一遍，明确给出相同的起止章节。新批次用新输出目录：

```powershell
.venv\Scripts\python.exe -X utf8 -u dev_tools/campaign_calibrate.py --start 48 --end 48 --output log/chapter_calibration_run_01
```

如需保存终端日志，先创建输出目录，使用新日志文件名：

```powershell
New-Item -ItemType Directory -Path log/chapter_calibration_run_01 -Force
.venv\Scripts\python.exe -X utf8 -u dev_tools/campaign_calibrate.py --start 48 --end 48 --output log/chapter_calibration_run_01 *> log/chapter_calibration_run_01/run_48_01.log
```

两种写法任选其一，不要同时启动。脚本按以下顺序工作：

1. 校验地图绑定并定位小队。每次地图观测先收回再展开刷新，之后收回面板采箭头。
   入口视野以窄路为主时，游戏与底图在道路边界上的 1～2 像素渲染差异会把 IoU 拖到 0.85 验收线下
   （不是迷雾遮挡，道路在游戏中均可见；宽路视野的周长面积比低，IoU 自然回升），
   按先四向、再斜向的顺序最多盲移八步走向更开阔的视野，
   每次移动后重新观测，仍无法通过置信度检查才判定失败。
2. 采集白／橙箭头动画周期：最多 48 帧、间隔至少 0.2 秒，在 16 帧窗口中检查稳定性；
   动画极值中点向下补偿 90 客户区像素，得到原标定语义的地面锚点。
   入口触发台座上小队显示橙色圆环且不显示箭头：先确认定位可信且 60 地图像素内无敌人标记，
   再按地图净空选择 400 地图像素内的开阔道路方向盲移（无法投影时退化为画面下方盲移），
   重新观测后重采；入口路径可能有连续多个台座，最多引导盲移四次，每次从新位置重新选路，
   引导落点零位移时追加一次四向盲点；仍无箭头则带现场失败。
3. 检查当前位置道路净空。低于 35 地图像素时，在 120 地图像素内选择净空至少 50 的可达位置；
   120 像素内没有净空至少 50 的位置时，改为选择净空明显高于当前的位置逐步爬坡接近开阔区域；
   完全没有更高净空的位置才判定失败。达到 35 后，若 120 像素内还有净空高出至少 8 的位置，
   继续向广场中心走，直到净空达到 45 或附近没有更好位置；更好位置不可达时接受当前合格站位。
   每次移动后重新定位、重采箭头，最多移位八次。
   目标须可点击，且离敌人标记至少 35 地图像素。
4. 依次执行下表中的 9 次相对锚点点击。每次先预测路径，检查路径净空至少 8 地图像素、落点有效且远离敌人。
5. 单击后保持紧凑小地图等待停稳，再展开定位。需连续四帧的小队圆环和道路都稳定；
   圆环坐标跨度不超过 6 紧凑图像素、共同可见道路像素至少 200、道路变化比例低于 0.005。
   最多 120 轮，每轮等待 1 秒另加截图／识别耗时；超时不追加点击。
6. 停稳后实测地图位移至少 2 像素才记为样本。将终点投回点击前的 ROI，再转为旧标定平面位移；
   与实际点击减锚点的客户区位移配对，避免把镜头回正误算为小队移动。
7. 前 6 次拟合，后 3 次独立验证；全部通过才保存新的标定文件。

| 样本 | 相对本次箭头地面锚点的点击偏移（客户区像素） | 用途 |
| --- | --- | --- |
| 1～6 | `(40,25)`、`(-40,-25)`、`(40,-25)`、`(-40,25)`、`(0,25)`、`(0,-25)` | 训练，覆盖两轴 |
| 7～9 | `(-15,-10)`、`(15,-10)`、`(0,10)` | 独立验证 |

### 4. 验收与证据检查

| 检查项 | 通过条件 |
| --- | --- |
| 数量 | 至少 6 个训练样本、3 个独立验证样本；当前脚本恰好采 9 个。 |
| 两轴覆盖 | 训练源坐标中心化后秩为 2，条件数不超过 10。 |
| 拟合误差 | `training_max_px <= 6`、`validation_max_px <= 8`，均为客户区像素误差。 |
| 支持域 | 所有验证源点在训练源点凸包内。 |
| 固定偏置 | 仿射矩阵平移项的长度不超过 8 客户区像素。 |
| 绑定 | `map.json`、`map.png`、`annotations.json`、`source/map_data.npz` 的 SHA-256 与源包一致。 |
| 结论 | 进度中 `status=validated`；已有匹配绑定且标为 validated 的结果才会返回 `reused`。 |

每次尝试的证据位于 `<output>/chapter_NN/<时间戳>/`：

| 文件 | 核对内容 |
| --- | --- |
| `map/`、`initial_observation.json`、`observe_*_location.json` | 本次运行地图快照、定位矩阵、小队位置。 |
| `reference.png`、`reposition_*.json` | 初始场景与开阔道路移位目标；没有移位时无后者。 |
| `sample_XX_before.png`、`sample_XX_after.png` | 每次采样点击前后截图；失败点击可能只有 before。 |
| `samples.json`、`observations.json` | 已完成的样本对及其点击、锚点、前后定位；不完整样本不会追加。 |
| `calibration.json` | 全部通过后的局部拟合结果，随后发布至源地图包的 `flat_calibration.json`。 |
| `failed_field.png`、`error.txt` | 异常现场及堆栈；部分初始化失败可能没有现场图。 |
| `<output>/progress.json` | 所有尝试的追加记录，同一章可以有多条；按最新一条判读当前尝试。 |

发布时若已有同名标定，先改名保留为 `flat_calibration_<时间戳>.json`。
复用目前只核对源文件绑定、保存状态和读取误差字段，不重新跑现场，也不重新验证历史证据图片。
复核时仍应检查完整的样本与验证指标。

### 5. 批量执行、停止与续跑

单章流程可用后，手动确认当前游戏章节与 `--start` 一致，再启动：

```powershell
.venv\Scripts\python.exe -X utf8 -u dev_tools/campaign_calibrate.py --start 48 --end 1 --output log/chapter_calibration_run_01
```

单章失败会记录 `failed`，再尝试切到上一章；切章验证失败则终止批次。
已有有效标定会复用，39、40 等排除项记录为 `skipped_nonflat`。
复用和跳过也会继续切章，所以它们不意味着整个批次无需游戏控制。

停止可用 `Ctrl+C`，或从另一终端创建本批次的停止文件：

```powershell
New-Item -ItemType File -Path log/chapter_calibration_run_01/STOP -Force
```

停止文件不会自动清除。准备续跑时，确认旧进程已退出，再删除这一个停止文件：

```powershell
Remove-Item -LiteralPath log/chapter_calibration_run_01/STOP
```

采样会话还检查 `log/campaign_prototype/STOP` 这个全局停止文件；若存在，先确认其用途，
准备恢复游戏控制后再处理，不能只清除本批次 STOP 就假定已可运行。

根据当前游戏章节显式设置新的 `--start`；例如当前为普通第 47 章时使用 `--start 47 --end 1`。
同一输出目录会追加进度，并为每次尝试生成新时间戳目录；不会从未完成的第几个样本继续拟合。
不要以进程退出码 0 或 `batch_finished` 作为通过依据：采集器会捕获单章失败后正常结束循环，
必须检查 `progress.json` 的逐章状态和正式标定文件。

## 已有证据与复测起点

最近四个会话依次为：

- `01a0f68f-e451-7661-ba45-bc405bd99cb5`：48→1 标定要求，并确认可跳过 39、40 等非平面地图。
- `01a0f6cf-f12a-7a43-b00d-41e7499c7fe1`：批量采集脚本与原批次执行记录。
- `01a0f71f-6dd6-7653-9d4e-fa6a91d8f924`：从 48 章重启的执行记录。
- `01a0f7a4-13eb-7e90-898e-b7105f63f2cf`：当前来源脚本和最新一次采样证据。

### 2026-10-03 大批量结果（log/chapter_calibration_run_01）

截至 2026-10-03 晚，33 章已落盘 `flat_calibration.json`（status=validated）：
48、47、46、44、43、42、41、38、37、35、34、33、31、30、29、28、27、25、24、23、22、21、19、18、17、12、11、9、8、6、4、3、1；
39、40 为非平面跳过。
其余 13 章（45、36、32、26、20、16、15、14、13、10、7、5、2）已写入 `status=estimated_consensus` 的共识标定：
取 33 个 validated 章矩阵的逐元素中位数，support 为各章 support 的包络盒。
共识矩阵在 33 个已标定章的全部样本上误差为：max 中位 6.91px、P90 10.68px、最差 12.32px（ch42）；mean 中位 3.80px。
这些是推算占位值，未现场测量；批量脚本的复用判定要求 `status=validated`，因此后续批次仍会尝试对它们实测标定，
实测成功时按既有流程改名保留推算值并替换为实测结果。

未通过实测的 13 章及归类（决定改用共识值前）：

| 归类 | 章节 | 说明 |
| --- | --- | --- |
| 垂直样本退化 | 45、20 | 垂直点击 `(0,±25)`、`(0,10)` 在这些章的镜头几何下地图位移恒低于 2 像素门槛，第 5/6/9 个样本必然失败，与站位无关。 |
| 采样测量离群 | 36 | 两次 9 样本齐全，但第 3、5 个训练样本系统性离群（训练误差 9~15px，限 6），疑似特定点击方向的镜头回正修正失真。 |
| 地面不平拟合差 | 26 | 训练误差普遍 5~9px、验证 8.75px，略超线；沙漠起伏地面，仿射模型整体贴合差。 |
| 漂移出界 | 16、32 | 9 样本轨迹的净漂移把小队推出小广场；32 还叠加调查触发点和大振幅箭头。 |
| 未人工复核 | 15、14、13、10、7、5、2 | 多为入口台座（橙环隐藏箭头）或同类轨迹问题，可按 42/34/31/21 的人工流程逐章处理。 |

人工补跑流程（本章已多次验证）：`tmp/goto_chapter.py N` 切章 → 截图确认小队状态
（橙环台座就向开阔路盲点一步唤醒箭头）→ 离线净空分析选广场 →
`module.campaign_prototype.manual_move` 预导航或手动盲点到位 →
`dev_tools/campaign_calibrate.py --start N --end N` 单章执行。
漂移类失败先读最新 `observations.json` 的净漂移方向，把小队放到广场逆漂移侧边缘再跑。

历史记录：截至 2026-10-01 核对时，正式地图目录内没有 `flat_calibration.json`。
原批次进度覆盖 48～43 章的 8 次失败尝试，原因包括箭头不可用、停稳超时和失焦；
重启批次有第 48 章的两次失败尝试。未运行章节不能记为通过或已跳过。

最近一次运行证据为：

```text
log/chapter_calibration_restart_20261001/run_48_resume.log
log/chapter_calibration_restart_20261001/progress.json
log/chapter_calibration_restart_20261001/chapter_48/1790862630044577500/
```

该次已经通过开阔道路移位与箭头采样，`samples.json` 中有 **1 个完整样本**。
第 2 次采样点击为客户区 `(851, 479)`，随后等待停稳超时，未写入第二个完整样本，也没有发布新标定。
后续应先复核第 48 章停稳路径，再完成单章 6+3 样本；不能从“地图已完整”推断剩余章节标定完成。

相关真实截图回放与实机复核位置：

- [第 48 章偏离中央的箭头](../tests/fixtures/squad_arrow/ch48_shifted_scene.md)：偏离中央、白／橙箭头、遮挡和歧义。
- [第 48 章移动反馈](../tests/fixtures/movement_feedback/README.md)：紧凑小地图圆环与收集品提示。
- [镜头导航回放](../tests/fixtures/camera_navigation/README.md)：小队位置变化、远点与镜头边界。

这些记录主要来自中文 `1776×999` 客户区；不能推广为其他语言、尺寸或整章移动已验收。
