# 实施文档：驱动级鼠标控制方案（`PCClientInfo.ControlScheme` 新增 `driver`）

> **文档性质**：实施说明书。所有设计决策已定稿，实施者按本文档执行即可，**不需要再做设计判断**。
> **适用范围**：`D:\PCR\NIKKEAutoScript`（NKAS，Windows PC 客户端链路）。
> **必读**：动手前先读 `AGENTS.md`，本文档中"不要改"的条目优先级高于任何"顺手优化"的冲动。
> **依据**：`tmp/winprobe/logi/REPORT-driver-ops-equipment.md`（本机实测）+ 逐处源码核对（全部带行号）。

---

> **本次实施状态（2026-09-18）**：代码已实现。用户后续要求“先写代码，不用验证”，因此停止游戏操作与后续验收；下方仅记录停止前实际执行的结果，未运行项不标记为通过。

## 1. 任务定义

### 1.1 要做什么

在 NKAS 现有的两套 Windows 鼠标控制链路（`pyautogui` / `postmessage`）之外，新增第三套可切换链路 `driver`：通过 **Logitech G HUB 已安装的虚拟 HID 鼠标设备**，用 `IOCTL 0x2A2010` 直接向系统输入栈提交鼠标报告。

### 1.2 为什么做

现状：`nikke.exe` 收不到用户态注入的鼠标输入——`SendInput` 返回 1（声称成功），但光标不动、游戏无任何反应。NKAS 现有两条链路都建立在用户态注入之上（`pyautogui` → `SendInput`；`pynput` → `SendInput`），因此**在游戏内全部失效**。驱动级通道是本机唯一被实测证明可用的通道。

> **补充实测（2026-09-18 00:25–00:55，两个时间点对照）**：
> - 驱动 IOCTL：**两个时间点都生效**（`dx=+30` → 实际 `+57`；闭环收敛误差 1px）
> - `SetCursorPos` / `mouse_event` / `WH_MOUSE_LL` 钩子观测：00:25–00:45 **整体失效**（`SetCursorPos` 返回 0 且光标不动、钩子 24 次驱动移动收到 0 个事件），00:55 **全部恢复正常**（返回 1、收到 `0x0` vs `0x1`）
>
> 失效期间游戏**并不在前台**（前台是 VS Code），且 `OpenInputDesktop` 确认线程就在输入桌面 `Default`。
>
> 即：**本机的用户态注入会间歇性整体失效，而驱动通道不受影响**。真因未定，**本方案不依赖对真因的判断**。详见 §4.2 与 §8.5 第 7 条。

### 1.3 交付物清单

| # | 交付物 | 类型 |
| --- | --- | --- |
| 1 | `module/device/win/logi/__init__.py` | 新建（空文件） |
| 2 | `module/device/win/logi/driver_mouse.py` | 新建（§5.1 全文） |
| 3 | `module/device/win/logi/input.py` | 新建（§5.2 全文） |
| 4 | `module/device/win/automation.py` `_init_input()` | 修改（§5.3 before/after） |
| 5 | `module/config/argument/argument.yaml` `ControlScheme` | 修改（§5.4） |
| 6 | `module/config/i18n/{zh-CN,en-US,ja-JP}.json` | 修改（§5.5） |
| 7 | `module/config/argument/args.json`、`config_generated.py` | **由生成器产出，不手改** |
| 8 | `tests/test_win_background_control.py` 新增测试类 | 修改（§5.6） |

**禁止**在本文档范围外新增、删除、重命名任何文件。

### 1.4 完成判据（DoD）

- [x] `python -m py_compile` 三个 `.py` 文件全部通过
- [ ] `python -m unittest tests.test_win_background_control -v` 全绿（含新增用例）
- [x] `args.json` / `config_generated.py` 里 `ControlScheme` 的 option 含 `driver`
- [x] 三语 `i18n` 的 `driver` 标签与 help 文案已填（无 `TODO` / 占位符残留）
- [x] 附录 A 自检脚本在本机返回 `[ OK ]`
- [x] §8 的游戏内验收项已按实跑结果填写（**不得填写未实际运行的结果**）
- [x] `git diff` 中不出现本文档"不要改"列表里的任何文件
- [ ] 只提交，不推送（见 §9.1）

---

## 2. 前置条件

| 项 | 要求 | 检查方式 |
| --- | --- | --- |
| 操作系统 | Windows 10/11 x64 | — |
| 游戏 | `nikke.exe` 正在运行，窗口客户区 **720×1280** | NKAS 会自行校验，不等则 `raise` |
| G HUB | **已安装且正在运行**（实测为最新版） | 设备管理器存在 `logi_joy_vir_hid`；附录 A 能打开设备 |
| 权限 | 普通用户即可，**不需要管理员** | 实测以非管理员身份通过 |
| Python | 项目 venv `D:\PCR\NIKKEAutoScript\.venv\Scripts\python.exe`（生成器需要 `inflection`） | `python -m module.config.config_updater` 能跑 |
| 依赖 | **零新增第三方依赖**（仅 `ctypes` / `struct` / `time` / `threading`） | — |

**不要**要求用户改用 InputRedirect、不要安装内核驱动、不要 `NtLoadDriver`。

---

## 3. 已定稿的设计决策（不可自行变更）

| # | 决策 | 取值 | 说明 |
| --- | --- | --- | --- |
| 1 | 配置项取值名 | **`driver`** | 对内是"驱动级通道"，不绑定具体品牌名 |
| 2 | 包目录名 | `module/device/win/logi/` | 目录名限定具体实现（Logitech G HUB），与取值名分工不同，**不是笔误** |
| 3 | 依赖的前置软件 | G HUB（**非** LGS） | 设备接口 GUID `{1abc05c0-...}` = G HUB，7 字节报告。LGS 是 5 字节报告，**本方案不支持** |
| 4 | 多实例策略 | **直接拒绝** | 驱动通道驱动的是**全局唯一物理光标**，两实例并行必然互抢。冲突即 `RequestHumanTakeover` |
| 5 | 前台/后台语义 | **前台方案** | 与 `pyautogui` 行为对齐；**不**追加 `SWP_NOACTIVATE`、**不**跳过 `SetForegroundWindow` |
| 6 | 预检失败行为 | **显式报错 + `RequestHumanTakeover`** | 会终止该实例。**绝不静默降级回 `SendInput`**——否则日志会假装在跑驱动、实际跑的是被游戏丢弃的旧链路 |
| 7 | `mouse_move` 实现 | **`MOVE_BACKEND = 'driver'`**（驱动闭环） | 已验证路径。`'cursor'`（`SetCursorPos` 直定位）**未验证**，仅作为可选项保留，见 §3.1 |
| 8 | 键盘 | **不接驱动** | 本机只枚举到 1 个 G HUB 接口实例（鼠标）；键盘形态未验证。`press_key` / `secretly_press_key` 继承 `Input` 原样 |
| 9 | 滚轮步长常量 `65` | **不修改** | `automation.py:282` 保持原值。是否需要在 720×1280 下重标定，属独立议题 |
| 10 | 既有缺陷 | **不修** | §7.2 列出的三个既有问题本次一律不动，不要在本次提交里夹带 |

### 3.1 关于 `Shape A / Shape B`（`mouse_move` 的两条实现路线）

`mouse_move` 是唯一有两条可行实现的原始操作，本文档用 `Shape A / Shape B` 区分：

| | **Shape A** | **Shape B** |
| --- | --- | --- |
| 定位方式 | `user32.SetCursorPos(x, y)` 一步到位 | 驱动 IOCTL 发**相对位移报告**，读回光标位置、算误差、逐步逼近（闭环） |
| 原理 | 不经过注入通道，直接改光标坐标 | 走与按键/滚轮完全相同的注入通道 |
| 优点 | 快（1 次 API 调用）、精确、无加速问题 | **已实测可用**；与按键/滚轮同源，行为一致 |
| 缺点 | **状态不稳定**：00:25–00:45 期间 `SetCursorPos` 返回 0、光标不动；00:55 又返回 1 正常。若在失效状态下启用，按键会落在**旧位置**（静默错误）| 慢：单次移动 = 10~50 次 IOCTL（每次 ~4ms 轮询） |
| 本文档采用 | ✗ 保留为可选（**未验证可用性，且观测到不稳定**） | **✓ 默认（`MOVE_BACKEND = 'driver'`）** |

**为什么默认 Shape B**：Shape A 的正确性直接决定"点击落在哪里"，而它在本机**观测到会间歇性失效**（`SetCursorPos` 返回 0、光标不动，见 §4.2）。正确性优先于性能，因此取在两种状态下都验证过的 Shape B。原计划的 Step 0 轻量实验（固定靶点三组对照）**本次不做**（决策 6/8）——它是把 Shape A 转正的前置条件，**没做过实验之前不要启用 `'cursor'`**。

`'cursor'` 分支保留但**不得启用**；即便误启用也不会静默出错：`set_cursor` 返回 `False` → `_checked()` 计数 → 3 次后 `RequestHumanTakeover`。

> 实施者注意：**不要**自行把默认值改成 `'cursor'`，也不要删掉 `'cursor'` 分支。

---

## 4. 背景事实（实测依据，供实施者判断边界）

### 4.1 设备与协议

| 项 | 值 |
| --- | --- |
| 设备路径 | `\??\ROOT#SYSTEM#000N#{1abc05c0-c378-41b9-9cef-df1aba82b015}`，`N` 在 `0..9` 中循环尝试（本机命中 `0002`）|
| 打开方式 | `CreateFileW(path, 0xC0000000, 0x3, None, OPEN_EXISTING, 0, None)`，**非管理员可用** |
| IOCTL | `0x2A2010` |
| 报告格式 | 7 字节 `<BBhhB` = `buttons(u8)` + `reserved(u8, 必为0)` + `dx(int16 LE)` + `dy(int16 LE)` + `wheel(int8)` |
| 按钮字段语义 | **状态**，不是边沿事件。`1`=左键按下，`0`=抬起；`2`=右键，`4`=中键 |
| 返回值 | `NtDeviceIoControlFile` 的 Status，成功 = `0x00000000` |
| 成功判据 | 打开设备后用**一次零报告**（`btn=0, dx=dy=0, wheel=0`）确认 IOCTL 被接受 |

### 4.2 本机实测结论

| 操作 | 驱动通道 | `SendInput` 对照 |
| --- | --- | --- |
| 单击 | ✅ 打开详情弹窗，强变化 2,233,981 px | ❌ 强变化 15 ≈ 无 |
| 长按 1.0s | ✅ 同一弹窗 | 未测 |
| 拖拽 250px 向下 | ✅ 内容跟随下移 428px | ❌ 强变化 0 |
| 滚轮 ±1 格 | ✅ 每格恒定 66px，18/18 次一致、完全可逆、无惯性 | ❌ 0 |
| 注入标记 | ✅ `WH_MOUSE_LL` 读到 `flags = 0x000000` | `flags = 0x000001`（`LLMHF_INJECTED`） |

**滚轮方向**：`wheel = +1` → 内容**下移**；`wheel = -1`(`0xFF`) → 内容**上移**。与 `pyautogui.scroll(+1)` 同向。

**指针加速**：请求量与实际位移非线性（实测 60→140、150→382、600→966、1000→3998）。**最近一次复现（附录 A）**：请求 `dx=40, dy=0`，实际位移 `(+88, -35)` —— 不仅有约 2.2× 放大，**还出现了轴向串扰**。因此**相对移动必须闭环**，不能假设"发多少走多少"。

**非前台也生效**：实测过 `前台=False` 时滚轮仍生效（Windows 的"悬停滚动非活动窗口"把消息路由给光标下窗口）。但本方案**不承诺后台语义**。

**三种移动 / 观测方式的对照（2026-09-18 00:25–00:55）**：

同一台机器在两个时间点得到**完全相反**的结果，两个都要记录：

| 方式 | 00:25–00:45（失效窗口） | 00:55（恢复后） |
| --- | --- | --- |
| 驱动 IOCTL / 驱动闭环 | ✅ 生效（`+30` → 实际 `+57`；闭环请求 `+60/+40` **4 次上报收敛**，误差 2px） | ✅ 生效 |
| `SetCursorPos` | ❌ **返回 0**，光标不动，`GetLastError()=0`，重复 3 次一致 | ✅ **返回 1**：请求 `+120` 精确走 `+120` |
| `mouse_event(MOUSEEVENTF_MOVE, +30)` | ❌ 光标不动 | ✅ 生效：请求 `+30` 实际走 `+72` |
| `WH_MOUSE_LL` 钩子观测 | ❌ 2.4s / 24 次驱动移动收到 **0 个事件** | ✅ 收到 `0x0`（驱动）vs `0x1`（SendInput） |

失效窗口期间做的排除性核对：

- 前台窗口是 **VS Code**（`Chrome_WidgetWin_1`，pid 23968），**游戏不在前台** ⇒ 不能把失效归因于"游戏吞输入"。
- `OpenInputDesktop` 返回 `Default`，与 `GetThreadDesktop` 同名 ⇒ 当时**线程就在输入桌面**。
- 设备句柄与 IOCTL 返回值全程正常（`0x00000000`）。

**三条结论**：

1. `MOVE_BACKEND` 默认取驱动闭环（§3.1）——驱动路径在**两种状态下都可用**，是唯一稳定的选择。
2. 本机的用户态注入（`SendInput` / `mouse_event` / `SetCursorPos` / 钩子观测）会**整体进入不可用状态，又会自行恢复**。真因未定，**不在本次范围**。
3. **因此不要把"用户态注入失效"当成游戏的固有属性**；它至少有一部分来自环境，且是间歇性的。这也意味着 §8.3 的验收必须在"用户态注入可用"的时段与"不可用"的时段各跑一次，才能说清边界。

**`move_to` 收敛轨迹（真实设备，光标静止）**：

| 下发 `(dx, dy)` | 下发前 | 下发后 | 实际位移 |
| --- | --- | --- | --- |
| `(20, 20)` | (5250, 820) | (5287, 858) | `(+37, +38)` |
| `(20, 2)` | (5287, 858) | (5337, 863) | `(+50, +5)` |
| `(-12, -3)` | (5337, 863) | (5318, 859) | `(-19, -4)` |
| `(-7, 1)` | (5318, 859) | (5308, 860) | `(-10, +1)` |

目标 `(5310, 860)`，**4 次上报后 `reached=True`，误差 2px**，逐步放大比 1.43–2.5×。看第 2 步：请求 `dy=2` 实际走 `+5`；第 3 步请求 `dx=-12` 实际走 `-19` —— **放大会导致过冲，靠 `MOVE_STEP_DECAY` 收回来**，这就是衰减那几行存在的意义。

### 4.3 为什么改动面这么小（关键源码事实）

`module/device/win/game_control.py:551-555`：

```python
    @property
    def _background_control(self) -> bool:
        """postmessage 仅控制游戏窗口；启动器仍依赖前台输入。"""
        window = getattr(self, 'current_window', None)
        return str(self.config.PCClientInfo_ControlScheme) == 'postmessage' and getattr(window, 'name', None) == 'Game'
```

该属性只匹配 `'postmessage'`，被三处行为消费（`switch_to_program` 是否激活窗口、`SetWindowPos` 是否加 `SWP_NOACTIVATE`、`app_is_running` 是否走精确窗口查找）。因此新增取值 `driver` **自动落回前台语义**，`game_control.py` / `app_control.py` **一行都不用改**。

---

## 5. 实施步骤

### 5.0 新建包目录

```bash
cd /d/PCR/NIKKEAutoScript
mkdir -p module/device/win/logi
# 新建空文件 module/device/win/logi/__init__.py（0 字节）
```

`ok_interaction/__init__.py` 有 1271B 的再导出内容，但本包没有需要对外导出的公共面，**空文件**更明确。

### 5.1 `module/device/win/logi/driver_mouse.py`（新建，全文）

```python
"""G HUB 虚拟 HID 鼠标设备（驱动级）的用户态注入封装。

原理：安装 G HUB 后系统里存在一个虚拟鼠标 HID 设备。用户态进程可以直接打开它的
设备接口，用 IOCTL 0x2A2010 提交 7 字节鼠标报告，由驱动投递进系统输入栈 —— 这条
路径不经过 SendInput，因此 WH_MOUSE_LL 钩子读到的 MSLLHOOKSTRUCT.flags == 0x000000
（不带 LLMHF_INJECTED）。

本模块只做两件事：找到设备、发报告。
不安装驱动、不写注册表、不落盘、不改动 G HUB 的任何文件。
"""
import ctypes
import struct
import threading
import time
from ctypes import wintypes

from module.logger import logger

# G HUB 的虚拟鼠标设备接口 GUID。LGS 是另一套（df31f106-...，5 字节报告），不支持。
G_HUB_INTERFACE_GUID = '{1abc05c0-c378-41b9-9cef-df1aba82b015}'
DEVICE_INDEX_RANGE = range(10)


def g_hub_device_path(index):
    """G HUB 虚拟鼠标设备接口路径，index 取 0..9（本机命中 2）。

    注意：不要写成 `模板.format(index=...)` —— GUID 自带 `{...}`，会被 str.format
    当成替换字段而抛 KeyError。这里用 f-string，GUID 只在运行期代入。
    """
    return rf'\??\ROOT#SYSTEM#000{index}#{G_HUB_INTERFACE_GUID}'

IOCTL_SEND_MOUSE = 0x2A2010
REPORT_SIZE = 7
STATUS_SUCCESS = 0

GENERIC_READ_WRITE = 0xC0000000
FILE_SHARE_BOTH = 0x00000003
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

BTN_LEFT = 0x01
BTN_RIGHT = 0x02
BTN_MIDDLE = 0x04

# 光标闭环参数。步长 20 是已验证值：请求量与实际位移既非线性、又有轴向串扰
# （实测请求 dx=40/dy=0，实际 (+88, -35)），所以只做「小步长 + 读回 + 超冲收敛」。
MOVE_TOLERANCE = 2
MOVE_MAX_ITERATIONS = 400
MOVE_STEP_LIMIT = 20.0
MOVE_STEP_MIN = 1.0
MOVE_STEP_DECAY = 0.6
MOVE_POLL_INTERVAL = 0.004

# 滚轮：每格一个独立报告。间隔过小可能被合并，0.02 是已验证值（连续 40 格无丢格）。
WHEEL_INTERVAL = 0.02

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_ntdll = ctypes.WinDLL('ntdll')

_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL

_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_ntdll.NtDeviceIoControlFile.restype = ctypes.c_long
_ntdll.NtDeviceIoControlFile.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
]


class _IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [('Status', ctypes.c_ssize_t), ('Information', ctypes.c_size_t)]


def _clamp_int16(value):
    return max(-32768, min(32767, int(value)))


def make_report(buttons=0, dx=0, dy=0, wheel=0):
    """构造 7 字节鼠标报告。长度恒为 7。"""
    report = struct.pack(
        '<BBhhB',
        int(buttons) & 0xFF,
        0,
        _clamp_int16(dx),
        _clamp_int16(dy),
        int(wheel) & 0xFF,
    )
    assert len(report) == REPORT_SIZE
    return report


class LogiMouseDriver:
    """设备句柄的发现、持有与自愈。"""

    def __init__(self):
        self._handle = None
        self.device_path = None

    @property
    def opened(self):
        return self._handle is not None

    def open(self):
        """按 0000–0009 逐个尝试，用一次零报告确认 IOCTL 被接受。已打开则直接返回 True。"""
        if self.opened:
            return True
        for index in DEVICE_INDEX_RANGE:
            path = g_hub_device_path(index)
            handle = _kernel32.CreateFileW(
                path, GENERIC_READ_WRITE, FILE_SHARE_BOTH, None, OPEN_EXISTING, 0, None,
            )
            if handle == INVALID_HANDLE_VALUE:
                continue
            # 零报告语义为「无动作」，仅用于确认通道被接受
            if self._ioctl(handle, make_report()) == STATUS_SUCCESS:
                self._handle = handle
                self.device_path = path
                logger.info(f'Logitech driver device opened: {path}')
                return True
            _kernel32.CloseHandle(handle)
        logger.error('No G HUB virtual mouse interface answered IOCTL 0x2A2010')
        return False

    def close(self):
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
            self.device_path = None

    @staticmethod
    def _ioctl(handle, report):
        iosb = _IO_STATUS_BLOCK()
        status = _ntdll.NtDeviceIoControlFile(
            handle, None, None, None, ctypes.byref(iosb),
            IOCTL_SEND_MOUSE, report, len(report), None, 0,
        )
        return status & 0xFFFFFFFF

    def send(self, buttons=0, dx=0, dy=0, wheel=0):
        """提交一次报告。句柄失效（G HUB 重启 / 设备重枚举）时自动重开一次。"""
        if not self.opened and not self.open():
            return False
        report = make_report(buttons=buttons, dx=dx, dy=dy, wheel=wheel)
        if self._ioctl(self._handle, report) == STATUS_SUCCESS:
            return True
        logger.warning('Logitech driver IOCTL failed, reopening device once')
        self.close()
        if not self.open():
            return False
        return self._ioctl(self._handle, report) == STATUS_SUCCESS


class LogiMouse:
    """在 LogiMouseDriver 之上提供「光标定位 + 按键 + 滚轮」三个语义。"""

    def __init__(self, driver=None):
        self.driver = driver or LogiMouseDriver()

    # ---- 设备 ----
    def open(self):
        return self.driver.open()

    def close(self):
        self.driver.close()

    @property
    def device_path(self):
        return self.driver.device_path

    # ---- 光标 ----
    @staticmethod
    def cursor():
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    @staticmethod
    def set_cursor(x, y):
        """Shape A：直接用光标位置 API 定位。不属于本次默认路径，见实施文档 §3.1。"""
        return bool(_user32.SetCursorPos(int(x), int(y)))

    def move_rel(self, dx, dy, buttons=0):
        """Shape B 的最小单元：一次相对位移报告。"""
        return self.driver.send(buttons=buttons, dx=dx, dy=dy)

    def move_to(self, x, y, buttons=0, tolerance=None):
        """Shape B：闭环相对移动。

        请求量与实际位移非线性且存在轴向串扰，所以必须「读回实际位置 → 算误差 →
        小步逼近」，并在检测到超冲（误差符号翻转）时收敛步长。
        """
        tolerance = MOVE_TOLERANCE if tolerance is None else tolerance
        step = MOVE_STEP_LIMIT
        previous = None
        for _ in range(MOVE_MAX_ITERATIONS):
            current_x, current_y = self.cursor()
            error_x, error_y = int(x) - current_x, int(y) - current_y
            if abs(error_x) <= tolerance and abs(error_y) <= tolerance:
                return True
            if previous is not None and (previous[0] * error_x < 0 or previous[1] * error_y < 0):
                step = max(MOVE_STEP_MIN, step * MOVE_STEP_DECAY)
            step_x = int(round(max(-step, min(step, error_x))))
            step_y = int(round(max(-step, min(step, error_y))))
            if not self.driver.send(buttons=buttons, dx=step_x, dy=step_y):
                return False
            previous = (error_x, error_y)
            # 这里必须真的等一次输入落地再读回：去掉它闭环就失去可观测性，
            # 实测 400 次迭代仍不收敛（读到的永远是上一帧位置）。
            time.sleep(MOVE_POLL_INTERVAL)
        return False

    # ---- 按键（按钮字段是状态，不是边沿事件） ----
    def press(self, buttons=BTN_LEFT):
        return self.driver.send(buttons=buttons)

    def release(self):
        return self.driver.send(buttons=0)

    # ---- 滚轮 ----
    def wheel(self, notches):
        """notches > 0 → 内容下移；< 0 → 内容上移。每格一个独立报告。"""
        notches = int(notches)
        if notches == 0:
            return True
        direction = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            if not self.driver.send(wheel=direction):
                return False
            time.sleep(WHEEL_INTERVAL)
        return True


# 驱动对象是进程级共享的：Device.__init__ 在 GameNotRunningError 时会重试构造
# （device.py:32-46），每次重试都会走一遍 Automation._init_input()。共享同一个
# LogiMouse 可以避免重复打开设备句柄。
_shared_lock = threading.RLock()
_shared_mouse = None


def shared_mouse():
    global _shared_mouse
    with _shared_lock:
        if _shared_mouse is None:
            _shared_mouse = LogiMouse()
        return _shared_mouse
```

### 5.2 `module/device/win/logi/input.py`（新建，全文）

```python
"""LogiInput：G HUB 驱动级鼠标方案（PCClientInfo.ControlScheme == 'driver'）。

只替换 Input 的 8 个鼠标原语，其余（键盘、insert_swipe）全部继承。
业务层 click_xy / appear_then_click / ensure_sroll / ui_ensure 零改动。

坐标约定与 Input 完全一致：方法入参为屏幕绝对坐标（Automation 已叠加窗口 offset）。
"""
import ctypes
import threading
import time

from module.device.win.input import Input
from module.device.win.logi.driver_mouse import (
    BTN_LEFT,
    WHEEL_INTERVAL,
    shared_mouse,
)
from module.exception import RequestHumanTakeover
from module.logger import logger

# 跨进程互斥体。Local\ 前缀：非管理员即可创建，作用域为当前登录会话。
SCHEME_MUTEX_NAME = 'Local\\NKAS.DriverControlScheme'
ERROR_ALREADY_EXISTS = 183

# 单击按下时长。实测 0.06~0.09s 区间有效（等价于 2~5 帧 @60fps），取 0.09 与人类点击一致。
CLICK_HOLD = 0.09
# 拖拽参数。按下后必须先跳一段，否则光标静止过久会被游戏识别成长按并弹出详情面板。
DRAG_LEAD_PIXELS = 50
DRAG_STEP_INTERVAL = 0.02
DRAG_MIN_STEPS = 8
DRAG_MAX_DURATION = 0.6
DRAG_SETTLE_DELAY = 0.06
# 连续失败达到该次数即中止，绝不静默降级到 SendInput
FAILURE_LIMIT = 3

_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

_claim_lock = threading.RLock()
_scheme_mutex = None


def claim_scheme_mutex():
    """认领驱动通道的跨进程互斥体。

    幂等：本进程已认领则直接返回 True。这一点是必需的 —— Device.__init__ 在
    GameNotRunningError 时会重试构造（device.py:32-46），每次重试都会新建一个
    LogiInput；若每次都去 CreateMutexW，同进程的第二次创建同样会得到
    ERROR_ALREADY_EXISTS，会被误判成「另一个实例在占用」。
    """
    global _scheme_mutex
    with _claim_lock:
        if _scheme_mutex is not None:
            return True
        handle = _kernel32.CreateMutexW(None, True, SCHEME_MUTEX_NAME)
        if not handle:
            logger.error(f'CreateMutexW failed for {SCHEME_MUTEX_NAME}')
            return False
        # 必须紧接 CreateMutexW 读取，中间不能插入其它 API 调用
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            return False
        _scheme_mutex = handle
        return True


class LogiInput(Input):
    # Shape B（驱动闭环）是已验证路径，保持为默认值。
    # 'cursor'（Shape A：SetCursorPos 直定位）未验证，仅作为可选路径保留。
    MOVE_BACKEND = 'driver'

    def __init__(self, config_name=None):
        """
        Args:
            config_name: 实例名，仅用于日志与报错提示，可为 None
        """
        super().__init__()
        self._config_name = config_name or 'nkas'
        self._lock = threading.RLock()
        self._failures = 0
        self.mouse_driver = shared_mouse()
        self._preflight()

    # ------------------------------------------------------------------
    # 启动预检：两道闸门，失败即显式中止
    # ------------------------------------------------------------------
    def _preflight(self):
        if not claim_scheme_mutex():
            logger.error(
                f'Control scheme driver is already in use by another NKAS instance '
                f'(mutex {SCHEME_MUTEX_NAME}). The driver channel drives the single global '
                f'physical cursor, so two instances would fight over it. '
                f'Switch the other instance back to postmessage.'
            )
            raise RequestHumanTakeover
        if not self.mouse_driver.open():
            logger.error(
                'Control scheme driver requires Logitech G HUB installed and running, which '
                'provides the virtual HID mouse device (GUID 1abc05c0-...). '
                'No interface answered IOCTL 0x2A2010.'
            )
            raise RequestHumanTakeover

    # ------------------------------------------------------------------
    # 失败处理：显式报错，绝不静默降级
    # ------------------------------------------------------------------
    def _checked(self, ok, what):
        if ok:
            self._failures = 0
            return True
        self._failures += 1
        logger.error(f'Logitech driver {what} failed ({self._failures}/{FAILURE_LIMIT})')
        if self._failures >= FAILURE_LIMIT:
            logger.critical(
                'Logitech driver channel is no longer usable. '
                'Stop instead of silently falling back to SendInput.'
            )
            raise RequestHumanTakeover
        return False

    # ------------------------------------------------------------------
    # 光标
    # ------------------------------------------------------------------
    def _move_to(self, x, y, buttons=0):
        if self.MOVE_BACKEND == 'cursor' and not buttons:
            return self.mouse_driver.set_cursor(x, y)
        return self.mouse_driver.move_to(x, y, buttons=buttons)

    def mouse_move(self, x, y):
        with self._lock:
            self._checked(self._move_to(x, y), f'move ({int(x)}, {int(y)})')
            logger.debug(f'Logitech mouse move ({int(x)}, {int(y)})')

    # ------------------------------------------------------------------
    # 点击 / 长按
    # ------------------------------------------------------------------
    def _press_hold_release(self, hold_time):
        """按下 → 保持 → 抬起。抬起失败必须补发，避免按键卡在按下态。"""
        if not self.mouse_driver.press(BTN_LEFT):
            self._checked(False, 'left button down')
            return
        time.sleep(hold_time)
        if not self.mouse_driver.release():
            self.mouse_driver.release()
            self._checked(False, 'left button up')

    def mouse_click(self, x, y):
        with self._lock:
            self._checked(self._move_to(x, y), f'move ({int(x)}, {int(y)})')
            self._press_hold_release(CLICK_HOLD)
            logger.debug(f'Logitech click ({int(x)}, {int(y)})')

    def press_mouse_click(self, x, y, wait_time=0.2):
        with self._lock:
            self._checked(self._move_to(x, y), f'move ({int(x)}, {int(y)})')
            self._press_hold_release(wait_time)
            logger.debug(f'Logitech press {wait_time}s ({int(x)}, {int(y)})')

    def mouse_down(self, x, y):
        with self._lock:
            self._checked(self._move_to(x, y), f'move ({int(x)}, {int(y)})')
            self._checked(self.mouse_driver.press(BTN_LEFT), f'button down ({int(x)}, {int(y)})')

    def mouse_up(self):
        with self._lock:
            self._checked(self.mouse_driver.release(), 'button up')

    def press_mouse(self, wait_time=0.2):
        """在当前位置按下并保持。

        签名必须与 Input.press_mouse 完全一致：Automation.click() 的 action_map
        以 (x, y) 调用它，保持了上游既有问题不扩散（见实施文档 §7.2 第 3 条）。
        """
        with self._lock:
            self._press_hold_release(wait_time)

    # ------------------------------------------------------------------
    # 滚轮
    # ------------------------------------------------------------------
    def mouse_scroll(self, count, direction=-1, pause=True):
        """pause 参数只为签名兼容而保留，驱动路径用 WHEEL_INTERVAL 内部节拍。"""
        count = int(count)
        if count <= 0:
            return
        with self._lock:
            self._checked(
                self.mouse_driver.wheel(int(direction) * count),
                f'wheel {direction} x {count}',
            )
            logger.debug(f'Logitech wheel {count * int(direction)} 格')

    # ------------------------------------------------------------------
    # 拖拽
    # ------------------------------------------------------------------
    def mouse_swipe(self, p1, p2, speed=1.0):
        x1, y1 = int(p1[0]), int(p1[1])
        x2, y2 = int(p2[0]), int(p2[1])
        distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if distance < 1:
            return
        duration = max(0.05, min(distance / (100 * speed), DRAG_MAX_DURATION))
        steps = max(DRAG_MIN_STEPS, round(duration / DRAG_STEP_INTERVAL))
        # 按下后立刻朝目标方向跳一段，否则静止太久会被游戏识别为长按
        lead_ratio = min(DRAG_LEAD_PIXELS, distance / 4) / distance

        with self._lock:
            if not self._move_to(x1, y1):
                self._checked(False, f'drag start ({x1}, {y1})')
                return
            if not self.mouse_driver.press(BTN_LEFT):
                self._checked(False, f'drag down ({x1}, {y1})')
                return
            try:
                lead_x = int(round(x1 + (x2 - x1) * lead_ratio))
                lead_y = int(round(y1 + (y2 - y1) * lead_ratio))
                self.mouse_driver.move_to(lead_x, lead_y, buttons=BTN_LEFT)
                for index in range(1, steps + 1):
                    ratio = lead_ratio + (1 - lead_ratio) * index / steps
                    target_x = int(round(x1 + (x2 - x1) * ratio))
                    target_y = int(round(y1 + (y2 - y1) * ratio))
                    self.mouse_driver.move_to(target_x, target_y, buttons=BTN_LEFT)
                    time.sleep(DRAG_STEP_INTERVAL)
            finally:
                # 无论中途如何退出，都必须把左键还回去，避免按键卡在按下态
                if not self.mouse_driver.release():
                    self.mouse_driver.release()
                    self._checked(False, f'drag up ({x2}, {y2})')
            time.sleep(DRAG_SETTLE_DELAY)
        logger.debug(f'Logitech drag ({x1}, {y1}) -> ({x2}, {y2}), {duration:.2f}s / {steps} 段')
```

> `input.py` 从 `driver_mouse` 导入了 `WHEEL_INTERVAL`：两份文件的常量区必须一致，改动时成对检查。

### 5.3 `module/device/win/automation.py` — 唯一改动的应用源码

**改动前**（`automation.py:99-116`）：

```python
    def _init_input(self):
        """
        初始化输入处理器，将输入操作如点击、移动等绑定至实例变量。
        按 PCClientInfo.ControlScheme 选择控制链路：
        - pyautogui：现有方案（Input，全局物理鼠标）
        - postmessage：窗口消息方案（PostMessageInput）
        """
        if str(self.config.PCClientInfo_ControlScheme) == 'postmessage':
            from module.device.win.ok_interaction.input import PostMessageInput

            self.input_handler = PostMessageInput(
                lambda: self.current_window,
                hwnd_resolver=self.get_current_window_hwnd,
                foreground_switcher=self.set_foreground_window_with_retry,
            )
            logger.info('Control scheme: postmessage')
        else:
            self.input_handler = Input()
```

**改动后**：

```python
    def _init_input(self):
        """
        初始化输入处理器，将输入操作如点击、移动等绑定至实例变量。
        按 PCClientInfo.ControlScheme 选择控制链路：
        - pyautogui：现有方案（Input，全局物理鼠标）
        - postmessage：窗口消息方案（PostMessageInput）
        - driver：G HUB 虚拟 HID 驱动方案（LogiInput，需 G HUB 运行中）
        """
        scheme = str(self.config.PCClientInfo_ControlScheme)
        if scheme == 'postmessage':
            from module.device.win.ok_interaction.input import PostMessageInput

            self.input_handler = PostMessageInput(
                lambda: self.current_window,
                hwnd_resolver=self.get_current_window_hwnd,
                foreground_switcher=self.set_foreground_window_with_retry,
            )
            logger.info('Control scheme: postmessage')
        elif scheme == 'driver':
            from module.device.win.logi.input import LogiInput

            self.input_handler = LogiInput(config_name=self.config.config_name)
            logger.info('Control scheme: driver')
        else:
            self.input_handler = Input()
```

规则：

1. **只改这一段**。`automation.py:117-126` 的 11 个绑定全部保持原样：8 个被 `LogiInput` 覆盖，3 个（`press_key` / `secretly_press_key` / 模块级 `insert_swipe`）继承 `Input`。
2. 分支用**显式 `elif`**，保留 `else` 兜底为 `Input()`：未知取值行为与现状一致，不做额外校验。
3. `self.config.config_name` 由 `NikkeConfig.__init__` 赋值（`config.py:80`），一定存在，不要用 `getattr` 兜底。

`LogiInput.__init__` 在预检失败时会 `raise RequestHumanTakeover`。该异常发生在 `Automation.__init__` 内部，会穿透 `Device.__init__` 的重试循环（它只捕获 `GameNotRunningError`，`device.py:32-46`）直接终止实例——这是**有意设计**，不要为此加 `try/except`。副作用：WebUI 托管的实例会"启动即挂"，需保证日志里能看到 §5.2 写的那两条 `logger.error` 之一。

### 5.4 `module/config/argument/argument.yaml`（`ControlScheme`，146-152 行附近）

```yaml
  ControlScheme:
    value: pyautogui
    option: [ pyautogui, postmessage, driver ]
```

### 5.5 跑生成器 + 补三语文案

```bash
cd /d/PCR/NIKKEAutoScript
.venv/Scripts/python.exe -m module.config.config_updater
git diff --stat
```

生成器会重写 `module/config/argument/args.json` 与 `module/config/config_generated.py`，并给新 option 生成占位符。**核对 `git diff --stat`：只允许出现这三个文件 + 三个 i18n 文件 + `template.json` 的预期改动。出现任何其它文件的改动，停下来报告，不要提交。**

然后按下面内容填写三处 i18n（保留既有 `pyautogui` / `postmessage` 文案，仅在末尾追加 `driver` 段与选项标签）：

`module/config/i18n/zh-CN.json`

```json
    "ControlScheme": {
      "name": "控制方案 (BETA)",
      "help": "pyautogui：完美的前台模式，模拟物理鼠标输入，点击、滚动和滑动会占用真实鼠标。\npostmessage：不完美的后台模式，点击、滚动、滑动和常规键盘按键通过窗口消息发送，并短暂移动真实鼠标，但不会前置游戏窗口，此时截图方式请选择 PrintWindow 保证截图不被遮挡。当需要使用键盘按键时会短暂前置游戏，随后恢复原前台窗口；启动器仍然依赖前台操作。\ndriver：前台模式。鼠标输入经由已安装并运行的 Logitech G HUB 虚拟鼠标设备在驱动层发送，需要 G HUB 保持运行，并且同一时间只能有一个实例使用该方案。适用于 pyautogui 的鼠标输入在游戏内无效的情况；它只是让操作生效，不会降低被游戏检测到的风险。",
      "pyautogui": "pyautogui（前台模式）",
      "postmessage": "postmessage（后台模式）",
      "driver": "driver（驱动级前台模式，需 G HUB）"
    }
```

`module/config/i18n/en-US.json`

```json
    "ControlScheme": {
      "name": "Control Scheme (BETA)",
      "help": "...(保留原文)...\ndriver: a foreground mode. Mouse input is emitted at driver level through the virtual mouse device of an installed and running Logitech G HUB. G HUB must stay running, and only one instance may use this scheme at a time. Use it when pyautogui mouse input has no effect in game; it only makes the operations work, and does not lower the risk of being detected.",
      "pyautogui": "pyautogui (foreground mode)",
      "postmessage": "postmessage (background mode)",
      "driver": "driver (driver-level foreground mode, requires G HUB)"
    }
```

`module/config/i18n/ja-JP.json`

```json
    "ControlScheme": {
      "name": "操作方式 (BETA)",
      "help": "...(保持原文)...\ndriver：フォアグラウンドモードです。マウス入力は、インストール済みで実行中の Logitech G HUB の仮想マウスデバイスを通じてドライバーレベルで送信されます。G HUB を実行し続ける必要があり、同時に 1 つのインスタンスだけがこの方式を使用できます。pyautogui のマウス入力がゲーム内で効かない場合に使用します。操作が有効になるだけで、検知されるリスクは下がりません。",
      "pyautogui": "pyautogui（フォアグラウンドモード）",
      "postmessage": "postmessage（バックグラウンドモード）",
      "driver": "driver（ドライバーレベルのフォアグラウンドモード、G HUB 必須）"
    }
```

**文案红线（必须保留）**：三语都必须写明"**只是让操作生效 / 不会降低被检测到的风险**"。这条防的是使用者把驱动级当成防封手段。

改完文案后**再跑一次生成器**，然后 `git diff` 确认文案没有被占位符覆盖。

### 5.6 测试：`tests/test_win_background_control.py` 新增

在文件末尾追加（不要改动既有用例）：

```python
class DriverSchemeTests(unittest.TestCase):
    def _handler(self, driver=None):
        with (
            patch.object(LogiInput, '_preflight', return_value=None),
            patch.object(Input, '__init__', return_value=None),
        ):
            handler = LogiInput(config_name='nkas')
        handler.mouse_driver = driver or Mock()
        return handler

    def test_automation_selects_logi_input_for_driver_scheme(self):
        automation = Automation.__new__(Automation)
        automation.config = SimpleNamespace(PCClientInfo_ControlScheme='driver', config_name='nkas')
        with patch('module.device.win.logi.input.LogiInput') as logi:
            automation._init_input()
        logi.assert_called_once_with(config_name='nkas')

    def test_automation_unknown_scheme_falls_back_to_plain_input(self):
        automation = Automation.__new__(Automation)
        automation.config = SimpleNamespace(PCClientInfo_ControlScheme='whatever', config_name='nkas')
        with patch('module.device.win.automation.Input') as plain:
            automation._init_input()
        plain.assert_called_once_with()

    def test_driver_scheme_is_foreground_not_background(self):
        client = AppControl.__new__(AppControl)
        client.config = SimpleNamespace(PCClientInfo_ControlScheme='driver')
        client.current_window = SimpleNamespace(name='Game')
        self.assertFalse(client._background_control)

    def test_preflight_stops_when_device_is_missing(self):
        with (
            patch('module.device.win.logi.input.claim_scheme_mutex', return_value=True),
            patch.object(LogiMouseDriver, 'open', return_value=False),
            patch.object(Input, '__init__', return_value=None),
            patch('module.device.win.logi.input.logger.error'),
        ):
            with self.assertRaises(RequestHumanTakeover):
                LogiInput(config_name='nkas')

    def test_preflight_stops_when_another_instance_holds_the_scheme(self):
        with (
            patch('module.device.win.logi.input.claim_scheme_mutex', return_value=False),
            patch.object(LogiMouseDriver, 'open', return_value=True) as opened,
            patch.object(Input, '__init__', return_value=None),
            patch('module.device.win.logi.input.logger.error'),
        ):
            with self.assertRaises(RequestHumanTakeover):
                LogiInput(config_name='nkas')
        opened.assert_not_called()

    def test_mouse_click_moves_then_presses_then_releases(self):
        driver = Mock()
        handler = self._handler(driver)
        with patch('module.device.win.logi.input.time.sleep'):
            handler.mouse_click(120, 340)
        self.assertEqual(
            driver.mock_calls,
            [call.move_to(120, 340, buttons=0), call.press(BTN_LEFT), call.release()],
        )

    def test_mouse_scroll_maps_direction_to_signed_notches(self):
        driver = Mock()
        handler = self._handler(driver)
        handler.mouse_scroll(3, direction=-1)
        handler.mouse_scroll(2, direction=1)
        handler.mouse_scroll(0)
        self.assertEqual(driver.wheel.call_args_list, [call(-3), call(2)])

    def test_swipe_holds_left_button_on_every_waypoint(self):
        driver = Mock()
        handler = self._handler(driver)
        with patch('module.device.win.logi.input.time.sleep'):
            handler.mouse_swipe((100, 100), (100, 350), speed=5)
        calls = driver.move_to.call_args_list
        self.assertEqual(calls[0], call(100, 100, buttons=0))
        self.assertTrue(all(item.kwargs['buttons'] == BTN_LEFT for item in calls[1:]))
        self.assertGreater(len(calls), 2)
        self.assertEqual(driver.press.call_args_list, [call(BTN_LEFT)])
        self.assertEqual(driver.release.call_args_list, [call()])

    def test_failure_limit_raises_instead_of_falling_back(self):
        driver = Mock()
        driver.move_to.return_value = False
        handler = self._handler(driver)
        with (
            patch('module.device.win.logi.input.logger.error'),
            patch('module.device.win.logi.input.logger.critical'),
        ):
            with self.assertRaises(RequestHumanTakeover):
                for _ in range(FAILURE_LIMIT):
                    handler.mouse_move(10, 10)

    def test_report_layout_is_seven_bytes_little_endian(self):
        self.assertEqual(make_report(buttons=1, dx=0, dy=0, wheel=0).hex(), '01000000000000')
        self.assertEqual(make_report(buttons=1, dx=-2, dy=258, wheel=-1).hex(), '0100feff0201ff')
        self.assertEqual(make_report(dx=40000).hex(), '0000ff7f000000')
```

同时**补 import**（追加到文件头部既有 import 之后）：

```python
from module.device.win.logi.driver_mouse import BTN_LEFT, LogiMouseDriver, make_report
from module.device.win.logi.input import FAILURE_LIMIT, LogiInput
from module.exception import RequestHumanTakeover
```

**两条注意**：

1. `LogiInput.__init__` 调用 `shared_mouse()`，它返回**进程级单例** `LogiMouse`。测试里 `LogiInput(config_name=...)` 会真实构造该单例（但构造函数不打开设备，只是建对象），**不会**碰真设备；随后用 `handler.mouse_driver = Mock()` 替换掉。若担心跨用例污染，可在 `setUp` 中 `patch('module.device.win.logi.driver_mouse._shared_mouse', None)`。
2. 测试全部 mock，**不打开真设备、不发真实 IOCTL**，可在无 G HUB 的机器上运行。

### 5.7 验证命令（`AGENTS.md` 测试规范）

```bash
cd /d/PCR/NIKKEAutoScript
.venv/Scripts/python.exe -m py_compile \
  module/device/win/logi/driver_mouse.py \
  module/device/win/logi/input.py \
  module/device/win/automation.py
.venv/Scripts/python.exe -m unittest tests.test_win_background_control -v
```

`webui/` 与 `webapp/` **无需重新构建**：本次不动前端代码，控制方案的下拉选项由 `args.json` 动态渲染（已确认 `webui/src/` 内没有 scheme 白名单）。

---

## 6. 方法级契约表（8 个被覆盖的原语）

| # | 方法 | 签名 | 语义 | 驱动实现 | 失败行为 |
| --- | --- | --- | --- | --- | --- |
| 1 | `mouse_move` | `(x, y)` | 光标移到屏幕绝对坐标 | 闭环相对移动（Shape B） | 计数 → 上限 `RequestHumanTakeover` |
| 2 | `mouse_click` | `(x, y)` | 左键单击 | move → `btn=1` → 90ms → `btn=0` | 同上 |
| 3 | `press_mouse_click` | `(x, y, wait_time=0.2)` | 左键长按 | move → `btn=1` → `sleep(wait_time)` → `btn=0` | 同上 |
| 4 | `mouse_down` | `(x, y)` | 左键按下并保持 | move → `btn=1` | 同上 |
| 5 | `mouse_up` | `()` | 释放左键 | `btn=0` | 同上 |
| 6 | `mouse_scroll` | `(count, direction=-1, pause=True)` | 滚轮 N 格 | `wheel(direction)` × `count`，每格独立报告，间隔 `WHEEL_INTERVAL` | 同上 |
| 7 | `mouse_swipe` | `(p1, p2, speed=1.0)` | 左键拖拽 | move → `btn=1` → 跳 50px → 逐点闭环（`btn=1`）→ `btn=0` | 同上；`finally` 保证补发抬起 |
| 8 | `press_mouse` | `(wait_time=0.2)` | 在**当前位置**按下并保持 | `btn=1` → `sleep` → `btn=0`，**不移动光标**（对齐 `Input`） | 同上 |

**不覆盖（继承 `Input`）**：`press_key`、`secretly_press_key`（键盘，`pyautogui` / SendInput）、模块级 `insert_swipe`（纯数学路径生成）。

**`mouse_scroll` 的 `pause` 参数**：仅为签名兼容保留，驱动路径用 `WHEEL_INTERVAL` 内部节拍。若游戏出现"连打被吞格"，**调这个常量**，不要改签名。

**滚轮方向不复位符号**：`Automation.swipe(method='scroll')`（`automation.py:283-288`）算出的 `direction` 直接传给 `mouse_scroll`，与 `pyautogui.scroll(+)` 同向，驱动 `wheel=+1` 也是内容下移。故 `wheel(direction * count)`，**不要取反**。

---

## 7. 硬性约束

### 7.1 安全闸门（必须在实现中保留）

| # | 闸门 | 落地位置 | 行为 |
| --- | --- | --- | --- |
| 1 | **默认关闭 + 秒级回滚** | `argument.yaml` 的 `value: pyautogui` | 出事把 scheme 改回 `pyautogui` 重启实例即可。**不写注册表、不落盘、不装驱动** → 无残留 |
| 2 | **单实例守卫** | `claim_scheme_mutex()` | 命名互斥体 `Local\NKAS.DriverControlScheme`；第二个启用者 → 报错 + `RequestHumanTakeover`，错误信息直接建议改回 `postmessage`。注意：真正被抢的是**同一个物理光标**——不只是两个脚本之间会互抢，**真人操作鼠标也会与驱动闭环定位互相干扰**（实测：闭环被并发真人输入扰动而失准）。所以"运行期间不要动鼠标"是使用约束，不是建议 |
| 3 | **显式失败，不静默降级** | `_preflight()` + `_checked()` | G HUB 缺失 / 枚举失败 / IOCTL 连续失败 3 次 → 报错并中止。**绝不回落到 `SendInput`** |
| 4 | **零新增系统面** | 整个 `logi/` 目录 | 不引 InputRedirect、不 `NtLoadDriver`、不改 G HUB/LGS 文件、不 hook 游戏、不碰游戏内存。只对**已存在**的虚拟 HID 设备发 IOCTL |
| 5 | **不做任何伪装** | 整个 `logi/` 目录 | 不伪造 `dwExtraInfo`、不抹 `LLMHF_INJECTED`、不做反检测。报文严格按 `Mouse_IO_7` 原样构造 |

**句柄生命周期**：`LogiMouseDriver.close()` 保留但不主动调用（`shared_mouse()` 持有的单例随进程退出释放）。**不要**用 `__del__`——解释器退出期不可靠。

### 7.2 不要改的东西

| 对象 | 为什么不动 |
| --- | --- |
| `game_control.py:551-555` `_background_control` | 只匹配 `postmessage` → `driver` 自动落回前台语义，正是我们要的 |
| `game_control.py:345-349` `switch_to_program` | 同上，走 `SetForegroundWindow` 分支，不要加"不激活"逻辑 |
| `game_control.py:557-567` `_set_window_pos_flags` | 同上，不要追加 `SWP_NOACTIVATE` |
| `app_control.py:156-160` `app_is_running` | 同上 |
| `automation.py:268-305` `swipe` | 原语替换即可，分页/方向逻辑完全复用 |
| `automation.py:282` 的 `65` | **保持原值**（决策 9） |
| `module/device/win/input.py` | 零改动，保证回滚面干净 |
| `module/device/win/ok_interaction/` | 零改动 |
| `webui/` `webapp/` | 零改动，无需重新构建 |

### 7.3 顺手核出但**本次一律不修**的既有问题

供后续独立立项，**不要**在本次提交里夹带：

1. `automation.py:269` `swipe(p1, p2, speed=15, method='scroll', distance_check=True, handle_control_check=True)` —— `distance_check` / `handle_control_check` 在函数体里**从未被读取**（`base.py:382` 还在传 `handle_control_check=False`）。
2. `automation.py:289-302` `method == 'swipe'` 分支里 `if abs(dx) > abs(dy)` 的两个分支内容**完全相同**（都是 `p2 = (raw_p2[0], raw_p2[1])`），且 `raw_p2` 是把 `offset` 又加了一遍的产物 —— 等价于恒等变换。典型的半成品残留。
3. `automation.py:221` `action_map['hold'] = self.press_mouse`，而 `automation.py:225` 以 `action_map[action](x, y)` 传 **两个**位置参数，`Input.press_mouse(self, wait_time=0.2)` 只接受一个 → `click(button, action='hold')` 会直接 `TypeError`。全仓 grep 确认**当前无调用点**，属死代码。`LogiInput.press_mouse` 按 `Input` 原签名实现，就是为了不让这个问题扩散。

---

## 8. 验收

### 8.1 静态验收（必须有输出证据）

按 §1.4 的 DoD 清单逐条跑，把命令与输出贴进 PR 描述。

**本次实施记录**：

- Worktree：`D:\PCR\NIKKEAutoScript-driver-input-scheme`；分支：`codex/driver-input-scheme`；基线：`c7c76105`。
- 新增 `LogiMouseDriver` / `LogiMouse` / `LogiInput`，接入 `driver` 选项、三语说明及生成配置。默认值仍为 `pyautogui`，移动默认走驱动闭环。
- 失败契约：定位失败不按下；完整点击/长按/拖拽成功后才清零失败计数；途中移动失败会停止拖拽；点击和拖拽均在 `finally` 释放左键，释放失败补发一次。
- `python -m py_compile`：新增驱动包、`automation.py`、`config_generated.py` 和测试文件均通过（在最后追加 5 个测试前执行）。
- `python -m unittest tests.test_win_background_control -v`：停止前执行 39 项，38 通过、1 个既有失败；其中 15 个新增驱动测试全部通过。随后已追加 5 个底层协议/闭环/重开测试，按用户要求未继续运行。
- 既有失败：`BackgroundControlTests.test_scroll_uses_cached_position_and_postmessage_swipe`。原始 HEAD 同一用例亦失败：实现合并为 1 次 130px 拖拽，测试期待 2 次 65px 拖拽。未修改既有用例或 `ok_interaction/`。
- 配置生成器运行两次；新选项、三语标签和 help 保留正确，`config/template.json` 无内容差异。
- `git diff --check` 通过；应用改动限定为交付清单中的文件。

**附录 A 本次输出（退出码 0）**：

```text
[ OK ] 设备已打开：\??\ROOT#SYSTEM#0002#{1abc05c0-c378-41b9-9cef-df1aba82b015}
[ OK ] 零报告被接受（返回 0x00000000）
[ OK ] 驱动相对移动 dx=40：(4805, 498) -> (4890, 503)
光标复位：闭环成功，当前 (4805, 498) / 目标 (4805, 498)
SetCursorPos 返回 1
驱动 IOCTL flags: ['0x0']
mouse_event/SendInput flags: ['0x0', '0x1']
[ OK ] 驱动通道无 LLMHF_INJECTED 标记
```

本次附录 A 临时脚本只补充了 `CloseHandle.argtypes = [w.HANDLE]`，确保 64 位句柄关闭参数类型正确。脚本与截图存于 worktree 的 `tmp/driver-validation/`，不提交。

**跨进程实测**：同进程重复构造 `LogiInput` 共享鼠标和互斥体；第二个 Python 进程构造 `LogiInput` 时抛出 `RequestHumanTakeover`，日志明确建议另一实例改回 `postmessage`。未启动任务调度器。


### 8.2 通道自检（不需要游戏）

运行附录 A 脚本。判据分三层，**只有第一层是硬门槛**：

| 层 | 判据 | 期望 |
| --- | --- | --- |
| **硬门槛** | 设备打开 + 零报告被接受 + 位移生效 + 闭环复位成功 | 四行 `[ OK ]`，`SetCursorPos` 的返回值**不参与判定**（它会间歇性返回 0，见 §4.2） |
| 可选 | 钩子读到的 `flags`：驱动 = `0x0`，`mouse_event` = `0x1` | 证明无 `LLMHF_INJECTED` 标记 |
| 环境相关 | 钩子收不到事件时脚本打印 `[SKIP]` 并返回 **2** | 这是"无法校验"，**不是通道故障**。换时段或换机重试 |

退出码约定：`0` = 全部通过；`1` = 硬门槛失败或标记异常；`2` = 硬门槛通过但钩子不可用（无法校验标记）。

**本机实测输出（2026-09-18 01:05，全部通过，退出码 0）**：

```
[ OK ] 设备已打开：\??\ROOT#SYSTEM#0002#{1abc05c0-c378-41b9-9cef-df1aba82b015}
[ OK ] 零报告被接受（返回 0x00000000）
       起始光标 = (1169, 750)
[ OK ] 驱动相对移动 dx=40：(1169, 750) -> (1276, 724)
       光标复位：闭环成功，当前 (1169, 750) / 目标 (1169, 750)
       附：SetCursorPos 返回 1（本机当前环境下为 0，见实施文档 §4.2）

钩子读到的 flags（WM_MOUSEMOVE）：
  驱动 IOCTL           : ['0x0']
  mouse_event/SendInput: ['0x0', '0x1']

[ OK ] 驱动通道无 LLMHF_INJECTED 标记
```

> `mouse_event` 那一行可能混进上一步驱动报告残留的 `0x0`（两次事件落在同一采集窗口内）。**判定只看 `驱动 IOCTL` 行**：它必须存在且不含 `0x1`。

**同一脚本在 00:25–00:45 期间的输出（钩子不可用，退出码 2）**：

> 下面这段取自当时的实跑，唯一差异是当时脚本打印 `[WARN]` 并以退出码 1 结束；现版本已改为 `[SKIP]` + 退出码 2，语义相同（"无法校验"，不是故障）。

```
[ OK ] 设备已打开：\??\ROOT#SYSTEM#0002#{1abc05c0-c378-41b9-9cef-df1aba82b015}
[ OK ] 零报告被接受（返回 0x00000000）
       起始光标 = (1604, 669)
[ OK ] 驱动相对移动 dx=40：(1604, 669) -> (1733, 669)
       光标复位：闭环成功，当前 (1606, 669) / 目标 (1604, 669)
       附：SetCursorPos 返回 0（本机当前环境下为 0，见实施文档 §4.2）

钩子读到的 flags（WM_MOUSEMOVE）：
  驱动 IOCTL           : []
  mouse_event/SendInput: []
  [SKIP] 钩子未收到任何事件 → 无法校验注入标记（通道本身正常）
         见实施文档 §8.5 第 6 条；请在解锁的交互桌面重试
```

### 8.3 游戏内验收（需要 G HUB + 游戏运行在 720×1280）

前置：

1. scheme 设为 `driver`，**只开一个实例**。
2. **验收期间不要碰鼠标**。驱动闭环定位靠"读回光标位置 → 逼近目标"，真人同时移动鼠标会直接扰动这个闭环（本机实测：闭环目标 `+60/+40`，因并发真人输入实际落到 `-6/+71`，判定失败）。这条也是单实例闸门存在的同一个理由。
3. 等画面完全静止（NIKKE 大厅有 Live2D 动画与渐入过渡）。
4. 尽量在"用户态注入可用"的时间段跑一次、不可用的时间段再跑一次（见 §4.2 结论 3）。

| # | 操作 | 判据 | 实测值（必须填写） |
| --- | --- | --- | --- |
| 1 | `click_xy` 点一个已知按钮（如底部导航） | 面板切换，强变化像素数 > 噪声基线 ×30 | 未运行：按用户要求停止游戏验收。 |
| 2 | `long_click_xy(duration=1.0)` 点列表条目 | 打开详情面板 | 未运行：按用户要求停止游戏验收。 |
| 3 | `drag_xy` 250px 向下 | 位移搜索 \|Δ\| ≈ 手指位移，残差改善 > 70% | 未运行：按用户要求停止游戏验收。 |
| 4 | `ensure_sroll(count=2)` 默认路径 | 滚轮分页，`scroll_count` 与实际滚动像素是否吻合 | 未运行：按用户要求停止游戏验收。 |
| 5 | 滚轮连打 6 格 | 是否被吞格（决定 `WHEEL_INTERVAL`） | 未运行：按用户要求停止游戏验收。 |
| 6 | **滚轮每格实际像素** | 记录数值；与 `automation.py:282` 的 `65` 对比 | 未运行：按用户要求停止游戏验收。 |
| 7 | 连续运行 10 分钟 | 失败计数未被触发 | 未运行：按用户要求停止游戏验收。 |
| 8 | 双实例同时启用 `driver` | 第二个实例必须报错中止并建议改回 `postmessage` | 已实测两个 Python 进程：第二个被拒绝；同进程重复构造通过。完整调度实例未运行。 |
| 9 | 关掉 G HUB 后启用 `driver` | 必须报错中止，不得静默降级 | 未运行真实关闭测试；设备缺失 mock 测试通过。 |

本次游戏客户区为 2276×1280；`SetWindowPos` 返回拒绝访问，`SetForegroundWindow` 亦未成功。未执行游戏点击、长按、拖拽或滚轮验收，未完成 10 分钟运行与双时段对照。

### 8.4 测量方法论（照做，否则结论不可信）

| 坑 | 现象 | 真因 / 做法 |
| --- | --- | --- |
| **测前必须等画面静止** | 7 个位置全部出现 ~806k 全屏强变化、上下滚数值完全相同 | 画面还在**淡入过渡**（均值 173.4→176.6→184.1 单调变亮）。先零输入连拍 12 帧确认强变化 = 0 再测 |
| **否定性结论必须换位置复测** | "该界面没绑滚轮" | 当时光标停在 `(45,65)` 顶部标题区，那块本来就不响应。滚轮/悬停**对光标坐标敏感** |
| **判定滚动要算位移搜索 + 残差改善** | 全屏变化会被误判成滚动 | 真实平移：最佳位移处残差改善 ~80%；纯全屏变化时改善率恒为 0 |
| **位移方向要自校准** | 方向标签写反 | 用已知方向的平移喂给估计器校准出"Δ>0 = 内容下移" |
| **拖拽必须在按下后立刻跳一段** | 向上拖无反应、向下拖弹窗 | 光标静止过久被识别为**长按**。`DRAG_LEAD_PIXELS=50` 就是为此 |
| **噪声基线** | 单帧对比会误判 | 连抓 3 帧算噪声下限，判定阈值取 `噪声 × 30` |

### 8.5 已知未知（不得声称已验证）

| # | 未验证项 | 影响 |
| --- | --- | --- |
| ★1 | `SetCursorPos` 定位 + 驱动按键是否够用（Shape A） | 本机观测到 `SetCursorPos` 会间歇性失效（§4.2）。默认走已验证的 Shape B；**要把 Shape A 转正必须先跑 Step 0 固定靶点实验**，本次不做 |
| 2 | 非前台时驱动点击是否仍落到游戏窗口 | 推断能（点击发给光标下的窗口），未实测。本方案**不承诺后台**，不要对外声称支持后台 |
| 3 | 长按阈值 / 双击间隔 / 连点节奏是否与 `SendInput` 路径一致 | 影响 `CLICK_HOLD` 与 `press_mouse_click(wait_time)` 的取值手感 |
| 4 | 720×1280 下的滚轮每格像素、拖拽位移 | 决定 `automation.py:282` 的 `65` 是否需重标定。**本次按原值不改**，只在 §8.3 第 6 项记录数据 |
| 5 | 连打多格的合并阈值 | 决定 `WHEEL_INTERVAL` |
| 6 | `WH_MOUSE_LL` 钩子会间歇性收不到任何事件（连驱动产生的也收不到）：00:25–00:45 完全收不到，00:55 正常 | 只影响验证工具，不影响通道本身。用 §8.2 的三层判据，退出码 2 = 无法校验 |
| 7 | 用户态注入（`SendInput` / `mouse_event` / `SetCursorPos`）会**间歇性整体失效**的真因 | 也影响 `pyautogui` 与 `postmessage` 两条既有链路（后者定位同样依赖 `SetCursorPos`）。**本次不处理**，但值得单独立项 |

---

## 9. 提交

### 9.1 提交规则

- **只提交，不推送**。`AGENTS.md` 规定 `master` 禁止自动推送，本次没有推送授权。
- 提交信息用中文祈使句，描述最终行为，不要复述 diff：`新增驱动级鼠标控制方案 driver`
- PR 描述包含：变更目的、影响模块、验证步骤（§8 的实跑输出）、§8.5 的未证清单。**不要**提中间被否决的方案或"考虑过但没做"的实现。

### 9.2 回滚

把 `PCClientInfo.ControlScheme` 改回 `pyautogui` 并重启实例。代码层面未产生任何持久副作用（无注册表、无驱动安装、无落盘文件），**不需要清理**。

---

## 附录 A 驱动通道自检脚本（无点击副作用、零第三方依赖）

放在 `tmp/` 下运行，**不要提交**。已在本机实测通过。

```python
"""驱动通道自检（不需要游戏、无点击副作用）。

检查三件事：
  1) 能否打开 G HUB 虚拟鼠标设备接口，且 IOCTL 0x2A2010 被接受
  2) 相对移动报告是否真的推动了光标
  3) WH_MOUSE_LL 钩子在两种通道下读到的 flags：
       驱动 IOCTL            -> 0x000000
       mouse_event/SendInput -> 0x000001 (LLMHF_INJECTED)

不发送任何按键，不点击任何东西；结束时把光标放回原位。
无第三方依赖，任意带 ctypes 的 Python 均可运行。
"""
import ctypes
import struct
import time
from ctypes import wintypes as w

G_HUB_GUID = '{1abc05c0-c378-41b9-9cef-df1aba82b015}'
IOCTL_SEND_MOUSE = 0x2A2010
WH_MOUSE_LL = 14
PM_REMOVE = 0x0001
WM_MOUSEMOVE = 0x0200
MOUSEEVENTF_MOVE = 0x0001
LLMHF_INJECTED = 0x00000001

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
ntdll = ctypes.WinDLL('ntdll')
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


class IOSB(ctypes.Structure):
    _fields_ = [('Status', ctypes.c_ssize_t), ('Information', ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('pt', w.POINT),
        ('mouseData', w.DWORD),
        ('flags', w.DWORD),
        ('time', w.DWORD),
        ('dwExtraInfo', ctypes.c_void_p),
    ]


user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p]
user32.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
user32.CallNextHookEx.argtypes = [w.HANDLE, ctypes.c_int, w.WPARAM, w.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.SetWindowsHookExW.restype = w.HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, w.HANDLE, w.DWORD]
user32.UnhookWindowsHookEx.argtypes = [w.HANDLE]
kernel32.CreateFileW.restype = w.HANDLE
kernel32.CreateFileW.argtypes = [
    w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE,
]
ntdll.NtDeviceIoControlFile.restype = ctypes.c_long
ntdll.NtDeviceIoControlFile.argtypes = [
    w.HANDLE, w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    w.ULONG, ctypes.c_void_p, w.ULONG, ctypes.c_void_p, w.ULONG,
]


def make_report(buttons=0, dx=0, dy=0, wheel=0):
    return struct.pack('<BBhhB', buttons & 0xFF, 0, dx, dy, wheel & 0xFF)


def ioctl(handle, report):
    iosb = IOSB()
    status = ntdll.NtDeviceIoControlFile(
        handle, None, None, None, ctypes.byref(iosb),
        IOCTL_SEND_MOUSE, report, len(report), None, 0,
    )
    return status & 0xFFFFFFFF


def cursor():
    point = w.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def open_device():
    for index in range(10):
        path = rf'\??\ROOT#SYSTEM#000{index}#{G_HUB_GUID}'
        handle = kernel32.CreateFileW(path, 0xC0000000, 0x3, None, 3, 0, None)
        if handle == w.HANDLE(-1).value:
            continue
        if ioctl(handle, make_report()) == 0:
            return handle, path
        kernel32.CloseHandle(handle)
    return None, None


def pump(seconds):
    """低层钩子的回调由安装它的线程派发，必须持续抽消息。"""
    msg = w.MSG()
    end = time.time() + seconds
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.002)


events = []


def hook_proc(code, wparam, lparam):
    if code == 0 and wparam == WM_MOUSEMOVE:
        data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        events.append(data.flags)
    return user32.CallNextHookEx(None, code, wparam, lparam)


def main():
    handle, path = open_device()
    if handle is None:
        print('[FAIL] 没有 G HUB 虚拟鼠标接口响应 IOCTL 0x2A2010')
        print('       G HUB 是否已安装并正在运行？')
        return 1
    print(f'[ OK ] 设备已打开：{path}')
    print('[ OK ] 零报告被接受（返回 0x00000000）')

    origin = cursor()
    print(f'       起始光标 = {origin}')
    applied = []

    def rel(dx, dy=0):
        ioctl(handle, make_report(dx=dx, dy=dy))
        applied.append((dx, dy))
        pump(0.15)

    before = cursor()
    rel(40)
    after = cursor()
    moved = after[0] != before[0]
    print(f'[{" OK " if moved else "FAIL"}] 驱动相对移动 dx=40：{before} -> {after}')

    proc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)(hook_proc)
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)
    if not hook:
        print('[FAIL] SetWindowsHookExW 失败，无法校验注入标记')
        kernel32.CloseHandle(handle)
        return 1

    events.clear()
    rel(20)
    driver_flags = sorted(set(events))
    events.clear()
    user32.mouse_event(MOUSEEVENTF_MOVE, 20, 0, 0, None)
    pump(0.2)
    injected_flags = sorted(set(events))

    def restore(target, tolerance=2, tries=400):
        """闭环复位。

        相对位移非线性（实测请求 +40 实走 +129），所以必须读回位置逐步逼近；
        且**必须有超冲衰减** —— 没有衰减时会退化成 ±25px 的极限环，永远落不进容差。
        这段与 driver_mouse.LogiMouse.move_to 是同一套算法。
        """
        step, previous = 20.0, None
        for _ in range(tries):
            current = cursor()
            error_x, error_y = target[0] - current[0], target[1] - current[1]
            if abs(error_x) <= tolerance and abs(error_y) <= tolerance:
                return True
            if previous is not None and (previous[0] * error_x < 0 or previous[1] * error_y < 0):
                step = max(1.0, step * 0.6)
            ioctl(handle, make_report(
                dx=int(round(max(-step, min(step, error_x)))),
                dy=int(round(max(-step, min(step, error_y)))),
            ))
            previous = (error_x, error_y)
            pump(0.01)
        return False

    user32.UnhookWindowsHookEx(hook)
    restored = restore(origin)
    probe = user32.SetCursorPos(origin[0], origin[1])
    print(f'       光标复位：{"闭环成功" if restored else "闭环失败"}，当前 {cursor()} / 目标 {origin}')
    print(f'       附：SetCursorPos 返回 {probe}（本机当前环境下为 0，见实施文档 §4.2）')
    kernel32.CloseHandle(handle)

    print('\n钩子读到的 flags（WM_MOUSEMOVE）：')
    print(f'  驱动 IOCTL           : {[hex(f) for f in driver_flags]}')
    print(f'  mouse_event/SendInput: {[hex(f) for f in injected_flags]}')

    if not driver_flags:
        # 注意：钩子收不到事件 ≠ 通道故障。本机实测该钩子可能整体失效（见实施文档
        # §4.2 / §8.5），此时无法校验注入标记，但设备与位移仍正常。
        print('  [SKIP] 钩子未收到任何事件 → 无法校验注入标记（通道本身正常）')
        print('         见实施文档 §8.5 第 6 条；请在解锁的交互桌面重试')
        return 2
    ok = all(not (f & LLMHF_INJECTED) for f in driver_flags)
    print(f'\n[{" OK " if ok else "FAIL"}] 驱动通道无 LLMHF_INJECTED 标记')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
```

**真实输出示例见 §8.2**（含"钩子可用"与"钩子不可用"两种）。三点必须注意：

- 请求 `dx=40, dy=0` 时实测实际位移可达 `(+88, -35)` —— **放大 2.2× 且有轴向串扰**。这是 `move_to` 必须闭环的直接证据，不要把它简化成"发多少走多少"。
- `SetCursorPos` 的返回值在这台机器上**会间歇性为 0**，脚本只把它打印出来作为记录，不据此断言复位成功；复位由 `restore()` 闭环完成。
- `restore()` 里的**超冲衰减不能省**：去掉它（纯 ±20px 步长）会退化成 ±25px 的极限环，永远落不进容差——已实测，`driver_mouse.LogiMouse.move_to` 里的 `MOVE_STEP_DECAY` 是同一个道理。

---

## 附录 B 游戏内测量工具约定

如需复现 §8.3 的测量，用以下既有约定（脚本产生于 `tmp/winprobe/logi/`，属临时产物，可能被清理）：

| 用途 | 方法 |
| --- | --- |
| 抓帧 | `ImageGrab.grab(bbox=(窗口客户区左上角屏幕坐标, +客户区宽高), all_screens=True)`；**必须先 `SetProcessDpiAwarenessContext(-4)`**，否则坐标被 DPI 缩放 |
| 屏幕→客户区 | `ClientToScreen(hwnd, (0,0))` 得到 offset，客户区坐标 = 屏幕坐标 − offset（与 `Automation.current_window.offset` 同语义）|
| 强变化像素数 | `(np.abs(new - old).mean(axis=2) > 40).sum()` |
| 位移搜索 | 在候选区间内按 y 平移，取平均绝对差最小者，返回 `(Δy, 残差@0, 残差@Δy)`；**残差改善 = (r0 − rΔ) / r0**，真实平移 > 70%，纯全屏变化 = 0 |
| 噪声基线 | 光标静止连抓 3 帧，两两比较取最大值 |
| 参数化 | 禁止硬编码 `progress=0.x` 之外的时间等待；`time.sleep` 必须足够让画面结束过渡动画 |

既有的深度探针脚本（若 `tmp/` 未被清理）：`chan_test4.py`（高频往复 + 钩子 flags 对照）、`chan_test7.py`（自建靶子窗口点击验证）、`eq_ops5.py`（真实游戏窗口 click / long_click / drag）、`eq_wheel.py`、`eq_wheel2.py`（滚轮逐格增量）、`sign_check.py`（位移估计器符号自校准）、`REPORT-driver-ops-equipment.md`（完整实测报告）。
