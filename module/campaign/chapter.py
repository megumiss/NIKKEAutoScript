"""主线地图右下角的章节号识别，输入为 1776×999 的 BGR 客户区截图。"""

import re

import cv2


CLIENT_SIZE = (1776, 999)
CHAPTER_AREA = (1608, 917, 1663, 953)
DIFFICULTY_AREA = (1613, 898, 1670, 912)


def chapter_identity(image, reader):
    """使用共享 OCR 读取章节及难度；任一字段不可靠时不确认身份。"""
    if image is None or image.shape != (CLIENT_SIZE[1], CLIENT_SIZE[0], 3):
        raise ValueError('章节识别需要 1776×999 的三通道客户区截图。')
    values = []
    for area, scale, threshold in ((CHAPTER_AREA, 3, .95), (DIFFICULTY_AREA, 4, .9)):
        x1, y1, x2, y2 = area
        crop = cv2.resize(image[y1:y2, x1:x2], None, fx=scale, fy=scale)
        values.append(reader.ocr(crop, direct_ocr=True, threshold=threshold)['text'].strip())
    number, difficulty = values
    if not re.fullmatch(r'[0-9]{1,2}', number) or int(number) < 1:
        return None
    if difficulty.upper() not in ('NORMAL', 'HARD'):
        return None
    return int(number), difficulty.lower()


def chapter_number(image, model):
    if image is None or image.shape != (CLIENT_SIZE[1], CLIENT_SIZE[0], 3):
        raise ValueError('章节识别需要 1776×999 的三通道客户区截图。')
    x1, y1, x2, y2 = CHAPTER_AREA
    crop = cv2.resize(image[y1:y2, x1:x2], None, fx=3, fy=3)
    result = next(iter(model.predict(crop)), None)
    if result is None:
        return None
    text = result.get('rec_text', '').strip()
    score = result.get('rec_score', 0)
    if not re.fullmatch(r'[0-9]{1,2}', text) or not score >= 0.95:
        return None
    number = int(text)
    return number if number > 0 else None
