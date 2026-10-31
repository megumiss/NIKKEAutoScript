# 自动推图开发工具

本目录集中存放自动推图的开发与维护工具；正式任务仍在 `module/campaign/`，
不把采集、写盘、训练依赖或编辑器逻辑混入运行期任务。

## 目录索引

| 目录 / 文件 | 职责 | 是否控制游戏 |
| --- | --- | --- |
| `capture/chapters.py` | 章节 OCR、切章、批量地图采集与恢复 | 是 |
| `capture/reconstruct.py` | 单章采集、跟踪、拼接、离线重建与诊断；提供 `DriverWindow` | 采集时是；`--rebuild` 不控制 |
| `capture/calibrate.py` | 平面章节小队位移采样与标定 | 是 |
| `maps/annotation_store.py` | 编辑器与批处理共用的地图枚举、版本校验、保存与备份 | 否 |
| `maps/connectivity.py` | 地图连通数据批处理 | 否；会写地图派生文件 |
| `maps/runtime.py` | 运行地图导出、精简与采用 | 否；会写地图包并备份 |
| `maps/wiki_collectibles.py` | Wiki 下载、配准、复核与增量标注导入 | 否；可联网、写缓存及标注 |
| `vision/dataset.py` | 采集清点、候选标注、复核拼图与 YOLO 数据集生成 | 否 |
| `vision/evaluate.py` | 使用运行期检测器进行离线回放评估 | 否 |
| `vision/reviews/` | 人工复核记录与历史验证报告 | 否 |
| `vision/requirements-train.txt` | 独立训练环境依赖，不用于主程序环境 | 否 |
| `editor/` | 本机地图编辑器、后台任务与静态页面 | 打开页面不会；执行扫描或移动时是 |

编辑器结构见 [地图编辑器说明](editor/README.md)。

## 常用入口

所有命令均在仓库根目录执行，使用项目已有的 Python 环境。先用 `--help` 检查参数；
采集、切章和标定命令会控制游戏窗口及鼠标，不应仅为目录验证而执行。

```powershell
# 地图编辑器
python -m dev_tools.campaign.editor

# 批量采集与单章重建工具参数
python -m dev_tools.campaign.capture.chapters --help
python -m dev_tools.campaign.capture.reconstruct --help
python -m dev_tools.campaign.capture.calibrate --help

# 从完整采集包导出运行地图，目标必须是新目录
python -m dev_tools.campaign.maps.runtime --source data/chapter_maps/local/captures/chapter_38 --destination data/resources/maps/chapter_38

# 连通数据重算与 Wiki 配准参数
python -m dev_tools.campaign.maps.connectivity --help
python -m dev_tools.campaign.maps.wiki_collectibles --help

# 视觉数据生成与离线回放参数
python -m dev_tools.campaign.vision.dataset --help
python -m dev_tools.campaign.vision.evaluate --help
```

## 路径约定

目录调整后不保留 `dev_tools/` 根目录下的旧脚本入口。仓库内调用、批处理脚本和维护文档均应使用上述 `dev_tools.campaign.*` 模块路径，避免同一工具存在两套入口。

## 数据与边界

- 正式运行地图仍由资源仓库同步到 `data/resources/maps/`；不要随本次目录整理移动或提交。
- 本地采集、原始包、实验数据仍在 `data/chapter_maps/local/`；日志仍在 `log/`。
- 人工复核 JSON 只迁移文件位置，不改动记录内容；记录中的历史采集路径不重写。
- 运行地图读写格式与连通算法在 `module/campaign/map/`；设备和导航实现仍沿用现有模块。
- YOLO 训练依赖在独立环境安装，不加入主程序的依赖清单。

## 操作说明

- [地图标注与运行包管理](../../doc/map-annotation.md)
- [地图采集与 Wiki 批处理](../../doc/wiki-collectibles-batch.md)
- [位移标定](../../doc/campaign-calibration.md)
- [地图与 Wiki 原理](../../doc/minimap-wiki-principles.md)
