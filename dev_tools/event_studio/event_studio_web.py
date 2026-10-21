"""
活动模板工作台 Web 校对界面（替代 tkinter GUI，避免游戏覆盖层杀掉本地窗口）。

启动：
    python dev_tools/event_studio/event_studio_web.py [--port 12273]
    或 python dev_tools/event_studio/event_template_studio.py gui

然后在浏览器打开 http://127.0.0.1:12273
agent 侧通过 dev_tools/event_studio/event_template_studio.py 的 CLI 写提案，本页面用于人工校对与导出。
"""
import argparse
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from functools import wraps

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import event_studio_model as model  # noqa: E402
import history_index  # noqa: E402
from event_template_studio import current_event_id, set_current_event_id  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response  # noqa: E402
from starlette.routing import Route, request_response  # noqa: E402

from module.logger import logger  # noqa: E402

STATUS_LABEL = {'missing': '缺失', 'draft': '草稿', 'confirmed': '已确认', 'rejected': '打回'}

INDEX_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'event_studio_web.html')


# ---------- 工具 ----------

def load_session_or_none(event_id=None):
    event_id = event_id or current_event_id()
    if not event_id:
        return None, None
    path = model.session_path(event_id)
    if not os.path.isfile(path):
        return None, None
    return event_id, model.load_session(event_id)


def request_session(request, required=True):
    event_id, session = load_session_or_none(request.headers.get('X-Event-ID')
                                            or request.query_params.get('event_id'))
    if required and session is None:
        raise ValueError('请先新建或打开会话')
    revision = request.headers.get('X-Session-Revision')
    if session and request.method == 'POST' and revision is not None:
        if int(revision) != session.get('revision', 0):
            raise ValueError('会话已被 agent 或其它页面更新，请刷新后重试')
    return event_id, session


def image_response(rgb):
    ok, buf = cv2.imencode('.png', rgb[:, :, ::-1], [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        return Response(status_code=500)
    return Response(bytes(buf), media_type='image/png')


def imread_rgb(path):
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    return img[:, :, ::-1]


async def read_json(request):
    """解析请求 JSON，编码/格式错误时返回 None（由调用方转 400）。"""
    try:
        body = await request.json()
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('请求体不是合法 JSON') from None
    if not isinstance(body, dict):
        raise ValueError('请求体必须是 JSON 对象')
    return body


def state_payload(event_id, session):
    rules = model.load_rules()
    types = {str(k): {'label': v['label'], 'phases': model.get_phases(rules, k)}
             for k, v in rules['types'].items()}
    payload = {'current': event_id, 'types': types, 'status_label': STATUS_LABEL,
               'session': None, 'scenes': None, 'rules': None, 'missing': None}
    if session is None:
        return payload
    model.sync_session_rules(session, rules)
    scenes = model.session_scenes(session, rules)
    payload['session'] = session
    payload['scenes'] = {k: {field: v[field] for field in ('label', 'state_hint', 'phase', 'scene')}
                         for k, v in scenes.items()}
    payload['rules'] = {t['name']: t for t in model.session_checklist(session, rules)}
    payload['missing'] = model.missing(session)
    payload['coverage'] = model.screenshot_coverage(session, rules)
    payload['session_file'] = model.session_path(event_id)
    payload['agent_request_file'] = os.path.join(os.path.dirname(payload['session_file']), 'agent_request.json')
    return payload


# ---------- 路由 ----------

async def index(request):
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        return HTMLResponse(f.read())


async def studio_asset(request):
    filename = request.path_params['filename']
    if filename not in ('studio.css', 'studio.js', 'manual_crop.js', 'history_review.css', 'history_review.js'):
        return Response(status_code=404)
    return FileResponse(os.path.join(os.path.dirname(INDEX_HTML), filename))


async def history_review(request):
    return FileResponse(os.path.join(os.path.dirname(INDEX_HTML), 'history_review.html'))


async def api_history(request):
    if request.method == 'POST':
        body = await read_json(request)
        return JSONResponse(history_index.save_index(body['data'], body['revision']))
    return JSONResponse(history_index.payload())


async def api_history_template(request):
    path = history_index.template_file(request.query_params.get('event_id', ''),
                                       request.query_params.get('name', ''))
    if not path or not os.path.isfile(path):
        return Response(status_code=404)
    rgb = imread_rgb(path)
    if rgb is None:
        return Response(status_code=404)
    bbox, _ = model.safe_bbox_color(rgb)
    if bbox is None:
        return Response(status_code=404)
    return image_response(rgb[bbox[1]:bbox[3], bbox[0]:bbox[2]])


async def api_state(request):
    event_id, session = request_session(request, required=False)
    return JSONResponse(state_payload(event_id, session))


async def api_sessions(request):
    result = []
    if os.path.isdir(model.SESSION_ROOT):
        for d in sorted(os.listdir(model.SESSION_ROOT), reverse=True):
            if os.path.isfile(os.path.join(model.SESSION_ROOT, d, 'session.json')):
                result.append(d)
    return JSONResponse({'sessions': result, 'events': model.historical_events()})


async def api_session_new(request):
    body = await read_json(request)
    if body is None:
        return JSONResponse({'error': '请求体不是合法 JSON (需 UTF-8)'}, status_code=400)
    event_id = str(body.get('event_id', '')).strip()
    phase = str(body.get('phase', 'day1'))
    if not event_id:
        return JSONResponse({'error': 'event_id 不能为空'}, status_code=400)
    if os.path.isfile(model.session_path(event_id)):
        return JSONResponse({'error': '会话已经存在，请打开已有会话'}, status_code=409)
    session = model.create_event_session(event_id, body.get('event_name'), body.get('base_event_id'),
                                         body.get('position'), phase, model.load_rules())
    set_current_event_id(event_id)
    return JSONResponse(state_payload(event_id, session))


async def api_session_open(request):
    body = await read_json(request)
    if body is None:
        return JSONResponse({'error': '请求体不是合法 JSON (需 UTF-8)'}, status_code=400)
    requested = str(body.get('event_id', '')).strip()
    if not requested:
        raise ValueError('请选择要打开的会话')
    event_id, session = load_session_or_none(requested)
    if session is None:
        return JSONResponse({'error': '会话不存在'}, status_code=404)
    set_current_event_id(event_id)
    return JSONResponse(state_payload(event_id, session))


async def api_session_phase(request):
    body = await read_json(request)
    event_id, session = request_session(request)
    model.advance_phase(session, body['phase'], model.load_rules())
    model.save_session(session)
    return JSONResponse(state_payload(event_id, session))


async def api_browse(request):
    """先按日期和竖屏分辨率筛选，再分页。"""
    q = request.query_params
    folder = q.get('dir', '')
    date_from = q.get('date_from', '').replace('-', '')
    date_to = q.get('date_to', '').replace('-', '')
    for value in (date_from, date_to):
        if value:
            datetime.strptime(value, '%Y%m%d')
    if date_from and date_to and date_from > date_to:
        raise ValueError('开始日期不能晚于结束日期')
    date_to = date_to or '99999999'
    if not os.path.isdir(folder):
        return JSONResponse({'error': f'目录不存在: {folder}'}, status_code=400)
    files = []
    for name in os.listdir(folder):
        if not name.lower().endswith(('.png', '.jpg', '.jpeg')):
            continue
        dates = re.findall(r'20\d{6}', name)
        if dates and not (date_from <= dates[0] <= date_to):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if not dates:
            modified_date = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y%m%d')
            if not (date_from <= modified_date <= date_to):
                continue
        try:
            with Image.open(path) as image:
                if image.size != (720, 1280):
                    continue
        except (OSError, UnidentifiedImageError):
            continue
        files.append({'file': path, 'name': name, 'mtime': os.path.getmtime(path),
                      'width': 720, 'height': 1280, 'valid': True})
    files.sort(key=lambda f: f['mtime'], reverse=True)
    offset = max(0, int(q.get('offset', 0)))
    limit = min(100, max(1, int(q.get('limit', 30))))
    page = files[offset:offset + limit]
    return JSONResponse({'files': page, 'total': len(files), 'offset': offset})


async def api_shots_import(request):
    body = await read_json(request)
    if body is None:
        return JSONResponse({'error': '请求体不是合法 JSON (需 UTF-8)'}, status_code=400)
    _, session = request_session(request)
    if session is None:
        return JSONResponse({'error': '请先新建/打开会话'}, status_code=400)
    files = body.get('files', [])
    if not isinstance(files, list) or not files:
        raise ValueError('请选择至少一张截图')
    scene = body.get('scene')
    if scene and f'{session["phase"]}:{scene}' not in model.session_scenes(session, model.load_rules()):
        raise ValueError('无效场景')
    for f in files:
        rgb = imread_rgb(f)
        if rgb is None or rgb.shape[:2] != (1280, 720):
            raise ValueError(f'请导入 720×1280 原始竖屏截图: {os.path.basename(f)}')
    for f in files:
        model.add_screenshot(session, f, scene=scene, state=body.get('state'), phase=session['phase'])
    model.save_session(session)
    return JSONResponse(state_payload(session['event_id'], session))


async def api_shot_scene(request):
    body = await read_json(request)
    if body is None:
        return JSONResponse({'error': '请求体不是合法 JSON (需 UTF-8)'}, status_code=400)
    _, session = request_session(request)
    idx = int(body.get('index'))
    if session is None or not (0 <= idx < len(session['screenshots'])):
        return JSONResponse({'error': '无效截图'}, status_code=400)
    scene = body.get('scene', session['screenshots'][idx].get('scene', ''))
    phase = body.get('phase', session['screenshots'][idx].get('phase', session['phase']))
    if phase not in session.get('phases', [session['phase']]):
        raise ValueError('无效截图阶段')
    if scene and f'{phase}:{scene}' not in model.session_scenes(session, model.load_rules()):
        raise ValueError('场景不属于所选阶段')
    session['screenshots'][idx]['scene'] = scene
    session['screenshots'][idx]['phase'] = phase
    for key in ('state', 'note'):
        if key in body:
            session['screenshots'][idx][key] = str(body[key])
    model.save_session(session)
    return JSONResponse(state_payload(session['event_id'], session))


async def api_shot_remove(request):
    body = await read_json(request)
    event_id, session = request_session(request)
    idx = int(body['index'])
    if not 0 <= idx < len(session['screenshots']):
        raise ValueError('无效截图')
    shot = session['screenshots'][idx]
    if any(t.get('source') == shot['file'] and t['phase'] == shot.get('phase', session['phase'])
           for t in session['templates']):
        raise ValueError('该截图已有裁剪提案，请先清除相关模板的框')
    session['screenshots'].pop(idx)
    model.save_session(session)
    return JSONResponse(state_payload(event_id, session))


async def api_image(request):
    path = request.query_params.get('path', '')
    if not path.lower().endswith(('.png', '.jpg', '.jpeg')) or not os.path.isfile(path):
        return Response(status_code=404)
    return FileResponse(path)


async def api_propose(request):
    body = await read_json(request)
    if body is None:
        return JSONResponse({'error': '请求体不是合法 JSON (需 UTF-8)'}, status_code=400)
    _, session = request_session(request)
    if session is None:
        return JSONResponse({'error': '请先新建/打开会话'}, status_code=400)
    try:
        model.propose(session, body['name'], body['source'], body['box'],
                      mode=body.get('mode'), proposer=body.get('proposer', 'user'))
    except (ValueError, FileNotFoundError, KeyError) as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    model.save_session(session)
    return JSONResponse(state_payload(session['event_id'], session))


async def api_status(request):
    body = await read_json(request)
    if body is None:
        return JSONResponse({'error': '请求体不是合法 JSON (需 UTF-8)'}, status_code=400)
    _, session = request_session(request)
    try:
        model.set_status(session, body['name'], body['value'])
    except (KeyError, ValueError) as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    model.save_session(session)
    return JSONResponse(state_payload(session['event_id'], session))


async def api_clear(request):
    body = await read_json(request)
    if body is None:
        return JSONResponse({'error': '请求体不是合法 JSON (需 UTF-8)'}, status_code=400)
    _, session = request_session(request)
    t = model.find_template(session, body['name'])
    if t is None:
        return JSONResponse({'error': '模板不存在'}, status_code=404)
    t.update({'box': None, 'source': None, 'status': 'missing', 'proposer': None, 'agent_note': ''})
    model.save_session(session)
    return JSONResponse(state_payload(session['event_id'], session))


def _find_in_session(request):
    name = request.query_params.get('name', '')
    event_id, session = request_session(request, required=False)
    if session is None:
        return None, None, None
    return event_id, session, model.find_template(session, name)


async def api_preview(request):
    """当前提案的裁剪预览（button 模式也显示裁剪区域局部图）。"""
    _, _, t = _find_in_session(request)
    if t is None or not t.get('source') or not t.get('box'):
        return Response(status_code=404)
    rgb = imread_rgb(t['source'])
    if rgb is None:
        return Response(status_code=404)
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = t['box']
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, w), min(y1, h)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return Response(status_code=404)
    return image_response(rgb[y0:y1, x0:x1])


async def api_reference(request):
    """历史同名模板（裁剪到有效区域）。"""
    event_id, session, t = _find_in_session(request)
    if t is None:
        return Response(status_code=404)
    ref = model.session_reference(session, t['name'])
    if not ref:
        return Response(status_code=404)
    rgb = imread_rgb(ref)
    if rgb is None:
        return Response(status_code=404)
    bbox, _ = model.safe_bbox_color(rgb)
    if bbox is None:
        return Response(status_code=404)
    return image_response(rgb[bbox[1]:bbox[3], bbox[0]:bbox[2]])


async def api_template_info(request):
    event_id, session, t = _find_in_session(request)
    if t is None:
        return JSONResponse({'error': '模板不存在'}, status_code=404)
    info = {'name': t['name'], 'mode': t['mode'], 'status': t['status'],
            'proposer': t.get('proposer'), 'source': t.get('source'), 'box': t.get('box'),
            'agent_note': t.get('agent_note', ''), 'review_note': t.get('review_note', '')}
    info['reference'] = model.session_reference(session, t['name'])
    if t.get('source') and t.get('box'):
        rgb = imread_rgb(t['source'])
        if rgb is not None:
            output = model.render_output(rgb, t['box'], t['mode'])
            bbox, color = model.safe_bbox_color(output)
            info['bbox'] = bbox
            info['color'] = color
    return JSONResponse(info)


async def api_export(request):
    _, session = request_session(request)
    if session is None:
        return JSONResponse({'error': '请先新建/打开会话'}, status_code=400)
    try:
        result = model.export_session(session, model.load_rules())
    except (ValueError, OSError) as e:
        logger.error(f'Export failed: {e}')
        return JSONResponse({'error': str(e)}, status_code=400 if isinstance(e, ValueError) else 500)
    return JSONResponse(result)


async def api_agent_request(request):
    _, session = request_session(request)
    return JSONResponse(model.write_agent_request(session, model.load_rules()))


async def api_agent_result(request):
    _, session = request_session(request)
    result_file = os.path.join(os.path.dirname(model.session_path(session['event_id'])), 'agent_result.json')
    result = model.apply_agent_result(session, result_file)
    model.save_session(session)
    return JSONResponse(dict(state_payload(session['event_id'], session), result=result))


async def api_review_note(request):
    body = await read_json(request)
    _, session = request_session(request)
    template = model.find_template(session, body['name'])
    if template is None:
        raise ValueError('模板不存在')
    template['review_note'] = str(body.get('note', ''))
    if body.get('reject'):
        model.set_status(session, template['name'], 'rejected')
    model.save_session(session)
    return JSONResponse(state_payload(session['event_id'], session))


def checked_endpoint(endpoint):
    @wraps(endpoint)
    async def checked(request):
        try:
            return await endpoint(request)
        except (ValueError, TypeError, KeyError, FileNotFoundError) as e:
            return JSONResponse({'error': str(e) or '无效请求'}, status_code=400)
        except OSError as e:
            logger.exception(f'工作台文件操作失败: {request.url.path}')
            return JSONResponse({'error': f'文件操作失败: {e}'}, status_code=500)
    return checked


def create_app():
    routes = [
        Route('/', index),
        Route('/history', history_review),
        Route('/{filename}', studio_asset),
        Route('/api/history', api_history, methods=['GET', 'POST']),
        Route('/api/history/template', api_history_template),
        Route('/api/state', api_state),
        Route('/api/sessions', api_sessions),
        Route('/api/session/new', api_session_new, methods=['POST']),
        Route('/api/session/open', api_session_open, methods=['POST']),
        Route('/api/session/phase', api_session_phase, methods=['POST']),
        Route('/api/browse', api_browse),
        Route('/api/shots/import', api_shots_import, methods=['POST']),
        Route('/api/shots/scene', api_shot_scene, methods=['POST']),
        Route('/api/shots/remove', api_shot_remove, methods=['POST']),
        Route('/api/image', api_image),
        Route('/api/propose', api_propose, methods=['POST']),
        Route('/api/status', api_status, methods=['POST']),
        Route('/api/clear', api_clear, methods=['POST']),
        Route('/api/preview', api_preview),
        Route('/api/reference', api_reference),
        Route('/api/template_info', api_template_info),
        Route('/api/export', api_export, methods=['POST']),
        Route('/api/agent/request', api_agent_request, methods=['POST']),
        Route('/api/agent/result', api_agent_result, methods=['POST']),
        Route('/api/review_note', api_review_note, methods=['POST']),
    ]
    for route in routes:
        if route.path.startswith('/api/'):
            route.endpoint = checked_endpoint(route.endpoint)
            route.app = request_response(route.endpoint)
    return Starlette(routes=routes)


def main(argv=None):
    parser = argparse.ArgumentParser(description='活动模板工作台 Web 校对界面')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=12273)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)

    import uvicorn
    url = f'http://{args.host}:{args.port}'
    logger.info(f'Event Template Studio: {url}')
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
