"""从切开道路的骨架识别分流后汇合的路段，不把宽路网格中的小环当作岔路。"""

import hashlib
import os
from pathlib import Path
import tempfile
from zipfile import BadZipFile
import zlib

import cv2
import networkx as nx
import numpy as np

from module.campaign.map.map_state import ROAD_TOLERANCE
from module.logger import logger

# 小于两个观测格的骨架环属于道路边缘的小孔，不扩大清理范围。
MIN_LOOP_SPAN = 64
# 骨架或归属规则变化时同步递增，防止旧算法结果被新进程复用。
TOPOLOGY_CACHE_VERSION = 1
TOPOLOGY_CACHE_ROOT = Path(__file__).resolve().parents[3] / 'data/campaign_cache/topology'


def _cache_key(road):
    """由道路实际内容、形状、算法版本与 OpenCV 版本确定本地缓存文件。

    不使用章节号或封锁编号替代内容摘要；编辑底图、改变切口或升级细化实现后均重新构建。
    文件位于独立本地数据目录，不写入资源仓库，也不保存可执行的 pickle 对象。
    """
    header = (TOPOLOGY_CACHE_VERSION, cv2.__version__, MIN_LOOP_SPAN, road.shape, road.dtype.str)
    digest = hashlib.sha256(repr(header).encode('utf-8'))
    digest.update(np.ascontiguousarray(road))
    return f'v{TOPOLOGY_CACHE_VERSION}_{digest.hexdigest()}'


def _mask_digest(skeleton, owners):
    """计算缓存数组的内容校验和，读取时拒绝完整 ZIP 内被损坏或错配的数据。"""
    digest = hashlib.sha256(np.ascontiguousarray(skeleton))
    digest.update(np.ascontiguousarray(owners))
    return digest.hexdigest()


def _load_masks(path, road):
    """只读取匹配道路版本的骨架与像素归属，缺失或损坏时返回 None 交原算法重建。

    图的邻接插入顺序不序列化，由骨架按原顺序重新生成，避免最短路同距分支发生变化。
    dtype、形状、编号范围及骨架节点自身归属均须有效，不能将部分可读缓存当作完整结果。
    """
    try:
        archive = np.load(path, allow_pickle=False)
        if not isinstance(archive, np.lib.npyio.NpzFile):
            raise ValueError('拓扑缓存不是 NPZ 数据包')
        with archive as data:
            skeleton, owners = data['skeleton'], data['owners']
            if (data['key'].item() != path.stem or skeleton.shape != road.shape or owners.shape != road.shape
                    or skeleton.dtype != np.bool_ or owners.dtype != np.int32):
                raise ValueError('拓扑缓存的版本、形状或类型不匹配')
            count = int(np.count_nonzero(skeleton))
            if (np.any(skeleton & (road == 0)) or np.any(owners < 0) or owners.max(initial=0) > count
                    or not np.array_equal(owners[skeleton], np.arange(1, count + 1))):
                raise ValueError('拓扑缓存的骨架或归属编号无效')
            if data['checksum'].item() != _mask_digest(skeleton, owners):
                raise ValueError('拓扑缓存校验和不匹配')
        return skeleton, owners
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, EOFError, BadZipFile, zlib.error) as exc:
        logger.warning(f'道路拓扑缓存读取失败，将重新构建：{path}；原因：{exc}')
        return None


def _save_masks(path, skeleton, owners):
    """原子保存非执行性数组缓存；写盘失败只告警，不影响本次已计算的拓扑。

    临时文件与目标位于同一目录，多个任务同时构建相同版本时不会读到半写入文件。
    仅清理本次创建的临时文件，不删除历史道路版本或其它任务的缓存。
    """
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=path.stem + '_', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, key=path.stem, skeleton=skeleton, owners=owners,
                                checksum=_mask_digest(skeleton, owners))
        os.replace(temporary, path)
    except OSError as exc:
        logger.warning(f'道路拓扑缓存写入失败，继续使用内存结果：{path}；原因：{exc}')
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(f'道路拓扑临时缓存清理失败：{temporary}；原因：{exc}')


class RoadTopology:
    """一版切开图的骨架和双连通路段；仅确定道路归属，实际移动仍须通过 A*。

    像素归属沿道路传播，不能直接按直线最近骨架分配，否则窄墙两侧会混成一条支路。
    双连通段只在起终点间的简单路径经过时选入，单入口的旁支环和死胡同不算沿途岔路。
    """

    def __init__(self, road, cache_directory=TOPOLOGY_CACHE_ROOT):
        """在全分辨率道路上细化并建图，复用同一道路内容的本地骨架与归属缓存。

        cache_directory=None 用于离线等价性检查，完全绕过磁盘缓存。构图与环筛选保持原
        顺序；补零边界防止贴图边的道路无法细化，读取失败时正常重算，不改变规划判据。
        """
        self.road = road
        self.cache_key = _cache_key(road)
        path = Path(cache_directory) / f'{self.cache_key}.npz' if cache_directory is not None else None
        cached = _load_masks(path, road) if path is not None else None
        skeleton = (cv2.ximgproc.thinning(np.pad(road.astype(np.uint8) * 255, 1))[1:-1, 1:-1] > 0
                    if cached is None else cached[0])
        rows, columns = np.nonzero(skeleton)
        self.points = np.vstack(([-1, -1], np.column_stack((columns, rows))))
        index = np.zeros(road.shape, np.int32)
        index[rows, columns] = np.arange(1, len(rows) + 1)
        graph = nx.Graph()
        graph.add_nodes_from(range(1, len(rows) + 1))
        height, width = road.shape
        for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
            ny, nx_ = rows + dy, columns + dx
            keep = (ny < height) & (nx_ >= 0) & (nx_ < width)
            y, x, ny, nx_ = rows[keep], columns[keep], ny[keep], nx_[keep]
            linked = index[ny, nx_] > 0
            if dy and dx:
                # 正交中间点已连通时去掉斜边，避免拐角的三个像素形成假环。
                linked &= (index[y, nx_] == 0) & (index[ny, x] == 0)
            graph.add_edges_from(zip(index[y[linked], x[linked]], index[ny[linked], nx_[linked]]))
        self.graph = graph
        self.cycles = []
        for edges in nx.biconnected_component_edges(graph):
            nodes = set(node for edge in edges for node in edge)
            if len(edges) < len(nodes) or np.ptp(self.points[list(nodes)], axis=0).max() < MIN_LOOP_SPAN:
                continue
            self.cycles.append(nodes)
        self.owners = cached[1] if cached is not None else (self._road_owners(index) if self.cycles else index)
        if cached is None and path is not None:
            _save_masks(path, skeleton, self.owners)

    def _road_owners(self, index):
        """沿八邻域同步扩展当前边界，得到与全图逐层膨胀相同的骨架归属编号。

        每层只处理上一层新增的像素；同层候选全部收集后才写入 owners，同距竞争仍取最大
        编号，已归属像素永不覆盖。保留 float32 最大值传播语义与补零边界，不让窄墙两侧
        按直线距离混合，也不让队列先后顺序改变结果；无种子的孤立道路始终为零。
        """
        owners = np.pad(index.astype(np.float32), 1)
        flat = owners.ravel()
        walkable = np.pad(self.road > 0, 1).ravel()
        pending = np.zeros_like(flat)
        frontier = np.flatnonzero(flat)
        width = owners.shape[1]
        offsets = (-width - 1, -width, -width + 1, -1, 1, width - 1, width, width + 1)
        while frontier.size:
            targets = []
            for offset in offsets:
                neighbors = frontier + offset
                keep = walkable[neighbors] & (flat[neighbors] == 0)
                if not keep.any():
                    continue
                neighbors = neighbors[keep]
                np.maximum.at(pending, neighbors, flat[frontier[keep]])
                targets.append(neighbors)
            if not targets:
                break
            frontier = np.unique(np.concatenate(targets))
            flat[frontier] = pending[frontier]
        return owners[1:-1, 1:-1].astype(np.int32)

    def node_at(self, point):
        """返回落点所属的骨架编号，允许 ROAD_TOLERANCE 像素的路外标记偏差；越界或远离路面返回零。"""
        point = np.asarray(point, float)
        if point.shape != (2,) or not np.isfinite(point).all():
            return 0
        x, y = np.rint(point).astype(int)
        height, width = self.road.shape
        if not (0 <= x < width and 0 <= y < height):
            return 0
        if self.owners[y, x]:
            return int(self.owners[y, x])
        top, left = max(0, y - ROAD_TOLERANCE), max(0, x - ROAD_TOLERANCE)
        window = self.owners[top:y + ROAD_TOLERANCE + 1, left:x + ROAD_TOLERANCE + 1]
        rows, columns = np.nonzero(window)
        if not len(rows):
            return 0
        distances = np.hypot(columns + left - point[0], rows + top - point[1])
        nearest = int(np.argmin(distances))
        return int(window[rows[nearest], columns[nearest]]) if distances[nearest] <= ROAD_TOLERANCE else 0

    def sections(self, start, target):
        """返回前方真实岔口之间分流后汇合的路段，不把当前站位人为当作分岔口。

        移除主路径后，旁路至少须连接路径上两个真实岔口；用虚拟节点连接这些岔口，
        只取包含虚拟节点的双连通块，剔除旁路附带的死胡同与单入口环。
        起终点若在普通路段中间，不作为旁路入口，避免为了短程目标反向绕完整张地图。
        """
        if not self.cycles:
            return []
        source, goal = self.node_at(start), self.node_at(target)
        if not source or not goal:
            return []
        try:
            path = nx.shortest_path(self.graph, source, goal)
        except nx.NetworkXNoPath:
            return []
        order = {node: index for index, node in enumerate(path)}
        ports = {node for node in path if self.graph.degree[node] >= 3}
        found = []
        for component in nx.connected_components(self.graph.subgraph(set(self.graph) - set(path))):
            contacts = {neighbor for node in component for neighbor in self.graph[node] if neighbor in ports}
            if len(contacts) < 2:
                continue
            linked = self.graph.subgraph(component | contacts).copy()
            linked.add_edges_from((0, node) for node in contacts)
            core = next(nodes for nodes in nx.biconnected_components(linked) if 0 in nodes and len(nodes) > 2)
            first, last = min(order[node] for node in contacts), max(order[node] for node in contacts)
            nodes = (core - {0}) | set(path[first:last + 1])
            if np.ptp(self.points[list(nodes)], axis=0).max() >= MIN_LOOP_SPAN:
                found.append((first, last, nodes))
        merged = []
        for first, last, nodes in sorted(found, key=lambda item: item[0]):
            if merged and first < merged[-1][1]:
                merged[-1][1] = max(last, merged[-1][1])
                merged[-1][2].update(nodes)
            else:
                merged.append([first, last, nodes])
        return [(nodes, path[first], path[last]) for first, last, nodes in merged]


def road_topology(cut):
    """复用当前道路内容的骨架，换图或原地改图后重新装载，不沿用过期连通性。

    道路对象与内容摘要均须相同才返回内存实例；内容变化会自动选取另一份磁盘缓存或重建。
    """
    topology = getattr(cut, '_topology', None)
    if (topology is None or topology.road is not cut.road
            or topology.cache_key != _cache_key(cut.road)):
        topology = RoadTopology(cut.road)
        cut._topology = topology
    return topology
