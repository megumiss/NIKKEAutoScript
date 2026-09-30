# 分层俯视地图与共用坐标

正式目标与平面章节一致：`reference.png` 和 `map.png` 使用同一个无透视的俯视画布，
Wiki 点、小队位置和移动目标都使用原图像素坐标。高低道路和坡道必须先校正到共同世界坐标，
不能把同一二维位置上的不同高度直接连接起来。

## 坐标与消费接口

`module/campaign_prototype/surface_geometry.py` 定义共同转换。
世界表面使用 `h = a*x + b*y + c`，其中 h 是相对相机高度，允许坡道斜率。
世界到截图的映射取决于表面和相机，世界到地图只使用固定仿射变换，保留等距俯视比例。
截图投影拟合残留的两轴比例差通过相机内参与正交网格约束恢复，不把画面旋转当作去透视。

- `map_point(frame, surface_id, roi_point)`：把 Wiki／小队的截图位置写入共同地图坐标。
- `roi_point(frame, surface_id, map_point)`：把指定表面的地图目标返回到标定视角。
- `SurfaceLocalizer.locate`：按表面匹配截图并检查独立留出特征；Wiki 和小队共用此入口。
- `SurfaceLocalizer.locate_squad`：先检测唯一小队圆环，再进入共同定位器，不使用固定图像中心。
- `surface_motion.plan_map_click`：使用目标所属表面的实测标定和当前场景配准计算落点；
  地图哈希、表面编号和标定视角必须一致。

Wiki 的 `MapMatcher` 根据 `coordinate_model` 选择平面或分层定位器。
分层结果保留 `surface_id`；同一物品的多个来源若表面身份不一致，即使二维坐标接近也保持待复核。
几何和表面身份未独立验收时，共同定位器不能输出 `accepted`。

上述转换与定位入口已实现，但**未替换现有移动会话**。`manual_move.prepare` 目前仍只支持第 38 章；
第 40 章缺少自己的场景落点标定，不能复用第 38 章矩阵或仅修改章节限制后发送点击。
多表面路线连接也尚未验收。辅助函数存在不代表第 40 章已经可移动。

## 导出

`dev_tools/minimap_orthographic.py` 消费已绑定原始扫描哈希和轨迹哈希的表面模型，
输出目录必须尚不存在。可使用已生成包复现到新目录：

```powershell
.venv\Scripts\python.exe -m dev_tools.minimap_orthographic --source data/chapter_maps/orthographic_package_20260930/chapter_40/source --model data/chapter_maps/orthographic_package_20260930/chapter_40/surface_model.json --tracks data/chapter_maps/orthographic_package_20260930/chapter_40/tracks.json --output data/chapter_maps/orthographic_replay/chapter_40
```

顶层沿用 `map.json`、`map.png`、`reference.png`、`annotations.json`。
`source/surface_map.npz` 额外保存每层道路、观测支持率、ROI 表面标签、各帧各表面的单应矩阵，
以及实拍像素的来源帧和取色表面。`surface_id` 描述显示的道路，`texture_surface_id` 描述参考图取色；
背景沿主要参考平面保留，其他道路按各自高度覆盖。参考图中的弱观测不自动成为可通行道路。
所有表面数据保留，二维显示采用较高表面；显示上的交叉不是路线连接证据。

## 第 40 章实测状态

结果位于 `data/chapter_maps/orthographic_package_20260930/chapter_40/`。
使用现有 104 帧，84 帧取色、20 帧留出取色检查；生成 **730×1395** 的俯视图与 6 个候选表面。
模型联合使用局部道路轨迹与跨重访特征，并对全局表面施加平面／坡道约束。
模型拟合仍是本机实验流程，未替换章节批量扫描入口；导出工具可独立重放已绑定模型。

- 105 个源文件、源码／模型／图片哈希、624 个表面—相机矩阵、图片尺寸及标注器加载检查通过。
- 317,142 个道路像素中，97,470 个落在多个候选表面上；其中哪些是真实叠层、哪些是错误重复解释，
  **尚未区分完成**。不得将该数量解释为已经恢复的真实多层区域。
- 20 个未取色帧的道路并集 IoU 中位数约 0.906、最低约 0.840；共同有效域覆盖率中位数约 79.2%、
  最低约 26.9%。这些帧参与过模型拟合，且并集可能掩盖重复表面，统计不是独立导航验收。
- 留出帧 4 检测到小队圆环，但表面特征配准不一致，定位未通过。抽查 3 张 Wiki 原图，
  每图最佳视角仅有 2–3 个可靠描述子对应，自动标注未通过。拒绝原因和叠加图保留在回放报告中。
- 普通 14 项、困难 6 项 Wiki 资料及原图已附带；20 项待办重新绑定新底图哈希，坐标和表面仍为空。
  可以在既有标注器中做二维人工标注，不能把它当作已验证的移动目标。

当前状态为 `needs_surface_identity_and_movement_validation`、`navigation_ready=false`。
本轮 Python 语法检查、78 项相关回归和 `git diff --check` 通过；
回归覆盖合成相机／双高度／坡道、未入库视角定位、表面与地图绑定及 Wiki 编号保存，
不能替代上述失败的真实截图验收。
尚未完成的工作是稳定表面身份、独立小队定位、真实跨层连接和第 40 章落点标定；
本次没有发送游戏移动输入，也没有覆盖旧地图或旧标注。

本机拟合入口在 `tmp/ch40_stroke120_20260930/`：`global_tracks.py`、`combined_geometry.py`、
`global_planes.py`、`constrain_planes.py`。最终模型和轨迹已随新包保存。
导出与回放复核入口是同目录的 `check_orthographic.py`，回执为包内 `verification.json`、
`squad_replay.json`、`wiki_replay.json`。后续应从这些明确失败项继续，不能只优化预览观感。
