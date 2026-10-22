"""兼容旧脚本启动路径；地图编辑器实现位于 dev_tools.map_annotator 包。"""

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dev_tools.map_annotator import main, make_server


if __name__ == '__main__':
    main()
