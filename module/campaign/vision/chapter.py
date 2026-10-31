"""主线地图右下角的章节号识别，输入为 1776×999 的 BGR 客户区截图。"""

import re

import cv2


CLIENT_SIZE = (1776, 999)
CHAPTER_AREA = (1608, 917, 1663, 953)
DIFFICULTY_AREA = (1613, 898, 1670, 912)


def chapter_identity(image, reader):
    """读取固定角标中的章节与难度；章节置信度至少 0.95，难度至少 0.7。

    难度左侧的装饰图标可能被 OCR 合并为一个非英数前缀，仅允许忽略该单字符及分隔空白。
    剩余内容必须完整匹配 NORMAL 或 HARD，不用子串匹配放过拼写错误、数字或多个难度名称。
    任一字段不可靠时返回 None，不能把未知身份当成已确认的章节。
    """
    if image is None or image.shape != (CLIENT_SIZE[1], CLIENT_SIZE[0], 3):
        raise ValueError('章节识别需要 1776×999 的三通道客户区截图。')
    values = []
    for area, scale, threshold in ((CHAPTER_AREA, 3, .95), (DIFFICULTY_AREA, 4, .7)):
        x1, y1, x2, y2 = area
        crop = cv2.resize(image[y1:y2, x1:x2], None, fx=scale, fy=scale)
        values.append(reader.ocr(crop, direct_ocr=True, threshold=threshold)['text'].strip())
    number, difficulty = values
    if not re.fullmatch(r'[0-9]{1,2}', number) or int(number) < 1:
        return None
    matched = re.fullmatch(r'[^A-Z\d]?\s*(NORMAL|HARD)', difficulty.upper())
    if matched is None:
        return None
    return int(number), matched.group(1).lower()


def chapter_number(image, model):
    """只读章节号的轻量版本，直接吃 PaddleOCR 模型而不经过项目的 Ocr 封装。

    供标定、离线批处理等没有任务上下文的场合使用，因此不识别难度；放大三倍再识别是因为
    原始数字只有约 36px 高。识别分数低于 0.95、不是一到两位数字或解析出 0 时一律返回 None，
    宁可让调用方重试也不要把误识别的章节号传下去。
    """
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
