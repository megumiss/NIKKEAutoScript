"""从完整采集包导出独立运行地图，保留原始数据和已有运行包。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def runtime_files(source):
    """按实际定位模型选择文件，分层地图保留参考帧、深度标签和已有局部标定。"""
    source = Path(source).resolve()
    metadata = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    names = ['map.png', 'map.json']
    if (source / 'annotations.json').exists():
        names.append('annotations.json')
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
        for difficulty in ('normal', 'hard'):
            directory = f'movement_calibration/{difficulty}'
            if (source / directory / 'calibration.json').exists():
                names.extend(f'{directory}/{name}' for name in ('calibration.json', 'reference.png', 'surface.png'))
    else:
        raise ValueError(f'Unsupported runtime coordinate model: {model}')
    for name in names:
        path = (source / name).resolve()
        if not path.is_relative_to(source) or not path.is_file():
            raise ValueError(f'Missing or invalid runtime file: {name}')
    return names


def export_runtime(source, destination):
    """先在临时目录校验复制结果，再发布到新路径；不覆盖已编辑的运行地图。"""
    if __package__:
        from .map_annotator import AnnotationStore
    else:
        from map_annotator import AnnotationStore
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(source):
        raise FileExistsError('Choose a new runtime directory outside the capture package')
    names = runtime_files(source)
    loaded = AnnotationStore(source.parent).load(source.name)
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
        candidate.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(export_runtime(args.source, args.destination))


if __name__ == '__main__':
    main()
