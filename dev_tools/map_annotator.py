"""Local chapter-map annotation editor. Run with --map <chapter directory> or --root <map collection>."""

import argparse
import copy
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image

if __package__:
    from .map_paths import DEFAULT_MAPS_ROOT
    from .map_movement import MovementJobs
else:
    from map_paths import DEFAULT_MAPS_ROOT
    from map_movement import MovementJobs

UI = Path(__file__).with_suffix('')
COORDINATES = {'unit': 'pixel', 'origin': 'top_left', 'x': 'right', 'y': 'down'}
CATEGORIES = {'normal_collectible', 'hard_collectible', 'ground_mechanism', 'ground_elevator', 'elevator_connection'}


class ConflictError(ValueError):
    pass


def digest(content):
    """计算内容的 SHA-256，同时用于图片绑定和标注乐观并发版本。"""
    return hashlib.sha256(content).hexdigest()


def validate_annotations(document, image_hash, size):
    """校验原图坐标、几何顶点、唯一 ID 与连接引用，防止错图、越界或悬空连接入库。"""
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        raise ValueError('不支持的标注格式，需要 schema_version = 1。')
    if document.get('image') != 'map.png' or document.get('image_sha256') != image_hash:
        raise ConflictError('标注绑定的底图与当前 map.png 不一致，请使用对应版本的地图包。')
    if document.get('coordinates') != COORDINATES:
        raise ValueError('标注必须使用原图像素坐标：左上角原点，X 向右，Y 向下。')
    objects, connections = document.get('objects'), document.get('connections')
    if not isinstance(objects, list) or not isinstance(connections, list):
        raise ValueError('objects 和 connections 必须是数组。')
    if len(objects) + len(connections) > 10000:
        raise ValueError('单张地图最多保存 10000 个对象和连接。')
    identifiers = set()
    for item in objects + connections:
        if not isinstance(item, dict):
            raise ValueError('每条标注必须是对象。')
        identifier = item.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
            raise ValueError('标注 ID 只能包含英文字母、数字、下划线和连字符。')
        if identifier in identifiers:
            raise ValueError(f'重复的标注 ID：{identifier}')
        identifiers.add(identifier)
        for field, limit in [('label', 256), ('note', 4000)]:
            if not isinstance(item.get(field, ''), str) or len(item.get(field, '')) > limit:
                raise ValueError(f'{identifier} 的 {field} 必须是长度不超过 {limit} 的文字。')
        if 'color' in item and not re.fullmatch(r'#[0-9a-fA-F]{6}', str(item['color'])):
            raise ValueError(f'{identifier} 的颜色必须为 #RRGGBB。')
        if 'category' in item and (not isinstance(item['category'], str) or item['category'] not in CATEGORIES):
            raise ValueError(f'{identifier} 的标注类型无效。')
    objects_by_id = {item['id']: item for item in objects}
    for item in objects:
        category = item.get('category')
        if category == 'elevator_connection':
            raise ValueError('电梯传送关系必须保存为连接。')
        if category in ('normal_collectible', 'hard_collectible'):
            if item.get('difficulty') != category.removesuffix('_collectible'):
                raise ValueError(f'{item["id"]} 的收集品类型与难度不一致。')
        kind, points = item.get('type'), item.get('points')
        minimum = {'point': 1, 'polyline': 2, 'polygon': 3}.get(kind)
        if minimum is None or not isinstance(points, list) or not minimum <= len(points) <= 10000:
            raise ValueError(f'{item["id"]} 的几何类型或顶点数量无效。')
        if kind == 'point' and len(points) != 1:
            raise ValueError('点标注只能有一个坐标。')
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('坐标必须为 [x, y]。')
            for value, bound in zip(point, size):
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not 0 <= value <= bound - 1):
                    raise ValueError(f'{item["id"]} 有无效或超出图像范围的坐标。')
        if len({tuple(point) for point in points}) < minimum:
            raise ValueError(f'{item["id"]} 需要至少 {minimum} 个不同的顶点。')
    for item in connections:
        if item.get('from') not in objects_by_id or item.get('to') not in objects_by_id:
            raise ValueError(f'{item["id"]} 引用了不存在的对象。')
        if item['from'] == item['to'] or not isinstance(item.get('directed'), bool):
            raise ValueError(f'{item["id"]} 的连接端点或方向无效。')
        if 'category' in item:
            if item['category'] != 'elevator_connection':
                raise ValueError('连接的标注类型必须是电梯传送关系。')
            if any(objects_by_id[item[end]].get('category') != 'ground_elevator' for end in ('from', 'to')):
                raise ValueError('传送关系只能连接两部地面电梯。')


class AnnotationStore:
    def __init__(self, root):
        """固定允许访问的地图根目录，并用可重入锁保护同进程读写。"""
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f'地图目录不存在：{self.root}')
        self.lock = threading.RLock()

    def package(self, identifier):
        """解析地图标识并验证实际路径仍在根目录内，拒绝越界或非地图目录。"""
        path = (self.root / identifier).resolve()
        if not path.is_relative_to(self.root) or not (path / 'map.json').is_file():
            raise ValueError('地图包不在指定目录内，或缺少 map.json。')
        return path

    def catalog(self):
        """枚举可读地图元数据，忽略损坏记录并按修改时间返回可选地图。"""
        maps = []
        for path in sorted(self.root.rglob('map.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.resolve().is_relative_to(self.root):
                continue
            try:
                metadata = json.loads(path.read_text(encoding='utf-8'))
                if not (path.parent / 'map.png').is_file():
                    continue
                identifier = path.parent.relative_to(self.root).as_posix()
                chapter = metadata.get('chapter', '?')
                maps.append({'id': identifier, 'title': f'第 {chapter} 章 · {identifier}'})
            except (OSError, ValueError, AttributeError):
                continue
        return maps

    def metadata(self, package):
        """核对底图文件的实际尺寸、哈希和坐标约定，实拍图必须能共用相同像素坐标。"""
        metadata = json.loads((package / 'map.json').read_text(encoding='utf-8'))
        if not isinstance(metadata, dict):
            raise ValueError('map.json 必须是包含图像尺寸和哈希的对象。')
        image_hash = digest((package / 'map.png').read_bytes())
        with Image.open(package / 'map.png') as image:
            size = list(image.size)
        if metadata.get('image_sha256') != image_hash or metadata.get('size') != size:
            raise ConflictError('map.png 已变化，与 map.json 的尺寸或哈希不一致。请重新导出地图包。')
        if metadata.get('coordinates') != COORDINATES:
            raise ValueError('当前工具只支持左上角原点的原图像素坐标。')
        reference = (package / 'reference.png').is_file()
        if reference:
            with Image.open(package / 'reference.png') as image:
                if list(image.size) != size:
                    raise ValueError('reference.png 与 map.png 的尺寸不同，无法共用标注坐标。')
        return metadata, image_hash, size, reference

    def load(self, identifier):
        """在锁内读取并校验标注，缺省创建空文档，同时返回磁盘内容摘要作为版本。"""
        with self.lock:
            package = self.package(identifier)
            metadata, image_hash, size, reference = self.metadata(package)
            path = package / 'annotations.json'
            raw = path.read_bytes() if path.exists() else b''
            document = json.loads(raw) if raw else {
                'schema_version': 1, 'image': 'map.png', 'image_sha256': image_hash,
                'coordinates': COORDINATES.copy(), 'objects': [], 'connections': [],
            }
            validate_annotations(document, image_hash, size)
            return {'id': identifier, 'chapter': metadata.get('chapter'), 'size': size,
                    'path': str(package), 'reference': reference, 'annotations': document,
                    'revision': digest(raw), 'coverage_verified': metadata.get('capture', {}).get(
                        'whole_camera_domain_verified', False)}

    def save(self, identifier, document, revision):
        """校验请求版本后备份旧标注，以同目录临时文件和原子替换提交新内容。"""
        with self.lock:
            package = self.package(identifier)
            _, image_hash, size, _ = self.metadata(package)
            validate_annotations(document, image_hash, size)
            path = package / 'annotations.json'
            previous = path.read_bytes() if path.exists() else b''
            if digest(previous) != revision:
                raise ConflictError('标注文件已被其他窗口或程序修改。请先下载当前副本，再重新加载比较。')
            content = (json.dumps(copy.deepcopy(document), ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            backup = None
            if previous:
                backup = package / '.annotation_backups' / f'annotations-{time.time_ns()}.json'
                backup.parent.mkdir(exist_ok=True)
                backup.write_bytes(previous)
            # Write beside the destination so replacement remains atomic on the same filesystem.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=package, suffix='.tmp', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
            return {'revision': digest(content), 'path': str(path), 'backup': str(backup) if backup else None}


def make_server(store, port=8766, initial=None, movement=None):
    """建立仅监听本机的服务；保存和移动请求共用会话令牌，设备在子进程中执行。"""
    token = secrets.token_urlsafe(32)
    movement = movement or MovementJobs(store)

    class Handler(BaseHTTPRequestHandler):
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
                          '/editor.js': ('editor.js', 'text/javascript; charset=utf-8'),
                          '/movement.js': ('movement.js', 'text/javascript; charset=utf-8'),
                          '/editor.css': ('editor.css', 'text/css; charset=utf-8')}
                if request.path in assets:
                    name, mime = assets[request.path]
                    return self.send((UI / name).read_bytes(), mime)
                if request.path == '/api/maps':
                    return self.send({'maps': store.catalog(), 'root': str(store.root),
                                      'token': token, 'initial': initial})
                if request.path == '/api/movement':
                    return self.send(movement.status())
                if request.path == '/api/movement/preview':
                    return self.send(movement.preview(params.get('job', [''])[0]), 'image/jpeg')
                identifier = params.get('map', [''])[0]
                if request.path == '/api/map':
                    return self.send(store.load(identifier))
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
            if endpoint not in ('/api/save', '/api/movement/start', '/api/movement/stop'):
                return self.send({'error': '未找到资源。'}, status=404)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 10_000_000:
                    raise ValueError('标注文件不能为空，也不能超过 10 MB。')
                payload = json.loads(self.rfile.read(length))
                if endpoint == '/api/movement/start':
                    result = movement.start(payload)
                elif endpoint == '/api/movement/stop':
                    result = movement.stop(payload['job'])
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
        def server_close(self):
            """服务退出也必须停止本服务持有的移动进程。"""
            try:
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
    store = AnnotationStore(root)
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
