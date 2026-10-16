"""生产任务的移动会话：复用原型导航闭环，设备与状态由 Campaign 持有。"""

import time
from pathlib import Path

import cv2
import numpy as np

from module.base.utils import publish_preview_frame
from module.campaign.navigation import controls as goto
from module.campaign.navigation.movement_feedback import TargetTriggered, collectible_indicator
from module.campaign.navigation.pathfinding import NoConnectedRoad
from module.campaign.navigation.pathfinding import RoadLocalizer as Localizer
from module.campaign.navigation.session import NavigationSession
from module.campaign.vision.chapter import chapter_identity
from module.campaign.vision.minimap import terrain
from module.campaign.vision.observation import EXPANDED_ROI
from module.campaign.vision.perception import detect_markers, project_centers
from module.campaign.vision.surface_motion import project
from module.campaign.window import CampaignWindow, WindowChanged
from module.config.config import TaskEnd
from module.logger import logger

# 单次执行（一章）的时限：到点后不再发新输入，等待中也停下交人工。按时间而不是输入次数限，
# 是因为镜头拖动、小地图开合与侦察拖动的次数随地形差别很大，次数上限要么误停要么形同虚设。
CHAPTER_TIME_LIMIT = 20 * 60
# 单个目标的输入次数上限（含场景手势与小地图开合），挡住单个目标上的原地空转。
TARGET_INPUT_BUDGET = 32
# 侦察拖动：每屏最多拖几次把落点拖到面板中心、落点离中心多近就不再拖、单次拖动的最长距离
# （客户区像素，起止点以面板中心对称，保证都落在 486×462 面板内）与拖完后的停稳等待。
SCOUT_DRAGS = 3
SCOUT_CENTER_TOLERANCE = 60.
SCOUT_DRAG_MAX = 300.
SCOUT_SETTLE_SECONDS = 1.0


class InputBudgetExceeded(RuntimeError):
    """单次执行超过时限或单目标输入预算耗尽；不可降级为普通目标失败后继续点击。"""


class CampaignInput:
    """生产驱动代理：每次新手势先校验会话并扣预算，释放操作不受停止与失焦限制。"""

    def __init__(self, session, handler):
        """只保存代理关系，不取得新驱动；驱动仍由任务调度器管理。"""
        self.session, self.handler = session, handler

    def __getattr__(self, name):
        """仅包装产生输入的方法；mouse_up 直通，保证取消后仍能释放已按下鼠标。"""
        value = getattr(self.handler, name)
        if name not in ('mouse_click', 'mouse_swipe', 'mouse_down', 'mouse_move'):
            return value

        def send(*args, **kwargs):
            """发送前校验窗口、章节与预算，发送后任一次驱动失败都立即停止。"""
            self.session.before_input()
            result = value(*args, **kwargs)
            if getattr(self.handler, '_failures', 0):
                raise WindowChanged(f'主线输入驱动发送 {name} 失败。')
            return result

        return send


class CampaignLocalizer:
    """给共享闭环提供定位与道路接口；配准看原图，寻路只看最新切开图。"""

    def __init__(self, matcher, cut):
        """使用已绑定章节的 MapMatcher，避免读取原型的全局默认章节或修改全局设置。"""
        self.matcher, self.cut = matcher, cut
        self.old_matrix = goto.ROI_TO_PLANE

    @property
    def road(self):
        """当前封锁切开后的道路；解封更新 cut 后不需要重建定位器。"""
        return self.cut.road

    @property
    def route_labels(self):
        """共享 A* 所需的八像素连通分区，与 road 同一修订。"""
        return self.cut.regions

    def route(self, start, target):
        """沿用原型 A*，先按原始坐标检查分量，防止网格吸附跨过光幕。"""
        home = self.cut.component(start)
        if not home or self.cut.component(target) != home:
            raise NoConnectedRoad('目标不在当前可步行分量内。')
        return Localizer.route(self, np.asarray(start, float), np.asarray(target, float))

    def locate(self, panel, tag, require_player=True):
        """把原始展开 ROI 配准到底图；视野观测可无小队，但低质量配准一律拒绝。"""
        if panel is None or panel.shape != (462, 486, 3):
            raise ValueError('展开地图定位需要原始 486×462 BGR ROI。')
        players, _ = detect_markers(panel, np.eye(3))
        if len(players) > 1 or (require_player and len(players) != 1):
            raise RuntimeError(f'展开地图需一个小队圆环，实际得到 {len(players)} 个。')
        player = np.asarray(players[0] if players else np.array(panel.shape[1::-1]) / 2, float)
        road = terrain(panel)
        valid = np.full(road.shape, 255, np.uint8)
        valid[:4] = valid[-4:] = 0
        valid[:, :4] = valid[:, -4:] = 0
        valid[-35:, -75:] = 0
        hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
        icons = cv2.inRange(hsv, (0, 0, 180), (179, 95, 255))
        red = cv2.inRange(hsv, (0, 65, 160), (10, 255, 255))
        red |= cv2.inRange(hsv, (155, 65, 160), (179, 255, 255))
        valid[cv2.dilate(icons | red, np.ones((7, 7), np.uint8)) > 0] = 0
        if players:
            cv2.circle(valid, tuple(np.rint(player).astype(int)), 34, 0, -1)
        road[valid == 0] = 0
        report = self.matcher.match_panel(panel, road, valid, player)
        if report['status'] != 'accepted':
            raise RuntimeError(f"{tag} 展开地图配准未被接受：{report['status']}。")
        position = np.asarray(report['position'], float)
        matrix = np.asarray(report['roi_to_map'], float)
        if position.shape != (2,) or matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            raise ValueError('展开地图配准输出的坐标或单应格式无效。')
        if not np.isfinite(position).all():
            raise ValueError('展开地图配准输出非有限坐标。')
        return dict(report, iou=report['road_iou'], player_roi=player.tolist() if players else None,
                    position_kind='squad' if players else 'viewport')


class CampaignSession(NavigationSession):
    """任务 Device 上的会话，复用 NavigationSession 的 plan、停稳与收尾，不创建采集器窗口。"""

    def __init__(self, owner):
        """绑定已确认的章节与切开图；这里只组装接口，首次 start 才取焦。"""
        self.owner = owner
        self.expected_identity = (owner.current_chapter, owner.current_difficulty)
        self.request = {'chapter': owner.current_chapter, 'difficulty': owner.current_difficulty,
                        'purpose': 'position'}
        self.index = 0
        self.folder = Path(__file__).resolve().parents[2] / 'log/campaign' / (
            f'{owner.current_difficulty}_{owner.current_chapter}')
        self.localizer = CampaignLocalizer(owner.observer.matcher, owner.cut)
        self.compact_matcher = owner.observer.matcher
        self.package = owner.observer.package
        self.win = CampaignWindow(owner)
        self.win.stop_check = self.check_stop
        self.win.input_check = self.before_input
        self.win.pause = self.pause
        self.raw_handler = self.win.handler
        self.win.handler = CampaignInput(self, self.raw_handler) if self.raw_handler is not None else None
        self.inputs = 0
        self.target_inputs = 0
        self.deadline = None
        self.popup_handling = False
        self.collectible_mode = owner.config.task.command == 'auto_main_collectible'

    def start(self):
        """开始前要求已存在输入通道，取焦一次并锁定窗口；不在会话中重建驱动。时限从这里起算。"""
        self.check_stop()
        self.deadline = time.monotonic() + CHAPTER_TIME_LIMIT
        if self.win.handler is None:
            raise WindowChanged('主线移动需要已初始化的鼠标输入通道。')
        self.folder.mkdir(parents=True, exist_ok=True)
        if not self.win.focus():
            raise WindowChanged('无法取得游戏客户区原点。')
        self.check()

    def check_stop(self):
        """遵守调度器的停止事件；使用 TaskEnd 保留调度器既有取消语义。"""
        stop_event = self.owner.config.stop_event
        if stop_event is not None and stop_event.is_set():
            raise TaskEnd

    def check(self):
        """检查停止、时限与窗口状态；坐标或焦点变化属于安全失败，不允许换目标继续。

        时限放在这里而不只在发输入前：等待停稳、复核换帧等阶段也会走到这里，超时就地停下。
        """
        self.check_stop()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise InputBudgetExceeded(f'主线单次执行超过 {CHAPTER_TIME_LIMIT // 60} 分钟时限，停止并交人工复核。')
        self.win.check()

    def begin_target(self, purpose):
        """切换目标用途并重置单目标预算；执行时限与已验证箭头基准继续保留。"""
        self.request['purpose'] = purpose
        self.target_inputs = 0

    def before_input(self):
        """每次场景手势之前重新截屏核对身份；超时或达到单目标预算时不发送下一次输入。"""
        self.check()
        if not self.popup_handling:
            field = self.win.client()
            self.identity(field)
            self.check_collectible(field)
        if self.target_inputs >= TARGET_INPUT_BUDGET:
            raise InputBudgetExceeded('主线场景输入预算已耗尽，停止并交人工复核。')
        self.inputs += 1
        self.target_inputs += 1

    def identity(self, field):
        """核对章节、难度与弹窗；普通弹窗保留，EX 只关闭一次，任何用途都立即结束任务。"""
        if goto.battle_popup_score(field) > .8:
            self.preview(field)
            score = cv2.minMaxLoc(cv2.matchTemplate(field, goto.EX_STAGE_TPL, cv2.TM_CCOEFF_NORMED))[1]
            evidence = {'kind': 'battle_popup', 'chapter': self.expected_identity[0]}
            if score > .85:
                self.popup_handling = True
                try:
                    origin = self.win.client_origin()
                    self.win.handler.mouse_click(origin[0] + 1155, origin[1] + 521)
                    self.pause(1.2)
                    if goto.battle_popup_score(self.win.client()) > .8:
                        raise RuntimeError('EX 战斗弹窗关闭失败，不再重试。')
                finally:
                    self.popup_handling = False
                evidence['kind'] = 'ex_stage_skipped'
            raise TargetTriggered('已出现战斗准备弹窗，主线任务停止；不自动进入战斗。', evidence)
        current = chapter_identity(field, self.owner.chapter_ocr)
        if current != self.expected_identity:
            raise WindowChanged(f'主线章节或难度无法确认／已变化：预期 {self.expected_identity}，读到 {current}。')

    def check_collectible(self, field):
        """收集品任务见到橙色倒三角即停；不追加拾取或微调点击。"""
        if not self.collectible_mode and self.request['purpose'] != 'collectible':
            return
        indicator = collectible_indicator(field)
        if indicator is not None:
            self.preview(field)
            raise TargetTriggered('已发现收集品提示，停止并交人工拾取。', indicator)

    def preview(self, field):
        """发布任务预览并保留最新证据；截图不代表战斗或拾取完成。"""
        from module.campaign.navigation.evidence import write_image
        self.folder.mkdir(parents=True, exist_ok=True)
        write_image(self.folder / 'last_field.png', field)
        publish_preview_frame(np.ascontiguousarray(field[:, :, ::-1]), interval=0,
                              source=self.owner.campaign_source(self.owner.last_snapshot))

    def emit(self, **values):
        """将共享闭环的进度写入任务日志，电梯和移动结果仍由执行层回写状态。"""
        logger.info(f'主线移动：{values}')

    def remember_scene(self, field):
        """移动反馈帧也累积敌人与封锁，下一步 A* 不沿用移动前的过期切开图。"""
        snapshot = self.owner.observer.observe(field, self.expected_identity, time.time())
        self.owner.last_snapshot = snapshot
        self.owner.state.update(snapshot, advance=False)
        if self.owner.cut.update(self.owner.state.blocking()):
            logger.info('移动反馈发现封锁变化，已刷新后续移动的切开路网。')
        return snapshot

    def observe(self):
        """回正并展开地图定位真实小队；计算后道路变化超过 3% 就拒绝旧结果。"""
        self.check()
        field = self.win.client()
        self.identity(field)
        self.check_collectible(field)
        self.remember_scene(field)
        goto.map_open(self.win, reset=True)
        panel = self.win.capture()
        self.index += 1
        report = self.localizer.locate(panel, f'observe_{self.index:03}')
        self.check()
        if np.mean(terrain(panel) != terrain(self.win.capture())) > .03:
            raise RuntimeError('定位期间道路仍在变化，停止使用旧位置。')
        report['enemy_markers'] = goto.normal_enemy_markers(panel, np.asarray(report['roi_to_map']))
        return report

    def move(self, click):
        """只发送有限、有效的客户区地面落点，窗口代理负责最终校验与扣预算。"""
        click = np.asarray(click, float)
        if click.shape != (2,) or not np.isfinite(click).all() or not (0 <= click[0] < 1776 and 0 <= click[1] < 999):
            raise ValueError('移动落点越界或不是有限二维坐标。')
        self.check()
        self.check_collectible(self.win.client())
        origin = self.win.client_origin()
        self.win.handler.mouse_click(origin[0] + round(click[0]), origin[1] + round(click[1]))

    def pause(self, seconds):
        """短间隔等待持续检查取消、焦点与几何，不依赖原型的全局活动窗口。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def snapshot(self, expanded=False):
        """取得一拍业务观测；展开只补记忆，返回前恢复紧凑地图。"""
        self.check()
        self.identity(self.win.client())
        self.win.reset_minimap(expanded=expanded, reset=expanded)
        field = self.win.client()
        self.identity(field)
        self.check_collectible(field)
        snapshot = self.owner.observer.observe(field, self.expected_identity, time.time())
        self.owner.last_snapshot = snapshot
        if expanded:
            self.win.reset_minimap(expanded=False, reset=False)
        return snapshot

    def scout_begin(self):
        """侦察前展开并回正大地图，视野从小队所在处开始拖。"""
        self.check()
        goto.map_open(self.win, reset=True)

    def scout_end(self):
        """侦察结束收回紧凑小地图；展开视野已被拖偏，之后的观测由 map_open(reset=True) 回正。"""
        goto.map_close(self.win)

    def scout_locate(self, tag):
        """配准当前展开视野（不要求小队在画面内），返回 (面板, 配准报告)；配准未接受或画面仍在动时返回 None。"""
        panel = self.win.capture()
        try:
            report = self.localizer.locate(panel, tag, require_player=False)
        except (RuntimeError, ValueError) as exc:
            logger.info(f'侦察视野配准未接受：{exc}')
            return None
        if np.mean(terrain(panel) != terrain(self.win.capture())) > .03:
            logger.info('侦察视野仍在移动，本屏不记。')
            return None
        return panel, report

    def scout_view(self, point, tag):
        """把展开地图拖到 point（底图坐标）居中后配准这一屏，敌人与已看区域并进运行状态；返回是否记下了这一屏。

        拖动是闭环的：每拖一次都重新配准，用实测视野换算剩余偏移，所以拖动有惯性或比例不准都能收敛；
        地图边缘拖不动时最多拖 SCOUT_DRAGS 次，之后按当时视野记录，落点没进视野由调用方记冷却。
        拖动经输入代理发送，与移动输入同样先核对窗口、章节与预算。
        """
        located = self.scout_locate(tag)
        for attempt in range(SCOUT_DRAGS):
            if located is None:
                return False
            panel, report = located
            height, width = panel.shape[:2]
            center = np.array([width / 2, height / 2])
            offset = center - project(np.linalg.inv(np.asarray(report['roi_to_map'], float)), point)
            length = float(np.linalg.norm(offset))
            if length <= SCOUT_CENTER_TOLERANCE:
                break
            # 地图内容跟手移动：从 center - v/2 拖到 center + v/2，落点正好挪到面板中心。
            offset *= min(1., SCOUT_DRAG_MAX / length)
            self.check()
            base = np.asarray(self.win.client_origin(), float) + EXPANDED_ROI[:2] + center
            start, end = np.rint(base - offset / 2).astype(int), np.rint(base + offset / 2).astype(int)
            self.win.handler.mouse_swipe(tuple(start.tolist()), tuple(end.tolist()))
            self.pause(SCOUT_SETTLE_SECONDS)
            located = self.scout_locate(f'{tag}_{attempt + 1}')
        if located is None:
            return False
        self.absorb_view(*located)
        return True

    def absorb_view(self, panel, report):
        """侦察这一屏的敌人与已看区域并进运行状态。

        这一屏只有视野配准、没有小队位置，所以按展开帧并入（不动封锁），也不推进决策拍。
        敌人标记识别与投影和观测器处理展开帧的方式一致，便于与其它帧的同一只敌人合并。
        """
        matrix = np.asarray(report['roi_to_map'], float)
        height, width = panel.shape[:2]
        _, markers = self.owner.observer.minimap_markers(panel)
        positions = project_centers([item['position'] for item in markers], matrix)
        size = self.owner.state.size
        enemies = [dict(item, position=position) for item, position in zip(markers, positions)
                   if 0 <= position[0] < size[0] and 0 <= position[1] < size[1]]
        changes = self.owner.state.update(dict(
            localization='accepted', roi_to_map=matrix.tolist(), coverage_size=[width, height],
            minimap={'mode': 'expanded', 'size': [width, height]}, enemies=enemies), advance=False)
        logger.info(f"侦察视野 {[round(float(value), 1) for value in report['position']]}："
                    f"敌人 {len(enemies)}，{changes}")
        self.preview(self.win.client())

    def close(self):
        """始终释放已按下鼠标，不关闭调度器 Device，也不在失焦或取消后点控件。"""
        try:
            if self.raw_handler is not None:
                self.raw_handler.mouse_up()
        finally:
            self.win.close()
