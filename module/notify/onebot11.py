import base64
import os

import requests
from onepush.core import Provider
from requests import Response

from module.logger import logger


class OneBot11(Provider):
    name = 'onebot11'

    def __init__(self):
        super().__init__()
        # 更新 onepush 校验所需的参数
        self._params = {
            'required': ['endpoint', 'message_type'],
            'optional': ['token', 'user_id', 'group_id', 'title', 'content', 'image_path']
        }

    def notify(self, **kwargs) -> Response:
        """重写 notify 方法，接管完整的推送逻辑"""
        # 读取新的参数格式
        endpoint = kwargs.get('endpoint', '').rstrip('/')
        token = kwargs.get('token', '')
        message_type = kwargs.get('message_type', '')
        user_id = kwargs.get('user_id')
        group_id = kwargs.get('group_id')
        
        # 准备一个假的 Response 对象，用于兼容原 notify.py 的 status_code 校验逻辑
        mock_resp = Response()

        # 根据 message_type 校验必须的 ID 参数
        if not endpoint:
            logger.error("Notifier onebot11 require param 'endpoint'")
            mock_resp.status_code = 400
            return mock_resp
        if message_type == 'private' and not user_id:
            logger.error("Notifier onebot11 require param 'user_id' when message_type is 'private'")
            mock_resp.status_code = 400
            return mock_resp
        elif message_type == 'group' and not group_id:
            logger.error("Notifier onebot11 require param 'group_id' when message_type is 'group'")
            mock_resp.status_code = 400
            return mock_resp
        elif message_type not in ['private', 'group']:
            logger.error("Notifier onebot11 'message_type' must be 'private' or 'group'")
            mock_resp.status_code = 400
            return mock_resp

        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'

        # 确定发送的 API 路由和基础载荷
        payload_base = {}
        if message_type == 'group':
            api_url = f"{endpoint}/send_group_msg"
            payload_base['group_id'] = int(group_id)
        else:
            api_url = f"{endpoint}/send_private_msg"
            payload_base['user_id'] = int(user_id)

        title = kwargs.get('title', '')
        content = kwargs.get('content', '')
        text_msg = f"{title}\n{content}".strip()
        
        success = True
        
        # 1. 优先发送文本消息
        if text_msg:
            payload_text = payload_base.copy()
            payload_text['message'] = [{'type': 'text', 'data': {'text': text_msg}}]
            try:
                resp_text = requests.post(api_url, json=payload_text, headers=headers)
                if resp_text.status_code != 200:
                    logger.warning(f'OneBot11 text push failed! HTTP Code:{resp_text.status_code}')
                    success = False
            except Exception as e:
                logger.error(f'OneBot11 text push error: {e}')
                success = False

        # 2. 随后发送图片消息 (转 Base64)
        image_path = kwargs.get('image_path')
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, 'rb') as f:
                    b64_data = base64.b64encode(f.read()).decode('utf-8')
                
                payload_img = payload_base.copy()
                payload_img['message'] = [{'type': 'image', 'data': {'file': f'base64://{b64_data}'}}]
                
                resp_img = requests.post(api_url, json=payload_img, headers=headers)
                if resp_img.status_code != 200:
                    logger.warning(f'OneBot11 image push failed! HTTP Code:{resp_img.status_code}')
                    success = False
            except Exception as e:
                logger.error(f'OneBot11 image push error: {e}')
                success = False

        # 只要成功发送，就返回 200 让上层判定成功
        mock_resp.status_code = 200 if success else 500
        return mock_resp