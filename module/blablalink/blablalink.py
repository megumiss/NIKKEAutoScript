import json
import random
import re
import time
from datetime import datetime, timedelta, timezone
from functools import cached_property
from pathlib import Path
from typing import Dict, Tuple

import requests

from module.config.utils import deep_get
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ui.ui import UI


class MissingHeader(Exception):
    pass


class Blablalink(UI):
    diff = datetime.now(timezone.utc).astimezone().utcoffset() - timedelta(hours=8)

    @cached_property
    def next_month(self) -> datetime:
        local_now = datetime.now()
        next_month = local_now.month % 12 + 1
        next_year = local_now.year + 1 if next_month == 1 else local_now.year
        return (
            local_now.replace(
                year=next_year,
                month=next_month,
                day=1,
                hour=4,
                minute=0,
                second=0,
                microsecond=0,
            )
            + self.diff
        )

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
        self._cdk_temp_path = Path('./tmp/cdk_history.json')  # 临时文件路径
        self._prepare_config()

    def _prepare_config(self):
        """从配置中准备所有必要参数"""
        # 获取Cookie
        cookie = deep_get(self.config.data, keys='BlaAuth.BlaAuth.Cookie')
        if not cookie:
            logger.error('Cookie not configured')
            raise RequestHumanTakeover('Cookie not set')
        self.common_headers['cookie'] = cookie
        # 获取x-common-params
        xCommonParams = deep_get(self.config.data, keys='BlaAuth.BlaAuth.XCommonParams')
        if not xCommonParams:
            logger.error('x-common-params not configured')
            raise RequestHumanTakeover('x-common-params not set')

        # 构建x-common-params
        # common_params = {
        #     'game_id': '16',
        #     'area_id': 'global',
        #     'source': 'pc_web',
        #     'intl_game_id': '29080',
        #     'language': 'zh-TW',
        #     'env': 'prod',
        #     'data_statistics_scene': 'outer',
        #     'data_statistics_page_id': f'https://www.blablalink.com/user?openid={openid}',
        #     'data_statistics_client_type': 'pc_web',
        #     'data_statistics_lang': 'zh-TW',
        # }
        # self.common_headers['x-common-params'] = json.dumps(common_params, ensure_ascii=False)
        self.common_headers['x-common-params'] = self.config.BlaAuth_XCommonParams

        # 获取user-agent
        useragent = deep_get(self.config.data, keys='BlaAuth.BlaAuth.UserAgent')
        if not useragent:
            logger.warning('User-agent configured')
            raise RequestHumanTakeover('User-agent not set')
        self.common_headers['user-agent'] = useragent

        logger.info('Headers build successfully')

    @cached_property
    def exchange_priority(self) -> list:
        priority = re.sub(r'\s+', '', self.config.BlaExchange_Priority).split('>')
        return priority

    EXCHANGES = {
        'Gem_×320': '珠寶 ×320',
        'Welcome_Gift_Core_Dust_×30': '指揮官見面禮：芯塵 ×30',
        'Gem_×30': '珠寶 ×30',
        'Skill_Manual_I_×5': '技能手冊 I ×5',
        'Ultra_Boost_Module_×5': '模組高級推進器 ×5',
        'Code_Manual_Selection_Box_×5': '代碼手冊選擇寶箱 ×5',
        'Gem_×60': '珠寶 ×60',
        'Mid-Quality_Mold_×3': '中品質鑄模 ×3',
        'Credit_Case_(1H)_x9': '信用點盒(1H) x9',
        'Core_Dust_Case_(1H)_×3': '芯塵盒 (1H) ×3',
        'Gem_×120': '珠寶 ×120',
        'Mid-Quality_Mold_×8': '中品質鑄模 ×8',
        'Battle_Data_Set_Case_(1H)_×6': '戰鬥數據輯盒 (1H) ×6',
        'Core_Dust_Case_(1H)_×6': '芯塵盒 (1H) ×6',
        'Skill_Manual_I_×30': '技能手冊 I ×30',
        'Ultra_Boost_Module_×30': '模組高級推進器 ×30',
        'Code_Manual_Selection_Box_×30': '代碼手冊選擇寶箱 ×30',
    }

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
        logger.info('Starting CDK redemption task')

        # 1. 获取兑换历史记录并追加到临时文件
        redeemed_cdks = self.get_cdk_redemption_history()
        self._append_cdks_to_temp(redeemed_cdks)

        # 2. 从官方接口获取未兑换的CDK列表
        official_cdks = self.get_official_cdks()
        unredeemed_cdks = official_cdks.copy()

        # 3. 如果开启额外来源，添加来源网站中的CDK
        if self.config.CDK_Extra:
            sources = self.config.CDK_Source
            if sources:
                # 从临时文件加载所有已记录CDK
                temp_cdks = self._load_cdks_from_temp()

                # 提取外部CDK并过滤已记录的
                extra_cdks = self.extract_external_cdks(sources)
                for cdk in extra_cdks:
                    if cdk not in temp_cdks and cdk not in unredeemed_cdks:
                        unredeemed_cdks.append(cdk)
            else:
                logger.warning('CDK_Extra enabled but no sources configured')
        else:
            logger.info('CDK_Extra disabled, only using official CDKs')

        if not unredeemed_cdks:
            logger.info('All CDK candidates have already been redeemed')
            return

        logger.info(f'Found {len(unredeemed_cdks)} unredeemed CDK candidates')

        # 4. 尝试兑换未使用的CDK
        success_count = 0
        for cdk in unredeemed_cdks:
            if self.redeem_cdk(cdk):
                success_count += 1
                # 如果兑换成功，将CDK追加到临时文件
                # self._append_cdks_to_temp([cdk])
            time.sleep(random.uniform(1.0, 3.0))

        logger.info(f'CDK redemption completed: {success_count}/{len(unredeemed_cdks)} successful')

    def _append_cdks_to_temp(self, cdks: list):
        """将CDK列表追加到临时文件"""
        try:
            # 确保父目录存在
            self._cdk_temp_path.parent.mkdir(parents=True, exist_ok=True)

            # 加载现有CDK
            existing_cdks = self._load_cdks_from_temp()

            # 过滤新CDK，只添加不在现有列表中的
            new_cdks = [cdk for cdk in cdks if cdk not in existing_cdks]

            if not new_cdks:
                return

            # 合并并保存
            all_cdks = existing_cdks + new_cdks
            with open(self._cdk_temp_path, 'w', encoding='utf-8') as f:
                json.dump(all_cdks, f, ensure_ascii=False, indent=2)

            logger.info(f'Appended {len(new_cdks)} new CDKs to temp file')
        except Exception as e:
            logger.error(f'Failed to append CDKs to temp file: {e}')

    def _load_cdks_from_temp(self) -> list:
        """从临时文件加载CDK列表"""
        # 如果文件不存在则创建空文件
        try:
            if not self._cdk_temp_path.exists():
                self._cdk_temp_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._cdk_temp_path, 'w', encoding='utf-8') as f:
                    json.dump([], f)
                return []
            with open(self._cdk_temp_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f'Failed to load CDKs from temp file: {e}')
            return []

    def get_official_cdks(self) -> list:
        """从官方接口获取未兑换的CDK列表"""
        try:
            result = self._request_with_retry(
                'POST', 'https://api.blablalink.com/api/game/proxy/Game/GetCdkRedemption', json={}
            )

            if result.get('code') != 0:
                logger.error(f'Failed to get official CDKs: {result.get("msg", "Unknown error")}')
                return []

            cdks = []
            for item in result.get('data', {}).get('cdk_redemption_list', []):
                # 只添加未兑换的CDK (status=0)
                if item.get('status') == 0:
                    cdks.append(item['cdk'])

            logger.info(f'Retrieved {len(cdks)} official CDKs (all unredeemed)')
            return cdks
        except Exception as e:
            logger.error(f'Exception when getting official CDKs: {str(e)}')
            return []

    def extract_external_cdks(self, sources: str) -> list:
        """从外部来源提取CDK"""
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

        return all_cdks

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

    def exchange(self):
        """金币兑换功能"""
        logger.info('Starting point exchange task')

        # 3. 根据优先级筛选需要兑换的奖励
        priority_list = self.exchange_priority
        if not priority_list:
            logger.warning('No exchange priority configured')
            return

        # 1. 获取用户信息
        role_info = self.get_role_info()
        if not role_info:
            logger.error('Failed to get role info, cannot proceed with exchange')
            return

        # 2. 分页获取奖励列表
        all_commodities = self.get_all_commodities()
        if not all_commodities:
            logger.warning('No commodities available for exchange')
            return

        # 获取当前金币数量
        current_points = deep_get(self.config.data, keys='BlaDaily.BlaDaily.Points')
        logger.info(f'Current points: {current_points}')

        # 4. 按优先级尝试兑换
        for priority in priority_list:
            logger.info(f'Prepare exchange {priority}')
            # 查找匹配的商品
            target_commodity = None
            for commodity in all_commodities:
                if self.EXCHANGES[priority] in commodity['commodity_name']:
                    target_commodity = commodity
                    break

            if not target_commodity:
                logger.warning(f'Commodity not found for priority: {priority}')
                continue

            # 检查兑换限制
            limit_num = target_commodity['account_exchange_limit']['limit_num']
            has_exchange_num = target_commodity['has_exchange_num']

            if has_exchange_num >= limit_num:
                logger.info(f'Commodity {priority} already reached exchange limit ({has_exchange_num}/{limit_num})')
                continue

            # 检查金币是否足够
            commodity_price = target_commodity['commodity_price']
            if current_points < commodity_price:
                logger.info(f'Not enough points for {priority} (need {commodity_price}, have {current_points})')
                continue

            # 检查商品是否可以兑换
            exchange_id = target_commodity['exchange_commodity_id']
            if not self.check_can_exchange(exchange_id):
                logger.warning(f'Cannot exchange commodity: {priority}')
                continue

            # 执行兑换
            if self.perform_exchange(exchange_id, commodity_price, role_info):
                logger.info(f'Successfully exchanged {priority}')
                current_points -= commodity_price
            else:
                logger.warning(f'Failed to exchange {priority}')

            # 每次兑换后暂停一会儿
            time.sleep(random.uniform(1.0, 3.0))

        # 更新金币数量
        self.config.modified['BlaDaily.BlaDaily.Points'] = current_points
        self.config.update()

    def get_role_info(self) -> Dict:
        """获取用户角色信息"""
        try:
            result = self._request_with_retry(
                'POST', 'https://api.blablalink.com/api/game/proxy/Game/GetSavedRoleInfo', json={}
            )

            if result.get('code') == 0 and result.get('data', {}).get('has_saved_role_info'):
                role_info = result['data']['role_info']
                logger.info(f'Got role info: {role_info["role_name"]}')
                return role_info
            else:
                logger.error(f'Failed to get role info: {result.get("msg", "Unknown error")}')
                return {}
        except Exception as e:
            logger.error(f'Exception when getting role info: {str(e)}')
            return {}

    def get_all_commodities(self) -> list:
        """分页获取所有商品列表"""
        commodities = []
        page_num = 1
        page_size = 10

        while True:
            try:
                result = self._request_with_retry(
                    'POST',
                    'https://api.blablalink.com/api/lip/proxy/commodity/Commodity/GetUserCommodityList',
                    json={'page_num': page_num, 'page_size': page_size, 'game_id_list': ['29080'], 'is_bind_lip': True},
                )

                if result.get('code') != 0:
                    logger.error(f'Failed to get commodities page {page_num}: {result.get("msg", "Unknown error")}')
                    break

                # 添加当前页的商品
                page_commodities = result.get('data', {}).get('commodity_list', [])
                commodities.extend(page_commodities)

                # 计算总页数
                total_num = result.get('data', {}).get('total_num', 0)
                total_pages = (total_num + page_size - 1) // page_size

                logger.info(f'Got page {page_num}/{total_pages} with {len(page_commodities)} commodities')

                # 检查是否还有下一页
                if page_num >= total_pages:
                    break

                page_num += 1
                time.sleep(random.uniform(1.0, 2.0))  # 页面间延迟

            except Exception as e:
                logger.error(f'Exception when getting commodities: {str(e)}')
                break

        logger.info(f'Total commodities: {len(commodities)}')
        return commodities

    def check_can_exchange(self, exchange_id: str) -> bool:
        """检查商品是否可以兑换"""
        try:
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/lip/proxy/commodity/Commodity/CheckUserCanExchange',
                json={'exchange_commodity_id': exchange_id},
            )

            if result.get('code') == 0:
                can_exchange = result.get('data', {}).get('can', False)
                logger.info(f'Exchange check for {exchange_id}: {"can" if can_exchange else "cannot"}')
                return can_exchange
            else:
                logger.warning(f'Exchange check failed: {result.get("msg", "Unknown error")}')
                return False
        except Exception as e:
            logger.error(f'Exception when checking exchange: {str(e)}')
            return False

    def perform_exchange(self, exchange_id: str, price: int, role_info: Dict) -> bool:
        """执行兑换操作"""
        try:
            result = self._request_with_retry(
                'POST',
                'https://api.blablalink.com/api/lip/proxy/commodity/Commodity/ExchangeCommodity',
                json={
                    'exchange_commodity_id': exchange_id,
                    'exchange_commodity_price': price,
                    'role_info': role_info,
                    'save_role': False,
                },
            )

            if result.get('code') == 0:
                logger.info(f'Exchange successful for {exchange_id}')
                return True
            else:
                logger.warning(f'Exchange failed: {result.get("msg", "Unknown error")}')
                return False
        except Exception as e:
            logger.error(f'Exception when performing exchange: {str(e)}')
            return False

    def run(self, task):
        try:
            local_now = datetime.now()
            target_time = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
            if local_now > target_time:
                if task == 'daily':
                    self.daily()
                    self.config.task_delay(server_update=True)
                if task == 'cdk':
                    self.cdk()
                    self.config.task_delay(server_update=True)
                if task == 'exchange':
                    self.exchange()
                    self.config.task_delay(target=self.next_month)
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
