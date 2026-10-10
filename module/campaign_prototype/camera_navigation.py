"""共享的远点镜头规划；小队位置与镜头观测分开保存，点击后重新确认小队停稳。"""

from types import SimpleNamespace

import cv2
import numpy as np

from . import perception

from . import goto, runtime, settings
from .probe import jacobian
from .surface_motion import project


FIELD_BOUNDS = (300, 220, 1450, 820)
ROI_BOUNDS = (12, 12, 474, 420)
# 横向利用场景全宽，纵向利用中部全高；两个矩形均避开固定 UI。
PAN_REGIONS = ((24, 320, 1752, 880), (250, 70, 1490, 880))


def pan_region(drag):
    """选择能容纳指定客户区拖动向量的场景矩形。

    返回区域边界与不超过 1 的等比例缩放系数；两轴一起缩放，避免避让 UI 时改变方向。
    """
    def available(region):
        """计算候选矩形对拖动向量的容纳比例。

        零位移轴按至少一个像素计算分母，返回值用于比较横向和纵向区域，而不修改输入向量。
        """
        left, top, right, bottom = region
        return min(1., * (np.array([right - left, bottom - top]) / np.maximum(np.abs(drag), 1)))

    region = max(PAN_REGIONS, key=available)
    return region, available(region)


def inside(point, bounds):
    """判断二维像素点是否位于闭合矩形内。

    bounds 按左、上、右、下排列；非有限坐标返回 False，边界点视为有效。
    """
    x, y = np.asarray(point, float)
    left, top, right, bottom = bounds
    return bool(np.isfinite([x, y]).all() and left <= x <= right and top <= y <= bottom)


def field_chart(localizer, observation, anchor, calibration=None):
    """从实测箭头锚点绑定 ROI→场景投影，保留透视项而不外推单点 Jacobian。

    observation 必须包含真实小队的地图坐标、player_roi 和 ROI→map 矩阵，anchor 使用客户区像素。
    平面地图采用配套场景矩阵，分层地图采用实测位移矩阵；返回以箭头锚点校正平移项的 ROI→场景单应。
    """
    if observation.get('position_kind') != 'squad' or observation.get('player_roi') is None:
        raise ValueError('必须先定位真实小队，才能建立镜头移动基准。')
    if np.linalg.norm(project(observation['roi_to_map'], observation['player_roi']) - observation['position']) > 2:
        raise ValueError('小队坐标与当前地图投影不一致。')
    affine = np.eye(3)
    if calibration is None:
        affine[:2, :2] = goto.A_INV
        chart = affine @ np.asarray(localizer.old_matrix, float)
    else:
        affine[:2, :2] = np.asarray(calibration['matrix'], float)[:2, :2]
        chart = affine @ np.asarray(observation['roi_to_map'], float)
    offset = np.asarray(anchor, float) - project(chart, observation['player_roi'])
    translation = np.eye(3)
    translation[:2, 2] = offset
    return translation @ chart


def target_projection(chart, observation, target):
    """将原图像素目标同时换算为小地图 ROI 点和客户区落点。

    先反解本次观测的地图变换，再使用场景单应；返回两个二维点，由调用者检查可见范围。
    """
    roi = project(np.linalg.inv(observation['roi_to_map']), target)
    return roi, project(chart, roi)


def camera_offset(chart, observation, target):
    """在最近一次实测视野上，把目标相对位置换算为场景方向和距离。"""
    matrix = np.asarray(observation['roi_to_map'], float)
    center = np.array([243., 231.])
    return jacobian(chart, center) @ np.linalg.solve(jacobian(matrix, center),
                                                     np.asarray(target) - project(matrix, center))


def camera_drag(chart, observation, target, gain=1.):
    """远处点只用局部方向规划镜头，避免视野外的透视地平线反转拖动方向。

    target 使用地图像素，gain 表示实测镜头响应比例；仅在 ROI 中心线性化目标方向。
    结果为避开固定 UI 的客户区拖动向量；幅度不足 8px 时抛出 ValueError，防止无效微拖循环。
    """
    drag = -camera_offset(chart, observation, target) / gain
    _, scale = pan_region(drag)
    drag *= scale
    if np.linalg.norm(drag) < 8:
        raise ValueError('目标仍不可点击，但镜头已没有有效平移方向。')
    return drag


def camera_response(chart, before, after, drag):
    """用重新配准的视野位移测量拖动响应，只接受方向一致且可测的推进。

    比较拖动前后 ROI 中心的地图坐标，并换算为场景位移。
    方向余弦至少为 0.8、位移至少为 8px 且增益在 0.2～2 内才返回增益，否则返回 None 供规划器忽略。
    """
    center = np.array([243., 231.])
    matrix = np.asarray(before['roi_to_map'], float)
    moved = project(after['roi_to_map'], center) - project(matrix, center)
    travel = jacobian(chart, center) @ np.linalg.solve(jacobian(matrix, center), moved)
    gain = float(travel @ -drag / (drag @ drag))
    alignment = float(travel @ -drag / max(np.linalg.norm(travel) * np.linalg.norm(drag), 1))
    if np.linalg.norm(travel) < 8 or alignment < .8 or not .2 <= gain <= 2:
        return None
    return gain


def pan_scene(session, drag):
    """收起小地图并核对身份后，把客户区拖动端点换算为屏幕坐标发送一次手势。

    端点越界或驱动失败立即抛错；成功后等待场景稳定，本方法不发送小队移动点击。
    """
    goto.map_close(session.win)
    field = goto.capture_client(session.win)
    session.identity(field)
    region, _ = pan_region(drag)
    left, top, right, bottom = region
    center = np.array([(left + right) / 2, (top + bottom) / 2])
    start = np.rint(center - drag / 2).astype(int)
    end = np.rint(center + drag / 2).astype(int)
    if not inside(start, region) or not inside(end, region):
        raise ValueError('镜头拖动超出有效场景区域。')
    session.check()
    origin = np.array(session.win.gui.ClientToScreen(session.win.hwnd, (0, 0)))
    session.win.handler.mouse_swipe(tuple(origin + start), tuple(origin + end))
    if session.win.handler._failures:
        raise RuntimeError('镜头平移失败，未发送移动点击。')
    session.pause(1.2)


def observe_camera(session, squad_position, surface=None):
    """允许小队离屏；接受的视野坐标只用于镜头，不更新冻结的小队位置。

    平移后重新配准展开地图；分层地图无小队圆环时使用可见的同层道路点。
    返回的视野参考只更新镜头位置；若可见小队偏离冻结坐标超过 12 地图像素，或定位未通过，则拒绝继续。
    """
    session.check()
    session.identity(goto.capture_client(session.win))
    goto.map_open(session.win)
    image = session.win.capture()
    session.index += 1
    tag = f'{getattr(session, "tag", "camera")}_{session.index:03}'
    if hasattr(session, 'calibration'):
        players, _ = perception.detect_markers(image, np.eye(3))
        if len(players) > 1:
            raise ValueError('镜头观测中存在多个小队标记。')
        if players:
            report = session.localizer.locate_squad(image, session.folder / f'{tag}.jpg')
        else:
            road = goto.mr.terrain(image)
            ys, xs = np.nonzero(road[20:420, 20:466])
            if not len(xs):
                raise ValueError('镜头视野没有可定位的本层道路。')
            points = np.column_stack([xs + 20, ys + 20])
            point = points[np.argmin(np.linalg.norm(points - [243, 231], axis=1))]
            report = session.localizer.locate(image, point, review=session.folder / f'{tag}.jpg')
            report.update(position_kind='viewport_reference', player_roi=None)
        if report['status'] != 'accepted':
            raise ValueError(f'镜头分层定位失败：{report.get("reason", "证据不足")}')
        from .edited_map import road_distance
        if surface is None or road_distance(surface, report['position']) > 2:
            raise ValueError('镜头定位点不在已标定的同层道路内。')
    else:
        report = session.localizer.locate(image, tag, require_player=False)
    if np.mean(goto.mr.terrain(image) != goto.mr.terrain(session.win.capture())) > .03:
        raise ValueError('镜头定位期间画面仍在变化。')
    if (report.get('position_kind') == 'squad'
            and np.linalg.norm(np.asarray(report['position']) - squad_position) > 12):
        from .movement_feedback import SquadPositionChanged
        raise SquadPositionChanged(squad_position, report['position'])
    goto.map_close(session.win)
    field = goto.capture_client(session.win)
    session.identity(field)
    runtime.write_image(session.folder / f'{tag}_field.jpg', field)
    return report


def predict_camera(chart, view, drag, gain):
    """用本批实测基准和累计拖动估算相对位移，不重复外推透视；不能作为点击依据。"""
    center = np.array([243., 231.])
    matrix = np.asarray(view['roi_to_map'])
    translation = np.eye(3)
    translation[:2, 2] = jacobian(matrix, center) @ np.linalg.solve(
        jacobian(chart, center), -np.asarray(drag) * gain)
    matrix = translation @ matrix
    return dict(roi_to_map=matrix.tolist(), position=project(matrix, [243, 231]).tolist(),
                position_kind='predicted_viewport', player_roi=None)


def scene_unchanged(before, after):
    """仅检测长拖后画面是否基本没变，不用场景配准估计位移或透视。"""
    frames = [cv2.resize(cv2.cvtColor(frame[320:880, 250:1490], cv2.COLOR_BGR2GRAY), (248, 112))
              for frame in (before, after)]
    # 空白或遮挡画面不足以判断边缘；少量角色动画不影响大部分静止地形。
    return bool(min(frame.std() for frame in frames) > 12
                and np.mean(cv2.absdiff(*frames) <= 6) > .95)


def plan_world_move(session, observation, target, calibration=None, max_pans=8, anchor=None, surface=None):
    """优先点击完整目标；视野外先平移，不按固定地图距离切成短步。

    以真实小队观测和箭头锚点建立场景投影，target 是调用者已验证的地图目标。
    目标同时进入 ROI 与场景安全区后返回客户区点击坐标；最多平移 max_pans 次，边界、身份或定位异常会中止。
    每批最多三次长拖动，以同一实测基准累计相对位移；估计位移完成或画面不动时提前校正。
    批末及点击前用小地图确认，估算位置不能直接用于点击。
    """
    from .movement_feedback import resolve_anchor
    target = np.asarray(target, float)
    squad_position = np.asarray(observation['position'], float).copy()
    if np.linalg.norm(target - squad_position) < 2:
        raise ValueError('当前落点与小队过近。')
    goto.map_close(session.win)
    field = goto.capture_client(session.win)
    session.identity(field)
    session.check_collectible(field)
    session.preview(field)
    if anchor is None:
        anchor = resolve_anchor(session, field, observation)
    chart = field_chart(session.localizer, observation, anchor, calibration)
    view, previous_center, responses = observation, None, []
    batch_pans, batch_drag = 0, np.zeros(2)
    batch_start = observation
    session.camera_pans = getattr(session, 'camera_pans', 0)
    for count in range(max_pans + 1):
        session.check()
        try:
            roi, click = target_projection(chart, view, target)
        except ValueError:
            roi = click = np.array([np.nan, np.nan])
        if batch_pans == 0 and inside(roi, ROI_BOUNDS) and inside(click, FIELD_BOUNDS):
            field = goto.capture_client(session.win)
            session.identity(field)
            session.check_collectible(field)
            session.preview(field)
            session.emit(state='planning', navigation_method='camera_pan' if count else 'direct_target',
                         camera_pans=session.camera_pans, plan_camera_pans=count,
                         planned_target=target.tolist(), click=click.tolist(),
                         message='目标已进入画面，准备点击并交给游戏寻路。')
            return click
        if count == max_pans:
            raise ValueError(f'已平移 {max_pans} 次，目标仍不在有效画面内；未发送移动点击。')
        center = project(view['roi_to_map'], [243, 231])
        if batch_pans == 0 and previous_center is not None and np.linalg.norm(center - previous_center) < 3:
            raise ValueError('镜头已到边界或没有产生有效平移，未发送移动点击。')
        if batch_pans == 0:
            previous_center = center
            batch_start = view
        gain = float(np.median(responses[-3:])) if responses else 1.
        drag = camera_drag(chart, view, target, gain=gain)
        session.emit(state='panning', camera_pans=session.camera_pans + 1, plan_camera_pans=count + 1,
                     camera_drag=drag.tolist(), camera_gain=gain,
                     message=f'正在平移画面寻找目标（{count + 1}/{max_pans}）…')
        before = goto.capture_client(session.win)
        pan_scene(session, drag)
        session.camera_pans += 1
        current = goto.capture_client(session.win)
        session.identity(current)
        session.check_collectible(current)
        session.preview(current)
        batch_pans += 1
        batch_drag += drag
        reason = 'prediction_failed'
        try:
            updated = predict_camera(chart, batch_start, batch_drag, gain)
            remaining = camera_offset(chart, updated, target)
            reason = ('no_scene_motion' if scene_unchanged(before, current) else
                      'pan_budget' if count + 1 == max_pans else
                      'batch_limit' if batch_pans >= 3 else
                      'relative_target_reached' if np.linalg.norm(remaining) < 8 else None)
        except ValueError:
            pass
        needs_fix = reason is not None
        if needs_fix:
            session.emit(camera_correction_reason=reason)
            updated = (observe_camera(session, squad_position, surface=surface) if calibration is not None
                       else observe_camera(session, squad_position))
            roi, click = target_projection(chart, updated, target)
            if (np.linalg.norm(project(updated['roi_to_map'], [243, 231])
                               - project(batch_start['roi_to_map'], [243, 231])) < 3
                    and not (inside(roi, ROI_BOUNDS) and inside(click, FIELD_BOUNDS))):
                raise ValueError('镜头已到边界或没有产生有效平移，未发送移动点击。')
            response = camera_response(chart, batch_start, updated, batch_drag)
            if response is not None:
                responses.append(response)
            batch_pans, batch_drag = 0, np.zeros(2)
        session.emit(camera_tracking='minimap' if needs_fix else 'predicted', camera_batch_pans=batch_pans)
        view = updated
        session.emit(camera_view_position=view['position'], camera_position_kind=view['position_kind'])
    raise AssertionError('Unreachable camera planning state')


def wait_for_squad(session):
    """直接采样实时紧凑地图，四帧道路与小队圆环都稳定后返回。

    圆环缺失或道路证据不足时重新累计；最多采样 120 轮，不展开会冻结画面的小地图面板。
    """
    from .perception import minimap_masks

    goto.map_close(session.win)
    samples = []
    for index in range(120):
        session.pause(1)
        session.check()
        field = goto.capture_client(session.win)
        session.identity(field)
        session.check_collectible(field)
        if index % 10 == 0:
            session.emit(state='moving', message='正在等待小队标记与地图道路停稳…', wait_samples=index + 1)
        try:
            _, road, valid, player = minimap_masks(field)
        except ValueError:
            samples = []
            session.emit(wait_player_compact=None)
            continue
        samples = [*samples, (road, valid, player)][-4:]
        session.emit(wait_player_compact=player.tolist())
        if len(samples) < 4 or np.max(np.ptp([s[2] for s in samples], axis=0)) > 6:
            continue
        # 圆环脉动及其遮挡不算道路位移；只比较每帧都可见的道路区域。
        common = np.logical_and.reduce([s[1] > 0 for s in samples])
        if np.count_nonzero((samples[0][0] > 0) & common) < 200:
            continue
        if all(np.mean((s[0] != samples[0][0])[common]) < .005 for s in samples[1:]):
            session.preview(field)
            return
    raise RuntimeError('等待小队停稳超时，未追加移动点击。')


def window_session(win, localizer, evidence, tag):
    """独立命令复用同一规划器，保留原命令的窗口与日志生命周期。

    把现有窗口、定位器和证据字典适配为规划器需要的会话接口。
    截图写入统一输出目录，状态合并到 evidence；窗口创建和释放仍由外层命令负责。
    """
    def identity(field):
        """检查客户区是否出现战斗准备弹窗。

        相关分数超过 0.8 时抛出 RuntimeError，使独立命令在规划下一次输入前停止。
        """
        if goto.battle_popup_score(field) > .8:
            raise RuntimeError('Battle popup; stop movement')

    return SimpleNamespace(win=win, localizer=localizer, folder=settings.output, index=0, tag=tag,
                           check=win.check, pause=runtime.pause, identity=identity, emit=evidence.update,
                           check_collectible=lambda field: None,
                           preview=lambda field: runtime.write_image(settings.output / f'{tag}_field.png', field))
