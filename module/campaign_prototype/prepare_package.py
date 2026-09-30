"""将已有静态地图复制为经过校验的独立实验包，保留原地图和人工标注。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from . import runtime
from .map_package import MapPackage


@runtime.command
def main():
    """在临时目录绑定文件哈希和标定，质量校验通过后才发布新目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--chapter', type=int, required=True)
    parser.add_argument('--difficulty', choices=('normal', 'hard'), default='normal')
    parser.add_argument('--calibration', type=Path, default=Path(__file__).parent / 'assets/calibration.json')
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists():
        raise FileExistsError(f'Preserve existing package: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='campaign-import-', dir=destination.parent) as temporary:
        candidate = Path(temporary) / 'package'
        (candidate / 'source').mkdir(parents=True)
        names = ('map.json', 'map.png', 'annotations.json', 'source/map_data.npz')
        for name in names:
            shutil.copyfile(args.source / name, candidate / name)
        shutil.copyfile(args.calibration, candidate / 'calibration.json')
        if (args.source / 'reference.png').exists():
            shutil.copyfile(args.source / 'reference.png', candidate / 'reference.png')
        metadata = json.loads((candidate / 'map.json').read_text(encoding='utf-8'))
        manifest = {
            'sha256': {name: hashlib.sha256((candidate / name).read_bytes()).hexdigest()
                       for name in (*names, 'calibration.json')},
            'client': [1776, 999], 'roi': [644, 280, 1130, 742], 'coverage': 'observed_roads',
            'whole_camera_domain_verified': metadata['capture']['whole_camera_domain_verified'],
        }
        (candidate / 'validation.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        package = MapPackage(candidate, args.chapter, args.difficulty)
        candidate.rename(destination)
    print(json.dumps({'status': 'completed', 'path': str(destination), 'binding': package.binding}), flush=True)


if __name__ == '__main__':
    main()
