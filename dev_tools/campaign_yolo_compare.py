"""Compare PyTorch and ONNX on identical normalized inputs before packaging a checkpoint."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dev_tools.campaign_yolo_dataset import load_frame, read_jsonl
from module.campaign_prototype.detection import Detector, decode, letterbox, scene_tiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--frames', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(4)
    model = Detector(args.model)
    checkpoint_hash = hashlib.sha256(args.weights.read_bytes()).hexdigest()
    if checkpoint_hash != model.metadata['checkpoint_sha256']:
        raise ValueError('ONNX metadata does not identify the supplied checkpoint.')
    network = YOLO(str(args.weights)).model.float().cpu().eval().fuse(verbose=False)
    counts, samples = Counter(), []
    for record in read_jsonl(args.frames):
        domain = record.get('roi_kind', record['domain'])
        if record.get('review_scope') != 'full_frame' or counts[domain] >= 3:
            continue
        image = load_frame(record)
        tiles = [tile for tile, _ in scene_tiles(image)] if domain == 'scene' else [image]
        for tile in tiles:
            tensor, scales, padding = letterbox(tile, model.size)
            with torch.no_grad():
                expected = network(torch.from_numpy(tensor))[0].numpy()
            actual = model.session.run([model.output_name], {model.input_name: tensor})[0]
            delta = np.abs(expected - actual)
            left = decode(expected, scales, padding, model.thresholds, tile.shape)
            right = decode(actual, scales, padding, model.thresholds, tile.shape)
            passed = (delta[:, :4].max() < .02 and delta[:, 4:].max() < .0002
                      and [x.label for x in left] == [x.label for x in right]
                      and all(np.max(np.abs(np.asarray(a.box) - b.box)) < .02 for a, b in zip(left, right)))
            samples.append(dict(id=record['id'], domain=domain, max_box_delta=float(delta[:, :4].max()),
                                max_score_delta=float(delta[:, 4:].max()), passed=bool(passed)))
        counts[domain] += 1
    result = dict(passed=bool(samples) and all(s['passed'] for s in samples), domains=dict(counts), samples=samples,
                  checkpoint_sha256=checkpoint_hash, model_sha256=model.metadata['sha256'])
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'samples'}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
