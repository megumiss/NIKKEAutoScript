"""以小队为共同原点匹配已选目标的大小地图方向，不拟合距离或认领底图身份。"""

import numpy as np

DIRECTION_TOLERANCE = 45.


def direction_candidates(minimap_vectors, scene_vectors):
    """返回与任一小地图敌人方向相差不超过 45 度的实景下标，保留同方向多候选。

    输入是各自图像中“小队→敌人”的二维向量，屏幕向右、向下为正；两侧单位不需要相同。
    不比较向量长度，不拟合缩放，不按角差排序，也不要求一一对应；45 度为粗方向容差。
    零向量或非有限向量没有可用方向，跳过而不以屏幕中心替代小队；没有可用方向返回空列表。
    输出仅供点击前筛选，不代表同一个敌人、道路可达或普通／Boss／EX 分类证据。
    """
    small = np.asarray(minimap_vectors, float).reshape(-1, 2)
    scene = np.asarray(scene_vectors, float).reshape(-1, 2)
    valid_small = np.isfinite(small).all(axis=1) & np.any(small != 0, axis=1)
    valid_scene = np.isfinite(scene).all(axis=1) & np.any(scene != 0, axis=1)
    small = small[valid_small]
    indices = np.flatnonzero(valid_scene)
    if not len(small) or not len(indices):
        return []
    scene = scene[indices]
    small_angles = np.degrees(np.arctan2(small[:, 1], small[:, 0]))
    scene_angles = np.degrees(np.arctan2(scene[:, 1], scene[:, 0]))
    differences = (scene_angles[:, None] - small_angles[None, :] + 180) % 360 - 180
    return indices[np.any(np.abs(differences) <= DIRECTION_TOLERANCE, axis=1)].tolist()


def match_target_direction(field, frame, matrix, target, squad, enemy_kind='normal'):
    """为已选底图敌人匹配同向脚圈，只返回点击落点，不选择或替换规划目标。

    frame 来自同帧 observe_rings，matrix 为已接受的紧凑面板到底图变换；target/squad 为底图像素。
    先沿用现有 30 地图像素范围与类别检查找到原目标标记，再反投影到小地图比较方向，
    不比较大小地图中的敌我距离。实景原点使用当前唯一箭头下移 90 客户区像素，离屏即拒绝。
    45 度内只有一个可点击脚圈且没有其他小地图敌人同向竞争时返回；否则继续原目标的导航，
    不按检测置信度、距离或检测顺序改点其他敌人。调用方仍须在点击前复核新帧。
    """
    from module.campaign.navigation.controls import ARROW_GROUND_OFFSET
    from module.campaign.vision.offscreen import squad_offscreen
    from module.campaign.vision.perception import squad_arrow
    from module.campaign.vision.surface_motion import project

    target, squad = [np.asarray(point, float) for point in (target, squad)]
    if any(point.shape != (2,) or not np.isfinite(point).all() for point in (target, squad)):
        return dict(reason='原目标或小队坐标无效。')
    if not len(frame.centers) or len(frame.squad) != 1:
        return dict(reason='缺少可点击脚圈或唯一小队圆心。')
    if np.linalg.norm(frame.squad[0] - squad) > 12:
        return dict(reason='小队位置变化，丢弃方向证据。')
    nearby = [(index, label) for index, (label, point) in enumerate(frame.enemies)
              if np.linalg.norm(point - target) <= 30]
    if (enemy_kind not in ('normal', 'boss') or len(nearby) != 1
            or nearby[0][1] != f'minimap_enemy_{enemy_kind}'):
        return dict(reason='本帧小地图无法唯一确认原目标的敌人类型。')
    arrow = squad_arrow(field)
    if arrow is None or squad_offscreen(field) is not None:
        return dict(reason='实景小队原点不可用，不以屏幕中心替代。')
    ground = np.asarray(arrow, float) + [0., ARROW_GROUND_OFFSET]
    inverse = np.linalg.inv(np.asarray(matrix, float))
    origin = project(inverse, frame.squad[0])
    vectors = np.asarray([project(inverse, point) - origin for _, point in frame.enemies])
    scene_vectors = np.asarray(frame.centers) - ground
    target_index = nearby[0][0]
    indices = direction_candidates([vectors[target_index]], scene_vectors)
    report = dict(candidate_count=len(indices), angle_tolerance=DIRECTION_TOLERANCE)
    if len(indices) != 1:
        return dict(report, reason='原目标方向没有唯一实景落点，保留原目标继续导航。')
    index = indices[0]
    others = np.delete(vectors, target_index, axis=0)
    if direction_candidates(others, [scene_vectors[index]]):
        return dict(report, reason='其他小地图敌人与落点同向，保留原目标继续导航。')
    return dict(report, reason='原目标与可点击脚圈粗方向对应。', click=frame.centers[index].tolist())
