"""自动主线剧情与收集品任务的公共准备流程。"""

import time
from functools import cached_property

import cv2

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.base.utils import publish_preview_frame
from module.campaign.chapter import CLIENT_SIZE, chapter_identity
from module.campaign.map_state import StaticMap
from module.campaign.maps import chapter_map
from module.campaign.navigation import TargetFinder
from module.campaign.observation import CampaignObserver
from module.config.config import TaskEnd
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Ocr
from module.webui.resource import resource_manager

# 同一进程内每隔若干小时才重新访问远端，避免每次任务都查一遍资源仓库。
RESOURCE_RECHECK_SECONDS = 6 * 3600
RESOURCE_SYNC_TIMEOUT = 1800


class Campaign(ModuleBase):
    RESOLUTION = CLIENT_SIZE
    current_chapter = None
    current_difficulty = None
    resources_checked_at = 0.0
    chart = None
    target_finder = None
    target_finder_chapter = None

    @cached_property
    def chapter_ocr(self):
        return Ocr([], name='CampaignIdentity', lang='ch', model_type=self.config.Optimization_OcrModelType)

    @cached_property
    def observer(self):
        return CampaignObserver()

    def ensure_resources(self):
        """运行前确认章节地图资源存在且为最新；缺失或过期时在任务内同步，本地没有资源又同步不了则交给人工。"""
        if time.time() - Campaign.resources_checked_at < RESOURCE_RECHECK_SECONDS:
            return
        logger.hr('检查地图资源', level=2)
        status = resource_manager.status(check=True)
        condition, local = status['condition'], status['local']
        if condition == 'latest':
            logger.info(f"地图资源已是最新：版本 {local.get('version')}")
        elif condition in ('missing', 'outdated'):
            logger.info('本地没有地图资源，开始同步…' if condition == 'missing' else '地图资源有新版本，开始同步…')
            self.wait_resource_sync()
        elif local['exists']:
            logger.warning(f"无法检查地图资源更新，继续使用本地版本：{status['remote'].get('error')}")
        else:
            logger.error(f"无法访问资源仓库，且本地没有地图资源：{status['remote'].get('error')}")
            raise RequestHumanTakeover
        Campaign.resources_checked_at = time.time()

    def wait_resource_sync(self):
        """阻塞等待后台同步线程结束，把进度转发到任务日志；停止请求和超时都会放弃等待。"""
        if not resource_manager.start_sync():
            logger.info('资源同步已在进行，等待完成…')
        timeout = Timer(RESOURCE_SYNC_TIMEOUT).start()
        last = None
        while resource_manager.state == 'syncing':
            if self.config.stop_event is not None and self.config.stop_event.is_set():
                raise TaskEnd
            line = resource_manager.log[-1] if resource_manager.log else None
            if line and line != last:
                logger.info(line)
                last = line
            if timeout.reached():
                logger.error('地图资源同步超时。')
                break
            time.sleep(1)
        status = resource_manager.status(check=False)
        line = status['log'][-1] if status['log'] else None
        if line and line != last:
            logger.info(line)
        if status['state'] == 'finish':
            logger.info(f"地图资源同步完成：版本 {status['local'].get('version')}")
            return
        if status['local']['exists']:
            logger.warning(f"地图资源同步未完成，继续使用本地版本：{status['error'] or '超时'}")
            return
        logger.error(f"地图资源同步失败，且本地没有地图资源：{status['error'] or '超时'}")
        raise RequestHumanTakeover

    def detect_current_chapter(self):
        self.current_chapter = self.current_difficulty = None
        reader = self.chapter_ocr
        timeout = Timer(30).start()
        previous, stable = None, 0
        while 1:
            if self.config.stop_event is not None and self.config.stop_event.is_set():
                raise TaskEnd
            self.device.screenshot()
            captured_at = time.time()
            # 设备截图统一为 RGB，OCR 与地图检测的 ndarray 输入使用 BGR。
            image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
            identity = chapter_identity(image, reader)
            stable = stable + 1 if identity is not None and identity == previous else int(identity is not None)
            previous = identity
            if stable >= 3:
                self.current_chapter, self.current_difficulty = identity
                logger.info(f'当前主线章节：{identity[1].upper()} 第 {identity[0]} 章')
                snapshot = self.observer.observe(image, identity, captured_at)
                publish_preview_frame(self.device.image, interval=0, source={'campaign': snapshot})
                return self.current_chapter
            if timeout.reached():
                logger.error('无法确认主线章节及难度，请确保主线地图右下角章节号和难度标识可见。')
                raise RequestHumanTakeover
            self.device.sleep(0.5)

    def find_next_target(self, snapshot):
        """选择下一个目标：可连通道路上最近的普通敌人，没有敌人时是附近的地面机关或电梯。

        snapshot 来自 observer.observe，要求定位已被接受；模型不支持或定位不可用时返回 None。
        """
        chapter = snapshot.get('chapter')
        if (chapter is None or snapshot.get('localization') != 'accepted' or snapshot.get('squad') is None
                or self.observer.matcher is None or chapter != self.observer.chapter):
            return None
        if self.target_finder_chapter != chapter:
            self.target_finder_chapter, self.chart, self.target_finder = chapter, None, None
            try:
                package, _ = chapter_map(chapter)
                # 静态层按章节只建一次，目标选择与后续的道路、敌人判断共用同一份。
                self.chart = StaticMap(package, self.observer.matcher)
                self.target_finder = TargetFinder(self.chart)
            except (OSError, ValueError, KeyError) as exc:
                logger.warning(f'第 {chapter} 章地图无法用于目标规划：{exc}')
        if self.target_finder is None:
            return None
        return self.target_finder.find(snapshot)

    def run(self):
        logger.hr(self.config.task.command, level=1)
        self.ensure_resources()
        self.device.check_resolution(*self.RESOLUTION)
        logger.info('主线任务分辨率：1776×999')
        chapter = self.detect_current_chapter()
        # TODO: 根据任务类型执行推图，并在移动决策与战斗前后发布章节状态。
        return chapter
