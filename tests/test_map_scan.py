import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dev_tools.map_scan import ScanJobs
from dev_tools.minimap_chapters import existing_package, finish_scan, run_batch


class ScanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = SimpleNamespace(root=self.root, load=Mock())
        self.jobs = ScanJobs(self.store)
        self.process = Mock()
        self.process.poll.return_value = None
        popen = patch('dev_tools.map_scan.subprocess.Popen', return_value=self.process)
        self.popen = popen.start()
        self.addCleanup(popen.stop)

    def test_invalid_options_never_start_process(self):
        for payload in [[], {}, {'chapter': True, 'process_3d': False},
                        {'chapter': 40, 'process_3d': 'yes'},
                        *[dict(chapter=40, process_3d=True, stroke_px=value)
                          for value in [0, 241, float('nan'), float('inf'), True]]]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.jobs.start(payload)
        self.popen.assert_not_called()

    def test_modes_separate_outputs_and_success_requires_valid_package(self):
        first = self.jobs.start(dict(chapter=40, process_3d=True))
        self.assertIn('--process-3d', self.popen.call_args.args[0])
        self.assertEqual(first['stroke_px'], 120)
        self.assertTrue(Path(first['output']).is_relative_to(self.root))
        with self.assertRaises(ValueError):
            self.jobs.start(dict(chapter=1, process_3d=False))
        self.process.poll.return_value = 0
        self.store.load.side_effect = ValueError('Missing map')
        self.assertEqual(self.jobs.status()['state'], 'failed')
        self.store.load.side_effect = None
        self.assertEqual(self.jobs.status()['state'], 'complete')
        second = self.jobs.start(dict(chapter=1, process_3d=False))
        self.assertNotIn('--process-3d', self.popen.call_args.args[0])
        self.assertNotEqual(first['output'], second['output'])

    def test_progress_stop_and_stale_job(self):
        job = self.jobs.start(dict(chapter=40, process_3d=True))
        package = Path(job['output']) / 'chapter_40'
        (package / 'source').mkdir(parents=True)
        (package / 'source/scan.json').write_text('{"frames":[{},{}]}')
        (package / 'processing_status.json').write_text('{"phase":"redraw"}')
        self.assertEqual(self.jobs.status()['frames'], 2)
        self.assertIn('投影', self.jobs.status()['message'])
        with self.assertRaises(ValueError):
            self.jobs.stop('old-job')
        self.assertFalse((Path(job['output']) / 'STOP').exists())
        self.assertEqual(self.jobs.stop(job['id'])['state'], 'stopping')
        self.process.poll.return_value = 130
        self.assertEqual(self.jobs.status()['state'], 'cancelled')
        self.store.load.assert_not_called()

    def test_failure_reports_worker_error(self):
        job = self.jobs.start(dict(chapter=40, process_3d=True))
        (Path(job['output']) / 'progress.json').write_text(
            json.dumps([{'attempts': [{'error': 'Game lost focus'}]}]))
        self.process.poll.return_value = 1
        self.assertEqual(self.jobs.status()['message'], 'Game lost focus')
        self.store.load.assert_not_called()

    def package(self):
        package = self.root / 'chapter_40'
        (package / 'source').mkdir(parents=True)
        (package / 'source/scan.json').write_text(json.dumps(
            dict(chapter=40, status='roads_exhausted', frames=[{}])))
        return package

    def test_flat_finish_keeps_original_rebuild_and_export(self):
        package = self.package()
        with patch('dev_tools.minimap_chapters.rebuild') as rebuild, patch(
                'dev_tools.minimap_chapters.export_static', return_value={'frames': 1}) as export, patch(
                'dev_tools.minimap_chapters.finish_scan_3d') as layered:
            self.assertEqual(finish_scan(package, 40), {'frames': 1})
        rebuild.assert_called_once_with(package / 'source')
        export.assert_called_once_with(package, 40)
        layered.assert_not_called()

    def test_3d_publication_and_cancel_do_not_publish_partial_map(self):
        package = self.package()
        stop = self.root / 'STOP'

        def redraw(source, output, **kwargs):
            self.assertFalse((package / 'map.json').exists())
            output.mkdir(parents=True)
            (output / 'map.json').write_text('{"map_render_mode":"projected_raw_regions"}')
            (output / 'map.png').write_bytes(b'map')

        with patch('dev_tools.minimap_layered.reconstruct'), patch(
                'dev_tools.minimap_projected_redraw.redraw', side_effect=redraw), patch(
                'dev_tools.minimap_chapters.rebuild') as flat:
            result = finish_scan(package, 40, True, stop)
        self.assertTrue(result['processing_3d'])
        flat.assert_not_called()
        metadata = json.loads((package / 'map.json').read_text())
        self.assertEqual(metadata['coordinates']['unit'], 'pixel')
        self.assertFalse(metadata['navigation_ready'])
        (package / 'map.json').unlink()
        with patch('dev_tools.minimap_layered.reconstruct', side_effect=lambda *a, **kw: stop.touch()), patch(
                'dev_tools.minimap_projected_redraw.redraw') as redraw_mock, self.assertRaises(KeyboardInterrupt):
            finish_scan(package, 40, True, stop)
        redraw_mock.assert_not_called()
        self.assertFalse((package / 'map.json').exists())
        self.assertTrue((package / 'source/scan.json').exists())

    def test_failed_3d_retries_preserve_completed_raw_frames(self):
        package = self.package()
        original = (package / 'source/scan.json').read_bytes()
        options = SimpleNamespace(output=self.root, start=40, end=40, retries=1,
                                  driver_root=self.root, process_3d=True)
        with patch('dev_tools.minimap_chapters.finish_scan', side_effect=ValueError('fit failed')) as finish, patch(
                'dev_tools.minimap_chapters.capture_chapter') as capture:
            self.assertEqual(run_batch(options, None), 1)
        self.assertEqual(finish.call_count, 2)
        capture.assert_not_called()
        self.assertEqual((package / 'source/scan.json').read_bytes(), original)

    def test_existing_mode_mismatch_and_incomplete_3d_rejected(self):
        import hashlib
        package = self.package()
        (package / 'map.png').write_bytes(b'map')
        (package / 'map.json').write_text(json.dumps(dict(chapter=40, processing_3d=True,
            image_sha256=hashlib.sha256(b'map').hexdigest(), map_render_mode='projected_raw_regions')))
        for mode in [False, True]:
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                existing_package(package, 40, mode)


if __name__ == '__main__':
    unittest.main()
