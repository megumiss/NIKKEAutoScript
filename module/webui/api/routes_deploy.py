"""Editable view of config/deploy.yaml.

The schema is parsed from deploy/template so groups, field order and the
comment above each field stay in sync with the file itself; values and
defaults come from the live DeployConfig.  Comments are split into a prose
description and `[Tag] advice` hints, then localized via FIELD_I18N /
TAG_I18N (English falls back to the template text).  Writes go through
DeployConfig.__setattr__, which rewrites deploy.yaml from the template and
keeps all comments intact.
"""

import json
import re
import shutil

from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from deploy.utils import DEPLOY_CONFIG, DEPLOY_TEMPLATE
from module.config.utils import nkas_instance
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
# Language options are self-named in every UI language; theme labels follow
# the UI language.
OPTION_LABELS = {
    'Language': {'zh-CN': '简体中文', 'en-US': 'English', 'ja-JP': '日本語'},
    'Theme': {
        'zh-CN': {'dark': '深色', 'light': '浅色'},
        'en-US': {'dark': 'Dark', 'light': 'Light'},
        'ja-JP': {'dark': 'ダーク', 'light': 'ライト'},
    },
}
# Templates offered by the one-click reset.
RESET_TEMPLATES = {
    'intl': './config/deploy.template.yaml',
    'cn': './config/deploy.template-cn.yaml',
    'docker-intl': './config/deploy.template-docker.yaml',
    'docker-cn': './config/deploy.template-docker-cn.yaml',
}

_GROUP_LINE = re.compile(r'^  (\w+):\s*$')
_FIELD_LINE = re.compile(r'^    (\w+):\s*')
_COMMENT_LINE = re.compile(r'^    #\s?(.*)$')
_HINT_LINE = re.compile(r'^\[(.+?)\]\s*(.*)$')

TAG_I18N = {
    'CN user': {'zh-CN': '大陆用户', 'ja-JP': '中国ユーザー'},
    'Other': {'zh-CN': '其他', 'ja-JP': 'その他'},
    'Easy installer': {'zh-CN': '一键安装', 'ja-JP': '簡単インストール'},
    'In most cases': {'zh-CN': '大多数情况下', 'ja-JP': 'ほとんどの場合'},
    'In few cases': {'zh-CN': '少数情况下', 'ja-JP': '稀な場合'},
    'Developer': {'zh-CN': '开发者', 'ja-JP': '開発者'},
    'Disable': {'zh-CN': '禁用', 'ja-JP': '無効'},
    'Default': {'zh-CN': '默认', 'ja-JP': 'デフォルト'},
    'In Docker': {'zh-CN': '在 Docker 中', 'ja-JP': 'Docker の場合'},
    'Use IPv6': {'zh-CN': '使用 IPv6', 'ja-JP': 'IPv6 を使う'},
}

# Per-field translations of the template comments.  `desc` replaces the prose
# lines, `hints` replaces `[Tag] advice` lines matched by tag; anything
# missing falls back to the English template text.
FIELD_I18N = {
    'Repository': {
        'zh-CN': {'desc': 'NKAS 仓库地址', 'hints': {
            'CN user': "使用 'https://git.megumiss.top/megumiss/NIKKEAutoScript'，下载更快更稳定",
            'Gitee': "使用 'https://gitee.com/megumiss/NIKKEAutoScript'，可能需要登录",
            'Other': "使用 'https://github.com/megumiss/NIKKEAutoScript'"}},
        'ja-JP': {'desc': 'NKAS リポジトリの URL', 'hints': {
            'CN user': "「https://git.megumiss.top/megumiss/NIKKEAutoScript」を使うと高速で安定",
            'Gitee': "「https://gitee.com/megumiss/NIKKEAutoScript」（ログインが必要な場合あり）",
            'Other': "「https://github.com/megumiss/NIKKEAutoScript」を使用"}},
    },
    'Branch': {
        'zh-CN': {'desc': 'NKAS 分支', 'hints': {
            'Developer': "使用 'dev'、'app' 等分支体验新功能",
            'Other': "使用稳定分支 'master'"}},
        'ja-JP': {'desc': 'NKAS のブランチ', 'hints': {
            'Developer': "「dev」「app」などで新機能を試す",
            'Other': "安定版ブランチ「master」を使用"}},
    },
    'GitExecutable': {
        'zh-CN': {'desc': 'git 可执行文件 git.exe 的路径', 'hints': {
            'Easy installer': "使用 './toolkit/Git/mingw64/bin/git.exe'",
            'Other': '使用你自己的 git'}},
        'ja-JP': {'desc': 'git 実行ファイル git.exe のパス', 'hints': {
            'Easy installer': "「./toolkit/Git/mingw64/bin/git.exe」を使用",
            'Other': '自分の git を使用'}},
    },
    'GitProxy': {
        'zh-CN': {'desc': '设置 git 代理', 'hints': {
            'CN user': '使用本地 http 代理（http://127.0.0.1:{port}）或 socks5 代理（socks5://127.0.0.1:{port}）',
            'Other': '留空'}},
        'ja-JP': {'desc': 'git プロキシを設定', 'hints': {
            'CN user': 'ローカルの http プロキシ（http://127.0.0.1:{port}）または socks5 プロキシ（socks5://127.0.0.1:{port}）を使用',
            'Other': 'null'}},
    },
    'SSLVerify': {
        'zh-CN': {'desc': 'SSL 证书校验', 'hints': {
            'In most cases': '建议打开',
            'Other': '连接不可信网络时建议关闭'}},
        'ja-JP': {'desc': 'SSL 検証', 'hints': {
            'In most cases': 'true',
            'Other': '信頼できないネットワークでは false'}},
    },
    'AutoUpdate': {
        'zh-CN': {'desc': '启动时自动更新 NKAS', 'hints': {'In most cases': '建议打开'}},
        'ja-JP': {'desc': '起動時に NKAS を更新', 'hints': {'In most cases': 'true'}},
    },
    'PythonExecutable': {
        'zh-CN': {'desc': 'python 可执行文件 python.exe 的路径', 'hints': {
            'Easy installer': "使用 './toolkit/python.exe'",
            'Other': '使用你自己的 python，建议 3.9.x 64 位'}},
        'ja-JP': {'desc': 'python 実行ファイル python.exe のパス', 'hints': {
            'Easy installer': "「./toolkit/python.exe」を使用",
            'Other': '自分の python を使用（3.9.x 64bit 推奨）'}},
    },
    'PypiMirror': {
        'zh-CN': {'desc': 'pypi 镜像地址', 'hints': {
            'CN user': "使用 'https://mirrors.aliyun.com/pypi/simple'，下载更快更稳定",
            'Other': '留空'}},
        'ja-JP': {'desc': 'pypi ミラーの URL', 'hints': {
            'CN user': "「https://mirrors.aliyun.com/pypi/simple」を使うと高速で安定",
            'Other': 'null'}},
    },
    'InstallDependencies': {
        'zh-CN': {'desc': '启动时安装依赖', 'hints': {'In most cases': '建议打开'}},
        'ja-JP': {'desc': '起動時に依存関係をインストール', 'hints': {'In most cases': 'true'}},
    },
    'RequirementsFile': {
        'zh-CN': {'desc': 'requirements.txt 的路径', 'hints': {
            'In most cases': "使用 'requirements.txt'",
            'In Docker': "使用 './deploy/docker/requirements.txt'"}},
        'ja-JP': {'desc': 'requirements.txt のパス', 'hints': {
            'In most cases': "「requirements.txt」",
            'In Docker': "「./deploy/docker/requirements.txt」"}},
    },
    'AdbExecutable': {
        'zh-CN': {'desc': 'ADB 可执行文件 adb.exe 的路径', 'hints': {
            'Easy installer': "使用 './toolkit/Lib/site-packages/adbutils/binaries/adb.exe'",
            'Other': '使用你自己的最新 ADB，不要用模拟器自带的 ADB'}},
        'ja-JP': {'desc': 'ADB 実行ファイル adb.exe のパス', 'hints': {
            'Easy installer': "「./toolkit/Lib/site-packages/adbutils/binaries/adb.exe」を使用",
            'Other': '自分の最新 ADB を使用（エミュレータ付属の ADB は不可）'}},
    },
    'ReplaceAdb': {
        'zh-CN': {'desc': '是否替换 ADB\n'
                          '国产模拟器（夜神、雷电、逍遥、MuMu）使用自带的旧版 ADB。\n'
                          '不同的 ADB 服务启动时会互相终结，导致连接断开。\n'
                          '为了兼容性，需要将它们全部替换。\n'
                          '此操作会：\n'
                          '  1. 终结当前 ADB 服务\n'
                          '  2. 将所有模拟器的 ADB 重命名为 *.bak，并替换为上面设置的 AdbExecutable\n'
                          '  3. 强制连接所有可用的模拟器实例',
                  'hints': {
                      'In most cases': '建议打开',
                      'In few cases': '如果你有其他程序正在使用 ADB，建议关闭'}},
        'ja-JP': {'desc': 'ADB を置き換えるかどうか\n'
                          '中国製エミュレータ（NoxPlayer、LDPlayer、MemuPlayer、MuMuPlayer）は独自の旧 ADB を使用します。\n'
                          '異なる ADB サーバーは起動時に互いを終了させ、切断の原因になります。\n'
                          '互換性のためすべて置き換えます。\n'
                          '実行内容：\n'
                          '  1. 現在の ADB サーバーを終了\n'
                          '  2. 全エミュレータの ADB を *.bak にリネームし、上記の AdbExecutable で置き換え\n'
                          '  3. 利用可能な全エミュレータインスタンスに強制接続',
                  'hints': {
                      'In most cases': 'true',
                      'In few cases': 'ADB を使う他のプログラムがある場合は false'}},
    },
    'AutoConnect': {
        'zh-CN': {'desc': '强制连接所有可用的模拟器实例', 'hints': {'In most cases': '建议打开'}},
        'ja-JP': {'desc': '利用可能な全エミュレータインスタンスに強制接続', 'hints': {'In most cases': 'true'}},
    },
    'InstallUiautomator2': {
        'zh-CN': {'desc': '重新安装 uiautomator2', 'hints': {'In most cases': '建议打开'}},
        'ja-JP': {'desc': 'uiautomator2 を再インストール', 'hints': {'In most cases': 'true'}},
    },
    'EnableReload': {
        'zh-CN': {'desc': '启用自动更新和内置更新器\n可能导致问题 https://github.com/LmeSzinc/AzurLaneAutoScript/issues/876'},
        'ja-JP': {'desc': '自動更新と内蔵アップデーターを有効化\n問題が起きる可能性: https://github.com/LmeSzinc/AzurLaneAutoScript/issues/876'},
    },
    'CheckUpdateInterval': {
        'zh-CN': {'desc': '每隔 X 分钟检查更新', 'hints': {'Disable': '0', 'Default': '5'}},
        'ja-JP': {'desc': 'X 分ごとに更新を確認', 'hints': {'Disable': '0', 'Default': '5'}},
    },
    'AutoRestartTime': {
        'zh-CN': {'desc': '定时重启时间\n如果有更新，NKAS 会在每天该时间自动重启并更新，\n并运行重启前正在运行的所有实例',
                  'hints': {'Disable': '留空', 'Default': '03:50'}},
        'ja-JP': {'desc': '定時再起動時刻\n更新がある場合、NKAS は毎日この時刻に自動で再起動・更新し、\n再起動前に実行中だった全インスタンスを実行します',
                  'hints': {'Disable': 'null', 'Default': '03:50'}},
    },
    'DesktopUpdateManifest': {
        'zh-CN': {'desc': '脚本exe 的更新清单，exe 版本独立于项目发布标签。'},
        'ja-JP': {'desc': 'デスクトップシェルの更新マニフェスト。デスクトップ版はプロジェクトのリリースタグから独立しています。'},
    },
    'WebuiHost': {
        'zh-CN': {'desc': '--host，监听地址', 'hints': {
            'Use IPv6': "'::'",
            'In most cases': "默认 '0.0.0.0'"}},
        'ja-JP': {'desc': '--host リッスンするホスト', 'hints': {
            'Use IPv6': "「::」",
            'In most cases': "デフォルト「0.0.0.0」"}},
    },
    'WebuiPort': {
        'zh-CN': {'desc': '--port，监听端口\n之后可通过 http://{host}:{port} 访问 Web UI',
                  'hints': {'In most cases': '默认 12271'}},
        'ja-JP': {'desc': '--port リッスンするポート\nhttp://{host}:{port} で Web UI にアクセスできます',
                  'hints': {'In most cases': 'デフォルト 12271'}},
    },
    'DpiScaling': {
        'zh-CN': {'desc': '脚本exe 跟随系统显示缩放'},
        'ja-JP': {'desc': 'デスクトップシェルでシステムの表示スケーリングに従う'},
    },
    'HardwareAcceleration': {
        'zh-CN': {'desc': '启用 WebView2 GPU 加速，修改后需重启脚本exe。'},
        'ja-JP': {'desc': 'WebView2 の GPU アクセラレーションを有効化。変更後はデスクトップシェルの再起動が必要です。'},
    },
    'Language': {
        'zh-CN': {'desc': 'Web UI 语言'},
        'ja-JP': {'desc': 'Web UI の言語'},
    },
    'Theme': {
        'zh-CN': {'desc': 'Web UI 主题'},
        'ja-JP': {'desc': 'Web UI のテーマ'},
    },
    'Run': {
        'zh-CN': {'desc': '--run，启动时自动运行指定实例'},
        'ja-JP': {'desc': '--run 起動時に自動実行する設定'},
    },
    'EnableStatistics': {
        'zh-CN': {'desc': '启动时上报匿名使用统计，每天最多一次，不收集账号、配置等任何个人数据\n'
                          '上报内容：随机匿名 ID、源码Commit号、系统平台名称、屏幕分辨率'},
        'ja-JP': {'desc': '起動時に匿名の使用統計を送信（1日1回まで）。アカウントや設定などの個人データは収集しません\n'
                          '送信内容: ランダム匿名 ID、ソースのコミット番号、OS プラットフォーム名、画面解像度'},
    },
}


def _json_error(message, status=400):
    return JSONResponse({'status': 'error', 'message': message}, status_code=status)


def _parse_template():
    """Group/field structure of deploy/template with comments split into
    prose description lines and `[Tag] advice` hints."""
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
                    desc, hints = [], []
                    for comment in comments:
                        hint = _HINT_LINE.match(comment)
                        if hint:
                            hints.append({'tag': hint.group(1), 'text': hint.group(2)})
                        else:
                            desc.append(comment)
                    group['fields'].append({
                        'key': key, 'title': key, 'wide': key in WIDE_KEYS,
                        'help': '\n'.join(desc).strip(), 'hints': hints,
                    })
                comments = []
                continue
            if line.strip():
                comments = []
    return groups


def _localize(field):
    """Translate desc/hints/tag labels into the current UI language."""
    if lang.LANG == 'en-US':
        return
    translations = FIELD_I18N.get(field['key'], {}).get(lang.LANG)
    if translations:
        if translations.get('desc'):
            field['help'] = translations['desc']
        hint_texts = translations.get('hints', {})
        for hint in field['hints']:
            if hint['tag'] in hint_texts:
                hint['text'] = hint_texts[hint['tag']]
    for hint in field['hints']:
        tag = TAG_I18N.get(hint['tag'], {})
        if lang.LANG in tag:
            hint['tag'] = tag[lang.LANG]


def _widget(key, default):
    if key in SELECT_OPTIONS:
        return 'select'
    if isinstance(default, bool):
        return 'checkbox'
    if isinstance(default, int):
        return 'number'
    return 'text'


def _select_options(key):
    labels = OPTION_LABELS[key]
    if key == 'Theme':
        labels = labels.get(lang.LANG) or labels['en-US']
    return [{'value': value, 'label': labels.get(value, value)} for value in SELECT_OPTIONS[key]]


def _run_value(raw):
    """The stored format stays a JSON-ish string ('["nkas","nkas2"]') or
    null; the multiselect widget works on a plain list."""
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(item) for item in data]
        except ValueError:
            pass
    return []


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
            if key == 'Run':
                field['widget'] = 'multiselect'
                field['options'] = [{'value': name, 'label': name} for name in nkas_instance()]
                field['value'] = _run_value(config.config.get(key))
                field['default'] = []
            else:
                field['widget'] = _widget(key, default)
                field['value'] = config.config.get(key)
                field['default'] = default
                if field['widget'] == 'select':
                    field['options'] = _select_options(key)
            _localize(field)
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
    if key == 'Run':
        # The widget sends a list; the file keeps the '["nkas","nkas2"]'
        # string format (null when empty).
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return _json_error('Run must be a list of instance names.', 422)
        try:
            setattr(config, key, json.dumps(value, separators=(',', ':')) if value else None)
        except OSError as exc:
            logger.exception(exc)
            return _json_error(str(exc), 500)
        return JSONResponse({'status': 'success', 'value': value})
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


async def deploy_reset(request: Request):
    try:
        data = await request.json()
    except ValueError:
        data = {}
    template = RESET_TEMPLATES.get(data.get('template'), RESET_TEMPLATES['intl'])
    try:
        shutil.copyfile(template, DEPLOY_CONFIG)
        State.deploy_config.read()
    except OSError as exc:
        logger.exception(exc)
        return _json_error(str(exc), 500)
    config = State.deploy_config
    State.theme = config.Theme
    lang.set_language(config.Language)
    return JSONResponse({'status': 'success', 'theme': config.Theme, 'language': config.Language})
