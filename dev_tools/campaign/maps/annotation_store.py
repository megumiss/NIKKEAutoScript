"""标注编辑器的读写仓库：枚举地图、加载、保存、备份与连通预览。

格式判据与地图包定位在 `module/campaign/map/annotations.py`，这里只加会写盘的那一半，
所以运行期的推图任务不会引入任何改资源目录的代码路径。
保存走乐观并发：以标注文件内容的 SHA-256 作为版本（revision），版本不符抛 ConflictError，
避免两个标签页互相覆盖。同进程的读写用基类的可重入锁串行。
"""

import copy
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

if not __package__:
    # 脚本方式运行时仓库根不在默认路径上。
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from module.campaign.map.annotations import COORDINATES, AnnotationStore, ConflictError, digest, validate_annotations
from module.campaign.map.connectivity import ensure_data, write_data
from module.campaign.map.paths import local_package_dir
from module.campaign.map.terrain import _atomic_write, bake_terrain, terrain_colors


class EditableAnnotationStore(AnnotationStore):
    """在只读仓库之上提供枚举、加载与写入；标注工具与批处理都用这个类。"""

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
