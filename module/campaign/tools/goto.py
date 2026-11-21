"""为地图标定与编辑器移动提供控件适配和普通／EX 弹窗处理。"""

import json

import cv2

from module.campaign.navigation.controls import (
    terrain as terrain,
    map_open as map_open,
    map_close as map_close,
    capture_client as capture_client,
    battle_popup_score as battle_popup_score,
    normal_enemy_markers as normal_enemy_markers,
    A_INV as A_INV,
    EX_STAGE_TPL as EX_STAGE_TPL,
)
from module.campaign.tools import settings, runtime


def check_battle_popup(win, log, iteration, phase, image=None):
    """普通准备弹窗保留供上层处理；EX 弹窗只关闭一次并确认结果。

    使用已采集的 image（缺省时重新截图）确认准备按钮，区分普通和 EX 并保存截图与日志。
    无弹窗返回 None，普通弹窗保留；EX 仅关闭一次并复查，关闭失败抛错。
    """
    if image is None:
        image = capture_client(win)
    score = battle_popup_score(image)
    if score <= 0.8:
        return None
    ex_result = cv2.matchTemplate(image, EX_STAGE_TPL, cv2.TM_CCOEFF_NORMED)
    ex_score = float(cv2.minMaxLoc(ex_result)[1])
    status = 'ex_stage_skipped' if ex_score > 0.85 else 'battle_popup'
    screenshot = settings.output / f'battle_popup_{iteration}_{phase}.png'
    runtime.write_image(str(screenshot), image)
    entry = {'status': status, 'iteration': iteration, 'phase': phase,
             'popup_score': round(score, 4), 'ex_score': round(ex_score, 4), 'screenshot': str(screenshot)}
    log.append(entry)
    print(json.dumps(entry), flush=True)
    if status == 'ex_stage_skipped':
        x, y = win.gui.ClientToScreen(win.hwnd, (0, 0))
        win.handler.mouse_click(x + 1155, y + 521)
        runtime.pause(1.2)
        if battle_popup_score(capture_client(win)) > 0.8:
            raise RuntimeError('EX stage popup did not close.')
    return status
