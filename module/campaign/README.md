# 自动主线模块

正式入口保持 `module.campaign.campaign.Campaign`。地图、视觉与移动代码统一在本包，
实验命令只作适配器，不拥有第二套移动实现。

## 目录职责

| 位置 | 职责 |
| --- | --- |
| `campaign.py` | 资源与客户区准备、章节定位、正式任务入口 |
| `execution.py`、`session.py`、`window.py` | 有限执行、生产 Device 会话、停止与输入护栏 |
| `goals.py`、`subgoals.py`、`scouting.py`、`run_state.py` | 目标选择、解封与电梯子目标、拖展开地图侦察敌人的路程排序、跨帧运行状态 |
| `map/` | 标注校验、章节资源定位与名称映射、静态道路、连通分区、道路编辑与坐标变换 |
| `vision/` | 小地图配准、YOLO 感知、箭头／离屏标记、场景地面配准；模型和模板也在此处 |
| `navigation/` | 共享 A*、切开路网、移动与镜头闭环、电梯、停稳反馈、共同会话接口 |
| `tools/` | 命令行实验、校验与回放、实验设置及采集器窗口；生产入口不导入此子包 |

正式会话和实验会话都复用 `navigation.movement.navigate` 与
`navigation.session.NavigationSession`。模板与 ONNX 模型在 `vision/assets/`、`vision/models/`，
资源常量从新位置读取。

## 操作与验证

- [自动推图流程与进度](../../doc/campaign-auto-flow.md)
- [移动坐标与反馈原理](../../doc/campaign-prototype-principles.md)
- [标注及实机验收路径](../../doc/map-annotation.md)
- [实验命令说明](tools/README.md)

实验命令统一使用 `python -m module.campaign.tools.<命令>`。
既有 `data/campaign_prototype/` 和 `log/campaign_prototype/` 是本地运行数据路径，保留原名；
本次目录整理不移动个人数据，也不迁移已有 STOP、锁文件或实验结果。

离线回归在仓库根目录运行：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_campaign_execution.py -v
```

代码与离线回归完成不等于实机验收完成，机关／电梯资源标注与游戏内验证仍按流程文档的待办执行。
