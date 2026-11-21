"""主线地图工具的 Python 文件入口，供命令行和 VS Code 直接启动。"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dev_tools.campaign.editor.server import main as editor_main  # noqa: E402


def main():
    """在仓库根目录启动地图编辑器，沿用服务端的参数解析与退出清理。

    文件入口可能从编辑器目录或其他工作目录执行，需固定仓库导入路径与相对参数的基准。
    直接调用服务端入口而不创建子进程，使调试器断点可进入 HTTP、标注及后台任务代码。
    """
    os.chdir(ROOT)
    editor_main()


if __name__ == '__main__':
    main()
