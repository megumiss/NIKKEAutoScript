import os

import onepush.core
import yaml
from onepush import get_notifier
from onepush.core import Provider
from onepush.exceptions import OnePushException
from onepush.providers.custom import Custom
from requests import Response

from module.logger import logger
from module.notify import handle_notify
from module.ui.ui import UI
from module.webui.icon import ICON

from .onebot11 import OneBot11
from .smtp import SMTP, smtp_image_parser

onepush.core._all_providers['onebot11'] = OneBot11
onepush.core._all_providers['smtp'] = SMTP
onepush.core.log = logger


def handle_notify_win(**kwargs) -> bool:
    from winotify import Notification

    icon = kwargs.get('image_path')
    if not icon or not os.path.exists(icon):
        icon = ICON.Icon

    toast = Notification(
        app_id='NKAS',
        title=kwargs.get('title', 'NKAS'),
        msg=kwargs.get('content', ''),
        icon=icon,
        duration='long',
    )
    toast.show()

    logger.info('Push notify success')
    return True


def handle_notify_linux(_config: str, **kwargs) -> bool:
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
        logger.info(f'Push notify via provider: {notifier.name}')
        title = kwargs.get('title')
        if title:
            logger.info(f'Push notify title: {title}')

        required: list[str] = notifier.params['required']
        image_path = kwargs.get('image_path')
        if image_path and os.path.exists(image_path):
            logger.info(f'Push notify with image: {image_path}')
            if provider_name.lower() == 'smtp':
                # 调用从 smtp.py 导入的解析器
                notifier.set_message_parser(smtp_image_parser)

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

        resp = notifier.notify(**config)
        if isinstance(resp, Response):
            if resp.status_code != 200:
                logger.warning(f'Push notify failed via {notifier.name}!')
                logger.warning(f'HTTP Code:{resp.status_code}')
                return False
    except OnePushException:
        logger.exception('Push notify failed')
        return False
    except Exception as e:
        logger.exception(e)
        return False

    logger.info(f'Push notify success via {notifier.name}')
    return True


class Notify(UI):
    def run(self):
        if self.config.Notification_WhenDailyTaskCompleted:
            handle_notify(
                config=self.config,
                title_key='DailyTaskCompleted.title',
                content_key='DailyTaskCompleted.content',
                always=self.config.Notification_WinOnePush,
            )
        else:
            logger.info('Notify config disabled, skip sending')
        # 按任务配置的周期/时间排期（执行时间管理页可改），不再写死服务器刷新时间
        self.config.task_delay(schedule=True)
