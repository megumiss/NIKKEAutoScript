# 仓库指南

## 项目结构与模块组织
核心自动化逻辑位于 `module/`，按功能划分（如 `module/coop`、`module/mission_pass`、`module/device`）。主要入口：
- `main.py`：任务调度与自动化主循环
- `gui.py`：Web UI 启动入口（Uvicorn）

配置与运行时文件在 `config/`；模板图与多语言资源在 `assets/`（如 `assets/zh-CN/event_dated/...`）；日志与错误截图在 `log/`。  
前端 Electron 工程独立在 `webapp/`，有自己的依赖与测试流程。

## 构建、测试与开发命令
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
变更后快速语法检查：
```powershell
python -m py_compile module\...\*.py
```
前端开发（在 `webapp/` 目录）：
```powershell
yarn install --frozen-lockfile
yarn run compile
yarn test
```

## 代码风格与命名规范
Python 使用 4 空格缩进，单行不超过 120 字符，字符串优先单引号（见 `pyproject.toml` 的 Ruff 配置）。  
命名遵循现有约定：函数/变量 `snake_case`，类名 `PascalCase`，模板与按钮常量全大写。  
功能代码放在对应模块目录，避免把设备层逻辑混入业务任务模块。

## 测试规范
仓库当前没有完整的 Python 单元测试体系；Python 改动至少执行 `py_compile`，并进行针对性运行验证。  
前端改动需执行 `yarn test`（当前为 `webapp/tests/app.spec.js`）和 `yarn run compile`。  
涉及 OCR/模板资源改动时，需提供可复现的游戏内验证路径，并覆盖对应语言资源。

## 提交与合并请求规范
提交信息保持简短、祈使语气，历史中常用中文，数据更新常带 `ZH:` 前缀。示例：
- `修复毒蛇Pass无法领取的问题`
- `ZH: 更新 NIKKE 咨询对话`

PR 建议包含：变更目的、影响模块/资源、验证步骤，以及 UI/OCR 改动对应的截图或日志片段；有相关 Issue 时请关联。

## 安全与配置建议
不要提交 `config/` 中的敏感信息或个人账号数据。  
Windows 环境建议使用纯英文（ASCII）路径，避免运行期路径编码问题。
