# 分层俯视地图与共用坐标

坐标约定与平面章节一致：`reference.png` 和 `map.png` 使用同一个无透视的俯视画布，
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

`orthographic_surfaces` 供表面几何与定位使用，移动会话不接受此模型。
移动会话支持平面地图，以及已有有效局部标定的 `local_parallax` 地图；后者仍受同层道路和标定支持域限制。
二维表面交叉不表示跨层连通，具体操作见[地图标注说明](map-annotation.md)。

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

## 验证边界

```powershell
.venv\Scripts\python.exe -m unittest tests.test_surface_geometry tests.test_surface_localizer tests.test_surface_motion
```

检查每个表面与相机的正反投影、来源哈希和地图绑定，再用独立截图复核表面身份与定位。
未参与取色的帧若参与过模型拟合，就不能作为独立几何验证；道路并集 IoU 也可能掩盖重复表面。
`navigation_ready=false` 的包不能仅凭预览平滑或回投可逆声明导航可用。
