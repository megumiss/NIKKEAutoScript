import mimetypes
import os
from email.message import EmailMessage

from module.logger import logger


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
        except Exception as e:
            logger.error(f'Failed to attach image for SMTP: {e}')
    return msg