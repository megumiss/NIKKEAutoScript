import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dev_tools.minimap_chapters import capture_chapter, run_batch


class BatchCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options = SimpleNamespace(output=self.root, start=3, end=1, retries=1, driver_root=self.root)
        self.summary = {'frames': 12, 'whole_camera_domain_verified': False}

    def results(self):
        return json.loads((self.root / 'progress.json').read_text(encoding='utf-8'))

    def test_scan_error_retries_preserves_attempt_then_continues(self):
        def capture(args, chapter, model, stop):
            (args.output / 'evidence.txt').write_text('preserve me')
            if chapter == 3:
                raise RuntimeError('Tracking lost')

        with patch('dev_tools.minimap_chapters.capture_chapter', side_effect=capture) as run, patch(
                'dev_tools.minimap_chapters.finish_scan', return_value=self.summary), patch(
                'dev_tools.minimap_chapters.move_to_previous') as move:
            code = run_batch(self.options, None)
        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 4)
        self.assertEqual([r['status'] for r in self.results()], ['failed', 'captured', 'captured'])
        self.assertEqual(move.call_count, 2)
        self.assertEqual(len(list((self.root / 'chapter_03/attempts').rglob('evidence.txt'))), 1)

    def test_rebuild_failure_does_not_abort_later_chapters(self):
        with patch('dev_tools.minimap_chapters.capture_chapter'), patch(
                'dev_tools.minimap_chapters.finish_scan', side_effect=[ValueError('bad registration'),
                                                                     ValueError('bad registration'),
                                                                     self.summary, self.summary]), patch(
                'dev_tools.minimap_chapters.move_to_previous'):
            self.assertEqual(run_batch(self.options, None), 1)
        self.assertEqual(self.results()[-1]['status'], 'captured')

    def test_transition_uncertainty_pauses_without_next_capture(self):
        with patch('dev_tools.minimap_chapters.capture_chapter') as capture, patch(
                'dev_tools.minimap_chapters.finish_scan', return_value=self.summary), patch(
                'dev_tools.minimap_chapters.move_to_previous', side_effect=RuntimeError('Wrong page')):
            self.assertEqual(run_batch(self.options, None), 2)
        capture.assert_called_once()
        self.assertEqual(self.results()[0]['transition']['status'], 'paused')

    def test_stop_is_not_retried_or_followed_by_navigation(self):
        with patch('dev_tools.minimap_chapters.capture_chapter', side_effect=KeyboardInterrupt()) as capture, patch(
                'dev_tools.minimap_chapters.move_to_previous') as move:
            self.assertEqual(run_batch(self.options, None), 130)
        capture.assert_called_once()
        move.assert_not_called()
        self.assertEqual(self.results()[0]['status'], 'stopped')

    def test_existing_packages_skip_capture(self):
        with patch('dev_tools.minimap_chapters.existing_package', return_value=True), patch(
                'dev_tools.minimap_chapters.capture_chapter') as capture, patch(
                'dev_tools.minimap_chapters.move_to_previous'):
            self.assertEqual(run_batch(self.options, None), 0)
        capture.assert_not_called()

    def test_interrupted_scan_can_finish_export_without_recapture(self):
        source = self.root / 'chapter_03/source'
        source.mkdir(parents=True)
        (source / 'scan.json').write_text('{}')
        self.options.end = 3
        with patch('dev_tools.minimap_chapters.finish_scan', return_value=self.summary), patch(
                'dev_tools.minimap_chapters.capture_chapter') as capture:
            self.assertEqual(run_batch(self.options, None), 0)
        capture.assert_not_called()

    def test_driver_released_even_if_failure_save_and_minimize_both_fail(self):
        window = Mock()
        window.reset_minimap.side_effect = [None, None, RuntimeError('Cannot minimize')]
        scanner = Mock()
        scanner.data = {}
        scanner.explore_drift.side_effect = RuntimeError('Tracking lost')
        scanner.save.side_effect = OSError('Disk full')
        args = SimpleNamespace(output=self.root)
        with patch('dev_tools.minimap_chapters.DriverWindow', return_value=window), patch(
                'dev_tools.minimap_chapters.DriftScanner', return_value=scanner), patch(
                'dev_tools.minimap_chapters.wait_for_chapter'):
            with self.assertRaisesRegex(RuntimeError, 'Cannot minimize'):
                capture_chapter(args, 3, None, self.root / 'STOP')
        window.close.assert_called_once()

    def test_incomplete_old_scan_does_not_consume_capture_budget(self):
        source = self.root / 'chapter_03/source'
        source.mkdir(parents=True)
        (source / 'scan.json').write_text('{"status":"incomplete"}')
        self.options.end = 3
        self.options.retries = 0
        with patch('dev_tools.minimap_chapters.finish_scan',
                   side_effect=[ValueError('incomplete'), self.summary]), patch(
                       'dev_tools.minimap_chapters.capture_chapter') as capture:
            self.assertEqual(run_batch(self.options, None), 0)
        capture.assert_called_once()
        self.assertEqual(len(list((self.root / 'chapter_03/attempts').rglob('scan.json'))), 1)


if __name__ == '__main__':
    unittest.main()
