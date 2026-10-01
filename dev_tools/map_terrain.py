"""人工道路修订的校验与栅格导出；原始地图和扫描证据保持独立。"""

import math

from PIL import Image, ImageDraw


def validate_terrain_edits(edits, size):
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


def terrain_colors(metadata):
    if metadata.get('coordinate_model') in ('local_parallax', 'orthographic_surfaces'):
        return {'add': '#3b8bba', 'erase': '#1c232c'}
    return {'add': '#2d8dc7', 'erase': '#0f141c'}


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
