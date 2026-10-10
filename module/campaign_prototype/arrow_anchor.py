"""在固定客户区尺寸下消除小队箭头动画，并换算为原标定使用的地面锚点。"""

import numpy as np


class ArrowUnavailable(ValueError):
    """采样预算用尽，但未发现小地图移动或身份变化。"""

    def __init__(self, message, field):
        """保存周期采样耗尽时的最后一张客户区图像。

        field 供上层遮挡恢复和失败截图使用；该异常仅表示箭头证据不足，不用于吞掉移动或身份异常。
        """
        super().__init__(message)
        self.field = field


def cycle_ground_anchor(points):
    """每半窗口至少六次有效观测，用一致的动画极值中点定位地面。

    points 必须保留 16 个时序位置，漏检以非有限坐标表示，每半窗口至少六帧有效。
    检查横向漂移、10～18px 振幅和半窗口极值一致性后，返回动画中点向下补偿 90px 的客户区地面坐标。
    """
    points = np.asarray(points, float)
    if points.shape != (16, 2):
        raise ValueError('箭头周期采样不完整。')
    valid = np.isfinite(points).all(axis=1)
    if any(np.count_nonzero(part) < 6 for part in np.array_split(valid, 2)):
        raise ValueError('箭头周期有效采样不足。')
    observed = points[valid]
    if np.ptp(observed[:, 0]) > 3:
        raise ValueError('采样期间小队横向位置变化。')
    halves = [part[np.isfinite(part).all(axis=1), 1] for part in np.array_split(points, 2)]
    bounds = np.array([[part.min(), part.max()] for part in halves])
    amplitude = np.ptp(observed[:, 1])
    if not 10 <= amplitude <= 18 or np.max(np.abs(bounds[0] - bounds[1])) > 3:
        raise ValueError('箭头动画周期不完整或小队仍在移动。')
    center = np.array([np.median(observed[:, 0]), (observed[:, 1].min() + observed[:, 1].max()) / 2])
    # 1776×999 客户区，第 33 章实测动画中点距原地面点击基准约 90px，保持标定坐标含义。
    return center + [0, 90]


def sample_ground_anchor(session, field):
    """最多采样 48 帧，在静止的 16 帧滑动窗口内验证箭头周期。

    field 必须是 1776×999 的 BGR 客户区图；每次新截图前后检查取消、身份和紧凑道路变化。
    返回锚点与最后截图，并发布采样证据；48 帧仍无完整周期抛出 ArrowUnavailable，移动或身份错误直接传播。
    """
    from . import goto
    from .live import squad_arrow

    if field.shape[:2] != (999, 1776):
        raise ValueError('箭头锚点仅适用于 1776×999 客户区。')
    points = []
    missing = 0
    failure = '未找到唯一的小队箭头。'
    first_terrain = goto.mr.terrain(field[90:248, 32:211])
    for index in range(48):
        session.check()
        if index:
            session.pause(.2)
            field = goto.capture_client(session.win)
            session.identity(field)
        current_terrain = goto.mr.terrain(field[90:248, 32:211])
        # 道路轮廓变化比角色动画可靠；采样期间发生行走或镜头移动时不使用箭头极值。
        if np.mean(first_terrain != current_terrain) > .03:
            raise ValueError('箭头采样期间小地图道路发生变化。')
        arrow = squad_arrow(field)
        missing += arrow is None
        points.append([np.nan, np.nan] if arrow is None else arrow)
        observed = np.asarray(points)
        valid = np.isfinite(observed).all(axis=1)
        if valid.any() and np.ptp(observed[valid, 0]) > 3:
            raise ValueError('采样期间小队横向位置变化。')
        if len(points) < 16 or arrow is None:
            continue
        try:
            anchor = cycle_ground_anchor(points[-16:])
        except ValueError as error:
            failure = str(error)
            continue
        session.emit(anchor_method='arrow_cycle',
                     arrow_samples=[np.asarray(p).tolist() if np.isfinite(p).all() else None for p in points[-16:]],
                     arrow_attempts=index + 1, arrow_missed_frames=missing, ground_anchor=anchor.tolist())
        return anchor, field
    session.emit(arrow_attempts=48, arrow_missed_frames=missing, anchor_method=None)
    raise ArrowUnavailable(f'箭头采样 48 帧后仍无法确定地面位置（漏检 {missing} 帧）：{failure}', field)


def sample_window_anchor(win, field, evidence):
    """独立实验入口共用周期采样，保留窗口、停止和战斗弹窗检查。

    使用 win 的停止检查和 runtime.pause 组成轻量会话，采样证据直接合并到 evidence。
    返回与 sample_ground_anchor 相同的锚点和截图；首次图像及后续帧都需通过战斗弹窗检查。
    """
    from types import SimpleNamespace
    from . import goto, runtime

    def check_field(current):
        """对周期采样的客户区图检查战斗准备模板。

        命中后直接抛错，避免把弹窗中的亮色形状识别成小队箭头继续采样。
        """
        if goto.battle_popup_score(current) > .8:
            raise RuntimeError('Battle popup during arrow sampling; stop')

    win.check()
    check_field(field)
    session = SimpleNamespace(win=win, check=win.check, pause=runtime.pause,
                              identity=check_field, emit=evidence.update)
    return sample_ground_anchor(session, field)
