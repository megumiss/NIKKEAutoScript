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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LABELS = ['scene_squad_arrow', 'scene_collectible_indicator', 'minimap_enemy_normal',
          'minimap_enemy_ex', 'minimap_squad_ring']
DEFAULT_ROOTS = ['data/chapter_maps', 'log/campaign_prototype', 'log/chapter_maps',
                 'log/chapter_calibration_20261001', 'log/chapter_calibration_restart_20261001',
                 'tmp/arrow_anchor_probe', 'tmp/arrow_fix', 'tmp/ch33_calibration_test',
                 'tmp/ch34_fragments_20260929', 'tmp/ch40_movement_20260930',
                 'tmp/formal_3d_validation', 'tmp/map_followup', 'tmp/map_movement_validation',
                 'tmp/minimap_full_20260925', 'tmp/minimap_full_20260925_run2']
EXCLUDE = re.compile(r'annotat|alignment|mosaic|reconstruct|coverage|preview|overlay|mask|_plan|_match', re.I)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text('utf-8').splitlines() if line.strip()]


def write_jsonl(path, records):
    with Path(path).open('w', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')


def chapter_from_path(path):
    found = re.findall(r'(?:chapter_?|\bch)(\d{1,2})(?!\d)', str(path).replace('\\', '/'), re.I)
    return int(found[-1]) if found else None


def inventory(output, roots):
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
            relative = path.relative_to(ROOT).as_posix()
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
    return dict(label=label, box=[float(v) for v in box], method=method, reviewed=False, **details)


def minimap_proposals(image):
    """Weak labels for review; these heuristics are not ground truth or runtime fallbacks."""
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
    assets = ROOT / 'module/campaign_prototype/assets'
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
    """Reconstruct derived ROIs from repository captures and verify the reviewed pixels."""
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
    small = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (32, 32))
    low = cv2.dct(small.astype(np.float32))[:8, :8].reshape(-1)[1:]
    return low > np.median(low)


def select_reviewed_partitions(records):
    """Reserve reviewed captures and their acquisition groups before image augmentation."""
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


def build(output, destination, extra_frame_reviews=()):
    from module.campaign_prototype.detection import scene_tiles

    if destination.exists():
        raise FileExistsError(f'Use a new dataset version: {destination}')
    records = read_jsonl(output / 'proposals.jsonl')
    by_id = {r['id']: r for r in records}
    review_path = ROOT / 'dev_tools/campaign_yolo/annotation_reviews.json'
    if review_path.exists():
        for review in json.loads(review_path.read_text('utf-8'))['reviews']:
            record = by_id.get(review['id'])
            if record is None or record['pixel_sha256'] != review['source_sha256']:
                raise ValueError(f'Annotation review source changed: {review["id"]}')
            matches = [o for o in record['objects'] if np.allclose(o['box'], review['box'], atol=.1)]
            if len(matches) != 1:
                raise ValueError(f'Annotation review box changed: {review["id"]} {review["box"]}')
            matches[0].update(label=review['label'], uncertain=review['uncertain'], reviewed=True)
    frame_review_path = ROOT / 'dev_tools/campaign_yolo/frame_reviews.json'
    exclusions = {}
    if frame_review_path.exists():
        apply_frame_reviews(records, frame_review_path)
    for path in extra_frame_reviews:
        apply_frame_reviews(records, path)
    if frame_review_path.exists() or extra_frame_reviews:
        records, exclusions = select_reviewed_partitions(records)
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
                   note='Mixed training labels; final accuracy must use unmasked reviewed frames and runtime gates.')
    (destination / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    balance_training(destination)
    print(json.dumps(summary), flush=True)


def balance_training(destination):
    """Resample scarce reviewed targets within training; never duplicate evaluation captures."""
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
        training.extend(['./' + record['image']] * repeats)
    (destination / 'train.txt').write_text('\n'.join(training) + '\n', encoding='utf-8')
    yaml_path = destination / 'dataset.yaml'
    yaml_path.write_text(yaml_path.read_text('utf-8').replace('train: images/train', 'train: train.txt'), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inventory', 'propose', 'sheet', 'refresh-minimap', 'client-crops', 'build'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--roots', nargs='+', default=DEFAULT_ROOTS)
    parser.add_argument('--label', choices=LABELS, default=LABELS[0])
    parser.add_argument('--page', type=int, default=0)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--frame-reviews', type=Path, action='append', default=[])
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
        build(args.output, args.destination, args.frame_reviews)
    else:
        contact_sheet(args.output, args.label, args.page)


if __name__ == '__main__':
    main()
