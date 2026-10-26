"""自动主线剧情与收集品任务的公共准备流程。"""

import time
from functools import cached_property

import cv2

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.base.utils import publish_preview_frame
from module.campaign.vision.chapter import CLIENT_SIZE, chapter_identity
from module.campaign.goals import choose_goal, describe
from module.campaign.map.map_state import StaticMap
from module.campaign.map.catalog import chapter_map
from module.campaign.vision.minimap import map_visible
from module.campaign.navigation.targets import TargetFinder
from module.campaign.vision.observation import EXPANDED_ROI, CampaignObserver
from module.campaign.navigation.routing import CutRoads
from module.campaign.report import (GOAL_REASONS, LOCALIZATION, MINIMAP_MODES, direction_text, label, points_text)
from module.campaign.run_state import RunState, describe_changes
from module.campaign.timing import Stopwatch
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
# 展开面板左上角的最小化按钮；与 navigation.controls.MAP_MINIMIZE 同源。
MINIMAP_COLLAPSE_POSITION = (EXPANDED_ROI[0] + 14, EXPANDED_ROI[1] - 11)
# 小地图重新打开后的面板动画等待秒数；采集器 wait_for 也按 1.2s 预留动画时间。
MINIMAP_REOPEN_WAIT_SECONDS = 1.5
# 展开面板内拖动后等待地图回正／停稳的秒数。
MINIMAP_PAN_SETTLE_SECONDS = 1.0
# 一次平移的客户区像素位移，以及各次重试的方向循环（右、下、左）。
# 位移不可过大：小队圆环会随视图平移移出面板，cropped_minimap 就找不到小队了；
# 方向轮流用是为了让视图始终围着小队附近转，而不是一路漂到章节边缘。
MINIMAP_PAN_PIXELS = 140
MINIMAP_PAN_DIRECTIONS = ((1, 0), (0, 1), (-1, 0))
# 小队定位的总尝试次数：第 1 次紧凑小地图，第 2 次展开大地图，之后在展开面板内平移后重判。
SQUAD_LOCATE_ATTEMPTS = 4
# 主线剧情界面检查的帧数与帧间隔：单帧 OCR 会被过场动画、技能特效或弹窗干扰，
# 连续这么多帧都读不到右下角章节角标才判定当前不在主线地图。
MAIN_STORY_PAGE_FRAMES = 3
MAIN_STORY_PAGE_INTERVAL = 0.5
# 决策空跑的拍数与拍间隔：空跑不发送输入，场面不会变，多拍只为验证跨帧状态确实在累积
# （同一只敌人要合并、封锁状态要按视野迁移），所以几拍就够。
DRY_RUN_TICKS = 3
DRY_RUN_INTERVAL = 1.0


class Campaign(ModuleBase):
    """自动主线任务的公共基类：校验分辨率、准备地图资源并确认当前章节与小队位置。

    章节、资源检查时间等状态都挂在类上而不是实例上，同一进程内多次建任务实例时共用，
    避免每个任务都重新查一遍资源仓库；地图静态层与目标选择器按章节缓存，换章才重建。
    last_snapshot 保存章节确认时那一帧的观测结果，定位已经确认过时不必再配准一次。
    run_state 是跨帧累积的运行状态，按每次任务重建：上一次任务见过的敌人与封锁不能直接拿来用。
    """

    RESOLUTION = CLIENT_SIZE
    current_chapter = None
    current_difficulty = None
    resources_checked_at = 0.0
    chart = None
    target_finder = None
    target_finder_chapter = None
    last_snapshot = None
    last_goal = None
    last_routes = None
    last_route_signature = None
    state = None
    cut = None

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

    def reopen_minimap_if_closed(self, image=None, check=None, pause=None):
        """小地图被关闭（左上角只剩水滴形箭头）时点击箭头重新打开，恢复后返回 True。

        image 为可选的 1776×999 BGR 客户区截图；缺省时用当前 device.image 转 BGR 判定。
        check / pause 可注入生产会话的输入闸门与可取消等待，默认保持既有准备流程。
        关闭判据与采集器 reset_minimap 的 closed 态一致：控件外框可见、控件区域偏暗且紧凑面板
        不可见。确认关闭后点击左上角箭头坐标（与采集器 map_open 同源），等待面板动画后复核；
        重开失败或状态仍不确定只告警并返回 False，由调用方决定是否放弃本帧观测。
        """
        if image is None:
            image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
        if not self.observer.minimap_closed(image):
            return False
        if check is not None:
            check()
            if not self.observer.minimap_closed(cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)):
                return False
        logger.warning('检测到小地图已被关闭，点击左上角箭头重新打开。')
        self.device.click_xy(*MINIMAP_TOGGLE_POSITION)
        (pause or self.device.sleep)(MINIMAP_REOPEN_WAIT_SECONDS)
        self.device.screenshot()
        image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
        if self.observer.minimap_closed(image):
            logger.warning('小地图重开失败，左上角仍为关闭态。')
            return False
        return True

    def minimap_expanded(self, image=None):
        """当前画面是否处于展开大地图形态：中部 EXPANDED_ROI 是青蓝色的地图面板。

        image 为可选的 1776×999 BGR 客户区截图；缺省时用当前 device.image 转 BGR 判定。
        判据与观察器 minimap_mode_and_panel 的展开分支一致，只用于切换后的状态确认。
        """
        if image is None:
            image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
        left, top, right, bottom = EXPANDED_ROI
        return map_visible(image[top:bottom, left:right])

    def expand_minimap(self, check=None, pause=None):
        """点击左上角控件把紧凑小地图展开成大地图面板，持续重试直到面板可见。

        check / pause 可注入生产会话闸门与可取消等待，任务时限、输入预算和窗口异常仍由这些闸门终止重试。
        那个控件是开合开关，再点一次会把已经展开的面板收起，所以每次点击之前都重新确认面板还没展开；
        一次点击没生效（面板动画没走完、或点击被战斗加载画面吃掉）时继续重试，不再单独限制展开点击次数。
        """
        attempt = 0
        while 1:
            if self.minimap_expanded():
                return True
            if check is not None:
                check()
                if self.minimap_expanded():
                    return True
            if attempt:
                logger.warning(f'展开大地图没有生效，再点一次（第 {attempt + 1} 次）。')
            else:
                logger.info('展开大地图补充视野或重新定位。')
            self.device.click_xy(*MINIMAP_TOGGLE_POSITION)
            (pause or self.device.sleep)(MINIMAP_REOPEN_WAIT_SECONDS)
            self.device.screenshot()
            if self.minimap_expanded():
                return True
            attempt += 1

    def collapse_minimap(self, check=None, pause=None):
        """把展开的大地图面板收起回紧凑小地图；失败只告警，不影响已经得到的定位结果。

        check / pause 可注入生产会话闸门与可取消等待。
        收起前先确认面板还在：已经是紧凑态时再点一次最小化按钮，坐标会落到场景上，
        可能被游戏当成一次移动指令。收起后必须确认面板消失，任务结束前若仍停在大地图上，
        后续推图会在地图盖住画面的状态下点击。
        """
        for _ in range(2):
            if not self.minimap_expanded():
                return True
            if check is not None:
                check()
                if not self.minimap_expanded():
                    return True
            self.device.click_xy(*MINIMAP_COLLAPSE_POSITION)
            (pause or self.device.sleep)(MINIMAP_REOPEN_WAIT_SECONDS)
            self.device.screenshot()
        if not self.minimap_expanded():
            return True
        logger.warning('展开大地图收起失败，请检查左上角小地图状态。')
        return False

    def pan_expanded_minimap(self, direction):
        """在展开面板内沿给定方向拖动一次，换一段地图区域后再判配准。

        direction 是客户区方向向量（1,0 表示向右拖），起终点以面板中心对称，位移为
        MINIMAP_PAN_PIXELS。拖动只平移地图视图，小队本身不动：这样原本只露出一小块路的视图
        会换成道路更完整的一段，配准的道路 IoU 与候选分差通常都会变好。拖动后等待视图停稳，
        调用方负责重新截图并观测。
        """
        left, top, right, bottom = EXPANDED_ROI
        center = ((left + right) // 2, (top + bottom) // 2)
        half = MINIMAP_PAN_PIXELS // 2
        dx, dy = direction
        self.device.drag_xy((center[0] - dx * half, center[1] - dy * half),
                            (center[0] + dx * half, center[1] + dy * half))
        self.device.sleep(MINIMAP_PAN_SETTLE_SECONDS)

    def confirm_squad_location(self, initial=None):
        """确认小队底图位置：紧凑小地图 → 展开大地图 → 面板内平移，最多 SQUAD_LOCATE_ATTEMPTS 次。

        第 1 次用紧凑小地图；失败就展开大地图重判（展开面板面积是紧凑面板的数倍，道路证据更多，
        配准分数通常明显更高），仍失败则在面板内换一段区域再判。展开与平移都会改变画面，
        所以退出前必须收起回紧凑形态，否则后续推图是在大地图盖住画面的状态下操作。
        initial 是章节确认时已经观测过的那一帧快照：它已经接受定位时直接采用，避免同一画面配准两次。
        接受时打印小队在章节地图上的坐标与使用的小地图形态；未接受时保留最后一次快照并发布预览，
        让界面能看到失败证据（具体未达标项由观察器的告警给出）。
        """
        logger.hr('确认小队位置', level=2)
        identity = (self.current_chapter, self.current_difficulty)
        if initial is not None and initial['localization'] == 'accepted':
            logger.info(f"小队在章节地图上的位置 {points_text(initial['squad'])}"
                        f"（{label(MINIMAP_MODES, initial['minimap']['mode'])}小地图，章节确认时已定位）")
            return initial
        snapshot, expanded = None, False
        for attempt in range(1, SQUAD_LOCATE_ATTEMPTS + 1):
            if attempt == 2:
                if not self.expand_minimap():
                    break
                expanded = True
            elif attempt > 2:
                direction = MINIMAP_PAN_DIRECTIONS[(attempt - 3) % len(MINIMAP_PAN_DIRECTIONS)]
                logger.info(f'{direction_text(direction)}挪一段视图，换个位置重新定位。')
                self.pan_expanded_minimap(direction)
            self.device.screenshot()
            image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
            snapshot = self.observer.observe(image, identity, time.time())
            if snapshot['localization'] == 'accepted':
                logger.info(f"小队在章节地图上的位置 {points_text(snapshot['squad'])}"
                            f"（{label(MINIMAP_MODES, snapshot['minimap']['mode'])}小地图，"
                            f"第 {attempt}/{SQUAD_LOCATE_ATTEMPTS} 次尝试）")
                break
            logger.warning(f"第 {attempt}/{SQUAD_LOCATE_ATTEMPTS} 次未定位到小队，换一种小地图形态或视图重试。")
        if snapshot is None or snapshot['localization'] != 'accepted':
            status = label(LOCALIZATION, snapshot['localization']) if snapshot is not None else '没有取到画面'
            logger.warning(f'最终未定位到小队（{status}），后续推图没有可用起点。')
        if snapshot is not None:
            publish_preview_frame(self.device.image, interval=0, source=self.campaign_source(snapshot))
        if expanded:
            self.collapse_minimap()
        return snapshot

    def prepare_client(self):
        """任务准备阶段：核对客户区尺寸与遮挡、应用自动 HDR 设置、检查虚拟鼠标通道。

        与设备启动阶段（AppControl.app_start）做同样几件事，但每次进入任务都重做一遍：device
        是长驻对象，分辨率、窗口位置、自动 HDR 和鼠标驱动通道都可能在这期间被外部改动（手动
        拖动窗口、系统 HDR 切换、G HUB 重启），而固定坐标与点击换算全部依赖它们。
        分辨率检查必须在资源检查之后、章节识别之前：章节号与难度的识别区域按 1776×999 客户区
        固定，分辨率不符时识别区域会整体偏移；客户区被任务栏切掉同样会让区域偏移。
        """
        logger.hr('准备客户区', level=2)
        width, height = self.RESOLUTION
        screen_n = self.config.PCClient_ScreenNumber
        self.device.check_resolution(width, height)
        if not self.device.ensure_client_visible(screen_n, self.config.PCClient_GameWindowPosition):
            logger.error('游戏窗口检查未通过：窗口缺失，或游戏画面被任务栏／屏幕边缘切掉，固定坐标会整体偏移。')
            raise RequestHumanTakeover
        self.device.apply_auto_hdr_setting()
        self.device.verify_input_channel()
        logger.info(f'主线任务分辨率：{width}×{height}')

    def detect_current_chapter(self):
        """确认主线剧情界面，并连续识别右下角章节号与难度后发布一帧章节快照。

        章节号与难度只在主线地图显示。首次读到有效身份前连续 MAIN_STORY_PAGE_FRAMES 帧缺失，
        直接按不在主线地图交人工，且不会点击小地图；读到身份后要求连续三帧一致，任一帧失败清零。
        已确认界面但身份持续不稳定时沿用 30 秒超时。小地图只在身份确认后重开，避免在错误页面输入，
        也避免为同一帧重复章节 OCR。必须在 prepare_client 之后调用，识别区域按 1776×999 客户区固定。
        """
        logger.hr('检查主线剧情界面并确认主线章节', level=2)
        self.current_chapter = self.current_difficulty = None
        reader = self.chapter_ocr
        timeout = Timer(30).start()
        previous, stable, missing_logged = None, 0, False
        page_confirmed, missing_frames = False, 0
        while 1:
            if self.config.stop_event is not None and self.config.stop_event.is_set():
                raise TaskEnd
            self.device.screenshot()
            captured_at = time.time()
            # 设备截图统一为 RGB，OCR 与地图检测的 ndarray 输入使用 BGR。
            image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
            identity = chapter_identity(image, reader)
            if identity is None:
                if not page_confirmed:
                    missing_frames += 1
                    logger.warning(f'第 {missing_frames}/{MAIN_STORY_PAGE_FRAMES} 帧未读到右下角章节角标。')
                    if missing_frames >= MAIN_STORY_PAGE_FRAMES:
                        logger.error('当前不在主线剧情界面：右下角读不到章节号与难度标识。请先进入主线地图，'
                                     '并确认右下角章节角标没有被弹窗或特效遮挡，然后重新运行任务。')
                        raise RequestHumanTakeover
                elif not missing_logged:
                    # 一段连续读不到只告警一次：同一个遮挡逐帧刷同一行没有信息量，恢复后会重新告警。
                    logger.warning('本帧未读到右下角章节号与难度，连续一致计数清零后继续识别。')
                missing_logged = True
            else:
                page_confirmed = True
                missing_logged = False
                if identity != previous:
                    logger.info(f'读到 {identity[1].upper()} 第 {identity[0]} 章，等待连续三帧一致。')
            stable = stable + 1 if identity is not None and identity == previous else int(identity is not None)
            previous = identity
            if stable >= 3:
                self.current_chapter, self.current_difficulty = identity
                logger.info(f'当前主线章节：{identity[1].upper()} 第 {identity[0]} 章')
                # 小地图被关闭时先点箭头重开再观测，否则快照会一直缺小地图导致推图无法继续。
                if self.reopen_minimap_if_closed(image):
                    image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
                    captured_at = time.time()
                snapshot = self.observer.observe(image, identity, captured_at)
                self.last_snapshot = snapshot
                publish_preview_frame(self.device.image, interval=0, source=self.campaign_source(snapshot))
                return self.current_chapter
            if timeout.reached():
                last = f'{previous[1].upper()} 第 {previous[0]} 章' if previous is not None else '未读到'
                logger.error(f'无法确认主线章节及难度（最后一帧：{last}，连续一致 {stable}/3 帧），'
                             f'请确保主线地图右下角章节号和难度标识可见。')
                raise RequestHumanTakeover
            self.device.sleep(MAIN_STORY_PAGE_INTERVAL)

    def ensure_chart(self, chapter):
        """按章节建一次静态层与目标选择器，换章才重建；构建失败只告警并返回 None。

        静态层与目标选择器共用同一份道路、分区和标注，所以一起建、一起作废。
        """
        if self.target_finder_chapter != chapter:
            self.target_finder_chapter, self.chart, self.target_finder = chapter, None, None
            try:
                package, _ = chapter_map(chapter)
                self.chart = StaticMap(package, self.observer.matcher)
                self.target_finder = TargetFinder(self.chart)
            except (OSError, ValueError, KeyError) as exc:
                logger.warning(f'第 {chapter} 章地图无法用于目标规划：{exc}')
        return self.chart

    def find_next_target(self, snapshot):
        """选择下一个目标：可连通道路上最近的普通敌人或 Boss，没有敌人时是附近的机关或电梯。

        snapshot 来自 observer.observe，要求定位已被接受；模型不支持或定位不可用时返回 None。
        """
        chapter = snapshot.get('chapter')
        if (chapter is None or snapshot.get('localization') != 'accepted' or snapshot.get('squad') is None
                or self.observer.matcher is None or chapter != self.observer.chapter):
            return None
        self.ensure_chart(chapter)
        if self.target_finder is None:
            return None
        return self.target_finder.find(snapshot)

    def log_snapshot_summary(self, snapshot):
        """把章节确认那一帧的观测写进日志，措辞与逐轮观测共用 describe_snapshot。

        snapshot 为 None 表示连一帧观测都没拿到（展开小地图失败等），此时只告警一行。
        """
        if snapshot is None:
            logger.warning('本次任务没有拿到章节观测，后续推图没有可用的画面信息。')
            return
        from module.campaign.session import describe_snapshot
        logger.info(f'章节确认时的观测：{describe_snapshot(snapshot)}')
        offscreen = snapshot['offscreen']
        if offscreen is not None:
            logger.info(f"小队已离开画面：画面边缘标记中心 {offscreen['center']}，朝向 {offscreen['direction']}°")

    def plan_tick(self, snapshot, advance=True):
        """一轮决策：把观测并进运行状态，按光幕刷新可行路网，选出下一个目标并打一行日志。

        不发送任何输入。返回目标描述或 None（章节地图底图不可用）。
        可行路网必须在选目标之前刷新：光幕集合变了还用上一轮的切开图，可达性判的就是过期事实。
        advance 为假用于同一轮内的补视野、复核帧与重新决策，不推进冷却计数（见 RunState.update）。
        决策行带上本轮耗时：候选的行走距离要各自重算距离场，是这一步的主要开销，几次决策的耗时能直接对比。
        """
        watch = Stopwatch()
        changes = self.state.update(snapshot, advance=advance)
        chart = self.ensure_chart(self.current_chapter)
        if chart is None or chart.road is None:
            self.last_goal, self.last_routes = None, None
            logger.warning('本章地图底图不可用，这一轮不做决策。')
            return None
        if self.cut is None or self.cut.chart is not chart:
            self.cut = CutRoads(chart)
        if self.cut.update(self.state.blocking()):
            logger.info(f'有 {len(self.cut.blocks)} 处光幕挡路，可行路网已按它重算。')
        goal = choose_goal(self.state, chart, snapshot, self.cut,
                           purpose='collectible' if self.config.task.command == 'auto_main_collectible' else 'story')
        self.last_goal = goal
        from module.campaign.route_planning import planning_routes
        self.last_routes = planning_routes(self.cut, snapshot, goal, self.state.route_goal, state=self.state)
        self.last_route_signature = self.cut.signature
        if self.state.route_goal is not None:
            destination = self.state.route_goal
            logger.info(f"路程目标：{describe(destination)}")
        logger.info(f'第 {self.state.tick} 轮决策（耗时 {watch}）：{describe(goal)}')
        detail = describe_changes(changes)
        if detail:
            logger.info(f'本帧变化：{detail}')
        # 每轮都推一次：远处敌人常只在补视野的展开帧里出现，等到移动反馈再推，界面就会漏掉这一轮的记忆与目标。
        publish_preview_frame(self.device.image, interval=0, source=self.campaign_source(snapshot))
        return goal

    def campaign_source(self, snapshot):
        """为预览快照附加章节设施标注、运行记忆与当前目标，不修改原始观测。

        设施只取已校验的静态地图点与传送关系，不混入场景检测或推断触发状态。章节、底图摘要及
        尺寸必须与快照一致；不可用时 annotations 为 None，已加载但没有设施时返回空列表。
        准备阶段即使尚无运行记忆也可展示标注。浅拷贝避免展示数据被重新当成本帧观测读回。
        """
        if snapshot is None:
            return {'campaign': None}
        source = dict(snapshot, annotations=None)
        info = snapshot.get('map')
        if info and snapshot['chapter'] == self.observer.chapter:
            chart = self.observer.static_map()
            if (chart is not None and chart.chapter == snapshot['chapter']
                    and chart.image_sha256 == info['image_sha256'] and list(chart.size) == list(info['size'])):
                source['annotations'] = dict(points=chart.targets('ground_mechanism', 'ground_elevator'),
                                             transfers=chart.transfers)
        if self.state is None:
            return {'campaign': source}
        memory = self.state.memory()
        route_valid = (snapshot.get('localization') == 'accepted' and self.cut is not None
                       and self.last_route_signature == self.cut.signature)
        memory['routes'] = (self.last_routes or {}) if route_valid else {}
        if memory.get('route_goal'):
            destination = memory['route_goal']
            destination['reason_text'] = label(GOAL_REASONS, destination.get('reason'))
        goal = self.last_goal
        if goal and goal.get('kind') != 'none' and goal.get('position') is not None:
            memory['goal'] = dict(kind=goal['kind'], id=goal['id'], enemy_kind=goal.get('enemy_kind'),
                                  reason_text=label(GOAL_REASONS, goal.get('reason')),
                                  position=[round(float(value), 1) for value in goal['position']])
        return {'campaign': dict(source, memory=memory)}

    def dry_run(self, initial, ticks=DRY_RUN_TICKS):
        """只观测只决策不点击的空跑，返回最后一轮的目标。

        空跑用于在真实场面上验证运行状态层与目标选择而不移动：每轮打出
        选中的目标、被排除的候选和状态变更，最后打一次状态摘要。
        第一轮用 initial（章节确认／小队定位时那帧），之后每轮重新截图观测。
        """
        logger.hr('推图试跑（只看不动）', level=2)
        info = self.observer.map_info
        if info is None:
            logger.warning('本章地图信息不可用，跳过试跑。')
            return None
        identity = (self.current_chapter, self.current_difficulty)
        self.state = RunState(self.current_chapter, self.current_difficulty, info['size'])
        goal, snapshot = None, initial
        for index in range(1, ticks + 1):
            if self.config.stop_event is not None and self.config.stop_event.is_set():
                raise TaskEnd
            if index > 1:
                self.device.sleep(DRY_RUN_INTERVAL)
                self.device.screenshot()
                image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
                snapshot = self.observer.observe(image, identity, time.time())
            if snapshot is None:
                logger.warning(f'第 {index}/{ticks} 轮没有可用的观测，跳过。')
                continue
            goal = self.plan_tick(snapshot)
        logger.info(f'运行状态：{self.state.summary_text()}')
        return goal

    def run(self):
        """主线任务入口：准备资源与客户区，一次完成界面及章节确认，再确认小队位置。

        客户区准备（分辨率、窗口位置、自动 HDR、鼠标通道）由 prepare_client 负责，
        必须在章节识别之前完成。章节识别同时确认主线剧情界面，未确认前不操作小地图。
        章节确认后立刻确认小队底图位置：这一步会按需展开大地图并在面板内平移重试，
        退出时恢复紧凑小地图，供后续推图直接使用。
        确认位置后接入有限推图闭环：逐拍补视野、选目标、移动与回写；遇到战斗准备弹窗就把这次战斗
        接管到底（可选快速战斗、结算与回到野外），回到野外后继续下一拍，而不是结束任务。
        """
        logger.hr(self.config.task.command, level=1)
        watch = Stopwatch()
        self.ensure_resources()
        self.prepare_client()
        chapter = self.detect_current_chapter()
        snapshot = self.confirm_squad_location(self.last_snapshot)
        self.log_snapshot_summary(snapshot)
        if snapshot is None or snapshot.get('localization') != 'accepted' or snapshot.get('squad') is None:
            logger.error('主线没有可信小队起点，停止并交人工定位。')
            raise RequestHumanTakeover
        chart = self.ensure_chart(chapter)
        if chart is None or not chart.supported or chart.road is None:
            logger.error('本章地图底图不可用，不能规划移动。')
            raise RequestHumanTakeover
        logger.info(f'准备阶段结束（地图资源、游戏窗口、章节与小队定位）耗时 {watch}，'
                    f'进入推图闭环：地图 {chart.size}，小队起点 {points_text(snapshot["squad"])}')
        from module.campaign.execution import CampaignExecutor
        from module.campaign.session import CampaignSession, InputBudgetExceeded
        from module.campaign.window import WindowChanged

        self.state = RunState(chapter, self.current_difficulty, chart.size)
        self.last_goal, self.last_routes = None, None
        self.cut = CutRoads(chart)
        self.cut.update([])
        session = CampaignSession(self)
        executor = CampaignExecutor(self, session,
                                    purpose='collectible' if self.config.task.command == 'auto_main_collectible' else 'story')
        try:
            result = executor.run(snapshot)
        except (WindowChanged, InputBudgetExceeded, OSError, ValueError, RuntimeError, cv2.error) as exc:
            logger.error(f'主线执行停止：{exc}（任务总耗时 {watch}）')
            raise RequestHumanTakeover from exc
        logger.info(f'主线执行结束：{self.current_difficulty.upper()} 第 {chapter} 章；{result}；'
                    f'任务总耗时 {watch}')
        logger.info(f'运行状态：{self.state.summary_text()}')
        if result['state'] == 'needs_review':
            raise RequestHumanTakeover
        return chapter
