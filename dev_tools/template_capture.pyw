"""
template_capture 无控制台启动入口。
双击此文件（由 pythonw.exe 打开）即可启动，不会弹出控制台窗口。
注意：需将 .pyw 关联到项目 .venv 的 pythonw.exe，或用下面的命令启动：
    .venv\\Scripts\\pythonw.exe dev_tools\\template_capture.pyw
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from template_capture import main

if __name__ == '__main__':
    main()
