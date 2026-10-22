"""编辑器的 Wiki 配准任务；先预览，再按冻结版本增量导入。"""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]


class WikiJobs:
    """标注工具里的 Wiki 收集品配准任务：先在子进程里匹配并预览，确认后再增量导入。

    匹配与导入分两步，是因为 Wiki 的坐标要配准到本章道路上，结果必然有需要人工复核的条目；
    先只产出 matches.json 让人看，确认后才写进标注。
    导入要求地图版本与底图哈希仍是匹配时那一份（冻结版本），中途有人编辑过就必须重新匹配，
    否则会把按旧道路配准的坐标写进新标注。
    同一时刻只跑一个任务，状态由 HTTP 线程并发访问，用可重入锁保护。
    """

    def __init__(self, store, output=None, cache=None):
        """绑定地图仓库、任务输出目录与共享的 Wiki 缓存目录，不启动进程。"""
        self.store = store
        self.output = Path(output or ROOT / 'log/wiki_annotation')
        self.cache = Path(cache or ROOT / 'data/wiki_collectibles')
        self.lock = threading.RLock()
        self.job = self.process = None

    def start(self, payload):
        """冻结地图版本并起一个只匹配不写入的子进程（--dry-run）。

        先核对请求里的 revision 与底图哈希：匹配结果要和某一版标注绑定，版本对不上直接拒绝。
        共享缓存里的页面树和本章已下载资源会复制到任务私有目录再用，这样取消任务或匹配出错都不会
        污染命令行导入时建立的缓存。
        """
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
        """汇总匹配进度与可导入状态。

        ready 表示「跑完且至少有一份可用结果」，是导入按钮的唯一依据：进程退出码为 0 也可能
        一条都没匹配上，那种情况算 failed。已停止和已导入都会把 ready 压回假，
        避免前端重复导入或在取消后仍提示可导入。
        失败时的提示取记录里第一条 error，退出码对使用者没有意义。
        """
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
        """列出本次匹配的逐条结果，供界面预览与人工复核。

        按难度分别读 matches.json；difficulty 为 both 时两份都读。
        没有任务时返回空列表而不是报错——界面在任意时刻都可能来拉一次预览。
        """
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
        """把匹配通过的条目增量写入标注，返回导入后的状态。

        导入是对标注文件的写操作，必须同时持任务锁与仓库锁，否则标注工具的并发保存会和导入
        互相覆盖。
        三重版本校验：请求里的 revision、仓库当前 revision、匹配时冻结的 revision 必须一致，
        底图哈希也要没变——匹配期间有人改了道路修订，这批坐标就不再对应当前道路。
        导入是增量的：已有标注与道路修订保留，只追加通过的条目，所以重复导入会被 ready 挡住。
        """
        from dev_tools.wiki_collectibles import save_annotations
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
        """请求停止匹配：放一个 STOP 标记让子进程在检查点自行退出。

        不杀进程是为了让它把已下载的页面与中间结果写完，下次匹配能复用缓存。
        identifier 不匹配说明前端状态过期，拒绝操作而不是停掉当前任务。
        """
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('Wiki 任务已更换。')
            (self.output / identifier / 'cache/STOP').touch()
            return self.status()

    def close(self):
        """标注工具退出时结束匹配进程：先 STOP 等 5 秒，超时再 terminate。

        这里只抓网络和文件，不碰游戏控制，所以等待时间比扫描任务短。
        """
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.stop(self.job['id'])
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
