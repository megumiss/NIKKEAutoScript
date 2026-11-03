"""屏障的预标注关联与状态跟踪；机关仍复用既有场景单应。

检测投影只选择最近光幕标注，封路使用标注几何，不从检测框估算封锁落点。
光幕由实际检测及已关联立柱在客户区帧间跟踪；仅在已触发机关的切镜阶段用双柱方向校验解锁对象。
"""

import numpy as np

from module.campaign.navigation.controls import (ARROW_GROUND_OFFSET, EXPANDED_CENTER, GAZE,
                                                 GROUND_TO_CLIENT, VIEWPORT_PIXEL)
from module.campaign.vision.chapter import CLIENT_SIZE
from module.campaign.vision.mechanisms import ANNOTATION_GAP
from module.campaign.vision.perception import squad_arrow
from module.campaign.vision.surface_motion import project

# 场景投影的落点误差（视野锚点中位约 6、p90 约 16 地图像素）大于标注偏移，吸附容差单独放宽。
SNAP_TOLERANCE = 28.
# 沿用客户区顶部栏、小地图与任务栏的标定区域；光幕只提供观测，不套用点击用的整条底部禁区。
BARRIER_HUD_RECTS = ((0, 0, 1776, 75), (0, 75, 245, 310), (1490, 75, 1776, 205))


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

    光幕和立柱不在此投影：其检测位置只用于预标注关联，不能直接外推成道路封锁。
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


def affine_project(objects, registration, kinds=('ground_mechanism',)):
    """箭头锚点缺失时的回退投影：固定镜头仿射把检测框底边中点换算到底图。

    视口底图坐标取本帧可信紧凑配准的 GAZE 投影，检测框偏移经 GROUND_TO_CLIENT 逆解，
    与光幕预标注关联使用相同的坐标换算。投影只用于将机关开关状态关联到最近的预标注机关，
    不作为导航落点或封路依据；关联歧义与检测冲突由 mechanism_observations 拒绝，
    registration 必须含本帧的 roi_to_map，不从历史视口套用投影矩阵。
    """
    items = [item for item in objects if item['kind'] in kinds]
    if not items:
        return []
    viewport = project(np.asarray(registration['roi_to_map'], float), np.asarray(GAZE, float))
    result = []
    for item in items:
        x1, y1, x2, y2 = item['box']
        offset = np.array([(x1 + x2) / 2, y2], float) - np.asarray(VIEWPORT_PIXEL, float)
        mapped = viewport + np.linalg.solve(GROUND_TO_CLIENT, offset)
        result.append(dict(item, map=[round(float(value), 1) for value in mapped], anchor='affine'))
    return result



CENTER_TOLERANCE = 18.
TRACK_TOLERANCE = 32.


def curtain_foot(item):
    """返回有效光幕检测框的底边中点，单位为原始客户区像素。

    底边中点代表地面接触位置；光幕及立柱框必须完整位于客户区，且不与标定 HUD 矩形相交。
    顶部栏下方的中央场景可以参与识别，不把整条顶部 120px 当成 HUD；底部仍只检查实际边界，
    不套用点击区域的整条底部禁区。坐标无效、截边或被 HUD 遮挡时抛 ValueError。
    """
    box = np.asarray(item.get('box'), float)
    if box.shape != (4,) or not np.isfinite(box).all():
        raise ValueError('光幕检测框坐标无效。')
    x1, y1, x2, y2 = box
    if not (3 < x1 < x2 < CLIENT_SIZE[0] - 3 and 3 < y1 < y2 < CLIENT_SIZE[1]):
        raise ValueError('光幕检测框被截断或位于固定界面区域。')
    if any(x1 < right and x2 > left and y1 < bottom and y2 > top
           for left, top, right, bottom in BARRIER_HUD_RECTS):
        raise ValueError('光幕检测框被固定界面遮挡。')
    return np.array([(x1 + x2) / 2, y2])


def unique_curtains(objects):
    """合并高度重叠的重复光幕检测框，按置信度保留同一实体的候选。

    两个框的交集占较小组面积至少 80% 时视为重复检测，优先处理高置信度框可保留较高分候选。
    空间上分离的框不因竞争同一预标注而合并，后续唯一关联检查仍会拒绝这种真实歧义。
    坐标无效的候选原样保留，交由 curtain_foot 按既有规则排除。
    """
    ordered = sorted((item for item in objects
                      if item.get('kind') == 'barrier' and item.get('state') == 'curtain'),
                     key=lambda item: float(item.get('confidence') or 0), reverse=True)
    unique = []
    for item in ordered:
        box = np.asarray(item.get('box'), float)
        if box.shape != (4,) or not np.isfinite(box).all():
            unique.append(item)
            continue
        duplicate = False
        for other in unique:
            other_box = np.asarray(other.get('box'), float)
            if other_box.shape != (4,) or not np.isfinite(other_box).all():
                continue
            width = max(0., min(box[2], other_box[2]) - max(box[0], other_box[0]))
            height = max(0., min(box[3], other_box[3]) - max(box[1], other_box[1]))
            smaller = min(max(0., box[2] - box[0]) * max(0., box[3] - box[1]),
                          max(0., other_box[2] - other_box[0]) * max(0., other_box[3] - other_box[1]))
            if smaller > 0 and width * height / smaller >= .8:
                duplicate = True
                break
        if not duplicate:
            unique.append(item)
    return unique


def annotated_curtains(snapshot, chart):
    """把实际检测到的光幕按最近预标注位置关联，返回标注封锁几何与同帧检测证据。

    必须有本帧已接受的紧凑配准，投影只用于选择标注，不作为最终封路位置；不设绝对距离上限。
    高度重叠的重复检测先合并；最近与次近距离差小于 ANNOTATION_GAP、空间上分离的多个光幕
    竞争同一标注时仍不返回该关联。立柱单独可见不构成光幕存在证据；两点标注直接作为封锁段，
    中心点标注按当地道路估计切口。
    """
    annotations = getattr(chart, 'barrier_annotations', [])
    if (not annotations or snapshot.get('localization') != 'accepted' or snapshot.get('roi_to_map') is None
            or (snapshot.get('minimap') or {}).get('mode') != 'compact'):
        return []
    viewport = project(np.asarray(snapshot['roi_to_map'], float), np.asarray(GAZE, float))
    candidates = []
    for item in unique_curtains(snapshot.get('objects', [])):
        try:
            offset = curtain_foot(item) - np.asarray(VIEWPORT_PIXEL, float)
        except ValueError:
            continue
        position = viewport + np.linalg.solve(GROUND_TO_CLIENT, offset)
        if not np.isfinite(position).all():
            continue
        ordered = sorted(((float(np.linalg.norm(position - np.asarray(entry['position']))), entry)
                          for entry in annotations), key=lambda pair: pair[0])
        if len(ordered) > 1 and ordered[1][0] - ordered[0][0] < ANNOTATION_GAP:
            continue
        annotation = ordered[0][1]
        segment = annotation['segment']
        if segment is None:
            inferred = chart.mark_barrier(annotation['position'], tolerance=SNAP_TOLERANCE)
            if inferred is None:
                continue
            segment = inferred['segment']
        candidates.append(dict(kind='barrier', anchor='annotation', annotation_id=annotation['id'],
                               point=list(annotation['position']), segment=segment, detection=item,
                               projected_point=position.tolist()))
    return [item for item in candidates
            if sum(other['annotation_id'] == item['annotation_id'] for other in candidates) == 1]


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


def centered_pillar_axis(objects):
    """从本帧 YOLO 立柱检测中选择靠近镜头中央的相邻双柱，返回柱脚连线证据。

    柱脚取客户区检测框底边中点，沿用模型门限与 HUD／截边检查；不手工选点或降低置信度。
    先排除绑定到仍可见能量幕的立柱，再按相邻关系配对：另一柱同时更靠近两端时不跨柱连线。
    双柱中点离注视点最近者优先，不设置距离或长度门限。缺柱、重合或中央配对歧义时抛 ValueError。
    本方法只校验已触发机关的解锁对象，不能凭孤立立柱发布光幕消失证据。
    """
    occupied = {id(pillar) for curtain in unique_curtains(objects)
                for pillar in associate_pillars(curtain, objects)}
    pillars, feet = [], []
    for item in objects:
        if item.get('kind') != 'barrier' or item.get('state') != 'pillar' or id(item) in occupied:
            continue
        try:
            foot = curtain_foot(item)
        except ValueError:
            continue
        if any(np.linalg.norm(foot - point) < 1e-6 for point in feet):
            continue
        pillars.append(item)
        feet.append(foot)
    pairs = []
    for first in range(len(feet)):
        for second in range(first + 1, len(feet)):
            span = np.linalg.norm(feet[second] - feet[first])
            if any(np.linalg.norm(point - feet[first]) < span and np.linalg.norm(point - feet[second]) < span
                   for index, point in enumerate(feet) if index not in (first, second)):
                continue
            midpoint = (feet[first] + feet[second]) / 2
            pairs.append((float(np.linalg.norm(midpoint - VIEWPORT_PIXEL)), first, second, midpoint))
    pairs.sort(key=lambda item: item[0])
    if not pairs:
        raise ValueError('机关切镜中央缺少可用于方向校验的 YOLO 双柱。')
    if len(pairs) > 1 and abs(pairs[1][0] - pairs[0][0]) < 1e-6:
        raise ValueError('机关切镜中央存在等距双柱配对，无法唯一校验光幕方向。')
    distance, first, second, midpoint = pairs[0]
    return dict(pillars=[pillars[first], pillars[second]], feet=[feet[first].tolist(), feet[second].tolist()],
                midpoint=midpoint.tolist(), center_distance=distance,
                vector=(feet[second] - feet[first]).tolist())


def mechanism_barrier_candidates(annotations, viewport):
    """按切镜视野距离排列光幕标注，返回最近两候选及是否需要双柱方向校验。

    距离只排序，不设置接受半径或最小分差。单标注直接匹配；两点标注的中心间距不超过
    两者标注长度之和时视为邻近竞争，否则最近光幕独立匹配，远处对象不参与方向否决。
    邻近判断随标注尺寸变化，不新增固定像素阈值。多标注缺两点几何时不能证明相互独立，
    交给后续方向校验拒绝；视野或标注中心非法、没有标注时抛 ValueError。
    """
    position = np.asarray(viewport, float)
    if position.shape != (2,) or not np.isfinite(position).all():
        raise ValueError('机关切镜视野中心坐标无效。')
    ordered = []
    for item in annotations:
        point = np.asarray(item.get('position'), float)
        if point.shape != (2,) or not np.isfinite(point).all():
            raise ValueError('机关解锁光幕标注中心坐标无效。')
        ordered.append((float(np.linalg.norm(point - position)), item))
    ordered.sort(key=lambda pair: pair[0])
    if not ordered:
        raise ValueError('本章缺少光幕标注，保留原封锁状态。')
    nearest = ordered[:2]
    if len(nearest) == 1:
        return nearest, False
    spans = []
    for _, item in nearest:
        segment = np.asarray(item.get('segment'), float)
        if segment.shape != (2, 2) or not np.isfinite(segment).all():
            return nearest, True
        length = float(np.linalg.norm(segment[1] - segment[0]))
        if length < 1e-6:
            return nearest, True
        spans.append(length)
    spacing = np.linalg.norm(np.asarray(nearest[0][1]['position']) - nearest[1][1]['position'])
    return nearest, bool(spacing <= sum(spans))


def match_mechanism_barrier(annotations, viewport, objects=()):
    """按距离匹配切镜后的最近光幕标注，仅邻近竞争候选使用 YOLO 双柱方向校验。

    距离只排序，不设接受半径或最小分差；单标注及远离其他光幕的最近标注直接匹配。
    邻近竞争候选先选最近者，再将两点标注经 GROUND_TO_CLIENT 换算到客户区，比较无向夹角；
    不混用画面与底图斜率，也不设置角度门限。最近者方向误差不得大于次近者，同向时仍由距离决定。
    不按斜率改选远处对象；缺双柱、缺两点标注或方向冲突均抛 ValueError，调用方保留机关与
    封锁状态。本方法只读取标注和检测对象，不以孤立立柱证明光幕消失。
    """
    ordered, competing = mechanism_barrier_candidates(annotations, viewport)
    candidates = [dict(id=item['id'], distance=distance) for distance, item in ordered]
    evidence = dict(method='single_annotation' if len(ordered) == 1 else 'nearest_annotation', candidates=candidates)
    if not competing:
        return ordered[0][1], evidence
    axis = centered_pillar_axis(objects)
    observed = np.asarray(axis['vector'], float)
    observed /= np.linalg.norm(observed)
    for candidate, (_, item) in zip(candidates, ordered):
        segment = np.asarray(item.get('segment'), float)
        if segment.shape != (2, 2) or not np.isfinite(segment).all():
            raise ValueError(f"光幕 {item['id']} 缺少两点标注，无法校验双柱方向。")
        expected = GROUND_TO_CLIENT @ (segment[1] - segment[0])
        length = np.linalg.norm(expected)
        if length < 1e-6:
            raise ValueError(f"光幕 {item['id']} 的标注端点重合，无法校验双柱方向。")
        cosine = abs(float(observed @ (expected / length)))
        candidate['angle_error'] = float(np.degrees(np.arccos(np.clip(cosine, 0., 1.))))
    if candidates[0]['angle_error'] > candidates[1]['angle_error'] + 1e-6:
        raise ValueError(f"最近光幕 {candidates[0]['id']} 与立柱方向冲突："
                         f"夹角 {candidates[0]['angle_error']:.1f}°，"
                         f"次近光幕 {candidates[1]['id']} 夹角 {candidates[1]['angle_error']:.1f}°；"
                         '保留原封锁状态。')
    evidence.update(method='distance_pillar_axis', pillar_axis=axis)
    return ordered[0][1], evidence


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
    if not (3 < predicted[0] < CLIENT_SIZE[0] - 3 and 3 < predicted[1] < CLIENT_SIZE[1]):
        raise ValueError('立柱推算的光幕底部已离开有效画面。')
    if any(left <= predicted[0] < right and top <= predicted[1] < bottom
           for left, top, right, bottom in BARRIER_HUD_RECTS):
        raise ValueError('立柱推算的光幕底部已进入固定界面区域。')
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

    不修改进程共享检测器阈值，不产生正向封路坐标；复用本帧光幕增强输入的场景输出，
    在场景全部类别中判类后使用独立低阈值。任何低分光幕落在目标附近都视为状态未确认，
    不能因为常规 0.5 阈值漏检而解除封锁。
    """
    from module.campaign.vision.detection import decode, detector

    model = detector('scene')
    point = np.asarray(group['foot'])
    output, scales, padding = model.infer(image, enhance_curtain=True)
    items = decode(output, scales, padding, {'scene_barrier_curtain': .2},
                   image.shape, model.labels, guards=(False,) * 4)
    for item in items:
        x1, y1, x2, y2 = item.box
        foot = np.array([(x1 + x2) / 2, y2])
        if np.linalg.norm(foot - point) <= 100:
            return True
    return False
