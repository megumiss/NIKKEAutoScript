"""光幕按预标注位置确认与复核：视觉证据充分前不改变可通行路网。"""

import cv2
import numpy as np

from module.campaign.navigation import controls as goto
from module.campaign.navigation.camera_navigation import pan_scene, target_view_offset
from module.campaign.vision.barriers import (CENTER_TOLERANCE, annotated_curtains, barrier_group, track_barrier,
                                             weak_curtain_visible)
from module.campaign.vision.offscreen import squad_offscreen
from module.campaign.vision.perception import squad_arrow
from module.campaign.vision.surface_motion import project
from module.logger import logger

MAX_PANS = 8
MAX_CURTAINS = 4
# 光幕消失复核的单次拖动上限，单位为客户区像素；不用于首次确认或估算封锁位置。
PAN_DRAG_MAX = 560.


class BarrierUnconfirmed(RuntimeError):
    """光幕标注关联或视觉复核未通过，调用方保留既有路网且不发送未经确认的移动点击。"""


def curtains(snapshot):
    """取当前帧的光幕候选，不包含立柱；截边候选仍保留，不能当作已确认不存在。"""
    return [item for item in snapshot.get('objects', [])
            if item.get('kind') == 'barrier' and item.get('state') == 'curtain']


def capture(session, squad):
    """重新核对野外安全状态并取得同帧视觉结果，只沿用传入的小队底图坐标。"""
    field = session.win.client()
    session.identity(field)
    return field, session.observe_frame(field, known_squad=squad)


def pan(session, field, drag):
    """只为复核已标注光幕平移镜头，按本帧小队箭头或离屏标记避开鼠标按下区域。

    不用历史光幕投影或未经校验的坐标猜测小队脚点；两种小队视觉依据均缺失时停止拖动。
    窗口、取消、弹窗和输入预算检查仍由原会话及 pan_scene 执行。
    """
    arrow = squad_arrow(field)
    if arrow is not None:
        ground = np.asarray(arrow) + [0., goto.ARROW_GROUND_OFFSET]
    elif squad_offscreen(field) is not None:
        ground = None
    else:
        raise BarrierUnconfirmed('无法确定安全拖动区域，停止光幕复核；未发送移动点击。')
    pan_scene(session, drag, squad_ground=ground)
    session.localized_report = None
    session.anchor_reference = None
    session.anchor_reference_method = None


def remembered_blocks(session, field, objects, drag=(0., 0.), gain=1., previous_gestures=None):
    """按本帧视口关联已确认的光幕标注，返回可复认封锁与尚未确认的光幕候选。

    旧客户区坐标不能授权封路；可见光幕必须由当前独立配准再次关联到已确认的标注编号。
    previous_gestures 只用于在明确的复核拖动后更新原立柱跟踪组，不作为光幕开关证据。
    配准或跟踪失败保留既有路网，并将未确认光幕留给点击前的安全检查。
    """
    remaining, blocks = curtains({'objects': objects}), []
    gestures = getattr(session, 'gestures', 0)
    annotated = {item['block']['annotation_id']: item for item in session.barrier_references}
    if previous_gestures is not None:
        for reference in annotated.values():
            if reference.get('gestures', gestures) != previous_gestures:
                continue
            try:
                current = track_barrier(reference['group'], objects, drag, gain)
            except (ValueError, cv2.error):
                continue
            reference.update(group=current, gestures=gestures)
    if not remaining or not annotated:
        return blocks, remaining
    viewport = session.owner.observer.locate_viewport(field)
    if viewport is None:
        return blocks, remaining
    snapshot = dict(localization='accepted', minimap={'mode': 'compact'},
                    roi_to_map=viewport['roi_to_map'], objects=objects)
    for block in annotated_curtains(snapshot, session.owner.cut.chart):
        reference = annotated.get(block['annotation_id'])
        if reference is None:
            continue
        current = barrier_group(block['detection'], objects)
        blocks.append(reference['block'])
        reference.update(group=current, gestures=gestures)
        remaining.remove(block['detection'])
    return blocks, remaining


def confirm_annotated(session, field, snapshot, squad):
    """连续两帧按同一预标注编号确认可见光幕，使用标注封锁段而不拖动镜头精定位。

    两帧都须通过紧凑地图配准和对象检测，全部光幕候选必须唯一关联且编号集合一致。
    立柱仅用于建立后续复核的跟踪组，不能代替实际光幕检测；任一歧义或漏检不改写封锁状态。
    """
    chart = session.owner.cut.chart
    first = annotated_curtains(snapshot, chart)
    if not first or len(first) != len(curtains(snapshot)) or len(first) > MAX_CURTAINS:
        raise BarrierUnconfirmed('当前光幕无法全部唯一关联到预标注位置。')
    session.pause(.35)
    field, snapshot = capture(session, squad)
    if not snapshot.get('objects_observed'):
        raise BarrierUnconfirmed('预标注光幕复核帧的对象检测不可用。')
    second = annotated_curtains(snapshot, chart)
    if (len(second) != len(curtains(snapshot))
            or {item['annotation_id'] for item in first} != {item['annotation_id'] for item in second}):
        raise BarrierUnconfirmed('连续两帧的光幕标注关联不一致，保留既有封锁状态。')
    references = []
    for item in second:
        detection = item['detection']
        block = {key: value for key, value in item.items() if key != 'detection'}
        block['id'] = item['annotation_id']
        references.append(dict(group=barrier_group(detection, snapshot['objects']), block=block,
                               gestures=getattr(session, 'gestures', 0)))
    identifiers = {item['block']['annotation_id'] for item in references}
    session.barrier_references[:] = [item for item in session.barrier_references
                                    if item['block']['annotation_id'] not in identifiers]
    session.barrier_references.extend(references)
    snapshot['blocks'] = [item['block'] for item in references]
    session.owner.state.update(snapshot, advance=False)
    session.owner.cut.update(session.owner.state.blocking())
    session.owner.last_snapshot = snapshot
    session.remember_navigation(snapshot)
    session.record('barriers_annotated', field=field, force=True, snapshot=snapshot)
    logger.info(f'光幕预标注确认：{len(references)} 处，封锁位置采用标注坐标，未平移镜头。')
    return snapshot


def confirm_visible(session, snapshot):
    """连续两帧确认可见光幕的预标注编号，封锁几何只取标注且不拖动镜头寻找落点。

    存在实际光幕候选时必须有本章 ground_barrier 标注和已知小队坐标；缺失即拒绝确认，
    不回退到仿射吸附或居中定位。候选歧义、帧间关联变化和视觉失败保留既有封锁。
    窗口、取消、弹窗和输入预算异常原样传播，不追加清理输入。
    """
    if not curtains(snapshot):
        return snapshot
    if not session.owner.cut.chart.barrier_annotations:
        raise BarrierUnconfirmed('本章缺少光幕预标注，无法确认封锁位置。')
    squad = snapshot.get('squad')
    if squad is None:
        raise BarrierUnconfirmed('光幕确认缺少已知小队起点。')
    goto.map_close(session.win)
    field, snapshot = capture(session, squad)
    if not snapshot.get('objects_observed') or not curtains(snapshot):
        raise BarrierUnconfirmed('复核帧的光幕识别不可用；不将候选消失当成道路已畅通。')
    try:
        return confirm_annotated(session, field, snapshot, squad)
    except (ValueError, cv2.error, np.linalg.LinAlgError) as exc:
        raise BarrierUnconfirmed(f'光幕预标注确认失败：{exc}；未发送移动点击。') from exc


def review_centered(session, snapshot, identifier):
    """将已确认光幕的地图位置移到注视点，仅对该编号发布两帧存在或消失证据。

    ground_barrier 标注必须包含原编号，观察位置取当前标注而非历史检测投影；只拖镜头不移动小队。
    可见光幕按标注编号确认，消失仍要求原双柱的可靠复认与低置信度光幕检查。
    任何一帧视觉或关联不可用均保留原封锁，不据此消除同屏其它屏障。
    """
    record = session.owner.state.blocks[identifier]
    annotation_id = record.get('annotation_id')
    chart = session.owner.cut.chart
    annotation = next((item for item in chart.barrier_annotations if item['id'] == annotation_id), None)
    if annotation is None:
        raise BarrierUnconfirmed('光幕复核缺少原编号的预标注，保留原封锁。')
    squad = snapshot.get('squad')
    if squad is None:
        raise BarrierUnconfirmed('光幕复核缺少已知小队起点，保留原封锁。')
    target = np.asarray(annotation['position'], float)
    goto.map_close(session.win)
    field, snapshot = capture(session, squad)
    try:
        for attempt in range(MAX_PANS + 1):
            if snapshot.get('localization') != 'accepted' or not snapshot.get('objects_observed'):
                raise ValueError('复核视野配准或目标检测不可用。')
            # 尺度来自当前帧匹配器，仅用于朝已知地点转移镜头；最终定位仍必须居中。
            from module.campaign.vision.perception import compact_road_masks
            panel, road, valid = compact_road_masks(field)
            result = session.owner.observer.matcher.match_panel(panel, road, valid, np.asarray(goto.GAZE))
            if result['status'] != 'accepted':
                raise ValueError('复核视口的独立配准未通过。')
            viewport = project(result['roi_to_map'], goto.GAZE)
            offset = target_view_offset(np.asarray(result['roi_to_map']), result['a'], 1., viewport, target)
            if offset is None:
                raise ValueError('无法计算复核镜头偏移。')
            if np.linalg.norm(offset) <= CENTER_TOLERANCE:
                break
            if attempt == MAX_PANS:
                raise ValueError('光幕复核位置未进入画面中央。')
            drag = -offset * min(1., PAN_DRAG_MAX / max(np.linalg.norm(offset), 1.))
            previous_gestures = getattr(session, 'gestures', 0)
            pan(session, field, drag)
            field, snapshot = capture(session, squad)
            remembered_blocks(session, field, snapshot['objects'], drag, previous_gestures=previous_gestures)
        confirmations = []
        for attempt in range(2):
            if attempt:
                session.pause(.35)
                field, snapshot = capture(session, squad)
            if snapshot.get('localization') != 'accepted' or not snapshot.get('objects_observed'):
                raise ValueError('光幕复核缺少两帧可信观测。')
            viewport = project(snapshot['roi_to_map'], goto.GAZE)
            if np.linalg.norm(viewport - target) > 12:
                raise ValueError('复核期间镜头偏离已确认光幕位置。')
            visible = annotated_curtains(snapshot, chart)
            if len(visible) != len(curtains(snapshot)):
                raise ValueError('复核帧存在无法唯一关联标注的光幕，不能判断消失。')
            block = next((item for item in visible if item['annotation_id'] == annotation_id), None)
            if block is None:
                references = [entry for entry in session.barrier_references
                              if entry['block']['annotation_id'] == annotation_id]
                if len(references) != 1:
                    raise ValueError('缺少原光幕的立柱关联，不能将漏检当成消失。')
                entry = references[0]
                tracked = track_barrier(entry['group'], snapshot['objects'])
                if (tracked['source'] != 'pillar_pair' or tracked['curtain'] is not None
                        or np.linalg.norm(np.asarray(tracked['foot']) - goto.VIEWPORT_PIXEL) > CENTER_TOLERANCE):
                    raise ValueError('原光幕的两根立柱未同时在中央复认，不能确认消失。')
                if weak_curtain_visible(field, tracked):
                    raise ValueError('中央区域仍有低置信度光幕，不解除封锁。')
            confirmations.append(block)
        if (confirmations[0] is None) != (confirmations[1] is None):
            raise ValueError('两帧光幕存在状态不一致。')
        if confirmations[1] is not None:
            block = confirmations[1]
            snapshot['blocks'] = [dict({key: value for key, value in block.items() if key != 'detection'},
                                       id=identifier)]
        else:
            snapshot['blocks'] = []
            session.barrier_references[:] = [item for item in session.barrier_references
                                            if item['block']['annotation_id'] != annotation_id]
        snapshot['barriers_observed'] = True
        snapshot['barrier_checks'] = [identifier]
        session.owner.last_snapshot = snapshot
        session.remember_navigation(snapshot)
        session.record('barrier_review', field=field, force=True, snapshot=snapshot, identifier=identifier)
        return snapshot
    except (ValueError, cv2.error, np.linalg.LinAlgError) as exc:
        raise BarrierUnconfirmed(f'光幕居中复核失败：{exc}；保留原封锁。') from exc
