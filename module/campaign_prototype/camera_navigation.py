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
# 融合判据的容差 IoU 接受线：双方道路各膨胀 7px 后，正确位置实测 ~0.36、错位 ±60px 即跌到
# ~0.07~0.11（38 章失败帧紧凑面板离线复算，判别间隔 ≥3 倍）。膨胀量取底图投影道路与实时
# 道路的宽度差之半（底图 18px 对 live 4px），门槛 0.25 居中，既接住底图质量差的区域，又排除错位。
FUSED_IOU = .25
# 容差 IoU 的膨胀半径（面板像素）：紧凑面板底图投影道路 ~18px、实时道路 ~4px，各膨胀 7px 后
# 宽度对齐；展开帧场景（宽度基本一致）用同值偏保守但不改变判别方向。
FUSED_DILATE = 7


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


def field_chart(localizer, observation, anchor):
    """从实测箭头锚点绑定 ROI→场景投影，保留透视项而不外推单点 Jacobian。

    observation 必须包含真实小队的地图坐标、player_roi 和 ROI→map 矩阵，anchor 使用客户区像素。
    返回以箭头锚点校正平移项的 ROI→场景单应。
    """
    if observation.get('position_kind') != 'squad' or observation.get('player_roi') is None:
        raise ValueError('必须先定位真实小队，才能建立镜头移动基准。')
    if np.linalg.norm(project(observation['roi_to_map'], observation['player_roi']) - observation['position']) > 2:
        raise ValueError('小队坐标与当前地图投影不一致。')
    affine = np.eye(3)
    affine[:2, :2] = goto.A_INV
    chart = affine @ np.asarray(localizer.old_matrix, float)
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


def observe_camera(session, squad_position):
    """允许小队离屏；接受的视野坐标只用于镜头，不更新冻结的小队位置。

    平移后重新配准展开地图。返回的视野参考只更新镜头位置；
    若可见小队偏离冻结坐标超过 12 地图像素，或定位未通过，则拒绝继续。
    """
    session.check()
    session.identity(goto.capture_client(session.win))
    goto.map_open(session.win)
    image = session.win.capture()
    session.index += 1
    tag = f'{getattr(session, "tag", "camera")}_{session.index:03}'
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


def plan_world_move(session, observation, target, max_pans=8, anchor=None):
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
    chart = field_chart(session.localizer, observation, anchor)
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
            updated = observe_camera(session, squad_position)
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


def panel_delta_to_client(a, delta):
    """紧凑面板位移换算为客户区位移：A_INV @ ROI_TO_PLANE 链在注视点处线性化。

    远点需要的面板位移可达成百上千像素，整段外推会跨过 ROI_TO_PLANE 的透视地平线，
    越界后 x/w、y/w 符号翻转，客户区拖动随之反向（镜头朝目标反方向撞边界）。
    这里只在 EXPANDED_CENTER 附近线性化，幅度误差交给响应闭环修正；
    单位面板位移约 2.7 客户区像素，此处的 Jacobian 与整段外推在界内时差异小于 1 像素。
    """
    affine = np.eye(3)
    affine[:2, :2] = goto.A_INV
    chain = affine @ goto.ROI_TO_PLANE
    base = np.asarray(goto.EXPANDED_CENTER, float)
    linear = np.column_stack([project(chain, base + step) - project(chain, base)
                              for step in np.eye(2)])
    return linear @ (a * np.asarray(delta, float))


def squad_ground(image):
    """小队脚下的客户区像素：箭头锚点向下补偿 ARROW_GROUND_OFFSET 得到脚点。

    箭头不可信（零个或多个候选）时返回 None，调用者据此判定小队不在画面里。
    """
    arrow = perception.squad_arrow(image)
    return None if arrow is None else np.asarray(arrow, float) + [0., goto.ARROW_GROUND_OFFSET]


def squad_click(matrix, scale, anchor, observation, target, bounds=FIELD_BOUNDS):
    """小队相对落点：小队脚下像素加上「目标 − 小队」的客户区相对位移。

    observation 必须来自真实小队定位（position_kind='squad'），其 position 是权威小队坐标；
    anchor 由 squad_ground 给出。相对向量在地图坐标里做完差，再用闭环拖动用的同一条换算链
    （注视点局部 Jacobian + A_INV @ ROI_TO_PLANE）换到客户区，因此平移偏差在做差与锚定中抵消，
    落点不使用视野中心像素与 VIEWPORT_PIXEL；尺度沿用响应增益闭环验证过的换算。
    返回客户区点击点；箭头不可信、小队定位非权威、单应退化、相对向量跨过投影地平线或落点
    越出 bounds 时返回 None，表示当前还轮不到小队相对落点（典型是小队已不在画面里）。
    """
    if anchor is None or observation.get('position_kind') != 'squad':
        return None
    try:
        delta_map = np.asarray(target, float) - np.asarray(observation['position'], float)
        delta_panel = np.linalg.solve(jacobian(matrix, goto.GAZE), delta_map)
        click = np.asarray(anchor, float) + panel_delta_to_client(scale, delta_panel)
    except (np.linalg.LinAlgError, ValueError):
        return None
    return click if inside(click, bounds) else None


def tolerance_iou(live_road, panel_valid, base_road, matrix, dilate=FUSED_DILATE):
    """把底图道路按矩阵拉回面板坐标，与实时面板道路各膨胀 dilate 像素后计算容差交并比。

    底图道路渲染宽度与实时面板不一致（紧凑面板实测底图投影 ~18px 对 live ~4px），
    逐像素硬碰会把正确位置的分数压到门槛以下；双方膨胀后宽度对齐，正确位置约 0.36、
    错位 ±60px 跌到 ~0.07~0.11，判别间隔 ≥3 倍（38 章失败帧离线复算）。
    live_road、panel_valid 为面板坐标（膨胀、腐蚀都在面板坐标系内做），
    base_road 为地图像素底图道路，matrix 为面板→地图单应。
    返回容差 IoU 浮点数；任何输入为空时返回 0.0。
    """
    if base_road is None or not np.any(base_road) or not np.any(live_road):
        return 0.
    base = (np.asarray(base_road, float)[..., None] * 255).astype(np.uint8)
    base = base[..., 0] if base.ndim == 3 else base
    recovered = cv2.warpPerspective(base, np.linalg.inv(np.asarray(matrix, float)),
                                    np.asarray(live_road.shape)[::-1], flags=cv2.INTER_NEAREST) > 0
    kernel = np.ones((2 * dilate + 1, 2 * dilate + 1), np.uint8)
    live = cv2.dilate((live_road > 0).astype(np.uint8), kernel) > 0
    mapped = cv2.dilate(recovered.astype(np.uint8), kernel) > 0
    common = cv2.erode((panel_valid > 0).astype(np.uint8), kernel) > 0
    intersection = float((live & mapped & common).sum())
    union = float(((live | mapped) & common).sum())
    return intersection / union if union else 0.


def fusion_acceptable(session, candidate, matrix, road, valid, origin, predicted, distance_old, target):
    """观测融合判据：needs_review 候选凭「拖动走廊先验 + 容差 IoU + 推进/位移合理性」接受。

    绝对 IoU 门槛同时检验位置正确性与底图质量，底图质量差的区域（38 章 observed_roads
    覆盖，失败区域 IoU 天花板 ~0.67）会冤杀正确位置。本判据改问三件事：候选位置落在
    「拖动起点→期望落点」走廊内（响应不足或过冲都在走廊上，偏离方向即拒绝，半宽随
    残差自适应）、容差 IoU 达标（形状真伪由 0.36 对 0.07~0.11 的判别间隔把关）、
    并保持既有 sane 与推进条件。底图道路模板缺失（localizer.road 缺失）或
    无预测先验时返回 False，维持原有保守行为。
    返回 True 表示该候选可作为本帧镜头矩阵。
    """
    base_road = getattr(session.localizer, 'road', None)
    if base_road is None or predicted is None:
        return False
    viewport = np.asarray(candidate['viewport'], float)
    origin = np.asarray(origin, float)
    predicted = np.asarray(predicted, float)
    # 先验是拖动走廊而非圆形半径：响应不足时正确落点在走廊中段，圆形先验会把正确位置
    # 拒掉（失败现场实测先验距离 386 > 半径 374）；偏离走廊方向的错位由半宽排除，
    # 半宽下限由错位对照（±60px 被拒）约束。
    segment = predicted - origin
    length = float(segment @ segment)
    half_width = max(100., .35 * distance_old)
    if length <= 0:
        deviation = float(np.linalg.norm(viewport - predicted))
    else:
        t = float((viewport - origin) @ segment) / length
        deviation = float(np.linalg.norm(viewport - (origin + segment * np.clip(t, 0., 1.5))))
    if deviation > half_width:
        return False
    score = tolerance_iou(road, valid, base_road, matrix)
    if score < FUSED_IOU:
        return False
    distance_new = float(np.linalg.norm(viewport - np.asarray(target, float)))
    if distance_new >= distance_old and distance_new > goto.SQUAD_REACH_WIDE:
        return False
    session.emit(fused_iou=round(score, 3), fused_deviation=round(deviation, 1))
    return True



def compact_viewport(session, matcher, panel, road, valid):
    """紧凑面板的无种子视野定位：用道路配准。

    返回 dict(matrix, a, viewport, iou, status)，matrix 为面板→地图单应；失败抛 ValueError。
    """
    result = matcher.match_panel(panel, road, valid, np.array([panel.shape[1] / 2, panel.shape[0] / 2]))
    matrix = np.asarray(result['roi_to_map'], float)
    return dict(matrix=matrix, a=float(result['a']), viewport=project(matrix, goto.GAZE),
                iou=float(result['road_iou']), status=result['status'])


def start_viewport(start, squad_position, tolerance=60.):
    """起始帧的视野中心以展开图小队定位为准：与之一致即采用，不要求配准状态为 accepted。

    相机经 observe 复位在小队上，注视点定位应与小队位置接近；返回偏差像素，超出 tolerance 抛 ValueError。
    细路视口的软重叠分会压在 match_panel 门槛附近，needs_review 只由调用者记录，不单独否决。
    """
    offset = float(np.linalg.norm(np.asarray(start['viewport'], float) - np.asarray(squad_position, float)))
    if offset > tolerance:
        raise ValueError(f"紧凑小地图与展开地图定位不一致（{start['status']}，偏差 {offset:.0f}px）。")
    return offset


def anchor_viewport(session, matrix, reference, squad_position, limit=900.):
    """批末展开小地图重新配准，只修正紧凑矩阵的平移项；不可用时返回 None。

    reference 是「展开 ROI 中心 → 注视点」的地图偏移，由起始帧同时测得的两种定位建立；
    这要求展开面板随镜头移动且本次规划期间镜头缩放不变，与拖动换算的前提一致。
    展开面板的道路证据远多于紧凑面板，用它清掉链式跟踪的累积漂移；只改平移项是因为
    局部比例与透视来自紧凑配准，本次校正不重新估计镜头缩放。
    修正量超过 limit 视为配准可疑并放弃；定位失败先收起面板，避免随后的落点点在展开面板上。
    """
    from .movement_feedback import SquadPositionChanged

    if reference is None:
        return None
    try:
        report = observe_camera(session, squad_position)
    except SquadPositionChanged:
        raise
    except (RuntimeError, ValueError) as error:
        session.emit(camera_anchor='failed', camera_anchor_error=str(error))
        goto.map_close(session.win)
        return None
    gaze = project(np.asarray(report['roi_to_map'], float), goto.EXPANDED_CENTER) + reference
    shift = gaze - project(matrix, goto.GAZE)
    if float(np.linalg.norm(shift)) > limit:
        session.emit(camera_anchor='rejected', camera_anchor_shift=np.round(shift, 1).tolist())
        return None
    translation = np.eye(3)
    translation[:2, 2] = shift
    session.emit(camera_anchor='accepted', camera_anchor_shift=np.round(shift, 1).tolist(),
                 camera_anchor_viewport=np.round(gaze, 1).tolist())
    return translation @ matrix


def plan_world_move_compact(session, observation, target, max_pans=12, batch_limit=3):
    """镜头规划：目标进入可点范围时按小队相对位置落点，否则先平移接近。

    对准在地图空间进行：视野中心由道路配准给出，远处目标不经逆单应外推（透视地平线）。
    拖动量取注视点处局部 Jacobian 换算，比例误差由实测响应闭环修正；帧间链式跟踪兜底
    空旷走廊的歧义配准，全局配准可用时立即修正漂移。箭头锚点、场景单应与两点临时标定
    都不参与镜头换算；面板位移到客户区拖动只在注视点线性化，远距离整段外推会跨过
    ROI_TO_PLANE 的地平线并反向，见 panel_delta_to_client。
    落点由 squad_click 给出：锚点是小队脚下像素（箭头锚点，不用 VIEWPORT_PIXEL），位移是
    「目标 − 小队」经拖动用的同一条换算链（注视点局部 Jacobian + A_INV @ ROI_TO_PLANE）得到的
    客户区向量。相对向量在地图坐标里做完差，平移偏差抵消，所以落点不依赖视野中心；尺度沿用
    响应增益闭环验证过的换算。点固定注视点等于把落脚点让给相机自己，相机已在小队身上时就是点
    小队脚下，游戏不会移动，闭环接受的残差会永久留在落点上，探针点也会退化成同一像素的重复点击。
    小队不在画面里（远目标）时没有箭头锚点，只能先把目标拖到注视点、再点注视点把小队送到相机
    所在处，那是接近手段。
    起始帧的视野中心以与展开图小队定位一致为准（start_viewport），不要求 accepted。
    平面地图每批最多 batch_limit 次平移，批末或两种跟踪都不可用时展开小地图重新锚定平移项；
    全局配准被接受即视为批末校正已完成。
    实测（点击固定注视点像素时）：近 80 地图像素落点误差 4.7，远 990 像素（小队完全离屏）误差 11.0。
    """
    from module.campaign.matching import MapMatcher

    target = np.asarray(target, float)
    squad_position = np.asarray(observation['position'], float)
    if np.linalg.norm(target - squad_position) < 2:
        raise ValueError('当前落点与小队过近。')
    matcher = getattr(session, 'compact_matcher', None)
    if matcher is None:
        matcher = MapMatcher(settings.package)
        session.compact_matcher = matcher
    goto.map_close(session.win)
    field = goto.capture_client(session.win)
    session.identity(field)
    session.check_collectible(field)
    session.preview(field)
    panel, road, valid = perception.compact_road_masks(field)
    start = compact_viewport(session, matcher, panel, road, valid)
    offset = start_viewport(start, squad_position)
    session.emit(start_registration=start['status'], start_iou=round(float(start['iou']), 4),
                 start_offset=round(offset, 1))
    if start['status'] != 'accepted':
        session.emit(message=f"起始紧凑配准为 {start['status']}（iou {start['iou']:.3f}），"
                             f"与展开图小队定位偏差 {offset:.1f}px，按一致采用。")
    matrix, scale_a, road_prev = start['matrix'], start['a'], road
    gain, responses, stalled, batch_pans = 1., [], 0, 0
    reference = None
    if matcher is not None and observation.get('roi_to_map') is not None:
        reference = (np.asarray(start['viewport'], float)
                     - project(np.asarray(observation['roi_to_map'], float), goto.EXPANDED_CENTER))
    session.camera_pans = getattr(session, 'camera_pans', 0)
    for count in range(max_pans + 1):
        session.check()
        # 落点优先走小队相对：箭头锚定 + 「目标 − 小队」相对向量，不使用视野中心。
        anchor = squad_ground(field)
        click = squad_click(matrix, scale_a, anchor, observation, target)
        if click is not None:
            session.emit(state='planning', navigation_method='squad_anchor', camera_pans=session.camera_pans,
                         plan_camera_pans=count, planned_target=target.tolist(),
                         click=np.round(click, 1).tolist(),
                         squad_anchor=np.round(anchor, 1).tolist(),
                         click_offset=np.round(click - np.asarray(goto.VIEWPORT_PIXEL, float), 1).tolist(),
                         message='目标已进入可点击范围，按小队相对位置点击并交给游戏寻路。')
            return click
        viewport = project(matrix, goto.GAZE)
        delta_map = target - viewport
        # 小队不在画面里（远目标）时没有箭头锚点，小队相对落点无从谈起：只能把目标拖到注视点后
        # 点注视点，让小队走到相机所在处。这是接近手段，不是微调，所以落点仍是固定像素。
        # 小队仍在画面里却没有可用落点（相对向量越界或跨地平线）时不退化成点注视点——相机就在
        # 小队身上，那等于点小队脚下、点了也不会动——直接失败交给外层重新观测。
        if float(np.linalg.norm(delta_map)) <= goto.SQUAD_REACH:
            if anchor is not None:
                raise ValueError('小队在画面内但小队相对落点不可用；未发送移动点击。')
            session.emit(state='planning', navigation_method='compact_gaze', camera_pans=session.camera_pans,
                         plan_camera_pans=count, planned_target=target.tolist(),
                         click=list(goto.VIEWPORT_PIXEL),
                         message='小队不在画面内，先把小队送到镜头注视点。')
            return np.asarray(goto.VIEWPORT_PIXEL, float)
        if count == max_pans:
            raise ValueError(f'已平移 {max_pans} 次，目标仍未到小地图中央；未发送移动点击。')
        delta_panel = np.linalg.solve(jacobian(matrix, goto.GAZE), delta_map)
        drag = -panel_delta_to_client(scale_a, delta_panel) / gain
        _, scale = pan_region(drag)
        drag = drag * scale
        if np.linalg.norm(drag) < 6:
            raise ValueError('剩余位移落入镜头拖动死区；未发送移动点击。')
        session.emit(state='panning', camera_pans=session.camera_pans + 1, plan_camera_pans=count + 1,
                     camera_drag=drag.tolist(), camera_gain=gain,
                     message=f'正在平移画面寻找目标（{count + 1}/{max_pans}）…')
        # 拖动指令换算的预期视野落点：融合判据的位置先验。响应未知（首拖 gain=1）时先验
        # 半径按残差自适应放宽，见 fusion_acceptable。
        predicted = viewport + delta_map * min(1. / gain, 1.5)
        pan_scene(session, drag)
        field = goto.capture_client(session.win)
        session.identity(field)
        session.check_collectible(field)
        session.preview(field)
        panel, road, valid = perception.compact_road_masks(field)
        chained = None
        match = goto.mr.register(road_prev, road, valid)
        if match is not None and match['iou'] >= .5 and match['area'] >= 80:
            sx, sy = match['delta']
            chained = matrix @ np.array([[1., 0., -sx], [0., 1., -sy], [0., 0., 1.]])
        use_matrix, use_a, source = None, scale_a, 'chained'
        try:
            candidate = compact_viewport(session, matcher, panel, road, valid)
        except ValueError:
            candidate = None
        if candidate is not None:
            if candidate['status'] == 'accepted':
                use_matrix, use_a, source = candidate['matrix'], candidate['a'], 'accepted'
            else:
                cand_viewport = candidate['viewport']
                distance_new = float(np.linalg.norm(cand_viewport - target))
                progress = distance_new < float(np.linalg.norm(viewport - target))
                sane = float(np.linalg.norm(cand_viewport - viewport)) <= 900.
                # 细路视口的软重叠分约 0.84 封顶；门槛取 0.7，可信度由推进与位移合理性共同判定。
                # 圆环罩住目标（长轴半径）后，低分配准的误接受由到点判定兜底，可按覆盖范围放宽。
                if candidate['iou'] >= .7 and sane and (progress or distance_new <= goto.SQUAD_REACH_WIDE):
                    use_matrix, use_a, source = candidate['matrix'], candidate['a'], 'needs_review'
                elif sane and (progress or distance_new <= goto.SQUAD_REACH_WIDE) and fusion_acceptable(
                        session, candidate, candidate['matrix'], road, valid, viewport, predicted,
                        float(np.linalg.norm(viewport - target)), target):
                    # 绝对 IoU 门槛同时检验位置与底图质量，底图质量差的区域会冤杀正确位置；
                    # 融合判据用拖动走廊先验 + 容差 IoU + 推进条件替代，错位候选由
                    # 0.36 对 0.07~0.11 的容差判别间隔排除，误接受由响应闭环与到点权威定位兜底。
                    use_matrix, use_a, source = candidate['matrix'], candidate['a'], 'fused'
        if use_matrix is None and chained is not None:
            use_matrix = chained
        batch_pans = 0 if source == 'accepted' else batch_pans + 1
        if source != 'accepted' and (use_matrix is None or batch_pans >= batch_limit):
            anchored = anchor_viewport(session, matrix if use_matrix is None else use_matrix,
                                       reference, squad_position)
            if anchored is not None:
                use_matrix, source, batch_pans = anchored, 'expanded', 0
        if use_matrix is None:
            raise ValueError('帧间跟踪、全局配准与批末展开都不可用；未发送移动点击。')
        new_viewport = project(use_matrix, goto.GAZE)
        actual = new_viewport - viewport
        response = float(actual @ delta_map) / float(delta_map @ delta_map)
        session.emit(camera_tracking=source, camera_response=response, camera_batch_pans=batch_pans)
        if .2 <= response <= 3.:
            responses.append(response)
            gain = float(np.median(responses[-3:]))
            stalled = 0
        else:
            stalled += 1
            if stalled >= 2:
                raise ValueError('连续两次平移没有产生有效位移；未发送移动点击。')
        matrix, scale_a = use_matrix, use_a
        road_prev = road
        session.camera_pans += 1
    raise AssertionError('Unreachable camera planning state')
