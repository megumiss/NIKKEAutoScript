"""主线异常后的独立镜头回正，不使用失败规划的坐标，也不发送小队移动。"""

import cv2
import numpy as np

from module.campaign.navigation import controls as goto
from module.campaign.navigation.camera_navigation import pan_scene
from module.campaign.vision.offscreen import squad_offscreen
from module.campaign.vision.perception import detect_markers, squad_arrow
from module.logger import logger

# 横向视野更长，左右各搜索四次；每次拖动端点仍在安全场景区域内。
RECOVERY_DRAGS = ((1100., 0.),) * 4 + ((-1100., 0.),) * 4 + ((0., 650.),) * 2 + ((0., -650.),) * 2
CENTER_TOLERANCE = 50.
GROUND_STABLE_TOLERANCE = 20.
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
    players, _ = detect_markers(panel, np.eye(3), mode='compact')
    ground = None if arrow is None else np.asarray(arrow, float) + [0., goto.ARROW_GROUND_OFFSET]
    evidence = dict(ground=ground, ring=players[0] if len(players) == 1 else None,
                    offscreen=squad_offscreen(field))
    session.record('recovery_center_frame', field=field, **evidence)
    return evidence


def centered(evidence):
    """只按箭头地面点判断小队是否位于视野中央附近。

    离屏图标出现、箭头缺失或坐标非有限值时不通过；小地图圆环不参与回正判定。
    """
    ground = evidence['ground']
    if evidence['offscreen'] is not None or ground is None:
        return False
    ground = np.asarray(ground, float)
    return bool(np.isfinite(ground).all()
                and np.linalg.norm(ground - goto.VIEWPORT_PIXEL) <= CENTER_TOLERANCE)


def recenter_squad(session):
    """短拖按箭头居中，长拖只用于寻找并点击离屏小队图标。

    初始箭头可见时持续按地面点短拖，双帧均以箭头居中且位移不超过 20 像素为准。需要定向
    搜索时在场景安全区域按右、左、下、上长拖，途中不再按箭头微调；离屏图标出现后立即点击
    并直接视为回正成功，不再复核。全程不展开地图，定向搜索用尽后抛 ValueError。
    全部输入经过原会话闸门，窗口、预算、取消及战斗异常不在此捕获。
    """
    session.check()
    session.verify_identity(session.await_field())
    goto.map_close(session.win)
    evidence = centering_frame(session)
    clicks, adjustments, drags = 0, 0, 0
    searching = False
    while True:
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
                return True
            evidence = checked
        if not searching:
            if centered(evidence):
                session.pause(VERIFY_INTERVAL)
                checked = centering_frame(session)
                if (centered(checked)
                        and np.linalg.norm(np.asarray(checked['ground']) - evidence['ground'])
                        <= GROUND_STABLE_TOLERANCE):
                    session.record('recovery_centered', force=True, clicks=clicks, drags=drags,
                                   adjustments=adjustments, **checked)
                    logger.info(f'小队居中已确认：图标点击 {clicks} 次，定向拖动 {drags} 次，微调 {adjustments} 次。')
                    return clicks > 0
                evidence = checked
                continue
            ground = evidence['ground']
            if ground is not None:
                offset = np.asarray(goto.VIEWPORT_PIXEL) - ground
                distance = float(np.linalg.norm(offset))
                if distance > CENTER_TOLERANCE:
                    pan_scene(session, offset * min(1., 560. / distance), squad_ground=ground)
                    adjustments += 1
                    evidence = centering_frame(session)
                    continue
        if drags == len(RECOVERY_DRAGS):
            break
        ground = evidence['ground']
        drag = np.asarray(RECOVERY_DRAGS[drags])
        drags += 1
        searching = True
        pan_scene(session, drag, squad_ground=ground)
        logger.info(f'小队回正定向拖动：第 {drags}/{len(RECOVERY_DRAGS)} 次，位移 {drag.tolist()} 客户区像素。')
        evidence = centering_frame(session)
    raise ValueError('小队回正未确认：定向拖动及图标点击已达上限，禁止沿用异常前的定位。')
