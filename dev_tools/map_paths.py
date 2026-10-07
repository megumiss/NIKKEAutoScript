"""运行地图与本地采集、诊断数据使用分离的目录。"""

from pathlib import Path


MAPS_ROOT = Path(__file__).resolve().parents[1] / 'data' / 'chapter_maps'
DEFAULT_MAPS_ROOT = MAPS_ROOT / 'runtime'
LOCAL_MAPS_ROOT = MAPS_ROOT / 'local'
DEFAULT_CAPTURE_ROOT = LOCAL_MAPS_ROOT / 'captures'


def local_package_dir(package):
    """将分发地图的辅助输出放到本地目录，显式指定的其他地图包保持原有行为。"""
    package = Path(package).resolve()
    if package.is_relative_to(DEFAULT_MAPS_ROOT.resolve()):
        return LOCAL_MAPS_ROOT / 'packages' / package.relative_to(DEFAULT_MAPS_ROOT.resolve())
    return package
