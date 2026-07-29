import asyncio
import html
import json
import queue
import re
import threading

from starlette.websockets import WebSocket, WebSocketDisconnect

from module.config.utils import nkas_instance
from module.logger import HTMLConsole
from module.webui.api.routes_tasks import queue_data
from module.webui.process_manager import ProcessManager

LOG_LINE_PATTERN = re.compile(
    r'^(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+'
    r'(?P<timestamp>\d{2}:\d{2}:\d{2}\.\d{3})\s*│\s?'
    r'(?P<message>.*?)\s*$'
)
RULE_CHARACTERS = '─═ '
# Traceback borders also contain ─; corners/vertical bars tell them apart
# from logger rules, which consist of rule characters and a title only.
BOX_CHARACTERS = '╭╮╰╯│|'
LEVEL_CHIP_CLASS = {
    'DEBUG': 'lv-info',
    'INFO': 'lv-info',
    'WARNING': 'lv-warn',
    'ERROR': 'lv-err',
    'CRITICAL': 'lv-err',
}


class LogRenderer:
    """Convert queued Rich renderables into SPA log fragments.

    Fragments follow the preview markup (styles live in webui base.css):
    normal lines carry a timestamp and a level chip, horizontal rules become
    section dividers instead of long runs of box characters, and anything
    else (exit notices, tracebacks) is shown as a plain line.  A wide,
    colorless console keeps one event on a single line; one renderer per
    connection keeps capture pairings thread-safe.
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
            return (
                f'<div class="log-line {LEVEL_CHIP_CLASS[level]}">'
                f'<span class="ts">{match.group("timestamp")}</span>'
                f'<span class="lv-chip {LEVEL_CHIP_CLASS[level]}">{level}</span>'
                f'<span class="log-message">{html.escape(match.group("message"))}</span>'
                '</div>'
            )
        if ('─' in line or '═' in line) and not any(char in line for char in BOX_CHARACTERS):
            title = line.strip(RULE_CHARACTERS).strip()
            if title:
                return f'<div class="log-line section"><span class="log-message">{html.escape(title)}</span></div>'
            return '<div class="log-line separator"></div>'
        return f'<div class="log-line plain"><span class="log-message">{html.escape(line)}</span></div>'

    def render(self, renderable) -> str:
        fragments = []
        for line in self._plain(renderable).splitlines():
            if not line.strip():
                continue
            fragments.append(self._render_line(line.rstrip()))
        return ''.join(fragments)


class LogBroker:
    """Adapter over ProcessManager's bounded subscriber queues."""

    @staticmethod
    def replay(name):
        return list(ProcessManager.get_manager(name).renderables)

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
    # Subscribe before replaying so lines arriving during the replay are
    # queued; entries already covered by the replay snapshot are skipped by
    # identity, so no line is lost or duplicated.
    subscriber = LogBroker.subscribe(name)
    try:
        replay = LogBroker.replay(name)
        replayed_ids = {id(entry) for entry in replay}
        for entry in replay:
            await websocket.send_json({'type': 'log', 'html': renderer.render(entry)})
        while True:
            try:
                entry = subscriber.get_nowait()
            except queue.Empty:
                try:
                    entry = await asyncio.get_running_loop().run_in_executor(
                        None, subscriber.get, True, 0.5)
                except queue.Empty:
                    continue
            if id(entry) in replayed_ids:
                continue
            await websocket.send_json({'type': 'log', 'html': renderer.render(entry)})
    except (WebSocketDisconnect, RuntimeError):
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
    except (WebSocketDisconnect, RuntimeError):
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
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
