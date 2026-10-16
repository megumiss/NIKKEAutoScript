"""自动推图的离线回归：合成路网与假设备，不访问游戏窗口或发送真实输入。"""

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from module.campaign.execution import (BATTLE_ADVANCE_LIMIT, BATTLE_DEFEAT_LIMIT, BATTLE_UNKNOWN_LIMIT,
                                       CampaignExecutor, single_transfer_annotations)
from module.campaign.goals import choose_goal
from module.campaign.navigation.routing import CutRoads, component_at
from module.campaign.run_state import COOLDOWN_TICKS, RunState, panel_quad
from module.campaign.session import (ADVANCE_POINT, DEFEAT_BACK_POINT, ENTER_BATTLE_POINT, ENTRY_CLOSE_POINT,
                                     LOCALIZE_RETRIES, QUICK_BATTLE_POINT, CampaignLocalizer, CampaignSession,
                                     InputBudgetExceeded)
from module.campaign.subgoals import available_annotations
from module.campaign.window import WindowChanged
from module.campaign.navigation.movement import navigate
from module.campaign.navigation.movement_feedback import TargetTriggered
from module.config.config import TaskEnd


def chart_fixture(split=False):
    """构造有分支的单分量路网，或由有向电梯连接的两个独立分量。"""
    road = np.zeros((256, 384), np.uint8)
    road[80:120, 16:360] = 1
    road[100:200, 64:96] = 1
    if split:
        road[:, 160:224] = 0
    labels = cv2.connectedComponents(road, connectivity=8)[1]
    chart = SimpleNamespace(supported=True, model=None, size=(384, 256), road=road,
                            regions=labels[::8, ::8], points=[], transfers=[], chapter=41)
    chart.connected = lambda first, second: bool(component_at(labels, first)
                                                and component_at(labels, first) == component_at(labels, second))
    return chart


def snapshot_fixture(squad=(40, 96), blocks=None, mode='compact', enemies=None, trusted=True):
    """提供同坐标系的定位快照；默认全图覆盖，方便明确区分动态封锁与未观测前沿。"""
    return dict(localization='accepted', squad=list(squad), roi_to_map=np.eye(3).tolist(),
                minimap={'size': [384, 256], 'mode': mode}, blocks=blocks or [], enemies=enemies or [],
                barriers_observed=trusted, mechanisms=[])


class GoalTests(unittest.TestCase):
    """验证纯目标规划的可达性、尝试记忆、失败冷却与难度隔离。"""

    def setUp(self):
        """每个测试使用独立状态与实际切开图，不读运行时地图资源。"""
        self.chart = chart_fixture()
        self.state = RunState(41, 'normal', self.chart.size)
        self.cut = CutRoads(self.chart)
        self.snapshot = snapshot_fixture()
        self.block = dict(id='curtain', point=[200, 100], segment=[[200, 60], [200, 140]])
        self.enemy = dict(kind='normal', position=[320, 96])

    def absorb(self, snapshot):
        """模拟一拍生产状态更新并刷新派生图，然后调用真正的目标选择。"""
        self.state.update(snapshot)
        self.cut.update(self.state.blocking())
        return choose_goal(self.state, self.chart, snapshot, self.cut)

    def test_nearest_enemy_and_ex_exclusion(self):
        """正常敌人参与推图，EX 不会因更近而被选择。"""
        goal = self.absorb(snapshot_fixture(enemies=[self.enemy, dict(kind='ex', position=[64, 96])]))
        self.assertEqual(goal['kind'], 'enemy')
        self.assertEqual(goal['position'], self.enemy['position'])

    def test_active_curtain_selects_annotated_mechanism(self):
        """只被光幕挡住时先去同侧机关，不走到光幕上。"""
        self.chart.points = [dict(id='switch', category='ground_mechanism', position=[80, 176])]
        goal = self.absorb(snapshot_fixture(blocks=[self.block], enemies=[self.enemy]))
        self.assertEqual(goal['kind'], 'mechanism')
        self.assertEqual(goal['id'], 'switch')
        self.assertEqual(goal['source'], 'annotation')
        self.assertNotEqual(self.cut.component([40, 96]), self.cut.component([320, 96]))

    def test_mechanism_requires_review_and_does_not_repeat(self):
        """到点记录尝试，先复核；仍在时尝试下一个机关。"""
        self.chart.points = [dict(id='first', category='ground_mechanism', position=[80, 176]),
                             dict(id='second', category='ground_mechanism', position=[112, 96])]
        self.absorb(snapshot_fixture(blocks=[self.block], enemies=[self.enemy]))
        self.state.mark_tried('first', 'block-1')
        goal = choose_goal(self.state, self.chart, self.snapshot, self.cut)
        self.assertEqual(goal['kind'], 'review_block')
        self.state.pending_reviews.clear()
        goal = choose_goal(self.state, self.chart, self.snapshot, self.cut)
        self.assertEqual(goal['id'], 'second')

    def test_no_mechanism_does_not_guess(self):
        """缺少机关点与可信兜底位置时交人工，不拿收集品当机关。"""
        self.chart.points = [dict(id='item', category='normal_collectible', position=[80, 176])]
        goal = self.absorb(snapshot_fixture(blocks=[self.block], enemies=[self.enemy]))
        self.assertEqual(goal['reason'], 'blocked_by_curtain')

    def test_fallback_merges_positions_and_preserves_attempts(self):
        """场景机关坐标抖动合并，已经踩过的事实不会丢失。"""
        first = snapshot_fixture(blocks=[self.block], enemies=[self.enemy])
        first['mechanisms'] = [dict(position=[80, 176], source='scene_fallback')]
        goal = self.absorb(first)
        self.assertEqual(goal['source'], 'scene_fallback')
        self.state.mark_tried(goal['id'], 'block-1')
        first['mechanisms'][0]['position'] = [82, 177]
        self.absorb(first)
        self.assertEqual(len(self.state.mechanisms), 1)
        self.assertTrue(self.state.tried_for(goal['id'], 'block-1'))

    def test_failed_projection_never_clears_curtain(self):
        """没检测到不是解封证明：投影不可用时即使点在窗口内也只能 stale。"""
        self.absorb(snapshot_fixture(blocks=[self.block]))
        self.absorb(snapshot_fixture(trusted=False))
        self.assertEqual(self.state.blocks['block-1']['status'], 'stale')
        self.assertFalse(self.cut.connected([40, 96], [320, 96]))

    def test_trusted_compact_clears_and_reconnects(self):
        """可靠紧凑帧里光幕消失才重连道路，展开帧不能解封。"""
        self.absorb(snapshot_fixture(blocks=[self.block]))
        self.absorb(snapshot_fixture(mode='expanded'))
        self.assertEqual(self.state.blocks['block-1']['status'], 'active')
        self.absorb(snapshot_fixture())
        self.assertEqual(self.state.blocks['block-1']['status'], 'cleared')
        self.assertTrue(self.cut.connected([40, 96], [320, 96]))

    def test_stale_curtain_uses_same_side_standoff(self):
        """离开视野的光幕要先复核，复核站位必须与小队同侧。"""
        self.absorb(snapshot_fixture(blocks=[self.block], enemies=[self.enemy]))
        narrow = snapshot_fixture()
        narrow['minimap']['size'] = [80, 64]
        goal = self.absorb(narrow)
        self.assertEqual(goal['kind'], 'review_block')
        self.assertTrue(self.cut.connected(narrow['squad'], goal['position']))

    def test_expanded_coverage_uses_registration_size(self):
        """展示 ROI 与配准裁剪尺寸不同，已观测四边形用配准尺寸。"""
        snapshot = snapshot_fixture(mode='expanded')
        snapshot['coverage_size'] = [300, 200]
        np.testing.assert_array_equal(panel_quad(snapshot)[2], [300, 200])

    def test_frontier_is_walkable_and_respects_cooldown(self):
        """前沿落点在当前分量道路上，失败格冷却时不反复选中。"""
        self.cut.update([])
        goal = choose_goal(self.state, self.chart, self.snapshot, self.cut)
        self.assertEqual(goal['kind'], 'explore')
        self.assertTrue(self.cut.connected(self.snapshot['squad'], goal['position']))
        self.state.penalize(goal['id'])
        other = choose_goal(self.state, self.chart, self.snapshot, self.cut)
        self.assertNotEqual(other['id'], goal['id'])

    def test_bad_location_never_produces_move(self):
        """定位失败或非有限坐标不能产生目标。"""
        self.cut.update([])
        for position, status in (([np.nan, 96], 'accepted'), ([40, 96], 'needs_review')):
            snapshot = snapshot_fixture()
            snapshot.update(squad=position, localization=status)
            self.assertEqual(choose_goal(self.state, self.chart, snapshot, self.cut)['kind'], 'none')

    def test_collectibles_respect_difficulty(self):
        """收集品任务不选敌人或另一难度的点。"""
        self.chart.points = [dict(id='normal', category='normal_collectible', position=[112, 96]),
                             dict(id='hard', category='hard_collectible', position=[64, 96])]
        self.absorb(snapshot_fixture(enemies=[dict(kind='normal', position=[80, 96])]))
        goal = choose_goal(self.state, self.chart, self.snapshot, self.cut, purpose='collectible')
        self.assertEqual(goal['kind'], 'collectible')
        self.assertEqual(goal['id'], 'normal')

    def test_elevator_route_direction_and_alternative_after_failure(self):
        """电梯入口到出口有向；失败后选备用航段，反向不被连带封掉。"""
        self.chart = chart_fixture(split=True)
        self.chart.transfers = [dict(id='lift-a', entry=[112, 96], exit=[256, 96], directed=False),
                                dict(id='lift-b', entry=[136, 96], exit=[280, 96], directed=True)]
        self.cut = CutRoads(self.chart)
        goal = self.absorb(snapshot_fixture(enemies=[self.enemy]))
        self.assertEqual(goal['kind'], 'elevator')
        self.assertEqual(goal['id'], 'lift-a')
        self.state.transfer_failed(goal['leg'])
        alternate = choose_goal(self.state, self.chart, self.snapshot, self.cut)
        self.assertEqual(alternate['id'], 'lift-b')
        annotations = available_annotations(self.state, self.chart)
        ends = {item['id']: item['points'][0] for item in annotations['objects']}
        reverse = [link for link in annotations['connections'] if link['id'] == 'lift-a']
        self.assertEqual(len(reverse), 1)
        self.assertEqual(ends[reverse[0]['from']], [256, 96])
        self.state.tick += COOLDOWN_TICKS
        self.assertTrue(self.state.transfer_available(goal['leg']))

    def test_transfer_fails_three_times_then_stays_unavailable(self):
        """重复失败不无限重试，成功确认出口后才清掉失败记录。"""
        leg = dict(connection='lift', entry=[112, 96], exit=[256, 96])
        for _ in range(3):
            self.state.transfer_failed(leg)
            self.state.tick += COOLDOWN_TICKS
        self.assertFalse(self.state.transfer_available(leg))
        self.state.transfer_succeeded(leg)
        self.assertTrue(self.state.transfer_available(leg))

    def test_tried_mechanisms_do_not_hide_later_candidates(self):
        """前八个已试过不能挡住第九个有效机关，候选预算在过滤之后计算。"""
        self.chart.points = [dict(id=f'switch-{index}', category='ground_mechanism', position=[80, 136 + index])
                             for index in range(9)]
        self.absorb(snapshot_fixture(blocks=[self.block], enemies=[self.enemy]))
        for index in range(8):
            self.state.mark_tried(f'switch-{index}', 'block-1')
        self.state.pending_reviews.clear()
        goal = choose_goal(self.state, self.chart, self.snapshot, self.cut)
        self.assertEqual(goal['id'], 'switch-8')


class SessionTests(unittest.TestCase):
    """以假设备覆盖输入闸门、取消、窗口变化、弹窗与只释放不关闭驱动。"""

    def setUp(self):
        """所有 Win32、截图、输入与 OCR 都是 Mock，测试不会访问真实窗口。"""
        chart = chart_fixture()
        self.cut = CutRoads(chart)
        self.cut.update([])
        self.handler = Mock(_failures=0)
        device = Mock(input_handler=self.handler, image=np.zeros((999, 1776, 3), np.uint8))
        device.get_current_window_hwnd.return_value = 123
        device.client_screen_rect.return_value = (10, 20, 1786, 1019)
        self.stop = Mock()
        self.stop.is_set.return_value = False
        self.owner = SimpleNamespace(device=device, config=SimpleNamespace(stop_event=self.stop,
                                    task=SimpleNamespace(command='auto_main_story')),
                                    current_chapter=41, current_difficulty='normal', cut=self.cut,
                                    observer=SimpleNamespace(matcher=Mock(), package=Path('unused')), chapter_ocr=Mock(), last_snapshot=None,
                                    reopen_minimap_if_closed=Mock(), collapse_minimap=Mock(return_value=True),
                                    expand_minimap=Mock(return_value=True), minimap_expanded=Mock(return_value=False))
        self.session = CampaignSession(self.owner)
        self.session.win.gui = Mock()
        self.session.win.gui.GetClientRect.return_value = (0, 0, 1776, 999)
        self.session.win.gui.GetForegroundWindow.return_value = 123
        self.session.win.origin, self.session.win.handle = (10, 20), 123
        for target, value in (('module.campaign.session.goto.battle_popup_score', 0),
                              ('module.campaign.session.chapter_identity', (41, 'normal'))):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session.preview = Mock()

    def test_move_converts_client_to_screen_once(self):
        """点击只加一次客户区原点，且新输入经过预算计数。"""
        self.session.move([100, 200])
        self.handler.mouse_click.assert_called_once_with(110, 220)
        self.assertEqual(self.session.inputs, 1)

    def test_invalid_clicks_send_nothing(self):
        """非有限或越界点在驱动前被拦截。"""
        for point in ([np.nan, 1], [-1, 1], [1776, 1], [1]):
            with self.assertRaises(ValueError):
                self.session.move(point)
        self.handler.mouse_click.assert_not_called()

    def test_cancel_and_focus_loss_allow_only_release(self):
        """停止或失焦不发新输入，mouse_up 仍可用于清理。"""
        self.stop.is_set.return_value = True
        with self.assertRaises(TaskEnd):
            self.session.move([100, 200])
        self.session.close()
        self.handler.mouse_click.assert_not_called()
        self.handler.mouse_up.assert_called_once()
        self.owner.device.close.assert_not_called()

    def test_window_geometry_handle_and_focus_changes_stop(self):
        """窗口原点、句柄、客户区和前台任何一项变化都不可继续移动。"""
        cases = [(self.owner.device.client_screen_rect, (30, 20, 1806, 1019)),
                 (self.owner.device.get_current_window_hwnd, 456),
                 (self.session.win.gui.GetClientRect, (0, 0, 1280, 720)),
                 (self.session.win.gui.GetForegroundWindow, 456)]
        for method, value in cases:
            with self.subTest(value=value):
                previous = method.return_value
                method.return_value = value
                with self.assertRaises(WindowChanged):
                    self.session.move([100, 200])
                method.return_value = previous
        self.handler.mouse_click.assert_not_called()

    def test_identity_checked_at_start_not_per_gesture(self):
        """身份只在会话开始时核对一次；之后每个手势不再读章节号，只留弹窗与收集品闸门。"""
        with patch('module.campaign.session.chapter_identity', return_value=(42, 'normal')):
            with self.assertRaises(WindowChanged):
                self.session.start()
        with patch('module.campaign.session.chapter_identity', return_value=(41, 'normal')) as reader:
            self.session.move([100, 200])
        reader.assert_not_called()
        self.handler.mouse_click.assert_called_once_with(110, 220)

    def test_input_budget_and_driver_failure_stop(self):
        """超过执行时限或单目标预算都不发送；驱动第一次失败即停止，不等待累计第三次。"""
        self.session.deadline = 0
        with self.assertRaises(InputBudgetExceeded):
            self.session.move([100, 200])
        self.session.deadline = None
        self.session.target_inputs = 32
        with self.assertRaises(InputBudgetExceeded):
            self.session.move([100, 200])
        self.handler.mouse_click.assert_not_called()
        self.session.target_inputs = 0
        self.handler._failures = 1
        with self.assertRaises(WindowChanged):
            self.session.move([100, 200])
        self.handler.mouse_click.assert_called_once()

    def test_capture_uses_device_and_preserves_bgr(self):
        """共享截图入口使用 Device，不退回桌面 ImageGrab；输出颜色通道正确。"""
        self.owner.device.image[:, :, 0] = 17
        from module.campaign.navigation.controls import capture_client
        image = capture_client(self.session.win)
        self.assertEqual(image[0, 0, 2], 17)
        self.owner.device.screenshot.assert_called_once()

    def test_battle_popup_is_terminal_and_not_entered(self):
        """普通战斗弹窗停留原地，任何目标用途均不点击进入战斗。"""
        with patch('module.campaign.session.goto.battle_popup_score', return_value=.99), \
                patch('module.campaign.session.cv2.matchTemplate', return_value=np.zeros((1, 1), np.float32)):
            with self.assertRaises(TargetTriggered) as triggered:
                self.session.move([100, 200])
        self.assertEqual(triggered.exception.evidence['kind'], 'battle_popup')
        self.handler.mouse_click.assert_not_called()

    def test_ex_popup_closed_once_then_terminal(self):
        """EX 仅关闭一次，确认关闭后抛终态；不会继续地面移动。"""
        with patch('module.campaign.session.goto.battle_popup_score', side_effect=[.99, 0]), \
                patch('module.campaign.session.cv2.matchTemplate', return_value=np.ones((1, 1), np.float32)), \
                patch.object(self.session, 'pause'):
            with self.assertRaises(TargetTriggered) as triggered:
                self.session.identity(self.owner.device.image)
        self.assertEqual(triggered.exception.evidence['kind'], 'ex_stage_skipped')
        self.handler.mouse_click.assert_called_once_with(1165, 541)

    def test_collectible_indicator_blocks_all_new_input(self):
        """收集任务即便在执行探索，看到提示后也不再发镜头手势或拾取点击。"""
        self.session.collectible_mode = True
        with patch('module.campaign.session.collectible_indicator', return_value={'kind': 'indicator'}):
            with self.assertRaises(TargetTriggered):
                self.session.win.handler.mouse_swipe((100, 100), (200, 200))
        self.handler.mouse_swipe.assert_not_called()

    def test_target_trigger_during_expanded_observation_sends_no_cleanup_input(self):
        """展开观测中遇终态后不再点最小化，避免退出后补发输入。"""
        self.session.win.reset_minimap = Mock()
        self.session.identity = Mock(side_effect=[None, TargetTriggered('stop', {'kind': 'battle_popup'})])
        with self.assertRaises(TargetTriggered):
            self.session.snapshot(expanded=True)
        self.session.win.reset_minimap.assert_called_once_with(expanded=True, reset=True)

    def test_localization_retry_expands_pans_and_reconfirms(self):
        """紧凑帧定位未被接受时展开面板换视野重试，过线后收回紧凑地图。"""
        failed = snapshot_fixture()
        failed.update(localization='needs_review', squad=None)
        self.owner.observer.observe = Mock(side_effect=[failed, failed, snapshot_fixture()])
        self.session.win.reset_minimap = Mock()
        self.session.pause = Mock()
        snapshot = self.session.snapshot()
        self.assertEqual(snapshot['localization'], 'accepted')
        self.assertEqual(self.owner.observer.observe.call_count, 3)
        self.handler.mouse_swipe.assert_called_once()
        self.assertEqual(self.session.win.reset_minimap.call_args_list,
                         [call(expanded=False, reset=False), call(expanded=True, reset=True),
                          call(expanded=False, reset=False)])

    def test_localization_retry_gives_up_and_restores_compact_map(self):
        """连续换视野仍不过线时不再多试，收回紧凑地图并把未接受的快照交回决策层。"""
        failed = snapshot_fixture()
        failed.update(localization='needs_review', squad=None)
        self.owner.observer.observe = Mock(return_value=failed)
        self.session.win.reset_minimap = Mock()
        self.session.pause = Mock()
        snapshot = self.session.snapshot()
        self.assertEqual(snapshot['localization'], 'needs_review')
        self.assertEqual(self.owner.observer.observe.call_count, 1 + LOCALIZE_RETRIES)
        self.assertEqual(self.handler.mouse_swipe.call_count, LOCALIZE_RETRIES - 1)
        self.assertEqual(self.session.win.reset_minimap.call_args_list[-1], call(expanded=False, reset=False))

    def test_expanded_observe_pans_before_reporting_failure(self):
        """移动闭环的展开观测配准失败时换视野重试，全部失败才抛回最后一次错误。"""
        self.owner.state = RunState(41, 'normal', (384, 256))
        self.session.remember_scene = Mock()
        self.session.localizer.locate = Mock(side_effect=RuntimeError('配准未被接受'))
        self.session.win.capture = Mock(return_value=np.zeros((462, 486, 3), np.uint8))
        self.session.pause = Mock()
        with self.assertRaises(RuntimeError):
            self.session.observe()
        self.assertEqual(self.session.localizer.locate.call_count, LOCALIZE_RETRIES + 1)
        self.assertEqual(self.handler.mouse_swipe.call_count, LOCALIZE_RETRIES)

    def test_movement_feedback_refreshes_curtains_before_next_route(self):
        """移动途中新看到的光幕写进状态，下一次路径检查不能再穿过它。"""
        self.owner.state = RunState(41, 'normal', (384, 256))
        block = dict(point=[200, 100], segment=[[200, 60], [200, 140]])
        self.owner.observer.observe = Mock(return_value=snapshot_fixture(blocks=[block]))
        self.session.remember_scene(self.owner.device.image)
        self.assertFalse(self.cut.connected([40, 96], [320, 96]))
        with self.assertRaises(RuntimeError):
            self.session.localizer.route([40, 96], [320, 96])

    def test_localizer_rejects_cross_curtain_before_astar(self):
        """导航与目标选择都使用切开图，不把被封锁点吸附到另一侧道路。"""
        self.cut.update([dict(id='block', segment=[[200, 60], [200, 140]])])
        localizer = CampaignLocalizer(Mock(), self.cut)
        with self.assertRaises(RuntimeError):
            localizer.route([40, 96], [320, 96])

    def test_reset_failure_does_not_continue_to_expand(self):
        """回正前收起失败必须终止，不能把旧视野当成小队居中。"""
        self.owner.minimap_expanded.return_value = True
        self.owner.collapse_minimap.return_value = False
        with self.assertRaises(WindowChanged):
            self.session.win.reset_minimap(expanded=True, reset=True)
        self.owner.expand_minimap.assert_not_called()

    def test_small_map_controls_receive_cancel_and_budget_callbacks(self):
        """窗口开合也走生产会话闸门与可取消等待，不只是场景点击受保护。"""
        self.session.win.reset_minimap(expanded=True)
        for name in ('reopen_minimap_if_closed', 'expand_minimap'):
            method = getattr(self.owner, name)
            method.assert_called_once_with(check=self.session.before_input, pause=self.session.pause)

    def test_battle_mode_lets_popup_click_through(self):
        """接管中准备弹窗不再是终态（要自己点它）；接管之外仍然停止。"""
        with patch('module.campaign.session.goto.battle_popup_score', return_value=.99):
            with self.assertRaises(TargetTriggered):
                self.session.popup(self.owner.device.image)
            self.session.battle_mode = True
            self.assertIsNone(self.session.popup(self.owner.device.image))
        self.handler.mouse_click.assert_not_called()

    def test_battle_step_taps_the_state_button(self):
        """每一步只按识别结果点一次：快速战斗／进入战斗、结算推进、战败返回、战败后只关弹窗。"""
        self.session.tap = Mock()
        cases = [(dict(state='entry', quick=True), False, QUICK_BATTLE_POINT),
                 (dict(state='entry', quick=False), False, ENTER_BATTLE_POINT),
                 (dict(state='entry', quick=True), True, ENTRY_CLOSE_POINT),
                 (dict(state='victory'), False, ADVANCE_POINT),
                 (dict(state='defeat'), False, DEFEAT_BACK_POINT)]
        for state, close_entry, point in cases:
            with self.subTest(state=state):
                self.session.tap.reset_mock()
                with patch('module.campaign.vision.battle.battle_state',
                           return_value=dict(state, scores={})):
                    result = self.session.battle_step(close_entry=close_entry)
                self.assertEqual(result['state'], state['state'])
                self.session.tap.assert_called_once_with(point)
        for state in ('battle', 'field', 'unknown'):
            with self.subTest(state=state):
                self.session.tap.reset_mock()
                with patch('module.campaign.vision.battle.battle_state',
                           return_value={'state': state, 'scores': {}}):
                    self.session.battle_step()
                self.session.tap.assert_not_called()

    def test_post_battle_frame_reuses_previous_squad_once(self):
        """战斗后第一帧定位未接受时沿用战斗前坐标，只沿用一帧，之后仍走换视野重试。"""
        self.owner.observer.observe = Mock(return_value=snapshot_fixture())
        self.session.win.reset_minimap = Mock()
        self.session.pause = Mock()
        self.session.snapshot()
        self.assertEqual(self.session.last_registration['squad'], [40, 96])

        failed = snapshot_fixture()
        failed.update(localization='needs_review', squad=None)
        self.owner.observer.observe = Mock(return_value=failed)
        self.session.reuse_squad = True
        snapshot = self.session.snapshot()
        self.assertEqual(snapshot['localization'], 'reused')
        self.assertEqual(snapshot['squad'], [40, 96])
        self.assertFalse(self.session.reuse_squad)

        reused = self.session.snapshot()
        self.assertEqual(reused['localization'], 'needs_review')
        self.assertGreaterEqual(self.handler.mouse_swipe.call_count, 1)

    def test_localizer_validates_geometry_and_registration(self):
        """原始展开 ROI 之外的尺寸及未接受的配准均不可用于点击换算。"""
        localizer = CampaignLocalizer(Mock(), self.cut)
        with self.assertRaises(ValueError):
            localizer.locate(np.zeros((100, 100, 3), np.uint8), 'test')
        panel = np.zeros((462, 486, 3), np.uint8)
        localizer.matcher.match_panel.return_value = {'status': 'needs_review'}
        with patch('module.campaign.session.detect_markers', return_value=([[243, 231]], [])):
            with self.assertRaises(RuntimeError):
                localizer.locate(panel, 'test')
            localizer.matcher.match_panel.return_value = dict(status='accepted', road_iou=.9, position=[112, 96],
                                                            roi_to_map=np.eye(3).tolist())
            report = localizer.locate(panel, 'test')
            self.assertEqual(report['position_kind'], 'squad')
            self.assertEqual(report['player_roi'], [243, 231])


class ExecutorTests(unittest.TestCase):
    """注入导航结果覆盖子目标回写、终态中断与必经 finally 释放。"""

    def setUp(self):
        """执行层使用假会话，只有纯 RunState 是真实实现。"""
        self.owner = SimpleNamespace(state=RunState(41, 'normal', (384, 256)), plan_tick=Mock())
        self.session = Mock()
        self.executor = CampaignExecutor(self.owner, self.session)

    def test_mechanism_arrival_records_pending_review(self):
        """只有到点成功才记机关，下一拍必须复核光幕。"""
        goal = dict(kind='mechanism', id='switch', position=[80, 176], block='block', source='annotation')
        with patch('module.campaign.execution.navigate', return_value={'state': 'arrived'}):
            self.executor.execute(goal)
        self.assertTrue(self.owner.state.tried_for('switch', 'block'))
        self.assertEqual(self.owner.state.pending_reviews['block'], 'switch')

    def test_failed_mechanism_is_not_marked_tried(self):
        """没有到点不能凭点击过就宣称踩过机关。"""
        goal = dict(kind='mechanism', id='switch', position=[80, 176], block='block', source='annotation')
        with patch('module.campaign.execution.navigate', side_effect=RuntimeError('stalled')):
            self.executor.execute(goal)
        self.assertFalse(self.owner.state.tried_for('switch', 'block'))
        self.assertTrue(self.owner.state.cooling('switch'))

    def test_elevator_failure_is_written_back(self):
        """入口停稳却没传送的失败回写到航段，不反复使用相同连接。"""
        leg = dict(connection='lift', entry=[112, 96], exit=[256, 96])
        goal = dict(kind='elevator', id='lift', position=leg['entry'], leg=leg)
        with patch('module.campaign.execution.navigate', side_effect=RuntimeError('not transferred')):
            self.executor.execute(goal)
        self.assertFalse(self.owner.state.transfer_available(leg))

    def test_arrival_without_transfer_evidence_does_not_clear_failure(self):
        """后来道路可步行到出口时，到点不能冒充电梯已经执行。"""
        leg = dict(connection='lift', entry=[112, 96], exit=[256, 96])
        goal = dict(kind='elevator', id='lift', position=leg['entry'], leg=leg)
        self.owner.state.transfer_failed(leg)
        with patch('module.campaign.execution.navigate', return_value={'state': 'arrived'}):
            self.executor.execute(goal)
        self.assertFalse(self.owner.state.transfer_available(leg))

    def test_confirmed_transfer_clears_failure(self):
        """共享闭环带出口确认的成功结果才清掉电梯失败记录。"""
        leg = dict(connection='lift', entry=[112, 96], exit=[256, 96])
        goal = dict(kind='elevator', id='lift', position=leg['entry'], leg=leg)
        self.owner.state.transfer_failed(leg)

        def finish(session, target, emit, **kwargs):
            """假导航发布实际出口证据，再返回到点。"""
            emit(elevator_transfers=[leg])
            return {'state': 'arrived'}

        with patch('module.campaign.execution.navigate', side_effect=finish):
            self.executor.execute(goal)
        self.assertTrue(self.owner.state.transfer_available(leg))

    def test_fatal_window_and_budget_errors_not_penalized_as_targets(self):
        """安全错误直达任务停止，不换一个目标继续点击。"""
        goal = dict(kind='enemy', id='enemy', position=[112, 96])
        for error in (WindowChanged('lost focus'), InputBudgetExceeded('budget')):
            with patch('module.campaign.execution.navigate', side_effect=error):
                with self.assertRaises(type(error)):
                    self.executor.execute(goal)
        self.assertEqual(self.owner.state.cooldowns, {})

    def test_terminal_popup_stops_loop_and_releases(self):
        """EX 弹窗与收集品提示这类终态仍然停止循环并释放会话，不走战斗接管。"""
        goal = dict(kind='enemy', id='enemy-1', position=[112, 96])
        self.owner.state.enemies['enemy-1'] = {'status': 'pending'}
        self.owner.plan_tick.return_value = goal
        with patch.object(self.executor, 'needs_expansion', return_value=False), \
                patch.object(self.executor, 'scout', return_value=False), \
                patch.object(self.executor, 'battle') as battle, \
                patch.object(self.executor, 'execute',
                             side_effect=TargetTriggered('ex', {'kind': 'ex_stage_skipped'})):
            result = self.executor.run(snapshot_fixture())
        self.assertEqual(result['state'], 'triggered')
        self.owner.plan_tick.assert_called_once()
        battle.assert_not_called()
        self.session.close.assert_called_once()

    def test_battle_popup_is_taken_over_and_loop_continues(self):
        """普通战斗弹窗由执行层接管，回到野外后继续下一拍而不是终结任务。"""
        goal = dict(kind='enemy', id='enemy-1', position=[112, 96])
        self.owner.state.enemies['enemy-1'] = {'status': 'pending'}
        self.owner.plan_tick.return_value = goal
        self.session.snapshot.return_value = snapshot_fixture()
        with patch.object(self.executor, 'needs_expansion', return_value=False), \
                patch.object(self.executor, 'scout', return_value=False), \
                patch.object(self.executor, 'battle') as battle, \
                patch.object(self.executor, 'execute',
                             side_effect=TargetTriggered('popup', {'kind': 'battle_popup'})):
            result = self.executor.run(snapshot_fixture(), ticks=2)
        self.assertEqual(result['reason'], 'tick_budget')
        self.assertEqual(battle.call_count, 2)
        self.session.close.assert_called_once()

    def test_battle_victory_marks_target_cleared(self):
        """胜利把该敌人记为 cleared、清零连续战败，并打开战后沿用坐标的许可。"""
        goal = dict(kind='enemy', id='enemy-1', position=[112, 96])
        self.owner.state.enemies['enemy-1'] = {'status': 'pending'}
        self.session.battle_step.side_effect = [dict(state='entry', quick=True), dict(state='battle'),
                                                dict(state='victory'), dict(state='field')]
        self.assertEqual(self.executor.battle(goal), 'victory')
        self.assertEqual(self.owner.state.enemies['enemy-1']['status'], 'cleared')
        self.assertEqual(self.executor.battle_defeats, 0)
        self.assertTrue(self.session.reuse_squad)
        self.assertFalse(self.session.battle_mode)
        self.session.begin_target.assert_any_call('battle')

    def test_battle_defeat_cools_target_then_stops_at_limit(self):
        """战败给目标记冷却并计数；连续到上限抛安全异常交人工，不当作普通目标失败。"""
        goal = dict(kind='enemy', id='enemy-1', position=[112, 96])
        self.owner.state.enemies['enemy-1'] = {'status': 'pending'}
        steps = [dict(state='entry', quick=False), dict(state='defeat'), dict(state='field')]
        self.session.battle_step.side_effect = list(steps)
        self.assertEqual(self.executor.battle(goal), 'defeat')
        self.assertTrue(self.owner.state.cooling('enemy-1'))
        self.assertEqual(self.executor.battle_defeats, 1)
        self.session.battle_step.side_effect = list(steps)
        with self.assertRaises(InputBudgetExceeded):
            self.executor.battle(goal)
        self.assertEqual(self.executor.battle_defeats, BATTLE_DEFEAT_LIMIT)

    def test_battle_unknown_screen_advances_then_stops(self):
        """连续认不出画面时按上限点几次推进兜底，仍认不出就停下交人工。"""
        goal = dict(kind='enemy', id='enemy-1', position=[112, 96])
        self.session.battle_step.return_value = dict(state='unknown')
        with self.assertRaises(RuntimeError):
            self.executor.battle(goal)
        self.assertEqual(self.session.tap.call_count, BATTLE_ADVANCE_LIMIT)
        self.assertGreaterEqual(self.session.battle_step.call_count, BATTLE_UNKNOWN_LIMIT)

    def test_cancel_during_start_still_releases(self):
        """开始阶段失败也走 finally，不能遗留按下鼠标。"""
        self.session.start.side_effect = TaskEnd
        with self.assertRaises(TaskEnd):
            self.executor.run(snapshot_fixture())
        self.session.close.assert_called_once()
        self.session.snapshot.assert_not_called()

    def test_expansion_happens_before_frontier_move(self):
        """前沿探索先展开补视野，再以新紧凑帧重选，而不是沿旧目标直接走。"""
        frontier = dict(kind='explore', id='cell', position=[112, 96])
        enemy = dict(kind='enemy', id='enemy', position=[136, 96])
        self.owner.plan_tick.side_effect = [frontier, frontier, enemy]
        self.session.snapshot.return_value = snapshot_fixture()
        with patch.object(self.executor, 'execute') as execute, \
                patch.object(self.executor, 'scout', return_value=False):
            result = self.executor.run(snapshot_fixture(), ticks=1)
        self.assertEqual(result['reason'], 'tick_budget')
        self.session.snapshot.assert_any_call(expanded=True)
        execute.assert_called_once_with(enemy)

    def test_review_untrusted_frame_keeps_pending(self):
        """光幕没可靠落图时，不能消耗机关的待验证结果。"""
        self.owner.state.blocks['block'] = dict(status='stale', point=[200, 100])
        self.owner.state.mark_tried('switch', 'block')
        self.session.snapshot.return_value = snapshot_fixture(trusted=False)
        self.executor.review_block({'id': 'block'})
        self.assertEqual(self.owner.state.pending_reviews['block'], 'switch')
        self.assertTrue(self.owner.state.cooling('review-block'))

    def test_shared_navigation_confirms_arrival_without_click(self):
        """真正的共享 navigate 连续确认三次到点，不追加移动点击。"""
        session = Mock()
        session.observe.return_value = dict(position=[112, 96], position_kind='squad', iou=.9)
        result = navigate(session, [112, 96], Mock(), max_moves=2)
        self.assertEqual(result['state'], 'arrived')
        self.assertEqual(session.observe.call_count, 3)
        session.move.assert_not_called()

    def test_shared_navigation_confirms_elevator_exit_twice(self):
        """通过共享导航验证两次出口确认后才完成传送，不靠入口点击次数猜成功。"""
        from module.campaign.subgoals import RoadView
        chart = chart_fixture(split=True)
        cut = CutRoads(chart)
        cut.update([])
        leg = dict(connection='lift', entry=[112, 96], exit=[256, 96])
        session = Mock(localizer=RoadView(cut))
        positions = [[112, 96], [256, 96], [256, 96], [256, 96], [256, 96], [256, 96]]
        session.observe.side_effect = [dict(position=point, position_kind='squad', iou=.9) for point in positions]
        emit = Mock()
        result = navigate(session, leg['exit'], emit, elevators=single_transfer_annotations(leg))
        self.assertEqual(result['state'], 'arrived')
        self.assertEqual(session.observe.call_count, 6)
        session.move.assert_not_called()
        transfers = [call.kwargs['elevator_transfers'] for call in emit.call_args_list
                     if call.kwargs.get('elevator_transfers')]
        self.assertEqual(len(transfers), 1)

    def test_production_import_does_not_load_capture_tools(self):
        """新进程加载生产入口不会反向导入 dev_tools，也不会初始化采集器驱动。"""
        code = ('import sys; from module.campaign.campaign import Campaign; '
                "assert not any(name.startswith(('dev_tools', 'module.campaign.tools')) for name in sys.modules)")
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)

    def test_shared_session_finish_view_uses_core_controls(self):
        """共用会话收尾使用核心控件，不从实验 CLI 导入地图开合函数。"""
        from module.campaign.navigation.session import NavigationSession
        session = Mock()
        field = np.zeros((999, 1776, 3), np.uint8)
        with (
            patch('module.campaign.navigation.controls.map_close') as close_map,
            patch('module.campaign.navigation.controls.capture_client', return_value=field),
        ):
            NavigationSession.finish_view(session)
        close_map.assert_called_once_with(session.win)
        session.preview.assert_called_once_with(field)

    def test_import_all_core_modules_without_experiment_dependencies(self):
        """地图、视觉及导航模块均能独立导入，不反向依赖实验工具和采集器。"""
        code = (
            'import importlib, pathlib, sys; '
            "paths = [p for p in pathlib.Path('module/campaign').rglob('*.py') if 'tools' not in p.parts]; "
            "[importlib.import_module(p.as_posix()[:-3].replace('/', '.')) for p in paths]; "
            "assert not any(name.startswith(('dev_tools', 'module.campaign.tools')) for name in sys.modules)"
        )
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)

    def test_reorganized_resource_paths_use_workspace_root(self):
        """目录多一层后仍从仓库根找运行资源与实验数据，模板只存在于 vision。"""
        from module.campaign.map.paths import DEFAULT_MAPS_ROOT
        from module.campaign.navigation.controls import ASSETS
        from module.campaign.tools import settings
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(DEFAULT_MAPS_ROOT, root / 'data/resources/maps')
        self.assertEqual(settings.ROOT, root)
        self.assertEqual(settings.package, root / 'data/campaign_prototype/chapter_38')
        self.assertEqual(ASSETS, root / 'module/campaign/vision/assets')
        self.assertTrue((ASSETS / 'calibration.json').is_file())


class CampaignEntryTests(unittest.TestCase):
    """准备流程和正式执行入口的集成回归；所有设备与会话构造均被替换。"""

    def setUp(self):
        """跳过 ModuleBase 的设备构造，用假准备结果验证 run 的接线。"""
        from module.campaign.campaign import Campaign
        self.campaign = Campaign.__new__(Campaign)
        self.campaign.config = SimpleNamespace(task=SimpleNamespace(command='auto_main_story'))
        self.campaign.current_chapter, self.campaign.current_difficulty = 41, 'normal'
        for name in ('ensure_resources', 'prepare_client', 'check_main_story_page', 'log_snapshot_summary'):
            setattr(self.campaign, name, Mock())
        self.campaign.detect_current_chapter = Mock(return_value=41)
        self.campaign.confirm_squad_location = Mock(return_value=snapshot_fixture())
        self.campaign.ensure_chart = Mock(return_value=chart_fixture())

    def test_run_uses_executor_instead_of_dry_run(self):
        """正式任务接上执行器；没有偷偷保留只打日志的空跑路径。"""
        self.campaign.dry_run = Mock()
        with (
            patch('module.campaign.session.CampaignSession') as session_class,
            patch('module.campaign.execution.CampaignExecutor') as executor_class,
        ):
            executor_class.return_value.run.return_value = {'state': 'triggered'}
            self.assertEqual(self.campaign.run(), 41)
        self.campaign.dry_run.assert_not_called()
        session_class.assert_called_once_with(self.campaign)
        executor_class.assert_called_once_with(self.campaign, session_class.return_value, purpose='story')

    def test_no_trusted_start_does_not_create_control_session(self):
        """起点不可信先停止，不能构造会话并抢输入焦点。"""
        from module.exception import RequestHumanTakeover
        self.campaign.confirm_squad_location.return_value = snapshot_fixture()
        self.campaign.confirm_squad_location.return_value['localization'] = 'needs_review'
        with patch('module.campaign.session.CampaignSession') as session_class:
            with self.assertRaises(RequestHumanTakeover):
                self.campaign.run()
        session_class.assert_not_called()

    def test_collectible_entry_passes_distinct_purpose(self):
        """收集任务不误用敌人优先级，入口用途明确传给执行器。"""
        self.campaign.config.task.command = 'auto_main_collectible'
        with (
            patch('module.campaign.session.CampaignSession'),
            patch('module.campaign.execution.CampaignExecutor') as executor_class,
        ):
            executor_class.return_value.run.return_value = {'state': 'triggered'}
            self.campaign.run()
        self.assertEqual(executor_class.call_args.kwargs['purpose'], 'collectible')

    def test_safety_failure_is_reported_as_human_takeover(self):
        """预算或窗口变化走既有调度器人工接管路径，不报成功。"""
        from module.exception import RequestHumanTakeover
        with (
            patch('module.campaign.session.CampaignSession'),
            patch('module.campaign.execution.CampaignExecutor') as executor_class,
        ):
            executor_class.return_value.run.side_effect = InputBudgetExceeded('budget')
            with self.assertRaises(RequestHumanTakeover):
                self.campaign.run()


if __name__ == '__main__':
    unittest.main()
