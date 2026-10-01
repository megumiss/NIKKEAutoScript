import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.campaign_prototype.edited_map import edited_roads
from module.campaign_prototype.parallax_localizer import ParallaxLocalizer
from module.campaign_prototype.parallax_movement import (
    checked_segment, calibrate, load_calibration, track_localization, recover_localization,
    load_live_references, save_live_references,
    run_movement,
)
from dev_tools.wiki_collectible_match import MapMatcher
from dev_tools.wiki_collectibles import find_package
from dev_tools.map_movement import MovementJobs
from module.campaign_prototype.live import field_ring


class ParallaxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'source').mkdir()
        (self.root / 'depth').mkdir()
        rng = np.random.default_rng(22)
        base = np.full((180, 240, 3), [186, 139, 59], np.uint8)
        cv2.imwrite(str(self.root / 'map.png'), base)
        texture = cv2.GaussianBlur(rng.random((180, 240)).astype(np.float32), (5, 5), .8)
        texture = .75 + (texture - texture.min()) / (texture.max() - texture.min()) * .6
        base = np.clip(base.astype(float) * texture[:, :, None], 0, 255).astype(np.uint8)
        self.query = base[:, :180].copy()
        digest = hashlib.sha256((self.root / 'map.png').read_bytes()).hexdigest()
        self.metadata = dict(coordinate_model='local_parallax', chapter=40, size=[240, 180],
            image_sha256=digest, projection=np.eye(3).tolist(), rotation=np.eye(2).tolist(),
            origin=[0, 0], vertical_origin=[0, 0], camera=[[0, 0], [50, 0]], source_sha256={}, frame_support=[])
        frames = []
        for i, shift in enumerate([0, 50]):
            name = f'frame_{i:05d}.png'
            cv2.imwrite(str(self.root / 'source' / name), base[:, shift:shift + 180])
            raw = (self.root / 'source' / name).read_bytes()
            self.metadata['source_sha256'][name] = hashlib.sha256(raw).hexdigest()
            frames.append({'file': name})
            np.savez(self.root / 'depth' / f'frame_{i:05d}.npz', local_surface=np.zeros((180, 180), np.int16))
            self.metadata['frame_support'].append({'local_planes': [
                dict(ratio_plane=[0, 0, 1], coordinate_scale=1, center=[0, 0])]})
        (self.root / 'source/scan.json').write_text(json.dumps(dict(frames=frames)))
        (self.root / 'map.json').write_text(json.dumps(self.metadata))
        self.annotation = dict(image_sha256=digest, terrain_edits=[])
        self.save_edits()

    def save_edits(self):
        (self.root / 'annotations.json').write_text(json.dumps(self.annotation))

    def test_two_views_use_same_projection_and_edit_cache_identity(self):
        localizer = ParallaxLocalizer(self.root)
        result = localizer.locate(self.query, [110, 90])
        self.assertEqual(result['status'], 'accepted', result.get('reason'))
        np.testing.assert_allclose(result['position'], [110, 90], atol=1)
        original = localizer.cache_digest
        self.annotation['objects'] = [{'id': 'wiki-point'}]
        self.save_edits()
        self.assertEqual(ParallaxLocalizer(self.root).cache_digest, original)
        self.annotation['terrain_edits'] = [dict(type='brush', operation='erase', width=40, points=[[110, 90]])]
        self.save_edits()
        modified = ParallaxLocalizer(self.root)
        self.assertNotEqual(modified.cache_digest, original)
        self.assertEqual(modified.road[90, 110], 0)
        with patch.object(modified, 'road_proposals', return_value=([], None)):
            self.assertEqual(modified.locate(self.query, [110, 90])['status'], 'needs_review')

    def test_wiki_dispatch_and_corrupt_source_rejected(self):
        self.assertIsInstance(MapMatcher(self.root).surface_matcher, ParallaxLocalizer)
        (self.root / 'source/frame_00000.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash'):
            ParallaxLocalizer(self.root)

    def test_wiki_search_ignores_hidden_processing_packages(self):
        hidden = self.root / '.processing/stage'
        hidden.mkdir(parents=True)
        (hidden / 'map.json').write_text(json.dumps(self.metadata))
        self.assertEqual(find_package([self.root.parent], 40), self.root)

    def test_ground_ring_without_arrow_and_ambiguous_centers(self):
        image = np.zeros((999, 1776, 3), np.uint8)
        cv2.ellipse(image, (880, 500), (65, 44), 0, 0, 360, (0, 150, 255), 2)
        np.testing.assert_allclose(field_ring(image), [880, 500], atol=1)
        cv2.ellipse(image, (1060, 500), (65, 44), 0, 0, 360, (0, 150, 255), 2)
        with self.assertRaisesRegex(RuntimeError, '多个'):
            field_ring(image)

    def test_overrides_and_uncalibrated_movement(self):
        self.annotation['terrain_edits'] = [dict(type='brush', operation='erase', width=10, points=[[100, 80]])]
        self.save_edits()
        road, _, _ = edited_roads(self.root, self.metadata)
        with self.assertRaises(ValueError):
            checked_segment(road, [80, 80], [120, 80])
        checked_segment(road, [80, 100], [120, 100])
        with self.assertRaisesRegex(ValueError, '标定'):
            load_calibration(self.root, ParallaxLocalizer(self.root), 'normal')

    def test_local_tracking_requires_agreement_and_stays_bound_to_reference(self):
        localizer = SimpleNamespace(allows_position=lambda point: True)
        report = dict(roi_to_map=np.eye(3), player_roi=[80, 80], status='accepted')
        support = [[0, 0], [160, 0], [160, 160], [0, 160]]
        matrix = np.array([[1., 0, 10], [0, 1, 5], [0, 0, 1]])
        image = np.zeros((180, 180, 3), np.uint8)
        with patch('module.campaign_prototype.parallax_movement.register_surface',
                   return_value=dict(matrix=matrix, support=support)):
            result = track_localization(localizer, image, report, image, [90, 85])
        np.testing.assert_allclose(result['position'], [80, 80])
        self.assertEqual(result['registration_method'], 'fixed_live_reference')
        with patch('module.campaign_prototype.parallax_movement.register_surface', side_effect=[
                dict(matrix=matrix, support=support), *[dict(matrix=np.eye(3), support=support)] * 7]):
            with self.assertRaisesRegex(ValueError, '不一致'):
                track_localization(localizer, image, report, image, [90, 85])

    def test_single_tracking_window_requires_four_held_out_points(self):
        localizer = SimpleNamespace(allows_position=lambda point: True)
        report = dict(roi_to_map=np.eye(3), player_roi=[80, 80], status='accepted')
        image = np.zeros((180, 180, 3), np.uint8)
        fit = dict(matrix=np.eye(3), support=[[0, 0], [160, 0], [160, 160], [0, 160]],
                   points=12, validation_points=4)
        for count, accepted in [(4, True), (3, False)]:
            fit['validation_points'] = count
            with patch('module.campaign_prototype.parallax_movement.register_surface',
                       side_effect=[fit, *[ValueError('wrong plane')] * 7]):
                if accepted:
                    result = track_localization(localizer, image, report, image, [80, 80])
                    self.assertEqual(result['status'], 'accepted')
                else:
                    with self.assertRaises(ValueError):
                        track_localization(localizer, image, report, image, [80, 80])

    def test_calibration_uses_real_landing_pairs_and_independent_validation(self):
        localizer = SimpleNamespace(digest='map', edits_digest='edits', cache_digest='geometry',
                                    metadata={'chapter': 40})
        session = Mock()
        session.localizer = localizer
        session.request = dict(package=str(self.root), difficulty='normal')
        session.folder = self.root / 'run'
        session.folder.mkdir()
        anchor = np.array([850., 500.])
        offsets = [(40, 25), (-40, -25), (40, -25), (-40, 25), (0, 25), (0, -25),
                   (-15, -10), (15, -10), (0, 10)]
        positions = [anchor, *[anchor + offset for offset in np.cumsum(offsets, axis=0)]]
        session.observe.side_effect = [dict(position=(p / 5).tolist()) for p in positions]
        field = np.zeros((999, 1776, 3), np.uint8)
        with patch('module.campaign_prototype.goto.map_close'), patch(
                'module.campaign_prototype.goto.capture_client', return_value=field), patch(
                'module.campaign_prototype.movement_feedback.resolve_anchor', return_value=anchor), patch(
                'module.campaign_prototype.parallax_movement.surface_mask', return_value=np.ones((240, 360), np.uint8)):
            result = calibrate(session)
        self.assertEqual(result['state'], 'calibrated')
        self.assertEqual(session.move.call_count, 9)
        data, _ = load_calibration(self.root, localizer, 'normal')
        self.assertEqual(data['validation_samples'], 3)
        np.testing.assert_allclose(np.asarray(data['matrix'])[:2, :2], np.eye(2) * 5, atol=1e-5)
        store = Mock()
        store.load.return_value = dict(path=str(self.root), annotations=self.annotation, chapter=40)
        jobs = MovementJobs(store)
        # 使用真实地图的版本绑定核对管理页状态，与移动执行器采用同一份标定。
        actual = ParallaxLocalizer(self.root)
        from module.campaign_prototype.parallax_movement import binding
        data['binding'] = binding(actual, 'normal')
        metadata = self.root / 'movement_calibration/normal/calibration.json'
        metadata.write_text(json.dumps(data))
        self.assertEqual(jobs.calibration('map', 'normal')['state'], 'ready')
        self.assertEqual(jobs.calibration('map', 'hard')['state'], 'missing')
        self.annotation['objects'] = [{'id': 'new-collectible'}]
        self.assertEqual(jobs.calibration('map', 'normal')['state'], 'ready')
        (metadata.parent / 'surface.png').write_bytes(b'changed')
        self.assertEqual(jobs.calibration('map', 'normal')['state'], 'invalid')
        self.annotation['terrain_edits'] = [dict(type='brush', operation='erase', width=2, points=[[10, 10]])]
        self.assertEqual(jobs.calibration('map', 'normal')['state'], 'stale')
        localizer.edits_digest = 'changed'
        with self.assertRaisesRegex(ValueError, '变化'):
            load_calibration(self.root, localizer, 'normal')

    def test_automatic_calibration_reuses_or_collects_then_moves(self):
        session = Mock()
        session.request = dict(package=str(self.root), difficulty='normal', target=[100, 80], auto_calibrate=True)
        calibration = ({'matrix': np.eye(3).tolist()}, self.query)
        module = 'module.campaign_prototype.parallax_movement'
        with patch(f'{module}.load_calibration', return_value=calibration), patch(f'{module}.calibrate') as collect, \
                patch('module.campaign_prototype.manual_move.navigate', return_value={'state': 'arrived'}) as move:
            self.assertEqual(run_movement(session)['state'], 'arrived')
            collect.assert_not_called()
            move.assert_called_once()

        with patch(f'{module}.load_calibration', side_effect=[ValueError('stale'), calibration]), \
                patch(f'{module}.calibrate', return_value={'movement_clicks': 9}) as collect, \
                patch('module.campaign_prototype.manual_move.navigate', return_value={'state': 'arrived'}) as move:
            self.assertEqual(run_movement(session)['state'], 'arrived')
            collect.assert_called_once_with(session)
            self.assertIs(session.calibration, calibration)
            move.assert_called_once()

    def test_move_preflight_requires_existing_calibration_even_with_auto_flag(self):
        from module.campaign_prototype.manual_move import prepare
        request = dict(package=str(self.root), chapter=40, difficulty='normal', action='move',
                       target=[60, 60], auto_calibrate=True, image_sha256=self.metadata['image_sha256'],
                       revision=hashlib.sha256((self.root / 'annotations.json').read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, '标定'):
            prepare(request, self.root / 'run')
        with patch('module.campaign_prototype.parallax_movement.load_calibration'):
            package, _ = prepare(request, self.root / 'run')
            self.assertEqual(package, self.root)
            self.assertIs(request['auto_calibrate'], False)
            with self.assertRaisesRegex(ValueError, '道路'):
                prepare({**request, 'target': [-1, 60]}, self.root / 'run')

    def test_failed_cancelled_or_disabled_calibration_never_starts_target_movement(self):
        session = Mock()
        session.request = dict(package=str(self.root), difficulty='normal', target=[100, 80], auto_calibrate=True)
        module = 'module.campaign_prototype.parallax_movement'
        for error in [ValueError('validation failed'), KeyboardInterrupt('STOP')]:
            with patch(f'{module}.load_calibration', side_effect=ValueError('missing')), \
                    patch(f'{module}.calibrate', side_effect=error), \
                    patch('module.campaign_prototype.manual_move.navigate') as move:
                with self.assertRaises(type(error)):
                    run_movement(session)
                move.assert_not_called()
        session.request['auto_calibrate'] = False
        with patch(f'{module}.load_calibration', side_effect=ValueError('missing')), \
                patch(f'{module}.calibrate') as collect, \
                patch('module.campaign_prototype.manual_move.navigate') as move:
            with self.assertRaisesRegex(ValueError, 'missing'):
                run_movement(session)
            collect.assert_not_called()
            move.assert_not_called()

    def test_recovery_requires_two_consistent_direct_references(self):
        references = [(self.query, {})] * 3
        with patch('module.campaign_prototype.parallax_movement.track_localization', side_effect=[
                dict(position=[10, 20]), ValueError('unavailable'), dict(position=[12, 21])]):
            result = recover_localization(None, references, self.query)
        self.assertEqual(result['registration_method'], 'cross_checked_live_references')
        with patch('module.campaign_prototype.parallax_movement.track_localization', side_effect=[
                dict(position=[10, 20]), ValueError('unavailable'), dict(position=[22, 21])]):
            with self.assertRaisesRegex(ValueError, '不一致'):
                recover_localization(None, references, self.query)

    def test_restart_retains_direct_references_and_ignores_corrupt_or_recovered_frames(self):
        localizer = ParallaxLocalizer(self.root)
        cache = self.root / 'live'
        report = dict(status='accepted', reference_depth=1, registration_method='fixed_live_reference')
        references = [(self.query, {**report, 'position': [index, 20]}) for index in range(3)]
        references.append((self.query, {**report, 'registration_method': 'cross_checked_live_references'}))
        save_live_references(cache, localizer, 'normal', references)
        loaded = load_live_references(cache, localizer, 'normal')
        self.assertEqual([r['position'] for _, r in loaded], [[0, 20], [1, 20], [2, 20]])
        (cache / 'live_0.png').write_bytes(b'interrupted write')
        self.assertEqual(len(load_live_references(cache, localizer, 'normal')), 2)
        self.assertEqual(load_live_references(cache, localizer, 'hard'), [])
        localizer.edits_digest = 'changed'
        self.assertEqual(load_live_references(cache, localizer, 'normal'), [])


if __name__ == '__main__':
    unittest.main()
