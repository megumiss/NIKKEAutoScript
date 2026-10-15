"""共享有限移动闭环：会话决定如何观测和输入，算法不创建实验窗口。"""

import time

import numpy as np


def movement_plan(localizer, observation, target, anchor):
    """验证道路连通性并生成画面内完整目标的落点与路线长度。

    先在修订道路上验证连通性，再把完整 target 投影到 ROI 和场景安全区域。
    返回客户区点击点与道路路径长度；离屏目标抛出 ValueError，供调用者选择镜头规划流程。
    """
    from module.campaign.navigation.camera_navigation import (
        FIELD_BOUNDS,
        ROI_BOUNDS,
        field_chart,
        inside,
        target_projection,
    )

    position = np.asarray(observation['position'], float)
    _, path = localizer.route(position, target)
    if np.linalg.norm(np.asarray(target) - position) < 2:
        raise ValueError('当前道路没有可用的前进落点，停止测试。')
    roi, click = target_projection(field_chart(localizer, observation, anchor), observation, target)
    if not inside(roi, ROI_BOUNDS) or not inside(click, FIELD_BOUNDS):
        raise ValueError('目标不在当前有效画面内，需要先平移镜头。')
    return click, float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())


def navigate(
    session, target, emit, max_moves=24, arrival_radius=None, required_near=3, purpose='position', elevators=None
):
    """有限步执行并重新定位，连续三次近点观测才算到达，无进展或预算耗尽明确失败。

    session 提供观测、规划、输入及停稳接口，target、到达半径和位置差都使用原图像素。
    到点模式按 required_near 次连续近点观测确认；收集品和敌人模式在附近进行有限试点，耗尽后返回 needs_review。
    arrival_radius 缺省取 goto.SQUAD_REACH（圆环覆盖半径）：点击放行残差与到点判定必须同源，
    否则镜头认为已到而到点循环不认，会空转触发停滞。
    每次点击后等待停稳再定位；无进展、max_moves 用尽或十分钟超时抛错，实际触发由会话异常传给入口处理。
    镜头平移导致小队观测变化时丢弃旧基准，最多两次等待停稳并重新定位，不使用旧计划点击。
    平面到点模式可传入已校验的电梯标注；入口仅作为中转，连续两次确认出口区域后继续最终目标。
    """
    from module.campaign.navigation.controls import SQUAD_REACH
    from module.campaign.navigation.movement_feedback import (
        AnchorUnresolved,
        SquadPositionChanged,
        resolve_anchor,
        wake_arrow_click,
    )

    if arrival_radius is None:
        arrival_radius = SQUAD_REACH
    target = np.asarray(target, float)
    clicks, near, stagnant, replans = 0, 0, 0, 0
    previous = None
    probes = None
    probe_index = 0
    arrow_recoveries = 0
    legs, transfers = None, []
    exit_near, entry_waits, leg_start_clicks = 0, 0, 0
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        session.check()
        emit(state='locating', message='正在定位小队…', movement_clicks=clicks)
        observation = session.observe()
        position = np.asarray(observation['position'], float)
        if observation.get('position_kind') != 'squad' or position.shape != (2,) or not np.isfinite(position).all():
            raise RuntimeError('小队定位无效，停止移动。')
        if elevators is not None and purpose == 'position':
            from module.campaign.navigation.elevator_navigation import at_elevator_exit, plan_elevators

            if legs is None:
                legs = plan_elevators(session.localizer, elevators, position, target)
                emit(elevator_route=legs.copy(), elevator_transfers=[])
            if legs and at_elevator_exit(session.localizer, position, legs[0]):
                exit_near += 1
                emit(
                    state='verifying',
                    message=f'正在确认电梯出口（{exit_near}/2）…',
                    position=position.tolist(),
                    distance=float(np.linalg.norm(target - position)),
                    iou=observation['iou'],
                )
                if exit_near < 2:
                    session.pause(1)
                    continue
                transfers.append(dict(legs.pop(0), position=position.tolist()))
                emit(
                    message='已确认电梯传送，继续前往目标。',
                    elevator_transfers=transfers.copy(),
                    elevator_entry=None,
                    elevator_exit=None,
                )
                session.anchor_reference = None
                session.anchor_reference_method = None
                previous, probes, probe_index, stagnant, near = None, None, 0, 0, 0
                exit_near, entry_waits, leg_start_clicks = 0, 0, clicks
                continue
            exit_near = 0
        transferring = bool(legs)
        approach = target
        if transferring:
            approach = np.asarray(legs[0]['entry'], float)
        if purpose == 'enemy':
            markers = [
                np.asarray(p)
                for p in observation.get('enemy_markers', [])
                if np.linalg.norm(np.asarray(p) - target) <= 30
            ]
            if len(markers) == 1:
                approach = markers[0]
                emit(target_marker=approach.tolist())
        distance = float(np.linalg.norm(approach - position))
        emit(
            position=position.tolist(),
            distance=float(np.linalg.norm(target - position)) if transferring else distance,
            iou=observation['iou'],
        )
        if distance <= arrival_radius and purpose == 'position' and not transferring:
            if elevators is not None:
                session.localizer.route(position, target)
            near += 1
            if near >= required_near:
                session.finish_view()
                return {
                    'state': 'arrived',
                    'message': f'已到达目标附近（连续 {required_near} 次在 {arrival_radius} 地图像素内）。',
                    'movement_clicks': clicks,
                    'position': position.tolist(),
                    'distance': distance,
                }
            emit(state='verifying', message=f'正在复核到达位置（{near}/{required_near}）…')
            session.pause(1)
            continue
        near = 0
        if transferring:
            emit(
                message='正在前往电梯入口并等待传送…',
                elevator_entry=legs[0]['entry'],
                elevator_exit=legs[0]['exit'],
                elevator_distance=distance,
            )
            if clicks - leg_start_clicks >= 3:
                raise RuntimeError('已尝试三次前往电梯入口，尚未确认传送，停止移动。')
            if distance < 2:
                entry_waits += 1
                if entry_waits > 3:
                    raise RuntimeError('已在电梯入口停稳，但未触发传送，停止移动。')
                session.pause(2)
                continue
        if previous is not None and probes is None:
            stagnant = stagnant + 1 if np.linalg.norm(position - previous) < 3 else 0
            if stagnant >= 2:
                raise RuntimeError('连续移动后小队没有明显位移，停止测试。')
        if clicks >= max_moves:
            raise RuntimeError(f'已达到 {max_moves} 次移动上限，尚未到达目标。')
        emit(state='planning', message='正在自动换算道路落点…')
        try:
            if purpose != 'position' and (distance <= arrival_radius or probes is not None):
                from module.campaign.navigation.movement_feedback import probe_targets

                if probes is None:
                    probes = probe_targets(approach)
                click = None
                while probe_index < len(probes):
                    candidate = probes[probe_index]
                    probe_index += 1
                    try:
                        click = session.plan(observation, candidate)
                        break
                    except AnchorUnresolved:
                        raise
                    except ValueError as exc:
                        emit(message=f'附近落点不可用：{exc}')
                if click is None:
                    session.finish_view()
                    return dict(
                        state='needs_review',
                        message='已到目标附近，有限范围尝试后仍未确认触发，请检查现场。',
                        movement_clicks=clicks,
                        position=position.tolist(),
                        distance=distance,
                    )
                emit(state='probing', message=f'正在尝试目标附近落点（{probe_index}/{len(probes)}）…')
            else:
                click = session.plan(observation, approach)
        except AnchorUnresolved:
            if arrow_recoveries:
                raise
            click = wake_arrow_click(session, observation)
            session.check()
            session.move(click)
            clicks += 1
            arrow_recoveries += 1
            emit(
                state='moving',
                movement_clicks=clicks,
                arrow_recoveries=arrow_recoveries,
                click=click.tolist(),
                message='正在沿道路短移，停稳后重新识别箭头…',
            )
            session.wait_stopped()
            updated = session.observe()
            from module.campaign.navigation import controls as goto

            goto.map_close(session.win)
            field = goto.capture_client(session.win)
            session.identity(field)
            session.check_collectible(field)
            session.preview(field)
            resolve_anchor(session, field, updated, allow_scene=False)
            previous, probes, probe_index, stagnant, near = None, None, 0, 0, 0
            continue
        except SquadPositionChanged as error:
            replans += 1
            emit(
                state='locating',
                message='镜头平移后小队位置变化，正在停稳并重新定位…',
                camera_replans=replans,
                squad_before_pan=error.before,
                squad_after_pan=error.after,
            )
            if replans > 2:
                raise RuntimeError('镜头平移后小队位置反复变化，已用尽两次重新定位机会；未追加移动点击。') from error
            session.anchor_reference = None
            session.anchor_reference_method = None
            previous, probes, probe_index, stagnant, near = None, None, 0, 0, 0
            session.wait_stopped()
            continue
        session.check()
        session.move(click)
        clicks += 1
        previous = position
        emit(state='moving', message='小队正在移动，等待停稳后重新定位…', movement_clicks=clicks, click=click.tolist())
        session.wait_stopped()
    raise RuntimeError('移动测试超过十分钟，停止测试。')
