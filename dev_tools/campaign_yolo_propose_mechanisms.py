"""Propose scene mechanism/elevator candidates by multi-scale template matching for later visual review."""

import argparse
from collections import Counter, defaultdict
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
TEMPLATE_DIR = ROOT / 'dev_tools/campaign_yolo/templates/scene'
DEFAULT_OUTPUT = Path(r'E:\CodexData\NIKKEAutoScript\yolo\data\raw\mechanism-captures')
DEFAULT_ROOTS = ['data/chapter_maps', 'log/campaign_prototype', 'log/chapter_maps',
                 'log/chapter_calibration_20261001', 'log/chapter_calibration_restart_20261001',
                 'log/chapter_calibration_run_01',
                 'tmp/arrow_anchor_probe', 'tmp/arrow_fix', 'tmp/ch33_calibration_test',
                 'tmp/ch34_fragments_20260929', 'tmp/ch40_movement_20260930',
                 'tmp/formal_3d_validation', 'tmp/map_followup', 'tmp/map_movement_validation',
                 'tmp/minimap_full_20260925', 'tmp/minimap_full_20260925_run2']
EXCLUDE = re.compile(r'annotat|alignment|mosaic|reconstruct|coverage|preview|overlay|mask|_plan|_match', re.I)
LABELS = ['scene_ground_mechanism_off', 'scene_ground_mechanism_on', 'scene_elevator_start',
          'scene_elevator_end']
# Candidate thresholds require visual review before annotation.
THRESHOLDS = {'scene_ground_mechanism_off': .6, 'scene_ground_mechanism_on': .88,
              'scene_elevator_start': .72, 'scene_elevator_end': .65}
SCALES = (.7, .85, 1., 1.15, 1.3, 1.45, 1.6)
# Frames are matched at half resolution; full-res matching is ~4x slower with no recall gain
# at these object sizes (100-230 px native). Candidate boxes are mapped back to full res.
FRAME_SCALE = .5
# Grayscale + TM_CCOEFF_NORMED: themes (desert/snow/grass) shift hue strongly, and CCOEFF is
# already mean/contrast normalized, so gray matching stays theme-invariant.
MAX_PER_CLASS = 5
NMS_IOU = .3


def imread_any(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def chapter_from_path(path):
    found = re.findall(r'(?:chapter_?|\bch)(\d{1,2})(?!\d)', str(path).replace('\\', '/'), re.I)
    return int(found[-1]) if found else None


def split_for(chapters, group):
    if any(c in (18, 28, 43) for c in chapters):
        return 'test'
    if any(c in (12, 33, 47) for c in chapters):
        return 'val'
    if not chapters:
        bucket = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 10
        return 'test' if bucket == 0 else 'val' if bucket == 1 else 'train'
    return 'train'


def load_templates():
    templates = defaultdict(list)
    for path in sorted(TEMPLATE_DIR.glob('*.png')):
        label = path.stem.split('__')[0]
        if label not in LABELS:
            continue
        image = imread_any(path)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        templates[label].append(dict(tag=path.stem.split('__')[1], gray=gray, shape=gray.shape))
    return templates


def scan_frames(roots):
    records, hashes, stats = [], set(), Counter()
    for folder in roots:
        base = Path(folder)
        if not base.is_absolute():
            base = ROOT / folder
        if not base.exists():
            print(json.dumps(dict(missing=folder)), flush=True)
            continue
        for path in sorted(base.rglob('*')):
            if path.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
                continue
            if EXCLUDE.search(path.name):
                stats['diagnostic_name'] += 1
                continue
            try:
                with Image.open(path) as image:
                    if image.size != (1776, 999):
                        stats['other_size'] += 1
                        continue
                    rgb = np.asarray(image.convert('RGB'))
            except OSError:
                stats['unreadable'] += 1
                continue
            digest = hashlib.sha256(rgb.tobytes()).hexdigest()
            if digest in hashes:
                stats['duplicate_pixels'] += 1
                continue
            hashes.add(digest)
            try:
                source = path.relative_to(ROOT).as_posix()
            except ValueError:
                source = path.as_posix()
            chapter = chapter_from_path(source)
            chapters = [] if chapter is None else [chapter]
            group = f'chapter_{chapters[0]:02}' if chapters else source.rsplit('/', 1)[0]
            records.append(dict(id=digest[:20], pixel_sha256=digest, source=source, chapters=chapters,
                                split=split_for(chapters, group), group=group))
            stats['unique'] += 1
    print(json.dumps(dict(stats=stats)), flush=True)
    return records


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.


def match_frame(image, templates, thresholds):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if FRAME_SCALE != 1:
        gray = cv2.resize(gray, None, fx=FRAME_SCALE, fy=FRAME_SCALE, interpolation=cv2.INTER_AREA)
    raw = []
    for label, variants in templates.items():
        threshold = thresholds[label]
        for variant in variants:
            for scale in SCALES:
                factor = scale * FRAME_SCALE
                size = (max(8, round(variant['shape'][1] * factor)), max(8, round(variant['shape'][0] * factor)))
                resized = cv2.resize(variant['gray'], size, interpolation=cv2.INTER_AREA)
                if resized.shape[0] >= gray.shape[0] or resized.shape[1] >= gray.shape[1]:
                    continue
                scores = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
                height, width = resized.shape
                for _ in range(4):
                    _, score, _, (x, y) = cv2.minMaxLoc(scores)
                    if score < threshold:
                        break
                    box = [v / FRAME_SCALE for v in (x, y, x + width, y + height)]
                    raw.append(dict(label=label, box=[round(v, 1) for v in box], score=round(float(score), 4),
                                    template=variant['tag'], scale=scale))
                    scores[max(0, y - height // 2):y + height // 2 + 1,
                           max(0, x - width // 2):x + width // 2 + 1] = -1
    objects = []
    for label in LABELS:
        pool = sorted((c for c in raw if c['label'] == label), key=lambda c: c['score'], reverse=True)
        kept = []
        for item in pool:
            if any(iou(item['box'], other['box']) > NMS_IOU for other in kept):
                continue
            kept.append(item)
            if len(kept) >= MAX_PER_CLASS:
                break
        objects.extend(kept)
    # mechanism_off and mechanism_on are visually similar; keep both candidates but annotate
    # overlapping pairs with each other's score so review can disambiguate.
    for item in objects:
        cross = [dict(label=other['label'], score=other['score'], iou=round(iou(item['box'], other['box']), 3))
                 for other in objects
                 if other is not item and other['label'] != item['label'] and iou(item['box'], other['box']) > NMS_IOU]
        if cross:
            item['cross'] = cross
    return objects


def propose(output, roots, limit=0):
    records = scan_frames(roots)
    if limit:
        records = records[:limit]
    templates = load_templates()
    counts = Counter()
    with (output / 'proposals.jsonl').open('w', encoding='utf-8') as stream:
        for index, record in enumerate(records):
            image = imread_any(ROOT / record['source'])
            if image is None:
                raise ValueError(f'Unreadable capture: {record["source"]}')
            record['objects'] = match_frame(image, templates, THRESHOLDS)
            counts.update(o['label'] for o in record['objects'])
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            if index % 100 == 0:
                print(json.dumps(dict(processed=index + 1, total=len(records), candidates=sum(counts.values()))),
                      flush=True)
    print(json.dumps(dict(candidates=counts, frames=len(records))), flush=True)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text('utf-8').splitlines() if line.strip()]


def contact_sheets(output, per_sheet=60, columns=8, cell=(240, 190)):
    records = read_jsonl(output / 'proposals.jsonl')
    by_label = defaultdict(list)
    for record in records:
        for obj in record['objects']:
            by_label[obj['label']].append((record, obj))
    for label, items in sorted(by_label.items()):
        items.sort(key=lambda pair: pair[1]['score'], reverse=True)
        for page in range((len(items) + per_sheet - 1) // per_sheet):
            selected = items[page * per_sheet:(page + 1) * per_sheet]
            rows = (len(selected) + columns - 1) // columns
            canvas = Image.new('RGB', (cell[0] * columns, cell[1] * rows), '#252525')
            draw = ImageDraw.Draw(canvas)
            for j, (record, obj) in enumerate(selected):
                image = imread_any(ROOT / record['source'])
                x1, y1, x2, y2 = obj['box']
                mx, my = (x2 - x1) * .3, (y2 - y1) * .3
                left, top = max(0, int(x1 - mx)), max(0, int(y1 - my))
                right, bottom = min(image.shape[1], int(x2 + mx)), min(image.shape[0], int(y2 + my))
                crop = Image.fromarray(cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2RGB))
                crop.thumbnail((cell[0] - 10, cell[1] - 30))
                origin = (j % columns * cell[0], j // columns * cell[1])
                canvas.paste(crop, (origin[0] + 5, origin[1] + 28))
                draw.text((origin[0] + 4, origin[1] + 4),
                          f'{page * per_sheet + j}: {record["id"][:12]} {obj["score"]:.2f} {obj["template"]}',
                          fill='white')
            path = output / f'sheet_{label}_{page:03}.jpg'
            canvas.save(path)
            print(json.dumps(dict(sheet=path.name, cells=len(selected))), flush=True)


def summarize(output):
    records = read_jsonl(output / 'proposals.jsonl')
    summary = {'frames': len(records), 'classes': {}}
    for label in LABELS:
        scores = [o['score'] for r in records for o in r['objects'] if o['label'] == label]
        frames = {r['id'] for r in records for o in r['objects'] if o['label'] == label}
        chapters = sorted({c for r in records for o in r['objects'] if o['label'] == label
                           for c in r['chapters']})
        entry = dict(candidates=len(scores), unique_frames=len(frames), chapters=chapters)
        if scores:
            array = np.asarray(scores)
            entry['score'] = dict(min=round(float(array.min()), 3), max=round(float(array.max()), 3),
                                  mean=round(float(array.mean()), 3),
                                  p25=round(float(np.quantile(array, .25)), 3),
                                  p50=round(float(np.quantile(array, .5)), 3),
                                  p75=round(float(np.quantile(array, .75)), 3))
        summary['classes'][label] = entry
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--roots', nargs='*', default=DEFAULT_ROOTS)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--sheets-only', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.sheets_only:
        propose(args.output, args.roots, args.limit)
    contact_sheets(args.output)
    summarize(args.output)


if __name__ == '__main__':
    main()
