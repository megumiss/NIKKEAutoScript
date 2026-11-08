"""共享的远点镜头规划；小队位置与镜头观测分开保存，点击后重新确认小队停稳。"""

from types import SimpleNamespace

import cv2
import numpy as np

from module.campaign.map.geometry import jacobian
from module.campaign.navigation import controls as goto
from module.campaign.navigation import evidence as runtime
from module.campaign.report import CAMERA_TRACKING, LOCALIZATION, label
from module.campaign.vision import perception
from module.campaign.vision.surface_motion import project

FIELD_BOUNDS = (300, 220, 1450, 820)
ROI_BOUNDS = (12, 12, 474, 420)
# 横向利用场景全宽，纵向利用中部全高；两个矩形均避开固定 UI。
PAN_REGIONS = ((24, 320, 1752, 880), (250, 70, 1490, 880))
# 避让小队时的取样步长（客户区像素）。排除矩形覆盖原 120×150 椭圆的外接范围，
# 并额外留出箭头定位与角色动画余量；边界上的点也算危险，避免取整后重新压到小队。
SQUAD_AVOID_STEP = 20.
SQUAD_EXCLUDE_HALF_WIDTH = 150.
SQUAD_EXCLUDE_HALF_HEIGHT = 180.
# 落点至少离开小队脚下这么多客户区像素，否则会被游戏当成原地点击（38 章实机：连点三次注视点，
# 小队坐标只变了 1.2 地图像素）。这里不能用拖动按下点的排除矩形：那个区域管的是另一件事——
# 按下点压在小队身上会被读成走位指令，不能让一次镜头拖动变成移动命令。
DIRECT_CLICK_MIN = 12.
# 融合判据的容差 IoU 接受线：双方道路各膨胀 7px 后，正确位置实测 ~0.36、错位 ±60px 即跌到
# ~0.07~0.11（38 章失败帧紧凑面板离线复算，判别间隔 ≥3 倍）。膨胀量取底图投影道路与实时
# 道路的宽度差之半（底图 18px 对 live 4px），门槛 0.25 居中，既接住底图质量差的区域，又排除错位。
FUSED_IOU = .25
# 容差 IoU 的膨胀半径（面板像素）：紧凑面板底图投影道路 ~18px、实时道路 ~4px，各膨胀 7px 后
# 宽度对齐；展开帧场景（宽度基本一致）用同值偏保守但不改变判别方向。
FUSED_DILATE = 7


class CameraRegistrationError(ValueError):
    """展开道路配准未通过；仅用于区分可报告的视觉失败与不能捕获的窗口、取消及输入异常。"""


class SquadOffscreen(RuntimeError):
    """道路停稳后的单帧小队定位不可用，交由当前目标确认机关或执行既有失败处理。"""


def pan_region(drag):
    """选择能容纳指定客户区拖动向量的场景矩形。

    返回区域边界与不超过 1 的等比例缩放系数；两轴一起缩放，避免避让 UI 时改变方向。
    两块矩形都装得下时取余量更大的那块：按下点还要能沿拖动反方向躲开小队，余量不足时
    避让候选会被区域边界裁掉（38 章实测：478 像素的纵向拖动在 560 高的矩形里只剩 41 像素
    上下余量），所以「装得下」不足以作为选择依据。
    """
    drag = np.asarray(drag, float)

    def available(region):
        """计算候选矩形对拖动向量的容纳比例。

        零位移轴按至少一个像素计算分母，返回值用于比较横向和纵向区域，而不修改输入向量。
        """
        left, top, right, bottom = region
        return min(1., * (np.array([right - left, bottom - top]) / np.maximum(np.abs(drag), 1)))

    def room(region):
        """矩形装下向量之后剩下的最小余量，单位客户区像素；装不下时为负。"""
        left, top, right, bottom = region
        return float(min(right - left - abs(drag[0]), bottom - top - abs(drag[1])))

    region = max(PAN_REGIONS, key=lambda item: (available(item), room(item)))
    return region, available(region)


def inside(point, bounds):
    """判断二维像素点是否位于闭合矩形内。

    bounds 按左、上、右、下排列；非有限坐标返回 False，边界点视为有效。
    """
    x, y = np.asarray(point, float)
    left, top, right, bottom = bounds
    return bool(np.isfinite([x, y]).all() and left <= x <= right and top <= y <= bottom)


def pan_endpoints(region, drag, squad_ground=None):
    """生成场景拖动端点，并在按下点落在小队排除矩形内时整体平移手势到空地。

    region 是允许起终点出现的客户区矩形，drag 保持调用者计算的向量不变。squad_ground 必须是
    当前场景箭头向下换算的脚下点；None 表示跳过小队避让，但仍检查端点边界。排除矩形以脚下点
    为中心，半宽 150、半高 180，边界也算占用；没有可行空地时抛 ValueError，禁止发成走位点击。
    """
    drag = np.asarray(drag, float)
    left, top, right, bottom = region
    center = np.array([(left + right) / 2, (top + bottom) / 2])
    start = np.rint(center - drag / 2).astype(int)
    end = np.rint(center + drag / 2).astype(int)
    if squad_ground is not None:
        ground = np.asarray(squad_ground, float)
        exclusion = np.array([SQUAD_EXCLUDE_HALF_WIDTH, SQUAD_EXCLUDE_HALF_HEIGHT])

        def on_squad(point):
            return bool(np.all(np.abs(np.asarray(point, float) - ground) <= exclusion))

        if on_squad(start):
            start, end = pan_start_beside_squad(region, drag, start, end, ground, on_squad)
    if not inside(start, region) or not inside(end, region):
        raise ValueError('镜头拖动超出有效场景区域。')
    return start, end


def pan_start_beside_squad(region, drag, start, end, ground, on_squad):
    """按下点压在小队排除区时，在「起点与终点都合法」的矩形里找离原按下点最近的空地按下点。

    region 是允许起终点出现的矩形，drag 是必须原样保留的拖动向量，start/end 是原本算出的端点。
    返回 (新起点, 新终点)：终点由新起点加同一个向量得到，所以位移方向与长度不变。
    只在合法矩形里按 SQUAD_AVOID_STEP 取样（四个角一定在样本里），取排除区外离原按下点最近的那个。
    取样而不是解析投影：排除区可能被区域边界裁掉一角，解析「最近可行点」要分类讨论多种情况，
    而这里每帧只调用一次，几千个候选的代价可以忽略。
    矩形装不下向量、或整块矩形都落在小队排除区内时抛 ValueError，宁可失败也不把按下点放在小队身上。
    """
    low = np.maximum([region[0], region[1]], [region[0], region[1]] - np.asarray(drag, float))
    high = np.minimum([region[2], region[3]], [region[2], region[3]] - np.asarray(drag, float))
    if np.any(high < low):
        raise ValueError('拖动向量放不进这块场景区域，未发送手势。')
    vector = np.asarray(end) - np.asarray(start)
    samples = [np.append(np.arange(low[index], high[index], SQUAD_AVOID_STEP), high[index])
               for index in (0, 1)]
    origin = np.asarray(start, float)
    best = None
    for x in samples[0]:
        for y in samples[1]:
            point = np.rint([x, y])
            if on_squad(point):
                continue
            span = float(np.linalg.norm(point - origin))
            if best is None or span < best[0]:
                best = (span, point.astype(int))
    if best is None:
        raise ValueError('拖动起点被小队占用，安全区内没有可保留向量的空地起点。')
    start = best[1]
    return start, start + vector


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
    """用当前场景箭头避让后发送一次镜头拖动，不发送小队移动点击。

    每次拖动都在最新帧重新识别唯一箭头，并向下换算脚下点；箭头缺失时跳过小队避让，
    离屏标记仅记录跳过原因，不再作为拖动前提。不用历史地图坐标估算小队画面位置，
    不修改已知小队底图坐标。端点越界、窗口异常或驱动失败仍立即抛错，成功后等待场景稳定。
    """
    from module.campaign.vision.offscreen import squad_offscreen
    from module.campaign.vision.perception import squad_arrow

    goto.map_close(session.win)
    field = goto.capture_client(session.win)
    session.identity(field)
    arrow = squad_arrow(field)
    if arrow is not None:
        ground = np.asarray(arrow, float) + [0., goto.ARROW_GROUND_OFFSET]
        session.emit(squad_avoidance='arrow', squad_avoid_ground=ground.tolist())
    elif squad_offscreen(field) is not None:
        ground = None
        session.emit(squad_avoidance='offscreen')
    else:
        ground = None
        session.emit(squad_avoidance='skipped', message='未检测到小队画面标记，跳过拖动起点的小队避让')
    region, _ = pan_region(drag)
    start, end = pan_endpoints(region, drag, ground)
    session.check()
    origin = np.array(session.win.gui.ClientToScreen(session.win.hwnd, (0, 0)))
    session.win.handler.mouse_swipe(tuple(origin + start), tuple(origin + end))
    if session.win.handler._failures:
        raise RuntimeError('镜头平移失败，未发送移动点击。')
    session.pause(1.2)


def observe_camera(session, squad_position, *, known_squad=False):
    """允许小队离屏；接受的视野坐标只用于镜头，不更新冻结的小队位置。

    平移后重新配准展开地图。返回的视野参考只更新镜头位置；
    若可见小队偏离冻结坐标超过 12 地图像素，或定位未通过，则拒绝继续。
    known_squad 供未移动小队的纯镜头规划使用：不检测圆环，仅独立配准视野，不更新小队坐标。
    定位器用 CameraRegistrationError 报告道路配准失败，窗口、取消和输入异常原样传播。
    """
    session.check()
    session.identity(goto.capture_client(session.win))
    goto.map_open(session.win)
    image = session.win.capture()
    session.index += 1
    tag = f'{getattr(session, "tag", "camera")}_{session.index:03}'
    options = {'locate_player': False} if known_squad else {}
    report = session.localizer.locate(image, tag, require_player=False, **options)
    if np.mean(goto.terrain(image) != goto.terrain(session.win.capture())) > .03:
        raise CameraRegistrationError('镜头定位期间画面仍在变化。')
    if (report.get('position_kind') == 'squad'
            and np.linalg.norm(np.asarray(report['position']) - squad_position) > 12):
        from module.campaign.navigation.movement_feedback import SquadPositionChanged
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
    from module.campaign.navigation.movement_feedback import resolve_anchor
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
                         message='目标已进入可点击区域，准备发送移动点击')
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
    """采样紧凑小地图，连续四帧道路稳定后返回最后一帧客户区图像。

    普通移动与机关切镜使用同一判据，不读取小队圆环，也不将停稳视为到达或机关触发证据。
    四帧共同有效区域内至少保留 200 个道路像素，后续帧相对首帧的道路差异比例均小于 0.005。
    道路提取失败清空连续采样，最多采样 120 轮；取消、身份和收集提示仍经过原有闸门。
    返回后由调用方独立定位小队或确认光幕，超时不追加移动点击。
    """
    from module.campaign.vision.perception import compact_road_masks

    goto.map_close(session.win)
    roads = []
    for index in range(120):
        session.pause(1)
        session.check()
        field = goto.capture_client(session.win)
        session.identity(field)
        session.check_collectible(field)
        if index % 10 == 0:
            session.emit(state='moving', message='正在等待小地图道路停稳…', wait_samples=index + 1)
        try:
            _, road, valid = compact_road_masks(field)
        except ValueError:
            roads = []
            continue
        roads = [*roads, (road, valid)][-4:]
        if len(roads) < 4:
            continue
        common = np.logical_and.reduce([sample[1] > 0 for sample in roads])
        if (np.count_nonzero((roads[0][0] > 0) & common) < 200
                or any(np.mean((sample[0] != roads[0][0])[common]) >= .005 for sample in roads[1:])):
            continue
        session.preview(field)
        return field
    raise RuntimeError('等待小地图道路停稳超时，未追加移动点击。')


def window_session(win, localizer, evidence, tag, folder=None, pause=None):
    """独立命令复用同一规划器，保留原命令的窗口与日志生命周期。

    folder / pause 可由实验入口注入，默认使用生产证据目录与窗口状态检查。
    把现有窗口、定位器和证据字典适配为规划器需要的会话接口。
    截图写入统一输出目录，状态合并到 evidence；窗口创建和释放仍由外层命令负责。
    """
    def identity(field):
        """检查客户区是否出现战斗准备弹窗。

        相关分数超过 0.8 时抛出 RuntimeError，使独立命令在规划下一次输入前停止。
        """
        if goto.battle_popup_score(field) > .8:
            raise RuntimeError('Battle popup; stop movement')

    from pathlib import Path
    folder = Path(folder) if folder is not None else Path(__file__).resolve().parents[3] / 'log/campaign'
    package = localizer.package.path
    return SimpleNamespace(win=win, localizer=localizer, folder=folder, index=0, tag=tag, package=package,
                           check=win.check, pause=pause or (lambda seconds: goto.pause_window(win, seconds)),
                           identity=identity, emit=evidence.update,
                           check_collectible=lambda field: None,
                           preview=lambda field: runtime.write_image(folder / f'{tag}_field.png', field))


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


def target_view_offset(matrix, scale_a, gain, viewport, target):
    """目标相对镜头注视点的客户区像素偏移：面板局部 Jacobian 换算，再按实测跟手比例校正。

    matrix 是面板→地图单应、scale_a 是紧凑配准的缩放、viewport 是注视点的地图坐标。
    换算链与拖动幅度同源（见 panel_delta_to_client），所以拖动闭环测到的比例误差在这里同样成立：
    gain 小于 1 说明画面实际动得比换算预测的少，目标的屏幕偏移就要按同样比例放大，
    否则落点会停在小队与目标之间。gain 沿用闭环的接受区间下界兜底，避免除出离谱的偏移。
    单应退化（局部 Jacobian 不可逆）时算不出偏移，返回 None 交给调用方退回固定注视点像素。
    """
    delta = np.asarray(target, float) - np.asarray(viewport, float)
    try:
        delta_panel = np.linalg.solve(jacobian(matrix, goto.GAZE), delta)
    except np.linalg.LinAlgError:
        return None
    return panel_delta_to_client(scale_a, delta_panel) / max(float(gain), .2)


def landing_choice(matrix, scale_a, gain, viewport, target, squad_ground):
    """目标进入注视点容差后选客户区落点，返回 (落点, 落点算法名)；两个候选都不能用时返回 None。

    容差取 SQUAD_REACH：点完之后小队落在落点附近，目标仍在到达半径以内，可以继续下一拍。
    首选目标自己那个像素。相机本来是对着小队的、小队就站在注视点上，这时继续点注视点等于叫它
    走到自己脚下，游戏会把这一下当成原地点击（38 章实机：连点三次，小队坐标只变了 1.2 地图像素）。
    落点偏移用 target_view_offset 换算，换算误差由「点了会走、走完重新定位」这条闭环自己收敛，
    不会把小队送错方向——老实现怕的是「按 目标 − 小队 直接算屏幕向量」：小队坐标取自另一帧、
    差 148 地图像素时落点指到了水面上，游戏随后把小队带向反方向。这里用同一帧的注视点做基准，
    小队坐标不参与换算。
    目标像素落在有效场景区域之外、离小队太近（会被当成原地点击）、或换算不可用时，退回固定注视点
    像素：它是老落点，方向不依赖任何地图→屏幕换算。两个候选都不能用就返回 None，由调用方继续平移。
    """
    delta_map = np.asarray(target, float) - np.asarray(viewport, float)
    if float(np.linalg.norm(delta_map)) > goto.SQUAD_REACH:
        return None
    gaze = np.asarray(goto.VIEWPORT_PIXEL, float)
    offset = target_view_offset(matrix, scale_a, gain, viewport, target)
    candidates = [(gaze, 'compact_gaze')] if offset is None else \
        [(gaze + offset, 'direct_target'), (gaze, 'compact_gaze')]
    for point, method in candidates:
        if not inside(point, FIELD_BOUNDS):
            continue
        if squad_ground is not None and np.linalg.norm(point - np.asarray(squad_ground, float)) < DIRECT_CLICK_MIN:
            continue
        return point, method
    return None


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
    """起始帧的视野中心以已确认的小队定位为准：与之一致即采用，不要求本次配准状态为 accepted。

    注视点定位应与已确认的小队位置接近；返回偏差像素，超出 tolerance 抛 ValueError。
    细路视口的软重叠分会压在 match_panel 门槛附近，needs_review 只由调用者记录，不单独否决。
    """
    offset = float(np.linalg.norm(np.asarray(start['viewport'], float) - np.asarray(squad_position, float)))
    if offset > tolerance:
        raise ValueError(f"紧凑小地图与小队定位不一致（{label(LOCALIZATION, start['status'])}，偏差 {offset:.0f} 像素）。")
    return offset


def anchor_viewport(session, matrix, reference, squad_position, limit=900., *, known_squad=False):
    """批末展开小地图重新配准，只修正紧凑矩阵的平移项；不可用时返回 None。

    reference 是「展开 ROI 中心 → 注视点」的地图偏移，由起始帧同时测得的两种定位建立；
    这要求展开面板随镜头移动且本次规划期间镜头缩放不变，与拖动换算的前提一致。
    展开面板的道路证据远多于紧凑面板，用它清掉链式跟踪的累积漂移；只改平移项是因为
    局部比例与透视来自紧凑配准，本次校正不重新估计镜头缩放。
    修正量超过 limit 视为配准可疑并放弃；定位失败先收起面板，避免随后的落点点在展开面板上。
    known_squad 保持纯视野定位模式；只处理 CameraRegistrationError，不吞掉窗口或输入异常。
    """
    if reference is None:
        return None
    try:
        options = {'known_squad': True} if known_squad else {}
        report = observe_camera(session, squad_position, **options)
    except CameraRegistrationError as error:
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


def prepare_compact_reference(session, matcher, start, squad_position, *, known_squad=False):
    """首次镜头拖动前建立同一视野的展开中心与紧凑注视点偏移，不用小队坐标代替镜头基准。

    只开合地图，不回正或拖动；展开配准必须通过原门限。收图后重新检查身份、收集提示和
    紧凑道路，沿用点击复核的 12 地图像素、12% 尺度稳定界限。失败时不发送镜头或小队移动。
    返回新紧凑帧及其配准，调用方不得继续使用开图前的道路或脚圈证据。
    """
    options = {'known_squad': True} if known_squad else {}
    report = observe_camera(session, squad_position, **options)
    field = goto.capture_client(session.win)
    session.identity(field)
    session.check_collectible(field)
    session.preview(field)
    panel, road, valid = perception.compact_road_masks(field)
    current = compact_viewport(session, matcher, panel, road, valid)
    if (current['status'] != 'accepted'
            or np.linalg.norm(current['viewport'] - start['viewport']) > 12
            or abs(current['a'] / start['a'] - 1.) > .12):
        raise CameraRegistrationError('地图开合后紧凑视野或尺度未通过复核，不能建立镜头校正基准。')
    reference = current['viewport'] - project(np.asarray(report['roi_to_map']), goto.EXPANDED_CENTER)
    session.emit(camera_reference='accepted', camera_reference_offset=np.round(reference, 1).tolist())
    return field, panel, road, valid, current, reference


def confirm_ring_click(session, matcher, reference, field, viewport, scale_a, target, squad_position, click,
                       enemy_kind='normal', direction_assist=False):
    """重新识别点击前的新帧，只返回本帧再次确认的原目标脚圈中心。

    不以整片场景像素是否相同判断镜头稳定，避免河流或角色动画使有效匹配失效。
    enemy_kind 必须沿用原目标的普通／Boss 类型，最终复核不能退回普通敌人默认值。
    新帧道路必须独立配准通过，镜头偏移不超过 12 地图像素、尺度变化不超过 12%；
    reference 必须是运动或方向匹配成功的观测，期间不发新手势；最新帧只跟踪已确认脚圈，
    不重复拖动前参考帧的身份推断，中心偏移不超过 TRACK_DISTANCE 且双向关联无歧义。
    direction_assist 要求最新帧再次满足原目标的粗方向对应，不能在复核时改选其他敌人。
    本方法不拖动或开合地图。复核失败保留前后帧并抛 ClickUnconfirmed，禁止自动展开重定位；
    截图、窗口、弹窗和取消异常直接传播，不在安全异常后追加操作。
    """
    from module.campaign.navigation.movement_feedback import ClickUnconfirmed
    from module.campaign.vision.enemy_ring import observe_rings, track_confirmed_ring

    fresh = goto.capture_client(session.win)
    session.identity(fresh)
    session.check_collectible(fresh)
    try:
        panel, road, valid = perception.compact_road_masks(fresh)
        current = compact_viewport(session, matcher, panel, road, valid)
        shift = float(np.linalg.norm(np.asarray(current['viewport']) - viewport))
        scale_change = abs(current['a'] / scale_a - 1.)
        if current['status'] != 'accepted':
            raise ValueError(f"最新帧道路定位未通过（重合度 {current['iou']:.3f}）")
        if shift > 12 or scale_change > .12:
            raise ValueError(f'最新帧镜头位置或比例变化超限（镜头偏移 {shift:.1f} 地图像素，'
                             f'比例变化 {scale_change:.1%}）')
        rings = observe_rings(fresh, panel, current['matrix'], current['viewport'], track_scene=False)
        result = track_confirmed_ring(reference, rings, target, squad_position, click, enemy_kind=enemy_kind)
        if result.get('click') is None:
            session.emit(message=f'脚圈目标跟踪未通过：{result["reason"]}', enemy_ring_recheck=result)
            raise ValueError(result['reason'].rstrip('。'))
        if direction_assist:
            from module.campaign.vision.enemy_direction import match_target_direction

            direction = match_target_direction(fresh, rings, current['matrix'], target, squad_position,
                                               enemy_kind=enemy_kind)
            if direction.get('click') is None or not np.allclose(direction['click'], result['click']):
                raise ValueError(f'原目标方向复核未通过：{direction["reason"]}')
        latest_click = np.asarray(result['click'], float)
        center_shift = float(np.linalg.norm(latest_click - click))
    except (OSError, ValueError, RuntimeError, cv2.error, np.linalg.LinAlgError) as error:
        runtime.write_image(session.folder / 'enemy_ring_before.png', field)
        runtime.write_image(session.folder / 'enemy_ring_latest.png', fresh)
        session.preview(fresh)
        raise ClickUnconfirmed(f'脚圈点击复核未通过：{str(error).rstrip("。")}；停止执行，未发送移动点击。') from error
    session.preview(fresh)
    session.emit(message=f"脚圈点击复核通过：道路重合度 {current['iou']:.3f}，"
                         f'镜头偏移 {shift:.1f} 地图像素，脚圈中心偏移 {center_shift:.1f} 客户区像素',
                 enemy_ring_recheck=result)
    return latest_click


def plan_world_move_compact(session, observation, target, max_pans=12, batch_limit=3, enemy_assist=False,
                            enemy_kind='normal'):
    """镜头规划：先把目标平移到镜头注视点，再点落点把小队送过去。

    对准在地图空间进行：视野中心由道路配准给出，远处目标不经逆单应外推（透视地平线）。
    拖动量取注视点处局部 Jacobian 换算，比例误差由实测响应闭环修正；帧间链式跟踪兜底
    空旷走廊的歧义配准，全局配准可用时立即修正漂移。箭头锚点、场景单应与两点临时标定
    都不参与镜头换算；面板位移到客户区拖动只在注视点线性化，远距离整段外推会跨过
    ROI_TO_PLANE 的地平线并反向，见 panel_delta_to_client。
    目标进入注视点容差（SQUAD_REACH 地图像素）后由 landing_choice 给落点：优先点目标自己那个
    像素，不能用时才退回固定的 VIEWPORT_PIXEL。只点注视点会漏掉一种常态——相机本来就对着小队、
    小队正站在注视点上，这时再点注视点等于叫它走到自己脚下（38 章实机连点三次，小队坐标只变了
    1.2 地图像素），而目标离注视点还有十几像素时这一步就走不完。
    普通起始帧的视野中心以与已确认的小队定位一致为准（start_viewport），不要求 accepted。
    纯镜头找敌人后携带 squad_source=known 时，小队坐标取自出发前，当前视口可远离小队；
    此时只要求当前道路配准 accepted，不因视口与小队不重合而展开重新寻找圆环。
    紧凑报告的起点即使来自本帧实测，展开校正也只定位视野；不能把两种面板的小队配准
    差异当成实际位移。普通展开起点仍保留同源小队位置变化检查，真正移动后由外层重新观测。
    平面地图每批最多 batch_limit 次平移，批末或两种跟踪都不可用时展开小地图重新锚定平移项；
    全局配准被接受即视为批末校正已完成。
    实测（退回固定注视点时）：近 80 地图像素落点误差 4.7，远 990 像素（小队完全离屏）误差 11.0。
    enemy_assist 先以原目标相对小队的粗方向匹配点击脚圈，不比较两视图敌我距离；
    同向落点或标记存在竞争时不替换目标，再尝试既有两帧运动辅助，不额外拖动来收集证据。
    enemy_kind 将普通／Boss 原目标类型传到匹配和最终点击复核，不允许跨类替代。
    证据只在本次规划内保留，允许跨过一帧不可信定位，但不使用该帧参与匹配；两参考帧最多
    相隔两次平移，累计镜头路程不超过 450 地图像素。重新锚定、缩放跳变或超限时丢弃证据；
    辅助失败继续原落点和拖动流程，不为等待可靠帧增加拖动。
    来自紧凑快照的小队位置没有展开 ROI 基准；需要拖动时先开合地图建立同视野校正基准。
    已知小队模式只定位视野，不回正或重找圆环；基准失败在拖动前停止，不留到跟踪丢失后处理。
    单次预计面板位移不超过短边四分之一，为帧间道路保留重叠；总平移次数不增加。
    脚圈已匹配时，点击前的新帧由 confirm_ring_click 独立复核，不将复核失败交给展开恢复。
    生产执行器可提供 camera_enemy_check，在初始和拖后视野配准前交回已确认的可见近敌；
    回调不发输入或修改投影，未发现近敌时原导航照常执行，其它调用方默认不启用。
    """
    from module.campaign.vision.matching import MapMatcher, register
    from module.campaign.vision.enemy_ring import MAX_CAMERA_STEP, match_target_ring, observe_rings
    from module.campaign.vision.enemy_direction import match_target_direction

    session.last_plan_enemy_ring = False
    target = np.asarray(target, float)
    squad_position = np.asarray(observation['position'], float)
    viewport_only = observation.get('minimap_mode') == 'compact' or observation.get('squad_source') == 'known'
    if np.linalg.norm(target - squad_position) < 2:
        raise ValueError('当前落点与小队过近。')
    matcher = getattr(session, 'compact_matcher', None)
    if matcher is None:
        matcher = MapMatcher(session.package)
        session.compact_matcher = matcher
    goto.map_close(session.win)
    field = goto.capture_client(session.win)
    session.identity(field)
    session.check_collectible(field)
    session.preview(field)
    camera_enemy_check = getattr(session, 'camera_enemy_check', None)
    if callable(camera_enemy_check):
        camera_enemy_check(field)
    panel, road, valid = perception.compact_road_masks(field)
    start = compact_viewport(session, matcher, panel, road, valid)
    if observation.get('squad_source') == 'known':
        if start['status'] != 'accepted':
            raise RuntimeError('找敌人后的当前镜头配准未通过，不能使用旧视野矩阵规划移动。')
        session.emit(start_registration=start['status'], start_iou=round(float(start['iou']), 4))
    else:
        offset = start_viewport(start, squad_position)
        session.emit(start_registration=start['status'], start_iou=round(float(start['iou']), 4),
                     start_offset=round(offset, 1))
    if start['status'] != 'accepted':
            session.emit(message=f"起始紧凑配准状态 {start['status']}，道路重合度 {start['iou']:.3f}，"
                             f"小队定位偏差 {offset:.1f} 地图像素，满足位置一致性要求")
    reference = None
    if observation.get('roi_to_map') is not None:
        reference = (np.asarray(start['viewport'], float)
                     - project(np.asarray(observation['roi_to_map'], float), goto.EXPANDED_CENTER))
    elif (observation.get('minimap_mode') == 'compact'
          and np.linalg.norm(target - start['viewport']) > goto.SQUAD_REACH):
        field, panel, road, valid, start, reference = prepare_compact_reference(
            session, matcher, start, squad_position, known_squad=viewport_only)
        if callable(camera_enemy_check):
            camera_enemy_check(field)
    matrix, scale_a, road_prev = start['matrix'], start['a'], road
    ring_previous = None
    ring_pan, ring_scale, ring_path = 0, scale_a, 0.
    ring_trusted = start['status'] == 'accepted'
    gain, responses, stalled, batch_pans = 1., [], 0, 0
    session.camera_pans = getattr(session, 'camera_pans', 0)
    for count in range(max_pans + 1):
        session.check()
        viewport = project(matrix, goto.GAZE)
        delta_map = target - viewport
        squad_panel = np.linalg.solve(jacobian(matrix, goto.GAZE), squad_position - viewport)
        ground = np.asarray(goto.VIEWPORT_PIXEL, float) + panel_delta_to_client(scale_a, squad_panel)
        current_rings = None
        if enemy_assist and ring_trusted:
            try:
                current_rings = observe_rings(field, panel, matrix, viewport)
                result = match_target_direction(field, current_rings, matrix, target, squad_position,
                                                enemy_kind=enemy_kind)
            except (OSError, ValueError, RuntimeError, cv2.error, np.linalg.LinAlgError) as error:
                result = dict(reason=f'目标方向辅助不可用：{error}')
            session.emit(message=f'目标方向辅助：{result["reason"].rstrip("。")}',
                         enemy_direction_match=result)
            if result.get('click') is not None:
                click = confirm_ring_click(session, matcher, current_rings, field, viewport, scale_a,
                                           target, squad_position, np.asarray(result['click'], float),
                                           enemy_kind=enemy_kind, direction_assist=True)
                session.last_plan_enemy_ring = True
                session.emit(state='planning', navigation_method='enemy_direction', camera_pans=session.camera_pans,
                             plan_camera_pans=count, planned_target=target.tolist(), click=click.tolist(),
                             message='原目标方向及新帧复核通过，直接采用脚圈点击落点')
                return click
        if enemy_assist and ring_trusted and ring_previous is not None:
            try:
                current_rings = (current_rings if current_rings is not None
                                 else observe_rings(field, panel, matrix, viewport))
                result = match_target_ring(ring_previous, current_rings, target, squad_position, goto.VIEWPORT_PIXEL,
                                           enemy_kind=enemy_kind)
            except (OSError, ValueError, RuntimeError, cv2.error, np.linalg.LinAlgError) as error:
                result = dict(reason=f'脚圈辅助不可用：{error}')
            session.emit(message=f'脚圈辅助：{result["reason"].rstrip("。")}',
                         enemy_ring_match={key: value for key, value in result.items() if key != 'reason'},
                         enemy_ring_pan_span=count - ring_pan)
            if result.get('click') is not None:
                click = confirm_ring_click(session, matcher, current_rings, field, viewport, scale_a,
                                           target, squad_position, np.asarray(result['click'], float),
                                           enemy_kind=enemy_kind)
                session.last_plan_enemy_ring = True
                session.emit(state='planning', navigation_method='enemy_ring', camera_pans=session.camera_pans,
                             plan_camera_pans=count, planned_target=target.tolist(), click=click.tolist(),
                             message='目标脚圈两帧匹配通过，采用脚圈中心作为点击落点')
                return click
        # 本次一步平移都还没发时不拿 1 当跟手比例：落点偏移会缩水一半（见 target_view_offset），
        # 上一次会话测到的比例比 1 更接近当前相机。
        landing_gain = gain if responses else float(getattr(session, 'camera_gain', 1.))
        landing = landing_choice(matrix, scale_a, landing_gain, viewport, target, ground)
        if landing is not None:
            click, method = landing
            span = f'{float(np.linalg.norm(delta_map)):.1f} 地图像素'
            session.emit(state='planning', navigation_method=method, camera_pans=session.camera_pans,
                         plan_camera_pans=count, planned_target=target.tolist(),
                         click=np.round(click, 1).tolist(),
                         click_offset=np.round(click - np.asarray(goto.VIEWPORT_PIXEL, float), 1).tolist(),
                         camera_view_position=np.round(viewport, 1).tolist(),
                         camera_view_offset=np.round(delta_map, 1).tolist(),
                         message=(f'目标已进入注视点容差范围，偏差 {span}，'
                                  + ('采用目标投影点作为点击落点' if method == 'direct_target'
                                     else '采用镜头注视点作为点击落点')))
            return click
        if count == max_pans:
            raise ValueError(f'已平移 {max_pans} 次，目标仍未到小地图中央；未发送移动点击。')
        if enemy_assist and ring_trusted:
            ring_previous = None
            try:
                ring_previous = (current_rings if current_rings is not None
                                 else observe_rings(field, panel, matrix, viewport))
                ring_pan, ring_scale, ring_path = count, scale_a, 0.
                session.emit(message=f'脚圈辅助参考帧已更新：平移序号 {count}，'
                                     f'可点击区域内脚圈 {len(ring_previous.centers)} 个')
            except (OSError, ValueError, RuntimeError, cv2.error, np.linalg.LinAlgError) as error:
                session.emit(message=f'脚圈辅助观测不可用，继续原有镜头规划：{error}')
        delta_panel = np.linalg.solve(jacobian(matrix, goto.GAZE), delta_map)
        drag = -panel_delta_to_client(scale_a, delta_panel) / gain
        _, region_scale = pan_region(drag)
        overlap_scale = min(1., min(road.shape) * .25 / max(np.linalg.norm(delta_panel) / gain, 1e-9))
        scale = min(region_scale, overlap_scale)
        drag = drag * scale
        commanded_delta = delta_map * scale
        if np.linalg.norm(drag) < 6:
            raise ValueError('剩余位移落入镜头拖动死区；未发送移动点击。')
        session.emit(state='panning', camera_pans=session.camera_pans + 1, plan_camera_pans=count + 1,
                     camera_drag=drag.tolist(), camera_gain=gain, camera_pan_scale=round(scale, 3),
                     message=f'正在平移画面寻找目标（{count + 1}/{max_pans}）…')
        # 拖动指令换算的预期视野落点：融合判据的位置先验。响应未知（首拖 gain=1）时先验
        # 半径按残差自适应放宽，见 fusion_acceptable。
        predicted = viewport + commanded_delta * min(1. / gain, 1.5)
        pan_scene(session, drag)
        field = goto.capture_client(session.win)
        session.identity(field)
        session.check_collectible(field)
        session.preview(field)
        if callable(camera_enemy_check):
            camera_enemy_check(field)
        panel, road, valid = perception.compact_road_masks(field)
        chained = None
        match = register(road_prev, road, valid)
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
            if ring_previous is not None:
                session.emit(message='脚圈辅助参考帧已失效：需要展开地图重新锚定')
            ring_previous = None
            if reference is None and observation.get('minimap_mode') == 'compact':
                raise ValueError('镜头跟踪需要展开地图基准，不能继续沿用紧凑小队定位。')
            anchor_options = {'known_squad': True} if viewport_only else {}
            anchored = anchor_viewport(session, matrix if use_matrix is None else use_matrix,
                                       reference, squad_position, **anchor_options)
            if anchored is not None:
                use_matrix, source, batch_pans = anchored, 'expanded', 0
        if use_matrix is None:
            raise ValueError('帧间跟踪、全局对齐与展开地图重新锚定都不可用；未发送移动点击。')
        ring_trusted = source == 'accepted'
        new_viewport = project(use_matrix, goto.GAZE)
        actual = new_viewport - viewport
        if ring_previous is not None:
            ring_path += float(np.linalg.norm(actual))
            span = count + 1 - ring_pan
            scale_change = max(abs(use_a / scale_a - 1.), abs(use_a / ring_scale - 1.))
            invalid = None
            if scale_change > .12:
                invalid = f'面板比例变化 {scale_change:.1%}，超过 12%'
            elif span > 2 or (not ring_trusted and span >= 2):
                invalid = '连续两帧定位未确认'
            elif ring_path > MAX_CAMERA_STEP:
                invalid = f'累计镜头路程 {ring_path:.1f}，超过 {MAX_CAMERA_STEP:.0f} 地图像素'
            if invalid is not None:
                ring_previous = None
                session.emit(message=f'脚圈辅助参考帧已失效：{invalid}')
        if enemy_assist and not ring_trusted:
            retained = '保留参考帧，等待下一可靠帧' if ring_previous is not None else '无可用参考帧'
            session.emit(message=f'脚圈辅助跳过本帧：镜头定位 {label(CAMERA_TRACKING, source)}；{retained}')
        response = float(actual @ commanded_delta) / float(commanded_delta @ commanded_delta)
        session.emit(camera_tracking=source, camera_response=response, camera_batch_pans=batch_pans)
        if .2 <= response <= 3.:
            responses.append(response)
            gain = float(np.median(responses[-3:]))
            # 记到会话上：下一次规划一开始还没拖动就要用落点换算，那时拿不到本批的实测比例。
            session.camera_gain = gain
            stalled = 0
        else:
            stalled += 1
            if stalled >= 2:
                raise ValueError('连续两次平移没有产生有效位移；未发送移动点击。')
        matrix, scale_a = use_matrix, use_a
        road_prev = road
        session.camera_pans += 1
    raise AssertionError('Unreachable camera planning state')
