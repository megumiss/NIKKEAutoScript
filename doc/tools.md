# NKAS 代码工具方法与常用逻辑速查

本文基于当前代码实现整理，重点覆盖以下高频内容：

- 页面相关：页面建模、页面切换、页面兜底处理
- Button 相关：识别、点击、偏移、批量筛选
- 等待/重试相关：`Timer`、限速、循环模板
- 常用代码块：可直接复用的业务流程模板

## 1. 工具层结构总览

核心链路（由底到高）：

1. `module/base/resource.py`
- `Resource.parse_property()`：解析多语言 dict 配置（如 `{'zh-CN': ..., 'en-US': ...}`），返回当前语言数据。
- `release_resources()`：释放 Button/Template 缓存。

2. `module/base/button.py`
- `Button`：封装区域、颜色、模板图、点击区域。
- 负责颜色检测、模板匹配、多尺度匹配、偏移跟随等。

3. `module/base/base.py`（`ModuleBase`）
- 把 `Button` 能力封装为业务常用 API：`appear` / `appear_then_click` / `appear_text` 等。
- 维护 `self.interval_timer` 做按钮级别限速。

4. `module/handler/info_handle.py`（`InfoHandler`）
- 统一处理公告、奖励、系统错误、登录弹窗等“干扰 UI”。

5. `module/ui/page.py` + `module/ui/ui.py`（`UI`）
- `Page` 图模型 + 页面跳转逻辑（`ui_get_current_page`、`ui_goto`、`ui_ensure`）。

6. 业务模块（`module/daily`、`module/shop` 等）
- 通过 `UI` 与 `ModuleBase` 提供的方法完成具体任务流程。

## 2. 页面相关方法

### 2.1 `Page` 页面图模型

文件：`module/ui/page.py`

- `Page(check_button)`：页面检测锚点（通常是 `XXX_CHECK`）。
- `Page.link(button, destination)`：声明“从当前页面点击哪个按钮可到达目标页面”。
- 全局 `page_xxx` + `.link(...)` 构成页面跳转图。

典型定义方式：

```python
page_main = Page(MAIN_CHECK)
page_shop = Page(SHOP_CHECK)
page_shop.link(button=GOTO_BACK, destination=page_main)
page_main.link(button=MAIN_GOTO_SHOP, destination=page_shop)
```

### 2.2 `UI.ui_get_current_page()`

文件：`module/ui/ui.py`

作用：

- 连续截图，遍历 `ui_pages` 用 `ui_page_appear()` 判定当前页。
- 若未知页，尝试 `GOTO_MAIN` 或 `ui_additional()` 弹窗处理。
- 超时后抛出 `GamePageUnknownError`，要求手动切到支持页。

适用场景：

- 任务开始前定位当前页。
- 页面切换失败后的重新定位。

### 2.3 `UI.ui_goto(destination)`

作用：

- 先根据 `Page.links` 反向构建“可到达目标页”的父子关系（`page.parent`）。
- 循环检测当前所在页，命中后点击对应 `link button` 逐步跳转。
- 到达目标页后按 `confirm_wait` 二次确认稳定。

特点：

- 不写死路径，依赖页面图自动路由。
- 对多级跳转（如 `main -> ark -> arena -> rookie_arena`）很友好。

### 2.4 `UI.ui_ensure(destination)`

作用：

- 先 `ui_get_current_page()`，若已在目标页直接返回。
- 否则调用 `ui_goto()` 执行跳转。

推荐：

- 业务入口统一用 `ui_ensure(page_xxx)`，不要手写多个页面判断分支。

### 2.5 `UI.ui_wait_loading()`

作用：

- 针对 `UI_LOADING_1~4` 轮询等待。
- `confirm_timer`：loading 持续出现足够时间后确认。
- `overall_timer`：loading 未出现也不会无限等待。

## 3. Button 相关方法

### 3.1 `ModuleBase.appear()`（最核心）

文件：`module/base/base.py`

关键参数：

- `offset=0`：走纯颜色检测（`button.appear_on`），快但抗干扰较弱。
- `offset=(x,y)` 或 `True`：走模板匹配（`button.match`），`True` 使用配置 `BUTTON_OFFSET`。
- `threshold`：模板匹配相似度或颜色阈值。
- `interval`：限速（命中/检测间隔），通过 `self.interval_timer` 控制。
- `static=False`：用于按钮非固定位置场景（弹窗确认键、动态布局等）。

建议：

- 静态 UI 元素优先 `offset=(30,30)` 模板匹配。
- 文本/颜色稳定且位置固定才考虑纯颜色 `offset=0`。

### 3.2 点击封装

- `appear_then_click()`：出现即点。
- `appear_then_click_any([...])`：多个按钮任一命中即点。
- `appear_with_flip_then_click()`：支持翻转模板（镜像元素）。
- `appear_with_scale_then_click()`：支持缩放模板（比例变化元素）。

### 3.3 位置获取与文本点击

- `appear_location(button, ...)`：返回按钮中心点 `(cx, cy)`。
- `appear_text(text, threshold=0.7, lang='ch')`：
  - OCR 识别并返回文本坐标；
  - 内置图片 hash 缓存，减少重复 OCR 开销。
- `appear_text_then_click(...)`：识别文本后点击。

### 3.4 `Button` 本体能力

文件：`module/base/button.py`

高频方法：

- `match()`：模板匹配，命中后会更新 `_button_offset`。
- `match_with_scale()`：多尺度模板匹配。
- `match_luma()`：亮度通道匹配（抗色偏）。
- `match_several()`：多目标匹配（列表场景常用）。
- `appear_on()`：颜色相似度检测。
- `match_appear_on()`：结合偏移后的颜色确认（二次验真，防误点）。
- `crop()` / `move()` / `shift_button()`：生成派生按钮区域。
- `filter_buttons_in_area()` / `merge_buttons()`：筛选、去重按钮集合。

## 4. 等待/限速/重试相关

### 4.1 `Timer`

文件：`module/base/timer.py`

常用方法：

- `Timer(limit, count=0).start()`
- `reached()`：超过 `limit` 且达到 `count` 次确认
- `reset()` / `clear()`
- `reached_and_reset()`

`count` 的意义：

- 防止“单帧误判导致提前结束”，适合截图周期不稳定环境。

### 4.2 按钮级限速（`interval`）

文件：`module/base/base.py`

- `appear`/`appear_then_click` 里的 `interval` 不是全局 sleep，而是“同名按钮冷却”。
- 高频循环中强烈建议给点击类判断加 `interval`，避免狂点。

### 4.3 通用重试装饰器 `retry`

文件：`module/base/retry.py`

能力：

- 指定异常类型、重试次数、退避系数、抖动。
- 适合包裹网络、IO 或偶发失败动作。

### 4.4 `run_once`

文件：`module/base/decorator.py`

用途：

- 在循环里只执行一次初始化动作（如首次滚动到顶部、只做一次健康检查）。

## 5. 常用代码块（可复用）

### 5.1 轮询 + 点击 + 稳定确认（标准模板）

```python
skip_first_screenshot = True

while 1:
    if skip_first_screenshot:
        skip_first_screenshot = False
    else:
        self.device.screenshot()

    if self.appear_then_click(TARGET_BTN, offset=(30, 30), interval=1):
        continue

    if self.appear(TARGET_PAGE_CHECK, offset=(30, 30)):
        break
```

### 5.2 先确保页面再执行动作

```python
self.ui_ensure(page_shop)

while 1:
    self.device.screenshot()
    if self.appear_then_click(BUY, offset=(30, 30), interval=1, static=False):
        continue
    if self.appear(SHOP_CHECK, offset=(30, 30)):
        break
```

### 5.3 处理干扰弹窗（推荐放主循环顶部）

```python
while 1:
    self.device.screenshot()

    if self.ui_additional():
        continue

    if self.appear_then_click(TARGET, offset=(30, 30), interval=1):
        continue
```

### 5.4 OCR 文本定位点击

```python
self.device.screenshot()
pos = self.appear_text("全部领取", threshold=0.7, interval=1, lang="ch")
if pos:
    self.device.click_minitouch(pos[0], pos[1])
```

### 5.5 列表页面滑动查找

```python
self.ensure_sroll_to_top(count=2, delay=0.8)

for _ in range(6):
    self.device.screenshot()
    if self.appear_then_click(TARGET_ITEM, offset=(10, 10), interval=1, static=False):
        break
    self.ensure_sroll(count=1, delay=0.5)
```

## 6. 参数经验值与踩坑

### 6.1 参数经验值

- `offset`：常见 `(5,5)`、`(10,10)`、`(30,30)`。
- 模板 `threshold`：常见 `0.8~0.95`，易误判场景建议更高。
- `click_timer`：常用 `Timer(0.3)`。
- 页面确认 `confirm_timer`：常用 `Timer(2~3, count=2~5)`。

### 6.2 常见坑位

1. 忘记循环内截图  
只点不截会一直用旧画面判断。

2. `interval` 没设置  
高频循环会产生重复点击，触发误操作或风控行为。

3. 动态按钮仍用 `static=True`  
弹窗确认按钮等位置变化场景需要 `static=False`。

4. 页面跳转完成后未二次确认  
建议使用 `confirm_timer` 防止“过场动画中误判已到达”。

5. 方法名拼写  
滚动相关方法当前是 `ensure_sroll*`（项目中即此拼写），调用时注意不要写成 `ensure_scroll*`。

## 7. 扩展新页面/新功能的推荐流程

1. 在 `module/ui/assets.py` 新增 `XXX_CHECK` / 跳转按钮 `Button` 资源。  
2. 在 `module/ui/page.py` 新增 `page_xxx = Page(XXX_CHECK)`，并补齐 `.link(...)`。  
3. 在 `module/ui/ui.py` 的 `ui_pages` 加入新页面。  
4. 业务代码中统一用 `self.ui_ensure(page_xxx)` 进入页面。  
5. 主循环顶部调用 `self.ui_additional()` 做弹窗兜底。  
6. 用 `Timer + interval` 控制点击节奏，避免过快操作。

## 8. `self.config` 相关速查

### 8.1 `self.config` 是什么

- 在 `ModuleBase` 中，`self.config` 是 `NikkeConfig` 实例（`module/config/config.py`）。
- 业务代码里几乎所有“可配置行为”都通过它读取，例如：
  - `self.config.Client_Platform`
  - `self.config.Optimization_OcrModelType`
  - `self.config.Daily_SendGift`

### 8.2 配置字段命名规则

- 配置文件路径规则是 `组.字段`（例如 `Client.Platform`）。
- 在代码里访问时会转换成 `组_字段`（例如 `Client_Platform`）。
- 转换逻辑来自 `path_to_arg()`（`module/config/utils.py`）。
- 可访问字段全集在自动生成文件 `module/config/config_generated.py`（`GeneratedConfig`）里。

### 8.3 读配置的常见方式

1. 读取当前任务已绑定字段（最常用）：

```python
if self.config.Daily_SendGift:
    ...
```

2. 读取跨任务/原始结构字段（需要精确路径时）：

```python
from module.config.utils import deep_get

value = deep_get(self.config.data, keys="BlaAuth.BlaAuth.Cookie")
```

3. 跨任务快捷读取：

```python
enabled = self.config.cross_get(keys=["Reward", "Scheduler", "Enable"], default=False)
```

### 8.4 写配置的常见方式

1. 直接赋值（会进入 `NikkeConfig.__setattr__`）：

```python
self.config.SpecialArenaWatch_CurrentRank = 12
```

- 若字段属于已绑定参数，会自动写入 `self.config.modified[...]`，并在 `auto_update=True` 时触发 `update()` 落盘。

2. 批量赋值（推荐）：

```python
with self.config.multi_set():
    self.config.foo1 = 1
    self.config.foo2 = 2
```

- 避免每次赋值都触发一次 `update()`，性能和一致性更好。

3. 运行期强制覆盖（不走配置文件路径）：

```python
self.config.override(Emulator_Serial="127.0.0.1:5555")
```

- `override()` 会写入 `self.overridden`，后续 `load/bind` 后仍保持覆盖。
- 适合临时运行环境覆盖（例如串口、调试参数）。

4. 按路径改原始配置（用于动态任务字段）：

```python
self.config.modified["SomeTask.Scheduler.Enable"] = False
self.config.update()
```

### 8.5 调度相关（`self.config` 的高频能力）

1. 延后当前/指定任务：

```python
self.config.task_delay(server_update=True)
# 或
self.config.task_delay(minute=30)
```

2. 立即唤起某任务：

```python
self.config.task_call("Reward")
```

3. 结束当前任务：

```python
self.config.task_stop("finish current flow")
```

### 8.6 实战建议与坑位

1. 业务分支判断优先用 `self.config.组_字段`，不要硬编码常量。  
2. 一次流程要改多个配置值时，优先 `multi_set()`。  
3. `override()` 是强覆盖，适用于运行态参数，不建议滥用。  
4. 只有“当前绑定任务 + 通用组”会自动映射到属性；跨任务字段请用 `cross_get` / `deep_get`。  
5. 路径里若包含 `{config_name}`（如 CSV 路径），会在绑定时按当前账号名解析。
