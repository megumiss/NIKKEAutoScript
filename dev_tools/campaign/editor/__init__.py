"""主线地图编辑器包；对外保留本机服务工厂与命令行入口。"""

from .server import main, make_server

__all__ = ['main', 'make_server']
