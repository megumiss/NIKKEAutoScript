"""章节标注的格式校验与地图包定位。

标注坐标绑定底图，所以读到的文档必须先过 `validate_annotations`：底图摘要、尺寸或坐标约定不符
说明标注与地图包不是同一版本，宁可报错也不要把错位的目标点喂给导航。
这里只做读取与校验，不写盘——运行期不改资源目录，保存、备份与预览属于标注编辑器
（`dev_tools/campaign/maps/annotation_store.py`）。
"""

import hashlib
import json
import math
import re
import threading
from pathlib import Path

from PIL import Image

from module.campaign.map.connectivity import validate_edits
from module.campaign.map.terrain import validate_terrain_edits

COORDINATES = {'unit': 'pixel', 'origin': 'top_left', 'x': 'right', 'y': 'down'}
CATEGORIES = {'normal_collectible', 'hard_collectible', 'ground_mechanism', 'ground_elevator',
              'ground_barrier', 'elevator_connection'}


class ConflictError(ValueError):
    """标注已被别处改动，本次请求基于过期版本。

    单独一个类型是为了让 HTTP 层把它映射成 409 而不是 400：前端收到 409 的处理是重新加载
    地图再改，和参数错误的处理完全不同。
    """


def digest(content):
    """计算内容的 SHA-256，同时用于图片绑定和标注乐观并发版本。"""
    return hashlib.sha256(content).hexdigest()


def validate_annotations(document, image_hash, size):
    """校验原图坐标、几何顶点、唯一 ID 与连接引用，防止错图、越界或悬空连接入库。"""
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        raise ValueError('不支持的标注格式，需要 schema_version = 1。')
    if document.get('image') != 'map.png' or document.get('image_sha256') != image_hash:
        raise ConflictError('标注绑定的底图与当前 map.png 不一致，请使用对应版本的地图包。')
    if document.get('coordinates') != COORDINATES:
        raise ValueError('标注必须使用原图像素坐标：左上角原点，X 向右，Y 向下。')
    validate_terrain_edits(document.get('terrain_edits', []), size)
    validate_edits(document.get('connectivity_edits', []), size)
    objects, connections = document.get('objects'), document.get('connections')
    if not isinstance(objects, list) or not isinstance(connections, list):
        raise ValueError('objects 和 connections 必须是数组。')
    if len(objects) + len(connections) > 10000:
        raise ValueError('单张地图最多保存 10000 个对象和连接。')
    identifiers = set()
    for item in objects + connections:
        if not isinstance(item, dict):
            raise ValueError('每条标注必须是对象。')
        identifier = item.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
            raise ValueError('标注 ID 只能包含英文字母、数字、下划线和连字符。')
        if identifier in identifiers:
            raise ValueError(f'重复的标注 ID：{identifier}')
        identifiers.add(identifier)
        for field, limit in [('label', 256), ('note', 4000)]:
            if not isinstance(item.get(field, ''), str) or len(item.get(field, '')) > limit:
                raise ValueError(f'{identifier} 的 {field} 必须是长度不超过 {limit} 的文字。')
        if 'color' in item and not re.fullmatch(r'#[0-9a-fA-F]{6}', str(item['color'])):
            raise ValueError(f'{identifier} 的颜色必须为 #RRGGBB。')
        if 'category' in item and (not isinstance(item['category'], str) or item['category'] not in CATEGORIES):
            raise ValueError(f'{identifier} 的标注类型无效。')
    objects_by_id = {item['id']: item for item in objects}
    for item in objects:
        category = item.get('category')
        if category == 'elevator_connection':
            raise ValueError('电梯传送关系必须保存为连接。')
        if category in ('normal_collectible', 'hard_collectible'):
            if item.get('difficulty') != category.removesuffix('_collectible'):
                raise ValueError(f'{item["id"]} 的收集品类型与难度不一致。')
        kind, points = item.get('type'), item.get('points')
        if category == 'ground_barrier' and (kind not in ('point', 'polyline')
                                             or not isinstance(points, list)
                                             or len(points) != (1 if kind == 'point' else 2)):
            raise ValueError(f'{item["id"]} 的光幕标注必须是中心点或两根立柱地面端点组成的线段。')
        minimum = {'point': 1, 'polyline': 2, 'polygon': 3}.get(kind)
        if minimum is None or not isinstance(points, list) or not minimum <= len(points) <= 10000:
            raise ValueError(f'{item["id"]} 的几何类型或顶点数量无效。')
        if kind == 'point' and len(points) != 1:
            raise ValueError('点标注只能有一个坐标。')
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('坐标必须为 [x, y]。')
            for value, bound in zip(point, size):
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not 0 <= value <= bound - 1):
                    raise ValueError(f'{item["id"]} 有无效或超出图像范围的坐标。')
        if len({tuple(point) for point in points}) < minimum:
            raise ValueError(f'{item["id"]} 需要至少 {minimum} 个不同的顶点。')
    for item in connections:
        if item.get('from') not in objects_by_id or item.get('to') not in objects_by_id:
            raise ValueError(f'{item["id"]} 引用了不存在的对象。')
        if item['from'] == item['to'] or not isinstance(item.get('directed'), bool):
            raise ValueError(f'{item["id"]} 的连接端点或方向无效。')
        if 'category' in item:
            if item['category'] != 'elevator_connection':
                raise ValueError('连接的标注类型必须是电梯传送关系。')
            if any(objects_by_id[item[end]].get('category') != 'ground_elevator' for end in ('from', 'to')):
                raise ValueError('传送关系只能连接两部地面电梯。')


class AnnotationStore:
    """标注仓库：在限定的地图根目录内定位章节地图包并核对底图绑定。

    所有路径都要经 package() 解析，确保请求里的地图标识不能越出根目录——编辑器会把浏览器
    请求的地图标识直接传进来，路径拼接必须当成不可信输入。
    锁在这里建好：编辑器的读写子类（`dev_tools/campaign/maps/annotation_store.py`）用它把同进程的读写串行化。
    """

    def __init__(self, root):
        """固定允许访问的地图根目录，并用可重入锁保护同进程读写。"""
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f'地图目录不存在：{self.root}')
        self.lock = threading.RLock()

    def package(self, identifier):
        """解析地图标识并验证实际路径仍在根目录内，拒绝越界或非地图目录。"""
        path = (self.root / identifier).resolve()
        if not path.is_relative_to(self.root) or not (path / 'map.json').is_file():
            raise ValueError('地图包不在指定目录内，或缺少 map.json。')
        return path

    def metadata(self, package):
        """核对底图文件的实际尺寸、哈希和坐标约定，实拍图必须能共用相同像素坐标。"""
        metadata = json.loads((package / 'map.json').read_text(encoding='utf-8'))
        if not isinstance(metadata, dict):
            raise ValueError('map.json 必须是包含图像尺寸和哈希的对象。')
        image_hash = digest((package / 'map.png').read_bytes())
        with Image.open(package / 'map.png') as image:
            size = list(image.size)
        if metadata.get('image_sha256') != image_hash or metadata.get('size') != size:
            raise ConflictError('map.png 已变化，与 map.json 的尺寸或哈希不一致。请重新导出地图包。')
        coordinates = metadata.get('coordinates')
        if coordinates != COORDINATES:
            raise ValueError('当前工具只支持左上角原点的原图像素坐标。')
        reference = (package / 'reference.png').is_file()
        if reference:
            with Image.open(package / 'reference.png') as image:
                if list(image.size) != size:
                    raise ValueError('reference.png 与 map.png 的尺寸不同，无法共用标注坐标。')
        return metadata, image_hash, size, reference
