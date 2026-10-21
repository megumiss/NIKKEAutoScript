"""通用模板工作台入口：模板资源管理、活动模板与历史索引校对。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dev_tools.template_studio.app import main  # noqa: E402

if __name__ == '__main__':
    main()
