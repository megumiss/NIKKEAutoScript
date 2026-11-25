"""主线异常后的独立镜头回正，不使用失败规划的坐标，也不发送小队移动。"""

import cv2
import numpy as np

from module.campaign.navigation import controls as goto
from module.campaign.navigation.camera_navigation import SquadOffscreen, pan_endpoints, pan_scene
from module.campaign.vision.offscreen import squad_offscreen
from module.campaign.vision.perception import detect_markers, squad_arrow
from module.campaign.vision.surface_motion import project
from module.logger import logger

CENTER_TOLERANCE = 50.
GROUND_STABLE_TOLERANCE = 20.
VERIFY_INTERVAL = .4
CENTER_ATTEMPTS = 4
MAX_CENTER_PANS = 6
CENTER_PROGRESS_MIN = 8.
CENTER_PROGRESS_FRACTION = .1
CENTER_GAIN_BOUNDS = (.25, 2.)


def centering_frame(session):
    """取得同一紧凑帧的小队箭头、圆环与离屏标记，不依赖道路配准或历史位置。

    圆环坐标属于缩放至 1920×1080 后的紧凑 ROI，与 controls.GAZE 同源；箭头换算的
    地面点属于原始客户区，只用于镜头调整及按下点避让，不作为小队移动的落点。
    圆环不唯一时按缺失处理；截图前检查取消、窗口和时限，弹窗由身份闸门向上传播。
    """
    session.check()
    field = session.win.client()
    session.identity(field)
    arrow = squad_arrow(field)
    panel = cv2.resize(field, (1920, 1080))[96:307, 25:243]
    players, _ = detect_markers(panel, np.eye(3), mode='compact')
    ground = None if arrow is None else np.asarray(arrow, float) + [0., goto.ARROW_GROUND_OFFSET]
    evidence = dict(ground=ground, ring=players[0] if len(players) == 1 else None,
                    offscreen=squad_offscreen(field))
    session.record('recovery_center_frame', field=field, **evidence)
    return evidence


def centered(evidence):
    """只按箭头地面点判断小队是否位于视野中央附近。

    离屏图标出现、箭头缺失或坐标非有限值时不通过；小地图圆环不参与回正判定。
    """
    ground = evidence['ground']
    if evidence['offscreen'] is not None or ground is None:
        return False
    ground = np.asarray(ground, float)
    return bool(np.isfinite(ground).all()
                and np.linalg.norm(ground - goto.VIEWPORT_PIXEL) <= CENTER_TOLERANCE)


def recenter_mechanism_edge(session, mechanism_position):
    """已关联中央解锁光幕后，按机关位置与新视口给边缘小队执行一次有界镜头短拖。

    仅用于机关触发后箭头和离屏图标同时缺失的场景。再次展开地图独立配准视口，将机关
    标注投回面板；落点位于面板内部安全区时拒绝推定小队在边缘。拖动方向由固定镜头
    仿射给出，最大 280 客户区像素，按下和抬起都限制在中央区域以避开边缘小队。
    不点击地面，不以投影位置作为导航小队坐标；拖动后仍走原箭头或离屏图标回正及实测定位。
    配准、标注、窗口或输入失败向上传播，普通恢复不调用本方法。
    """
    position = np.asarray(mechanism_position, float)
    if (position.shape != (2,) or not np.isfinite(position).all()
            or np.any(position < 0) or np.any(position >= session.owner.state.size)):
        raise ValueError('机关边缘回正缺少有效机关标注位置。')
    report = session.mechanism_viewport()
    matrix = np.asarray(report['roi_to_map'], float)
    panel_point = project(np.linalg.inv(matrix), position)
    if (not np.isfinite(panel_point).all()
            or (32 <= panel_point[0] <= 486 - 32 and 32 <= panel_point[1] <= 462 - 32)):
        raise ValueError('机关位置未处于展开地图视野边缘，不能执行边缘小队回正。')
    offset = -goto.GROUND_TO_CLIENT @ (position - np.asarray(report['position'], float))
    distance = float(np.linalg.norm(offset))
    if not np.isfinite(distance) or distance < 8:
        raise ValueError('机关边缘回正没有有效镜头拖动方向。')
    drag = offset * min(1., 280. / distance)
    start, end = pan_endpoints((500, 280, 1275, 760), drag)
    goto.map_close(session.win)
    session.identity(session.win.client())
    session.check()
    origin = np.asarray(session.win.client_origin(), int)
    session.win.handler.mouse_swipe(tuple(origin + start), tuple(origin + end))
    if session.win.handler._failures:
        raise RuntimeError('机关边缘回正镜头短拖失败，未发送地面点击。')
    session.record('mechanism_edge_recovery', force=True, mechanism_position=position.tolist(),
                   viewport=report['position'], panel_point=panel_point.tolist(), drag=drag.tolist())
    session.pause(1.2)


def _initial_center_gain(session):
    """读取会话已测的镜头跟手比例并限制在安全区间，无效时退回 1.0。

    Mock 会话或未初始化场景可能返回非数值，必须做类型与有限值校验。
    """
    raw = getattr(session, 'camera_gain', 1.)
    if isinstance(raw, (int, float, np.floating)) and np.isfinite(raw):
        return float(np.clip(raw, *CENTER_GAIN_BOUNDS))
    return 1.


def _measure_pan_gain(before, latest, commanded, responses, gain):
    """按回正拖动前后的实测地面位移更新跟手比例；拖动过短或异常响应时保持原值。

    与镜头导航采用相同的投影比判据（0.2~3.0），并用近三次中位数限制单帧抖动。
    """
    norm = float(np.linalg.norm(commanded))
    if norm < 20.:
        return gain
    actual = np.asarray(latest, float) - np.asarray(before, float)
    response = float(actual @ commanded) / float(commanded @ commanded)
    if .2 <= response <= 3.:
        responses.append(response)
        return float(np.clip(np.median(responses[-3:]), *CENTER_GAIN_BOUNDS))
    return gain


def recenter_squad(session, mechanism_position=None):
    """回正小队并复核停稳，图标回正返回 True，视觉居中返回 False，缺少标记返回 None。

    未找到安全拖动起点时交由调用方在地图面板定位。每次微调前以两帧核对箭头和圆环，
    按实测拖距自适应估算镜头跟手比例；只要向中心存在明显收敛进展就重置停滞计数，
    小队仍在移动、未稳或无进展停滞达到上限才停止。已绑定机关的边缘回正只允许一次。
    """
    session.check()
    session.await_field()
    goto.map_close(session.win)
    session.pause(VERIFY_INTERVAL)
    session.verify_identity(session.await_field())
    evidence = centering_frame(session)
    if mechanism_position is not None and evidence['ground'] is None and evidence['offscreen'] is None:
        recenter_mechanism_edge(session, mechanism_position)
        evidence = centering_frame(session)
    gain = _initial_center_gain(session)
    responses = []
    pending_pan = None
    stalls = 0
    pans = 0
    for _ in range(CENTER_ATTEMPTS + MAX_CENTER_PANS + 2):
        if stalls >= CENTER_ATTEMPTS:
            break
        session.pause(VERIFY_INTERVAL)
        checked = centering_frame(session)
        marker, current = evidence['offscreen'], checked['offscreen']
        if marker is not None and current is not None:
            if np.linalg.norm(np.asarray(current['center']) - marker['center']) <= 12.:
                session.click_client(current['center'], camera_only=True)
                logger.info('已双帧确认并点击离屏小队图标。')
                return True
            stalls += 1
        elif current is None and marker is None:
            ground, latest = evidence['ground'], checked['ground']
            ring, new_ring = evidence['ring'], checked['ring']
            if ground is None and latest is None:
                logger.info('未取得小队场景箭头或离屏图标，转入地图定位。')
                return None
            if ground is not None and latest is not None:
                stable = np.linalg.norm(np.asarray(latest) - ground) <= GROUND_STABLE_TOLERANCE
                if ring is not None and new_ring is not None:
                    stable = stable and np.linalg.norm(np.asarray(new_ring) - ring) <= 3.
                if stable:
                    if pending_pan is not None:
                        before, commanded = pending_pan
                        pending_pan = None
                        gain = _measure_pan_gain(before, latest, commanded, responses, gain)
                        before_dist = float(np.linalg.norm(before - goto.VIEWPORT_PIXEL))
                        after_dist = float(np.linalg.norm(np.asarray(latest, float) - goto.VIEWPORT_PIXEL))
                        if after_dist <= before_dist - max(CENTER_PROGRESS_MIN, CENTER_PROGRESS_FRACTION * before_dist):
                            stalls = 0
                        else:
                            stalls += 2
                    if centered(evidence) and centered(checked):
                        session.record('recovery_centered', force=True, **checked)
                        return False
                    if centered(checked):
                        evidence = checked
                        continue
                    if stalls < CENTER_ATTEMPTS and pans < MAX_CENTER_PANS:
                        offset = np.asarray(goto.VIEWPORT_PIXEL) - latest
                        drag = offset / gain
                        norm = float(np.linalg.norm(drag))
                        if norm > 560.:
                            drag = drag * (560. / norm)
                        try:
                            pan_scene(session, drag, require_arrow=True)
                        except SquadOffscreen:
                            return None
                        pans += 1
                        pending_pan = (np.asarray(latest, float), np.asarray(drag, float))
                        evidence = centering_frame(session)
                        continue
                    break
                session.reset_navigation()
                stalls += 1
            else:
                stalls += 1
        else:
            stalls += 1
        evidence = checked
    raise ValueError('回正复核达到上限，小队位置或镜头仍未稳定。')
