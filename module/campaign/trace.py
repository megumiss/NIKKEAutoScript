"""自动推图的有界运行证据归档；复用业务截图，不增加游戏输入或截图轮询。"""

import json
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from module.campaign.vision.observation import CampaignObserver
from module.campaign.vision.matching import ROI
from module.logger import logger

FRAME_INTERVAL = 1.0
RUN_BYTES_LIMIT = 256 * 1024 * 1024
COMPLETED_RUNS_LIMIT = 10

REPLAY_HTML = '''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>自动推图运行回放</title>
<style>
body{margin:20px;background:#171b22;color:#e8edf5;font:14px system-ui}
header{display:flex;gap:12px;align-items:center;flex-wrap:wrap}button,input{min-height:44px}
input{flex:1;min-width:180px}button{cursor:pointer}img{max-width:100%;object-fit:contain}
main{display:grid;grid-template-columns:minmax(0,3fr) minmax(240px,1fr);gap:16px;margin-top:16px}
pre{white-space:pre-wrap;overflow-wrap:anywhere}#panel{image-rendering:auto}
@media(max-width:800px){main{grid-template-columns:1fr}}
</style>
<h1>自动推图运行回放</h1>
<p>图片仅对应实际采集帧；事件引用最近一帧，不代表操作已生效。方向键切帧，空格播放／暂停。</p>
<header><button id="prev" aria-label="上一帧">上一帧</button><button id="play">播放</button>
<button id="next" aria-label="下一帧">下一帧</button>
<input id="seek" aria-label="回放进度" type="range" min="0" value="0"><span id="counter"></span></header>
<main><section><img id="field" alt="游戏客户区截图"></section>
<section><img id="panel" alt="原始小地图裁图"><pre id="details"></pre></section></main>
<script src="events.js"></script><script>
const frames=events.filter(e=>e.frame), seek=document.getElementById('seek');
const counter=document.getElementById('counter'), field=document.getElementById('field');
const panel=document.getElementById('panel'), details=document.getElementById('details');
const play=document.getElementById('play');let timer=null;
seek.max=Math.max(0,frames.length-1);
function show(i){if(!frames.length){details.textContent='没有成功保存的截图，请检查 events.jsonl 和 summary.json。';return;}
i=Math.max(0,Math.min(frames.length-1,i));seek.value=i;const e=frames[i];
field.src=e.frame;panel.hidden=!e.minimap;if(e.minimap)panel.src=e.minimap;
const end=frames[i+1]?.sequence??Infinity;
const related=events.filter(x=>x.sequence>=e.sequence&&x.sequence<end);
details.textContent=JSON.stringify(related,null,2);counter.textContent=`${i+1} / ${frames.length} · ${e.elapsed}s`;
}
function stop(){clearInterval(timer);timer=null;play.textContent='播放';}
function toggle(){if(timer){stop();return;}play.textContent='暂停';timer=setInterval(()=>{
if(+seek.value>=frames.length-1){stop();return;}show(+seek.value+1);},750);}
document.getElementById('prev').onclick=()=>{stop();show(+seek.value-1);};
document.getElementById('next').onclick=()=>{stop();show(+seek.value+1);};
play.onclick=toggle;seek.oninput=()=>{stop();show(+seek.value);};
document.addEventListener('keydown',e=>{if(e.target===seek)return;
if(e.key==='ArrowLeft'){e.preventDefault();stop();show(+seek.value-1);}
if(e.key==='ArrowRight'){e.preventDefault();stop();show(+seek.value+1);}
if(e.code==='Space'){e.preventDefault();toggle();}});show(0);
</script></html>'''


def trace_value(value):
    """转换定位／操作元数据为 JSON，剔除内嵌预览图，避免重复保存大段 Base64。

    仅供诊断归档使用，不修改业务对象；数组保留数值，未知对象只记录类型，不展开配置或设备内容。
    """
    if isinstance(value, dict):
        return {str(key): trace_value(item) for key, item in value.items() if key != 'image'}
    if isinstance(value, (list, tuple)):
        return [trace_value(item) for item in value]
    if isinstance(value, np.ndarray):
        return trace_value(value.tolist())
    if isinstance(value, np.generic):
        return trace_value(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    return f'<{type(value).__name__}>'


class CampaignTrace:
    """每次执行独立保存采样画面、原始小地图和 JSONL 时间线，记录失败不影响安全闸门。

    全画面为高质量 JPEG，小地图为无损 PNG；普通帧最多每秒一张，关键事件不受限频影响。
    每次运行图片上限 256 MiB，达到上限仍写事件；正常收尾另存最后一张无损客户区画面。
    只清理本目录内已完成的旧归档，保留最近十次及全部未完成归档，不删除 last_field.png。
    """

    def __init__(self, root, metadata):
        """创建惰性记录器；start 前不建目录，允许离线假会话复用生产接口而不产生归档。"""
        self.root = Path(root)
        self.metadata = trace_value(metadata)
        self.folder = None
        self.started = None
        self.last_sample = float('-inf')
        self.last_field = None
        self.last_captured = None
        self.last_frame = None
        self.pending = None
        self.sequence = 0
        self.bytes = 0
        self.frames = 0
        self.limited = False
        self.disabled = False
        self.finished = False

    def start(self):
        """建立独立归档并清理已完成旧记录；命名含微秒与随机后缀，避免多实例覆盖。

        目录或元数据写入失败只告警并停用记录，不因此跳过窗口校验或继续不安全的游戏操作。
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            completed = sorted((path for path in self.root.iterdir()
                                if path.is_dir() and not path.is_symlink() and (path / 'summary.json').is_file()),
                               key=lambda path: path.name, reverse=True)
            for path in completed[COMPLETED_RUNS_LIMIT - 1:]:
                if path.resolve().parent != self.root.resolve():
                    continue
                try:
                    shutil.rmtree(path)
                except OSError as exc:
                    logger.warning(f'自动推图旧归档清理失败：{path}；原因：{exc}；保留并继续建立当前归档。')
            self.folder = self.root / f'{datetime.now():%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}'
            self.folder.mkdir()
            (self.folder / 'frames').mkdir()
            (self.folder / 'index.html').write_text(REPLAY_HTML, encoding='utf-8')
            (self.folder / 'run.json').write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2),
                                                encoding='utf-8')
            self.started = time.monotonic()
            self.record('run_start', metadata=self.metadata)
            logger.info(f'自动推图回放归档：{self.folder.resolve()}')
        except OSError as exc:
            self.disable(exc)

    def disable(self, error):
        """归档写入失败时单次告警并停用，保留已经成功落盘的记录供人工读取。"""
        if not self.disabled:
            logger.warning(f'自动推图回放记录不可用：{error}；停止证据写入，不改变任务执行。')
        self.disabled = True

    def capture(self, field):
        """接收既有客户区截图，限频保存；输入后第一张实际截图强制保留，不额外截图。

        原始 BGR 数组保留到下一次截图，异常收尾直接使用该证据，不在失焦／取消后再访问游戏窗口。
        """
        self.last_field = field
        self.last_captured = time.monotonic()
        pending, self.pending = self.pending, None
        self.record('input_after' if pending else 'sample', field=field, force=bool(pending), details=pending)

    def record(self, event, field=None, force=False, **details):
        """追加即刻落盘的事件，按限频与容量保存图片；事件引用最近帧并注明采集时间。

        force 仅绕过普通帧限频，不绕过图片容量上限。元数据过滤预览图与未知对象，写盘失败只告警。
        """
        if field is not None and field is not self.last_field:
            self.last_field = field
            self.last_captured = time.monotonic()
        if self.started is None or self.disabled or self.finished:
            return
        try:
            self.sequence += 1
            now = time.monotonic()
            row = dict(sequence=self.sequence, time=datetime.now().isoformat(timespec='milliseconds'),
                       elapsed=round(now - self.started, 3), event=event, **trace_value(details))
            if field is not None and (force or now - self.last_sample >= FRAME_INTERVAL) and not self.limited:
                mode, panel = CampaignObserver.minimap_mode_and_panel(field)
                if mode == 'compact':
                    x1, y1, x2, y2 = np.rint(np.asarray(ROI) * (1776 / 1920)).astype(int)
                    panel = field[y1:y2, x1:x2]
                encoded = []
                ok, image = cv2.imencode('.jpg', field, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if not ok:
                    raise OSError('客户区截图编码失败。')
                encoded.append(('frame', f'frames/{self.sequence:06d}.jpg', image))
                if panel is not None:
                    ok, image = cv2.imencode('.png', panel)
                    if not ok:
                        raise OSError('小地图截图编码失败。')
                    encoded.append(('minimap', f'frames/{self.sequence:06d}_minimap.png', image))
                size = sum(image.nbytes for _, _, image in encoded)
                if self.bytes + size > RUN_BYTES_LIMIT:
                    self.limited = True
                    logger.warning('自动推图回放图片达到 256 MiB 上限；继续记录事件，停止新增采样图片。')
                else:
                    for key, path, image in encoded:
                        (self.folder / path).write_bytes(image.tobytes())
                        row[key] = path
                    row['minimap_mode'] = mode
                    row['captured_elapsed'] = round((self.last_captured or now) - self.started, 3)
                    self.last_sample = now
                    self.bytes += size
                    self.frames += 1
                    self.last_frame = dict(sequence=self.sequence, elapsed=row['captured_elapsed'],
                                           path=row['frame'])
            row['frame_ref'] = self.last_frame
            with (self.folder / 'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        except (OSError, ValueError, TypeError, cv2.error) as exc:
            self.disable(exc)

    def after_input(self, **details):
        """记录已发送操作并标记下一张业务截图为操作后证据，不把驱动返回当作画面已停稳。"""
        self.record('input_sent', **details)
        self.pending = details

    def finish(self, error=None, **details):
        """收尾生成离线回放数据和摘要，异常时保留最后实际采集的无损画面，不发送清理输入。

        JSONL 每条即刻关闭文件，进程被强制结束时仍可读取；未完成目录不参与自动清理。
        完成标识最后写入，避免把缺少回放数据的归档误当作可清理记录。
        """
        if self.started is None or self.disabled or self.finished:
            return
        failure = None if error is None else dict(
            type=type(error).__name__, message=str(error),
            traceback=''.join(traceback.format_exception(type(error), error, error.__traceback__)))
        self.record('run_end', error=failure,
                    **details)
        if self.disabled:
            return
        try:
            if self.last_field is not None:
                ok, image = cv2.imencode('.png', self.last_field)
                if not ok:
                    raise OSError('结束画面编码失败。')
                (self.folder / 'last_field.png').write_bytes(image.tobytes())
            lines = (self.folder / 'events.jsonl').read_text(encoding='utf-8').splitlines()
            events = [json.loads(line) for line in lines]
            data = json.dumps(events, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
            (self.folder / 'events.js').write_text(f'const events = {data};\n', encoding='utf-8')
            summary = dict(metadata=self.metadata, frames=self.frames, image_bytes=self.bytes,
                           image_limit_reached=self.limited, elapsed=round(time.monotonic() - self.started, 3),
                           error=failure, **trace_value(details))
            (self.folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                    encoding='utf-8')
            self.finished = True
        except (OSError, ValueError, TypeError, cv2.error) as exc:
            self.disable(exc)
