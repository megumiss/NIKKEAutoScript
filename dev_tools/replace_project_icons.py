#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import io
from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]


ICON_TARGETS: List[Tuple[Path, Tuple[int, int], str]] = [
    (Path('webapp/buildResources/icon.png'), (256, 256), 'main app icon (png)'),
    (Path('webapp/buildResources/icon.ico'), (256, 256), 'main app icon (ico)'),
    (Path('webapp/buildResources/icon.icns'), (256, 256), 'main app icon (icns)'),
    (Path('assets/gui/icon/nkas.svg'), (256, 256), 'webui logo icon (svg)'),
    (Path('webapp/packages/main/public/icon.png'), (256, 256), 'electron tray icon source'),
    (Path('assets/gui/icon/nkas.ico'), (256, 256), 'windows notification fallback icon'),
    (Path('dev_tools/cookie/icon16.png'), (16, 16), 'cookie extension icon 16'),
    (Path('dev_tools/cookie/icon48.png'), (48, 48), 'cookie extension icon 48'),
    (Path('dev_tools/cookie/icon128.png'), (128, 128), 'cookie extension icon 128'),
]


def _open_rgba(path: Path) -> Image.Image:
    with Image.open(path) as img:
        return img.convert('RGBA')


def _fit_center(src: Image.Image, size: Tuple[int, int]) -> Image.Image:
    tw, th = size
    sw, sh = src.size
    if sw <= 0 or sh <= 0:
        raise ValueError(f'Invalid source image size: {src.size}')
    scale = min(tw / sw, th / sh)
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    ox = (tw - nw) // 2
    oy = (th - nh) // 2
    canvas.paste(resized, (ox, oy), resized)
    return canvas


def _ico_sizes(max_side: int) -> List[Tuple[int, int]]:
    candidates = [16, 24, 32, 48, 64, 128, 256]
    return [(s, s) for s in candidates if s <= max_side]


def _save_icon(image: Image.Image, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix == '.png':
        image.save(target, format='PNG', optimize=True)
        return
    if suffix == '.ico':
        max_side = min(max(image.size), 256)
        sizes = _ico_sizes(max_side) or [image.size]
        image.save(target, format='ICO', sizes=sizes)
        return
    if suffix == '.icns':
        image.save(target, format='ICNS')
        return
    if suffix == '.svg':
        _save_svg_embedded_png(image, target)
        return
    raise ValueError(f'Unsupported icon format: {target}')


def _save_svg_embedded_png(image: Image.Image, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    image.save(buf, format='PNG', optimize=True)
    payload = base64.b64encode(buf.getvalue()).decode('ascii')
    svg = (
        '<svg class="alas-icon" xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        f'<image xlink:href="data:image/png;base64,{payload}" ></image>'
        '</svg>\n'
    )
    target.write_text(svg, encoding='utf-8')


def _iter_targets() -> Iterable[Tuple[Path, Tuple[int, int], str]]:
    for rel_path, size, note in ICON_TARGETS:
        yield PROJECT_ROOT / rel_path, size, note


def cmd_list() -> int:
    print('Project icon targets:')
    for abs_path, size, note in _iter_targets():
        rel = abs_path.relative_to(PROJECT_ROOT)
        exists = 'exists' if abs_path.exists() else 'missing'
        print(f'- {rel} | size={size[0]}x{size[1]} | {exists} | {note}')
    return 0


def cmd_replace(source: Path, dry_run: bool) -> int:
    src_abs = source if source.is_absolute() else (PROJECT_ROOT / source)
    if not src_abs.exists():
        raise FileNotFoundError(f'Source image not found: {src_abs}')

    src_img = _open_rgba(src_abs)
    print(f'Source: {src_abs} ({src_img.size[0]}x{src_img.size[1]})')
    print(f'Dry-run: {dry_run}')

    for abs_path, size, note in _iter_targets():
        rel = abs_path.relative_to(PROJECT_ROOT)
        out = _fit_center(src_img, size)
        if dry_run:
            print(f'[DRY] would replace: {rel} -> {size[0]}x{size[1]} ({note})')
            continue
        _save_icon(out, abs_path)
        print(f'[OK ] replaced: {rel} -> {size[0]}x{size[1]} ({note})')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Replace project icons from one source image.',
    )
    parser.add_argument(
        '--source',
        type=Path,
        help='Source icon image path (png/jpg/webp etc). Required unless --list is used.',
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List all icon targets used by the project.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview replacements without writing files.',
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list:
        return cmd_list()

    if not args.source:
        parser.error('--source is required unless --list is used.')

    return cmd_replace(args.source, args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
