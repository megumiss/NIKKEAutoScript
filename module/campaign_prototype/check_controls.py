"""Capture the current field, or exercise compact/expanded controls without moving the squad."""
import argparse

from . import goto, runtime, settings
from .map_package import MapPackage


@runtime.command
def main():
    """校验地图包后记录现场，最多十次展开与最小化循环，只检验地图控制而不移动小队。

    验证地图包后进行客户区与地图开关检查，--cycles 0 只保留初始现场证据。
    循环次数受命令参数限制，每次切换确认状态；退出时释放控制并写回执，不执行小队导航。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles', type=int, default=0)
    parser.add_argument('--tag', default='controls')
    settings.arguments(parser)
    args = parser.parse_args()
    settings.configure(args)
    if not 0 <= args.cycles <= 10:
        raise ValueError('Choose 0..10 control cycles')
    receipt = settings.output / f'{args.tag}.json'
    if receipt.exists():
        raise FileExistsError(receipt)
    package = MapPackage()
    report = {'binding': package.binding, 'cycles_completed': 0}
    window = runtime.Window()
    try:
        window.focus()
        runtime.write_image(settings.output / f'{args.tag}_before.png', goto.capture_client(window))
        for i in range(args.cycles):
            goto.map_open(window)
            runtime.write_image(settings.output / f'{args.tag}_{i:02}_expanded.png', window.capture())
            goto.map_close(window)
            runtime.write_image(settings.output / f'{args.tag}_{i:02}_compact.png', goto.capture_client(window))
            report['cycles_completed'] = i + 1
    finally:
        runtime.finish(window, receipt, report)


if __name__ == '__main__':
    main()
