"""Train campaign detections locally, or export a reviewed checkpoint for the Python 3.9 runtime."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from module.campaign_prototype.detection import LABELS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['train', 'export'])
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--weights', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--name', default='campaign_v1')
    parser.add_argument('--epochs', type=int, default=80)
    parser.add_argument('--batch', type=int, default=32)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    for name in ('data', 'weights', 'destination'):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    config = workspace / 'ultralytics'
    config.mkdir(exist_ok=True)
    os.environ['YOLO_CONFIG_DIR'] = str(config)
    os.chdir(workspace)
    from ultralytics import YOLO, settings
    settings.update({'sync': False, 'weights_dir': str(workspace / 'weights'),
                     'runs_dir': str(workspace / 'runs')})
    versions = {p: importlib.metadata.version(p) for p in
                ('torch', 'torchvision', 'ultralytics', 'onnx', 'onnxruntime', 'numpy', 'opencv-python')}
    if args.action == 'train':
        if args.data is None:
            parser.error('train requires --data')
        model = YOLO(str(args.weights or workspace / 'weights/yolo11n.pt'))
        run = workspace / 'runs' / args.name
        if run.exists():
            raise FileExistsError(f'Use a new run name, or explicitly resume the original training job: {run}')
        manifest = args.data.resolve().parent / 'manifest.jsonl'
        provenance = dict(versions=versions, dataset=str(args.data.resolve()),
                          manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                          train_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          train_list_sha256=hashlib.sha256((manifest.parent / 'train.txt').read_bytes()).hexdigest(),
                          dataset_summary=json.loads((manifest.parent / 'summary.json').read_text('utf-8')),
                          labels=list(LABELS), seed=20261002,
                          annotation_note='Acceptance requires unmasked runtime replay and acquisition-group isolation.')
        (workspace / f'{args.name}-provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
        model.train(data=str(args.data.resolve()), project=str(workspace / 'runs'), name=args.name,
                    epochs=args.epochs, patience=20, imgsz=640, batch=args.batch, device=0, workers=args.workers,
                    seed=20261002, deterministic=False, cache=False, amp=True,
                    degrees=10, shear=12, perspective=.0005, scale=.6, translate=.15,
                    mosaic=.7, close_mosaic=10, mixup=.05, fliplr=0., flipud=0.,
                    hsv_h=.01, hsv_s=.3, hsv_v=.3, plots=True, save_period=10)
    else:
        if args.weights is None or args.destination is None:
            parser.error('export requires --weights and --destination')
        model = YOLO(str(args.weights.resolve()))
        if list(model.names.values()) != list(LABELS):
            raise ValueError('Checkpoint classes do not match the campaign detector.')
        exported = Path(model.export(format='onnx', imgsz=640, batch=1, dynamic=False, half=False,
                                     simplify=False, opset=17, nms=False, device='cpu'))
        args.destination.mkdir(parents=True, exist_ok=True)
        target = args.destination / 'campaign.onnx'
        shutil.copyfile(exported, target)
        metadata = dict(schema_version=1, format='yolo11-detect-bcn-fp32', labels=list(LABELS),
                        input_size=640, sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                        thresholds={label: .5 for label in LABELS}, versions=versions,
                        checkpoint_sha256=hashlib.sha256(args.weights.read_bytes()).hexdigest(),
                        acceptance='pending_independent_validation')
        (args.destination / 'campaign.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        print(json.dumps(metadata), flush=True)


if __name__ == '__main__':
    main()
