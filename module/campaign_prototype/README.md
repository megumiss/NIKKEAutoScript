# 战役地图独立原型

本目录保存从旧工作树迁入的定位、镜头、导航验证和寻敌回放代码。它不注册任务，不接入 `main.py`、配置生成或 Web UI；当前实验环境为中文 Windows 客户区 `1776×999`。

- [运行控制、地图包与导航原理](../../doc/campaign-prototype-principles.md)
- [地图采集、重建与 Wiki 标注原理](../../doc/minimap-wiki-principles.md)
- [阶段计划](../../doc/autopush-plan.md)与[实际执行记录](../../doc/minimap-autopush-execution.md)

## 代码与数据

| 文件 | 用途 |
| --- | --- |
| `settings.py`、`runtime.py` | 路径、窗口检查、停止、释放与命令失败契约 |
| `map_package.py`、`prepare_package.py` | 地图包验证和从已有静态地图导入独立实验副本 |
| `goto.py` | 公共地图控制、标记识别及旧 41 章离线回放 |
| `probe.py`、`adaptive.py` | 固定／自适应道路定位、局部路线与投影换算 |
| `live.py` | 小队脚下圆环识别与单步移动实验 |
| `surface_motion.py` | 指定局部道路的配准、独立停靠样本标定及目标区域落点检查；尚未接入通用导航 |
| `run.py`、`camera_to.py` | 单次观察／镜头平移／指定点击及有限镜头寻位 |
| `match_coarse.py`、`match_scene.py` | 道路相关辅助与 Wiki 场景落点估计 |
| `check_controls.py`、`scene_pan.py`、`pan_check.py` | 地图状态切换及两类镜头行为的现场实验 |
| `assets/`、`migration.json` | 必要弹窗模板、历史标定及迁移来源哈希 |

地图默认在仓库的 `data/campaign_prototype/chapter_38/`，输出在 `log/campaign_prototype/`。两者均为本机数据，不随 Git 分发。代码不从 `tmp/` 或旧工作树导入 Python 模块。`migration.json` 中的哈希对应迁移源文件，不是修改后的目标代码。

`assets/chapter_38_validation.json` 保存本机已验证地图副本的初始文件清单，用于核对历史数据；实际运行读取所选包自己的 `validation.json`。Wiki 全场景图为可选本地参照，默认路径 `data/campaign_prototype/wiki_references/NN.png`；没有它们仍可运行地图校验、定位回放及控制检查。

## 常用入口

从仓库根目录执行，默认使用项目已有虚拟环境。查看任意入口参数：

```powershell
.venv\Scripts\python.exe -m module.campaign_prototype.run --help
.venv\Scripts\python.exe -m module.campaign_prototype.map_package
```

把已导出的静态地图导入到**尚不存在**的目录。下面的源路径应替换为实际采集包；默认标定只在已有 38 章实验中验证过。

```powershell
.venv\Scripts\python.exe -m module.campaign_prototype.prepare_package --source data/chapter_maps/repaired/chapter_38 --destination data/campaign_prototype/chapter_38 --chapter 38
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

停止可用 `Ctrl+C`，或在另一终端创建共享停止文件：

```powershell
New-Item -ItemType File -Path log/campaign_prototype/STOP -Force
```

停止文件保留直到操作者准备重新开始并移除它；程序不会自动清除。退出码 `0` 只表示本次命令完成，`1` 表示失败，`130` 表示取消；不代表物品已拾取、战斗获胜或章节完成。

当前 M0 代码、离线故障路径与连续十次真实地图切换均已验证。首次现场测试失焦后正确停止并释放；保持游戏前台后复测十次通过。详见执行记录，正式推图架构仍未接入。
