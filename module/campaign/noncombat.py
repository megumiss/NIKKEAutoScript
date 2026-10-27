"""主线章节结束后的非战斗页面识别与换章流程。"""

import re
import time
from pathlib import Path

import cv2
import numpy as np

from module.campaign.vision import battle
from module.campaign.vision.chapter import chapter_identity
from module.logger import logger


ASSETS = Path(__file__).resolve().parent / 'assets'
NONCOMBAT_TIME_LIMIT = 300
NONCOMBAT_POLL_SECONDS = .5
NONCOMBAT_TAP_INTERVAL = 2.
ARK_NOTICE_DELAY = 5.
CHAPTER_LIST_ROI = (630, 80, 1145, 920)
CHAPTER_LIST_BRIGHT_RATIO = .35
CHAPTER_OCR_THRESHOLD = .7
CHAPTER_NEXT_OFFSET = 35
CHAPTER_ENTRY_POINT = (1635, 940)
CHAPTER_CLOSE_POINT = (1098, 120)

TEMPLATES = {
    'chapter_clear': ((680, 340, 1090, 440), .9),
    'chapter_redirect': ((650, 390, 1130, 510), .85),
    'gift_check': ((740, 860, 1030, 980), .85),
    'gift_confirm': ((630, 390, 1150, 510), .85),
    'main_check': ((0, 130, 100, 240), .9),
    'ark_check': ((0, 0, 150, 90), .9),
    'guide_arrow': ((1680, 60, 1776, 150), .9),
    'ark_notice': ((350, 60, 1150, 160), .9),
}


def load_templates():
    """读取全部非战斗模板；缺文件时直接失败，避免静默退化为固定坐标点击。"""
    templates = {}
    for name in TEMPLATES:
        image = cv2.imread(str(ASSETS / f'{name}.png'))
        if image is None:
            raise FileNotFoundError(ASSETS / f'{name}.png')
        templates[name] = image
    return templates


def locate(field, templates, name):
    """在指定客户区 ROI 中定位模板，返回匹配分数及模板中心；输入为 1776×999 BGR 图像。"""
    template = templates[name]
    (x1, y1, x2, y2), threshold = TEMPLATES[name]
    region = field[y1:y2, x1:x2]
    if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
        return 0., None
    if name == 'ark_check':
        template = cv2.threshold(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), 180, 255, cv2.THRESH_BINARY)[1]
        region = cv2.threshold(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), 180, 255, cv2.THRESH_BINARY)[1]
    _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))
    height, width = template.shape[:2]
    center = [x1 + point[0] + width / 2, y1 + point[1] + height / 2]
    return float(score), center if score >= threshold else None


def chapter_next(field, reader):
    """用 OCR 定位章节列表中的 NEW CHAPTER，返回对应章节卡片的客户区点击中心。

    只在中央高亮面板占比较高时调用 OCR，避免在野外和结算页逐拍空跑。文字框可位于列表左支或右支，
    点击取其中心并向下偏移到章节名称卡片；文字缺失、框格式异常或面板不符时返回 None，不点击固定位置。
    """
    x1, y1, x2, y2 = CHAPTER_LIST_ROI
    region = field[y1:y2, x1:x2]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    if np.mean(gray > 160) < CHAPTER_LIST_BRIGHT_RATIO:
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


def chapter_progress(field, reader):
    """读取章节列表中当前章节的蓝色敌人计数，返回（面板是否确认，进度或 None）。

    当前章节由「正在执行作战」或英文 MAIN QUEST 标识定位，不能使用固定卡片坐标：章节列表会滚动，
    左右分支也会变化。在当前卡片下方用颜色与连通域尺寸定位蓝色菱形，再只 OCR 图标右侧的原尺寸
    计数。这样黄色计数、红色计数和整页其他章节都不会进入识别框，也避免整页小字粘连造成的误读。
    面板、当前标识、唯一蓝色图标或唯一合法比值未确认时返回 None。
    """
    x1, y1, x2, y2 = (630, 80, 1145, 830)
    result = reader.ocr(field[y1:y2, x1:x2], direct_ocr=True, threshold=CHAPTER_OCR_THRESHOLD)
    details = result['details']
    visible = any(' '.join(item['text'].upper().split()) == 'CHAPTER LIST' for item in details)
    current = next((item for item in details
                    if '正在执行作战' in item['text'] or 'MAIN QUEST' in item['text'].upper()), None)
    if not visible or current is None:
        return visible, None
    box = np.asarray(current.get('bbox'), float)
    if box.size not in (4, 8):
        return visible, None
    box = box.reshape(-1, 2)
    center_x = float(box[:, 0].mean()) + x1
    bottom = float(box[:, 1].max()) + y1
    left, right = int(center_x - 90), int(center_x + 90)
    top, lower = int(bottom + 15), int(bottom + 100)
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


class CampaignNoncombat:
    """推进章节结束至下一章野外的页面状态机，每拍最多发送一次点击。

    只有识别到 MISSION CLEAR 才进入换章流程，避免把普通主页或方舟上的引导当作本任务目标。
    剧情和礼包可插入任意页面，因此每拍优先识别，不按固定页面顺序等待。返回新章节野外时必须
    读到预期章节号，不能只凭小地图出现就复用旧章节的运行状态。
    """

    def __init__(self, session, chapter):
        """绑定当前推图会话与旧章节号；构造时不访问游戏画面。"""
        self.session = session
        self.chapter = chapter
        self.next_chapter = chapter + 1
        self.templates = load_templates()
        self.active = False
        self.deadline = None
        self.ark_arrow_at = None

    def activate(self):
        """开始换章并初始化等待时限，由章节结束画面的可靠模板触发。"""
        self.active = True
        self.deadline = time.monotonic() + NONCOMBAT_TIME_LIMIT
        self.ark_arrow_at = None
        logger.info(f'第 {self.chapter} 章已结束，开始处理换章及非战斗页面。')

    def tap(self, point, label):
        """通过现有会话发送非战斗点击，关闭只适用于野外移动的弹窗与收集品输入闸门。"""
        self.session.popup_handling = True
        try:
            clicked = self.session.tap(point, NONCOMBAT_TAP_INTERVAL)
        finally:
            self.session.popup_handling = False
        if clicked:
            logger.info(f'换章流程：已点击{label}，客户区落点 ({point[0]:.0f}, {point[1]:.0f})。')
        return clicked

    def enemy_progress(self):
        """打开章节列表读取当前章节的蓝色敌人进度，确认面板后才允许点击关闭。

        只在 Boss 前检查调用，不改变章节或难度。面板未确认、当前卡片不可见或计数不可靠时返回 None，
        由调用方回退到区域补扫；关闭后等待界面返回，再由执行层重新取得野外快照。
        """
        self.session.begin_target('position')
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

    def step(self):
        """识别当前客户区并推进一步；返回 None 表示普通野外，字典表示换章等待或完成。

        未知画面不补固定坐标点击；等待超过五分钟由会话时限异常交给上层安全停止。
        """
        if not self.active:
            field = self.session.win.client()
            score, point = locate(field, self.templates, 'chapter_clear')
            if point is None:
                return None
            self.activate()
        else:
            self.session.check()
            field = self.session.win.client()
            if time.monotonic() >= self.deadline:
                raise RuntimeError('换章非战斗流程超过 5 分钟仍未返回新章节野外。')
            score, point = locate(field, self.templates, 'chapter_clear')
        scores = {'chapter_clear': round(score, 3)}

        skip_score, skip_point = battle.story_skip(field)
        scores['story_skip'] = round(skip_score, 3)
        if skip_point is not None:
            self.tap(skip_point, '剧情 SKIP')
            return self.wait('story', scores, field)

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
            self.tap(point, label)
            return self.wait(name, scores, field)

        point = chapter_next(field, self.session.owner.chapter_ocr)
        if point is not None:
            self.tap(point, '新章节')
            return self.wait('chapter_next', scores, field)

        main_score, main_point = locate(field, self.templates, 'main_check')
        ark_score, ark_point = locate(field, self.templates, 'ark_check')
        arrow_score, arrow_point = locate(field, self.templates, 'guide_arrow')
        scores.update(main=round(main_score, 3), ark=round(ark_score, 3), guide_arrow=round(arrow_score, 3))
        if main_point is not None and ark_point is None and arrow_point is not None:
            self.ark_arrow_at = None
            self.tap(arrow_point, '主页右上角引导箭头')
            return self.wait('main', scores, field)
        if ark_point is not None:
            if arrow_point is not None and self.ark_arrow_at is None:
                if self.tap(arrow_point, '方舟右上角引导箭头'):
                    self.ark_arrow_at = time.monotonic()
                return self.wait('ark_arrow', scores, field)
            if self.ark_arrow_at is not None and time.monotonic() - self.ark_arrow_at >= ARK_NOTICE_DELAY:
                notice_score, notice_point = locate(field, self.templates, 'ark_notice')
                scores['ark_notice'] = round(notice_score, 3)
                if notice_point is not None:
                    self.ark_arrow_at = None
                    self.tap(notice_point, '方舟中央感叹号')
                    return self.wait('ark_notice', scores, field)
            return self.wait('ark', scores, field)

        identity = chapter_identity(field, self.session.owner.chapter_ocr)
        if battle.field_visible(field) and identity == (self.next_chapter, self.session.expected_identity[1]):
            logger.info(f'换章流程完成：已进入第 {self.next_chapter} 章野外，重新建立章节会话。')
            self.session.record('noncombat_complete', field=field, force=True, chapter=self.next_chapter)
            return {'state': 'chapter_transition', 'chapter': self.next_chapter}
        return self.wait('transition', scores, field)

    def wait(self, state, scores, field):
        """记录本拍页面证据并限速等待，不改变状态或假定下一帧页面。"""
        self.session.record('noncombat_state', field=field, state=state, scores=scores)
        self.session.pause(NONCOMBAT_POLL_SECONDS)
        return {'state': 'noncombat', 'page': state}
