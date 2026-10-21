"""
event_template_studio 的会话模型与导出逻辑（无 GUI 依赖，CLI 与 GUI 共用）。

会话文件: tmp/event_studio/<event_id>/session.json
规则库:   dev_tools/event_studio/event_template_rules.yaml
"""
import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pprint import pformat
from textwrap import indent

import cv2
import numpy as np
import yaml
from filelock import FileLock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from module.base.utils import get_bbox, get_color  # noqa: E402
from module.config.manual_config import ManualConfig  # noqa: E402
from module.logger import logger  # noqa: E402

RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'event_template_rules.yaml')
EVENTS_FILE = os.path.join(REPO_ROOT, 'module', 'config', 'events.py')
EVENT_MODULE_ROOT = os.path.join(REPO_ROOT, 'module', 'event')
SESSION_ROOT = os.path.join(REPO_ROOT, 'tmp', 'event_studio')
EVENT_ASSETS_ROOT = os.path.join(REPO_ROOT, 'assets', 'zh-CN', 'event_dated')
ARCHIVE_ASSETS_ROOT = os.path.join(os.path.dirname(REPO_ROOT), 'NIKKEAutoScriptArchive',
                                   'assets', 'zh-CN', 'event_dated')

VALID_STATUS = ('missing', 'draft', 'confirmed', 'rejected')
VALID_MODE = ('button', 'template')


def read_events():
    with open(EVENTS_FILE, encoding='utf-8') as f:
        source = f.read()
    tree = ast.parse(source)
    node = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'EVENTS' for t in n.targets))
    events = ast.literal_eval(node)
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        raise ValueError('EVENTS 必须是活动字典列表')
    return source, node, events


def event_assets_source(event_id):
    session_path(event_id)
    for root in (EVENT_ASSETS_ROOT, ARCHIVE_ASSETS_ROOT):
        folder = os.path.join(root, event_id)
        if os.path.isdir(folder) and any(name.lower().endswith('.png') for name in os.listdir(folder)):
            return folder
    return None


def historical_events():
    _, _, events = read_events()
    return [dict(event, source_dir=event_assets_source(event['event_id'])) for event in events]


def create_event_session(event_id, event_name, base_event_id, position, phase, rules):
    """发布新资源与会话后再注册活动，失败时仅回滚本次创建的目录。"""
    session_path(event_id)
    if not isinstance(event_name, str) or not event_name.strip():
        raise ValueError('请填写活动名称')
    if type(position) is not int or position not in (1, 2):
        raise ValueError('活动序号只能选择 1 或 2')
    os.makedirs(SESSION_ROOT, exist_ok=True)
    with FileLock(os.path.join(SESSION_ROOT, '.events.lock')):
        source, node, events = read_events()
        if any(e['event_id'] == event_id for e in events):
            raise ValueError('活动 ID 已在 EVENTS 中，请使用新的活动 ID')
        base = next((e for e in events if e['event_id'] == base_event_id), None)
        if base is None:
            raise ValueError('请选择 EVENTS 中的历史活动')
        base_dir = event_assets_source(base_event_id)
        if base_dir is None:
            raise ValueError('所选历史活动在当前仓库及存档中均没有可复制的资源')
        session = new_session(event_id, base['event_type'], phase, rules)
        session.update(event_name=event_name.strip(), base_event_id=base_event_id,
                       base_assets_dir=base_dir, event_position=position)
        targets = [os.path.join(EVENT_ASSETS_ROOT, event_id), os.path.dirname(session_path(event_id)),
                   os.path.join(EVENT_MODULE_ROOT, event_id)]
        for path in targets:
            if os.path.exists(path):
                raise ValueError(f'目标目录已存在，不能覆盖: {path}')
        event = {'event_id': event_id, 'event_name': event_name.strip(), 'event_type': base['event_type'],
                 'mini_game': False, 'mini_game_play': True, 'extend': False,
                 'story_part': 'Story_2' if phase.startswith('story2') else 'Story_1',
                 'story_difficulty': 'Hard' if phase in ('hard', 'story2_hard') else 'Normal'}
        lines = source.splitlines(keepends=True)
        insertion = node.elts[position - 1].lineno - 1 if len(node.elts) >= position else node.end_lineno - 1
        lines.insert(insertion, indent(pformat(event, width=100, sort_dicts=False), '    ') + ',\n')
        updated_source = ''.join(lines)
        ast.parse(updated_source)
        created = []
        with tempfile.TemporaryDirectory(prefix='create-', dir=SESSION_ROOT) as staging:
            staged_assets = os.path.join(staging, 'assets')
            shutil.copytree(base_dir, staged_assets)
            try:
                os.makedirs(EVENT_ASSETS_ROOT, exist_ok=True)
                os.rename(staged_assets, targets[0])
                created.append(targets[0])
                os.mkdir(targets[1])
                created.append(targets[1])
                save_session(session)
                os.makedirs(EVENT_MODULE_ROOT, exist_ok=True)
                os.mkdir(targets[2])
                created.append(targets[2])
                extract_event_assets(event_id)
                # 保留历史条目的注释；若外部编辑器已保存新内容，则拒绝覆盖。
                with open(EVENTS_FILE, encoding='utf-8') as f:
                    if f.read() != source:
                        raise ValueError('EVENTS 已被其它操作更新，请重新创建')
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                                 dir=os.path.dirname(EVENTS_FILE), delete=False) as f:
                    temporary = f.name
                    f.write(updated_source)
                try:
                    os.replace(temporary, EVENTS_FILE)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            except Exception:
                logger.exception(f'创建活动 {event_id} 失败，回滚本次资源、会话和资产定义')
                for path in reversed(created):
                    if path not in targets or os.path.dirname(os.path.realpath(path)) not in (
                            os.path.realpath(EVENT_ASSETS_ROOT), os.path.realpath(SESSION_ROOT),
                            os.path.realpath(EVENT_MODULE_ROOT)):
                        raise ValueError(f'拒绝回滚范围外目录: {path}')
                    shutil.rmtree(path)
                raise
        events.insert(position - 1, event)
        ManualConfig.EVENTS[:] = events
        return session


def load_rules():
    with open(RULES_FILE, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def _merge_extras(scenes, extras):
    for extra in extras:
        scene = scenes.setdefault(extra['scene'], {'label': extra['scene'], 'templates': []})
        scene['templates'].extend(extra.get('templates', []))


def get_scenes(rules, event_type, phase):
    """
    解析某活动类型在某阶段的场景表（处理 inherit + extras）。

    Returns:
        dict: scene_key -> {label, state_hint, templates}
    """
    types = rules['types']
    entry = types[event_type]
    if 'inherit' in entry:
        base = get_scenes(rules, entry['inherit'], phase)
        scenes = {k: {'label': v['label'], 'state_hint': v.get('state_hint', ''),
                      'templates': list(v['templates'])} for k, v in base.items()}
        for extra in entry.get('extras', []):
            if extra['phase'] == phase:
                _merge_extras(scenes, [extra])
        return scenes
    phases = entry['phases']
    if phase not in phases:
        return {}
    return phases[phase]['scenes']


def get_phases(rules, event_type):
    """Returns: dict: phase_key -> label"""
    entry = rules['types'][event_type]
    if 'inherit' in entry:
        return get_phases(rules, entry['inherit'])
    return {k: v['label'] for k, v in entry['phases'].items()}


def get_event_types(rules):
    """Returns: list[tuple(int, str)]: (event_type, label)"""
    return [(int(k), v['label']) for k, v in rules['types'].items()]


def checklist(rules, event_type, phase):
    """
    展开某阶段的所需模板清单。

    Returns:
        list[dict]: name, mode, scene, desc, requires, optional
    """
    items = []
    for scene_key, scene in get_scenes(rules, event_type, phase).items():
        for t in scene['templates']:
            item = {
                'name': t['name'],
                'mode': t.get('mode', 'button'),
                'phase': phase,
                'scene': scene_key,
                'desc': t.get('desc', ''),
                'requires': t.get('requires', ''),
                'optional': bool(t.get('optional', False)),
            }
            items.append(item)
    return items


# ---------- 会话 ----------

def session_path(event_id):
    if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', event_id):
        raise ValueError('活动 ID 只能包含英文字母、数字、下划线和短横线')
    return os.path.join(SESSION_ROOT, event_id, 'session.json')


def new_session(event_id, event_type, phase, rules):
    session_path(event_id)
    if event_type not in rules['types'] or phase not in get_phases(rules, event_type):
        raise ValueError('无效的活动类型或更新阶段')
    templates = []
    for item in checklist(rules, event_type, phase):
        templates.append({
            'name': item['name'],
            'mode': item['mode'],
            'phase': phase,
            'scene': item['scene'],
            'source': None,
            'box': None,
            'status': 'missing',
            'proposer': None,
            'optional': item['optional'],
        })
    return {
        'event_id': event_id,
        'event_type': event_type,
        'phase': phase,
        'phases': [phase],
        'screenshots': [],
        'templates': templates,
    }


def session_scenes(session, rules):
    scenes = {}
    labels = get_phases(rules, session['event_type'])
    for phase in session.get('phases', [session['phase']]):
        for key, scene in get_scenes(rules, session['event_type'], phase).items():
            scenes[f'{phase}:{key}'] = {'phase': phase, 'scene': key, 'label': f'{labels[phase]} · {scene["label"]}',
                                       'state_hint': scene.get('state_hint', ''), 'templates': list(scene['templates'])}
    return scenes


def session_checklist(session, rules):
    items = {}
    for phase in session.get('phases', [session['phase']]):
        for item in checklist(rules, session['event_type'], phase):
            items[item['name']] = item
    return list(items.values())


def sync_session_rules(session, rules):
    """保留有效裁剪数据，模板范围及属性始终以当前 YAML 为准。"""
    if session['event_type'] not in rules['types']:
        raise ValueError(f'当前 YAML 不包含活动类型 {session["event_type"]}')
    existing = {t['name']: t for t in session['templates']}
    templates = []
    for item in session_checklist(session, rules):
        template = dict(existing.get(item['name'], {
            'name': item['name'], 'source': None, 'box': None, 'status': 'missing', 'proposer': None,
        }))
        if template.get('mode', item['mode']) != item['mode']:
            template['status'] = 'draft' if template.get('source') and template.get('box') else 'missing'
        template.update({key: item[key] for key in ('mode', 'phase', 'scene', 'optional')})
        templates.append(template)
    changed = session['templates'] != templates
    session['templates'] = templates
    return changed


def advance_phase(session, phase, rules):
    if phase not in get_phases(rules, session['event_type']):
        raise ValueError('无效的更新阶段')
    names = {t['name'] for t in session['templates']}
    for t in new_session(session['event_id'], session['event_type'], phase, rules)['templates']:
        if t['name'] not in names:
            session['templates'].append(t)
            names.add(t['name'])
    phases = session.setdefault('phases', [session['phase']])
    if phase not in phases:
        phases.append(phase)
    session['phase'] = phase
    sync_session_rules(session, rules)


def load_session(event_id):
    path = session_path(event_id)
    with open(path, 'r', encoding='utf-8') as f:
        session = json.load(f)
    for template in session['templates']:
        template.pop('reuse', None)
        if template['status'] in ('confirmed', 'draft') and not (template.get('source') and template.get('box')):
            template['status'] = 'missing'
    for shot in session['screenshots']:
        shot.setdefault('phase', session['phase'])
    sync_session_rules(session, load_rules())
    return session


def save_session(session):
    sync_session_rules(session, load_rules())
    path = session_path(session['event_id'])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with FileLock(path + '.lock'):
        if os.path.isfile(path):
            current = load_session(session['event_id'])
            if current.get('revision', 0) != session.get('revision', 0):
                raise ValueError('会话已被其它操作更新，请刷新后重试')
        revision = session.get('revision', 0) + 1
        updated = dict(session, revision=revision)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=os.path.dirname(path), delete=False) as f:
            temporary = f.name
            json.dump(updated, f, ensure_ascii=False, indent=2)
        try:
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        session['revision'] = revision
    return path


def find_template(session, name):
    for t in session['templates']:
        if t['name'] == name:
            return t
    return None


def add_screenshot(session, file, scene=None, state=None, note=None, phase=None):
    file = os.path.abspath(file)
    image = _load_image(file)
    if image.shape[:2] != (1280, 720):
        raise ValueError(f'请提供 720×1280 原始竖屏截图: {file}')
    phase = phase or session['phase']
    for s in session['screenshots']:
        if os.path.abspath(s['file']) == file and s.get('phase', session['phase']) == phase:
            s.update({k: v for k, v in {'scene': scene, 'state': state, 'note': note,
                                      'phase': phase}.items() if v is not None})
            return s
    entry = {'file': file, 'scene': scene or '', 'state': state or '', 'note': note or '',
             'phase': phase}
    session['screenshots'].append(entry)
    return entry


def propose(session, name, source, box, mode=None, proposer='agent'):
    """
    登记/更新一个模板提案。box: (x0, y0, x1, y1) 图像坐标。
    仅允许更新会话清单内的模板。
    """
    sync_session_rules(session, load_rules())
    name = name.upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
        raise ValueError('无效模板名')
    if mode is not None and mode not in VALID_MODE:
        raise ValueError('无效裁剪模式')
    if find_template(session, name) is None:
        raise ValueError(f'{name}: 模板不在 YAML 任务清单中')
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError(f'box too small: {(x0, y0, x1, y1)}')
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    shape = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    if shape is None:
        raise ValueError(f'cannot read image: {source}')
    h, w = shape.shape[:2]
    if (w, h) != (720, 1280):
        raise ValueError(f'请提供 720×1280 原始竖屏截图，当前为 {w}×{h}')
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise ValueError(f'box {(x0, y0, x1, y1)} out of image {w}x{h}')

    t = find_template(session, name)
    if t is not None and proposer == 'agent':
        if t['status'] == 'confirmed':
            raise ValueError(f'{name}: 已确认模板不能被 agent 覆盖')
        if t.get('scene') and not any(s['file'] == os.path.abspath(source) and s.get('scene') == t['scene']
                                      and s.get('phase', session['phase']) == t['phase']
                                      for s in session['screenshots']):
            raise ValueError(f'{name}: 来源截图尚未关联到该阶段和场景')
    if t is None:
        raise ValueError(f'{name}: 模板不在 YAML 任务清单中')
    if mode is not None and mode != t['mode']:
        raise ValueError(f'{name}: 输出模式与规则不一致')
    t.update({
        'source': os.path.abspath(source),
        'box': [x0, y0, x1, y1],
        'status': 'draft',
        'proposer': proposer,
    })
    if mode:
        t['mode'] = mode
    t['agent_note'] = ''
    return t


def set_status(session, name, status):
    if status not in VALID_STATUS:
        raise ValueError(f'invalid status: {status}')
    t = find_template(session, name)
    if t is None:
        raise KeyError(name)
    if status == 'confirmed':
        if t.get('source') and t.get('box'):
            image = _load_image(t['source'])
            x0, y0, x1, y1 = t['box']
            if image.shape[:2] != (1280, 720) or not (0 <= x0 < x1 <= 720 and 0 <= y0 < y1 <= 1280):
                raise ValueError('模板坐标或原图尺寸无效')
            if safe_bbox_color(render_output(image, t['box'], t['mode']))[0] is None:
                raise ValueError('裁剪区域全黑，无法生成有效模板')
        else:
            raise ValueError('模板没有有效的新截图裁剪，不能确认')
    t['status'] = status
    return t


def screenshot_coverage(session, rules):
    items = session_checklist(session, rules)
    scenes = session_scenes(session, rules)
    result = []
    for key, scene in scenes.items():
        phase, scene_id = scene['phase'], scene['scene']
        pending = [t for t in session['templates'] if t.get('scene') == scene_id and t.get('phase') == phase
                   and t['status'] != 'confirmed' and not t.get('optional')]
        shots = [s for s in session['screenshots'] if s.get('scene') == scene_id
                 and s.get('phase', session['phase']) == phase]
        result.append({'key': key, 'phase': phase, 'scene': scene_id,
                       'label': scene['label'], 'state_hint': scene.get('state_hint', ''),
                       'screenshots': len(shots), 'pending': len(pending),
                       'needs_screenshot': bool(pending and not shots),
                       'templates': [t for t in items if t['scene'] == scene_id and t['phase'] == phase]})
    return result


def request_fingerprint(session):
    inputs = {k: session[k] for k in ('event_id', 'event_type', 'phase', 'screenshots', 'templates')}
    inputs['phases'] = session.get('phases', [session['phase']])
    inputs['rules'] = session_scenes(session, load_rules())
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def write_agent_request(session, rules):
    """生成可交给 agent 的任务文件；提案由 CLI 写回，确认仍由用户完成。"""
    sync_session_rules(session, rules)
    shots = session['screenshots']
    if any(not s.get('scene') for s in shots):
        raise ValueError('请先为已导入截图选择场景')
    for shot in shots:
        if f'{shot.get("phase", session["phase"])}:{shot["scene"]}' not in session_scenes(session, rules):
            raise ValueError(f'截图场景不属于该阶段: {shot["scene"]}')
        if _load_image(shot['file']).shape[:2] != (1280, 720):
            raise ValueError(f'原图尺寸无效: {shot["file"]}')
    items = {t['name']: t for t in session_checklist(session, rules)}
    targets = []
    for template in session['templates']:
        if template['status'] == 'confirmed' or template['name'] not in items:
            continue
        item = dict(template, **items[template['name']])
        item['candidates'] = [s for s in shots if s['scene'] == template.get('scene')
                              and s.get('phase', session['phase']) == template['phase']]
        item['reference'] = session_reference(session, template['name'])
        targets.append(item)
    if not targets:
        raise ValueError('没有待处理模板')
    coverage = screenshot_coverage(session, rules)
    warnings = [f'{c["label"]}缺少截图（{c["pending"]} 个必需项）' for c in coverage if c['needs_screenshot']]
    directory = os.path.dirname(session_path(session['event_id']))
    request_path = os.path.join(directory, 'agent_request.json')
    result_path = os.path.join(directory, 'agent_result.json')
    request = {'version': 1, 'event_id': session['event_id'], 'event_type': session['event_type'],
               'phase': session['phase'], 'phases': session.get('phases', [session['phase']]),
               'fingerprint': request_fingerprint(session), 'session_file': session_path(session['event_id']),
               'rules_file': RULES_FILE, 'result_file': result_path, 'screenshots': shots,
               'templates': targets, 'warnings': warnings, 'base_event_id': session.get('base_event_id')}
    with open(request_path, 'w', encoding='utf-8') as f:
        json.dump(request, f, ensure_ascii=False, indent=2)
    prompt = (f'请读取 {request_path}，按照 dev_tools/event_studio/README.md 的 agent 协议，'
              '截图阶段和场景以用户手动关联为准，只从对应阶段及场景的截图定位 YAML 模板，不再判断或校验画面状态。'
              'state_hint 和 requires 是给用户准备截图的说明，不作为筛选或否定已关联截图的条件。'
              '检查历史参考及通用逻辑；所有待处理模板都必须从新截图裁剪，旧模板仅供参考。'
              '仅在对应场景没有截图或无法定位裁剪目标时，列出具体缺项及原因。'
              '将裁剪结果以草稿写回此会话，保留已确认模板，不处理 YAML 之外的资源。'
              '完成后告知我刷新工作台进行校对，不要确认或导出资产。')
    prompt_path = os.path.join(directory, 'agent_prompt.txt')
    with open(prompt_path, 'w', encoding='utf-8') as f:
        f.write(prompt)
    return {'request_file': request_path, 'prompt_file': prompt_path, 'result_file': result_path,
            'prompt': prompt, 'warnings': warnings, 'targets': len(targets)}


def apply_agent_result(session, result_file):
    sync_session_rules(session, load_rules())
    with open(result_file, 'r', encoding='utf-8') as f:
        result = json.load(f)
    if result.get('event_id') != session['event_id'] or result.get('fingerprint') != request_fingerprint(session):
        raise ValueError('结果属于其它会话或标注/提案已更新，请重新生成任务')
    proposals = result.get('proposals', [])
    unresolved = result.get('unresolved', [])
    if not isinstance(proposals, list) or not isinstance(unresolved, list):
        raise ValueError('proposals 和 unresolved 必须是列表')
    # 先验证完整批次，防止一半成功、一半失败。
    updated = json.loads(json.dumps(session))
    names = set()
    for entry in proposals + unresolved:
        name = entry['name']
        template = find_template(updated, name)
        if template is None or template['status'] == 'confirmed' or name in names:
            raise ValueError(f'未知、已确认或重复模板: {name}')
        names.add(name)
    for entry in proposals:
        template = find_template(updated, entry['name'])
        if entry.get('reuse'):
            raise ValueError(f'{entry["name"]}: YAML 中的模板必须重新裁剪，不能沿用旧模板')
        source = os.path.abspath(entry['source'])
        if not any(s['file'] == source and s['scene'] == template['scene']
                   and s.get('phase', session['phase']) == template['phase'] for s in session['screenshots']):
            raise ValueError(f'{entry["name"]}: 来源未关联到该模板阶段和场景')
        if entry.get('mode', template['mode']) != template['mode']:
            raise ValueError(f'{entry["name"]}: 输出模式与规则不一致')
        propose(updated, entry['name'], source, entry['box'], proposer='agent')
        find_template(updated, entry['name'])['agent_note'] = str(entry.get('note', ''))
    for entry in unresolved:
        find_template(updated, entry['name'])['agent_note'] = str(entry.get('reason', '需要补充截图'))
    session['templates'] = updated['templates']
    return {'proposals': len(proposals), 'unresolved': len(unresolved)}


def missing(session, include_optional=False):
    """清单中尚未 confirmed 的模板名。"""
    return [t['name'] for t in session['templates']
            if t['status'] != 'confirmed' and (include_optional or not t.get('optional'))]


# ---------- 导出 ----------

def _load_image(file):
    # cv2.imread 不支持中文路径，用 np.fromfile + imdecode
    img = cv2.imdecode(np.fromfile(file, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f'cannot read image: {file}')
    return img[:, :, ::-1]  # BGR -> RGB


def render_output(image, box, mode):
    """与 template_capture.save() 一致的裁剪逻辑。image: RGB"""
    x0, y0, x1, y1 = box
    if mode == 'button':
        canvas = np.zeros_like(image)
        canvas[y0:y1, x0:x1] = image[y0:y1, x0:x1]
        return canvas
    return image[y0:y1, x0:x1]


def safe_bbox_color(output):
    """
    计算 (bbox, color)；选区全黑时 get_bbox 会 IndexError，返回 (None, None)。

    Returns:
        (tuple|None, tuple|None)
    """
    if output.size == 0 or int(output.max()) == 0:
        return None, None
    bbox = tuple(int(v) for v in get_bbox(output))
    color = tuple(int(v) for v in np.rint(get_color(image=output, area=bbox)))
    return bbox, color


def _find_reference_source(name, event_id, event_type=None):
    """旧会话尚未选择基底活动时，从同类型历史资源中查找参考。"""
    _, _, events = read_events()
    event_types = {e['event_id']: e['event_type'] for e in events}
    candidates = []
    for root in (EVENT_ASSETS_ROOT, ARCHIVE_ASSETS_ROOT):
        if not os.path.isdir(root):
            continue
        for folder in os.listdir(root):
            if not folder.startswith('event_') or folder == event_id:
                continue
            if event_type is not None and event_types.get(folder) != event_type:
                continue
            candidate = os.path.join(root, folder, f'{name}.png')
            if os.path.isfile(candidate):
                candidates.append((folder, candidate))
    if candidates:
        return max(candidates, key=lambda pair: pair[0])[1]
    return None


def session_reference(session, name):
    if session.get('base_assets_dir'):
        candidate = os.path.join(session['base_assets_dir'], f'{name}.png')
        return candidate if os.path.isfile(candidate) else None
    return reference_template(name, session['event_id'], session['event_type'])


def extract_event_assets(event_id):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from button_extract import CACHE_FILE, ExtractCache, FileIndex, ModuleExtractor, process_file

    from module.config.config import NikkeConfig

    index = FileIndex(NikkeConfig.ASSETS_FOLDER)
    cache = ExtractCache(CACHE_FILE)
    extractor = ModuleExtractor('event_dated', event_id)
    for file in extractor.list_files(index):
        extractor.expressions.append(process_file(extractor.module_path, file, index, cache))
    extractor.write()
    cache.save()


def export_session(session, rules, out_root=None, run_extract=None, allow_partial=False):
    """
    导出 confirmed 模板到资产目录，并重新生成 assets.py。

    Args:
        out_root: 覆盖输出目录（默认 assets/zh-CN/event_dated/<event_id>，测试用）
        run_extract: 是否跑 button_extract 重新生成 assets.py（默认仅在 out_root 为 None 时）

    Returns:
        dict: exported(名单), missing(未确认清单), out_dir
    """
    sync_session_rules(session, rules)
    if missing(session) and not allow_partial:
        raise ValueError(f'还有 {len(missing(session))} 个必需模板未确认，请先完成校对')
    event_id = session['event_id']
    session_path(event_id)
    for t in session['templates']:
        if t['status'] == 'confirmed':
            set_status(session, t['name'], 'confirmed')
    out_dir = out_root or os.path.join(EVENT_ASSETS_ROOT, event_id)
    os.makedirs(out_dir, exist_ok=True)
    if run_extract is None:
        run_extract = out_root is None

    rule_names = {t['name'] for t in session_checklist(session, rules)}
    exported = []
    for t in session['templates']:
        if t['status'] != 'confirmed' or t['name'] not in rule_names:
            continue
        name = t['name'].upper()
        mode = t['mode']
        if mode == 'template' and not name.startswith('TEMPLATE_'):
            name = f'TEMPLATE_{name}'
        image = _load_image(t['source'])
        x0, y0, x1, y1 = t['box']
        h, w = image.shape[:2]
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise ValueError(f'{name}: box {(x0, y0, x1, y1)} out of image {w}x{h}')
        output = render_output(image, t['box'], mode)
        ok, encoded = cv2.imencode('.png', output[:, :, ::-1])
        if not ok:
            raise ValueError(f'{name}: PNG 编码失败')
        encoded.tofile(os.path.join(out_dir, f'{name}.png'))
        exported.append(name)
        bbox, color = safe_bbox_color(output)
        logger.info(f'Exported: {name} bbox={bbox} color={color}')

    if run_extract:
        extract_event_assets(event_id)

    return {
        'exported': exported,
        'missing': missing(session),
        'out_dir': out_dir,
    }


def reference_template(name, event_id, event_type=None):
    """找最近其它活动的同名模板，供 GUI 对照。"""
    return _find_reference_source(name.upper(), event_id, event_type)
