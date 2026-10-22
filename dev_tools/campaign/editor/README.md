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

旧入口已移除；仓库内调用统一使用 `dev_tools.campaign.editor`。
`--map` 必须位于 `--root` 内；不指定目录时使用项目默认运行地图目录。

## 目录职责

```text
editor/
├─ __init__.py             # 导出 main、make_server
├─ __main__.py             # python -m 启动入口
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
