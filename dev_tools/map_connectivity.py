"""章节连通数据的批处理入口：遍历地图目录，逐章重算并写回 connectivity.json。

计算与缓存判定在 module/campaign/connectivity.py，标注工具与生产侧地图包都直接引用那里。
"""

import argparse
import json
import sys
from pathlib import Path

if not __package__:
    # 脚本方式运行时仓库根不在默认路径上。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from annotation_store import EditableAnnotationStore
else:
    from .annotation_store import EditableAnnotationStore

from module.campaign.connectivity import ensure_data


def main():
    """批量为地图目录下每个章节重算并写入连通数据，逐行打印分区数量。

    按行输出 JSON 并 flush，方便在标注工具里实时显示进度。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('data/resources/maps'))
    args = parser.parse_args()
    store = EditableAnnotationStore(args.root)
    for item in store.catalog():
        package = store.package(item['id'])
        loaded = store.load(item['id'])
        metadata = json.loads((package / 'map.json').read_text(encoding='utf-8'))
        data = ensure_data(package, metadata, loaded['annotations'], persist=True)
        print(json.dumps(dict(map=item['id'], automatic=len(data['automatic']['regions']),
                              effective=len(data['effective']['regions'])),
                         ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
