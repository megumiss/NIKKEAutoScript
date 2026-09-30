"""章节采集、标注和 Wiki 导入共用的当前地图目录。"""

from pathlib import Path


DEFAULT_MAPS_ROOT = Path(__file__).resolve().parents[1] / 'data' / 'chapter_maps' / 'current'
