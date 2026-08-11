import asyncio
import html
import json
import queue
import re
import threading

from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from module.config.utils import nkas_instance
from module.logger import HTMLConsole
from module.webui.api.routes_tasks import queue_data
from module.webui.api.log_utils import attr_value_kind, split_traceback
from module.webui.process_manager import ProcessManager

LOG_LINE_PATTERN = re.compile(
    r'^(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+'
    r'(?P<timestamp>\d{2}:\d{2}:\d{2}\.\d{3})\s*│\s?'
    r'(?P<message>.*?)\s*$'
)
HR_PATTERN = re.compile(r'^(?P<marker>===|==|--|>)\s+(?P<title>.*?)(?:\s+(?P=marker))?$')
ATTR_PATTERN = re.compile(r'^\[(?P<name>[^\]]+)]\s*(?P<value>.*)$')
LEVEL_CHIP_CLASS = {
    'DEBUG': 'lv-debug',
    'INFO': 'lv-info',
    'WARNING': 'lv-warn',
    'ERROR': 'lv-err',
    'CRITICAL': 'lv-err',
}


class LogRenderer:
    """Convert queued Rich renderables into SPA log fragments.

    Fragments follow the preview markup (styles live in webui base.css):
    Normal lines carry a timestamp and a level chip, hr/attr records become
    dedicated UI rows, and a traceback stays attached to its error summary.
    A wide, colorless console keeps one event on a single line; one renderer
    per connection keeps capture pairings thread-safe.
    """

    def __init__(self):
        self._console = HTMLConsole(
            force_terminal=False,
            force_interactive=False,
            width=1000,
            no_color=True,
            markup=False,
            safe_box=False,
        )
        self._lock = threading.Lock()

    def _plain(self, renderable) -> str:
        with self._lock:
            with self._console.capture() as capture:
                self._console.print(renderable)
            return capture.get()

    @staticmethod
    def _render_line(line: str) -> str:
        match = LOG_LINE_PATTERN.match(line)
        if match:
            level = match.group('level')
            message = match.group('message')
            hr_match = HR_PATTERN.match(message)
            if hr_match:
                marker = hr_match.group('marker')
                section_level = {'===': 0, '==': 1, '--': 2, '>': 3}[marker]
                return (
                    f'<div class="log-line section section-level-{section_level}">'
                    f'<span class="log-message">{html.escape(hr_match.group("title"))}</span>'
                    '</div>'
                )
            attr_match = ATTR_PATTERN.match(message)
            if attr_match:
                value = attr_match.group('value')
                value_kind = attr_value_kind(value)
                return (
                    f'<div class="log-line {LEVEL_CHIP_CLASS[level]} attr">'
                    f'<span class="ts">{match.group("timestamp")}</span>'
                    f'<span class="lv-chip {LEVEL_CHIP_CLASS[level]}">{level}</span>'
                    '<span class="log-message">'
                    f'<span class="log-attr-key">{html.escape(attr_match.group("name"))}:</span>'
                    f'<span class="log-attr-value attr-value-{value_kind}">{html.escape(value)}</span>'
                    '</span></div>'
                )
            return (
                f'<div class="log-line {LEVEL_CHIP_CLASS[level]}">'
                f'<span class="ts">{match.group("timestamp")}</span>'
                f'<span class="lv-chip {LEVEL_CHIP_CLASS[level]}">{level}</span>'
                f'<span class="log-message">{html.escape(message)}</span>'
                '</div>'
            )
        return f'<div class="log-line plain"><span class="log-message">{html.escape(line)}</span></div>'

    def render(self, renderable) -> str:
        lines = [line.rstrip() for line in self._plain(renderable).splitlines()]
        if lines and LOG_LINE_PATTERN.match(lines[0]):
            stack_index = next(
                (index for index, line in enumerate(lines[1:], start=1) if line == 'Traceback (most recent call last):'),
                None,
            )
            if stack_index is not None:
                message = ''.join(self._render_line(line) for line in lines[:stack_index] if line.strip())
                stack = '\n'.join(lines[stack_index:]).strip()
                primary, collapsed, _ = split_traceback(stack)
                details = ''
                if collapsed:
                    details = (
                        '<details class="log-traceback-more">'
                        '<summary>详细信息</summary>'
                        f'<pre class="log-traceback-collapsed">{html.escape(collapsed)}</pre>'
                        '</details>'
                    )
                return (
                    message
                    + '<div class="log-traceback">'
                    + details
                    + f'<pre class="log-traceback-primary">{html.escape(primary)}</pre>'
                    + '</div>'
                )

        fragments = []
        for line in lines:
            if line.strip():
                fragments.append(self._render_line(line))
        return ''.join(fragments)


class LogBroker:
    """Adapter over ProcessManager's bounded subscriber queues."""

    @staticmethod
    def replay(name):
        return ProcessManager.get_manager(name).replay_renderables()

    @staticmethod
    def subscribe(name):
        return ProcessManager.get_manager(name).subscribe_log()

    @staticmethod
    def unsubscribe(name, subscriber):
        ProcessManager.get_manager(name).unsubscribe_log(subscriber)


class StateWatcher:
    """Snapshot helper used by state WebSocket connections."""

    @staticmethod
    def states():
        return {name: ProcessManager.get_manager(name).state for name in nkas_instance()}


async def log_socket(websocket: WebSocket):
    name = websocket.path_params['name']
    if name not in nkas_instance():
        await websocket.close(code=4404)
        return
    await websocket.accept()
    renderer = LogRenderer()
    loop = asyncio.get_running_loop()
    # Subscribe before replaying so lines arriving during the replay are
    # queued; entries already covered by the replay snapshot are skipped by
    # identity, so no line is lost or duplicated.
    subscriber = LogBroker.subscribe(name)
    try:
        replay = LogBroker.replay(name)
        replayed_ids = {id(entry) for entry in replay}
        # Batch the whole replay into a single message: Rich capture is
        # synchronous CPU work, so it runs in the executor, and sending one
        # array payload lets the SPA append once instead of re-rendering per
        # line.
        if replay:
            fragments = [await loop.run_in_executor(None, renderer.render, entry) for entry in replay]
            await websocket.send_json({'type': 'log', 'html': fragments})
        while True:
            try:
                entry = subscriber.get_nowait()
            except queue.Empty:
                try:
                    entry = await loop.run_in_executor(None, subscriber.get, True, 0.5)
                except queue.Empty:
                    continue
            if id(entry) in replayed_ids:
                continue
            fragment = await loop.run_in_executor(None, renderer.render, entry)
            await websocket.send_json({'type': 'log', 'html': fragment})
    except (WebSocketDisconnect, RuntimeError, ConnectionClosed):
        pass
    finally:
        LogBroker.unsubscribe(name, subscriber)


async def state_socket(websocket: WebSocket):
    await websocket.accept()
    previous = {}
    try:
        while True:
            current = StateWatcher.states()
            for name, value in current.items():
                if previous.get(name) != value:
                    await websocket.send_json({'type': 'state', 'name': name, 'state': value})
            previous = current
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError, ConnectionClosed):
        pass


async def queue_socket(websocket: WebSocket):
    name = websocket.path_params['name']
    if name not in nkas_instance():
        await websocket.close(code=4404)
        return
    await websocket.accept()
    previous = None
    try:
        while True:
            current = queue_data(name)
            encoded = json.dumps(current, sort_keys=True)
            if encoded != previous:
                await websocket.send_json({'type': 'queue', 'name': name, **current})
                previous = encoded
            await asyncio.sleep(10)
    except (WebSocketDisconnect, RuntimeError, OSError, ConnectionClosed):
        pass
