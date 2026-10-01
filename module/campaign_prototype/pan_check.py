"""One minimap pan, capturing the resulting minimap and field without moving the squad."""
import argparse
import json

from . import settings, runtime


from .probe import goto
from .map_package import MapPackage



@runtime.command
def main():
    """在展开面板内拖动一次并记录前后视野，验证小地图平移与主场景镜头的区别。"""
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
        goto.map_open(w)
        runtime.write_image(str(settings.output / f'{args.tag}_before_roi.png'), w.capture())
        w.drag_vector(args.dx, args.dy)
        runtime.pause(2)
        runtime.write_image(str(settings.output / f'{args.tag}_after_roi.png'), w.capture())
        goto.map_close(w)
        runtime.write_image(str(settings.output / f'{args.tag}_after.png'), goto.capture_client(w))
        print(json.dumps({'tag': args.tag, 'drag': [args.dx, args.dy]}), flush=True)
    finally:
        w.close()


if __name__ == '__main__':
    main()
