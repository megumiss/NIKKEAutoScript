import json
import random
import re
import time
from datetime import datetime, timedelta
from typing import Dict, Tuple

import requests

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
        'x-channel-type': '2',
        'x-language': 'zh-TW',
    }

    def __init__(self, config):
        super().__init__(config)
        self.session = requests.Session()
        self.common_headers = self.base_headers.copy()
        self._prepare_config()

    def _prepare_config(self):
        """从配置中准备所有必要参数"""
        # 获取Cookie
        cookie = self.config.BlaAuth_Cookie
        if not cookie:
            logger.error('Cookie not configured')
            raise RequestHumanTakeover('Cookie not set')
        self.common_headers['cookie'] = cookie
        # 获取OpenID
        openid = self.config.BlaAuth_OpenID
        if not openid:
            logger.error('OpenID not configured')
            raise RequestHumanTakeover('OpenID not set')

        # 构建x-common-params
        common_params = {
            'game_id': '16',
            'area_id': 'global',
            'source': 'pc_web',
            'intl_game_id': '29080',
            'language': 'zh-TW',
            'env': 'prod',
            'data_statistics_scene': 'outer',
            'data_statistics_page_id': f'https://www.blablalink.com/user?openid={openid}',
            'data_statistics_client_type': 'pc_web',
            'data_statistics_lang': 'zh-TW',
        }
        self.common_headers['x-common-params'] = json.dumps(common_params, ensure_ascii=False)
        # 获取user-agent
        useragent = self.config.BlaAuth_UserAgent
        if not useragent:
            logger.warning('User-agent configured')
            raise RequestHumanTakeover('User-agent not set')
        self.common_headers['user-agent'] = useragent

        logger.info('Headers build successfully')

    def _request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs) -> Dict:
        """带重试机制的请求封装"""
        for attempt in range(max_retries):
            delay = random.uniform(3.0, 10.0)
            time.sleep(delay)

            try:
                response = self.session.request(method, url, headers=self.common_headers, **kwargs)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f'Request failed, retrying ({attempt + 1}/{max_retries}): {str(e)}')
        return {}

    def check_daily_status(self, data: Dict) -> Tuple[bool, bool, str]:
        """检查签到状态"""
        try:
            tasks = data.get('data', {}).get('tasks', [])
            for task in tasks:
                if task.get('task_name') == '每日簽到':
                    reward = next(iter(task.get('reward_infos', [])), None)
                    task_id = task.get('task_id', '')
                    return (
                        True,
                        reward.get('is_completed', False) if reward else False,
                        task_id,
                    )
            return False, False, ''
        except Exception as e:
            logger.error(f'Status check exception: {str(e)}')
            return False, False, ''

    def get_tasks(self) -> Dict:
        """获取任务列表"""
        try:
            return self._request_with_retry(
                'GET',
                'https://api.blablalink.com/api/lip/proxy/lipass/Points/GetTaskListWithStatusV2?get_top=false&intl_game_id=29080',
                # params={'get_top': 'false', 'intl_game_id': '29080'}
            )
        except Exception as e:
            logger.error(f'Failed to get task list: {str(e)}')
            return {}

    def execute_signin(self, task_id: str):
        """执行签到任务"""
        if not task_id:
            logger.error('No signin task ID found')
            return

        logger.info('Executing signin task')
        if self.perform_signin(task_id):
            logger.info('Signin completed successfully')
        else:
            logger.error('Signin failed')

    def perform_signin(self, task_id: str) -> bool:
        """执行签到操作"""
        try:
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/lip/proxy/lipass/Points/DailyCheckIn',
                json={'task_id': task_id},
            )
            if result.get('msg') == 'ok':
                logger.info('Sign-in successful')
                return True
            logger.error(f'Sign-in failed: {result.get("msg", "Unknown error")}')
            return False
        except Exception as e:
            logger.error(f'Sign-in request exception: {str(e)}')
            return False

    def get_points(self) -> int:
        """获取金币数量"""
        try:
            result = self._request_with_retry(
                'GET',
                'https://api.blablalink.com/api/lip/proxy/lipass/Points/GetUserTotalPoints',
            )
            if result.get('msg') == 'ok':
                return result.get('data', {}).get('total_points', 0)
            return 0
        except Exception as e:
            logger.error(f'Failed to get points: {str(e)}')
            return 0

    def get_post_list(self, exclude_liked: bool = False) -> list:
        """获取帖子列表
        :param exclude_liked: 是否排除已点赞的帖子
        """
        try:
            url = 'https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetPostList'
            body = {
                'search_type': 0,
                'plate_id': 38,
                'plate_unique_id': 'outpost',
                'nextPageCursor': '',
                'order_by': 1,
                'limit': '20' if exclude_liked else '10',  # 需要过滤时获取更多帖子
            }
            response = self._request_with_retry('POST', url, json=body)

            if response.get('code') != 0:
                logger.warning(f'Failed to get post list: {response.get("msg", "Unknown error")}')
                return []

            posts = response.get('data', {}).get('list', [])
            result = []

            for post in posts:
                post_uuid = post.get('post_uuid')

                # 排除已点赞的帖子
                if exclude_liked and post.get('my_upvote') is not None:
                    continue

                if post_uuid:
                    result.append(post_uuid)

            logger.info(f'Got {len(result)} posts (exclude_liked={exclude_liked})')
            return result
        except Exception as e:
            logger.error(f'Exception when getting post list: {str(e)}')
            return []

    def like_post(self, post_uuid: str) -> bool:
        """点赞单个帖子"""
        try:
            url = 'https://api.blablalink.com/api/ugc/proxy/standalonesite/Dynamics/PostStar'
            result = self._request_with_retry('POST', url, json={'post_uuid': post_uuid, 'type': 1, 'like_type': 1})

            if result.get('code') == 0:
                logger.info(f'Liked successfully: {post_uuid}')
                return True
            logger.error(f'Like failed: {result.get("msg", "Unknown error")}')
            return False
        except Exception as e:
            logger.error(f'Exception when liking: {str(e)}')
            return False

    def like_random_posts(self):
        """随机点赞5个帖子（只选未点赞过的）"""
        logger.info('Starting like task')
        # 获取未点赞的帖子列表
        post_uuids = self.get_post_list(exclude_liked=True)

        if not post_uuids:
            logger.warning('No unliked posts available')
            return

        selected = random.sample(post_uuids, min(5, len(post_uuids)))
        logger.info(f'Randomly selected {len(selected)} posts to like')

        for post_uuid in selected:
            self.like_post(post_uuid)
            time.sleep(random.uniform(1.5, 3.5))

    def open_post(self, post_uuid: str) -> bool:
        """打开单个帖子"""
        try:
            url = 'https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetPost'
            result = self._request_with_retry('POST', url, json={'post_uuid': post_uuid})

            if result.get('code') == 0:
                logger.info(f'Opened post successfully: {post_uuid}')
                return True
            logger.error(f'Failed to open post: {result.get("msg", "Unknown error")}')
            return False
        except Exception as e:
            logger.error(f'Exception when opening post: {str(e)}')
            return False

    def open_random_posts(self):
        """随机打开3个帖子"""
        logger.info('Starting browse posts task')
        # 获取所有帖子（不过滤点赞状态）
        post_uuids = self.get_post_list()

        if not post_uuids:
            logger.warning('No posts available to browse')
            return

        selected = random.sample(post_uuids, min(3, len(post_uuids)))
        logger.info(f'Randomly selected {len(selected)} posts to browse')

        for post_uuid in selected:
            self.open_post(post_uuid)
            time.sleep(random.uniform(2.0, 5.0))

    def _get_random_emoji(self) -> str:
        """获取随机表情URL"""
        try:
            response = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetAllEmoticons',
            )

            if response.get('code') == 0:
                emojis = []
                for group in response.get('data', {}).get('list', []):
                    emojis.extend([icon['pic_url'] for icon in group.get('icon_list', [])])
                if emojis:
                    return random.choice(emojis)
            return ''
        except Exception as e:
            logger.error(f'Exception when getting emoji list: {str(e)}')
            return ''

    def post_comment(self):
        """发布评论"""
        logger.info('Starting comment task')
        post_uuid = self.config.BlaDaily_PostID
        comment_uuid = self.config.BlaDaily_CommentID

        if not post_uuid:
            logger.warning('PostID is required')
            return

        request_body = {
            'pic_urls': [],
            'post_uuid': f'{post_uuid}',
            'type': 1,  # 评论帖子
            'users': [],
        }

        if comment_uuid:
            request_body['comment_uuid'] = f'{comment_uuid}'
            request_body['type'] = 2  # 回复评论
            logger.info(f'Replying to comment {comment_uuid} in post {post_uuid}')
        else:
            logger.info(f'Commenting on post {post_uuid}')

        emoji_url = self._get_random_emoji()
        if not emoji_url:
            logger.warning('No available emoji found')
            return
        request_body['content'] = f'<p><img src="{emoji_url}?imgtype=emoji" width="60" height="60"></p>'

        try:
            # _ = self._request_with_retry(
            #     'OPTIONS',
            #     'https://api.blablalink.com/api/ugc/proxy/standalonesite/Dynamics/PostComment'
            # )
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/ugc/proxy/standalonesite/Dynamics/PostComment',
                json=request_body,
            )

            if result.get('code') == 0:
                if comment_uuid:
                    logger.info(f'Reply successful (PID: {post_uuid})')
                else:
                    logger.info(f'Comment successful (PID: {post_uuid})')
            else:
                logger.error(f'Comment failed: {result.get("msg", "Unknown error")}')
        except Exception as e:
            logger.error(f'Exception when posting comment: {str(e)}')

    def check_login(self) -> bool:
        """检查登录状态"""
        try:
            url = 'https://api.blablalink.com/api/user/CheckLogin'
            response = self._request_with_retry('GET', url)

            code = response.get('code', -1)
            msg = response.get('msg', '')

            if code == 0 and msg == 'ok':
                logger.info('Login status: valid')
                return True
            else:
                logger.error(f'Login check failed: code={code}, msg={msg}')
                return False
        except Exception as e:
            logger.error(f'Login check exception: {str(e)}')
            return False

    def parse_task_status(self, tasks_data: Dict) -> Dict:
        """解析任务状态
        :return: 包含任务状态和必要ID的字典
        """
        status = {
            'signin_completed': True,
            'like_completed': True,
            'browse_completed': True,
            'comment_completed': True,
            'signin_task_id': '',
        }

        try:
            tasks = tasks_data.get('data', {}).get('tasks', [])
            for task in tasks:
                task_name = task.get('task_name', '')
                reward = next(iter(task.get('reward_infos', [])), None)
                is_completed = reward.get('is_completed', False) if reward else False

                if '每日簽到' in task_name:
                    status['signin_completed'] = is_completed
                    status['signin_task_id'] = task.get('task_id', '')
                    logger.info(f'Signin task: {"completed" if is_completed else "pending"}')

                elif '按讚' in task_name:
                    status['like_completed'] = is_completed
                    logger.info(f'Like task: {"completed" if is_completed else "pending"}')

                elif '瀏覽' in task_name:
                    status['browse_completed'] = is_completed
                    logger.info(f'Browse task: {"completed" if is_completed else "pending"}')

                elif '評論' in task_name:
                    status['comment_completed'] = is_completed
                    logger.info(f'Comment task: {"completed" if is_completed else "pending"}')

        except Exception as e:
            logger.error(f'Failed to parse task status: {str(e)}')

        return status

    def daily(self):
        logger.info('Starting blablalink daily tasks')
        # 检查Cookie
        if not self.check_login():
            raise RequestHumanTakeover

        # 获取任务列表
        tasks_data = self.get_tasks()
        if not tasks_data:
            logger.error('Failed to get task list')
            return

        # 解析任务状态
        task_status = self.parse_task_status(tasks_data)

        # 执行未完成的任务
        if not task_status.get('signin_completed', True):
            self.execute_signin(task_status.get('signin_task_id'))

        if not task_status.get('like_completed', True):
            self.like_random_posts()

        if not task_status.get('browse_completed', True):
            self.open_random_posts()

        if not task_status.get('comment_completed', True):
            self.post_comment()

        # 获取金币数量
        points = self.get_points()
        self.config.BlaDaily_Points = points
        logger.info(f'Current points: {points}')

    def cdk(self):
        """CDK兑换功能"""
        sources = self.config.CDK_Source
        if not sources:
            logger.info('No CDK sources configured')
            return

        # 1. 解析所有来源网址
        all_cdks = []
        for url in sources.splitlines():
            url = url.strip()
            if not url:
                continue

            try:
                logger.info(f'Processing CDK source: {url}')

                # 根据域名决定提取方式
                if 'gamewith.jp' in url:
                    cdks = self.extract_from_gamewith(url)
                # 添加其他网站的处理方式
                # elif 'otherdomain.com' in url:
                #     cdks = self.extract_from_other(url)
                else:
                    logger.warning(f'Unsupported CDK source: {url}')
                    cdks = []

                if cdks:
                    logger.info(f'Found {len(cdks)} CDK candidates: {", ".join(cdks)}')
                    all_cdks.extend(cdks)
            except Exception as e:
                logger.error(f'Failed to process CDK source {url}: {str(e)}')

        if not all_cdks:
            logger.warning('No CDK candidates found in any sources')
            return

        # 2. 获取兑换历史记录
        redeemed_cdks = self.get_cdk_redemption_history()
        unredeemed_cdks = [cdk for cdk in all_cdks if cdk not in redeemed_cdks]

        if not unredeemed_cdks:
            logger.info('All CDK candidates have already been redeemed')
            return

        logger.info(f'Found {len(unredeemed_cdks)} unredeemed CDK candidates')

        # 3. 尝试兑换未使用的CDK
        success_count = 0
        for cdk in unredeemed_cdks:
            if self.redeem_cdk(cdk):
                success_count += 1
            time.sleep(random.uniform(1.0, 3.0))  # 随机延迟防止请求过快

        logger.info(f'CDK redemption completed: {success_count}/{len(unredeemed_cdks)} successful')

    def get_cdk_redemption_history(self) -> list:
        """获取CDK兑换历史记录"""
        try:
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/game/proxy/Game/GetCdkRedemptionHistory',
                json={'page_num': 1, 'page_size': 20},
            )

            if result.get('code') != 0:
                logger.error(f'Failed to get CDK history: {result.get("msg", "Unknown error")}')
                return []

            history = result.get('data', {}).get('cdk_redemption_list', [])
            redeemed_cdks = [item['cdk'] for item in history]
            logger.info(f'Retrieved {len(redeemed_cdks)} redeemed CDKs from history')
            return redeemed_cdks
        except Exception as e:
            logger.error(f'Exception when getting CDK history: {str(e)}')
            return []

    def redeem_cdk(self, cdk: str) -> bool:
        """兑换单个CDK"""
        try:
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/game/proxy/Game/RecordCdkRedemption',
                json={'cdkey': cdk},
            )

            if result.get('code') == 0 and result.get('msg') == 'ok':
                logger.info(f'Successfully redeemed CDK: {cdk}')
                return True

            # 处理不同的错误情况
            msg = result.get('msg', 'CDK Exchange err')
            logger.error(f'Failed to redeem CDK {cdk}: {msg}')
            return False
        except Exception as e:
            logger.error(f'Exception when redeeming CDK {cdk}: {str(e)}')
            return False

    def extract_from_gamewith(self, url: str) -> list:
        """从gamewith.jp提取CDK"""
        try:
            logger.info(f'Fetching CDKs from gamewith.jp: {url}')
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # 使用正则表达式提取CDK
            pattern = r'<div class="w-clipboard-copy-ui">([^<]+)</div>'
            matches = re.findall(pattern, response.text)
            # 过滤空值并去空格
            cdks = [cdk.strip() for cdk in matches if cdk.strip()]

            # 返回找到的所有值（最多10个）
            return cdks[:10]
        except Exception as e:
            logger.error(f'Failed to extract CDKs from gamewith.jp {url}: {str(e)}')
            return []

    # 其他网站的提取方法
    # def extract_from_other(self, url: str) -> list:
    #     """从其他网站提取CDK"""
    #     # 实现其他网站的CDK提取逻辑
    #     return []

    def run(self):
        try:
            local_now = datetime.now()
            target_time = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
            if local_now > target_time or self.config.BlaDaily_Immediately:
                if self.config.BlaDaily_Enable:
                    self.daily()
                if self.config.CDK_Enable:
                    self.cdk()
                self.config.task_delay(server_update=True)
            else:
                random_minutes = random.randint(5, 30)
                target_time = target_time + timedelta(minutes=random_minutes)
                self.config.task_delay(target=target_time)
        except MissingHeader:
            logger.error('Please check all parameters settings')
            raise RequestHumanTakeover
        except Exception as e:
            logger.error(f'Blablalink exception: {str(e)}')
            raise RequestHumanTakeover
