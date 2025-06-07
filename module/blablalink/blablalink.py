from datetime import datetime, timedelta
import random
import requests
import json
import time
from module.logger import logger
from module.ui.ui import UI

class NoCookie(Exception):
    pass

class Blablalink(UI):
    # 基本头部信息（不含x-common-params）
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
        'x-language': 'zh-TW',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36'
    }
    
    def __init__(self, config):
        super().__init__(config)
        self.session = requests.Session()
        self.common_headers = self.base_headers.copy()
        self._prepare_config()
        
    def _prepare_config(self):
        """从配置中准备所有必要参数"""
        # 获取Cookie
        cookie = self.config.data.get('BlablalinkCookie')
        if not cookie:
            raise NoCookie("未配置Cookie")
        self.common_headers['cookie'] = cookie
        logger.info("✅ Cookie设置成功")
        
        # 获取OpenID
        openid = self.config.data.get('BlablalinkOpenid')
        if not openid:
            logger.warning("⚠️ 未配置OpenID，使用默认值")
            openid = "MjkwODAtNjYwMjIxODA2MzI4MDE3MDY2Nw=="  # 默认值
        
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
        logger.info(f"✅ OpenID设置成功: {openid[:8]}...")
    
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
                logger.warning(f"请求失败，正在重试 ({attempt+1}/{max_retries}): {str(e)}")
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
            logger.error(f"状态检查异常: {str(e)}")
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
            logger.error(f"获取任务列表失败: {str(e)}")
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
                logger.info("✅ 签到成功")
                return True
            logger.error(f"❌ 签到失败: {result.get('msg', '未知错误')}")
            return False
        except Exception as e:
            logger.error(f"签到请求异常: {str(e)}")
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
            logger.error(f"获取金币失败: {str(e)}")
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
            logger.warning(f"⚠️ 获取帖子列表失败：{response.get('msg', '未知错误')}")
            return []
        except Exception as e:
            logger.error(f"⚠️ 获取帖子列表异常：{str(e)}")
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
                logger.info(f"✅ 点赞成功：{post_uuid[:8]}...")
                return True
            logger.error(f"❌ 点赞失败：{result.get('msg', '未知错误')}")
            return False
        except Exception as e:
            logger.error(f"⚠️ 点赞请求异常：{str(e)}")
            return False
    
    def like_random_posts(self):
        """随机点赞5个帖子"""
        logger.info("\n👍 开始执行点赞任务")
        post_uuids = self.get_post_list()
        
        if not post_uuids:
            logger.warning("⚠️ 没有可点赞的帖子")
            return

        selected = random.sample(post_uuids, min(5, len(post_uuids)))
        logger.info(f"🔍 随机选择 {len(selected)} 个帖子进行点赞")
        
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
                logger.info(f"✅ 打开帖子成功：{post_uuid[:8]}...")
                return True
            logger.error(f"❌ 打开帖子失败：{result.get('msg', '未知错误')}")
            return False
        except Exception as e:
            logger.error(f"⚠️ 打开请求异常：{str(e)}")
            return False
    
    def open_random_posts(self):
        """随机打开3个帖子"""
        logger.info("\n📖 开始浏览帖子任务")
        post_uuids = self.get_post_list()
        
        if not post_uuids:
            logger.warning("⚠️ 没有可浏览的帖子")
            return

        selected = random.sample(post_uuids, min(3, len(post_uuids)))
        logger.info(f"🔍 随机选择 {len(selected)} 个帖子浏览")
        
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
            logger.error(f"⚠️ 获取表情列表异常：{str(e)}")
            return ""
    
    def post_comment(self):
        """发布评论"""
        logger.info("\n💬 开始评论任务")
        comment_config = self.config.data.get('BlablalinkComment')
        if not comment_config:
            logger.warning("⚠️ 未配置评论参数")
            return

        post_uuid = comment_config.get("post_uuid")
        comment_uuid = comment_config.get("comment_uuid")
        
        if not post_uuid or not comment_uuid:
            logger.warning("⚠️ 评论参数不完整")
            return

        emoji_url = self._get_random_emoji()
        if not emoji_url:
            logger.warning("⚠️ 未找到可用表情")
            return

        content = f'<p><img src="{emoji_url}?imgtype=emoji" width="60" height="60"></p>'
        
        try:
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/ugc/proxy/standalonesite/Dynamics/PostComment',
                json={
                    "pic_urls": [],
                    "content": content,
                    "post_uuid": post_uuid,
                    "comment_uuid": comment_uuid,
                    "type": 2,
                    "users": []
                }
            )
            
            if result.get('code') == 0:
                logger.info(f"✅ 评论成功 (PID: {post_uuid[:8]}...)")
            else:
                logger.error(f"❌ 评论失败：{result.get('msg', '未知错误')}")
        except Exception as e:
            logger.error(f"⚠️ 评论请求异常：{str(e)}")
    
    def run(self):
        """主执行流程"""
        local_now = datetime.now()
        target_time = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
        
        if local_now > target_time:
            try:
                logger.info("✅ 开始签到流程")
                
                # 点赞任务
                self.like_random_posts()
                
                # 浏览任务
                self.open_random_posts()
                
                # 评论任务
                self.post_comment()
                
                # 获取任务列表
                tasks_data = self.get_tasks()
                if not tasks_data:
                    logger.error("⚠️ 无法获取任务列表")
                    return
                
                # 检查签到状态
                found, completed, task_id = self.check_daily_status(tasks_data)
                if not found:
                    logger.error("⚠️ 未找到每日签到任务")
                    return
                
                logger.info(f"🔍 提取到任务ID: {task_id}")
                status_msg = "已完成" if completed else "未完成"
                logger.info(f"📅 签到状态: {status_msg}")
                
                # 执行签到
                if not completed:
                    if self.perform_signin(task_id):
                        points = self.get_points()
                        logger.info(f"💰 当前金币: {points}")
            
            except NoCookie as e:
                logger.error(f"NoCookie: {str(e)}")
                logger.warning("请确认已正确配置Cookie")
            except Exception as e:
                logger.error(f"主流程异常: {str(e)}")
            
            # 设置延迟到第二天8点后
            next_day = local_now + timedelta(days=1)
            next_target = next_day.replace(hour=8, minute=random.randint(5, 30), second=0)
            self.config.task_delay(target=next_target)
        else:
            # 计算随机延迟时间
            random_minutes = random.randint(5, 30)
            target_time = target_time + timedelta(minutes=random_minutes)
            self.config.task_delay(target=target_time)