# 自动推图 YOLO 接入方案与替换清单

更新：2026-10-02。本文是接入设计，尚未实现或启用 YOLO，也未训练、下载模型或安装依赖。

范围以 [战役地图独立原型](../module/campaign_prototype/README.md) 为主，并盘点活动推图与战斗模块可复用的部分。当前原型尚未形成持续推图闭环，检测器升级不改变[自动推图计划](autopush-plan.md)中的导航、单场战斗和恢复任务。

## 建议结论

优先让 YOLO 负责识别小队箭头、收集提示、小地图普通敌人和 EX；保留道路配准、坐标投影、OCR 和有界动作状态机。小队圆环在第二步接入，因为它影响定位、停稳、镜头控制和分层地图多条路径。

建议从 YOLO11n 的目标检测模型微调开始，导出 ONNX，在运行端使用 ONNX Runtime CPU。这是候选基线，不是性能或准确率结论；YOLOv8n 也可作为对照，但首版只实现并验证一种导出契约。通用预训练权重不认识 NIKKE 的这些图标，不能安装库后直接替换现有识别。

## 替换清单

| 顺序 | 识别对象与现有位置 | 接入方式 | 保留的约束 |
| --- | --- | --- | --- |
| 首批 | 主场景白／橙小队箭头：[live.py](../module/campaign_prototype/live.py) `squad_arrow` | 用 YOLO 找箭头候选，替代全场景多尺度模板搜索；候选内精定位后返回原语义的中心 | 多个小队候选时拒绝定位；[arrow_anchor.py](../module/campaign_prototype/arrow_anchor.py) 的静止检查、周期采样和地面换算继续生效 |
| 首批 | 带放大镜的橙色收集提示：[movement_feedback.py](../module/campaign_prototype/movement_feedback.py) `collectible_indicator` | 替换透明模板匹配，输出提示位置、类别和检测分数 | 排除小地图计数图标与 HUD；[manual_move.py](../module/campaign_prototype/manual_move.py) 中发现提示即停止的行为保留，不据此宣布已拾取 |
| 首批 | 小地图普通敌人／EX：[goto.py](../module/campaign_prototype/goto.py) `normal_enemy_markers` | 用不同类别代替红色轮廓的实心／空心规则；普通敌人中心再按本帧矩阵投影 | 边缘截断、计数器区域排除；EX 或类别冲突不进入普通目标表；弹窗级 EX 确认保留 |
| 第二步 | 展开／紧凑小地图的小队圆环：[minimap_reconstruct.py](../dev_tools/minimap_reconstruct.py) `detect_markers` | YOLO 定位圆环候选，候选内拟合中心；先通过原型适配器接入 | 不能将视野中心当小队；紧凑和展开模式单独验证；先不改变地图采集器的默认识别 |
| 有数据后 | Boss、机关、区域入口、场景敌人 | 对实际需要的对象补类别和标注，再接目标选择器 | 当前没有统一可靠识别器可以直接替换；发现对象不等于可达、机关已激活或章节完成 |
| 后续可选 | 战斗准备／EX 弹窗：[goto.py](../module/campaign_prototype/goto.py) `battle_popup_score`、`check_battle_popup` | 固定模板易受皮肤或布局影响时再训练；以页面状态与按钮联合判定 | YOLO 分数不能代入原来的模板相关分数阈值；未知页面不继续移动 |
| 后续可选 | 主线场景剧情标记：[semi_combat.py](../module/daemon/semi_combat.py) `MAIN_STORY_MARK_IN/OUT` | 可复用场景检测器，但需独立样本及点击偏移验证 | 该模块使用设备截图，与原型原始客户区坐标不同，不能直接共享点击坐标 |
| 后续可选 | 活动关卡标记：[story.py](../module/event/story.py)；战斗状态：[semi_combat.py](../module/daemon/semi_combat.py)、[auto_tower.py](../module/daemon/auto_tower.py)、[simulation_room/event.py](../module/simulation_room/event.py) | 反复维护的图标可集中检测；固定按钮目前继续模板识别 | 活动关卡图标及战斗页面需另建数据集；主线训练结果不代表活动、塔或模拟室支持 |

小队圆环不能只改一个调用点。至少需要覆盖 `adaptive.py`、`probe.py`、`camera_navigation.py`、`surface_localizer.py`、`parallax_localizer.py`、`parallax_movement.py`、`surface_motion.py` 和旧 `goto.py`，避免定位与停稳检查使用不同的中心定义。优先新增原型范围的统一适配器，不把业务检测器反向塞入通用采集工具。

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

本机已核对：Python **3.9.13**、OpenCV **4.10.0**、NumPy **1.24.4**；没有安装 `ultralytics`、`torch`、`onnxruntime` 或 `onnx`。仓库中的 OCR ONNX 文件不是推图目标检测模型。

| 项目 | 是否需要 | 说明 |
| --- | --- | --- |
| 游戏专用 `.onnx` 权重 | 必须 | 由标注数据微调和导出，包含约定类别；不能使用 COCO 通用权重直接识别图标 |
| 模型说明文件 | 必须 | 保存模型哈希、类别顺序、输入尺寸、颜色顺序、归一化、输出布局、阈值、训练／导出版本和已验证范围 |
| `onnxruntime` CPU | 推荐 | 增加一个直接推理依赖，另有传递依赖；不要求 CUDA，不加载 PyTorch |
| OpenCV、NumPy | 已有 | 负责图像裁切、缩放、坐标还原、NMS 和局部精定位 |
| `torch`、`torchvision`、`ultralytics` | 推荐方案的运行端不需要 | 留在训练／导出环境 |
| GPU 推理运行库 | 可选 | CPU 测速不满足要求时再评估；需要按显卡、驱动及 CUDA/cuDNN 版本选择，CPU 与 GPU 发行包不混装 |

PyPI 核对时，ONNX Runtime 最新版 `1.30.0` 要求 Python ≥3.11；`1.19.2` 提供 `cp39-win_amd64` wheel，且声明 NumPy ≥1.21.6，可作为当前环境的兼容性验证候选。**这只确认发行包条件，尚未完成安装、依赖求解或模型推理验证。** 实施时单独生成可选依赖清单并锁定版本，不直接更新现有 Python 和全项目依赖。

另一个可行后端是已有的 `cv2.dnn.readNetFromONNX`，可减少新增运行依赖；但必须用实际导出模型验证 OpenCV 的算子、输出及速度支持。首版选择一个后端完成验证，不同时维护两套推理实现。

### 训练与导出端

使用独立的 Python 3.11 环境或训练机器，安装并锁定 `ultralytics`、`torch`、`torchvision`、`onnx` 及导出所需依赖。NVIDIA GPU 可显著缩短训练时间，CPU 也能训练小模型；实际耗时和显存需求需按输入尺寸、批量与数据量测量。

不要把 Ultralytics 直接加进当前主运行环境：本次查询到的版本要求 `opencv-python>=4.7.0`，而 [requirements.in](../requirements.in) 固定了 `opencv-python==4.6.0.66`，同时还声明了 `opencv-contrib-python==4.10.0.84`。这会触发依赖冲突或 OpenCV 包替换；训练与运行隔离可避免为此次接入改动现有图像栈。

还需要标注工具（例如 CVAT 或 Label Studio）、导出为 YOLO 格式的图片与标签，以及训练／验证／测试划分。现有模板、零散回放图和 Wiki 地图坐标可辅助找样本，但没有提供完整的图像框标注，不能直接当作训练集。

项目当前采用 GPL-3.0，Ultralytics 提供 AGPL-3.0／商业许可选项；选定代码和预训练权重时记录对应许可，模型导出为 ONNX 不会自动改变原许可条件。

## 数据与类别约定

首版类别建议如下，主场景和小地图采用不同类别名：

| 类别 | 标注内容 | 用途 |
| --- | --- | --- |
| `scene_squad_arrow` | 白／橙箭头的稳定主体，不含动画光晕 | 场景小队候选；颜色作为同一类别的变化 |
| `scene_collectible_indicator` | 带放大镜的橙色倒三角完整主体 | 收集提示出现检测 |
| `minimap_enemy_normal` | 完整的普通敌人图标 | 普通目标候选 |
| `minimap_enemy_ex` | 完整的 EX 图标 | 明确排除 EX |
| `minimap_squad_ring` | 小队圆环主体，中心语义与现有圆拟合一致 | 第二步接入定位和停稳 |

Boss 在有可靠正样本后单列 `minimap_boss`，不能预先把所有非 EX 红点都标成普通敌人。看不清类型的图标先标为待复核，不灌入普通类别训练；部分遮挡、截断目标的标注策略需固定，运行时不将截断中心用于导航。

第一轮可从约 **500～1,000 张去重截图／ROI** 起步，这是采样预算建议，不是达到可用准确率的保证。需要覆盖白／橙箭头、动画相位、雪地／暗色地图、镜头平移、遮挡、边缘、多个相似物、没有目标的负样本和 EX。实际规模由独立测试集的错误类型决定。

训练输入应与运行时一致：主场景 ROI 与小地图 ROI 分别采样，记录原截图、裁切偏移、客户端尺寸、章节和采集批次。先使用一个模型处理两种 ROI，并按 ROI 过滤不适用类别；若两域准确率或耗时存在明显冲突，再拆模型。不能只把完整 `1776×999` 截图缩到很小，期待微小图标仍可分辨。

按章节和采集批次划分训练／验证／测试集，避免相邻动画帧分散到不同集合；必须留出未参与调参的章节和采集批次。第一版只验收中文 Windows `1776×999`；其他语言、尺寸和 Android 需要对应数据及独立验证。

## 接入接口与坐标约定

建议新增原型内的 `detection.py` 管理推理和结果，`perception.py` 适配现有函数。以下名称和配置均为拟议接口，目前尚不可调用。

```text
BGR 截图／ROI
  → 等比例缩放与补边 → ONNX 推理 → 类别过滤与 NMS
  → 撤销补边／缩放 → 恢复输入图像坐标 → 候选完整性与歧义检查
  → 箭头／圆环局部精定位，或敌人同帧地图投影
  → 现有导航与反馈状态机
```

每次检测返回 `label`、`confidence`、`bbox_xyxy`、帧标识和坐标空间；框首先属于传入图像的像素坐标。模型说明约定固定输入大小与输出格式，首版使用普通检测导出、batch=1、FP32、关闭图内 NMS，明确选择并验证 opset。不要把不同 YOLO 版本、分割模型或带 NMS 的输出交给同一个未经检查的解码器。

- 主场景 ROI 要加回裁切偏移，才能得到客户区坐标；发送输入时仍由现有驱动换算到屏幕坐标。
- 展开小地图输入目前为 `486×462` ROI；检测中心只使用该帧已验收的 `roi_to_map`，并绑定章节及地图版本。不能用上一帧矩阵投影本帧框。
- 相邻普通／EX 候选冲突时返回不确定；低分、缺失或截断不允许通过旧识别结果补成普通敌人。零检测也不代表章节完成。
- 箭头框中心精度未必满足现有横向漂移 ≤3 px 的约束。初版采用 YOLO 候选内局部精定位，并复用动画周期；若完全取消精定位，需证明中心误差满足约束并重新核对地面偏移和标定，不能直接套用现有 `+90 px`。
- 同一帧、同一 ROI 的检测结果复用；会话内加载一次模型。预热后测量端到端耗时，避免反复加载或重复推理拖慢停稳与周期采样。

配置建议使用明确的 `legacy`、`shadow`、`yolo` 三种模式：现有识别、只记录 YOLO 对比、YOLO 驱动已验收的识别项。逐项记录哪些检测已迁移；模型缺失、类别不符或推理异常时明确失败，不能静默切回旧算法并声称正在使用 YOLO。新参数须从独立命令和管理界面移动会话传到子进程，统一初始化和证据记录。

保留 `normal_enemy_markers(image, matrix)` 等上层语义，可缩小导航侧改动。`collectible_indicator` 的结果应使用新的 `confidence` 字段，不能伪造旧的 `match_error`；战斗模板相关分数也不能与 YOLO 分数共用阈值。

## 实施顺序与验收

1. **建立数据与模型基线。** 整理实际截图、明确标签、去重分组、训练小模型；产出权重、数据版本、模型说明和独立测试结果。先固定可用的一个导出格式。
2. **完成离线推理。** 加载本地 ONNX，输出带框截图及 JSON；测试颜色通道、补边还原、非方形 ROI、类别映射、NMS、空输出、边缘及异常模型。同步对比 PyTorch 与 ONNX 的结果，确认导出未改变判定。
3. **接入首批三个函数。** 箭头、收集提示、普通／EX 敌人先以 `shadow` 模式回放和现场记录。保留传统检测结果用于对比，控制动作仍来自原路径。
4. **验收后启用首批识别。** 回归 `test_arrow_anchor.py`、`test_movement_feedback.py`、`test_camera_navigation.py`、`test_campaign_prototype.py`，并增加模型输出与真实回放边界验证。箭头尤其要验证周期成功率、锚点偏差与漏检恢复，不能只报框的 mAP。
5. **再接小队圆环和其他任务。** 展开／紧凑图分别验收，多定位器保持一致中心语义；补充 `test_surface_localizer.py`、`test_surface_motion.py`、`test_parallax_integration.py` 及相关地图回归。活动／战斗识别按自己的样本和流程独立接入。

首批至少报告各类别 precision／recall、普通与 EX 混淆、场景负样本误报、箭头／圆环中心误差，以及目标机器上的端到端 p50／p95 耗时。阈值只用验证集选择。独立回放集中不得出现 EX 被选为可点击普通目标；有限样本零误选不代表真实环境零风险，弹窗复核仍保留。

可复现的现场验证路径：在已核对地图包的中文 `1776×999` 客户区，记录一次紧凑→展开→紧凑切换；分别录制白／橙箭头完整周期、镜头平移后的偏心小队、一次遮挡恢复、普通／EX 同图以及收集提示出现／未出现。先只记录检测，再在受保护的有界移动入口验证普通目标接近与 EX 排除；检查日志中模型哈希、模式、坐标转换和失败原因。完整战斗胜负及章节完成仍按自动推图原计划验收。

本轮只新增方案文档和索引，验证范围为代码位置、依赖元数据、文档链接及 `git diff --check`，未执行训练、模型测速或游戏现场验证。

## 依赖参考

- [ONNX Runtime Python 安装说明](https://onnxruntime.ai/docs/install/)
- [ONNX Runtime 当前发行元数据](https://pypi.org/pypi/onnxruntime/json)与 [1.19.2 元数据](https://pypi.org/pypi/onnxruntime/1.19.2/json)：以上 Python／wheel／NumPy 条件来自本次查询；当前发行页后续会变化。
- [Ultralytics 发行元数据](https://pypi.org/pypi/ultralytics/json)：本次返回 `8.4.171`，上述 OpenCV 约束来自该版本。
- [YOLO11 模型说明](https://docs.ultralytics.com/models/yolo11/)、[ONNX 导出说明](https://docs.ultralytics.com/integrations/onnx/)、[标注格式](https://docs.ultralytics.com/datasets/detect/)、[许可说明](https://www.ultralytics.com/license)
