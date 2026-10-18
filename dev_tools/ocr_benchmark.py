"""
Full-screen OCR benchmark on saved frames, without touching the device.

Times model init and, per image, detection / recognition / everything else
inside `predict()`, then records texts, boxes and the location of target
texts exactly as `ModuleBase.appear_text` would resolve them. Save a baseline
run with --out, then rerun with other options and --compare against it.

Examples (from the repository root):
    python -m dev_tools.ocr_benchmark --images ./frames --out base.json
    taskset -c 2,3 python -m dev_tools.ocr_benchmark --images ./frames --threads 2
    python -m dev_tools.ocr_benchmark --images ./frames --det-limit-side-len 960 \\
        --det-limit-type max --compare base.json
    python -m dev_tools.ocr_benchmark --images ./frames --sidecar "<OcrSidecarCommand>" --compare base.json
"""
import argparse
import json
import os
import platform
import time

import numpy as np

from module.base.langs import Langs
from module.base.utils import load_image
from module.ocr.models import OCR_MODEL, paddle_options
from module.ocr.ocr import Ocr

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp')
DEFAULT_TARGETS = ('CLAIM_ALL', 'CLAIM_REWARD')


class StageTimer:
    """Wrap a PaddleX sub-model and accumulate the wall time of its calls."""

    def __init__(self, inner):
        self.inner = inner
        self.elapsed = 0.0
        self.batches = 0

    def __call__(self, *args, **kwargs):
        start = time.perf_counter()
        # Pipelines consume these generators lazily, so materialise them here
        # to attribute the inference time to this stage.
        result = list(self.inner(*args, **kwargs))
        self.elapsed += time.perf_counter() - start
        self.batches += 1
        return iter(result)

    def __getattr__(self, item):
        return getattr(self.inner, item)

    def reset(self):
        self.elapsed = 0.0
        self.batches = 0


def list_images(folder):
    files = [
        os.path.join(folder, name) for name in sorted(os.listdir(folder))
        if name.lower().endswith(IMAGE_EXTENSIONS)
    ]
    images = []
    for file in files:
        try:
            image = load_image(file)
        except OSError as e:
            # Error snapshots written while the process was being killed can be truncated.
            print(f'Skip unreadable image {file}: {e}')
            continue
        # Blank frames from a bridge that has not rendered yet carry no text.
        if image.size and int(image.max()) > 8:
            images.append((os.path.basename(file), image))
    return images


def peak_rss_mb():
    try:
        with open('/proc/self/status', encoding='utf-8') as f:
            for line in f:
                if line.startswith('VmHWM:'):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def effective_options(model):
    pipeline = model.paddlex_pipeline
    det, rec = pipeline.text_det_model, pipeline.text_rec_model
    info = {
        'det_limit_side_len': getattr(pipeline, 'text_det_limit_side_len', None),
        'det_limit_type': getattr(pipeline, 'text_det_limit_type', None),
        'det_max_side_limit': getattr(pipeline, 'text_det_max_side_limit', None),
        'rec_batch_size': getattr(getattr(rec, 'batch_sampler', None), 'batch_size', None),
    }
    for name, sub in (('det', det), ('rec', rec)):
        option = getattr(sub, 'pp_option', None)
        if option is not None:
            info[f'{name}_run_mode'] = getattr(option, 'run_mode', None)
            info[f'{name}_cpu_threads'] = getattr(option, 'cpu_threads', None)
            info[f'{name}_device'] = getattr(option, 'device_type', None)
    return info


def run(args):
    options = paddle_options(args.lang, args.model_type)
    if args.threads:
        options['cpu_threads'] = args.threads
    if args.rec_batch_size:
        options['text_recognition_batch_size'] = args.rec_batch_size
    predict_kwargs = {}
    if args.det_limit_side_len:
        predict_kwargs['text_det_limit_side_len'] = args.det_limit_side_len
    if args.det_limit_type:
        predict_kwargs['text_det_limit_type'] = args.det_limit_type

    images = list_images(args.images)
    if not images:
        raise SystemExit(f'No usable images in {args.images}')

    start = time.perf_counter()
    if args.sidecar:
        if predict_kwargs or args.threads or args.rec_batch_size:
            raise SystemExit('--sidecar uses the runtime options; drop the model override flags')
        from module.ocr.remote import RemoteOcr

        # Same path as the worker with OcrSidecarCommand set; this process never imports paddle.
        model = RemoteOcr(args.lang, args.model_type, 0, args.sidecar)
        model.predict([images[0][1]])
        det = rec = StageTimer(None)
        versions = effective = None
    else:
        import paddle
        import paddleocr
        import paddlex
        from module.ocr.nikke_ocr import NIKKEOcr

        model = NIKKEOcr(**options)
        # PaddleX wraps the real pipeline (AutoParallel...Pipeline._pipeline); the
        # wrapper only delegates attribute reads, so patch the inner object.
        pipeline = getattr(model.paddlex_pipeline, '_pipeline', model.paddlex_pipeline)
        det = pipeline.text_det_model = StageTimer(pipeline.text_det_model)
        rec = pipeline.text_rec_model = StageTimer(pipeline.text_rec_model)
        versions = {'paddle': paddle.__version__, 'paddleocr': paddleocr.__version__, 'paddlex': paddlex.__version__}
        effective = effective_options(model)
    init_seconds = time.perf_counter() - start
    # Ocr is only used for its result post-processing, identical to appear_text.
    post = Ocr(buttons=[], lang=args.lang, model_type=args.model_type)
    targets = {name: getattr(Langs, name, None) or name for name in args.targets}

    if args.warmup:
        model.predict([images[0][1]], **predict_kwargs)

    records = []
    for name, image in images:
        for _ in range(args.repeat):
            det.reset()
            rec.reset()
            start = time.perf_counter()
            raw = model.predict([image], **predict_kwargs)
            predict_seconds = time.perf_counter() - start
            start = time.perf_counter()
            result = post._process_ocr_result(raw, args.threshold)
            post_seconds = time.perf_counter() - start
            boxes = sum(len(page.get('rec_texts', [])) for page in raw)
            records.append({
                'image': name,
                'predict': round(predict_seconds, 3),
                'det': round(det.elapsed, 3),
                'rec': round(rec.elapsed, 3),
                'other': round(predict_seconds - det.elapsed - rec.elapsed, 3),
                'post': round(post_seconds, 4),
                'boxes': boxes,
                'rec_batches': rec.batches,
                'texts': [d['text'] for d in result['details']],
                'bboxes': [np.asarray(d['bbox']).tolist() for d in result['details']],
                'targets': {
                    key: OCR_MODEL.get_location(text, result) for key, text in targets.items()
                },
            })
            print(
                f"{name}: predict {predict_seconds:.2f}s det {det.elapsed:.2f}s "
                f"rec {rec.elapsed:.2f}s boxes {boxes} lines {len(result['details'])}",
                flush=True,
            )

    summary = {
        'platform': platform.platform(),
        'affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
        'sidecar': bool(args.sidecar),
        'versions': versions,
        'options': {k: v for k, v in options.items() if k != 'device'},
        'predict_kwargs': predict_kwargs,
        'effective': effective,
        'init_seconds': round(init_seconds, 2),
        'images': len(images),
    }
    summary['peak_rss_mb'] = peak_rss_mb()
    for key in ('predict', 'det', 'rec', 'other'):
        values = [r[key] for r in records]
        summary[f'{key}_mean'] = round(float(np.mean(values)), 3)
        summary[f'{key}_median'] = round(float(np.median(values)), 3)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary, records


def compare(records, baseline_file, tolerance):
    with open(baseline_file, encoding='utf-8') as f:
        baseline = {r['image']: r for r in json.load(f)['records']}
    changed = 0
    for record in records:
        base = baseline.get(record['image'])
        if base is None:
            continue
        old, new = set(base['texts']), set(record['texts'])
        missing, extra = sorted(old - new), sorted(new - old)
        moved = {}
        for key, location in record['targets'].items():
            before = base['targets'].get(key)
            if (before is None) != (location is None):
                moved[key] = (before, location)
            elif before is not None and max(abs(a - b) for a, b in zip(before, location)) > tolerance:
                moved[key] = (before, location)
        if missing or extra or moved:
            changed += 1
            print(f"[diff] {record['image']}: missing={missing} extra={extra} targets={moved}")
    print(f'Compared {len(records)} records against {baseline_file}: {changed} differ')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--images', required=True, help='Folder of saved 720x1280 RGB frames')
    parser.add_argument('--lang', default='ch', choices=('ch', 'en'))
    parser.add_argument('--model-type', default='mobile', choices=('mobile', 'server'))
    parser.add_argument('--threads', type=int, default=0, help='Override Optimization.OcrThreads')
    parser.add_argument('--rec-batch-size', type=int, default=0)
    parser.add_argument('--det-limit-side-len', type=int, default=0)
    parser.add_argument('--det-limit-type', choices=('min', 'max'))
    parser.add_argument('--threshold', type=float, default=0.51, help='Same default as Ocr.ocr')
    parser.add_argument('--targets', nargs='*', default=list(DEFAULT_TARGETS),
                        help='Langs attribute names or literal texts to locate')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--no-warmup', dest='warmup', action='store_false')
    parser.add_argument('--sidecar', help='Run through the OCR sidecar started by this command (OcrSidecarCommand)')
    parser.add_argument('--out', help='Write summary and records as JSON')
    parser.add_argument('--compare', help='Baseline JSON written by --out')
    parser.add_argument('--tolerance', type=float, default=6, help='Allowed target shift in pixels')
    args = parser.parse_args()

    summary, records = run(args)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump({'summary': summary, 'records': records}, f, ensure_ascii=False, indent=1)
    if args.compare:
        compare(records, args.compare, args.tolerance)


if __name__ == '__main__':
    main()
