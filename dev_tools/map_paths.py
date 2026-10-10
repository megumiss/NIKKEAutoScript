"""运行地图与本地采集、诊断数据使用分离的目录。"""

from pathlib import Path


MAPS_ROOT = Path(__file__).resolve().parents[1] / 'data' / 'chapter_maps'
# 运行地图来自独立的资源仓库，data/resources 是它的本地同步副本（maps/ 为章节地图），不随主项目提交。
RESOURCE_ROOT = MAPS_ROOT.parent / 'resources'
DEFAULT_MAPS_ROOT = RESOURCE_ROOT / 'maps'
LOCAL_MAPS_ROOT = MAPS_ROOT / 'local'
DEFAULT_CAPTURE_ROOT = LOCAL_MAPS_ROOT / 'captures'


def local_package_dir(package):
    """将分发地图的辅助输出放到本地目录，显式指定的其他地图包保持原有行为。"""
    package = Path(package).resolve()
    if package.is_relative_to(DEFAULT_MAPS_ROOT.resolve()):
        return LOCAL_MAPS_ROOT / 'packages' / package.relative_to(DEFAULT_MAPS_ROOT.resolve())
    return package
