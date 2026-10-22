"""屏障跟踪与居中定位；机关仍复用既有场景单应，光幕不得从画面边缘直接落图。

光幕由实际检测及已关联立柱在客户区帧间跟踪。只有移至注视点并连续确认后，
才能用当前小地图视口的地面坐标生成封路段；立柱单独可见不是光幕开启或消失的证据。
"""

import cv2
import numpy as np

from module.campaign.navigation.controls import ARROW_GROUND_OFFSET, EXPANDED_CENTER, VIEWPORT_PIXEL
from module.campaign.vision.perception import squad_arrow
from module.campaign.vision.surface_motion import project

# 场景投影的落点误差（视野锚点中位约 6、p90 约 16 地图像素）大于标注偏移，吸附容差单独放宽。
SNAP_TOLERANCE = 28.


def scene_chart(registration, pixel, panel_point):
    """客户区 → 地图的单应：A_INV @ ROI_TO_PLANE 换算到展开 ROI，再经紧凑面板的配准矩阵到地图。

    registration 为 match_panel 的结果（roi_to_map、a、面板尺寸），pixel 与 panel_point 是同一个
    地面点在客户区和紧凑面板里的坐标，用来钉住客户区与展开 ROI 之间的平移。
    """
    from module.campaign.navigation.controls import A_INV, ROI_TO_PLANE
    width, height = registration['panel_size']
    scale = float(registration['a'])
    to_expanded = np.array([[scale, 0, EXPANDED_CENTER[0] - scale * width / 2],
                            [0, scale, EXPANDED_CENTER[1] - scale * height / 2],
                            [0, 0, 1.]])
    affine = np.eye(3)
    affine[:2, :2] = A_INV
    panel_to_client = affine @ ROI_TO_PLANE @ to_expanded
    offset = np.asarray(pixel, float) - project(panel_to_client, panel_point)
    translation = np.eye(3)
    translation[:2, 2] = offset
    return np.asarray(registration['roi_to_map'], float) @ np.linalg.inv(translation @ panel_to_client)


def scene_anchor(image, squad, viewport):
    """选择场景→地图单应：箭头锚点优先，其次视野中心；都不可用返回 None。"""
    if squad is not None:
        arrow = squad_arrow(image)
        if arrow is not None:
            pixel = np.asarray(arrow, float) + [0., ARROW_GROUND_OFFSET]
            return scene_chart(squad, pixel, squad['player_center']), 'arrow'
    if viewport is not None:
        width, height = viewport['panel_size']
        return scene_chart(viewport, VIEWPORT_PIXEL, (width / 2, height / 2)), 'viewport'
    return None


def project_barriers(objects, image, squad, viewport=None, kinds=('barrier',)):
    """把屏障检测框的地面落点（底边中点）换算为地图坐标；没有可用锚点时返回空列表。

    光幕和立柱不在此投影：远离注视点时地面落点误差会造成错误封路，必须经过居中确认。
    kinds 可包含地面机关或电梯，以同一地面锚点提供缺标注时的临时位置。
    squad / viewport 是 observation 保存的配准结果（含 roi_to_map、a、panel_size；squad 另含
    player_center），不是裸坐标。
    """
    barriers = [item for item in objects if item['kind'] in kinds and item['kind'] != 'barrier']
    if not barriers:
        return []
    anchor = scene_anchor(image, squad, viewport)
    if anchor is None:
        return []
    chart, kind = anchor
    result = []
    for item in barriers:
        x1, y1, x2, y2 = item['box']
        mapped = project(chart, [(x1 + x2) / 2, y2])
        result.append(dict(item, map=[round(float(value), 1) for value in mapped], anchor=kind))
    return result



CENTER_TOLERANCE = 18.
TRACK_TOLERANCE = 32.


def curtain_foot(item):
    """返回有效光幕检测框的底边中点，单位为原始客户区像素。

    底边中点代表需要移到注视点的地面接触位置；截边或固定 HUD 内的检测不能用于精定位。
    """
    box = np.asarray(item.get('box'), float)
    if box.shape != (4,) or not np.isfinite(box).all():
        raise ValueError('光幕检测框坐标无效。')
    x1, y1, x2, y2 = box
    if not (3 < x1 < x2 < 1773 and 120 < y1 < y2 < 900):
        raise ValueError('光幕检测框被截断或位于固定界面区域。')
    return np.array([(x1 + x2) / 2, y2])


def track_curtain(reference, current, item, candidates):
    """以局部静态地面的双向特征配准跟踪同一光幕，歧义或支持域不足时拒绝关联。

    变换只在两张客户区截图间关联目标，不把屏幕坐标外推到底图。光幕动画区域被排除；
    目标底边必须在特征支持凸包内，候选残差不超过 32px，且次选至少多 20px。
    """
    from module.campaign.vision.surface_motion import contains, register_surface

    if not candidates:
        raise ValueError('新帧没有可关联的光幕。')
    point = curtain_foot(item)
    left, top = np.maximum(point - [260, 240], [235, 122])
    # 左侧远处目标的局部区域允许贴近画面边缘，但不包含左上小地图。
    if point[0] < 235:
        left, top = 4., max(295., point[1] - 240)
    right, bottom = np.minimum(point + [260, 180], [1648, 898])
    polygon = [[left, top], [right, top], [right, bottom], [left, bottom]]
    x1, y1, x2, y2 = item['box']
    exclude = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    fit = register_surface(reference, current, polygon, exclude=exclude)
    if not contains(fit['support'], point):
        raise ValueError('光幕底部不在静态特征支持域内。')
    predicted = project(fit['matrix'], point)
    distances = sorted((float(np.linalg.norm(curtain_foot(other) - predicted)), index)
                       for index, other in enumerate(candidates))
    if distances[0][0] > TRACK_TOLERANCE or (len(distances) > 1 and distances[1][0] - distances[0][0] < 20):
        raise ValueError('光幕帧间关联不唯一。')
    return candidates[distances[0][1]]


def centered_curtain(item, snapshot, chart):
    """仅把注视点附近的光幕落到已接受的紧凑地图视口，再按局部道路方向生成封锁段。

    不使用屏幕边缘的单应外推或远处立柱方向。底部距注视点最多 18 客户区像素；
    以当前紧凑地图的已标定注视点作为地面坐标，离道路过远仍拒绝吸附。
    """
    from module.campaign.navigation.controls import GAZE

    if (snapshot.get('localization') != 'accepted'
            or (snapshot.get('minimap') or {}).get('mode') != 'compact'):
        raise ValueError('光幕居中后缺少可信紧凑地图配准。')
    if np.linalg.norm(curtain_foot(item) - VIEWPORT_PIXEL) > CENTER_TOLERANCE:
        raise ValueError('光幕底部尚未进入注视点范围。')
    point = project(snapshot['roi_to_map'], GAZE)
    block = chart.mark_barrier(point, tolerance=SNAP_TOLERANCE)
    if block is None:
        raise ValueError('居中光幕无法关联到有效道路。')
    return dict(kind='barrier', anchor='centered', **block)


MAX_PILLAR_ONLY_FRAMES = 3


def associate_pillars(curtain, objects):
    """只将光幕左右端附近且唯一的立柱绑定到该光幕，不从下一帧临时选择最近立柱。

    坐标为客户区像素；柱脚允许随道路斜向分布。每端最多一根，竞争候选接近时该端不绑定。
    立柱同时贴近另一光幕时不参与辅助，避免密集屏障串组。
    """
    x1, y1, x2, y2 = curtain['box']
    width, height = x2 - x1, y2 - y1
    candidates = []
    for item in objects:
        if item.get('kind') != 'barrier' or item.get('state') != 'pillar':
            continue
        try:
            foot = curtain_foot(item)
        except ValueError:
            continue
        side = 0 if foot[0] < (x1 + x2) / 2 else 1
        edge = x1 if side == 0 else x2
        distance = abs(foot[0] - edge)
        if distance > max(30., width * .3) or not y1 - height * .2 <= foot[1] <= y2 + height * .65:
            continue
        competing = False
        for other in objects:
            if other is curtain or other.get('state') != 'curtain':
                continue
            ox1, oy1, ox2, oy2 = other['box']
            if (oy1 - 20 <= foot[1] <= oy2 + 55
                    and min(abs(foot[0] - ox1), abs(foot[0] - ox2)) <= distance + 12):
                competing = True
        if not competing:
            candidates.append((side, distance, item))
    associated = []
    for side in (0, 1):
        matches = sorted((candidate for candidate in candidates if candidate[0] == side), key=lambda x: x[1])
        if matches and (len(matches) == 1 or matches[1][1] - matches[0][1] >= 15):
            associated.append(matches[0][2])
    return associated


def barrier_group(curtain, objects):
    """从实际看见的光幕建立跟踪组；柱脚与光幕底部偏移只绑定于该帧客户区。"""
    return dict(curtain=curtain, foot=curtain_foot(curtain).tolist(),
                pillars=associate_pillars(curtain, objects), source='curtain', missing_frames=0)


def track_pillar(reference, current, pillar, candidates):
    """以旧立柱的实际外观匹配新帧检测候选，拒绝弱纹理、尺度突变或外观竞争。

    检测框只限定搜索域；位移由局部模板匹配得到，不将最近框当成同一立柱。
    单帧位移不得超过 400px，避免原立柱离屏后错误关联到远处同样外观的立柱。
    允许 10% 尺度变化，NCC 至少 0.78，次选差至少 0.08；回传的是客户区位移。
    """
    x1, y1, x2, y2 = np.rint(pillar['box']).astype(int)
    template = cv2.cvtColor(reference[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    if min(template.shape) < 8 or template.std() < 8:
        raise ValueError('立柱外观纹理不足。')
    matches = []
    for item in candidates:
        try:
            if np.linalg.norm(curtain_foot(item) - curtain_foot(pillar)) > 400:
                continue
        except ValueError:
            continue
        a, b, c, d = np.rint(item['box']).astype(int)
        if abs((c - a) / (x2 - x1) - 1.) > .25 or abs((d - b) / (y2 - y1) - 1.) > .25:
            continue
        left, top = max(0, a - 16), max(0, b - 16)
        patch = cv2.cvtColor(current[top:min(999, d + 16), left:min(1776, c + 16)], cv2.COLOR_BGR2GRAY)
        best = None
        for scale in (.9, 1., 1.1):
            resized = cv2.resize(template, None, fx=scale, fy=scale)
            if any(x > y for x, y in zip(resized.shape, patch.shape)):
                continue
            _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(patch, resized, cv2.TM_CCOEFF_NORMED))
            center = np.array([left, top]) + location + np.array(resized.shape[::-1]) / 2
            if best is None or score > best[0]:
                best = (score, center)
        if best is not None:
            matches.append((best[0], item, best[1] - [(x1 + x2) / 2, (y1 + y2) / 2]))
    matches.sort(key=lambda match: match[0], reverse=True)
    if (not matches or matches[0][0] < .78
            or (len(matches) > 1 and matches[0][0] - matches[1][0] < .08)):
        raise ValueError('关联立柱的外观匹配不足或存在歧义。')
    _, item, displacement = matches[0]
    return item, displacement


def track_barrier(reference, current, group, objects):
    """优先用已绑定立柱传递同组地面位置，光幕变弱时允许有限帧继续跟踪，但不伪造光幕状态。

    双柱的位移必须一致，单柱只能作短期跟踪。重新出现的光幕还须接近组预测位置且唯一；
    立柱和光幕都无法可靠关联时拒绝继续。仅柱可见最多三帧，最终封路仍要求实际光幕两帧确认。
    """
    candidates = [item for item in objects if item.get('kind') == 'barrier' and item.get('state') == 'curtain']
    pillars = [item for item in objects if item.get('kind') == 'barrier' and item.get('state') == 'pillar']
    tracked, shifts = [], []
    for old in group['pillars']:
        try:
            item, shift = track_pillar(reference, current, old, pillars)
        except (ValueError, cv2.error):
            continue
        if item in tracked:
            raise ValueError('多个旧立柱关联到同一新立柱。')
        tracked.append(item)
        shifts.append(shift)
    if shifts:
        if len(shifts) == 2 and np.linalg.norm(shifts[0] - shifts[1]) > 12:
            raise ValueError('同组两根立柱的位移不一致。')
        predicted = np.asarray(group['foot']) + np.mean(shifts, axis=0)
        if not (3 < predicted[0] < 1773 and 120 < predicted[1] < 900):
            raise ValueError('立柱关联推算的光幕底部已离开有效画面。')
        distances = []
        for item in candidates:
            try:
                distances.append((float(np.linalg.norm(curtain_foot(item) - predicted)), item))
            except ValueError:
                continue
        distances.sort(key=lambda value: value[0])
        if distances and distances[0][0] <= TRACK_TOLERANCE:
            if len(distances) > 1 and distances[1][0] - distances[0][0] < 20:
                raise ValueError('立柱关联范围内有多个光幕。')
            found = barrier_group(distances[0][1], objects)
            found['source'] = 'pillar_pair' if len(shifts) == 2 else 'pillar'
            return found
        missing = group['missing_frames'] + 1
        if missing > MAX_PILLAR_ONLY_FRAMES:
            raise ValueError('关联立柱可见但光幕连续多帧未确认。')
        return dict(curtain=None, foot=predicted.tolist(), pillars=tracked,
                    source='pillar_pair' if len(shifts) == 2 else 'pillar', missing_frames=missing)
    if group['curtain'] is None:
        raise ValueError('光幕与已关联立柱均未能可靠跟踪。')
    found = track_curtain(reference, current, group['curtain'], candidates)
    return barrier_group(found, objects)


def weak_curtain_visible(image, group):
    """在已关联立柱确定的中央区域以 0.2 置信度复查光幕，仅用于否决消失结论。

    不修改进程共享检测器阈值，不产生正向封路坐标；原 640² 切片和全类别 argmax 保持不变。
    任何低分光幕落在目标附近都视为状态未确认，不能因为常规 0.5 阈值漏检而解除封锁。
    """
    from module.campaign.vision.detection import decode, detector, grid_tiles, letterbox

    model = detector()
    model.selected(('scene_barrier_curtain',))
    point = np.asarray(group['foot'])
    for tile, offset, guards in grid_tiles(image):
        left, top = offset
        if not (left <= point[0] + 100 and left + 640 >= point[0] - 100
                and top <= point[1] and top + 640 >= point[1] - 150):
            continue
        tensor, scales, padding = letterbox(tile, model.size)
        with model.lock:
            output = model.session.run([model.output_name], {model.input_name: tensor})[0]
        for item in decode(output, scales, padding, {'scene_barrier_curtain': .2},
                           tile.shape, model.labels, guards):
            x1, y1, x2, y2 = item.box
            foot = np.array([(x1 + x2) / 2 + left, y2 + top])
            if np.linalg.norm(foot - point) <= 100:
                return True
    return False
