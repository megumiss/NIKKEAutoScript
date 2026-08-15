import sys
import threading
from multiprocessing import Event, Process

# import pyuac
from module.logger import logger
from module.webui.setting import State

# if not pyuac.isUserAdmin():
#     try:
#         pyuac.runAsAdmin(False)
#         sys.exit(0)
#     except Exception:
#         sys.exit(1)

def func(ev: threading.Event):
    import argparse
    import asyncio
    import sys

    import uvicorn

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    State.restart_event = ev

    # 子进程由父进程 fork 而来时（Linux/Docker，multiprocessing 默认 fork），
    # 会继承父进程启动时初始化的 deploy 配置缓存（如公告已读 ReadNoticeIds）。
    # 运行期的新修改只写入了子进程内存与磁盘，父进程缓存不更新；程序重启后
    # 新子进程若沿用旧缓存，即使磁盘已持久化也不会重新读取，导致已读等状态丢失。
    # 清掉缓存强制子进程从磁盘重新加载。Windows spawn 不继承内存，本操作无害。
    try:
        delattr(State, '_deploy_config_')
    except AttributeError:
        pass

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
    # parser.add_argument(
    #     "-k", "--key", type=str, help="Password of nkas. No password by default"
    # )
    parser.add_argument(
        "--electron", action="store_true", help="Runs by electron client."
    )
    # parser.add_argument(
    #     "--ssl-key", dest="ssl_key", type=str, help="SSL key file path for HTTPS support"
    # )
    # parser.add_argument(
    #     "--ssl-cert", type=str, help="SSL certificate file path for HTTPS support"
    # )
    parser.add_argument(
        "--run",
        nargs="+",
        type=str,
        help="Run nkas by config names on startup",
    )
    args, _ = parser.parse_known_args()

    host = args.host or State.deploy_config.WebuiHost or "0.0.0.0"
    port = args.port or int(State.deploy_config.WebuiPort) or 12271
    # ssl_key = args.ssl_key or State.deploy_config.WebuiSSLKey
    # ssl_cert = args.ssl_cert or State.deploy_config.WebuiSSLCert
    # ssl = ssl_key is not None and ssl_cert is not None
    State.electron = args.electron

    logger.hr("Launcher config")
    logger.attr("Host", host)
    logger.attr("Port", port)
    # logger.attr("SSL", ssl)
    logger.attr("Electron", args.electron)
    logger.attr("Reload", ev is not None)

    if State.electron:
        # https://github.com/LmeSzinc/AzurLaneAutoScript/issues/2051
        logger.info("Electron detected, remove log output to stdout")
        from module.logger import console_hdlr
        logger.removeHandler(console_hdlr)

    # if ssl_cert is None and ssl_key is not None:
    #     logger.error("SSL key provided without certificate. Please provide both SSL key and certificate.")
    # elif ssl_key is None and ssl_cert is not None:
    #     logger.error("SSL certificate provided without key. Please provide both SSL key and certificate.")

    # if ssl:
    #     uvicorn.run("module.webui.app:app", host=host, port=port, factory=True, ssl_keyfile=ssl_key, ssl_certfile=ssl_cert)
    # else:
    uvicorn.run("module.webui.app:app", host=host, port=port, factory=True)


if __name__ == "__main__":
    if State.deploy_config.EnableReload:
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
    else:
        func(None)
