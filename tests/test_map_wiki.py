import json
import unittest
from unittest.mock import Mock, patch

from dev_tools.map_wiki import WikiJobs
from tests.test_map_annotator import MapFixture


class WikiJobTests(MapFixture, unittest.TestCase):
    def job(self):
        loaded = self.document()
        loaded['annotations']['terrain_edits'] = [dict(type='brush', operation='add', width=5, points=[[40, 40]])]
        self.store.save(loaded['id'], loaded['annotations'], loaded['revision'])
        loaded = self.store.load(loaded['id'])
        jobs = WikiJobs(self.store, self.root / 'jobs', self.root / 'cache')
        process = Mock()
        process.poll.return_value = None
        payload = dict(id=loaded['id'], revision=loaded['revision'], image_sha256=self.image_hash)
        with patch('dev_tools.map_wiki.subprocess.Popen', return_value=process) as launch:
            job = jobs.start(payload)
        self.assertIn(str(self.package), launch.call_args.args[0])
        self.assertIn('--dry-run', launch.call_args.args[0])
        return jobs, process, job

    def ready(self, jobs, process, job):
        cache = jobs.output / job['id'] / 'cache'
        folder = cache / 'chapter_01/normal'
        folder.mkdir(parents=True)
        record = dict(chapter=1, difficulty='normal', article_id=99, url='https://example.com/99',
                      status='needs_review', items=2, accepted=1)
        match = dict(number=1, status='accepted', image_sha256=self.image_hash, road_iou=.96, roi=[0, 0, 50, 50],
                     roi_to_map=[[1, 0, 0], [0, 1, 0], [0, 0, 1]], player_center=[40, 40], position=[40, 40],
                     image_url='https://example.com/1.png', name='sample', normalized_size=[50, 50])
        matches = [match, dict(number=2, status='needs_review')]
        (folder / 'matches.json').write_text(json.dumps(dict(matches=matches)))
        (cache / 'progress.json').write_text(json.dumps([record]))
        process.poll.return_value = 1

    def test_partial_results_preview_then_import_preserves_edits_and_manual_objects(self):
        jobs, process, job = self.job()
        self.ready(jobs, process, job)
        self.assertEqual(jobs.status()['state'], 'ready')
        self.assertEqual(len(jobs.review()), 2)
        before = self.store.load('chapter_01')['annotations']
        jobs.apply(dict(job=job['id'], revision=job['revision']))
        after = self.store.load('chapter_01')['annotations']
        self.assertEqual(after['terrain_edits'], before['terrain_edits'])
        self.assertEqual(after['objects'][:3], before['objects'])
        self.assertEqual(len(after['objects']), 4)
        self.assertEqual(jobs.status()['state'], 'imported')

    def test_changed_revision_and_cancel_prevent_import(self):
        jobs, process, job = self.job()
        self.ready(jobs, process, job)
        loaded = self.store.load('chapter_01')
        loaded['annotations']['objects'][0]['label'] = 'changed'
        self.store.save('chapter_01', loaded['annotations'], loaded['revision'])
        with self.assertRaisesRegex(ValueError, '变化'):
            jobs.apply(dict(job=job['id'], revision=job['revision']))
        jobs.stop(job['id'])
        self.assertFalse(jobs.status()['ready'])


if __name__ == '__main__':
    unittest.main()
