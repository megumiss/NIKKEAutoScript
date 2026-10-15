"""把现有小地图检测和章节地图定位结果转换为界面快照。"""

import base64
import time

import cv2
import numpy as np

from dev_tools.minimap_reconstruct import DriverWindow
from module.campaign.matching import (ACCEPT_COARSE_MARGIN, ACCEPT_PARAMETER_SPREAD_PX, ACCEPT_ROAD_IOU,
                                       MapMatcher, ROI)
from module.campaign.minimap import cropped_minimap
from module.campaign.barriers import mark_blocks, project_barriers
from module.campaign.map_state import StaticMap
from module.campaign.maps import chapter_map
from module.campaign.objects import detect_objects
from module.campaign.offscreen import squad_offscreen
from module.campaign_prototype.perception import (compact_road_masks, marker_candidates, minimap_masks,
                                                  project_centers, refine_ring)
from module.logger import logger

# 展开小地图（点击左上角控件后出现的大地图面板）在 1776×999 客户区里的固定 ROI；
# 与采集器 args.roi、campaign_prototype.goto.MAP_MINIMIZE 同源。展开态下紧凑面板不再显示，
# 面板四角位置会随窗口尺寸略有浮动，所以配准前还要用 cropped_minimap 自己定位面板边界。
EXPANDED_ROI = (644, 280, 1130, 742)


class CampaignObserver:
    """逐帧产出章节快照：场景目标、小地图标记与底图定位。

    地图资源、配准器和静态层都按章节缓存，换章才整体重建，所以同一个任务要复用同一个实例。
    各路识别相互独立，任一路失败只在快照里留空并告警，不影响其他字段；告警按最后一次错误文本
    去重，避免每帧刷同一行日志。观测器本身不累积历史，快照每帧整体替换。
    """

    def __init__(self):
        """初始化按章节缓存的地图状态与各路识别的告警去重字段。"""
        self.chapter = None
        self.map_info = None
        self.matcher = None
        self.package = None
        self.chart = None
        self.chart_failed = False
        self.last_error = None
        self.last_localization_error = None
        self.last_object_error = None
        self.last_offscreen_error = None
        self.last_barrier_error = None
        self.last_viewport_error = None

    @staticmethod
    def minimap_closed(image):
        """判断小地图是否被关闭（左上角只剩水滴形箭头控件）。

        image 是 1776×999 BGR 客户区截图。判据沿用采集器 DriverWindow.reset_minimap 的三态
        识别：左上角控件外框可见，但控件外框偏暗（展开态该处是亮的紧凑面板顶栏）且紧凑面板
        区域没有青蓝色地图，即认为处于关闭态；控件不可见或面板可见时返回 False，不确定不算关闭。
        """
        control = image[76:120, 20:64]
        if not DriverWindow.minimap_control_visible(control):
            return False
        if not DriverWindow.map_visible(image[118:273, 27:207]):
            icon = cv2.cvtColor(control, cv2.COLOR_BGR2HSV)
            # 关闭态的控件底色比展开／紧凑态暗，与 reset_minimap 的 closed 判据保持一致。
            return (icon[:, :, 2] < 130).mean() > 0.5
        return False

    def observe(self, image, identity, captured_at=None, objects=True):
        """观测一帧 1776×999 BGR 客户区截图，返回界面与导航共用的章节快照。

        identity 是已确认的（章节号, 难度）；章节变化会丢掉地图缓存并重新加载，加载失败只告警，
        之后的定位字段保持 unavailable。
        小地图按左上角控件与面板分三态：紧凑（控件可见且左上小面板是地图）、展开（左上小面板
        不再是地图，地图占满中部 EXPANDED_ROI）、关闭或不在田野页（两者都不是，直接返回）。
        紧凑面板要先把画面缩放到 1920×1080 再按 ROI 裁，展开面板直接裁固定 ROI，但配准前还要用
        cropped_minimap 重新定位面板边界——展开面板的位置会随窗口尺寸浮动，而且它给出的圆环
        自带圆度与道路支持校验，比固定 ROI 更可靠。两种形态的配准结果走同一套接受判据与告警。
        定位成功后敌人和小队都投影到地图坐标，落到图外视为 needs_review——这种情况多半是配准
        漂移而不是真的出界。objects=False 用于只需要小地图的场合，可以省掉整幅画面的八片推理。
        """
        chapter, difficulty = identity
        snapshot = dict(chapter=chapter, difficulty=difficulty, captured_at=captured_at or time.time(),
                        map=None, minimap=None, localization='unavailable', squad=None, enemies=[], objects=[],
                        barriers=[], blocks=[], offscreen=None, viewport=None, roi_to_map=None,
                        minimap_closed=self.minimap_closed(image))
        if objects:
            snapshot['objects'] = self.observe_objects(image)
        snapshot['offscreen'] = self.observe_offscreen(image)
        if chapter != self.chapter:
            self.chapter, self.map_info, self.matcher = chapter, None, None
            self.package, self.chart, self.chart_failed = None, None, False
            try:
                package, self.map_info = chapter_map(chapter)
                self.matcher = MapMatcher(package)
                self.package = package
            except (OSError, ValueError, KeyError) as exc:
                logger.warning(f'第 {chapter} 章地图资源不可用于定位：{exc}')
        snapshot['map'] = self.map_info
        mode, panel = self.minimap_mode_and_panel(image)
        if mode is None:
            return snapshot
        players, enemies = self.minimap_markers(panel)
        if mode == 'compact' and not players and not enemies:
            return snapshot
        ok, encoded = cv2.imencode('.jpg', panel, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError('Cannot encode campaign minimap.')
        snapshot['minimap'] = dict(image='data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii'),
                                   size=list(panel.shape[1::-1]), squad=players[0] if len(players) == 1 else None,
                                   enemies=enemies, mode=mode)
        if self.matcher is None:
            return snapshot
        squad_registration = None
        try:
            if mode == 'compact':
                _, road, valid, player = minimap_masks(image)
                markers = enemies
            else:
                # 展开面板按蓝框重新裁一次：配准用的面板与上面展示的面板尺寸不同，
                # 敌人标记必须回到这次配准的面板坐标系里才能一起投影。
                panel, road, valid, player, _ = cropped_minimap(image)
                markers = self.minimap_markers(panel)[1]
            registration = self.locate(snapshot, chapter, panel, road, valid, player, markers)
            # 屏障投影链（barriers.scene_chart）按「紧凑面板 × a 后钉在展开 ROI 中心」标定，
            # 而展开形态的面板是裁蓝框后缩放到 300px 的坐标系，与那套标定不同源，
            # 直接把这份配准喂进去会让屏障落点整体偏。展开帧不标屏障，等下一次紧凑帧再标。
            squad_registration = registration if mode == 'compact' else None
        except (OSError, ValueError, KeyError, cv2.error) as exc:
            snapshot['localization'] = 'needs_review'
            message = str(exc)
            if message != self.last_error:
                logger.warning(f'第 {chapter} 章小地图定位不可用：{message}')
                self.last_error = message
        viewport = None
        if snapshot['squad'] is None and any(item['kind'] == 'barrier' for item in snapshot['objects']):
            viewport = self.locate_viewport(image)
            if viewport is not None:
                snapshot['viewport'] = viewport['position']
        self.observe_barriers(image, snapshot, squad_registration, viewport)
        return snapshot

    @staticmethod
    def minimap_mode_and_panel(image):
        """判断小地图形态并裁出用于展示与标记识别的面板；小地图不可用时返回 (None, None)。

        紧凑态要求左上角控件可见且紧凑面板是地图（判据与 observe 之前的版本一致）；否则看中部
        EXPANDED_ROI 是否是地图，是则判为展开态；两者都不满足说明小地图被关闭或当前不在田野页。
        返回的紧凑面板是 1920×1080 归一化坐标下的裁图，展开面板是客户区 ROI 的原始裁图。
        """
        if DriverWindow.map_visible(image[118:273, 27:207]):
            if not DriverWindow.minimap_control_visible(image[76:120, 20:64]):
                return None, None
            x1, y1, x2, y2 = ROI
            return 'compact', cv2.resize(image, (1920, 1080))[y1:y2, x1:x2]
        left, top, right, bottom = EXPANDED_ROI
        panel = image[top:bottom, left:right]
        if DriverWindow.map_visible(panel):
            return 'expanded', panel
        return None, None

    @staticmethod
    def minimap_markers(panel):
        """识别小地图面板里的标记，返回（小队圆环中心列表, 敌人列表），坐标都是该面板的像素。

        圆环中心优先用圆环拟合结果，拟合不通过时退回检测框中心（只影响展示，底图定位仍要求
        圆环拟合通过校验）；敌人保留类型与置信度，投影到底图由调用方按同一份配准矩阵完成。
        """
        players, enemies = [], []
        for item in marker_candidates(panel):
            if item.label == 'minimap_squad_ring':
                center = refine_ring(panel, item)
                players.append((center if center is not None else item.center).round(1).tolist())
            elif item.label in ('minimap_enemy_normal', 'minimap_enemy_ex'):
                enemies.append(dict(kind='ex' if item.label.endswith('_ex') else 'normal',
                                    position=item.center.round(1).tolist(), confidence=round(item.confidence, 3)))
        return players, enemies

    def locate(self, snapshot, chapter, panel, road, valid, player, markers):
        """对一份已裁好的小地图面板做底图配准并把结果写进快照。

        panel/road/valid/player/markers 必须同属这一份面板的像素坐标系（紧凑与展开形态各调一次），
        否则投影出的敌人位置会整体偏移。配准被接受时写入 squad、roi_to_map、敌人底图坐标与屏障
        锚点并返回该锚点；未被接受（判据不足或小队投影越界）时按原因去重告警并返回 None，
        调用方据此决定是否换形态重试。
        """
        result = self.matcher.match_panel(panel, road, valid, player)
        snapshot['localization'] = result['status']
        if result['status'] != 'accepted':
            failed = [name for name, passed in
                      (('road_iou', result['road_iou'] >= ACCEPT_ROAD_IOU),
                       ('coarse_margin', result['coarse_margin'] >= ACCEPT_COARSE_MARGIN),
                       ('parameter_spread', result['parameter_spread_px'] <= ACCEPT_PARAMETER_SPREAD_PX))
                      if not passed]
            self.report_localization_error(
                chapter, f"{result['status']}:{','.join(failed)}",
                f"{result['status']}，road_iou={result['road_iou']:.3f}，"
                f"margin={result['coarse_margin']:.3f}，spread={result['parameter_spread_px']:.1f}px，"
                f"未达标：{','.join(failed) or '小队投影点未落在底图内'}")
            return None
        matrix = result['roi_to_map']
        width, height = self.map_info['size']
        squad = project_centers([player], matrix)[0]
        if not (0 <= squad[0] < width and 0 <= squad[1] < height):
            snapshot['localization'] = 'needs_review'
            self.report_localization_error(chapter, 'out_of_bounds',
                                           f'配准已接受，但小队投影 {squad} 超出底图 {width}×{height}')
            return None
        snapshot['squad'] = squad
        # 屏障投影要沿用这一次配准的完整单应与面板缩放，裸的小队坐标不够。
        registration = dict(roi_to_map=matrix, a=result['a'], panel_size=list(panel.shape[1::-1]),
                            player_center=player)
        snapshot['roi_to_map'] = [[float(value) for value in row] for row in matrix]
        positions = project_centers([item['position'] for item in markers], matrix)
        snapshot['enemies'] = [dict(item, position=position) for item, position in zip(markers, positions)
                               if 0 <= position[0] < width and 0 <= position[1] < height]
        self.last_error = None
        self.last_localization_error = None
        return registration

    def report_localization_error(self, chapter, key, detail):
        """底图定位未被接受时告警，同一种失败只打一次，避免移动闭环里逐帧刷同一行。

        key 是失败原因的稳定标识（未达标的判据组合、投影越界等），detail 带当次的量化指标，
        因此同一段失败里指标取首次出现的值。配准重新被接受时由 locate 复位，
        同一原因再次出现会重新告警。
        """
        if key == self.last_localization_error:
            return
        self.last_localization_error = key
        logger.warning(f'第 {chapter} 章底图定位未确认（{detail}），小队底图坐标不更新。')

    def locate_viewport(self, image):
        """小队离屏时用紧凑小地图道路配准定位视野中心（无小队圆环种子）；接受时返回配准结果。"""
        if self.matcher is None:
            return None
        try:
            panel, road, valid = compact_road_masks(image)
            center = np.array([panel.shape[1] / 2, panel.shape[0] / 2])
            result = self.matcher.match_panel(panel, road, valid, center)
        except (ValueError, cv2.error) as exc:
            message = str(exc)
            if message != self.last_viewport_error:
                logger.warning(f'视野中心定位不可用：{message}')
                self.last_viewport_error = message
            return None
        if result['status'] != 'accepted':
            return None
        self.last_viewport_error = None
        logger.info(f"视野中心定位：{[round(v, 1) for v in result['position']]}，IoU {result['road_iou']:.2f}")
        return dict(position=result['position'], roi_to_map=result['roi_to_map'], a=result['a'],
                    panel_size=list(panel.shape[1::-1]))

    def static_map(self):
        """静态地图信息，按章节只建一次；构建失败记录后本章不再重试。"""
        if self.chart is None and not self.chart_failed and self.matcher is not None:
            try:
                self.chart = StaticMap(self.package, self.matcher)
            except (OSError, ValueError, KeyError) as exc:
                self.chart_failed = True
                logger.warning(f'第 {self.chapter} 章地图无法用于封锁标记：{exc}')
        return self.chart

    def observe_barriers(self, image, snapshot, squad, viewport):
        """把屏障投影到地图并标记道路封锁；锚点优先小队箭头，其次视野中心。失败只告警。

        squad / viewport 是本帧的配准结果（见 barriers.project_barriers）。
        """
        if not any(item['kind'] == 'barrier' for item in snapshot['objects']):
            return
        chart = self.static_map()
        if chart is None or chart.model is not None:
            return
        try:
            barriers = project_barriers(snapshot['objects'], image, squad, viewport)
            if not barriers:
                return
            snapshot['barriers'] = barriers
            snapshot['blocks'] = mark_blocks(chart, barriers)
            self.last_barrier_error = None
        except (OSError, ValueError, KeyError, np.linalg.LinAlgError, cv2.error) as exc:
            message = str(exc)
            if message != self.last_barrier_error:
                logger.warning(f'屏障封锁标记不可用：{message}')
                self.last_barrier_error = message
        return

    def observe_objects(self, image):
        """客户区里的机关、电梯与屏障；模型缺类或推理不可用时只告警，不影响章节与定位。"""
        try:
            return detect_objects(image)
        except (OSError, ValueError, KeyError, RuntimeError, cv2.error) as exc:
            message = str(exc)
            if message != self.last_object_error:
                logger.warning(f'场景目标识别不可用：{message}')
                self.last_object_error = message
            return []

    def observe_offscreen(self, image):
        """小队离屏时画面边缘的头像标记；识别失败只告警，不影响其他字段。"""
        try:
            return squad_offscreen(image)
        except (ValueError, cv2.error) as exc:
            message = str(exc)
            if message != self.last_offscreen_error:
                logger.warning(f'小队离屏标记识别不可用：{message}')
                self.last_offscreen_error = message
            return None
