"""Explicit paths for standalone experiments; imports never read local capture data."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data/campaign_prototype'
package = DATA / 'chapter_38'
output = ROOT / 'log/campaign_prototype'
legacy_cache = DATA / 'legacy_chapter_41'
reference_dir = DATA / 'wiki_references'
chapter = 38
difficulty = 'normal'
stop_file = output / 'STOP'


def arguments(parser):
    """为各独立实验入口添加一致的地图包、章节、难度、输出与停止参数。"""
    parser.add_argument('--package', type=Path, default=package)
    parser.add_argument('--chapter', type=int, default=chapter)
    parser.add_argument('--difficulty', choices=['normal', 'hard'], default=difficulty)
    parser.add_argument('--output', type=Path, default=output)
    parser.add_argument('--stop-file', type=Path, default=stop_file)
    parser.add_argument('--legacy-cache', type=Path, default=legacy_cache)
    parser.add_argument('--reference-dir', type=Path, default=reference_dir)


def configure(args):
    """解析并保存绝对路径，限制文件标签字符，创建输出目录但不读写个人配置。"""
    global package, output, chapter, difficulty, stop_file, legacy_cache, reference_dir
    package, output = args.package.resolve(), args.output.resolve()
    chapter, difficulty = args.chapter, args.difficulty
    stop_file, legacy_cache = args.stop_file.resolve(), args.legacy_cache.resolve()
    reference_dir = args.reference_dir.resolve()
    for name in ('tag', 'prefix', 'from_tag'):
        value = getattr(args, name, None)
        if value is not None and (not value or any(c not in 'abcdefghijklmnopqrstuvwxyz'
                                                   'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in value)):
            raise ValueError(f'{name} must be an ASCII filename tag')
    output.mkdir(parents=True, exist_ok=True)


def child_arguments():
    """把父进程当前地图身份与路径完整传给子进程，避免子进程退回默认数据。"""
    return ['--package', str(package), '--chapter', str(chapter), '--difficulty', difficulty,
            '--output', str(output), '--stop-file', str(stop_file), '--legacy-cache', str(legacy_cache),
            '--reference-dir', str(reference_dir)]


def driver_args():
    """集中声明当前验证过的客户区、展开 ROI 和地图开关坐标。"""
    return argparse.Namespace(driver_root=ROOT, roi=[644, 280, 1130, 742], client=[1776, 999],
                              step=180, settle=0.5, map_open=[42, 98], stop_file=stop_file)
