"""清点推图截图并生成待人工复核的候选标注，不控制游戏或导出训练数据集。"""

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
DEFAULT_ROOTS = ['data/chapter_maps/local', 'log/campaign', 'log/campaign_prototype', 'log/chapter_maps']
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


def main():
    """命令行入口：完成截图清点、候选提议、复核拼图及完整面板补裁。

    各步通过 --output 目录下的 inventory.jsonl / proposals.jsonl 传递结果，可分次继续。
    候选标注不代表完整真值；现行三模型的训练和数据划分由独立训练工程负责。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inventory', 'propose', 'sheet', 'refresh-minimap', 'client-crops'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--roots', nargs='+', default=DEFAULT_ROOTS)
    parser.add_argument('--label', choices=LABELS, default=LABELS[0])
    parser.add_argument('--page', type=int, default=0)
    args = parser.parse_args()
    if args.action == 'inventory':
        inventory(args.output, args.roots)
    elif args.action == 'propose':
        propose(args.output)
    elif args.action == 'refresh-minimap':
        refresh_minimap(args.output)
    elif args.action == 'client-crops':
        client_map_crops(args.output)
    else:
        contact_sheet(args.output, args.label, args.page)


if __name__ == '__main__':
    main()
