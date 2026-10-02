# 自动推图 YOLO 接入方案与替换清单

更新：2026-10-02。本文定义接入范围、替换位置、依赖和验收标准。原型调用点已接入 YOLO 适配器，第四版模型随应用分发，验收重点为普通关卡识别；EX 类别仅作辅助，敌人移动模式通过弹窗确认并排除 EX。实现状态见[实施记录](campaign-yolo-progress.md)，不能将工具或训练完成视为识别替换完成。

范围以 [战役地图独立原型](../module/campaign_prototype/README.md) 为主，并盘点活动推图与战斗模块可复用的部分。当前原型尚未形成持续推图闭环，检测器升级不改变[自动推图计划](autopush-plan.md)中的导航、单场战斗和恢复任务。

## 建议结论

让 YOLO 负责识别小队箭头、收集提示、小地图普通敌人和小队圆环，并保留辅助 EX 类别；保留道路配准、坐标投影、OCR 和有界动作状态机。首批与第二批作为同一交付范围，包含展开和紧凑小地图；可以分步开发，但全部路径通过验收后才算替换完成。

建议从 YOLO11n 的目标检测模型微调开始，导出 ONNX，在运行端使用 ONNX Runtime CPU。这是候选基线，不是性能或准确率结论；YOLOv8n 也可作为对照，但首版只实现并验证一种导出契约。通用预训练权重不认识 NIKKE 的这些图标，不能安装库后直接替换现有识别。

## 替换清单

| 顺序 | 识别对象与现有位置 | 接入方式 | 保留的约束 |
| --- | --- | --- | --- |
| 首批 | 主场景白／橙小队箭头：[live.py](../module/campaign_prototype/live.py) `squad_arrow` | 用 YOLO 找箭头候选，替代全场景多尺度模板搜索；候选内精定位后返回原语义的中心 | 多个小队候选时拒绝定位；[arrow_anchor.py](../module/campaign_prototype/arrow_anchor.py) 的静止检查、周期采样和地面换算继续生效 |
| 首批 | 带放大镜的橙色收集提示：[movement_feedback.py](../module/campaign_prototype/movement_feedback.py) `collectible_indicator` | 替换透明模板匹配，输出提示位置、类别和检测分数 | 排除小地图计数图标与 HUD；[manual_move.py](../module/campaign_prototype/manual_move.py) 中发现提示即停止的行为保留，不据此宣布已拾取 |
| 首批 | 小地图普通敌人／EX：[goto.py](../module/campaign_prototype/goto.py) `normal_enemy_markers` | 用不同类别代替红色轮廓的实心／空心规则；普通敌人中心再按本帧矩阵投影 | 边缘截断、计数器区域排除；已识别 EX 或类别冲突不进入普通目标表；接触后用弹窗确认，EX 关闭一次并终止本次移动 |
| 第二批，同次交付 | 展开小地图圆环：[minimap_reconstruct.py](../dev_tools/minimap_reconstruct.py) `detect_markers`；紧凑小地图圆环：[wiki_collectible_match.py](../dev_tools/wiki_collectible_match.py) `minimap_masks` | YOLO 定位圆环候选，候选内拟合中心；通过原型适配器接入 | 不能将视野中心当小队；紧凑和展开模式单独验证；先不改变地图采集器的默认识别 |
| 有数据后 | Boss、机关、区域入口、场景敌人 | 对实际需要的对象补类别和标注，再接目标选择器 | 当前没有统一可靠识别器可以直接替换；发现对象不等于可达、机关已激活或章节完成 |
| 后续可选 | 战斗准备／EX 弹窗：[goto.py](../module/campaign_prototype/goto.py) `battle_popup_score`、`check_battle_popup` | 固定模板易受皮肤或布局影响时再训练；以页面状态与按钮联合判定 | YOLO 分数不能代入原来的模板相关分数阈值；未知页面不继续移动 |
| 后续可选 | 主线场景剧情标记：[semi_combat.py](../module/daemon/semi_combat.py) `MAIN_STORY_MARK_IN/OUT` | 可复用场景检测器，但需独立样本及点击偏移验证 | 该模块使用设备截图，与原型原始客户区坐标不同，不能直接共享点击坐标 |
| 后续可选 | 活动关卡标记：[story.py](../module/event/story.py)；战斗状态：[semi_combat.py](../module/daemon/semi_combat.py)、[auto_tower.py](../module/daemon/auto_tower.py)、[simulation_room/event.py](../module/simulation_room/event.py) | 反复维护的图标可集中检测；固定按钮目前继续模板识别 | 活动关卡图标及战斗页面需另建数据集；主线训练结果不代表活动、塔或模拟室支持 |

小队圆环不能只改一个调用点。至少需要覆盖 `adaptive.py`、`probe.py`、`camera_navigation.py`、`surface_localizer.py`、`parallax_localizer.py`、`parallax_movement.py`、`surface_motion.py` 和旧 `goto.py`，避免定位与停稳检查使用不同的中心定义。优先新增原型范围的统一适配器，不把业务检测器反向塞入通用采集工具。

以下是原型内需要切换的完整调用清单；路径均相对于 `module/campaign_prototype/`：

| 文件／入口 | 当前识别入口 | 目标入口与输出 |
| --- | --- | --- |
| `live.py:squad_arrow` | 白／橙掩码与全场景多尺度模板 | `perception.squad_arrow` → 客户区箭头中心或 `None` |
| `movement_feedback.py:collectible_indicator` | 透明模板匹配 | `perception.collectible_indicator` → 类型、客户区位置及 YOLO 置信度 |
| `goto.py:normal_enemy_markers` | 红色轮廓、实心／空心规则 | `perception.normal_enemy_markers` → 排除 EX 后的普通敌人地图坐标 |
| `goto.py:Localizer.locate_frame` | `mr.detect_markers` | `perception.detect_markers` → 同帧投影后的小队／敌人坐标 |
| `adaptive.py`、`probe.py`、`camera_navigation.py` | `goto.mr.detect_markers` | 统一从 `perception.detect_markers` 获取观测 |
| `surface_localizer.py`、`parallax_localizer.py`、`surface_motion.py` | 直接导入 `dev_tools.minimap_reconstruct.detect_markers` | 改用原型适配器，保留 `terrain` 道路处理 |
| `parallax_movement.py` | 两处局部导入 `detect_markers` | 两处一起切换，覆盖正常定位和移动后恢复 |
| `camera_navigation.py:wait_for_squad` | 局部导入 `wiki_collectible_match.minimap_masks` | `perception.minimap_masks` → 紧凑图圆环中心，同时保留道路停稳判据 |

`detect_markers` 中的敌人观测用于定位，不能直接作为普通敌人点击清单；点击候选必须经过 `normal_enemy_markers` 的类别排除和后续弹窗复核。对应测试中的旧导入与 mock 边界也要同步更新，避免测试仍替换旧函数而遗漏新路径。

## 继续使用现有算法的部分

| 能力 | 相关位置 | 原因 |
| --- | --- | --- |
| 章节号、收集计数、关卡文字 | `minimap_chapters.py`、`movement_feedback.collectible_counter` | YOLO 能找到文字区域，具体数字仍交给 OCR；已有数字范围、置信度和连续确认规则保留 |
| 道路提取、地图拼接和道路配准 | `minimap_reconstruct.py`、`adaptive.py`、`match_coarse.py` | 检测框不提供道路像素及精确几何；若以后改道路分割，需要单独的分割模型和像素标注 |
| 透视变换、局部地面标定、分层地图定位 | `local_projection.py`、`surface_motion.py`、`surface_localizer.py`、`parallax_localizer.py` | YOLO 只提供图像中的观测，不能替代 ROI→地图→地面点击的坐标转换 |
| 寻路、镜头平移、遮挡恢复、停稳和到达判定 | `camera_navigation.py`、`movement_feedback.py`、`manual_move.py` | 检测结果是这些流程的输入，不能由单帧框推断已到达、已拾取或可安全点击 |
| 小地图开关与输入保护 | `DriverWindow.reset_minimap`、`runtime.py` | 继续使用现有状态转换、焦点检查、STOP 和鼠标释放机制 |

## 需要引入什么

### 运行端

本机主运行环境已核对：Python **3.9.13**、OpenCV **4.10.0**、NumPy **1.24.4**、ONNX Runtime **1.19.2**。仓库中的 OCR ONNX 文件不是推图目标检测模型。

| 项目 | 是否需要 | 说明 |
| --- | --- | --- |
| 游戏专用 `.onnx` 权重 | 必须 | 由标注数据微调和导出，包含约定类别；不能使用 COCO 通用权重直接识别图标 |
| 模型说明文件 | 必须 | 保存模型哈希、类别顺序、输入尺寸、颜色顺序、归一化、输出布局、阈值、训练／导出版本和已验证范围 |
| `onnxruntime` CPU | 推荐 | 增加一个直接推理依赖，另有传递依赖；不要求 CUDA，不加载 PyTorch |
| OpenCV、NumPy | 已有 | 负责图像裁切、缩放、坐标还原、NMS 和局部精定位 |
| `torch`、`torchvision`、`ultralytics` | 推荐方案的运行端不需要 | 留在训练／导出环境 |
| GPU 推理运行库 | 可选 | CPU 测速不满足要求时再评估；需要按显卡、驱动及 CUDA/cuDNN 版本选择，CPU 与 GPU 发行包不混装 |

运行依赖已单列到 [requirements-yolo.txt](../requirements-yolo.txt)，锁定 `onnxruntime==1.19.2`，与本机 Python 3.9 环境兼容；无需为此次接入升级主环境 Python。其他机器在已有基础环境中安装时使用 `.venv\Scripts\python.exe -m pip install -r requirements-yolo.txt`。库安装成功不代表模型准确率通过验收。

另一个可行后端是已有的 `cv2.dnn.readNetFromONNX`，可减少新增运行依赖；但必须用实际导出模型验证 OpenCV 的算子、输出及速度支持。首版选择一个后端完成验证，不同时维护两套推理实现。

### 训练与导出端

使用独立的 Python 3.11 环境或训练机器，安装并锁定 `ultralytics`、`torch`、`torchvision`、`onnx` 及导出所需依赖。NVIDIA GPU 可显著缩短训练时间，CPU 也能训练小模型；实际耗时和显存需求需按输入尺寸、批量与数据量测量。

当前版本清单见 [dev_tools/requirements-yolo-train.txt](../dev_tools/requirements-yolo-train.txt)：`ultralytics==8.3.230`、`torch==2.9.1`、`torchvision==0.24.1`、`onnx==1.17.0`、`onnxruntime==1.19.2`、`numpy==1.26.4`、`opencv-python==4.10.0.84`。本机 RTX 5090 D v2 使用 PyTorch CUDA 12.8 wheel；CUDA 是训练环境要求，不是 CPU 推理端要求。现有训练脚本固定使用 `device=0`，若换 CPU 训练需显式调整脚本。

不要把 Ultralytics 直接加进当前主运行环境：[requirements.in](../requirements.in) 固定了 `opencv-python==4.6.0.66`，同时还声明了 `opencv-contrib-python==4.10.0.84`，与训练环境的 OpenCV 版本不同。训练与运行隔离可避免此次接入触发主环境依赖冲突或 OpenCV 包替换。

还需要标注工具（例如 CVAT 或 Label Studio，也可沿用已有候选复核工具）、导出为 YOLO 格式的图片与标签，以及训练／验证／测试划分。现有模板、回放图和 Wiki 地图坐标可辅助找样本；自动生成的候选标签需要整帧复核，不能直接当作验收真值。

项目当前采用 GPL-3.0，Ultralytics 提供 AGPL-3.0／商业许可选项；选定代码和预训练权重时记录对应许可，模型导出为 ONNX 不会自动改变原许可条件。

## 数据与类别约定

首版类别建议如下，主场景和小地图采用不同类别名：

| 类别 | 标注内容 | 用途 |
| --- | --- | --- |
| `scene_squad_arrow` | 白／橙箭头的稳定主体，不含动画光晕 | 场景小队候选；颜色作为同一类别的变化 |
| `scene_collectible_indicator` | 带放大镜的橙色倒三角完整主体 | 收集提示出现检测 |
| `minimap_enemy_normal` | 完整的普通敌人图标 | 普通目标候选 |
| `minimap_enemy_ex` | 完整的 EX 图标 | 辅助筛除；最终通过弹窗排除 EX |
| `minimap_squad_ring` | 小队圆环主体，中心语义与现有圆拟合一致 | 展开／紧凑小地图定位和停稳 |

Boss 在有可靠正样本后单列 `minimap_boss`，不能预先把所有非 EX 红点都标成普通敌人。看不清类型的图标先标为待复核，不灌入普通类别训练；部分遮挡、截断目标的标注策略需固定，运行时不将截断中心用于导航。

第一轮可从约 **500～1,000 张去重截图／ROI** 起步，这是采样预算建议，不是达到可用准确率的保证。需要覆盖白／橙箭头、动画相位、雪地／暗色地图、镜头平移、遮挡、边缘、多个相似物、没有目标的负样本和 EX。实际规模由独立测试集的错误类型决定。

现有数据已能用于建立实验基线，无需从零收集：已扫描 36,090 个图像文件，像素去重后有 13,939 个候选原图，并补提取 669 张客户区地图 ROI。第一版使用 4,764 张训练图、533 张验证图及 296 张预留图，但验证／预留集没有普通敌人实例，标签也存在普通／EX 混淆及蓝色菱形误当圆环。因此下一步重点是补齐整帧标注和独立测试集，而不是仅增加训练轮数。

训练输入应与运行时一致：主场景 ROI 与小地图 ROI 分别采样，记录原截图、裁切偏移、客户端尺寸、章节和采集批次。先使用一个模型处理两种 ROI，并按 ROI 过滤不适用类别；若两域准确率或耗时存在明显冲突，再拆模型。不能只把完整 `1776×999` 截图缩到很小，期待微小图标仍可分辨。

按章节和采集批次划分训练／验证／测试集，避免相邻动画帧分散到不同集合；必须留出未参与调参的章节和采集批次。历史目录与预览目录存在近重复截图，仅按目录或像素哈希去重不够，还要隔离相邻采集块并检查感知相似度。独立测试截图及其近重复图必须从下一版训练数据中移除；已参与训练或调参的图只能用于诊断。第一版只验收中文 Windows `1776×999`；其他语言、尺寸和 Android 需要对应数据及独立验证。

### 邻近、边缘与透视专项

| 场景 | 标注／训练要求 | 运行与验收要求 |
| --- | --- | --- |
| 普通敌人与 EX 紧邻或重叠 | 每个可辨识目标单独标框；加入密集图和部分遮挡图 | NMS 按类别处理；普通与 EX 冲突时拒绝普通点击，不把两个目标合并成中心点 |
| 圆环靠近敌人、蓝色菱形或计数器 | 区分真实圆环、菱形和 HUD；对可见完整 ROI 复核漏标 | 在 YOLO 候选内做局部中心拟合；多小队候选、拟合失败或明显遮挡时拒绝定位 |
| 目标靠近地图／小地图面板 | 同时保留面板内图标和面板外近边缘场景目标样本 | 用正确的坐标空间过滤；验证地图遮罩不会抹掉面板外箭头／提示，也不会把图内符号当场景目标 |
| 图像、地图 ROI 或场景分块边缘 | 区分完整目标与截断目标；场景分块重叠覆盖 | 完整目标应保留；截断目标不输出可点击中心；跨分块同一目标去重，不能因内部裁切边缘永久漏检 |
| 斜视、压扁、尺寸变化与动画相位 | 以真实透视截图为主，辅以旋转、缩放、剪切和轻量透视增强 | 按展开／紧凑图和透视程度分别统计召回与中心误差；检测框中心不能直接代替椭圆拟合中心或地面锚点 |
| 无目标、EX 仅余感叹号、未知星形图标 | 无目标帧须人工确认；类型不清的图先排除训练或复核，不标成普通敌人 | 独立测试记录未知／截断对象，验证不会选为普通目标；零检测不代表章节完成 |

标注、训练和推理必须使用相同版本的裁切与遮罩。当前实验数据生成后调整过场景遮罩阈值，下一版需重新生成，不能把旧数据结果当作新预处理的验证结果。

## 接入接口与坐标约定

原型内的 [detection.py](../module/campaign_prototype/detection.py) 实现推理与坐标还原，[perception.py](../module/campaign_prototype/perception.py) 已接入原型调用点。以下为接入约定；应用目录已包含第四版模型；仍需符合原型的地图绑定和有界移动约束。

```text
BGR 截图／ROI
  → 等比例缩放与补边 → ONNX 推理 → 类别过滤与 NMS
  → 撤销补边／缩放 → 恢复输入图像坐标 → 候选完整性与歧义检查
  → 箭头／圆环局部精定位，或敌人同帧地图投影
  → 现有导航与反馈状态机
```

当前检测对象返回 `label`、`confidence` 和 `box`（XYXY）；框属于传入图像的像素坐标。集成时还需由调用上下文绑定帧标识、坐标空间及地图版本。当前导出契约为 YOLO11 检测模型、输入 `[1,3,640,640]`、RGB／除以 255、FP32、opset 17、关闭图内 NMS，原始输出 `[1,9,N]`，类别顺序严格按上述五类。不要把不同 YOLO 版本、分割模型或带 NMS 的输出交给同一个未经检查的解码器。

- 主场景 ROI 要加回裁切偏移，才能得到客户区坐标；发送输入时仍由现有驱动换算到屏幕坐标。
- 展开小地图输入目前为 `486×462` ROI；检测中心只使用该帧已验收的 `roi_to_map`，并绑定章节及地图版本。不能用上一帧矩阵投影本帧框。
- 紧凑小地图沿用现有坐标约定：客户区图先归一到 `1920×1080`，再取 `[96:307,25:243]`。返回中心属于该 `218×211` ROI，不能直接作为原始客户区点击坐标。
- 相邻普通／EX 候选冲突时返回不确定；低分、缺失或截断不允许通过旧识别结果补成普通敌人。零检测也不代表章节完成。
- 箭头框中心精度未必满足现有横向漂移 ≤3 px 的约束。初版采用 YOLO 候选内局部精定位，并复用动画周期；若完全取消精定位，需证明中心误差满足约束并重新核对地面偏移和标定，不能直接套用现有 `+90 px`。
- 同一帧、同一 ROI 的检测结果复用；会话内加载一次模型。预热后测量端到端耗时，避免反复加载或重复推理拖慢停稳与周期采样。

迁移期间可用明确的 `legacy`、`shadow`、`yolo` 三种模式：现有识别、只记录 YOLO 对比、YOLO 驱动已验收的识别项。这些是设计选项，当前未提供对应命令行参数。逐项记录哪些检测已迁移；模型缺失、类别不符或推理异常时明确失败，不能静默切回旧算法并声称正在使用 YOLO。若增加模式参数，须从独立命令和管理界面移动会话传到子进程，统一初始化和证据记录。

保留 `normal_enemy_markers(image, matrix)` 等上层语义，可缩小导航侧改动。`collectible_indicator` 的结果应使用新的 `confidence` 字段，不能伪造旧的 `match_error`；战斗模板相关分数也不能与 YOLO 分数共用阈值。

最终分发至少包含 `module/campaign_prototype/models/campaign.onnx`、`campaign.json` 和可选运行依赖清单。模型说明应记录类别顺序、哈希、预处理／导出契约、各类阈值、数据版本和验收范围；目前导出脚本标记为 `pending_independent_validation` 的实验权重不能作为已验收模型启用移动。训练图片与环境不必随应用分发，但需要保留来源、标注和可复现训练记录。现有数据脚本见 [campaign_yolo_dataset.py](../dev_tools/campaign_yolo_dataset.py)，训练／导出脚本见 [campaign_yolo_train.py](../dev_tools/campaign_yolo_train.py)。

## 实施顺序与验收

1. **完成可验收的数据与模型。** 在已有实验基线上复核整帧标注，补齐普通敌人和紧凑图等缺口，隔离独立测试集及近重复图后训练下一版；产出权重、数据版本、模型说明和独立测试结果。保持一个导出格式。
2. **完成离线推理。** 加载本地 ONNX，输出带框截图及 JSON；测试颜色通道、补边还原、非方形 ROI、类别映射、NMS、空输出、边缘及异常模型。同步对比 PyTorch 与 ONNX 的结果，确认导出未改变判定。
3. **接入全部五类和所有调用点。** 按上表完成箭头、收集提示、普通／EX 敌人和展开／紧凑圆环适配，先离线回放，再只记录现场检测。旧结果仅用于对比，不能成为 YOLO 模式的隐藏回退。
4. **通过回归及专项验收。** 编译改动 Python 文件，运行 `test_campaign_yolo.py`、`test_arrow_anchor.py`、`test_movement_feedback.py`、`test_camera_navigation.py`、`test_campaign_prototype.py`、`test_surface_localizer.py`、`test_surface_motion.py`、`test_parallax_integration.py` 及相关受影响路径。箭头需验证周期成功率、锚点偏差与漏检恢复；圆环需分别验证两种小地图和各定位器；不能只报框的 mAP。
5. **启用并交付已验收模型。** 检查所有计划调用点均已迁移，再在受保护的有界移动入口完成现场验证，记录模型哈希和失败原因。活动／战斗等可选替换按自己的样本和流程另行接入。

五类验收至少报告各类别样本数和 precision／recall、普通与 EX 混淆、场景负样本误报、箭头／圆环中心误差，以及目标机器上的端到端 p50／p95 耗时。展开／紧凑、相邻／边缘／透视样本分别列出结果，不能用整体均值掩盖某类缺样本。阈值只用验证集选择。验收以普通关卡的检测精度及有效目标中心为重点，EX 检测精度不作为独立阻断条件；候选实际触发 EX 弹窗时必须关闭并排除，不进入战斗。检测召回与边界过滤后的目标数量分别报告，有限样本不代表任意场景的准确率保证。性能需包含场景所有分块、预处理和后处理，训练日志中的单图 GPU 耗时不能代替运行端 CPU 测速。

可复现的现场验证路径：在已核对地图包的中文 `1776×999` 客户区，记录一次紧凑→展开→紧凑切换；分别录制白／橙箭头完整周期、镜头平移后的偏心小队、一次遮挡恢复、普通／EX 同图以及收集提示出现／未出现。先只记录检测，再在受保护的有界移动入口验证普通目标接近与 EX 排除；检查日志中模型哈希、模式、坐标转换和失败原因。完整战斗胜负及章节完成仍按自动推图原计划验收。

本次方案核对覆盖实际调用位置、依赖清单、已有训练产物和文档链接；文档修改执行 `git diff --check`。第四版结果、样本局限和现场证据见实施记录；新增语言、尺寸及完整推图闭环需单独验证。

## 依赖参考

- [ONNX Runtime Python 安装说明](https://onnxruntime.ai/docs/install/)
- [ONNX Runtime 1.19.2 元数据](https://pypi.org/pypi/onnxruntime/1.19.2/json)
- [Ultralytics 8.3.230 元数据](https://pypi.org/pypi/ultralytics/8.3.230/json)
- [YOLO11 模型说明](https://docs.ultralytics.com/models/yolo11/)、[ONNX 导出说明](https://docs.ultralytics.com/integrations/onnx/)、[标注格式](https://docs.ultralytics.com/datasets/detect/)、[许可说明](https://www.ultralytics.com/license)
