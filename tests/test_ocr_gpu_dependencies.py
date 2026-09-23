import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deploy.config import ExecutionError
from deploy.pip import DataDependency, PipManager


class PaddleInstallTests(unittest.TestCase):
    def manager(self, device, installed):
        manager = PipManager.__new__(PipManager)
        manager.OcrDevice = device
        manager.InstallDependencies = True
        manager.PypiMirror = None
        manager.SSLVerify = True
        manager.pip = 'python -m pip'
        target = 'paddlepaddle-gpu' if device == 'gpu' else 'paddlepaddle'
        manager.set_required_dependency = {DataDependency(target, '3.1.0')}
        manager.set_installed_dependency = {DataDependency(name, '3.1.0') for name in installed}
        manager.execute = Mock()
        manager._detect_cuda_variant = Mock(return_value='cu126')
        return manager

    def test_first_gpu_start_installs_gpu_package(self):
        manager = self.manager('gpu', ['paddlepaddle'])
        manager._paddle_prepare()
        commands = [call.args[0] for call in manager.execute.call_args_list]
        self.assertIn('uninstall -y paddlepaddle', commands[0])
        self.assertIn('install paddlepaddle-gpu==3.1', commands[1])
        self.assertIn('/cu126/', commands[1])
        self.assertNotIn('set_installed_dependency', manager.__dict__)

    def test_gpu_to_cpu_installs_cpu_after_uninstall(self):
        manager = self.manager('cpu', ['paddlepaddle-gpu'])
        manager._paddle_prepare()
        commands = [call.args[0] for call in manager.execute.call_args_list]
        self.assertIn('uninstall -y paddlepaddle-gpu', commands[0])
        self.assertIn('install paddlepaddle==3.1', commands[1])
        manager._detect_cuda_variant.assert_not_called()

    def test_coexisting_packages_repair_shared_module(self):
        for device in ('cpu', 'gpu'):
            with self.subTest(device=device):
                manager = self.manager(device, ['paddlepaddle', 'paddlepaddle-gpu'])
                manager._paddle_prepare()
                self.assertIn('--force-reinstall --no-deps', manager.execute.call_args.args[0])

    def test_installed_gpu_does_not_reinstall(self):
        manager = self.manager('gpu', ['paddlepaddle-gpu'])
        manager._paddle_prepare()
        manager.execute.assert_not_called()

    def test_disabled_installation_leaves_packages_unchanged(self):
        manager = self.manager('gpu', ['paddlepaddle'])
        manager.InstallDependencies = False
        manager.pip_install()
        manager.execute.assert_not_called()

    def test_driver_failure_preserves_cpu_package(self):
        manager = self.manager('gpu', ['paddlepaddle'])
        manager._detect_cuda_variant.side_effect = ExecutionError('No NVIDIA driver')
        with self.assertRaisesRegex(ExecutionError, 'No NVIDIA driver'):
            manager._paddle_prepare()
        manager.execute.assert_not_called()


class CudaDetectionTests(unittest.TestCase):
    def test_old_and_new_driver_headers(self):
        for header, expected in (
            ('CUDA Version: 11.8', 'cu118'),
            ('CUDA Version: 12.6', 'cu126'),
            ('CUDA Version: 12.9', 'cu129'),
            ('KMD Version: 610.74        CUDA UMD Version: 13.3', 'cu129'),
        ):
            with self.subTest(header=header):
                manager = PipManager.__new__(PipManager)
                manager.PaddleCuda = 'auto'
                with patch('deploy.pip.shutil.which', return_value='nvidia-smi'):
                    with patch('deploy.pip.subprocess.run', return_value=Mock(stdout=header)):
                        self.assertEqual(manager._detect_cuda_variant(), expected)

    def test_cu129_uses_paddle_with_blackwell_support(self):
        with tempfile.TemporaryDirectory() as directory:
            requirements = Path(directory) / 'requirements.txt'
            requirements.write_text('paddlepaddle==3.1.0\npaddleocr==3.1.0\n', encoding='utf-8')
            manager = PipManager.__new__(PipManager)
            manager.OcrDevice = 'gpu'
            manager.paddle_cuda_variant = 'cu129'
            manager.requirements_file = str(requirements)
            self.assertEqual(manager.set_required_dependency, {
                DataDependency('paddlepaddle-gpu', '3.2.0'), DataDependency('paddleocr', '3.1.0'),
            })


class OcrDeviceTests(unittest.TestCase):
    def test_cpu_paddle_rejects_gpu_before_model_initialization(self):
        from module.exception import RequestHumanTakeover
        from module.ocr.nikke_ocr import NIKKEOcr

        with patch('module.ocr.nikke_ocr.paddle.is_compiled_with_cuda', return_value=False):
            with patch('module.ocr.nikke_ocr.PaddleOCR.__init__') as initialize:
                with self.assertRaises(RequestHumanTakeover):
                    NIKKEOcr(device='gpu')
        initialize.assert_not_called()


if __name__ == '__main__':
    unittest.main()
