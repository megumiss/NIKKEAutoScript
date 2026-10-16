"""自动推图执行层：逐拍决策、有限移动、子目标回写；战斗仅停在准备弹窗。"""

import numpy as np

from module.campaign.run_state import RunState, panel_quad
from module.campaign.scouting import scout_focus
from module.campaign.session import InputBudgetExceeded
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
        if snapshot.get('localization') != 'accepted' or snapshot.get('squad') is None:
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

    def run(self, initial, ticks=CAMPAIGN_TICK_BUDGET):
        """每拍先检查主线身份、补视野再决策；遇弹窗立即终止，不进入战斗或继续探索。"""
        goal, snapshot = None, initial
        try:
            self.session.start()
            for index in range(ticks):
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
                self.execute(goal)
            logger.warning(f'主线已达到 {ticks} 拍上限，停止并交人工复核。')
            return {'state': 'needs_review', 'reason': 'tick_budget', 'goal': goal}
        except TargetTriggered as exc:
            if goal is not None and goal['kind'] == 'enemy' and exc.evidence.get('kind') == 'battle_popup':
                self.owner.state.enemies[goal['id']]['status'] = 'engaged'
            logger.info(f'主线停止：{exc}；证据 {exc.evidence}')
            return {'state': 'triggered', 'evidence': exc.evidence}
        finally:
            self.session.close()
