"""自动推图执行层：逐拍决策、有限移动、战斗接管与子目标回写。"""

import time

import numpy as np

from module.campaign.run_state import RunState, located, panel_quad
from module.campaign.scouting import scout_focus
from module.campaign.session import ADVANCE_POINT, InputBudgetExceeded
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
        全部不可信才记一次复核失败并保留待复核，下一拍由目标选择决定是否交人工。
        """
        mechanism = self.owner.state.pending_reviews.get(goal['id'])
        state = self.owner.state
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
            logger.warning(f"封锁 {goal['id']} 连续 {REVIEW_FRAMES} 帧未进入可靠视野，保留待复核状态。")
            return
        state.pending_reviews.pop(goal['id'], None)
        if record['status'] == 'cleared':
            logger.info(f"封锁 {goal['id']} 已解除，机关 {mechanism or '未指定'}；派生路网已刷新。")
            self.owner.plan_tick(self.session.snapshot(expanded=True), advance=False)
        else:
            logger.info(f"封锁 {goal['id']} 仍在，机关 {mechanism or '未指定'} 未解除此封锁。")
            state.penalize(f"review-{goal['id']}", failure=False)

    def execute(self, goal):
        """执行一个目标并回写结果；普通移动失败进入冷却，安全失败直接向外传播。"""
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
            logger.warning(f"目标 {kind} {goal['id']} 失败，已回写冷却：{exc}")
            self.session.preview(self.session.win.client())
            return
        if result.get('state') != 'arrived':
            self.owner.state.penalize(goal['id'])
            logger.warning(f"目标 {goal['id']} 没有确认触发：{result}")
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
        logger.info(f"目标 {kind} {goal['id']} 结果：{result}")

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
        for view in range(SCOUT_VIEWS):
            known = '无' if focus['known'] is None else focus['known']
            logger.info(f"侦察第 {view + 1} 屏：{focus['id']} @ {[round(value) for value in focus['position']]}，"
                        f"路程 {focus['distance']}，已知最近敌人路程 {known}")
            recorded = self.session.scout_view(focus['position'], f'scout_{view + 1:02}')
            if not recorded or not state.is_observed(focus['position']):
                state.penalize(focus['id'])
            focus = scout_focus(state, self.owner.cut, squad)
            if focus is None:
                break
        else:
            logger.info(f'侦察已达 {SCOUT_VIEWS} 屏上限，按当前已知敌人决策。')
        self.session.scout_end()
        return True

    def battle(self, goal):
        """接管一次战斗：准备弹窗→（快速）战斗→结算→回到野外；返回 'victory' / 'defeat' / None。

        每一步都由会话识别并按状态点一次，这里只负责节奏、上限与状态回写。整场超过
        BATTLE_TIME_LIMIT 秒、或连续多帧认不出画面都停下交人工：不无限等，也不盲目连点。
        战败先原地重打同一关，最多 BATTLE_DEFEAT_RETRIES 次；用尽才点返回，回到野外后抛
        BattleFailed 直接交人工——打不过不是普通目标失败，不该换个敌人接着点。
        战斗结束时给会话打开一次「沿用战斗前坐标」的许可，第一帧重新配准失败就用旧坐标继续推图。
        """
        self.session.begin_target('battle')
        self.session.battle_mode = True
        outcome, close_entry, unknown, advances, retries = None, False, 0, 0, 0
        deadline = time.monotonic() + BATTLE_TIME_LIMIT
        try:
            while 1:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'战斗接管超过 {BATTLE_TIME_LIMIT} 秒仍未回到野外，交人工。')
                state = self.session.battle_step(retry_defeat=retries < BATTLE_DEFEAT_RETRIES,
                                                 close_entry=close_entry)
                kind = state['state']
                if kind == 'field':
                    break
                if kind == 'victory':
                    outcome, close_entry, unknown = 'victory', False, 0
                elif kind == 'defeat':
                    unknown = 0
                    if retries < BATTLE_DEFEAT_RETRIES:
                        retries += 1
                        logger.warning(f'战斗失败：原地重打同一关（第 {retries}/{BATTLE_DEFEAT_RETRIES} 次重试）。')
                    else:
                        outcome, close_entry = 'defeat', True
                        logger.warning('战斗失败且重试用尽：点返回，回到野外后交人工接管。')
                elif kind in ('entry', 'battle'):
                    unknown = 0
                else:
                    unknown += 1
                    if unknown % BATTLE_ADVANCE_EVERY == 0 and advances < BATTLE_ADVANCE_LIMIT:
                        advances += 1
                        logger.info(f'战斗画面连续 {unknown} 帧无法识别，按推进点一次兜底（{advances}/{BATTLE_ADVANCE_LIMIT}）。')
                        self.session.tap(ADVANCE_POINT)
                    if unknown >= BATTLE_UNKNOWN_LIMIT:
                        raise RuntimeError(f'战斗接管连续 {unknown} 帧认不出画面，交人工。')
                self.session.pause(BATTLE_POLL_SECONDS)
        finally:
            self.session.battle_mode = False
        self.session.reuse_squad = True
        if outcome == 'defeat':
            raise BattleFailed(f'同一关原地重打 {BATTLE_DEFEAT_RETRIES} 次仍战败，停止并交人工。')
        if outcome == 'victory':
            if goal is not None and goal.get('kind') == 'enemy':
                self.owner.state.enemies[goal['id']]['status'] = 'cleared'
                logger.info(f'战斗胜利：敌人 {goal["id"]} 已打掉，不再作为目标。')
            else:
                logger.info('战斗胜利：本拍目标不是敌人，只继续推图。')
        else:
            logger.warning('战斗接管回到野外，但没读到胜负结算，按未结算处理（不改状态）。')
        return outcome

    def run(self, initial, ticks=CAMPAIGN_TICK_BUDGET):
        """会话开始时核对一次主线身份，之后每拍查弹窗、补视野再决策；遇战斗弹窗接管战斗后继续推图。

        战斗接管放在拍内：胜负结算回到野外后继续下一拍，而不是终结任务。非战斗的终态（收集品提示、
        EX 弹窗）仍然直接停止；战斗接管自身的上限与连续战败会抛出安全异常，同样交人工。
        """
        goal, snapshot = None, initial
        try:
            self.session.start()
            for index in range(ticks):
                # 只有移动（execute）里撞上的弹窗才认这一次的目标：补视野或观测时冒出来的弹窗
                # 与上一拍的目标无关，拿旧目标回写「已打掉」会把敌人记错。
                engaged = None
                try:
                    self.session.begin_target('collectible' if self.purpose == 'collectible' else 'position')
                    self.session.check()
                    if index:
                        snapshot = self.session.snapshot()
                    else:
                        self.session.identity(self.session.win.client())
                    goal = self.owner.plan_tick(snapshot)
                    if self.needs_expansion(goal, snapshot):
                        self.owner.plan_tick(self.session.snapshot(expanded=True), advance=False)
                        snapshot = self.session.snapshot()
                        goal = self.owner.plan_tick(snapshot, advance=False)
                    if self.scout(goal, snapshot):
                        snapshot = self.session.snapshot()
                        goal = self.owner.plan_tick(snapshot, advance=False)
                    if goal is None or goal['kind'] == 'none':
                        logger.warning(f'主线本拍无可执行目标：{goal}')
                        return {'state': 'needs_review', 'goal': goal}
                    engaged = goal
                    self.execute(goal)
                except TargetTriggered as exc:
                    if exc.evidence.get('kind') != 'battle_popup':
                        raise
                    logger.info(f'遇到战斗准备弹窗，接管这次战斗；本拍目标 {engaged}')
                    self.battle(engaged)
            logger.warning(f'主线已达到 {ticks} 拍上限，停止并交人工复核。')
            return {'state': 'needs_review', 'reason': 'tick_budget', 'goal': goal}
        except TargetTriggered as exc:
            if goal is not None and goal['kind'] == 'enemy' and exc.evidence.get('kind') == 'battle_popup':
                self.owner.state.enemies[goal['id']]['status'] = 'engaged'
            logger.info(f'主线停止：{exc}；证据 {exc.evidence}')
            return {'state': 'triggered', 'evidence': exc.evidence}
        finally:
            self.session.close()
