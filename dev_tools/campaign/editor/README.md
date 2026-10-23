# 主线地图编辑器

独立的本地浏览器工作台，使用项目现有 Python 依赖，不依赖 Vue SPA 构建。
服务只监听 `127.0.0.1`；页面加载不会启动游戏控制，扫描和移动只有点击执行后才启动子进程。

## 启动

在仓库根目录执行：

```powershell
python -m dev_tools.campaign.editor
python -m dev_tools.campaign.editor --map data/resources/maps/chapter_41
python -m dev_tools.campaign.editor --root data/resources/maps --port 8766 --no-open
```

也可直接运行 Python 文件入口；入口会以仓库根目录作为相对路径参数的基准：

```powershell
python dev_tools/campaign/editor/launch.py
python dev_tools/campaign/editor/launch.py --map data/resources/maps/chapter_38
python dev_tools/campaign/editor/launch.py --port 8766 --no-open
```

VS Code 的本地 `.vscode/launch.json` 已提供「NKAS: 主线地图工具」调试项，选择后按 F5 启动。
该配置通过 `program` 直接运行 `dev_tools/campaign/editor/launch.py`，使用仓库 `.venv`；地图、
端口和自动打开浏览器行为可通过 `args` 调整。入口直接调用服务端，不额外启动 Python 子进程，
可在 `server.py` 中设置断点。`.vscode` 按现有规则不提交入库。

文件入口与 `python -m` 共用 `dev_tools.campaign.editor.server` 实现，不复制工具逻辑。
`--map` 必须位于 `--root` 内；不指定目录时使用项目默认运行地图目录。

## 目录职责

```text
editor/
├─ __init__.py             # 导出 main、make_server
├─ __main__.py             # python -m 启动入口
├─ launch.py               # Python 文件入口与 VS Code 调试入口
├─ server.py               # 本机 HTTP、静态资源白名单、会话令牌和 API
├─ jobs/
│  ├─ movement.py          # 移动任务、冻结地图版本和子进程状态
│  ├─ scan.py              # 单章扫描、运行包导出与采用
│  └─ wiki.py              # Wiki 配准预览与增量导入
└─ static/
   ├─ index.html           # 编辑工具、地图画布、管理侧栏
   ├─ css/editor.css       # 工作台主题与响应式布局
   └─ js/
      ├─ editor.js         # 画布、标注、属性、撤销/重做与保存
      ├─ panels.js         # 左右页签与键盘导航
      ├─ connectivity.js   # 连通预览与修订
      ├─ movement.js       # 移动任务面板
      ├─ scan.js           # 扫描任务面板
      └─ wiki.js           # Wiki 任务面板
```

共用地图存储、导出和 Wiki 工具位于相邻的 `../maps/`；地图采集子进程使用 `../capture/`。
完整工具索引见 [自动推图工具说明](../README.md)。运行期格式、校验和算法仍位于
`module/campaign/map/`。静态资源通过 `/static/css/`、`/static/js/` 的显式白名单提供，
不开放任意文件访问。

## 页面分区

页面沿用通用模板工作台（`dev_tools/template_studio/web/pages/templates.html`）的浅色主题、
资源选择侧栏、分组页签和内嵌画布；地图画布按横向编辑需求分配空间，不套用截图裁剪的竖屏比例。

- 左侧：地图选择与目录刷新，标注、道路、连通三组编辑任务；画布操作和图层显示保持可访问。
- 中间：地图、难度筛选、编辑历史和缩放工具。
- 右侧：对象列表与属性编辑独立滚动；Wiki、移动和扫描保留独立参数、状态与操作区。
- 文件与副本：展开为浮层，不压缩对象属性编辑区，保留下载、重新加载与历史恢复操作。

详细格式、安全约束和操作流程见 [地图标注文档](../../../doc/map-annotation.md)。

## 光幕与立柱标注

在类型下拉框选择「光幕（立柱连线）」，默认进入折线工具：依次点击两根立柱的地面端点，
按 Enter 完成两点线段；也可切换点工具标记光幕中心。光幕不支持区域或多点折线。
保存格式为 `category=ground_barrier`，沿用当前地图的底图绑定和标注版本校验。

运行时按检测位置关联最近光幕标注，不设置固定距离上限，最终封锁使用标注位置与线段。
标注本身不代表光幕处于关闭或开启状态；须连续两帧实际检测到同一标注的光幕才封路，
立柱单独可见不会封路或解除封锁。光幕确认必须有本章预标注；缺少标注时拒绝确认，
不再从检测框估算封锁落点。解除封锁仍需指定编号的可靠视觉复核。
