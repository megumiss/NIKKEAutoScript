"""把现有小地图检测和章节地图定位结果转换为界面快照。"""

import base64
import time

import cv2
import numpy as np

from module.campaign.vision.minimap import map_visible, minimap_control_visible
from module.campaign.vision.frame import EXPANDED_ROI, frame_inputs, observation_frame
from module.campaign.vision.matching import (ACCEPT_COARSE_MARGIN, ACCEPT_PARAMETER_SPREAD_PX,
                                             ACCEPT_ROAD_IOU, MapMatcher)
from module.campaign.vision.barriers import affine_project, project_barriers, scene_anchor
from module.campaign.map.map_state import StaticMap
from module.campaign.map.catalog import chapter_map
from module.campaign.report import LOCALIZATION, LOCATE_CRITERIA, label, points_text
from module.campaign.vision.objects import detect_objects
from module.campaign.vision.mechanisms import mechanism_observations
from module.campaign.vision.story_marker import story_markers
from module.campaign.vision.offscreen import squad_offscreen
from module.campaign.vision.perception import (compact_road_masks, expanded_minimap_masks, marker_candidates,
                                             minimap_masks, project_centers, refine_ring)
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
        self.last_localization_error = None
        self.last_object_error = None
        self.last_offscreen_error = None
        self.last_barrier_error = None
        self.last_viewport_error = None
        self.last_story_error = None

    @staticmethod
    def minimap_closed(image):
        """判断小地图是否被关闭（左上角只剩水滴形箭头控件）。

        image 是 1776×999 BGR 客户区截图。判据沿用采集器 DriverWindow.reset_minimap 的三态
        识别：左上角控件外框可见，但控件外框偏暗（展开态该处是亮的紧凑面板顶栏）且紧凑面板
        区域没有青蓝色地图，即认为处于关闭态；控件不可见或面板可见时返回 False，不确定不算关闭。
        """
        control = image[76:120, 20:64]
        if not minimap_control_visible(control):
            return False
        if not map_visible(image[118:273, 27:207]):
            icon = cv2.cvtColor(control, cv2.COLOR_BGR2HSV)
            # 关闭态的控件底色比展开／紧凑态暗，与 reset_minimap 的 closed 判据保持一致。
            return (icon[:, :, 2] < 130).mean() > 0.5
        return False

    def observe(self, image, identity, captured_at=None, objects=True, expected_squad=None, known_squad=None):
        """在一个截图作用域内生成完整观测，所有消费者共享场景和当前小地图的原始推理。

        模式由本帧确定，缩略与展开互斥；异常退出也清理作用域，不能把结果交给下一帧。
        其余定位、对象筛选与已知小队参数保持原语义。
        """
        with observation_frame(image) as inputs:
            return self._observe(inputs.image, identity, captured_at, objects, expected_squad, known_squad)

    def _observe(self, image, identity, captured_at=None, objects=True, expected_squad=None, known_squad=None):
        """观测一帧 1776×999 BGR 客户区截图，返回界面与导航共用的章节快照。

        identity 是已确认的（章节号, 难度）；章节变化会丢掉地图缓存并重新加载，加载失败只告警，
        之后的定位字段保持 unavailable。
        小地图按左上角控件与面板分三态：紧凑（控件可见且左上小面板是地图）、展开（左上小面板
        不再是地图，地图占满中部 EXPANDED_ROI）、关闭或不在田野页（两者都不是，直接返回）。
        紧凑面板先归一化到 1920×1080 再裁 ROI；展开面板与导航共用原始 ROI、道路掩码和圆环拟合。
        expected_squad 为小队未移动时已确认的底图坐标，只约束展开帧的实测圆环；不接受旧矩阵
        代替本帧配准，调用方负责在移动或战斗后停用旧坐标约束。
        known_squad 仅用于未移动小队的纯镜头找敌人阶段：保留调用方已确认的底图坐标，不检测、
        拟合或校验实时小队圆环；当前面板仍独立道路配准，敌人和覆盖只使用本帧矩阵。
        定位成功后敌人和小队都投影到地图坐标，落到图外视为 needs_review——这种情况多半是配准
        漂移而不是真的出界。objects=False 不返回场景对象；普通定位的机关投影仍可能需要箭头，因此不能保证省掉场景推理。
        """
        chapter, difficulty = identity
        snapshot = dict(chapter=chapter, difficulty=difficulty, captured_at=captured_at or time.time(),
                        map=None, minimap=None, localization='unavailable', squad=None, enemies=[], objects=[],
                        barriers=[], blocks=[], mechanisms=[], barriers_observed=False, barrier_checks=[],
                        offscreen=None, viewport=None, roi_to_map=None,
                        objects_observed=False, story_markers=[], minimap_closed=self.minimap_closed(image))
        if objects:
            detected = self.observe_objects(image)
            snapshot['objects'] = detected or []
            snapshot['objects_observed'] = detected is not None
        snapshot['offscreen'] = self.observe_offscreen(image)
        if chapter != self.chapter:
            self.chapter, self.map_info, self.matcher = chapter, None, None
            self.package, self.chart, self.chart_failed = None, None, False
            try:
                package, self.map_info = chapter_map(chapter)
                self.matcher = MapMatcher(package)
                self.package = package
            except (OSError, ValueError, KeyError) as exc:
                logger.warning(f'第 {chapter} 章地图资源不可用，无法定位：{exc}')
        snapshot['map'] = self.map_info
        mode, panel = self.minimap_mode_and_panel(image)
        if mode is None:
            return snapshot
        if objects:
            try:
                snapshot['story_markers'] = story_markers(image)
                self.last_story_error = None
            except (OSError, ValueError, cv2.error) as exc:
                if str(exc) != self.last_story_error:
                    logger.warning(f'主线菱形模板识别不可用：{exc}')
                    self.last_story_error = str(exc)
        players, enemies = (self.minimap_markers(panel, mode, observe_squad=False) if known_squad is not None
                            else self.minimap_markers(panel, mode))
        scenes = [item for item in snapshot['story_markers'] if item['kind'] == 'scene']
        if len(scenes) == 1 and len(enemies) == 1 and enemies[0]['kind'] == 'ex':
            enemies[0] = dict(enemies[0], kind='boss')
            logger.info('场景存在场内主线菱形，唯一 EX 候选按 Boss 分类。')
        if mode == 'compact' and not players and not enemies and known_squad is None:
            return snapshot
        ok, encoded = cv2.imencode('.jpg', panel, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError('Cannot encode campaign minimap.')
        snapshot['minimap'] = dict(image='data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii'),
                                   size=list(panel.shape[1::-1]), squad=players[0] if len(players) == 1 else None,
                                   enemies=enemies, mode=mode)
        if self.matcher is None:
            return snapshot
        squad_registration, viewport = None, None
        try:
            if known_squad is not None:
                position = np.asarray(known_squad, float)
                width, height = self.map_info['size']
                if (position.shape != (2,) or not np.isfinite(position).all()
                        or not (0 <= position[0] < width and 0 <= position[1] < height)):
                    raise ValueError('找敌人需要已确认且位于本章地图内的小队底图坐标。')
                snapshot['squad'] = position.tolist()
                snapshot['squad_source'] = 'known'
                if mode == 'compact':
                    _, road, valid = compact_road_masks(image)
                else:
                    road, valid, _ = expanded_minimap_masks(panel, require_player=False, locate_player=False)
                player = np.array(panel.shape[1::-1], float) / 2
                options = {'known_squad': position.tolist()}
            else:
                if mode == 'compact':
                    _, road, valid, player = minimap_masks(image)
                else:
                    road, valid, player = expanded_minimap_masks(panel)
                options = ({'expected_position': expected_squad}
                           if mode == 'expanded' and expected_squad is not None else {})
            registration = self.locate(snapshot, chapter, panel, road, valid, player, enemies, **options)
            # 屏障投影链（barriers.scene_chart）按「紧凑面板 × a 后钉在展开 ROI 中心」标定，
            # 而展开形态的面板是原始展开 ROI 的坐标系，与那套标定不同源，
            # 直接把这份配准喂进去会让屏障落点整体偏。展开帧不标屏障，等下一次紧凑帧再标。
            if mode == 'compact':
                if known_squad is not None:
                    viewport = registration
                else:
                    squad_registration = registration
        except (OSError, ValueError, KeyError, cv2.error) as exc:
            snapshot['localization'] = 'needs_review'
            message = str(exc)
            snapshot['localization_error'] = message
            if message != self.last_error:
                logger.warning(f'第 {chapter} 章小地图定位不可用：{message}')
                self.last_error = message
        if viewport is None and snapshot['squad'] is None and any(
                item['kind'] == 'barrier' for item in snapshot['objects']):
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
        inputs = frame_inputs(image)
        return inputs.mode, inputs.panel

    @staticmethod
    def minimap_markers(panel, mode, observe_squad=True):
        """识别小地图面板里的标记，返回（小队圆环中心列表, 敌人列表），坐标都是该面板的像素。

        observe_squad=False 专供纯镜头找敌人，跳过圆环拟合且不提供实时小队标记。
        圆环中心优先用圆环拟合结果，拟合不通过时退回检测框中心（只影响展示，底图定位仍要求
        圆环拟合通过校验）；敌人保留类型与置信度，投影到底图由调用方按同一份配准矩阵完成。
        """
        players, enemies = [], []
        for item in marker_candidates(panel, mode):
            if item.label == 'minimap_squad_ring':
                if observe_squad:
                    center = refine_ring(panel, item)
                    players.append((center if center is not None else item.center).round(1).tolist())
            elif item.label in ('minimap_enemy_normal', 'minimap_enemy_ex', 'minimap_enemy_boss'):
                enemies.append(dict(kind=item.label.removeprefix('minimap_enemy_'),
                                    position=item.center.round(1).tolist(), confidence=round(item.confidence, 3)))
        return players, enemies

    def locate(self, snapshot, chapter, panel, road, valid, player, markers, expected_position=None,
               known_squad=None):
        """对一份已裁好的小地图面板做底图配准并把结果写进快照。

        panel/road/valid/player/markers 必须同属这一份面板的像素坐标系（紧凑与展开形态各调一次），
        否则投影出的敌人位置会整体偏移。配准被接受时写入 squad、roi_to_map、敌人底图坐标与屏障
        锚点并返回该锚点；未被接受（判据不足或小队投影越界）时按原因去重告警并返回 None，
        调用方据此决定是否换形态重试。
        expected_position 仅约束当前实测圆环的底图位置，不改变道路接受阈值。
        known_squad 模式中的 player 是面板视口种子，不代表小队；配准不施加小队坐标约束，
        小队坐标由调用方提供，viewport、敌人及覆盖均由当前面板的独立配准生成。
        """
        options = {'expected_position': expected_position} if expected_position is not None else {}
        result = self.matcher.match_panel(panel, road, valid, player, **options)
        snapshot['localization'] = result['status']
        snapshot['localization_diagnostics'] = {
            name: result[name] for name in ('road_iou', 'coarse_margin', 'parameter_spread_px', 'anchor_error')
            if name in result}
        if result['status'] != 'accepted':
            failed = [name for name, passed in
                      (('road_iou', result['road_iou'] >= ACCEPT_ROAD_IOU),
                       ('coarse_margin', result['coarse_margin'] >= ACCEPT_COARSE_MARGIN),
                       ('parameter_spread', result['parameter_spread_px'] <= ACCEPT_PARAMETER_SPREAD_PX))
                      if not passed]
            self.report_localization_error(
                chapter, f"{result['status']}:{','.join(failed)}",
                f"{label(LOCALIZATION, result['status'])}：道路重合度 {result['road_iou']:.3f}，"
                f"候选位置分差 {result['coarse_margin']:.3f}，"
                f"参数离散度 {result['parameter_spread_px']:.1f} 像素，"
                f"未达标项：{'、'.join(label(LOCATE_CRITERIA, name) for name in failed) or '小队位置落在章节地图外'}")
            return None
        matrix = result['roi_to_map']
        width, height = self.map_info['size']
        view_position = project_centers([player], matrix)[0]
        squad = view_position if known_squad is None else list(known_squad)
        if not (0 <= squad[0] < width and 0 <= squad[1] < height):
            snapshot['localization'] = 'needs_review'
            self.report_localization_error(chapter, 'out_of_bounds',
                                           f'配准后小队坐标 {points_text(squad)} '
                                           f'超出章节地图范围 {width}×{height}')
            return None
        snapshot['squad'] = squad
        if known_squad is not None:
            snapshot['viewport'] = view_position
        snapshot['road_iou'] = float(result['road_iou'])
        if expected_position is not None:
            snapshot['anchor_error'] = float(result['anchor_error'])
        # 屏障投影要沿用这一次配准的完整单应与面板缩放，裸的小队坐标不够。
        registration = dict(roi_to_map=matrix, a=result['a'], panel_size=list(panel.shape[1::-1]),
                            player_center=player)
        snapshot['coverage_size'] = list(panel.shape[1::-1])
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
        logger.warning(f'第 {chapter} 章章节地图定位未通过：{detail}；本帧不采用其坐标。')

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
        logger.info(f"视野中心定位到 {points_text(result['position'])}，道路重合度 {result['road_iou']:.3f}")
        return dict(position=result['position'], roi_to_map=result['roi_to_map'], a=result['a'],
                    panel_size=list(panel.shape[1::-1]))

    def static_map(self):
        """静态地图信息，按章节只建一次；构建失败记录后本章不再重试。"""
        if self.chart is None and not self.chart_failed and self.matcher is not None:
            try:
                self.chart = StaticMap(self.package, self.matcher)
            except (OSError, ValueError, KeyError) as exc:
                self.chart_failed = True
                logger.warning(f'第 {self.chapter} 章地图无法用于标记光幕：{exc}')
        return self.chart

    def observe_barriers(self, image, snapshot, squad, viewport):
        """读取机关状态；光幕检测不得在纯观测阶段直接投影成道路封锁。

        squad / viewport 是本帧的配准结果（见 barriers.project_barriers），仅供机关关联使用。
        小队箭头锚点缺失而紧凑配准可信时，机关改用固定镜头仿射投影（见 barriers.affine_project）。
        本层只保留 objects 中的光幕与立柱候选；barriers_observed 始终为假，不能把漏检当作解封。
        封路和指定编号的消失证据由会话的居中复核流程写入。
        """
        chart = self.static_map()
        if chart is None or chart.model is not None or (squad is None and viewport is None):
            return
        try:
            if scene_anchor(image, squad, viewport) is not None:
                projected = project_barriers(snapshot['objects'], image, squad, viewport,
                                             kinds=('ground_mechanism',))
            elif squad is not None:
                projected = affine_project(snapshot['objects'], squad, kinds=('ground_mechanism',))
            else:
                return
            snapshot['mechanisms'] = mechanism_observations(projected, snapshot['objects'], chart.points)
            # 光幕仅保留在 objects 中，封路与消失证据由会话的居中确认流程写入。
            snapshot['barriers_observed'] = False
            self.last_barrier_error = None
        except (OSError, ValueError, KeyError, np.linalg.LinAlgError, cv2.error) as exc:
            message = str(exc)
            if message != self.last_barrier_error:
                logger.warning(f'光幕／机关无法换算到章节地图：{message}')
                self.last_barrier_error = message


    def observe_objects(self, image):
        """客户区里的机关、电梯与屏障；模型缺类或推理不可用时只告警并返回 None，不影响章节与定位。

        失败返回 None 而不是空列表：调用方要据此区分「没识别」和「识别了但画面里没有」，
        后者才能作为光幕消失的证据。
        """
        try:
            return detect_objects(image)
        except (OSError, ValueError, KeyError, RuntimeError, cv2.error) as exc:
            message = str(exc)
            if message != self.last_object_error:
                logger.warning(f'场景目标（机关、电梯、光幕、敌人脚圈）识别不可用：{message}')
                self.last_object_error = message
            return None

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
