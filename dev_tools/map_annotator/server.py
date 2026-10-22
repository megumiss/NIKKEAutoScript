"""主线地图编辑器的本机 HTTP 服务与命令行入口，支持地图标注和后台任务管理。"""

import argparse
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..annotation_store import EditableAnnotationStore
from .jobs.movement import MovementJobs
from .jobs.scan import ScanJobs
from .jobs.wiki import WikiJobs

from module.campaign.map.annotations import ConflictError
from module.campaign.map.paths import DEFAULT_MAPS_ROOT

UI = Path(__file__).parent / 'static'


def make_server(store, port=8766, initial=None, movement=None, scans=None, wiki=None):
    """建立仅监听本机的服务；保存和移动请求共用会话令牌，设备在子进程中执行。"""
    token = secrets.token_urlsafe(32)
    movement = movement or MovementJobs(store)
    scans = scans or ScanJobs(store)
    wiki = wiki or WikiJobs(store)
    device_lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        """标注工具的本机 HTTP 处理器：静态页面加标注、扫描、Wiki 与移动接口。

        所有写操作都要求会话令牌，令牌随服务启动随机生成并只通过本机页面下发；
        响应统一加 no-store 与 CSP，禁止页面被嵌入或加载外部脚本。
        """

        def send(self, content, content_type='application/json; charset=utf-8', status=200):
            """统一发送 JSON 或静态资源，设置长度、禁缓存和页面资源策略。"""
            if not isinstance(content, bytes):
                content = json.dumps(content, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def allowed_host(self):
            """只接受当前本机服务的 Host，避免其他来源借用本地标注接口。"""
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            """提供编辑器、地图列表、标注和白名单图片，将版本冲突与输入错误区分返回。"""
            if not self.allowed_host():
                return self.send({'error': '不允许的访问来源。'}, status=403)
            request = urlsplit(self.path)
            params = parse_qs(request.query)
            try:
                assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                          '/static/js/editor.js': ('js/editor.js', 'text/javascript; charset=utf-8'),
                          '/static/js/movement.js': ('js/movement.js', 'text/javascript; charset=utf-8'),
                          '/static/js/scan.js': ('js/scan.js', 'text/javascript; charset=utf-8'),
                          '/static/js/wiki.js': ('js/wiki.js', 'text/javascript; charset=utf-8'),
                          '/static/js/panels.js': ('js/panels.js', 'text/javascript; charset=utf-8'),
                          '/static/js/connectivity.js': ('js/connectivity.js', 'text/javascript; charset=utf-8'),
                          '/static/css/editor.css': ('css/editor.css', 'text/css; charset=utf-8')}
                if request.path in assets:
                    name, mime = assets[request.path]
                    return self.send((UI / name).read_bytes(), mime)
                if request.path == '/api/maps':
                    return self.send({'maps': store.catalog(), 'root': str(store.root),
                                      'token': token, 'initial': initial})
                if request.path == '/api/movement':
                    return self.send(movement.status())
                if request.path == '/api/scan':
                    return self.send(scans.status())
                if request.path == '/api/wiki':
                    return self.send(wiki.status())
                if request.path == '/api/wiki/review':
                    return self.send(wiki.review())
                if request.path == '/api/movement/preview':
                    return self.send(movement.preview(params.get('job', [''])[0]), 'image/jpeg')
                identifier = params.get('map', [''])[0]
                if request.path == '/api/movement/calibration':
                    return self.send(movement.calibration(identifier, params.get('difficulty', ['normal'])[0]))
                if request.path == '/api/map':
                    return self.send(store.load(identifier))
                if request.path == '/api/backups':
                    return self.send(store.list_backups(identifier))
                if request.path == '/api/image':
                    filename = params.get('image', ['map.png'])[0]
                    if filename not in ('map.png', 'reference.png'):
                        raise ValueError('不支持的图片文件。')
                    return self.send((store.package(identifier) / filename).read_bytes(), 'image/png')
                self.send({'error': '未找到资源。'}, status=404)
            except ConflictError as exc:
                self.send({'error': str(exc)}, status=409)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.send({'error': str(exc)}, status=400)

        def do_POST(self):
            """校验会话令牌、路径及请求大小，再执行带版本检查的标注保存。"""
            if not self.allowed_host() or self.headers.get('X-Annotation-Token') != token:
                return self.send({'error': '页面会话已失效，请刷新后重试。'}, status=403)
            endpoint = urlsplit(self.path).path
            if endpoint not in ('/api/save', '/api/backup/restore', '/api/movement/start', '/api/movement/stop',
                                '/api/scan/start', '/api/scan/stop', '/api/scan/adopt', '/api/wiki/start',
                                '/api/wiki/stop', '/api/wiki/apply', '/api/connectivity/preview'):
                return self.send({'error': '未找到资源。'}, status=404)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 10_000_000:
                    raise ValueError('标注文件不能为空，也不能超过 10 MB。')
                payload = json.loads(self.rfile.read(length))
                if endpoint == '/api/connectivity/preview':
                    result = store.preview_connectivity(payload['id'], payload['annotations'], payload['revision'])
                elif endpoint == '/api/backup/restore':
                    result = store.restore_backup(payload['id'], payload['kind'], payload.get('name'))
                elif endpoint == '/api/movement/start':
                    with device_lock:
                        if scans.status().get('running'):
                            raise ValueError('地图扫描正在运行，请先停止扫描。')
                        result = movement.start(payload)
                elif endpoint == '/api/scan/start':
                    with device_lock:
                        if movement.status().get('running'):
                            raise ValueError('小队移动正在运行，请先停止移动。')
                        result = scans.start(payload)
                elif endpoint == '/api/scan/stop':
                    result = scans.stop(payload['job'])
                elif endpoint == '/api/scan/adopt':
                    with device_lock:
                        if movement.status().get('running'):
                            raise ValueError('小队移动正在运行，请先停止移动。')
                        result = scans.adopt(payload['job'])
                elif endpoint == '/api/movement/stop':
                    result = movement.stop(payload['job'])
                elif endpoint == '/api/wiki/start':
                    result = wiki.start(payload)
                elif endpoint == '/api/wiki/stop':
                    result = wiki.stop(payload['job'])
                elif endpoint == '/api/wiki/apply':
                    with device_lock:
                        if movement.status().get('running'):
                            raise ValueError('请在小队移动结束后导入。')
                        result = wiki.apply(payload)
                else:
                    result = store.save(payload['id'], payload['annotations'], payload['revision'])
                self.send(result)
            except ConflictError as exc:
                self.send({'error': str(exc)}, status=409)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.send({'error': str(exc)}, status=400)

        def log_message(self, fmt, *args):
            """只记录非成功响应，减少静态图片与轮询产生的重复日志。"""
            if len(args) > 1 and str(args[1]) not in ('200', '304'):
                super().log_message(fmt, *args)

    class Server(ThreadingHTTPServer):
        """多线程本机服务，关闭时负责连带结束本服务持有的子进程。

        扫描和移动子进程都占着游戏控制，服务退出不收尾会把游戏留在被接管的状态，
        所以 server_close 用嵌套 try/finally 保证三个任务都被尝试关闭。
        """

        def server_close(self):
            """服务退出也必须停止本服务持有的扫描和移动进程。"""
            try:
                try:
                    try:
                        wiki.close()
                    finally:
                        scans.close()
                finally:
                    movement.close()
            finally:
                super().server_close()

    return Server(('127.0.0.1', port), Handler)


def main():
    """选择地图根目录和初始地图，启动独立编辑器并在退出时关闭服务器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='Root containing chapter map packages; searched recursively.')
    parser.add_argument('--map', type=Path, help='Chapter directory containing map.png and map.json to open first.')
    parser.add_argument('--port', type=int, default=8766, help='Local port; 0 chooses an available port.')
    parser.add_argument('--no-open', action='store_true', help='Do not open the browser automatically.')
    args = parser.parse_args()
    selected = args.map.resolve() if args.map else None
    root = args.root or (selected.parent if selected else DEFAULT_MAPS_ROOT)
    store = EditableAnnotationStore(root)
    initial = None
    if selected:
        if not selected.is_relative_to(store.root):
            parser.error('--map must be inside --root.')
        initial = selected.relative_to(store.root).as_posix()
        store.load(initial)
    server = make_server(store, args.port, initial)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'Map annotation editor: {url}\nMap root: {store.root}\nPress Ctrl+C to stop.', flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
