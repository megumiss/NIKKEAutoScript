# 立体小地图缓存重建

`dev_tools/minimap_layered.py` 消费已完成的版本 2 扫描，在独立目录生成局部视差重建预览。
当前输出固定标记为 `needs_geometry_review`、`navigation_ready: false`。正式章节扫描可通过
`--process-3d` 接入分层重建与原始道路区域重绘；Wiki 自动标注和小队移动尚未完成验收，
不能作为已验收的导航地图。

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

第 40 章本次结果保存在 `data/chapter_maps/layered_review_20260930/chapter_40/`。
`verification.json` 区分文件／坐标一致性和重访道路轮廓统计；
`preview.jpg` 与 `layers.png` 用于查看道路和相对高度分布。

本次处理 134 帧，导出尺寸为 1497×1313；135 个原始文件及副本哈希一致，
地图反查原帧的数值回投误差最大 1.386 个输出像素，文件与坐标数据检查通过。
这些只验证导出内部一致性。

整图质量检查未通过：12 对远隔重访帧、2717 个轮廓样本的最近道路轮廓距离中位数约
9.22 个 ROI 像素，P95 约 32.28px；相同样本采用共同运动比例时分别约 10.00px、29.00px。
局部模型没有稳定改善重访误差，不能据稀疏轨迹的 1.91px 中位留出误差宣布整图合格。
后续需要可靠的跨重访表面身份和全局约束，并重新验证整图；本次结果保持待复核，
没有替换 `current/chapter_40`，也没有发送游戏输入。

## 第 40 章 120px 缓存的融合针孔修复

`stroke120_20260930/chapter_40/source/` 的 104 帧已用于同输入对照。
融合权重先以 `float32` 保存，再与 `float64` 候选比较，会因向上舍入漏写像素的深度和来源。
当前统一比较与存储精度，并为重复命中同一像素的并列候选固定选择首个观测，
颜色、深度与来源共用唯一索引。

新结果：`data/chapter_maps/stroke120_fusion_fix_20260930/chapter_40/`。
`comparison.jpg` 为左旧右新的对照，`fix_verification.json` 保存校验结果。
使用原 `stroke120_layered_20260930/chapter_40/` 的 `--depth-cache` 重建；
相机、深度模型、初步道路支持率、画布及最终阈值均未改变。

| 同批像素指标 | 修复前 | 修复后 |
| --- | ---: | ---: |
| 初步道路像素缺少深度／来源 | 50,217 | 0 |
| 道路内部面积 1–9px 的背景小孔 | 12,217 | 899 |
| 道路内部未显示的像素 | 65,017 | 43,203 |

统计区域以初步道路支持率大于 0.85 为准；内部区域由 7×7 腐蚀去除边缘，
背景小孔按八连通分组。小孔数量减少约 92.6%，不等同于道路几何误差减少。
105 个输入文件与副本哈希、逐像素深度与来源帧绑定均通过；
来源坐标回投至输出像素的最大距离为 1.413px 以下，符合四邻点投影的栅格范围。
15 项分层／局部模型回归及 Python 语法检查通过，新增回归覆盖浮点舍入、重复命中、
并列候选和历史较强观测保护。

较大的重影、错接与缺口仍需要跨帧表面几何修复，结果保持
`needs_geometry_review`、`navigation_ready: false`；本次未控制游戏或覆盖旧地图。

## 整帧实拍参考图实验

`dev_tools/minimap_reference.py` 从已完成的分层缓存生成视觉参考图，再直接提取道路。
旧分层 `reference.png` 仅投影道路样本，依赖逐像素视差，缺少整帧空白的覆盖约束，
因此几何偏差会把道路错误地叠加到空隙中。该工具保留完整 RGB 帧，用共同参考平面投影，
按有效视野中心距离选择来源；背景与道路具有相同的覆盖规则，同权重保留先前来源。
参考比例取稀疏拟合视差比例的中位数，仅作为视觉拼接基准，不表示所有道路位于同一高度。

```powershell
.venv\Scripts\python.exe -X utf8 -u -m dev_tools.minimap_reference --source data/chapter_maps/stroke120_fusion_fix_20260930/chapter_40 --output data/chapter_maps/reference_first_20260930/chapter_40
```

输出目录必须尚不存在且不能位于输入目录内部。默认 `--holdout-stride 5` 每五帧留出一帧，
这些帧不参与取色，但仍参与此前缓存几何拟合。工具不控制游戏，不改写输入或正式包。

| 文件 | 内容 |
| --- | --- |
| `reference.png` | 含网格、道路、空白和可见图标的整帧拼图 |
| `map.png`、`terrain_mask.png` | 用现有青蓝色道路阈值、连通域筛选与闭运算直接提取的道路 |
| `reference_data.npz` | 每像素来源帧及 ROI 坐标、每帧单应矩阵、共同参考比例及裁剪后坐标 |
| `map.json` | 输入／代码／图片哈希、尺寸、未取色帧检查、质量限制 |

第 40 章本次使用 84 帧取色、20 帧检查，输出 1382×1019、369,561 个道路像素。
实拍纹理、网格及道路之间的空隙明显恢复；直接提取的道路保留同一坐标。
逐像素来源颜色完全一致，ROI 数值回投最大误差低于 0.000027 输出像素，
107 个输入文件和 3 个源码哈希、图片与数组尺寸、道路与实拍提取的一致性全部通过。
这些检查验证数据绑定和导出，不验证真实地图几何。

未取色帧回投只统计共同有效域，覆盖率中位数 88.8%、最低 32.9%；
道路 IoU 中位数 0.672、最低 0.362，双向最近轮廓距离中位数 4.12、P95 29.73 个 ROI 像素。
道路阈值与最邻近轮廓不能证明同一条道路或层间连接；共同参考平面仍保留高度视差和接缝错位。
该结果固定使用 `shared_plane_reference`、`navigation_ready: false`，
没有提供分层身份、跨层可通行关系、独立几何验收或真实移动验证。

`preview.jpg` 显示新实拍／道路，`comparison.jpg` 显示旧／新实拍；
`verification.json` 保存实际缓存检查，复核脚本在 `tmp/ch40_stroke120_20260930/verify_reference_first.py`。
新工具的回归涵盖共同平面正反变换、空白覆盖道路、同权重稳定选择、留出帧隔离与空覆盖检查：

```powershell
.venv\Scripts\python.exe -m py_compile dev_tools/minimap_reference.py tests/test_minimap_reference.py
.venv\Scripts\python.exe -m unittest tests.test_minimap_reference tests.test_minimap_layered tests.test_minimap_surface_fit
```

本次 21 项回归通过；实验包可供人工观察和后续几何校正，不替换此前完整资料包。

## 原始帧局部修复

对用户选定的去噪分层基线运行：

```powershell
.venv\Scripts\python.exe -X utf8 -m dev_tools.minimap_repair --source data/chapter_maps/stroke120_fusion_fix_20260930/chapter_40 --output data/chapter_maps/raw_frame_repair_20260930/chapter_40
```

输出目录必须不存在，且不能位于基线内部。工具仅提出封闭小洞和 7×7 闭运算缺口，
闭运算本身不直接生成道路。取 18 像素内 16 个可信邻点的高度分位数；高度跨度超过 0.06 则拒绝。
偶数帧选候选（道路支持比例 >90%），奇数帧验证固定候选（>85%）；两组分别要求至少三帧道路内部支持、
相机位置跨度至少 80。留出帧也曾参与父版本几何拟合，所以这不是独立几何验收。
小于等于 256 像素的孤岛只在整块无合格核心时隐藏；缺少支持不能证明道路不存在。

第 40 章实际补回 13,213 个空白像素、提升 8,011 个不确定像素、隐藏 5,596 个碎片像素。
输出同时更新道路数组和修补点来源，可查 `repair_evidence.npz` 中的候选高度、训练／验证统计及修改掩码。
原始逐帧深度不变，修补点深度来自局部推断加原始帧验证，不能套用父版本逐帧深度完全相等的校验。
整体坐标保持不变，复杂边界仍保留缺损；不会因此开放小队移动。

## 道路面与边界联合修复

此方案被用户指出会把错误碎片并入路面，已停止推进；保留代码和产物供复核。
当前方向见下一节「原始帧投影后二次绘制」。

用户要求利用面连续性进一步补齐道路、去除碎片，使用：

```powershell
.venv\Scripts\python.exe -X utf8 -m dev_tools.minimap_surface_repair --source data/chapter_maps/raw_frame_repair_20260930/chapter_40 --output data/chapter_maps/surface_repair_final_20260930/chapter_40
```

输出目录必须为新目录；输入是逐像素分层基线，不能是已经进行面推断的输出。
工具保持原相机、投影、旋转、原点和画布，处理过程如下：

1. 对原始帧建立遮挡掩码，彩色图标／白字不计入道路或空白观测；暗色背景仍参与空白反证。
2. 从 40 像素内的 32 个可信邻点提出五个高度分位数，加上原深度，共六个候选。
   偶数帧选择高度，奇数帧复核；稳定路面核心使用原高度。19×19 闭运算只生成候选范围。
3. 对整个候选范围执行二元最小割：数据项以 70% 支持为道路／背景等代价位置，
   八邻域的边界代价为 `3 / 邻距 × exp(-(高度差/0.06)^2)`。
   同层内部的细洞与毛刺会增加多余边界，因而倾向补齐／剔除；高度突变不强行合并。
4. 任一帧组支持比例低于 35%、少于三次道路观测或相机跨度不足 60 的点禁止成为道路，
   即使周围道路连续也不覆盖这些点。合格的稳定道路核心作为保留约束。

本次比逐像素修复版补回 40,338 像素，剔除 41,560 像素（其中原蓝色道路 718 像素）；
小于等于 256 像素的空洞从 1,283 降为 71，碎片从 58 降为 5。
测试覆盖内部缺测、附着毛刺、真实间隙、高度跳变、真实小岛和图标遮挡，28 项相关回归通过。

输出字段有明确区分：

| 字段／文件 | 含义 |
| --- | --- |
| `surface_road_mask` | 最终空间推断路面，`map.png` 按此渲染 |
| `terrain_probability` | 选定深度在原始帧中的道路支持比例；不由最小割标签伪造概率 |
| `surface_validation_probability` | 偶／奇帧组道路支持比例的较小值 |
| `surface_inferred_mask` | 最终路面中，较低帧组支持不足 85% 的区域 |
| `inference_overlay.png` | 黄色显示上述推断区域 |
| `surface_repair_evidence.npz` | 点、深度、稳定核心、候选范围、禁止填充掩码及两组逐点证据 |

推断区域 84,288 像素，占最终路面约 18%；新增像素中有 1,873 个原始支持比例不足一半。
面约束改善完整性，也可能扩大错误道路，不能只凭视觉平滑或回投可逆宣称真实几何正确。
两组观测共同参与最终面选择，不构成独立几何验收；原逐帧深度缓存保留，输出深度来自候选选择。
正式定位／移动消费者尚未接入 `surface_road_mask`，继续按旧概率阈值渲染会与本版 map 不一致。
本版明确记录 `map_render_mode=surface_road_mask` 并保持 `navigation_ready=false`，未执行游戏内点击。

## 原始帧投影后二次绘制

```powershell
.venv\Scripts\python.exe -X utf8 -m dev_tools.minimap_projected_redraw --source data/chapter_maps/stroke120_fusion_fix_20260930/chapter_40 --output data/chapter_maps/frame_projection_20260930/chapter_40
```

输入为未经过面推断的 `local_parallax` 基线；输出为新目录，不覆盖旧包。
从每帧已有 `ratio_plane` 严格推导 ROI→map 的局部单应，保留坡度，不重新拟合全局投影。
道路轮廓和空白都用 `warpPerspective` 直接绘制，空白可以擦掉基线碎片，RGB 使用完全相同的变换。
只有源图标／边缘不可用或已知属于另一高度的区域不参与该块绘制。

输出画布分为 64×64 块：至少 100 个原基线可信像素作为锚点；局部单应相对原坐标的中位误差≤2、
P90≤5 像素；可见比例≥85%、锚点道路召回≥95%。还要求另一帧相机跨度≥60、
共同可见比例≥80%、道路 IoU≥90%，然后选单一来源绘制整块共同有效区域。
未通过的块原样保留；这些检查只是局部一致性，不能独立证明真实几何。

本次 168 个候选块中 42 个通过，绘制 169,369 个像素，补回 2,102 个空白像素并擦除 7,784 个旧路面像素。
另有不确定像素转为原帧道路，共改变 17,016 个像素颜色。不是全图修复，未做形态补面。

| 文件 | 含义 |
| --- | --- |
| `map.png` / `reference.png` | 通过检查的块按原始帧重绘，其余保留父版本 |
| `redraw_data.npz` | 每像素 patch ID、source frame、source ROI，以及绘制后的蓝色道路掩码 |
| `redraw_report.json` | 每块矩阵、来源与检查帧、残差／IoU、输入与代码哈希 |
| `surface_data.npz` | 未改写的父版本缓存，不冒充新绘制结果的完整几何数据 |
| `projection_examples.jpg` | 原始帧黄框、旧 map、重绘 map，按变化像素最多选三个块 |
| `redraw_coverage.png` | 绿色为实际重绘区域 |

共享变换在 `module/campaign_prototype/local_projection.py`；对于已配准至原始帧的 Wiki 局部截图，
`query_to_map = roi_to_map @ query_to_roi`，并需检查目标位于同一表面的有效绘制区域。
测试覆盖坡道精确展开、非零原点、旋转、Wiki 裁剪／缩放组合和空白覆盖；19 项相关回归通过。
真实 Wiki 截图→原始帧配准与目标所属表面的判定仍待验证，正式 Wiki 匹配入口未改为接受本实验包。
同样未接入小队移动，`navigation_ready=false`。

复核脚本 `tmp/ch40_stroke120_20260930/verify_projected_redraw.py` 对全部绘制点检查图像与来源精确一致，
回投误差小于 0.000026 像素，未绘制区域与父版本完全相同；回执保存在输出 `verification.json`。

## 按原始道路和高度区域重绘

```powershell
.venv\Scripts\python.exe -X utf8 -m dev_tools.minimap_projected_redraw --regions --source data/chapter_maps/stroke120_fusion_fix_20260930/chapter_40 --output data/chapter_maps/road_region_projection_20260930/chapter_40
```

`--regions` 使用 `minimap_region_redraw.py`。原始帧的道路／高度标签界定局部范围，
不再以旧 map 的逐像素形状或固定 64×64 方块决定是否允许重绘。
源区域的闭运算和外扩仅用于界定可检查范围，实际道路始终来自未经这些操作修改的原始帧道路掩码。
全局坐标和局部高度平面仍来自原分层缓存，没有宣称重新标定了所有高度。

平面在两帧之间满足 `q_other = q_source - ratio(q_source) * camera_delta`，
其对应单应在原帧间执行轮廓与高度互验，然后沿用 `frame_plane_to_map` 投影到固定画布。
来源和检查帧相机跨度在 60～200 之间，两张检查帧彼此跨度至少 40；
每帧共同可见域≥60%、道路 IoU≥90%，双向边界距离 P90≤2 ROI 像素且两边各至少 40 个边界像素。
对应位置至少 200 个有效高度观测，高度差中位数≤0.025、P80≤0.05。
两检查帧最终公共区域再次验证 IoU 和边界距离，避免大区域的平均分掩盖局部错位。

通过的来源之间以区域整体质量排序，单个像素不因道路／空白而改变优先级。
`regions/NNNN.png` 保存每条来源记录的实际原图有效域；`redraw_data.npz` 每像素归属来源，
每条记录保存最终贡献像素数与两组验证变换，可供之后 Wiki 对应链复用。
来源互验依赖共同缓存相机和高度先验，不是独立几何验收，也不证明跨层连通或移动可行。

第 40 章最终 189 个来源区域贡献 537,760 个像素，是固定方块版 169,369 像素的 3.18 倍；
未通过区域仍保留父版本。上方部分区域仍有缺损，`surface_data.npz` 仍是未改写的父版本证据，
本次显示数据在 `redraw_data.npz`，格式标记为 `projected_raw_regions`，导航继续禁用。
当前已经打开的手工编辑版本不受影响；新包也可通过编辑器 `--map` 参数打开。

`rejection_events` 区分候选区域拒绝和逐检查帧拒绝次数，不能把跨层级事件相加当作失败区域数。
对照图在 `coverage_comparison.jpg`、`comparison.jpg`，绿色覆盖图为 `redraw_coverage.png`。
复核脚本 `tmp/ch40_stroke120_20260930/verify_region_redraw.py` 检查全部绘制域和原始帧投影，
原始输入、旧图、坐标和父缓存均未变；16 项相关测试覆盖坡道转换、同色误匹配、边界对齐及高低层分离。
