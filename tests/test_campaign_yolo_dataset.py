"""Regression checks for reviewed-label precedence and capture isolation."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from dev_tools.campaign_yolo_dataset import apply_frame_reviews, select_reviewed_partitions
from dev_tools.campaign_yolo_evaluate import evaluate
from module.campaign_prototype.detection import Detection


class DatasetTests(unittest.TestCase):
    def test_full_frame_review_removes_false_candidates_and_adds_missed_targets(self):
        record = dict(id='image', pixel_sha256='hash', objects=[dict(label='minimap_enemy_ex', box=[1, 2, 3, 4])])
        corrected = [dict(label='minimap_squad_ring', box=[40, 40, 60, 60], status='complete')]
        review = dict(id='image', pixel_sha256='hash', review_scope='full_frame', review='accepted', objects=corrected)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.json'
            path.write_text(json.dumps(dict(frames=[review])), encoding='utf-8')
            apply_frame_reviews([record], path)
            self.assertEqual(record['objects'], corrected)
            record['pixel_sha256'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'source changed'):
                apply_frame_reviews([record], path)

    def test_group_and_near_copy_isolation_applies_before_training(self):
        def record(name, split='train', chapter=5, gold=False, group=None):
            return dict(id=name, split=split, chapters=[chapter], group=group or f'chapter_{chapter}',
                        source=f'{name}/frame.png', pixel_sha256=name, size=[486, 462], domain='minimap',
                        objects=[], quality='full_frame_reviewed' if gold else 'weak')

        records = [record('test', 'test', 43, True), record('chapter_copy', chapter=43),
                   record('near_copy'), record('distinct'), record('weak_val', 'val'),
                   record('val_copy', 'val', 12, True), record('unreviewed_41', chapter=41)]
        vectors = dict(test=np.zeros(63, bool), chapter_copy=np.ones(63, bool),
                       near_copy=np.array([True] * 4 + [False] * 59), distinct=np.ones(63, bool),
                       weak_val=np.ones(63, bool), val_copy=np.zeros(63, bool))
        with patch('dev_tools.campaign_yolo_dataset.load_frame', side_effect=lambda r: vectors[r['id']]), \
                patch('dev_tools.campaign_yolo_dataset.perceptual_hash', side_effect=lambda image: image):
            selected, excluded = select_reviewed_partitions(records)
        self.assertEqual([r['id'] for r in selected], ['test', 'distinct'])
        self.assertEqual(excluded['held_acquisition_group'], 1)
        self.assertEqual(excluded['near_duplicate_of_holdout'], 1)
        self.assertEqual(excluded['validation_near_test'], 1)


class ReplayTests(unittest.TestCase):
    def test_wrong_class_cannot_count_as_recall_and_unsafe_click_is_reported(self):
        record = dict(id='frame', source='frame.png', split='test', domain='minimap',
                      review_scope='full_frame', ignored=[], objects=[
                          dict(label='minimap_enemy_ex', box=[30, 30, 70, 70], status='complete'),
                          dict(label='minimap_enemy_normal', box=[80, 30, 110, 70], status='complete')])
        wrong = Detection('minimap_enemy_normal', .99, (30, 30, 70, 70))
        with patch('dev_tools.campaign_yolo_evaluate.load_frame', return_value=np.zeros((160, 160, 3), np.uint8)), \
                patch('module.campaign_prototype.detection.detect_minimap', return_value=[wrong]), \
                patch('module.campaign_prototype.perception.detect_markers', return_value=([], [[50, 50]])), \
                patch('module.campaign_prototype.perception.normal_enemy_markers', return_value=[[50, 50]]):
            result = evaluate([record], None, ['test'])
        self.assertEqual(result['metrics']['minimap_enemy_ex']['recall'], 0.)
        self.assertEqual(result['metrics']['minimap_enemy_normal']['recall'], 0.)
        self.assertEqual(result['metrics']['minimap_enemy_normal']['fp'], 1)
        self.assertEqual(result['unsafe_normal_click_frames'], 1)


if __name__ == '__main__':
    unittest.main()
