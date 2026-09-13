# 仓库指南

## 项目结构与模块组织
核心自动化逻辑位于 `module/`，按功能划分（如 `module/coop`、`module/mission_pass`、`module/device`）。主要入口：
- `main.py`：任务调度与自动化主循环
- `gui.py`：Web UI 启动入口（Uvicorn）

配置与运行时文件在 `config/`；模板图与多语言资源在 `assets/`（如 `assets/zh-CN/event_dated/...`）；日志与错误截图在 `log/`。  
桌面 Tauri 2 工程位于 `webapp/`；Vue SPA 位于 `webui/`，由 Python 后端托管。  
SPA 源码结构：`webui/src/views/`（按页面拆分）、`webui/src/stores/`（Pinia 共享状态）、`webui/src/composables/`（Tauri 壳、文本域等复用逻辑）、`webui/src/components/`（壳与通用组件）；`App.vue` 只是布局壳，按路由切换视图。

## 任务范围与 Skill 选择
- 本文件只适用于 NIKKEAutoScript。
- 按实际任务和技术栈选择 Skill。Vue 界面复用现有组件与主题；纯协议、数据解析、文档或构建说明修改不触发 UI 设计、品牌或营销流程。
- 只读取与当前改动相关的文档章节；本任务中已读且未变更的内容不重复读取。
- 目标明确的低风险、可逆操作按上下文继续；仅在关键信息无法推断，或敏感、范围外操作尚缺必要授权时确认。已有会话授权在原范围内有效，Git 推送仍遵守下方限制。

## 构建、测试与开发命令
以下为环境初始化和启动命令；验证要求统一见“测试规范”。依赖已就绪且依赖清单未变更时无需重复安装。

Python 环境初始化：
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
启动脚本与 Web UI：
```powershell
python main.py
python gui.py --host 127.0.0.1 --port 12271
```
桌面壳依赖初始化（在 `webapp/` 目录，需要 Node.js 20、stable Rust 和 Windows C++ Build Tools）：
```powershell
yarn install --frozen-lockfile
```
SPA 依赖初始化（在 `webui/` 目录）：
```powershell
yarn install --frozen-lockfile
```
构建产物 `webui/dist` 提交入库。注意 `webui/vite.config.ts` 设置了 `emptyOutDir: false`，构建不会自动清理旧的哈希产物；提交 `dist` 前先删除不再被 `index.html`/chunk 引用的旧文件，只提交当前构建实际引用的文件。

## 代码风格与命名规范
Python 使用 4 空格缩进，单行不超过 120 字符，字符串优先单引号（见 `pyproject.toml` 的 Ruff 配置）。  
命名遵循现有约定：函数/变量 `snake_case`，类名 `PascalCase`，模板与按钮常量全大写。  
功能代码放在对应模块目录，避免把设备层逻辑混入业务任务模块。

## 常见代码习惯
- 优先小步改动：一次只解决一个问题，避免顺手大改或跨模块重构。
- 优先早返回：减少嵌套层级，让主流程更清晰（尤其是 UI/OCR 循环逻辑）。
- 异常处理要具体：能捕获明确异常类型就不要直接 `except Exception`；兜底异常要带上下文日志。
- 日志可追踪：关键分支打印必要信息（任务、页面、按钮/模板名），避免无意义重复日志。
- 边界保护先行：对坐标、数组索引、OCR 结果为空等情况先判空/校验再继续。
- 保持向后兼容：新增参数应提供默认值，避免影响既有任务配置与调用链。
- 资源改动要成对：模板重命名/替换时同步检查 `assets.py` 与实际图片，避免悬空引用。
- 删除代码要可验证：删除调试或废弃逻辑后，至少执行语法检查与一次最小路径回归。
- 注释只写 non-obvious 的原因（为什么这么做/不能这么做），不复述代码行为；禁止在注释里保留中间尝试过程的痕迹。

## 高频代码用法说明
- Web UI 为 `webui/` 下的 Vue SPA（构建产物 `webui/dist` 提交入库），后端是纯 Starlette 工厂 `module/webui/app.py:app`，通过 `module/webui/api/` 暴露 REST 与 WebSocket；pywebio 已移除。
- 页面切换：优先 `self.ui_ensure(page_xxx)` 进入目标页面，不要直接假设当前页面状态。
- 循环骨架：多数任务采用 `while 1` + `self.device.screenshot()` + 条件分支；每个分支完成后 `continue`，保持状态机清晰。
- 点击防抖：优先使用 `appear_then_click(..., interval=1)` 或 `Timer` 控制点击频率，避免短时间重复点击导致误操作。
- 按钮判定：单按钮用 `appear/appear_then_click`，多候选用 `appear_any/appear_then_click_any`，减少重复 if 代码。
- OCR 文本：优先复用 `appear_text` / `appear_text_then_click`；涉及多语言文本时优先使用 `Langs.xxx`，避免硬编码中文或英文。
- 滑动翻页：统一用 `ensure_sroll(...)`（项目内保持该方法名），并在翻页后重新截图再识别。
- 资源常量：按钮/模板统一从各模块 `assets.py` 引用；`assets.py` 多为自动生成文件，非必要不要手改结构。
- 日志习惯：关键流程用 `logger.hr` 标阶段，分支结果用 `logger.info/warning`，异常路径保留足够上下文便于复现。

## 测试规范
按受影响的模块选择检查，跨模块改动合并对应要求；以下为唯一验证清单。

| 改动范围 | 必要检查 |
| --- | --- |
| 纯文档（含 AGENTS.md），不修改应用源码或构建输入 | 核对内容、引用路径和 `git diff --check`；不触发应用构建 |
| Python 源码 | 对改动的 `.py` 文件执行 `python -m py_compile`，并进行相关路径的运行验证；目前没有完整的 Python 单元测试体系 |
| 桌面壳源码或构建配置 | 在 `webapp/` 执行 `yarn test` 和 `yarn run compile` |
| SPA 源码或构建配置 | 在 `webui/` 执行 `yarn run build`，交互变化验证相关页面流程 |
| OCR/模板资源 | 提供可复现的游戏内验证路径，覆盖受影响的语言资源 |

桌面开发可先用 `yarn run check` 快速反馈；相同 target/features 的检查已由测试或构建覆盖时不重复执行，不同配置仍需单独验证。检查通过后，仅在相关输入变更、新失败或尚未覆盖的风险出现时重跑。无法完成的必要验证应说明原因，不把构建通过当作游戏内验证完成。

## 提交与合并请求规范
提交信息保持简短、祈使语气，历史中常用中文，数据更新常带 `ZH:` 前缀。示例：
- `修复毒蛇Pass无法领取的问题`
- `ZH: 更新 NIKKE 咨询对话`

## Git 推送限制
- `master` 分支禁止自动推送。只有用户在当前请求中明确要求推送到 `master` 时，才可以执行推送。
- 用户未明确要求时，只允许在本地提交；不得以“验证”“同步”或其他理由自行推送。

PR 建议包含：变更目的、影响模块/资源、验证步骤，以及 UI/OCR 改动对应的截图或日志片段；有相关 Issue 时请关联。
PR 描述只写最终行为：diff 里看不出来的取舍可以写，从未合入的中间状态/被否决的方案一律不提。

## 安全与配置建议
不要提交 `config/` 中的敏感信息或个人账号数据。  
Windows 环境建议使用纯英文（ASCII）路径，避免运行期路径编码问题。
