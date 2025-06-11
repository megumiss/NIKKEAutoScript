from datetime import datetime, timedelta
import random
from typing import Dict, Tuple
import requests
import json
import time
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ui.ui import UI

class MissingHeader(Exception):
    pass

class Blablalink(UI):
    # 基本头部信息
    base_headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'zh-CN,zh;q=0.9',
        'content-type': 'application/json',
        'origin': 'https://www.blablalink.com',
        'priority': 'u=1, i',
        'referer': 'https://www.blablalink.com/',
        'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'x-language': 'zh-TW'
    }
    
    def __init__(self, config):
        super().__init__(config)
        self.session = requests.Session()
        self.common_headers = self.base_headers.copy()
        self._prepare_config()
        
    def _prepare_config(self):
        """从配置中准备所有必要参数"""
        # 获取Cookie
        cookie = self.config.Blablalink_Cookie
        if not cookie:
            logger.error("Cookie not configured")
            raise RequestHumanTakeover("Cookie not set")
        self.common_headers['cookie'] = cookie
        # 获取OpenID
        openid = self.config.Blablalink_OpenID
        if not openid:
            logger.error("OpenID not configured")
            raise RequestHumanTakeover("OpenID not set")

        # 构建x-common-params
        common_params = {
            "game_id": "16",
            "area_id": "global",
            "source": "pc_web",
            "intl_game_id": "29080",
            "language": "zh-TW",
            "env": "prod",
            "data_statistics_scene": "outer",
            "data_statistics_page_id": f"https://www.blablalink.com/user?openid={openid}",
            "data_statistics_client_type": "pc_web",
            "data_statistics_lang": "zh-TW"
        }
        self.common_headers['x-common-params'] = json.dumps(common_params, ensure_ascii=False)
        # 获取user-agent
        useragent = self.config.Blablalink_UserAgent
        if not useragent:
            logger.warning("User-agent configured")
            raise RequestHumanTakeover("User-agent not set")
        self.common_headers['user-agent'] = useragent

        logger.info(f"Headers build successfully")
    
    def _request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs) -> Dict:
        """带重试机制的请求封装"""
        for attempt in range(max_retries):
            delay = random.uniform(3.0, 10.0)
            time.sleep(delay)
            
            try:
                response = self.session.request(
                    method, 
                    url, 
                    headers=self.common_headers, 
                    **kwargs
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f"Request failed, retrying ({attempt+1}/{max_retries}): {str(e)}")
        return {}
    
    def check_daily_status(self, data: Dict) -> Tuple[bool, bool, str]:
        """检查签到状态"""
        try:
            tasks = data.get('data', {}).get('tasks', [])
            for task in tasks:
                if task.get('task_name') == '每日簽到':
                    reward = next(iter(task.get('reward_infos', [])), None)
                    task_id = task.get('task_id', '')
                    return True, reward.get('is_completed', False) if reward else False, task_id
            return False, False, ''
        except Exception as e:
            logger.error(f"Status check exception: {str(e)}")
            return False, False, ''
    
    def get_tasks(self) -> Dict:
        """获取任务列表"""
        try:
            return self._request_with_retry(
                'POST', 
                'https://api.blablalink.com/api/lip/proxy/lipass/Points/GetTaskListWithStatusV2',
                params={'get_top': 'true', 'intl_game_id': '29080'}
            )
        except Exception as e:
            logger.error(f"Failed to get task list: {str(e)}")
            return {}
    
    def perform_signin(self, task_id: str) -> bool:
        """执行签到操作"""
        try:          
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/lip/proxy/lipass/Points/DailyCheckIn',
                json={"task_id": task_id}
            )
            if result.get('msg') == 'ok':
                logger.info("Sign-in successful")
                return True
            logger.error(f"Sign-in failed: {result.get('msg', 'Unknown error')}")
            return False
        except Exception as e:
            logger.error(f"Sign-in request exception: {str(e)}")
            return False
    
    def get_points(self) -> int:
        """获取金币数量"""
        try:
            result = self._request_with_retry(
                'GET',
                'https://api.blablalink.com/api/lip/proxy/lipass/Points/GetUserTotalPoints'
            )
            if result.get('msg') == 'ok':
                return result.get('data', {}).get('total_points', 0)
            return 0
        except Exception as e:
            logger.error(f"Failed to get points: {str(e)}")
            return 0
    
    def get_post_list(self) -> list:
        """获取帖子列表"""
        try:
            url = "https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetPostList"
            body = {
                "search_type": 0,
                "plate_id": 38,
                "plate_unique_id": "outpost",
                "nextPageCursor": "",
                "order_by": 1,
                "limit": "10"
            }
            response = self._request_with_retry('POST', url, json=body)
            
            if response.get('code') == 0:
                return [post['post_uuid'] for post in response.get('data', {}).get('list', [])]
            logger.warning(f"Failed to get post list: {response.get('msg', 'Unknown error')}")
            return []
        except Exception as e:
            logger.error(f"Exception when getting post list: {str(e)}")
            return []
    
    def like_post(self, post_uuid: str) -> bool:
        """点赞单个帖子"""
        try:
            url = "https://api.blablalink.com/api/ugc/proxy/standalonesite/Dynamics/PostStar"
            result = self._request_with_retry(
                'POST',
                url,
                json={"post_uuid": post_uuid, "type": 1, "like_type": 1}
            )
            
            if result.get('code') == 0:
                logger.info(f"Liked successfully: {post_uuid}")
                return True
            logger.error(f"Like failed: {result.get('msg', 'Unknown error')}")
            return False
        except Exception as e:
            logger.error(f"Exception when liking: {str(e)}")
            return False
    
    def like_random_posts(self):
        """随机点赞5个帖子"""
        logger.info("Starting like task")
        post_uuids = self.get_post_list()
        
        if not post_uuids:
            logger.warning("No posts available to like")
            return

        selected = random.sample(post_uuids, min(5, len(post_uuids)))
        logger.info(f"Randomly selected {len(selected)} posts to like")
        
        for post_uuid in selected:
            self.like_post(post_uuid)
            time.sleep(random.uniform(1.5, 3.5))
    
    def open_post(self, post_uuid: str) -> bool:
        """打开单个帖子"""
        try:
            url = "https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetPost"
            result = self._request_with_retry(
                'POST',
                url,
                json={"post_uuid": post_uuid}
            )
            
            if result.get('code') == 0:
                logger.info(f"Opened post successfully: {post_uuid}")
                return True
            logger.error(f"Failed to open post: {result.get('msg', 'Unknown error')}")
            return False
        except Exception as e:
            logger.error(f"Exception when opening post: {str(e)}")
            return False
    
    def open_random_posts(self):
        """随机打开3个帖子"""
        logger.info("Starting browse posts task")
        post_uuids = self.get_post_list()
        
        if not post_uuids:
            logger.warning("No posts available to browse")
            return

        selected = random.sample(post_uuids, min(3, len(post_uuids)))
        logger.info(f"Randomly selected {len(selected)} posts to browse")
        
        for post_uuid in selected:
            self.open_post(post_uuid)
            time.sleep(random.uniform(2.0, 5.0))
    
    def _get_random_emoji(self) -> str:
        """获取随机表情URL"""
        try:
            response = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetAllEmoticons'
            )
            
            if response.get('code') == 0:
                emojis = []
                for group in response.get('data', {}).get('list', []):
                    emojis.extend([icon['pic_url'] for icon in group.get('icon_list', [])])
                if emojis:
                    return random.choice(emojis)
            return ""
        except Exception as e:
            logger.error(f"Exception when getting emoji list: {str(e)}")
            return ""
    
    def post_comment(self):
        """发布评论"""
        logger.info("Starting comment task")
        post_uuid = self.config.Blablalink_PostID
        comment_uuid = self.config.Blablalink_CommentID
        
        if not post_uuid:
            logger.warning("PostID is required")
            return
        
        request_body = {
            "pic_urls": [],
            "post_uuid": f"{post_uuid}",
            "type": 1, # 评论帖子
            "users": []
        }
        
        if comment_uuid:
            request_body["comment_uuid"] = f"{comment_uuid}"
            request_body["type"] = 2  # 回复评论
            logger.info(f"Replying to comment {comment_uuid} in post {post_uuid}")
        else:
            logger.info(f"Commenting on post {post_uuid}")
        
        emoji_url = self._get_random_emoji()
        if not emoji_url:
            logger.warning("No available emoji found")
            return
        request_body["content"] = f'<p><img src="{emoji_url}?imgtype=emoji" width="60" height="60"></p>'
        
        try:
            # _ = self._request_with_retry(
            #     'OPTIONS',
            #     'https://api.blablalink.com/api/ugc/proxy/standalonesite/Dynamics/PostComment'
            # )
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/ugc/proxy/standalonesite/Dynamics/PostComment',
                json=request_body
            )
            
            if result.get('code') == 0:
                if comment_uuid:
                    logger.info(f"Reply successful (PID: {post_uuid})")
                else:
                    logger.info(f"Comment successful (PID: {post_uuid})")
            else:
                logger.error(f"Comment failed: {result.get('msg', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Exception when posting comment: {str(e)}")
    
    def run(self):
        """主执行流程"""
        local_now = datetime.now()
        target_time = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
        
        if local_now > target_time or self.config.Blablalink_Immediately:
            try:
                logger.info("Starting blablalink daily tasks")
                # 点赞任务
                self.like_random_posts()
                # 浏览任务
                self.open_random_posts()
                # 评论任务
                self.post_comment()
                # 签到
                # 获取任务列表
                tasks_data = self.get_tasks()
                if not tasks_data:
                    logger.error("Failed to get task list")
                    return
                # 检查签到状态
                found, completed, task_id = self.check_daily_status(tasks_data)
                if not found:
                    logger.error("Daily checkin task not found")
                    return
                logger.info(f"Checkin task ID: {task_id}")
                status_msg = "Completed" if completed else "Not completed"
                logger.info(f"Checkin status: {status_msg}")
                # 执行签到
                if not completed:
                    if not self.perform_signin(task_id):
                        logger.error("Failed to get task list")
                
                # 获取金币数量
                points = self.get_points()
                self.config.Blablalink_Points = points
                logger.info(f"Current points: {points}")
            except MissingHeader as e:
                logger.error("Please check all parameters settings")
                raise RequestHumanTakeover
            except Exception as e:
                logger.error(f"Blablalink exception: {str(e)}")
                raise RequestHumanTakeover
            self.config.task_delay(server_update=True)
        else:
            random_minutes = random.randint(5, 30)
            target_time = target_time + timedelta(minutes=random_minutes)
            self.config.task_delay(target=target_time)
