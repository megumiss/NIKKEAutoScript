"""自动推图执行层：逐拍决策、有限移动、战斗接管与子目标回写。"""

import time

import numpy as np

from module.campaign.run_state import RunState, located, panel_quad
from module.campaign.scouting import scout_focus
from module.campaign.session import ADVANCE_POINT, DEFEAT_BACK_POINT, DEFEAT_RETRY_POINT, InputBudgetExceeded
from module.campaign.timing import Stopwatch
from module.campaign.vision.battle import score_text, value_text
from module.campaign.window import WindowChanged
from module.campaign.navigation.movement import navigate
from module.campaign.navigation.movement_feedback import TargetTriggered
from module.logger import logger

# 每次调度只执行有限拍数，避免无进展的状态机长期占住输入通道。
CAMPAIGN_TICK_BUDGET = 48
TARGET_MOVES = 12
# 复核站位上最多连看的紧凑帧数：箭头锚点、配准或检测偶发失手会让单帧不可信，换一帧往往就能看清；
# 连看都不可信才算这次复核失败。
REVIEW_FRAMES = 3
# 一次侦察最多看几屏：每屏最多 3 次拖动，连同展开、收起不超过单目标 32 次输入。
SCOUT_VIEWS = 8
# 战斗接管：每次识别之间的间隔、整场接管的时间上限（超过就交人工，不无限等）、
# 连续无法识别时点一次「推进」的上限，以及同一关原地重打的次数上限（用尽就返回并交人工）。
BATTLE_POLL_SECONDS = 2.0
BATTLE_TIME_LIMIT = 300
BATTLE_ADVANCE_EVERY = 5
BATTLE_ADVANCE_LIMIT = 3
BATTLE_UNKNOWN_LIMIT = 30
BATTLE_DEFEAT_RETRIES = 3


class BattleFailed(RuntimeError):
    """同一关原地重打用尽仍然战败：按安全失败停止并交人工，不降级成普通目标失败继续点。"""


def single_transfer_annotations(leg):
    """把一段电梯包装为共享闭环的有向标注，不让导航自行换用未筛选的连接。"""
    return {'objects': [dict(id='entry', category='ground_elevator', type='point', points=[leg['entry']]),
                        dict(id='exit', category='ground_elevator', type='point', points=[leg['exit']])],
            'connections': [dict(id=leg['connection'], category='elevator_connection', directed=True,
                                 **{'from': 'entry', 'to': 'exit'})]}


class CampaignExecutor:
    """连接纯决策与设备会话；任何身份、焦点或预算异常都不转成普通目标失败。"""

    def __init__(self, owner, session, purpose='story'):
        """注入会话以便离线回放；业务状态仍由 Campaign 统一持有。"""
        self.owner, self.session, self.purpose = owner, session, purpose
        # 最近一次真正派出去执行的目标：弹窗在非移动阶段（补视野、观测）冒出来时，用它认领这次战斗，
        # 否则打死的敌人会留在 pending，下一拍白走一趟。
        self.last_executed = None

    def needs_expansion(self, goal, snapshot):
        """没有目标或前沿探索时展开；正常目标的道路经过未观测格时也补一次视野。"""
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
        logger.info(f"开始复核封锁 {goal['id']}（机关 {mechanism or '未指定'}）：站位 "
                    f"{[round(float(v), 1) for v in standoff] if standoff is not None else '未指定'}，"
                    f"最多换 {REVIEW_FRAMES} 帧确认光幕是否还在。")
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
            logger.warning(f"封锁 {goal['id']} 连续 {REVIEW_FRAMES} 帧未进入可靠视野，保留待复核状态；"
                           f"耗时 {watch}。")
            return
        state.pending_reviews.pop(goal['id'], None)
        if record['status'] == 'cleared':
            logger.info(f"封锁 {goal['id']} 已解除（第 {attempt + 1} 帧确认），机关 {mechanism or '未指定'}；"
                        f"派生路网已刷新，耗时 {watch}。")
            self.owner.plan_tick(self.session.snapshot(expanded=True), advance=False)
        else:
            logger.info(f"封锁 {goal['id']} 仍在（第 {attempt + 1} 帧确认），机关 {mechanism or '未指定'} "
                        f"未解除此封锁；耗时 {watch}。")
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
        logger.info(f"派发目标 {kind} {goal['id']}：落点 {[round(float(v), 1) for v in target]}，"
                    f"预计路程 {goal.get('distance')}，最多 {TARGET_MOVES} 次移动（{goal.get('reason')}）")
        try:
            result = navigate(self.session, target, progress, **kwargs)
        except TargetTriggered:
            raise
        except (WindowChanged, InputBudgetExceeded):
            raise
        except (RuntimeError, ValueError) as exc:
            if kind == 'elevator':
                self.owner.state.transfer_failed(goal['leg'])
            else:
                identifier = f"review-{goal['id']}" if kind == 'review_block' else goal['id']
                self.owner.state.penalize(identifier)
            logger.warning(f"目标 {kind} {goal['id']} 失败，已回写冷却：{exc}；"
                           f"移动与观测耗时 {watch}，本目标输入 {self.session.target_inputs} 次")
            self.session.preview(self.session.win.client())
            return
        if result.get('state') != 'arrived':
            self.owner.state.penalize(goal['id'])
            logger.warning(f"目标 {goal['id']} 没有确认触发：{result}；"
                           f"耗时 {watch}，本目标输入 {self.session.target_inputs} 次")
            return
        if kind == 'mechanism':
            self.owner.state.mark_tried(goal['id'], goal['block'])
            logger.info(f"机关 {goal['id']} 已到点（{goal['source']}），下一步复核封锁 {goal['block']}。")
        elif kind == 'review_block':
            self.review_block(goal)
        elif kind == 'elevator':
            key = self.owner.state.transfer_key(goal['leg'])
            if not any(self.owner.state.transfer_key(leg) == key for leg in confirmed_transfers):
                logger.info(f"电梯 {goal['id']} 未产生出口确认，本次只完成到点，重新规划。")
                return
            self.owner.state.transfer_succeeded(goal['leg'])
            self.session.anchor_reference = None
            self.session.anchor_reference_method = None
            logger.info(f"电梯 {goal['id']} 已确认出口，重新选择目标。")
        elif kind == 'explore':
            # 到点不是观察成功：只记短冷却，新快照自行更新观测覆盖。
            self.owner.state.penalize(goal['id'])
        logger.info(f"目标 {kind} {goal['id']} 结束：{result['state']}，"
                    f"移动 {result.get('movement_clicks')} 次，本目标输入 {self.session.target_inputs} 次，"
                    f"耗时 {watch}")

    def scout(self, goal, snapshot):
        """侦察：小队不动，拖展开大地图按路程由近到远看本分量，直到最近的敌人可以确定；返回是否侦察过。

        只用于剧情任务（收集品有标注，不靠看）。刚踩过机关、要先复核光幕时不侦察，复核结果会改变路网。
        配准失败或落点拖不进视野的格记冷却，累计失败到上限后本次任务不再看它。
        侦察中遇到安全异常直接向外传播，不补发收起地图的输入。
        """
        if self.purpose != 'story' or (goal is not None and goal['kind'] == 'review_block'):
            return False
        if not located(snapshot) or snapshot.get('squad') is None:
            return False
        state, squad = self.owner.state, snapshot['squad']
        focus = scout_focus(state, self.owner.cut, squad)
        if focus is None:
            return False
        self.session.begin_target('scout')
        self.session.scout_begin()
        views = 0
        total = Stopwatch()
        for view in range(SCOUT_VIEWS):
            known = '无' if focus['known'] is None else focus['known']
            logger.info(f"侦察第 {view + 1} 屏：{focus['id']} @ {[round(value) for value in focus['position']]}，"
                        f"路程 {focus['distance']}，已知最近敌人路程 {known}")
            watch = Stopwatch()
            recorded = self.session.scout_view(focus['position'], f'scout_{view + 1:02}')
            views += 1
            logger.info(f"侦察第 {view + 1} 屏结果：{'已记下' if recorded else '未记下'}"
                        f"（未记下说明配准没接受或落点没进视野），耗时 {watch}")
            if not recorded or not state.is_observed(focus['position']):
                state.penalize(focus['id'])
            focus = scout_focus(state, self.owner.cut, squad)
            if focus is None:
                break
        else:
            logger.info(f'侦察已达 {SCOUT_VIEWS} 屏上限，按当前已知敌人决策。')
        self.session.scout_end()
        logger.info(f'侦察结束：共看 {views} 屏，展开与拖动总耗时 {total}')
        return True

    def battle(self, goal):
        """接管一次战斗：准备弹窗→（快速）战斗→结算→回到野外。

        每一步都由会话识别并按状态点一次，这里负责节奏、上限与状态回写。整段战斗超过
        BATTLE_TIME_LIMIT 秒、或连续多帧认不出画面都停下交人工：不无限等，也不盲目连点；
        每段战斗（进弹窗、重打后重新开打）都重新计时，所以 3 次原地重打不会被前一段的余量卡住，
        总量仍由会话的章节时限兜住。战败先原地重打同一关，最多 BATTLE_DEFEAT_RETRIES 次；用尽才
        点返回，回到野外后抛 BattleFailed 直接交人工——打不过不是普通目标失败，不换个敌人接着点。
        胜利返回 'victory'，没读到结算就返回 None；战斗结束时给会话打开一次「沿用战斗前坐标」的许可，
        第一帧重新配准失败就用旧坐标继续推图。
        """
        self.session.begin_target('battle')
        self.session.battle_mode = True
        outcome, close_entry, unknown, advances, retries = None, False, 0, 0, 0
        previous, returning, polls = None, False, 0
        total = Stopwatch()
        deadline = time.monotonic() + BATTLE_TIME_LIMIT
        segment = Stopwatch()
        label = '无（弹窗不是从移动里冒出来的）' if goal is None else f"{goal['kind']} {goal['id']}"
        logger.info(f'开始战斗接管：目标 {label}，轮询间隔 {BATTLE_POLL_SECONDS} 秒，单段时限 {BATTLE_TIME_LIMIT} 秒，'
                    f'连续 {BATTLE_UNKNOWN_LIMIT} 帧认不出画面交人工，最多原地重打 {BATTLE_DEFEAT_RETRIES} 次。')
        try:
            while 1:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'战斗接管超过 {BATTLE_TIME_LIMIT} 秒仍未回到野外，交人工。')
                state = self.session.battle_step(close_entry=close_entry)
                # scores 只用于日志：识别状态本身在 state 里，缺分数时按「—」显示而不是编成 0.00。
                kind, scores = state['state'], state.get('scores')
                polls += 1
                if kind != previous:
                    # 只在画面切换时打分数：同一状态每 2 秒轮询一次，逐次打印会把日志刷满。
                    logger.info(f"战斗画面转为 {kind}（第 {polls} 次识别，本段耗时 {segment}，"
                                f"接管累计 {total}）：进入战斗 {score_text(scores, 'entry')}，"
                                f"胜利 {score_text(scores, 'victory')}，战败 {score_text(scores, 'defeat')}，"
                                f"AUTO 模板 {score_text(scores, 'auto_tpl')}，"
                                f"暂停键 {score_text(scores, 'pause')}，"
                                f"AUTO 橙色占比 {value_text(state.get('auto_ratio'))}")
                if kind == 'field':
                    break
                if kind == 'victory':
                    outcome, close_entry, unknown, returning = 'victory', False, 0, False
                elif kind == 'defeat':
                    unknown = 0
                    # 只在「新的一次战败」上计数：结算屏停留超过一个轮询周期时沿用上一次的决定继续点，
                    # 同一张屏不会把重打额度连刷掉；点没生效时也会重复点同一个按钮，而不是误点返回。
                    if previous != 'defeat':
                        if retries < BATTLE_DEFEAT_RETRIES:
                            retries += 1
                            logger.warning(f'战斗失败：原地重打同一关（第 {retries}/{BATTLE_DEFEAT_RETRIES} 次重试），'
                                           f'本段耗时 {segment}，接管累计 {total}。')
                        else:
                            outcome, close_entry, returning = 'defeat', True, True
                            logger.warning('战斗失败且重试用尽：点返回，回到野外后交人工接管。')
                    if returning:
                        self.session.tap(DEFEAT_BACK_POINT)
                        deadline, segment = time.monotonic() + BATTLE_TIME_LIMIT, Stopwatch()
                    else:
                        self.session.tap(DEFEAT_RETRY_POINT)
                elif kind in ('entry', 'battle'):
                    unknown = 0
                    # 每段战斗单独计时：准备弹窗、以及加载结束真正进入战斗时都重置。
                    if kind == 'entry' or previous != 'battle':
                        deadline, segment = time.monotonic() + BATTLE_TIME_LIMIT, Stopwatch()
                else:
                    unknown += 1
                    if unknown % BATTLE_ADVANCE_EVERY == 0 and advances < BATTLE_ADVANCE_LIMIT:
                        advances += 1
                        logger.info(f'战斗画面连续 {unknown} 帧无法识别，按推进点一次兜底（{advances}/{BATTLE_ADVANCE_LIMIT}）。')
                        self.session.tap(ADVANCE_POINT)
                    if unknown >= BATTLE_UNKNOWN_LIMIT:
                        raise RuntimeError(f'战斗接管连续 {unknown} 帧认不出画面，交人工。')
                previous = kind
                self.session.pause(BATTLE_POLL_SECONDS)
        finally:
            self.session.battle_mode = False
        self.session.reuse_squad = True
        logger.info(f'战斗接管结束：{outcome}，识别 {polls} 次，原地重打 {retries} 次，兜底推进 {advances} 次，'
                    f'输入累计 {self.session.target_inputs} 次，总耗时 {total}。')
        if outcome == 'defeat':
            raise BattleFailed(f'同一关原地重打 {BATTLE_DEFEAT_RETRIES} 次仍战败，停止并交人工。')
        if outcome == 'victory':
            if goal is not None and goal.get('kind') == 'enemy':
                self.owner.state.enemies[goal['id']]['status'] = 'cleared'
                logger.info(f'战斗胜利：敌人 {goal["id"]} 已打掉，不再作为目标。')
            else:
                logger.warning(f'战斗胜利，但这次弹窗不是从移动里冒出来的（目标 {goal}），不回写敌人状态。')
        else:
            logger.warning('战斗接管回到野外，但没读到胜负结算，按未结算处理（不改状态）。')
        return outcome

    def run(self, initial, ticks=CAMPAIGN_TICK_BUDGET):
        """会话开始时核对一次主线身份，之后每拍查弹窗、补视野再决策；遇战斗弹窗接管战斗后继续推图。

        战斗接管放在拍内：胜负结算回到野外后继续下一拍，而不是终结任务。非战斗的终态（收集品提示、
        EX 弹窗）仍然直接停止；战斗接管自身的上限与连续战败会抛出安全异常，同样交人工。
        每拍首尾各一行（拍号、已运行时长、本拍输入与耗时）：整条推图日志因此能按拍切分，
        把等待时间落到具体的拍上；拍内的观测、侦察、移动、战斗各自另有带耗时的行。
        """
        goal, snapshot = None, initial
        total = Stopwatch()
        try:
            self.session.start()
            for index in range(ticks):
                watch = Stopwatch()
                logger.info(f'第 {index + 1}/{ticks} 拍开始：任务已运行 {total}，'
                            f'输入累计 {self.session.inputs} 次')
                # 只有移动（execute）里撞上的弹窗才确定属于本拍目标；补视野或观测时冒出来的弹窗，
                # 归给最近一次真正派出去的目标（见 last_executed），而不是把旧目标随手标成已处理。
                try:
                    self.session.begin_target('collectible' if self.purpose == 'collectible' else 'position')
                    self.session.check()
                    if index:
                        snapshot = self.session.snapshot()
                    else:
                        self.session.identity(self.session.win.client())
                    goal = self.owner.plan_tick(snapshot)
                    if self.needs_expansion(goal, snapshot):
                        logger.info(f'这一拍的视线不够（目标 {goal["kind"] if goal else "无"}），先展开大地图补视野再决策。')
                        self.owner.plan_tick(self.session.snapshot(expanded=True), advance=False)
                        snapshot = self.session.snapshot()
                        goal = self.owner.plan_tick(snapshot, advance=False)
                    if self.scout(goal, snapshot):
                        snapshot = self.session.snapshot()
                        goal = self.owner.plan_tick(snapshot, advance=False)
                    if goal is None or goal['kind'] == 'none':
                        logger.warning(f'主线本拍无可执行目标：{goal}')
                        return {'state': 'needs_review', 'goal': goal}
                    self.last_executed = goal
                    self.execute(goal)
                except TargetTriggered as exc:
                    if exc.evidence.get('kind') != 'battle_popup':
                        raise
                    logger.info(f'遇到战斗准备弹窗，接管这次战斗；按最近派出的目标回写 {self.last_executed}')
                    self.battle(self.last_executed)
                finally:
                    logger.info(f'第 {index + 1} 拍结束：耗时 {watch}，输入累计 {self.session.inputs} 次')
            logger.warning(f'主线已达到 {ticks} 拍上限，停止并交人工复核。')
            return {'state': 'needs_review', 'reason': 'tick_budget', 'goal': goal}
        except TargetTriggered as exc:
            if self.last_executed is not None and self.last_executed.get('kind') == 'enemy' \
                    and exc.evidence.get('kind') == 'battle_popup':
                self.owner.state.enemies[self.last_executed['id']]['status'] = 'engaged'
            logger.info(f'主线停止：{exc}；证据 {exc.evidence}')
            return {'state': 'triggered', 'evidence': exc.evidence}
        finally:
            logger.info(f'主线会话收尾：任务共运行 {total}，累计输入 {self.session.inputs} 次，'
                        f'决策 {self.owner.state.tick} 拍，定位未接受 {self.owner.state.unlocated} 帧。')
            self.session.close()
