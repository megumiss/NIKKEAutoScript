"""离线验证原型控制边界；合成地图与模拟驱动不操作真实游戏。"""
import copy
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from dev_tools.map_annotator import COORDINATES
from dev_tools.minimap_reconstruct import DriverWindow
from module.campaign_prototype import goto, runtime, settings
from module.campaign_prototype.map_package import MapPackage, screenshot_to_client, transform


class PackageTests(unittest.TestCase):
    def setUp(self):
        """建立有完整坐标、质量和文件哈希的最小地图包。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'chapter_38'
        (self.path / 'source').mkdir(parents=True)
        cv2.imwrite(str(self.path / 'map.png'), np.full((64, 80, 3), 120, np.uint8))
        digest = hashlib.sha256((self.path / 'map.png').read_bytes()).hexdigest()
        crop = {'source_size': [100, 84], 'box': [10, 10, 90, 74], 'margin_px': 4}
        self.transforms = {'projection': np.eye(3).tolist(), 'warp_size': [40, 32],
                           'origin': [10, 10], 'positions': np.zeros((16, 2)).tolist(),
                           'scan_to_map': np.eye(3).tolist()}
        self.meta = {
            'schema_version': 1, 'chapter': 38, 'difficulty': 'normal', 'image': 'map.png',
            'image_sha256': digest, 'coordinates': COORDINATES, 'size': [80, 64], 'crop': crop,
            'transforms': self.transforms,
            'capture': {'registration': {'status': 'joint_grid_road', 'residual_median_px': .5},
                        'frames': 16, 'localized_frames': 16, 'excluded_unlocalized_frames': 0,
                        'status': 'roads_exhausted', 'unresolved_frontiers': [],
                        'whole_camera_domain_verified': False, 'crop': crop},
        }
        self.annotations = {
            'schema_version': 1, 'image': 'map.png', 'image_sha256': digest, 'coordinates': COORDINATES,
            'objects': [{'id': 'normal_1', 'type': 'point', 'points': [[30, 20]],
                         'source': {'number': 1, 'difficulty': 'normal'}},
                        {'id': 'hard_1', 'type': 'point', 'points': [[50, 40]],
                         'source': {'number': 1, 'difficulty': 'hard'}}], 'connections': [],
        }
        self.calibration = {'projection': np.eye(3).tolist(), 'field_inverse': goto.A_INV.tolist(),
                            'client': [1776, 999], 'roi': [644, 280, 1130, 742]}
        self.persist()

    def persist(self):
        """重新绑定修改后的合成文件，使测试能够独立检查语义校验而非只触发哈希错误。"""
        for name, value in [('map.json', self.meta), ('annotations.json', self.annotations),
                            ('calibration.json', self.calibration)]:
            (self.path / name).write_text(json.dumps(value), encoding='utf-8')
        np.savez(self.path / 'source/map_data.npz', **self.transforms,
                 terrain_probability=np.ones((64, 80)))
        names = ['map.json', 'annotations.json', 'map.png', 'source/map_data.npz', 'calibration.json']
        manifest = {'sha256': {n: hashlib.sha256((self.path / n).read_bytes()).hexdigest() for n in names},
                    'client': [1776, 999], 'roi': [644, 280, 1130, 742], 'coverage': 'observed_roads',
                    'whole_camera_domain_verified': False}
        (self.path / 'validation.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_load_binds_normal_targets_and_partial_coverage(self):
        """混合难度标注只能选出当前难度，部分道路覆盖不得升级为全图证明。"""
        package = MapPackage(self.path, 38, 'normal')
        np.testing.assert_equal(package.targets[1], [30, 20])
        self.assertFalse(package.binding['whole_camera_domain_verified'])

    def test_wrong_identity_and_modified_cache_are_rejected(self):
        """章节、难度和缓存内容变化都必须使加载失败。"""
        for chapter, difficulty in [(39, 'normal'), (38, 'hard')]:
            with self.subTest(chapter=chapter, difficulty=difficulty), self.assertRaises(ValueError):
                MapPackage(self.path, chapter, difficulty)
        with (self.path / 'source/map_data.npz').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            MapPackage(self.path, 38, 'normal')

    def test_quality_failures_survive_rehashing(self):
        """即使重新生成哈希，未定位帧、失败配准、错误裁剪等不合格内容仍被拒绝。"""
        original = copy.deepcopy(self.meta)
        cases = [
            ('registration', {'status': 'insufficient_joint_evidence', 'residual_median_px': .5}),
            ('registration', {'status': 'joint_grid_road', 'residual_median_px': float('nan')}),
            ('excluded_unlocalized_frames', 1), ('localized_frames', 15), ('frames', 0),
            ('unresolved_frontiers', [[20, 20]]), ('whole_camera_domain_verified', True),
            ('status', 'incomplete'), ('crop', {'box': [0, 0, 2, 2]}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.meta = copy.deepcopy(original)
                self.meta['capture'][key] = value
                self.persist()
                with self.assertRaises(ValueError):
                    MapPackage(self.path, 38, 'normal')

    def test_annotation_and_calibration_geometry_are_checked(self):
        """标注不得使用错图坐标，标定也必须对应当前原始截图几何。"""
        self.annotations['objects'][0]['points'] = [[80, 20]]
        self.persist()
        with self.assertRaises(ValueError):
            MapPackage(self.path, 38, 'normal')
        self.annotations['objects'][0]['points'] = [[30, 20]]
        self.calibration['client'] = [1280, 720]
        self.persist()
        with self.assertRaisesRegex(ValueError, 'Calibration capture geometry'):
            MapPackage(self.path, 38, 'normal')

    def test_invalid_package_fails_before_driver_acquisition(self):
        """控制检查入口先验证地图包，错误数据不能取得鼠标。"""
        from module.campaign_prototype.check_controls import main
        with patch.object(sys, 'argv', ['check_controls', '--package', str(self.path), '--chapter', '39']), patch(
                'module.campaign_prototype.settings.configure'), patch.object(settings, 'package', self.path), patch(
                'module.campaign_prototype.check_controls.MapPackage', side_effect=ValueError('Wrong chapter')), patch(
                'module.campaign_prototype.runtime.Window') as window:
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 1)
        window.assert_not_called()

    def test_importer_validates_and_preserves_existing_destination(self):
        """实际运行导入入口，验证可复现导入及禁止覆盖原目录。"""
        destination = self.path.parent / 'imported'
        command = [sys.executable, '-m', 'module.campaign_prototype.prepare_package', '--source', str(self.path),
                   '--destination', str(destination), '--chapter', '38',
                   '--calibration', str(self.path / 'calibration.json')]
        result = subprocess.run(command, capture_output=True, text=True, cwd=settings.ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(MapPackage(destination, 38, 'normal').binding['chapter'], 38)
        result = subprocess.run(command, capture_output=True, text=True, cwd=settings.ROOT)
        self.assertEqual(result.returncode, 1)


class ControlTests(unittest.TestCase):
    def setUp(self):
        """建立模拟窗口与临时停止路径，整个测试不加载真实鼠标驱动。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        patcher = patch.object(settings, 'stop_file', self.folder / 'STOP')
        patcher.start()
        self.addCleanup(patcher.stop)

    def window(self):
        """构造无需运行平台初始化的受保护窗口，记录所有输入和释放调用。"""
        window = runtime.Window.__new__(runtime.Window)
        window.args = SimpleNamespace(client=[1776, 999])
        window.hwnd = 1
        window.gui = Mock()
        window.gui.GetClientRect.return_value = (0, 0, 1776, 999)
        window.gui.GetForegroundWindow.return_value = 1
        window.gui.ClientToScreen.return_value = (10, 20)
        window._origin = (10, 20)
        window._focused, window._closed = True, False
        window._signals = {}
        window.capture_dc = None
        window.release = Mock()
        window.handler = runtime.GuardedInput(window, Mock(_failures=0))
        return window

    def test_invalid_window_and_stop_block_input_but_release(self):
        """失焦、尺寸变化、窗口移动和取消都禁止新输入，但必须抬起鼠标并释放。"""
        for fault in ['focus', 'size', 'origin', 'stop']:
            with self.subTest(fault=fault):
                window = self.window()
                if fault == 'focus':
                    window.gui.GetForegroundWindow.return_value = 2
                elif fault == 'size':
                    window.gui.GetClientRect.return_value = (0, 0, 1280, 720)
                elif fault == 'origin':
                    window.gui.ClientToScreen.return_value = (30, 40)
                else:
                    settings.stop_file.touch()
                with self.assertRaises((RuntimeError, KeyboardInterrupt)):
                    try:
                        window.handler.mouse_click(500, 500)
                    finally:
                        runtime.finish(window, self.folder / 'receipt.json', {})
                window.handler.handler.mouse_click.assert_not_called()
                window.handler.handler.mouse_up.assert_called_once()
                window.release.assert_called_once()
                receipt = json.loads((self.folder / 'receipt.json').read_text())
                self.assertEqual(receipt['status'], 'cancelled' if fault == 'stop' else 'failed')

    def test_interrupt_and_disk_failure_keep_original_failure(self):
        """回执写盘失败不能覆盖取消或操作失败，也不能阻止驱动释放。"""
        for failure in [KeyboardInterrupt('cancel'), RuntimeError('capture failed')]:
            window = self.window()
            receipt = Mock()
            receipt.write_text.side_effect = OSError('disk full')
            with self.assertRaises(type(failure)) as raised:
                try:
                    raise failure
                finally:
                    runtime.finish(window, receipt, {})
            self.assertIs(raised.exception, failure)
            window.release.assert_called_once()

    def test_write_failure_after_success_and_release_failure_are_nonzero(self):
        """正常操作后的写盘失败也属于失败；抬起异常仍继续释放底层驱动。"""
        window = self.window()
        receipt = Mock()
        receipt.write_text.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            runtime.finish(window, receipt, {})
        window.release.assert_called_once()
        window = self.window()
        window.handler.handler.mouse_up.side_effect = RuntimeError('mouse up failed')
        with self.assertRaisesRegex(RuntimeError, 'mouse up failed'):
            runtime.finish(window, self.folder / 'release.json', {})
        window.release.assert_called_once()
        window.close()
        window.release.assert_called_once()

    def test_initialization_error_releases_acquired_driver(self):
        """信号注册失败时必须回收已经初始化的窗口输入。"""
        mock = self.window()

        def initialize(window, args):
            """复用模拟字段替代真实平台构造。"""
            window.__dict__.update(mock.__dict__)

        with patch.object(DriverWindow, '__init__', initialize), patch.object(
                signal, 'signal', side_effect=[ValueError('main thread only'), None]):
            with self.assertRaisesRegex(ValueError, 'main thread only'):
                runtime.Window()
        mock.release.assert_called_once()
        self.assertIsNone(runtime._active)

    def test_command_codes_and_child_failure(self):
        """异常与子进程非零退出必须向外传播，避免外层误认为镜头操作成功。"""
        for failure, code in [(KeyboardInterrupt('cancel'), 130), (RuntimeError('failed'), 1)]:
            with self.assertRaises(SystemExit) as error:
                runtime.command(Mock(side_effect=failure))()
            self.assertEqual(error.exception.code, code)
        with self.assertRaisesRegex(RuntimeError, 'child failed.*7'):
            runtime.run_child([sys.executable, '-c', 'raise SystemExit(7)'], self.folder / 'child.log')

    def test_timeout_requests_cooperative_child_release(self):
        """超时创建共享 STOP，子进程先写释放标记再结束，不需强制终止。"""
        released = self.folder / 'released'
        code = ('from pathlib import Path; import sys,time\n'
                'while not Path(sys.argv[1]).exists(): time.sleep(.01)\n'
                'Path(sys.argv[2]).touch()\n')
        with self.assertRaises(TimeoutError):
            runtime.run_child([sys.executable, '-c', code, str(settings.stop_file), str(released)],
                              self.folder / 'timeout.log', timeout=.5)
        self.assertTrue(released.exists())

    def test_stop_write_failure_still_reaps_child(self):
        """停止文件写盘失败且子进程不响应时仍回收进程，不能遗留后台输入任务。"""
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('child', 15), 0]
        with patch.object(subprocess, 'Popen', return_value=child), patch.object(
                runtime.time, 'monotonic', side_effect=[0, 2]), patch.object(
                Path, 'touch', side_effect=OSError('disk full')):
            with self.assertRaises(TimeoutError):
                runtime.run_child(['unused'], self.folder / 'unresponsive.log', timeout=1)
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)

    def test_coordinate_scale_and_projection_horizon(self):
        """标准截图按相同比例换算原始客户区，异比例及投影地平线必须拒绝。"""
        np.testing.assert_allclose(screenshot_to_client([640, 360], [1280, 720]), [888, 499.5])
        with self.assertRaises(ValueError):
            screenshot_to_client([640, 360], [1280, 800])
        with self.assertRaises(ValueError):
            transform([[1, 0, 0], [0, 1, 0], [0, 1, -10]], [5, 10])

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_ten_map_cycles_use_only_expand_and_minimize(self, grab, sleep):
        """复用真实地图状态机完成十次模拟循环，返回只允许左上角最小化。"""
        window = self.window()
        window.args.map_open = [42, 98]
        window.roi = (644, 280, 1130, 742)
        window.focus = Mock()
        blue = np.full((462, 486, 3), (160, 100, 30), np.uint8)
        blank = np.zeros_like(blue)
        window.capture = Mock(side_effect=[blank, blue, blue, blank] * 10)
        compact = np.full((218, 208, 3), (30, 100, 160), np.uint8)
        cv2.circle(compact, (22, 22), 10, (255, 255, 255), 2)
        grab.side_effect = [compact, np.zeros_like(compact), np.zeros_like(compact), compact] * 10
        for _ in range(10):
            goto.map_open(window)
            goto.map_close(window)
        clicks = [c.args for c in window.handler.handler.mouse_click.call_args_list]
        self.assertEqual(clicks, [(52, 118), (668, 289)] * 10)
        window.close()

    @patch('PIL.ImageGrab.grab')
    def test_target_map_state_does_not_reset_view(self, grab):
        """已经是目标状态时不发点击，保护导航已经平移到的地图视野。"""
        window = self.window()
        window.args.map_open = [42, 98]
        window.roi = (644, 280, 1130, 742)
        window.focus = Mock()
        blue = np.full((462, 486, 3), (160, 100, 30), np.uint8)
        window.capture = Mock(return_value=blue)
        grab.return_value = np.zeros((218, 208, 3), np.uint8)
        goto.map_open(window)
        window.capture.return_value = np.zeros_like(blue)
        grab.return_value = np.full((218, 208, 3), (30, 100, 160), np.uint8)
        cv2.circle(grab.return_value, (22, 22), 10, (255, 255, 255), 2)
        goto.map_close(window)
        window.handler.handler.mouse_click.assert_not_called()
        window.close()

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_squad_map_refresh_minimizes_then_reopens(self, grab, sleep):
        """小队定位显式刷新已经展开的面板，必须先点最小化再重新打开。"""
        window = self.window()
        window.args.map_open = [42, 98]
        window.roi = (644, 280, 1130, 742)
        window.focus = Mock()
        blue = np.full((462, 486, 3), (160, 100, 30), np.uint8)
        blank = np.zeros_like(blue)
        compact = np.full((218, 208, 3), (30, 100, 160), np.uint8)
        cv2.circle(compact, (22, 22), 10, (255, 255, 255), 2)
        window.capture = Mock(side_effect=[blue, blank, blue])
        grab.side_effect = [np.zeros_like(compact), compact, np.zeros_like(compact)]
        goto.map_open(window, reset=True)
        clicks = [call.args for call in window.handler.handler.mouse_click.call_args_list]
        self.assertEqual(clicks, [(668, 289), (52, 118)])
        window.close()

    def test_localizers_reject_full_client_as_roi(self):
        """防止把标准截图或完整客户区当作已绑定标定的地图 ROI 使用。"""
        from module.campaign_prototype.adaptive import AdaptiveLocalizer
        from module.campaign_prototype.probe import Localizer
        for cls in (Localizer, AdaptiveLocalizer):
            with self.subTest(localizer=cls.__name__), self.assertRaisesRegex(ValueError, '486x462'):
                cls.locate(cls.__new__(cls), np.zeros((720, 1280, 3), np.uint8), 'bad_roi')


if __name__ == '__main__':
    unittest.main()
