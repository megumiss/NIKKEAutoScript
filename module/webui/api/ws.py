import asyncio
import json
import queue
import threading

from starlette.websockets import WebSocket, WebSocketDisconnect

from module.config.utils import nkas_instance
from module.logger import HTMLConsole, Highlighter, WEB_THEME
from module.webui.api.routes_tasks import queue_data
from module.webui.process_manager import ProcessManager
from module.webui.setting import State
from module.webui.utils import DARK_TERMINAL_THEME, LIGHT_TERMINAL_THEME, LOG_CODE_FORMAT


class LogRenderer:
    """Render ConsoleRenderable entries to HTML fragments.

    Mirrors RichLog.render (module/webui/widgets.py) without any pywebio
    dependency, so WebSocket handlers can reuse the exact same log styling.
    One instance per connection keeps capture/export pairings thread-safe.
    """

    def __init__(self):
        self._console = HTMLConsole(
            force_terminal=False,
            force_interactive=False,
            width=120,
            color_system='truecolor',
            markup=False,
            record=True,
            safe_box=False,
            highlighter=Highlighter(),
            theme=WEB_THEME,
        )
        self._lock = threading.Lock()

    def render(self, renderable) -> str:
        terminal_theme = DARK_TERMINAL_THEME if State.theme == 'dark' else LIGHT_TERMINAL_THEME
        with self._lock:
            with self._console.capture():
                self._console.print(renderable)
            return self._console.export_html(
                theme=terminal_theme,
                clear=True,
                code_format=LOG_CODE_FORMAT,
                inline_styles=True,
            )


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
