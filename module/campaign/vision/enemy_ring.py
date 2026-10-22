"""用正常镜头拖动产生的两帧识别目标脚圈，不使用场景与小地图之间的历史换算矩阵。"""

from dataclasses import dataclass

import cv2
import numpy as np

from module.campaign.vision.detection import detect_grid
from module.campaign.vision.perception import marker_candidates, refine_ring
from module.campaign.vision.surface_motion import project

RING_CONFIDENCE = .7
# 1776×999 客户区固定 HUD 的排除域；只限制真实点击，不能把控件下的候选当作已识别敌人。
HUD_RECTS = ((0, 0, 1776, 75), (0, 75, 245, 310), (1490, 75, 1776, 205),
             (0, 900, 1776, 999))
# 匹配门限是保守的初始操作点，不是由三帧回放推导出的泛化准确率。
MIN_CAMERA_STEP = 12.
MAX_CAMERA_STEP = 450.
MAX_TRANSVERSE = 16.
MAX_RESIDUAL = 40.
MIN_RESIDUAL_GAP = 80.
TRACK_DISTANCE = 15.
TRACK_GAP = 12.


def clickable_ring(box):
    """检查脚圈框及中心是否避开固定 UI；不要求接近中央，也不代表道路可达或身份已确认。

    输入为 1776×999 客户区的 xyxy 框；真实画面边缘截断、非有限坐标及压住 HUD 的框均拒绝。
    弹窗和野外状态由导航会话在调用前与实际输入前检查。
    """
    box = np.asarray(box, float)
    if box.shape != (4,) or not np.isfinite(box).all():
        return False
    x1, y1, x2, y2 = box
    if not (8 <= x1 < x2 <= 1768 and 8 <= y1 < y2 <= 991):
        return False
    return not any(x1 < right and x2 > left and y1 < bottom and y2 > top
                   for left, top, right, bottom in HUD_RECTS)


@dataclass
class RingFrame:
    """一次无小队移动的场景观测；只在单次镜头规划内复用，不跨目标或小地图重新锚定。

    centers/keys 为客户区像素，viewport/enemies/squad 为章节底图像素；两类坐标只比较各自的
    帧间变化。特征与检测来自同一客户区帧，matrix 必须由该帧的紧凑小地图配准得到。
    """

    centers: np.ndarray
    keys: np.ndarray
    descriptors: object
    viewport: np.ndarray
    enemies: list
    squad: list


def observe_rings(image, panel, matrix, viewport, observe_squad=True):
    """检测脚圈、紧凑小地图标记和场景纹理；只接受模型规定的客户区尺寸，不发输入。

    脚圈阈值至少 0.7，小地图旧类沿用自身阈值。SIFT 排除 HUD 与脚圈附近的角色动画；
    小地图到章节底图仅用于确认原目标和小队未移动，不把任何脚圈投影到小地图。
    observe_squad=False 用于纯镜头找敌人，不拟合实时小队圆环；实际移动的脚圈辅助默认保留校验。
    """
    if image.shape != (999, 1776, 3):
        raise ValueError('脚圈辅助要求 1776×999 客户区截图。')
    rings = [item for item in detect_grid(image, ('scene_enemy_ring',))
             if item.confidence >= RING_CONFIDENCE and clickable_ring(item.box)]
    centers = np.asarray([item.center for item in rings], float).reshape(-1, 2)
    mask = np.full(image.shape[:2], 255, np.uint8)
    for left, top, right, bottom in HUD_RECTS:
        mask[top:bottom, left:right] = 0
    for x, y in np.rint(centers).astype(int):
        cv2.rectangle(mask, (x - 65, max(0, y - 140)), (x + 65, y + 35), 0, -1)
    keys, descriptors = cv2.SIFT_create(nfeatures=6000).detectAndCompute(
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), mask)
    enemies, squad = [], []
    for item in marker_candidates(panel):
        if item.label == 'minimap_squad_ring':
            if observe_squad:
                center = refine_ring(panel, item)
                if center is not None:
                    squad.append(project(matrix, center))
        else:
            enemies.append((item.label, project(matrix, item.center)))
    return RingFrame(centers, np.asarray([key.pt for key in keys]),
                     descriptors, np.asarray(viewport, float).copy(), enemies, squad)


def scene_tracks(before, after):
    """以双向特征匹配求场景前帧到后帧单应，再双向唯一关联脚圈；失败抛 ValueError。

    单应只用于场景跟踪，不是大小地图换算。至少 30 个内点、30% 内点率及 2.5px 中位误差，
    脚圈还须落在特征支持凸包内；15px 关联距离与 12px 次选间隔防止长拖后串目标。
    返回每个后帧脚圈的前帧位置和有实测轨迹的掩码。没有轨迹的框只用逆单应估计作歧义否决，
    不能获得点击许可，避免把新入屏或上一帧漏检的竞争候选静默排除。
    """
    if (before.descriptors is None or after.descriptors is None
            or min(len(before.keys), len(after.keys)) < 30):
        raise ValueError('场景纹理不足。')
    matcher = cv2.BFMatcher()
    reverse = matcher.match(after.descriptors, before.descriptors)
    matches = []
    for pair in matcher.knnMatch(before.descriptors, after.descriptors, k=2):
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < .7 * second.distance and reverse[first.trainIdx].trainIdx == first.queryIdx:
            matches.append(first)
    if len(matches) < 30:
        raise ValueError('场景双向对应点不足。')
    source = np.float32([before.keys[item.queryIdx] for item in matches])
    destination = np.float32([after.keys[item.trainIdx] for item in matches])
    matrix, inliers = cv2.findHomography(source, destination, cv2.RANSAC, 4., maxIters=5000, confidence=.999)
    if matrix is None or inliers is None or inliers.sum() < 30 or inliers.mean() < .3:
        raise ValueError('场景帧间配准不可靠。')
    keep = inliers.ravel().astype(bool)
    if np.median(np.linalg.norm(project(matrix, source[keep]) - destination[keep], axis=1)) > 2.5:
        raise ValueError('场景帧间重投影误差过大。')
    inverse = np.linalg.inv(matrix)
    old = project(inverse, after.centers)
    tracked = np.zeros(len(after.centers), bool)
    if not len(before.centers):
        return old, tracked
    predicted = project(matrix, before.centers)
    costs = np.linalg.norm(predicted[:, None] - after.centers[None, :], axis=2)
    source_hull = cv2.convexHull(source[keep])
    destination_hull = cv2.convexHull(destination[keep])
    for j, point in enumerate(after.centers):
        i = int(np.argmin(costs[:, j]))
        if costs[i, j] > TRACK_DISTANCE or int(np.argmin(costs[i])) != j:
            continue
        alternatives = np.r_[np.delete(costs[:, j], i), np.delete(costs[i], j)]
        if len(alternatives) and alternatives.min() - costs[i, j] < TRACK_GAP:
            continue
        if (cv2.pointPolygonTest(source_hull, tuple(before.centers[i]), False) < 0
                or cv2.pointPolygonTest(destination_hull, tuple(point), False) < 0):
            continue
        old[j], tracked[j] = before.centers[i], True
    return old, tracked


def match_target_ring(before, after, target, squad, gaze):
    """判断两帧是否唯一支持原目标脚圈；返回带中文原因的报告，拒绝时不提供 click。

    target/squad 与两帧 viewport 均为章节底图像素，gaze 和返回 click 为客户区像素。
    只用观测到的沿向位移比例，不拟合完整二维换算：镜头位移限 12～450 地图像素，剩余横向
    分量不超过 16 地图像素且不超过本步的 15%，比例绝对值不超过 1。两帧均须唯一对应到原
    普通敌人且可见小队未移动；候选残差不超过 40 客户区像素，次选至少多 80px 且至少两倍。
    这些保守门限不保证泛化；无证据、残差过大或竞争候选不明确时由调用方继续原导航。
    配准接受状态和面板缩放跳变由导航调用方先行检查。
    """
    report = {'reason': '没有可比较的脚圈。'}
    if not len(before.centers) or not len(after.centers):
        return report
    target, squad, gaze = [np.asarray(value, float) for value in (target, squad, gaze)]
    if any(value.shape != (2,) or not np.isfinite(value).all()
           for value in (target, squad, gaze, before.viewport, after.viewport)):
        return dict(reason='目标或镜头坐标无效。')
    for frame in (before, after):
        nearby = [label for label, point in frame.enemies if np.linalg.norm(point - target) <= 30]
        if nearby != ['minimap_enemy_normal']:
            return dict(reason='本帧小地图无法唯一确认原普通敌人。')
        if len(frame.squad) > 1 or any(np.linalg.norm(point - squad) > 12 for point in frame.squad):
            return dict(reason='可见小队位置变化，丢弃脚圈证据。')
    step = after.viewport - before.viewport
    length = float(np.linalg.norm(step))
    if not MIN_CAMERA_STEP <= length <= MAX_CAMERA_STEP:
        return dict(reason='镜头实测位移太小或太大。', step=round(length, 1))
    remaining = target - after.viewport
    ratio = float(remaining @ step / (length * length))
    transverse = float(np.linalg.norm(remaining - ratio * step))
    report = dict(ratio=round(ratio, 3), transverse=round(transverse, 1))
    if abs(ratio) > 1 or transverse > min(MAX_TRANSVERSE, .15 * length):
        return dict(report, reason='外推幅度或未解释的横向位移过大。')
    old, tracked = scene_tracks(before, after)
    shifts = after.centers - old
    residuals = np.linalg.norm(after.centers - gaze + ratio * shifts, axis=1)
    if not np.isfinite(residuals).all():
        return dict(report, reason='脚圈运动残差不是有限值。')
    order = np.argsort(residuals)
    best = int(order[0])
    error = float(residuals[best])
    runner = float(residuals[order[1]]) if len(order) > 1 else None
    report.update(residual=round(error, 1), runner=None if runner is None else round(runner, 1))
    if not tracked[best] or np.linalg.norm(shifts[best]) < 12:
        return dict(report, reason='首选脚圈没有可靠的两帧位移证据。')
    if error > MAX_RESIDUAL or (runner is not None and (runner - error < MIN_RESIDUAL_GAP or runner < 2 * error)):
        return dict(report, reason='脚圈残差过大或候选无法区分。')
    return dict(report, reason='两帧运动唯一对应当前目标。', click=after.centers[best].tolist())
