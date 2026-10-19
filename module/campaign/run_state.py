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

# 小地图标记每帧重检，同一只敌人的落点会在几像素内抖动；超过这个距离才算另一只。
ENEMY_MERGE_RADIUS = 25.
# 合并半径之外的第二道重复判据：比这个距离更近的既有记录，只要本帧视野盖住了它的位置、
# 本帧却没在那里认出敌人，就认为新标记是同一只敌人被配准投偏了，不新建记录。
# 细路配准对「沿路滑动」不敏感：38 章实机里同一只敌人在紧凑帧与展开帧相差 55 地图像素，
# 光靠 25 像素的合并半径拦不住，多出来的一条会按更近的距离抢走目标选择。
ENEMY_SHADOW_RADIUS = 90.
# 已观测区域的粗网格边长，只用来回答「这块地方看过没有」，不参与路径规划。
OBSERVED_CELL = 32
# 可用的定位状态：accepted 是本帧配准被接受；reused 是沿用上一次被接受的坐标
# （见 session.reuse_registration），两者都给了可信的小队底图坐标。
LOCALIZED = ('accepted', 'reused')
# 本帧检到的光幕与已记录封锁的配对距离：屏障投影实测中位约 6、p90 约 16 底图像素，
# mark_barrier 自己还有 28 的吸附容差，取 48 兼顾投影误差与相邻封锁不混淆。
CURTAIN_MATCH_RADIUS = 48.
# 判「它该出现在视野里却没检到」时要求封锁点离面板边界的余量：边界附近的检测不可靠，
# 贴边的一帧不足以断定光幕已消失。
BLOCK_VISIBLE_MARGIN = 24.
# 同一目标累计失败到这个次数，本次任务就不再选它（敌人同时标为暂时走不到）；冷却期按决策拍计。
MAX_FAILURES = 3
COOLDOWN_TICKS = 5


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
        parts.append('新发现敌人 ' + '、'.join(changes['enemies_added']))
    if changes.get('enemies_refreshed'):
        parts.append(f"更新已知敌人位置 {changes['enemies_refreshed']} 个")
    if changes.get('enemies_held'):
        parts.append(f"过滤疑似重复敌人标记 {changes['enemies_held']} 处")
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
        # 按目的地保留汇合岔路；机关临时抢占主线时不丢弃已进入但尚未清完的另一侧。
        self.route_branches = {}
        self.story_target = None
        self.pending_reviews = {}
        self.transfers = {}
        self.cooldowns = {}
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
                                              can_clear=snapshot.get('barriers_observed', True)))
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
        """把本帧的敌人标记并进状态：按距离合并到既有项，否则新建。

        只收普通敌人：EX 不在本期的目标范围内（也不参与选择），但仍按 kind 记下来，
        供后续判断「这条路会触发战斗」。
        合并半径之外还有一道重复判据：既有记录的位置就在本帧视野里（quad 覆盖、且离边界有余量），
        本帧却没在那里认出敌人，却在它附近冒出一个新标记——那更可能是同一只敌人被配准投偏了，
        而不是多了一只。这时并入既有记录、保留它原来的坐标（来自更可信的那一帧），不新建。
        视野没盖住既有记录时不作判断：那可能真是另一只敌人。
        本帧已经认到或新建的记录一律不参与这条判据：同一帧不可能把同一只敌人认成两处落点，
        所以帧内相距几十像素的两个标记就是两只敌人。先扫一遍本帧会认到哪些既有记录，
        判定才与标记的先后顺序无关。
        """
        added, merged, held = [], 0, 0
        matched = set()
        for item in enemies:
            position = np.asarray(item.get('position'), float)
            if position.shape != (2,) or not np.isfinite(position).all():
                continue
            match = self._nearest_enemy(position)
            if match is not None:
                matched.add(match)
        for item in enemies:
            position = np.asarray(item.get('position'), float)
            if position.shape != (2,) or not np.isfinite(position).all():
                continue
            match = self._nearest_enemy(position)
            if match is None and self._is_duplicate_projection(position, quad, matched):
                held += 1
                continue
            if match is None:
                identifier = self._identifier('enemy')
                self.enemies[identifier] = {'id': identifier, 'kind': item.get('kind'),
                                            'position': position.tolist(), 'status': 'pending',
                                            'first_seen': self.tick, 'last_seen': self.tick, 'failures': 0}
                added.append(identifier)
                matched.add(identifier)
                continue
            record = self.enemies[match]
            # 位置取最近一次观测：小队走近后的投影比远处那次准。
            record['position'] = position.tolist()
            record['last_seen'] = self.tick
            record['kind'] = item.get('kind') or record['kind']
            merged += 1
        return {'enemies_added': added, 'enemies_refreshed': merged, 'enemies_held': held}

    def _is_duplicate_projection(self, position, quad, matched):
        """这个落在合并半径外的新标记，是不是既有记录的同一只敌人被投偏了。

        只在既有记录仍是待清、位置确实在本帧视野里、且本帧没有别的标记认到它时才认：那种情况下
        本帧本该在它原来的位置看到敌人，却在旁边几十像素处看到了一个——细路配准沿路滑动就是这个
        样子。matched 是本帧已经认到或新建的记录编号，它们不能再被当成重复投影。
        """
        shadow = self._nearest_enemy(position, ENEMY_SHADOW_RADIUS)
        if shadow is None or shadow in matched or self.enemies[shadow]['status'] != 'pending':
            return False
        return self._inside(quad, self.enemies[shadow]['position'])

    def _nearest_enemy(self, position, radius=ENEMY_MERGE_RADIUS):
        """合并半径内最近的既有敌人编号；没有就返回 None。"""
        best, distance = None, radius
        for identifier, record in self.enemies.items():
            span = float(np.linalg.norm(np.asarray(record['position'], float) - position))
            if span <= distance:
                best, distance = identifier, span
        return best

    def _absorb_blocks(self, blocks, quad, can_clear=True):
        """更新光幕封锁的三态；返回新增、刷新与判定解除的编号。

        本帧检到的光幕按距离配对到既有封锁，配对上就刷新为 active；
        既有封锁没被配对时分两种：封锁点在本帧视野内（且离边界有余量）说明该看到却没看到，
        判 cleared；不在视野内只能判 stale，等规划要穿过它时再去复核。
        """
        added, refreshed, cleared = [], 0, []
        matched = set()
        for block in blocks:
            point = np.asarray(block.get('point'), float)
            if point.shape != (2,) or not np.isfinite(point).all():
                continue
            identifier = self._nearest_block(point, matched)
            if identifier is None:
                identifier = self._identifier('block')
                self.blocks[identifier] = {'id': identifier, 'point': point.tolist(),
                                           'segment': block.get('segment'), 'status': 'active',
                                           'first_seen': self.tick, 'last_seen': self.tick}
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
            if can_clear and self._inside(quad, record['point']):
                record['status'] = 'cleared'
                record['cleared_at'] = self.tick
                cleared.append(identifier)
            else:
                record['status'] = 'stale'
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
        """仍可作为目标的敌人：状态为 pending 且不在冷却中。"""
        return [record for record in self.enemies.values()
                if record['status'] == 'pending' and not self.cooling(record['id'])]

    def remember_story_target(self, position):
        """把已通过两帧身份验证的主线敌人并入记忆，返回稳定编号；存在歧义或已清除时不覆盖。

        position 为可靠小地图配准的底图坐标，不能传入场景菱形或脚圈客户区坐标。
        普通敌人的开战与清除仍使用同一编号，不另外建立一份主线敌人状态。
        """
        position = np.asarray(position, float)
        if position.shape != (2,) or not np.isfinite(position).all():
            return None
        if np.any(position < 0) or np.any(position >= self.size):
            return None
        nearby = [record for record in self.enemies.values()
                  if np.linalg.norm(np.asarray(record['position']) - position) <= ENEMY_MERGE_RADIUS]
        if len(nearby) > 1 or (nearby and (nearby[0]['kind'] != 'normal' or nearby[0]['status'] != 'pending')):
            return None
        if not nearby:
            self._absorb_enemies([dict(kind='normal', position=position.tolist())], None)
            nearby = [self.enemies[self._nearest_enemy(position)]]
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
        self.route_branches.pop(('mechanism', mechanism), None)

    def tried_for(self, mechanism, block):
        """这个机关是否已经为该封锁踩过。"""
        return block in self.mechanisms.get(mechanism, {}).get('tried_for', {})

    def _absorb_mechanisms(self, mechanisms):
        """按标注编号或兜底位置合并机关，保留开关状态、邻近敌人证据和到点记录。

        开启在本次任务中不可逆，后续漏检或 off 误检不能恢复为未开启。标注编号由观测层唯一
        关联后提供；未标注机关才按位置合并，抖动不能清掉既有的封锁关联记录。
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
            if record.get('state') != 'on' and item.get('state') in ('on', 'off'):
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

    def summary_text(self):
        """状态摘要，供日志打一行：决策轮数、敌人与光幕的各状态计数、看清了多少地图、有多少待办。"""
        enemies = {}
        for record in self.enemies.values():
            name = label(ENEMY_STATUS, record['status'])
            enemies[name] = enemies.get(name, 0) + 1
        blocks = {}
        for record in self.blocks.values():
            name = label(BLOCK_STATUS, record['status'])
            blocks[name] = blocks.get(name, 0) + 1
        tried = sum(bool(item.get('completed')) for item in self.mechanisms.values())
        return (f"已决策 {self.tick} 轮；敌人 {counts_text(enemies)}；光幕 {counts_text(blocks)}；"
                f"已覆盖 {int(self.observed.sum())} 格地图；已到达机关 {tried} 处；"
                f"待复核光幕 {len(self.pending_reviews)} 处；失败电梯 {len(self.transfers)} 段；"
                f"定位失败 {self.unlocated} 帧")

    def memory(self):
        """推给界面的跨帧记忆：记住的敌人与仍在挡路的封锁，坐标为底图像素。

        快照每帧整体替换，远处敌人往往只在补视野的展开帧里出现过一次，随后的紧凑帧里就没有了；
        界面要靠这份记忆才能继续把它们画在章节地图上。只给展示字段，冷却与尝试记录不外露。
        已解除的封锁不再影响路网，不推送；已打掉的敌人也不再画出来。
        """
        enemies = [dict(id=record['id'], kind=record['kind'], status=record['status'],
                        position=[round(float(value), 1) for value in record['position']])
                   for record in self.enemies.values() if record['status'] != 'cleared']
        blocks = [dict(id=record['id'], status=record['status'], point=record['point'], segment=record['segment'])
                  for record in self.blocking() if record.get('segment')]
        mechanisms = [dict(id=item['id'], state=item.get('state', 'unknown'),
                           completed=bool(item.get('completed')))
                      for item in self.mechanisms.values()]
        return dict(tick=self.tick, enemies=enemies, blocks=blocks, mechanisms=mechanisms,
                    route_goal=dict(self.route_goal) if self.route_goal else None)
