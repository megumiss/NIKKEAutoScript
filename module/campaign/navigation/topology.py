"""从切开道路的骨架识别分流后汇合的路段，不把宽路网格中的小环当作岔路。"""

import cv2
import networkx as nx
import numpy as np

from module.campaign.map.map_state import ROAD_TOLERANCE

# 小于两个观测格的骨架环属于道路边缘的小孔，不扩大清理范围。
MIN_LOOP_SPAN = 64


class RoadTopology:
    """一版切开图的骨架和双连通路段；仅确定道路归属，实际移动仍须通过 A*。

    像素归属沿道路传播，不能直接按直线最近骨架分配，否则窄墙两侧会混成一条支路。
    双连通段只在起终点间的简单路径经过时选入，单入口的旁支环和死胡同不算沿途岔路。
    """

    def __init__(self, road):
        """在全分辨率道路上细化并建图；补零边界防止贴图边的道路无法细化。"""
        self.road = road
        skeleton = cv2.ximgproc.thinning(np.pad(road.astype(np.uint8) * 255, 1))[1:-1, 1:-1] > 0
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
        self.owners = self._road_owners(index) if self.cycles else index

    def _road_owners(self, index):
        """按八邻域逐层填充骨架编号，传播范围严格限制在道路内；无骨架的孤立区域保留零。"""
        owners = index.astype(np.float32)
        kernel = np.ones((3, 3), np.uint8)
        while True:
            spread = cv2.dilate(owners, kernel)
            changed = (self.road > 0) & (owners == 0) & (spread > 0)
            if not changed.any():
                break
            owners[changed] = spread[changed]
        return owners.astype(np.int32)

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
    """复用当前道路版本的骨架；光幕增删导致掩码更换时重建，不沿用过期连通性。"""
    topology = getattr(cut, '_topology', None)
    if topology is None or topology.road is not cut.road:
        topology = RoadTopology(cut.road)
        cut._topology = topology
    return topology
