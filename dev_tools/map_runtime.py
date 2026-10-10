"""从完整采集包导出独立运行地图，保留原始数据和已有运行包；运行包只含正式推图实际读取的数据。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

import numpy as np

if __package__:
    from .map_paths import DEFAULT_MAPS_ROOT, LOCAL_MAPS_ROOT
else:
    from map_paths import DEFAULT_MAPS_ROOT, LOCAL_MAPS_ROOT

PLANE_CACHE_KEYS = ('terrain_probability', 'projection', 'warp_size', 'origin', 'positions', 'scan_to_map')
DEPTH_RUNTIME_KEYS = ('local_surface',)
SCAN_RUNTIME_KEYS = ('version', 'chapter', 'roi', 'client', 'cell_px', 'matrix', 'warp_size', 'capture_mode',
                     'display_orientation', 'frames')
# 运行时定位只读相机、局部平面、表面编号与哈希；重绘审计、闭环报告、深度缓存指纹只服务离线重建，留在原始包里。
LAYERED_AUDIT_KEYS = ('projected_redraw', 'loop_closure', 'surface_clustering', 'display_evidence', 'limitations',
                      'depth_model', 'frame_to_map', 'base_map', 'source', 'depth_cache', 'depth_cache_sha256',
                      'code_sha256', 'validation', 'rejected_observations', 'supported_observation_fraction',
                      'tracks', 'map_render_mode', 'processing_3d', 'runtime', 'depth_directory', 'raw_frames')


def runtime_files(source):
    """按实际定位模型选择文件，分层地图保留参考帧与深度标签；移动标定在任务内临时生成，不随包导出。"""
    source = Path(source).resolve()
    metadata = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    names = ['map.png', 'map.json']
    if (source / 'annotations.json').exists():
        names.append('annotations.json')
    if (source / 'connectivity.json').exists():
        names.append('connectivity.json')
    model = metadata.get('coordinate_model')
    if model is None:
        names.append('source/map_data.npz')
    elif model == 'local_parallax':
        names.append('source/scan.json')
        scan = json.loads((source / 'source/scan.json').read_text(encoding='utf-8'))
        for index, frame in enumerate(scan['frames']):
            name = frame['file']
            if Path(name).name != name or '\\' in name or '/' in name or ':' in name:
                raise ValueError('Invalid source frame filename')
            path = source / 'source' / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != metadata['source_sha256'][name]:
                raise ValueError(f'Source frame hash mismatch: {name}')
            names.extend([f'source/{name}', f'depth/frame_{index:05d}.npz'])
    else:
        raise ValueError(f'Unsupported runtime coordinate model: {model}')
    for name in names:
        path = (source / name).resolve()
        if not path.is_relative_to(source) or not path.is_file():
            raise ValueError(f'Missing or invalid runtime file: {name}')
    return names


def _stash(package, relative, extras):
    """把将被改写的原件搬到本地 extras 目录；已有同名文件时保留带时间戳的副本。"""
    target = extras / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(f'{target.name}.{time.time_ns()}')
    shutil.move(str(package / relative), str(target))
    return target


def _write_atomic(path, writer):
    temporary = path.with_name(f'.{path.name}.tmp{path.suffix}')
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _slim_npz(package, relative, keep, extras, report):
    path = package / relative
    with np.load(path, allow_pickle=False) as data:
        present = list(data.files)
        if set(present) <= set(keep):
            return
        arrays = {name: data[name] for name in keep if name in present}
    _stash(package, relative, extras)
    _write_atomic(path, lambda temporary: np.savez_compressed(temporary, **arrays))
    report.append(dict(file=relative, dropped=sorted(set(present) - set(keep))))


def _slim_json(package, relative, extras, report, keep=None, drop=(), update=None):
    path = package / relative
    data = json.loads(path.read_text(encoding='utf-8'))
    removed = [key for key in data if (keep is not None and key not in keep) or key in drop]
    if not removed and update is None:
        return
    for key in removed:
        del data[key]
    if update is not None:
        update(data)
    _stash(package, relative, extras)
    content = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    _write_atomic(path, lambda temporary: temporary.write_bytes(content))
    report.append(dict(file=relative, dropped=removed))


def slim_package(package, extras=None):
    """把运行包精简到正式推图实际读取的数据；被替换的原件搬到本地 runtime_extras，不进入版本库。

    平面包的缓存只留道路概率与投影，分层包的深度文件只留局部表面标签，map.json/scan.json 去掉重绘审计与扫描轨迹。
    底图、标注、连通文件和参考帧不改动，现有哈希绑定保持有效；重复执行不再产生改动。
    """
    package = Path(package).resolve()
    metadata = json.loads((package / 'map.json').read_text(encoding='utf-8'))
    extras = Path(extras or LOCAL_MAPS_ROOT / 'runtime_extras' / package.name).resolve()
    report = []
    model = metadata.get('coordinate_model')
    if model is None:
        if (package / 'source/map_data.npz').is_file():
            _slim_npz(package, 'source/map_data.npz', PLANE_CACHE_KEYS, extras, report)
    elif model == 'local_parallax':
        scan_path = package / 'source/scan.json'
        before = hashlib.sha256(scan_path.read_bytes()).hexdigest()
        _slim_json(package, 'source/scan.json', extras, report, keep=SCAN_RUNTIME_KEYS)
        after = hashlib.sha256(scan_path.read_bytes()).hexdigest()
        update = None
        if after != before and metadata.get('source_sha256', {}).get('scan.json') == before:
            def update(data):
                data['source_sha256']['scan.json'] = after
        _slim_json(package, 'map.json', extras, report, drop=LAYERED_AUDIT_KEYS, update=update)
        for path in sorted((package / 'depth').glob('frame_*.npz')):
            _slim_npz(package, f'depth/{path.name}', DEPTH_RUNTIME_KEYS, extras, report)
    else:
        raise ValueError(f'Unsupported runtime coordinate model: {model}')
    if report:
        manifest = extras / 'manifest.json'
        history = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
        history.append(dict(package=str(package), slimmed_at=time.strftime('%Y-%m-%d %H:%M:%S'), files=report))
        manifest.write_text(json.dumps(history, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return dict(package=str(package), extras=str(extras), changed=len(report))


def replace_directory(candidate, destination):
    """Windows 上刚写入的目录可能被杀毒或索引短暂占用，改名失败时退避重试，最后退化为复制。"""
    for attempt in range(5):
        try:
            candidate.rename(destination)
            return
        except PermissionError:
            if attempt == 4:
                break
            time.sleep(.5 * (attempt + 1))
    shutil.copytree(candidate, destination)


def export_runtime(source, destination, lean=True):
    """先在临时目录校验复制结果，再发布到新路径；不覆盖已编辑的运行地图。默认导出即精简。"""
    if __package__:
        from .map_annotator import AnnotationStore
    else:
        from map_annotator import AnnotationStore
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(source):
        raise FileExistsError('Choose a new runtime directory outside the capture package')
    loaded = AnnotationStore(source.parent).load(source.name)
    names = runtime_files(source)
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.map-export-', dir=destination.parent) as temporary:
        temporary = Path(temporary).resolve()
        if not temporary.is_relative_to(destination.parent):
            raise ValueError('Temporary export directory escaped destination parent')
        candidate = temporary / 'package'
        candidate.mkdir()
        for name in names:
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashes[name]:
                raise ValueError(f'Source changed during runtime export: {name}')
        if 'annotations.json' not in names:
            (candidate / 'annotations.json').write_text(
                json.dumps(loaded['annotations'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        AnnotationStore(temporary).load('package')
        if lean:
            slim_package(candidate, LOCAL_MAPS_ROOT / 'runtime_extras' / destination.name)
            AnnotationStore(temporary).load('package')
        replace_directory(candidate, destination)
    return destination


def adopt_runtime(source, chapter, maps_root=None):
    """把扫描包采用为本章运行地图：已有运行包整体移到本地历史目录备份，再导出新包到同一路径。

    旧标注的坐标属于旧底图，不自动迁移；采用后需重新导入 Wiki 收集品或手工标注。导出失败时还原备份。
    """
    source = Path(source).resolve()
    maps_root = Path(maps_root or DEFAULT_MAPS_ROOT).resolve()
    metadata = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    if metadata.get('chapter') != chapter:
        raise ValueError(f'扫描包属于第 {metadata.get("chapter")} 章，不能采用为第 {chapter} 章。')
    destination = maps_root / f'chapter_{chapter:02d}'
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('采用来源不能是运行地图本身。')
    extras = LOCAL_MAPS_ROOT / 'runtime_extras' / destination.name
    backup = stashed_extras = None
    if destination.exists():
        backup = LOCAL_MAPS_ROOT / 'history' / f'replaced_{time.strftime("%Y%m%d_%H%M%S")}' / destination.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(backup))
        # 旧运行包与它拆出的 extras 一起备份，需要时能还原成完整包。
        if extras.exists():
            stashed_extras = backup.parent / 'runtime_extras' / destination.name
            stashed_extras.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(extras), str(stashed_extras))
    try:
        export_runtime(source, destination)
    except BaseException:
        if backup is not None and not destination.exists():
            shutil.move(str(backup), str(destination))
            if stashed_extras is not None and not extras.exists():
                shutil.move(str(stashed_extras), str(extras))
            shutil.rmtree(backup.parent, ignore_errors=True)
        raise
    return dict(destination=str(destination), backup=None if backup is None else str(backup))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--replace', action='store_true',
                        help='Back up an existing chapter runtime directory to local history, then export in its place.')
    parser.add_argument('--slim', type=Path, nargs='+', metavar='PACKAGE_OR_ROOT',
                        help='Slim existing runtime packages in place; removed originals go to local/runtime_extras.')
    args = parser.parse_args()
    if args.slim:
        for root in args.slim:
            targets = [root] if (root / 'map.json').is_file() else sorted(p.parent for p in root.glob('*/map.json'))
            for target in targets:
                print(json.dumps(slim_package(target), ensure_ascii=False))
        return
    if args.source is None or args.destination is None:
        parser.error('--source and --destination are required unless --slim is given.')
    if args.replace:
        chapter = json.loads((Path(args.source) / 'map.json').read_text(encoding='utf-8'))['chapter']
        if args.destination.name != f'chapter_{chapter:02d}':
            parser.error('--replace requires the destination to be the chapter runtime directory.')
        print(adopt_runtime(args.source, chapter, args.destination.parent))
    else:
        print(export_runtime(args.source, args.destination))


if __name__ == '__main__':
    main()
