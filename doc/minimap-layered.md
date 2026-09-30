# 立体小地图缓存重建

`dev_tools/minimap_layered.py` 消费已完成的版本 2 扫描，在独立目录生成局部视差重建预览。
当前输出固定标记为 `needs_geometry_review`、`navigation_ready: false`，尚未接入章节批量扫描、
Wiki 自动标注或小队移动；不能作为已验收的导航地图。

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
