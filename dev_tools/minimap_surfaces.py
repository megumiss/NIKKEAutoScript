"""把分层包里每帧的局部平面按三维 (x, y, 高度) 连通性聚成章节级表面，输出带 surface_id 的新包与审阅图。

python -m dev_tools.minimap_surfaces --package <local_parallax 包目录> --output <新目录>

不改几何、深度或相机，只给每个局部平面补章节级表面编号；表面身份与跨层连通仍需独立验收。
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage

from dev_tools.minimap_layered import project
from dev_tools.minimap_region_redraw import plane_ratio
from dev_tools.minimap_verify import LayeredPackage
from module.campaign_prototype.local_projection import frame_plane_to_map

PALETTE = np.array([[59, 139, 186], [86, 186, 103], [226, 141, 55], [160, 95, 224], [223, 88, 120],
                    [75, 196, 201], [199, 177, 60], [120, 120, 220], [220, 120, 160], [100, 170, 120]], np.uint8)


def sha(path):
    """绑定输入与代码，便于复现。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plane_nodes(package, labels, minimum_pixels=400, scale=.5):
    """每个 (帧, 局部平面) 生成半分辨率地图栅格上的高度图，供三维连通与两两比较。"""
    meta = package.meta
    height, width = package.images[0].shape[:2]
    pixels = np.indices((height, width))[::-1].transpose(1, 2, 0).reshape(-1, 2).astype(float)
    rectified = project(package.projection, pixels)
    canvas = (int(np.ceil(package.size[0] * scale)), int(np.ceil(package.size[1] * scale)))
    shrink = np.array([[scale, 0, 0], [0, scale, 0], [0, 0, 1.]])
    nodes = []
    for frame, entry in enumerate(meta['frame_support']):
        road = (package.roads[frame] > 0) & (package.valid > 0)
        for label, plane in enumerate(entry['local_planes']):
            if plane is None:
                continue
            mask = road & (labels[frame] == label)
            if np.count_nonzero(mask) < minimum_pixels:
                continue
            ratio = plane_ratio(plane, rectified).reshape(height, width)
            # 高度用相对相机高度 1 - 1/ratio 表示，与 depth_model 的定义一致。
            elevation = np.where(mask, 1 - 1 / ratio, np.nan).astype(np.float32)
            matrix = shrink @ frame_plane_to_map(meta, frame, plane)
            warped = cv2.warpPerspective(elevation, matrix, canvas, flags=cv2.INTER_NEAREST,
                                         borderMode=cv2.BORDER_CONSTANT, borderValue=float('nan'))
            ys, xs = np.nonzero(np.isfinite(warped))
            if len(xs) < minimum_pixels * scale * scale:
                continue
            box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
            nodes.append(dict(frame=frame, label=label, box=box, elevation=warped[box[1]:box[3], box[0]:box[2]],
                              pixels=int(len(xs))))
    return nodes, canvas


def node_voxels(node, low, bin_size):
    """返回节点有观测像素的画布坐标与高度格编号。"""
    left, top, _, _ = node['box']
    patch = node['elevation']
    ys, xs = np.nonzero(np.isfinite(patch))
    levels = np.rint((patch[ys, xs] - low) / bin_size).astype(int)
    return xs + left, ys + top, levels


def voxel_surfaces(nodes, canvas, bin_size=.03):
    """在 (x, y, 高度) 三维栅格上做 26 邻域连通分量；同一位置不同高度的道路不连通，坡道经相邻高度格相连。

    返回每个节点的表面编号（按体素数降序重编号）以及各表面的体素数。
    """
    width, height = canvas
    elevations = np.concatenate([node['elevation'][np.isfinite(node['elevation'])] for node in nodes])
    low = float(np.floor(elevations.min() / bin_size) * bin_size)
    bins = int(np.ceil((elevations.max() - low) / bin_size)) + 2
    occupancy = np.zeros((bins, height, width), np.uint8)
    for node in nodes:
        xs, ys, levels = node_voxels(node, low, bin_size)
        occupancy[levels, ys, xs] = 1
    labeled, _ = ndimage.label(occupancy, structure=np.ones((3, 3, 3), np.uint8))
    sizes = np.bincount(labeled.ravel())
    components = []
    for node in nodes:
        xs, ys, levels = node_voxels(node, low, bin_size)
        votes = np.bincount(labeled[levels, ys, xs])
        votes[0] = 0
        components.append(int(np.argmax(votes)))
    order = {component: rank for rank, component in
             enumerate(sorted(set(components), key=lambda component: -int(sizes[component])))}
    identities = [order[component] for component in components]
    voxels = {order[component]: int(sizes[component]) for component in order}
    return identities, voxels, dict(bin_size=bin_size, low=low, bins=bins)


def overlap(first, second):
    """返回两个节点在公共包围盒内都有观测的高度差数组。"""
    left, top = max(first['box'][0], second['box'][0]), max(first['box'][1], second['box'][1])
    right, bottom = min(first['box'][2], second['box'][2]), min(first['box'][3], second['box'][3])
    if right <= left or bottom <= top:
        return np.zeros(0, np.float32)
    a = first['elevation'][top - first['box'][1]:bottom - first['box'][1], left - first['box'][0]:right - first['box'][0]]
    b = second['elevation'][top - second['box'][1]:bottom - second['box'][1],
                            left - second['box'][0]:right - second['box'][0]]
    both = np.isfinite(a) & np.isfinite(b)
    return (a[both] - b[both]).astype(np.float32)


def overlap_statistics(nodes, identities, minimum_overlap=50, same_height=.025, distinct_height=.06):
    """两两重叠的诊断统计：同表面与跨表面各有多少高度一致、模糊、明显不同的重叠。"""
    counts = {key: 0 for key in ('same_surface_agree', 'same_surface_ambiguous', 'same_surface_distinct',
                                 'cross_surface_agree', 'cross_surface_ambiguous', 'cross_surface_distinct')}
    for i, first in enumerate(nodes):
        for j in range(i + 1, len(nodes)):
            second = nodes[j]
            if first['frame'] == second['frame']:
                continue
            delta = overlap(first, second)
            if len(delta) < minimum_overlap:
                continue
            median = float(np.median(np.abs(delta)))
            prefix = 'same_surface_' if identities[i] == identities[j] else 'cross_surface_'
            kind = 'agree' if median <= same_height else 'distinct' if median >= distinct_height else 'ambiguous'
            counts[prefix + kind] += 1
    return counts


def describe_surfaces(nodes, identities, voxels, canvas):
    """按表面汇总成员、像素数、高度分布与全局仿射高度拟合，并生成显示用的表面编号栅格。"""
    width, height = canvas
    display = np.full((height, width), -1, np.int16)
    best = np.full((height, width), -np.inf, np.float32)
    surfaces = {}
    for node, identity in zip(nodes, identities):
        left, top, right, bottom = node['box']
        patch = node['elevation']
        finite = np.isfinite(patch)
        region = best[top:bottom, left:right]
        choose = finite & (np.nan_to_num(patch, nan=-np.inf) > region)
        region[choose] = patch[choose]
        display[top:bottom, left:right][choose] = identity
        entry = surfaces.setdefault(identity, dict(id=int(identity), nodes=[], samples=[]))
        entry['nodes'].append([node['frame'], node['label']])
        ys, xs = np.nonzero(finite)
        step = max(1, len(xs) // 400)
        entry['samples'].append(np.column_stack([xs[::step] + left, ys[::step] + top, patch[ys[::step], xs[::step]]]))
    records = []
    for identity in sorted(surfaces):
        entry = surfaces[identity]
        samples = np.concatenate(entry['samples'])
        design = np.column_stack([samples[:, :2] / 500, np.ones(len(samples))])
        coefficients, *_ = np.linalg.lstsq(design, samples[:, 2], rcond=None)
        residual = np.abs(design @ coefficients - samples[:, 2])
        records.append(dict(id=entry['id'], nodes=entry['nodes'], frames=len({n[0] for n in entry['nodes']}),
                            voxels=voxels.get(identity, 0), display_pixels=int(np.count_nonzero(display == identity)),
                            elevation_median=float(np.median(samples[:, 2])),
                            elevation_spread=float(np.percentile(samples[:, 2], 90) - np.percentile(samples[:, 2], 10)),
                            affine_fit=dict(coefficients=coefficients.tolist(), coordinate_scale=500,
                                            residual_p90=float(np.percentile(residual, 90)))))
    return records, display


def render_preview(package, display):
    """把表面编号着色后叠在底图上，多表面叠置处显示较高的表面。"""
    base = cv2.resize(cv2.imread(str(package.path / 'map.png')), display.shape[::-1], interpolation=cv2.INTER_AREA)
    overlay = base.copy()
    for identity in np.unique(display[display >= 0]):
        overlay[display == identity] = PALETTE[identity % len(PALETTE)]
    return cv2.addWeighted(base, .35, overlay, .65, 0)


def assign_surfaces(package_dir, output, bin_size=.03):
    """读取基线包、三维连通聚类并写出带 surface_id 的新包；输入包保持不变。"""
    source, output = Path(package_dir).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a new output directory outside the input package')
    package = LayeredPackage(source)
    if package.hash_mismatches:
        raise ValueError('Source frame hash mismatch')
    labels = []
    for index in range(len(package.images)):
        with np.load(source / 'depth' / f'frame_{index:05d}.npz', allow_pickle=False) as data:
            labels.append(data['local_surface'])
    print(f'Projecting local planes from {len(package.images)} frames', flush=True)
    nodes, canvas = plane_nodes(package, labels)
    if len(nodes) < 2:
        raise ValueError('Too few local planes with road support')
    print(f'Clustering {len(nodes)} planes in (x, y, elevation)', flush=True)
    identities, voxels, grid = voxel_surfaces(nodes, canvas, bin_size)
    statistics = overlap_statistics(nodes, identities)
    surfaces, display = describe_surfaces(nodes, identities, voxels, canvas)
    meta = json.loads(json.dumps(package.meta))
    for node, identity in zip(nodes, identities):
        meta['frame_support'][node['frame']]['local_planes'][node['label']]['surface_id'] = int(identity)
    output.mkdir(parents=True)
    for folder in ('source', 'depth', 'regions'):
        if (source / folder).exists():
            shutil.copytree(source / folder, output / folder)
    for name in ('map.png', 'reference.png', 'surface_data.npz', 'tracks.json', 'surface_model.json',
                 'redraw_data.npz', 'redraw_report.json', 'annotations.json', 'connectivity.json'):
        if (source / name).exists():
            shutil.copyfile(source / name, output / name)
    cv2.imwrite(str(output / 'surfaces_preview.png'), render_preview(package, display))
    np.savez_compressed(output / 'surfaces.npz', display_surface_id=display, display_scale=.5)
    root = Path(__file__).resolve().parents[1]
    meta['surfaces'] = surfaces
    meta['surface_clustering'] = dict(
        method='voxel_connected_components', input=str(source), planes=len(nodes), surfaces=len(surfaces),
        grid=grid, overlap_statistics=statistics,
        display='surfaces_preview.png shows the higher surface where several overlap',
        code_sha256={name: sha(root / name) for name in ('dev_tools/minimap_surfaces.py',
                                                          'dev_tools/minimap_verify.py',
                                                          'module/campaign_prototype/local_projection.py')},
        limitations=['Surface ids come from (x, y, elevation) connectivity of projected local planes; they are '
                     'not verified walkable layers and do not establish cross-layer connectivity.',
                     'Planes without enough road support keep no surface id.',
                     'Cross-surface overlaps with distinct elevation mark stacked roads for review only.'])
    (output / 'map.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    (output / 'surface_report.json').write_text(json.dumps(dict(meta['surface_clustering'], surfaces=surfaces),
                                                           indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(output), planes=len(nodes), surfaces=len(surfaces), overlap=statistics,
                          largest=[(s['id'], s['frames'], s['display_pixels'], round(s['elevation_median'], 3))
                                   for s in surfaces[:8]])), flush=True)
    return meta['surface_clustering']


def main():
    """只消费本机分层包，不连接游戏，不改写输入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bin-size', type=float, default=.03)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    assign_surfaces(args.package, args.output, args.bin_size)


if __name__ == '__main__':
    main()
