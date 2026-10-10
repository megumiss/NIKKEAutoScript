"""箭头周期定位、小地图辅助恢复与有限目标触发。"""

import re
from pathlib import Path

import cv2
import numpy as np

from . import perception

from .surface_motion import contains, project, register_surface


class SquadPositionChanged(RuntimeError):
    """镜头平移后的小队观测已变化，必须丢弃旧计划并重新定位。"""

    def __init__(self, before, after):
        super().__init__('平移期间小队位置发生变化，需要停稳后重新定位。')
        self.before = np.asarray(before, float).tolist()
        self.after = np.asarray(after, float).tolist()


class AnchorUnresolved(ValueError):
    """箭头和会话地面基准均无法提供落点，允许导航层尝试一次道路短移。"""


class TargetTriggered(RuntimeError):
    def __init__(self, message, evidence):
        """携带实际触发原因及结构化证据结束导航。

        上层将该异常映射为 triggered 状态；与普通到点或失败区分，便于确认拾取计数和战斗弹窗。
        """
        super().__init__(message)
        self.evidence = evidence


def scene_anchor(reference, current, anchor, displacement):
    """角色全遮挡时，用周围静态地面跟踪已实测地面位置；两组支持域必须一致。

    anchor 和 displacement 均为参考客户区像素，先估计小队在参考帧中的新地面位置。
    排除角色区域后，用两种大小的静态地面范围分别配准；支持域不足或结果相差超过 8px 时拒绝恢复。
    """
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
    """仅从箭头建立基准；短暂丢失时以小地图位移和静态地面交叉恢复。

    成功的箭头周期会更新会话中的截图、锚点和地图位置基准。
    仅捕获 ArrowUnavailable；允许遮挡恢复时结合真实小队位移和两组地面配准，恢复结果不更新箭头基准。
    没有有效证据时保存 anchor_unresolved.png 并抛错；allow_scene=False 用于必须直接观测箭头的标定采样。
    """
    from . import goto, runtime
    from .arrow_anchor import ArrowUnavailable, sample_ground_anchor
    try:
        anchor, current = sample_ground_anchor(session, field)
    except ArrowUnavailable as error:
        reason = str(error)
        session.emit(arrow_fallback=str(error))
        field = error.field
    else:
        session.anchor_reference = (current.copy(), anchor, np.asarray(observation['position'], float))
        session.anchor_reference_method = 'arrow_cycle'
        return anchor
    if (allow_scene and getattr(session, 'anchor_reference', None) is not None
            and getattr(session, 'anchor_reference_method', None) == 'arrow_cycle'
            and observation.get('position_kind') == 'squad'):
        reference, anchor, position = session.anchor_reference
        delta = np.asarray(observation['position']) - position
        try:
            if hasattr(session, 'calibration'):
                matrix = np.asarray(session.calibration[0]['matrix'])
                displacement = matrix[:2, :2] @ delta
            else:
                from .probe import jacobian
                roi = np.asarray(observation['player_roi'], float)
                conversion = jacobian(np.asarray(observation['roi_to_map']), roi) @ np.linalg.inv(
                    jacobian(session.localizer.old_matrix, roi))
                displacement = goto.A_INV @ np.linalg.solve(conversion, delta)
            anchor = scene_anchor(reference, field, anchor, displacement)
        except (ValueError, np.linalg.LinAlgError) as error:
            session.emit(anchor_recovery_error=str(error))
        else:
            session.emit(anchor_method='minimap_ground_projection', ground_anchor=anchor.tolist())
            return anchor
    runtime.write_image(session.folder / 'anchor_unresolved.png', field)
    raise AnchorUnresolved(f'无法恢复小队箭头位置，未追加点击；现场已保存。{reason}')


def road_recovery_click(session, observation, target):
    """从本次会话的临时标定和同层道路掩码出发，仅生成一次恢复短移的落点。"""
    from . import goto
    from .camera_navigation import FIELD_BOUNDS, inside
    from .parallax_movement import checked_segment

    if observation.get('position_kind') != 'squad' or getattr(session, 'calibration', None) is None:
        raise ValueError('道路短移需要真实小队圆环和本次会话的临时标定。')
    road = getattr(session, 'surface', None)
    if road is None:
        raise ValueError('缺少同层道路掩码，无法进行道路短移。')
    data, reference = session.calibration
    position = np.asarray(observation['position'], float)
    origin = np.asarray(data['origin'], float)
    goto.map_close(session.win)
    field = goto.capture_client(session.win)
    session.identity(field)
    session.check_collectible(field)
    session.preview(field)
    matrix = np.asarray(data['matrix'], float)[:2, :2]
    direction = np.asarray(target, float) - position
    angle = np.arctan2(direction[1], direction[0])
    candidates = [position + radius * np.array([np.cos(angle + turn), np.sin(angle + turn)])
                  for radius in (10, 6) for turn in (0, np.pi / 4, -np.pi / 4, np.pi / 2, -np.pi / 2, np.pi)]
    for candidate in candidates:
        try:
            checked_segment(road, position, candidate)
            click = scene_anchor(reference, field, data['origin_anchor'], matrix @ (candidate - origin))
        except ValueError:
            continue
        if inside(click, FIELD_BOUNDS):
            session.emit(navigation_method='arrow_road_recovery', recovery_target=candidate.tolist(),
                         message='箭头不可见，尝试沿附近道路短移一次后重新识别。')
            return click
    raise ValueError('附近没有同时通过连续道路和场景配准验证的短移落点。')


def wake_arrow_click(session, observation):
    """站位不渲染箭头时，向附近开阔道路生成一次唤醒短移落点。

    台座类站位显示橙环且等再久也不出现箭头，需离开约 200 客户区像素才恢复。
    无箭头可用，按镜头居中特性把小队近似为客户区 (888, 505)，结合道路净空、敌人距离和路径验证选路；
    仅用于平面地图，分层地图的投影必须由实测标定建立。
    """
    from . import goto
    from .camera_navigation import FIELD_BOUNDS, field_chart, inside, target_projection
    from .parallax_localizer import ParallaxLocalizer

    if isinstance(session.localizer, ParallaxLocalizer):
        raise ValueError('分层地图不使用镜头居中近似做唤醒短移。')
    if observation.get('position_kind') != 'squad':
        raise ValueError('唤醒短移需要可信的小队圆环定位。')
    position = np.asarray(observation['position'], float)
    enemies = np.asarray(observation.get('enemy_markers', []), float)
    if enemies.size and np.min(np.linalg.norm(enemies - position, axis=-1)) < 60:
        raise ValueError('站位无箭头且敌人标记过近，放弃唤醒短移。')
    clearance = cv2.distanceTransform(session.localizer.road.astype(np.uint8), cv2.DIST_L2, 5)
    ys, xs = np.where(clearance >= 40)
    if enemies.size:
        keep = np.min(np.linalg.norm(np.stack([xs, ys], 1)[None] - enemies[:, None, :], axis=-1), axis=0) >= 50
        xs, ys = xs[keep], ys[keep]
    distances = np.hypot(xs - position[0], ys - position[1])
    # 过近的落点等于点击小队自身，无法离开橙环站位；80 地图像素约对应 200 客户区像素的恢复距离。
    nearby = np.where((distances >= 80) & (distances <= 400))[0]
    chart = field_chart(session.localizer, observation, np.array([888., 505.]))
    scores = distances[nearby] - 1.5 * clearance[ys[nearby], xs[nearby]]
    for candidate in nearby[np.argsort(scores)]:
        target = np.array([xs[candidate], ys[candidate]], float)
        try:
            session.localizer.route(position, target)
            _, projected = target_projection(chart, observation, target)
        except ValueError:
            continue
        if inside(projected, FIELD_BOUNDS):
            goto.map_close(session.win)
            session.emit(navigation_method='arrow_wake', wake_target=target.tolist(),
                         message='站位不显示箭头，向开阔道路短移一次唤醒后再重新识别。')
            return projected
    goto.map_close(session.win)
    session.emit(navigation_method='arrow_wake', wake_target=None,
                 message='站位不显示箭头且附近开阔道路不可投影，向画面下方盲移一次尝试唤醒。')
    return np.array([888., 700.])


def collectible_indicator(field):
    """检测场景收集提示，返回 YOLO 置信度与客户区位置。"""
    return perception.collectible_indicator(field)


def collectible_counter(field, model):
    """紧凑小地图右下角的已收集/总数是拾取反馈，不依赖闪光或角色外观。

    从固定客户区区域裁出计数器，放大后交给调用者提供的 OCR 模型。
    只接受置信度至少 0.9 且满足 0≤已收集≤总数≤30 的数字对；不可信结果返回 None，由会话做连续确认。
    """
    crop = field[252:281, 177:226]
    result = next(iter(model.predict(cv2.resize(crop, None, fx=4, fy=4))))
    match = re.fullmatch(r'(\d{1,2})\s*/\s*(\d{1,2})', result['rec_text'].strip())
    if result['rec_score'] < .9 or match is None:
        return None
    found, total = map(int, match.groups())
    return (found, total) if 0 <= found <= total <= 30 else None


def probe_targets(target):
    """生成目标及其四个正交邻点，顺序为中心、右、左、下、上。

    偏移量为 10 个地图像素；仅提供有限候选，调用者仍需逐点检查道路、标定支持域和触发反馈。
    """
    target = np.asarray(target, float)
    return [target + offset for offset in [(0, 0), (10, 0), (-10, 0), (0, 10), (0, -10)]]
