"""路程内汇合岔路的清理记忆：跨战斗保留范围，侦察两侧并稳定处理同一支路。"""

import networkx as nx
import numpy as np

from module.campaign.navigation.targets import walking_distance
from module.campaign.navigation.topology import road_topology
from module.campaign.run_state import OBSERVED_CELL
from module.logger import logger


class BranchSection:
    """一个汇合段的原始道路范围与支路顺序；道路后来被切断也不丢弃同侧尚未清理的部分。"""

    def __init__(self, topology, nodes, entry, exit_):
        """以入口、出口分出各侧道路，嵌套岔路保留在所属一侧，避免内部岔口打断该侧的清理顺序。"""
        self.topology, self.nodes = topology, nodes
        graph = topology.graph.subgraph(nodes)
        self.lanes = {}
        for lane, component in enumerate(nx.connected_components(graph.subgraph(nodes - {entry, exit_}))):
            self.lanes.update((node, lane) for node in component)
        self.preferred = None
        self.active = False
        self._road = None
        self._home = None
        self.cells = np.empty((0, 2), int)

    def contains(self, point):
        """按沿路传播的骨架归属判是否在本汇合段，不以直线距离扩大到邻近的独立道路。"""
        return self.topology.node_at(point) in self.nodes

    def refresh_cells(self, cut, home):
        """为本段当前可达路面每个观测格选一个实际道路点；切口与异分量格不进入侦察门禁。"""
        if self._road is cut.road and self._home == home:
            return
        selected = np.zeros(len(self.topology.points), bool)
        selected[list(self.nodes)] = True
        mask = selected[self.topology.owners] & (cut.labels == home) & (cut.road > 0)
        rows, columns = np.nonzero(mask)
        width = int(np.ceil(cut.road.shape[1] / OBSERVED_CELL))
        _, indices = np.unique((rows // OBSERVED_CELL) * width + columns // OBSERVED_CELL, return_index=True)
        self.cells = np.column_stack((columns[indices], rows[indices]))
        self._road, self._home = cut.road, home

    def enemies(self, state, cut, squad, destination):
        """返回本段未清普通敌人及当前 A* 距离；最终主线敌人留到整段清理完再处理。

        同分量但 A* 无法给出路线的敌人也保留，交执行规划暂停，不能因寻路失败认定已清空。
        """
        result = []
        home = cut.component(squad)
        for enemy in state.enemies.values():
            if enemy['kind'] != 'normal' or enemy['status'] == 'cleared' or enemy['id'] == destination['id']:
                continue
            if cut.component(enemy['position']) != home or not self.contains(enemy['position']):
                continue
            distance = walking_distance(cut.road, cut.regions, squad, enemy['position'])
            node = self.topology.node_at(enemy['position'])
            result.append((distance, enemy, self.lanes.get(node, -node)))
        return result


def route_branches(state, cut, squad, destination):
    """取得目的地的岔路队列；目标抢占只暂停旧队列，恢复时不能因小队已到汇合口而漏掉另一边。

    每次道路版本或所在分量变化都补认当前路程上的汇合段。旧段仍按新切开图过滤可达区域，
    新段完全落在已记录范围内时不重复加入；开启光幕后新增的支路则补入队列。
    """
    key = (destination['kind'], destination['id'])
    memory = state.route_branches.setdefault(
        key, dict(topology=None, home=None, origin=list(squad), sections=[], current=None))
    topology, home = road_topology(cut), cut.component(squad)
    if memory['topology'] is topology and memory['home'] == home:
        return memory
    sections = memory['sections']
    # 解封时仍从本路程起点补认，否则小队已经进入一侧后，新开放的另一侧会被当作身后道路。
    origin = memory['origin'] if cut.component(memory['origin']) == home else squad
    for nodes, entry, exit_ in topology.sections(origin, destination['position']):
        points = topology.points[list(nodes)]
        if any(all(section.contains(point) for point in points) for section in sections):
            continue
        sections.append(BranchSection(topology, nodes, entry, exit_))
        logger.info(f"路程 {destination['id']} 新增汇合岔路：入口 {topology.points[entry].tolist()}，"
                    f"出口 {topology.points[exit_].tolist()}，支路数 {len(set(sections[-1].lanes.values()))}")
    memory.update(topology=topology, home=home)
    return memory


def branch_enemy(state, cut, squad, destination):
    """选择第一段未清汇合岔路的敌人，同一支路未清完不因另一侧变近而切换。

    已扫描和已清除不是同一条件：即使当前没有敌人，未扫描的另一侧仍保留为当前段，
    由出发前侦察门禁确认。冷却和不可执行状态由路程规划统一转成暂停，不能当作清除。
    """
    memory = route_branches(state, cut, squad, destination)
    memory['current'] = None
    for section in memory['sections']:
        section.refresh_cells(cut, memory['home'])
        enemies = section.enemies(state, cut, squad, destination)
        unseen = any(not state.is_observed(point) for point in section.cells)
        if not enemies and not unseen:
            continue
        memory['current'] = section
        if not enemies:
            return None
        preferred = [item for item in enemies if item[2] == section.preferred]
        distance, enemy, lane = min(preferred or enemies, key=lambda item: (item[0], item[1]['id']))
        if section.preferred != lane:
            section.preferred = lane
            logger.info(f"路程 {destination['id']} 选择汇合支路：首个敌人 {enemy['id']}，"
                        f"本段未清敌人 {len(enemies)} 个")
        return dict(kind='enemy', id=enemy['id'], position=enemy['position'], enemy_kind=enemy['kind'],
                    distance=round(float(distance), 1), reason='enemy_on_route')
    return None


def branch_scout_points(state, cut, squad, path):
    """当前执行段进入汇合岔路时返回两侧侦察点；激活后走到汇合口也继续保留未扫描的一侧。

    只补当前第一段未清岔路，不提前扫描后续所有环路。格子是否已观察及冷却仍由侦察层判断。
    """
    destination = state.route_goal
    if destination is None:
        return []
    memory = route_branches(state, cut, squad, destination)
    section = memory['current']
    if section is None:
        return []
    if not section.active and any(section.contains(point) for point in path):
        section.active = True
    if not section.active:
        return []
    section.refresh_cells(cut, cut.component(squad))
    order = np.argsort(np.linalg.norm(section.cells - np.asarray(squad), axis=1), kind='stable')
    return section.cells[order]
