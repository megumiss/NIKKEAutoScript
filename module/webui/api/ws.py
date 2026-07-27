import asyncio
import json
import queue

from rich.console import Console
from starlette.websockets import WebSocket, WebSocketDisconnect

from module.config.utils import nkas_instance
from module.webui.api.routes_tasks import queue_data
from module.webui.process_manager import ProcessManager


def _render(log):
    console = Console(record=True, width=120)
    with console.capture():
        console.print(log)
    return console.export_html(inline_styles=True, clear=True)


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
    for entry in LogBroker.replay(name):
        await websocket.send_json({'type': 'log', 'html': _render(entry)})
    subscriber = LogBroker.subscribe(name)
    try:
        while True:
            entry = await asyncio.get_running_loop().run_in_executor(None, subscriber.get)
            await websocket.send_json({'type': 'log', 'html': _render(entry)})
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
