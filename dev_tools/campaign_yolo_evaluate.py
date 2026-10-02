"""Replay reviewed captures through the production detector without sending game input."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dev_tools.campaign_yolo_dataset import load_frame, read_jsonl
from module.campaign_prototype import detection, perception


def overlap(first, second):
    first, second = np.asarray(first), np.asarray(second)
    intersection = np.prod(np.maximum(0, np.minimum(first[2:], second[2:]) - np.maximum(first[:2], second[:2])))
    union = np.prod(first[2:] - first[:2]) + np.prod(second[2:] - second[:2]) - intersection
    return float(intersection / union) if union > 0 else 0.


def inside(point, box, margin=0):
    return bool(np.all(point >= np.asarray(box[:2]) - margin) and np.all(point <= np.asarray(box[2:]) + margin))


def evaluate(records, model, splits):
    counters = {label: Counter() for label in detection.LABELS}
    center_errors = {label: [] for label in detection.LABELS}
    latencies, failures, details = {}, [], []
    with patch.object(detection, 'detector', return_value=model):
        for record in records:
            if record['split'] not in splits or record.get('review_scope') != 'full_frame':
                continue
            image = load_frame(record)
            domain = record.get('roi_kind', record['domain'])
            start = time.perf_counter()
            predicted = detection.detect_scene(image) if record['domain'] == 'scene' else detection.detect_minimap(image)
            latencies.setdefault(domain, []).append((time.perf_counter() - start) * 1000)
            complete = [obj for obj in record['objects'] if obj.get('status') == 'complete']
            partial = [obj for obj in record['objects'] if obj.get('status') != 'complete']
            ignored = record.get('ignored', [])
            matched, found, errors = set(), set(), []
            pairs = sorted(((overlap(item.box, obj['box']), i, j)
                            for i, item in enumerate(predicted) for j, obj in enumerate(complete)
                            if item.label == obj['label']), reverse=True)
            for iou, i, j in pairs:
                if iou < .5 or i in found or j in matched:
                    continue
                found.add(i)
                matched.add(j)
                item, obj = predicted[i], complete[j]
                counters[item.label]['tp'] += 1
                if 'center' in obj:
                    center = perception.refine_ring(image, item) if item.label == 'minimap_squad_ring' else item.center
                    if center is None:
                        counters[item.label]['refinement_missing'] += 1
                    else:
                        center_errors[item.label].append(float(np.linalg.norm(center - obj['center'])))
            for j, obj in enumerate(complete):
                if j not in matched:
                    counters[obj['label']]['fn'] += 1
                    errors.append(dict(kind='miss', expected=obj))
            for i, item in enumerate(predicted):
                if i in found:
                    continue
                if any(inside(item.center, obj['box']) for obj in partial + ignored):
                    counters[item.label]['partial_or_unknown_detection'] += 1
                    errors.append(dict(kind='partial_or_unknown', predicted=item.__dict__))
                else:
                    counters[item.label]['fp'] += 1
                    errors.append(dict(kind='false_positive', predicted=item.__dict__))
            # Cache this frame's detections so the action-adapter check sees exactly the measured output.
            unsafe, runtime = [], {}
            if record['domain'] == 'minimap':
                with patch.object(perception, 'detect_minimap', return_value=predicted):
                    players, enemies = perception.detect_markers(image, np.eye(3))
                    normals = perception.normal_enemy_markers(image, np.eye(3))
                runtime = dict(players=players, enemies=enemies, normals=normals)
                forbidden = partial + ignored + [obj for obj in complete if obj['label'] == 'minimap_enemy_ex']
                for point in normals:
                    if any(inside(np.asarray(point), obj['box']) for obj in forbidden):
                        unsafe.append(point)
            else:
                with patch.object(perception, 'detect_scene', return_value=predicted):
                    center = perception.squad_arrow(image)
                    indicator = perception.collectible_indicator(image)
                runtime = dict(arrow=None if center is None else center.tolist(), indicator=indicator)
                arrows = [obj for obj in complete if obj['label'] == 'scene_squad_arrow']
                if arrows and center is None:
                    errors.append(dict(kind='runtime_arrow_missing'))
                elif center is not None and not any(inside(center, obj['box']) for obj in arrows):
                    errors.append(dict(kind='runtime_arrow_wrong', point=center.tolist()))
            if unsafe:
                errors.append(dict(kind='unsafe_normal_click', points=unsafe))
            detail = dict(id=record['id'], source=record.get('source_origin') or record['source'],
                          split=record['split'], domain=domain, expected=record['objects'],
                          predicted=[item.__dict__ for item in predicted], runtime=runtime, errors=errors)
            details.append(detail)
            if errors:
                failures.append(detail)
    metrics = {}
    for label, counts in counters.items():
        tp, fp, fn = (counts[key] for key in ('tp', 'fp', 'fn'))
        values = center_errors[label]
        metrics[label] = dict(counts, precision=tp / (tp + fp) if tp + fp else None,
                              recall=tp / (tp + fn) if tp + fn else None,
                              center_error_p95=float(np.quantile(values, .95)) if values else None,
                              center_samples=len(values))
    timing = {domain: dict(frames=len(values), median_ms=float(np.median(values)),
                           p95_ms=float(np.quantile(values, .95))) for domain, values in latencies.items()}
    return dict(frames=len(details), metrics=metrics, latency=timing, failing_frames=len(failures),
                unsafe_normal_click_frames=sum(any(e['kind'] == 'unsafe_normal_click' for e in f['errors'])
                                               for f in failures), details=details)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--splits', nargs='+', choices=['train', 'val', 'test'], default=['val', 'test'])
    args = parser.parse_args()
    records = (read_jsonl(args.frames) if args.frames.suffix == '.jsonl'
               else json.loads(args.frames.read_text('utf-8'))['frames'])
    model = detection.Detector(args.model)
    result = evaluate(records, model, args.splits)
    result.update(model_sha256=model.metadata['sha256'],
                  frames_sha256=hashlib.sha256(args.frames.read_bytes()).hexdigest(), splits=args.splits,
                  note='Replay metrics only; capture independence depends on the training manifest.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'details'}, indent=2))


if __name__ == '__main__':
    main()
