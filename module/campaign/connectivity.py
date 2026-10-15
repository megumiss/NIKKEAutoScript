"""章节道路连通数据及人工分隔；与定位使用的道路图像分开保存。

生产侧的地图包连通查询（map_state、原型 map_package/probe）与标注工具的连通预览共用这里的
分区计算与缓存判定，批处理入口见 dev_tools/map_connectivity.py。
"""

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw

from module.campaign.terrain import render_terrain, terrain_colors


ALGORITHM = {'name': 'road_grid_v3', 'step': 8, 'clearance': 0, 'diagonal': 'no_corner_cutting'}


def validate_edits(edits, size):
    """校验人工连通修订；任何畸形输入都在参与计算和哈希绑定之前拒绝。

    只支持 cut（分隔线）：把两块不该互通的道路切开。反向的「让两块互通」不能用画线表达，
    必须走电梯传送关系，否则分区会凭空多出一条不存在的路。
    条数、顶点数、线宽和坐标范围都有上限，修订来自浏览器端的标注工具，不能假定其规模可信；
    线宽按地图像素给（8～160），太细切不断道路，太粗会连带切掉邻近的通路。
    """
    if not isinstance(edits, list) or len(edits) > 1000:
        raise ValueError('连通修订必须是列表，最多 1000 条。')
    identifiers = set()
    for edit in edits:
        if not isinstance(edit, dict) or edit.get('operation') != 'cut':
            raise ValueError('连通修订仅支持分隔线；跨区域请使用电梯传送关系。')
        identifier, points = edit.get('id'), edit.get('points')
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100 or identifier in identifiers:
            raise ValueError('连通修订 ID 无效或重复。')
        identifiers.add(identifier)
        if not isinstance(points, list) or not 2 <= len(points) <= 2000:
            raise ValueError('连通修订至少需要两个顶点，最多 2000 个。')
        if type(edit.get('width')) is not int or not 8 <= edit['width'] <= 160:
            raise ValueError('分隔线宽度必须为 8～160 地图像素。')
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v < bound
                    for v, bound in zip(point, size)):
                raise ValueError('连通修订坐标无效或超出地图。')
        if len({tuple(p) for p in points}) < 2:
            raise ValueError('连通修订需要两个不同顶点。')


def package_road(package, metadata, annotations):
    """合成地图包的二值道路掩码，已套用人工地形修订。

    平面章节有扫描得到的道路概率缓存，优先用它（阈值 0.5）再套修订：override 为 1 表示抹掉、
    2 表示补上，这样人工修订只改局部，不影响其余像素的扫描证据。
    没有概率缓存的包退回渲染出的地形图，按 add 颜色精确比色取道路。
    """
    package = Path(package)
    with Image.open(package / 'map.png') as image:
        edited, override = render_terrain(image, annotations.get('terrain_edits', []), terrain_colors(metadata))
    cache = package / 'source/map_data.npz'
    if metadata.get('coordinate_model') is None and cache.exists():
        with np.load(cache, allow_pickle=False) as data:
            road = (data['terrain_probability'] >= .5).astype(np.uint8)
        if road.shape != (metadata['size'][1], metadata['size'][0]):
            raise ValueError('道路缓存尺寸与地图不一致。')
        override = np.asarray(override)
        road[override == 1], road[override == 2] = 0, 1
        return road
    color = terrain_colors(metadata)['add']
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    return np.all(np.asarray(edited) == rgb, axis=2).astype(np.uint8)


def binding(road, image_hash, edits):
    """连通数据的绑定指纹：底图哈希、尺寸、道路掩码哈希与修订哈希。

    连通分区是算出来的派生数据，底图、地形修订或分隔线任一变化都会让它失效。
    修订取 sort_keys 的 JSON 做哈希，保证只是键顺序不同的同一份修订不会被判成变化。
    """
    return dict(image_sha256=image_hash, size=list(road.shape[::-1]),
                road_sha256=hashlib.sha256(road.astype(np.uint8).tobytes()).hexdigest(),
                edits_sha256=hashlib.sha256(json.dumps(edits, sort_keys=True).encode()).hexdigest())


def encode_labels(labels):
    """把分区网格压成按行的游程，并附带每个分区的概要。

    整张分区网格直接存 JSON 会有几十万个数字，而道路很稀疏，游程编码能小一两个数量级。
    概要里的 center 取离质心最近的实际网格点而不是质心本身：分区可能是 L 形，质心会落在道路外，
    标注工具拿它定位就会跳到空地。概要坐标乘回 8，换成地图像素，便于直接用于显示。
    """
    rows, regions = [], []
    for row in labels:
        changes = np.flatnonzero(np.diff(np.r_[0, row, 0]))
        rows.append([[int(a), int(b), int(row[a])] for a, b in zip(changes[:-1], changes[1:]) if row[a]])
    for identifier in sorted(set(np.unique(labels)) - {0}):
        y, x = np.nonzero(labels == identifier)
        center = np.array([x.mean(), y.mean()])
        i = np.argmin(np.linalg.norm(np.column_stack([x, y]) - center, axis=1))
        regions.append(dict(id=int(identifier), nodes=len(x), center=[int(x[i] * 8), int(y[i] * 8)],
                            bounds=[int(x.min() * 8), int(y.min() * 8), int(x.max() * 8), int(y.max() * 8)]))
    return dict(rows=rows, regions=regions)


def decode_labels(data, shape):
    """把游程还原成分区网格，顺带严格校验数据结构。

    这份数据是外部文件，损坏或被手工改过时不能直接信：行数、游程必须递增不重叠、不越界、
    标签为正，任一不满足就报错。宁可让调用方重算，也不要还原出一张分区错乱的网格——
    连通判断错了，推图会往不可达的目标走。
    """
    labels = np.zeros(shape, np.int32)
    if len(data['rows']) != shape[0]:
        raise ValueError('连通数据行数不正确。')
    for y, runs in enumerate(data['rows']):
        end = 0
        for start, stop, label in runs:
            if (any(type(v) is not int for v in (start, stop, label))
                    or not end <= start < stop <= shape[1] or label <= 0):
                raise ValueError('连通数据网格无效。')
            labels[y, start:stop], end = label, stop
    return labels


def build(road, image_hash, edits):
    """计算连通数据：automatic 是纯几何结果，effective 额外套用人工分隔线。

    分区在全分辨率道路掩码上按八邻域计算，再重采样到 8 像素网格存储：只要道路像素实际连通
    就判为能走。旧的「净空大于 6 的网格四邻域」在细路上会同时产生假分裂（门槛砍掉 18% 真实
    道路、8px 采样在细斜路上漏格）和单格孤岛（吸附按零点几像素差抢走目标后误报不连通）；
    净空偏好仍由 A* 的边代价承担，不在这里设硬门槛。掩码先开 3×3 去掉孤立噪点，道路很稀疏，
    开运算不会切断实际通路。两份分区都算是为了在标注工具里看出「哪些连通是被人工切开的」，
    排查误切时需要对照。
    """
    validate_edits(edits, road.shape[::-1])
    automatic_mask = cv2.morphologyEx(road.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    _, automatic_full = cv2.connectedComponents(automatic_mask, connectivity=8)
    automatic = encode_labels(automatic_full[::8, ::8])
    cut = Image.new('L', road.shape[::-1], 0)
    draw = ImageDraw.Draw(cut)
    for edit in edits:
        draw.line([tuple(p) for p in edit['points']], fill=255, width=edit['width'], joint='curve')
    cut = np.asarray(cut)
    effective_mask = automatic_mask.copy()
    effective_mask[cut > 0] = 0
    _, effective_full = cv2.connectedComponents(effective_mask, connectivity=8)
    effective = encode_labels(effective_full[::8, ::8])
    return dict(schema_version=1, algorithm=ALGORITHM, binding=binding(road, image_hash, edits),
                automatic=automatic, effective=effective)


def read_bound(path, road, image_hash, edits):
    """读取并确认连通数据仍与当前道路、修订配套，不配套即报错。

    算法参数也参与比较：换了步长或净空阈值，旧文件里的分区就不能再用。
    读出来后两份分区都走一遍 decode_labels，顺带验证结构，避免把损坏的数据交给调用方。
    """
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if (data.get('schema_version') != 1 or data.get('algorithm') != ALGORITHM
            or data.get('binding') != binding(road, image_hash, edits)):
        raise ValueError('连通数据与当前道路或修订不一致，请在地图标注工具保存并重新计算。')
    decode_labels(data['automatic'], road[::8, ::8].shape)
    decode_labels(data['effective'], road[::8, ::8].shape)
    return data


def write_data(path, data):
    """原子写入连通数据：先写同目录临时文件再 os.replace。

    标注工具可能正在读这份文件，直接覆盖会让它读到半截 JSON；临时文件放同目录才能保证
    replace 是同一文件系统上的原子操作。finally 里清理残留，写失败不留 .tmp。
    """
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def ensure_data(package, metadata, annotations, persist=False):
    """取得与当前道路配套的连通数据：缓存可用就复用，否则重算。

    缓存不配套（底图或修订变了）和缓存损坏走同一条路——直接重算，不向调用方报错，
    因为重算的代价只是几百毫秒，而让运行期因为一份派生文件失败没有意义。
    persist 默认关闭：运行期只需要结果，写盘留给标注工具和批处理，避免任务进程去改资源目录。
    """
    road = package_road(package, metadata, annotations)
    edits = annotations.get('connectivity_edits', [])
    path = Path(package) / 'connectivity.json'
    if path.exists():
        try:
            return read_bound(path, road, metadata['image_sha256'], edits)
        except (ValueError, KeyError, TypeError):
            pass
    data = build(road, metadata['image_sha256'], edits)
    if persist:
        write_data(path, data)
    return data
