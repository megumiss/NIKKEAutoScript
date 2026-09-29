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

ROOT = Path(__file__).resolve().parents[1]
UI = Path(__file__).with_suffix('')
COORDINATES = {'unit': 'pixel', 'origin': 'top_left', 'x': 'right', 'y': 'down'}


class ConflictError(ValueError):
    pass


def digest(content):
    return hashlib.sha256(content).hexdigest()


def validate_annotations(document, image_hash, size):
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
    object_ids = {item['id'] for item in objects}
    for item in objects:
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
        if item.get('from') not in object_ids or item.get('to') not in object_ids:
            raise ValueError(f'{item["id"]} 引用了不存在的对象。')
        if item['from'] == item['to'] or not isinstance(item.get('directed'), bool):
            raise ValueError(f'{item["id"]} 的连接端点或方向无效。')


class AnnotationStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f'地图目录不存在：{self.root}')
        self.lock = threading.RLock()

    def package(self, identifier):
        path = (self.root / identifier).resolve()
        if not path.is_relative_to(self.root) or not (path / 'map.json').is_file():
            raise ValueError('地图包不在指定目录内，或缺少 map.json。')
        return path

    def catalog(self):
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


def make_server(store, port=8766, initial=None):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def send(self, content, content_type='application/json; charset=utf-8', status=200):
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
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.allowed_host():
                return self.send({'error': '不允许的访问来源。'}, status=403)
            request = urlsplit(self.path)
            params = parse_qs(request.query)
            try:
                assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                          '/editor.js': ('editor.js', 'text/javascript; charset=utf-8'),
                          '/editor.css': ('editor.css', 'text/css; charset=utf-8')}
                if request.path in assets:
                    name, mime = assets[request.path]
                    return self.send((UI / name).read_bytes(), mime)
                if request.path == '/api/maps':
                    return self.send({'maps': store.catalog(), 'root': str(store.root),
                                      'token': token, 'initial': initial})
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
            if not self.allowed_host() or self.headers.get('X-Annotation-Token') != token:
                return self.send({'error': '页面会话已失效，请刷新后重试。'}, status=403)
            if urlsplit(self.path).path != '/api/save':
                return self.send({'error': '未找到资源。'}, status=404)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 10_000_000:
                    raise ValueError('标注文件不能为空，也不能超过 10 MB。')
                payload = json.loads(self.rfile.read(length))
                result = store.save(payload['id'], payload['annotations'], payload['revision'])
                self.send(result)
            except ConflictError as exc:
                self.send({'error': str(exc)}, status=409)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.send({'error': str(exc)}, status=400)

        def log_message(self, fmt, *args):
            if len(args) > 1 and str(args[1]) not in ('200', '304'):
                super().log_message(fmt, *args)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='Root containing chapter map packages; searched recursively.')
    parser.add_argument('--map', type=Path, help='Chapter directory containing map.png and map.json to open first.')
    parser.add_argument('--port', type=int, default=8766, help='Local port; 0 chooses an available port.')
    parser.add_argument('--no-open', action='store_true', help='Do not open the browser automatically.')
    args = parser.parse_args()
    selected = args.map.resolve() if args.map else None
    root = args.root or (selected.parent if selected else ROOT / 'data' / 'chapter_maps')
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
