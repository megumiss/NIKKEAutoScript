"""战斗接管的状态识别：准备弹窗、战斗中、胜利结算、战败结算与野外地图。

模板取自 1776×999 实机客户区截图，所以不涉及缩放；准备弹窗沿用 controls 的进入战斗按钮模板。
EX 关卡标题另取只含 "EX" 的窄模板：整条 "EX STAGE" 与普通关卡的 "38-20 STAGE" 共享 "STAGE"
字样，实测假命中 0.853，足以把普通关卡当成 EX 而跳过。

本模块只识别画面、不发输入：点什么、隔多久点由会话与执行层决定。
"""

from pathlib import Path

import cv2
import numpy as np

from module.campaign.navigation.controls import ENTER_BATTLE_TPL
from module.campaign.vision.minimap import map_visible, minimap_control_visible

ASSETS = Path(__file__).resolve().parent / 'assets'


def _load(name):
    """读取同目录 assets 下的模板；缺文件直接失败，避免运行时静默认不出状态。"""
    image = cv2.imread(str(ASSETS / name))
    if image is None:
        raise FileNotFoundError(ASSETS / name)
    return image


AUTO_TPL = _load('battle_auto.png')
PAUSE_TPL = _load('battle_pause.png')
VICTORY_TPL = _load('battle_victory.png')
DEFEAT_TPL = _load('battle_defeat.png')
ENTRY_CLOSE_TPL = _load('battle_entry_close.png')
EX_TITLE_TPL = _load('battle_ex_title.png')

# 状态模板的接受阈值（CCOEFF_NORMED）。实测每个模板在自身画面上为 1.000、在其余实机画面上最高 0.66，
# 所以 0.9 留了足够余量；进入战斗按钮沿用 controls 既有的 0.8 判据。
THRESHOLD = .9
ENTRY_THRESHOLD = .8
EX_THRESHOLD = .85
# 橙色判据统一用同一段 HSV：准备弹窗的快速战斗按钮与战斗中的 AUTO 图标都是「橙色 = 可用／已开」。
# 两处的占比阈值不同，是因为橙色面积不同：实心按钮实测 0.64，图标本体只有 0.171（关闭时都是 0.000）。
ORANGE_LOW, ORANGE_HIGH = (5, 110, 110), (30, 255, 255)
QUICK_BATTLE_ROI = (900, 830, 1130, 856)
QUICK_BATTLE_ORANGE_RATIO = .2
# AUTO 图标区（客户区像素）。只看图标本体这一小块，比整条按钮更不容易被变化的背景影响；
# 这块是叠在场景上的：画面左上角若恰好有大片橙色特效，可能被读成已开启（后果只是不点 AUTO）。
AUTO_ICON_ROI = (80, 18, 116, 54)
AUTO_ORANGE_RATIO = .05


def orange_ratio(image, roi):
    """一块 ROI 里橙色像素的占比；两处「橙色判可用」共用同一段 HSV。"""
    x1, y1, x2, y2 = roi
    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return float(cv2.inRange(hsv, ORANGE_LOW, ORANGE_HIGH).mean()) / 255
# 野外判据的两块 ROI 与观察器 minimap_mode_and_panel / minimap_closed 同源。
COMPACT_PANEL_ROI = (27, 118, 207, 273)
MINIMAP_CONTROL_ROI = (20, 76, 64, 120)
CONTROL_DARK_VALUE = 130


def score(template, image):
    """模板在整幅客户区里的最高归一化相关分数；模板比画面大时返回 0。"""
    if template.shape[0] > image.shape[0] or template.shape[1] > image.shape[1]:
        return 0.
    return float(cv2.minMaxLoc(cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED))[1])


def quick_battle_available(image):
    """准备弹窗里的快速战斗按钮是否可用（橙色实心）。只在该弹窗可见时才有意义。"""
    return orange_ratio(image, QUICK_BATTLE_ROI) > QUICK_BATTLE_ORANGE_RATIO


def auto_enabled(image):
    """战斗中的自动射击（左上 AUTO）是否已开启；关闭时要由接管点一下打开。"""
    return orange_ratio(image, AUTO_ICON_ROI) > AUTO_ORANGE_RATIO


def ex_stage(image):
    """准备弹窗是不是 EX 关卡：标题区只含 EX 两个字母的窄模板达标即算。"""
    return score(EX_TITLE_TPL, image) >= EX_THRESHOLD


def field_visible(image):
    """画面是否回到野外地图：紧凑面板可见，或面板被关掉但左上角小地图控件仍在。

    战斗、结算与准备弹窗都没有面板、也没有那个控件，所以不会把战斗画面当成野外。
    关闭态判据与观察器 minimap_closed 一致（控件外框可见且偏暗）。
    """
    x1, y1, x2, y2 = COMPACT_PANEL_ROI
    if map_visible(image[y1:y2, x1:x2]):
        return True
    x1, y1, x2, y2 = MINIMAP_CONTROL_ROI
    control = image[y1:y2, x1:x2]
    if not minimap_control_visible(control):
        return False
    return bool((cv2.cvtColor(control, cv2.COLOR_BGR2HSV)[:, :, 2] < CONTROL_DARK_VALUE).mean() > .5)


def classify(scores, field, quick, auto_on):
    """把一路打分与野外判据判成一个状态；纯函数，便于用假分数固定优先级。

    scores 的键是 auto_tpl / pause / victory / defeat / entry（前两个是模板分，别与返回的 auto 混淆）。
    优先级从「画面唯一」到「画面常见」：战败与胜利结算、准备弹窗、战斗中，最后才是野外——野外判据
    只看小地图，放最后可以避免弹窗盖住画面时被误判成回到野外。quick / auto_on 是颜色判据的结果，
    只在对应状态里有意义。
    """
    if scores.get('defeat', 0.) >= THRESHOLD:
        return {'state': 'defeat', 'quick': quick, 'auto': auto_on}
    if scores.get('victory', 0.) >= THRESHOLD:
        return {'state': 'victory', 'quick': quick, 'auto': auto_on}
    if scores.get('entry', 0.) >= ENTRY_THRESHOLD:
        return {'state': 'entry', 'quick': quick, 'auto': auto_on}
    if max(scores.get('auto_tpl', 0.), scores.get('pause', 0.)) >= THRESHOLD:
        return {'state': 'battle', 'quick': quick, 'auto': auto_on}
    if field:
        return {'state': 'field', 'quick': quick, 'auto': auto_on}
    return {'state': 'unknown', 'quick': quick, 'auto': auto_on}


def battle_state(image):
    """识别一帧 1776×999 客户区截图属于战斗接管的哪个状态。

    返回 dict(state, quick, auto, scores)：state 是 defeat / victory / entry / battle / field / unknown，
    quick 只在 entry 时有意义（快速战斗是否可用），auto 是战斗中的自动射击开关状态（不是模板分，
    模板分在 scores['auto_tpl']），scores 供日志与离线核对。
    """
    if image is None or image.shape != (999, 1776, 3):
        raise ValueError('战斗状态识别需要 1776×999 的三通道客户区截图。')
    scores = {'auto_tpl': score(AUTO_TPL, image), 'pause': score(PAUSE_TPL, image),
              'victory': score(VICTORY_TPL, image), 'defeat': score(DEFEAT_TPL, image),
              'entry': score(ENTER_BATTLE_TPL, image)}
    quick = quick_battle_available(image) if scores['entry'] >= ENTRY_THRESHOLD else False
    return dict(classify(scores, field_visible(image), quick, auto_enabled(image)), scores=scores)


def score_text(scores, key):
    """把一项识别分数写成日志文本；缺项显示为「—」，免得替身或旧快照被读成 0.00 的假分数。"""
    value = (scores or {}).get(key)
    return '—' if value is None else f'{float(value):.2f}'
