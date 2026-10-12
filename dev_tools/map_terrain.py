"""道路修订的校验与栅格化；保存时直接烘焙进 map.png 与道路概率缓存。"""

import hashlib
import math
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def validate_terrain_edits(edits, size):
    """校验人工道路修订；修订会被烘焙进底图，畸形输入必须在落盘前挡住。

    只支持 brush（画笔）与 polygon（填充）两种，各自的顶点数下限不同；除单笔上限外还有总顶点
    上限，笔数合法但总量过大时栅格化会卡住浏览器端的保存请求。
    画笔直径按原图像素给整数：修订要能逐像素复现，浮点直径在不同平台的栅格化结果不一致。
    坐标来自前端，bool 是 int 的子类所以要单独排除，否则 True 会被当成坐标 1。
    """
    if not isinstance(edits, list) or len(edits) > 10000:
        raise ValueError('道路修订必须是列表，最多 10000 笔。')
    total = 0
    for edit in edits:
        if not isinstance(edit, dict) or edit.get('operation') not in ('add', 'erase'):
            raise ValueError('道路修订操作必须为补路或擦除。')
        kind, points = edit.get('type'), edit.get('points')
        minimum = {'brush': 1, 'polygon': 3}.get(kind)
        if minimum is None or not isinstance(points, list) or not minimum <= len(points) <= 20000:
            raise ValueError('道路修订的类型或顶点数量无效。')
        total += len(points)
        if total > 200000:
            raise ValueError('道路修订总顶点数不能超过 200000。')
        if kind == 'brush':
            width = edit.get('width')
            if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 160:
                raise ValueError('画笔直径必须为 1～160 个原图像素的整数。')
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('道路修订顶点必须为 [x, y]。')
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   or not 0 <= v <= bound - 1 for v, bound in zip(point, size)):
                raise ValueError('道路修订坐标无效或超出底图范围。')
        if kind == 'polygon' and len({tuple(p) for p in points}) < 3:
            raise ValueError('道路填充至少需要三个不同顶点。')


def terrain_colors(metadata=None):
    """道路与背景的渲染颜色。

    留着 metadata 参数是为了兼容按坐标模型取色的旧调用，现在所有模型统一双色，直接忽略。
    颜色值同时是 package_road 的比色依据（分层地图靠精确比色取道路），改色必须同步改那里。
    """
    # 全模型统一双色：道路蓝 #2d8dc7，背景 #1e2b37。擦除语义是"不是路"，必须沿用背景色。
    return {'add': '#2d8dc7', 'erase': '#1e2b37'}


def render_terrain(image, edits, colors):
    """按操作顺序覆盖，掩码 0=未修订、1=擦除、2=补路，不修改原始观测概率。"""
    validate_terrain_edits(edits, image.size)
    override = Image.new('L', image.size, 0)
    draw = ImageDraw.Draw(override)
    for edit in edits:
        value = 2 if edit['operation'] == 'add' else 1
        points = [tuple(p) for p in edit['points']]
        if edit['type'] == 'polygon':
            draw.polygon(points, fill=value)
        else:
            width = edit['width']
            if len(points) > 1:
                draw.line(points, fill=value, width=width, joint='curve')
            radius = (width - 1) / 2
            for x, y in points:
                if width == 1:
                    draw.point((x, y), fill=value)
                else:
                    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=value)
    result = image.convert('RGB')
    for operation, value in [('erase', 1), ('add', 2)]:
        mask = override.point(lambda pixel: 255 if pixel == value else 0)
        result.paste(colors[operation], (0, 0, *image.size), mask)
    return result, override


def _atomic_write(path, writer):
    """在目标同目录写临时文件后原子替换，避免半截文件进入哈希绑定。"""
    path = Path(path)
    temporary = path.with_name(f'.{path.name}.tmp{path.suffix}')
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def bake_terrain(package, metadata, edits):
    """把道路修订直接写入 map.png 与平面地图的道路概率缓存，返回新的图像哈希。

    分层地图没有概率缓存，只烘焙 map.png；概率缓存的其余数组原样保留。
    """
    package = Path(package)
    with Image.open(package / 'map.png') as image:
        edited, override = render_terrain(image, edits, terrain_colors(metadata))
    cache = package / 'source' / 'map_data.npz'
    if metadata.get('coordinate_model') is None and cache.exists():
        with np.load(cache, allow_pickle=False) as data:
            arrays = {name: data[name] for name in data.files}
        if 'terrain_probability' in arrays:
            mask = np.asarray(override)
            probability = arrays['terrain_probability']
            probability[mask == 1] = 0
            probability[mask == 2] = 1
            _atomic_write(cache, lambda temporary: np.savez_compressed(temporary, **arrays))
    target = package / 'map.png'
    _atomic_write(target, edited.save)
    return hashlib.sha256(target.read_bytes()).hexdigest()
