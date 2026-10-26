"""主线章节结束后的非战斗页面识别与换章流程。"""

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

TEMPLATES = {
    'chapter_clear': ((680, 340, 1090, 440), .9),
    'chapter_next': ((630, 130, 1140, 830), .9),
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
    if name == 'chapter_next':
        template = cv2.inRange(cv2.cvtColor(template, cv2.COLOR_BGR2HSV), (80, 40, 50), (130, 255, 255))
        region = cv2.inRange(cv2.cvtColor(region, cv2.COLOR_BGR2HSV), (80, 40, 50), (130, 255, 255))
        overlap = cv2.matchTemplate(region, template, cv2.TM_CCORR) / (255. ** 2)
        ink = cv2.matchTemplate(region, np.ones_like(template), cv2.TM_CCORR) / 255.
        denominator = ink + np.count_nonzero(template)
        scores = np.divide(2 * overlap, denominator, out=np.zeros_like(overlap), where=denominator > 0)
        _, score, _, point = cv2.minMaxLoc(scores)
    else:
        if name == 'ark_check':
            template = cv2.threshold(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), 180, 255, cv2.THRESH_BINARY)[1]
            region = cv2.threshold(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), 180, 255, cv2.THRESH_BINARY)[1]
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))
    height, width = template.shape[:2]
    center = [x1 + point[0] + width / 2, y1 + point[1] + height / 2]
    return float(score), center if score >= threshold else None


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
                ('chapter_next', '新章节'),
                ('chapter_redirect', '前往相应位置确认')):
            score, point = locate(field, self.templates, name)
            scores[name] = round(score, 3)
            if point is None:
                continue
            if name == 'chapter_clear':
                point = (887, 643)
            elif name == 'chapter_next':
                point = (point[0], point[1] + 35)
            elif name in ('chapter_redirect', 'gift_confirm'):
                point = (1000, 625)
            self.tap(point, label)
            return self.wait(name, scores, field)

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
