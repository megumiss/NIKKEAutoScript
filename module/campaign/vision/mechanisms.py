"""地面机关的标注关联与状态观测，不生成移动输入。"""

import numpy as np

# 机关标注稀疏，场景投影存在视野边缘偏差；只比较候选歧义，不用固定半径丢弃检测。
ANNOTATION_GAP = 24.


def mechanism_observations(projected, objects, points):
    """关联同帧机关与章节标注，保留 YOLO 开关状态及画面邻近敌人的证据。

    projected 必须来自可信紧凑配准。标注存在时按最近标注关联，不设绝对距离上限，
    路线规划使用标注坐标，可见直点使用 click；最近与次近距离差小于 ANNOTATION_GAP、多个检测竞争同一标注
    或开关类别冲突均放弃该关联。near_enemy 只表示画面接近，规划还须用道路距离确认。
    所有 position 为底图像素，click 与 box 为客户区像素。
    """
    annotations = [item for item in points if item['category'] == 'ground_mechanism']
    rings = [np.asarray(item['position'], float) for item in objects
             if item['kind'] == 'enemy_ring' and item.get('confidence', 0.) >= .7]
    candidates = []
    for item in projected:
        if item['kind'] != 'ground_mechanism' or item.get('state') not in ('on', 'off'):
            continue
        position = np.asarray(item['map'], float)
        if position.shape != (2,) or not np.isfinite(position).all():
            continue
        identifier, source = None, 'scene_fallback'
        if annotations:
            ordered = sorted(((float(np.linalg.norm(position - np.asarray(point['position']))), point)
                              for point in annotations), key=lambda pair: pair[0])
            if len(ordered) > 1 and ordered[1][0] - ordered[0][0] < ANNOTATION_GAP:
                continue
            point = ordered[0][1]
            identifier, position, source = point['id'], np.asarray(point['position']), 'annotation'
        left, top, right, bottom = item['box']
        center = np.array([(left + right) / 2, (top + bottom) / 2])
        near = any(np.linalg.norm(ring - center) <= 3 * max(right - left, bottom - top) for ring in rings)
        candidates.append(dict(id=identifier, position=position.tolist(), source=source, click=center.tolist(),
                               state=item['state'], near_enemy=near))
    return [item for item in candidates
            if (item['id'] is not None and sum(other['id'] == item['id'] for other in candidates) == 1)
            or (item['id'] is None and not any(other['state'] != item['state']
                and np.linalg.norm(np.asarray(other['position']) - item['position']) <= 25 for other in candidates))]
