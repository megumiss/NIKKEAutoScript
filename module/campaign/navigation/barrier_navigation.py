"""光幕精定位：只平移镜头，居中和两帧复核完成前不改变可通行路网。"""

import cv2
import numpy as np

from module.campaign.navigation import controls as goto
from module.campaign.navigation.camera_navigation import pan_scene, target_view_offset
from module.campaign.vision.barriers import (CENTER_TOLERANCE, barrier_group, centered_curtain,
                                             curtain_foot, track_barrier, weak_curtain_visible)
from module.campaign.vision.offscreen import squad_offscreen
from module.campaign.vision.perception import squad_arrow
from module.campaign.vision.surface_motion import project
from module.logger import logger

MAX_PANS = 8
MAX_CURTAINS = 4


class BarrierUnconfirmed(RuntimeError):
    """光幕身份或居中定位未确认；禁止降级成普通目标失败后继续移动。"""


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
    """通过原输入预算平移镜头，场景箭头只用于避开小队脚下的鼠标按下区域。"""
    arrow = squad_arrow(field)
    if arrow is None and squad_offscreen(field) is None:
        raise BarrierUnconfirmed('无法确定安全拖动区域，停止光幕确认；未发送移动点击。')
    ground = None if arrow is None else np.asarray(arrow) + [0., goto.ARROW_GROUND_OFFSET]
    pan_scene(session, drag, squad_ground=ground)
    session.localized_report = None
    session.anchor_reference = None
    session.anchor_reference_method = None


def remembered_blocks(session, field, objects):
    """用实测场景跟踪复认已居中的光幕；失败只留下待确认候选，不采用边缘投影猜测身份。"""
    remaining, blocks = curtains({'objects': objects}), []
    for reference in session.barrier_references:
        try:
            available = [item for item in objects if item.get('state') != 'curtain' or item in remaining]
            current = track_barrier(reference['field'], field, reference['group'], available)
        except (ValueError, cv2.error):
            continue
        if current['curtain'] is not None:
            blocks.append(reference['block'])
        if current['curtain'] in remaining:
            remaining.remove(current['curtain'])
        reference.update(field=field, group=current)
    return blocks, remaining


def confirm_visible(session, snapshot):
    """逐个居中未确认光幕，连续两帧的地面落点与底图配准稳定后才发布封路证据。

    小队保持原地；同一目标最多八次 260px 镜头拖动，每次最多确认四处光幕。
    所有关联都在客户区帧之间完成，旧屏幕边缘投影从不用于生成或匹配封路段。
    窗口、取消、输入异常原样传播；定位失败抛 BarrierUnconfirmed，不能绕过未知光幕继续点击。
    """
    if not curtains(snapshot):
        return snapshot
    squad = snapshot.get('squad')
    if squad is None:
        raise BarrierUnconfirmed('光幕确认缺少已知小队起点。')
    goto.map_close(session.win)
    field, snapshot = capture(session, squad)
    if not snapshot.get('objects_observed') or not curtains(snapshot):
        raise BarrierUnconfirmed('复核帧的光幕识别不可用；不将候选消失当成道路已畅通。')
    try:
        for target_index in range(MAX_CURTAINS + 1):
            blocks, pending = remembered_blocks(session, field, snapshot.get('objects', []))
            if not pending:
                snapshot['blocks'] = blocks
                session.owner.last_snapshot = snapshot
                session.remember_navigation(snapshot)
                session.record('barriers_confirmed', field=field, force=True, snapshot=snapshot)
                return snapshot
            if target_index == MAX_CURTAINS:
                raise ValueError('单次光幕确认数量达到上限，仍有未确认候选。')
            item = min(pending, key=lambda candidate: np.linalg.norm(curtain_foot(candidate) - goto.VIEWPORT_PIXEL))
            group = barrier_group(item, snapshot['objects'])
            gain, pans = 1., 0
            for attempt in range(MAX_PANS + 1):
                point = np.asarray(group['foot'])
                delta = np.asarray(goto.VIEWPORT_PIXEL) - point
                if np.linalg.norm(delta) <= CENTER_TOLERANCE:
                    if group['curtain'] is None:
                        session.pause(.35)
                        fresh, latest = capture(session, squad)
                        group = track_barrier(field, fresh, group, latest['objects'])
                        field, snapshot = fresh, latest
                        continue
                    first = centered_curtain(group['curtain'], snapshot, session.owner.cut.chart)
                    session.pause(.35)
                    fresh, latest = capture(session, squad)
                    confirmed = track_barrier(field, fresh, group, latest['objects'])
                    if confirmed['curtain'] is None:
                        field, snapshot, group = fresh, latest, confirmed
                        continue
                    block = centered_curtain(confirmed['curtain'], latest, session.owner.cut.chart)
                    if np.linalg.norm(np.asarray(first['point']) - block['point']) > 12:
                        raise ValueError('居中光幕两帧底图位置不稳定。')
                    block['id'] = f"centered-{len(session.barrier_references) + 1}"
                    session.barrier_references.append(dict(field=fresh, group=confirmed, block=block))
                    session.record('barrier_centered', field=fresh, force=True, block=block,
                                   client_foot=confirmed['foot'], tracking=confirmed['source'], pans=pans)
                    # 先保存已确认的这一处；后续候选失败也不丢弃有效证据，且不消除视野外封锁。
                    latest['blocks'] = [block]
                    session.owner.state.update(latest, advance=False)
                    session.owner.cut.update(session.owner.state.blocking())
                    logger.info(f"光幕居中确认：底图位置 {block['point']}，镜头平移 {pans} 次。")
                    field, snapshot = fresh, latest
                    break
                if attempt == MAX_PANS:
                    raise ValueError('镜头平移次数耗尽，光幕仍未居中。')
                drag = delta / max(gain, .2)
                drag *= min(1., 260. / max(np.linalg.norm(drag), 1.))
                pan(session, field, drag)
                pans += 1
                fresh, latest = capture(session, squad)
                current = track_barrier(field, fresh, group, latest['objects'])
                movement = np.asarray(current['foot']) - point
                if np.linalg.norm(movement) < 3:
                    raise ValueError('镜头到达边界或光幕未产生有效位移。')
                response = float(movement @ drag / (drag @ drag))
                if .2 <= response <= 2:
                    gain = response
                session.record('barrier_centering', field=fresh, client_foot=current['foot'],
                               tracking=current['source'],
                               curtain_seen=current['curtain'] is not None,
                               camera_drag=drag.tolist(), attempt=attempt + 1)
                field, snapshot, group = fresh, latest, current
            else:
                raise ValueError('光幕在确认预算内未恢复可靠识别。')
    except (ValueError, cv2.error, np.linalg.LinAlgError) as exc:
        session.record('barrier_confirmation_failed', field=field, force=True, reason=str(exc))
        raise BarrierUnconfirmed(f'光幕居中确认失败：{exc}；停止执行，未发送移动点击。') from exc
    raise AssertionError('光幕确认流程未收敛。')


def review_centered(session, snapshot, identifier):
    """将已确认光幕的地图位置移到注视点，仅对该编号发布两帧存在或消失证据。

    复核只用旧地图坐标寻找观察位置，不直接据此移动小队。附近仍有光幕却无法居中唯一确认、
    或任何一帧视觉不可用时保留旧封锁；其它屏障即使处于本帧地图覆盖内，也不据此消除。
    """
    record = session.owner.state.blocks[identifier]
    squad, target = snapshot['squad'], np.asarray(record['point'], float)
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
            drag = -offset * min(1., 260. / max(np.linalg.norm(offset), 1.))
            pan(session, field, drag)
            field, snapshot = capture(session, squad)
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
            nearby = [item for item in curtains(snapshot)
                      if np.linalg.norm(curtain_foot(item) - goto.VIEWPORT_PIXEL) <= 100]
            if len(nearby) > 1:
                raise ValueError('复核区域内有多个光幕，不能确认身份。')
            block = centered_curtain(nearby[0], snapshot, session.owner.cut.chart) if nearby else None
            if block is None:
                references = [entry for entry in session.barrier_references
                              if np.linalg.norm(np.asarray(entry['block']['point']) - target) <= 28]
                if len(references) != 1:
                    raise ValueError('缺少原光幕的立柱关联，不能将漏检当成消失。')
                entry = references[0]
                tracked = track_barrier(entry['field'], field, entry['group'], snapshot['objects'])
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
            if np.linalg.norm(np.asarray(block['point']) - target) > 28:
                raise ValueError('居中复核的光幕与原记录不一致。')
            snapshot['blocks'] = [dict(block, id=identifier)]
        else:
            session.barrier_references[:] = [item for item in session.barrier_references
                                            if np.linalg.norm(np.asarray(item['block']['point']) - target) > 28]
        snapshot['barriers_observed'] = True
        snapshot['barrier_checks'] = [identifier]
        session.owner.last_snapshot = snapshot
        session.remember_navigation(snapshot)
        session.record('barrier_review', field=field, force=True, snapshot=snapshot, identifier=identifier)
        return snapshot
    except (ValueError, cv2.error, np.linalg.LinAlgError) as exc:
        raise BarrierUnconfirmed(f'光幕居中复核失败：{exc}；保留原封锁。') from exc
