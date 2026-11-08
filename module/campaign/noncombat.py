"""主线章节结束后的非战斗页面识别与换章流程。"""

import json
import re
import time
from pathlib import Path

import cv2
import numpy as np

from module.campaign.map.paths import CHAPTER_NAMES_FILE
from module.campaign.vision import battle
from module.campaign.vision.chapter import chapter_identity
from module.logger import logger
from module.ui.assets import (CAMPAIGN_SELECT_CHECK, CAMPAIGN_SELECT_GOTO_SURFACE, GOTO_MAIN,
                              MAIN_GOTO_CAMPAIGN_SELECT)


ASSETS = Path(__file__).resolve().parent / 'assets'
NONCOMBAT_TIME_LIMIT = 300
NONCOMBAT_POLL_SECONDS = .5
NONCOMBAT_TAP_INTERVAL = 2.
ARK_NOTICE_DELAY = 5.
# 相对中央卡片感叹号锚点的客户区偏移，不改变模板识别位置或主界面引导落点。
ARK_NOTICE_CLICK_OFFSET = (-40., 65.)
# 指挥中心蓝色感叹号位于底部卡片右上角，偏移只用于点击卡片内部。
OUTPOST_NOTICE_CLICK_OFFSET = (-65., 40.)
# 弹窗感叹号位于进入按钮右上角，偏移使落点避开图标并保持在按钮中央。
OUTPOST_ENTRY_CLICK_OFFSET = (-100., 35.)
# 电梯感叹号位于入口图标右上角，落点偏移到两扇门的中央。
OUTPOST_ELEVATOR_CLICK_OFFSET = (-35., 30.)
DISABLED_BATTLE_MAX_SATURATION = 30
DISABLED_BATTLE_MAX_VALUE = 180
DISABLED_BATTLE_NEUTRAL_RATIO = .9
CHAPTER_OCR_THRESHOLD = .7
CHAPTER_TAB_ROI = (640, 850, 1140, 900)
CHAPTER_CARD_ROI = (630, 130, 1140, 830)
CHAPTER_NEXT_OFFSET = 35
CHAPTER_ENTRY_POINT = (1635, 940)
CHAPTER_CLOSE_POINT = (1098, 120)

TEMPLATES = {
    'chapter_clear': ((680, 340, 1090, 440), .9),
    'chapter_redirect': ((650, 390, 1130, 510), .85),
    'battle_disabled': ((910, 870, 1160, 970), .9),
    'unlock': ((780, 370, 1000, 460), .9),
    'unlock_continue': ((780, 590, 1000, 680), .9),
    'story_redirect': ((800, 530, 950, 595), .9),
    'story_redirect_close': ((1045, 385, 1110, 445), .9),
    'chapter_list': ((630, 80, 860, 150), .9),
    'gift_check': ((740, 860, 1030, 980), .85),
    'gift_confirm': ((630, 390, 1150, 510), .85),
    'main_check': ((0, 130, 100, 240), .9),
    'ark_check': ((0, 0, 150, 90), .9),
    'guide_arrow': ((1680, 60, 1776, 150), .9),
    'ark_notice': ((800, 300, 1040, 550), .7),
    'command_center_check': ((0, 0, 150, 90), .9),
    'outpost_check': ((0, 0, 150, 90), .9),
    'outpost_notice': ((850, 755, 1000, 855), .7),
    'outpost_entry_notice': ((1030, 870, 1140, 940), .7),
    'outpost_elevator_notice': ((830, 870, 925, 945), .7),
}


def load_templates():
    """读取全部非战斗模板；缺文件时直接失败，避免静默退化为固定坐标点击。"""
    templates = {}
    for name in TEMPLATES:
        filename = 'ark_notice' if name in (
            'outpost_notice', 'outpost_entry_notice', 'outpost_elevator_notice') else name
        image = cv2.imread(str(ASSETS / f'{filename}.png'))
        if image is None:
            raise FileNotFoundError(ASSETS / f'{filename}.png')
        templates[name] = image
    return templates


def locate(field, templates, name):
    """在指定客户区 ROI 中定位模板，返回匹配分数及模板中心；输入为 1776×999 BGR 图像。

    章节列表标题同时接受正反色相关匹配，兼容普通浅底黑字与困难深底白字，保持原阈值。
    灰色战斗按钮还要求匹配区域至少 90% 为低饱和暗色，避免蓝色按钮被交给此场景分支。
    UNLOCK 标题与继续提示只匹配亮色笔画，避免遮罩后的地图背景变化影响相关分数。
    """
    template = templates[name]
    (x1, y1, x2, y2), threshold = TEMPLATES[name]
    region = field[y1:y2, x1:x2]
    if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
        return 0., None
    if name in ('ark_check', 'unlock', 'unlock_continue'):
        template = cv2.threshold(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), 180, 255, cv2.THRESH_BINARY)[1]
        region = cv2.threshold(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), 180, 255, cv2.THRESH_BINARY)[1]
    response = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    if name == 'chapter_list':
        response = np.abs(response)
    _, score, _, point = cv2.minMaxLoc(response)
    height, width = template.shape[:2]
    center = [x1 + point[0] + width / 2, y1 + point[1] + height / 2]
    if name == 'battle_disabled' and score >= threshold:
        matched = region[point[1]:point[1] + height, point[0]:point[0] + width]
        hsv = cv2.cvtColor(matched, cv2.COLOR_BGR2HSV)
        neutral = (hsv[:, :, 1] <= DISABLED_BATTLE_MAX_SATURATION) & (hsv[:, :, 2] <= DISABLED_BATTLE_MAX_VALUE)
        if neutral.mean() < DISABLED_BATTLE_NEUTRAL_RATIO:
            return float(score), None
    return float(score), center if score >= threshold else None


def chapter_next(field, reader, templates=None):
    """用 OCR 定位章节列表中的 NEW CHAPTER，返回对应章节卡片的客户区点击中心。

    先确认章节列表标题再调用 OCR，不以面板亮度区分页面，以兼容困难模式的深色列表。
    templates 可传入场景控制器已加载的模板，省略时自行加载；标题未命中时不调用 OCR。
    文字框可位于列表左支或右支，点击取其中心并向下偏移到章节名称卡片；
    文字缺失或框格式异常时返回 None，不点击固定位置。
    """
    if templates is None:
        templates = load_templates()
    if locate(field, templates, 'chapter_list')[1] is None:
        return None
    x1, y1, x2, y2 = (630, 130, 1140, 830)
    result = reader.ocr(field[y1:y2, x1:x2], direct_ocr=True, threshold=CHAPTER_OCR_THRESHOLD)
    for item in result['details']:
        if ' '.join(item['text'].upper().split()) != 'NEW CHAPTER':
            continue
        box = np.asarray(item.get('bbox'), float)
        if box.size not in (4, 8):
            continue
        center = box.reshape(-1, 2).mean(axis=0) + [x1, y1 + CHAPTER_NEXT_OFFSET]
        return center.tolist()
    return None


def chapter_list_difficulty(field, templates):
    """用已标定标题的正反色区分普通／困难列表，不读取被弹窗遮罩的野外难度角标。

    普通列表为浅底黑字，困难列表为深底白字；两者都必须达到原章节标题模板阈值。
    标题缺失或同一搜索区域同时命中两种极性时返回 None，切换加载帧不得作为确认依据。
    """
    (x1, y1, x2, y2), threshold = TEMPLATES['chapter_list']
    region, template = field[y1:y2, x1:x2], templates['chapter_list']
    if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
        return None
    low, high, _, _ = cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))
    normal, hard = high >= threshold, -low >= threshold
    if normal == hard:
        return None
    return 'normal' if normal else 'hard'


def chapter_list_text_point(field, reader, area, pattern):
    """在已确认的章节列表内精确 OCR 匹配唯一按钮或章节名，返回客户区中心。

    area 为客户区裁剪范围，pattern 是完整匹配表达式；忽略 OCR 插入的空白，拒绝重复候选。
    四值矩形及四顶点框均支持；空框、非数值、非有限值、退化框或越界框均不产生点击。
    调用方必须先验证列表标题及目标难度，不能用于在未知页面搜索文字后盲点。
    """
    x1, y1, x2, y2 = area
    result = reader.ocr(field[y1:y2, x1:x2], direct_ocr=True, threshold=CHAPTER_OCR_THRESHOLD)
    points = []
    for item in result['details']:
        if re.fullmatch(pattern, ''.join(item['text'].split())) is None:
            continue
        try:
            box = np.asarray(item.get('bbox'), float)
        except (TypeError, ValueError):
            return None
        if box.size not in (4, 8) or not np.isfinite(box).all():
            return None
        box = box.reshape(-1, 2)
        low, high = box.min(axis=0), box.max(axis=0)
        if np.any(low < 0) or np.any(high > [x2 - x1, y2 - y1]) or np.any(high <= low):
            return None
        points.append((box.mean(axis=0) + [x1, y1]).tolist())
    return points[0] if len(points) == 1 else None


def chapter_progress(field, reader):
    """读取章节列表中当前章节的蓝色敌人计数，返回（面板是否确认，进度或 None）。

    优先用「正在执行作战」定位当前卡片，仅缺少该文本时用英文 MAIN QUEST；章节列表会滚动，
    左右分支也会变化，不能使用固定卡片坐标。图标搜索从卡片标题下方开始，排除蓝色章节名文字；
    中文执行标识下移 45、英文标题下移 70 客户区像素，两者均覆盖卡片下方的计数行。
    按颜色与连通域尺寸定位唯一蓝色菱形，再只 OCR 图标右侧原尺寸计数，不读取黄色、红色或其他章节。
    面板、当前标识、唯一蓝色图标或唯一合法比值未确认时返回 None。
    """
    x1, y1, x2, y2 = (630, 80, 1145, 830)
    result = reader.ocr(field[y1:y2, x1:x2], direct_ocr=True, threshold=CHAPTER_OCR_THRESHOLD)
    details = result['details']
    visible = any(' '.join(item['text'].upper().split()) == 'CHAPTER LIST' for item in details)
    current = [item for item in details if '正在执行作战' in item['text']]
    progress_offset = 45
    if not current:
        current = [item for item in details if 'MAIN QUEST' in item['text'].upper()]
        progress_offset = 70
    if not visible or len(current) != 1:
        return visible, None
    current = current[0]
    box = np.asarray(current.get('bbox'), float)
    if box.size not in (4, 8):
        return visible, None
    box = box.reshape(-1, 2)
    center_x = float(box[:, 0].mean()) + x1
    bottom = float(box[:, 1].max()) + y1
    left, right = int(center_x - 90), int(center_x + 90)
    top, lower = int(bottom + progress_offset), int(bottom + 100)
    if not (0 <= left < right <= field.shape[1] and 0 <= top < lower <= field.shape[0]):
        return visible, None
    region = field[top:lower, left:right]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 0] > 70) & (hsv[:, :, 0] < 120)
            & (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 70)).astype(np.uint8)
    _, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    icons = []
    for stat, center in zip(stats[1:], centers[1:]):
        x, y, width, height, area = map(int, stat)
        if 4 <= width <= 18 and 4 <= height <= 18 and area >= 8:
            icons.append((area, center))
    if len(icons) != 1:
        return visible, None
    center = icons[0][1] + [left, top]
    cx, cy = center
    crop = field[int(cy - 10):int(cy + 10), int(cx + 5):int(cx + 53)]
    result = reader.ocr(crop, direct_ocr=True, threshold=CHAPTER_OCR_THRESHOLD)
    ratios = []
    for item in result['details']:
        match = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', item['text'])
        if match is not None:
            ratios.append(tuple(map(int, match.groups())))
    if len(ratios) != 1:
        return visible, None
    cleared, total = ratios[0]
    if total <= 0 or cleared > total:
        return visible, None
    return visible, (cleared, total)


class ChapterTransitionTimeout(RuntimeError):
    """章节间页面超过换章时限仍未确认新章野外，必须停止而不能按旧章节恢复导航。"""


class SceneRequired(RuntimeError):
    """章节导航已离开野外，交任务上层处理页面，不进入旧章节自动回正恢复。"""


class CampaignSceneController:
    """任务上层的场景入口与换章状态机，不依赖已确认章节或小队位置。

    启动允许剧情、主页、方舟、前哨基地、指挥中心、章节列表、战斗和结算；
    确认野外身份后三帧一致才建立章节会话，指挥中心确认后按现有主页按钮返回主界面。
    首次确认野外后锁定本次任务难度，主页恢复误入另一难度时通过列表返回最近章节。
    有旧章节时仍以 MISSION CLEAR 触发严格的下一章约束。正常入口与换章遇到未知页面时有界等待，
    仅异常恢复模式匹配未知页面的返回主页控件，避免页面标题尚未显示时中断引导。
    不靠猜测坐标点击，不把主页引导直接视为章节结束。
    """

    def __init__(self, session, chapter=None):
        """绑定章节无关场景会话；chapter 可缺省，首次野外身份确认后再绑定。"""
        self.session = session
        self.chapter = chapter
        self.next_chapter = None if chapter is None else chapter + 1
        self.templates = load_templates()
        self.active = False
        self.deadline = None
        self.ark_arrow_at = None
        self.transition_expected = None
        self.clear_seen = False
        self.entry_route_sent = False
        self.last_page = None
        self.ui_templates = {}
        self.returning_home = False
        self.preferred_difficulty = None
        self.difficulty_target = None

    def bind_field(self, identity):
        """绑定连续确认的野外身份，并在本次任务首次绑定时锁定难度。

        难度锁跨换章、场景接管和返回主页恢复保留；错误难度不能覆盖已有章节与会话身份。
        只清除本轮切换状态，不保存小队坐标或旧章节配准。
        """
        if self.preferred_difficulty is not None and identity[1] != self.preferred_difficulty:
            raise ValueError(f'野外难度 {identity[1]} 与任务难度 {self.preferred_difficulty} 不一致，禁止绑定。')
        if self.difficulty_target is not None and identity != self.difficulty_target:
            raise ValueError(f'野外身份 {identity} 与恢复目标 {self.difficulty_target} 不一致，禁止绑定。')
        self.preferred_difficulty = identity[1]
        self.difficulty_target = None
        self.chapter = identity[0]
        self.next_chapter = self.chapter + 1
        self.session.expected_identity = identity
        self.active = False
        self.deadline = None
        self.transition_expected = None
        self.clear_seen = False
        self.entry_route_sent = False
        self.ark_arrow_at = None
        self.last_page = None

    def ensure_field(self):
        """从当前任意已支持页面返回可靠野外，连续三帧章节与难度一致后才交导航。

        启动没有旧身份时接受当前野外章节并锁定难度；恢复后难度不符时先通过列表切回原章。
        MISSION CLEAR 已确认时必须满足下一章及原难度，不能因切换难度解除此约束。
        页面处理中不加载地图包或点击小地图，最多等待五分钟，取消、窗口与驱动异常直接传播。
        """
        if not self.active:
            self.activate()
        previous, stable = None, 0
        while True:
            result = self.step()
            identity = result.get('identity') if result is not None else None
            stable = stable + 1 if identity is not None and identity == previous else int(identity is not None)
            previous = identity
            if stable >= 3:
                self.bind_field(identity)
                self.session.record('scene_field_ready', field=result['field'], force=True, identity=identity)
                logger.info(f'场景接管完成：已连续三帧确认 {identity[1].upper()} 第 {identity[0]} 章野外。')
                return identity
            if identity is not None:
                self.session.pause(NONCOMBAT_POLL_SECONDS)

    def require_difficulty(self, field):
        """为错误难度建立一次返回目标，保留通关换章约束且不重复重置页面超时。

        首次野外尚未确认时不调用；普通恢复返回最近绑定章节，通关接管返回已要求的下一章。
        目标只在正确野外连续确认后清除，不能因点击难度标签或章节卡片就提前视为恢复成功。
        """
        if self.difficulty_target is not None:
            return
        self.difficulty_target = self.transition_expected or (self.chapter, self.preferred_difficulty)
        chapter, difficulty = self.difficulty_target
        self.session.record('scene_difficulty_restore', field=field, force=True, identity=self.difficulty_target)
        logger.warning(f'主线难度发生偏离，先返回 {difficulty.upper()} 第 {chapter} 章，暂停章节导航。')

    def restore_difficulty(self, field, scores):
        """在已确认的章节列表中切回任务难度，并按章节名字返回目标章。

        切标签与点章节分帧执行；先用标题极性确认列表难度，再 OCR 目标标签或章节名。
        章节名复用仓库中文映射，只接受精确名字及可选的同章数字；名字缺失或目标未解锁时等待，
        不选择 NEW CHAPTER 或任意可见条目。若野外角标已是目标章则关闭列表，避免点击当前章无效。
        全程沿用场景五分钟时限，未确认的字段不触发固定位置点击。
        """
        difficulty = chapter_list_difficulty(field, self.templates)
        if difficulty != self.preferred_difficulty:
            if difficulty is not None:
                self.require_difficulty(field)
                label = '困难' if self.preferred_difficulty == 'hard' else '普通'
                point = chapter_list_text_point(field, self.session.owner.chapter_ocr, CHAPTER_TAB_ROI, label)
                if point is not None:
                    self.tap(point, f'章节列表切换{label}难度')
            return self.wait('difficulty_tab', scores, field)
        if self.difficulty_target is None:
            return None
        identity = chapter_identity(field, self.session.owner.chapter_ocr)
        if identity == self.difficulty_target:
            self.tap(CHAPTER_CLOSE_POINT, '目标难度及章节已确认，关闭章节列表')
            return self.wait('difficulty_list_close', scores, field)
        chapter, _ = self.difficulty_target
        names = json.loads(CHAPTER_NAMES_FILE.read_text(encoding='utf-8'))
        name = names.get(str(chapter))
        if name is not None:
            pattern = rf'(?:0*{chapter})?{re.escape(name)}(?:0*{chapter})?'
            point = chapter_list_text_point(field, self.session.owner.chapter_ocr, CHAPTER_CARD_ROI, pattern)
            if point is not None:
                self.tap(point, f'恢复章节 {chapter} {name}')
        return self.wait('difficulty_chapter', scores, field)

    def return_home(self, reason):
        """业务异常后按现有页面按钮循环返回主界面，成功前不得重新进入推图。

        本阶段单独使用五分钟时限，不延长原换章阶段，也不取消已确认的下一章约束。
        返回模式关闭已识别弹窗／章节列表并点击返回主页，不点击作战入口或进入战斗；
        正在战斗时沿用结算处理，未知页没有返回按钮就等待，不猜固定坐标。
        必须确认主界面才返回；窗口、取消、驱动异常或超时直接传播，调用方不再次重启。
        """
        self.session.check()
        if getattr(self.session.raw_handler, '_failures', 0):
            from module.campaign.window import WindowChanged
            raise WindowChanged('鼠标驱动已报告失败，禁止异常后的返回主页输入。')
        self.active = False
        self.activate()
        self.returning_home = True
        self.session.target_inputs = 0
        self.session.record('campaign_return_home_begin', force=True, reason=reason)
        try:
            while True:
                result = self.step()
                if result is None or result.get('state') != 'scene_home':
                    continue
                self.active = False
                self.deadline = None
                self.ark_arrow_at = None
                self.last_page = None
                self.session.record('campaign_return_home_complete', field=result['field'], force=True, reason=reason)
                logger.info('异常恢复已确认主界面，重新开始主线入口流程。')
                return
        finally:
            self.returning_home = False

    def handle_story(self, field):
        """在启动、野外插入或战斗剧情中共用 SKIP 判据，每次只点击当前帧的可靠按钮。"""
        score, point = battle.story_skip(field)
        if point is None:
            return False
        self.tap(point, '剧情 SKIP', interval=1.)
        self.session.record('scene_story', field=field, score=score, point=point)
        return True

    def raise_if_needed(self, field, known_only=False):
        """仅检测章节会话是否应交还上层，不在导航输入闸门中执行页面点击。

        MISSION CLEAR 优先于旧身份 OCR，普通剧情、主页、方舟或未知非战斗页面也交上层；
        普通蓝色战斗准备弹窗仍由章节执行层接管以保留敌人归属；UNLOCK、灰色战斗按钮及主线直接前往弹窗
        在野外判据之前交上层处理，避免背景小地图可见时放行导航输入。展开小地图本身不触发场景切换。
        known_only 供战斗轮询使用：未识别的加载帧保留在原战斗接管中，只有可靠页面证据才交上层。
        """
        if self.active or self.start_if_complete(field):
            raise SceneRequired('章节页面已进入上层场景接管。')
        if battle.story_skip(field)[1] is not None:
            self.activate()
            raise SceneRequired('剧情页面交由上层场景接管。')
        if any(locate(field, self.templates, name)[1] is not None
               for name in ('unlock', 'battle_disabled', 'story_redirect')):
            self.activate()
            raise SceneRequired('解锁提示、灰色战斗按钮或主线引导弹窗交由上层场景接管。')
        if battle.field_visible(field) or self.session.owner.minimap_expanded(field):
            return
        kind = battle.battle_state(field)['state']
        if kind in ('entry', 'battle', 'victory', 'defeat', 'continue'):
            return
        if known_only and not any(locate(field, self.templates, name)[1] is not None for name in (
                'main_check', 'ark_check', 'outpost_check', 'command_center_check', 'gift_check', 'gift_confirm',
                'chapter_redirect', 'chapter_list')):
            return
        self.activate()
        raise SceneRequired('当前非野外页面交由上层场景接管。')

    def activate(self):
        """开始章节无关页面接管；重复接管不得延长同一段五分钟等待时限。"""
        if self.active:
            return
        self.active = True
        self.deadline = time.monotonic() + NONCOMBAT_TIME_LIMIT
        self.ark_arrow_at = None
        self.last_page = None
        logger.info('开始处理主线入口及非野外场景。')

    def start_if_complete(self, field):
        """在已取得的客户区截图上确认章节结束并接管，不发送点击或复用旧章节坐标。

        只接受现有 MISSION CLEAR 模板的阈值与搜索区域；Boss 胜利本身不能代替页面证据。
        弹窗覆盖旧章节难度角标时，先激活有界换章流程，再由 step 处理下一章入口与身份确认。
        未命中返回 False，调用方仍须执行原身份检查；重复确认不会重置换章时限。
        """
        score, point = locate(field, self.templates, 'chapter_clear')
        if point is None:
            return False
        if not self.clear_seen:
            self.clear_seen = True
            if self.next_chapter is not None and self.session.expected_identity is not None:
                self.transition_expected = (self.next_chapter, self.session.expected_identity[1])
            self.activate()
            self.session.reuse_squad = False
            self.session.record('noncombat_begin', field=field, force=True, chapter=self.chapter,
                                scores={'chapter_clear': round(score, 3)})
        return True

    def tap(self, point, label, interval=NONCOMBAT_TAP_INTERVAL):
        """通过场景会话发送页面点击；各按钮间隔默认两秒，剧情沿用一秒，停止及窗口闸门不关闭。"""
        self.session.popup_handling = True
        try:
            clicked = self.session.tap(point, interval)
        finally:
            self.session.popup_handling = False
        if clicked:
            logger.info(f'场景流程：已点击{label}，客户区落点 ({point[0]:.0f}, {point[1]:.0f})。')
        return clicked

    def enemy_progress(self):
        """打开章节列表读取当前章节的蓝色敌人进度，确认面板后才允许点击关闭。

        只在 Boss 前检查调用，不改变章节或难度。面板未确认、当前卡片不可见或计数不可靠时返回 None，
        由调用方回退到区域补扫；关闭后等待界面返回，再由执行层重新取得野外快照。
        """
        self.session.target_inputs = 0
        self.tap(CHAPTER_ENTRY_POINT, '章节列表')
        self.session.pause(1)
        field = self.session.win.client()
        visible, progress = chapter_progress(field, self.session.owner.chapter_ocr)
        self.session.record('chapter_enemy_progress', field=field, force=True,
                            visible=visible, progress=progress)
        if visible:
            self.tap(CHAPTER_CLOSE_POINT, '关闭章节列表')
            self.session.pause(.5)
        if progress is None:
            logger.warning('当前章节敌人进度未能可靠读取，Boss 前继续使用区域补扫。')
        else:
            logger.info(f'当前章节敌人进度：{progress[0]}/{progress[1]}（Boss 尚未击败）。')
        return progress

    def ui_point(self, field, button, bounds=(630, 250, 1145, 850)):
        """只在按钮所在的客户区范围匹配现有 UI 资源，不搜索全屏或遍历无关菜单。

        横屏按钮尝试已有的 0.78 缩放及原尺寸，接受门限保持 0.9；调用方必须给出当前页面
        按钮所在范围，默认只用于中央作战选择面板。多个相隔八像素的候选时拒绝点击。
        返回本帧实际模板中心；未匹配时返回 None，交主循环重新截图，不识别加载动画。
        """
        button.ensure_template()
        key = str(button)
        if key not in self.ui_templates:
            image = cv2.cvtColor(button.image, cv2.COLOR_RGB2BGR)
            self.ui_templates[key] = [cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                                      for scale in (.78, 1.)]
        x1, y1, x2, y2 = bounds
        region = field[y1:y2, x1:x2]
        points = []
        for template in self.ui_templates[key]:
            height, width = template.shape[:2]
            if height > region.shape[0] or width > region.shape[1]:
                continue
            response = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            local = cv2.dilate(response, np.ones((3, 3), np.uint8))
            ys, xs = np.nonzero((response >= .9) & (response >= local))
            if len(xs) and (int(xs.max() - xs.min()) > 8 or int(ys.max() - ys.min()) > 8):
                return None
            points.extend(([x1 + x + width / 2, y1 + y + height / 2]) for x, y in zip(xs, ys))
        if not points or any(np.linalg.norm(np.asarray(point) - points[0]) > 8 for point in points[1:]):
            return None
        return points[0]

    def step(self):
        """识别当前客户区并推进一步；返回未接管、页面等待或待连续确认的野外身份。

        方舟右上引导点击后等待五秒，仅在中央卡片范围匹配小型感叹号模板，避免点到顶部任务提示。
        灰色战斗按钮确认后优先点击可用的快速战斗，否则点击本帧灰色按钮匹配中心。
        UNLOCK 标题确认后只点击本帧已识别的继续提示，提示未出现时等待；主线弹窗点击直接前往箭头。
        异常恢复模式仅关闭战斗准备与主线引导弹窗，不点击快速战斗、进入战斗或直接前往。
        解锁提示也可在异常恢复中关闭；以上弹窗均优先于背景页面判定。
        指挥中心标题确认后匹配主页按钮返回主界面，按钮未命中则等待，不点击咨询或回忆入口。
        前哨基地优先处理指挥中心弹窗进入，再按电梯、指挥中心卡片、右上引导的顺序处理。
        电梯及进入按钮只在各自范围匹配感叹号并偏移点击中央；异常恢复模式不进入建筑或电梯。
        不识别加载动画或遍历所有菜单；章节 OCR 只在章节列表或野外已确认时调用。
        未知画面正常等待，仅异常恢复模式尝试返回主页；不补固定坐标点击。
        等待超过五分钟抛出专用换章超时异常，绕过旧章节自动恢复并安全停止。
        """
        self.session.check()
        field = self.session.win.client()
        if not self.active:
            if not self.start_if_complete(field):
                return None
        if time.monotonic() >= self.deadline:
            destination = '主界面' if self.returning_home else '可进入的主线野外'
            raise ChapterTransitionTimeout(f'上层场景流程超过 5 分钟仍未确认{destination}。')
        score, point = locate(field, self.templates, 'chapter_clear')
        scores = {'chapter_clear': round(score, 3)}
        if point is not None:
            self.start_if_complete(field)

        unlock_score, unlock_point = locate(field, self.templates, 'unlock')
        scores['unlock'] = round(unlock_score, 3)
        if unlock_point is not None:
            continue_score, continue_point = locate(field, self.templates, 'unlock_continue')
            scores['unlock_continue'] = round(continue_score, 3)
            if continue_point is not None:
                self.tap(continue_point, 'UNLOCK 点击继续', interval=1.)
            return self.wait('unlock', scores, field)

        skip_score, skip_point = battle.story_skip(field)
        scores['story_skip'] = round(skip_score, 3)
        if skip_point is not None:
            self.handle_story(field)
            return self.wait('story', scores, field)

        disabled_score, disabled_point = locate(field, self.templates, 'battle_disabled')
        scores['battle_disabled'] = round(disabled_score, 3)
        if disabled_point is not None:
            if self.returning_home:
                close_score = battle.score(battle.ENTRY_CLOSE_TPL, field[490:560, 1100:1185])
                scores['battle_disabled_close'] = round(close_score, 3)
                if close_score >= battle.THRESHOLD:
                    from module.campaign.scene_session import ENTRY_CLOSE_POINT
                    self.tap(ENTRY_CLOSE_POINT, '异常恢复关闭灰色战斗按钮弹窗')
            elif battle.quick_battle_available(field):
                from module.campaign.scene_session import ENTRY_TAP_INTERVAL, QUICK_BATTLE_POINT
                scores['quick_ratio'] = round(battle.orange_ratio(field, battle.QUICK_BATTLE_ROI), 3)
                self.tap(QUICK_BATTLE_POINT, '快速战斗', interval=ENTRY_TAP_INTERVAL)
            else:
                self.tap(disabled_point, '灰色进入战斗按钮')
            return self.wait('battle_disabled', scores, field)

        redirect_score, redirect_point = locate(field, self.templates, 'story_redirect')
        scores['story_redirect'] = round(redirect_score, 3)
        if redirect_point is not None:
            if self.returning_home:
                close_score, close_point = locate(field, self.templates, 'story_redirect_close')
                scores['story_redirect_close'] = round(close_score, 3)
                if close_point is not None:
                    self.tap(close_point, '异常恢复关闭主线引导弹窗')
                return self.wait('story_redirect_close', scores, field)
            if self.tap(redirect_point, '主线引导弹窗直接前往箭头'):
                self.entry_route_sent = True
            return self.wait('story_redirect', scores, field)

        for name, label in (
                ('gift_confirm', '礼包关闭确认'),
                ('gift_check', '关闭礼包'),
                ('chapter_clear', '开启下一章节'),
                ('chapter_redirect', '前往相应位置确认')):
            score, point = locate(field, self.templates, name)
            scores[name] = round(score, 3)
            if point is None:
                continue
            if name == 'chapter_clear':
                point = (887, 643)
            elif name in ('chapter_redirect', 'gift_confirm'):
                point = (1000, 625)
            if self.tap(point, label) and name == 'chapter_redirect':
                self.entry_route_sent = True
            return self.wait(name, scores, field)

        center_score, center_point = locate(field, self.templates, 'command_center_check')
        scores['command_center'] = round(center_score, 3)
        if center_point is not None:
            home = self.ui_point(field, GOTO_MAIN, (0, 850, 450, 999))
            if home is not None:
                self.tap(home, '指挥中心返回主页')
            return self.wait('command_center', scores, field)

        main_score, main_point = locate(field, self.templates, 'main_check')
        ark_score, ark_point = locate(field, self.templates, 'ark_check')
        arrow_score, arrow_point = locate(field, self.templates, 'guide_arrow')
        scores.update(main=round(main_score, 3), ark=round(ark_score, 3), guide_arrow=round(arrow_score, 3))
        outpost_score, outpost_point = locate(field, self.templates, 'outpost_check')
        scores['outpost'] = round(outpost_score, 3)
        if outpost_point is not None and not self.returning_home:
            entry_score, entry_point = locate(field, self.templates, 'outpost_entry_notice')
            scores['outpost_entry_notice'] = round(entry_score, 3)
            if entry_point is not None:
                click = (np.asarray(entry_point, float) + OUTPOST_ENTRY_CLICK_OFFSET).tolist()
                self.tap(click, '前哨基地指挥中心弹窗进入按钮')
                return self.wait('outpost_entry', scores, field)
            elevator_score, elevator_point = locate(field, self.templates, 'outpost_elevator_notice')
            scores['outpost_elevator_notice'] = round(elevator_score, 3)
            if elevator_point is not None:
                click = (np.asarray(elevator_point, float) + OUTPOST_ELEVATOR_CLICK_OFFSET).tolist()
                self.tap(click, '前哨基地电梯入口')
                return self.wait('outpost_elevator', scores, field)
            notice_score, notice_point = locate(field, self.templates, 'outpost_notice')
            scores['outpost_notice'] = round(notice_score, 3)
            if notice_point is not None:
                click = (np.asarray(notice_point, float) + OUTPOST_NOTICE_CLICK_OFFSET).tolist()
                self.tap(click, '前哨基地指挥中心卡片')
                return self.wait('outpost_notice', scores, field)
            if arrow_point is not None:
                self.tap(arrow_point, '前哨基地右上角引导箭头')
            return self.wait('outpost', scores, field)

        if main_point is not None and ark_point is None and outpost_point is None:
            if self.returning_home:
                return {'state': 'scene_home', 'field': field}
            self.ark_arrow_at = None
            point = arrow_point or self.ui_point(field, MAIN_GOTO_CAMPAIGN_SELECT, (700, 730, 1200, 999))
            if point is not None:
                self.tap(point, '主页右上角引导箭头' if arrow_point is not None else '主页作战入口')
            return self.wait('main', scores, field)
        if ark_point is not None and not self.returning_home:
            if self.ark_arrow_at is None and arrow_point is None:
                self.ark_arrow_at = time.monotonic()
            if arrow_point is not None and self.ark_arrow_at is None:
                if self.tap(arrow_point, '方舟右上角引导箭头'):
                    self.ark_arrow_at = time.monotonic()
                return self.wait('ark_arrow', scores, field)
            if self.ark_arrow_at is not None and time.monotonic() - self.ark_arrow_at >= ARK_NOTICE_DELAY:
                notice_score, notice_point = locate(field, self.templates, 'ark_notice')
                scores['ark_notice'] = round(notice_score, 3)
                if notice_point is not None:
                    self.ark_arrow_at = None
                    click = (np.asarray(notice_point, float) + ARK_NOTICE_CLICK_OFFSET).tolist()
                    self.tap(click, '方舟中央政府司令部卡片')
                    return self.wait('ark_notice', scores, field)
                if arrow_point is None:
                    home = self.ui_point(field, GOTO_MAIN, (0, 850, 450, 999))
                    if home is not None:
                        self.tap(home, '方舟返回主页')
                        return self.wait('ark_home', scores, field)
            return self.wait('ark', scores, field)

        list_score, list_point = locate(field, self.templates, 'chapter_list')
        scores['chapter_list'] = round(list_score, 3)
        if list_point is not None:
            if self.returning_home:
                self.tap(CHAPTER_CLOSE_POINT, '异常恢复关闭章节列表')
                return self.wait('chapter_list_close', scores, field)
            if self.preferred_difficulty is not None:
                result = self.restore_difficulty(field, scores)
                if result is not None:
                    return result
            point = chapter_next(field, self.session.owner.chapter_ocr, self.templates)
            if point is not None:
                if self.tap(point, '新章节'):
                    self.entry_route_sent = True
                return self.wait('chapter_next', scores, field)
            return self.wait('chapter_list', scores, field)

        state = battle.battle_state(field)
        kind = state['state']
        if kind in ('entry', 'battle', 'victory', 'continue', 'defeat'):
            if kind == 'entry' and (self.returning_home or battle.ex_stage(field)):
                from module.campaign.scene_session import ENTRY_CLOSE_POINT
                self.tap(ENTRY_CLOSE_POINT, '异常恢复关闭战斗准备' if self.returning_home else '启动 EX 弹窗关闭')
            elif kind == 'defeat':
                from module.campaign.scene_session import DEFEAT_BACK_POINT
                self.tap(DEFEAT_BACK_POINT, '启动战败返回')
            else:
                if self.returning_home:
                    self.session.battle_step(close_entry=True)
                else:
                    self.session.battle_step()
            return self.wait(kind, scores, field)
        if not self.returning_home and (battle.field_visible(field) or self.session.owner.minimap_expanded(field)):
            identity = chapter_identity(field, self.session.owner.chapter_ocr)
            if identity is not None and self.preferred_difficulty is not None:
                if identity[1] != self.preferred_difficulty:
                    self.require_difficulty(field)
                if self.difficulty_target is not None and identity != self.difficulty_target:
                    self.tap(CHAPTER_ENTRY_POINT, '难度恢复打开章节列表')
                    return self.wait('difficulty_open', scores, field)
            pending_unknown_clear = self.clear_seen and self.chapter is None and not self.entry_route_sent
            if (identity is not None and not pending_unknown_clear
                    and (self.transition_expected is None or identity == self.transition_expected)):
                return {'state': 'scene_field', 'identity': identity, 'field': field}
            return self.wait('field_identity', scores, field)
        if not self.returning_home and self.ui_point(field, CAMPAIGN_SELECT_CHECK) is not None:
            point = self.ui_point(field, CAMPAIGN_SELECT_GOTO_SURFACE)
            if point is not None:
                self.tap(point, '作战选择前往地面')
            return self.wait('campaign_select', scores, field)
        if self.returning_home:
            home = self.ui_point(field, GOTO_MAIN, (0, 850, 450, 999))
            if home is not None:
                self.tap(home, '当前页面返回主页')
                return self.wait('home', scores, field)
        return self.wait('transition', scores, field)

    def wait(self, state, scores, field):
        """记录本拍页面证据并限速等待，不改变状态或假定下一帧页面。"""
        if state != self.last_page:
            logger.info(f'场景接管页面：{state}。')
            self.last_page = state
        self.session.record('noncombat_state', field=field, state=state, scores=scores)
        self.session.pause(NONCOMBAT_POLL_SECONDS)
        return {'state': 'noncombat', 'page': state}


# 保留既有导入名称；实例的持有者改为 Campaign 任务上层。
CampaignNoncombat = CampaignSceneController
