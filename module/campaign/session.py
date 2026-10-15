"""生产任务的移动会话：复用原型导航闭环，设备与状态由 Campaign 持有。"""

import time
from pathlib import Path

import cv2
import numpy as np

from module.base.utils import publish_preview_frame
from module.campaign.chapter import chapter_identity
from module.campaign.minimap import terrain
from module.campaign.window import CampaignWindow, WindowChanged
from module.campaign_prototype import goto
from module.campaign_prototype.manual_move import GameSession
from module.campaign_prototype.movement_feedback import TargetTriggered, collectible_indicator
from module.campaign_prototype.perception import detect_markers
from module.campaign_prototype.probe import Localizer, NoConnectedRoad
from module.config.config import TaskEnd
from module.logger import logger

# 包含场景手势与小地图开合，单目标预算之外还有章节总预算。
CHAPTER_INPUT_BUDGET = 120
TARGET_INPUT_BUDGET = 32


class InputBudgetExceeded(RuntimeError):
    """章节或单目标场景输入预算耗尽；不可降级为普通目标失败后继续点击。"""


class CampaignInput:
    """生产驱动代理：每次新手势先校验会话并扣预算，释放操作不受停止与失焦限制。"""

    def __init__(self, session, handler):
        """只保存代理关系，不取得新驱动；驱动仍由任务调度器管理。"""
        self.session, self.handler = session, handler

    def __getattr__(self, name):
        """仅包装产生输入的方法；mouse_up 直通，保证取消后仍能释放已按下鼠标。"""
        value = getattr(self.handler, name)
        if name not in ('mouse_click', 'mouse_swipe', 'mouse_down', 'mouse_move'):
            return value

        def send(*args, **kwargs):
            """发送前校验窗口、章节与预算，发送后任一次驱动失败都立即停止。"""
            self.session.before_input()
            result = value(*args, **kwargs)
            if getattr(self.handler, '_failures', 0):
                raise WindowChanged(f'主线输入驱动发送 {name} 失败。')
            return result

        return send


class CampaignLocalizer:
    """给共享闭环提供定位与道路接口；配准看原图，寻路只看最新切开图。"""

    def __init__(self, matcher, cut):
        """使用已绑定章节的 MapMatcher，避免读取原型的全局默认章节或修改全局设置。"""
        self.matcher, self.cut = matcher, cut
        self.old_matrix = goto.ROI_TO_PLANE

    @property
    def road(self):
        """当前封锁切开后的道路；解封更新 cut 后不需要重建定位器。"""
        return self.cut.road

    @property
    def route_labels(self):
        """共享 A* 所需的八像素连通分区，与 road 同一修订。"""
        return self.cut.regions

    def route(self, start, target):
        """沿用原型 A*，先按原始坐标检查分量，防止网格吸附跨过光幕。"""
        home = self.cut.component(start)
        if not home or self.cut.component(target) != home:
            raise NoConnectedRoad('目标不在当前可步行分量内。')
        return Localizer.route(self, np.asarray(start, float), np.asarray(target, float))

    def locate(self, panel, tag, require_player=True):
        """把原始展开 ROI 配准到底图；视野观测可无小队，但低质量配准一律拒绝。"""
        if panel is None or panel.shape != (462, 486, 3):
            raise ValueError('展开地图定位需要原始 486×462 BGR ROI。')
        players, _ = detect_markers(panel, np.eye(3))
        if len(players) > 1 or (require_player and len(players) != 1):
            raise RuntimeError(f'展开地图需一个小队圆环，实际得到 {len(players)} 个。')
        player = np.asarray(players[0] if players else np.array(panel.shape[1::-1]) / 2, float)
        road = terrain(panel)
        valid = np.full(road.shape, 255, np.uint8)
        valid[:4] = valid[-4:] = 0
        valid[:, :4] = valid[:, -4:] = 0
        valid[-35:, -75:] = 0
        hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
        icons = cv2.inRange(hsv, (0, 0, 180), (179, 95, 255))
        red = cv2.inRange(hsv, (0, 65, 160), (10, 255, 255))
        red |= cv2.inRange(hsv, (155, 65, 160), (179, 255, 255))
        valid[cv2.dilate(icons | red, np.ones((7, 7), np.uint8)) > 0] = 0
        if players:
            cv2.circle(valid, tuple(np.rint(player).astype(int)), 34, 0, -1)
        road[valid == 0] = 0
        report = self.matcher.match_panel(panel, road, valid, player)
        if report['status'] != 'accepted':
            raise RuntimeError(f"{tag} 展开地图配准未被接受：{report['status']}。")
        position = np.asarray(report['position'], float)
        matrix = np.asarray(report['roi_to_map'], float)
        if position.shape != (2,) or matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            raise ValueError('展开地图配准输出的坐标或单应格式无效。')
        if not np.isfinite(position).all():
            raise ValueError('展开地图配准输出非有限坐标。')
        return dict(report, iou=report['road_iou'], player_roi=player.tolist() if players else None,
                    position_kind='squad' if players else 'viewport')


class CampaignSession(GameSession):
    """任务 Device 上的会话，复用 GameSession 的 plan、停稳与收尾，不创建采集器窗口。"""

    def __init__(self, owner):
        """绑定已确认的章节与切开图；这里只组装接口，首次 start 才取焦。"""
        self.owner = owner
        self.expected_identity = (owner.current_chapter, owner.current_difficulty)
        self.request = {'chapter': owner.current_chapter, 'difficulty': owner.current_difficulty,
                        'purpose': 'position'}
        self.index = 0
        self.folder = Path(__file__).resolve().parents[2] / 'log/campaign' / (
            f'{owner.current_difficulty}_{owner.current_chapter}')
        self.localizer = CampaignLocalizer(owner.observer.matcher, owner.cut)
        self.compact_matcher = owner.observer.matcher
        self.win = CampaignWindow(owner)
        self.win.stop_check = self.check_stop
        self.win.input_check = self.before_input
        self.win.pause = self.pause
        self.raw_handler = self.win.handler
        self.win.handler = CampaignInput(self, self.raw_handler) if self.raw_handler is not None else None
        self.inputs = 0
        self.target_inputs = 0
        self.popup_handling = False
        self.collectible_mode = owner.config.task.command == 'auto_main_collectible'

    def start(self):
        """开始前要求已存在输入通道，取焦一次并锁定窗口；不在会话中重建驱动。"""
        self.check_stop()
        if self.win.handler is None:
            raise WindowChanged('主线移动需要已初始化的鼠标输入通道。')
        self.folder.mkdir(parents=True, exist_ok=True)
        if not self.win.focus():
            raise WindowChanged('无法取得游戏客户区原点。')
        self.check()

    def check_stop(self):
        """遵守调度器的停止事件；使用 TaskEnd 保留调度器既有取消语义。"""
        stop_event = self.owner.config.stop_event
        if stop_event is not None and stop_event.is_set():
            raise TaskEnd

    def check(self):
        """检查停止与窗口状态；坐标或焦点变化属于安全失败，不允许换目标继续。"""
        self.check_stop()
        self.win.check()

    def begin_target(self, purpose):
        """切换目标用途并重置单目标预算；章节总预算与已验证箭头基准继续保留。"""
        self.request['purpose'] = purpose
        self.target_inputs = 0

    def before_input(self):
        """每次场景手势之前重新截屏核对身份；达到预算时不发送下一次输入。"""
        self.check()
        if not self.popup_handling:
            field = self.win.client()
            self.identity(field)
            self.check_collectible(field)
        if self.inputs >= CHAPTER_INPUT_BUDGET or self.target_inputs >= TARGET_INPUT_BUDGET:
            raise InputBudgetExceeded('主线场景输入预算已耗尽，停止并交人工复核。')
        self.inputs += 1
        self.target_inputs += 1

    def identity(self, field):
        """核对章节、难度与弹窗；普通弹窗保留，EX 只关闭一次，任何用途都立即结束任务。"""
        if goto.battle_popup_score(field) > .8:
            self.preview(field)
            score = cv2.minMaxLoc(cv2.matchTemplate(field, goto.EX_STAGE_TPL, cv2.TM_CCOEFF_NORMED))[1]
            evidence = {'kind': 'battle_popup', 'chapter': self.expected_identity[0]}
            if score > .85:
                self.popup_handling = True
                try:
                    origin = self.win.client_origin()
                    self.win.handler.mouse_click(origin[0] + 1155, origin[1] + 521)
                    self.pause(1.2)
                    if goto.battle_popup_score(self.win.client()) > .8:
                        raise RuntimeError('EX 战斗弹窗关闭失败，不再重试。')
                finally:
                    self.popup_handling = False
                evidence['kind'] = 'ex_stage_skipped'
            raise TargetTriggered('已出现战斗准备弹窗，主线任务停止；不自动进入战斗。', evidence)
        current = chapter_identity(field, self.owner.chapter_ocr)
        if current != self.expected_identity:
            raise WindowChanged(f'主线章节或难度无法确认／已变化：预期 {self.expected_identity}，读到 {current}。')

    def check_collectible(self, field):
        """收集品任务见到橙色倒三角即停；不追加拾取或微调点击。"""
        if not self.collectible_mode and self.request['purpose'] != 'collectible':
            return
        indicator = collectible_indicator(field)
        if indicator is not None:
            self.preview(field)
            raise TargetTriggered('已发现收集品提示，停止并交人工拾取。', indicator)

    def preview(self, field):
        """发布任务预览并保留最新证据；截图不代表战斗或拾取完成。"""
        from module.campaign_prototype.runtime import write_image
        self.folder.mkdir(parents=True, exist_ok=True)
        write_image(self.folder / 'last_field.png', field)
        publish_preview_frame(np.ascontiguousarray(field[:, :, ::-1]), interval=0,
                              source={'campaign': self.owner.last_snapshot})

    def emit(self, **values):
        """将共享闭环的进度写入任务日志，电梯和移动结果仍由执行层回写状态。"""
        logger.info(f'主线移动：{values}')

    def remember_scene(self, field):
        """移动反馈帧也累积敌人与封锁，下一步 A* 不沿用移动前的过期切开图。"""
        snapshot = self.owner.observer.observe(field, self.expected_identity, time.time())
        self.owner.last_snapshot = snapshot
        self.owner.state.update(snapshot)
        if self.owner.cut.update(self.owner.state.blocking()):
            logger.info('移动反馈发现封锁变化，已刷新后续移动的切开路网。')
        return snapshot

    def observe(self):
        """回正并展开地图定位真实小队；计算后道路变化超过 3% 就拒绝旧结果。"""
        self.check()
        field = self.win.client()
        self.identity(field)
        self.check_collectible(field)
        self.remember_scene(field)
        goto.map_open(self.win, reset=True)
        panel = self.win.capture()
        self.index += 1
        report = self.localizer.locate(panel, f'observe_{self.index:03}')
        self.check()
        if np.mean(terrain(panel) != terrain(self.win.capture())) > .03:
            raise RuntimeError('定位期间道路仍在变化，停止使用旧位置。')
        report['enemy_markers'] = goto.normal_enemy_markers(panel, np.asarray(report['roi_to_map']))
        return report

    def move(self, click):
        """只发送有限、有效的客户区地面落点，窗口代理负责最终校验与扣预算。"""
        click = np.asarray(click, float)
        if click.shape != (2,) or not np.isfinite(click).all() or not (0 <= click[0] < 1776 and 0 <= click[1] < 999):
            raise ValueError('移动落点越界或不是有限二维坐标。')
        self.check()
        self.check_collectible(self.win.client())
        origin = self.win.client_origin()
        self.win.handler.mouse_click(origin[0] + round(click[0]), origin[1] + round(click[1]))

    def pause(self, seconds):
        """短间隔等待持续检查取消、焦点与几何，不依赖原型的全局活动窗口。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def snapshot(self, expanded=False):
        """取得一拍业务观测；展开只补记忆，返回前恢复紧凑地图。"""
        self.check()
        self.identity(self.win.client())
        self.win.reset_minimap(expanded=expanded, reset=expanded)
        field = self.win.client()
        self.identity(field)
        self.check_collectible(field)
        snapshot = self.owner.observer.observe(field, self.expected_identity, time.time())
        self.owner.last_snapshot = snapshot
        if expanded:
            self.win.reset_minimap(expanded=False, reset=False)
        return snapshot

    def close(self):
        """始终释放已按下鼠标，不关闭调度器 Device，也不在失焦或取消后点控件。"""
        try:
            if self.raw_handler is not None:
                self.raw_handler.mouse_up()
        finally:
            self.win.close()
