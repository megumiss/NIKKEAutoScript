"""Inventory campaign captures and prepare reviewable YOLO annotations without controlling the game."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
LABELS = ['scene_squad_arrow', 'scene_collectible_indicator', 'minimap_enemy_normal',
          'minimap_enemy_ex', 'minimap_squad_ring', 'scene_ground_mechanism_off',
          'scene_ground_mechanism_on', 'scene_elevator_start', 'scene_elevator_end']
DEFAULT_ROOTS = ['data/chapter_maps', 'log/campaign_prototype', 'log/chapter_maps',
                 'log/chapter_calibration_20261001', 'log/chapter_calibration_restart_20261001',
                 'tmp/arrow_anchor_probe', 'tmp/arrow_fix', 'tmp/ch33_calibration_test',
                 'tmp/ch34_fragments_20260929', 'tmp/ch40_movement_20260930',
                 'tmp/formal_3d_validation', 'tmp/map_followup', 'tmp/map_movement_validation',
                 'tmp/minimap_full_20260925', 'tmp/minimap_full_20260925_run2']
EXCLUDE = re.compile(r'annotat|alignment|mosaic|reconstruct|coverage|preview|overlay|mask|_plan|_match', re.I)


def read_jsonl(path):
    """读取 JSONL，跳过空行。

    清单动辄上万条，用 JSONL 而不是单个 JSON 数组，是为了能边跑边追加、中断后还能续。
    """
    return [json.loads(line) for line in Path(path).read_text('utf-8').splitlines() if line.strip()]


def write_jsonl(path, records):
    """整体重写 JSONL；追加写请直接 open(mode='a')，不要先读再全量回写。"""
    with Path(path).open('w', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')


def chapter_from_path(path):
    """从采集路径里猜章节号，猜不出返回 None。

    采集目录命名不统一（chapter_33、ch33、chapter33 都有），所以用宽松的正则；
    取最后一个匹配是因为路径前段常含无关数字，而章节目录通常在末端。
    """
    found = re.findall(r'(?:chapter_?|\bch)(\d{1,2})(?!\d)', str(path).replace('\\', '/'), re.I)
    return int(found[-1]) if found else None


def inventory(output, roots):
    """扫描历史采集目录，整理出可用于标注的截图清单。

    只收两种尺寸：1776×999 的客户区和 486×462 的展开小地图 ROI，其它尺寸的图不是原始采集。
    文件名含 annotat/overlay/mask 等字样的是诊断叠图，按名字先排掉——这些图上画着旧的检测框，
    当成采集数据会把标注污染。
    同时按像素哈希去重：同一帧常被多个实验目录各存一份。
    """
    output.mkdir(parents=True, exist_ok=True)
    records, hashes, stats = [], {}, Counter()
    for folder in roots:
        for path in sorted((ROOT / folder).rglob('*')):
            if path.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
                continue
            stats['images_seen'] += 1
            if EXCLUDE.search(path.name):
                stats['diagnostic_name'] += 1
                continue
            try:
                with Image.open(path) as image:
                    if image.size not in ((486, 462), (1776, 999)):
                        stats['other_size'] += 1
                        continue
                    rgb = np.asarray(image.convert('RGB'))
            except OSError:
                stats['unreadable'] += 1
                continue
            digest = hashlib.sha256(rgb.tobytes()).hexdigest()
            try:
                relative = path.relative_to(ROOT).as_posix()
            except ValueError:
                relative = path.as_posix()
            chapter = chapter_from_path(relative)
            if digest in hashes:
                existing = records[hashes[digest]]
                existing['aliases'].append(relative)
                if chapter is not None:
                    existing['chapters'] = sorted(set(existing['chapters'] + [chapter]))
                stats['duplicate_pixels'] += 1
                continue
            hashes[digest] = len(records)
            records.append(dict(id=digest[:20], pixel_sha256=digest, source=relative, aliases=[],
                                chapters=[] if chapter is None else [chapter],
                                size=list(rgb.shape[1::-1]), domain='scene' if rgb.shape[1] == 1776 else 'minimap'))
            if len(records) % 500 == 0:
                print(json.dumps(dict(unique=len(records), seen=stats['images_seen'])), flush=True)
    for record in records:
        chapters = record['chapters']
        # All views and copies of a chapter share a partition, including raw and client crops.
        record['split'] = ('test' if any(c in (18, 28, 43) for c in chapters) else
                           'val' if any(c in (12, 33, 47) for c in chapters) else 'train')
        record['group'] = f'chapter_{chapters[0]:02}' if chapters else record['source'].rsplit('/', 1)[0]
        if not chapters:
            bucket = int(hashlib.sha256(record['group'].encode()).hexdigest()[:8], 16) % 10
            record['split'] = 'test' if bucket == 0 else 'val' if bucket == 1 else 'train'
    write_jsonl(output / 'inventory.jsonl', records)
    summary = dict(stats=stats, unique=len(records), domains=Counter(r['domain'] for r in records),
                   partitions=Counter(r['split'] for r in records),
                   chapters=Counter(str(r['chapters']) for r in records))
    (output / 'inventory-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


def candidate(label, box, method, **details):
    """构造一条待复核的弱标注。

    reviewed 一律初始为 False：这些框来自启发式提议，没有人工确认过，不能直接当训练标签。
    method 记下提议来源，复核时要据此判断该信多少。
    """
    return dict(label=label, box=[float(v) for v in box], method=method, reviewed=False, **details)


def minimap_proposals(image):
    """小地图上的弱标注提议，仅供人工复核，既不是真值也不是运行期的兜底识别。

    按红色取敌人、低饱和白色取小队圆环，并把红色膨胀后从白色里剔除，避免重叠处互相干扰。
    面板右下角图例和紧凑面板顶部两角是界面元素，先清掉。
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, width = image.shape[:2]
    red = cv2.inRange(hsv, (135, 45, 80), (179, 255, 255)) | cv2.inRange(hsv, (0, 50, 100), (8, 255, 255))
    white = cv2.inRange(hsv, (0, 0, 165), (179, 105, 255))
    white[cv2.dilate(red, np.ones((7, 7), np.uint8)) > 0] = 0
    for mask in (red, white):
        mask[-35:, -75:] = 0
        if width < 300:
            mask[:29, :42] = mask[:29, -34:] = 0
    contours, _ = cv2.findContours(cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)),
                                  cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    result = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        hull = cv2.convexHull(contour)
        if min(w, h) < 4 or max(w, h) > 150 or cv2.contourArea(hull) < 8:
            continue
        mask = np.zeros((h, w), np.uint8)
        cv2.drawContours(mask, [hull - [x, y]], -1, 255, cv2.FILLED)
        fill = float(np.count_nonzero(red[y:y + h, x:x + w] & mask) / max(1, np.count_nonzero(mask)))
        label = 'minimap_enemy_normal' if fill >= .65 else 'minimap_enemy_ex'
        clipped = x <= 2 or y <= 2 or x + w >= width - 2 or y + h >= height - 2
        vertices = len(cv2.approxPolyDP(hull, cv2.arcLength(hull, True) * .06, True))
        uncertain = label == 'minimap_enemy_normal' and (vertices != 3 or clipped)
        result.append(candidate(label, (x, y, x + w, y + h), 'red_component',
                                fill=fill, clipped=clipped, uncertain=uncertain))
    # A broken EX outline still encloses its exclamation mark; do not label that inner fragment as an enemy.
    result = [item for item in result if not any(
        item is not other and other['label'] == 'minimap_enemy_ex'
        and item['box'][0] >= other['box'][0] - 3 and item['box'][1] >= other['box'][1] - 3
        and item['box'][2] <= other['box'][2] + 3 and item['box'][3] <= other['box'][3] + 3
        for other in result)]
    count, components, _, _ = cv2.connectedComponentsWithStats(cv2.dilate(white, np.ones((5, 5), np.uint8)))
    for component in range(1, count):
        ys, xs = np.nonzero((components == component) & (white > 0))
        points = np.column_stack((xs, ys)).astype(np.float32)
        if len(points) < 20:
            continue
        x, y, w, h = cv2.boundingRect(points)
        if min(w, h) < 8 or max(w, h) > 100 or not .3 <= w / h <= 3.3:
            continue
        (cx, cy), axes, angle = cv2.fitEllipse(points)
        if min(axes) < 6:
            continue
        radians = np.deg2rad(angle)
        rotation = np.array([[np.cos(radians), np.sin(radians)], [-np.sin(radians), np.cos(radians)]])
        normalized = (points - [cx, cy]) @ rotation.T / (np.asarray(axes) / 2)
        residual = float(np.quantile(np.abs(np.linalg.norm(normalized, axis=1) - 1), .85))
        coverage = len(np.unique(np.floor((np.arctan2(normalized[:, 1], normalized[:, 0]) + np.pi) * 8 / np.pi)))
        if residual > .25 or coverage < 12 or not x <= cx <= x + w or not y <= cy <= y + h:
            continue
        hull = cv2.convexHull(points)
        uncertain = len(cv2.approxPolyDP(hull, cv2.arcLength(hull, True) * .025, True)) < 6
        result.append(candidate('minimap_squad_ring', (x, y, x + w, y + h), 'white_ellipse',
                                center=[cx, cy], axes=list(axes), angle=angle, uncertain=uncertain,
                                clipped=x <= 2 or y <= 2 or x + w >= width - 2 or y + h >= height - 2))
    return result


def scene_proposals(image):
    """客户区截图上的弱标注提议，同样只供人工复核。

    箭头用模板在三组白/黄掩码上多尺度匹配；顶栏、底栏和左上任务区先清零，
    那里的高亮界面元素会刷出大量假候选。
    """
    assets = ROOT / 'module/campaign/vision/assets'
    template = cv2.imread(str(assets / 'squad_arrow_tpl.png'), cv2.IMREAD_GRAYSCALE)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    masks = [cv2.inRange(hsv, (0, 0, 215), (179, 110, 255)),
             cv2.inRange(hsv, (0, 0, 245), (179, 80, 255)),
             cv2.inRange(hsv, (5, 160, 190), (30, 255, 255))]
    proposals = []
    for mask in masks:
        mask[:100] = mask[900:] = 0
        mask[:295, :230] = 0
        mask[:, 1650:] = 0
        for scale in (.6, .7, .8, .9, 1., 1.1, 1.25):
            resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            scores = cv2.matchTemplate(mask, resized, cv2.TM_CCOEFF_NORMED)
            for _ in range(3):
                _, score, _, (x, y) = cv2.minMaxLoc(scores)
                if score < .66:
                    break
                proposals.append(candidate('scene_squad_arrow',
                                            (x, y, x + resized.shape[1], y + resized.shape[0]),
                                            'arrow_template', score=score))
                scores[max(0, y - 30):y + 31, max(0, x - 30):x + 31] = -1
    indicator = cv2.imread(str(assets / 'collectible_indicator.png'), cv2.IMREAD_UNCHANGED)
    scores = cv2.matchTemplate(image[100:900, :1650], indicator[:, :, :3], cv2.TM_SQDIFF_NORMED,
                              mask=indicator[:, :, 3])
    scores[~np.isfinite(scores)] = 1
    for _ in range(3):
        error, _, (x, y), _ = cv2.minMaxLoc(scores)
        if error > .14:
            break
        proposals.append(candidate('scene_collectible_indicator',
                                    (x, y + 100, x + indicator.shape[1], y + 100 + indicator.shape[0]),
                                    'indicator_template', score=1 - error))
        scores[max(0, y - 30):y + 31, max(0, x - 30):x + 31] = 1
    selected = []
    for item in sorted(proposals, key=lambda p: p['score'], reverse=True):
        center = (np.asarray(item['box'][:2]) + item['box'][2:]) / 2
        if any(item['label'] == other['label'] and np.linalg.norm(
                center - (np.asarray(other['box'][:2]) + other['box'][2:]) / 2) < 25 for other in selected):
            continue
        selected.append(item)
    return selected


def propose(output):
    """为清单里尚无提议的截图生成弱标注，追加写入 proposals.jsonl。

    已有记录按 id 跳过，所以可以反复运行补做新增采集，不会重复提议、也不会覆盖已复核的结果。
    逐条写入并 flush，上万张图跑很久，中断后不丢已完成的部分。
    """
    records = read_jsonl(output / 'inventory.jsonl')
    existing = {r['id']: r for r in read_jsonl(output / 'proposals.jsonl')} if (output / 'proposals.jsonl').exists() else {}
    with (output / 'proposals.jsonl').open('a', encoding='utf-8') as stream:
        for index, record in enumerate(records):
            if record['id'] in existing:
                continue
            image = cv2.imread(str(ROOT / record['source']))
            if image is None:
                raise ValueError(f'Unreadable capture: {record["source"]}')
            record['objects'] = scene_proposals(image) if record['domain'] == 'scene' else minimap_proposals(image)
            record['review'] = 'pending'
            stream.write(json.dumps(record) + '\n')
            stream.flush()
            if index % 100 == 0:
                print(json.dumps(dict(processed=index + 1, total=len(records))), flush=True)


def contact_sheet(output, label, page, count=80):
    """把某一类的候选拼成一张分页缩略图，并输出对应的坐标清单。

    人工复核逐张开图太慢，拼图能一屏看掉几十个候选；旁边的 .json 记录每格对应哪条记录，
    复核结论要靠它写回。标为 uncertain 的候选不进拼图，它们需要单独看原图判断。
    """
    records = read_jsonl(output / 'proposals.jsonl')
    items = [(r, i, obj) for r in records for i, obj in enumerate(r['objects'])
             if obj['label'] == label and not obj.get('uncertain')]
    selected = items[page * count:(page + 1) * count]
    canvas = Image.new('RGB', (160 * 8, 140 * ((len(selected) + 7) // 8)), '#252525')
    draw = ImageDraw.Draw(canvas)
    references = []
    for j, (record, index, obj) in enumerate(selected):
        image = cv2.imread(str(ROOT / record['source']))
        x1, y1, x2, y2 = np.rint(obj['box']).astype(int)
        left, top = max(0, x1 - 15), max(0, y1 - 15)
        right, bottom = min(image.shape[1], x2 + 15), min(image.shape[0], y2 + 15)
        crop = Image.fromarray(cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2RGB))
        crop.thumbnail((150, 105))
        origin = (j % 8 * 160, j // 8 * 140)
        canvas.paste(crop, (origin[0] + 5, origin[1] + 30))
        text = f'{page * count + j}: ch{record["chapters"]} {obj.get("fill", obj.get("score", 0)):.2f}'
        draw.text((origin[0] + 3, origin[1] + 3), text, fill='white')
        references.append(dict(number=page * count + j, id=record['id'], object=index, source=record['source']))
    path = output / f'review_{label}_{page:03}'
    canvas.save(path.with_suffix('.jpg'))
    path.with_suffix('.json').write_text(json.dumps(references, indent=2), encoding='utf-8')
    print(json.dumps(dict(path=str(path.with_suffix('.jpg')), objects=len(items), page=page)))


def refresh_minimap(output):
    """重做所有小地图记录的提议，并把旧文件留存为带版本号的副本。

    判据是面板蓝色占比：地图没打开时截到的是场景，当成小地图标注会引入整类错误标签；
    紧凑面板留白多，阈值比展开面板低。不达标的记为 excluded_diagnostic 而不是删除，
    保留记录才能看出排除了多少、为什么排除。
    旧 proposals.jsonl 改名成 proposals-v<N>.jsonl 而不是覆盖，复核结论只存在于旧文件里。
    """
    records = read_jsonl(output / 'proposals.jsonl')
    for record in records:
        if record['domain'] != 'minimap':
            continue
        image = cv2.imread(str(ROOT / record['source']))
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        blue_fraction = float(((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 120) & (hsv[:, :, 1] > 40)).mean())
        record['blue_fraction'] = blue_fraction
        if blue_fraction < (.75 if record.get('roi_kind') == 'compact' else .85):
            record['review'] = 'excluded_diagnostic'
            record['objects'] = []
        else:
            record['review'] = 'pending'
            record['objects'] = minimap_proposals(image)
    version = 1
    while (output / f'proposals-v{version}.jsonl').exists():
        version += 1
    previous = output / f'proposals-v{version}.jsonl'
    (output / 'proposals.jsonl').replace(previous)
    write_jsonl(output / 'proposals.jsonl', records)
    print(json.dumps(Counter(o['label'] for r in records for o in r['objects'])))


def client_map_crops(output):
    """从客户区截图里裁出小地图面板，补成额外的小地图样本。

    紧凑面板要先把画面缩放到 1920×1080 再裁，展开面板直接按 1776×999 裁——两套 ROI 分别是在
    各自尺寸下标定的，混用会偏。
    裁出的图按像素哈希与现有记录去重，并记下 source_origin 和 crop，这样 load_frame 能从原图
    重建出同样的像素而不必再存一份图片。
    """
    records = read_jsonl(output / 'proposals.jsonl')
    by_hash = {r['pixel_sha256']: r for r in records}
    directory = output / 'client_maps'
    directory.mkdir(exist_ok=True)
    additions = []
    for record in records:
        if record['domain'] != 'scene':
            continue
        image = cv2.imread(str(ROOT / record['source']))
        normalized = cv2.resize(image, (1920, 1080))
        crops = [('compact', normalized[96:307, 25:243], [25, 96, 243, 307], [1920, 1080]),
                 ('expanded', image[280:742, 644:1130], [644, 280, 1130, 742], [1776, 999])]
        for kind, crop, bounds, client in crops:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            fraction = np.mean((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 120) & (hsv[:, :, 1] > 40))
            if fraction < .45:
                continue
            digest = hashlib.sha256(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).tobytes()).hexdigest()
            if digest in by_hash:
                continue
            path = directory / f'{digest[:20]}.png'
            if not cv2.imwrite(str(path), crop):
                raise OSError(path)
            item = dict(id=digest[:20], pixel_sha256=digest, source=path.as_posix(), aliases=[],
                        source_origin=record['source'], parent=record['id'], crop=bounds, client=client,
                        chapters=record['chapters'], split=record['split'], group=record['group'],
                        domain='minimap', roi_kind=kind, size=list(crop.shape[1::-1]), review='pending',
                        objects=minimap_proposals(crop))
            additions.append(item)
            by_hash[digest] = item
    write_jsonl(output / 'proposals.jsonl', records + additions)
    print(json.dumps(dict(client_crops=len(additions), objects=Counter(
        o['label'] for r in additions for o in r['objects']))))


def load_frame(record):
    """按记录重建它所对应的图像，并核对复核时的像素哈希。

    派生 ROI 不单独存图，而是从原始采集重新裁一次（source_origin + crop），避免同一帧存两份。
    像素哈希不符说明原图被改过或替换过，此时这条复核结论已经不对应这张图，必须报错。
    """
    source = record.get('source_origin') if record.get('crop') else record['source']
    image = cv2.imread(str(ROOT / source))
    if image is None:
        raise FileNotFoundError(source)
    if record.get('crop'):
        image = cv2.resize(image, tuple(record['client']))
        left, top, right, bottom = record['crop']
        image = image[top:bottom, left:right]
    digest = hashlib.sha256(cv2.cvtColor(image, cv2.COLOR_BGR2RGB).tobytes()).hexdigest()
    if digest != record['pixel_sha256']:
        raise ValueError(f'Capture pixels changed: {record["id"]}')
    return image


def apply_frame_reviews(records, path):
    """把整帧复核结论套用到清单记录上，并标记为 full_frame_reviewed。

    只接受 review_scope 为 full_frame 且已 accepted 的条目：部分复核的帧上仍可能有漏标的目标，
    当成完整真值会把「漏检」算成「误检」。
    来源像素哈希必须一致，否则这份结论对应的是另一张图，宁可报错中止。
    """
    reviews = json.loads(Path(path).read_text('utf-8'))['frames']
    by_id = {record['id']: record for record in records}
    for review in reviews:
        if review.get('review_scope') != 'full_frame' or review.get('review') != 'accepted':
            raise ValueError(f'Incomplete frame review: {review["id"]}')
        original = by_id.get(review['id'])
        if original is None or original['pixel_sha256'] != review['pixel_sha256']:
            raise ValueError(f'Frame review source changed: {review["id"]}')
        original.update(review)
        original['quality'] = 'full_frame_reviewed'
    return reviews


def perceptual_hash(image):
    """63 位感知哈希，用于判断两帧是否近似重复。

    取 DCT 低频 8×8 去掉直流项后与中位数比较，所以对亮度整体变化和轻微压缩不敏感——
    采集里的「同一处换个时间再截一张」正是这种差异。
    """
    small = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (32, 32))
    low = cv2.dct(small.astype(np.float32))[:8, :8].reshape(-1)[1:]
    return low > np.median(low)


def select_reviewed_partitions(records):
    """划分训练与评估集合：先把已复核的留出帧连同它们的采集批次整体隔离，再挑训练样本。

    评估集只收整帧复核过的帧，否则漏标会被当成误检。
    隔离是按「采集批次」而不是按单帧做的：同一次扫描里的相邻帧几乎相同，只隔离单帧等于把
    评估帧的近邻留在训练里，评估结果会虚高。章节、group 和采集目录三条线索任一命中就排除。
    41 章的采集分成多个独立批次，只有已复核的那批能进训练。
    最后再用感知哈希排掉与留出帧近似重复的训练帧，以及与测试集近似的验证帧——
    两个评估集合之间也必须互不重叠。
    返回（选中记录, 各类排除计数），计数用于核对数据集构成。
    """
    reviewed = [r for r in records if r.get('quality') == 'full_frame_reviewed']
    held = [r for r in reviewed if r['split'] != 'train']
    held_chapters = {c for r in held for c in r['chapters'] if c != 41}
    held_groups = {r['group'] for r in held if not r['chapters']}
    held_origins = {str(Path(r.get('source_origin') or r['source']).parent) for r in held
                    if not r['chapters']}
    hashes = [(r['split'], r['size'], perceptual_hash(load_frame(r))) for r in held]
    selected, excluded = [], Counter()
    for record in records:
        gold = record.get('quality') == 'full_frame_reviewed'
        if not gold and record['split'] != 'train':
            excluded['unreviewed_evaluation'] += 1
            continue
        if record['split'] == 'train':
            origin = str(Path(record.get('source_origin') or record['source']).parent)
            if (set(record['chapters']) & held_chapters or record['group'] in held_groups
                    or origin in held_origins):
                excluded['held_acquisition_group'] += 1
                continue
            # Chapter 41 uses separated capture blocks; only the reviewed training block is eligible.
            if 41 in record['chapters'] and not gold:
                excluded['unreviewed_chapter41'] += 1
                continue
            if record.get('review') == 'excluded_diagnostic':
                continue
            if not gold and record['domain'] == 'minimap':
                if record.get('roi_kind') == 'compact' or any(
                        o['label'].startswith('minimap_enemy') and not o.get('reviewed')
                        for o in record['objects']):
                    excluded['unreviewed_minimap'] += 1
                    continue
            fingerprint = perceptual_hash(load_frame(record))
            if any(size == record['size'] and np.count_nonzero(fingerprint != other) <= 8
                   for _, size, other in hashes):
                excluded['near_duplicate_of_holdout'] += 1
                continue
        selected.append(record)
    # Evaluation partitions must also be distinct; drop validation copies of test captures.
    tests = [(r['size'], perceptual_hash(load_frame(r))) for r in selected if r['split'] == 'test']
    result = []
    for record in selected:
        if record['split'] == 'val':
            fingerprint = perceptual_hash(load_frame(record))
            if any(size == record['size'] and np.count_nonzero(fingerprint != other) <= 8
                   for size, other in tests):
                excluded['validation_near_test'] += 1
                continue
        result.append(record)
    return result, dict(excluded)


def iou(first, second):
    """两个 [x1, y1, x2, y2] 框的交并比；并集为 0 时返回 0 而不是除零。"""
    a = np.asarray(first, float)
    b = np.asarray(second, float)
    inter = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2]))
    union = np.prod(a[2:] - a[:2]) + np.prod(b[2:] - b[:2]) - np.prod(inter)
    return float(np.prod(inter) / union) if union > 0 else 0.


def merge_extra_objects(records, path):
    """把拼图复核确认的目标（如机关、电梯）按帧 id 合并进记录。

    合并进来的目标不带模板分数，因此会绕过弱提议的阈值过滤——它们是人工确认过的，
    不需要再被启发式分数筛一遍。
    与已有同类目标 IoU 超过 0.5 的视为重复，跳过；返回合并与跳过计数和来源文件哈希，
    让数据集摘要能追溯这批标注的出处。
    """
    merged = skipped = 0
    by_id = {record['id']: record for record in records}
    data = json.loads(Path(path).read_text('utf-8'))
    for frame in data['frames']:
        record = by_id.get(frame['id'])
        if record is None:
            skipped += 1
            continue
        for obj in frame['objects']:
            if any(existing['label'] == obj['label'] and iou(existing['box'], obj['box']) > .5
                   for existing in record['objects']):
                skipped += 1
                continue
            merged_obj = {'label': obj['label'], 'box': obj['box'], 'reviewed': True}
            if obj.get('status'):
                merged_obj['status'] = obj['status']
            record['objects'].append(merged_obj)
            merged += 1
    return {'path': str(path), 'merged': merged, 'skipped': skipped,
            'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def build(output, destination, extra_frame_reviews=(), extra_objects=()):
    """套用各级复核结论，导出一个新版本的 YOLO 数据集目录。

    destination 必须是不存在的新目录：数据集是训练结果的唯一依据，就地覆盖会让已训好的模型
    失去可复现的来源。
    逐级校验「结论仍对应当前数据」——标注复核核对来源哈希与框坐标，整帧复核核对像素哈希，
    任一不符就报错中止，而不是跳过那一条：这类不一致意味着清单被重跑过，整批结论都需要重新确认。
    """
    from module.campaign.vision.detection import scene_tiles

    if destination.exists():
        raise FileExistsError(f'Use a new dataset version: {destination}')
    records = read_jsonl(output / 'proposals.jsonl')
    by_id = {r['id']: r for r in records}
    review_path = Path(__file__).parent / 'reviews/annotation_reviews.json'
    if review_path.exists():
        for review in json.loads(review_path.read_text('utf-8'))['reviews']:
            record = by_id.get(review['id'])
            if record is None or record['pixel_sha256'] != review['source_sha256']:
                raise ValueError(f'Annotation review source changed: {review["id"]}')
            matches = [o for o in record['objects'] if np.allclose(o['box'], review['box'], atol=.1)]
            if len(matches) != 1:
                raise ValueError(f'Annotation review box changed: {review["id"]} {review["box"]}')
            matches[0].update(label=review['label'], uncertain=review['uncertain'], reviewed=True)
    frame_review_path = Path(__file__).parent / 'reviews/frame_reviews.json'
    exclusions = {}
    if frame_review_path.exists():
        apply_frame_reviews(records, frame_review_path)
    for path in extra_frame_reviews:
        apply_frame_reviews(records, path)
    if frame_review_path.exists() or extra_frame_reviews:
        records, exclusions = select_reviewed_partitions(records)
    object_merges = [merge_extra_objects(records, path) for path in extra_objects]
    destination.mkdir(parents=True)
    write_jsonl(destination / 'frames.jsonl', records)
    manifest, counts, negatives = [], Counter(), Counter()
    rng = np.random.default_rng(20261002)
    for split in ('train', 'val', 'test'):
        (destination / 'images' / split).mkdir(parents=True)
        (destination / 'labels' / split).mkdir(parents=True)
    for record in records:
        if record.get('review') == 'excluded_diagnostic' or any(o.get('uncertain') for o in record['objects']):
            continue
        image = load_frame(record)
        # YOLO has no ignore-region labels. Hide ambiguous symbols from its training loss.
        for ignored in record.get('ignored', []):
            left, top, right, bottom = np.asarray(ignored['box']).astype(int)
            image[top:bottom, left:right] = 0
        objects = record['objects']
        if record['domain'] == 'minimap':
            if not objects:
                key = (record['group'], record['split'])
                if negatives[key] >= 20:
                    continue
                negatives[key] += 1
            tiles = [(image, (0, 0))]
        else:
            # Weak arrow proposals below the legacy acceptance threshold are queued for review.
            if any(o.get('score', 1) < .72 for o in objects):
                continue
            tiles = list(scene_tiles(image))
        for index, (tile, offset) in enumerate(tiles):
            height, width = tile.shape[:2]
            annotations = []
            for obj in objects:
                box = np.asarray(obj['box']) - np.tile(offset, 2)
                clipped = np.clip(box, 0, [width, height, width, height])
                size = clipped[2:] - clipped[:2]
                if min(size) < 3 or np.prod(size) < np.prod(box[2:] - box[:2]) * .3:
                    continue
                center = (clipped[:2] + clipped[2:]) / 2
                # Scene map overlays are blacked out in both training and inference.
                x1, y1, x2, y2 = np.rint(clipped).astype(int)
                if not np.any(tile[y1:y2, x1:x2]):
                    continue
                annotations.append((LABELS.index(obj['label']), *(center / [width, height]),
                                    *(size / [width, height])))
            if record['domain'] == 'scene' and not annotations and rng.random() > .25:
                continue
            name = f'{record["id"]}_{index:02}'
            split = record['split']
            path = destination / 'images' / split / f'{name}.png'
            if not cv2.imwrite(str(path), tile):
                raise OSError(path)
            label_path = destination / 'labels' / split / f'{name}.txt'
            label_path.write_text(''.join(' '.join([str(a[0]), *(f'{v:.7f}' for v in a[1:])]) + '\n'
                                          for a in annotations), encoding='utf-8')
            manifest.append(dict(image=path.relative_to(destination).as_posix(), source=record['source'],
                                 source_id=record['id'], source_origin=record.get('source_origin'),
                                 group=record['group'], split=split, roi_kind=record.get('roi_kind', record['domain']),
                                 offset=list(offset), quality=record.get('quality', 'weak'),
                                 labels_sha256=hashlib.sha256(label_path.read_bytes()).hexdigest(),
                                 pixels_sha256=hashlib.sha256(tile.tobytes()).hexdigest(), objects=len(annotations)))
            counts.update(f'{split}/{LABELS[a[0]]}' for a in annotations)
    write_jsonl(destination / 'manifest.jsonl', manifest)
    yaml = f'path: {destination.as_posix()}\ntrain: train.txt\nval: images/val\ntest: images/test\nnames:\n'
    yaml += ''.join(f'  {i}: {label}\n' for i, label in enumerate(LABELS))
    (destination / 'dataset.yaml').write_text(yaml, encoding='utf-8')
    summary = dict(images=Counter(m['split'] for m in manifest), objects=counts,
                   annotation_quality=Counter(m['quality'] for m in manifest), exclusions=exclusions,
                   frame_reviews_sha256=hashlib.sha256(frame_review_path.read_bytes()).hexdigest()
                   if frame_review_path.exists() else None,
                   extra_frame_reviews={str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                        for path in extra_frame_reviews},
                   extra_objects=object_merges,
                   note='Mixed training labels; final accuracy must use unmasked reviewed frames and runtime gates.')
    (destination / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    balance_training(destination)
    print(json.dumps(summary), flush=True)


def balance_training(destination):
    """对训练集里稀缺的类别做重复采样，改写 train.txt；评估集永不重复。

    重复采样只改训练清单（train.txt 里同一路径出现多次），不复制图片，所以不会把评估帧带进来。
    倍数按类别稀缺程度给：新接入的机关、电梯类样本最少，倍数最高；只有整帧复核过的帧才享受
    更高倍数，未复核帧重复多了会把它的漏标也放大。
    """
    destination = Path(destination)
    manifest = read_jsonl(destination / 'manifest.jsonl')
    training = []
    for record in manifest:
        if record['split'] != 'train':
            continue
        labels = destination / record['image'].replace('images/', 'labels/').replace('.png', '.txt')
        classes = {int(line.split()[0]) for line in labels.read_text('utf-8').splitlines()}
        repeats = 8 if 1 in classes else 1
        if record['quality'] == 'full_frame_reviewed':
            if 2 in classes:
                repeats = max(repeats, 16)
            if 3 in classes or (4 in classes and record['roi_kind'] == 'compact'):
                repeats = max(repeats, 8)
        # New mechanism/elevator classes are contact-sheet reviewed; repeat the rarest ones.
        if 5 in classes or 8 in classes:
            repeats = max(repeats, 16)
        elif 7 in classes:
            repeats = max(repeats, 4)
        elif 6 in classes:
            repeats = max(repeats, 2)
        training.extend(['./' + record['image']] * repeats)
    (destination / 'train.txt').write_text('\n'.join(training) + '\n', encoding='utf-8')
    yaml_path = destination / 'dataset.yaml'
    yaml_path.write_text(yaml_path.read_text('utf-8').replace('train: images/train', 'train: train.txt'), encoding='utf-8')


def main():
    """命令行入口：按子命令依次完成清点、提议、拼图复核、补裁与数据集导出。

    各步之间通过 --output 目录下的 inventory.jsonl / proposals.jsonl 传递中间结果，
    所以要按顺序跑，且可以分多次继续；只有 build 需要 --destination。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inventory', 'propose', 'sheet', 'refresh-minimap', 'client-crops', 'build'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--roots', nargs='+', default=DEFAULT_ROOTS)
    parser.add_argument('--label', choices=LABELS, default=LABELS[0])
    parser.add_argument('--page', type=int, default=0)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--frame-reviews', type=Path, action='append', default=[])
    parser.add_argument('--extra-objects', type=Path, action='append', default=[],
                        help='Reviewed object lists (confirmed-objects.json) merged into frames by id.')
    args = parser.parse_args()
    if args.action == 'inventory':
        inventory(args.output, args.roots)
    elif args.action == 'propose':
        propose(args.output)
    elif args.action == 'refresh-minimap':
        refresh_minimap(args.output)
    elif args.action == 'client-crops':
        client_map_crops(args.output)
    elif args.action == 'build':
        if args.destination is None:
            parser.error('build requires --destination')
        build(args.output, args.destination, args.frame_reviews, args.extra_objects)
    else:
        contact_sheet(args.output, args.label, args.page)


if __name__ == '__main__':
    main()
