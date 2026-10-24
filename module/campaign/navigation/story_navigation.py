"""主线标记的镜头定位、敌人身份确认与无敌人地面目的地回退；定位阶段不发送移动点击。"""

import cv2
import numpy as np

from module.campaign.navigation import controls as goto
from module.campaign.navigation.camera_navigation import compact_viewport, pan_scene
from module.campaign.vision.barriers import scene_anchor
from module.campaign.vision.enemy_ring import match_target_ring, observe_rings, scene_tracks
from module.campaign.vision.offscreen import squad_offscreen
from module.campaign.vision.perception import compact_road_masks, squad_arrow
from module.campaign.vision.story_marker import marked_ring, story_markers
from module.campaign.vision.surface_motion import project
from module.logger import logger

MAX_PANS = 4
MAX_EDGE_CLICKS = 2


def story_frame(session, squad):
    """获取一帧独立接受的紧凑视口配准、脚圈及主线模板，不使用旧镜头缓存替代当前证据。

    小队底图位置由进入找敌人流程前的可信观测提供；本帧只定位镜头和敌人，不重新校验小队圆环。
    返回 None 表示当前帧不足以确定主线身份；窗口、输入、战斗等安全异常由调用方处理。
    """
    field = session.win.client()
    session.identity(field)
    markers = story_markers(field)
    if not any(item['kind'] == 'scene' for item in markers):
        return None
    panel, road, valid = compact_road_masks(field)
    view = compact_viewport(session, session.owner.observer.matcher, panel, road, valid)
    if view['status'] != 'accepted':
        return None
    rings = observe_rings(field, panel, view['matrix'], view['viewport'], observe_squad=False)
    ring = marked_ring(markers, rings.centers)
    view.update(panel_size=list(panel.shape[1::-1]), roi_to_map=view['matrix'])
    return dict(field=field, rings=rings, ring=ring, scale=view['a'], view=view)


def story_ground(frame):
    """把菱形下方唯一地面环投到底图，供没有小地图敌人的主线目标使用。

    只接受同帧已接受配准与视口锚点，不使用悬浮菱形中心，也不跨帧套用场景矩阵。
    投影误差由原有道路端点吸附和到点闭环复核；缺少地面环时返回 None。
    """
    if frame is None or frame['ring'] is None:
        return None
    anchor = scene_anchor(frame['field'], None, frame['view'])
    if anchor is None:
        return None
    chart, _ = anchor
    return project(chart, frame['ring'])


def story_enemy_between(before, after, squad):
    """反向验证主线所属敌人，返回两帧唯一支持的底图坐标与普通／Boss 类型。

    使用各自坐标系的实测帧间位移，不建立章节通用的大小地图换算。主线必须连续关联到同一
    脚圈；仅同向移动、场内邻近或单帧靠近注视点均不足以认领敌人。
    """
    if before['ring'] is None or after['ring'] is None:
        return None
    if abs(after['scale'] / before['scale'] - 1.) > .12:
        return None
    first, second = before['rings'], after['rings']
    old, tracked = scene_tracks(first, second)
    index = int(np.argmin(np.linalg.norm(second.centers - after['ring'], axis=1)))
    if not tracked[index] or np.linalg.norm(old[index] - before['ring']) > 15:
        return None
    matched = []
    for kind, position in second.enemies:
        if kind not in ('minimap_enemy_normal', 'minimap_enemy_boss'):
            continue
        enemy_kind = kind.removeprefix('minimap_enemy_')
        result = match_target_ring(first, second, position, squad, goto.VIEWPORT_PIXEL, enemy_kind=enemy_kind)
        if result.get('click') is not None and np.linalg.norm(np.asarray(result['click']) - after['ring']) <= 15:
            matched.append(dict(position=np.asarray(position).tolist(), kind=enemy_kind))
    return matched[0] if len(matched) == 1 else None


def reveal_story_marker(session):
    """仅点击新帧复核通过的离屏主线箭头，最多两次；返回是否确实发送过镜头点击。

    场内标记从不由此函数点击。切镜头后清除相机相关缓存并等停稳，不把点击记为小队到达。
    """
    changed = False
    for _ in range(MAX_EDGE_CLICKS):
        field = session.win.client()
        session.identity(field)
        markers = story_markers(field)
        if any(item['kind'] == 'scene' for item in markers):
            break
        edges = [item for item in markers if item['kind'] == 'edge']
        if len(edges) != 1:
            break
        session.pause(.25)
        fresh = session.win.client()
        session.identity(fresh)
        confirmed = [item for item in story_markers(fresh) if item['kind'] == 'edge'
                     and np.linalg.norm(np.asarray(item['position']) - edges[0]['position']) <= 10]
        if len(confirmed) != 1:
            break
        point = tuple(int(round(value)) for value in confirmed[0]['position'])
        if not session.tap(point, interval=2.):
            break
        changed = True
        session.localized_report = None
        session.anchor_reference = None
        session.anchor_reference_method = None
        logger.info(f'主线离屏标记已点击：客户区位置 {point}；等待镜头切换，不发送小队移动。')
        session.pause(1.5)
    return changed


def locate_story_target(session, squad):
    """有限平移优先确认主线敌人；无对应敌人时把菱形下方唯一地面环记为独立路程目的地。

    优先利用拖动前后两帧，最多四次平移；只向已识别的脚圈调整镜头，不按悬浮标记做地图投影。
    小队不可定位且离屏标记也未确认时禁止拖动，以免按下点落在小队上变成走位。
    地面回退使用最后一次可靠帧的同帧视口锚点；没有地面环或投影越界时才进入失败冷却。
    """
    state = session.owner.state
    session.begin_target('story_location')
    goto.map_close(session.win)
    changed = False
    before = None
    try:
        changed = reveal_story_marker(session)
        before = story_frame(session, squad)
        for attempt in range(MAX_PANS):
            if before is None:
                break
            field = before['field']
            arrow = squad_arrow(field)
            if arrow is None and squad_offscreen(field) is None:
                break
            ground = None if arrow is None else np.asarray(arrow) + [0., goto.ARROW_GROUND_OFFSET]
            if before['ring'] is None:
                # 候选重叠时只换小视角，不把图标中心当作机关或敌人的地面落点。
                drag = np.array([80., 0.])
            else:
                drag = np.asarray(goto.VIEWPORT_PIXEL) - before['ring']
                length = float(np.linalg.norm(drag))
                drag = drag * min(1., 320. / max(length, 1.))
                if length < 70:
                    drag = np.array([80., 0.])
            pan_scene(session, drag, squad_ground=ground)
            changed = True
            session.localized_report = None
            session.anchor_reference = None
            session.anchor_reference_method = None
            after = story_frame(session, squad)
            if after is None:
                break
            enemy = story_enemy_between(before, after, squad)
            if enemy is not None:
                position = enemy['position']
                identifier = state.remember_story_target(position, kind=enemy['kind'])
                if identifier is not None:
                    logger.info(f'主线路程目标已定位：敌人 {identifier}，底图位置 {position}，'
                                f'镜头平移 {attempt + 1} 次。')
                    return changed
            before = after
        position = story_ground(before)
        if (position is not None and np.isfinite(position).all()
                and np.all(position >= 0) and np.all(position < state.size)):
            position = position.tolist()
            state.route_goal = dict(kind='story', id='story-marker', position=position,
                                    reason='main_story_location')
            state.cooldowns.pop('story-location', None)
            logger.info(f'主线标记没有可确认的小地图敌人，已将其下方地面环设为路程目标：'
                        f'底图位置 {np.round(position, 1).tolist()}。')
            return changed
    except (ValueError, cv2.error, np.linalg.LinAlgError) as exc:
        logger.info(f'主线标记对应关系未确认：{exc}。')
    state.penalize('story-location')
    logger.info('主线标记未能确认敌人或下方地面位置，已记录失败冷却，保留普通敌人推进策略。')
    return changed
