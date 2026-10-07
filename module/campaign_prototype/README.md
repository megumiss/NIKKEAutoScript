# 战役地图独立原型

本目录提供战役地图定位、镜头导航、局部移动标定和离线回放。独立命令与地图标注工具共用导航逻辑；不注册 `main.py` 调度任务。现场运行使用中文 Windows 客户区 `1776×999`。

- [运行控制、地图包与导航原理](../../doc/campaign-prototype-principles.md)
- [地图采集、重建与 Wiki 标注原理](../../doc/minimap-wiki-principles.md)
- [自动推图计划](../../doc/autopush-plan.md)
- [YOLO 接入方案、替换清单与依赖](../../doc/campaign-yolo-plan.md)
- [地图标注与移动操作](../../doc/map-annotation.md)
- [标定脚本、采样步骤与验收条件](../../doc/campaign-calibration.md)

## 代码与数据

| 文件 | 用途 |
| --- | --- |
| `settings.py`、`runtime.py` | 路径、窗口检查、停止、释放与命令失败契约 |
| `map_package.py`、`prepare_package.py` | 地图包验证和从已有静态地图导入独立实验副本 |
| `goto.py` | 公共地图控制、标记识别及 41 章缓存离线回放 |
| `probe.py`、`adaptive.py` | 固定／自适应道路定位、局部路线与投影换算 |
| `live.py`、`arrow_anchor.py` | 白／橙箭头检测、容错周期采样与单步移动实验 |
| `movement_feedback.py` | 小地图位移与静态地面交叉恢复箭头锚点、有限目标触发 |
| `camera_navigation.py`、`manual_move.py` | 管理界面与命令入口共用的远点平移、完整目标点击、停稳检测与到达修正 |
| `surface_motion.py` | 指定局部道路的配准、独立停靠样本标定及目标区域落点检查；分层移动复用局部配准与标定验证 |
| `run.py`、`camera_to.py` | 单次观察／自动移动／指定手势及有限镜头寻位 |
| `match_coarse.py`、`match_scene.py` | 道路相关辅助与 Wiki 场景落点估计 |
| `check_controls.py`、`scene_pan.py`、`pan_check.py` | 地图状态切换及两类镜头行为的现场实验 |
| `assets/`、`migration.json` | 必要弹窗模板、历史标定及迁移来源哈希 |

命令行实验包默认在 `data/campaign_prototype/chapter_38/`，输出在 `log/campaign_prototype/`，两者均为本机数据。需要提交的章节地图位于 `data/chapter_maps/runtime/`，由 [map_runtime.py](../../dev_tools/map_runtime.py) 从完整采集包导出；完整采集、重建与辅助数据位于 `data/chapter_maps/local/`，不提交。平面章节使用下方命令导入实验包。第 40 章保留局部定位所需参考帧和深度数据，但仍缺现场局部移动标定。`migration.json` 中的哈希对应迁移源文件，不是修改后的目标代码。

`assets/chapter_38_validation.json` 保存本机已验证地图副本的初始文件清单，用于核对历史数据；实际运行读取所选包自己的 `validation.json`。Wiki 全场景图为可选本地参照，默认路径 `data/campaign_prototype/wiki_references/NN.png`；没有它们仍可运行地图校验、定位回放及控制检查。

## YOLO 模型

`models/campaign.onnx` 与 `campaign.json` 为已分发的第四版模型，适用于中文 Windows `1776×999`。运行依赖可用 `.venv\Scripts\python.exe -m pip install -r requirements-yolo.txt` 安装；训练环境无需随应用安装。模型契约、数据与已验证范围见 [模型说明](models/README.md)。

普通关卡优先使用 YOLO 识别；敌人移动模式出现普通战斗准备弹窗时保留，出现 EX 弹窗时关闭一次并返回 `ex_stage_skipped`，结束本次移动。该结果不表示普通关卡到达或战斗完成。

## 常用入口

从仓库根目录执行，默认使用项目已有虚拟环境。查看任意入口参数：

```powershell
.venv\Scripts\python.exe -m module.campaign_prototype.run --help
.venv\Scripts\python.exe -m module.campaign_prototype.map_package
```

把已导出的静态地图导入到**尚不存在**的目录。下面的源路径应替换为实际采集包；默认标定只在已有 38 章实验中验证过。

```powershell
.venv\Scripts\python.exe -m module.campaign_prototype.prepare_package --source data/chapter_maps/runtime/chapter_38 --destination data/campaign_prototype/chapter_38 --chapter 38
```

离线定位需传入一张 `486×462` 的展开地图 ROI：

```powershell
.venv\Scripts\python.exe -m module.campaign_prototype.adaptive log/campaign_prototype/sample_roi.png --tag replay_01
```

现场检查会取得游戏输入控制。每次用新标签保留证据；第一次可用零次循环只截图：

```powershell
.venv\Scripts\python.exe -m module.campaign_prototype.check_controls --cycles 0 --tag snapshot_01
.venv\Scripts\python.exe -m module.campaign_prototype.check_controls --cycles 10 --tag controls_01
```

管理界面的到点／收集品／敌人模式、`live --move`、现场 `probe` 和 `run --move` 均共用远距离镜头规划。
目标离屏时按目标相对位置分批斜向长拖动，每批最多三次，两轴等比例缩放；批内沿同一实测基准累计位移。
批末、预计相对位移完成或拖后画面基本不变时用小地图校正；确认无进展且目标仍不可点击时停止。
估算视野仅用于镜头规划；点击完整目标前必须通过小地图定位确认，再交给游戏寻路，单次计划最多平移 8 次。
点击后直接采样紧凑小地图，四帧道路和小队圆环稳定后再定位，等待期间不反复展开面板。
镜头校正发现小队位置变化时，管理界面移动测试丢弃旧点击基准，停稳后重新定位，最多恢复两次。
管理界面移动测试无法恢复箭头时，若有有效局部场景标定及小地图小队定位，可沿同层连续道路短移一次；平面地图没有局部场景标定时，改按镜头居中近似向附近开阔道路做一次唤醒短移（橙环站位不渲染箭头，等待不会出现，仅平面地图适用）。
短移落点须通过标定截图与当前地面的交叉配准，唤醒落点须通过道路净空、敌人距离与路径验证；停稳后重新定位并直接采样箭头，仍不可见则停止，不继续试点。
收集品模式检测到带放大镜的橙色倒三角即停止并保存现场，不再微调或点击拾取。
`live --step` 只用于显式短移实验；`run --click`／`--pan` 仍执行指定手势。
分层地图必须已有有效局部标定，平移和落点都受同层道路及实测支持域约束。

停止可用 `Ctrl+C`，或在另一终端创建共享停止文件：

```powershell
New-Item -ItemType File -Path log/campaign_prototype/STOP -Force
```

停止文件保留直到操作者准备重新开始并移除它；程序不会自动清除。退出码 `0` 只表示本次命令完成，`1` 表示失败，`130` 表示取消；不代表物品已拾取、战斗获胜或章节完成。
