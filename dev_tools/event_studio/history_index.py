"""历史截图索引的临时校对数据接口。"""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from filelock import FileLock
from PIL import Image

import event_studio_model as model

INDEX_FILE = Path(__file__).with_name('history_matches.json')
BACKUP_DIR = Path(model.SESSION_ROOT) / 'history_backups'


def read_index():
    raw = INDEX_FILE.read_bytes()
    return json.loads(raw.decode('utf-8-sig')), hashlib.sha256(raw).hexdigest()


def catalog():
    rules = model.load_rules()
    result = {}
    for event in model.historical_events():
        event_id = event['event_id']
        phases = {}
        event_type = event.get('event_type')
        if event_type not in rules['types']:
            continue
        for phase, label in model.get_phases(rules, event_type).items():
            scenes, templates = {}, {}
            for key, scene in model.get_scenes(rules, event_type, phase).items():
                scenes[key] = {'label': scene['label'], 'state_hint': scene.get('state_hint', '')}
                for item in scene['templates']:
                    template = templates.setdefault(item['name'], {'desc': item.get('desc', ''), 'scenes': []})
                    if key not in template['scenes']:
                        template['scenes'].append(key)
            folder = event['source_dir']
            if folder:
                for name, template in templates.items():
                    path = Path(folder) / f'{name}.png'
                    if path.is_file():
                        template['file'] = str(path)
            phases[phase] = {'label': label, 'scenes': scenes, 'templates': templates}
        result[event_id] = {'name': event.get('event_name', event_id), 'phases': phases}
    return result


def payload():
    data, revision = read_index()
    return {'data': data, 'revision': revision, 'file': str(INDEX_FILE), 'events': catalog()}


def validate_index(data):
    if not isinstance(data, dict) or set(data) != {'screenshots', 'missing'}:
        raise ValueError('索引必须包含 screenshots 和 missing 两个列表')
    result = {'screenshots': [], 'missing': []}
    events = catalog()
    grouped, checked_paths = {}, set()
    for section in result:
        if not isinstance(data[section], list):
            raise ValueError(f'{section} 必须是列表')
        for row in data[section]:
            if not isinstance(row, dict) or set(row) != {'screenshot_file', 'event_id', 'phase', 'scene', 'templates'}:
                raise ValueError('每条索引必须包含截图路径、活动 ID、阶段、场景和模板列表')
            event_id, scene, names = row['event_id'], row['scene'], row['templates']
            model.session_path(event_id)
            if not isinstance(scene, str) or not re.fullmatch(r'[a-zA-Z0-9_]+', scene):
                raise ValueError('请选择场景')
            if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch(r'[A-Z0-9_]+', n)
                                                  for n in names):
                raise ValueError('模板列表必须使用大写字母、数字和下划线组成的名称')
            event = events.get(event_id)
            if event is None:
                raise ValueError(f'{event_id}: 活动类型不在当前 YAML 中')
            phase = row['phase']
            if not isinstance(phase, str) or phase not in event['phases']:
                raise ValueError(f'{event_id}: 请选择当前 YAML 中的阶段')
            phase_info = event['phases'][phase]
            if scene not in phase_info['scenes']:
                raise ValueError(f'{event_id}: 场景 {scene} 不在当前阶段 {phase} 中')
            unknown = [name for name in names if name not in phase_info['templates']]
            if unknown:
                raise ValueError(f'{event_id}: 模板不在当前阶段 {phase} 的 YAML 清单中：{", ".join(unknown)}')
            path = row['screenshot_file']
            if path is not None and not isinstance(path, str):
                raise ValueError('截图路径必须是文本或 null')
            path = path.strip() if path else None
            path = path or None
            if path and path not in checked_paths:
                source = Path(path)
                if not source.is_absolute() or source.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
                    raise ValueError('请填写 PNG/JPG 截图的绝对路径')
                if not source.is_file():
                    raise ValueError(f'截图不存在：{path}')
                try:
                    with Image.open(source) as image:
                        if image.size != (720, 1280):
                            raise ValueError(f'截图必须是 720×1280：{source.name}')
                        image.verify()
                except OSError as error:
                    raise ValueError(f'无法读取截图：{path}') from error
                checked_paths.add(path)
            key = (os.path.normcase(os.path.normpath(path)) if path else None, event_id, phase, scene)
            if key not in grouped:
                grouped[key] = {'screenshot_file': path, 'event_id': event_id, 'phase': phase,
                                'scene': scene, 'templates': []}
                result['screenshots' if path else 'missing'].append(grouped[key])
            for name in names:
                if name not in grouped[key]['templates']:
                    grouped[key]['templates'].append(name)
    return result


def save_index(data, revision):
    data = validate_index(data)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with FileLock(str(BACKUP_DIR / '.save.lock')):
        raw = INDEX_FILE.read_bytes()
        if hashlib.sha256(raw).hexdigest() != revision:
            raise ValueError('索引已被其它页面或程序修改；请先下载当前修改，再重新读取文件')
        with tempfile.NamedTemporaryFile(prefix='history-', suffix='.json', dir=BACKUP_DIR, delete=False) as backup:
            backup.write(raw)
            backup_path = backup.name
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=INDEX_FILE.parent,
                                             prefix='.history-', suffix='.tmp', delete=False) as output:
                temporary = output.name
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.write('\n')
            os.replace(temporary, INDEX_FILE)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        saved = INDEX_FILE.read_bytes()
        return {'data': data, 'revision': hashlib.sha256(saved).hexdigest(), 'backup': backup_path}


def template_file(event_id, name):
    model.session_path(event_id)
    if not re.fullmatch(r'[A-Z0-9_]+', name):
        raise ValueError('无效模板名')
    folder = model.event_assets_source(event_id)
    return os.path.join(folder, f'{name}.png') if folder else None
