import threading
from multiprocessing import Event, Process

from module.webui.setting import State


def func(ev: threading.Event):
    import argparse

    import uvicorn

    State.restart_event = ev

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--host',
        type=str,
        help='Host to listen. Default to WebuiHost in deploy setting',
    )
    parser.add_argument(
        '-p',
        '--port',
        type=int,
        help='Port to listen. Default to WebuiPort in deploy setting',
    )
    args, _ = parser.parse_known_args()
    host = args.host or '0.0.0.0'
    port = args.port or 12271

    uvicorn.run('module.webui.app:app', host=host, port=port, factory=True)


if __name__ == '__main__':
    # func(None)
    """
        https://blog.csdn.net/HAH_HAH/article/details/105276221
    
        通过进程启动uvicorn，然后持续调用event.wait()，检查event是否调用过set()
        在通过 git pull 更新成功后，uvicorn进程调用event.set()，这时主线程的event.wait(1)会返回True
        在杀掉uvicorn进程后，重新启动，完成在不重新编译gui.py的情况下热更新
    """
    should_exit = False
    while not should_exit:
        event = Event()
        process = Process(target=func, args=(event,))
        process.start()
        while not should_exit:
            try:
                b = event.wait(1)
            except KeyboardInterrupt:
                should_exit = True
                break
            if b:
                process.kill()
                break
            elif process.is_alive():
                continue
            else:
                should_exit = True
