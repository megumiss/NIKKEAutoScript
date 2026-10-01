"""多帧地面锚点、遮挡恢复与有限目标触发；不把角色贴图中心当成地面坐标。"""

import re

import cv2
import numpy as np

from .surface_motion import contains, project, register_surface


class TargetTriggered(RuntimeError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def scene_anchor(reference, current, anchor, displacement):
    """角色全遮挡时，用周围静态地面跟踪已实测地面位置；两组支持域必须一致。"""
    anchor = np.asarray(anchor, float)
    source = anchor + np.asarray(displacement, float)
    exclude = anchor + [[-78, -160], [78, -160], [78, 50], [-78, 50]]
    candidates = []
    for width, height in [(170, 95), (210, 120)]:
        polygon = anchor + [[-width, -40], [width, -40], [width, height], [-width, height]]
        polygon = np.clip(polygon, [1, 1], np.asarray(reference.shape[1::-1]) - 2)
        fit = register_surface(reference, current, polygon, exclude=exclude)
        if not contains(fit['support'], source):
            raise ValueError('遮挡位置不在可跟踪的地面范围内。')
        candidates.append(project(fit['matrix'], source))
    if np.linalg.norm(candidates[0] - candidates[1]) > 8:
        raise ValueError('遮挡恢复的两组地面投影不一致。')
    return np.mean(candidates, axis=0)


def resolve_anchor(session, field, observation, allow_scene=True):
    """优先用箭头完整周期定位地面；失败时保留圆环与已有地面投影恢复。"""
    from . import goto, runtime
    from .arrow_anchor import sample_ground_anchor
    from .live import movement_anchor
    try:
        anchor, current = sample_ground_anchor(session, field)
    except ValueError as error:
        session.emit(arrow_fallback=str(error))
        field = goto.capture_client(session.win)
        session.identity(field)
    else:
        session.anchor_reference = (current.copy(), anchor, np.asarray(observation['position'], float))
        return anchor
    centers, frames = [], []
    current = field
    for attempt in range(6):
        session.check()
        frames.append(current)
        try:
            center = movement_anchor(current)
            centers.append(center)
            if len(centers) >= 2 and np.linalg.norm(centers[-1] - centers[-2]) <= 8:
                anchor = np.mean(centers[-2:], axis=0)
                session.anchor_reference = (current.copy(), anchor, np.asarray(observation['position'], float))
                session.emit(anchor_method='multi_frame_ring')
                return anchor
        except RuntimeError:
            pass
        if attempt < 5:
            session.pause(.3)
            current = goto.capture_client(session.win)
            session.identity(current)
    # 转动的断续圆弧在停稳的多帧中合并，颜色统一后只用于几何检测。
    gray = [cv2.cvtColor(im[360:620, 670:1110], cv2.COLOR_BGR2GRAY).astype(np.float32) for im in frames]
    shifts = [cv2.phaseCorrelate(gray[0], other)[0] for other in gray[1:]]
    if all(np.linalg.norm(shift) < 2 for shift in shifts):
        fused = np.zeros_like(current)
        for frame in frames:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, (5, 45, 175), (30, 255, 255))
            mask |= cv2.inRange(hsv, (90, 65, 175), (125, 255, 255))
            fused[mask > 0] = (255, 150, 0)
        try:
            anchor = movement_anchor(fused)
            session.anchor_reference = (current.copy(), anchor, np.asarray(observation['position'], float))
            session.emit(anchor_method='temporal_ring')
            return anchor
        except RuntimeError:
            pass
    if allow_scene and getattr(session, 'anchor_reference', None) is not None and hasattr(session, 'calibration'):
        reference, anchor, position = session.anchor_reference
        matrix = np.asarray(session.calibration[0]['matrix'])
        delta = np.asarray(observation['position']) - position
        anchor = scene_anchor(reference, current, anchor, matrix[:2, :2] @ delta)
        session.emit(anchor_method='occluded_ground_projection')
        return anchor
    runtime.write_image(session.folder / 'anchor_unresolved.png', current)
    raise ValueError('多帧均无法恢复小队地面位置；未追加点击，现场已保存。')


def collectible_counter(field, model):
    """紧凑小地图右下角的已收集/总数是拾取反馈，不依赖闪光或角色外观。"""
    crop = field[252:281, 177:226]
    result = next(iter(model.predict(cv2.resize(crop, None, fx=4, fy=4))))
    match = re.fullmatch(r'(\d{1,2})\s*/\s*(\d{1,2})', result['rec_text'].strip())
    if result['rec_score'] < .9 or match is None:
        return None
    found, total = map(int, match.groups())
    return (found, total) if 0 <= found <= total <= 30 else None


def probe_targets(target):
    target = np.asarray(target, float)
    return [target + offset for offset in [(0, 0), (10, 0), (-10, 0), (0, 10), (0, -10)]]
