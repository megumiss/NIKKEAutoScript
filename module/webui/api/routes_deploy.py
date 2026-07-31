"""Editable view of config/deploy.yaml.

The schema is parsed from deploy/template so groups, field order and the
comment above each field stay in sync with the file itself; values and
defaults come from the live DeployConfig.  Writes go through
DeployConfig.__setattr__, which rewrites deploy.yaml from the template and
keeps all comments intact.
"""

import re
import shutil

from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from deploy.utils import DEPLOY_CONFIG, DEPLOY_TEMPLATE
from module.logger import logger
from module.webui.setting import State

# Runtime state written by dismissing the startup notice, not a user setting.
EXCLUDED_KEYS = {'StartupNoticeDismissedId'}
# Path/URL values that benefit from a full-row input instead of the standard
# narrow control.
WIDE_KEYS = {'Repository', 'GitExecutable', 'AdbExecutable', 'DesktopUpdateManifest', 'PypiMirror', 'GitProxy'}
SELECT_OPTIONS = {
    'Language': ['zh-CN', 'en-US', 'ja-JP'],
    'Theme': ['dark', 'light'],
}

_GROUP_LINE = re.compile(r'^  (\w+):\s*$')
_FIELD_LINE = re.compile(r'^    (\w+):\s*')
_COMMENT_LINE = re.compile(r'^    #\s?(.*)$')


def _json_error(message, status=400):
    return JSONResponse({'status': 'error', 'message': message}, status_code=status)


def _parse_template():
    groups = []
    group = None
    comments = []
    with open(DEPLOY_TEMPLATE, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            match = _GROUP_LINE.match(line)
            if match:
                group = {'key': match.group(1), 'name': match.group(1), 'fields': []}
                groups.append(group)
                comments = []
                continue
            match = _COMMENT_LINE.match(line)
            if match:
                comments.append(match.group(1))
                continue
            match = _FIELD_LINE.match(line)
            if match and group is not None:
                key = match.group(1)
                if key not in EXCLUDED_KEYS:
                    group['fields'].append({'key': key, 'title': key, 'help': '\n'.join(comments).strip(), 'wide': key in WIDE_KEYS})
                comments = []
                continue
            if line.strip():
                comments = []
    return groups


def _widget(key, default):
    if key in SELECT_OPTIONS:
        return 'select'
    if isinstance(default, bool):
        return 'checkbox'
    if isinstance(default, int):
        return 'number'
    return 'text'


def _coerce(key, value, default):
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError('Expected a boolean value.')
        return value
    if isinstance(default, int):
        if isinstance(value, bool):
            raise ValueError('Expected an integer value.')
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError('Expected an integer value.')
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            return None
        raise ValueError('Value cannot be empty.')
    return str(value)


async def deploy_schema(_: Request):
    config = State.deploy_config
    groups = _parse_template()
    for group in groups:
        for field in group['fields']:
            key = field['key']
            default = config.config_template.get(key)
            field['widget'] = _widget(key, default)
            field['value'] = config.config.get(key)
            field['default'] = default
            if field['widget'] == 'select':
                field['options'] = [{'value': v, 'label': v} for v in SELECT_OPTIONS[key]]
    return JSONResponse({'groups': groups})


async def deploy_patch(request: Request):
    try:
        data = await request.json()
        key = str(data['key'])
        value = data.get('value')
    except (KeyError, TypeError, ValueError):
        return _json_error('Expected key and value in request body.')
    config = State.deploy_config
    if key in EXCLUDED_KEYS or key not in config.config_template:
        return _json_error(f'Unknown deploy key: {key}', 404)
    if key in SELECT_OPTIONS and value not in SELECT_OPTIONS[key]:
        return _json_error(f'{key} must be one of {SELECT_OPTIONS[key]}.', 422)
    try:
        value = _coerce(key, value, config.config_template[key])
    except ValueError as exc:
        return _json_error(str(exc), 422)
    try:
        setattr(config, key, value)
    except OSError as exc:
        logger.exception(exc)
        return _json_error(str(exc), 500)
    # Theme and language take effect immediately; everything else applies on
    # the next restart, which the page warning calls out.
    if key == 'Theme':
        State.theme = value
    elif key == 'Language':
        lang.set_language(value)
    return JSONResponse({'status': 'success', 'value': value})


async def deploy_reset(_: Request):
    try:
        shutil.copyfile(DEPLOY_TEMPLATE, DEPLOY_CONFIG)
        State.deploy_config.read()
    except OSError as exc:
        logger.exception(exc)
        return _json_error(str(exc), 500)
    config = State.deploy_config
    State.theme = config.Theme
    lang.set_language(config.Language)
    return JSONResponse({'status': 'success', 'theme': config.Theme, 'language': config.Language})
