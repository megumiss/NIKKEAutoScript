import time

import cv2
import numpy as np

from module.base.button import merge_buttons
from module.base.timer import Timer
from module.conversation.assets import ANSWER_CHECK
from module.event.event_20251204.assets_game import *
from module.logger import logger
from module.ui.page import *


def start_game(self, skip_first_screenshot=True):
    logger.info('Open event mini game')
    click_timer = Timer(0.3)
    confirm_timer = Timer(2, count=3)

    # 游戏开始
    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        # 点击开始
        if click_timer.reached() and self.appear_then_click(MINI_GAME_START, offset=10, interval=2):
            logger.info('Start event mini game')
            click_timer.reset()
            continue

        # 点击开始
        if click_timer.reached() and self.appear_then_click(MINI_GAME_START_CONFIRM, offset=10, interval=2):
            logger.info('Start event mini game confirm')
            click_timer.reset()
            continue

        if self.appear(MINI_GAME_EXEC_CHECK, offset=10):
            break

    # TODO 更换角色 表格大小
    # 识别数字输出表格
    digit_templates = {
        1: TEMPLATE_MINI_GAME_SOLINE_NUM_1,
        2: TEMPLATE_MINI_GAME_SOLINE_NUM_2,
        3: TEMPLATE_MINI_GAME_SOLINE_NUM_3,
        4: TEMPLATE_MINI_GAME_SOLINE_NUM_4,
        5: TEMPLATE_MINI_GAME_SOLINE_NUM_5,
        6: TEMPLATE_MINI_GAME_SOLINE_NUM_6,
        7: TEMPLATE_MINI_GAME_SOLINE_NUM_7,
        8: TEMPLATE_MINI_GAME_SOLINE_NUM_8,
        9: TEMPLATE_MINI_GAME_SOLINE_NUM_9,
    }
    # 列数，行数
    grid_cols_rows = (8, 14)

    logger.info('Recognizing digit grid...')
    # 识别网格
    grid = recognize_digit_grid_robust(
        self.device.image,
        digit_templates,
        grid_cols=grid_cols_rows[0],
        grid_rows=grid_cols_rows[1],
        merge_x_threshold=15,  # 合并阈值
        merge_y_threshold=15,
        tolerance_x=15,  # 聚类容差
        tolerance_y=15,
    )

    # 打印结果到日志
    logger.info('Grid Recognition Result:')
    for row_idx, row in enumerate(grid):
        row_str = ' '.join([str(cell['digit']) if cell['digit'] != 10 else '_' for cell in row])
        logger.info(f'Row {row_idx:02d}: {row_str}')

    # 计算步骤
    logger.info('Solving puzzle using Beam Search...')
    solver = TenSumBeamSolver(grid, beam_width=50)
    steps = solver.solve()

    logger.info(f'Solution found: {len(steps)} steps total.')

    # 游戏操作执行
    for i, step in enumerate(steps):
        # 提取详细信息
        vals = step['eliminated_values']
        val_str = '+'.join(str(v) for v in vals)
        start_pt = step['start_point']
        end_pt = step['end_point']

        # 输出友好的日志
        logger.info(
            f'Step {i + 1:02d}/{len(steps)}: Swipe {start_pt} -> {end_pt} | '
            f'Eliminate: {val_str} = 10 (Count: {step["eliminated_count"]})'
        )

        # 执行滑动
        self.ensure_sroll(start_pt, end_pt, method='swipe', speed=30, count=2, delay=0.3)

    # 游戏结束逻辑处理
    while 1:
        self.device.screenshot()

        # 结束返回
        if click_timer.reached() and self.appear_then_click(MINI_GAME_BACK, offset=10, interval=2):
            logger.info('Event mini game done')
            click_timer.reset()
            continue

        # 关闭结算弹窗
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_CLOSE, offset=30, interval=1, static=False):
            click_timer.reset()
            continue

        # 跳过对话
        if (
            self.config.Event_GameStorySkip
            and click_timer.reached()
            and self.appear_then_click(SKIP, offset=10, interval=1)
        ):
            click_timer.reset()
            continue
        # 选择对话选项
        if click_timer.reached() and self.appear_then_click(ANSWER_CHECK, offset=10, interval=1, static=False):
            click_timer.reset()
            continue

        # 回到小游戏主页
        if self.appear(MINI_GAME_CHECK, offset=10):
            if not confirm_timer.started():
                confirm_timer.start()

            if confirm_timer.reached():
                break
        else:
            confirm_timer.clear()


def recognize_digit_grid_robust(
    device_image,
    digit_templates,
    grid_cols=8,
    grid_rows=14,
    merge_x_threshold=10,
    merge_y_threshold=10,
    tolerance_x=20,
    tolerance_y=20,
    default_value=10,
):
    """
    鲁棒的数字网格识别,缺失位置填充默认值

    Args:
        device_image: 设备截图
        digit_templates: 数字模板字典 {1: TEMPLATE_NUM_1, 2: TEMPLATE_NUM_2, ...}
        grid_cols: 表格列数,默认8
        grid_rows: 表格行数,默认14
        merge_x_threshold: 横向合并阈值,默认10
        merge_y_threshold: 纵向合并阈值,默认10
        tolerance_x: x方向坐标容差,默认20
        tolerance_y: y方向坐标容差,默认20
        default_value: 缺失位置的默认值,默认10

    Returns:
        list: 二维数组,每个元素为 {'digit': int, 'x': int, 'y': int, 'row': int, 'col': int}
              缺失位置填充为 {'digit': default_value, ...}
    """

    # 1. 收集所有匹配结果
    all_matches = []
    for digit, template in digit_templates.items():
        matches = template.match_multi(device_image, similarity=0.85, threshold=3)

        # 对当前数字的匹配结果进行合并去重
        matches = merge_buttons(matches, x_threshold=merge_x_threshold, y_threshold=merge_y_threshold)

        for match in matches:
            x, y = match.location
            all_matches.append({'digit': digit, 'x': x, 'y': y, 'button': match})

    if not all_matches:
        logger.warning('No digits recognized, returning default empty grid.')
        return _create_default_grid(grid_rows, grid_cols, default_value)

    # 2. 建立坐标参考系
    y_coords = sorted(set(m['y'] for m in all_matches))
    x_coords = sorted(set(m['x'] for m in all_matches))

    # 聚类得到实际的行列坐标
    y_clusters = _cluster_coordinates(y_coords, tolerance_y)
    x_clusters = _cluster_coordinates(x_coords, tolerance_x)

    # 计算每个簇的中心坐标作为参考
    reference_y = [int(np.mean(cluster)) for cluster in y_clusters]
    reference_x = [int(np.mean(cluster)) for cluster in x_clusters]

    # 3. 检测并补全缺失的行列坐标
    reference_y = _complete_grid_coordinates(reference_y, grid_rows, tolerance_y)
    reference_x = _complete_grid_coordinates(reference_x, grid_cols, tolerance_x)

    # 4. 初始化网格
    grid = _create_default_grid(grid_rows, grid_cols, default_value, reference_x, reference_y)

    # 5. 填充结果
    for match in all_matches:
        row = _find_nearest_index(match['y'], reference_y, tolerance_y)
        col = _find_nearest_index(match['x'], reference_x, tolerance_x)

        if row is not None and col is not None:
            grid[row][col] = {
                'digit': match['digit'],
                'x': match['x'],
                'y': match['y'],
                'row': row,
                'col': col,
                'button': match['button'],
            }

    return grid


def _cluster_coordinates(coords, tolerance):
    """将坐标聚类到相近的组"""
    if not coords:
        return []

    clusters = [[coords[0]]]
    for coord in coords[1:]:
        if coord - clusters[-1][-1] <= tolerance:
            clusters[-1].append(coord)
        else:
            clusters.append([coord])

    return clusters


def _complete_grid_coordinates(reference_coords, expected_count, tolerance):
    """
    补全缺失的网格坐标

    如果识别到的坐标数量少于期望数量,推测并插入缺失的坐标
    """
    if len(reference_coords) >= expected_count:
        return reference_coords[:expected_count]

    if len(reference_coords) < 2:
        # 坐标太少,无法推测,直接返回
        return reference_coords

    # 计算平均间距
    intervals = [reference_coords[i + 1] - reference_coords[i] for i in range(len(reference_coords) - 1)]
    avg_interval = int(np.median(intervals))

    # 补全坐标
    completed = list(reference_coords)

    # 向后补全
    while len(completed) < expected_count:
        next_coord = completed[-1] + avg_interval
        completed.append(next_coord)

    return completed[:expected_count]


def _find_nearest_index(coord, reference_coords, tolerance):
    """
    找到坐标最接近的参考索引

    Args:
        coord: 待匹配的坐标
        reference_coords: 参考坐标列表
        tolerance: 容差范围

    Returns:
        int or None: 最近的索引,如果超出容差返回None
    """
    min_distance = float('inf')
    nearest_idx = None

    for idx, ref_coord in enumerate(reference_coords):
        distance = abs(coord - ref_coord)
        if distance < min_distance and distance <= tolerance:
            min_distance = distance
            nearest_idx = idx

    return nearest_idx


def _create_default_grid(rows, cols, default_value, reference_x=None, reference_y=None):
    """
    创建填充默认值的网格

    Args:
        rows: 行数
        cols: 列数
        default_value: 默认填充值
        reference_x: x坐标参考列表(可选)
        reference_y: y坐标参考列表(可选)

    Returns:
        list: 二维网格
    """
    grid = []
    for row in range(rows):
        grid_row = []
        for col in range(cols):
            x = reference_x[col] if reference_x and col < len(reference_x) else 0
            y = reference_y[row] if reference_y and row < len(reference_y) else 0

            grid_row.append(
                {
                    'digit': default_value,
                    'x': x,
                    'y': y,
                    'row': row,
                    'col': col,
                    'button': None,
                }
            )
        grid.append(grid_row)

    return grid


class TenSumBeamSolver:
    def __init__(self, complex_grid, beam_width=50):
        """
        Args:
            complex_grid: recognize_digit_grid_robust 返回的二维数组
            beam_width: 搜索宽度
        """
        self.beam_width = beam_width
        self.rows = len(complex_grid)
        self.cols = len(complex_grid[0]) if self.rows > 0 else 0

        # --- 1. 数据预处理 ---
        # logic_grid: 仅用于算法计算的二维数字数组 (1-9, 0代表空或10)
        # coord_map: (row, col) -> (x, y) 的映射表
        self.logic_grid = []
        self.coord_map = {}

        for r in range(self.rows):
            row_vals = []
            for c in range(self.cols):
                cell = complex_grid[r][c]
                val = cell['digit']

                # 规则1：如果数字为10，视为0（空），参与求和但不增加计数
                if val == 10:
                    val = 0

                row_vals.append(val)
                # 记录坐标映射
                self.coord_map[(r, c)] = (cell['x'], cell['y'])
            self.logic_grid.append(row_vals)

    def get_valid_moves(self, grid):
        """寻找所有合法走法"""
        moves = []
        rows, cols = self.rows, self.cols

        for r1 in range(rows):
            for c1 in range(cols):
                for r2 in range(r1, rows):
                    for c2 in range(c1, cols):
                        # 快速计算
                        s, count = self.fast_sum(grid, r1, c1, r2, c2)

                        # 逻辑判断：和为10 且 包含至少一个有效数字
                        if s == 10 and count > 0:
                            moves.append(((r1, c1, r2, c2), count))

                        # 剪枝：数字均为非负，一旦超10，向右扩展无意义
                        if s > 10:
                            break
        return moves

    def fast_sum(self, grid, r1, c1, r2, c2):
        """快速区域求和"""
        s = 0
        c = 0
        for i in range(r1, r2 + 1):
            row_data = grid[i]
            for j in range(c1, c2 + 1):
                v = row_data[j]
                s += v
                if v > 0:
                    c += 1
                if s > 10:
                    return s, c
        return s, c

    def apply_move(self, grid, coords):
        """应用消除，生成新网格"""
        new_grid = [row[:] for row in grid]
        r1, c1, r2, c2 = coords
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                new_grid[r][c] = 0  # 消除变为0
        return new_grid

    def solve(self):
        """执行 Beam Search 并返回包含消除值的详细步骤"""
        # (当前分数, 当前网格, 历史步骤)
        current_states = [(0, self.logic_grid, [])]
        best_final_state = None

        start_time = time.time()

        # --- A. 搜索阶段 ---
        while True:
            next_states = []
            expanded_any = False

            for score, grid, history in current_states:
                moves = self.get_valid_moves(grid)

                if not moves:
                    if best_final_state is None or score > best_final_state[0]:
                        best_final_state = (score, grid, history)
                    continue

                expanded_any = True

                for coords, count in moves:
                    new_score = score + count
                    new_grid = self.apply_move(grid, coords)
                    # 只存网格坐标，最后再映射回屏幕坐标
                    new_history = history + [(coords, count)]
                    next_states.append((new_score, new_grid, new_history))

            if not expanded_any:
                break

            # 排序筛选
            next_states.sort(key=lambda x: x[0], reverse=True)

            unique_states = []
            seen_grids = set()
            for state in next_states:
                grid_tuple = tuple(tuple(row) for row in state[1])
                if grid_tuple not in seen_grids:
                    seen_grids.add(grid_tuple)
                    unique_states.append(state)
                if len(unique_states) >= self.beam_width:
                    break
            current_states = unique_states

        # 如果 best_final_state 为空（直接没解），取当前状态
        if best_final_state is None and current_states:
            best_final_state = current_states[0]

        final_score, _, raw_steps = best_final_state

        # --- B. 回放阶段 (提取消除的数字值) ---
        formatted_steps = []

        # 使用一个临时的网格进行回放，以获取每次操作时被消除的具体数字
        replay_grid = [row[:] for row in self.logic_grid]

        for coords, count in raw_steps:
            r1, c1, r2, c2 = coords

            # 获取屏幕坐标
            start_x, start_y = self.coord_map.get((r1, c1), (0, 0))
            end_x, end_y = self.coord_map.get((r2, c2), (0, 0))

            # 提取本步骤消除的具体数字值
            eliminated_values = []
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    val = replay_grid[r][c]
                    if val > 0:
                        eliminated_values.append(val)
                        replay_grid[r][c] = 0  # 标记为消除

            step_info = {
                'grid_rect': (r1, c1, r2, c2),  # 网格索引 (row, col)
                'start_point': (start_x, start_y),  # 屏幕像素坐标 (x, y)
                'end_point': (end_x, end_y),  # 屏幕像素坐标 (x, y)
                'eliminated_count': count,
                'eliminated_values': eliminated_values,  # 新增：消除的数字列表 [5, 5]
            }
            formatted_steps.append(step_info)

        logger.info(f'Calculation finished: {time.time() - start_time:.3f}s, Total eliminated: {final_score}')
        return formatted_steps
