"""生产任务的移动会话：复用原型导航闭环，设备与状态由 Campaign 持有。"""

import time
from pathlib import Path

import cv2
import numpy as np

from module.base.timer import Timer
from module.base.utils import publish_preview_frame
from module.campaign.navigation import controls as goto
from module.campaign.navigation.movement_feedback import TargetTriggered, collectible_indicator
from module.campaign.navigation.pathfinding import NoConnectedRoad
from module.campaign.navigation.pathfinding import RoadLocalizer as Localizer
from module.campaign.navigation.session import NavigationSession
from module.campaign.report import (ANCHOR_METHODS, ANCHOR_RESULTS, CAMERA_TRACKING, CORRECTION_REASONS,
                                    LOCALIZATION, MINIMAP_MODES, MOVE_STATES, NAVIGATION_METHODS,
                                    POSITION_KINDS, label, points_text)
from module.campaign.run_state import describe_changes, located
from module.campaign.timing import Stopwatch
from module.campaign.vision.chapter import chapter_identity
from module.campaign.vision import battle
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
# 定位未被接受时最多再观测几轮：第 1 轮展开并回正（展开面板的道路证据是紧凑面板的数倍），
# 之后每轮在面板内换一段视野。只用来救「配准差一点没过线」，连续失败仍按定位不可用处理。
LOCALIZE_RETRIES = 3
# 换视野的方向（客户区向量）、位移与停稳等待：起止点以面板中心对称，落点仍在 486×462 面板内。
# 四个方向都留：视野已经贴着地图某一边时，沿那条边再拖是白费一次重试。
PAN_DIRECTIONS = ((1., 0.), (-1., 0.), (0., 1.), (0., -1.))
PAN_PIXELS = 140.
PAN_SETTLE_SECONDS = 1.0
# 战斗接管的点击落点（客户区像素）：准备弹窗的两个按钮、弹窗右上角关闭、AUTO 开关、结算推进；
# 战败的两个点（重新挑战／返回）由执行层按重试次数选用，常量放这里是为了所有落点在一处。
QUICK_BATTLE_POINT = (1014, 842)
ENTER_BATTLE_POINT = (1014, 916)
ENTRY_CLOSE_POINT = (1147, 513)
AUTO_POINT = (105, 35)
ADVANCE_POINT = (888, 500)
DEFEAT_BACK_POINT = (73, 908)
DEFEAT_RETRY_POINT = (1688, 926)
# AUTO 的点按策略：连续这么多帧都读成关闭外观才点，且同一场战斗最多点一次。实机里 AUTO 按钮
# 叠在场景上、HUD 会淡出或整体挪动，单帧判据偶尔会把开着的读成关着；照单帧就点，那一下正好
# 把自动射击关掉（开与关是同一个按钮），战斗会被拖长甚至打输。
AUTO_CONFIRM_FRAMES = 2
# 设备卡死判据的续期阈值（秒）：它判的是「一直在等某个按钮匹配成功」，而推图全程不做按钮匹配，
# 计时到 6 分钟就会在 device.screenshot 里抛 GameStuckError('Wait too long')；
# 调度器只在每个任务开始时清一次计时，而一章可能跑满 20 分钟，所以这里提前续期。
STUCK_CLEAR_SECONDS = 300


# 共享移动闭环的进度字段（emit 的键）与它们在日志里的写法。闭环只传英文标识与原始数值，
# 翻译集中在这里：顺序固定让同一字段每次出现在同一位置，表外的键按 key=value 追加，
# 新增字段因此不会在日志里悄悄丢掉，也不会让这一行整行打不出来（格式化失败同样退回 key=value）。
MOVE_FIELDS = (
    ('state', lambda value: f'状态 {label(MOVE_STATES, value)}'),
    ('message', lambda value: '' if value is None else str(value)),
    ('movement_clicks', lambda value: f'第 {value} 次移动'),
    ('position', lambda value: f'小队位置 {points_text(value)}'),
    ('distance', lambda value: f'距离 {float(value):.1f} 像素'),
    ('iou', lambda value: f'道路重合度 {float(value):.3f}'),
    ('click', lambda value: f'点击位置 {points_text(value)}'),
    ('road_remaining', lambda value: f'沿道路还要走 {float(value):.1f} 像素'),
    ('target_marker', lambda value: f'目标标记 {points_text(value)}'),
    ('navigation_method', lambda value: f'落点算法 {label(NAVIGATION_METHODS, value)}'),
    ('planned_target', lambda value: f'规划目标 {points_text(value)}'),
    ('plan_camera_pans', lambda value: f'本段第 {value} 次平移'),
    ('camera_pans', lambda value: f'累计平移 {value} 次'),
    ('camera_drag', lambda value: f'拖动画面 {points_text(value)}'),
    ('camera_gain', lambda value: f'画面跟手比例 {float(value):.2f}'),
    ('camera_response', lambda value: f'本次画面跟手比例 {float(value):.2f}'),
    ('camera_tracking', lambda value: f'镜头位置来源 {label(CAMERA_TRACKING, value)}'),
    ('camera_batch_pans', lambda value: f'本批已平移 {value} 次'),
    ('camera_correction_reason', lambda value: f'提前收尾的原因：{label(CORRECTION_REASONS, value)}'),
    ('camera_view_position', lambda value: f'视野中心 {points_text(value)}'),
    ('camera_position_kind', lambda value: f'视野坐标取的是{label(POSITION_KINDS, value)}'),
    ('camera_anchor', lambda value: f'展开地图校正 {label(ANCHOR_RESULTS, value)}'),
    ('camera_anchor_error', lambda value: f'展开地图校正失败：{value}'),
    ('camera_anchor_shift', lambda value: f'校正位移 {points_text(value)}'),
    ('camera_anchor_viewport', lambda value: f'校正后的视野中心 {points_text(value)}'),
    ('start_registration', lambda value: f'起始对齐 {label(LOCALIZATION, value)}'),
    ('start_iou', lambda value: f'起始对齐的道路重合度 {float(value):.3f}'),
    ('start_offset', lambda value: f'与展开地图定位相差 {float(value):.1f} 像素'),
    ('squad_anchor', lambda value: f'小队箭头锚点 {points_text(value)}'),
    ('click_offset', lambda value: f'相对镜头注视点 {points_text(value)}'),
    ('fused_iou', lambda value: f'容差对齐的道路重合度 {float(value):.3f}'),
    ('fused_deviation', lambda value: f'偏离拖动方向 {float(value):.1f} 像素'),
    ('anchor_method', lambda value: f'箭头锚点来源 {label(ANCHOR_METHODS, value)}'),
    ('ground_anchor', lambda value: f'地面锚点 {points_text(value)}'),
    ('arrow_attempts', lambda value: f'箭头采样 {value} 帧'),
    ('arrow_missed_frames', lambda value: f'箭头漏检 {value} 帧'),
    ('arrow_fallback', lambda value: f'箭头回退原因：{value}'),
    ('arrow_recoveries', lambda value: f'找回箭头 {value} 次'),
    ('anchor_recovery_error', lambda value: f'找回箭头失败：{value}'),
    ('camera_replans', lambda value: f'镜头重定位 {value} 次'),
    ('squad_before_pan', lambda value: f'平移前小队位置 {points_text(value)}'),
    ('squad_after_pan', lambda value: f'平移后小队位置 {points_text(value)}'),
    ('wait_samples', lambda value: f'停稳采样 {value} 次'),
    ('wait_player_compact', lambda value: '小队圆环未读到' if value is None else f'小队圆环 {points_text(value)}'),
    ('elevator_entry', lambda value: f'电梯入口 {points_text(value)}'),
    ('elevator_exit', lambda value: f'电梯出口 {points_text(value)}'),
    ('elevator_distance', lambda value: f'离电梯入口 {float(value):.1f} 像素'),
    ('elevator_route', lambda value: f'要坐 {len(value)} 段电梯'),
    ('elevator_transfers', lambda value: f'已确认传送 {len(value)} 次'),
    ('wake_target', lambda value: f'唤醒目标 {points_text(value)}' if value else '没有可唤醒的目标'),
)


def describe_move(values):
    """把共享闭环的一次进度回调压成一行可读中文；表外字段按 key=value 追加在后面。

    message 已经是一句完整中文时不再重复打状态：带 message 的回调说的就是同一件事
    （例如 state=planning 配「正在自动换算道路落点…」），两样都打只会让每行长一倍。
    """
    parts, used = [], set()
    for key, render in MOVE_FIELDS:
        if key not in values:
            continue
        used.add(key)
        if key == 'state' and values.get('message'):
            continue
        try:
            text = render(values[key])
        except (TypeError, ValueError, KeyError, IndexError):
            text = f'{key}={values[key]}'
        if text:
            parts.append(text)
    parts.extend(f'{key}={value}' for key, value in values.items() if key not in used)
    return '，'.join(parts)


def describe_snapshot(snapshot):
    """把一帧业务观测压成一行中文：定位可信度、小队位置、小地图形态与各路识别的计数。"""
    minimap = snapshot.get('minimap') or {}
    detail = [f"定位 {label(LOCALIZATION, snapshot.get('localization'))}"]
    if snapshot.get('squad') is not None:
        detail.append(f"小队位置 {points_text(snapshot['squad'])}")
    detail.append(f"小地图 {label(MINIMAP_MODES, minimap.get('mode')) if minimap else '不可用'}")
    detail.append(f"小地图里看到敌人 {len(minimap.get('enemies') or [])} 个")
    detail.append(f"换算到章节地图的敌人 {len(snapshot.get('enemies') or [])} 个")
    detail.append(f"场景目标 {len(snapshot.get('objects') or [])} 个")
    detail.append(f"光幕 {len(snapshot.get('barriers') or [])} 处")
    detail.append(f"挡路光幕 {len(snapshot.get('blocks') or [])} 处")
    return '，'.join(detail)


class InputBudgetExceeded(RuntimeError):
    """单次执行超过时限或同一个目标的操作次数用尽；不可降级为普通目标失败后继续点击。"""


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
            """发送前校验窗口、弹窗与预算，发送后任一次驱动失败都立即停止。"""
            self.session.before_input()
            result = value(*args, **kwargs)
            if getattr(self.handler, '_failures', 0):
                raise WindowChanged(f'鼠标操作 {name} 发送失败。')
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
            raise NoConnectedRoad('目标不在当前能走到的区域里。')
        return Localizer.route(self, np.asarray(start, float), np.asarray(target, float))

    def locate(self, panel, tag, require_player=True):
        """把原始展开 ROI 配准到底图；视野观测可无小队，但低质量配准一律拒绝。

        tag 是这次定位的画面标识，由调用方在留存证据图时使用，不拼进失败文本：
        那句话要给人看，画面标识对用户没有意义（调用方的日志里已经写清是第几次尝试）。
        """
        if panel is None or panel.shape != (462, 486, 3):
            raise ValueError('展开地图定位需要 486×462 的原始画面裁图。')
        players, _ = detect_markers(panel, np.eye(3))
        if len(players) > 1 or (require_player and len(players) != 1):
            raise RuntimeError(f'展开地图里应该只有 1 个代表小队的圆环，实际看到 {len(players)} 个。')
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
            raise RuntimeError(f"展开地图定位未通过（{label(LOCALIZATION, report['status'])}）。")
        position = np.asarray(report['position'], float)
        matrix = np.asarray(report['roi_to_map'], float)
        if position.shape != (2,) or matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            raise ValueError('展开地图定位算出的坐标无效。')
        if not np.isfinite(position).all():
            raise ValueError('展开地图定位算出了非有限坐标。')
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
        # 战斗接管中：准备弹窗不再是终态，允许会话自己点它上面的按钮。
        self.battle_mode = False
        # 本场战斗给 AUTO 点过几次、连续几帧读成关闭外观，以及「不再重复点击」的告警是否已经打过。
        self.auto_taps = 0
        self.auto_off_frames = 0
        self.auto_skip_logged = False
        # 战斗结束后的第一帧允许沿用战斗前的小队坐标（见 reuse_registration）。
        self.reuse_squad = False
        self.last_registration = None
        self.collectible_mode = owner.config.task.command == 'auto_main_collectible'

    def start(self):
        """开始前要求已存在输入通道，取焦一次并锁定窗口；不在会话中重建驱动。时限从这里起算。

        章节与难度只在这里核对一次：同一段执行里逐帧重读章节号不会带来新信息，章节真的变了会先
        表现为配准与静态层对不上（定位不可用），而逐帧核对会让每个手势都多两次 OCR 与一张截图。
        """
        self.check_stop()
        watch = Stopwatch()
        self.deadline = time.monotonic() + CHAPTER_TIME_LIMIT
        if self.win.handler is None:
            raise WindowChanged('主线移动需要已连接的鼠标操作通道。')
        self.folder.mkdir(parents=True, exist_ok=True)
        if not self.win.focus():
            raise WindowChanged('无法读取游戏窗口位置。')
        self.check()
        self.verify_identity(self.win.client())
        logger.info(f"主线会话开始：{self.expected_identity[1].upper()} 第 {self.expected_identity[0]} 章，"
                    f"游戏窗口左上角 {self.win.client_origin()}，截图与身份核对耗时 {watch}；"
                    f"单次执行最多 {CHAPTER_TIME_LIMIT // 60} 分钟，"
                    f"同一个目标最多操作 {TARGET_INPUT_BUDGET} 次")

    def check_stop(self):
        """遵守调度器的停止事件；使用 TaskEnd 保留调度器既有取消语义。"""
        stop_event = self.owner.config.stop_event
        if stop_event is not None and stop_event.is_set():
            raise TaskEnd

    def check(self):
        """检查停止、时限与窗口状态；坐标或焦点变化属于安全失败，不允许换目标继续。

        时限放在这里而不只在发输入前：等待停稳、复核换帧等阶段也会走到这里，超时就地停下。
        这里同时给设备的卡死判据续期（见 STUCK_CLEAR_SECONDS）：这一章的每一步观测都要经过
        device.screenshot，而那个判据的前提「一直在等按钮匹配」在本会话里从头到尾都不成立。
        """
        self.check_stop()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise InputBudgetExceeded(f'主线单次执行超过 {CHAPTER_TIME_LIMIT // 60} 分钟时限，停止并交人工复核。')
        stuck = getattr(self.owner.device, 'stuck_timer', None)
        if isinstance(stuck, Timer) and stuck.current() > STUCK_CLEAR_SECONDS:
            self.owner.device.stuck_record_clear()
        self.win.check()

    def begin_target(self, purpose):
        """切换目标用途并重置单目标预算；执行时限与已验证箭头基准继续保留。"""
        self.request['purpose'] = purpose
        self.target_inputs = 0

    def before_input(self):
        """每次场景手势之前核对弹窗与收集品提示；超时或达到单目标预算时不发送下一次输入。

        这里不重读章节身份：身份由 start 核对一次（见 verify_identity）。真正必须逐帧看的是
        战斗弹窗与收集品提示——漏一帧就可能把落点点进战斗，或踩掉一次拾取机会。
        """
        self.check()
        if not self.popup_handling:
            self.identity(self.win.client())
        if self.target_inputs >= TARGET_INPUT_BUDGET:
            raise InputBudgetExceeded(f'同一个目标已操作 {TARGET_INPUT_BUDGET} 次仍未完成，停止并交人工复核。')
        self.inputs += 1
        self.target_inputs += 1

    def popup(self, field):
        """战斗弹窗闸门：战斗接管之外把它抛给执行层，接管之内放行让会话点它。

        弹窗出现时画面已经不是主线地图，平时任何落点点击都可能点进战斗或结算，所以立即停止并交给
        执行层（普通关卡由执行层接管）；battle_mode 为真说明已经进入接管（或正要接管），这时弹窗
        就是要被点的对象。EX 弹窗只关一次后仍然是终态：本次实现不接管 EX 关卡。
        """
        if goto.battle_popup_score(field) <= .8:
            return
        self.preview(field)
        if self.battle_mode:
            return
        evidence = {'kind': 'battle_popup', 'chapter': self.expected_identity[0]}
        message = '画面出现战斗准备弹窗，交由执行层接管这场战斗。'
        if battle.ex_stage(field):
            self.popup_handling = True
            try:
                origin = self.win.client_origin()
                self.win.handler.mouse_click(origin[0] + 1155, origin[1] + 521)
                self.pause(1.2)
                if goto.battle_popup_score(self.win.client()) > .8:
                    raise RuntimeError('EX 关卡弹窗未能关闭，不再重试。')
            finally:
                self.popup_handling = False
            evidence['kind'] = 'ex_stage_skipped'
            message = '画面出现 EX 关卡弹窗：仅关闭，不接管该关卡。'
        raise TargetTriggered(message, evidence)

    def identity(self, field):
        """逐帧闸门：只查战斗弹窗与收集品提示，不重读章节身份。

        每个手势、每次观测以及共享移动闭环都调用它，所以必须便宜。章节与难度只在会话开始
        （start 的 verify_identity）核对一次：逐帧重读章节号不会带来新信息，而弹窗与收集品提示
        漏一帧就可能把落点点进战斗或踩掉一次拾取机会。
        """
        self.popup(field)
        self.check_collectible(field)

    def verify_identity(self, field):
        """核对章节、难度与弹窗；普通弹窗保留，EX 只关闭一次，任何用途都立即结束任务。

        只在会话开始时调用：读到别的章节／难度或读不到时抛 WindowChanged，禁止后续输入。
        """
        self.popup(field)
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
        """将共享闭环的进度写入任务日志，电梯和移动结果仍由执行层回写状态。

        一次回调一行：字段翻译见 describe_move，前面带上这个目标已经操作过多少次，
        好让日志能直接读出「这一步花了几次操作」。
        """
        logger.info(f'主线移动（本目标第 {self.target_inputs} 次操作）：{describe_move(values)}')

    def remember_scene(self, field):
        """移动反馈帧也累积敌人与封锁，下一步 A* 不沿用移动前的过期切开图。"""
        snapshot = self.owner.observer.observe(field, self.expected_identity, time.time())
        self.owner.last_snapshot = snapshot
        self.owner.state.update(snapshot, advance=False)
        if self.owner.cut.update(self.owner.state.blocking()):
            logger.info('移动中发现光幕有变化，后面的路线按新的挡路情况重算。')
        return snapshot

    def observe(self):
        """回正并展开地图定位真实小队；配准未被接受时换视野重试，计算后道路变化超过 3% 就拒绝旧结果。

        每次尝试的失败原因与量化指标由观察器的告警给出，这里只在成功时补一行：接受了哪一次尝试、
        道路重合度与小队坐标是多少、整段展开与定位花了多久——这一段的耗时是移动闭环的主要开销。
        """
        self.check()
        watch = Stopwatch()
        field = self.win.client()
        self.identity(field)
        self.remember_scene(field)
        goto.map_open(self.win, reset=True)
        error = None
        for attempt in range(LOCALIZE_RETRIES + 1):
            if attempt:
                logger.info(f'展开地图定位未通过，挪动视野重试（{attempt}/{LOCALIZE_RETRIES}）。')
                self.pan_expanded(PAN_DIRECTIONS[(attempt - 1) % len(PAN_DIRECTIONS)])
            panel = self.win.capture()
            self.index += 1
            try:
                report = self.localizer.locate(panel, f'observe_{self.index:03}')
            except (RuntimeError, ValueError) as exc:
                error = exc
                continue
            self.check()
            if np.mean(terrain(panel) != terrain(self.win.capture())) > .03:
                raise RuntimeError('定位期间道路还在变，不能用这次位置。')
            report['enemy_markers'] = goto.normal_enemy_markers(panel, np.asarray(report['roi_to_map']))
            logger.info(f"展开地图定位成功：第 {attempt + 1} 次尝试，小队位置 {points_text(report['position'])}，"
                        f"道路重合度 {float(report['road_iou']):.3f}，"
                        f"面板上有 {len(report['enemy_markers'])} 个敌人标记，耗时 {watch}")
            return report
        logger.warning(f'展开地图定位连续 {LOCALIZE_RETRIES + 1} 次未通过，耗时 {watch}，按定位失败处理。')
        raise error

    def move(self, click):
        """只发送有限、有效的客户区地面落点，窗口代理负责最终校验与扣预算。"""
        click = np.asarray(click, float)
        if click.shape != (2,) or not np.isfinite(click).all() or not (0 <= click[0] < 1776 and 0 <= click[1] < 999):
            raise ValueError('点击位置越界或不是有效坐标。')
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

    def wait_stopped(self):
        """等待小队标记与地图道路停稳；这是移动闭环里占比最大的等待，单独记一行耗时。

        停稳判据与采样轮数沿用共享实现（见 camera_navigation.wait_for_squad），这里只补日志：
        没有这一行时，一步移动的耗时只能从相邻两行日志的时间戳里反推。
        """
        watch = Stopwatch()
        super().wait_stopped()
        logger.info(f'停稳等待结束（小队标记与道路都已稳定），耗时 {watch}')

    def snapshot(self, expanded=False):
        """取得一拍业务观测；定位未被接受时换视野重试，展开只补记忆，返回前恢复紧凑地图。

        战斗结束后的第一帧允许沿用战斗前的小队坐标：战斗打断了视野与箭头，重新配准常只差一点
        没过线，而战斗前后小队不会换位置（见 reuse_registration）。
        每帧都打一行观测摘要（见 describe_snapshot）与这一段耗时：推图的等待绝大部分花在这里。
        """
        self.check()
        watch = Stopwatch()
        self.identity(self.win.client())
        self.win.reset_minimap(expanded=expanded, reset=expanded)
        field = self.win.client()
        self.identity(field)
        snapshot = self.owner.observer.observe(field, self.expected_identity, time.time())
        if located(snapshot):
            self.remember_registration(snapshot)
        elif self.reuse_squad:
            snapshot = self.reuse_registration(snapshot)
        else:
            snapshot = self.retry_localization(snapshot)
        self.owner.last_snapshot = snapshot
        if expanded:
            self.win.reset_minimap(expanded=False, reset=False)
        logger.info(f'观测完成：{describe_snapshot(snapshot)}，耗时 {watch}')
        return snapshot

    def battle_step(self, close_entry=False):
        """战斗接管的一步：识别当前画面并按状态点一次，返回识别结果并交给执行层决策。

        close_entry 用于战败返回之后：再看到准备弹窗只能关掉，不能又进一次战斗。
        战败结算不在这里点：重打还是返回要按重试次数决定，属于执行层的策略（见 CampaignExecutor.battle）。
        战斗中只在自动射击关闭时点一次 AUTO（开着就不再点，点法与限量见 handle_auto）；野外不发输入，
        交给执行层收尾。点击都走场景输入闸门与预算；每次点击的日志都带上当次识别分数与颜色判据的
        实际读数，便于对着现场截图复盘误判。
        返回 battle.battle_state 的结果供执行层记录与决策。
        """
        self.check()
        state = battle.battle_state(self.win.client())
        # scores 与颜色占比都只用于日志，缺项由 battle.value_text / score_text 显示为「—」。
        kind, scores = state['state'], state.get('scores')
        quick = battle.value_text(state.get('quick_ratio'))
        if kind not in ('battle', 'unknown'):
            # 一场战斗从准备弹窗开始；战败重打的下一场没有弹窗，所以结算与回野外也要重置额度。
            self.auto_taps, self.auto_off_frames, self.auto_skip_logged = 0, 0, False
        if kind == 'entry':
            if close_entry:
                logger.info(f"战斗弹窗再次出现（弹窗按钮命中度 {battle.score_text(scores, 'entry')}，"
                            f"按钮橙色占比 {quick}）：本次不再进入，先关掉回到野外。")
                self.tap(ENTRY_CLOSE_POINT)
            elif state['quick']:
                logger.info(f"快速战斗可用：点快速战斗（弹窗按钮命中度 {battle.score_text(scores, 'entry')}，"
                            f"按钮橙色占比 {quick}）。")
                self.tap(QUICK_BATTLE_POINT)
            else:
                logger.info(f"快速战斗不可用：点进入战斗（弹窗按钮命中度 {battle.score_text(scores, 'entry')}，"
                            f"按钮橙色占比 {quick}）。")
                self.tap(ENTER_BATTLE_POINT)
        elif kind == 'battle':
            if not state.get('auto', True):
                self.handle_auto(state)
        elif kind == 'victory':
            logger.info(f"战斗结算：胜利（结算画面命中度 {battle.score_text(scores, 'victory')}），点继续。")
            self.tap(ADVANCE_POINT)
        return state

    def handle_auto(self, state):
        """战斗中把自动射击打开：连续多帧都读成关闭外观才点，且同一场战斗最多点一次。

        AUTO 的开与关是同一个按钮，误判一次就等于把开着的自动射击关掉。实机日志里出现过相反方向的
        问题：一整场战斗提示了四次「自动射击未开启」、也就连点了四次 AUTO，而现场观察这个开关一直开着。
        所以判据读数不够硬时宁可不动它：先要求连续 AUTO_CONFIRM_FRAMES 帧都是关闭外观，点过一次之后
        即使再读成关闭也只告警。按钮没被可靠定位（auto_score 低于阈值）时 battle_state 已按未开启返回，
        这里的告警会把定位框、模板分与橙色占比一起打出来，便于判断是「按钮是灰的」还是「根本没定位到」。
        """
        ratio = battle.value_text(state.get('auto_ratio'))
        if state.get('auto', True):
            self.auto_off_frames = 0
            return
        self.auto_off_frames += 1
        box = state.get('auto_box')
        detail = (f"AUTO 按钮模板 {battle.value_text(state.get('auto_score'))}，"
                  f"框内橙色占比 {ratio}" + (f"，定位框 {box}" if box else ''))
        if self.auto_taps:
            if not self.auto_skip_logged:
                self.auto_skip_logged = True
                logger.warning(f'本场战斗已经点过一次 AUTO，仍读成未开启（{detail}）：不再重复点击，'
                               f'该按钮是同一个开关，重复点击会把它拨回关闭。')
            return
        if self.auto_off_frames < AUTO_CONFIRM_FRAMES:
            logger.info(f'AUTO 读成未开启（{detail}），再确认一帧（'
                        f'{self.auto_off_frames}/{AUTO_CONFIRM_FRAMES}）后才点。')
            return
        self.auto_taps += 1
        logger.info(f'战斗中自动射击未开启（连续 {self.auto_off_frames} 帧关闭外观，{detail}）：点 AUTO 打开。')
        self.tap(AUTO_POINT)

    def tap(self, point):
        """按客户区坐标点一次界面按钮：与移动落点共用同一套校验、闸门与预算。"""
        self.move(point)

    def remember_registration(self, snapshot):
        """记下最近一次被接受的定位，供战斗结束后沿用。"""
        self.last_registration = dict(squad=list(snapshot['squad']),
                                      roi_to_map=snapshot.get('roi_to_map'),
                                      coverage_size=snapshot.get('coverage_size'))

    def reuse_registration(self, snapshot):
        """战后第一帧定位未接受时，沿用战斗前记下的小队坐标。

        战斗前后小队不会换位置，而战斗打断了视野与箭头，重新配准往往只差一点没过线；
        沿用旧坐标比再花十几秒换视野重试更贴近事实。只能沿用一帧：之后的观测仍要求重新配准，
        否则一次沿用会在整段任务里一直传下去。没有记过坐标时原样返回，交给正常重试路径。
        结算后存在强制位移／传送的关卡（EX 等）不适用这条假设，那种帧的坐标会错一拍，
        下一帧要求重新配准把它兜回来。
        """
        self.reuse_squad = False
        if self.last_registration is None:
            return snapshot
        snapshot = dict(snapshot, localization='reused', squad=list(self.last_registration['squad']),
                        roi_to_map=self.last_registration['roi_to_map'],
                        coverage_size=self.last_registration['coverage_size'])
        logger.warning(f"战后沿用战斗前的小队位置 {points_text(snapshot['squad'])}（这一帧未能重新定位）。")
        return snapshot

    def pan_expanded(self, direction):
        """在展开面板里沿客户区方向拖一次换视野；起止点以面板中心对称，拖动经输入闸门发送。

        只平移地图视图，小队本身不动：原本道路证据太少、配准差一点没过线的视野会换成另一段，
        停稳后由调用方重新截图配准。方向向量是客户区方向（(1, 0) 表示把地图内容向右拖）。
        """
        left, top, right, bottom = EXPANDED_ROI
        center = np.array([(left + right) / 2, (top + bottom) / 2])
        half = np.asarray(direction, float) * PAN_PIXELS / 2
        self.check()
        base = np.asarray(self.win.client_origin(), float) + center
        start, end = np.rint(base - half).astype(int), np.rint(base + half).astype(int)
        self.win.handler.mouse_swipe(tuple(start.tolist()), tuple(end.tolist()))
        self.pause(PAN_SETTLE_SECONDS)

    def retry_localization(self, snapshot):
        """紧凑帧定位未被接受时，展开并回正、再换几段视野重新观测，返回最后一次快照。

        只在「配准差一点没过线」时管用：展开面板面积是紧凑面板的数倍，道路证据更多，换一段区域
        往往就能过线。开合与拖动都走输入闸门，退出前收回紧凑地图；重试都在同一拍内完成，
        不改运行状态也不推进决策拍，全部失败时返回最后一次未接受的快照交给调用方处置。
        每次尝试都记一行定位状态与耗时：紧凑面板的量化失败指标由观察器告警（同类只打一次），
        这里给出的是「哪几次、各花了多久」，用来判断重试是否值得继续。
        """
        expanded = False
        for attempt in range(LOCALIZE_RETRIES):
            self.check()
            watch = Stopwatch()
            if not expanded:
                goto.map_open(self.win, reset=True)
                expanded = True
            else:
                self.pan_expanded(PAN_DIRECTIONS[(attempt - 1) % len(PAN_DIRECTIONS)])
            snapshot = self.owner.observer.observe(self.win.client(), self.expected_identity, time.time())
            if snapshot['localization'] == 'accepted':
                logger.info(f"第 {attempt + 1}/{LOCALIZE_RETRIES} 次挪动视野后重新定位成功："
                            f"小队位置 {points_text(snapshot['squad'])}，耗时 {watch}")
                break
            logger.info(f"第 {attempt + 1}/{LOCALIZE_RETRIES} 次挪动视野后仍未定位成功"
                        f"（{label(LOCALIZATION, snapshot['localization'])}），耗时 {watch}")
        else:
            logger.warning(f'挪动视野重试 {LOCALIZE_RETRIES} 次仍未定位成功，按当前观测继续。')
        if expanded:
            goto.map_close(self.win)
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
            logger.info(f'侦察这一屏定位未通过：{exc}')
            return None
        if np.mean(terrain(panel) != terrain(self.win.capture())) > .03:
            logger.info('这一屏画面仍在变化，不记录。')
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
        detail = describe_changes(changes) or '没有新的敌人或区域'
        logger.info(f"侦察这一屏定位到 {points_text(report['position'])}：看到 {len(enemies)} 个敌人，{detail}")
        self.preview(self.win.client())

    def close(self):
        """始终释放已按下鼠标，不关闭调度器 Device，也不在失焦或取消后点控件。"""
        try:
            if self.raw_handler is not None:
                self.raw_handler.mouse_up()
        finally:
            self.win.close()
