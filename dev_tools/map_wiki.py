"""编辑器的 Wiki 配准任务；先预览，再按冻结版本增量导入。"""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]


class WikiJobs:
    def __init__(self, store, output=None, cache=None):
        self.store = store
        self.output = Path(output or ROOT / 'log/wiki_annotation')
        self.cache = Path(cache or ROOT / 'data/wiki_collectibles')
        self.lock = threading.RLock()
        self.job = self.process = None

    def start(self, payload):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError('Wiki 标注正在匹配，请等待或停止。')
            loaded = self.store.load(payload['id'])
            if (payload.get('revision') != loaded['revision']
                    or payload.get('image_sha256') != loaded['annotations']['image_sha256']):
                raise ValueError('地图或标注已变化，请重新加载。')
            difficulty = payload.get('difficulty', 'both')
            if difficulty not in ('normal', 'hard', 'both'):
                raise ValueError('请选择普通、困难或两种难度。')
            identifier = secrets.token_hex(12)
            folder = self.output / identifier
            folder.mkdir(parents=True)
            cache = folder / 'cache'
            cache.mkdir()
            # 资源按任务隔离，取消与匹配结果不会覆盖命令行导入的缓存。
            if (self.cache / 'tree.json').exists():
                shutil.copyfile(self.cache / 'tree.json', cache / 'tree.json')
            chapter = f"chapter_{loaded['chapter']:02d}"
            if (self.cache / chapter).exists():
                shutil.copytree(self.cache / chapter, cache / chapter)
            command = [sys.executable, '-X', 'utf8', '-u', '-m', 'dev_tools.wiki_collectibles',
                       '--maps-root', loaded['path'], '--cache', str(cache), '--chapters', str(loaded['chapter']),
                       '--difficulty', difficulty, '--dry-run']
            with (folder / 'worker.log').open('w', encoding='utf-8') as stream:
                self.process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.job = dict(id=identifier, map_id=loaded['id'], chapter=loaded['chapter'], difficulty=difficulty,
                            revision=loaded['revision'], image_sha256=loaded['annotations']['image_sha256'])
            return self.status()

    def status(self):
        with self.lock:
            if self.job is None:
                return dict(state='idle', running=False, message='匹配当前地图的 Wiki 收集品，完成后预览并导入。')
            folder = self.output / self.job['id']
            progress = folder / 'cache/progress.json'
            records = json.loads(progress.read_text(encoding='utf-8')) if progress.exists() else []
            code = self.process.poll()
            running = code is None
            ready = not running and any(r.get('status') in ('complete', 'needs_review') for r in records)
            stopped = (folder / 'cache/STOP').exists()
            state = 'running' if running else 'ready' if ready else 'failed'
            if stopped:
                state = 'stopping' if running else 'cancelled'
                ready = False
            if self.job.get('imported'):
                state, ready = 'imported', False
            accepted = sum(r.get('accepted', 0) for r in records)
            total = sum(r.get('items', 0) for r in records)
            message = '正在下载攻略并匹配当前道路…' if running else f'匹配通过 {accepted}/{total}，其余需人工复核。'
            if state == 'failed':
                message = next((r['error'] for r in records if r.get('error')), 'Wiki 匹配未完成，请查看日志。')
            elif state in ('stopping', 'cancelled'):
                message = '正在停止 Wiki 匹配…' if running else 'Wiki 匹配已停止，未导入。'
            elif state == 'imported':
                message = '已增量导入通过的标注，已有标注与道路修订已保留。'
            return dict(self.job, state=state, running=running, ready=ready, message=message,
                        accepted=accepted, total=total, records=records, log_path=str(folder / 'worker.log'))

    def review(self):
        with self.lock:
            if self.job is None:
                return []
            items = []
            folder = self.output / self.job['id'] / 'cache' / f"chapter_{self.job['chapter']:02d}"
            modes = ('normal', 'hard') if self.job['difficulty'] == 'both' else (self.job['difficulty'],)
            for mode in modes:
                path = folder / mode / 'matches.json'
                if path.exists():
                    matches = json.loads(path.read_text(encoding='utf-8'))['matches']
                    items.extend(dict(number=m['number'], name=m.get('name', ''), difficulty=mode,
                                      status=m['status'], reason=m.get('reason', ''), position=m.get('position'))
                                 for m in matches)
            return items

    def apply(self, payload):
        if __package__:
            from .wiki_collectibles import save_annotations
        else:
            from wiki_collectibles import save_annotations
        with self.lock, self.store.lock:
            state = self.status()
            if not state.get('ready') or payload.get('job') != state['id']:
                raise ValueError('没有已完成且尚未导入的匹配任务。')
            loaded = self.store.load(state['map_id'])
            if loaded['revision'] != state['revision'] or payload.get('revision') != state['revision']:
                raise ValueError('匹配期间标注已变化，请重新匹配后导入。')
            if loaded['annotations']['image_sha256'] != state['image_sha256']:
                raise ValueError('底图已变化，请重新匹配。')
            receipts = []
            for record in state['records']:
                if record.get('status') not in ('complete', 'needs_review'):
                    continue
                path = self.output / state['id'] / 'cache' / f"chapter_{state['chapter']:02d}"
                matches = json.loads((path / record['difficulty'] / 'matches.json').read_text(encoding='utf-8'))
                receipts.append(save_annotations(Path(loaded['path']), record, matches['matches']))
            self.job['imported'] = receipts
            return self.status()

    def stop(self, identifier):
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('Wiki 任务已更换。')
            (self.output / identifier / 'cache/STOP').touch()
            return self.status()

    def close(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.stop(self.job['id'])
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
