"""Build immutable ZIPs from a fixed Git commit or an already-built desktop EXE."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from deploy.update_package import HISTORY, MANIFEST, VERSION, owned_path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def source_package(root, revision, output, metadata_dir=None):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])

    sha = git('rev-parse', revision + '^{commit}').decode().strip()
    data = {}
    with tempfile.TemporaryFile() as snapshot:
        subprocess.run(['git', '-C', str(root), 'archive', '--format=zip', sha], stdout=snapshot, check=True)
        snapshot.seek(0)
        with zipfile.ZipFile(snapshot) as source:
            for entry in source.infolist():
                name = entry.filename
                if entry.is_dir() or not owned_path(name):
                    continue
                if name.startswith(('webapp/', 'doc/', 'tests/', 'dev_tools/', 'webui/')) and not name.startswith(
                    'webui/dist/'
                ):
                    continue
                if name.startswith('.') or name == 'AGENTS.md':
                    continue
                data[name] = source.read(entry)
    commits = []
    for line in git('log', '-50', '--format=%H%x1f%an%x1f%aI%x1f%s', sha).decode('utf-8').splitlines():
        commit, author, date, message = line.split('\x1f', 3)
        commits.append(
            {
                'sha': commit,
                'author': author,
                'date': date,
                'message': message,
                'url': f'https://github.com/megumiss/NIKKEAutoScript/commit/{commit}',
            }
        )
    data[VERSION] = encode({'version': sha})
    data[HISTORY] = encode({'version': sha, 'commits': commits})
    data[MANIFEST] = encode({'files': {name: hashlib.sha256(value).hexdigest() for name, value in data.items()}})
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(data.items()):
            entry = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, value)
    if metadata_dir:
        Path(metadata_dir).mkdir(parents=True, exist_ok=True)
        for name in (VERSION, HISTORY, MANIFEST):
            (Path(metadata_dir) / name).write_bytes(data[name])
    return sha


def desktop_package(exe, manifest, output):
    metadata = json.loads(Path(manifest).read_text(encoding='utf-8-sig'))
    data = Path(exe).read_bytes()
    if metadata['sha256'] != hashlib.sha256(data).hexdigest() or metadata['size'] != len(data):
        raise ValueError('Desktop artifact does not match its manifest')
    if not 0 < len(data) <= 30 * 1024**2 or metadata['target'] != 'x86_64-pc-windows-msvc':
        raise ValueError('Invalid desktop artifact')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('nkas.exe', data)
        archive.writestr('nkas-desktop.json', encode(metadata))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['source', 'desktop'])
    parser.add_argument('--root', default='.')
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--output', required=True)
    parser.add_argument('--metadata-dir')
    parser.add_argument('--exe')
    parser.add_argument('--manifest')
    args = parser.parse_args()
    if args.kind == 'source':
        print(source_package(args.root, args.ref, args.output, args.metadata_dir))
    else:
        desktop_package(args.exe, args.manifest, args.output)
