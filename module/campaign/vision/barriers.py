"""屏障跟踪与居中定位；机关仍复用既有场景单应，光幕不得从画面边缘直接落图。

光幕由实际检测及已关联立柱在客户区帧间跟踪。只有移至注视点并连续确认后，
才能用当前小地图视口的地面坐标生成封路段；立柱单独可见不是光幕开启或消失的证据。
"""

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


def located_curtain(item, snapshot, chart):
    """用固定镜头仿射直接把光幕底部换算到底图并吸附道路，不要求先移到注视点。

    与 centered_curtain 的差别只在地面点来源：注视点坐标改为
    视口 + GROUND_TO_CLIENT 逆解的当前检测位置。仿射常数须由调用方在本会话用实测拖动
    校验（barrier_navigation.confirm_visible）；配准未接受、面板非紧凑模式或检测框截边时
    抛 ValueError。
    """
    from module.campaign.navigation.controls import GAZE, GROUND_TO_CLIENT

    if (snapshot.get('localization') != 'accepted'
            or (snapshot.get('minimap') or {}).get('mode') != 'compact'):
        raise ValueError('光幕定位缺少可信紧凑地图配准。')
    viewport = project(np.asarray(snapshot['roi_to_map'], float), GAZE)
    offset = curtain_foot(item) - np.asarray(VIEWPORT_PIXEL, float)
    point = viewport + np.linalg.solve(GROUND_TO_CLIENT, offset)
    block = chart.mark_barrier(point, tolerance=SNAP_TOLERANCE)
    if block is None:
        raise ValueError('光幕落点无法关联到有效道路。')
    return dict(kind='barrier', anchor='affine', **block)


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


def match_drag_target(point, candidates, drag=(0., 0.), gain=1.):
    """按拖动方向与大致距离关联同类 YOLO 框，返回目标及客户区位移记录。

    point、drag 均为客户区像素，gain 为最近一次实际跟手比例。只取预测位置附近的同类候选，
    不比较图像外观、旋转或缩放。允许拖动响应偏差；方向明显相反、横向偏差过大或两个候选
    同样接近时不猜测。没有新拖动时只允许 32px 检测抖动，不凭旧坐标搜索整幅画面。
    """
    point, drag = np.asarray(point, float), np.asarray(drag, float)
    if (point.shape != (2,) or drag.shape != (2,) or not np.isfinite([point, drag]).all()
            or not np.isfinite(gain) or not .2 <= gain <= 2.):
        raise ValueError('屏障跟踪的拖动坐标或跟手比例无效。')
    length = float(np.linalg.norm(drag))
    predicted = point + drag * gain
    tolerance = max(48., length * .65) if length >= 3 else TRACK_TOLERANCE
    matches = []
    for item in candidates:
        try:
            foot = curtain_foot(item)
        except ValueError:
            continue
        displacement = foot - point
        if length >= 3:
            along = float(displacement @ drag / length)
            across = float(np.linalg.norm(displacement - along * drag / length))
            if not -24 <= along <= length * 2 + 32 or across > 48:
                continue
        distance = float(np.linalg.norm(foot - predicted))
        if distance <= tolerance:
            matches.append((distance, item, foot))
    matches.sort(key=lambda result: result[0])
    if not matches:
        raise ValueError('拖动预计范围内没有同类 YOLO 屏障候选。')
    if len(matches) > 1 and matches[1][0] - matches[0][0] < 20:
        raise ValueError('拖动预计范围内有多个相近屏障候选。')
    distance, item, foot = matches[0]
    return item, dict(predicted=predicted.tolist(), observed=foot.tolist(), residual=round(distance, 1),
                      displacement=(foot - point).tolist(), drag=drag.tolist(), gain=float(gain))


def track_barrier(group, objects, drag=(0., 0.), gain=1.):
    """用本次拖动与新帧 YOLO 结果跟踪同组光幕或立柱，不再进行图像配准。

    优先取拖动预计位置附近的光幕；光幕未检出时，用事先绑定立柱的实际框位移推算光幕底部。
    双柱位移差允许 32px，单柱可短期继续；仅柱跟踪最多三帧，不能据此判光幕开启或消失。
    无拖动参数表示同视野重采样，调用方不能把不同镜头的历史坐标当作未移动帧使用。
    """
    candidates, pillars = [], []
    for item in objects:
        if item.get('kind') != 'barrier':
            continue
        try:
            curtain_foot(item)
        except ValueError:
            continue
        if item.get('state') == 'curtain':
            candidates.append(item)
        elif item.get('state') == 'pillar':
            pillars.append(item)
    try:
        item, detail = match_drag_target(group['foot'], candidates, drag, gain)
    except ValueError:
        pass
    else:
        found = barrier_group(item, objects)
        found['tracking_evidence'] = [dict(detail, target='curtain')]
        return found
    tracked, shifts, evidence, failures = [], [], [], []
    for old in group['pillars']:
        try:
            item, detail = match_drag_target(curtain_foot(old), pillars, drag, gain)
        except ValueError as exc:
            failures.append(str(exc))
            continue
        if item in tracked:
            raise ValueError('多个旧立柱关联到同一新立柱。')
        tracked.append(item)
        shifts.append(np.asarray(detail['displacement']))
        evidence.append(dict(detail, target='pillar', reference_box=old['box'], current_box=item['box']))
    if not shifts:
        reason = '；'.join(failures) or '没有已关联立柱'
        raise ValueError(f'光幕未能按拖动位置确认；立柱辅助不可用：{reason}')
    if len(shifts) == 2 and np.linalg.norm(shifts[0] - shifts[1]) > 32:
        raise ValueError('同组两根立柱的 YOLO 位移不一致。')
    predicted = np.asarray(group['foot']) + np.mean(shifts, axis=0)
    if not (3 < predicted[0] < 1773 and 120 < predicted[1] < 900):
        raise ValueError('立柱推算的光幕底部已离开有效画面。')
    source = 'pillar_pair' if len(shifts) == 2 else 'pillar'
    try:
        # 立柱给出实际移动量后，附近光幕可再关联一次，不再使用粗略的拖动比例。
        item, detail = match_drag_target(predicted, candidates)
    except ValueError:
        if any(np.linalg.norm(curtain_foot(item) - predicted) <= TRACK_TOLERANCE for item in candidates):
            raise ValueError('立柱附近光幕存在关联歧义。')
    else:
        found = barrier_group(item, objects)
        found.update(source=source, tracking_evidence=evidence)
        return found
    missing = group['missing_frames'] + 1
    if missing > MAX_PILLAR_ONLY_FRAMES:
        raise ValueError('关联立柱可见但光幕连续多帧未确认。')
    return dict(curtain=None, foot=predicted.tolist(), pillars=tracked, source=source,
                missing_frames=missing, tracking_evidence=evidence)


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
