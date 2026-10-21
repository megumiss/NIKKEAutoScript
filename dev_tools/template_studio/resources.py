"""中文模板资源的浏览、截图导入及带备份的增删改。"""
import ast
import hashlib
import io
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

import cv2
import numpy as np
from filelock import FileLock
from PIL import Image, UnidentifiedImageError

from .events import model

REPO_ROOT = Path(model.REPO_ROOT)
ASSET_ROOT = REPO_ROOT / 'assets'
MODULE_ROOT = REPO_ROOT / 'module'
WORK_ROOT = REPO_ROOT / 'tmp' / 'template_studio'
NAME_PATTERN = r'[A-Z][A-Z0-9_]*(?:\.(?:AREA|COLOR|BUTTON))?'


def within(root, relative):
    root = root.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError('路径必须位于所选资源目录内')
    return path


def feature_dir(feature):
    if not isinstance(feature, str) or not re.fullmatch(r'[a-zA-Z0-9_]+(?:/event_[a-zA-Z0-9_-]+)?', feature):
        raise ValueError('请选择有效功能目录')
    path = within(ASSET_ROOT / 'zh-CN', feature)
    if not path.is_dir():
        raise ValueError('功能目录不存在，请刷新功能列表')
    return path


def definition_path(feature):
    parts = feature.split('/')
    if len(parts) == 2:
        if parts[0] not in ('event_dated', 'event_minigame'):
            raise ValueError('此子目录没有对应的资产定义规则')
        filename = 'assets_game.py' if parts[0] == 'event_minigame' else 'assets.py'
        return within(MODULE_ROOT, f'event/{parts[1]}/{filename}')
    return within(MODULE_ROOT, f'{feature}/assets.py')


def features():
    with (REPO_ROOT / 'module/config/i18n/zh-CN.json').open(encoding='utf-8') as f:
        tasks = json.load(f)['Task']
    labels = {re.sub(r'[^a-z0-9]', '', key.lower()): value['name'] for key, value in tasks.items()}
    labels.update(ui='通用界面', handler='弹窗处理', event='活动通用', eventdated='剧情活动',
                  eventminigame='活动小游戏', warehousestats='仓库统计', conversation='咨询', daemon='后台守护',
                  challenge='挑战', gift='礼包', outpost='前哨基地', surface='地面')
    result = []
    for directory in sorted((ASSET_ROOT / 'zh-CN').iterdir()):
        if not directory.is_dir():
            continue
        folders = sorted(p for p in directory.iterdir() if p.is_dir() and p.name.startswith('event_'))
        for folder in folders or [directory]:
            feature = folder.relative_to(ASSET_ROOT / 'zh-CN').as_posix()
            feature_dir(feature)
            label = labels.get(directory.name.replace('_', ''), directory.name)
            result.append({'id': feature, 'label': f'{label} · {feature}',
                           'count': sum(p.suffix.lower() in ('.png', '.gif') for p in folder.iterdir())})
    return result


def revision(feature):
    feature_dir(feature)
    files = []
    for language in ASSET_ROOT.iterdir():
        folder = language / feature
        if language.is_dir() and folder.is_dir():
            files.extend(p for p in folder.rglob('*') if p.is_file())
    files.append(definition_path(feature))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def asset_path(feature, name):
    if not isinstance(name, str) or not re.fullmatch(NAME_PATTERN + r'\.(?:png|gif)', name):
        raise ValueError('资源名应为大写字母、数字或下划线，扩展名为 png/gif')
    return within(feature_dir(feature), name)


def read_rgb(path):
    with Image.open(path) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError('图片不能超过 4000 万像素')
        return np.asarray(image.convert('RGB')).copy()


def resource_info(path):
    image = read_rgb(path)
    mode = 'template' if path.name.startswith('TEMPLATE_') else 'button'
    bbox = model.safe_bbox_color(image)[0]
    return {'name': path.name, 'mode': mode, 'width': image.shape[1], 'height': image.shape[0],
            'box': list(bbox) if bbox else None, 'editable': path.suffix == '.png',
            'override': len(path.name.split('.')) > 2}


def list_assets(feature):
    directory = feature_dir(feature)
    items = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in ('.png', '.gif'):
            continue
        # 数字开头的捕获原图不参与资产定义生成。
        if not re.fullmatch(NAME_PATTERN + r'\.(?:png|gif)', path.name):
            continue
        item = resource_info(path)
        if not item['override']:
            item['companions'] = [p.name for p in sorted(directory.glob(path.stem + '.*.*'))
                                  if re.fullmatch(NAME_PATTERN + r'\.(?:png|gif)', p.name)]
        items.append(item)
    return {'feature': feature, 'directory': str(directory), 'definition': str(definition_path(feature)),
            'revision': revision(feature), 'assets': items}


def upload(data, name):
    if not data or len(data) > 32 * 1024 * 1024:
        raise ValueError('请导入不超过 32 MB 的 PNG/JPG 截图')
    try:
        image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError):
        raise ValueError('文件不是可读取的 PNG/JPG 截图') from None
    with image:
        if image.format not in ('PNG', 'JPEG') or image.width * image.height > 40_000_000:
            raise ValueError('请导入不超过 4000 万像素的 PNG/JPG 截图')
        try:
            image.load()
        except OSError:
            raise ValueError('截图数据损坏，请重新导入') from None
        folder = WORK_ROOT / 'uploads'
        folder.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r'[^\w.-]', '_', Path(name.replace('\\', '/')).stem)[:80] or 'screenshot'
        path = folder / f'{uuid.uuid4().hex[:12]}_{filename}.png'
        image.convert('RGB').save(path)
    return shot_info(path)


def shot_info(path):
    with Image.open(path) as image:
        return {'file': str(path.resolve()), 'name': path.name, 'width': image.width, 'height': image.height}


def uploads():
    folder = WORK_ROOT / 'uploads'
    return [shot_info(p) for p in sorted(folder.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True)]


def crop(body):
    source = Path(str(body.get('source', ''))).resolve()
    upload_root = (WORK_ROOT / 'uploads').resolve()
    if upload_root not in source.parents or not source.is_file():
        raise ValueError('请先通过工作台导入截图')
    mode = body.get('mode')
    if mode not in ('button', 'template'):
        raise ValueError('请选择 Button 或 Template')
    image = read_rgb(source)
    height, width = image.shape[:2]
    if mode == 'button' and (width, height) != (720, 1280):
        raise ValueError('Button 需要 720 × 1280 原始截图；其它尺寸可使用 Template')
    box = body.get('box')
    if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box):
        raise ValueError('裁剪框必须包含四个整数')
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height and x1 - x0 >= 2 and y1 - y0 >= 2):
        raise ValueError('裁剪框超出原图或小于 2 × 2 像素')
    output = model.render_output(image, box, mode)
    if model.safe_bbox_color(output)[0] is None:
        raise ValueError('选区全黑，无法生成有效模板')
    return output


def regenerate(feature):
    from dev_tools.button_extract import CACHE_FILE, IMPORT_EXP, ExtractCache, FileIndex, ModuleExtractor, process_file
    from module.config.config import NikkeConfig

    if Path(NikkeConfig.ASSETS_FOLDER).resolve() != ASSET_ROOT.resolve():
        raise ValueError('资源路径不一致，请在仓库根目录启动模板工作台')
    index = FileIndex(NikkeConfig.ASSETS_FOLDER)
    cache = ExtractCache(CACHE_FILE)
    extractor = ModuleExtractor(*feature.split('/'))
    expressions = [process_file(extractor.module_path, name, index, cache) for name in extractor.list_files(index)]
    source = '\n'.join(IMPORT_EXP + sorted(expressions)) + '\n'
    ast.parse(source)
    return source.encode('utf-8')


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(data)
        temp_path = Path(temporary.name)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def mutate(body, deleting=False):
    feature = body.get('feature')
    feature_dir(feature)
    name = body.get('name')
    if not deleting:
        if not isinstance(name, str):
            raise ValueError('请填写模板名称')
        name = name.strip().upper().removesuffix('.PNG')
        if body.get('mode') == 'template' and not name.startswith('TEMPLATE_'):
            name = 'TEMPLATE_' + name
        if body.get('mode') == 'button' and name.startswith('TEMPLATE_'):
            raise ValueError('Button 名称不能使用 TEMPLATE_ 前缀')
        if name.startswith('TEMPLATE_') and '.' in name:
            raise ValueError('Template 小图不支持 AREA/COLOR/BUTTON 属性覆盖后缀')
        name += '.png'
    target = asset_path(feature, name)
    output = None if deleting else crop(body)
    definition = definition_path(feature)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with FileLock(str(WORK_ROOT / '.assets.lock')):
        if body.get('revision') != revision(feature):
            raise ValueError('资源或资产定义已更新，请刷新资源列表后重新核对')
        exists = target.is_file()
        if deleting and not exists:
            raise ValueError('模板已不存在，请刷新列表')
        if not deleting:
            action = body.get('action')
            if (action not in ('add', 'overwrite') or (action == 'add' and exists)
                    or (action == 'overwrite' and not exists)):
                raise ValueError('新增/覆盖状态不匹配，请刷新并重新选择操作')
            if target.with_suffix('.gif').exists():
                raise ValueError('存在同名 GIF，请使用不同名称')
            if '.' in target.stem and not asset_path(feature, target.stem.split('.')[0] + '.png').exists():
                raise ValueError('属性覆盖图必须有同名基础 Button')
        affected = [target]
        if deleting and '.' not in target.stem:
            affected.extend(asset_path(feature, p.name) for p in sorted(target.parent.glob(target.stem + '.*.*'))
                            if re.fullmatch(NAME_PATTERN + r'\.(?:png|gif)', p.name))
        affected.append(definition)
        original = {path: path.read_bytes() if path.is_file() else None for path in affected}
        backup = WORK_ROOT / 'backups' / uuid.uuid4().hex
        backup.mkdir(parents=True)
        manifest = []
        for i, (path, data) in enumerate(original.items()):
            if data is not None:
                (backup / str(i)).write_bytes(data)
            manifest.append({'path': str(path), 'backup': str(i) if data is not None else None})
        (backup / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            if deleting:
                for path in affected[:-1]:
                    path.unlink()
            else:
                ok, encoded = cv2.imencode('.png', output[:, :, ::-1])
                if not ok:
                    raise ValueError('PNG 编码失败')
                atomic_write(target, encoded.tobytes())
            atomic_write(definition, regenerate(feature))
        except Exception:
            model.logger.exception(f'模板工作台写入失败，恢复资源与定义: {feature}/{name}')
            for path, data in original.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, data)
            raise
    model.logger.info(f'模板工作台 {"删除" if deleting else "保存"}: {feature}/{name}')
    return dict(list_assets(feature), saved_name=None if deleting else name, backup=str(backup))
