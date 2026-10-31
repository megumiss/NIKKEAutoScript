"""共享客户区标定、小地图控件与弹窗模板；不加载采集器或实验配置。"""

import time
from pathlib import Path

import cv2
import numpy as np

from module.campaign.vision import perception
from module.campaign.vision.minimap import terrain as terrain

ASSETS = Path(__file__).resolve().parents[1] / 'vision/assets'
MAP_MINIMIZE = (644 + 14, 280 - 11)
# 客户区位移 = A_INV @ 标定参考平面位移；使用前须核对地图包中的配套矩阵。
A_INV = np.array([[-1.9955, 1.2530], [1.6580, 0.8463]])
# 展开小地图 ROI → 标定参考平面的单应（calibration.json 的 projection）。参考平面是全局共享的，
# 与各章节地图像素系不同：A_INV 只能作用在它上面，直接作用在地图像素上会转错方向。
ROI_TO_PLANE = np.array(
    [
        [-1.2724089568292891, 3.5215890963515264, 638.6837515677],
        [1.3973418443646326, 3.4636053644742213, 20.0],
        [3.0143883607574224e-05, 0.0026849827674979183, 1.0],
    ]
)
# 视野中心对应的客户区像素；30 帧历史截图实测均值，见 tmp/barrier_test/historical_error.json。
VIEWPORT_PIXEL = (887.7, 496.0)
# 地面点的底图位移 → 客户区位移的固定镜头仿射（1776×999 客户区）。2026-10-22 用第 38 章
# 两次独立运行的光幕／立柱脚部轨迹标定：拟合残差 ≤16.5 客户区像素，跨运行预测误差
# ≤18 客户区像素（约 9 地图像素），同运行 SIFT 场景位移复核误差 ≤9px。
# 固定朝向纯平移的镜头用于预标注关联与机关状态投影，不用于估算光幕封锁位置。
GROUND_TO_CLIENT = np.array([[1.9091, -0.5931], [-0.0173, 1.5890]])
# 紧凑面板里相机注视点的位置：小队居中停稳时圆环稳定在此（非面板几何中心），实测见
# tmp/barrier_test/center_first.json。
GAZE = (107.2, 104.0)
# 箭头动画中点距小队脚下地面的客户区像素：箭头锚点换算脚点的标定约定（1776×999 客户区），
# 场景投影共用同一数值，见 arrow_anchor.cycle_ground_anchor、barriers.scene_anchor。
ARROW_GROUND_OFFSET = 90.0
# template_matrix 把紧凑面板中心钉在展开 ROI 的这个点上（module.campaign.vision.matching.template_matrix）。
EXPANDED_CENTER = (243.0, 231.0)
# 大地图地面圆环在小队脚下覆盖的地图像素半径：客户区投影为 154×215 椭圆（半轴 76.9/107.5 px，
# 长短轴比 0.72），按 1 地图像素 ≈ 2.7 客户区像素换算得短轴 28、长轴 40。椭圆随镜头朝向旋转，
# 各向同性判定（点击放行、到点确认）只能用最坏值 SQUAD_REACH；SQUAD_REACH_WIDE 只用于
# 「目标已被圆环罩住、低分配准也可信」的场景，两者语义不同，不得互相替代。
SQUAD_REACH = 28.0
SQUAD_REACH_WIDE = 40.0
ENTER_BATTLE_TPL = cv2.imread(str(ASSETS / 'enter_battle_tpl.png'))
if ENTER_BATTLE_TPL is None:
    raise FileNotFoundError(ASSETS / 'enter_battle_tpl.png')
EX_STAGE_TPL = cv2.imread(str(ASSETS / 'ex_stage_tpl.png'))
if EX_STAGE_TPL is None:
    raise FileNotFoundError(ASSETS / 'ex_stage_tpl.png')


def map_open(win, reset=False):
    """默认保留镜头视野；定位小队时 reset=True 先最小化再打开，使小地图回正。

    通过窗口状态机展开小地图，reset=False 保留镜头寻位后的视野。
    reset=True 用于真实小队观测，先收起再展开以回正地图；切换失败由窗口实现向上传播。
    """
    win.reset_minimap(expanded=True, reset=reset)


def map_close(win):
    """将展开面板收起为紧凑地图，保留地图观测能力。

    请求紧凑地图状态，保留小地图可见以便观察道路变化。
    不切换到完全隐藏状态，也不复位镜头；控件识别与有限重试由 reset_minimap 负责。
    """
    win.reset_minimap(expanded=False, reset=False)


def capture_client(win):
    """检查前台与窗口几何后截取原始客户区，输出 BGR 图供模板和落点分析。

    在确认前台和窗口几何后截取 1776×999 客户区，返回 BGR 数组。
    截图范围使用客户区屏幕原点并允许多显示器；生产窗口失焦时先恢复，不能返回其他窗口画面。
    没有 client 接口的实验适配器仍在前台校验失败时直接报错。
    """
    if hasattr(win, 'client'):
        win.check()
        return win.client()
    from PIL import ImageGrab

    win.check()
    if win.gui.GetForegroundWindow() != win.hwnd:
        raise RuntimeError('Game lost focus; client capture stopped.')
    x, y = win.gui.ClientToScreen(win.hwnd, (0, 0))
    image = ImageGrab.grab(bbox=(x, y, x + 1776, y + 999), all_screens=True)
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def battle_popup_score(image):
    """在原始客户区寻找进入战斗按钮，仅返回模板相关分数，不点击按钮。

    image 为 BGR 客户区截图，以进入战斗按钮模板计算归一化相关峰值。
    只返回浮点分数，阈值与后续动作由调用者决定；此函数不发送输入。
    """
    result = cv2.matchTemplate(image, ENTER_BATTLE_TPL, cv2.TM_CCOEFF_NORMED)
    return float(cv2.minMaxLoc(result)[1])


def normal_enemy_markers(image, matrix, mode):
    """检测普通敌人并排除 EX，将完整候选按本帧矩阵投影。"""
    return perception.normal_enemy_markers(image, matrix, mode=mode)


def movement_click(anchor, offset):
    """把旧标定平面的位移换成场景点击，统一缩短两轴以保持方向并限制安全范围。

    anchor 为客户区地面点，offset 属于配套场景标定的参考平面位移。
    两轴按同一比例缩放至 240px 及安全边界内，返回浮点落点；锚点本身越界则抛错。
    """
    screen_offset = A_INV @ offset
    length = float(np.linalg.norm(screen_offset))
    scale = min(1.0, 240 / max(length, 1))
    # Scale both axes together so safe bounds do not change the walking direction.
    for axis, (lower, upper) in enumerate(((250, 1500), (180, 880))):
        if not lower <= anchor[axis] <= upper:
            raise ValueError('Squad anchor is outside the safe field area.')
        delta = screen_offset[axis]
        if delta > 0:
            scale = min(scale, (upper - anchor[axis]) / delta)
        elif delta < 0:
            scale = min(scale, (lower - anchor[axis]) / delta)
    return anchor + screen_offset * scale


def pause_window(win, seconds):
    """短周期等待期间检查受控窗口，供不持有调度器的实验适配器复用。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        win.check()
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
