"""推送渠道注册表。

WebUI 的「通知渠道」组件按这里的定义渲染渠道下拉与参数字段，用户不必再对照
onepush 文档手写 YAML。运行时推送链路不变：仍然只读
NKAS.Notification.OnePushConfig 里的 YAML 文本。

渠道与参数来自 onepush 自身的自省（`required` / `optional`），所以升级 onepush
不会让这里和新渠道脱节；本模块只补充中文标签、帮助与控件类型。
"""

import json

import onepush.core
import yaml
from onepush.core import _all_providers

from module.notify.onebot11 import OneBot11
from module.notify.smtp import SMTP

# onepush 只认内置渠道，自定义渠道在这里注册（notify.py 复用本模块的注册结果）
_all_providers.setdefault('onebot11', OneBot11)
_all_providers.setdefault('smtp', SMTP)

# 推送时由 handle_notify 注入的字段（title/content/message/image_path），
# 以及无法用 YAML 表达的对象字段（smtp 的 msg）——都不需要用户填写。
RUNTIME_PARAMS = ('title', 'content', 'message', 'image_path', 'msg')


def _l(zh_cn, en_us, ja_jp=None):
    """三语文案；日语缺省回落英语（与 i18n.get_text 的回退链一致）。"""
    return {'zh-CN': zh_cn, 'en-US': en_us, 'ja-JP': ja_jp or en_us}


def _select(*options):
    return [{'value': value, 'label': label} for value, label in options]


# 参数名 -> 控件元数据。同名参数在不同渠道含义一致（如 token/key 都是密钥），
# 渠道特有的说明写在 PROVIDERS 的 params 覆盖里。
PARAM_META = {
    'key': {'label': _l('密钥', 'Key'), 'type': 'password'},
    'token': {'label': _l('令牌', 'Token'), 'type': 'password'},
    'endpoint': {
        'label': _l('接口地址', 'Endpoint'), 'type': 'text',
        'placeholder': 'http://127.0.0.1:5700',
    },
    'url': {'label': _l('服务地址', 'URL'), 'type': 'text', 'placeholder': 'https://'},
    'webhook': {'label': _l('Webhook 地址', 'Webhook URL'), 'type': 'text', 'placeholder': 'https://'},
    'secret': {'label': _l('加签密钥', 'Sign secret'), 'type': 'password'},
    'user_id': {'label': _l('用户 ID', 'User ID'), 'type': 'text'},
    'userid': {'label': _l('目标用户 ID', 'Target user ID'), 'type': 'text'},
    'group_id': {'label': _l('群号', 'Group ID'), 'type': 'text'},
    'qq': {'label': _l('目标 QQ 号', 'Target QQ'), 'type': 'text'},
    'topic': {'label': _l('群组编码', 'Topic'), 'type': 'text'},
    'channel': {'label': _l('推送渠道', 'Channel'), 'type': 'text'},
    'openid': {'label': _l('接收者 openid', 'Receiver openid'), 'type': 'text'},
    'corpid': {'label': _l('企业 ID', 'Corp ID'), 'type': 'password'},
    'corpsecret': {'label': _l('应用密钥', 'Corp secret'), 'type': 'password'},
    'agentid': {'label': _l('应用 ID', 'Agent ID'), 'type': 'number'},
    'touser': {'label': _l('接收成员账号', 'Recipient'), 'type': 'text'},
    'sckey': {'label': _l('SendKey', 'SendKey'), 'type': 'password'},
    'sctkey': {'label': _l('SendKey', 'SendKey'), 'type': 'password'},
    'pushkey': {'label': _l('PushKey', 'PushKey'), 'type': 'password'},
    'host': {'label': _l('服务器地址', 'SMTP host'), 'type': 'text', 'placeholder': 'smtp.qq.com'},
    'user': {'label': _l('账号', 'Username'), 'type': 'text', 'placeholder': 'you@example.com'},
    'password': {'label': _l('密码 / 授权码', 'Password / auth code'), 'type': 'password'},
    'port': {'label': _l('端口', 'Port'), 'type': 'number', 'placeholder': '465'},
    'ssl': {'label': _l('使用 SSL', 'Use SSL'), 'type': 'bool'},
    'starttls': {'label': _l('使用 STARTTLS', 'Use STARTTLS'), 'type': 'bool'},
    'From': {'label': _l('发件人', 'From'), 'type': 'text', 'placeholder': 'you@example.com'},
    'To': {'label': _l('收件人', 'To'), 'type': 'text', 'placeholder': 'you@example.com'},
    'subject': {'label': _l('邮件主题', 'Subject'), 'type': 'text'},
    'api_url': {'label': _l('API 地址', 'API host'), 'type': 'text', 'placeholder': 'api.telegram.org'},
    'cipherkey': {'label': _l('加密密钥', 'Cipher key'), 'type': 'password'},
    'ciphermethod': {
        'label': _l('加密方式', 'Cipher method'), 'type': 'select',
        'options': _select(('ecb', _l('ECB', 'ECB')), ('cbc', _l('CBC', 'CBC'))),
    },
    'sound': {'label': _l('提示音', 'Sound'), 'type': 'text'},
    'isarchive': {'label': _l('保存到历史记录', 'Archive message'), 'type': 'bool'},
    'icon': {'label': _l('图标地址', 'Icon URL'), 'type': 'text'},
    'group': {'label': _l('分组', 'Group'), 'type': 'text'},
    'copy': {'label': _l('自动复制的文本', 'Text to copy'), 'type': 'text'},
    'autocopy': {'label': _l('自动复制', 'Auto copy'), 'type': 'bool'},
    'markdown': {'label': _l('使用 Markdown', 'Use Markdown'), 'type': 'bool'},
    'auto_escape': {'label': _l('转义为纯文本', 'Auto escape'), 'type': 'bool'},
    'mode': {'label': _l('发送方式', 'Mode'), 'type': 'text', 'placeholder': 'send'},
    'message_type': {'label': _l('消息类型', 'Message type'), 'type': 'text'},
    'type': {'label': _l('消息类型', 'Message type'), 'type': 'text'},
    'datatype': {
        'label': _l('请求体格式', 'Payload format'), 'type': 'select',
        'options': _select(('data', _l('表单 data', 'form data')), ('json', _l('JSON', 'JSON'))),
    },
    'method': {
        'label': _l('请求方法', 'HTTP method'), 'type': 'select',
        'options': _select(('post', _l('POST', 'POST')), ('get', _l('GET', 'GET'))),
    },
    'data': {'label': _l('请求体', 'Payload'), 'type': 'json'},
    'priority': {'label': _l('优先级', 'Priority'), 'type': 'number'},
    'color': {'label': _l('颜色', 'Color'), 'type': 'number'},
    'username': {'label': _l('机器人昵称', 'Bot username'), 'type': 'text'},
    'avatar_url': {'label': _l('机器人头像', 'Bot avatar URL'), 'type': 'text'},
    'keyword': {'label': _l('关键词', 'Keyword'), 'type': 'text'},
    'sign': {'label': _l('签名密钥', 'Sign secret'), 'type': 'password'},
    'callbackUrl': {'label': _l('回调地址', 'Callback URL'), 'type': 'text'},
    'path': {'label': _l('接口路径', 'Path'), 'type': 'text', 'placeholder': '/send_msg'},
}

# 渠道清单：顺序即下拉顺序（国内常用渠道在前）。
PROVIDERS = [
    {
        'id': 'onebot11',
        'label': _l('OneBot 11（QQ 机器人）', 'OneBot 11 (QQ bot)'),
        'site': 'https://github.com/botuniverse/onebot-11',
        'params': {
            'endpoint': {'help': _l(
                'QQ 机器人（如 Lagrange、NapCat）的 HTTP 接口地址，例如 http://127.0.0.1:3000',
                'HTTP endpoint of your QQ bot (Lagrange, NapCat, ...), e.g. http://127.0.0.1:3000')},
            'message_type': {
                'type': 'select',
                'options': _select(
                    ('private', _l('私聊', 'Private')),
                    ('group', _l('群聊', 'Group')),
                ),
                'help': _l('选私聊填用户 ID，选群聊填群号', 'Fill user ID for private, group ID for group'),
            },
            'token': {'help': _l('机器人配置的 access token，未启用鉴权可留空',
                                 'Access token of the bot; leave empty if auth is disabled')},
        },
    },
    {
        'id': 'bark',
        'label': _l('Bark（iOS）', 'Bark (iOS)'),
        'site': 'https://bark.day.app',
        'params': {
            'key': {'help': _l(
                'Bark App 首页服务器地址的最后一段，如 https://api.day.app/XXXXXXXX 中的 XXXXXXXX',
                'Last segment of the server URL on the Bark app home screen, e.g. XXXXXXXX in https://api.day.app/XXXXXXXX')},
        },
    },
    {
        'id': 'telegram',
        'label': _l('Telegram', 'Telegram'),
        'site': 'https://core.telegram.org/bots',
        'params': {
            'token': {'help': _l('找 @BotFather 创建机器人后拿到，形如 123456:ABC-DEF…',
                                 'Create a bot via @BotFather to get it, e.g. 123456:ABC-DEF…')},
            'userid': {'help': _l('你的数字 ID（找 @userinfobot 查询），或 @频道名',
                                  'Your numeric ID (query via @userinfobot), or @channelname')},
            'api_url': {'help': _l('被墙时填自建反代地址，留空用官方 api.telegram.org',
                                   'Fill in your own reverse proxy if api.telegram.org is unreachable')},
        },
    },
    {
        'id': 'dingtalk',
        'label': _l('钉钉群机器人', 'DingTalk bot'),
        'site': 'https://developers.dingtalk.com/document/app/custom-robot-access',
        'params': {
            'token': {'help': _l(
                '机器人 Webhook 里的 access_token 参数，如 https://oapi.dingtalk.com/robot/send?access_token=XXXX 中的 XXXX',
                'The access_token in the bot webhook URL')},
            'secret': {'help': _l('安全设置选「加签」时生成的密钥，选「自定义关键词」可留空',
                                  'Secret generated by the "sign" security setting; leave empty for keyword mode')},
        },
    },
    {
        'id': 'wechatworkbot',
        'label': _l('企业微信群机器人', 'WeCom group bot'),
        'site': 'https://developer.work.weixin.qq.com/document/path/91770',
        'params': {
            'key': {'help': _l(
                '群机器人 Webhook 里的 key 参数，如 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=XXXX 中的 XXXX',
                'The key in the group bot webhook URL')},
        },
    },
    {
        'id': 'wechatworkapp',
        'label': _l('企业微信应用', 'WeCom app'),
        'site': 'https://developer.work.weixin.qq.com/document/path/90236',
        'params': {
            'corpid': {'help': _l('企业微信「我的企业」页面底部的企业 ID', 'Corp ID shown at the bottom of the WeCom "My company" page')},
            'corpsecret': {'help': _l('自建应用的 Secret', 'Secret of the custom app')},
            'agentid': {'help': _l('自建应用的 AgentId', 'AgentId of the custom app')},
            'touser': {'help': _l('接收消息的成员账号，多个用 | 分隔，@all 表示全部',
                                  'Recipient account(s), separated by |; @all for everyone')},
        },
    },
    {
        'id': 'lark',
        'label': _l('飞书群机器人', 'Lark / Feishu bot'),
        'site': 'https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot',
        'params': {
            'webhook': {'help': _l('群机器人 Webhook 地址', 'Group bot webhook URL')},
            'keyword': {'help': _l('安全设置选「自定义关键词」时填写', 'Fill when the security setting is "custom keyword"')},
            'sign': {'help': _l('安全设置选「签名校验」时填写', 'Fill when the security setting is "signature"')},
        },
    },
    {
        'id': 'serverchanturbo',
        'label': _l('Server 酱³', 'ServerChan Turbo'),
        'site': 'https://sct.ftqq.com',
        'params': {
            'sctkey': {'help': _l('SendKey，形如 SCTxxxxxx', 'SendKey, e.g. SCTxxxxxx')},
        },
    },
    {
        'id': 'serverchan',
        'label': _l('Server 酱（旧版）', 'ServerChan (legacy)'),
        'site': 'https://sc.ftqq.com/3.version',
        'params': {
            'sckey': {'help': _l('SCKEY，形如 SCUxxxxxx', 'SCKEY, e.g. SCUxxxxxx')},
        },
    },
    {
        'id': 'pushplus',
        'label': _l('PushPlus', 'PushPlus'),
        'site': 'https://www.pushplus.plus/doc',
        'params': {
            'token': {'help': _l('PushPlus 首页的 token', 'Token shown on the PushPlus home page')},
            'topic': {'help': _l('群组编码，一对一推送可留空', 'Group code; leave empty for one-to-one push')},
            'channel': {'help': _l('留空默认走微信公众号', 'Leave empty to use the WeChat official account')},
        },
    },
    {
        'id': 'pushdeer',
        'label': _l('PushDeer', 'PushDeer'),
        'site': 'https://www.pushdeer.com/official.html',
        'params': {
            'pushkey': {'help': _l('PushDeer App 里显示的 PushKey', 'PushKey shown in the PushDeer app')},
            'url': {'help': _l('自建服务地址，留空用官方 api2.pushdeer.com',
                               'Self-hosted endpoint; leave empty for api2.pushdeer.com')},
            'type': {
                'type': 'select',
                'options': _select(
                    ('text', _l('纯文本', 'Text')),
                    ('markdown', _l('Markdown', 'Markdown')),
                    ('image', _l('图片', 'Image')),
                ),
            },
        },
    },
    {
        'id': 'qmsg',
        'label': _l('Qmsg 酱', 'Qmsg'),
        'site': 'https://qmsg.zendee.cn/api.html',
        'params': {
            'key': {'help': _l('Qmsg 控制台的 key', 'Key shown in the Qmsg console')},
            'qq': {'help': _l('接收消息的 QQ 号', 'QQ number that receives the message')},
        },
    },
    {
        'id': 'gocqhttp',
        'label': _l('go-cqhttp 旧版接口', 'go-cqhttp (legacy)'),
        'site': 'https://docs.go-cqhttp.org',
        'params': {
            'endpoint': {'help': _l('go-cqhttp 的 HTTP 接口地址', 'HTTP endpoint of go-cqhttp')},
            'token': {'help': _l('access token，未启用鉴权可留空', 'Access token; leave empty if auth is disabled')},
            'message_type': {
                'type': 'select',
                'options': _select(
                    ('private', _l('私聊', 'Private')),
                    ('group', _l('群聊', 'Group')),
                ),
            },
        },
    },
    {
        'id': 'gotify',
        'label': _l('Gotify', 'Gotify'),
        'site': 'https://gotify.net',
        'params': {
            'url': {'help': _l('自建 Gotify 地址，如 https://push.example.com',
                               'Your Gotify server, e.g. https://push.example.com')},
            'token': {'help': _l('应用令牌', 'App token')},
        },
    },
    {
        'id': 'discord',
        'label': _l('Discord', 'Discord'),
        'site': 'https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks',
        'params': {
            'webhook': {'help': _l('频道设置 → 整合 → Webhook 里复制的地址', 'Channel settings → Integrations → Webhooks')},
        },
    },
    {
        'id': 'smtp',
        'label': _l('邮件（SMTP）', 'Email (SMTP)'),
        'site': 'https://github.com/LmeSzinc/AzurLaneAutoScript/wiki/Onepush-configuration-%5BCN%5D',
        'params': {
            'host': {'help': _l('邮箱服务商的 SMTP 服务器，如 smtp.qq.com / smtp.163.com',
                                'SMTP server of your mail provider, e.g. smtp.qq.com')},
            'user': {'help': _l('完整邮箱地址，邮件由自己发给自己', 'Full mail address; the mail is sent to yourself')},
            'password': {'help': _l('邮箱的 SMTP 授权码（不是登录密码）', 'SMTP auth code (not your login password)')},
            'port': {'help': _l('留空按 465（SSL）或 25 推断', 'Leave empty to infer 465 (SSL) or 25')},
            'From': {'help': _l('留空用账号本身', 'Leave empty to use the account itself')},
            'To': {'help': _l('留空发给自己', 'Leave empty to mail yourself')},
        },
    },
    {
        'id': 'custom',
        'label': _l('自定义接口', 'Custom endpoint'),
        'params': {
            'url': {'help': _l('接收 POST 的完整地址', 'Full URL that receives the POST')},
            'data': {'help': _l('额外请求体，JSON 格式；推送时会附带 title 与 content',
                                'Extra payload as JSON; title and content are added on push')},
        },
    },
]

# 渠道 -> 中文名的补充说明（下拉里跟在渠道名后的灰字）
PROVIDER_IDS = [item['id'] for item in PROVIDERS]
_PROVIDER_OVERRIDES = {item['id']: item.get('params') or {} for item in PROVIDERS}


def _localize(node, lang):
    if node is None:
        return ''
    if isinstance(node, str):
        return node
    text = node.get(lang)
    if not isinstance(text, str):
        text = node.get('en-US')
    if not isinstance(text, str):
        text = node.get('zh-CN')
    return text if isinstance(text, str) else ''


def _param_names(provider_id: str):
    """渠道声明的参数字段名（必填在前），与 onepush 的自省结果保持一致。"""
    notifier_class = _all_providers.get(provider_id)
    if notifier_class is None:
        return []
    params = notifier_class().params or {}
    names = [name for name in params.get('required', []) if name not in RUNTIME_PARAMS]
    names += [name for name in params.get('optional', []) if name not in RUNTIME_PARAMS]
    # 去重保序：个别渠道把同一参数同时列进 required 与 optional。
    return list(dict.fromkeys(names))


def _param_spec(name, required, overrides):
    spec = dict(PARAM_META.get(name, {}))
    spec.update(overrides.get(name) or {})
    spec['key'] = name
    spec['required'] = bool(required)
    spec.setdefault('type', 'text')
    spec.setdefault('placeholder', '')
    return spec


def describe(lang: str) -> list:
    """渠道清单与参数字段定义，供 WebUI 组件直接渲染。"""
    providers = []
    for item in PROVIDERS:
        provider_id = item['id']
        notifier_class = _all_providers.get(provider_id)
        if notifier_class is None:
            continue
        required_names = {
            name for name in (notifier_class().params or {}).get('required', [])
            if name not in RUNTIME_PARAMS
        }
        overrides = item.get('params', {})
        providers.append({
            'id': provider_id,
            'label': _localize(item.get('label'), lang),
            'site': item.get('site') or getattr(notifier_class, 'site_url', None) or '',
            'params': [
                _resolve(_param_spec(name, name in required_names, overrides), lang)
                for name in _param_names(provider_id)
            ],
        })
    return providers


def _resolve(spec, lang):
    resolved = {key: value for key, value in spec.items() if key not in ('label', 'help', 'options', 'placeholder')}
    resolved['label'] = _localize(spec.get('label'), lang) or spec['key']
    resolved['help'] = _localize(spec.get('help'), lang)
    resolved['placeholder'] = _localize(spec.get('placeholder'), lang)
    resolved['options'] = [
        {'value': option['value'], 'label': _localize(option['label'], lang)}
        for option in spec.get('options', [])
    ]
    return resolved


def read_config(text) -> dict:
    """把 OnePushConfig 的 YAML 文本读成扁平字典（与 handle_notify_linux 同规则）。"""
    config = {}
    if not text:
        return config
    for item in yaml.safe_load_all(str(text)):
        if isinstance(item, dict):
            config.update(item)
    return config


def describe_plan(text, lang: str) -> dict:
    """渠道清单 + 当前配置的解析结果，WebUI 组件一次请求拿到全部渲染数据。"""
    try:
        config = read_config(text)
        error = ''
    except yaml.YAMLError as exc:
        # 用户可能正在源码模式里编辑半成品 YAML，这里不让接口失败。
        return {'providers': describe(lang), 'config': {}, 'provider': '', 'extra': [], 'error': str(exc)}
    known = set()
    for provider_id in PROVIDER_IDS:
        known.update(_param_names(provider_id))
    return {
        'providers': describe(lang),
        'config': config,
        'provider': config.get('provider') or '',
        'extra': sorted(key for key in config if key != 'provider' and key not in known),
        'error': error,
    }


def write_config(config: dict) -> str:
    """把扁平字典写回 YAML 文本，字段顺序与渠道定义一致，便于人工阅读。"""
    provider = config.get('provider') or None
    order = _param_names(provider) if provider in PROVIDER_IDS else []
    ordered = {'provider': provider}
    for name in order:
        if name in config:
            ordered[name] = config[name]
    for name, value in config.items():
        if name != 'provider' and name not in ordered:
            ordered[name] = value
    return yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()


def coerce(name: str, value, provider_id: str):
    """按控件类型把前端传来的字符串转成 onepush 期望的类型。"""
    spec = {**PARAM_META.get(name, {}), **(_PROVIDER_OVERRIDES.get(provider_id, {}).get(name) or {})}
    kind = spec.get('type', 'text')
    if isinstance(value, str):
        value = value.strip()
    if value == '' or value is None:
        return None
    if kind == 'bool':
        if isinstance(value, bool):
            return value
        return str(value).lower() in ('1', 'true', 'yes', 'on')
    if kind == 'number':
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if kind == 'json':
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value
