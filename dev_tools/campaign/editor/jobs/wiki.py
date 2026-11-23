"""编辑器的 Wiki 配准任务；先预览，再按冻结版本增量导入。"""

import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[4]


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
            command = [sys.executable, '-X', 'utf8', '-u', '-m', 'dev_tools.campaign.maps.wiki_collectibles',
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

    def _review_cache(self, map_id, difficulty):
        """选择与当前地图和难度相符的只读缓存，优先使用本次匹配的任务副本。"""
        if (self.job is not None and self.job['map_id'] == map_id and self.process.poll() is not None
                and self.job['difficulty'] in ('both', difficulty)):
            return self.output / self.job['id'] / 'cache', self.job['id']
        return self.cache, ''

    def review(self, map_id, difficulty='both'):
        """读取当前地图的 Wiki 补录队列，合并已有标注、跳过状态与全部候选坐标。

        队列以 items.json 为基准，因此缺少 matches.json 的条目仍可手工点选。候选只有绑定当前
        底图哈希时才返回；标注模板不带坐标，由编辑器确认后加入正常标注文档。
        """
        from dev_tools.campaign.maps.wiki_collectibles import build_wiki_annotation
        if difficulty not in ('normal', 'hard', 'both'):
            raise ValueError('请选择普通、困难或两种难度。')
        with self.lock:
            loaded = self.store.load(map_id)
            image_hash = loaded['annotations']['image_sha256']
            cache, job = self._review_cache(map_id, difficulty)
            chapter = f"chapter_{loaded['chapter']:02d}"
            folder = cache / chapter
            state_path = self.cache / chapter / 'review.json'
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            skipped = set(state.get('skipped', [])) if state.get('image_sha256') == image_hash else set()
            imported = {obj['id'] for obj in loaded['annotations']['objects']}
            modes = ('normal', 'hard') if difficulty == 'both' else (difficulty,)
            items = []
            for mode in modes:
                manifest_path = folder / mode / 'items.json'
                if not manifest_path.exists():
                    continue
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                matches_path = folder / mode / 'matches.json'
                matches_document = json.loads(matches_path.read_text(encoding='utf-8')) if matches_path.exists() else {}
                matches_valid = matches_document.get('image_sha256') == image_hash
                matches = {item['number']: item for item in matches_document.get('matches', [])}
                downloaded = manifest.get('downloaded', {})
                for source_item in manifest.get('items', []):
                    match = matches.get(source_item['number'], {})
                    candidates = []
                    seen = set()
                    for candidate in [match, *match.get('errors', [])]:
                        position = candidate.get('position')
                        if not matches_valid or not isinstance(position, list) or len(position) != 2:
                            continue
                        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                               or not 0 <= value <= loaded['size'][axis] - 1 for axis, value in enumerate(position)):
                            continue
                        key = (candidate.get('image_url', ''), tuple(round(value, 4) for value in position))
                        if key in seen:
                            continue
                        seen.add(key)
                        candidates.append({key: candidate[key] for key in
                                           ('position', 'road_iou', 'coarse_margin', 'parameter_spread_px',
                                            'image_url', 'status') if key in candidate})
                    candidates.sort(key=lambda candidate: candidate.get('road_iou', 0), reverse=True)
                    best = candidates[0] if candidates else {}
                    original_status = match.get('status', 'not_matched')
                    annotation_item = {**match, **best, 'status': original_status,
                                       'number': source_item['number'], 'name': source_item['name'],
                                       'images': source_item.get('images', [])}
                    annotation = build_wiki_annotation(manifest, annotation_item, reviewed=True)
                    identifier = annotation['id']
                    is_imported = identifier in imported
                    is_skipped = not is_imported and identifier in skipped
                    items.append(dict(id=identifier, number=source_item['number'], name=source_item['name'],
                                      difficulty=mode, status=original_status, reason=match.get('reason', ''),
                                      imported=is_imported, skipped=is_skipped, match_stale=bool(
                                          matches_document and not matches_valid),
                                      images=[dict(url=image['url'], role=image.get('role', 'unclassified'),
                                                   available=image['url'] in downloaded)
                                              for image in source_item.get('images', [])],
                                      candidates=candidates, annotation=annotation))
            counts = {'total': len(items),
                      'imported': sum(item['imported'] for item in items),
                      'skipped': sum(item['skipped'] for item in items)}
            counts['pending'] = counts['total'] - counts['imported'] - counts['skipped']
            return dict(job=job, map_id=map_id, chapter=loaded['chapter'], revision=loaded['revision'],
                        image_sha256=image_hash, difficulty=difficulty, items=items, counts=counts)

    def image(self, map_id, difficulty, url):
        """读取清单中明确记录的 Wiki 缓存图片，拒绝未登记 URL 和越出图片目录的路径。"""
        if difficulty not in ('normal', 'hard'):
            raise ValueError('Wiki 图片难度无效。')
        with self.lock:
            loaded = self.store.load(map_id)
            cache, _ = self._review_cache(map_id, difficulty)
            folder = cache / f"chapter_{loaded['chapter']:02d}" / difficulty
            manifest = json.loads((folder / 'items.json').read_text(encoding='utf-8'))
            relative = manifest.get('downloaded', {}).get(url)
            if not relative:
                raise ValueError('Wiki 图片未下载或不在当前清单中。')
            image_root = (folder / 'images').resolve()
            path = (folder / relative).resolve()
            if not path.is_relative_to(image_root) or not path.is_file():
                raise ValueError('Wiki 图片路径无效。')
            content = path.read_bytes()
            mime = 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg'
            return content, mime

    def skip(self, payload):
        """保存当前底图下的跳过状态，支持恢复；已导入条目不允许再标记为跳过。"""
        with self.lock:
            review = self.review(payload['id'], 'both')
            identifier = payload['item']
            item = next((item for item in review['items'] if item['id'] == identifier), None)
            if item is None:
                raise ValueError('收集品不在当前章节的 Wiki 清单中。')
            if item['imported']:
                raise ValueError('已导入的收集品不能标记为跳过。')
            skipped = bool(payload.get('skipped', True))
            path = self.cache / f"chapter_{review['chapter']:02d}" / 'review.json'
            state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            values = set(state.get('skipped', [])) if state.get('image_sha256') == review['image_sha256'] else set()
            if skipped:
                values.add(identifier)
            else:
                values.discard(identifier)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'image_sha256': review['image_sha256'], 'skipped': sorted(values)},
                                            ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(path)
            return {'item': identifier, 'skipped': skipped}

    def apply(self, payload):
        """把匹配通过的条目增量写入标注，返回导入后的状态。

        导入是对标注文件的写操作，必须同时持任务锁与仓库锁，否则标注工具的并发保存会和导入
        互相覆盖。
        三重版本校验：请求里的 revision、仓库当前 revision、匹配时冻结的 revision 必须一致，
        底图哈希也要没变——匹配期间有人改了道路修订，这批坐标就不再对应当前道路。
        导入是增量的：已有标注与道路修订保留，只追加通过的条目，所以重复导入会被 ready 挡住。
        """
        from dev_tools.campaign.maps.wiki_collectibles import save_annotations
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
