"""Pan the field camera once, without issuing a movement click."""
import argparse
import json

from . import settings, runtime


from .probe import goto
from .map_package import MapPackage



@runtime.command
def main():
    """在主场景安全内框执行单次镜头拖动，保存前后地图证据并返回紧凑态。

    对已确认的客户区执行单次主场景拖动，端点必须位于有效场景区域。
    重新采集地图观测和客户区证据并恢复紧凑地图；输入失败或身份异常会通过统一命令契约返回。
    """
    p = argparse.ArgumentParser()
    p.add_argument('--tag', required=True)
    p.add_argument('--dx', type=float, required=True)
    p.add_argument('--dy', type=float, required=True)
    settings.arguments(p)
    args = p.parse_args()
    settings.configure(args)
    if (settings.output / f'{args.tag}_after.png').exists():
        raise FileExistsError('Choose a new evidence tag')
    MapPackage()
    w = runtime.Window(goto.ARGS)
    try:
        w.focus()
        goto.map_close(w)
        if goto.battle_popup_score(goto.capture_client(w)) > .8:
            raise RuntimeError('Battle popup; no camera pan')
        runtime.write_image(str(settings.output / f'{args.tag}_before.png'), goto.capture_client(w))
        x, y = w.gui.ClientToScreen(w.hwnd, (0, 0))
        a = (1000 - round(args.dx / 2), 500 - round(args.dy / 2))
        b = (1000 + round(args.dx / 2), 500 + round(args.dy / 2))
        if any(not (300 < px < 1450 and 220 < py < 820) for px, py in (a, b)):
            raise ValueError('Pan outside field interior')
        w.handler.mouse_swipe((x + a[0], y + a[1]), (x + b[0], y + b[1]))
        if w.handler._failures:
            raise RuntimeError('Field pan failed')
        runtime.pause(2)
        runtime.write_image(str(settings.output / f'{args.tag}_after.png'), goto.capture_client(w))
        goto.map_open(w)
        runtime.write_image(str(settings.output / f'{args.tag}_roi.png'), w.capture())
        runtime.write_image(str(settings.output / f'{args.tag}_expanded.png'), goto.capture_client(w))
        goto.map_close(w)
        print(json.dumps({'tag': args.tag, 'drag': [args.dx, args.dy]}), flush=True)
    finally:
        w.close()


if __name__ == '__main__':
    main()
