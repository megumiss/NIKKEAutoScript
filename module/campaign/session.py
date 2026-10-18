"""生产任务的移动会话：复用原型导航闭环，设备与状态由 Campaign 持有。"""

import time
from pathlib import Path

import cv2
import numpy as np

from module.base.timer import Timer
from module.base.utils import publish_preview_frame
from module.campaign.navigation import controls as goto
from module.campaign.navigation.movement_feedback import SquadPositionChanged, TargetTriggered, collectible_indicator
from module.campaign.navigation.pathfinding import NoConnectedRoad
from module.campaign.navigation.pathfinding import RoadLocalizer as Localizer
from module.campaign.navigation.session import NavigationSession
from module.campaign.report import (ANCHOR_METHODS, ANCHOR_RESULTS, BATTLE_STATES, CAMERA_TRACKING,
                                    CORRECTION_REASONS, LOCALIZATION, MINIMAP_MODES, MOVE_STATES,
                                    NAVIGATION_METHODS, POSITION_KINDS, label, points_text)
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
# 定位未被接受时最多再观测几轮：尚未展开时先展开回正，已展开则首轮只换新帧，
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
# 每个按钮自己的最短点击间隔（秒），与仓库 appear_then_click 的 interval 同义：一次点击没生效时，
# 间隔内的轮询不再重复点。数值照仓库的战斗循环取（进入战斗／快速战斗 2~5 秒、结算 1 秒、自动射击 5 秒）。
ENTRY_TAP_INTERVAL = 2.
SETTLE_TAP_INTERVAL = 1.
AUTO_TAP_INTERVAL = 5.
# 野外动作前的等待：画面不是野外（加载、过场、黑屏）时按这个间隔重看，最多等这么多秒。
FIELD_POLL_SECONDS = 0.5
FIELD_WAIT_LIMIT = 30.
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
    ('road_remaining', lambda value: f'剩余道路距离 {float(value):.1f} 像素'),
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
    ('camera_view_offset', lambda value: f'目标离注视点 {points_text(value)}'),
    ('camera_position_kind', lambda value: f'视野坐标类型 {label(POSITION_KINDS, value)}'),
    ('camera_anchor', lambda value: f'展开地图校正 {label(ANCHOR_RESULTS, value)}'),
    ('camera_anchor_error', lambda value: f'展开地图校正失败：{value}'),
    ('camera_anchor_shift', lambda value: f'校正位移 {points_text(value)}'),
    ('camera_anchor_viewport', lambda value: f'校正后的视野中心 {points_text(value)}'),
    ('start_registration', lambda value: f'起始对齐 {label(LOCALIZATION, value)}'),
    ('start_iou', lambda value: f'起始对齐的道路重合度 {float(value):.3f}'),
    ('start_offset', lambda value: f'与小队定位相差 {float(value):.1f} 像素'),
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
    detail.append(f"小地图敌人标记 {len(minimap.get('enemies') or [])} 个")
    detail.append(f"底图投影敌人 {len(snapshot.get('enemies') or [])} 个")
    detail.append(f"场景目标 {len(snapshot.get('objects') or [])} 个")
    detail.append(f"光幕 {len(snapshot.get('barriers') or [])} 处")
    detail.append(f"挡路光幕 {len(snapshot.get('blocks') or [])} 处")
    return '，'.join(detail)


class InputBudgetExceeded(RuntimeError):
    """单次执行超过时限或同一个目标的操作次数用尽；不可降级为普通目标失败后继续点击。"""


class CampaignInput:
    """生产驱动代理：每次新手势先校验会话并扣预算，释放操作不受停止与失焦限制。

    这里也是「真正发出去过手势」的唯一记账点：小地图开合走的是 `device.click_xy`（未包装的原始驱动），
    只经过 `before_input` 这道闸门、不开手势，所以不能算进 `session.gestures`。
    """

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
            # 闸门放行之后才算一次真手势：只有它可能让小队移动或镜头平移（见 reuse_reason）。
            self.session.gestures += 1
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
        # 真正发给游戏的手势数（点击／拖动／按下／移动）。inputs 把「只过闸门、不开手势」的小地图
        # 开合也算进预算，所以判定画面有没有变过要用这一个（见 CampaignInput.send、reuse_reason）。
        self.gestures = 0
        self.deadline = None
        self.popup_handling = False
        # 战斗接管中：准备弹窗不再是终态，允许会话自己点它上面的按钮。
        self.battle_mode = False
        # 本场战斗给 AUTO 点过几次、连续几帧读成关闭外观，以及「不再重复点击」的告警是否已经打过。
        self.auto_taps = 0
        self.auto_off_frames = 0
        self.auto_skip_logged = False
        # 每个按钮上一次点击的时刻：战斗接管的按钮各自有最短点击间隔（见 tap 的 interval）。
        self.tap_times = {}
        # 战斗结束后的第一帧允许沿用战斗前的小队坐标（见 reuse_registration）。
        self.reuse_squad = False
        self.last_registration = None
        # 最近一次可供导航使用的定位报告与当时的手势计数：navigate 每走一步都会重新观测小队，
        # 而两次手势之间画面不会变，同一拍里的下一次观测直接复用（见 reusable_report）。
        self.localized_report = None
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
        """战斗闸门：弹窗、战斗中与结算都抛给执行层，接管之内放行让会话点这些按钮。

        这几种画面上都不是主线地图，平时任何落点点击都可能点进战斗、点掉结算或点错按钮，所以立即
        停止并交给执行层（普通关卡由执行层接管）；battle_mode 为真说明已经进入接管（或正要接管），
        这时它们就是要被点的对象。EX 弹窗只关一次后仍然是终态：本次实现不接管 EX 关卡。
        实机里点「进入战斗」之后战斗更早就开始了：外层若只认弹窗，就会在战斗画面上继续做野外动作
        （点地图控件、点地面），所以战斗中与结算也在这里拦住。
        认战斗画面要扫五个模板（约 350ms），而这个闸门每个手势都会走一次，所以先用 0.1ms 的野外判据
        挡掉正常情况：地图控件在画面上就说明不在战斗里。
        """
        score = goto.battle_popup_score(field)
        if score <= .8:
            if self.battle_mode or battle.field_visible(field):
                return
            kind = battle.battle_state(field)['state']
            if kind in ('battle', 'victory', 'defeat'):
                self.preview(field)
                raise TargetTriggered(f'画面是{label(BATTLE_STATES, kind)}，交由执行层接管这场战斗。',
                                      {'kind': 'battle_popup', 'chapter': self.expected_identity[0]})
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
            logger.info('移动期间光幕状态变化，已更新阻断路网。')
        return snapshot

    def await_field(self):
        """等到画面确实可以操作野外再发输入，返回那一帧。

        点「进入战斗」之后的过场、以及结算回野外前的加载，都会有短暂「不是野外」的画面；在那些帧上
        点地图控件会被动画吃掉，展开随即失败、任务按窗口异常停下。所以野外动作之前先确认画面：
        弹窗、战斗中与结算由身份闸门交给执行层，认不出（加载、黑屏、剧情对话）就在这里等，
        等到超过 FIELD_WAIT_LIMIT 秒仍不是野外才按画面异常停下交人工（不再发任何输入）。
        """
        watch = Stopwatch()
        while 1:
            field = self.win.client()
            self.identity(field)
            if self.field_ready(field):
                return field
            if watch.seconds >= FIELD_WAIT_LIMIT:
                raise WindowChanged(f'画面已经 {watch} 不是主线野外，等不到能操作的地图，交人工。')
            self.pause(FIELD_POLL_SECONDS)

    def field_ready(self, field):
        """画面是否可以操作野外：小地图控件在（紧凑或关闭态），或展开地图面板开着。

        只用现成的地图外观判据，不额外识别模板：要的就是「这里有地图控件可点」这一条。
        """
        return bool(battle.field_visible(field) or self.owner.minimap_expanded(field))

    def observe(self):
        """优先复用未发生手势的有效小队定位，否则回正并展开定位，失败时有限换视野重试。

        每次尝试的失败原因与量化指标由观察器的告警给出，这里只在成功时补一行：接受了哪一次尝试、
        道路重合度与小队坐标是多少、整段展开与定位花了多久——这一段的耗时是移动闭环的主要开销。
        成功那一帧上认出的敌人会一并并进运行状态（见 absorb_expanded）。
        上一次被接受的定位之后没有再发过手势时，这一拍要的坐标与上次完全相同，直接复用而不展开面板
        （见 reusable_report）：navigate 每走一步都会调用这里，而两步之间只有中间那次点击会改变画面。
        """
        self.check()
        watch = Stopwatch()
        field = self.await_field()
        self.remember_scene(field)
        cached = self.reusable_report()
        if cached is not None:
            source = '紧凑小地图' if cached.get('minimap_mode') == 'compact' else '展开地图'
            logger.info(f"复用已确认的{source}定位：小队位置 {points_text(cached['position'])}，"
                        f"缓存后无新增手势，跳过展开与配准，耗时 {watch}")
            return cached
        goto.map_open(self.win, reset=True)
        error = None
        for attempt in range(LOCALIZE_RETRIES + 1):
            if attempt:
                logger.info(f'展开地图定位未通过，平移视野后重试（{attempt}/{LOCALIZE_RETRIES}）。')
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
            self.absorb_expanded(panel, report)
            self.localized_report = {'report': report, 'gestures': self.gestures}
            self.remember_registration(report['position'], report.get('roi_to_map'),
                                       list(panel.shape[1::-1]))
            return report
        logger.warning(f'展开地图定位连续 {LOCALIZE_RETRIES + 1} 次未通过，耗时 {watch}，按定位失败处理。')
        raise error

    def plan(self, observation, target, enemy_assist=False):
        """用已确认的小队定位开始镜头规划，紧凑定位不足时仅重试一次完整展开定位。

        紧凑快照只提供底图位置，不冒充展开 ROI 的投影基准。起始镜头不一致或跟踪需要展开
        校正时，先回正并取得原始展开报告再规划；若小队已移动超过 12 地图像素，交回移动
        闭环更新起点。窗口、弹窗和输入预算异常不捕获，也不补发清理输入。
        """
        try:
            return super().plan(observation, target, enemy_assist=enemy_assist)
        except ValueError as exc:
            if observation.get('minimap_mode') != 'compact':
                raise
            logger.info(f'紧凑定位不足以完成镜头规划，按需展开重新定位一次：{exc}')
        self.localized_report = None
        updated = self.observe()
        if np.linalg.norm(np.asarray(updated['position']) - np.asarray(observation['position'])) > 12:
            raise SquadPositionChanged(observation['position'], updated['position'])
        return super().plan(updated, target, enemy_assist=enemy_assist)

    def absorb_expanded(self, panel, report):
        """把展开面板上认出的敌人与看到的区域并进运行状态，只记一行敌人的合并结果。

        展开面板本来就认出了整屏敌人标记，但以前只用它们定位小队、以及微调当前目标：同一屏里其它
        敌人既没进状态、也没参与选目标，于是「面板上有 8 个敌人标记」的下一轮仍按紧凑小地图里的
        1 个敌人决策。这里按侦察那一套规则并入（RunState 按距离合并到既有敌人，展开帧只补敌人与
        已观测区域、不推进决策轮）：越走越准的位置由后续帧刷新，远处敌人提前进入候选。
        """
        matrix = np.asarray(report['roi_to_map'], float)
        height, width = panel.shape[:2]
        enemies = self.panel_enemies(panel, matrix)
        changes = self.owner.state.update(dict(
            localization='accepted', roi_to_map=matrix.tolist(), coverage_size=[width, height],
            minimap={'mode': 'expanded', 'size': [width, height]}, enemies=enemies), advance=False)
        detail = describe_changes({key: value for key, value in changes.items()
                                   if key.startswith('enemies_')})
        if detail:
            logger.info(f'展开地图敌人状态更新：{detail}')

    def panel_enemies(self, panel, matrix):
        """把面板上的敌人标记按本帧配准投到底图，返回可直接并进状态的记录（含类型与置信度）。"""
        _, markers = self.owner.observer.minimap_markers(panel)
        positions = project_centers([item['position'] for item in markers], np.asarray(matrix, float))
        size = self.owner.state.size
        return [dict(item, position=position) for item, position in zip(markers, positions)
                if 0 <= position[0] < size[0] and 0 <= position[1] < size[1]]

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

    def snapshot(self, expanded=False, keep_expanded=False):
        """取得一拍业务观测；定位失败时有限重试，展开默认收起，也可留给紧接着的侦察。

        战斗结束后的第一帧允许沿用战斗前的小队坐标（见 reuse_reason）；此外小队只在收到点击时才
        移动、镜头只在收到拖动时才平移，所以上一次被接受的定位之后没有再发过手势时同样直接沿用：
        紧凑面板配准没过线时重新展开大地图只会得到同一个坐标，白花一次开合与几秒配准。
        每帧都打一行观测摘要（见 describe_snapshot）与这一段耗时：推图的等待绝大部分花在这里。
        keep_expanded 仅用于 expanded=True 后直接衔接侦察；调用方负责正常结束时收起。
        已经展开的定位重试保留面板，不再强制开合回正。可信紧凑帧可交给移动入口复用小队位置。
        """
        if keep_expanded and not expanded:
            raise ValueError('保留展开面板要求 expanded=True。')
        self.check()
        watch = Stopwatch()
        self.await_field()
        self.win.reset_minimap(expanded=expanded, reset=expanded)
        field = self.win.client()
        self.identity(field)
        snapshot = self.owner.observer.observe(field, self.expected_identity, time.time())
        if not located(snapshot):
            why = self.reuse_reason()
            snapshot = (self.reuse_registration(snapshot, why) if why is not None
                        else self.retry_localization(snapshot, expanded=expanded, keep_expanded=keep_expanded))
        if snapshot.get('localization') == 'accepted':
            # 只记刚配准成功的坐标：沿用帧的坐标本来就是这个记录，不需要再写一遍。
            self.remember_registration(snapshot['squad'], snapshot.get('roi_to_map'),
                                       snapshot.get('coverage_size'))
            self.remember_navigation(snapshot)
        self.owner.last_snapshot = snapshot
        if expanded and not keep_expanded:
            self.win.reset_minimap(expanded=False, reset=False)
        logger.info(f'观测完成：{describe_snapshot(snapshot)}，耗时 {watch}')
        return snapshot

    def battle_step(self, close_entry=False):
        """战斗接管的一步：截一帧，按优先级最多点一个按钮，返回识别结果交给执行层决策。

        优先级与点法照仓库里已经跑熟的战斗循环（module/daemon/semi_combat.py、module/event/challenge.py）：
        准备弹窗（关掉／快速战斗／进入战斗）、结算（点继续）、战斗中（自动射击关着才点 AUTO）；
        每一拍只点一个按钮，认不出画面就什么都不点——加载与过场本来就没有按钮，硬猜只会点错。
        每个按钮有自己的最短点击间隔（ENTRY_TAP_INTERVAL / SETTLE_TAP_INTERVAL / AUTO_TAP_INTERVAL），
        一次点击没生效时后续几拍不再重复点同一个按钮。
        战败不在这里点：重打还是返回要按重试次数决定，属于执行层的策略（见 CampaignExecutor.battle）。
        close_entry 用于战败返回之后：再看到准备弹窗只能关掉，不能又进一次战斗。
        每次点击的日志都带上当次识别分数与颜色判据的实际读数，便于对着现场截图复盘误判。
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
                if self.tap(ENTRY_CLOSE_POINT, SETTLE_TAP_INTERVAL):
                    logger.info(f"战斗弹窗再次出现（弹窗按钮命中度 {battle.score_text(scores, 'entry')}，"
                                f"按钮橙色占比 {quick}）：已点击关闭，等待返回野外。")
            elif state['quick']:
                if self.tap(QUICK_BATTLE_POINT, ENTRY_TAP_INTERVAL):
                    logger.info(f"快速战斗可用：已点击快速战斗（弹窗按钮命中度 {battle.score_text(scores, 'entry')}，"
                                f"按钮橙色占比 {quick}）。")
            elif self.tap(ENTER_BATTLE_POINT, ENTRY_TAP_INTERVAL):
                logger.info(f"快速战斗不可用：已点击进入战斗（弹窗按钮命中度 {battle.score_text(scores, 'entry')}，"
                            f"按钮橙色占比 {quick}）。")
        elif kind == 'battle':
            if not state.get('auto', True):
                self.handle_auto(state)
        elif kind == 'victory':
            if self.tap(ADVANCE_POINT, SETTLE_TAP_INTERVAL):
                logger.info(f"战斗结算：胜利（结算画面命中度 {battle.score_text(scores, 'victory')}），已点击继续。")
        return state

    def handle_auto(self, state):
        """战斗中把自动射击打开：连续多帧都读成关闭外观才点，且同一场战斗最多点一次。

        AUTO 的开与关是同一个按钮，误判一次就等于把开着的自动射击关掉。实机日志里出现过相反方向的
        问题：一整场战斗提示了四次「自动射击未开启」、也就连点了四次 AUTO，而现场观察这个开关一直开着。
        所以判据读数不够硬时宁可不动它：先要求连续 AUTO_CONFIRM_FRAMES 帧都是关闭外观，点过一次之后
        即使再读成关闭也只告警；点击还要过 AUTO_TAP_INTERVAL，避免同一秒里补第二下。
        按钮没被可靠定位（auto_score 低于阈值）时 battle_state 已按未开启返回，这里的告警会把定位框、
        模板分与橙色占比一起打出来，便于判断是「按钮是灰的」还是「根本没定位到」。
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
                logger.warning(f'AUTO 状态仍为未开启（{detail}）；已达到本场单次点击上限，禁止重复切换。')
            return
        if self.auto_off_frames < AUTO_CONFIRM_FRAMES:
            logger.info(f'AUTO 状态待确认（{detail}），连续关闭帧数 '
                        f'{self.auto_off_frames}/{AUTO_CONFIRM_FRAMES}，暂不点击。')
            return
        if not self.tap(AUTO_POINT, AUTO_TAP_INTERVAL):
            return
        self.auto_taps += 1
        logger.info(f'AUTO 关闭状态已确认（连续 {self.auto_off_frames} 帧，{detail}），已发送开启点击。')

    def tap(self, point, interval=0.):
        """按客户区坐标点一次界面按钮：与移动落点共用同一套校验、闸门与预算。

        interval 是这一点自己的最短点击间隔（秒），与仓库 appear_then_click 的 interval 同义：
        间隔内的重复点击直接丢掉并返回 False，真的点了返回 True。战斗接管的按钮都靠它防连点——
        点击没生效时（面板动画中、被过场吃掉）下一轮只是再点一次，而不是每拍补一下。
        """
        key = tuple(point)
        now = time.monotonic()
        if interval and now - self.tap_times.get(key, 0.) < interval:
            return False
        self.tap_times[key] = now
        self.move(point)
        return True

    def remember_registration(self, squad, roi_to_map, coverage_size):
        """记下刚被接受的小队坐标与它的配准矩阵，供之后没再发过手势的帧直接沿用。

        squad / roi_to_map / coverage_size 必须来自同一帧、同一个面板的配准结果：沿用时会整组套用，
        坐标系不配套会把「本帧看过哪里」算到别处（见 run_state.panel_quad）。
        同时记下当时的手势计数——小队只在收到点击时才移动、镜头只在收到拖动时才平移，
        所以「之后没再发过手势」等价于「这份坐标一定还成立」（见 reuse_reason）。
        """
        self.last_registration = dict(squad=list(squad), roi_to_map=roi_to_map,
                                      coverage_size=coverage_size, gestures=self.gestures)

    def reuse_reason(self):
        """本帧重新配准未被接受时，返回可以沿用上一次定位的原因；不能沿用则返回 None。

        两种情况可以不重新配准：一是战斗刚结束（reuse_squad）——战斗打断了视野与箭头，重新配准
        往往只差一点没过线，而战斗前后小队不会换位置；二是上一次被接受的定位之后没有再发过任何
        手势，那么小队与镜头都不可能动过。后一种在小地图配准常年压在门槛下的章节里是常态：
        重新展开大地图配准只会得到同一个坐标，白花一次开合与几秒配准。
        返回的原因是给日志看的标识（'battle' / 'unchanged'），不参与判定。
        """
        if self.reuse_squad:
            return 'battle'
        record = self.last_registration
        if record is not None and record.get('gestures') == self.gestures:
            return 'unchanged'
        return None

    def reuse_registration(self, snapshot, why):
        """定位未被接受时沿用上一次记下的小队坐标，只补小队位置与配准矩阵。

        沿用帧只提供小队位置（供本拍选目标），本帧的视野与标记不并进运行状态（见 RunState.update）：
        展开面板与紧凑面板的坐标系不同源，混着用会把封锁与已观测区域算偏。
        没有记过坐标时原样返回，交给正常重试路径。
        结算后存在强制位移／传送的关卡（EX 等）不适用这条假设，那种帧的坐标会错一拍，
        下一次真的需要配准时由 retry_localization 把它兜回来。
        """
        self.reuse_squad = False
        if self.last_registration is None:
            return snapshot
        snapshot = dict(snapshot, localization='reused', squad=list(self.last_registration['squad']),
                        roi_to_map=self.last_registration['roi_to_map'],
                        coverage_size=self.last_registration['coverage_size'])
        reason = '战后首次观测允许复用战前定位' if why == 'battle' \
            else '缓存后无新增手势'
        logger.warning(f"本帧配准未通过，复用已确认的小队位置 {points_text(snapshot['squad'])}"
                       f"（依据：{reason}）。")
        return snapshot

    def remember_navigation(self, snapshot):
        """将本帧可信紧凑小队定位交给移动入口，只携带底图坐标和同帧普通敌人。

        不携带 roi_to_map：紧凑矩阵和裁蓝框的展开矩阵都不能充当原始展开 ROI 的镜头基准。
        沿用帧、侦察视口和缺少实际配准分数的快照不建立导航缓存，后续任一手势使缓存失效。
        """
        if snapshot.get('localization') != 'accepted' or (snapshot.get('minimap') or {}).get('mode') != 'compact':
            return
        position = np.asarray(snapshot.get('squad'), float)
        iou = snapshot.get('road_iou')
        if position.shape != (2,) or not np.isfinite(position).all() or iou is None or not np.isfinite(iou):
            return
        markers = [list(item['position']) for item in snapshot.get('enemies', []) if item.get('kind') == 'normal']
        report = dict(position=position.tolist(), position_kind='squad', iou=float(iou),
                      minimap_mode='compact', enemy_markers=markers)
        self.localized_report = {'report': report, 'gestures': self.gestures}

    def reusable_report(self):
        """返回可以直接沿用的小队定位报告；没有成功过、或之后已经发过手势时返回 None。

        展开报告来自原始 ROI，紧凑报告只携带底图位置，不混用投影矩阵。
        navigate 每一步都会重新观测小队，而两次手势之间画面不会变，复用不会漏掉任何新信息。
        返回副本，避免调用方改动缓存里的报告。
        """
        cached = self.localized_report
        if cached is None or cached['gestures'] != self.gestures:
            return None
        return dict(cached['report'])

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

    def retry_localization(self, snapshot, expanded=False, keep_expanded=False):
        """定位未被接受时利用展开面板有限重试，返回最后一次快照。

        只在「配准差一点没过线」时管用：展开面板面积是紧凑面板的数倍，道路证据更多，换一段区域
        往往就能过线。开合与拖动都走输入闸门，退出前收回紧凑地图；重试都在同一拍内完成，
        不改运行状态也不推进决策拍，全部失败时返回最后一次未接受的快照交给调用方处置。
        每次尝试都记一行定位状态与耗时：紧凑面板的量化失败指标由观察器告警（同类只打一次），
        这里给出的是「哪几次、各花了多久」，用来判断重试是否值得继续。
        expanded 表示调用方已经展开并回正，首轮只取新帧，不再为回正重复开合；
        keep_expanded 让补视野直接衔接侦察，默认仍在正常返回前收起。异常时不补发输入。
        """
        for attempt in range(LOCALIZE_RETRIES):
            self.check()
            watch = Stopwatch()
            if not expanded:
                goto.map_open(self.win, reset=True)
                expanded = True
            elif attempt:
                self.pan_expanded(PAN_DIRECTIONS[(attempt - 1) % len(PAN_DIRECTIONS)])
            snapshot = self.owner.observer.observe(self.win.client(), self.expected_identity, time.time())
            if snapshot['localization'] == 'accepted':
                logger.info(f"第 {attempt + 1}/{LOCALIZE_RETRIES} 次展开视野重试定位成功："
                            f"小队位置 {points_text(snapshot['squad'])}，耗时 {watch}")
                break
            logger.info(f"第 {attempt + 1}/{LOCALIZE_RETRIES} 次展开视野重试仍未定位成功"
                        f"（{label(LOCALIZATION, snapshot['localization'])}），耗时 {watch}")
        else:
            logger.warning(f'展开视野重试 {LOCALIZE_RETRIES} 次仍未定位成功，返回未通过定位的观测。')
        if expanded and not keep_expanded:
            goto.map_close(self.win)
        return snapshot

    def scout_begin(self):
        """侦察前确保地图已展开；沿用补视野面板，不为回正额外收起再开。

        小队位置由调用方可信快照提供，侦察用每帧配准定位视口，不要求面板中心就是小队。
        """
        self.check()
        goto.map_open(self.win, reset=False)

    def scout_end(self):
        """侦察结束收回紧凑小地图；移动前由新紧凑帧确认小队，不能沿用拖偏的侦察视口。"""
        goto.map_close(self.win)

    def scout_locate(self, tag):
        """配准当前展开视野（不要求小队在画面内），返回 (面板, 配准报告)；配准未接受或画面仍在动时返回 None。"""
        panel = self.win.capture()
        try:
            report = self.localizer.locate(panel, tag, require_player=False)
        except (RuntimeError, ValueError) as exc:
            logger.info(f'侦察帧定位未通过：{exc}')
            return None
        if np.mean(terrain(panel) != terrain(self.win.capture())) > .03:
            logger.info('侦察帧稳定性检查未通过，跳过本帧记录。')
            return None
        return panel, report

    def scout_view(self, point, tag):
        """把展开地图拖向 point（底图坐标），沿途可信帧均记录敌人与覆盖；返回是否记录过可信帧。

        拖动是闭环的：每拖一次都重新配准，用实测视野换算剩余偏移，所以拖动有惯性或比例不准都能收敛；
        落点所在整格已经进入视野就停止，不必强求居中。地图边缘拖不动时最多拖 SCOUT_DRAGS 次，
        落点没进视野由调用方记冷却；后续配准失败不会丢掉途中已记录的观测。
        拖动经输入代理发送，与移动输入同样先核对窗口、章节与预算。
        """
        located = self.scout_locate(tag)
        recorded = False
        for attempt in range(SCOUT_DRAGS + 1):
            if located is None:
                return recorded
            self.absorb_view(*located)
            recorded = True
            if self.owner.state.is_observed(point) or attempt == SCOUT_DRAGS:
                return True
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
        return recorded

    def absorb_view(self, panel, report):
        """侦察这一屏的敌人与已看区域并进运行状态。

        这一屏只有视野配准、没有小队位置，所以按展开帧并入（不动封锁），也不推进决策拍。
        敌人标记识别与投影与 observe 共用 panel_enemies，便于与其它帧的同一只敌人合并。
        """
        matrix = np.asarray(report['roi_to_map'], float)
        height, width = panel.shape[:2]
        enemies = self.panel_enemies(panel, matrix)
        changes = self.owner.state.update(dict(
            localization='accepted', roi_to_map=matrix.tolist(), coverage_size=[width, height],
            minimap={'mode': 'expanded', 'size': [width, height]}, enemies=enemies), advance=False)
        detail = describe_changes(changes) or '无新增敌人或观测区域'
        logger.info(f"侦察帧配准位置 {points_text(report['position'])}：检测敌人 {len(enemies)} 个，{detail}")
        self.preview(self.win.client())

    def close(self):
        """始终释放已按下鼠标，不关闭调度器 Device，也不在失焦或取消后点控件。"""
        try:
            if self.raw_handler is not None:
                self.raw_handler.mouse_up()
        finally:
            self.win.close()
