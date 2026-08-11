import mimetypes
import os
from email.message import EmailMessage

from onepush.providers.smtp import SMTP as OnePushSMTP

from module.logger import logger


class SMTP(OnePushSMTP):
    """带日志的 SMTP provider，替换 onepush 默认实现（自身无任何日志）"""

    name = 'smtp'

    def _prepare_url(self, host, user, password, port=0, ssl=None, starttls=False, **kwargs):
        logger.info(f'SMTP connect: host={host}, port={port}, ssl={ssl}, starttls={starttls}')
        super()._prepare_url(host=host, user=user, password=password, port=port,
                             ssl=ssl, starttls=starttls, **kwargs)
        logger.info(f'SMTP login success: user={user}')

    def _send_message(self):
        msg = self.data.get('msg')
        logger.info(f"SMTP sending: From={msg['From']}, To={msg['To']}, Subject={msg['Subject']}")
        refused = super()._send_message()
        if refused:
            logger.warning(f'SMTP recipients refused: {list(refused)}')
        else:
            logger.info('SMTP message sent')
        return refused


def smtp_image_parser(self, subject='', title='', content='', From=None, user=None, To=None, image_path=None, **kwargs):
    """SMTP 自定义解析器 (支持图片附件)"""
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
            logger.info(f'SMTP image attached: {filename}')
        except Exception as e:
            logger.error(f'Failed to attach image for SMTP: {e}')
            logger.warning('SMTP will send without image attachment')
    return msg