"""自动主线剧情与收集品任务的公共准备流程。"""

import time
from functools import cached_property

import cv2

from dev_tools.minimap_reconstruct import DriverWindow
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
# 小地图被关闭时左上角水滴箭头控件的客户区点击坐标；与采集器 args.map_open 同源（紧凑展开按钮）。
MINIMAP_TOGGLE_POSITION = (42, 98)
# 小地图重新打开后的面板动画等待秒数；采集器 wait_for 也按 1.2s 预留动画时间。
MINIMAP_REOPEN_WAIT_SECONDS = 1.5


class Campaign(ModuleBase):
    """自动主线任务的公共基类：校验分辨率、准备地图资源并确认当前章节。

    章节、资源检查时间等状态都挂在类上而不是实例上，同一进程内多次建任务实例时共用，
    避免每个任务都重新查一遍资源仓库；地图静态层与目标选择器按章节缓存，换章才重建。
    """

    RESOLUTION = CLIENT_SIZE
    current_chapter = None
    current_difficulty = None
    resources_checked_at = 0.0
    chart = None
    target_finder = None
    target_finder_chapter = None

    @cached_property
    def chapter_ocr(self):
        """章节号与难度共用的 OCR 实例。

        识别对象只有数字和 NORMAL/HARD，候选词表留空走直接识别；模型类型跟随优化设置，
        保证与其它任务使用同一套 OCR 模型，不额外占用显存。
        """
        return Ocr([], name='CampaignIdentity', lang='ch', model_type=self.config.Optimization_OcrModelType)

    @cached_property
    def observer(self):
        """章节快照观测器，整个任务生命周期内只建一个。

        观测器内部会缓存章节底图与配准器，复用同一实例才能命中缓存；每次新建都要重新加载地图。
        """
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

    def reopen_minimap_if_closed(self, image=None):
        """小地图被关闭（左上角只剩水滴形箭头）时点击箭头重新打开，恢复后返回 True。

        image 为可选的 1776×999 BGR 客户区截图；缺省时用当前 device.image 转 BGR 判定。
        关闭判据与采集器 reset_minimap 的 closed 态一致：控件外框可见、控件区域偏暗且紧凑面板
        不可见。确认关闭后点击左上角箭头坐标（与采集器 map_open 同源），等待面板动画后复核；
        重开失败或状态仍不确定只告警并返回 False，由调用方决定是否放弃本帧观测。
        """
        if image is None:
            image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
        if not self.observer.minimap_closed(image):
            return False
        logger.warning('检测到小地图已被关闭，点击左上角箭头重新打开。')
        self.device.click_xy(*MINIMAP_TOGGLE_POSITION)
        self.device.sleep(MINIMAP_REOPEN_WAIT_SECONDS)
        self.device.screenshot()
        image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
        if self.observer.minimap_closed(image):
            logger.warning('小地图重开失败，左上角仍为关闭态。')
            return False
        return True

    def detect_current_chapter(self):
        """连续识别主线地图右下角的章节号与难度，确认后发布一帧章节快照。

        OCR 单帧结果会受动画和特效影响，要求连续三帧得到相同身份才确认；任一帧识别失败会把
        连续计数清零，30 秒内无法确认则判定为人工介入，通常是章节号被遮挡或不在主线地图。
        确认的同时做一次观测并推送预览，让界面在任务刚开始时就能看到定位结果。
        """
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
            # 小地图被关闭时先点箭头重开再识别，否则快照会一直缺小地图导致推图无法继续。
            if self.reopen_minimap_if_closed(image):
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
        """主线任务入口：准备地图资源、校验分辨率并确认章节。

        推图本体尚未接入，当前只返回确认到的章节号；分辨率检查必须在资源检查之后、识别之前，
        章节号和难度标识的坐标按 1776×999 客户区固定，分辨率不符时识别区域会整体偏移。
        """
        logger.hr(self.config.task.command, level=1)
        self.ensure_resources()
        self.device.check_resolution(*self.RESOLUTION)
        logger.info('主线任务分辨率：1776×999')
        chapter = self.detect_current_chapter()
        # TODO: 根据任务类型执行推图，并在移动决策与战斗前后发布章节状态。
        return chapter
