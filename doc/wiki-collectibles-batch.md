# Wiki 收集物批量导入与地图补采

入口：[wiki_collectibles.py](../dev_tools/wiki_collectibles.py)。它读取 GameKee 的“地图收集（主线）”和“地图收集（困难）”目录，下载文章与图片，将可靠配准的点写入已有地图包。无需启动游戏；所有图片、文章和配准结果保存在缓存目录，可以重复执行。

解析支持结构化表格、HTML 表格、无表头表格和“标题＋图片组”。同一条目的场景图和地图图不会重复计数；文章及条目总数按实际获取结果统计。

## 一条命令运行

在仓库根目录、项目 Python 环境中运行。标注和 Wiki 导入默认读取 `data/chapter_maps/runtime/`，完整采集包存放在 `data/chapter_maps/local/`。`--maps-root` 可显式指定其他集合目录或单章包：

```powershell
python dev_tools/wiki_collectibles.py
```

默认处理 Wiki 当前公布的全部普通／困难文章，缓存位于 `data/wiki_collectibles/`。可以多次传入 `--maps-root`，按给定顺序选择本章底图；同一根目录内若有多个同章包，则报告歧义，需缩小根目录，避免混用坐标。不会自动用最新修改时间替用户挑地图。

普通和困难共用本章 `map.png`、坐标系与哈希；各自的对象、名称、位置和来源独立。新对象包含 `difficulty: normal|hard`，标签和颜色也区分难度。`map.json` 中的 `difficulty` 仍描述底图采集来源，不限制可写入的收集物难度。

常用参数：

```powershell
# 只处理第 38 章两种难度
python dev_tools/wiki_collectibles.py --chapters 38

# 地图尚未采齐时，先下载 Wiki 资料
python dev_tools/wiki_collectibles.py --download-only

# 沿用缓存，补图后离线配准及导入
python dev_tools/wiki_collectibles.py --offline

# 只核对配准结果，不写 annotations.json
python dev_tools/wiki_collectibles.py --chapters 38 --dry-run
```

`--difficulty normal|hard|both` 默认 `both`；`--cache` 指定可复用缓存；`--refresh` 重新获取文章和图片；`--retries` 默认每次下载额外重试 2 次。HTTP 被 CDN 拦截时，脚本用独立的无头 Edge 浏览器打开公开 Wiki 页面并读取响应。此路径需要 Python `playwright` 和已安装的 Microsoft Edge。其他设备若缺少 Python 包，可在其项目环境执行 `python -m pip install playwright`，无需下载额外浏览器。不会使用个人浏览器资料或登录状态。

## 缓存、续跑与写入规则

- 每章按 `chapter_NN/normal/`、`chapter_NN/hard/` 缓存文章、`items.json`、原始图片、`matches.json` 和配准叠图。全局 `catalog.json` 保存来源目录，`progress.json` 保存本次执行结果。
- 缺图标为 `missing_map`，已下载资料保留。补齐底图后执行同一命令即可；匹配缓存绑定原始图片、底图、道路缓存哈希和算法版本。
- 配准失败、无小队圆环、缺少足够道路、未支持的截图布局、多个截图给出冲突位置，均记为 `needs_review`，保留证据，不写入猜测坐标。错误配准的地图包也拒绝使用。
- 自动匹配支持 16:9 场景截图、独立小地图裁图和包含完整小地图的局部场景图。裁图通过蓝色矩形面板与实际小队圆环识别；无道路支持、多个圆环或面板位置不明确时保留待复核。裁图来源记录原图尺寸、裁剪框及映射原始裁图像素的变换，缩放不会改变标注坐标。解析出条目不表示已完成坐标配准。
- 采用道路 IoU ≥ 0.85、不同位置候选分差 ≥ 0.10、参数变化导致的坐标分散 ≤ 20px，且结果必须在底图内。原始 PNG 会先过滤细网格线，避免将网格误识别为道路。
- 对象 ID 为 `wiki_文章ID_编号`。默认保留已有同源对象的坐标、名称、备注和连接，只补充旧导入对象的难度信息；普通第 38 章旧 ID 兼容，不会重复添加。
- `--update-existing` 才会重新覆盖同源点，包含对这些点所做的人工修改。默认不删除 Wiki 中已消失的旧对象。每次实际写入都复用标注工具的哈希检查、修订冲突检测、备份与原子替换；不修改底图或参考图。
- 单篇错误记录后继续后续篇章。退出码 `0` 为所有所选条目处理完成，`1` 为仍有缺图／待复核／单篇失败，`2` 为初始化或全局错误，`130` 为 Ctrl+C／缓存根目录 `STOP` 文件停止。非零结果不等于进度丢失；详情以 `progress.json` 为准。

## 批量地图补采

[minimap_chapters.py](../dev_tools/minimap_chapters.py) 仍从游戏当前打开的普通章节向前倒序采集。采集一套底图即可供两种难度的 Wiki 点复用。

```powershell
# 先在游戏进入普通 38 章野外；按当前实际章节修改 --start
python dev_tools/minimap_chapters.py --start 38 --end 1 --retries 2
```

游戏以管理员身份运行时，命令也需同等权限。工具核对窗口、焦点、章节 OCR 和紧凑地图后才扫描或切章。默认复用正上方俯视比例、游戏斜向朝向、120px 连续拖动和高频采样。

`--output` 默认 `data/chapter_maps/local/captures/`，`--driver-root` 默认当前代码所在仓库。每章固定为 `chapter_NN/`；日常续采继续使用该本地目录。历史预览、失败包和旧 `current/` 快照也位于 `local/`，需检查时显式指定 `--root` / `--maps-root`。已有不合格旧包不自动升级为可用地图。

采集结束后，将单章运行所需数据导出到尚不存在的运行目录；既有地图不会被覆盖：

```powershell
python -m dev_tools.map_runtime --source data/chapter_maps/local/captures/chapter_38 --destination data/chapter_maps/runtime/chapter_38
```

若目标已存在，使用新的名称（如 `runtime/rescan_20261007/chapter_38`）并在编辑器中选择该版本。导出保留采集包中已有标注；不会把另一坐标系的旧标注自动迁移到新图。

批量入口支持分别设置探索拖动和保存帧间距。例如，保持 120px 拖动并把保存帧间距减至 40px：

```powershell
python dev_tools/minimap_chapters.py --start 34 --end 1 --stroke-px 120 --keyframe-px 40
```

`--stroke-px` 是探索阶段的鼠标行程，默认 120；最初两次运动标定仍各拖动 240px。`--keyframe-px` 是保存原始帧的相机位移间距，默认 80；高频截图间隔仍为 0.03 秒。缩短拖动并减小关键帧间距有助于增加重叠，但不能修复透视偏差或缺少道路约束；扫描完成也不代表分层重建几何通过验收。已有合格包会跳过，参数不会自动重采或改变它的坐标。

- 每章最多 1 次初始采集＋`--retries` 次重试。采集、校准、重建或配准失败都有记录，失败次数耗尽后仍可继续下一章。
- 失败扫描移入该章 `attempts/时间戳/`，保留原图和扫描记录。已完整扫描但尚未导出的数据优先尝试重建；无效旧扫描不会占用本次初始采集次数。
- 已导出的合格包跳过，保护其标注坐标；既有不合格包也不覆盖，应改用新的输出根目录补采。`roads_exhausted` 仍不代表完整相机域已验证。
- 逐章进度通过临时文件替换保存。重启时根据游戏实际章节设置 `--start`，复用输出目录；它不会自动把游戏切回之前的章节。
- 扫描结束或异常恢复使用左上角最小化。保存失败或最小化失败仍进入驱动释放路径。
- **无法确认切章页面时会受控暂停，退出码 `2`。** 后续章节的输入依赖当前页面，不能为了继续批次而盲点。`progress.json` 记录暂停原因；处理现场后从实际章节续跑。扫描期间也检查输出根目录的 `STOP` 文件，停止不进入下一章。
- 退出码 `0` 为所选章节均已采集／跳过，`1` 为批次已走完但有失败章节，`2` 为页面不确定或全局错误，`130` 为用户停止。

## 验证

真实采集需核对章节切换、道路覆盖和导出包；匹配需核对普通／困难来源及目标坐标。
合成地图不能代替全章节采集或游戏内到点验证。
