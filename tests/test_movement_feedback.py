import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.campaign_prototype.manual_move import navigate, GameSession
from module.campaign_prototype.arrow_anchor import ArrowUnavailable
from module.campaign_prototype.movement_feedback import (
    scene_anchor, collectible_counter, collectible_indicator, TargetTriggered, resolve_anchor, SquadPositionChanged,
)
from module.campaign_prototype.surface_motion import fit_calibration


class FeedbackTests(unittest.TestCase):
    def test_squad_change_discards_plan_and_reobserves_after_waiting(self):
        for purpose in ('position', 'collectible'):
            session = Mock()
            positions = ([0, 0], [40, 0], [100, 0]) if purpose == 'position' else ([100, 0], [40, 0], [100, 0])
            session.observe.side_effect = [dict(position=p, position_kind='squad', iou=.95) for p in positions]
            session.plan.side_effect = [SquadPositionChanged(positions[0], [30, 0]), np.array([800, 500])]
            session.wait_stopped.side_effect = [None, TargetTriggered('done', {})]
            with self.subTest(purpose=purpose), self.assertRaises(TargetTriggered):
                navigate(session, [100, 0], Mock(), purpose=purpose)
            self.assertEqual(session.observe.call_count, 2)
            self.assertEqual(session.wait_stopped.call_count, 2)
            session.move.assert_called_once()
            self.assertEqual(session.plan.call_args.args[0]['position'], [40, 0])
            self.assertIsNone(session.anchor_reference)
            self.assertIsNone(session.anchor_reference_method)
            calls = [call[0] for call in session.mock_calls]
            self.assertLess(calls.index('wait_stopped'), calls.index('move'))

    def test_repeated_squad_changes_stop_without_movement_clicks(self):
        session = Mock()
        session.observe.return_value = dict(position=[0, 0], position_kind='squad', iou=.95)
        session.plan.side_effect = SquadPositionChanged([0, 0], [30, 0])
        with self.assertRaisesRegex(RuntimeError, '两次重新定位'):
            navigate(session, [100, 0], Mock())
        self.assertEqual(session.plan.call_count, 3)
        self.assertEqual(session.wait_stopped.call_count, 2)
        session.move.assert_not_called()

    def test_recovery_wait_failure_or_collectible_stops_before_replanning(self):
        for error in (KeyboardInterrupt('cancel'), RuntimeError('identity'), TargetTriggered('indicator', {})):
            session = Mock()
            session.observe.return_value = dict(position=[0, 0], position_kind='squad', iou=.95)
            session.plan.side_effect = SquadPositionChanged([0, 0], [30, 0])
            session.wait_stopped.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                navigate(session, [100, 0], Mock())
            session.observe.assert_called_once()
            session.plan.assert_called_once()
            session.move.assert_not_called()

    def recorded_field(self, index):
        root = Path(__file__).parent / 'fixtures/movement_feedback'
        field = np.zeros((999, 1776, 3), np.uint8)
        field[310:650, 750:1100] = cv2.imread(str(root / f'ch48_scene_{index:03}.png'))
        field[80:290, 18:230] = cv2.imread(str(root / f'ch48_compact_{index:03}.png'))
        return field

    def test_recorded_indicator_detects_prompt_not_squad_arrow_or_counter(self):
        for index in range(4, 12):
            with self.subTest(index=index):
                result = collectible_indicator(self.recorded_field(index))
                if index < 6:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result['kind'], 'collectible_indicator')
        self.assertIsNone(collectible_indicator(np.zeros((999, 1776, 3), np.uint8)))

    def test_indicator_stops_before_ocr_and_only_for_collectible(self):
        field = self.recorded_field(6)
        for purpose in ('position', 'enemy', 'collectible'):
            session = SimpleNamespace(request={'purpose': purpose}, model=Mock(), preview=Mock())
            if purpose == 'collectible':
                with self.assertRaises(TargetTriggered) as caught:
                    GameSession.check_collectible(session, field)
                self.assertEqual(caught.exception.evidence['kind'], 'collectible_indicator')
                session.preview.assert_called_once()
            else:
                GameSession.check_collectible(session, field)
                session.preview.assert_not_called()
            session.model.predict.assert_not_called()

    def test_indicator_stops_before_observation_or_movement_input(self):
        from module.campaign_prototype.parallax_movement import ParallaxSessionMixin
        for operation in (GameSession.observe, ParallaxSessionMixin.observe, GameSession.move):
            session = SimpleNamespace(request={'purpose': 'collectible'}, model=Mock(), preview=Mock(),
                                      check=Mock(), identity=Mock(), win=Mock())
            session.check_collectible = lambda field: GameSession.check_collectible(session, field)
            with self.subTest(operation=operation.__qualname__), patch(
                    'module.campaign_prototype.goto.capture_client', return_value=self.recorded_field(6)), patch(
                    'module.campaign_prototype.goto.map_open') as open_map:
                with self.assertRaises(TargetTriggered):
                    operation(session, [888, 500]) if operation is GameSession.move else operation(session)
            open_map.assert_not_called()
            session.win.handler.mouse_click.assert_not_called()

    def test_indicator_during_wait_prevents_next_navigation_iteration(self):
        from module.campaign_prototype.camera_navigation import wait_for_squad
        session = Mock()
        session.request = {'purpose': 'collectible'}
        session.observe.return_value = dict(position=[20, 20], position_kind='squad', iou=.95)
        session.plan.return_value = np.array([888, 500])
        session.wait_stopped.side_effect = lambda: wait_for_squad(session)
        session.check_collectible.side_effect = lambda field: GameSession.check_collectible(session, field)
        with patch('module.campaign_prototype.goto.capture_client', return_value=self.recorded_field(6)), patch(
                'module.campaign_prototype.goto.map_open') as open_map, patch(
                'module.campaign_prototype.goto.map_close'):
            with self.assertRaises(TargetTriggered):
                navigate(session, [100, 100], Mock(), purpose='collectible')
        session.move.assert_called_once()
        session.observe.assert_called_once()
        open_map.assert_not_called()

    def test_local_calibration_checks_map_units_with_independent_samples(self):
        points = np.array([[-20, -20], [20, -20], [20, 20], [-20, 20], [0, -20], [0, 20]], float)
        validation = np.array([[-10, -10], [10, -10], [0, 10]], float)
        training = np.stack([points, points * 3], axis=1)
        held_out = np.stack([validation, validation * 3 + [0, 27]], axis=1)
        result = fit_calibration(training, held_out, map_error_limits=(8, 12))
        self.assertAlmostEqual(result['validation_map_max_px'], 9)
        held_out[:, 1, 1] += 12
        with self.assertRaisesRegex(ValueError, 'tolerance'):
            fit_calibration(training, held_out, map_error_limits=(8, 12))

    def test_full_squad_occlusion_recovers_from_surrounding_ground(self):
        rng = np.random.default_rng(42)
        field = rng.integers(0, 255, (999, 1776, 3), dtype=np.uint8)
        field = cv2.GaussianBlur(field, (5, 5), 1)
        moved = cv2.warpAffine(field, np.float32([[1, 0, -10], [0, 1, 5]]), (1776, 999))
        moved[330:548, 792:968] = 0
        anchor = scene_anchor(field, moved, [888, 500], [12, 0])
        np.testing.assert_allclose(anchor, [890, 505], atol=1)
        with self.assertRaises(ValueError):
            scene_anchor(field, np.zeros_like(field), [888, 500], [12, 0])

    def test_minimap_displacement_recovers_only_from_arrow_reference(self):
        from module.campaign_prototype import goto
        rng = np.random.default_rng(42)
        reference = cv2.GaussianBlur(rng.integers(0, 255, (999, 1776, 3), dtype=np.uint8), (5, 5), 1)
        current = cv2.warpAffine(reference, np.float32([[1, 0, -10], [0, 1, 5]]), (1776, 999))
        current[330:548, 792:968] = 0
        baseline = (reference, np.array([888, 500]), np.array([0, 0]))
        for local in (False, True):
            with self.subTest(local=local):
                session = SimpleNamespace(anchor_reference=baseline, anchor_reference_method='arrow_cycle', emit=Mock())
                if local:
                    session.calibration = ({'matrix': np.diag([2, 3, 1])}, reference)
                    delta = [6, 0]
                else:
                    session.localizer = SimpleNamespace(old_matrix=np.eye(3))
                    delta = np.linalg.solve(goto.A_INV, [12, 0])
                observation = dict(position_kind='squad', position=delta,
                                   player_roi=[80, 80], roi_to_map=np.eye(3))
                with patch('module.campaign_prototype.arrow_anchor.sample_ground_anchor',
                           side_effect=ArrowUnavailable('hidden', current)):
                    anchor = resolve_anchor(session, reference, observation)
                np.testing.assert_allclose(anchor, [890, 505], atol=1)
                self.assertEqual(session.emit.call_args.kwargs['anchor_method'], 'minimap_ground_projection')
                self.assertIs(session.anchor_reference, baseline)

    def test_missing_reference_viewport_or_rejected_ground_cannot_supply_anchor(self):
        from pathlib import Path
        field = np.zeros((999, 1776, 3), np.uint8)
        for mode in ('no_reference', 'old_reference', 'viewport', 'no_scene', 'bad_ground'):
            with self.subTest(mode=mode):
                session = SimpleNamespace(emit=Mock(), folder=Path('unused'),
                                          calibration=({'matrix': np.eye(3)}, field))
                if mode != 'no_reference':
                    session.anchor_reference = (field, np.array([888, 500]), np.array([0, 0]))
                if mode != 'old_reference':
                    session.anchor_reference_method = 'arrow_cycle'
                observation = dict(position=[12, 0], position_kind='viewport_center' if mode == 'viewport' else 'squad')
                with patch('module.campaign_prototype.arrow_anchor.sample_ground_anchor',
                           side_effect=ArrowUnavailable('hidden', field)), patch(
                        'module.campaign_prototype.movement_feedback.scene_anchor',
                        side_effect=ValueError('ground rejected')) as recover, patch(
                        'module.campaign_prototype.runtime.write_image') as save:
                    with self.assertRaisesRegex(ValueError, '无法恢复小队箭头位置'):
                        resolve_anchor(session, field, observation, allow_scene=mode != 'no_scene')
                self.assertEqual(recover.call_count, int(mode == 'bad_ground'))
                save.assert_called_once()

    def test_motion_identity_and_cancellation_never_use_recovery(self):
        field = np.zeros((999, 1776, 3), np.uint8)
        for error in (ValueError('moving'), RuntimeError('wrong chapter'), KeyboardInterrupt('stop')):
            with self.subTest(error=type(error)), patch(
                    'module.campaign_prototype.arrow_anchor.sample_ground_anchor', side_effect=error), patch(
                    'module.campaign_prototype.movement_feedback.scene_anchor') as recover:
                session = SimpleNamespace(emit=Mock())
                with self.assertRaises(type(error)):
                    resolve_anchor(session, field, {'position': [0, 0]})
                session.emit.assert_not_called()
                recover.assert_not_called()

    def test_probe_budget_and_blocked_roads_do_not_claim_trigger(self):
        session = Mock()
        session.observe.return_value = dict(position=[100, 100], position_kind='squad', iou=.95)
        session.plan.side_effect = [ValueError('hole'), *[np.array([888, 500])] * 4]
        result = navigate(session, [100, 100], Mock(), purpose='collectible', arrival_radius=20)
        self.assertEqual(result['state'], 'needs_review')
        self.assertEqual(session.move.call_count, 4)
        self.assertEqual(session.plan.call_count, 5)

    def test_counter_feedback_requires_two_confirmations_and_same_total(self):
        session = SimpleNamespace(request={'purpose': 'collectible'}, model=Mock(), preview=Mock())
        field = np.zeros((999, 1776, 3), np.uint8)
        with patch('module.campaign_prototype.movement_feedback.collectible_counter',
                   side_effect=[(0, 14), (1, 6), (1, 14), None, (1, 14), (1, 14)]):
            for _ in range(5):
                GameSession.check_collectible(session, field)
            with self.assertRaises(TargetTriggered):
                GameSession.check_collectible(session, field)
        session.preview.assert_called_once()

    def test_counter_ocr_rejects_noise(self):
        model = Mock()
        field = np.zeros((999, 1776, 3), np.uint8)
        for text, score, expected in [('0/14', .99, (0, 14)), ('3/14', .5, None), ('114', .99, None),
                                      ('15/14', .99, None)]:
            model.predict.return_value = [{'rec_text': text, 'rec_score': score}]
            self.assertEqual(collectible_counter(field, model), expected)


if __name__ == '__main__':
    unittest.main()
