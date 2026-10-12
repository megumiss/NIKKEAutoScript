"""把现有小地图检测和章节地图定位结果转换为界面快照。"""

import base64
import time

import cv2
import numpy as np

from dev_tools.minimap_reconstruct import DriverWindow
from dev_tools.wiki_collectible_match import MapMatcher, ROI
from module.campaign.barriers import mark_blocks, project_barriers
from module.campaign.map_state import StaticMap
from module.campaign.maps import chapter_map
from module.campaign.objects import detect_objects
from module.campaign.offscreen import squad_offscreen
from module.campaign_prototype.parallax_movement import surface_identity
from module.campaign_prototype.perception import (compact_road_masks, marker_candidates, minimap_masks,
                                                  project_centers, refine_ring)
from module.logger import logger


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
        小地图分展开和紧凑两种形态，按左上角控件判断：展开面板直接从固定区域裁，紧凑面板要先把
        画面缩放到 1920×1080 再按 ROI 裁，底图配准只在紧凑模式下做。地图不可见时提前返回，
        展开模式下没有任何标记也提前返回，避免推送无内容的面板。
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
        compact = DriverWindow.minimap_control_visible(image[76:120, 20:64])
        if compact:
            if not DriverWindow.map_visible(image[118:273, 27:207]):
                return snapshot
            x1, y1, x2, y2 = ROI
            panel = cv2.resize(image, (1920, 1080))[y1:y2, x1:x2]
        else:
            panel = image[280:742, 644:1130]
            if not DriverWindow.map_visible(panel):
                return snapshot
        players, enemies = [], []
        for item in marker_candidates(panel):
            if item.label == 'minimap_squad_ring':
                center = refine_ring(panel, item)
                # 检测框中心可展示在原小地图上；底图定位仍要求圆环拟合通过校验。
                players.append((center if center is not None else item.center).round(1).tolist())
            elif item.label in ('minimap_enemy_normal', 'minimap_enemy_ex'):
                enemies.append(dict(kind='ex' if item.label.endswith('_ex') else 'normal',
                                    position=item.center.round(1).tolist(), confidence=round(item.confidence, 3)))
        if not compact and not players and not enemies:
            return snapshot
        ok, encoded = cv2.imencode('.jpg', panel, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError('Cannot encode campaign minimap.')
        snapshot['minimap'] = dict(image='data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii'),
                                   size=list(panel.shape[1::-1]), squad=players[0] if len(players) == 1 else None,
                                   enemies=enemies, mode='compact' if compact else 'expanded')
        if not compact or self.matcher is None:
            return snapshot
        squad_registration = None
        try:
            _, road, valid, player = minimap_masks(image)
            if self.matcher.surface_matcher is not None:
                result = self.matcher.surface_matcher.locate(panel, player, valid)
            else:
                result = self.matcher.match_panel(panel, road, valid, player)
            snapshot['localization'] = result['status']
            if result['status'] != 'accepted':
                return snapshot
            matrix = result['roi_to_map']
            width, height = self.map_info['size']
            squad = project_centers([player], matrix)[0]
            if not (0 <= squad[0] < width and 0 <= squad[1] < height):
                snapshot['localization'] = 'needs_review'
                return snapshot
            snapshot['squad'] = squad
            if self.matcher.surface_matcher is None:
                # 屏障投影要沿用这一次配准的完整单应与面板缩放，裸的小队坐标不够。
                squad_registration = dict(roi_to_map=matrix, a=result['a'], panel_size=list(panel.shape[1::-1]),
                                          player_center=player)
            # 分层地图的目标规划需要小队所在表面，平面地图没有这个字段。
            snapshot['roi_to_map'] = [[float(value) for value in row] for row in matrix]
            if 'reference_frame' in result and 'local_surface' in result:
                snapshot['surface'] = dict(reference_frame=result['reference_frame'],
                                           local_surface=result['local_surface'],
                                           id=surface_identity(self.matcher.surface_matcher, result))
            positions = project_centers([item['position'] for item in enemies], matrix)
            snapshot['enemies'] = [dict(item, position=position) for item, position in zip(enemies, positions)
                                   if 0 <= position[0] < width and 0 <= position[1] < height]
            self.last_error = None
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

    def locate_viewport(self, image):
        """小队离屏时用紧凑小地图道路配准定位视野中心（无小队圆环种子）；接受时返回配准结果。"""
        if self.matcher is None or self.matcher.surface_matcher is not None:
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

        squad / viewport 是本帧的配准结果（见 barriers.project_barriers），分层地图直接跳过。
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
