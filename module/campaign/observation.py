"""把现有小地图检测和章节地图定位结果转换为界面快照。"""

import base64
import time

import cv2

from dev_tools.minimap_reconstruct import DriverWindow
from dev_tools.wiki_collectible_match import MapMatcher, ROI
from module.campaign.maps import chapter_map
from module.campaign_prototype.perception import marker_candidates, minimap_masks, project_centers, refine_ring
from module.logger import logger


class CampaignObserver:
    def __init__(self):
        self.chapter = None
        self.map_info = None
        self.matcher = None
        self.last_error = None

    def observe(self, image, identity, captured_at=None):
        chapter, difficulty = identity
        snapshot = dict(chapter=chapter, difficulty=difficulty, captured_at=captured_at or time.time(),
                        map=None, minimap=None, localization='unavailable', squad=None, enemies=[])
        if chapter != self.chapter:
            self.chapter, self.map_info, self.matcher = chapter, None, None
            try:
                package, self.map_info = chapter_map(chapter)
                self.matcher = MapMatcher(package)
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
        return snapshot
