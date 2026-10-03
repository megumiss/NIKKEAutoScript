"""普通平面章节的小地图／场景位移采样；保存局部标定，供独立验证与复用。"""

import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

import cv2
from filelock import FileLock
import numpy as np

from dev_tools.minimap_chapters import chapter_number, click, wait_for_chapter, write_progress
from module.campaign_prototype import goto, runtime, settings
from module.campaign_prototype.camera_navigation import field_chart, inside, target_projection, FIELD_BOUNDS
from module.campaign_prototype.manual_move import GameSession, prepare, sha
from module.campaign_prototype.movement_feedback import AnchorUnresolved, resolve_anchor
from module.campaign_prototype.surface_motion import fit_calibration, project


def emit(**values):
    if 'wait_player_compact' not in values:
        print(json.dumps(values, ensure_ascii=False), flush=True)


def binding(package):
    return {name: sha(package / name) for name in
            ('map.json', 'map.png', 'annotations.json', 'source/map_data.npz')}


def entry_blind_move(session, folder, point, tag):
    """入口恢复共用的盲移步骤：身份检查后单击、停稳、重新观测并保存现场。"""
    emit(action='entry_blind_move', tag=tag, click=[int(point[0]), int(point[1])])
    session.check()
    session.move(np.array(point))
    session.wait_stopped()
    updated = session.observe()
    write_progress(folder / f'{tag}_observation.json', updated)
    goto.map_close(session.win)
    field = goto.capture_client(session.win)
    session.identity(field)
    runtime.write_image(folder / f'{tag}_field.png', field)
    return updated, field


def explore_entry_view(session, folder):
    """未探索章节入口迷雾导致定位置信度不足时，盲移探索揭开迷雾后重试观测。

    迷雾下小地图可见道路过少会使 IoU 低于验收线；按先四向、再斜向的顺序最多盲移八步，
    每次移动后重新观测，仍无法通过置信度检查则带现场抛错。
    """
    points = ((888, 700), (888, 300), (1188, 500), (588, 500),
              (1088, 680), (688, 320), (1188, 320), (688, 680))
    for index, point in enumerate(points, 1):
        goto.map_close(session.win)
        field = goto.capture_client(session.win)
        session.identity(field)
        try:
            updated, _ = entry_blind_move(session, folder, point, f'explore_{index:02}')
        except RuntimeError as error:
            if 'Uncertain adaptive localization' not in str(error):
                raise
            continue
        return updated
    raise RuntimeError('Entry view exploration did not yield a confident localization.')


def wake_entry_arrow(session, observation, folder):
    """入口站位不显示箭头时，沿道路向更开阔处盲移一次唤醒箭头，再恢复锚点。

    入口触发台座上小队显示橙色圆环且不显示箭头，需离开台座约 200 客户区像素才恢复。
    小队初始位置由入口镜头居中特性近似为 (888, 505)，结合地图净空选择唤醒方向；
    台座可能连续分布，最多引导盲移四次，每次从新位置重新选路；
    仅在定位可信且附近没有敌人标记时尝试；无位移或仍无箭头则带现场抛错。
    """
    from module.campaign_prototype.camera_navigation import field_chart, target_projection

    if observation.get('position_kind') != 'squad':
        raise AnchorUnresolved('入口无箭头且小队定位不可信，放弃唤醒短移。')
    clearance = cv2.distanceTransform(session.localizer.road.astype(np.uint8), cv2.DIST_L2, 5)
    for attempt in range(4):
        position = np.asarray(observation['position'], float)
        enemies = np.asarray(observation.get('enemy_markers', []), float)
        if enemies.size and np.min(np.linalg.norm(enemies - position, axis=-1)) < 60:
            raise AnchorUnresolved('入口无箭头且敌人标记过近，放弃唤醒短移。')
        ys, xs = np.where(clearance >= 40)
        if enemies.size:
            keep = np.min(np.linalg.norm(np.stack([xs, ys], 1)[None] - enemies[:, None, :], axis=-1), axis=0) >= 50
            xs, ys = xs[keep], ys[keep]
        distances = np.hypot(xs - position[0], ys - position[1])
        nearby = np.where(distances <= 400)[0]
        chart = field_chart(session.localizer, observation, np.array([888., 505.]))
        point = None
        scores = distances[nearby] - 1.5 * clearance[ys[nearby], xs[nearby]]
        for candidate in nearby[np.argsort(scores)]:
            target = np.array([xs[candidate], ys[candidate]], float)
            try:
                session.localizer.route(position, target)
                _, projected = target_projection(chart, observation, target)
            except ValueError:
                continue
            if inside(projected, FIELD_BOUNDS):
                point = np.rint(projected).astype(int)
                break
        if point is None:
            point = np.array([888, 700])
        updated, field = entry_blind_move(session, folder, point, f'wake_{attempt + 1:02}')
        if np.linalg.norm(np.asarray(updated['position'], float) - position) < 2:
            blind = ((888, 700), (888, 300), (1188, 500), (588, 500))[attempt]
            updated, field = entry_blind_move(session, folder, blind, f'wake_{attempt + 1:02}b')
            if np.linalg.norm(np.asarray(updated['position'], float) - position) < 2:
                raise AnchorUnresolved('唤醒短移未产生小队位移，入口仍无箭头。')
        observation = updated
        try:
            return observation, field, resolve_anchor(session, field, observation, allow_scene=False)
        except AnchorUnresolved:
            if attempt == 3:
                raise
    raise AnchorUnresolved('多次唤醒短移后入口仍无箭头。')


def calibrate(chapter, folder):
    package = ROOT / f'data/chapter_maps/current/chapter_{chapter:02}'
    metadata = json.loads((package / 'map.json').read_text('utf-8'))
    if chapter in (39, 40) or metadata.get('processing_3d') or metadata.get('coordinate_model'):
        return dict(status='skipped_nonflat', reason='Non-flat chapter excluded by task scope.')
    expected = binding(package)
    saved = package / 'movement_calibration/normal/flat_calibration.json'
    if saved.exists():
        data = json.loads(saved.read_text('utf-8'))
        if data.get('binding') == expected and data.get('status') == 'validated':
            return dict(status='reused', calibration=str(saved), validation_max_px=data['validation_max_px'])
    with np.load(package / 'source/map_data.npz') as cache:
        ys, xs = np.nonzero(cache['terrain_probability'] >= .5)
    request = dict(package=str(package), chapter=chapter, difficulty='normal', action='move',
                   purpose='position', target=[int(xs[0]), int(ys[0])],
                   image_sha256=expected['map.png'], revision=expected['annotations.json'])
    settings.chapter = chapter
    settings.output = folder
    settings.package, hashes = prepare(request, folder)
    session = GameSession(request, hashes, folder, emit)
    samples, evidence = [], []
    try:
        try:
            observation = session.observe()
        except RuntimeError as error:
            if 'Uncertain adaptive localization' not in str(error):
                raise
            observation = explore_entry_view(session, folder)
        write_progress(folder / 'initial_observation.json', observation)
        goto.map_close(session.win)
        field = goto.capture_client(session.win)
        runtime.write_image(folder / 'reference.png', field)
        clearance = cv2.distanceTransform(session.localizer.road.astype(np.uint8), cv2.DIST_L2, 5)
        try:
            anchor = resolve_anchor(session, field, observation, allow_scene=False)
        except AnchorUnresolved:
            observation, field, anchor = wake_entry_arrow(session, observation, folder)
        for attempt in range(9):
            position = np.asarray(observation['position'])
            available = float(clearance[round(position[1]), round(position[0])])
            emit(chapter=chapter, position=position.tolist(), clearance=available)
            if available >= 35:
                break
            if attempt == 8:
                if available >= 35:
                    break
                raise ValueError('Open-road repositioning did not converge after eight measured moves.')
            ys, xs = np.where(clearance >= 50)
            distances = np.hypot(xs - position[0], ys - position[1])
            nearby = np.where(distances <= 120)[0]
            if not nearby.size:
                # 入口附近没有开阔区域时先向更高净空处爬坡，逐次移位接近采样区。
                ys, xs = np.where(clearance >= max(available + 5, 15))
                distances = np.hypot(xs - position[0], ys - position[1])
                nearby = np.where(distances <= 120)[0]
                if not nearby.size:
                    raise ValueError('No reachable open road for calibration.')
                emit(chapter=chapter, action='reposition_hill_climb', clearance=available)
            chart = field_chart(session.localizer, observation, anchor)
            scores = distances[nearby] - 1.5 * clearance[ys[nearby], xs[nearby]]
            for candidate in nearby[np.argsort(scores)]:
                target = np.array([xs[candidate], ys[candidate]])
                roi, point = target_projection(chart, observation, target)
                if not inside(point, FIELD_BOUNDS) or not inside(roi, (12, 12, 474, 420)):
                    continue
                enemies = np.asarray(observation.get('enemy_markers', []), float)
                if enemies.size and np.min(np.linalg.norm(enemies - target, axis=-1)) < 35:
                    continue
                try:
                    session.localizer.route(position, target)
                except ValueError:
                    continue
                write_progress(folder / f'reposition_{attempt + 1:02}.json',
                               dict(before=observation, target=target.tolist(), click=point.tolist()))
                emit(chapter=chapter, action='reposition_to_open_road', target=target.tolist())
                session.move(point)
                session.wait_stopped()
                observation = session.observe()
                goto.map_close(session.win)
                field = goto.capture_client(session.win)
                anchor = resolve_anchor(session, field, observation, allow_scene=False)
                break
            else:
                if available >= 35:
                    # 更好的位置不可达时接受当前合格站位，不因此判负。
                    break
                raise ValueError('No reachable open road for calibration.')
        offsets = [(40, 25), (-40, -25), (40, -25), (-40, 25), (0, 25), (0, -25),
                   (-15, -10), (15, -10), (0, 10)]
        for index, offset in enumerate(offsets):
            session.check()
            session.identity(field)
            anchor = resolve_anchor(session, field, observation, allow_scene=False)
            point = np.rint(anchor + offset).astype(int)
            if not inside(point, FIELD_BOUNDS):
                raise ValueError('Calibration click lies outside the field area.')
            chart = field_chart(session.localizer, observation, anchor)
            roi = project(np.linalg.inv(chart), point)
            predicted = project(observation['roi_to_map'], roi)
            before = np.asarray(observation['position'])
            segment = np.rint(np.linspace(before, predicted, 50)).astype(int)
            if (np.any(segment < 0) or np.any(segment >= clearance.shape[::-1])
                    or np.min(clearance[segment[:, 1], segment[:, 0]]) < 8):
                raise ValueError('Insufficient open road for two-direction calibration at current position.')
            enemies = np.asarray(observation.get('enemy_markers', []), float)
            if enemies.size and np.min(np.linalg.norm(enemies - predicted, axis=-1)) < 35:
                raise ValueError('Calibration target is too close to an enemy marker.')
            emit(chapter=chapter, sample=index + 1, click=point.tolist())
            runtime.write_image(folder / f'sample_{index + 1:02}_before.png', field)
            session.move(point)
            session.wait_stopped()
            after_observation = session.observe()
            after = np.asarray(after_observation['position'])
            if np.linalg.norm(after - before) < 2:
                raise ValueError('No measurable squad displacement after calibration click.')
            # Express the observed endpoint in the BEFORE view; camera recentering is not squad movement.
            endpoint_roi = project(np.linalg.inv(observation['roi_to_map']), after)
            source = (project(session.localizer.old_matrix, endpoint_roi)
                      - project(session.localizer.old_matrix, observation['player_roi']))
            samples.append([source.tolist(), (point - anchor).tolist()])
            evidence.append(dict(before=observation, after=after_observation, anchor=anchor.tolist(),
                                 click=point.tolist(), predicted_map=predicted.tolist()))
            write_progress(folder / 'samples.json', samples)
            write_progress(folder / 'observations.json', evidence)
            goto.map_close(session.win)
            field = goto.capture_client(session.win)
            runtime.write_image(folder / f'sample_{index + 1:02}_after.png', field)
            observation = after_observation
        fit = fit_calibration(samples[:6], samples[6:])
        if np.linalg.norm(np.asarray(fit['matrix'])[:2, 2]) > 8:
            raise ValueError('Calibration has excessive fixed anchor bias.')
        fit.update(status='validated', method='flat_old_projection_displacement', chapter=chapter,
                   difficulty='normal', client=[1776, 999], roi=[644, 280, 1130, 742],
                   binding=expected, projection=session.localizer.old_matrix.tolist(), samples=samples,
                   evidence=str(folder), scope='Measured local road and displacement support only.',
                   whole_chapter_verified=False, runtime_auto_loaded=False)
        write_progress(folder / 'calibration.json', fit)
        saved.parent.mkdir(parents=True, exist_ok=True)
        if saved.exists():
            saved.rename(saved.with_name(f'flat_calibration_{time.time_ns()}.json'))
        write_progress(saved, fit)
        session.finish_view()
        return dict(status='validated', samples=9, calibration=str(saved),
                    training_max_px=fit['training_max_px'], validation_max_px=fit['validation_max_px'])
    except (ValueError, RuntimeError, KeyError, TypeError, cv2.error):
        if session.win is not None:
            runtime.write_image(folder / 'failed_field.png', goto.capture_client(session.win))
        raise
    finally:
        if session.win is not None:
            session.win.close()


def transition(chapter, model, folder):
    settings.output = folder
    win = runtime.Window()
    try:
        win.focus()
        field = goto.capture_client(win)
        if goto.battle_popup_score(field) > .8 or chapter_number(field, model) != chapter:
            raise RuntimeError('Cannot verify safe field page before chapter transition.')
        win.reset_minimap(expanded=False)
        click(win, (1550, 944))
        wait_for_chapter(win, chapter - 1, model, folder, settings.stop_file)
    finally:
        win.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=int, required=True, help='当前普通章节，按章节号递减执行')
    parser.add_argument('--end', type=int, default=1, help='最后处理的章节（包含），单章测试请与 --start 相同')
    parser.add_argument('--output', type=Path, default=ROOT / 'log/chapter_calibration',
                        help='进度、截图、样本和 STOP 文件所在目录')
    args = parser.parse_args()
    if not 1 <= args.end <= args.start <= 48:
        parser.error('Expected 1 <= end <= start <= 48.')
    base = args.output.resolve()
    base.mkdir(parents=True, exist_ok=True)
    settings.stop_file = base / 'STOP'
    settings.output = base
    progress_path = base / 'progress.json'
    results = json.loads(progress_path.read_text('utf-8')) if progress_path.exists() else []
    from paddleocr import TextRecognition
    model = TextRecognition(model_name='PP-OCRv5_mobile_rec',
                            model_dir=str(ROOT / 'bin/paddleocr/PP-OCRv5_mobile_rec_infer'),
                            device='cpu', cpu_threads=2)
    with FileLock(ROOT / 'log/campaign_prototype/manual_movement.lock', timeout=0):
        for chapter in range(args.start, args.end - 1, -1):
            runtime.check_stop()
            folder = base / f'chapter_{chapter:02}' / str(time.time_ns())
            folder.mkdir(parents=True)
            record = dict(chapter=chapter, status='running', evidence=str(folder))
            results.append(record)
            write_progress(progress_path, results)
            emit(chapter=chapter, status='starting')
            try:
                record.update(calibrate(chapter, folder))
            except KeyboardInterrupt:
                record.update(status='cancelled')
                write_progress(progress_path, results)
                raise
            except Exception as error:
                record.update(status='failed', reason=f'{type(error).__name__}: {error}')
                (folder / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
            write_progress(progress_path, results)
            emit(**record)
            if chapter > args.end:
                try:
                    transition(chapter, model, folder)
                except (ValueError, RuntimeError, OSError) as error:
                    record['transition_error'] = f'{type(error).__name__}: {error}'
                    write_progress(progress_path, results)
                    raise
    emit(status='batch_finished', end_chapter=args.end)


if __name__ == '__main__':
    main()
