"""主线异常后的独立镜头回正，不使用失败规划的坐标，也不发送小队移动。"""

import cv2
import numpy as np

from module.campaign.navigation import controls as goto
from module.campaign.navigation.camera_navigation import pan_scene
from module.campaign.vision.offscreen import squad_offscreen
from module.campaign.vision.perception import detect_markers, squad_arrow
from module.logger import logger

# 两次同向场景拖动累计超过客户区宽／高；每次端点仍在安全场景区域内。
RECOVERY_DRAGS = ((1100., 0.),) * 2 + ((-1100., 0.),) * 2 + ((0., 650.),) * 2 + ((0., -650.),) * 2
CENTER_TOLERANCE = 50.
RING_TOLERANCE = 8.
VERIFY_INTERVAL = .4


def centering_frame(session):
    """取得同一紧凑帧的小队箭头、圆环与离屏标记，不依赖道路配准或历史位置。

    圆环坐标属于缩放至 1920×1080 后的紧凑 ROI，与 controls.GAZE 同源；箭头换算的
    地面点属于原始客户区，只用于镜头调整及按下点避让，不作为小队移动的落点。
    圆环不唯一时按缺失处理；截图前检查取消、窗口和时限，弹窗由身份闸门向上传播。
    """
    session.check()
    field = session.win.client()
    session.identity(field)
    arrow = squad_arrow(field)
    panel = cv2.resize(field, (1920, 1080))[96:307, 25:243]
    players, _ = detect_markers(panel, np.eye(3))
    ground = None if arrow is None else np.asarray(arrow, float) + [0., goto.ARROW_GROUND_OFFSET]
    evidence = dict(ground=ground, ring=players[0] if len(players) == 1 else None,
                    offscreen=squad_offscreen(field))
    session.record('recovery_center_frame', field=field, **evidence)
    return evidence


def centered(evidence, icon_clicked=False):
    """按箭头地面点与圆环交叉判断居中；成功点过离屏图标后允许箭头被遮挡。

    圆环必须在注视点 8 个紧凑 ROI 像素内；可见箭头地面点必须在客户区中心 50 像素内，
    余量包含箭头动画。仅有居中圆环而无箭头、也未点击图标时不能证明已回正。
    """
    ring, ground = evidence['ring'], evidence['ground']
    if evidence['offscreen'] is not None or ring is None:
        return False
    ring = np.asarray(ring, float)
    if not np.isfinite(ring).all() or np.linalg.norm(ring - goto.GAZE) > RING_TOLERANCE:
        return False
    if ground is None:
        return icon_clicked
    return bool(np.linalg.norm(ground - goto.VIEWPORT_PIXEL) <= CENTER_TOLERANCE)


def recenter_squad(session):
    """有限调整镜头并双帧确认小队居中，优先使用游戏自身的离屏小队图标回正。

    已有图标须双帧位置一致后才点击，最多点两次。箭头和圆环同时可见时先做至多两次
    小幅居中；未确认则分次长拖，按右、左、下、上换向。看不清箭头时只在展开地图内
    拖动，再收起检查图标，避免未知小队位置把场景按下手势变成走位。
    成功返回是否点过图标，仅表示镜头回正，调用方仍须重新定位和十字扫描；用尽尝试抛 ValueError。
    全部输入经过原会话闸门，窗口、预算、取消及战斗异常不在此捕获。
    """
    session.check()
    session.verify_identity(session.await_field())
    goto.map_close(session.win)
    evidence = centering_frame(session)
    clicks, adjustments, drags = 0, 0, 0
    while True:
        if centered(evidence, icon_clicked=clicks > 0):
            session.pause(VERIFY_INTERVAL)
            checked = centering_frame(session)
            if (centered(checked, icon_clicked=clicks > 0)
                    and np.linalg.norm(np.asarray(checked['ring']) - evidence['ring']) <= 2.):
                session.record('recovery_centered', force=True, clicks=clicks, drags=drags,
                               adjustments=adjustments, **checked)
                logger.info(f'小队居中已确认：图标点击 {clicks} 次，定向拖动 {drags} 次，微调 {adjustments} 次。')
                return clicks > 0
            evidence = checked
        marker = evidence['offscreen']
        if marker is not None and clicks < 2:
            session.pause(VERIFY_INTERVAL)
            checked = centering_frame(session)
            current = checked['offscreen']
            if (current is not None
                    and np.linalg.norm(np.asarray(current['center']) - marker['center']) <= 12.):
                session.click_client(current['center'])
                clicks += 1
                logger.info(f"小队离屏图标已点击：客户区位置 {current['center']}，第 {clicks}/2 次。")
                session.pause(1.2)
                evidence = centering_frame(session)
                continue
            evidence = checked
        ground = evidence['ground']
        if ground is not None and evidence['ring'] is not None and adjustments < 2:
            offset = np.asarray(goto.VIEWPORT_PIXEL) - ground
            distance = float(np.linalg.norm(offset))
            if distance > CENTER_TOLERANCE:
                pan_scene(session, offset * min(1., 560. / distance), squad_ground=ground)
                adjustments += 1
                evidence = centering_frame(session)
                continue
        if drags == len(RECOVERY_DRAGS):
            break
        drag = np.asarray(RECOVERY_DRAGS[drags])
        drags += 1
        if ground is None:
            # 未定位小队时不能在场景盲拖；面板内的按下点不会成为地面移动指令。
            goto.map_open(session.win, reset=False)
            drag = drag / np.linalg.norm(drag) * 300.
            session.drag_expanded_offset(drag)
            goto.map_close(session.win)
            method = '展开地图'
        else:
            pan_scene(session, drag, squad_ground=ground)
            method = '场景'
        logger.info(f'小队回正定向拖动：第 {drags}/{len(RECOVERY_DRAGS)} 次，区域 {method}，位移 {drag.tolist()} 客户区像素。')
        evidence = centering_frame(session)
    raise ValueError('小队回正未确认：定向拖动及图标点击已达上限，禁止沿用异常前的定位。')
