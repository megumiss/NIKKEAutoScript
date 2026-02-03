import base64
import os

import onepush.core
import yaml
from onepush import get_notifier
from onepush.core import Provider
from onepush.exceptions import OnePushException
from onepush.providers.custom import Custom
from requests import Response

from module.logger import logger
from module.webui.icon import ICON

onepush.core.log = logger


def handle_notify_win(**kwargs) -> bool:
    from winotify import Notification

    toast = Notification(
        app_id='NKAS',
        title=kwargs['title'],
        msg=kwargs['content'],
        icon=ICON.Icon,
        duration='long',
    )
    toast.show()

    logger.info('Push notify success')
    return True


def handle_notify_linux(_config: str, **kwargs) -> bool:
    # 引入 SMTP 图片处理所需的库
    import mimetypes
    from email.message import EmailMessage

    # 定义 SMTP 自定义解析器 (支持图片附件)
    def _smtp_image_parser(subject='', title='', content='', From=None, user=None, To=None, image_path=None, **kwargs):
        msg = EmailMessage()
        msg["Subject"] = subject or title
        msg["From"] = From or user
        msg["To"] = To or user
        msg.set_content(content)

        if image_path and os.path.exists(image_path):
            ctype, encoding = mimetypes.guess_type(image_path)
            if ctype is None or encoding is not None:
                ctype = 'application/octet-stream'
            maintype, subtype = ctype.split('/', 1)
            try:
                with open(image_path, 'rb') as f:
                    file_data = f.read()
                    filename = os.path.basename(image_path)
                    msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=filename)
            except Exception as e:
                logger.error(f'Failed to attach image for SMTP: {e}')
        return msg

    try:
        config = {}
        for item in yaml.safe_load_all(_config):
            config.update(item)
    except Exception:
        logger.error('Fail to load onepush config, skip sending')
        return False
    try:
        provider_name: str = config.pop('provider', None)
        if provider_name is None:
            logger.info('No provider specified, skip sending')
            return False
        notifier: Provider = get_notifier(provider_name)

        required: list[str] = notifier.params['required']

        image_path = kwargs.get('image_path')
        if image_path and os.path.exists(image_path):
            # 保留原有的 gocqhttp 图片处理逻辑
            if provider_name.lower() == 'gocqhttp':
                try:
                    with open(image_path, 'rb') as f:
                        b64 = base64.b64encode(f.read()).decode('utf-8')
                    cq = f'[CQ:image,file=base64://{b64}]'
                    if 'content' in kwargs:
                        kwargs['content'] += f'\n{cq}'
                    elif 'content' in config:
                        config['content'] += f'\n{cq}'
                    else:
                        kwargs['content'] = cq
                except Exception as e:
                    logger.error(f'Failed to process image for gocqhttp: {e}')
            if provider_name.lower() == 'smtp':
                notifier.set_message_parser(_smtp_image_parser)

        config.update(kwargs)

        # pre check
        for key in required:
            if key not in config:
                logger.warning(f"Notifier {notifier.name} require param '{key}' but not provided")

        if isinstance(notifier, Custom):
            if 'method' not in config or config['method'] == 'post':
                config['datatype'] = 'json'
            if not ('data' in config or isinstance(config['data'], dict)):
                config['data'] = {}
            if 'title' in kwargs:
                config['data']['title'] = kwargs['title']
            if 'content' in kwargs:
                config['data']['content'] = kwargs['content']

        if provider_name.lower() == 'gocqhttp':
            access_token = config.get('access_token')
            if access_token:
                config['token'] = access_token

        resp = notifier.notify(**config)
        if isinstance(resp, Response):
            if resp.status_code != 200:
                logger.warning('Push notify failed!')
                logger.warning(f'HTTP Code:{resp.status_code}')
                return False
            else:
                if provider_name.lower() == 'gocqhttp':
                    return_data: dict = resp.json()
                    if return_data['status'] == 'failed':
                        logger.warning('Push notify failed!')
                        logger.warning(f'Return message:{return_data["wording"]}')
                        return False
    except OnePushException:
        logger.exception('Push notify failed')
        return False
    except Exception as e:
        logger.exception(e)
        return False

    logger.info('Push notify success')
    return True
