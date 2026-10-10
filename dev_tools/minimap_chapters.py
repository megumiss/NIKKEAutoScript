"""Collect normal chapter maps in descending order from an already open field page."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageGrab

if __package__:
    from .map_paths import DEFAULT_CAPTURE_ROOT
    from .minimap_reconstruct import DriverWindow, DriftScanner, parse_args, rebuild, terrain
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from map_paths import DEFAULT_CAPTURE_ROOT
    from minimap_reconstruct import DriverWindow, DriftScanner, parse_args, rebuild, terrain

from module.campaign.chapter import chapter_number


def screenshot(window):
    """在窗口尺寸及焦点有效时截取完整客户区，供章节 OCR 和页面稳定性检查。"""
    window.check()
    if window.gui.GetForegroundWindow() != window.hwnd:
        raise RuntimeError('Game lost focus during chapter transition.')
    x, y = window.gui.ClientToScreen(window.hwnd, (0, 0))
    return cv2.cvtColor(np.array(ImageGrab.grab(bbox=(x, y, x + 1776, y + 999), all_screens=True)),
                        cv2.COLOR_RGB2BGR)


def wait_for_chapter(window, chapter, model, output, stop=None):
    """同时确认章节号、紧凑小地图和道路稳定，加载最少等待八秒、最多两分钟。"""
    deadline = time.monotonic() + 120
    started = time.monotonic()
    previous = None
    stable = 0
    while time.monotonic() < deadline:
        if stop is not None and stop.exists():
            raise KeyboardInterrupt('Stopped by STOP file.')
        image = screenshot(window)
        number = chapter_number(image, model)
        compact = window.map_visible(image[123:277, 25:206])
        # Clouds and idle character animations never stop; compare minimap roads instead.
        scene = terrain(image[123:250, 25:206]) > 0
        still = previous is not None and np.mean(scene != previous) < 0.02
        stable = stable + 1 if number == chapter and compact and still else 0
        if stable >= 3 and time.monotonic() - started >= 8:
            cv2.imwrite(str(output / 'chapter_verified.png'), image)
            return
        previous = scene
        time.sleep(1)
    cv2.imwrite(str(output / 'transition_failed.png'), screenshot(window))
    raise RuntimeError(f'Chapter {chapter} did not reach a stable field page in 120 seconds.')


def click(window, point):
    """将客户区坐标换算为屏幕坐标，发送前校验窗口并检查驱动失败计数。"""
    window.check()
    if window.gui.GetForegroundWindow() != window.hwnd:
        raise RuntimeError('Game lost focus before chapter control click.')
    x, y = window.gui.ClientToScreen(window.hwnd, point)
    window.handler.mouse_click(x, y)
    if window.handler._failures:
        raise RuntimeError('Driver failed to click chapter control.')


def export_static(output, chapter):
    """将合格重建导出为底图、实拍图和绑定哈希的元数据，禁止覆盖既有标注坐标系。"""
    source = output / 'source'
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    if (output / 'map.json').exists() or (output / 'annotations.json').exists():
        raise RuntimeError('Static package already exists; preserve its annotation coordinate system.')
    if summary.get('registration', {}).get('status') == 'insufficient_joint_evidence':
        raise RuntimeError('Road registration failed; inspect source/registration.json before exporting a static map.')
    shutil.copyfile(source / 'reconstruction.png', output / 'map.png')
    shutil.copyfile(source / 'observed_mosaic.png', output / 'reference.png')
    digest = hashlib.sha256((output / 'map.png').read_bytes()).hexdigest()
    with np.load(source / 'map_data.npz') as cache:
        transforms = {key: cache[key].tolist() for key in
                      ('origin', 'projection', 'positions', 'warp_size', 'scan_to_map')}
    metadata = {
        'schema_version': 1, 'chapter': chapter, 'difficulty': 'normal',
        'image': 'map.png', 'image_sha256': digest, 'size': summary['canvas_size'],
        'coordinates': {'unit': 'pixel', 'origin': 'top_left', 'x': 'right', 'y': 'down'},
        'orientation': summary['orientation'], 'transforms': transforms,
        'crop': summary.get('crop'),
        'frame_to_map': 'project ROI pixel with projection, then add positions[frame_index] - origin',
        'capture': summary, 'reference_image': 'reference.png',
        'base_map': 'Reconstructed terrain; no added grid or marker overlays. Not manually verified.',
    }
    (output / 'map.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    (output / 'annotations.json').write_text(json.dumps({
        'schema_version': 1, 'image': 'map.png', 'image_sha256': digest,
        'coordinates': metadata['coordinates'], 'objects': [], 'connections': [],
    }, indent=2), encoding='utf-8')
    return summary


def write_progress(path, results):
    """通过同目录临时文件原子替换批次进度，避免留下半份 JSON。"""
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def existing_package(package, chapter, process_3d=False):
    """核对已有地图的章节、图片哈希与道路配准状态，决定能否跳过重复采集。"""
    if not (package / 'map.json').exists():
        return False
    metadata = json.loads((package / 'map.json').read_text(encoding='utf-8'))
    if metadata.get('chapter') != chapter:
        raise ValueError('Existing package belongs to a different chapter.')
    if hashlib.sha256((package / 'map.png').read_bytes()).hexdigest() != metadata['image_sha256']:
        raise ValueError('Existing map hash changed; preserve the package for inspection.')
    if metadata.get('processing_3d', False) != process_3d:
        raise ValueError('Existing package uses a different processing mode; choose a new output root.')
    if process_3d:
        if (metadata.get('map_render_mode') != 'projected_raw_regions'
                or metadata.get('capture', {}).get('status') != 'roads_exhausted'
                or not all((package / name).is_file() for name in
                           ['redraw_data.npz', 'surface_data.npz', 'reference.png', 'source/scan.json'])):
            raise ValueError('Incomplete 3D package; preserve it and choose a new output root.')
        return True
    if metadata['capture'].get('registration', {}).get('status') != 'joint_grid_road':
        raise ValueError('Existing package failed registration; use a new output root to recollect.')
    if metadata['capture']['status'] != 'roads_exhausted':
        raise ValueError('Existing package is incomplete; use a new output root to recollect.')
    return True


def archive_source(package):
    """把失败采集移动到本章 attempts 目录，先验证源与目标均位于该地图包内。"""
    source = (package / 'source').resolve()
    target = (package / 'attempts' / f'{time.time_ns()}').resolve()
    boundary = package.resolve()
    if not source.is_relative_to(boundary) or not target.is_relative_to(boundary):
        raise ValueError('Attempt archive must stay inside its chapter package.')
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)


def finish_scan(package, chapter, process_3d=False, stop=None):
    """仅重建已完成的同章扫描，再导出静态包，支持进程中断后的离线续跑。"""
    output = package / 'source'
    data = json.loads((output / 'scan.json').read_text(encoding='utf-8'))
    if data.get('chapter', chapter) != chapter:
        raise ValueError('Saved scan belongs to a different chapter.')
    if data.get('status') != 'roads_exhausted':
        raise ValueError(f"Scan is incomplete: {data.get('stop_reason', data.get('status'))}")
    if process_3d:
        return finish_scan_3d(package, chapter, data, stop)
    rebuild(output)
    return export_static(package, chapter)


def finish_scan_3d(package, chapter, scan, stop=None):
    """正式 3D 分支：分层重建、重访闭环、原帧区域重绘、最后发布元数据，保留各阶段证据。"""
    from dev_tools.minimap_layered import reconstruct
    from dev_tools.minimap_loops import close_loops
    from dev_tools.minimap_projected_redraw import redraw
    from dev_tools.minimap_surfaces import assign_surfaces

    if any((package / name).exists() for name in ['map.json', 'annotations.json']):
        raise ValueError('Preserve existing package and annotations; choose a new output root.')
    work = package / '.processing' / str(time.time_ns())
    work.mkdir(parents=True)

    def stage(name):
        if stop is not None and stop.exists():
            raise KeyboardInterrupt('Stopped by STOP file.')
        write_progress(package / 'processing_status.json', {'phase': name})
        print(f'Chapter {chapter}: {name}', flush=True)

    stage('layered')
    reconstruct(package / 'source', work / 'layered', stop_file=stop, depth_method='robust')
    stage('loops')
    baseline, loop_status = work / 'layered', None
    try:
        close_loops(work / 'layered', work / 'loops')
        baseline = work / 'loops'
    except ValueError as exc:
        # 重访对不足时沿用未闭环基线，并把原因写进元数据，避免静默降级。
        loop_status = {'status': 'skipped', 'reason': str(exc)}
        print(f'Chapter {chapter}: loop closure skipped: {exc}', flush=True)
    stage('surfaces')
    surface_status = None
    try:
        assign_surfaces(baseline, work / 'surfaces')
        baseline = work / 'surfaces'
    except ValueError as exc:
        # 没有足够局部平面时不影响重绘，只记录原因；移动端会退回单帧局部平面掩码。
        surface_status = {'status': 'skipped', 'reason': str(exc)}
        print(f'Chapter {chapter}: surface clustering skipped: {exc}', flush=True)
    stage('redraw')
    redraw(baseline, work / 'redrawn', regions=True, stop_file=stop)
    stage('export')
    final = work / 'redrawn'
    metadata = json.loads((final / 'map.json').read_text(encoding='utf-8'))
    summary = {'frames': len(scan['frames']), 'status': scan['status'], 'whole_camera_domain_verified': False,
               'processing_3d': True}
    metadata.update(chapter=chapter, processing_3d=True, capture=summary,
                    coordinates={'unit': 'pixel', 'origin': 'top_left', 'x': 'right', 'y': 'down'},
                    navigation_ready=False)
    if loop_status is not None:
        metadata['loop_closure'] = loop_status
    if surface_status is not None:
        metadata['surface_clustering'] = surface_status
    for item in final.iterdir():
        if item.name in ('source', 'map.json'):
            continue
        if item.is_dir():
            shutil.copytree(item, package / item.name, dirs_exist_ok=True)
        else:
            shutil.copyfile(item, package / item.name)
    stage('publishing')
    write_progress(package / 'map.json', metadata)
    write_progress(package / 'processing_status.json', {'phase': 'complete'})
    return summary


def capture_chapter(args, chapter, model, stop):
    """验证章节后采集，失败保留扫描记录；成功时最小化，异常时直接释放而不补发点击。"""
    window = None
    scanner = None
    try:
        window = DriverWindow(args)
        window.focus()
        window.reset_minimap(expanded=False)
        wait_for_chapter(window, chapter, model, args.output, stop)
        window.reset_minimap()
        scanner = DriftScanner(args, window)
        scanner.data['chapter'] = chapter
        scanner.explore_drift()
    except (Exception, KeyboardInterrupt) as exc:
        if scanner is not None:
            scanner.data.update(status='incomplete', stop_reason=str(exc))
            try:
                scanner.save()
            except Exception as save_error:
                print(f'Chapter {chapter}: could not save failed scan: {save_error}', flush=True)
        raise
    finally:
        if window is not None:
            try:
                if sys.exc_info()[0] is None:
                    window.reset_minimap(expanded=False)
            finally:
                window.close()


def move_to_previous(args, chapter, model, stop):
    """确认当前章节后点击上一章，等待新章节稳定并无条件释放驱动。"""
    window = DriverWindow(args)
    try:
        window.focus()
        window.reset_minimap(expanded=False)
        wait_for_chapter(window, chapter, model, args.output, stop)
        click(window, (1550, 944))
        wait_for_chapter(window, chapter - 1, model, args.output, stop)
    finally:
        window.close()


def run_batch(options, model):
    """按章节倒序执行有限重试、失败归档和续跑；切章不确定时停止批次并报告恢复位置。"""
    root = options.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    progress = root / 'progress.json'
    results = json.loads(progress.read_text(encoding='utf-8')) if progress.exists() else []
    stop = root / 'STOP'
    had_failure = False
    for chapter in range(options.start, options.end - 1, -1):
        if stop.exists():
            results.append({'chapter': chapter, 'status': 'stopped', 'reason': 'STOP file'})
            write_progress(progress, results)
            return 130
        package = root / f'chapter_{chapter:02d}'
        output = package / 'source'
        args = parse_args(['--output', str(output), '--driver-root', str(options.driver_root),
                           '--stroke-px', str(getattr(options, 'stroke_px', 120.0)),
                           '--keyframe-px', str(getattr(options, 'keyframe_px', 80.0)),
                           '--stop-file', str(stop)])
        record = {'chapter': chapter, 'output': str(package), 'attempts': [], 'status': 'pending'}
        results.append(record)
        try:
            if existing_package(package, chapter, getattr(options, 'process_3d', False)):
                record['status'] = 'existing'
            elif (package / 'annotations.json').exists() or (package / 'map.json').exists():
                raise ValueError('Preserve existing annotations/package; choose a new output root.')
            else:
                for attempt in range(options.retries + 1):
                    if stop.exists():
                        raise KeyboardInterrupt('Stopped by STOP file.')
                    entry = {'attempt': attempt + 1, 'phase': 'capture'}
                    record['attempts'].append(entry)
                    write_progress(progress, results)
                    try:
                        # A completed scan can survive a process crash before rebuild/export.
                        summary = None
                        process_3d = getattr(options, 'process_3d', False)
                        if (output / 'scan.json').exists() and (attempt == 0 or process_3d):
                            entry['phase'] = 'resume_rebuild'
                            try:
                                summary = finish_scan(package, chapter, getattr(options, 'process_3d', False), stop)
                            except Exception as exc:
                                entry['resume_error'] = f'{type(exc).__name__}: {exc}'
                                if (package / 'map.json').exists() or (package / 'annotations.json').exists():
                                    raise
                                try:
                                    saved_scan = json.loads((output / 'scan.json').read_text(encoding='utf-8'))
                                except ValueError:
                                    saved_scan = {}
                                if process_3d and saved_scan.get('status') == 'roads_exhausted':
                                    raise
                        if summary is None:
                            if output.exists():
                                archive_source(package)
                            output.mkdir(parents=True)
                            entry['phase'] = 'capture'
                            write_progress(progress, results)
                            capture_chapter(args, chapter, model, stop)
                            entry['phase'] = 'rebuild'
                            write_progress(progress, results)
                            summary = finish_scan(package, chapter, getattr(options, 'process_3d', False), stop)
                        record.update(status='captured', frames=summary['frames'],
                                      whole_camera_domain_verified=summary['whole_camera_domain_verified'])
                        entry['status'] = 'captured'
                        break
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        if stop.exists():
                            raise KeyboardInterrupt('Stopped by STOP file.') from exc
                        # Isolate chapter failures, including OpenCV/OCR/driver errors, at the batch boundary.
                        entry.update(status='failed', error=f'{type(exc).__name__}: {exc}',
                                     traceback=traceback.format_exc())
                        record['status'] = 'failed'
                        print(json.dumps({'chapter': chapter, **entry}), flush=True)
                        write_progress(progress, results)
                        if (package / 'map.json').exists() or (package / 'annotations.json').exists():
                            break
        except KeyboardInterrupt as exc:
            record.update(status='stopped', error=str(exc))
            write_progress(progress, results)
            return 130
        except Exception as exc:
            record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        had_failure |= record['status'] == 'failed'
        write_progress(progress, results)
        print(json.dumps({'chapter': chapter, 'status': record['status']}), flush=True)
        if chapter > options.end:
            try:
                output.mkdir(parents=True, exist_ok=True)
                move_to_previous(args, chapter, model, stop)
            except KeyboardInterrupt as exc:
                record['transition'] = {'status': 'stopped', 'error': str(exc)}
                write_progress(progress, results)
                return 130
            except Exception as exc:
                record['transition'] = {'status': 'paused', 'error': f'{type(exc).__name__}: {exc}',
                                        'resume_start': chapter}
                write_progress(progress, results)
                print(f'Chapter {chapter}: transition unverified; batch paused: {exc}', flush=True)
                return 2
    return 1 if had_failure else 0


def main(argv=None):
    """初始化章节 OCR 与批次参数，将取消、批次失败和初始化失败映射为明确退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=int, required=True)
    parser.add_argument('--end', type=int, default=1)
    parser.add_argument('--output', type=Path, default=DEFAULT_CAPTURE_ROOT,
                        help='Capture directory (default: data/chapter_maps/local/captures).')
    parser.add_argument('--driver-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--retries', type=int, default=2, help='Additional attempts per failed chapter.')
    parser.add_argument('--process-3d', action='store_true',
                        help='Use layered reconstruction and raw-region redraw; omit for flat maps.')
    parser.add_argument('--stroke-px', type=float, default=120.0, help='Cursor travel per drag (default: 120).')
    parser.add_argument('--keyframe-px', type=float, default=80.0,
                        help='Tracked camera travel between saved frames (default: 80).')
    options = parser.parse_args(argv)
    if not 1 <= options.end <= options.start <= 99:
        parser.error('Expected 1 <= end <= start <= 99.')
    if options.retries < 0:
        parser.error('--retries must be nonnegative.')
    if not 0 < options.stroke_px <= 240 or not 0 < options.keyframe_px < float('inf'):
        parser.error('Expected 0 < --stroke-px <= 240 and a positive finite --keyframe-px.')
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    try:
        from paddleocr import TextRecognition

        model = TextRecognition(model_name='PP-OCRv5_mobile_rec',
                                model_dir=str(options.driver_root / 'bin/paddleocr/PP-OCRv5_mobile_rec_infer'),
                                device='cpu', cpu_threads=2)
        return run_batch(options, model)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'Batch initialization/progress storage failed: {type(exc).__name__}: {exc}', flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
