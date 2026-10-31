"""推图的运行状态层：跨帧累积敌人、封锁、机关尝试与已观测区域。

观测层（`CampaignObserver.observe`）每帧整体替换，没看到的东西就等于不存在；紧凑小地图又只覆盖
小队附近一小块（实测前方约 400、身后约 165 底图像素）。所以「这只敌人见过没有」「光幕还在不在」
「这个机关为哪块封锁试过了」必须在这一层跨帧记住，否则单帧推不出下一步该做什么。
一份状态对应一个章节加难度，进程内持有、不落盘：换章或换难度要新建一份，旧的坐标不能复用。
"""

import cv2
import numpy as np

from module.campaign.report import BLOCK_STATUS, ENEMY_STATUS, counts_text, label
from module.logger import logger

# 小地图标记每帧重检；此半径仅用于复用邻近记录的编号，不证明跨帧敌人身份。
ENEMY_MERGE_RADIUS = 25.
# 历史线索靠近本轮已确认的同类敌人时不重复侦察或展示；只隐藏线索，不合并敌人或删除记忆。
ENEMY_HINT_RADIUS = 90.
# 已观测区域的粗网格边长，只用来回答「这块地方看过没有」，不参与路径规划。
OBSERVED_CELL = 32
# 可用的定位状态：accepted 是本帧配准被接受；reused 是沿用上一次被接受的坐标
# （见 session.reuse_registration），两者都给了可信的小队底图坐标。
LOCALIZED = ('accepted', 'reused')
# Boss 与普通敌人均可战斗；EX 只记忆和展示，禁止自动选择。
COMBAT_ENEMY_KINDS = ('normal', 'boss')
# 本帧检到的光幕与已记录封锁的配对距离：屏障投影实测中位约 6、p90 约 16 底图像素，
# mark_barrier 自己还有 28 的吸附容差，取 48 兼顾投影误差与相邻封锁不混淆。
CURTAIN_MATCH_RADIUS = 48.
# 判「它该出现在视野里却没检到」时要求封锁点离面板边界的余量：边界附近的检测不可靠，
# 贴边的一帧不足以断定光幕已消失。
BLOCK_VISIBLE_MARGIN = 24.
# 同一目标累计失败到这个次数，本次任务就不再选它（敌人同时标为暂时走不到）；冷却期按决策拍计。
MAX_FAILURES = 3
COOLDOWN_TICKS = 5
# 暂不可扫格的解除距离：小队走近到底图像素内，游戏渲染与配准条件可能已变化，恢复可侦察。
UNSCOUTABLE_REARM = 150.


def located(snapshot):
    """本帧是否给出了可用的小队底图坐标：配准被接受，或沿用了上一次被接受的坐标。"""
    return snapshot.get('localization') in LOCALIZED


def panel_quad(snapshot):
    """把本帧小地图面板的四角投影到底图，给出这一帧实际看到的四边形；无法投影时返回 None。

    面板是透视渲染的，四角投到底图后是个凸四边形而不是矩形（实测 38 章紧凑面板
    上沿约 700、下沿约 280 底图像素宽），所以「看过哪里」只能按这个四边形算，不能按半径。
    需要 accepted 定位给出的 roi_to_map 与面板尺寸，两者缺一不可。
    """
    matrix = snapshot.get('roi_to_map')
    minimap = snapshot.get('minimap')
    if matrix is None or minimap is None:
        return None
    width, height = snapshot.get('coverage_size', minimap['size'])
    corners = np.array([[0, 0], [width, 0], [width, height], [0, height]], float)
    matrix = np.asarray(matrix, float)
    points = np.column_stack((corners, np.ones(len(corners)))) @ matrix.T
    divisor = points[:, 2]
    if not np.isfinite(divisor).all() or np.any(np.abs(divisor) < 1e-9):
        return None
    quad = points[:, :2] / divisor[:, None]
    return quad if np.isfinite(quad).all() else None


def describe_changes(changes):
    """把一帧的运行状态变更写成一句中文；没有可说的变更时返回空串，调用方据此决定要不要打这一行。

    键与 update 返回的 changes 一一对应：列表类字段（新发现的敌人与光幕编号）逐个列出，
    计数类字段直接说明；定位不可信或沿用旧坐标的帧只说明原因，不带计数。
    """
    if not changes:
        return ''
    if changes.get('skipped') == 'localization':
        return '本章地图定位不可信，未并入运行状态'
    if changes.get('reused') == 'localization':
        return '复用已确认的小队位置；本帧视野与敌人未更新'
    parts = []
    if changes.get('observed_cells'):
        parts.append(f"新覆盖 {changes['observed_cells']} 格地图")
    if changes.get('enemies_added'):
        parts.append('记录本帧敌人 ' + '、'.join(changes['enemies_added']))
    if changes.get('enemies_refreshed'):
        parts.append(f"更新已知敌人位置 {changes['enemies_refreshed']} 个")
    if changes.get('blocks_added'):
        parts.append('新发现挡路光幕 ' + '、'.join(changes['blocks_added']))
    if changes.get('blocks_refreshed'):
        parts.append(f"确认 {changes['blocks_refreshed']} 处光幕仍存在")
    if changes.get('blocks_cleared'):
        parts.append('光幕 ' + '、'.join(changes['blocks_cleared']) + ' 已消失')
    return '，'.join(parts)


class RunState:
    """一章推图期间累积的运行状态。

    只记跨帧才有意义的事实，不缓存静态层能直接查的东西（道路、分区、标注点都从 StaticMap 取）。
    所有坐标都是底图像素，与 StaticMap 同一坐标系。
    """

    def __init__(self, chapter, difficulty, size):
        """按章节、难度与底图尺寸建一份空状态。

        size 是底图的 (宽, 高)，只用来定已观测粗网格的形状；它必须与定位用的底图同一版本，
        否则记下的格子和实际位置会错开。
        """
        self.chapter = chapter
        self.difficulty = difficulty
        self.size = (int(size[0]), int(size[1]))
        self.tick = 0
        # 定位未被接受的帧数：这种帧的坐标不可信，一律不并进状态，只计数供诊断。
        self.unlocated = 0
        self.enemies = {}
        self.blocks = {}
        self.mechanisms = {}
        self.route_goal = None
        self.story_target = None
        self.execution_target = None
        self.pending_reviews = {}
        self.transfers = {}
        self.cooldowns = {}
        # 暂不可扫的侦察格：cell_id → 标记时的小队底图坐标。配准失败说明从当前距离看不清
        # （探索前沿无渲染内容，或走廊过细产生歧义），既不占失败冷却也不阻塞出发，
        # 小队走近后恢复可扫。
        self.unscoutable = {}
        shape = (int(np.ceil(self.size[1] / OBSERVED_CELL)), int(np.ceil(self.size[0] / OBSERVED_CELL)))
        self.observed = np.zeros(shape, np.uint8)
        self._sequence = {'enemy': 0, 'block': 0, 'mechanism': 0}

    def _identifier(self, kind):
        """给新记录发一个本章内稳定的编号，日志里据此跟踪同一个目标。"""
        self._sequence[kind] += 1
        return f'{kind}-{self._sequence[kind]}'

    def update(self, snapshot, advance=True):
        """把一帧快照并进状态，返回本帧的变更摘要。

        定位未被接受时直接返回：那种帧的底图坐标不可信，并进去会污染已记住的位置。
        屏障只在紧凑帧更新——展开面板的投影链与屏障标定不同源，展开帧的落点会整体偏，
        所以展开帧只用来补敌人与已观测区域。
        advance 为假时只并入观测、不推进决策拍：移动闭环每走一步都要观测一次，同一拍内还有
        补视野与复核帧，这些帧都推进的话，冷却会在下一个目标走到一半时就提前耗完。
        """
        if advance:
            self.tick += 1
        if not located(snapshot):
            self.unlocated += 1
            return {'skipped': 'localization'}
        if snapshot.get('localization') == 'reused':
            # 沿用帧只提供小队位置（供本拍选目标），本帧的视野与标记不并进状态：
            # 视野四边形来自记下坐标的那一帧，而且沿用帧与它常常是两个坐标系，
            # 拿它判断「这块看没看到」会误判封锁与已观测区域。
            return {'reused': 'localization'}
        quad = panel_quad(snapshot)
        changes = {'observed_cells': self._absorb_observed(quad)}
        changes.update(self._absorb_enemies(snapshot.get('enemies') or [], quad))
        minimap = snapshot.get('minimap') or {}
        if minimap.get('mode') == 'compact':
            changes.update(self._absorb_blocks(snapshot.get('blocks') or [], quad,
                                              can_clear=snapshot.get('barriers_observed', False),
                                              checked_ids=snapshot.get('barrier_checks')))
            self._absorb_mechanisms(snapshot.get('mechanisms') or [])
        for identifier in list(self.pending_reviews):
            if self.blocks.get(identifier, {}).get('status') == 'cleared':
                self.pending_reviews.pop(identifier, None)
        return {key: value for key, value in changes.items() if value}

    def _absorb_observed(self, quad):
        """仅把完整落在本帧面板四边形内的粗网格标成已观测，返回新增格数。

        四角均位于凸四边形内才能确认整格；直接取整填多边形会把仅露出边缘的整格记为已看，
        从而漏掉格内尚在视野外的敌人。底图最右、最下的不满格只检查实际地图范围。
        """
        if quad is None:
            return 0
        before = int(self.observed.sum())
        rows, columns = np.indices(self.observed.shape)
        left, top = columns * OBSERVED_CELL, rows * OBSERVED_CELL
        right = np.minimum(left + OBSERVED_CELL, self.size[0])
        bottom = np.minimum(top + OBSERVED_CELL, self.size[1])
        edges = np.roll(quad, -1, axis=0) - quad
        area = float(np.sum(quad[:, 0] * np.roll(quad[:, 1], -1)
                            - quad[:, 1] * np.roll(quad[:, 0], -1)))
        if abs(area) < 1e-6:
            return 0
        inside = np.ones(self.observed.shape, bool)
        for x, y in ((left, top), (right, top), (right, bottom), (left, bottom)):
            for origin, edge in zip(quad, edges):
                cross = edge[0] * (y - origin[1]) - edge[1] * (x - origin[0])
                inside &= np.sign(area) * cross >= -1e-6
        self.observed[inside] = 1
        return int(self.observed.sum()) - before

    def _absorb_enemies(self, enemies, quad):
        """以本帧检测替换覆盖区域的确认结果，历史位置仅作为待复核线索。

        纯观测和补视野不推进决策拍，因此同一拍中不同视野的可靠检测可共同参与规划；下一拍
        未重新检出的普通敌人退为线索。覆盖区域内的漏检只撤销确认，不表示敌人已清除。
        新检测点邻域内的旧确认也退为线索，以容忍视野边界的投影偏差；不会删掉相邻真实敌人。
        战斗敌人原位置仅检出重叠 EX 时属于分类歧义，保留待复核线索，不能据此改选无关区域。
        25 像素近邻仅复用编号与失败状态，不依靠跨帧位移或远距合并来决定本帧有几只敌人。
        主线及执行目标由独立引用保留；每条旧记录在本帧最多复用一次。
        """
        observations = []
        for item in enemies:
            position = np.asarray(item.get('position'), float)
            if (position.shape == (2,) and np.isfinite(position).all()
                    and np.all(position >= 0) and np.all(position < self.size)):
                observations.append((item, position))
        added, merged, assigned = [], 0, set()
        contour = np.asarray(quad, np.float32).reshape(-1, 1, 2) if quad is not None else None
        for record in self.enemies.values():
            record['visible'] = False
            covered = contour is not None and cv2.pointPolygonTest(contour, tuple(record['position']), False) >= 0
            nearby = any(record['kind'] == item.get('kind')
                         and np.linalg.norm(position - record['position']) <= ENEMY_HINT_RADIUS
                         for item, position in observations)
            ambiguous_ex = record['kind'] in COMBAT_ENEMY_KINDS and any(
                item.get('kind') == 'ex' and np.linalg.norm(position - record['position']) <= ENEMY_MERGE_RADIUS
                for item, position in observations)
            if covered or nearby or contour is None:
                record['confirmed_at'] = None
            if covered and not ambiguous_ex:
                record['reviewed_at'] = self.tick
        for item, position in observations:
            match = self._nearest_enemy(position, taken=assigned, kind=item.get('kind'))
            if match is None:
                match = self._identifier('enemy')
                self.enemies[match] = {'id': match, 'kind': item.get('kind'), 'status': 'pending',
                                       'first_seen': self.tick, 'failures': 0}
                added.append(match)
            else:
                merged += 1
            assigned.add(match)
            self.enemies[match].update(position=position.tolist(), last_seen=self.tick,
                                      kind=item.get('kind') or self.enemies[match]['kind'],
                                      confirmed_at=self.tick, reviewed_at=None, visible=True)
        return {'enemies_added': added, 'enemies_refreshed': merged}

    def confirmed_enemies(self):
        """返回本决策拍可靠观测的敌人，以及独立保留的主线与执行目标。

        包含交战及不可达状态，供沿途门禁保留失败保护；已清除记录永不因漏检或旧帧恢复。
        补视野属于同一决策拍，其确认可在收起地图后继续使用，不要求每次镜头变动都重认身份。
        """
        pinned = {self.story_target, self.execution_target}
        if self.route_goal is not None and self.route_goal.get('kind') == 'story':
            pinned.add(self.route_goal['id'])
        return [record for record in self.enemies.values() if record['status'] != 'cleared'
                and (record.get('confirmed_at') == self.tick or record['id'] in pinned)]

    def enemy_hints(self):
        """返回尚未复核的历史敌人位置，不作为可直接执行的战斗目标。

        新可靠视野已覆盖而未检出的旧位置在本拍暂停复核，下一拍仍可重查；保留原记录，
        不认定已清除。
        与本拍同类确认点相邻的线索仅暂时隐藏，不能据此合并两只相邻敌人。
        这些线索用于展示和生成 explore 侦察目标，不直接生成战斗目标，也不阻止当前目标执行。
        """
        confirmed = self.confirmed_enemies()
        identifiers = {record['id'] for record in confirmed}
        return [record for record in self.enemies.values()
                if record['status'] == 'pending' and record['id'] not in identifiers
                and record.get('reviewed_at') != self.tick
                and not any(record['kind'] == current['kind']
                            and np.linalg.norm(np.asarray(record['position']) - current['position'])
                            <= ENEMY_HINT_RADIUS for current in confirmed)]

    def _nearest_enemy(self, position, radius=ENEMY_MERGE_RADIUS, taken=(), kind=None):
        """合并半径内优先返回同类且未分配的旧记录，避免 Boss 与旁边普通敌人交换编号。

        未指定类型时保持最近邻行为；普通与 Boss 之间允许本帧可靠分类修正旧标签。
        EX 与战斗敌人可能是同一图标的分类歧义，也可能相邻共存，两者禁止合并或互相改型。
        taken 只包含本帧已分配记录，相邻不同目标不能覆盖同一编号。
        """
        best, rank = None, (2, radius)
        for identifier, record in self.enemies.items():
            if identifier in taken:
                continue
            if kind is not None and (kind == 'ex') != (record['kind'] == 'ex'):
                continue
            span = float(np.linalg.norm(np.asarray(record['position'], float) - position))
            candidate = (int(kind is not None and record['kind'] != kind), span)
            if span <= radius and candidate <= rank:
                best, rank = identifier, candidate
        return best

    def _absorb_blocks(self, blocks, quad, can_clear=True, checked_ids=None):
        """更新光幕封锁的三态；返回新增、刷新与判定解除的编号。

        预标注光幕按 annotation_id 保持稳定身份；无标注光幕按距离配对到既有封锁，刷新为 active；
        既有封锁未被配对时保持原状态、几何和时间戳；漏检、截边或关联失败都不是状态变化证据。
        只有 can_clear 为真且封锁点在本帧视野内（离边界有余量），才允许记为 cleared。
        checked_ids 存在时只允许解除经过居中复核的指定编号，不能用一处光幕的证据清除整屏封锁。
        """
        added, refreshed, cleared = [], 0, []
        matched = set()
        for block in blocks:
            point = np.asarray(block.get('point'), float)
            if point.shape != (2,) or not np.isfinite(point).all():
                continue
            identifier = block.get('annotation_id')
            if identifier in matched:
                continue
            if identifier is None:
                identifier = self._nearest_block(point, matched) or self._identifier('block')
            if identifier not in self.blocks:
                self.blocks[identifier] = {'id': identifier, 'point': point.tolist(),
                                           'segment': block.get('segment'), 'status': 'active',
                                           'first_seen': self.tick, 'last_seen': self.tick}
                if block.get('annotation_id') is not None:
                    self.blocks[identifier]['annotation_id'] = block['annotation_id']
                added.append(identifier)
            else:
                record = self.blocks[identifier]
                record.update(point=point.tolist(), segment=block.get('segment'),
                              status='active', last_seen=self.tick)
                refreshed += 1
            matched.add(identifier)
        for identifier, record in self.blocks.items():
            if identifier in matched or record['status'] == 'cleared':
                continue
            if (can_clear and (checked_ids is None or identifier in checked_ids)
                    and self._inside(quad, record['point'])):
                record['status'] = 'cleared'
                record['cleared_at'] = self.tick
                cleared.append(identifier)
        return {'blocks_added': added, 'blocks_refreshed': refreshed, 'blocks_cleared': cleared}

    def _nearest_block(self, point, taken):
        """配对半径内最近且未被本帧其它光幕占用的封锁编号。"""
        best, distance = None, CURTAIN_MATCH_RADIUS
        for identifier, record in self.blocks.items():
            if identifier in taken:
                continue
            span = float(np.linalg.norm(np.asarray(record['point'], float) - point))
            if span <= distance:
                best, distance = identifier, span
        return best

    @staticmethod
    def _inside(quad, point):
        """点是否落在本帧视野四边形内，且离边界还有 BLOCK_VISIBLE_MARGIN 的余量。"""
        if quad is None:
            return False
        contour = np.rint(quad).astype(np.int32).reshape(-1, 1, 2)
        return cv2.pointPolygonTest(contour, (float(point[0]), float(point[1])), True) >= BLOCK_VISIBLE_MARGIN

    def is_observed(self, point):
        """这个底图坐标所在的粗网格是否已经被小地图覆盖过。"""
        point = np.asarray(point, float)
        if point.shape != (2,) or not np.isfinite(point).all():
            return False
        x, y = (int(point[0]) // OBSERVED_CELL, int(point[1]) // OBSERVED_CELL)
        if not (0 <= x < self.observed.shape[1] and 0 <= y < self.observed.shape[0]):
            return False
        return bool(self.observed[y, x])

    def pending_enemies(self):
        """可直接执行的敌人：本拍已确认或独立绑定，且待清、不在冷却中。

        历史位置不进入战斗候选；enemy_hints 仅用于展示，不要求为历史位置补做侦察。
        """
        return [record for record in self.confirmed_enemies()
                if record['status'] == 'pending' and not self.cooling(record['id'])]

    def remember_story_target(self, position, kind='normal'):
        """把已通过两帧身份验证的主线敌人并入记忆，返回稳定编号；存在歧义或已清除时不覆盖。

        position 为可靠小地图配准的底图坐标，不能传入场景菱形或脚圈客户区坐标。
        普通敌人和 Boss 的开战与清除仍使用同一编号，不另外建立一份主线敌人状态。
        kind 必须来自两帧一致的小地图分类，默认 normal 保持旧调用兼容，EX 不可成为主线目标。
        """
        position = np.asarray(position, float)
        if position.shape != (2,) or not np.isfinite(position).all():
            return None
        if np.any(position < 0) or np.any(position >= self.size):
            return None
        nearby = [record for record in self.confirmed_enemies()
                  if np.linalg.norm(np.asarray(record['position']) - position) <= ENEMY_MERGE_RADIUS]
        if kind not in COMBAT_ENEMY_KINDS:
            return None
        if len(nearby) > 1 or (nearby and (nearby[0]['kind'] != kind or nearby[0]['status'] != 'pending')):
            return None
        if not nearby:
            identifier = self._identifier('enemy')
            self.enemies[identifier] = dict(id=identifier, kind=kind, position=position.tolist(), status='pending',
                                            first_seen=self.tick, last_seen=self.tick, failures=0,
                                            confirmed_at=self.tick, reviewed_at=None, visible=False)
            nearby = [self.enemies[identifier]]
        nearby[0]['position'] = position.tolist()
        self.story_target = nearby[0]['id']
        self.cooldowns.pop('story-location', None)
        return self.story_target

    def blocking(self):
        """需要当成道路封锁看待的记录：active 与 stale 都算。

        stale 是「记过但没再看过」，它可能已经消失也可能还在；规划阶段宁可当它还在，
        等复核过再解除，否则会把小队送去撞光幕。
        """
        return [record for record in self.blocks.values() if record['status'] in ('active', 'stale')]

    def mark_tried(self, mechanism, block=None):
        """导航确认到点即记为开启，本次任务不再选择；有关联光幕时另行复核道路。

        不以发送过点击或接近坐标作为调用条件。主动机关没有 block，不能产生空编号的光幕复核。
        """
        record = self.mechanisms.setdefault(mechanism, {'id': mechanism, 'tried_for': {}})
        record.update(state='on', completed=True)
        if block is not None:
            record['tried_for'][block] = self.tick
            self.pending_reviews[block] = mechanism
        if self.route_goal and self.route_goal['id'] == mechanism:
            self.route_goal = None

    def tried_for(self, mechanism, block):
        """这个机关是否已经为该封锁踩过。"""
        return block in self.mechanisms.get(mechanism, {}).get('tried_for', {})

    def _absorb_mechanisms(self, mechanisms):
        """按标注编号或兜底位置合并机关，保留开关状态、邻近敌人证据和到点记录。

        未完成机关按本帧有效观测更新开关状态，视觉 on 不作为不可逆的完成证据；只有导航
        确认到点的 completed 记录保持开启，避免重复执行。漏检不清除既有状态。标注编号
        由观测层唯一关联后提供；未标注机关才按位置合并，抖动不能清掉既有的封锁关联记录。
        """
        for item in mechanisms:
            position = np.asarray(item.get('position'), float)
            if position.shape != (2,) or not np.isfinite(position).all():
                continue
            nearest = item.get('id')
            for identifier, record in self.mechanisms.items():
                if nearest is not None:
                    break
                if record.get('source') != 'scene_fallback' or 'position' not in record:
                    continue
                if np.linalg.norm(position - np.asarray(record['position'])) <= ENEMY_MERGE_RADIUS:
                    nearest = identifier
                    break
            identifier = nearest or self._identifier('mechanism')
            record = self.mechanisms.setdefault(identifier, {'id': identifier, 'tried_for': {}})
            record.update(position=position.tolist(), source=item.get('source', 'scene_fallback'),
                          last_seen=self.tick, near_enemy=bool(item.get('near_enemy')))
            if not record.get('completed') and item.get('state') in ('on', 'off'):
                record['state'] = item['state']

    @staticmethod
    def transfer_key(leg):
        """电梯失败按有向航段记忆，双向连接的一侧失败不封掉反向出口。"""
        ends = tuple(tuple(round(float(value), 1) for value in leg[end]) for end in ('entry', 'exit'))
        return (leg['connection'], *ends)

    def transfer_available(self, leg):
        """失败航段冷却后可重试，累计三次失败后本次任务不再使用该方向。"""
        record = self.transfers.get(self.transfer_key(leg))
        return record is None or (record['failures'] < MAX_FAILURES and self.tick >= record['until'])

    def transfer_failed(self, leg):
        """回写电梯航段失败与冷却；调用方随后重新规划，不把传送边当普通道路。"""
        key = self.transfer_key(leg)
        record = self.transfers.setdefault(key, {'failures': 0, 'until': 0, 'status': 'available'})
        record['failures'] += 1
        record['until'] = self.tick + COOLDOWN_TICKS
        record['status'] = 'unavailable'
        return record

    def transfer_succeeded(self, leg):
        """只有共享闭环连续两次确认出口后才清掉该航段的失败记录。"""
        self.transfers.pop(self.transfer_key(leg), None)

    def penalize(self, identifier, failure=True):
        """给目标记一次失败并进入冷却；失败到上限的敌人标为暂时走不到。

        failure 为假时只冷却不计失败：用于「做成了但短期内不该再派」的情形（如复核确认光幕仍在），
        否则它会占掉失败上限，让后续真正需要的复核被禁用。
        """
        record = self.cooldowns.setdefault(identifier, {'failures': 0, 'until': 0})
        if failure:
            record['failures'] += 1
        record['until'] = self.tick + COOLDOWN_TICKS
        enemy = self.enemies.get(identifier)
        if enemy is not None:
            enemy['failures'] = record['failures']
            if record['failures'] >= MAX_FAILURES:
                enemy['status'] = 'unreachable'
                logger.warning(f"目标 {identifier} 连续 {record['failures']} 次未能到达，标为暂时不可达。")
        return record

    def cooling(self, identifier):
        """目标是否暂不可选：还在冷却期内，或累计失败已到上限（本次任务不再选）。"""
        record = self.cooldowns.get(identifier)
        return record is not None and (self.tick < record['until'] or record['failures'] >= MAX_FAILURES)

    def mark_unscoutable(self, identifier, squad):
        """把侦察格标为暂不可扫：当前距离上配准不出可信视野，原因多为探索前沿或走廊歧义。

        只记标记时的小队位置，不计失败冷却；is_unscoutable 在小队走近后解除标记。
        squad 为本帧可信的小队底图坐标。
        """
        self.unscoutable[identifier] = list(np.asarray(squad, float))

    def is_unscoutable(self, identifier, squad):
        """identifier 是否仍按暂不可扫跳过；小队移动超过 UNSCOUTABLE_REARM 后标记解除。"""
        marked = self.unscoutable.get(identifier)
        if marked is None:
            return False
        if np.linalg.norm(np.asarray(squad, float) - np.asarray(marked, float)) <= UNSCOUTABLE_REARM:
            return True
        del self.unscoutable[identifier]
        return False

    def summary_text(self):
        """状态摘要，供日志打一行：决策轮数、敌人与光幕的各状态计数、看清了多少地图、有多少待办。"""
        enemies = {}
        for record in self.confirmed_enemies():
            name = label(ENEMY_STATUS, record['status'])
            enemies[name] = enemies.get(name, 0) + 1
        blocks = {}
        for record in self.blocks.values():
            name = label(BLOCK_STATUS, record['status'])
            blocks[name] = blocks.get(name, 0) + 1
        tried = sum(bool(item.get('completed')) for item in self.mechanisms.values())
        return (f"已决策 {self.tick} 轮；确认敌人 {counts_text(enemies)}；"
                f"历史位置线索 {len(self.enemy_hints())} 处；光幕 {counts_text(blocks)}；"
                f"已覆盖 {int(self.observed.sum())} 格地图；已到达机关 {tried} 处；"
                f"待复核光幕 {len(self.pending_reviews)} 处；失败电梯 {len(self.transfers)} 段；"
                f"定位失败 {self.unlocated} 帧")

    def memory(self):
        """推给界面的跨帧记忆：记住的敌人与仍在挡路的封锁，坐标为底图像素。

        本拍可靠观测与绑定目标显示为敌人；未确认的历史位置仅显示为线索，不叠加为确定敌人。
        同拍补视野的远处确认点可继续展示。只给展示字段，冷却与尝试记录不外露。
        已解除的封锁不再影响路网，不推送；已打掉的敌人也不再画出来。
        """
        confirmed = self.confirmed_enemies()
        identifiers = {record['id'] for record in confirmed}
        enemies = [dict(id=record['id'], kind=record['kind'], status=record['status'],
                        confirmed=record['id'] in identifiers, visible=record.get('visible', False),
                        position=[round(float(value), 1) for value in record['position']])
                   for record in confirmed + self.enemy_hints()]
        blocks = [dict(id=record['id'], status=record['status'], point=record['point'], segment=record['segment'])
                  for record in self.blocking() if record.get('segment')]
        mechanisms = [dict(id=item['id'], state=item.get('state', 'unknown'),
                           completed=bool(item.get('completed')))
                      for item in self.mechanisms.values()]
        return dict(tick=self.tick, enemies=enemies, enemy_confirmation='current_turn',
                    blocks=blocks, mechanisms=mechanisms,
                    route_goal=dict(self.route_goal) if self.route_goal else None)
