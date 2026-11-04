"""章节无关的页面输入会话与共享战斗按钮操作，启动时不需要地图资源或小队位置。"""

import sys
import time
from pathlib import Path

import cv2
import numpy as np

from module.base.timer import Timer
from module.campaign.report import BATTLE_STATES, label, points_text
from module.campaign.trace import CampaignTrace
from module.campaign.vision import battle
from module.campaign.vision.red_circle import find_red_circles
from module.campaign.window import CampaignWindow, WindowChanged
from module.config.config import TaskEnd
from module.logger import logger

QUICK_BATTLE_POINT = (1014, 842)
ENTER_BATTLE_POINT = (1014, 916)
ENTRY_CLOSE_POINT = (1147, 513)
DEFEAT_BACK_POINT = (73, 908)
DEFEAT_RETRY_POINT = (1688, 926)
# 横屏红圈的瞄准偏移尚未标定，默认使用检测圆心；不要套用竖屏的压缩与纵向补偿公式。
RED_CIRCLE_OFFSET = (0., 0.)
RED_CIRCLE_HOLD_SECONDS = 1.
# 每个按钮自己的最短点击间隔（秒），与仓库 appear_then_click 的 interval 同义：一次点击没生效时，
# 间隔内的轮询不再重复点。数值照仓库的战斗循环取（进入战斗／快速战斗 2~5 秒、结算 1 秒、自动射击 5 秒）。
ENTRY_TAP_INTERVAL = 2.
SETTLE_TAP_INTERVAL = 1.
AUTO_TAP_INTERVAL = 5.
# 自动射击与爆发各自连续这么多帧可靠关闭才点，同一场每个开关最多点一次。实机里 AUTO 按钮
# 叠在场景上、HUD 会淡出或整体挪动，单帧判据偶尔会把开着的读成关着；照单帧就点，那一下正好
# 把自动射击关掉（开与关是同一个按钮），战斗会被拖长甚至打输。
AUTO_CONFIRM_FRAMES = 2
STUCK_CLEAR_SECONDS = 300


class CampaignInput:
    """生产驱动代理：每次新手势先校验安全条件，成功发送后统计，释放操作不受停止限制。

    场景手势与可能移动小队的输入序号在此统计；地图控件经独立成功回调统计实际操作，
    不冒充场景手势。驱动失败、编码后取消或失焦均不能增加成功操作次数。
    """

    def __init__(self, session, handler):
        """只保存代理关系，不取得新驱动；驱动仍由任务调度器管理。"""
        self.session, self.handler = session, handler

    def __getattr__(self, name):
        """仅包装产生输入的方法；mouse_up 直通，保证取消后仍能释放已按下鼠标。"""
        value = getattr(self.handler, name)
        if name not in ('mouse_click', 'mouse_swipe', 'mouse_down', 'mouse_move', 'press_mouse_click'):
            return value

        def send(*args, **kwargs):
            """发送前校验窗口与弹窗，成功后统计；驱动失败立即停止，不算作成功输入。"""
            self.session.before_input()
            action = dict(method=name, args=args, kwargs=kwargs, coordinate_space='screen',
                          client_origin=self.session.win.origin)
            self.session.record('input_before', field=self.session.trace.last_field, force=True, action=action)
            # 编码与写盘期间也可能失焦或收到取消；发给驱动前再次校验。
            self.session.check()
            result = value(*args, **kwargs)
            if getattr(self.handler, '_failures', 0):
                raise WindowChanged(f'鼠标操作 {name} 发送失败。')
            self.session.count_input(scene=True, moves_squad=name != 'mouse_swipe')
            self.session.trace.after_input(action=action, inputs=self.session.inputs,
                                           target_inputs=self.session.target_inputs,
                                           gestures=self.session.gestures)
            return result

        return send


class CampaignBattleActions:
    """复用横屏战斗按钮与防抖，剧情点击统一委托任务上层场景控制器。"""

    def battle_step(self, close_entry=False):
        """战斗接管的一步：截一帧，按优先级最多点一个按钮，返回识别结果交给执行层决策。

        优先级与点法照仓库里已经跑熟的战斗循环（module/daemon/semi_combat.py、module/event/challenge.py）：
        准备弹窗（关掉／快速战斗／进入战斗）、结算及升级附加页（点继续）、剧情（点 SKIP）、
        战斗中（优先处理红圈，没有红圈且 AUTO 关着才点 AUTO）；
        每一拍只点一个按钮，认不出画面就什么都不点——加载与过场本来就没有按钮，硬猜只会点错。
        每个按钮有自己的最短点击间隔（ENTRY_TAP_INTERVAL / SETTLE_TAP_INTERVAL / AUTO_TAP_INTERVAL），
        一次点击没生效时后续几拍不再重复点同一个按钮。
        战败不在这里点：重打还是返回要按重试次数决定，属于执行层的策略（见 CampaignExecutor.battle）。
        close_entry 用于战败返回之后：再看到准备弹窗只能关掉，不能又进一次战斗。
        胜利与升级附加页均只点击本帧已通过门限的 END_FIGHTING 提示；标题先出现而提示
        尚未可见时只返回胜利状态，不发送画面中央点击。
        战前、战后剧情只点击本帧通过门限的 SKIP；沿用 1 秒点击间隔与输入安全闸门。
        导航会话在准备弹窗与野外／未知帧先检查上层弹窗接管，灰色锁定按钮不得按可进入弹窗输入。
        每次点击的日志都带上当次识别分数与颜色判据的实际读数，便于对着现场截图复盘误判。
        """
        self.check()
        field = self.win.client()
        state = battle.battle_state(field)
        self.record('battle_state', state=state)
        # 继续与跳过在输入前复核分数，其余判据由状态识别提供；日志缺项显示为「—」。
        kind, scores = state['state'], state.get('scores')
        if kind in ('unknown', 'field', 'entry') and hasattr(self, 'reset_navigation'):
            self.owner.scene_controller.raise_if_needed(field, known_only=True)
        quick = battle.value_text(state.get('quick_ratio'))
        if kind != 'battle':
            self.auto_off_frames = {key: 0 for key in battle.AUTO_CONTROLS}
            self.auto_last_points.clear()
        if kind not in ('battle', 'unknown', 'story'):
            # 一场战斗从准备弹窗开始；战败重打的下一场没有弹窗，所以结算与回野外也要重置额度。
            # 剧情或加载不能证明新一场战斗开始，不能恢复 AUTO 切换额度。
            self.auto_taps = {key: 0 for key in battle.AUTO_CONTROLS}
            self.auto_skip_logged.clear()
        if kind == 'entry':
            if battle.ex_stage(field):
                self.popup(field)
                return state
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
            if self.handle_red_circles(field):
                self.auto_off_frames = {key: 0 for key in battle.AUTO_CONTROLS}
                self.auto_last_points.clear()
            else:
                self.handle_auto(state)
        elif kind == 'story':
            controller = self.owner.scene_controller
            controller.handle_story(field)
            if hasattr(self, 'reset_navigation'):
                self.reset_navigation()
        elif (kind in ('victory', 'continue') and state.get('continue_point') is not None
              and (scores or {}).get('continue', 0.) >= battle.CONTINUE_THRESHOLD):
            point = tuple(state['continue_point'])
            if self.tap(point, SETTLE_TAP_INTERVAL):
                logger.info(f"战斗结算：{label(BATTLE_STATES, kind)}，已点击 END_FIGHTING 提示"
                            f"（命中度 {battle.score_text(scores, 'continue')}，客户区落点 {points_text(point)}）。")
        return state

    def handle_auto(self, state):
        """分别开启自动射击与爆发，只点击连续两帧可靠关闭且落点稳定的当前按钮中心。

        沿用半自动战斗的关闭模板与 5 秒防抖，同一场每个开关最多点击一次、每帧最多一个。
        未定位、颜色未知、已开启或按钮中心变化超过 15 客户区像素都会中断连续关闭计数。
        未知不等于关闭，不能以默认 AUTO 坐标点击；窗口与停止仍由 tap 校验。
        """
        controls = state.get('auto_controls') or {}
        for key in battle.AUTO_CONTROLS:
            control = controls.get(key) or {}
            point = control.get('point')
            if control.get('status') != 'off' or point is None:
                self.auto_off_frames[key] = 0
                self.auto_last_points.pop(key, None)
                continue
            previous = self.auto_last_points.get(key)
            if previous is None or np.linalg.norm(np.asarray(point) - previous) > 15:
                self.auto_off_frames[key] = 0
            self.auto_off_frames[key] += 1
            self.auto_last_points[key] = point
        for key in battle.AUTO_CONTROLS:
            count = self.auto_off_frames[key]
            if not count:
                continue
            control = controls[key]
            name = '自动射击' if key == 'shoot' else '自动爆发'
            if self.auto_taps[key]:
                if key not in self.auto_skip_logged:
                    self.auto_skip_logged.add(key)
                    logger.warning(f'{name}仍为关闭状态；已达到本场单次点击上限，不重复切换。')
                continue
            if count < AUTO_CONFIRM_FRAMES:
                continue
            point = tuple(control['point'])
            if not self.tap(point, AUTO_TAP_INTERVAL):
                continue
            self.auto_taps[key] += 1
            logger.info(f'{name}关闭状态已确认（连续 {count} 帧，模板 {control["score"]:.3f}，'
                        f'图标橙色占比 {control["orange_ratio"]:.3f}），已点击开启，客户区落点 {point}。')
            return

    def tap(self, point, interval=0.):
        """按客户区坐标点一次界面按钮，保留输入安全闸门，但不执行地面移动的光幕检查。

        interval 是这一点自己的最短点击间隔（秒），与仓库 appear_then_click 的 interval 同义：
        间隔内的重复点击直接丢掉并返回 False，真的点了返回 True。战斗接管的按钮都靠它防连点——
        点击没生效时（面板动画中、被过场吃掉）下一轮只是再点一次，而不是每拍补一下。
        """
        key = tuple(point)
        now = time.monotonic()
        if interval and now - self.tap_times.get(key, 0.) < interval:
            return False
        self.click_client(point)
        self.tap_times[key] = now
        return True

    def hold_client(self, point, duration):
        """在有效客户区坐标按住鼠标指定秒数，窗口、弹窗、停止和留档均走 CampaignInput。"""
        point = np.asarray(point, float)
        if point.shape != (2,) or not np.isfinite(point).all() or not (0 <= point[0] < 1776 and 0 <= point[1] < 999):
            raise ValueError('长按位置越界或不是有效坐标。')
        self.check()
        origin = self.win.client_origin()
        self.win.handler.press_mouse_click(origin[0] + round(point[0]), origin[1] + round(point[1]), duration)

    def handle_red_circles(self, field):
        """检测战斗中的红黄同心圈，并按横屏偏移长按瞄准；未启用或未检出时返回 False。

        field 为当前战斗客户区 BGR 帧，检测器复用其他战斗任务的 RGB 实现。偏移与按住时长只使用
        主线横屏参数；每帧最多处理一个圆，由战斗轮询和长按时间共同限制频率。
        """
        if not self.owner.config.Optimization_AutoRedCircle:
            return False
        circles = find_red_circles(cv2.cvtColor(field, cv2.COLOR_BGR2RGB))
        if not circles:
            return False
        center = np.asarray(circles[0], float)
        point = center + RED_CIRCLE_OFFSET
        self.hold_client(point, RED_CIRCLE_HOLD_SECONDS)
        logger.info(f"战斗红圈：检测圆心 {points_text(center)}，长按落点 {points_text(point)}，"
                    f"偏移 {points_text(RED_CIRCLE_OFFSET)}，持续 {RED_CIRCLE_HOLD_SECONDS:.1f} 秒。")
        return True

class CampaignSceneSession(CampaignBattleActions):
    """仅绑定客户区与输入驱动的场景会话，不加载章节配准器或旧小队坐标。

    页面切换与启动接管均使用独立留档；实际输入经过同一窗口、取消与驱动失败检查。
    与章节会话共享驱动但不共享输入计数、投影矩阵和定位缓存，退出只释放按键而不关闭设备。
    """

    def __init__(self, owner):
        """创建章节无关窗口与证据记录；start 时才校验驱动、窗口和前台。"""
        self.owner = owner
        self.expected_identity = None
        self.debug_mode = getattr(owner.config, 'CampaignSettings_DebugMode', False)
        self.trace = CampaignTrace(Path(__file__).resolve().parents[2] / 'log/campaign/scenes/runs', dict(
            instance=getattr(owner.config, 'config_name', None), task=owner.config.task.command,
            client_size=[1776, 999], purpose='scene'))
        self.win = CampaignWindow(owner)
        self.win.trace = self.trace
        self.win.stop_check = self.check_stop
        self.raw_handler = self.win.handler
        self.win.handler = CampaignInput(self, self.raw_handler) if self.raw_handler is not None else None
        self.inputs = self.target_inputs = self.gestures = self.squad_inputs = 0
        self.tap_times = {}
        self.popup_handling = False
        self.reuse_squad = False
        self.auto_taps = {key: 0 for key in battle.AUTO_CONTROLS}
        self.auto_off_frames = {key: 0 for key in battle.AUTO_CONTROLS}
        self.auto_skip_logged = set()
        self.auto_last_points = {}

    def start(self):
        """校验已有驱动与受控窗口，不要求章节角标、地图或小队可见。"""
        self.check_stop()
        if self.debug_mode:
            self.trace.start()
        if self.win.handler is None or not self.win.focus():
            raise WindowChanged('场景接管需要有效游戏窗口及已连接的鼠标操作通道。')
        self.check()

    def check_stop(self):
        """在页面等待、抢回前台与每次输入前响应用户停止，不发送清理页面点击。"""
        event = self.owner.config.stop_event
        if event is not None and event.is_set():
            raise TaskEnd

    def check(self):
        """保留窗口几何与取消检查，仅给页面状态机自身的有界等待续期设备计时器。"""
        self.check_stop()
        stuck = getattr(self.owner.device, 'stuck_timer', None)
        if isinstance(stuck, Timer) and stuck.current() > STUCK_CLEAR_SECONDS:
            self.owner.device.stuck_record_clear()
        self.win.check()

    def before_input(self):
        """场景按钮输入前检查窗口与停止；页面是否可点击由当前帧模板或战斗判据决定。"""
        self.check()

    def count_input(self, scene=False, moves_squad=False):
        """仅在驱动成功发送后累计操作，页面按钮不建立或沿用小队定位证据。"""
        self.inputs += 1
        self.target_inputs += 1
        self.gestures += int(scene)
        self.squad_inputs += int(moves_squad)

    def click_client(self, point):
        """将有限客户区点换算成屏幕坐标发送，越界或非有限位置直接拒绝。"""
        point = np.asarray(point, float)
        if point.shape != (2,) or not np.isfinite(point).all() or not (0 <= point[0] < 1776 and 0 <= point[1] < 999):
            raise ValueError('场景按钮位置越界或不是有效客户区坐标。')
        self.check()
        origin = self.win.client_origin()
        self.win.handler.mouse_click(origin[0] + round(point[0]), origin[1] + round(point[1]))

    def pause(self, seconds):
        """按短间隔检查窗口与停止，场景等待不依赖章节导航的身份闸门。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def record(self, event, field=None, force=False, **details):
        """将已有页面帧与实际输入数写入独立场景留档，不访问章节状态或额外截图。"""
        self.trace.record(event, field=field, force=force, inputs=self.inputs,
                          target_inputs=self.target_inputs, gestures=self.gestures, **details)

    def popup(self, field):
        """启动时没有敌人归属，只关闭可靠识别的 EX 准备弹窗，不进入或猜测其地图位置。"""
        if battle.ex_stage(field):
            self.tap(ENTRY_CLOSE_POINT, SETTLE_TAP_INTERVAL)

    def close(self):
        """保留最终场景证据并释放按键；取消或异常后也不发送返回主页等页面输入。"""
        error = sys.exc_info()[1]
        try:
            if self.raw_handler is not None:
                self.raw_handler.mouse_up()
        finally:
            self.trace.finish(error=error, inputs=self.inputs, gestures=self.gestures)
            self.win.close()
