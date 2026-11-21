# 自动推图独立维护适配

本目录为地图编辑器的移动任务和位移标定提供独立窗口、地图校验与定位适配。
正式任务不导入本子包；导航算法仍位于 `../navigation/`，不注册额外调度任务。
现场运行使用中文 Windows 客户区 `1776×999`。

## 保留文件与调用方

| 文件 | 用途与调用方 |
| --- | --- |
| `manual_move.py` | 编辑器移动任务子进程入口；`capture/calibrate.py` 复用地图准备与 `GameSession` |
| `adaptive.py`、`probe.py` | 移动与标定使用的自适应道路定位；`probe.py` 仅加载绑定地图与标定 |
| `goto.py` | 移动与标定使用的地图控件适配、普通／EX 弹窗处理，不提供独立寻敌入口 |
| `map_package.py`、`prepare_package.py` | 实验副本的版本、哈希、道路及标定校验；支持导入新副本 |
| `check_controls.py` | 标定前的截图与地图开合检查，不移动小队 |
| `settings.py`、`runtime.py`、`window.py` | 独立工具的路径、停止、受保护输入、窗口释放与结果契约 |

正式地图由资源仓库同步到 `data/resources/maps/`。独立工具默认实验包位于
`data/campaign_prototype/chapter_38/`，输出位于 `log/campaign_prototype/`；这些本机数据不提交。
编辑器移动任务会创建绑定当前地图与标注版本的独立副本。

## 常用入口

在仓库根目录执行，使用项目已有虚拟环境。`--help` 不取得游戏输入控制。

```powershell
# 编辑器：移动、扫描与 Wiki 导入共用统一页面
.venv\Scripts\python.exe -m dev_tools.campaign.editor

# 查看独立维护入口参数
.venv\Scripts\python.exe -m module.campaign.tools.manual_move --help
.venv\Scripts\python.exe -m module.campaign.tools.prepare_package --help
.venv\Scripts\python.exe -m module.campaign.tools.adaptive --help
.venv\Scripts\python.exe -m module.campaign.tools.check_controls --help
```

从静态地图导入到尚不存在的实验目录，章节号与源路径必须匹配：

```powershell
.venv\Scripts\python.exe -m module.campaign.tools.prepare_package --source data/resources/maps/chapter_38 --destination data/campaign_prototype/chapter_38 --chapter 38
```

`check_controls --cycles 0` 只截图，`1..10` 次会实际展开／收回地图，仍取得游戏输入控制。
不要为检查目录或参数而运行现场控制。普通敌人弹窗保留给上层处理；EX 弹窗关闭一次并返回
`ex_stage_skipped`，结束本次移动，不能把它视为普通目标到达或战斗成功。

## 说明

- [自动主线模块与运行依赖](../README.md)
- [坐标、地图包与导航原理](../../../doc/campaign-prototype-principles.md)
- [编辑器移动操作与游戏内验收](../../../doc/map-annotation.md)
- [位移标定与控制检查](../../../doc/campaign-calibration.md)
- [模型契约与验证边界](../vision/models/README.md)
