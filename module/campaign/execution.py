"""自动推图执行层：逐拍决策、有限移动、战斗接管与子目标回写。"""

import time

import numpy as np

from module.campaign.run_state import MAX_FAILURES, RunState, located, panel_quad
from module.campaign.goals import describe, frontier_point, nearest_enemy, unobserved_component_cells
from module.campaign.report import (BATTLE_OUTCOMES, BATTLE_STATES, EVIDENCE_KINDS, GOAL_KINDS, GOAL_REASONS,
                                    RESULT_STATES, TARGET_SOURCES, label, points_text)
from module.campaign.route_planning import planning_routes
from module.campaign.scouting import scout_focus
from module.campaign.session import (DEFEAT_BACK_POINT, DEFEAT_RETRY_POINT, SETTLE_TAP_INTERVAL,
                                     InputBudgetExceeded)
from module.campaign.timing import Stopwatch
from module.campaign.vision.battle import score_text, value_text
from module.campaign.window import WindowChanged
from module.base.timer import Timer
from module.campaign.navigation.movement import navigate
from module.campaign.navigation.ex_bypass import ExBypassUnavailable
from module.campaign.navigation.camera_navigation import SquadOffscreen
from module.campaign.navigation.movement_feedback import ClickUnconfirmed, TargetTriggered
from module.campaign.navigation.recovery import centered, centering_frame
from module.campaign.navigation.story_navigation import locate_story_target
from module.campaign.navigation.barrier_navigation import BarrierUnconfirmed, curtains
from module.campaign.noncombat import CampaignNoncombat
from module.logger import logger

TARGET_MOVES = 12
# 限制连续未完成的恢复尝试；回正、重新定位及十字扫描全部通过后清零。
CAMPAIGN_RECOVERY_LIMIT = 3
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
BATTLE_TIME_LIMIT = 10 * 60
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
    name = 'Boss' if goal.get('enemy_kind') == 'boss' else label(GOAL_KINDS, goal['kind'])
    return f"{name} {goal.get('id')}"


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
        self.terminal_continuation = None
        self.terminal_destination = None
        self.interrupted_goal = None
        self.interrupted_inputs = 0
        self.ex_pending = False
        self.terminal_checked = None
        self.terminal_scan_stalls = 0
        self.recoveries = 0
        self.noncombat = CampaignNoncombat(session, owner.current_chapter) if purpose == 'story' else None

    def plan_tick(self, snapshot, advance=True):
        """更新观测后优先恢复未完成目标，不让补视野和战后重规划覆盖已通过检查的 Boss。

        EX 中断目标优先于 Boss 续行，以保留临时清怪或机关的执行语义；实际执行前仍经过道路、侦察、
        光幕及 Boss 遗漏门禁。仅从目标成功、确认战斗结束或检查发现遗漏的分支释放对应引用。
        """
        preferred = self.interrupted_goal or self.terminal_destination
        return self.owner.plan_tick(snapshot, advance=advance, preferred_goal=preferred)

    def defer_ex(self, goal=None):
        """保存被 EX 中断且尚未完成的目标，将重新定位和绕行延迟到下一轮统一异常门禁内。

        启动、侦察、移动和战斗接管都可触发；已清除敌人不可恢复，不能把上轮完成目标误认成续行目标。
        原目标已消耗的输入额度继续累计，不能借 EX 中断重置单目标上限。
        """
        self.interrupted_goal = next((dict(candidate) for candidate in
                                      (goal, self.interrupted_goal, self.terminal_destination, self.last_executed)
                                      if candidate and candidate.get('kind') != 'none'
                                      and self.owner.state.enemies.get(candidate.get('id'), {}).get('status')
                                      != 'cleared'), None)
        if self.interrupted_goal is not None:
            self.interrupted_inputs = max(self.interrupted_inputs, self.session.target_inputs)
        self.ex_pending = True
        logger.info(f'EX 弹窗已关闭，保留目标 {goal_brief(self.interrupted_goal)}；'
                    '下一轮重新定位并规划绕行。')

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
            snapshot = self.session.review_barrier(snapshot, goal['id'])
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

    def complete_mechanism(self, goal):
        """按机关已触发完成收尾：解除当前挡路光幕，回正小队并十字扫描，不再安排复核。

        目标显式携带 block 时只解除该编号；未关联时解除当前 active/stale 光幕，避免附近机关
        到点后旧封锁仍参与规划。解除只影响本次运行状态，后续回正、定位或扫描失败均向上抛出，
        不继续使用自动转镜前的视野与路线缓存。
        """
        state = self.owner.state
        identifiers = ([goal['block']] if goal.get('block')
                       else [record['id'] for record in state.blocking()])
        state.mark_tried(goal['id'])
        cleared = []
        for identifier in identifiers:
            record = state.blocks.get(identifier)
            if record is None:
                continue
            record.update(status='cleared', cleared_at=state.tick)
            state.mechanisms[goal['id']]['tried_for'][identifier] = state.tick
            state.pending_reviews.pop(identifier, None)
            cleared.append(identifier)
        if cleared:
            session = self.session
            session.barrier_references[:] = [item for item in session.barrier_references
                                             if item['block']['annotation_id'] not in cleared]
        self.owner.cut.update(state.blocking())
        self.owner.last_goal = None
        self.owner.last_routes = None
        self.owner.last_route_signature = None
        logger.info(f"地面机关 {goal['id']} 已触发，解除光幕 {cleared or '无记录'}；"
                    '跳过复核，开始小队回正与十字扫描。')
        snapshot = self.session.recover_center()
        state.update(snapshot, advance=False)
        self.owner.cut.update(state.blocking())
        if not self.scan_start('机关触发后', center_checked=True):
            raise RuntimeError('地面机关触发后的十字扫描未取得可信视野。')
        self.session.record('mechanism_complete', goal=goal, cleared=cleared, snapshot=snapshot)

    def execute(self, goal):
        """执行一个目标并回写结果；剧情移动失败交给回正恢复，安全失败直接向外传播。

        导航报错与战斗弹窗可能前后脚出现，记失败冷却前必须重查当前画面；确认为战斗时
        直接交既有接管链路，保留未完成目标，避免战后把机关等位置目标误当成已失败而跳过。
        派发与结束各一行，带上落点、预计距离、移动次数、本目标消耗的输入次数与耗时：
        同一个目标在共享闭环里会打十几行进度，这两行是把它们括起来的边界。
        """
        kind = goal['kind']
        self.owner.state.execution_target = goal['id'] if kind == 'enemy' else None
        purpose = kind if kind in ('enemy', 'collectible') else 'position'
        self.session.begin_target(purpose)
        if self.interrupted_goal and self.interrupted_goal['id'] == goal['id']:
            self.session.target_inputs = self.interrupted_inputs
        target = goal['position']
        kwargs = dict(max_moves=TARGET_MOVES, purpose=purpose)
        if kind == 'enemy' and goal.get('enemy_kind', 'normal') != 'normal':
            kwargs['enemy_kind'] = goal['enemy_kind']
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
            if self.session.ex_bypass.obstacles:
                self.session.ex_bypass.check_target(target)
            result = navigate(self.session, target, progress, **kwargs)
        except TargetTriggered:
            raise
        except (WindowChanged, InputBudgetExceeded, ClickUnconfirmed, ExBypassUnavailable):
            self.owner.state.execution_target = None
            raise
        except (RuntimeError, ValueError) as exc:
            self.session.identity(self.session.win.client())
            self.owner.state.execution_target = None
            if kind == 'mechanism' and isinstance(exc, SquadOffscreen):
                self.complete_mechanism(goal)
                return
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
            if self.purpose == 'story':
                raise
            return
        self.owner.state.execution_target = None
        if result.get('state') != 'arrived':
            self.owner.state.penalize(goal['for_target'] if kind == 'advance' else goal['id'])
            logger.warning(f"{goal_brief(goal)} 未确认触发：{describe_result(result)}；"
                           f"耗时 {watch}，本目标操作 {self.session.target_inputs} 次")
            if self.purpose == 'story':
                raise RuntimeError(f'移动未完成：{describe_result(result)}')
            return
        if kind == 'mechanism':
            self.complete_mechanism(goal)
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
            logger.info(f"电梯 {goal['id']} 已确认传送成功，执行一次十字扫描后重新选择目标。")
            if not self.scan_start('电梯后') and self.purpose == 'story':
                raise RuntimeError('电梯后十字扫描未取得可信视野。')
        elif kind == 'explore':
            # 到点不是观察成功：只记短冷却，新快照自行更新观测覆盖。
            self.owner.state.penalize(goal['id'])
        elif kind == 'story':
            self.owner.state.route_goal = None
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
                      'nearer_road': '可能更近的支路'}[focus['source']]
            logger.info(f"侦察第 {view + 1} 屏：目标 {focus['id']}，位置 {points_text(focus['position'])}，"
                        f"{source}，路程 {focus['distance']} 像素，本段检查范围 {known}")
            watch = Stopwatch()
            recorded = self.session.scout_view(focus['position'], f'scout_{view + 1:02}')
            views += 1
            covered = state.is_observed(focus['position'])
            logger.info(f"侦察第 {view + 1} 屏结果：{'已记录可信帧' if recorded else '定位未通过'}，"
                        f"目标格{'已覆盖' if covered else '尚未完整覆盖'}，耗时 {watch}")
            failure = self.session.scout_failure
            if not recorded and failure is not None:
                # 配准失败说明当前距离上看不清该格（探索前沿或走廊歧义）：标记后既不占失败
                # 冷却也不阻塞出发（scouting.py 跳过），小队走近后自动恢复侦察。
                state.mark_unscoutable(focus['id'], squad)
                logger.info(f"侦察目标 {focus['id']} 暂不可扫（{failure['reason']}），"
                            f'标记后跳过；小队走近后自动恢复。')
            elif not recorded or not covered:
                state.penalize(focus['id'])
            focus = scout_focus(state, self.owner.cut, squad, goal=goal)
            if focus is None:
                break
        else:
            logger.info(f'侦察达到单轮 {SCOUT_VIEWS} 屏上限，剩余区域延后至下一轮检查，暂不执行移动。')
        self.scout_pending = scout_focus(state, self.owner.cut, squad, include_cooling=True, goal=goal)
        # 侦察是内部有界的批量阶段；收尾输入翻篇记录，不与侦察叠加触发单目标上限。
        self.session.begin_target('position')
        self.session.scout_end()
        logger.info(f'侦察结束：检查 {views} 屏，展开与拖动总耗时 {total}')
        return True

    def terminal_region_enemies(self, squad):
        """返回当前连通分量内可处理的普通敌人，Boss 与 EX 不参与遗漏清扫。"""
        home = self.owner.cut.component(squad)
        return [enemy for enemy in self.owner.state.pending_enemies() if enemy['kind'] == 'normal'
                and (not home or self.owner.cut.component(enemy['position']) == home)]

    def scan_terminal_region(self, snapshot):
        """在 Boss 前补扫当前连通分量的未覆盖格，返回最新紧凑观测。

        每轮最多检查 SCOUT_VIEWS 屏；发现普通敌人、扫描失败或预算用尽都会停止本批，
        由调用方重新决策。连续 MAX_FAILURES 轮没有新增覆盖或普通敌人时转入有界恢复，
        扫描冷却不能把剩余未覆盖格视为完成，安全异常照常向上传播。
        """
        state, squad = self.owner.state, snapshot['squad']
        observed_before = int(state.observed.sum())
        self.session.begin_target('scout')
        self.session.scout_begin()
        views = 0
        for view in range(SCOUT_VIEWS):
            if self.terminal_region_enemies(squad):
                break
            focus = frontier_point(state, self.owner.cut, squad, distance_limit=None)
            if focus is None:
                break
            recorded = self.session.scout_view(focus['position'], f'terminal_{view + 1:02d}')
            views += 1
            covered = state.is_observed(focus['position'])
            logger.info(f"终局遗漏补扫第 {views} 屏：{focus['id']}，"
                        f"{'已记录可信帧' if recorded else '定位未通过'}，"
                        f"目标格{'已覆盖' if covered else '尚未完整覆盖'}。")
            if not recorded or not covered:
                state.penalize(focus['id'])
                break
        self.session.begin_target('position')
        self.session.scout_end()
        logger.info(f'终局遗漏补扫结束：本轮检查 {views} 屏。')
        if int(state.observed.sum()) > observed_before or self.terminal_region_enemies(squad):
            self.terminal_scan_stalls = 0
        else:
            self.terminal_scan_stalls += 1
            if self.terminal_scan_stalls >= MAX_FAILURES:
                self.terminal_scan_stalls = 0
                raise RuntimeError(f'终局遗漏补扫连续 {MAX_FAILURES} 轮无新增覆盖或普通敌人。')
        return self.session.snapshot(known_squad=list(squad))

    def terminal_precheck(self, goal, snapshot):
        """仅在 Boss 前检查遗漏，未就绪时保留目的地并返回 None。

        已知普通敌人始终优先清理；没有已知遗漏时先读章节列表的蓝色敌人进度，Boss 未击败所以
        已清数加一达到总数即可跳过补扫。进度不足或读取失败再检查未可信覆盖的道路格。补扫或清扫期间
        terminal_continuation 保留 Boss 目的地，完成后从当前小队位置续走，不返回补扫前的位置。
        补扫确认的清怪目标通过 execution_target 保留到下一轮，仍经过原有侦察与导航门禁；
        未实际派发前不修改 last_executed，避免把突发战斗误关联到尚未执行的目标。
        相同目的地、路网与观测只需完成一次检查；新增敌人或覆盖后重新检查。
        """
        key = (goal['kind'], goal['id'], self.owner.cut.signature,
               len(self.owner.state.enemies), int(self.owner.state.observed.sum()))
        pending = self.terminal_region_enemies(snapshot['squad'])
        if self.terminal_checked == key and not pending:
            return goal, snapshot
        continuation = dict(goal)
        self.terminal_continuation = continuation
        squad = snapshot['squad']
        unseen = unobserved_component_cells(self.owner.state, self.owner.cut, squad)
        progress = None
        if pending or unseen is None or unseen > 0:
            logger.info(f"{goal_brief(continuation)} 前检查区域遗漏：待清普通敌人 {len(pending)} 个，"
                        f'未覆盖道路 {unseen if unseen is not None else "未知"} 格；暂缓目的地执行。')
            if not pending and unseen:
                progress = self.noncombat.enemy_progress()
                snapshot = self.session.snapshot(known_squad=list(squad))
                self.owner.plan_tick(snapshot, advance=False)
                if progress is not None and progress[0] + 1 >= progress[1]:
                    logger.info(f'章节敌人进度 {progress[0]}/{progress[1]}，除 Boss 外已清理，跳过区域补扫。')
                    unseen = 0
                else:
                    snapshot = self.scan_terminal_region(snapshot)
                    self.owner.plan_tick(snapshot, advance=False)
            pending = self.terminal_region_enemies(squad)
            if unseen != 0:
                unseen = unobserved_component_cells(self.owner.state, self.owner.cut, squad)
        ready = not pending and unseen == 0
        self.session.record('terminal_precheck', goal=continuation, ready=ready, progress=progress,
                            pending_enemies=[enemy['id'] for enemy in pending], unobserved_cells=unseen)
        if not ready:
            self.terminal_destination = None
            if self.interrupted_goal and self.interrupted_goal['id'] == goal['id']:
                self.interrupted_goal = None
                self.interrupted_inputs = 0
            if pending:
                cleanup, _ = nearest_enemy(self.owner.state, self.owner.cut, squad)
                if cleanup is None or cleanup.get('enemy_kind') != 'normal':
                    raise RuntimeError('终局补扫发现普通敌人，但未能生成可达清怪目标。')
                self.owner.state.execution_target = cleanup['id']
                self.terminal_scan_stalls = 0
                logger.info(f'终局补扫保留清怪目标 {goal_brief(cleanup)}，下一轮完成前置侦察后执行。')
            return None, snapshot
        key = (goal['kind'], goal['id'], self.owner.cut.signature,
               len(self.owner.state.enemies), int(self.owner.state.observed.sum()))
        self.terminal_checked = key
        self.terminal_continuation = None
        self.terminal_destination = continuation
        self.owner.last_goal = continuation
        self.owner.last_routes = planning_routes(self.owner.cut, snapshot, continuation,
                                                 self.owner.state.route_goal, state=self.owner.state)
        logger.info(f'{goal_brief(continuation)} 前区域遗漏检查通过，从当前位置恢复目的地。')
        return continuation, snapshot

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
            self.owner.state.execution_target = None
        self.session.reuse_squad = True
        logger.info(f'战斗接管结束：{label(BATTLE_OUTCOMES, settled)}，采样 {polls} 次，'
                    f'重试 {retries} 次，累计操作 {self.session.target_inputs} 次，总耗时 {total}。')
        if settled == 'defeat':
            raise BattleFailed(f'当前关卡重试 {BATTLE_DEFEAT_RETRIES} 次仍战败，停止执行并请求人工接管。')
        if goal is not None and goal.get('kind') == 'enemy':
            self.owner.state.enemies[goal['id']]['status'] = 'cleared'
            logger.info(f'战斗胜利：{goal_brief(goal)} 已标记为清除，移出候选集合。')
            if goal.get('enemy_kind') == 'boss':
                self.terminal_destination = None
                # Boss 可能触发换章，旧地图和目标记忆不能跨章继续使用。
                self.session.verify_identity(self.session.win.client())
        else:
            logger.warning(f'战斗胜利但无关联敌人目标（最近执行目标：{goal_brief(goal)}），不更新敌人状态。')
        return settled

    def scan_start(self, scene='开局', center_checked=False):
        """按场景名十字扫描小队周围，返回可信屏数，零屏由剧情执行层触发恢复。

        开局、电梯传送后与异常恢复共用中心与四向扫描。扫描前必须确认小队居中，否则先独立回正并重新定位；
        刚完成 recover_center 的调用方传入 center_checked，避免丢失离屏图标点击记录后重复回正。
        扫描是内部有界的批量阶段，不是同一目标上的原地空转，所以阶段结束即翻篇：收起地图与刷新快照的
        收尾输入记入新预算窗口，不与扫描叠加触发单目标上限。安全异常（窗口、弹窗、取消）不捕获，
        与侦察一致：此时不补发收起地图的输入。
        """
        if not center_checked and not centered(centering_frame(self.session)):
            logger.info(f'{scene}十字扫描前小队未居中，先执行回正与重新定位。')
            self.session.recover_center()
        self.session.begin_target('scout')
        self.session.scout_begin()
        watch = Stopwatch()
        scanned = 0
        try:
            scanned = self.session.scan_explored()
        except (TargetTriggered, WindowChanged, InputBudgetExceeded):
            raise
        except (RuntimeError, ValueError) as exc:
            logger.warning(f'{scene}扫描未能完成（{exc}），本轮不采纳扫描结果。')
        else:
            logger.info(f'{scene}扫描完成：{scanned} 屏，耗时 {watch}')
        self.session.begin_target('position')
        self.session.scout_end()
        return scanned

    def recover(self, reason):
        """清除本轮临时决策，独立回正并从十字扫描重新开始；最多连续尝试三次。

        已清除敌人、机关、电梯、光幕和道路事实保留，失败目标的冷却不重置；只解除镜头侦察的
        临时失败与主线标记定位冷却，允许新视野重新确认。恢复过程仍受原输入及时限闸门约束，
        任何失败交回主循环处理；回正、定位或扫描未通过时不能继续使用异常前的目标。
        完整恢复成功后清零连续计数，同章之后的独立异常重新获得三次尝试机会。
        """
        if self.recoveries >= CAMPAIGN_RECOVERY_LIMIT:
            raise RuntimeError('主线连续自动恢复次数已达上限。')
        self.recoveries += 1
        self.session.record('recovery_start', force=True, attempt=self.recoveries, reason=reason)
        logger.warning(f'主线自动恢复开始：连续第 {self.recoveries}/{CAMPAIGN_RECOVERY_LIMIT} 次，原因 {reason}。')
        state = self.owner.state
        state.execution_target = None
        state.route_goal = None
        state.unscoutable.clear()
        for identifier in list(state.cooldowns):
            if identifier.startswith('scout-') or identifier == 'story-location':
                state.cooldowns.pop(identifier)
        self.scout_pending = None
        self.terminal_scan_stalls = 0
        self.owner.last_goal = None
        self.owner.last_routes = None
        self.owner.last_route_signature = None
        snapshot = self.session.recover_center()
        state.update(snapshot, advance=False)
        self.owner.cut.update(state.blocking())
        if not self.scan_start('恢复后', center_checked=True):
            raise RuntimeError('回正后的十字扫描未取得可信视野。')
        self.session.record('recovery_complete', attempt=self.recoveries)
        logger.info(f'主线自动恢复完成：连续第 {self.recoveries}/{CAMPAIGN_RECOVERY_LIMIT} 次，'
                    '连续计数已清零，下一轮重新选择目标。')
        self.recoveries = 0

    def confirm_barriers_safe(self, snapshot):
        """确认当前可见光幕；确认失败时保留原快照与既有路网，不终止推图。

        光幕确认失败只说明这一处暂时看不清（斜视角能量网隐身、配准不稳等），
        路网保持不变：若路线真的撞上没开的光幕，小队会在墙前停住，
        由移动闭环的停滞检测记失败并换目标，没有安全风险。
        """
        if not curtains(snapshot):
            return snapshot
        try:
            return self.session.confirm_barriers(snapshot)
        except BarrierUnconfirmed as exc:
            logger.warning(f'光幕确认未完成（{exc}）；路网保持不变，继续推图。')
            return snapshot

    def run(self, initial):
        """会话开始时核对一次主线身份，之后每轮查弹窗、补视野再决策；遇战斗弹窗接管战斗后继续推图。

        战斗接管放在一轮内：胜负结算回到野外后继续下一轮，而不是终结任务。收集品提示仍然直接停止；
        EX 关闭后重新定位并绕行，绕行不可用或战斗失败时交人工。
        找主线敌人和侦察阶段冻结已有小队底图坐标，只重新配准镜头与敌人；跨轮侦察同样不要求
        实时小队圆环。实际执行移动或接管战斗前结束此模式，后续恢复移动闭环的位置观测。
        剧情定位、规划或侦察异常在下一轮独立回正并重扫，丢弃冻结坐标；窗口、输入预算、取消
        和战斗失败仍立即停止。连续恢复尝试有界，完整恢复成功后清零；不设单章总时限或主循环轮数上限。
        Boss 执行前先核对章节敌人进度，进度不足才补扫当前连通分量；电梯不再触发终局遗漏检查。
        终局清怪战斗结束后，下一拍先做状态刷新与终局检查；通过后持续保留 Boss 目标直到战斗确认完成，
        不先把 Boss 当成普通最近敌人直接放行。每轮首尾各一行（轮号、已运行时长、累计操作与耗时）：
        整条推图日志因此能按轮切分，把等待时间落到具体的轮上；轮内的观测、侦察、移动、战斗各自另有带耗时的行。
        """
        goal, snapshot = None, initial
        index = -1
        camera_options = {}
        recovery_reason = None
        total = Stopwatch()
        try:
            try:
                self.session.start()
            except TargetTriggered as exc:
                if exc.evidence.get('kind') != 'ex_stage_closed':
                    raise
                self.defer_ex()
            self.session.record('initial_snapshot', snapshot=initial)
            if not self.ex_pending and initial.get('localization') == 'accepted' and initial.get('squad') is not None:
                # 准备阶段与首轮之间没有移动，小队坐标可直接约束第一次展开配准。
                self.session.remember_registration(initial['squad'], initial.get('roi_to_map'),
                                                   initial.get('coverage_size'))
                self.session.remember_navigation(initial)
            while 1:
                index += 1
                watch = Stopwatch()
                logger.info(f'第 {index + 1} 轮开始：任务已运行 {total}，'
                            f'累计操作 {self.session.inputs} 次')
                # 只有移动（execute）里撞上的弹窗才确定属于本轮目标；补视野或观测时冒出来的弹窗，
                # 归给最近一次真正派出去的目标（见 last_executed），而不是把旧目标随手标成已处理。
                try:
                    self.session.begin_target('collectible' if self.purpose == 'collectible' else 'position')
                    self.session.check()
                    if self.noncombat is not None:
                        noncombat = self.noncombat.step()
                        if noncombat is not None:
                            if noncombat['state'] == 'chapter_transition':
                                return noncombat
                            continue
                    if recovery_reason is not None:
                        camera_options = {}
                        self.recover(recovery_reason)
                        recovery_reason = None
                        goal = None
                        continue
                    resumed_ex = self.ex_pending
                    if resumed_ex:
                        camera_options = {}
                        self.session.prepare_ex_bypass(self.interrupted_goal or self.terminal_destination)
                        self.ex_pending = False
                    if index or resumed_ex:
                        snapshot = self.session.snapshot(**camera_options)
                    else:
                        self.session.identity(self.session.win.client())
                        if self.purpose == 'story' and located(initial) and initial.get('squad') is not None:
                            if not self.scan_start():
                                raise RuntimeError('开局十字扫描未取得可信视野。')
                            snapshot = self.session.snapshot()
                    if self.purpose == 'story' and (not located(snapshot) or snapshot.get('squad') is None):
                        raise RuntimeError('当前小队定位不可用，停止本轮目标选择。')
                    if curtains(snapshot):
                        snapshot = self.confirm_barriers_safe(snapshot)
                        camera_options = ({'known_squad': list(snapshot['squad'])}
                                          if located(snapshot) and snapshot.get('squad') is not None else {})
                    if self.terminal_continuation is not None:
                        logger.info('恢复已保留 Boss 的遗漏检查。')
                        self.owner.state.update(snapshot, advance=False)
                        self.owner.cut.update(self.owner.state.blocking())
                        goal, snapshot = self.terminal_precheck(self.terminal_continuation, snapshot)
                    goal = self.plan_tick(snapshot)
                    if (self.purpose == 'story' and self.owner.state.route_goal is None
                            and snapshot.get('story_markers') and located(snapshot)
                            and snapshot.get('squad') is not None
                            and (goal is None or goal['kind'] != 'review_block')
                            and not self.owner.state.cooling('story-location')):
                        changed = locate_story_target(self.session, snapshot['squad'])
                        self.session.record('story_location_result', camera_changed=changed,
                                            route_goal=self.owner.state.route_goal)
                        if changed:
                            camera_options = {'known_squad': list(snapshot['squad'])}
                            snapshot = self.session.snapshot(**camera_options)
                            if curtains(snapshot):
                                snapshot = self.confirm_barriers_safe(snapshot)
                        goal = self.plan_tick(snapshot, advance=False)
                    expanded_for_scout = False
                    if (self.purpose == 'story' and located(snapshot) and snapshot.get('squad') is not None
                            and (goal is None or goal['kind'] != 'review_block')):
                        camera_options = {'known_squad': list(snapshot['squad'])}
                    if self.needs_expansion(goal, snapshot):
                        logger.info(f'当前视野不足（目标：{goal_brief(goal)}），'
                                    f'先展开大地图补充视野再决策。')
                        expanded_for_scout = (self.purpose == 'story'
                                              and (goal is None or goal['kind'] != 'review_block'))
                        if expanded_for_scout:
                            snapshot = self.session.snapshot(expanded=True, keep_expanded=True, **camera_options)
                        else:
                            self.plan_tick(self.session.snapshot(expanded=True), advance=False)
                            snapshot = self.session.snapshot()
                        goal = self.plan_tick(snapshot, advance=False)
                    scouted = self.scout(goal, snapshot)
                    if expanded_for_scout and not scouted:
                        # 没有新侦察观测时沿用已接受的展开快照，收起面板不再做一次可能失败的紧凑配准。
                        self.session.win.reset_minimap(expanded=False, reset=False)
                    elif scouted:
                        snapshot = self.session.snapshot(**camera_options)
                        if curtains(snapshot):
                            snapshot = self.confirm_barriers_safe(snapshot)
                        goal = self.plan_tick(snapshot, advance=False)
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
                            if self.purpose == 'story':
                                raise RuntimeError(f"前置道路 {pending['id']} 累计 {failures} 次未能确认。")
                            logger.warning(f"前置道路 {pending['id']} 累计 {failures} 次未能确认，停止推图并请求人工接管。")
                            return {'state': 'needs_review', 'reason': 'scouting_incomplete',
                                    'goal': goal, 'focus': pending}
                        logger.info(f"前置道路尚未确认（{pending['id']}），小队保持原位，下一轮继续侦察。")
                        continue
                    if (goal is not None and goal['kind'] == 'enemy' and goal.get('enemy_kind') == 'boss'):
                        goal, snapshot = self.terminal_precheck(goal, snapshot)
                        if goal is None:
                            continue
                    if goal is None or goal['kind'] == 'none':
                        if self.purpose == 'story':
                            raise RuntimeError(describe(goal) if goal else '无目标规划结果')
                        logger.warning(f'停止推图：{describe(goal) if goal else "无目标规划结果"}')
                        return {'state': 'needs_review', 'goal': goal}
                    self.session.record('decision', goal=goal, snapshot=snapshot)
                    self.last_executed = goal
                    camera_options = {}
                    self.execute(goal)
                    self.interrupted_goal = None
                    self.interrupted_inputs = 0
                except TargetTriggered as exc:
                    if exc.evidence.get('kind') == 'ex_stage_closed':
                        self.defer_ex(goal)
                        camera_options = {}
                        continue
                    if exc.evidence.get('kind') != 'battle_popup':
                        raise
                    logger.info(f'战斗闸门已触发，开始接管；关联最近执行目标：'
                                f'{goal_brief(self.last_executed)}')
                    camera_options = {}
                    try:
                        self.battle(self.last_executed)
                    except TargetTriggered as battle_exc:
                        if battle_exc.evidence.get('kind') != 'ex_stage_closed':
                            raise
                        self.defer_ex(self.last_executed)
                        continue
                    self.interrupted_goal = None
                    self.interrupted_inputs = 0
                except (WindowChanged, InputBudgetExceeded, BattleFailed, ExBypassUnavailable):
                    raise
                except (RuntimeError, ValueError) as exc:
                    if self.purpose != 'story':
                        raise
                    camera_options = {}
                    recovery_reason = str(exc)
                    self.session.record('recovery_required', force=True, reason=recovery_reason,
                                        attempt=self.recoveries, goal=goal)
                    if self.recoveries >= CAMPAIGN_RECOVERY_LIMIT:
                        logger.warning(f'主线连续自动恢复已达 {CAMPAIGN_RECOVERY_LIMIT} 次上限，停止执行：{exc}')
                        return {'state': 'needs_review', 'reason': 'recovery_limit',
                                'error': recovery_reason, 'goal': goal}
                    logger.warning(f'主线本轮异常，下一轮回正并重扫：{exc}')
                finally:
                    logger.info(f'第 {index + 1} 轮结束：本轮耗时 {watch}，'
                                f'累计操作 {self.session.inputs} 次')
        except ExBypassUnavailable as exc:
            logger.warning(f'EX 绕行不可用：{exc}；停止移动并请求人工复核。')
            return {'state': 'needs_review', 'reason': 'ex_bypass_unavailable', 'error': str(exc), 'goal': goal}
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
