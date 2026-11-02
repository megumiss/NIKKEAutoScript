"""主线标记的镜头定位、敌人身份确认与无敌人地面目的地回退；定位阶段不发送移动点击。"""

from dataclasses import replace

import cv2
import numpy as np

from module.campaign.navigation import controls as goto
from module.campaign.navigation.camera_navigation import compact_viewport, pan_scene
from module.campaign.vision.enemy_ring import match_target_ring, observe_rings, scene_tracks
from module.campaign.vision.offscreen import squad_offscreen
from module.campaign.vision.perception import compact_road_masks, squad_arrow
from module.campaign.vision.story_marker import marked_ring, story_markers
from module.logger import logger

MAX_PANS = 4
MAX_EDGE_CLICKS = 2
STORY_CENTER_TOLERANCE = 40.


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
    """读取已居中主线脚圈对应的底图视口中心，不外推远离注视点的场景坐标。

    脚圈须在注视点的 40 客户区像素内，视口中心来自同帧已接受的紧凑小地图配准。
    未居中、坐标无效或缺少地面环时返回 None；调用方仍须复核第二帧及道路归属后才能写入目标。
    """
    if frame is None or frame['ring'] is None:
        return None
    ring = np.asarray(frame['ring'], float)
    if (ring.shape != (2,) or not np.isfinite(ring).all()
            or np.linalg.norm(ring - goto.VIEWPORT_PIXEL) > STORY_CENTER_TOLERANCE):
        return None
    position = np.asarray(frame['view']['viewport'], float)
    if position.shape != (2,) or not np.isfinite(position).all():
        return None
    return position


def story_enemy_between(before, after, squad):
    """反向验证主线所属敌人，返回两帧唯一支持的底图坐标与普通／Boss 类型。

    使用各自坐标系的实测帧间位移，不建立章节通用的大小地图换算。参考帧和当前帧须通过
    场景跟踪关联到同一主线脚圈，可跨越多次纯镜头平移；仅同向移动、场内邻近或单帧靠近
    注视点均不足以认领敌人。EX 候选只有在两帧都唯一出现于同一底图位置、运动匹配落到
    菱形下同一脚圈时才作为 Boss 返回；原始观测类型不原地改写。
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
        if kind not in ('minimap_enemy_normal', 'minimap_enemy_boss', 'minimap_enemy_ex'):
            continue
        enemy_kind = kind.removeprefix('minimap_enemy_')
        candidate_frames = (first, second)
        if enemy_kind == 'ex':
            candidate_frames = story_ex_frames(first, second, position)
            if candidate_frames is None:
                continue
            enemy_kind = 'boss'
        result = match_target_ring(*candidate_frames, position, squad, goto.VIEWPORT_PIXEL, enemy_kind=enemy_kind)
        if result.get('click') is not None and np.linalg.norm(np.asarray(result['click']) - after['ring']) <= 15:
            matched.append(dict(position=np.asarray(position).tolist(), kind=enemy_kind,
                                ex_matched=kind == 'minimap_enemy_ex'))
    return matched[0] if len(matched) == 1 else None


def story_ex_frames(before, after, position):
    """仅为指定 EX 候选创建两帧匹配副本，不将同屏其他 EX 或原始观测改成 Boss。

    两帧目标位置附近必须各有且仅有一个 EX，不得混入普通或 Boss 竞争标记；30 地图像素
    范围沿用初次脚圈匹配的目标关联规则。副本只供运动匹配试算，必须最终对应到主线脚圈
    才能产生纠正证据，不能因为同屏各有一个标记就直接改型。
    """
    frames = []
    for frame in (before, after):
        indices = [index for index, (_, point) in enumerate(frame.enemies)
                   if np.linalg.norm(np.asarray(point) - position) <= 30]
        if len(indices) != 1 or frame.enemies[indices[0]][0] != 'minimap_enemy_ex':
            return None
        enemies = list(frame.enemies)
        index = indices[0]
        enemies[index] = ('minimap_enemy_boss', enemies[index][1])
        frames.append(replace(frame, enemies=enemies))
    return tuple(frames)


def story_enemy_centered(before, after):
    """用两帧居中的主线脚圈与视口附近唯一小地图敌人建立位置对应，不外推远处菱形坐标。

    调用前须通过两帧主线脚圈居中、镜头尺度与位置稳定检查。小地图候选使用既有 30 地图
    像素目标范围，两帧必须各唯一且类型一致，敌人位置漂移不超过既有 12 地图像素。
    返回 None 表示没有此范围内敌人，调用方才可使用地面位置兜底；竞争或冲突直接报错。
    EX 返回 Boss 候选及独立纠正证据，由状态层拒绝已通过真实 EX 弹窗确认的位置。
    """
    if (story_ground(before) is None or story_ground(after) is None
            or abs(after['scale'] / before['scale'] - 1.) > .12
            or np.linalg.norm(np.asarray(after['view']['viewport']) - before['view']['viewport']) > 12
            or np.linalg.norm(np.asarray(after['ring']) - before['ring']) > 15):
        raise ValueError('主线脚圈未通过两帧居中与镜头稳定检查')
    candidates = []
    for frame in (before, after):
        position = story_ground(frame)
        if position is None:
            raise ValueError('主线脚圈未居中，不能关联小地图敌人')
        candidates.append([(kind, np.asarray(point, float)) for kind, point in frame['rings'].enemies
                           if np.linalg.norm(np.asarray(point) - position) <= 30])
    if not any(candidates):
        return None
    if any(len(items) != 1 for items in candidates):
        raise ValueError('居中主线附近的小地图敌人缺失或存在竞争候选')
    first, second = candidates[0][0], candidates[1][0]
    if first[0] != second[0] or np.linalg.norm(first[1] - second[1]) > 12:
        raise ValueError('居中主线附近的小地图敌人类型或位置不一致')
    if second[0] not in ('minimap_enemy_normal', 'minimap_enemy_boss', 'minimap_enemy_ex'):
        raise ValueError('居中主线附近的标记不属于可验证的敌人类型')
    is_ex = second[0] == 'minimap_enemy_ex'
    return dict(position=second[1].tolist(), kind='boss' if is_ex else second[0].removeprefix('minimap_enemy_'),
                ex_matched=is_ex)


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
    """有限平移定位主线脚圈，以已确认敌人或两帧居中的视口中心作为路程目的地。

    最多四次平移，只向已识别的脚圈调整镜头，不按悬浮标记做地图投影。保留本次定位中最多
    四个已识别主线脚圈的参考帧，优先用早期帧积累位移证据；单个参考帧匹配失败不影响其余帧，
    每对帧仍须通过原有身份与运动校验。小队不可定位且离屏标记也未确认时禁止拖动，以免按下点
    落在小队上变成走位。地面目标须连续两帧处于注视点 40 客户区像素内，尺度变化
    不超过 12%、视口偏差不超过 12 地图像素、脚圈偏差不超过 15 客户区像素，并通过道路归属校验；
    居中后优先关联两帧同位置唯一小地图敌人；运动匹配和居中匹配均可提供 EX 误分类纠正证据。
    无小地图敌人才用地面位置兜底，类型冲突或已确认 EX 不能降级为无类型地面目的地。
    未确认时记录失败冷却，不吸附远离道路的点，保留普通敌人推进策略。
    """
    state = session.owner.state
    session.begin_target('story_location')
    goto.map_close(session.win)
    changed = False
    try:
        changed = reveal_story_marker(session)
        before = story_frame(session, squad)
        references = []
        for attempt in range(MAX_PANS + 1):
            if before is None:
                break
            position = story_ground(before)
            if position is not None:
                session.pause(.35)
                after = story_frame(session, squad)
                confirmed = story_ground(after)
                if (confirmed is None or abs(after['scale'] / before['scale'] - 1.) > .12
                        or np.linalg.norm(confirmed - position) > 12
                        or np.linalg.norm(after['ring'] - before['ring']) > 15):
                    raise ValueError('主线地面位置未通过两帧居中复核')
                if (np.any(confirmed < 0) or np.any(confirmed >= state.size)
                        or not session.owner.cut.chart.walkable(confirmed)):
                    raise ValueError('主线居中位置越出地图或缺少邻近道路证据')
                enemy = story_enemy_centered(before, after)
                if enemy is not None:
                    identifier = state.remember_story_target(enemy['position'], kind=enemy['kind'],
                                                            ex_matched=enemy['ex_matched'])
                    if identifier is None:
                        raise ValueError('主线敌人位置存在已确认 EX、类型冲突或已清除记录')
                    session.record('story_enemy_confirmed', enemy=enemy, identifier=identifier, method='centered')
                    logger.info(f"主线脚圈与小地图敌人两帧居中对应：目标 {identifier}，类型 {enemy['kind']}，"
                                f"底图位置 {enemy['position']}。")
                    return changed
                position = confirmed.tolist()
                if any(np.linalg.norm(confirmed - point) <= 30 for point in state.confirmed_ex_positions):
                    raise ValueError('主线居中位置位于已确认 EX，禁止作为地面目的地')
                state.route_goal = dict(kind='story', id='story-marker', position=position,
                                        reason='main_story_location')
                state.cooldowns.pop('story-location', None)
                logger.info(f'主线地面路程目标已通过两帧居中与道路校验：'
                            f'底图位置 {np.round(position, 1).tolist()}，镜头平移 {attempt} 次。')
                return changed
            if attempt == MAX_PANS:
                break
            field = before['field']
            arrow = squad_arrow(field)
            if arrow is None and squad_offscreen(field) is None:
                break
            if before['ring'] is None:
                # 候选重叠时只换小视角，不把图标中心当作机关或敌人的地面落点。
                drag = np.array([80., 0.])
            else:
                drag = np.asarray(goto.VIEWPORT_PIXEL) - before['ring']
                length = float(np.linalg.norm(drag))
                drag = drag * min(1., 320. / max(length, 1.))
            pan_scene(session, drag)
            changed = True
            session.localized_report = None
            session.anchor_reference = None
            session.anchor_reference_method = None
            after = story_frame(session, squad)
            if after is None:
                break
            if before['ring'] is not None:
                references.append((attempt, before))
            for reference_pan, reference in references:
                try:
                    enemy = story_enemy_between(reference, after, squad)
                except (ValueError, cv2.error, np.linalg.LinAlgError) as exc:
                    logger.info(f'主线敌人帧间匹配不可用：参考平移序号 {reference_pan}，'
                                f'当前平移序号 {attempt + 1}，原因 {exc}。')
                    continue
                if enemy is None:
                    continue
                position = enemy['position']
                identifier = state.remember_story_target(position, kind=enemy['kind'],
                                                        ex_matched=enemy.get('ex_matched', False))
                if identifier is not None:
                    session.record('story_enemy_confirmed', enemy=enemy, identifier=identifier, method='motion')
                    logger.info(f'主线路程目标已定位：敌人 {identifier}，底图位置 {position}，'
                                f'镜头平移 {attempt + 1} 次，参考平移序号 {reference_pan}。')
                    return changed
            before = after
    except (ValueError, cv2.error, np.linalg.LinAlgError) as exc:
        logger.info(f'主线标记对应关系未确认：{exc}。')
    state.penalize('story-location')
    logger.info('主线标记未能确认敌人或居中地面位置，已记录失败冷却，保留普通敌人推进策略。')
    return changed
