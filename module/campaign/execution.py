"""自动推图执行层：逐拍决策、有限移动、战斗接管与子目标回写。"""

import time

import numpy as np

from module.campaign.run_state import MAX_FAILURES, RunState, located, panel_quad
from module.campaign.goals import describe
from module.campaign.report import (BATTLE_OUTCOMES, BATTLE_STATES, EVIDENCE_KINDS, GOAL_KINDS, GOAL_REASONS,
                                    RESULT_STATES, TARGET_SOURCES, label, points_text)
from module.campaign.scouting import scout_focus
from module.campaign.session import (DEFEAT_BACK_POINT, DEFEAT_RETRY_POINT, SETTLE_TAP_INTERVAL,
                                     InputBudgetExceeded)
from module.campaign.timing import Stopwatch
from module.campaign.vision.battle import score_text, value_text
from module.campaign.window import WindowChanged
from module.base.timer import Timer
from module.campaign.navigation.movement import navigate
from module.campaign.navigation.movement_feedback import ClickUnconfirmed, TargetTriggered
from module.campaign.navigation.story_navigation import locate_story_target
from module.logger import logger

# 每次调度只执行有限拍数，避免无进展的状态机长期占住输入通道。
CAMPAIGN_TICK_BUDGET = 48
TARGET_MOVES = 12
# 复核站位上最多连看的紧凑帧数：箭头锚点、配准或检测偶发失手会让单帧不可信，换一帧往往就能看清；
# 连看都不可信才算这次复核失败。
REVIEW_FRAMES = 3
# 一次侦察最多看几屏：每屏最多 3 次拖动，连同展开、收起不超过单目标 32 次输入。
SCOUT_VIEWS = 8
# 战斗接管的节奏与上限，照仓库里已经跑熟的战斗循环取（module/daemon/semi_combat.py、
# module/event/challenge.py）：循环本身跑得快，每个按钮各自有最短点击间隔（间隔在会话里，
# 见 session.ENTRY_TAP_INTERVAL / SETTLE_TAP_INTERVAL / AUTO_TAP_INTERVAL）。
BATTLE_POLL_SECONDS = 0.5
# 一段战斗（进弹窗、重打后重新开打）等的上限：等不到结算就交人工，不无限等。
BATTLE_TIME_LIMIT = 300
BATTLE_DEFEAT_RETRIES = 3
# 战后野外确认窗口：已识别胜负、连续多帧回到野外且至少经过 BATTLE_SETTLE_CONFIRM 秒才结束。
# 升级弹窗、加载或未识别画面不计入野外确认，Timer 的 count 语义仍为 count + 1 帧。
BATTLE_SETTLE_CONFIRM = 1.0
BATTLE_SETTLE_FRAMES = 3
# 一段战斗超过这么多秒还没有结算就报一次进度：说明一直在等游戏自己推进（战斗里没有该点的按钮）。
BATTLE_IDLE_REPORT = 60


class BattleFailed(RuntimeError):
    """同一关原地重打用尽仍然战败：按安全失败停止并交人工，不降级成普通目标失败继续点。"""


def single_transfer_annotations(leg):
    """把一段电梯包装为共享闭环的有向标注，不让导航自行换用未筛选的连接。"""
    return {'objects': [dict(id='entry', category='ground_elevator', type='point', points=[leg['entry']]),
                        dict(id='exit', category='ground_elevator', type='point', points=[leg['exit']])],
            'connections': [dict(id=leg['connection'], category='elevator_connection', directed=True,
                                 **{'from': 'entry', 'to': 'exit'})]}


def goal_brief(goal):
    """目标的一行短描述，例如「普通敌人 enemy-1」；没有记录时写「没有记录」。"""
    if not goal or goal.get('kind') == 'none':
        return '没有记录'
    return f"{label(GOAL_KINDS, goal['kind'])} {goal.get('id')}"


def describe_result(result):
    """把移动闭环的返回值写成一句中文：终态、闭环给的原话、移动次数与最后离目标多远。"""
    parts = [label(RESULT_STATES, result.get('state'))]
    if result.get('message'):
        parts.append(str(result['message']))
    if result.get('movement_clicks') is not None:
        parts.append(f"移动点击 {result['movement_clicks']} 次")
    if result.get('distance') is not None:
        parts.append(f"最终目标距离 {float(result['distance']):.1f} 像素")
    return '，'.join(parts)


def describe_evidence(evidence):
    """把任务中断时附带的证据写成一句中文：是什么画面，加上能说清的补充信息。"""
    if not evidence:
        return '没有附加证据'
    detail = label(EVIDENCE_KINDS, evidence.get('kind'))
    if evidence.get('chapter'):
        detail += f"（第 {evidence['chapter']} 章）"
    if evidence.get('position') is not None:
        detail += f"，位置 {points_text(evidence['position'])}"
    if evidence.get('confidence') is not None:
        detail += f"，识别置信度 {float(evidence['confidence']):.2f}"
    return detail


class CampaignExecutor:
    """连接纯决策与设备会话；任何身份、焦点或预算异常都不转成普通目标失败。"""

    def __init__(self, owner, session, purpose='story'):
        """注入会话以便离线回放；业务状态仍由 Campaign 统一持有。"""
        self.owner, self.session, self.purpose = owner, session, purpose
        # 最近一次真正派出去执行的目标：弹窗在非移动阶段（补视野、观测）冒出来时，用它认领这次战斗，
        # 否则打死的敌人会留在 pending，下一拍白走一趟。
        self.last_executed = None
        self.scout_pending = None

    def needs_expansion(self, goal, snapshot):
        """没有目标或前沿探索时展开；正常目标的道路经过未观测格时也补一次视野。"""
        if goal and goal['kind'] == 'none' and goal.get('reason', '').startswith('route_'):
            return False
        if goal is None or goal['kind'] in ('none', 'explore'):
            return True
        try:
            _, path = self.session.localizer.route(np.asarray(snapshot['squad']), np.asarray(goal['position']))
        except RuntimeError:
            return True
        return any(not self.owner.state.is_observed(point) for point in path)

    def review_block(self, goal):
        """到安全站位后只用新紧凑帧验证光幕；看不清不当成解封，也不消耗机关结果。

        单帧不可信时原地换帧重看，最多 REVIEW_FRAMES 帧，帧间等待照常检查停止与窗口；
        全部不可信才记一次复核失败并保留待复核，下一拍由目标选择决定是否交下一步。
        复核要连看几帧，每帧都做一次完整观测与决策；结论行带上用掉第几帧与总耗时。
        """
        mechanism = self.owner.state.pending_reviews.get(goal['id'])
        state = self.owner.state
        watch = Stopwatch()
        standoff = goal.get('position')
        logger.info(f"开始复核光幕 {goal['id']}（机关：{mechanism or '无记录'}）：站位 "
                    f"{points_text(standoff) if standoff is not None else '未指定'}，"
                    f"最多 {REVIEW_FRAMES} 帧确认光幕是否仍然存在。")
        for attempt in range(REVIEW_FRAMES):
            if attempt:
                self.session.pause(1)
            snapshot = self.session.snapshot()
            self.owner.plan_tick(snapshot, advance=False)
            record = state.blocks.get(goal['id'])
            quad = panel_quad(snapshot)
            if (snapshot.get('localization') == 'accepted' and snapshot.get('barriers_observed', False)
                    and record is not None and RunState._inside(quad, record['point'])):
                break
        else:
            state.penalize(f"review-{goal['id']}")
            logger.warning(f"光幕 {goal['id']} 连续 {REVIEW_FRAMES} 帧未取得有效观测，保留待复核状态；"
                           f"耗时 {watch}。")
            return
        state.pending_reviews.pop(goal['id'], None)
        if record['status'] == 'cleared':
            logger.info(f"光幕 {goal['id']} 已消失（第 {attempt + 1} 帧确认），"
                        f"关联机关 {mechanism or '无记录'}；可通行路网已更新，耗时 {watch}。")
            self.owner.plan_tick(self.session.snapshot(expanded=True), advance=False)
        else:
            logger.info(f"光幕 {goal['id']} 仍然存在（第 {attempt + 1} 帧确认），"
                        f"关联机关 {mechanism or '无记录'} 未解除该光幕；耗时 {watch}。")
            state.penalize(f"review-{goal['id']}", failure=False)

    def execute(self, goal):
        """执行一个目标并回写结果；普通移动失败进入冷却，安全失败直接向外传播。

        派发与结束各一行，带上落点、预计距离、移动次数、本目标消耗的输入次数与耗时：
        同一个目标在共享闭环里会打十几行进度，这两行是把它们括起来的边界。
        """
        kind = goal['kind']
        purpose = kind if kind in ('enemy', 'collectible') else 'position'
        self.session.begin_target(purpose)
        target = goal['position']
        kwargs = dict(max_moves=TARGET_MOVES, purpose=purpose)
        if kind == 'elevator':
            target = goal['leg']['exit']
            kwargs['elevators'] = single_transfer_annotations(goal['leg'])
        confirmed_transfers = []

        def progress(**values):
            """保留共享闭环实际确认的出口；到达 exit 坐标本身不能证明执行过电梯。"""
            if values.get('elevator_transfers'):
                confirmed_transfers[:] = values['elevator_transfers']
            self.session.emit(**values)

        watch = Stopwatch()
        logger.info(f"开始执行目标 {goal_brief(goal)}：目标位置 {points_text(target)}，"
                    f"预计道路距离 {goal.get('distance')} 像素，移动点击上限 {TARGET_MOVES} 次"
                    f"（依据：{label(GOAL_REASONS, goal.get('reason'))}）")
        try:
            result = navigate(self.session, target, progress, **kwargs)
        except TargetTriggered:
            raise
        except (WindowChanged, InputBudgetExceeded, ClickUnconfirmed):
            raise
        except (RuntimeError, ValueError) as exc:
            if kind == 'elevator':
                self.owner.state.transfer_failed(goal['leg'])
            else:
                identifier = f"review-{goal['id']}" if kind == 'review_block' else goal['id']
                if kind == 'advance':
                    identifier = goal['for_target']
                self.owner.state.penalize(identifier)
            logger.warning(f"{goal_brief(goal)} 执行失败，已记一次失败并冷却：{exc}；"
                           f"耗时 {watch}，本目标操作 {self.session.target_inputs} 次")
            self.session.preview(self.session.win.client())
            return
        if result.get('state') != 'arrived':
            self.owner.state.penalize(goal['for_target'] if kind == 'advance' else goal['id'])
            logger.warning(f"{goal_brief(goal)} 未确认触发：{describe_result(result)}；"
                           f"耗时 {watch}，本目标操作 {self.session.target_inputs} 次")
            return
        if kind == 'mechanism':
            self.owner.state.mark_tried(goal['id'], goal.get('block'))
            logger.info(f"已到达地面机关 {goal['id']}（坐标来自{label(TARGET_SOURCES, goal.get('source'))}），"
                        '按开启状态记录，本次任务不再重复选择。')
            if goal.get('block'):
                logger.info(f"机关 {goal['id']} 对应光幕 {goal['block']} 待复核。")
        elif kind == 'review_block':
            self.review_block(goal)
        elif kind == 'elevator':
            key = self.owner.state.transfer_key(goal['leg'])
            if not any(self.owner.state.transfer_key(leg) == key for leg in confirmed_transfers):
                logger.info(f"电梯 {goal['id']} 未确认传送成功，仅确认入口到达，重新选择目标。")
                return
            self.owner.state.transfer_succeeded(goal['leg'])
            self.session.anchor_reference = None
            self.session.anchor_reference_method = None
            logger.info(f"电梯 {goal['id']} 已确认传送成功，重新选择目标。")
        elif kind == 'explore':
            # 到点不是观察成功：只记短冷却，新快照自行更新观测覆盖。
            self.owner.state.penalize(goal['id'])
        logger.info(f"{goal_brief(goal)} 结束：{describe_result(result)}，"
                    f"本目标操作 {self.session.target_inputs} 次，耗时 {watch}")

    def scout(self, goal, snapshot):
        """小队原地侦察候选路径与可能更近的支路；返回是否展开过，未完成落点留在 scout_pending。

        只用于剧情任务（收集品有标注，不靠看）。刚踩过机关、要先复核光幕时不侦察，复核结果会改变路网。
        单轮屏数用尽后保留待查区域，下一轮继续；冷却格不能当作已确认，失败到上限交人工。
        侦察中遇到安全异常直接向外传播，不补发收起地图的输入。
        """
        self.scout_pending = None
        if self.purpose != 'story' or (goal is not None and goal['kind'] == 'review_block'):
            return False
        if goal and goal['kind'] == 'none' and goal.get('reason', '').startswith('route_'):
            return False
        if not located(snapshot) or snapshot.get('squad') is None:
            return False
        state, squad = self.owner.state, snapshot['squad']
        focus = scout_focus(state, self.owner.cut, squad, goal=goal)
        if focus is None:
            self.scout_pending = scout_focus(state, self.owner.cut, squad, include_cooling=True, goal=goal)
            return False
        self.session.begin_target('scout')
        self.session.scout_begin()
        views = 0
        total = Stopwatch()
        for view in range(SCOUT_VIEWS):
            known = '无可达候选' if focus['known'] is None else f"{focus['known']} 像素"
            source = {'target_route': '候选目标的前置路段', 'route_road': '路程前方道路与相邻岔口',
                      'route_branch': '路程汇合岔路（距离为直线参考）',
                      'nearer_road': '可能更近的支路'}[focus['source']]
            logger.info(f"侦察第 {view + 1} 屏：目标 {focus['id']}，位置 {points_text(focus['position'])}，"
                        f"{source}，路程 {focus['distance']} 像素，本段检查范围 {known}")
            watch = Stopwatch()
            recorded = self.session.scout_view(focus['position'], f'scout_{view + 1:02}')
            views += 1
            covered = state.is_observed(focus['position'])
            logger.info(f"侦察第 {view + 1} 屏结果：{'已记录可信帧' if recorded else '定位未通过'}，"
                        f"目标格{'已覆盖' if covered else '尚未完整覆盖'}，耗时 {watch}")
            if not recorded or not covered:
                state.penalize(focus['id'])
            focus = scout_focus(state, self.owner.cut, squad, goal=goal)
            if focus is None:
                break
        else:
            logger.info(f'侦察达到单轮 {SCOUT_VIEWS} 屏上限，剩余区域延后至下一轮检查，暂不执行移动。')
        self.scout_pending = scout_focus(state, self.owner.cut, squad, include_cooling=True, goal=goal)
        self.session.scout_end()
        logger.info(f'侦察结束：检查 {views} 屏，展开与拖动总耗时 {total}')
        return True

    def battle(self, goal):
        """按「按钮循环」接管一场战斗：准备弹窗→（快速）战斗→结算被点掉→回到野外。

        循环照仓库里已经跑熟的战斗循环写（`module/daemon/semi_combat.py`、`module/event/challenge.py`）：
        每一拍由会话截一帧、按优先级最多点一个按钮，认不出画面就什么都不点——加载与过场本来就没有
        按钮可点，而「画面像不像野外」正好会在点「进入战斗」之后的过场里为真，用它判断战斗结束
        等于把战斗丢回给推图流程。所以必须先取得胜负结算，再连续确认野外才交回控制权：
        胜利与升级附加页分别点继续，战败按 BATTLE_DEFEAT_RETRIES 决定重打还是返回。
        战前、战后剧情由会话点击 SKIP；剧情、附加页或未识别画面清空野外确认计数。
        剧情跳过与继续提示均不作为胜利证据，剧情期间保留已确认的胜负及原有超时约束。
        一段战斗（进弹窗、重打后重新开打）单独计 BATTLE_TIME_LIMIT 秒，等不到结算就交人工：
        不无限等，也不盲目连点。战斗结束时给会话打开一次「沿用战斗前坐标」的许可，
        第一帧重新配准失败就用旧坐标继续推图。
        """
        self.session.begin_target('battle')
        self.session.battle_mode = True
        settled, close_entry, retries, polls = None, False, 0, 0
        previous = None
        total, segment = Stopwatch(), Stopwatch()
        deadline = time.monotonic() + BATTLE_TIME_LIMIT
        settle_gone = Timer(BATTLE_SETTLE_CONFIRM, count=BATTLE_SETTLE_FRAMES)
        reported = False
        label_goal = '无关联目标' if goal is None else goal_brief(goal)
        logger.info(f'开始战斗接管：目标 {label_goal}，采样间隔 {BATTLE_POLL_SECONDS} 秒，'
                    f'单段超时 {BATTLE_TIME_LIMIT} 秒，重试上限 {BATTLE_DEFEAT_RETRIES} 次。')
        try:
            while 1:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'当前战斗阶段超过 {BATTLE_TIME_LIMIT} 秒未结算，需要人工接管。')
                state = self.session.battle_step(close_entry=close_entry)
                # scores 只用于日志：识别状态本身在 state 里，缺分数时按「—」显示而不是编成 0.00。
                kind, scores = state['state'], state.get('scores')
                polls += 1
                if kind != previous:
                    # 只在画面切换时打分数：同一状态每 0.5 秒轮询一次，逐次打印会把日志刷满。
                    logger.info(f"战斗画面：{label(BATTLE_STATES, kind)}（第 {polls} 次采样，"
                                f"当前阶段耗时 {segment}，接管累计 {total}）；"
                                f"画面匹配度：准备弹窗 {score_text(scores, 'entry')}、"
                                f"胜利结算 {score_text(scores, 'victory')}、"
                                f"战败结算 {score_text(scores, 'defeat')}、"
                                f"继续提示 {score_text(scores, 'continue')}、"
                                f"剧情 SKIP {score_text(scores, 'story_skip')}、"
                                f"AUTO 按钮 {score_text(scores, 'auto_tpl')}、"
                                f"暂停键 {score_text(scores, 'pause')}；"
                                f"AUTO 橙色占比 {value_text(state.get('auto_ratio'))}")
                if kind != 'field':
                    settle_gone.reset()
                elif settled is not None and settle_gone.reached():
                    break
                if kind == 'victory':
                    if settled != 'victory':
                        settled = 'victory'
                        logger.info('战斗结果：胜利，等待结算关闭及野外画面确认。')
                elif kind == 'defeat':
                    # 只在「新的一次战败」上决定重打还是返回：同一张结算屏停留时沿用上一次的决定，
                    # 不会把额度连刷掉，点击没生效时也只是继续点同一个按钮。
                    if previous != 'defeat':
                        if retries < BATTLE_DEFEAT_RETRIES:
                            retries += 1
                            settled = None
                            logger.warning(f'战斗失败：重试当前关卡（第 {retries}/{BATTLE_DEFEAT_RETRIES} 次），'
                                           f'当前阶段耗时 {segment}，接管累计 {total}。')
                        else:
                            settled, close_entry = 'defeat', True
                            logger.warning('战斗失败且重试次数耗尽，准备返回野外并请求人工接管。')
                        deadline, segment, reported = time.monotonic() + BATTLE_TIME_LIMIT, Stopwatch(), False
                    self.session.tap(DEFEAT_BACK_POINT if settled == 'defeat' else DEFEAT_RETRY_POINT,
                                     SETTLE_TAP_INTERVAL)
                elif kind in ('entry', 'battle'):
                    # 每段战斗单独计时：准备弹窗、以及加载结束真正进入战斗时都重新计时。
                    if kind == 'entry' or previous != 'battle':
                        deadline, segment, reported = time.monotonic() + BATTLE_TIME_LIMIT, Stopwatch(), False
                if settled is None and not reported and segment.seconds >= BATTLE_IDLE_REPORT:
                    reported = True
                    logger.info(f'等待战斗结算：当前画面 {label(BATTLE_STATES, kind)}，阶段耗时 {segment}。')
                previous = kind
                self.session.pause(BATTLE_POLL_SECONDS)
        finally:
            self.session.battle_mode = False
        self.session.reuse_squad = True
        logger.info(f'战斗接管结束：{label(BATTLE_OUTCOMES, settled)}，采样 {polls} 次，'
                    f'重试 {retries} 次，累计操作 {self.session.target_inputs} 次，总耗时 {total}。')
        if settled == 'defeat':
            raise BattleFailed(f'当前关卡重试 {BATTLE_DEFEAT_RETRIES} 次仍战败，停止执行并请求人工接管。')
        if goal is not None and goal.get('kind') == 'enemy':
            self.owner.state.enemies[goal['id']]['status'] = 'cleared'
            logger.info(f'战斗胜利：{goal_brief(goal)} 已标记为清除，移出候选集合。')
        else:
            logger.warning(f'战斗胜利但无关联敌人目标（最近执行目标：{goal_brief(goal)}），不更新敌人状态。')
        return settled

    def run(self, initial, ticks=CAMPAIGN_TICK_BUDGET):
        """会话开始时核对一次主线身份，之后每轮查弹窗、补视野再决策；遇战斗弹窗接管战斗后继续推图。

        战斗接管放在一轮内：胜负结算回到野外后继续下一轮，而不是终结任务。非战斗的终态（收集品提示、
        EX 弹窗）仍然直接停止；战斗接管自身的上限与连续战败会抛出安全异常，同样交人工。
        每轮首尾各一行（轮号、已运行时长、累计操作与耗时）：整条推图日志因此能按轮切分，
        把等待时间落到具体的轮上；轮内的观测、侦察、移动、战斗各自另有带耗时的行。
        """
        goal, snapshot = None, initial
        total = Stopwatch()
        try:
            self.session.start()
            self.session.record('initial_snapshot', snapshot=initial)
            if initial.get('localization') == 'accepted' and initial.get('squad') is not None:
                # 准备阶段与首轮之间没有移动，小队坐标可直接约束第一次展开配准。
                self.session.remember_registration(initial['squad'], initial.get('roi_to_map'),
                                                   initial.get('coverage_size'))
                self.session.remember_navigation(initial)
            for index in range(ticks):
                watch = Stopwatch()
                logger.info(f'第 {index + 1}/{ticks} 轮开始：任务已运行 {total}，'
                            f'累计操作 {self.session.inputs} 次')
                # 只有移动（execute）里撞上的弹窗才确定属于本轮目标；补视野或观测时冒出来的弹窗，
                # 归给最近一次真正派出去的目标（见 last_executed），而不是把旧目标随手标成已处理。
                try:
                    self.session.begin_target('collectible' if self.purpose == 'collectible' else 'position')
                    self.session.check()
                    if index:
                        snapshot = self.session.snapshot()
                    else:
                        self.session.identity(self.session.win.client())
                    goal = self.owner.plan_tick(snapshot)
                    if (self.purpose == 'story' and self.owner.state.route_goal is None
                            and snapshot.get('story_markers') and located(snapshot)
                            and snapshot.get('squad') is not None
                            and (goal is None or goal['kind'] != 'review_block')
                            and not self.owner.state.cooling('story-location')):
                        changed = locate_story_target(self.session, snapshot['squad'])
                        self.session.record('story_location_result', camera_changed=changed,
                                            route_goal=self.owner.state.route_goal)
                        if changed:
                            snapshot = self.session.snapshot()
                        goal = self.owner.plan_tick(snapshot, advance=False)
                    expanded_for_scout = False
                    if self.needs_expansion(goal, snapshot):
                        logger.info(f'当前视野不足（目标：{goal_brief(goal)}），'
                                    f'先展开大地图补充视野再决策。')
                        expanded_for_scout = (self.purpose == 'story'
                                              and (goal is None or goal['kind'] != 'review_block'))
                        if expanded_for_scout:
                            snapshot = self.session.snapshot(expanded=True, keep_expanded=True)
                        else:
                            self.owner.plan_tick(self.session.snapshot(expanded=True), advance=False)
                            snapshot = self.session.snapshot()
                        goal = self.owner.plan_tick(snapshot, advance=False)
                    scouted = self.scout(goal, snapshot)
                    if scouted or expanded_for_scout:
                        snapshot = self.session.snapshot()
                        goal = self.owner.plan_tick(snapshot, advance=False)
                        if (self.purpose == 'story' and (goal is None or goal['kind'] != 'review_block')
                                and located(snapshot) and snapshot.get('squad') is not None):
                            # 收起后的紧凑帧也可能修正敌人位置；出发依据必须与最终选出的目标一致。
                            self.scout_pending = scout_focus(self.owner.state, self.owner.cut, snapshot['squad'],
                                                             include_cooling=True, goal=goal)
                    self.session.record('planning_result', goal=goal, snapshot=snapshot,
                                        scout_pending=self.scout_pending)
                    if self.scout_pending is not None:
                        pending = self.scout_pending
                        failures = self.owner.state.cooldowns.get(pending['id'], {}).get('failures', 0)
                        if failures >= MAX_FAILURES:
                            logger.warning(f"前置道路 {pending['id']} 累计 {failures} 次未能确认，停止推图并请求人工接管。")
                            return {'state': 'needs_review', 'reason': 'scouting_incomplete',
                                    'goal': goal, 'focus': pending}
                        logger.info(f"前置道路尚未确认（{pending['id']}），小队保持原位，下一轮继续侦察。")
                        continue
                    if goal is None or goal['kind'] == 'none':
                        logger.warning(f'停止推图：{describe(goal) if goal else "无目标规划结果"}')
                        return {'state': 'needs_review', 'goal': goal}
                    self.session.record('decision', goal=goal, snapshot=snapshot)
                    self.last_executed = goal
                    self.execute(goal)
                except TargetTriggered as exc:
                    if exc.evidence.get('kind') != 'battle_popup':
                        raise
                    logger.info(f'战斗闸门已触发，开始接管；关联最近执行目标：'
                                f'{goal_brief(self.last_executed)}')
                    self.battle(self.last_executed)
                finally:
                    logger.info(f'第 {index + 1}/{ticks} 轮结束：本轮耗时 {watch}，'
                                f'累计操作 {self.session.inputs} 次')
            logger.warning(f'已达到 {ticks} 轮上限，停止执行并请求人工复核。')
            return {'state': 'needs_review', 'reason': 'tick_budget', 'goal': goal}
        except TargetTriggered as exc:
            if self.last_executed is not None and self.last_executed.get('kind') == 'enemy' \
                    and exc.evidence.get('kind') == 'battle_popup':
                self.owner.state.enemies[self.last_executed['id']]['status'] = 'engaged'
            logger.info(f'主线停止：{exc}；{describe_evidence(exc.evidence)}')
            return {'state': 'triggered', 'evidence': exc.evidence}
        finally:
            logger.info(f'主线会话收尾：任务共运行 {total}，累计操作 {self.session.inputs} 次，'
                        f'决策 {self.owner.state.tick} 轮，定位失败 {self.owner.state.unlocated} 帧。')
            self.session.close()
