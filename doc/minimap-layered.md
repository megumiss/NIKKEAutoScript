# 立体小地图缓存重建

`dev_tools/minimap_layered.py` 消费已完成的版本 2 扫描，在独立目录生成局部视差重建预览。
当前输出固定标记为 `needs_geometry_review`、`navigation_ready: false`。正式章节扫描可通过
`--process-3d` 接入分层重建与原始道路区域重绘；`local_parallax` 已接通 Wiki 原始帧定位和
独立的局部移动标定入口，整章几何、跨层移动仍未验收，不能作为已验收的全章导航地图。
当前使用方式见 [分层地图的 Wiki 和移动测试](map-annotation.md#分层地图的-wiki-和移动测试)。

正式单章入口（游戏须先进入第 40 章普通野外页面，使用新的输出目录）：

```powershell
.venv\Scripts\python.exe -X utf8 -m dev_tools.minimap_chapters --start 40 --end 40 --process-3d --stroke-px 120 --output data/chapter_maps/formal_3d
```

省略 `--process-3d` 时沿用平面重建，默认步长为 120。该选项作用于本次命令的所有章节，
混合平面与 3D 章节时分开运行。编辑器也提供单章扫描、3D 选项、停止及打开结果，
见 [标注编辑器](map-annotation.md#正式扫描)。
3D 处理失败可用相同命令续跑已完成的原始采集；已有正式包的处理模式不一致时拒绝覆盖，需换输出目录。

## 运行

从仓库根目录执行，输出目录必须尚不存在：

```powershell
.venv\Scripts\python.exe -m dev_tools.minimap_layered --source data/chapter_maps/current/chapter_40/source --output data/chapter_maps/layered_new/chapter_40
```

可用 `--depth-cache <已有分层重建目录>` 重用其原始逐像素视差。
工具会重新拟合轨迹，并核对原始扫描哈希、投影和相机位置是否一致；缓存文件的哈希也写入新结果。
原始帧与旧地图、标注不被改写。

## 几何与文件

重建复用 `minimap_surface_fit` 的局部轨迹模型，联合估计相机修正、投影修正和道路视差比例。
多视角逐像素匹配补充道路内部观测，局部仿射视差平面区分不同高度并允许坡道斜率。
候选道路最后重投影到所有原帧，空白视野参与否决错误道路。

`map.png` 的蓝色表示超过 85% 的可见原帧支持道路，且至少有 3 帧正观测；灰色表示
支持率超过 50%、仍待核对。它们是观测一致性条件，不是独立几何验收门槛。
缺少可靠观测的位置不会凭空补路，所有原始证据保留在数组和帧副本中。

| 文件 | 内容 |
| --- | --- |
| `map.png`、`reference.png` | 道路一致性预览、同坐标实拍拼图 |
| `map.json` | 章节、图片哈希、输入与代码哈希、坐标模型、逐帧支持量及局部平面 |
| `surface_model.json`、`tracks.json` | 稀疏联合拟合结果、全部入选轨迹及留出误差 |
| `surface_data.npz` | 反投影道路支持率、正观测／可见帧数、原帧反查坐标及视差比例 |
| `depth/frame_NNNNN.npz` | 每帧的原始和局部平面视差、置信度、局部区域标签 |
| `source/` | 按 SHA-256 核对过的原始扫描及全部帧副本 |

局部区域标签只在所属帧内有意义；不同帧的标签编号不表示同一层。
连续视差数据保留不同高度观测，但二维预览每个像素只显示一个候选表面，不能表示跨层连通。

ROI 点到地图的换算为：

`map = (vertical + (project(projection, roi) - vertical) / ratio + camera[frame]) @ rotation.T - origin`

`ratio` 必须来自对应局部表面，不能统一设为 1。
垂直原点沿用背景网格的正方形、主点位于 ROI 中央的相机假设；
`1 - 1 / ratio` 是相对相机高度的估计，不是实际游戏高度。

## 验证边界

轨迹误差保留末两次观测作为留出样本；它们仍共享其他轨迹拟合的相机，不能视作独立整图验证。
整图还需核对远隔重访帧、跨方向观测、层间接缝及真实移动。
`roads_exhausted` 也不证明全部相机域已经覆盖。

合成回归覆盖双高度共同坐标、不同道路位移、坡道与离群深度、跨层插值保护、
空白视野反证和原始数据保护。运行：

```powershell
.venv\Scripts\python.exe -m unittest tests.test_minimap_layered tests.test_minimap_surface_fit -v
```

## 离线重建与修订工具

以下工具从本地缓存生成新包，输出目录必须尚不存在；输入地图、原始帧和人工标注应保留。
各工具的坐标模型及证据不同，选择下游定位器前先核对 `map.json`，不能只复制预览图片。

| 入口 | 输入与结果 | 使用限制 |
| --- | --- | --- |
| `dev_tools.minimap_reference` | 分层缓存生成共同参考平面的整帧 RGB 拼图，并提取道路 | `shared_plane_reference` 仅用于视觉参照，不提供跨层身份 |
| `dev_tools.minimap_repair` | 对分层基线的小洞提出局部高度候选，经不同帧组核对后修补 | 候选深度属于局部推断，不能冒充逐帧实测深度 |
| `dev_tools.minimap_surface_repair` | 以原帧支持和高度约束推断连续道路面 | `surface_road_mask` 与原始观测概率分开保存，推断不能证明可通行 |
| `dev_tools.minimap_projected_redraw` | 按局部高度平面把原始道路、空白及 RGB 投影到固定画布 | 固定块通过支持域和跨帧道路检查后才绘制 |
| `dev_tools.minimap_projected_redraw --regions` | 按原始道路／高度区域投影，使用两张跨视角帧验证 | 正式 3D 扫描使用此路径；验证仍不证明跨层连通 |

查看参数与所需输入：

```powershell
.venv\Scripts\python.exe -m dev_tools.minimap_projected_redraw --help
.venv\Scripts\python.exe -m dev_tools.minimap_reference --help
.venv\Scripts\python.exe -m dev_tools.minimap_repair --help
.venv\Scripts\python.exe -m dev_tools.minimap_surface_repair --help
```

### 原始帧投影结果

局部转换由 `module/campaign_prototype/local_projection.py` 提供，保留相机、旋转、原点和坡度。
道路和空白共用投影及覆盖规则，RGB 与道路使用相同来源；不以形态填充结果代替原始观测。

- `redraw_data.npz` 保存每像素来源帧、ROI 坐标、绘制道路和区域归属。
- `redraw_report.json` 保存来源矩阵、检查帧、残差、IoU 与输入／代码哈希。
- `regions/NNNN.png` 保存区域模式的实际原图有效域。
- `surface_data.npz` 保留父包几何缓存，绘制变化以 `redraw_data.npz` 为准。
- 未通过检查的区域保留父包像素，`navigation_ready=false` 不会因局部重绘而自动变为真。

Wiki 图先配准到原始帧，再组合 `query_to_map = roi_to_map @ query_to_roi`。
目标必须有同一表面的局部证据；人工道路修订通过 `annotations.json.terrain_edits` 消费。
已保存有效局部标定的 `local_parallax` 包可进行同层移动测试，详见[地图标注说明](map-annotation.md)。

### 回归与现场检查

```powershell
.venv\Scripts\python.exe -m unittest tests.test_minimap_reference tests.test_minimap_repair tests.test_minimap_surface_repair tests.test_minimap_region_redraw tests.test_local_projection
```

核对来源颜色、ROI 回投、输入哈希及未绘制区域是否保持一致，再检查远隔重访轮廓、层间接缝与真实道路。
多组验证帧若共享拟合相机和高度先验，属于一致性检查，不能替代独立几何或游戏内移动验证。
