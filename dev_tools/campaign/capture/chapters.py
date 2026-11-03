"""Collect normal chapter maps in descending order from an already open field page."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageGrab

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dev_tools.campaign.capture.reconstruct import DriverWindow, DriftScanner, parse_args, rebuild

from module.campaign.vision.chapter import chapter_number
from module.campaign.vision.minimap import map_visible, terrain
from module.campaign.map.paths import CHAPTER_NAMES_FILE, DEFAULT_CAPTURE_ROOT


def screenshot(window):
    """在窗口尺寸及焦点有效时截取完整客户区，供章节 OCR 和页面稳定性检查。"""
    window.check()
    if window.gui.GetForegroundWindow() != window.hwnd:
        raise RuntimeError('Game lost focus during chapter transition.')
    x, y = window.gui.ClientToScreen(window.hwnd, (0, 0))
    return cv2.cvtColor(np.array(ImageGrab.grab(bbox=(x, y, x + 1776, y + 999), all_screens=True)),
                        cv2.COLOR_RGB2BGR)


def wait_for_chapter(window, chapter, model, output, stop=None):
    """同时确认章节号、紧凑小地图和道路稳定，加载最少等待八秒、最多两分钟。

    超时错误里带上最后一次读到的章节号，便于判断实际进到了哪一章。
    """
    deadline = time.monotonic() + 120
    started = time.monotonic()
    previous = None
    stable = 0
    seen = None
    while time.monotonic() < deadline:
        if stop is not None and stop.exists():
            raise KeyboardInterrupt('Stopped by STOP file.')
        image = screenshot(window)
        number = chapter_number(image, model)
        seen = number if number is not None else seen
        compact = map_visible(image[123:277, 25:206])
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
    raise RuntimeError(f'Chapter {chapter} did not reach a stable field page in 120 seconds '
                       f'(last readable chapter: {seen}).')


def click(window, point):
    """将客户区坐标换算为屏幕坐标，发送前校验窗口并检查驱动失败计数。"""
    window.check()
    if window.gui.GetForegroundWindow() != window.hwnd:
        raise RuntimeError('Game lost focus before chapter control click.')
    x, y = window.gui.ClientToScreen(window.hwnd, point)
    window.handler.mouse_click(x, y)
    if window.handler._failures:
        raise RuntimeError('Driver failed to click chapter control.')


# 章节列表（CHAPTER LIST）窗口几何：条目名字在两列间锯齿交替，序章（章节 0）在最上方的左列。
# 条目左侧的小号数字标签实测无法 OCR（PP-OCRv5 识别分数 <0.2），章节身份一律按大字名字识别。
LIST_AREA = (620, 60, 1140, 900)
LIST_TOP_Y = 200.0
LIST_PITCH = 94.0
LIST_INDICATOR = (1635, 935)
DIFFICULTY_TABS = {'story': (770, 878), 'normal': (887, 878), 'hard': (1017, 878)}
# 标定值：列表贴顶时 00 章名字中心 200、相邻章行距 94.0。
# 名字牌分两列锯齿交替：偶数章在左列、奇数章在右列，下面是含留白的整块横向范围。
LIST_COLUMN_X = {0: (660, 816), 1: (960, 1130)}
# 滚动参数：手势起止点只按客户区 y 计算，落点取列表中部，上下都留出可点空间。
LIST_DRAG_X = 745
LIST_TARGET_Y = 500.0
# 驱动的拖动时长封顶 0.6s（VirtualMouseInput.mouse_swipe），一次手势能走多远只由位移决定。
LIST_DRAG_LIMIT = 700.0
LIST_CLICK_BAND = (170.0, 800.0)
# 不预测惯性：内容位移对手势位移的放大并不稳定，所以一律固定步长扫。这个 2 只当保护用——
# 请求位移取剩余距离的一半，即使碰到放大到 2 倍的惯性，内容也追不到目标行以外，不会来回震荡。
LIST_STEP_GUARD = 2.0
LIST_POLL_INTERVAL = 0.25
LIST_STILL_TIMEOUT = 4.0
LIST_STILL_DIFF = 0.5
# 面板左上角固定的「CHAPTER LIST」标题：标题只属于面板，开/关判据不受当前地图明暗影响。
LIST_TITLE = (640, 96, 900, 146)
LIST_TITLE_SIMILARITY = 0.6
LIST_OPEN_TRIES = 3
LIST_OPEN_PROBE = 1.0
LIST_OPEN_TIMEOUT = 6.0
LIST_CLOSE_TIMEOUT = 8.0
# 目标行条带半高只包住名字牌，避开下方的 COMPLETE 条。
LIST_ROW_HALF = 22.0
LIST_ROW_SCORE = 0.60
LIST_NAME_SIMILARITY = 0.5
LIST_CLICK_TRIES = 3
LIST_EDGE_CONFIRM = 2
LIST_SCROLL_TRIES = 12


def load_chapter_names():
    """读取章节号到列表条目名字的映射；文件缺章时拒绝切换而不是猜位置。"""
    data = json.loads(CHAPTER_NAMES_FILE.read_text(encoding='utf-8'))
    return {int(key): value for key, value in data.items() if key.isdigit()}


def list_entry_y(chapter):
    """章节号到列表内容的绝对行坐标（列表贴顶时名字中心 y）；列位置锯齿交替不影响滚动计算。

    行坐标与滚动位置无关，所以同一帧任意可见条目都能反推滚动偏移；标定后贴顶时 00 章的
    偏移为 0，可以直接当作列表到顶的判据。
    """
    return LIST_TOP_Y + chapter * LIST_PITCH


def list_offset(entries):
    """由可见条目名字反推列表滚动偏移：0 表示列表停在最上方，正值表示内容已上移。

    条目绝对行坐标来自 list_entry_y，与滚动位置无关，所以同一帧里任意可见条目都能反推
    同一个偏移；取中位数抵抗个别识别框的纵向偏差。调用方须先确认 entries 非空。
    """
    return float(np.median([list_entry_y(chapter) - y for chapter, (_, y) in entries.items()]))


def grab_region(window, area):
    """抓客户区里的指定矩形（BGR）；比整屏截图快一个数量级，用于小块判据轮询。"""
    window.check()
    origin = window.gui.ClientToScreen(window.hwnd, (0, 0))
    x1, y1, x2, y2 = area
    image = np.array(ImageGrab.grab(bbox=(origin[0] + x1, origin[1] + y1, origin[0] + x2, origin[1] + y2),
                                    all_screens=True))[:, :, :3]
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def grab_list_gray(window):
    """列表区域灰度，供惯性静止判定用。"""
    return cv2.cvtColor(grab_region(window, LIST_AREA), cv2.COLOR_BGR2GRAY).astype(np.int16)


def list_panel_open(crop, rec):
    """判断一小块图像里是不是面板标题「CHAPTER LIST」。

    标题的识别分数会低到不可用，所以只按字母相似度比对，不看识别分数。
    """
    result = next(iter(rec.predict(cv2.resize(crop, None, fx=3, fy=3,
                                              interpolation=cv2.INTER_CUBIC))), None)
    text = '' if result is None else str(result.get('rec_text', '')).upper()
    letters = ''.join(char for char in text if char.isalpha())
    return SequenceMatcher(None, letters, 'CHAPTERLIST').ratio() >= LIST_TITLE_SIMILARITY


def wait_for_list_open(window, rec, timeout=LIST_OPEN_TIMEOUT):
    """等章节列表面板显示出来，返回是否等到。

    面板有打开动画，点完立刻读会读不到标题，所以按 LIST_POLL_INTERVAL 轮询标题文字；
    标题只属于面板，判断开关不受当前地图明暗影响。
    """
    deadline = time.monotonic() + timeout
    while True:
        if list_panel_open(grab_region(window, LIST_TITLE), rec):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(LIST_POLL_INTERVAL)


def wait_for_list_still(window, timeout=LIST_STILL_TIMEOUT):
    """等章节列表的惯性停下，返回静止时的列表区域灰度。

    连续两帧列表区域几乎不变才算停住：惯性没停就截图，点击会落在条目移走后的空白或
    下方 COMPLETE 条上。判据只看整块区域的帧间差，锁定章节那种读不出名字的模糊底图
    同样能判定。
    """
    deadline = time.monotonic() + timeout
    region = grab_list_gray(window)
    while time.monotonic() < deadline:
        time.sleep(LIST_POLL_INTERVAL)
        current = grab_list_gray(window)
        if float(np.mean(np.abs(current - region))) < LIST_STILL_DIFF:
            return current
        region = current
    return region


def read_row_name(image, chapter, offset, rec):
    """按行距推算的行位置直接识别指定章节的名字，绕过检测模型整行漏框。

    检测模型会整块漏掉浅色底图上的名字牌，而行距是均匀的，所以按 list_entry_y 推算出的
    位置裁一条窄带直接送识别模型。返回 (行中心 y, 识别文本, 识别分数)；条带不含 COMPLETE
    条，offset 为该帧的滚动偏移。
    """
    y = list_entry_y(chapter) - offset
    x1, x2 = LIST_COLUMN_X[chapter % 2]
    crop = image[int(y - LIST_ROW_HALF):int(y + LIST_ROW_HALF), x1:x2]
    if crop.size == 0:
        return y, '', 0.0
    result = next(iter(rec.predict(cv2.resize(crop, None, fx=3, fy=3,
                                              interpolation=cv2.INTER_CUBIC))), None)
    if result is None:
        return y, '', 0.0
    return y, str(result.get('rec_text', '')).strip(), float(result.get('rec_score', 0))


def name_matches(text, expected):
    """名字比对：识别结果与目标章名字足够相似即认为读的就是目标行。

    只用来确认「按行距推算的同一行」是不是目标章，不做跨行识别，所以容忍一两个字的
    识别偏差，按相似度而不是全等比对。
    """
    return SequenceMatcher(None, text, expected).ratio() >= LIST_NAME_SIMILARITY


def wait_for_list_closed(window, det, rec, names, timeout=LIST_CLOSE_TIMEOUT):
    """点条目后轮询列表区域，读不出任何章节名即认为点击生效（面板收起或已进入加载）。

    以章节名为凭据，被弹窗之类的东西挡住时也不会误判成「已收起」。
    """
    deadline = time.monotonic() + timeout
    while True:
        if not read_list_names(screenshot(window), det, rec, names):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(LIST_POLL_INTERVAL)


def read_list_names(image, det, rec, names):
    """识别列表区域里的条目名字，返回 {章节号: (x, y)}（客户区坐标）。

    检测模型在列表区域找文本框，识别模型逐框读字；只保留名字表内的精确匹配且
    识别分数 >=0.85 的结果，COMPLETE 进度条、数字进度和小号标签都被名字表天然过滤。
    """
    x1, y1, x2, y2 = LIST_AREA
    area = image[y1:y2, x1:x2]
    result = next(iter(det.predict(area)), None)
    if result is None:
        return {}
    by_name = {name: chapter for chapter, name in names.items()}
    found = {}
    boxes = result['dt_polys']
    for box, score in zip(boxes, result.get('dt_scores', [1.0] * len(boxes))):
        if score < 0.5:
            continue
        bx1, by1, bx2, by2 = box[:, 0].min(), box[:, 1].min(), box[:, 0].max(), box[:, 1].max()
        crop = area[int(by1):int(by2), int(bx1):int(bx2)]
        if crop.size == 0:
            continue
        r = next(iter(rec.predict(cv2.resize(crop, None, fx=3, fy=3,
                                             interpolation=cv2.INTER_CUBIC))), None)
        if r is None:
            continue
        chapter = by_name.get(r.get('rec_text', '').strip())
        if chapter is None or r.get('rec_score', 0) < 0.85:
            continue
        found[chapter] = (float((bx1 + bx2) / 2 + x1), float((by1 + by2) / 2 + y1))
    return found


def drag_list(window, delta):
    """在章节列表中部拖动一次，delta 为正表示把内容上移（看到更靠后的章节）。

    返回实际生效的位移：delta 按 LIST_DRAG_LIMIT 截断，起止点始终落在列表可视区内，
    避免手指滑出列表后被别处的控件接管手势。位移小于 1 像素时不发送手势。
    """
    delta = max(-LIST_DRAG_LIMIT, min(LIST_DRAG_LIMIT, float(delta)))
    if abs(delta) < 1:
        return 0.0
    origin = window.gui.ClientToScreen(window.hwnd, (0, 0))
    half = delta / 2
    window.handler.mouse_swipe((origin[0] + LIST_DRAG_X, origin[1] + LIST_TARGET_Y + half),
                               (origin[0] + LIST_DRAG_X, origin[1] + LIST_TARGET_Y - half))
    return delta


def switch_to_chapter(window, target, det, rec, output, stop=None):
    """通过章节列表直达目标章节：点开列表、按名字定位条目、点击进入并等田野页就绪。

    滚动方式：不做惯性预测，一律固定步长扫。列表打开时停在剧情进度那一章附近，离目标可能有
    两三屏，所以远距离每步都用满 LIST_DRAG_LIMIT；接近目标时把这一步收窄到剩余距离的一半
    （LIST_STEP_GUARD），内容位移即使被惯性放大也够不到目标行以外，落点只会偏短，由下一步补齐。
    方向每轮都由可见条目名字反查的偏移（list_offset）重新判定，滑过头会自动反向；一帧都读不出
    名字（可见章节全被锁定）时才退回按当前章节与目标的先后选方向，固定步长继续扫。
    目标一进入可点击带就结束。
    按下前必须认到目标章的名字：优先用检测出的同名条目，检测整行漏框时退回到按行距推算的
    位置直接识别那一行；点完以列表收起为凭据，最后再等田野页确认章节号。
    列表两端这样识别：顶部看偏移是否归零（贴顶时 00 章回到 LIST_TOP_Y），底部看这次滑动是否
    已经推不动列表（区域帧间差归零）；已经贴住目标方向的头仍点不到条目时，连续两次确认后
    立即按「不可达」报错。
    点空（列表收起失败）会重新测量再点，最多 LIST_CLICK_TRIES 次。
    目标就是当前章时直接返回（当前章条目带"正在执行作战"横幅，点击无响应）。
    前置条件：游戏停在任一主线田野页（右下角有章节指示），窗口为 1776×999。
    """
    names = load_chapter_names()
    if target not in names:
        raise ValueError(f'Chapter {target} has no known name; chapter_names.json covers '
                         f'{min(names)}-{max(names)}.')
    current = chapter_number(screenshot(window), rec)
    if current is None:
        raise RuntimeError('Chapter indicator unreadable; open a main-story field page first.')
    if current == target:
        print(json.dumps({'switch': target, 'status': 'already_there'}), flush=True)
        return
    # 章节指示器是开/关切换按钮：先短等一下确认面板是否已经开着（上一次运行可能留下了开着的
    # 面板），没开才点；点完必须轮询到面板真的渲染出来，否则连续点击会把面板又关掉。
    opened = wait_for_list_open(window, rec, timeout=LIST_OPEN_PROBE)
    for _ in range(LIST_OPEN_TRIES):
        if opened:
            break
        click(window, LIST_INDICATOR)
        opened = wait_for_list_open(window, rec)
    if not opened:
        raise RuntimeError('Chapter list did not open after clicking the chapter indicator.')
    # 难度标签：普通未选中（中心像素不是亮蓝）时先切回普通，否则点到的条目是其他难度的章。
    image = screenshot(window)
    if image[DIFFICULTY_TABS['normal'][1], DIFFICULTY_TABS['normal'][0]][0] < 200:
        click(window, DIFFICULTY_TABS['normal'])
        time.sleep(1.0)
    region = wait_for_list_still(window)
    image = screenshot(window)
    previous_region, previous_offset, last_drag = None, None, 0.0
    edge, edge_hits, click_tries, swipes = None, 0, 0, 0
    for _ in range(LIST_SCROLL_TRIES):
        if stop is not None and stop.exists():
            raise KeyboardInterrupt('Stopped by STOP file.')
        entries = read_list_names(image, det, rec, names)
        measured = list_offset(entries) if entries else None
        # 读不出名字（章节锁定后名字牌是模糊底图）时按上次偏移加上已滑动的步长继续推算方向。
        # 步长按手指位移计，比内容实际位移小（惯性只在放大），所以推算值落后于真实位置，
        # 只会多滑一两步，不会把方向判反。
        offset = measured if measured is not None else (
            None if previous_offset is None else previous_offset + last_drag)
        clicked = target in entries and LIST_CLICK_BAND[0] < entries[target][1] < LIST_CLICK_BAND[1]
        matched = names[target]
        if clicked:
            x, y = entries[target]
        elif offset is not None and LIST_CLICK_BAND[0] < list_entry_y(target) - offset < LIST_CLICK_BAND[1]:
            # 检测模型会整行漏框：目标行的推算位置已经在可点带里却读不到时，按行位置直接识别
            # 这一行来确认，确认到才按推算坐标点，避免为了读不出名字的条目反复空滑。
            y, text, score = read_row_name(image, target, offset, rec)
            clicked = score >= LIST_ROW_SCORE and name_matches(text, names[target])
            x, matched = sum(LIST_COLUMN_X[target % 2]) / 2, text
        if clicked:
            click(window, (int(x), int(y)))
            if wait_for_list_closed(window, det, rec, names):
                wait_for_chapter(window, target, rec, output, stop)
                print(json.dumps({'switch': f'{current}->{target}', 'status': 'done', 'row': matched,
                                  'click': [int(x), int(y)], 'swipes': swipes}), flush=True)
                return
            click_tries += 1
            if click_tries >= LIST_CLICK_TRIES:
                raise RuntimeError(f'Clicking chapter {target} did not close the chapter list; '
                                   f'the entry keeps moving out from under the click.')
            previous_region, previous_offset, last_drag = None, None, 0.0
            region = wait_for_list_still(window)
            image = screenshot(window)
            continue
        at_top = offset is not None and offset <= LIST_PITCH / 2
        stalled = (previous_region is not None and last_drag
                   and float(np.mean(np.abs(region - previous_region))) < LIST_STILL_DIFF)
        reached = ('bottom' if last_drag > 0 else 'top') if stalled else ('top' if at_top else None)
        if offset is None:
            # 首帧就读不出条目（可见章节全部锁定）：按当前章节与目标的先后选方向，固定步长扫过去。
            drag = LIST_DRAG_LIMIT if target > current else -LIST_DRAG_LIMIT
        else:
            remaining = list_entry_y(target) - offset - LIST_TARGET_Y
            drag = (1 if remaining >= 0 else -1) * min(LIST_DRAG_LIMIT, abs(remaining) / LIST_STEP_GUARD)
        facing = 'top' if drag < 0 else 'bottom'
        if reached == facing:
            # 单帧可能只是名字没读出来，连续两帧判到同一侧才认定目标不可达。
            edge_hits = edge_hits + 1 if edge == facing else 1
            edge = facing
            if edge_hits >= LIST_EDGE_CONFIRM:
                raise RuntimeError(f'Chapter {target} is not reachable past the list {facing} '
                                   f'(offset {offset if offset is None else round(offset)}); check '
                                   f'chapter_names.json, that row\'s OCR and LIST_CLICK_BAND.')
        else:
            edge, edge_hits = None, 0
        previous_region, previous_offset = region, offset
        last_drag = drag_list(window, drag)
        swipes += 1 if last_drag else 0
        region = wait_for_list_still(window)
        image = screenshot(window)
    raise RuntimeError(f'Chapter {target} did not become clickable after {LIST_SCROLL_TRIES} '
                       f'scroll corrections.')


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
    # 写 bytes：文本模式在 Windows 上翻成 CRLF，标注文件在仓库里会变成全文件 churn。
    (output / 'annotations.json').write_bytes(json.dumps({
        'schema_version': 1, 'image': 'map.png', 'image_sha256': digest,
        'coordinates': metadata['coordinates'], 'objects': [], 'connections': [],
    }, indent=2).encode('utf-8'))
    return summary


def write_progress(path, results):
    """通过同目录临时文件原子替换批次进度，避免留下半份 JSON。"""
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def existing_package(package, chapter):
    """核对已有地图的章节、图片哈希与道路配准状态，决定能否跳过重复采集。"""
    if not (package / 'map.json').exists():
        return False
    metadata = json.loads((package / 'map.json').read_text(encoding='utf-8'))
    if metadata.get('chapter') != chapter:
        raise ValueError('Existing package belongs to a different chapter.')
    if hashlib.sha256((package / 'map.png').read_bytes()).hexdigest() != metadata['image_sha256']:
        raise ValueError('Existing map hash changed; preserve the package for inspection.')
    if metadata.get('coordinate_model') is not None or metadata.get('processing_3d'):
        raise ValueError('Existing package uses a different processing mode; choose a new output root.')
    if metadata['capture'].get('registration', {}).get('status') != 'joint_grid_road':
        raise ValueError('Existing package failed registration; use a new output root to recollect.')
    if metadata['capture']['status'] != 'roads_exhausted':
        raise ValueError('Existing package is incomplete; use a new output root to recollect.')
    return True


def existing_capture(package, chapter):
    """只采集模式的跳过判据：本章扫描已完整即可跳过，重建与导出留给离线步骤。

    与 existing_package 的区别是不要求 map.json —— 只采集模式下地图包尚未导出。
    扫描记录属于其他章节时直接报错，避免把别章的采集当成本章成果。
    """
    scan = package / 'source/scan.json'
    if not scan.exists():
        return False
    data = json.loads(scan.read_text(encoding='utf-8'))
    if data.get('chapter', chapter) != chapter:
        raise ValueError('Existing scan belongs to a different chapter.')
    return data.get('status') == 'roads_exhausted'


def archive_source(package):
    """把失败采集移动到本章 attempts 目录，先验证源与目标均位于该地图包内。"""
    source = (package / 'source').resolve()
    target = (package / 'attempts' / f'{time.time_ns()}').resolve()
    boundary = package.resolve()
    if not source.is_relative_to(boundary) or not target.is_relative_to(boundary):
        raise ValueError('Attempt archive must stay inside its chapter package.')
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)


def finish_scan(package, chapter, stop=None):
    """仅重建已完成的同章扫描，再导出静态包，支持进程中断后的离线续跑。"""
    output = package / 'source'
    data = json.loads((output / 'scan.json').read_text(encoding='utf-8'))
    if data.get('chapter', chapter) != chapter:
        raise ValueError('Saved scan belongs to a different chapter.')
    if data.get('status') != 'roads_exhausted':
        raise ValueError(f"Scan is incomplete: {data.get('stop_reason', data.get('status'))}")
    rebuild(output)
    return export_static(package, chapter)


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
    """按章节倒序执行有限重试、失败归档和续跑；切章不确定时停止批次并报告恢复位置。

    options.capture_only 为真时只保存扫描记录，不重建也不导出地图包：进度里记
    `rebuild: deferred`，跳过判据改用扫描完整性（见 existing_capture），重建由离线步骤
    对 `chapter_XX/source/scan.json` 补齐。
    """
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
        argv = ['--output', str(output), '--driver-root', str(options.driver_root),
                '--stroke-px', str(getattr(options, 'stroke_px', 120.0)),
                '--keyframe-px', str(getattr(options, 'keyframe_px', 80.0)),
                '--stop-file', str(stop)]
        if getattr(options, 'client', None):
            # 游戏窗口已被外部调成非默认尺寸且权限不允许改写时，按实际客户区采集。
            argv += ['--client', str(options.client[0]), str(options.client[1])]
        if getattr(options, 'keep_window_position', False):
            argv += ['--keep-window-position']
        args = parse_args(argv)
        record = {'chapter': chapter, 'output': str(package), 'attempts': [], 'status': 'pending'}
        results.append(record)
        capture_only = getattr(options, 'capture_only', False)
        try:
            if existing_capture(package, chapter) if capture_only else existing_package(package, chapter):
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
                        if not capture_only and (output / 'scan.json').exists() and attempt == 0:
                            entry['phase'] = 'resume_rebuild'
                            try:
                                summary = finish_scan(package, chapter, stop)
                            except Exception as exc:
                                entry['resume_error'] = f'{type(exc).__name__}: {exc}'
                                if (package / 'map.json').exists() or (package / 'annotations.json').exists():
                                    raise
                        if summary is None:
                            if output.exists():
                                archive_source(package)
                            output.mkdir(parents=True)
                            entry['phase'] = 'capture'
                            write_progress(progress, results)
                            capture_chapter(args, chapter, model, stop)
                            if not capture_only:
                                entry['phase'] = 'rebuild'
                                write_progress(progress, results)
                                summary = finish_scan(package, chapter, stop)
                        if capture_only:
                            # 帧数取自扫描记录；覆盖判定来自重建摘要，离线重建后才有。
                            scan = json.loads((output / 'scan.json').read_text(encoding='utf-8'))
                            record.update(status='captured', frames=len(scan['frames']), rebuild='deferred')
                        else:
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


def goto_chapter(options, model, det):
    """--goto 模式：建立受控窗口，经章节列表切换到目标章并等待田野页就绪后退出。

    输出目录仅用于 wait_for_chapter 的验证截图；切换失败时异常原样传播为退出码 2。
    """
    root = options.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    argv = ['--output', str(root / 'goto'), '--driver-root', str(options.driver_root)]
    if getattr(options, 'client', None):
        argv += ['--client', str(options.client[0]), str(options.client[1])]
    if getattr(options, 'keep_window_position', False):
        argv += ['--keep-window-position']
    window = DriverWindow(parse_args(argv))
    try:
        window.focus()
        window.reset_minimap(expanded=False)
        switch_to_chapter(window, options.goto, det, model, root, stop=root / 'STOP')
    finally:
        window.close()
    return 0


def main(argv=None):
    """初始化章节 OCR 与批次参数，将取消、批次失败和初始化失败映射为明确退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=int, default=None)
    parser.add_argument('--end', type=int, default=1)
    parser.add_argument('--goto', type=int, default=None, metavar='CHAPTER',
                        help='Switch to the chapter via the chapter list and exit (no capture). '
                             'Overrides --start/--end.')
    parser.add_argument('--output', type=Path, default=DEFAULT_CAPTURE_ROOT,
                        help='Capture directory (default: data/chapter_maps/local/captures).')
    parser.add_argument('--driver-root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--retries', type=int, default=2, help='Additional attempts per failed chapter.')
    parser.add_argument('--capture-only', action='store_true',
                        help='Save scans without rebuilding or exporting; run the offline rebuild '
                             'afterwards (keeps the game session free of rebuild CPU time).')
    parser.add_argument('--stroke-px', type=float, default=120.0, help='Cursor travel per drag (default: 120).')
    parser.add_argument('--keyframe-px', type=float, default=80.0,
                        help='Tracked camera travel between saved frames (default: 80).')
    parser.add_argument('--client', type=int, nargs=2, default=None,
                        help='Expected game client size (default: 1776x999; pass the actual size '
                             'when the window cannot be resized by this process).')
    parser.add_argument('--keep-window-position', action='store_true',
                        help='Keep the current window position when the client size already matches '
                             '(for an elevated game this process may not move the window).')
    options = parser.parse_args(argv)
    if options.goto is None:
        if options.start is None:
            parser.error('--start is required unless --goto is given.')
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
        if options.goto is not None:
            from paddleocr import TextDetection

            det = TextDetection(model_name='PP-OCRv5_mobile_det',
                                model_dir=str(options.driver_root / 'bin/paddleocr/PP-OCRv5_mobile_det_infer'),
                                device='cpu', cpu_threads=2)
            return goto_chapter(options, model, det)
        return run_batch(options, model)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'Batch initialization/progress storage failed: {type(exc).__name__}: {exc}', flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
