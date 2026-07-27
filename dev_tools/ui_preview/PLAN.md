# NKAS UI 重构实施计划

> 对应预览：`dev_tools/ui_preview/preview.html`（v4）。
> 本文档把改造落实到文件、类、方法级，作为正式施工的依据与验收清单。

## 0. 目标与非目标

**目标**

- 用「FastAPI 风格 REST + WebSocket 后端 / Vue 3 SPA 前端 / Electron 薄壳」替换 pywebio 服务端渲染 UI。
- 实现预览确认的布局与操作逻辑：入口自适应（单实例直进调度总览）、任务两级直达、工具任务归实例、调度器组默认折叠、统一控件规格。
- 发布即切换：同一更新内 `/` 重定向到新 SPA、旧 pywebio 页面随之删除；用户配置零迁移。

**非目标与硬约束**

- 不重写 `module/` 下的设备、任务、调度、OCR 任何业务逻辑。
- 不改 `module/config/argument/args.json` / `menu.json` 结构，不改 `config/*.json` 用户配置格式。
- 不改变现有对外 HTTP API 路径语义（`/api/{name}/start` 等已有外部调用方）。
- 不做登录/账号体系；移动端适配放二期（先保证桌面与 Electron）。
- **webapp（Electron 工程）不承载任何实际 UI 代码**：SPA 的源码与构建产物都不进 `webapp/`。Electron 升级成本极高且为手动更新，因此 UI 的任何迭代都不得触发客户端发版——webapp 只保留最小壳（窗口/loadURL/托盘/全局快捷键/Python 进程管理），界面永远由后端实时下发。
- **构建产物入库**：新 UI 为 `webui/`（Vite 工程），构建产物 `webui/dist/` **提交进 git**，随 git 更新通道分发；不改 Updater、不新增 CI 产物分发。代价与纪律见 6.3。
- **不做按时间过渡**：不发布双 UI 并存的迁移版本；发布即切换，只保证更新后旧 webapp/Electron 能正常完整使用。
- **兼容基线**：只存在「旧 Electron + 新后端」一个方向；不存在「新 Electron + 旧后端」场景（Electron 手动发布永远晚于对应后端能力上线），新客户端无需对旧后端做握手降级。

## 1. 总体架构

```
浏览器 / Electron 窗口
   └─ Vue 3 SPA（webui/，Vite 工程；构建产物 webui/dist/ 入库，由后端托管）
        ├─ HTTP  REST  ─┐
        └─ WS  /ws/... ─┤
                        ▼
        uvicorn → Starlette（module/webui/app.py 内挂载）
                        ├─ /            302 → /app/（发布即切换，无并存期）
                        ├─ /app/        新 SPA 静态托管（webui/dist）
                        ├─ /api/...     REST（新，含现有 6 个路由迁移）
                        └─ /ws/...      WebSocket（日志流 / 状态流）
                        ▼
        复用层（不改行为）：ProcessManager / NikkeConfig / ConfigUpdater /
        updater / RemoteAccess / args.json / i18n json
```

依赖评审结论：**不新增 Python 包**。`starlette 0.26.1`、`uvicorn`、`websockets 10.4`、`pydantic 2.11.7` 均已在 `requirements.txt`，直接基于 Starlette 原生 `Route` / `WebSocketRoute` 实现，不引入 FastAPI 包（避免与 starlette 0.26 的版本对齐风险）。

前端位置与分发决策：新 SPA 落在**本仓库顶层 `webui/`**（独立 Node/Vite 工程），与 `webapp/` 零耦合——webapp 不 import、不构建、不分发它。构建产物 `webui/dist/` 直接提交进本仓库，UI 更新与 Python 代码同走 git pull 到达用户；Electron 客户端完全不参与 UI 生命周期。

## 2. 后端改造

### 2.1 新增包 `module/webui/api/`

| 文件 | 内容 |
|---|---|
| `__init__.py` | 导出 `mount_api(app)` |
| `app.py` | `mount_api(app)`：把全部 REST/WS 路由挂到现有 Starlette 实例；`create_spa_mount()`：挂载 `/app/` 静态目录（`webui/dist`，目录不存在时跳过并记日志） |
| `models.py` | pydantic 模型：`InstanceInfo`、`ConfigPatchRequest`、`QueueInfo`、`MonitorInfo`、`ApiResult` |
| `deps.py` | 公共依赖：`get_manager(name) -> ProcessManager`、`load_instance_config(name) -> NikkeConfig`、`validate_instance(name)`（不存在返回 404 的统一处理） |
| `service_config.py` | **新增类 `ConfigService`**：从 `NKASGUI._save_config`（`module/webui/app.py:843`）抽出的配置写逻辑，供 REST 与旧 UI 共用 |
| `routes_instances.py` | 实例 CRUD 与生命周期路由 |
| `routes_config.py` | 配置 schema 下发与即改即存路由 |
| `routes_tasks.py` | 任务队列路由 |
| `routes_system.py` | 系统级路由（含现有 4 个迁移） |
| `ws.py` | **新增类 `LogBroker`、`StateWatcher`** 与 WS 路由处理 |

### 2.2 `ConfigService`（service_config.py，新增）

从 `NKASGUI._save_config` 提取，方法级：

- `ConfigService.patch(config_name: str, key: str, raw_value: Any) -> PatchResult`
  - 校验链：`deep_get(NKAS_ARGS, key + '.valuetype')` → `parse_pin_value` → `deep_get(... '.validate')` + `re_fullmatch` → 空值回退默认（同原逻辑）。
  - 特殊键：`NKAS.Account.Account` / `NKAS.Account.Password` 走 `save_account()` 加密存储并回显 `******`（同原逻辑）。
  - 联动写：遍历 `config_updater.save_callback(k, v)` 返回的派生键一并落盘（同原逻辑）。
  - 落盘：`config_updater.write_file(config_name, config)`。
  - 返回 `PatchResult{ ok, applied: dict, derived: dict, invalid: bool, message }`，由路由层转 JSON；前端据此闪现「已保存」或标红回滚。
- `NKASGUI._save_config` 改为瘦壳：组包后调 `ConfigService.patch`，pin 相关 UI 副作用（invalid 标记、toast）保留在旧 UI 层。

### 2.3 REST 路由清单

**routes_instances.py**

| 路由 | 方法 | 实现 |
|---|---|---|
| `/api/instances` | GET | 新增。遍历 `nkas_instance()`，对每个名字取 `ProcessManager.get_manager(name).state`、`get_config_mod(name)`；运行中实例读 `NikkeConfig.get_next_task()` 得当前/下一任务。返回 `list[InstanceInfo]`，供全局总览与侧边栏 |
| `/api/{name}/start` | POST | 迁移自 `app.py:api_start`（含 `all` 语义），行为不变 |
| `/api/{name}/stop` | POST | 迁移自 `app.py:api_stop`（含 `all` 语义），行为不变 |
| `/api/instances` | POST | 新增。新建实例：复制 `load_config(origin).read_file(origin)` → `State.config_updater.write_file(name, r, mod)`（逻辑同 `ui_add_nkas.add`），校验非法字符与 template 前缀 |
| `/api/{name}` | DELETE | 新增。运行中拒绝（同 `ui_manage._delete`），否则删配置文件 |
| `/api/{name}/export` | GET | 新增。直接文件下载（同 `ui_manage._export`） |
| `/api/instances/import` | POST | 新增。multipart 上传 json，解析文件名规则同 `ui_manage._import` |

**routes_config.py**

| 路由 | 方法 | 实现 |
|---|---|---|
| `/api/{name}/schema` | GET | 新增。合并 `menu.json` + `args.json` + 当前语言 i18n json，产出前端任务树（见 2.4） |
| `/api/{name}/config` | GET | 新增。`load_config(name).read_file(name)` 原样返回（前端按需取键） |
| `/api/{name}/config` | PATCH | 迁移+扩展自 `app.py:api_config_update`，内部改调 `ConfigService.patch`；请求体 `ConfigPatchRequest{key, value}` |

**routes_tasks.py**

| 路由 | 方法 | 实现 |
|---|---|---|
| `/api/{name}/queue` | GET | 新增。复用 `nkas_update_overview_task` 的拆分逻辑：`config.load()` → `get_next_task()` → 按 `manager.alive` 切出 running/pending/waiting，返回 `QueueInfo`（每项含 `command`、`next_run`、`name_i18n`） |
| `/api/{name}/task/{task}/run` | POST | 新增。「立即运行」：`manager.start(func=<task 对应 func>)` 经由 `get_available_mod_func` 解析，等同现有 Tool 任务启动路径 |

**routes_system.py**

| 路由 | 方法 | 实现 |
|---|---|---|
| `/api/restart` | POST | 迁移自 `api_system_restart`，行为不变 |
| `/api/update` | POST | 迁移自 `api_system_update`，行为不变 |
| `/api/rotate` | POST | 迁移自 `api_system_rotate`，行为不变 |
| `/api/system/status` | GET | 新增。`{version: git 短 SHA + 分支, updater_state: updater.state, theme, language}` |
| `/api/system/monitors` | GET | 新增。把 `app.py:_query_windows_monitors` / `_build_screen_number_options` 原样搬入（`app.py:133-278`），替代现在的 JS 点击刷新 hack；前端打开 ScreenNumber 下拉时调用 |
| `/api/system/language` `/api/system/theme` | POST | 新增。写 `State.deploy_config.Language/Theme`（旧 UI 换语言/主题是整页刷新，SPA 改为前端热切换 + 持久化） |

**路由注册顺序**：字面路由（`/api/restart`、`/api/update`、`/api/rotate`、`/api/instances`、`/api/instances/import`、`/api/system/*`）必须先于参数化路由（`/api/{name}...`）注册——否则 `DELETE /api/restart` 这类请求会被 `/api/{name}` 以 `name='restart'` 捕获。迁移现有 6 个路由时保持原有路径与语义不变。

### 2.4 schema 端点产出格式（`/api/{name}/schema`）

```jsonc
{
  "menus": [                      // 来自 menu.json，保持顺序
    { "key": "Daily", "name": "日常", "page": "setting",
      "tasks": [ { "key": "Conversation", "name": "咨询", "help": "..." } ] }
  ],
  "tasks": {                      // 来自 args.json
    "Conversation": {
      "groups": [
        { "key": "Scheduler", "name": "调度器", "collapsed": true,   // Scheduler 组恒 true（预览 v4 结论）
          "fields": [
            { "key": "Conversation.Scheduler.Enable", "arg": "Enable",
              "widget": "checkbox", "title": "启用任务", "help": "...",
              "value": true, "display": "show", "options": [...], "validate": null }
          ] }
      ]
    }
  }
}
```

规则：

- `display: "hide"` 的字段**不下发**（args.json 中 160 个 hide 字段，含 SuccessInterval / FailureInterval / ServerUpdate / Command——预览中用户确认的隐藏需求由数据天然保证）。
- `display: "disabled"` / `"readonly"` 下发标记，前端置灰。
- i18n 键 `{group}.{arg}.name/.help`、`Task.{task}.name/.help`、`Menu.{menu}.name` 在后端按当前语言解析后下发，前端不做 i18n 查表；切换语言 → 重新拉 schema。
- widget 类型全集（来自 args.json 统计，共 447 字段）：`input`(175) / `checkbox`(108) / `storage`(50) / `select`(43) / `datetime`(39) / `textarea`(28) / `lock`(1) / `item_table`(1) / `interception_stone_import`(1) / `interception_stone_charts`(1)。前端渲染器必须全覆盖，缺一即报错兜底为只读文本。

### 2.5 WebSocket（ws.py，新增）

**`class LogBroker`**（每实例一个，懒创建）

- 数据结构：`subscribers: set[WebSocket]`；后台线程复用 `RichLog.put_log` 的增量算法（`module/webui/widgets.py:189-208`）：跟踪 `pm.renderables` 长度与 `renderables_reduce_length` 回卷，增量渲染 HTML 后 `asyncio.run_coroutine_threadsafe` 广播。
- HTML 渲染复用 `RichLog.render` 的纯渲染部分（`widgets.py:101-112`，不依赖 pywebio session），主题随 `State.theme`。
- 首次连接：先回放 `pm.renderables` 全量（与旧 UI `reset+extend` 一致），再进增量循环。
- 路由：`/ws/{name}/log`。连接关闭/实例退出时清理订阅；`ProcessManager.stop` 后保留最后一屏（renderables 不清）。

**`class StateWatcher`**（全局单例）

- 线程每 1s 扫 `ProcessManager._processes` 的 `state`（1 运行 / 2 空闲 / 3 异常 / 4 更新中），变化才广播 `{"type":"state","name":..., "state":...}`。
- 每 10s 对前台订阅的实例算一次 queue（同 `/api/{name}/queue`），变化才广播 `{"type":"queue", ...}`（对齐旧 UI `nkas_update_overview_task` 的 10s 节拍与可见性节流思路）。
- 路由：`/ws/state`（所有实例状态）、`/ws/{name}/queue`（单实例队列）。

**ProcessManager 修改**（`module/webui/process_manager.py`，增量最小）

- `__init__` 新增 `self._log_subscribers: list[queue.Queue] = []`。
- 新增方法 `subscribe_log(self) -> queue.Queue` / `unsubscribe_log(self, q)`。
- `_thread_log_queue_handler` 在 `self.renderables.append(log)` 后追加一行：向各订阅队列 `put_nowait`（满则丢弃最旧，保证不阻塞日志线程）。不改 `renderables` 缓存语义，旧 `RichLog.put_log` 路径不受影响。

### 2.6 `module/webui/app.py` 修改点

- `app()` 内：删除 4 个手写 API 函数（`api_start`/`api_stop`/`api_system_restart`/`api_system_update`/`api_system_rotate`/`api_config_update`），改为 `from module.webui.api import mount_api; mount_api(app)`；路由路径与语义保持完全一致（外部调用方无感）。
- 显示器枚举三函数（`_get_monitor_orientation_label` / `_query_windows_monitors` / `_build_screen_number_options`）移到 `module/webui/api/routes_system.py`，`app.py` 保留 import 以兼容旧 UI（`_bind_dynamic_screen_number_select` 逻辑随旧 UI 退役再删）。
- `NKASGUI._save_config` 改调 `ConfigService`（见 2.2）。
- 施工期 pywebio 页面（`index`/`manage`）原样保留在 `/` 与 `/manage`，供开发对照与回归；发布时（阶段 6）在同一更新内把 `/` 切换为 302 → `/app/` 并删除旧页面——不发布双 UI 并存的过渡版本。

### 2.7 特殊行为迁移清单

| 现有行为 | 位置 | 新方案 |
|---|---|---|
| ScreenNumber 下拉动态枚举显示器 | `app.py:308-417`（JS 注入点击刷新） | `GET /api/system/monitors`，前端打开下拉时拉取 |
| 路径选择（游戏路径等）走 Electron 原生对话框 | `widgets.py:419-660`（postMessage 过 iframe） | SPA 内实现三档回退（见 6.2），旧 Electron 走 postMessage 协议；浏览器降级为普通输入框 |
| Account/Password 加密存储 | `app.py:870-889` | `ConfigService.patch` 内置（见 2.2） |
| `storage` 类型（记录值展示） | `widgets.py:put_arg_storage` | 前端 `FieldStorage` 只读组件 |
| `item_table`（仓库物品表） | `widgets.py:put_arg_item_table` | 前端 `FieldItemTable`，数据来自 config 值 |
| `interception_stone_import` / `interception_stone_charts` | `widgets.py:968-1629` | 前端两个独立组件；图表用 ECharts（按需 chunk，见 3.1） |
| 捐赠/语言/主题首页 | `app.py:show()` | 拆入 AboutView / SettingsView |

## 3. 前端改造（新建 `webui/`，Vite 工程，dist 入库）

**硬约束**：`webui/` 是仓库顶层独立 Node 工程，不依赖 `webapp/` 的任何配置、依赖与构建链；`webapp/` 一个文件都不改（见第 4 节）。构建产物 `webui/dist/` **提交进 git**（`.gitignore` 只忽略 `node_modules`），随 git 更新通道分发。

**分发决策（已确认）**：采用「Vite 构建 + dist 入库」而非免构建源码，理由是开发体验（naive-ui 组件与暗色主题、SFC、TS）。成本账（实测口径见 6.3）：单次 UI 更新入库增量典型 0.1~0.5MB、最坏（vendor 升级）约 1MB，随 git pull 分发可接受；代价是每个 UI 提交必须同时提交重新构建的 dist，由 CI 校验兜底。

### 3.1 技术栈

- Vue 3 + vue-router 4 + Vite 构建（`browserslist: Chrome 94` 与 Electron 15 对齐）。
- npm 依赖：`pinia`、`naive-ui`（组件库，暗色主题完善）、`echarts`（拦截战统计图，单独 async chunk 按需加载，不进首屏）、`dayjs`。
- `naive-ui` 兼容 Chrome 94，已在选型时核实其 ES2020 语法底线；构建 target 锁 `chrome94`，不升级 Vite 大版本。
- TypeScript 宽松模式；类型错误不阻塞构建。
- 本地开发：`cd webui && yarn dev`（vite dev server 代理 `/api`、`/ws` 到 `127.0.0.1:12271`）；提交前 `yarn build`，dist 随提交入库。

### 3.2 目录结构

```
webui/
├── package.json / vite.config.ts / tsconfig.json / .gitignore（忽略 node_modules，不忽略 dist）
├── dist/                    # 构建产物，提交入库，由后端托管于 /app/
└── src/
    ├── main.ts / App.vue
    ├── router.ts                  # 路由表（见 3.4，hash 模式）
    ├── api/
    │   ├── client.ts              # fetch 封装：get/post/patch/del，统一错误 toast
    │   ├── schema.ts              # schema/config/queue 接口
    │   ├── instances.ts           # 实例 CRUD/start/stop
    │   ├── system.ts              # 系统接口
    │   └── ws.ts                  # LogSocket / StateSocket：自动重连（2s 退避），页面隐藏暂停
    ├── stores/
    │   ├── instances.ts           # 实例列表、状态 map、当前实例名
    │   ├── schema.ts              # 任务树/字段 schema 缓存（按实例+语言）
    │   ├── logs.ts                # 每实例环形日志缓冲（400 条，对齐后端）
    │   └── app.ts                 # 主题/语言/入口模式
    ├── components/
    │   ├── InstanceCard.vue       # 总览实例卡（状态点/当前任务/启停/进入）
    │   ├── TaskRail.vue           # 二级任务栏：分组折叠、启用点、运行旋转、筛选框
    │   ├── LogConsole.vue         # 定宽时间戳+等级 chip、自动滚动开关、WS 增量渲染
    │   ├── StatusPill.vue / SwitchField.vue 等基础件
    │   └── config/
    │       ├── ConfigForm.vue     # schema 驱动渲染器：遍历 groups/fields，分发字段组件
    │       ├── GroupCard.vue      # 分组卡片：collapsed 支持（Scheduler 默认折叠+摘要行）
    │       └── fields/            # FieldInput / FieldNumber(单位后缀) / FieldSwitch /
    │                              # FieldSelect / FieldDatetime(图标) / FieldTextarea /
    │                              # FieldStorage / FieldItemTable / FieldStoneImport /
    │                              # FieldStoneCharts / FieldLock / FieldReadonly（兜底）
    ├── views/
    │   ├── DashboardView.vue      # 全局总览
    │   ├── InstanceLayout.vue     # 实例工作台外壳：TaskRail + <router-view>
    │   ├── OverviewView.vue       # 调度总览（hero 调度卡 + 时间线队列 + LogConsole）
    │   ├── TaskView.vue           # 普通任务页（hero + ConfigForm + 右侧锚点 + 本任务搜索）
    │   ├── ToolView.vue           # 工具任务页（hero 启停 + ConfigForm + LogConsole）
    │   ├── ManageView.vue         # 实例管理（新建/导入/导出/删除，复刻 ui_manage 交互）
    │   ├── SettingsView.vue       # 更新器/界面/远程访问
    │   └── AboutView.vue          # 捐赠与帮助
    └── styles/
        ├── tokens.css             # CSS 变量：直接沿用 assets/gui/css 的 --nkas-* 值（双主题）
        └── base.css
```

### 3.3 关键交互逻辑（与预览 v4 对齐）

- **入口自适应**：`router.beforeEach` 或 `App.onMounted`：`instances.length === 1` → `replace('/i/{name}/overview')`，否则 → `'/'`。
- **即改即存**：字段 `change` → `api/schema.patchConfig` → 成功显示「✓ 已保存」1.2s；`invalid` 时字段标红并回滚旧值；失败 toast。
- **任务栏状态**：绿点 = `Scheduler.Enable`；旋转 = `StateWatcher` 推送的运行任务命中。
- **日志**：进入 Overview/Tool 页 `LogSocket.connect(name)`，离开断开；HTML 片段 `v-html` 追加（后端已渲染），自动滚动开关本地状态。
- **主题/语言**：切换 → `POST /api/system/theme|language` + 前端热切换（主题即切；语言重新拉 schema 后刷新当前页，不整页 reload）。
- **守卫**：删除运行中实例禁用（同旧逻辑）；停止按钮二次确认。
- **标题栏安全区**：旧 Electron 的 `AppHeader` 透明悬浮在 iframe 之上（顶部 51px 整条是窗口 drag 区，右上约 220px 有 4 个窗口控制按钮），该区域内的点击落在壳上而不是 SPA。SPA 顶栏布局必须避开：顶栏高度 ≤51px 时右侧预留 ≥220px 无交互区；顶栏内容超出 51px 的部分不受影响。主题/语言切换等按钮不得放在右上死角。
- **后端重启自愈**：`/api/restart` 后旧客户端不会重新 loadURL（stderr 监听在首次就绪后已移除），页面必须自愈：REST 失败时显示「后端连接中」遮罩并轮询 `/api/system/status`，恢复后重拉当前页数据；WS 断线 2s 退避重连（已含在 ws.ts）。

### 3.4 路由表（hash 模式）

采用 `createWebHashHistory`：StaticFiles 直挂即可，无需服务端 history fallback，刷新/深链天然可用。

```
#/                     DashboardView（多实例入口）
#/i/:name              InstanceLayout
  ├─ overview          OverviewView
  ├─ task/:task        TaskView
  └─ tool/:task        ToolView
#/manage               ManageView
#/settings             SettingsView
#/about                AboutView
```

## 4. Electron 定位与改造

**决策：`webapp/` 不承载任何实际 UI 代码，Electron 保持手动更新，UI 迁移不以新客户端发布为前提。** UI 源码在 `webui/`、产物入库由后端托管，Electron 只是加载后端页面的壳。旧 Electron + 新后端的兼容由第 5 节契约保证。

现有 webapp 各部分的处置：

| 部分 | 处置 |
|---|---|
| `main/`（窗口、托盘、全局快捷键、单实例锁、`pyshell.ts`） | **不动** |
| `preload`（现仅暴露 `window.electron.versions`） | **不动**；pickPath 直调属于可选新客户端内容，见下 |
| `packages/renderer`（`NKAS.vue` iframe 壳 + postMessage `nkas-webui` 中继） | **不动**。它是已发布旧客户端的一部分，其中的 postMessage 中继必须永久有效——SPA 端保留该协议的回退实现（6.2），但协议代码本身不新增、不修改 |
| 打包/发布（`electron-updater` 手动提示） | **不动**，不纳入后端 Updater |

可选的新客户端（仅当未来决定手动发布时）只包含壳级改动，仍不引入任何业务 UI：

- `main/src/config.ts`：`webuiUrl` 由 `http://127.0.0.1:12271` 改为 `http://127.0.0.1:12271/app/`。
- `preload`：新增 `window.nkas = { pickPath(options) => ipcRenderer.invoke('dialog:pick-path', options) }`；`main` 的 `dialog:pick-path` handler 不变。
- renderer 缩减为纯 splash/降级提示页（无业务 UI）；SPA 不随 Electron 打包，`webui/dist` 一律由后端托管，避免双份资源错配。

## 5. 版本兼容策略（不更新 Electron 的场景）

先明确现状两条更新通道：

- **后端**：应用内 Updater（git pull + pip），高频、免费、无需重装。**SPA 产物随这条通道分发**（dist 入库，见 6.3），与 Electron 完全解耦。
- **Electron 客户端**：`electron-updater` 的 `checkForUpdatesAndNotify`（仅提示，用户手动下载），低频，**保持手动，本次改造不触碰**。
- **关键事实**：Electron 的界面从来不是打包在客户端里的——现有 renderer 只是 iframe 壳（`NKAS.vue`），页面由后端 `http://127.0.0.1:12271` 实时下发。因此「UI 更新」历来跟后端走，不更新客户端也能拿到新界面；本次改造只是把这个事实固化成约束（webapp 零 UI 代码）。

场景基线（已确认的决策）：

- 不做按时间过渡：不发布双 UI 并存版本，发布即切换。
- 不存在「新 Electron + 旧后端」场景：Electron 手动发布永远晚于对应后端能力上线，新客户端无需对旧后端做握手降级。

需要保证的场景只有**旧 Electron + 新后端**：

| 场景 | 结果 |
|---|---|
| 更新后（旧 Electron + 新后端） | **可完整使用新 UI**：`/` 302 → `/app/`，SPA 在旧 iframe 里运行；pickPath 走 postMessage 旧协议回退（6.2）；WS/REST 同源无跨域；顶栏避开 AppHeader 安全区（3.3）；后端重启页面自愈（3.3） |

### 6.0 旧客户端接触点审计（逐行核对 webapp 源码后的完整清单）

旧 Electron 与后端一共只有 7 个接触点，逐一核对结论：

| # | 接触点 | 位置 | 兼容结论 |
|---|---|---|---|
| 1 | 进程启动：`gui.py --port <port> --electron`，python 路径与端口读 `config/deploy.yaml` | `main/src/config.ts`、`pyshell.ts` | ✅ 不动 `gui.py`；`--electron` 参数永久保留 |
| 2 | 就绪探测：监听后端 **stderr**，匹配 `Application startup complete` / `bind on address`（uvicorn 默认日志行） | `main/src/index.ts:153-158` | ✅ 不改 uvicorn 启动方式即天然满足；固化为契约 4 |
| 3 | 页面加载：iframe `src = webuiUrl`（即 `/`） | `NKAS.vue`、`main/src/index.ts:144-150` | ✅ 契约 1 覆盖；302 重定向 iframe 自动跟随，origin 不变 |
| 4 | 全局快捷键：POST `/api/all/start`、`/api/all/stop`、`/api/restart`、`/api/update`、`/api/rotate`，handler 无条件 `.json()` 解析响应 | `main/src/index.ts:226-269` | ✅ 契约 2 覆盖；补充为契约 5：这 5 个路由**任何情况下都必须返回合法 JSON**（含 4xx/5xx），否则旧客户端抛未捕获异常 |
| 5 | pickPath：postMessage 协议 | `NKAS.vue:45-87` ↔ `widgets.py:524-660` | ✅ 逐字段核对一致（见 6.2），旧壳校验 `event.origin === webuiOrigin`，SPA 同源天然满足 |
| 6 | 窗口标题栏：`AppHeader` 透明悬浮层覆盖页面顶部 51px（drag 区 + 右上窗口控制按钮） | `App.vue`、`AppHeader.vue` | ⚠️ **新发现布局约束**：SPA 顶栏必须避开该区域，已写入 3.3「标题栏安全区」 |
| 7 | 后端重启：`/api/restart` 后客户端不重载页面，页面需自愈 | `main/src/index.ts:153-158`（`removeAllListeners`） | ⚠️ pywebio 时代靠手动刷新快捷键；SPA 内建重连与恢复，已写入 3.3「后端重启自愈」 |

### 6.1 永久契约（写入设计约束，不可破坏）

1. `/` 永远返回可用页面：发布后 = 302 → `/app/`（同一更新完成切换，无并存期）。旧客户端因此「被动升级」到新 UI，与历史行为一致。
2. 现有 6 个 `/api/*` 路由（`{name}/start|stop|config`、`restart`、`update`、`rotate`）路径与语义永久冻结（Electron 全局快捷键与外部脚本依赖）。
3. `/api/system/status` 上报 `{ api_version, spa_version, capabilities: { spa: true, websocket: true } }`，作为新旧两端的能力握手依据；旧后端无此端点即视为 `api_version = 1`。
4. 进程级契约：后端启动入口保持 `gui.py --port <port> --electron`；uvicorn 的 stderr 启动日志（`Application startup complete` / `bind on address`）永久保留，不得关闭、重定向或改文案——旧 Electron 靠它判定就绪（`main/src/index.ts:153`）。
5. 快捷键 5 路由（`/api/all/start|stop`、`/api/restart|update|rotate`）任何情况下返回合法 JSON 响应体（含错误分支），旧客户端 handler 无条件 `.json()` 解析。

### 6.2 pickPath 三档回退（SPA 内实现）

```
window.nkas?.pickPath            → 新 Electron（preload 直调）
postMessage 'nkas-webui' 协议     → 旧 Electron（其 renderer 的 NKAS.vue 中继仍然有效）
普通文本输入框                    → 纯浏览器
```

协议字段（与 `NKAS.vue:45-87`、`widgets.py:524-660` 逐字段核对一致，SPA 照搬）：

- 请求：`{source:'nkas-webui', type:'dialog:pick-path:request', requestId, payload:{mode, title, defaultPath, accept}}`
- 响应：`{source:'nkas-electron', type:'dialog:pick-path:response', requestId, payload:{ok, canceled, path, error}}`

### 6.3 SPA 资源分发（dist 入库）与构建纪律

- `webui/dist/` 提交进 git（`.gitignore` 只忽略 `node_modules`），随 git 更新通道自然分发；**Updater、Electron 打包一律不改**。
- **构建纪律**：凡改动 `webui/src` 的提交必须同时提交重新构建的 `dist/`；CI 增加一道校验——重新 `yarn build` 并与工作区 `git diff --exit-code webui/dist`，不一致则失败，防止「忘构建」导致源码与产物错位（用户走主分支 git pull，不是按 tag 更新，错位会直接落到用户端）。
- **体积账（按 git 机制的实际口径）**：产物全量约 2~4MB raw；git blob zlib 压缩后约 1/3~1/4。文件名带内容 hash——未变化的 chunk（vue/echarts/naive-ui vendor 占大头）两次构建完全相同，git 天然去重；单次 UI 更新的入库增量 ≈ 变化的 chunk 压缩后大小，**典型 0.1~0.5MB，最坏（vendor 升级）约 1MB**，永久累积、只增不减。构建配置据此优化：vendor 与业务代码分包（`manualChunks`），把 Vue/naive-ui/echarts 固定为独立 chunk，让日常提交的增量最小化。
- 后端启动时若 `webui/dist` 缺失/过旧（源码检出不完整）：`/app/` 返回引导页（提示执行一次更新），不影响 `/api/*`。
- 浏览器与 Electron 看到的都是后端托管的同一份产物，**单一事实源，版本永远一致**。
- 已否决的替代方案：CI 产 zip + Updater 下载分发（新增整套发布基础设施与失败面，且与 dist 入库相比没有体积优势）；免构建 ESM 源码直挂（分发最省，但放弃 naive-ui/SFC/TS 的开发体验）。

## 6. 阶段计划与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| 0 准备 | 冻结预览；建 `module/webui/api/` 与 `webui/` 工程骨架（含 manualChunks 分包、dist 入库配置、CI 构建一致性校验）；确认不新增 Python 依赖 | 计划评审通过 |
| 1 后端 API + WS | 2.1-2.6 全部；`ConfigService` 单测式脚本验证（临时脚本调 patch 校验写盘结果） | `curl` 验证全部 REST；`websocat` 看到日志流与状态推送；旧 UI 主要页面手动回归无异常；`py_compile` 通过 |
| 2 SPA 骨架 + 只读页 | `webui/` 工程：hash 路由/布局/TaskRail/Dashboard/Overview + LogSocket/StateSocket | `yarn build` 通过且 dist 入库；浏览器打开 `/app/`：入口自适应、实时日志滚动、状态变化实时反映；**旧 Electron 冒烟**（Chrome 94 兼容、iframe 内渲染、标题栏安全区） |
| 3 配置系统 | schema 端点 + ConfigForm 全 11 种 widget + 即改即存 + 特殊行为（monitors/pickPath/账号加密） | 抽样 20 个任务全部字段可渲染可保存；保存后 `config/nkas.json` 与旧 UI 写出的值逐项一致；hide 字段不出现 |
| 4 剩余页面 | Manage / Settings / About / Tool 页；`item_table`、stone import/charts | 对照旧 UI 功能清单逐条过；ECharts 按需 chunk 加载、渲染一致 |
| 5 新客户端打包（可选，手动发布） | 第 4 节壳级改动（loadURL 指向 `/app/`、preload pickPath）；作为独立手动 release，不阻塞主流程；webapp 不引入任何业务 UI | 新客户端直进 SPA；文件选择框可用；单实例锁/托盘正常；旧客户端用户不更新也不受影响 |
| 6 切换与清理 | **同一更新内**：`/` 改为 302 → `/app/`；删除 pywebio 页面、`widgets.py` 等死代码（第 8 节清单） | 新 UI 全功能回归；仓库 grep 无 pywebio 页面引用；旧 Electron 更新后端后直接使用新 UI |

## 7. 兼容与风险

- **配置兼容**：读写都走 `ConfigUpdater`，格式零变化；施工期新旧 UI 可同时操作同一实例。
- **API 兼容**：现有 6 个 `/api/*` 路径语义不变（外部脚本/远程控制不受影响）。
- **端口/部署**：uvicorn 入口、端口、`deploy/` 安装流程不变；`webui/dist` 入库随 git 分发，无 Updater 改动；CI 仅新增「dist 构建一致性」校验。
- **风险 1（中）**：`starlette 0.26` 的 WebSocket 与 pywebio 同进程共存——施工期实测；若冲突，WS 改挂独立 path 已在设计内，影响可控。
- **风险 2（中）**：`naive-ui` 在 Chrome 94（Electron 15）下的兼容——构建 target 锁 `chrome94`，阶段 2 用旧 Electron 做冒烟页验证；不通过则回退自写组件（预览的 CSS 体系可直接用，工作量为组件层）。
- **风险 3（高工作量）**：`item_table` / `interception_stone_*` / `storage` 三个特殊 widget 迁移，集中在阶段 4，预留充分时间。
- **风险 4（低）**：多语言——schema 后端解析 i18n，语言切换即重拉，无前端表维护成本。
- **风险 5（低）**：dist 入库的维护纪律——靠 CI 构建一致性校验兜底；vendor 分包保证日常增量在 0.1~0.5MB。

## 8. 完成后删除清单（阶段 6，与切换同一更新执行）

- `module/webui/app.py` 中 `NKASGUI` 全部 pywebio 页面方法、`app_manage`、`debug()`
- `module/webui/widgets.py`、`module/webui/pin.py`、`module/webui/base.py`
- `assets/gui/css/`（变量值已迁入 `webui/src/styles/tokens.css`）
- `module/webui/fastapi.py` 中 pywebio 静态挂载（保留 Starlette 壳）
- `webapp/` 不删、不改：旧客户端的 iframe 壳永久作为兼容层存在
