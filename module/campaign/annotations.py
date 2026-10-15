"""章节标注的格式校验与读写仓库。

标注坐标绑定底图：`validate_annotations` 同时校验几何、ID 唯一性与连接引用，`AnnotationStore`
负责在限定根目录内读写并提供乐观并发。生产侧的地图包（`maps`、`map_state`、原型 `map_package`）
与标注工具（`dev_tools/map_annotator.py` 的本机服务）共用同一份实现，两边必须按同样的判据接受或
拒绝一份标注，否则工具里存得下的标注会在推图时被判非法。
"""

import copy
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

from module.campaign.connectivity import ensure_data, validate_edits, write_data
from module.campaign.paths import local_package_dir
from module.campaign.terrain import _atomic_write, bake_terrain, terrain_colors, validate_terrain_edits

COORDINATES = {'unit': 'pixel', 'origin': 'top_left', 'x': 'right', 'y': 'down'}
CATEGORIES = {'normal_collectible', 'hard_collectible', 'ground_mechanism', 'ground_elevator', 'elevator_connection'}


class ConflictError(ValueError):
    """标注已被别处改动，本次请求基于过期版本。

    单独一个类型是为了让 HTTP 层把它映射成 409 而不是 400：前端收到 409 的处理是重新加载
    地图再改，和参数错误的处理完全不同。
    """


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
    validate_terrain_edits(document.get('terrain_edits', []), size)
    validate_edits(document.get('connectivity_edits', []), size)
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
    """标注仓库：在限定的地图根目录内读写章节标注，并提供乐观并发控制。

    所有路径都要经 package() 解析，确保请求里的地图标识不能越出根目录——这是个接受浏览器
    请求的服务，路径拼接必须当成不可信输入。
    保存走乐观并发：以标注文件内容的 SHA-256 作为版本（revision），版本不符抛 ConflictError，
    避免两个标签页互相覆盖。同进程的读写用可重入锁串行。
    """

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
            if any(part.startswith('.') for part in path.relative_to(self.root).parts[:-1]):
                continue
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
        coordinates = metadata.get('coordinates')
        if coordinates != COORDINATES:
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
            connectivity = ensure_data(package, metadata, document, persist=True)
            return {'id': identifier, 'chapter': metadata.get('chapter'), 'size': size,
                    'coordinate_model': metadata.get('coordinate_model'),
                    'terrain_colors': terrain_colors(metadata),
                    'path': str(package), 'reference': reference, 'annotations': document, 'connectivity': connectivity,
                    'revision': digest(raw), 'coverage_verified': metadata.get('capture', {}).get(
                        'whole_camera_domain_verified', False)}

    def _backup_terrain_original(self, package, edits_count):
        """首次烘焙前把原始底图、元数据与概率缓存复制到本地备份目录，已备份则保持原样。"""
        backup = local_package_dir(package) / '.annotation_backups' / 'terrain_original'
        if backup.exists():
            return str(backup)
        backup.mkdir(parents=True)
        for name in ('map.png', 'map.json', 'annotations.json'):
            source = package / name
            if source.is_file():
                shutil.copy2(source, backup / name)
        cache = package / 'source' / 'map_data.npz'
        if cache.is_file():
            (backup / 'source').mkdir(exist_ok=True)
            shutil.copy2(cache, backup / 'source' / 'map_data.npz')
        manifest = {'backed_up_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'pending_edits': edits_count}
        (backup / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                              encoding='utf-8')
        return str(backup)

    def list_backups(self, identifier):
        """列出可恢复的历史版本：首次烘焙前的原始地形，以及历次保存的标注备份。"""
        with self.lock:
            folder = local_package_dir(self.package(identifier)) / '.annotation_backups'
            result = {'terrain_original': None, 'annotations': []}
            if not folder.is_dir():
                return result
            original = folder / 'terrain_original'
            if (original / 'map.png').is_file():
                manifest = {}
                try:
                    manifest = json.loads((original / 'manifest.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
                result['terrain_original'] = manifest
            for path in sorted(folder.glob('annotations-*.json'), reverse=True)[:50]:
                result['annotations'].append({
                    'name': path.name,
                    'saved_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(path.stat().st_mtime))})
            return result

    def restore_backup(self, identifier, kind, name=None):
        """恢复历史版本：标注备份重新绑定当前底图，原始地形备份整体回滚底图与概率缓存。

        恢复前把当前标注 stash 到备份目录；恢复后重新加载校验并重建道路连通数据。
        """
        with self.lock:
            package = self.package(identifier)
            folder = local_package_dir(package) / '.annotation_backups'
            folder.mkdir(parents=True, exist_ok=True)
            current = package / 'annotations.json'
            if current.exists():
                shutil.copy2(current, folder / f'annotations-{time.time_ns()}.json')
            if kind == 'annotations':
                if not name or not re.fullmatch(r'annotations-\d+\.json', name):
                    raise ValueError('无效的标注备份。')
                source = folder / name
                if not source.is_file():
                    raise ValueError('标注备份不存在。')
                document = json.loads(source.read_bytes())
                metadata, image_hash, size, _ = self.metadata(package)
                document['image_sha256'] = image_hash
                validate_annotations(document, image_hash, size)
                content = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
                _atomic_write(current, lambda temporary: temporary.write_bytes(content))
            elif kind == 'terrain':
                source = folder / 'terrain_original'
                if not (source / 'map.png').is_file():
                    raise ValueError('没有原始地形备份。')
                for filename in ('map.png', 'map.json', 'annotations.json'):
                    backup_file = source / filename
                    if backup_file.is_file():
                        _atomic_write(package / filename,
                                      lambda temporary, src=backup_file: shutil.copyfile(src, temporary))
                cache = source / 'source' / 'map_data.npz'
                if cache.is_file():
                    (package / 'source').mkdir(exist_ok=True)
                    _atomic_write(package / 'source' / 'map_data.npz',
                                  lambda temporary: shutil.copyfile(cache, temporary))
            else:
                raise ValueError('未知的备份类型。')
            return self.load(identifier)

    def save(self, identifier, document, revision):
        """校验请求版本后备份旧标注；道路修订直接烘焙进底图与概率缓存，再原子提交新标注。"""
        with self.lock:
            package = self.package(identifier)
            metadata, image_hash, size, _ = self.metadata(package)
            validate_annotations(document, image_hash, size)
            path = package / 'annotations.json'
            previous = path.read_bytes() if path.exists() else b''
            if digest(previous) != revision:
                raise ConflictError('标注文件已被其他窗口或程序修改。请先下载当前副本，再重新加载比较。')
            baked = 0
            if document.get('terrain_edits'):
                baked = len(document['terrain_edits'])
                self._backup_terrain_original(package, baked)
                image_hash = bake_terrain(package, metadata, document['terrain_edits'])
                metadata['image_sha256'] = image_hash
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=package, suffix='.tmp', delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write((json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, package / 'map.json')
                finally:
                    if temporary is not None and temporary.exists():
                        temporary.unlink()
                document = copy.deepcopy(document)
                document['image_sha256'] = image_hash
                document['terrain_edits'] = []
            connectivity = ensure_data(package, metadata, document)
            content = (json.dumps(copy.deepcopy(document), ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            backup = None
            if previous:
                backup = local_package_dir(package) / '.annotation_backups' / f'annotations-{time.time_ns()}.json'
                backup.parent.mkdir(parents=True, exist_ok=True)
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
            write_data(package / 'connectivity.json', connectivity)
            return {'revision': digest(content), 'path': str(path), 'backup': str(backup) if backup else None,
                    'connectivity': connectivity, 'baked': baked, 'image_sha256': image_hash}

    def preview_connectivity(self, identifier, document, revision):
        """按未保存的标注算一次连通分区，只返回结果不写盘。

        用于标注工具里即时查看分隔线的效果；仍要校验 revision 和标注合法性，
        否则预览会和保存用的是两套数据，看着对、存下来却不对。
        """
        with self.lock:
            package = self.package(identifier)
            metadata, image_hash, size, _ = self.metadata(package)
            path = package / 'annotations.json'
            if digest(path.read_bytes() if path.exists() else b'') != revision:
                raise ConflictError('标注已变化，请先重新加载地图。')
            validate_annotations(document, image_hash, size)
            return ensure_data(package, metadata, document)
