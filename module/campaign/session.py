"""生产任务的移动会话：复用原型导航闭环，设备与状态由 Campaign 持有。"""

import sys
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
from module.campaign.run_state import MAX_FAILURES, describe_changes, located
from module.campaign.timing import Stopwatch
from module.campaign.trace import CampaignTrace
from module.campaign.navigation.ex_bypass import ExBypass, ExBypassUnavailable
from module.campaign.navigation.progress import MovementProgress, NavigationStalled
from module.campaign.vision.chapter import chapter_identity
from module.campaign.vision import battle
from module.campaign.vision.minimap import terrain
from module.campaign.vision.observation import EXPANDED_ROI
from module.campaign.vision.perception import expanded_minimap_masks, project_centers
from module.campaign.vision.surface_motion import project
from module.campaign.window import CampaignWindow, WindowChanged
from module.campaign.scene_session import CampaignBattleActions, CampaignInput
from module.campaign.scene_session import DEFEAT_BACK_POINT as DEFEAT_BACK_POINT
from module.campaign.scene_session import DEFEAT_RETRY_POINT as DEFEAT_RETRY_POINT
from module.campaign.scene_session import SETTLE_TAP_INTERVAL as SETTLE_TAP_INTERVAL
from module.config.config import TaskEnd
from module.logger import logger

# 侦察拖动：每屏最多拖几次把落点拖到面板中心、落点离中心多近就不再拖、单次拖动的最长距离
# （客户区像素，起止点以面板中心对称，保证都落在 486×462 面板内）与拖完后的停稳等待。
SCOUT_DRAGS = 3
SCOUT_CENTER_TOLERANCE = 60.
SCOUT_DRAG_MAX = 300.
SCOUT_SETTLE_SECONDS = 1.0
# 展开地图只渲染已探索区域（实测见 doc/campaign-auto-flow.md）：面板道路占比低于该值说明
# 视口已到探索前沿，那边既没有道路可配准也没有敌人标记，平移重试无益，按前沿失败处理。
FRONTIER_BLUE_MAX = 18.
# 稳定性检查失败时先原地换帧复验的次数与间隔：长拖后地图惯性滚动会超过既有停稳等待，
# 直接平移会白白丢掉一个本来能用的视野。
STABLE_RESAMPLES = 2
STABLE_RESAMPLE_SECONDS = 0.6
# 开局扫描：中心一屏加四向各两站。步进是面板尺寸的倍数（内容跟手，与章节缩放无关），
# 每臂两站把覆盖扩到小队周围约两个面板步距，主线剧情提示一般都在这个范围内。
SCAN_ARM_STEP = 0.6
SCAN_ARM_STOPS = 2
# 定位未被接受时最多再观测几轮：尚未展开时先展开回正，已展开则首轮只换新帧，
# 之后每轮在面板内换一段视野。只用来救「配准差一点没过线」，连续失败仍按定位不可用处理。
LOCALIZE_RETRIES = 3
# 会话身份复核只在读不到时换帧，明确读到其他章节或难度时立即停止。
IDENTITY_CHECK_ATTEMPTS = 3
IDENTITY_CHECK_INTERVAL = 0.5
# 换视野的方向（客户区向量）、位移与停稳等待：起止点以面板中心对称，落点仍在 486×462 面板内。
# 四个方向都留：视野已经贴着地图某一边时，沿那条边再拖是白费一次重试。
PAN_DIRECTIONS = ((1., 0.), (-1., 0.), (0., 1.), (0., -1.))
PAN_PIXELS = 140.
PAN_SETTLE_SECONDS = 1.0
# 展开定位首次失败后仅补一次小幅地图平移，避免在相同视野上反复更换侦察目标。
EXPANDED_RETRY_PIXELS = 60.
# 战斗接管的固定点击落点（客户区像素）：准备弹窗的两个按钮、弹窗右上角关闭和 AUTO 开关；
# 结算继续落点由当前帧 END_FIGHTING 提示定位，不使用画面中心代替。
# 战败的两个点（重新挑战／返回）由执行层按重试次数选用，常量放这里是为了所有落点在一处。
# 野外动作前的等待：画面不是野外（加载、过场、黑屏）时按这个间隔重看，最多等这么多秒。
FIELD_POLL_SECONDS = 0.5
FIELD_WAIT_LIMIT = 30.
# 设备卡死判据的续期阈值（秒）：它判的是「一直在等某个按钮匹配成功」，而推图全程不做按钮匹配，
# 计时到 6 分钟就会在 device.screenshot 里抛 GameStuckError('Wait too long')；
# 调度器只在每个任务开始时清一次计时，推图会话可能长时间持续，因此这里提前续期。
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


class CampaignLocalizer:
    """给共享闭环提供定位与道路接口；配准看原图，寻路只看最新切开图。"""

    def __init__(self, matcher, cut):
        """使用已绑定章节的 MapMatcher，避免读取原型的全局默认章节或修改全局设置。"""
        self.matcher, self.cut = matcher, cut
        self.old_matrix = goto.ROI_TO_PLANE
        self.record = None

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

    def locate(self, panel, tag, require_player=True, expected_position=None, locate_player=True):
        """把原始展开 ROI 配准到底图；视野观测可无小队，但低质量配准一律拒绝。

        tag 是这次定位的画面标识，由调用方在留存证据图时使用，不拼进失败文本：
        那句话要给人看，画面标识对用户没有意义（调用方的日志里已经写清是第几次尝试）。
        expected_position 只约束当前可见的实测小队圆环；小队离屏时仍按无锚点道路配准处理。
        locate_player=False 用于纯镜头侦察，完全跳过实时小队圆环检测与校验，只返回视口坐标。
        """
        road, valid, player = expanded_minimap_masks(panel, require_player=require_player, locate_player=locate_player)
        has_player = player is not None
        options = {'expected_position': expected_position} if has_player and expected_position is not None else {}
        if not has_player:
            player = np.array(panel.shape[1::-1], float) / 2
        report = self.matcher.match_panel(panel, road, valid, player, **options)
        if self.record is not None:
            self.record('panel_registration', tag=tag, require_player=require_player,
                        expected_position=expected_position, report=report)
        if report['status'] != 'accepted':
            raise RuntimeError(f"展开地图定位未通过（{label(LOCALIZATION, report['status'])}）。")
        position = np.asarray(report['position'], float)
        matrix = np.asarray(report['roi_to_map'], float)
        if position.shape != (2,) or matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            raise ValueError('展开地图定位算出的坐标无效。')
        if not np.isfinite(position).all():
            raise ValueError('展开地图定位算出了非有限坐标。')
        return dict(report, iou=report['road_iou'], player_roi=player.tolist() if has_player else None,
                    position_kind='squad' if has_player else 'viewport')


class CampaignSession(CampaignBattleActions, NavigationSession):
    """任务 Device 上的会话，复用 NavigationSession 的 plan、停稳与收尾，不创建采集器窗口。"""

    def __init__(self, owner):
        """绑定已确认的章节与切开图；这里只组装接口，首次 start 才取焦。"""
        self.owner = owner
        self.expected_identity = (owner.current_chapter, owner.current_difficulty)
        self.request = {'chapter': owner.current_chapter, 'difficulty': owner.current_difficulty,
                        'purpose': 'position'}
        self.index = 0
        self.debug_mode = getattr(owner.config, 'CampaignSettings_DebugMode', False)
        self.ex_bypass = ExBypass()
        self.ex_popup_count = 0
        self.folder = Path(__file__).resolve().parents[2] / 'log/campaign' / (
            f'{owner.current_difficulty}_{owner.current_chapter}')
        self.localizer = CampaignLocalizer(owner.observer.matcher, owner.cut)
        self.compact_matcher = owner.observer.matcher
        self.package = owner.observer.package
        self.trace = CampaignTrace(self.folder / 'runs', dict(
            chapter=owner.current_chapter, difficulty=owner.current_difficulty,
            instance=getattr(owner.config, 'config_name', None), task=owner.config.task.command,
            client_size=[1776, 999]))
        self.win = CampaignWindow(owner)
        self.win.trace = self.trace
        self.win.stop_check = self.check_stop
        self.win.input_check = self.before_input
        self.win.input_sent = self.map_input_sent
        self.win.pause = self.pause
        self.raw_handler = self.win.handler
        self.win.handler = CampaignInput(self, self.raw_handler) if self.raw_handler is not None else None
        self.inputs = 0
        self.target_inputs = 0
        # 场景手势序号只使镜头/小队定位缓存失效，地图控件点击另计实际 inputs。
        self.gestures = 0
        self.squad_inputs = 0
        self.popup_handling = False
        # 战斗接管中：准备弹窗不再是终态，允许会话自己点它上面的按钮。
        self.battle_mode = False
        # 本场战斗给 AUTO 点过几次、连续几帧读成关闭外观，以及「不再重复点击」的告警是否已经打过。
        self.auto_taps = {key: 0 for key in battle.AUTO_CONTROLS}
        self.auto_off_frames = {key: 0 for key in battle.AUTO_CONTROLS}
        self.auto_skip_logged = set()
        self.auto_last_points = {}
        # 每个按钮上一次点击的时刻：战斗接管的按钮各自有最短点击间隔（见 tap 的 interval）。
        self.tap_times = {}
        # 战斗结束后的第一帧允许沿用战斗前的小队坐标（见 reuse_registration）。
        self.reuse_squad = False
        self.last_registration = None
        self.barrier_references = []
        # 最近一次可供导航使用的定位报告与当时的手势计数：navigate 每走一步都会重新观测小队，
        # 而两次手势之间画面不会变，同一拍里的下一次观测直接复用（见 reusable_report）。
        self.localized_report = None
        # 最近一次侦察/扫描配准的失败原因与面板道路占比：供调用方区分普通失败与探索前沿。
        self.scout_failure = None
        self.planned_enemy_click = None
        self.last_enemy_click = None
        self.planned_movement_click = None
        self.movement_progress = {}
        self.active_movement_progress = None
        self.collectible_mode = owner.config.task.command == 'auto_main_collectible'
        self.localizer.record = self.record

    def start(self):
        """开始前要求已存在输入通道，取焦一次并锁定窗口；不在会话中重建驱动。

        章节与难度只在这里核对一次：同一段执行里逐帧重读章节号不会带来新信息，章节真的变了会先
        表现为配准与静态层对不上（定位不可用），而逐帧核对会让每个手势都多两次 OCR 与一张截图。
        """
        self.check_stop()
        if self.debug_mode:
            self.trace.start()
        watch = Stopwatch()
        if self.win.handler is None:
            raise WindowChanged('主线移动需要已连接的鼠标操作通道。')
        if not self.win.focus():
            raise WindowChanged('无法读取游戏窗口位置。')
        self.check()
        self.verify_identity(self.win.client())
        logger.info(f"主线会话开始：{self.expected_identity[1].upper()} 第 {self.expected_identity[0]} 章，"
                    f"游戏窗口左上角 {self.win.client_origin()}，截图与身份核对耗时 {watch}；"
                    '移动按路线进展与各阶段重试上限保护')

    def check_stop(self):
        """响应调度器停止事件，包括窗口失焦恢复期间；不按单章累计时长停止。"""
        stop_event = self.owner.config.stop_event
        if stop_event is not None and stop_event.is_set():
            raise TaskEnd

    def check(self):
        """检查停止与窗口状态；坐标变化安全失败，失焦则等待恢复后继续。

        停止检查由 check_stop 统一执行，窗口层等待恢复前台时也必须响应停止。
        这里同时给设备的卡死判据续期（见 STUCK_CLEAR_SECONDS）：这一章的每一步观测都要经过
        device.screenshot，而那个判据的前提「一直在等按钮匹配」在本会话里从头到尾都不成立。
        """
        self.check_stop()
        stuck = getattr(self.owner.device, 'stuck_timer', None)
        if isinstance(stuck, Timer) and stuck.current() > STUCK_CLEAR_SECONDS:
            self.owner.device.stuck_record_clear()
        self.win.check()

    def begin_target(self, purpose):
        """切换用途并重置该阶段成功操作统计；稳定目标的路线进展历史不清空。"""
        self.request['purpose'] = purpose
        self.planned_enemy_click = None
        self.planned_movement_click = None
        self.last_enemy_click = None
        self.active_movement_progress = None
        self.target_inputs = 0
        self.record('target_begin')

    def before_input(self):
        """输入及地图状态检查前核对窗口、停止、弹窗与收集提示，不增加操作统计。

        这里不重读章节身份：身份由 start 核对一次（见 verify_identity）。真正必须逐帧看的是
        战斗弹窗与收集品提示——漏一帧就可能把落点点进战斗，或踩掉一次拾取机会。
        """
        self.check()
        if not self.popup_handling:
            self.identity(self.win.client())

    def count_input(self, scene=False, moves_squad=False):
        """成功发送输入后统计实际操作，场景序号与小队输入序号继续服务于缓存失效。

        纯截图、状态检查或驱动失败不能调用；地图控件不改变小队或场景位置，只统计实际点击。
        scene 标识场景手势，moves_squad 标识可能移动小队的场景输入，不用于推断操作成功结果。
        """
        self.inputs += 1
        self.target_inputs += 1
        if scene:
            self.gestures += 1
        if moves_squad:
            self.squad_inputs += 1

    def map_input_sent(self, point):
        """地图控件实际点击成功后统计并留档；已有目标状态直接返回时不会调用。

        地图控件不纳入场景手势或小队移动序号，不能让一次纯面板开合使已知小队位置失效。
        """
        self.count_input()
        self.record('map_input_sent', point=list(point), coordinate_space='client')

    def begin_move_target(self, goal, target):
        """按稳定执行编号选择路线进展历史，EX 续行或同目标恢复不重新取得失败额度。

        target 使用地图坐标，在首次派发时冻结；后来标记位置微调不能刷新剩余路程基准。
        这里只选择纯计算对象，不发送输入；用途切换只暂停监控，不删除同编号历史。
        """
        key = (goal['kind'], goal['id'])
        if key not in self.movement_progress:
            self.movement_progress[key] = MovementProgress(target)
        self.active_movement_progress = self.movement_progress[key]

    def complete_move_target(self, goal):
        """真实到点或战斗胜利后释放该目标的路线快照，不把失败或切换用途当作完成。

        完成记录由执行层的既有成功分支确认；未完成目标仍保留失败次数与历史最优值供续行。
        及时释放道路数组，避免同章清理大量敌人后为已完成目标继续保存多份道路快照。
        """
        progress = self.movement_progress.pop((goal['kind'], goal['id']), None)
        if self.active_movement_progress is progress:
            self.active_movement_progress = None
        self.planned_movement_click = None

    def review_move_progress(self, observation):
        """真实移动后用可靠停稳定位复核路线进展，沿用坐标或只有镜头变化时不消费证据。

        冻结道路与目标由移动规划建立；敌人标记、视野或用途切换不能提供进展。
        连续无进展到上限向执行层抛 NavigationStalled，不经过回正重扫清空失败记录。
        """
        progress = self.active_movement_progress
        if (progress is None or observation.get('squad_source') == 'known'
                or observation.get('position_kind') != 'squad'
                or observation.get('status') not in ('accepted', None)):
            return
        score = observation.get('iou')
        if score is None or not np.isfinite(score):
            return
        try:
            result = progress.observe(observation['position'])
        except NavigationStalled:
            self.record('movement_progress', **progress.last_result)
            raise
        if result is not None:
            self.record('movement_progress', **result)
            logger.info(f"移动路线进展：阶段 {result['stage']}，剩余路程 {result['remaining']} 地图像素，"
                        f"有效推进 {result['progressed']}，连续无进展 {result['failures']} 次。")

    def popup(self, field):
        """战斗闸门：弹窗、战斗中、结算与可跳过剧情都抛给执行层，接管之内放行按钮点击。

        这几种画面上都不是主线地图，平时任何落点点击都可能点进战斗、点掉结算或点错按钮，所以立即
        停止并交给执行层（普通关卡由执行层接管）；battle_mode 为真说明已经进入接管（或正要接管），
        这时它们就是要被点的对象。EX 弹窗关闭后交执行层重新定位并绕行，不进入 EX 战斗。
        实机里点「进入战斗」之后战斗更早就开始了：外层若只认弹窗，就会在战斗画面上继续做野外动作
        （点地图控件、点地面），所以战斗中与结算也在这里拦住。
        识别战斗与剧情需要匹配多个模板，而这个闸门每个手势都会走一次，所以先用野外判据
        挡掉正常情况：地图控件在画面上就说明不在战斗里。
        """
        score = goto.battle_popup_score(field)
        if score <= .8:
            if self.battle_mode or battle.field_visible(field):
                return
            kind = battle.battle_state(field)['state']
            if kind in ('battle', 'victory', 'defeat', 'continue', 'story'):
                self.preview(field)
                raise TargetTriggered(f'识别到{label(BATTLE_STATES, kind)}，转交战斗接管。',
                                      {'kind': 'battle_popup', 'chapter': self.expected_identity[0]})
            return
        self.preview(field)
        is_ex = battle.ex_stage(field)
        if self.battle_mode and not is_ex:
            return
        evidence = {'kind': 'battle_popup', 'chapter': self.expected_identity[0]}
        message = '画面出现战斗准备弹窗，交由执行层接管这场战斗。'
        if is_ex:
            # 该记录只证明发出了移动目的地，弹窗可能来自途中 EX；实际归属由重新定位确认。
            target_click = self.last_enemy_click
            if (target_click is not None and target_click['squad_inputs'] == self.squad_inputs
                    and target_click['id'] == self.owner.state.execution_target):
                evidence['target_click'] = dict(target_click)
            self.last_enemy_click = None
            self.popup_handling = True
            try:
                origin = self.win.client_origin()
                self.win.handler.mouse_click(origin[0] + 1155, origin[1] + 521)
                self.pause(1.2)
                if goto.battle_popup_score(self.win.client()) > .8:
                    raise RuntimeError('EX 关卡弹窗未能关闭，不再重试。')
            finally:
                self.popup_handling = False
            self.ex_popup_count += 1
            if self.ex_popup_count > 6:
                raise ExBypassUnavailable('本章累计关闭 EX 弹窗超过 6 次，停止重复尝试。')
            self.localized_report = None
            self.last_registration = None
            self.reuse_squad = False
            evidence['kind'] = 'ex_stage_closed'
            message = 'EX 关卡弹窗已关闭，重新定位后绕行并继续原目标。'
            self.record('ex_popup_closed', force=True, count=self.ex_popup_count,
                        target_click=evidence.get('target_click'))
        raise TargetTriggered(message, evidence)

    def prepare_ex_bypass(self, goal=None):
        """关闭 EX 后重新定位小队，关联附近 EX 并保存临时避让区，不沿用移动前的冻结坐标。

        本方法不点击地面；下一次 plan 根据实时小队位置计算安全路点。EX 标记可能被小队遮挡，
        可使用运行状态中的近距离历史 EX，但缺失或歧义时不拿原目标代替 EX 坐标。
        返回实际关联的避让记录及敌人编号，供执行层区分目标本身与途中触发；不在此处改型。
        """
        self.localized_report = None
        self.last_registration = None
        self.reuse_squad = False
        self.verify_identity(self.await_field())
        observation = self.observe()
        squad = observation['position']
        obstacle = self.ex_bypass.encounter(squad, self.owner.state.enemies.values())
        for progress in self.movement_progress.values():
            progress.interrupt()
        target = goal.get('position') if goal else None
        self.record('ex_bypass_begin', squad=squad, goal=goal, obstacle=obstacle)
        logger.info(f'EX 绕行定位：小队 {points_text(squad)}，EX {points_text(obstacle["position"])}，'
                    f'原目标 {points_text(target) if target is not None else "尚未确定"}，'
                    f'避让半径 {obstacle["radius"]:.1f} 像素，触发 {obstacle["encounters"]} 次。')
        return obstacle

    def identity(self, field):
        """逐帧闸门：非野外交上层场景控制器，战斗与收集提示保留原接管，不重读章节身份。

        每个手势、每次观测以及共享移动闭环都调用它，所以必须便宜。章节与难度只在会话开始
        （start 的 verify_identity）核对一次：逐帧重读章节号不会带来新信息，而弹窗与收集品提示
        漏一帧就可能把落点点进战斗或踩掉一次拾取机会。
        """
        if not self.battle_mode:
            self.owner.scene_controller.raise_if_needed(field, known_only=True)
        self.popup(field)
        self.check_collectible(field)

    def verify_identity(self, field):
        """在会话开始、战后和恢复时复核身份；读不到时最多重看三帧，不把单帧漏读当成换章。

        调用前由流程保证角标可见，镜头回正须先收起展开地图。每帧检查窗口与停止请求，普通弹窗
        转战斗接管，EX 关闭后转绕行；重试只等待并截图，不发送地面输入。读到其他章节或难度时
        立即交上层重新确认场景，连续读不到也停止，不使用旧身份兜底。
        """
        for attempt in range(IDENTITY_CHECK_ATTEMPTS):
            self.check()
            self.owner.scene_controller.raise_if_needed(field, known_only=True)
            self.popup(field)
            current = chapter_identity(field, self.owner.chapter_ocr)
            self.check()
            if current == self.expected_identity:
                return
            if current is not None:
                from module.campaign.noncombat import SceneRequired
                raise SceneRequired(f'主线身份已变化：预期 {self.expected_identity}，读到 {current}，交上层重建会话。')
            if attempt + 1 < IDENTITY_CHECK_ATTEMPTS:
                logger.warning(f'主线章节或难度暂未读到：预期 {self.expected_identity}，'
                               f'等待新帧复验（{attempt + 1}/{IDENTITY_CHECK_ATTEMPTS - 1}）。')
                self.pause(IDENTITY_CHECK_INTERVAL)
                field = self.await_field()
        raise WindowChanged(f'主线章节或难度连续 {IDENTITY_CHECK_ATTEMPTS} 帧无法确认：预期 {self.expected_identity}。')

    def check_collectible(self, field):
        """收集品任务见到橙色倒三角即停；不追加拾取或微调点击。"""
        if not self.collectible_mode and self.request['purpose'] != 'collectible':
            return
        indicator = collectible_indicator(field)
        if indicator is not None:
            self.preview(field)
            raise TargetTriggered('已发现收集品提示，停止并交人工拾取。', indicator)

    def preview(self, field):
        """发布任务预览，仅在调试模式下保存最新截图；截图不代表战斗或拾取完成。"""
        from module.campaign.navigation.evidence import write_image
        self.record('preview', field=field, force=True)
        if self.debug_mode:
            self.folder.mkdir(parents=True, exist_ok=True)
            write_image(self.folder / 'last_field.png', field)
        publish_preview_frame(np.ascontiguousarray(field[:, :, ::-1]), interval=0,
                              source=self.owner.campaign_source(self.owner.last_snapshot))

    def record(self, event, field=None, force=False, **details):
        """把当前目标、实际操作统计与决策轮附在证据事件上，不重新访问窗口或运行视觉识别。

        field 必须是本次业务已取得的 BGR 客户区画面；归档失败由记录器告警，不改变操作控制流。
        """
        state = getattr(self.owner, 'state', None)
        self.trace.record(event, field=field, force=force, purpose=self.request['purpose'],
                          tick=getattr(state, 'tick', None), inputs=self.inputs,
                          target_inputs=self.target_inputs, gestures=self.gestures, **details)

    def observe_frame(self, field, **options):
        """观测既有客户区截图并保存同帧识别与定位结果，失败帧强制留证。

        接受结果、圆环数量、敌人／光幕、配准指标和错误原因均保留；内嵌预览图由记录器剔除。
        此入口只包装原观察器调用，不改变定位阈值与重试次数。
        """
        snapshot = self.owner.observer.observe(field, self.expected_identity, time.time(), **options)
        self.record('observation', field=field, force=not located(snapshot), snapshot=snapshot,
                    options=options)
        return snapshot

    def emit(self, **values):
        """将共享闭环的进度写入任务日志，电梯和移动结果仍由执行层回写状态。

        一次回调一行：字段翻译见 describe_move，前面带上这个目标已经操作过多少次，
        好让日志能直接读出「这一步花了几次操作」。
        """
        self.record('navigation', values=values)
        logger.info(f'主线移动（本目标第 {self.target_inputs} 次操作）：{describe_move(values)}')

    def remember_scene(self, field):
        """移动反馈帧累积敌人与封锁并立即发布，下一步 A* 不沿用移动前的过期切开图。"""
        snapshot = self.observe_frame(field)
        self.owner.last_snapshot = snapshot
        self.owner.state.update(snapshot, advance=False)
        if self.owner.cut.update(self.owner.state.blocking()):
            logger.info('移动期间光幕状态变化，已更新阻断路网。')
        publish_preview_frame(np.ascontiguousarray(field[:, :, ::-1]), interval=0,
                              source=self.owner.campaign_source(snapshot))
        return snapshot

    def await_field(self):
        """等到画面确实可以操作野外再发输入，返回那一帧。

        点「进入战斗」之后的过场、以及结算回野外前的加载，都会有短暂「不是野外」的画面；在那些帧上
        点地图控件会被动画吃掉，展开随即失败、任务按窗口异常停下。所以野外动作之前先确认画面：
        弹窗、战斗中、结算与可跳过剧情由身份闸门交给执行层，认不出（加载、黑屏等）就在这里等，
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
                self.record('localization_failed', field=self.trace.last_field, force=True,
                            attempt=attempt + 1, error=str(exc))
                continue
            self.record('localization_result', report=report, attempt=attempt + 1)
            self.check()
            if np.mean(terrain(panel) != terrain(self.win.capture())) > .03:
                raise RuntimeError('定位期间道路还在变，不能用这次位置。')
            matrix = np.asarray(report['roi_to_map'])
            report['enemy_markers'] = goto.normal_enemy_markers(panel, matrix, mode='expanded')
            from module.campaign.vision.perception import boss_enemy_markers
            report['enemy_markers_by_kind'] = {
                'normal': report['enemy_markers'], 'boss': boss_enemy_markers(panel, matrix, mode='expanded')}
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

    def short_move_for_localization(self):
        """展开地图定位失败后向附近开阔道路短移一次，改变下一轮的地图道路特征。

        先用新的紧凑小地图定位确认小队位置，再从 80～160 地图像素外选择净空至少 40 的道路点；
        候选不得靠近当前已确认的敌人 60 地图像素以内。每个候选都经过道路连通与既有镜头规划、
        EX 避让、弹窗和输入闸门检查，只有取得有效落点才发送一次点击并等待停稳。
        没有安全候选、紧凑定位未通过或规划失败时抛出异常，不使用固定屏幕方向盲移。
        """
        goto.map_close(self.win)
        snapshot = self.snapshot()
        observation = self.reusable_report()
        if snapshot.get('localization') != 'accepted' or observation is None:
            raise ValueError('短移前的紧凑小地图定位未通过。')
        position = np.asarray(observation['position'], float)
        clearance = cv2.distanceTransform(self.localizer.road.astype(np.uint8), cv2.DIST_L2, 5)
        ys, xs = np.where(clearance >= 40)
        distances = np.hypot(xs - position[0], ys - position[1])
        nearby = np.where((distances >= 80) & (distances <= 160))[0]
        enemies = np.asarray(observation.get('enemy_markers', []), float)
        if enemies.size and nearby.size:
            points = np.column_stack([xs[nearby], ys[nearby]])
            keep = np.min(np.linalg.norm(points[:, None, :] - enemies[None, :, :], axis=-1), axis=1) >= 60
            nearby = nearby[keep]
        if not nearby.size:
            raise ValueError('附近没有满足距离、净空和敌人间距要求的短移道路。')
        scores = np.abs(distances[nearby] - 120) - 1.5 * clearance[ys[nearby], xs[nearby]]
        failure = None
        for candidate in nearby[np.argsort(scores)]:
            target = np.array([xs[candidate], ys[candidate]], float)
            try:
                self.localizer.route(position, target)
                click = self.plan(observation, target)
            except (NoConnectedRoad, ValueError) as exc:
                failure = exc
                continue
            logger.info(f'定位恢复短移：目标 {points_text(target)}，距小队 {distances[candidate]:.1f} 地图像素，'
                        '停稳后重新展开地图定位。')
            self.move(click)
            self.wait_stopped()
            return target
        raise ValueError(f'附近道路均无法生成短移落点：{failure}')

    def confirm_barriers(self, snapshot):
        """只平移镜头确认当前可见光幕，返回已有小队坐标与新视口配准，不发送地面点击。"""
        from module.campaign.navigation.barrier_navigation import confirm_visible
        return confirm_visible(self, snapshot)

    def review_barrier(self, snapshot, identifier):
        """居中复核指定封锁，只有两帧可信结果才允许该编号的光幕被消除。"""
        from module.campaign.navigation.barrier_navigation import review_centered
        return review_centered(self, snapshot, identifier)

    def mechanism_viewport(self):
        """机关触发后保留当前镜头并展开地图，独立定位视野中心，不读取小队圆环。

        连续两帧道路配准均须通过原门限，视野中心漂移不超过 12 地图像素且位于本章范围内。
        展开不复位地图，不把面板中心当作小队，也不把此报告写入小队定位缓存；窗口、剧情、
        战斗和取消仍经过原闸门。配准失败或镜头未停稳时抛 ValueError，不发送地面输入。
        """
        self.check()
        self.await_field()
        goto.map_open(self.win, reset=False)
        reports = []
        for index in range(2):
            if index:
                self.pause(.4)
            field = self.win.client()
            self.identity(field)
            panel = self.win.capture()
            try:
                report = self.localizer.locate(panel, f'mechanism_view_{index}',
                                               require_player=False, locate_player=False)
            except RuntimeError as exc:
                raise ValueError(f'机关触发后的视野道路配准未通过：{exc}') from exc
            point = np.asarray(report['position'], float)
            if np.any(point < 0) or np.any(point >= self.owner.state.size):
                raise ValueError('机关触发后的视野中心超出章节地图范围。')
            reports.append(report)
        if np.linalg.norm(np.asarray(reports[0]['position']) - reports[1]['position']) > 12:
            raise ValueError('机关触发后的镜头尚未停稳，保留原封锁状态。')
        self.record('mechanism_viewport', field=field, force=True, reports=reports)
        return reports[-1]

    def centered_mechanism_barrier(self, goal):
        """按机关切镜后的距离排序与 YOLO 双柱方向匹配解锁光幕，不依赖小队定位。

        先保留镜头展开地图，通过既有两帧道路配准取得视野中心。距离不设置接受半径；
        仅一个或与其他光幕独立的标注直接匹配，邻近两候选才校验双柱方向。YOLO 前收起展开面板，
        避免遮住中央立柱，但不回正镜头、不降低模型门限、不追加确认帧或移动点击。
        无标注、缺双柱或距离／方向冲突均抛 ValueError，调用方不得修改机关及封锁状态。
        """
        from module.campaign.vision.barriers import match_mechanism_barrier, mechanism_barrier_candidates
        from module.campaign.vision.objects import detect_objects

        report = self.mechanism_viewport()
        annotations = self.owner.cut.chart.barrier_annotations
        candidates, competing = mechanism_barrier_candidates(annotations, report['position'])
        objects, field = [], None
        if competing:
            goto.map_close(self.win)
            field = self.win.client()
            self.identity(field)
            objects = detect_objects(field, kinds=('barrier',))
        self.record('mechanism_barrier_candidates', field=field, force=True, goal=goal,
                    viewport=report['position'], candidates=candidates, objects=objects, competing=competing)
        annotation, evidence = match_mechanism_barrier(annotations, report['position'], objects)
        self.record('mechanism_barrier_located', field=field, force=True, goal=goal, annotation=annotation,
                    viewport=report['position'], evidence=evidence)
        logger.info(f"机关解锁光幕关联：标注 {annotation['id']}，"
                    f"距离 {evidence['candidates'][0]['distance']:.1f} 地图像素，"
                    f"依据 {'最近标注与 YOLO 双柱方向' if competing else '独立最近标注'}。")
        return annotation

    def trigger_visible_mechanism(self, goal):
        """直接点击当前画面中唯一关联到目标的未开启机关，确认成功后返回 True。

        只接受紧凑帧里已经关联到 goal 编号的 off 检测，点击框中心且必须位于地面安全区；
        点击并等待停稳后，以同编号 on 或小队进入到达半径确认成功。未识别、关联歧义、
        落点越界或点击后未确认均返回 False，由调用方继续原有地图标注导航，不把发送点击当成触发。
        """
        from module.campaign.navigation.camera_navigation import FIELD_BOUNDS, inside
        from module.campaign.navigation.controls import SQUAD_REACH

        snapshot = self.snapshot()
        matches = [item for item in snapshot.get('mechanisms', [])
                   if item.get('id') == goal['id'] and item.get('state') == 'off' and item.get('click') is not None]
        if len(matches) != 1:
            return False
        click = np.asarray(matches[0]['click'], float)
        if click.shape != (2,) or not np.isfinite(click).all() or not inside(click, FIELD_BOUNDS):
            logger.info(f'地面机关 {goal["id"]} 的可见落点不在安全点击区，沿用地图标注导航。')
            return False
        self.record('mechanism_direct_click', goal=goal, click=click.tolist(), snapshot=snapshot)
        logger.info(f'地面机关 {goal["id"]} 已在画面中唯一关联，直接点击检测框中心 {points_text(click)}。')
        self.move(click)
        self.wait_stopped()
        after = self.snapshot()
        current = next((item for item in after.get('mechanisms', []) if item.get('id') == goal['id']), None)
        if current is not None and current.get('state') == 'on':
            logger.info(f'地面机关 {goal["id"]} 直点后已识别为开启。')
            return True
        squad = after.get('squad')
        if (after.get('localization') == 'accepted' and squad is not None
                and np.linalg.norm(np.asarray(squad, float) - np.asarray(goal['position'], float)) <= SQUAD_REACH):
            logger.info(f'地面机关 {goal["id"]} 直点后小队已进入到达范围。')
            return True
        logger.info(f'地面机关 {goal["id"]} 直点后尚未确认触发，继续地图标注导航。')
        return False

    def plan(self, observation, target, enemy_assist=False, enemy_kind='normal'):
        """用已确认的小队定位开始镜头规划，紧凑定位不足时仅重试一次完整展开定位。

        紧凑快照只提供底图位置，不冒充展开 ROI 的投影基准。起始镜头不一致或跟踪需要展开
        校正时，先回正并取得原始展开报告再规划；若小队已移动超过 12 地图像素，交回移动
        闭环更新起点。窗口、弹窗和路线停滞异常不捕获，也不补发清理输入。
        纯镜头找敌人后的已知起点不进入实时小队定位回退；当前视野不足以规划时直接停止。
        enemy_kind 传递原目标类型，Boss 脚圈辅助不得关联成普通敌人。
        """
        from module.campaign.navigation.barrier_navigation import BarrierUnconfirmed, curtains
        self.planned_enemy_click = None
        self.planned_movement_click = None
        options = {'enemy_kind': enemy_kind} if enemy_kind != 'normal' else {}
        field = self.win.client()
        self.identity(field)
        snapshot = self.observe_frame(field, known_squad=observation['position'])
        if curtains(snapshot):
            try:
                snapshot = self.confirm_barriers(snapshot)
            except BarrierUnconfirmed as exc:
                # 光幕确认失败不再终止任务：路网保持原样，真撞上未开光幕由移动闭环停滞检测兜底。
                logger.warning(f'光幕确认未完成（{exc}）；路网保持不变，继续规划。')
            else:
                self.owner.state.update(snapshot, advance=False)
                self.owner.cut.update(self.owner.state.blocking())
                self.remember_navigation(snapshot)
                observation = self.reusable_report() or observation
        original_target = target
        if (self.active_movement_progress is not None
                and self.active_movement_progress.failures >= MAX_FAILURES):
            raise NavigationStalled('同一目标连续移动未取得可靠路线进展，禁止继续镜头规划。')
        if self.ex_bypass.obstacles:
            waypoint = self.ex_bypass.waypoint(self.owner.cut.road, observation['position'], target)
            self.record('ex_bypass_waypoint', squad=observation['position'], target=target,
                        waypoint=waypoint.tolist(), obstacles=self.ex_bypass.obstacles)
            logger.info(f'EX 避让路点：小队 {points_text(observation["position"])}，'
                        f'原目标 {points_text(target)}，本次落点 {points_text(waypoint)}。')
            target = waypoint
            enemy_assist = False
        try:
            click = super().plan(observation, target, enemy_assist=enemy_assist, **options)
        except ValueError as exc:
            if observation.get('squad_source') == 'known':
                raise RuntimeError(f'当前镜头无法完成移动规划：{exc}；停止执行，不重新寻找小队圆环。') from exc
            if observation.get('minimap_mode') != 'compact':
                raise
            logger.info(f'紧凑定位不足以完成镜头规划，按需展开重新定位一次：{exc}')
        else:
            self.prepare_move_progress(observation, target, click)
            self.planned_enemy_click = dict(click=np.asarray(click).tolist(),
                                           target=np.asarray(target).tolist(),
                                           direct=np.array_equal(target, original_target))
            return click
        self.localized_report = None
        updated = self.observe()
        if np.linalg.norm(np.asarray(updated['position']) - np.asarray(observation['position'])) > 12:
            raise SquadPositionChanged(observation['position'], updated['position'])
        if self.ex_bypass.obstacles:
            target = self.ex_bypass.waypoint(self.owner.cut.road, updated['position'], original_target)
        click = super().plan(updated, target, enemy_assist=enemy_assist, **options)
        self.prepare_move_progress(updated, target, click)
        self.planned_enemy_click = dict(click=np.asarray(click).tolist(), target=np.asarray(target).tolist(),
                                       direct=np.array_equal(target, original_target))
        return click

    def prepare_move_progress(self, observation, waypoint, click):
        """成功规划后冻结路线进展基准，只有实际发送该点击才允许后续定位评估。

        EX 退出路点与安全绕行道路由同一份有效路网生成；原目标坐标在首次派发时冻结，
        不能用本帧重新识别或新的计划坐标替代历史最优值。未发送的计划不计一次失败或进展。
        """
        if self.active_movement_progress is None:
            return
        self.active_movement_progress.prepare(self.owner.cut.road, observation['position'], waypoint,
                                              self.ex_bypass.obstacles)
        self.planned_movement_click = np.asarray(click, float).copy()

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
        _, markers = self.owner.observer.minimap_markers(panel, 'expanded')
        positions = project_centers([item['position'] for item in markers], np.asarray(matrix, float))
        size = self.owner.state.size
        return [dict(item, position=position) for item, position in zip(markers, positions)
                if 0 <= position[0] < size[0] and 0 <= position[1] < size[1]]

    def click_client(self, point, *, camera_only=False):
        """发送有效的客户区点击，共用窗口、弹窗与停止闸门，不调用地面移动入口。

        坐标按 1776×999 客户区验证并换算到屏幕；最终发送仍由 CampaignInput 校验窗口、取消、
        停止请求。界面按钮直接使用此入口，地面落点须先由 move 复核收集提示及光幕。
        camera_only 仅供已复核的镜头控件点击，不得用于地面或身份未知的点击。
        """
        point = np.asarray(point, float)
        if point.shape != (2,) or not np.isfinite(point).all() or not (0 <= point[0] < 1776 and 0 <= point[1] < 999):
            raise ValueError('点击位置越界或不是有效坐标。')
        self.check()
        origin = self.win.client_origin()
        options = {'_campaign_camera_only': True} if camera_only else {}
        self.win.handler.mouse_click(origin[0] + round(point[0]), origin[1] + round(point[1]), **options)

    def move(self, click):
        """复核收集提示后发送客户区地面落点，沿用规划阶段最后确认的光幕路网。

        光幕确认失败不改变既有状态，也不单独否决移动；未确认障碍导致的停滞由移动闭环处理。
        坐标、窗口、弹窗与取消仍由当前会话和生产驱动代理校验。
        """
        click = np.asarray(click, float)
        if click.shape != (2,) or not np.isfinite(click).all() or not (0 <= click[0] < 1776 and 0 <= click[1] < 999):
            raise ValueError('点击位置越界或不是有效坐标。')
        self.check()
        field = self.win.client()
        self.check_collectible(field)
        self.click_client(click)
        planned, self.planned_movement_click = self.planned_movement_click, None
        if (self.active_movement_progress is not None and planned is not None
                and np.array_equal(planned, click)):
            self.active_movement_progress.sent()

    def remember_enemy_click(self, click, target, is_final):
        """记录实际成功发送的敌人目标直点，供重新定位后核对实际 EX 与原执行编号。

        仅由导航在 move 成功后调用；target 是本次选定的敌人落点，click 为客户区落点。
        附近试点、箭头恢复短移和 EX 绕行路点不记录为原目标直点。只消费匹配本次规划结果的
        直点，记录发送后的移动输入计数；后续其他移动输入或切换目标会使该记录失效。
        本记录不证明已经到达或选中敌人，弹窗可能由途中 EX 触发，须另取得实际触发编号。
        此处坐标相等仅核对同一规划与输入，不用于重新推断 EX 与 Boss 的空间身份。
        """
        self.last_enemy_click = None
        planned, self.planned_enemy_click = self.planned_enemy_click, None
        identifier = self.owner.state.execution_target
        if (not is_final or planned is None or not planned['direct'] or identifier is None
                or not np.array_equal(click, planned['click']) or not np.array_equal(target, planned['target'])):
            return
        self.last_enemy_click = dict(id=identifier, target=np.asarray(target).tolist(),
                                    click=np.asarray(click).tolist(), squad_inputs=self.squad_inputs)
        self.record('enemy_target_click', **self.last_enemy_click)

    def pause(self, seconds):
        """短间隔等待持续检查取消、焦点与几何，不依赖原型的全局活动窗口。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def wait_stopped(self):
        """按统一道路判据结束等待，再单独检查停稳帧中的小队定位是否可用。

        普通移动和机关切镜共用道路停稳检测，不要求圆环连续出现或位置稳定。
        停稳后只识别一次圆环；不是唯一圆环时抛 SquadOffscreen，不重启等待、不追加移动，
        机关目标交已有中央光幕确认分支，其余目标交既有失败处理，不能据道路停稳推断到达。
        """
        from module.campaign.navigation.camera_navigation import SquadOffscreen
        from module.campaign.vision.frame import frame_inputs
        from module.campaign.vision.perception import detect_markers

        watch = Stopwatch()
        field = super().wait_stopped()
        logger.info(f'停稳等待结束（小地图道路已稳定），耗时 {watch}')
        players, _ = detect_markers(frame_inputs(field).compact, np.eye(3), mode='compact')
        if len(players) != 1:
            raise SquadOffscreen(f'道路已停稳，后续小队定位不可用：本帧圆环数量为 {len(players)}；'
                                 '不继续等待，交由目标确认或定位恢复。')

    def snapshot(self, expanded=False, keep_expanded=False, known_squad=None, *, retry_localization=True):
        """取得一拍业务观测；定位失败时有限重试，展开默认收起，也可留给紧接着的侦察。

        战斗结束后的第一帧允许沿用战斗前的小队坐标（见 reuse_reason）；此外小队只在收到点击时才
        移动、镜头只在收到拖动时才平移，所以上一次被接受的定位之后没有再发过手势时同样直接沿用：
        紧凑面板配准没过线时重新展开大地图只会得到同一个坐标，白花一次开合与几秒配准。
        每帧都打一行观测摘要（见 describe_snapshot）与这一段耗时：推图的等待绝大部分花在这里。
        keep_expanded 仅用于 expanded=True 后直接衔接侦察；调用方负责正常结束时收起。
        已经展开的定位重试保留面板，不再强制开合回正。可信紧凑帧可交给移动入口复用小队位置。
        展开时只用无新增手势的已确认小队坐标约束本帧圆环；失败后小幅拖动再识别一次，
        仍失败则停止，不能只复用小队坐标而把尚未定位的敌人当成不存在。
        known_squad 只供尚未发送移动的找敌人阶段使用：跳过实时小队定位和圆环重试，
        使用已有小队底图坐标规划路线；当前视野配准失败可复用小队位置，但不并入当前观测。
        定位后只有镜头拖动时自动使用已知小队坐标，即使小队已离屏也不重新寻找圆环；
        点击、其他可能移动小队的输入或战斗会使这条自动复用路径失效。
        retry_localization=False 只返回本帧结果，不展开重试或复用旧定位；供主循环先尝试
        无底图坐标的可见近敌。失败仍标为未定位，不允许写入路网、覆盖或用于机关／电梯规划。
        """
        if keep_expanded and not expanded:
            raise ValueError('保留展开面板要求 expanded=True。')
        self.check()
        watch = Stopwatch()
        self.await_field()
        if (known_squad is None and self.last_registration is not None
                and self.last_registration['gestures'] != self.gestures):
            known_squad = self.stationary_squad()
            if known_squad is not None:
                logger.info(f'定位后仅有镜头拖动，保留小队位置 {points_text(known_squad)}，重新配准当前视野。')
        expected = self.stationary_squad() if expanded and known_squad is None else None
        options = {'expected_squad': expected} if expected is not None else {}
        if known_squad is not None:
            options['known_squad'] = known_squad
        self.win.reset_minimap(expanded=expanded, reset=expanded and known_squad is None)
        field = self.win.client()
        self.identity(field)
        snapshot = self.observe_frame(field, **options)
        if retry_localization and expanded and snapshot.get('localization') != 'accepted':
            logger.info(f'展开地图定位未通过：小幅平移 {EXPANDED_RETRY_PIXELS:.0f} 客户区像素后重试（1/1）。')
            self.win.capture()
            self.pan_expanded((1., 0.), distance=EXPANDED_RETRY_PIXELS)
            field = self.win.client()
            self.identity(field)
            snapshot = self.observe_frame(field, **options)
            if snapshot.get('localization') != 'accepted':
                self.owner.last_snapshot = snapshot
                self.preview(field)
                raise RuntimeError('展开地图小幅平移重试后仍未定位成功；停止目标选择并请求人工复核。')
        elif retry_localization and known_squad is not None and not located(snapshot):
            if (self.stationary_squad() is not None
                    and self.last_registration['gestures'] == self.gestures):
                snapshot = self.reuse_registration(snapshot, 'unchanged')
            else:
                self.owner.last_snapshot = snapshot
                self.preview(field)
                raise RuntimeError('找敌人的当前视野配准未通过；停止目标选择，不重新定位小队。')
        elif retry_localization and not located(snapshot):
            why = self.reuse_reason()
            snapshot = (self.reuse_registration(snapshot, why) if why is not None
                        else self.retry_localization(snapshot, expanded=expanded, keep_expanded=keep_expanded))
        if snapshot.get('localization') == 'accepted':
            if snapshot.get('anchor_error') is not None:
                logger.info(f"展开地图小队约束定位通过：小队偏差 {snapshot['anchor_error']:.1f} 地图像素，"
                            f"道路重合度 {snapshot['road_iou']:.3f}，已定位敌人 {len(snapshot['enemies'])} 个。")
            # 只记刚配准成功的坐标：沿用帧的坐标本来就是这个记录，不需要再写一遍。
            if known_squad is None:
                self.remember_registration(snapshot['squad'], snapshot.get('roi_to_map'),
                                           snapshot.get('coverage_size'))
            self.remember_navigation(snapshot)
        self.owner.last_snapshot = snapshot
        if expanded and not keep_expanded:
            self.win.reset_minimap(expanded=False, reset=False)
        self.record('snapshot_result', snapshot=snapshot)
        logger.info(f'观测完成：{describe_snapshot(snapshot)}，耗时 {watch}')
        return snapshot

    def remember_registration(self, squad, roi_to_map, coverage_size):
        """记下刚被接受的小队坐标与它的配准矩阵，供之后没再发过手势的帧直接沿用。

        squad / roi_to_map / coverage_size 必须来自同一帧、同一个面板的配准结果：沿用时会整组套用，
        坐标系不配套会把「本帧看过哪里」算到别处（见 run_state.panel_quad）。
        同时记下当时的手势计数——小队只在收到点击时才移动、镜头只在收到拖动时才平移，
        所以「之后没再发过手势」等价于「这份坐标一定还成立」（见 reuse_reason）。
        单独记录可能移动小队的输入计数：纯拖动后可继续使用小队底图坐标，
        但旧配准矩阵仍按完整手势计数失效，必须重新配准当前视口。
        新定位同时结束战前坐标的临时复用许可，使战后重新确认的位置可用于展开约束。
        """
        self.last_registration = dict(squad=list(squad), roi_to_map=roi_to_map,
                                      coverage_size=coverage_size, gestures=self.gestures,
                                      squad_inputs=self.squad_inputs)
        self.reuse_squad = False

    def stationary_squad(self):
        """返回未被移动输入或战斗失效的小队底图坐标，供纯镜头观测或展开配准约束使用。

        只返回底图坐标，不传递旧面板矩阵；纯拖动、展开与收起均不会移动小队。
        点击及其他可能移动小队的输入使坐标失效，战后未经重新确认的位置也不能作为约束。
        """
        record = self.last_registration
        if (self.battle_mode or self.reuse_squad or record is None
                or record['squad_inputs'] != self.squad_inputs):
            return None
        position = np.asarray(record['squad'], float)
        if position.shape != (2,) or not np.isfinite(position).all():
            return None
        return position.tolist()

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
        why=camera 表示只有镜头拖动，小队坐标仍有效；why=battle 用于战后首次观测，
        why=unchanged 表示缓存后没有任何手势。没有记过坐标时原样返回，交正常重试路径。
        """
        self.reuse_squad = False
        if self.last_registration is None:
            return snapshot
        snapshot = dict(snapshot, localization='reused', squad=list(self.last_registration['squad']),
                        roi_to_map=self.last_registration['roi_to_map'],
                        coverage_size=self.last_registration['coverage_size'])
        reason = {'battle': '战后首次观测允许复用战前定位', 'unchanged': '缓存后无新增手势',
                  'camera': '仅镜头拖动，小队未移动'}[why]
        logger.warning(f"本帧配准未通过，复用已确认的小队位置 {points_text(snapshot['squad'])}"
                       f"（依据：{reason}）。")
        return snapshot

    def remember_navigation(self, snapshot):
        """将本帧可信紧凑小队定位交给移动入口，只携带底图坐标和同帧分类敌人。

        不携带 roi_to_map：紧凑矩阵不能充当原始展开 ROI 的镜头基准。
        沿用帧和缺少实际配准分数的快照不建立导航缓存，后续任一手势使缓存失效。
        纯镜头找敌人后的快照保留 squad_source=known，移动入口不得要求当前视口与小队重合；
        首次实际移动后缓存失效，后续位置由移动闭环重新观测。
        """
        if snapshot.get('localization') != 'accepted' or (snapshot.get('minimap') or {}).get('mode') != 'compact':
            return
        position = np.asarray(snapshot.get('squad'), float)
        iou = snapshot.get('road_iou')
        if position.shape != (2,) or not np.isfinite(position).all() or iou is None or not np.isfinite(iou):
            return
        markers = [list(item['position']) for item in snapshot.get('enemies', []) if item.get('kind') == 'normal']
        report = dict(position=position.tolist(), position_kind='squad', iou=float(iou),
                      minimap_mode='compact', enemy_markers=markers,
                      enemy_markers_by_kind={'normal': markers, 'boss': [list(item['position'])
                          for item in snapshot.get('enemies', []) if item.get('kind') == 'boss']})
        if snapshot.get('squad_source') == 'known':
            report['squad_source'] = 'known'
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

    def pan_expanded(self, direction, distance=PAN_PIXELS):
        """在展开面板里沿客户区方向拖一次换视野；起止点以面板中心对称，拖动经输入闸门发送。

        只平移地图视图，小队本身不动：原本道路证据太少、配准差一点没过线的视野会换成另一段，
        停稳后由调用方重新截图配准。方向向量是客户区方向（(1, 0) 表示把地图内容向右拖）。
        distance 是客户区像素长度，定位恢复可使用较小位移；默认保持既有换视野幅度。
        """
        left, top, right, bottom = EXPANDED_ROI
        center = np.array([(left + right) / 2, (top + bottom) / 2])
        half = np.asarray(direction, float) * distance / 2
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
            snapshot = self.observe_frame(self.win.client())
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

    def reset_navigation(self):
        """清除异常前的位置与镜头证据，保留章节资源和累计输入计数。

        回正前与重新定位前调用，防止 snapshot 自动沿用冻结小队坐标或战前定位；镜头增益、
        箭头与光幕参考帧也不能跨异常复用。敌人清除、机关和道路记忆由执行层保留。
        """
        self.last_registration = None
        self.localized_report = None
        self.reuse_squad = False
        self.anchor_reference = None
        self.anchor_reference_method = None
        self.barrier_references = []
        self.camera_gain = 1.
        self.last_plan_enemy_ring = False
        self.scout_failure = None
        self.owner.last_snapshot = None

    def recover_center(self, mechanism_position=None, allow_unlocalized=False):
        """独立回正小队并取得全新实测定位；失败时不把旧位置降级为可用。

        回正与重新定位各使用一个单目标输入窗口；前者不发送小队移动。
        默认要求新快照来自实际小队圆环并通过既有配准门限；短拖回正后仍须居中，图标回正信任游戏结果。
        mechanism_position 仅供已确定中央解锁光幕的机关触发后回正，允许一次有界边缘小队镜头短拖；
        普通恢复不提供此参数；缺少画面标记时按既有定向搜索拖动，跳过小队避让但保留端点检查。
        allow_unlocalized 仅供执行层已通过近敌门禁的普通恢复使用：回正后只取单帧、不展开重试，
        可返回未定位快照供纯画面近敌检查，但不能据此恢复地图规划。回正失败与安全异常仍直接传播。
        机关与扫描调用方默认保持严格定位要求，不能降级使用已知或失败坐标。
        """
        from module.campaign.navigation.recovery import centered, centering_frame, recenter_squad

        self.reset_navigation()
        self.begin_target('recovery')
        icon_clicked = recenter_squad(self, mechanism_position=mechanism_position)
        self.reset_navigation()
        self.begin_target('position')
        snapshot = self.snapshot(retry_localization=not allow_unlocalized)
        if not allow_unlocalized and (snapshot.get('localization') != 'accepted' or snapshot.get('squad') is None
                                      or snapshot.get('squad_source') == 'known'):
            raise ValueError('小队回正后的实测定位未通过，不能恢复地图规划。')
        if not icon_clicked and not centered(centering_frame(self)):
            raise ValueError('重新定位后镜头偏离小队，不能恢复地图规划或实景近敌检查。')
        return snapshot

    def scout_begin(self):
        """侦察前确保地图已展开；沿用补视野面板，不为回正额外收起再开。

        小队位置由调用方可信快照提供，侦察用每帧配准定位视口，不要求面板中心就是小队。
        """
        self.check()
        goto.map_open(self.win, reset=False)

    def scout_end(self):
        """侦察结束收回紧凑小地图；调用方保留侦察前小队坐标，并重新配准当前视野。"""
        goto.map_close(self.win)

    def scout_locate(self, tag):
        """只配准展开侦察视口，不检测或约束小队圆环，独立道路配准通过后才采用本帧敌人。

        成功返回 (panel, report)；失败返回 None，原因与面板道路占比存 self.scout_failure，
        由调用方按类型降级（记录失败或前沿标记），不再直接终止侦察与目标选择。
        稳定性检查失败先原地换帧复验（最多 STABLE_RESAMPLES 次），仍不稳才小幅平移重试：
        长拖后的惯性滚动往往换一帧就稳，直接平移会丢掉本来可用的视野。
        面板道路占比低于 FRONTIER_BLUE_MAX 说明视口已出已探索区域，平移重试无益，立即返回。
        截图、窗口、取消和输入异常不捕获；不在异常后补发地图收起动作。
        """
        self.scout_failure = None
        for attempt in range(2):
            if attempt:
                self.win.capture()
                self.pan_expanded((1., 0.), distance=EXPANDED_RETRY_PIXELS)
            panel = self.win.capture()
            blue = float(np.mean(terrain(panel)))
            try:
                report = self.localizer.locate(panel, tag, require_player=False, locate_player=False)
            except (RuntimeError, ValueError) as exc:
                reason = str(exc)
            else:
                for _ in range(STABLE_RESAMPLES + 1):
                    if np.mean(terrain(panel) != terrain(self.win.capture())) <= .03:
                        return panel, report
                    self.pause(STABLE_RESAMPLE_SECONDS)
                reason = '道路画面尚未稳定'
            self.scout_failure = {'reason': reason, 'blue_ratio': blue}
            self.record('scout_localization_failed', field=self.trace.last_field, force=True,
                        tag=tag, attempt=attempt + 1, reason=reason, blue_ratio=round(blue, 1))
            if blue < FRONTIER_BLUE_MAX:
                logger.info(f'侦察定位未通过：{reason}；面板道路占比 {blue:.0f}%，'
                            f'视口已出已探索区域，按探索前沿处理。')
                return None
            if not attempt:
                logger.info(f'侦察定位未通过：{reason}；小幅平移 '
                            f'{EXPANDED_RETRY_PIXELS:.0f} 客户区像素后重试（1/1）。')
        return None

    def scout_drag(self, panel, report, point):
        """把展开地图向 point（底图坐标）拖一次；返回实际发送的面板像素偏移，已到位返回 None。

        地图内容跟随拖动：实测剩余偏移按本帧配准换算，单次拖动不超过 SCOUT_DRAG_MAX，
        闭环调用方在拖动后重新配准。逆投影的齐次分母以当前视口中心归一化；远处目标跨越
        投影地平线时只沿中心到目标的方向分段推进，不直接除以负分母而反向拖动。
        """
        height, width = panel.shape[:2]
        center = np.array([width / 2, height / 2])
        point = np.asarray(point, float)
        if point.shape != (2,) or not np.isfinite(point).all():
            raise ValueError('侦察目标必须是有限的二维地图坐标。')
        matrix = np.asarray(report['roi_to_map'], float)
        origin = project(matrix, center)
        inverse = np.linalg.inv(matrix)
        homogeneous = inverse @ np.r_[point, 1.]
        homogeneous /= (inverse @ np.r_[origin, 1.])[2]
        offset = center * homogeneous[2] - homogeneous[:2]
        length = float(np.linalg.norm(offset))
        if length <= SCOUT_CENTER_TOLERANCE * homogeneous[2]:
            return None
        # 先按最大拖距约束正分母，等价于投影同一直线上的近处中间点，避免跨地平线外推。
        offset /= max(float(homogeneous[2]), length / SCOUT_DRAG_MAX)
        if homogeneous[2] <= 0:
            logger.info(f'侦察目标超出当前投影范围，采用分段拖动：目标 {points_text(point)}。')
        return self.drag_expanded_offset(offset)

    def drag_expanded_offset(self, offset):
        """按面板像素向量发送一次展开地图拖动，返回实际发送的偏移；过短不拖动返回 None。

        停稳等待随拖动长度伸缩：实测 300 像素长拖后地图惯性可超过 2 秒，固定等待不够。
        拖动前检查停止与窗口。
        """
        offset = np.asarray(offset, float)
        length = float(np.linalg.norm(offset))
        if length < 3:
            return None
        # 地图内容跟手移动：从 center - v/2 拖到 center + v/2，落点正好挪到面板中心。
        offset = offset * min(1., SCOUT_DRAG_MAX / length)
        self.check()
        left, top, right, bottom = EXPANDED_ROI
        center = np.array([(right - left) / 2, (bottom - top) / 2])
        base = np.asarray(self.win.client_origin(), float) + [left, top] + center
        start, end = np.rint(base - offset / 2).astype(int), np.rint(base + offset / 2).astype(int)
        self.win.handler.mouse_swipe(tuple(start.tolist()), tuple(end.tolist()))
        self.pause(SCOUT_SETTLE_SECONDS + min(length, SCOUT_DRAG_MAX) / SCOUT_DRAG_MAX)
        return offset

    def scout_view(self, point, tag, check_enemies=False, enemy_id=None):
        """把展开地图拖向 point（底图坐标），沿途可信帧均记录敌人与覆盖；返回是否记录过可信帧。

        拖动是闭环的：每拖一次都重新配准，用实测视野换算剩余偏移，所以拖动有惯性或比例不准都能收敛；
        默认按道路整格覆盖停止；check_enemies 要求安全敌人覆盖，enemy_id 还要求该历史敌人本拍已复核，
        不能因过去看过道路就停止。地图边缘拖不动时最多拖 SCOUT_DRAGS 次，
        落点没进视野由调用方记录失败；配准失败只放弃本屏（见 scout_locate），不丢掉途中已记录的观测。
        拖动经输入代理发送，与移动输入同样先核对窗口、章节与停止。
        """
        located = self.scout_locate(tag)
        if located is None:
            return False
        recorded = False
        for attempt in range(SCOUT_DRAGS + 1):
            self.absorb_view(*located)
            recorded = True
            covered = (self.owner.state.is_enemy_observed(point, enemy_id) if check_enemies
                       else self.owner.state.is_observed(point))
            if covered or attempt == SCOUT_DRAGS:
                return True
            if self.scout_drag(located[0], located[1], point) is None:
                break
            located = self.scout_locate(f'{tag}_{attempt + 1}')
            if located is None:
                return recorded
        return recorded

    def scan_explored(self):
        """开局扫描小队周围：中心一屏加四向各 SCAN_ARM_STOPS 站，返回成功配准的屏数。

        向外拖动用固定面板向量（面板尺寸的 SCAN_ARM_STEP 倍），每臂逐站配准吸收，失败就不再深入。
        拖回以未移动的小队底图坐标为目标，每帧按实测配准计算相对偏移，最多拖 SCOUT_DRAGS 次；
        地图边缘造成斜向位移时因此仍能回到小队附近。向外配准失败使用最后可信视野拖回，
        拖回失败或超过次数只跳过当前方向，其余方向仍按序尝试。最后一个方向结束后不再拖回，
        由调用方直接收起展开地图；纯地图拖动不改变扫描前的小队坐标。安全异常照常传播。
        """
        squad = self.stationary_squad()
        if squad is None:
            raise ValueError('十字扫描缺少可信的小队位置，不能按相对位置拖回。')
        located = self.scout_locate('scan_00')
        if located is None:
            return 0
        panel = located[0]
        step = np.array([panel.shape[1], panel.shape[0]], float) * SCAN_ARM_STEP
        scanned = 0
        directions = ((0., 0.), (1., 0.), (-1., 0.), (0., 1.), (0., -1.))
        for index, direction in enumerate(directions):
            if index == 0:
                self.absorb_view(*located)
                scanned += 1
                continue
            vector = step * np.asarray(direction, float)
            sent = 0
            for stop in range(SCAN_ARM_STOPS):
                if self.drag_expanded_offset(vector) is None:
                    break
                sent += 1
                previous = located
                located = self.scout_locate(f'scan_{index:02}_{stop}')
                if located is None:
                    logger.info('当前扫描方向配准未通过，停止深入并使用最后可信视野拖回小队。')
                    located = previous
                    break
                self.absorb_view(*located)
                scanned += 1
            if not sent or index == len(directions) - 1:
                continue
            returned = False
            for attempt in range(SCOUT_DRAGS + 1):
                if located is not None and self.scout_drag(located[0], located[1], squad) is None:
                    returned = True
                    break
                if located is None or attempt == SCOUT_DRAGS:
                    break
                located = self.scout_locate(f'scan_{index:02}_back_{attempt}')
            if not returned:
                logger.warning('十字扫描未能按相对位置拖回小队附近，跳过当前方向并继续下一方向。')
                continue
        return scanned


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
        error = sys.exc_info()[1]
        try:
            if self.raw_handler is not None:
                self.raw_handler.mouse_up()
        finally:
            self.trace.finish(error=error, inputs=self.inputs, gestures=self.gestures,
                              snapshot=self.owner.last_snapshot, goal=getattr(self.owner, 'last_goal', None))
            self.win.close()
