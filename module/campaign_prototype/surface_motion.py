"""局部道路的图像配准与场景落点标定；坐标始终绑定参考图和区域，不假设全图共面。"""

import cv2
import numpy as np

from dev_tools.minimap_reconstruct import terrain
from .perception import detect_markers


def project(matrix, points):
    """转换一个或多个二维点，拒绝非有限输入与投影地平线。

    支持单点 (2,) 或批量点 (N,2)，输入矩阵必须为可逆 3×3。
    返回与点数组同形状的投影坐标；任何点的齐次分母接近零都使整个调用失败。
    """
    matrix, points = np.asarray(matrix, float), np.asarray(points, float)
    if (matrix.shape != (3, 3) or points.shape[-1:] != (2,) or points.ndim not in (1, 2)
            or not np.isfinite(matrix).all() or not np.isfinite(points).all()
            or abs(np.linalg.det(matrix)) < 1e-10):
        raise ValueError('Invalid surface transform')
    homogeneous = np.column_stack([points.reshape(-1, 2), np.ones(points.size // 2)]) @ matrix.T
    if np.any(np.abs(homogeneous[:, 2]) < 1e-6):
        raise ValueError('Surface point lies on the horizon')
    return (homogeneous[:, :2] / homogeneous[:, 2:]).reshape(points.shape)


def contains(polygon, point):
    """点必须位于有观测支持的凸区域内，不将局部变换外推到相邻高度。

    polygon 为至少三个有限二维顶点，point 与其使用相同坐标单位。
    边界点也返回 True；函数不检查地形身份，调用者必须传入对应表面的观测支持多边形。
    """
    polygon, point = np.asarray(polygon, np.float32), np.asarray(point, float)
    if (polygon.ndim != 2 or polygon.shape[1] != 2 or len(polygon) < 3 or point.shape != (2,)
            or not np.isfinite(polygon).all() or not np.isfinite(point).all()):
        raise ValueError('Invalid surface domain')
    return cv2.pointPolygonTest(polygon, tuple(point), False) >= 0


def fit_registration(source, destination):
    """先用三分之二角点拟合，剩余角点独立验证；整块多平面冲突不能靠删验证点通过。

    source/destination 为至少八组同序二维对应点，每三点留出一点验证。
    训练 RANSAC 与留出误差通过后才重拟合，返回单应、源支持凸包及误差统计；不删掉失败验证点来强行通过。
    """
    source, destination = np.asarray(source, float), np.asarray(destination, float)
    if (source.ndim != 2 or source.shape[1] != 2 or source.shape != destination.shape
            or len(source) < 8 or not np.isfinite(source).all() or not np.isfinite(destination).all()):
        raise ValueError('Insufficient surface correspondences')
    validation = np.arange(len(source)) % 3 == 0
    matrix, inliers = cv2.findHomography(source[~validation], destination[~validation], cv2.RANSAC, 2)
    if matrix is None or inliers.mean() < .8:
        raise ValueError('Inconsistent surface registration')
    error = np.linalg.norm(project(matrix, source) - destination, axis=1)
    if np.max(error[validation]) > 3 or np.percentile(error, 90) > 3:
        raise ValueError('Surface registration failed held-out validation')
    keep = error <= 3
    final, _ = cv2.findHomography(source[keep], destination[keep], 0)
    if final is None:
        raise ValueError('Degenerate surface registration')
    return {'matrix': final.tolist(), 'support': cv2.convexHull(source[keep].astype(np.float32))[:, 0].tolist(),
            'points': len(source), 'validation_points': int(validation.sum()),
            'validation_max_px': float(error[validation].max()), 'p90_px': float(np.percentile(error, 90))}


def register_surface(reference, current, polygon, minimap=False, exclude=None):
    """配准给定道路区域，以双向特征对应和留出点验证约束参考图到当前图的变换。

    reference/current 必须为同尺寸 BGR 图，polygon 和 exclude 均使用参考图像素。
    场景纹理使用双向 SIFT 匹配，小地图使用道路掩码上的双向光流；合格对应点统一经留出验证返回参考→当前变换。
    """
    if (reference is None or current is None or reference.shape != current.shape
            or reference.ndim != 3 or reference.shape[2] != 3):
        raise ValueError('Surface images must have the same BGR dimensions')
    polygon = np.asarray(polygon, float)
    contains(polygon, polygon[0])
    if np.any(polygon < 0) or np.any(polygon >= reference.shape[1::-1]):
        raise ValueError('Surface polygon lies outside the reference image')
    mask = np.zeros(reference.shape[:2], np.uint8)
    cv2.fillPoly(mask, [np.rint(polygon).astype(np.int32)], 255)
    if exclude is not None:
        cv2.fillPoly(mask, [np.rint(exclude).astype(np.int32)], 0)
    if minimap:
        first, second = [cv2.GaussianBlur(cv2.morphologyEx(terrain(im), cv2.MORPH_OPEN,
                                                        np.ones((3, 3), np.uint8)), (5, 5), 1)
                         for im in (reference, current)]
        players, _ = detect_markers(reference, np.eye(3))
        for player in players:
            cv2.circle(mask, tuple(np.rint(player).astype(int)), 28, 0, -1)
    else:
        first, second = [cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) for im in (reference, current)]
        sift = cv2.SIFT_create(contrastThreshold=.015)
        source_keys, source_features = sift.detectAndCompute(first, mask)
        target_keys, target_features = sift.detectAndCompute(second, None)
        if source_features is None or target_features is None or len(target_features) < 2:
            raise ValueError('Too few surface corners')
        matcher = cv2.BFMatcher()
        reverse = matcher.match(target_features, source_features)
        matches = [a for a, b in matcher.knnMatch(source_features, target_features, k=2)
                   if a.distance < b.distance * .6 and reverse[a.trainIdx].trainIdx == a.queryIdx]
        source = [source_keys[match.queryIdx].pt for match in matches]
        destination = [target_keys[match.trainIdx].pt for match in matches]
        return fit_registration(source, destination)
    # 较大的角点窗口和质量门槛排除二值直边上的台阶伪角点，避免沿直边滑动的孔径歧义。
    points = cv2.goodFeaturesToTrack(first, 300, .12 if minimap else .02, 9, mask=mask,
                                    blockSize=9 if minimap else 5)
    if points is None or len(points) < 8:
        raise ValueError('Too few surface corners')
    options = dict(winSize=(31, 31), maxLevel=4, criteria=(3, 60, .001))
    moved, ok, error = cv2.calcOpticalFlowPyrLK(first, second, points, None, **options)
    if moved is None:
        raise ValueError('Surface tracking failed')
    back, reverse, _ = cv2.calcOpticalFlowPyrLK(second, first, moved, None, **options)
    if back is None:
        raise ValueError('Reverse surface tracking failed')
    keep = ((ok[:, 0] > 0) & (reverse[:, 0] > 0) & (error[:, 0] < 15)
            & (np.linalg.norm(points[:, 0] - back[:, 0], axis=1) < .6)
            & np.all(moved[:, 0] >= 0, axis=1)
            & np.all(moved[:, 0] < current.shape[1::-1], axis=1))
    return fit_registration(points[keep, 0], moved[keep, 0])


def fit_calibration(training, validation, training_limit=6, validation_limit=8, map_error_limits=None):
    """用实际小队落点拟合局部仿射变换；独立停靠样本和可插值范围同时限制使用域。

    样本形状为 (N,2,2)，每对依次是地图或参考 ROI 位移、客户区位移，至少六个训练和三个验证样本。
    验证点必须在训练凸包内；默认按客户区像素限制误差，传 map_error_limits 后按逆投影地图误差验收。
    """
    training, validation = np.asarray(training, float), np.asarray(validation, float)
    for values, minimum in ((training, 6), (validation, 3)):
        if (values.ndim != 3 or values.shape[1:] != (2, 2) or len(values) < minimum
                or not np.isfinite(values).all()):
            raise ValueError('Expected independent minimap/field sample pairs')
    source, destination = training[:, 0], training[:, 1]
    centered = source - source.mean(axis=0)
    if np.linalg.matrix_rank(centered) < 2 or np.linalg.cond(centered) > 10:
        raise ValueError('Calibration samples do not cover two directions')
    matrix = np.eye(3)
    matrix[:2] = np.linalg.lstsq(np.column_stack([source, np.ones(len(source))]), destination, rcond=None)[0].T
    training_error = np.linalg.norm(project(matrix, source) - destination, axis=1)
    validation_error = np.linalg.norm(project(matrix, validation[:, 0]) - validation[:, 1], axis=1)
    map_metrics = {}
    if map_error_limits is None:
        if training_error.max() > training_limit or validation_error.max() > validation_limit:
            raise ValueError('Surface click calibration failed independent validation')
    else:
        inverse = np.linalg.inv(matrix)
        training_map = np.linalg.norm(project(inverse, destination) - source, axis=1)
        validation_map = np.linalg.norm(project(inverse, validation[:, 1]) - validation[:, 0], axis=1)
        if training_map.max() > map_error_limits[0] or validation_map.max() > map_error_limits[1]:
            raise ValueError('Local movement calibration exceeds map arrival tolerance')
        map_metrics = dict(training_map_max_px=float(training_map.max()),
                           validation_map_max_px=float(validation_map.max()), map_error_limits=list(map_error_limits))
    support = cv2.convexHull(source.astype(np.float32))[:, 0]
    if any(not contains(support, p) for p in validation[:, 0]):
        raise ValueError('Calibration validation leaves the sampled surface')
    return {'matrix': matrix.tolist(), 'support': support.tolist(), 'training_samples': len(training),
            'validation_samples': len(validation), 'training_max_px': float(training_error.max()),
            'training_limit_px': training_limit, 'validation_limit_px': validation_limit,
            'validation_max_px': float(validation_error.max()), **map_metrics}


def plan_click(target, surface_id, calibration, registration, client=(1776, 999)):
    """按目标道路自己的标定生成场景落点，不借用小队所在高度的 Jacobian 或二维交叉连通性。

    target 使用标定输入坐标，surface_id 必须匹配，且目标位于标定及图像配准两级支持域中。
    返回固定尺寸客户区的安全落点；表面不一致、域外或落点接近 UI 时失败，不发送实际点击。
    """
    if calibration.get('surface_id') != surface_id:
        raise ValueError('Target and click calibration belong to different surfaces')
    if not contains(calibration['support'], target):
        raise ValueError('Target lies outside the calibrated surface')
    field_reference = project(calibration['matrix'], target)
    if not contains(registration['support'], field_reference):
        raise ValueError('Target lacks current field registration support')
    click = project(registration['matrix'], field_reference)
    if tuple(client) != (1776, 999) or not (250 <= click[0] <= 1500 and 180 <= click[1] <= 880):
        raise ValueError('Target lies outside the validated field area')
    return click


def plan_map_click(geometry, target, surface_id, image_sha256, calibration, registration, client=(1776, 999)):
    """地图目标先按自己的高度返回标定视角，再使用该表面的实测落点标定。

    先核对标定和配准的底图哈希及表面身份，再把地图目标投回标定来源帧。
    使用该表面的 plan_click 生成客户区坐标；任一证据属于其他地图或高度时拒绝组合。
    """
    if (not image_sha256 or calibration.get('image_sha256') != image_sha256
            or registration.get('image_sha256') != image_sha256):
        raise ValueError('Surface movement evidence belongs to a different map')
    if calibration.get('surface_id') != surface_id or registration.get('surface_id') != surface_id:
        raise ValueError('Movement target and evidence belong to different surfaces')
    reference_roi = geometry.roi_point(calibration.get('source_frame'), surface_id, target)
    return plan_click(reference_roi, surface_id, calibration, registration, client)
