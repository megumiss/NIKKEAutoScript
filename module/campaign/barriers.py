"""把场景中检测到的屏障投影到地图坐标，并在静态地图上标记道路封锁。

场景→地图走完整单应链：客户区像素 → 展开 ROI → 紧凑面板 → 地图，中间保留透视项。
客户区与展开 ROI 之间是 A_INV @ ROI_TO_PLANE，平移项由锚点钉住：小队同框时用箭头锚点
（箭头动画中点向下补偿 90 客户区像素得到脚下）对应小地图圆环；小队离屏时用视野中心像素
VIEWPORT_PIXEL 对应紧凑面板中心。两级都不可用时不标记，等下一帧。
分层地图各表面场景几何不同，不适用单一单应，由调用方跳过。
"""

import numpy as np

from module.campaign_prototype.goto import ARROW_GROUND_OFFSET, EXPANDED_CENTER, VIEWPORT_PIXEL
from module.campaign_prototype.perception import squad_arrow
from module.campaign_prototype.surface_motion import project

# 立柱与光幕配对的最大地图距离；同一组屏障的立柱在光幕两侧不远处。
PAIR_DISTANCE = 120.
# 场景投影的落点误差（视野锚点中位约 6、p90 约 16 地图像素）大于标注偏移，吸附容差单独放宽。
SNAP_TOLERANCE = 28.


def scene_chart(registration, pixel, panel_point):
    """客户区 → 地图的单应：A_INV @ ROI_TO_PLANE 换算到展开 ROI，再经紧凑面板的配准矩阵到地图。

    registration 为 match_panel 的结果（roi_to_map、a、面板尺寸），pixel 与 panel_point 是同一个
    地面点在客户区和紧凑面板里的坐标，用来钉住客户区与展开 ROI 之间的平移。
    """
    from module.campaign_prototype.goto import A_INV, ROI_TO_PLANE
    width, height = registration['panel_size']
    scale = float(registration['a'])
    to_expanded = np.array([[scale, 0, EXPANDED_CENTER[0] - scale * width / 2],
                            [0, scale, EXPANDED_CENTER[1] - scale * height / 2],
                            [0, 0, 1.]])
    affine = np.eye(3)
    affine[:2, :2] = A_INV
    panel_to_client = affine @ ROI_TO_PLANE @ to_expanded
    offset = np.asarray(pixel, float) - project(panel_to_client, panel_point)
    translation = np.eye(3)
    translation[:2, 2] = offset
    return np.asarray(registration['roi_to_map'], float) @ np.linalg.inv(translation @ panel_to_client)


def scene_anchor(image, squad, viewport):
    """选择场景→地图单应：箭头锚点优先，其次视野中心；都不可用返回 None。"""
    if squad is not None:
        arrow = squad_arrow(image)
        if arrow is not None:
            pixel = np.asarray(arrow, float) + [0., ARROW_GROUND_OFFSET]
            return scene_chart(squad, pixel, squad['player_center']), 'arrow'
    if viewport is not None:
        width, height = viewport['panel_size']
        return scene_chart(viewport, VIEWPORT_PIXEL, (width / 2, height / 2)), 'viewport'
    return None


def project_barriers(objects, image, squad, viewport=None):
    """把屏障检测框的地面落点（底边中点）换算为地图坐标；没有可用锚点时返回空列表。

    squad / viewport 是 observation 保存的配准结果（含 roi_to_map、a、panel_size；squad 另含
    player_center），不是裸坐标。
    """
    barriers = [item for item in objects if item['kind'] == 'barrier']
    if not barriers:
        return []
    anchor = scene_anchor(image, squad, viewport)
    if anchor is None:
        return []
    chart, kind = anchor
    result = []
    for item in barriers:
        x1, y1, x2, y2 = item['box']
        mapped = project(chart, [(x1 + x2) / 2, y2])
        result.append(dict(item, map=[round(float(value), 1) for value in mapped], anchor=kind))
    return result


def mark_blocks(chart, barriers):
    """按光幕配对立柱确定封锁方向，在静态地图上标记道路封锁，返回本帧的封锁段列表。"""
    curtains = [item for item in barriers if item['state'] == 'curtain']
    pillars = [item for item in barriers if item['state'] == 'pillar']
    blocks = []
    for curtain in curtains:
        center = np.asarray(curtain['map'], float)
        nearby = [item for item in pillars
                  if np.linalg.norm(np.asarray(item['map'], float) - center) <= PAIR_DISTANCE]
        direction = None
        if len(nearby) >= 2:
            nearby.sort(key=lambda item: np.linalg.norm(np.asarray(item['map'], float) - center))
            vector = np.asarray(nearby[1]['map'], float) - np.asarray(nearby[0]['map'], float)
            if np.linalg.norm(vector) >= 4:
                direction = vector
        block = chart.mark_barrier(center, direction, tolerance=SNAP_TOLERANCE)
        if block is not None:
            blocks.append(dict(id=f'barrier-{len(blocks) + 1}', kind='barrier', **block))
    chart.blocks = blocks
    return blocks
