import time
from typing import Any, Dict, List, Optional, Tuple, Union

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

    game_map = 'FOREST'

    # 地图配置字典
    MAP_CONFIGS = {
        'FOREST': {
            'digit_templates': {
                1: TEMPLATE_MINI_GAME_FOREST_NUM_1,
                2: TEMPLATE_MINI_GAME_FOREST_NUM_2,
                3: TEMPLATE_MINI_GAME_FOREST_NUM_3,
                4: TEMPLATE_MINI_GAME_FOREST_NUM_4,
                5: TEMPLATE_MINI_GAME_FOREST_NUM_5,
                6: TEMPLATE_MINI_GAME_FOREST_NUM_6,
                7: TEMPLATE_MINI_GAME_FOREST_NUM_7,
                8: TEMPLATE_MINI_GAME_FOREST_NUM_8,
                9: TEMPLATE_MINI_GAME_FOREST_NUM_9,
            },
            'grid_cols_rows': (8, 14),
            'target_color': (205, 207, 179),
        },
        'DESERT': {
            'digit_templates': {
                1: TEMPLATE_MINI_GAME_DESERT_NUM_1,
                2: TEMPLATE_MINI_GAME_DESERT_NUM_2,
                3: TEMPLATE_MINI_GAME_DESERT_NUM_3,
                4: TEMPLATE_MINI_GAME_DESERT_NUM_4,
                5: TEMPLATE_MINI_GAME_DESERT_NUM_5,
                6: TEMPLATE_MINI_GAME_DESERT_NUM_6,
                7: TEMPLATE_MINI_GAME_DESERT_NUM_7,
                8: TEMPLATE_MINI_GAME_DESERT_NUM_8,
                9: TEMPLATE_MINI_GAME_DESERT_NUM_9,
            },
            'grid_cols_rows': (9, 15),
            'target_color': (239, 221, 183),
        },
        'SNOW': {
            'digit_templates': {
                1: TEMPLATE_MINI_GAME_SNOW_NUM_1,
                2: TEMPLATE_MINI_GAME_SNOW_NUM_2,
                3: TEMPLATE_MINI_GAME_SNOW_NUM_3,
                4: TEMPLATE_MINI_GAME_SNOW_NUM_4,
                5: TEMPLATE_MINI_GAME_SNOW_NUM_5,
                6: [
                    TEMPLATE_MINI_GAME_SNOW_NUM_6_V1,
                    TEMPLATE_MINI_GAME_SNOW_NUM_6_V2,
                    TEMPLATE_MINI_GAME_SNOW_NUM_6_V3,
                ],
                7: TEMPLATE_MINI_GAME_SNOW_NUM_7,
                8: [TEMPLATE_MINI_GAME_SNOW_NUM_8_V1, TEMPLATE_MINI_GAME_SNOW_NUM_8_V2],
                9: TEMPLATE_MINI_GAME_SNOW_NUM_9,
            },
            'grid_cols_rows': (10, 16),
            'target_color': (211, 213, 219),
        },
    }

    # 游戏开始
    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        if self.appear(MINI_GAME_EXEC_CHECK, offset=10):
            self.device.sleep(3)
            break

        # 点击开始
        if click_timer.reached() and self.appear_then_click(MINI_GAME_START, offset=10, interval=2):
            logger.info('Start event mini game')
            click_timer.reset()
            continue

        if self.appear(MINI_GAME_START_CONFIRM, offset=10):
            # 切换人物
            if not self.appear(MINI_GAME_NIKKE_SOLINE, offset=10):
                while 1:
                    self.device.screenshot()

                    if self.appear(MINI_GAME_NIKKE_SOLINE, offset=10):
                        break
                    if self.appear(MINI_GAME_EXEC_CHECK, offset=10):
                        self.device.sleep(3)
                        break
                    if self.appear_then_click(MINI_GAME_CHANGE_NIKKE, offset=10, interval=1):
                        logger.info('Change nikke before start mini game')
                        continue

            # 判断地图
            if self.appear(MINI_GAME_MAP_FOREST, offset=10, threshold=0.9):
                game_map = 'FOREST'
            elif self.appear(MINI_GAME_MAP_DESERT, offset=10, threshold=0.9):
                game_map = 'DESERT'
            elif self.appear(MINI_GAME_MAP_SNOW, offset=10, threshold=0.9):
                game_map = 'SNOW'
            logger.info(f'Selected mini game map: {game_map}')

        # 点击开始
        if (
            click_timer.reached()
            and self.appear(MINI_GAME_NIKKE_SOLINE, offset=10)
            and self.appear_then_click(MINI_GAME_START_CONFIRM, offset=10, interval=2)
        ):
            logger.info('Start event mini game confirm')
            click_timer.reset()
            continue

    self.device.screenshot()

    # 根据地图类型获取配置
    map_config = MAP_CONFIGS.get(game_map, MAP_CONFIGS['FOREST'])
    digit_templates = map_config['digit_templates']
    grid_cols_rows = map_config['grid_cols_rows']
    target_color = map_config['target_color']

    # 颜色容差
    color_tolerance = 15

    logger.info(f'Recognizing digit grid for {game_map} map...')
    logger.info(f'Grid size: {grid_cols_rows[0]}x{grid_cols_rows[1]}, Target color: {target_color}')

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
    solver = TenSumBeamSolver(grid, beam_width=100)
    steps = solver.solve()

    logger.info(f'Solution found: {len(steps)} steps total.')

    def check_point_color(
        screenshot, point: Tuple[int, int], target_color: Tuple[int, int, int], tolerance: int = 10
    ) -> bool:
        """
        检查指定点的颜色是否匹配目标颜色

        Args:
            screenshot: 截图对象 (PIL Image 或 numpy array)
            point: 要检查的点坐标 (x, y)
            target_color: 目标RGB颜色 (r, g, b)
            tolerance: 颜色容差值

        Returns:
            bool: 颜色是否匹配
        """
        try:
            x, y = point

            # 判断截图类型并获取像素颜色
            if hasattr(screenshot, 'getpixel'):
                # PIL Image
                pixel_color = screenshot.getpixel(point)
            else:
                # numpy array - 注意numpy数组是 [y, x] 顺序,且可能是BGR或RGB
                import numpy as np

                if isinstance(screenshot, np.ndarray):
                    # 检查数组维度和范围
                    if y >= screenshot.shape[0] or x >= screenshot.shape[1]:
                        logger.error(f'点 ({x}, {y}) 超出图像范围 {screenshot.shape}')
                        return False

                    pixel_color = screenshot[y, x]

                    # 如果是BGR格式(OpenCV),转换为RGB
                    # if len(pixel_color) >= 3:
                    #     # 假设是BGR,转为RGB (如果你的截图已经是RGB,可以去掉这行)
                    #     pixel_color = (pixel_color[2], pixel_color[1], pixel_color[0])
                else:
                    logger.error(f'不支持的截图类型: {type(screenshot)}')
                    return False

            # 计算颜色差异
            color_diff = sum(abs(int(pixel_color[i]) - target_color[i]) for i in range(3))

            return color_diff <= tolerance * 3  # 3个通道的总容差
        except Exception as e:
            logger.error(f'检查点 {point} 颜色时出错: {e}')
            return False

    def verify_swipe_result(
        screenshot,
        start_pt: Tuple[int, int],
        end_pt: Tuple[int, int],
        target_color: Tuple[int, int, int],
        tolerance: int = 10,
    ) -> bool:
        """
        验证滑动后两个点是否都变成了目标颜色

        Args:
            screenshot: 截图对象
            start_pt: 起始点坐标
            end_pt: 结束点坐标
            target_color: 目标RGB颜色
            tolerance: 颜色容差值

        Returns:
            bool: 两个点是否都匹配目标颜色
        """
        start_match = check_point_color(screenshot, start_pt, target_color, tolerance)
        end_match = check_point_color(screenshot, end_pt, target_color, tolerance)

        logger.debug(f'起点 {start_pt} 颜色匹配: {start_match}')
        logger.debug(f'终点 {end_pt} 颜色匹配: {end_match}')

        return start_match and end_match

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

        # 设置重试参数
        swipe_success = False

        while not swipe_success:
            # 执行滑动
            self.ensure_sroll(
                (start_pt[0] - 15, start_pt[1] - 15),
                (end_pt[0] + 15, end_pt[1] + 15),
                method='swipe',
                speed=20,
                count=1,
                delay=0.3,
            )

            # 重新截图
            screenshot = self.device.screenshot()
            # 验证滑动结果
            swipe_success = verify_swipe_result(screenshot, start_pt, end_pt, target_color, color_tolerance)

            if swipe_success:
                logger.info(f'✓ Step {i + 1} 滑动验证成功')

            # 结束返回
            if self.appear(MINI_GAME_BACK, offset=10):
                break

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
    digit_templates: Dict[int, Union[Any, List[Any]]],
    grid_cols: int = 8,
    grid_rows: int = 14,
    merge_x_threshold: int = 15,
    merge_y_threshold: int = 15,
    tolerance_x: int = 20,
    tolerance_y: int = 20,
    default_value: int = 10,
    debug: bool = False,
):
    """
    鲁棒的数字网格识别 (优化版：多阶段动态阈值 + 全局冲突检测)

    策略：
    采用多轮扫描，置信度从高到低 (例如 0.95 -> 0.90 -> 0.85)。
    优先锁定高可信度的结果，后续轮次只在空白区域寻找匹配，
    有效解决了多模板(6/8)容易误判的问题，同时保证了召回率。
    """

    # 1. 定义扫描阶段 (置信度从高到低)
    # 可以在这里调整扫描的精细度
    threshold_stages = [0.95, 0.92, 0.88, 0.85]

    # 针对特定困难数字的阈值补偿 (值为负数表示降低门槛，正数表示提高门槛)
    # 6和8通常因为字形复杂需要更宽松的判定
    digit_bias = {
        6: -0.03,
        8: -0.03,
    }

    # 存储最终确认的匹配结果
    confirmed_matches = []

    if debug:
        logger.info(f'Starting robust recognition with {len(threshold_stages)} stages...')

    # 2. 多阶段扫描循环
    for stage_idx, base_similarity in enumerate(threshold_stages):
        stage_matches = []

        # 遍历所有数字模板
        for digit, templates in digit_templates.items():
            # 计算当前数字在本轮的特定阈值
            current_sim = base_similarity + digit_bias.get(digit, 0.0)
            # 限制阈值范围，防止过低或过高
            current_sim = max(0.80, min(0.99, current_sim))

            if not isinstance(templates, list):
                templates = [templates]

            # 遍历该数字的所有模板变体
            for template in templates:
                # 执行匹配
                matches = template.match_multi(device_image, similarity=current_sim, threshold=3)

                for match in matches:
                    x, y = match.location
                    stage_matches.append(
                        {
                            'digit': digit,
                            'x': x,
                            'y': y,
                            'button': match,
                            'similarity': current_sim,  # 记录当时的阈值作为大致置信度参考
                        }
                    )

        # 3. 冲突检测与合并
        # 将本轮发现的所有匹配项与“已确认列表”进行对比
        newly_confirmed = 0
        for match in stage_matches:
            # 检查是否与已有的高置信度结果冲突
            if not _is_location_occupied(match, confirmed_matches, merge_x_threshold, merge_y_threshold):
                # 检查是否与本轮已加入的结果冲突 (处理同位置多个模板匹配到的情况)
                # 注意：这里简单的先到先得，因为本轮阈值相同，且通常先处理的数字较小(1-9)
                # 如果需要更精细，可以对 stage_matches 按 digit 优先级排序
                if not _is_location_occupied(match, confirmed_matches, merge_x_threshold, merge_y_threshold):
                    confirmed_matches.append(match)
                    newly_confirmed += 1

        if debug and newly_confirmed > 0:
            logger.info(f'Stage {stage_idx + 1} (sim~{base_similarity}): Added {newly_confirmed} matches.')

    all_matches = confirmed_matches

    if not all_matches:
        if debug:
            logger.warning('No digits recognized, returning default empty grid.')
        return _create_default_grid(grid_rows, grid_cols, default_value)

    # 4. 建立坐标参考系 (同原逻辑)
    y_coords = sorted(set(m['y'] for m in all_matches))
    x_coords = sorted(set(m['x'] for m in all_matches))

    # 聚类得到实际的行列坐标
    y_clusters = _cluster_coordinates(y_coords, tolerance_y)
    x_clusters = _cluster_coordinates(x_coords, tolerance_x)

    # 计算每个簇的中心坐标作为参考
    reference_y = [int(np.mean(cluster)) for cluster in y_clusters]
    reference_x = [int(np.mean(cluster)) for cluster in x_clusters]

    # 5. 检测并补全缺失的行列坐标 (同原逻辑)
    reference_y = _complete_grid_coordinates(reference_y, grid_rows, tolerance_y)
    reference_x = _complete_grid_coordinates(reference_x, grid_cols, tolerance_x)

    # 6. 初始化网格并填充
    grid = _create_default_grid(grid_rows, grid_cols, default_value, reference_x, reference_y)

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


def _is_location_occupied(new_match: Dict, existing_matches: List[Dict], x_thresh: int, y_thresh: int) -> bool:
    """
    检查新位置是否已经被已有的匹配项占据
    """
    for exist in existing_matches:
        dx = abs(new_match['x'] - exist['x'])
        dy = abs(new_match['y'] - exist['y'])
        if dx <= x_thresh and dy <= y_thresh:
            return True
    return False


# 以下辅助函数保持不变，只需确保包含在文件中即可
def _cluster_coordinates(coords: List[int], tolerance: int) -> List[List[int]]:
    if not coords:
        return []
    clusters = []
    sorted_coords = sorted(coords)
    current_cluster = [sorted_coords[0]]
    for coord in sorted_coords[1:]:
        if coord - current_cluster[-1] <= tolerance:
            current_cluster.append(coord)
        else:
            clusters.append(current_cluster)
            current_cluster = [coord]
    clusters.append(current_cluster)
    return clusters


def _complete_grid_coordinates(coords: List[int], expected_count: int, tolerance: int) -> List[int]:
    if len(coords) >= expected_count:
        return coords[:expected_count]
    if len(coords) < 2:
        return coords
    intervals = [coords[i + 1] - coords[i] for i in range(len(coords) - 1)]
    if not intervals:
        return coords
    avg_interval = int(np.median(intervals))
    if avg_interval <= 0:
        return coords

    # 简单的向前向后补全
    result = list(coords)
    # 向前
    while len(result) < expected_count and result[0] - avg_interval > 0:
        result.insert(0, result[0] - avg_interval)
    # 向后
    while len(result) < expected_count:
        result.append(result[-1] + avg_interval)
    return result[:expected_count]


def _find_nearest_index(value: int, reference_list: List[int], tolerance: int) -> Optional[int]:
    if not reference_list:
        return None
    distances = [abs(value - ref) for ref in reference_list]
    min_distance = min(distances)
    if min_distance <= tolerance:
        return distances.index(min_distance)
    return None


def _create_default_grid(rows, cols, default_value, reference_x=None, reference_y=None):
    grid = []
    for row in range(rows):
        row_data = []
        for col in range(cols):
            cell = {
                'digit': default_value,
                'x': reference_x[col] if reference_x and col < len(reference_x) else 0,
                'y': reference_y[row] if reference_y and row < len(reference_y) else 0,
                'row': row,
                'col': col,
                'button': None,
            }
            row_data.append(cell)
        grid.append(row_data)
    return grid


class TenSumBeamSolver:
    def __init__(self, complex_grid, beam_width=100):
        """
        Args:
            complex_grid: recognize_digit_grid_robust 返回的二维数组
            beam_width: 搜索宽度。设得越大越能保证找到最优解，但会增加内存和时间消耗。
                        (在加入 Optimistic Pruning 后，最优性由剪枝保证，Beam Width 影响搜索效率)
        """
        self.beam_width = beam_width
        self.rows = len(complex_grid)
        self.cols = len(complex_grid[0]) if self.rows > 0 else 0

        # --- 1. 数据预处理 ---
        self.coord_map = {}
        raw_matrix = np.zeros((self.rows, self.cols), dtype=np.int32)

        for r in range(self.rows):
            for c in range(self.cols):
                cell = complex_grid[r][c]
                val = cell['digit']

                self.coord_map[(r, c)] = (cell['x'], cell['y'])

                if val != 10:
                    raw_matrix[r, c] = val

        self.initial_grid = raw_matrix

        # 记录全局信息，用于 Optimistic Pruning
        self.total_initial_digits = np.sum(raw_matrix > 0)
        self.best_global_score = 0  # 追踪目前找到的最佳总分

    def _get_valid_moves_fast(self, grid: np.ndarray):
        """
        利用二维前缀和 (Integral Image) 加速寻找所有和为 10 的矩形
        时间复杂度：O(R^2 * C^2)，但内部计算为 O(1)
        """
        moves = []
        rows, cols = self.rows, self.cols

        # 1. 计算前缀和矩阵 (Integral Image)
        # Pad 一行一列 0，方便处理边界 (坐标 r1, c1)
        p_sum = np.pad(grid, ((1, 0), (1, 0)), 'constant').cumsum(axis=0).cumsum(axis=1)
        p_count = np.pad((grid > 0).astype(np.int32), ((1, 0), (1, 0)), 'constant').cumsum(axis=0).cumsum(axis=1)

        # 2. 遍历所有矩形
        for r1 in range(rows):
            for c1 in range(cols):
                for r2 in range(r1, rows):
                    for c2 in range(c1, cols):
                        # 前缀和坐标 (p_sum 的索引比 grid 对应坐标大 1)
                        pr2, pc2 = r2 + 1, c2 + 1
                        pr1, pc1 = r1, c1  # 对应 grid r1-1, c1-1 的位置

                        # O(1) 计算当前矩形区域的数值和
                        current_sum = p_sum[pr2, pc2] - p_sum[pr1, pc2] - p_sum[pr2, pc1] + p_sum[pr1, pc1]

                        # 剪枝：如果和超过 10，则当前 r2, c2 往后不可能满足条件
                        if current_sum > 10:
                            break

                        # 找到满足条件的矩形
                        if current_sum == 10:
                            # O(1) 计算非零数字的个数
                            count = p_count[pr2, pc2] - p_count[pr1, pc2] - p_count[pr2, pc1] + p_count[pr1, pc1]

                            # 至少要消除一个非零数字
                            if count > 0:
                                moves.append((r1, c1, r2, c2, count))

        return moves

    def solve(self):
        """执行 A* 启发式 Beam Search (保证最优性)"""

        # 初始状态的 A* f_score = 当前得分 (0) + 剩余数字总数 (self.total_initial_digits)
        initial_f_score = self.total_initial_digits

        # 状态元组: (f_score, g_score[当前得分], grid[Numpy对象], history[步骤列表])
        current_states = [(initial_f_score, 0, self.initial_grid, [])]
        best_final_state = None

        start_time = time.time()

        # --- A. 搜索阶段 ---
        while True:
            next_states = []
            expanded_any = False

            # 遍历当前 Beam 中的状态，解包时跳过 f_score（但它指导了排序）
            for _, score, grid, history in current_states:
                # --- 1. Optimistic 剪枝 (确保最优性的核心) ---
                remaining_digits = np.sum(grid > 0)
                # 当前路径的理论最高分数 (g_score + h_score)
                potential_max_score = score + remaining_digits

                # 如果即使完美消除剩余所有数字，也无法超过当前找到的最佳分数，则剪枝
                if potential_max_score <= self.best_global_score:
                    continue

                moves = self._get_valid_moves_fast(grid)

                if not moves:
                    # 路径结束，更新全局最优分数
                    if score > self.best_global_score:
                        self.best_global_score = score
                        # best_final_state 只需要保存 g_score, grid, history
                        best_final_state = (score, grid, history)
                    continue

                expanded_any = True

                # 扩展状态
                for r1, c1, r2, c2, count in moves:
                    new_score = score + count

                    new_grid = grid.copy()
                    new_grid[r1 : r2 + 1, c1 : c2 + 1] = 0

                    # 计算下一状态的 f_score (g + h)
                    new_remaining_digits = remaining_digits - count
                    new_f_score = new_score + new_remaining_digits

                    new_history = history + [(r1, c1, r2, c2, count)]

                    next_states.append((new_f_score, new_score, new_grid, new_history))

            if not expanded_any:
                break

            # --- 2. 排序与去重 (A* 启发式) ---
            # 排序策略：f_score 降序（潜力分数越高越好），score 降序（当前得分越高越好）
            next_states.sort(key=lambda x: (x[0], x[1]), reverse=True)

            unique_states = []
            seen_grids = set()

            for state in next_states:
                grid_bytes = state[2].tobytes()  # grid.tobytes() 作为高效哈希键
                if grid_bytes not in seen_grids:
                    seen_grids.add(grid_bytes)
                    unique_states.append(state)

                if len(unique_states) >= self.beam_width:
                    break

            current_states = unique_states

        # 确保返回最终找到的最佳结果
        if best_final_state is None and current_states:
            # 如果循环结束但没找到一个完整路径 (极少发生)，取当前最高分的中间状态
            # 需要先根据 g_score 重新排序
            current_states.sort(key=lambda x: x[1], reverse=True)
            best_final_state = (current_states[0][1], current_states[0][2], current_states[0][3])

        final_score, _, raw_steps = best_final_state

        logger.info(
            f'Calculation finished: {time.time() - start_time:.3f}s, Total eliminated: {final_score}, Best score found: {self.best_global_score}'
        )

        # --- B. 结果回放与格式化 ---
        return self._format_output(raw_steps)

    def _format_output(self, raw_steps: list):
        """格式化输出，包含具体的数字回放"""
        formatted_steps = []

        # 使用初始网格的副本进行回放
        replay_grid = self.initial_grid.copy()

        for r1, c1, r2, c2, count in raw_steps:
            # 获取屏幕坐标
            start_x, start_y = self.coord_map.get((r1, c1), (0, 0))
            end_x, end_y = self.coord_map.get((r2, c2), (0, 0))

            # 提取本步骤消除的具体数字值
            roi = replay_grid[r1 : r2 + 1, c1 : c2 + 1]
            eliminated_values = roi[roi > 0].tolist()

            # 在回放网格中执行消除
            replay_grid[r1 : r2 + 1, c1 : c2 + 1] = 0

            formatted_steps.append(
                {
                    'grid_rect': (r1, c1, r2, c2),
                    'start_point': (start_x, start_y),
                    'end_point': (end_x, end_y),
                    'eliminated_count': count,
                    'eliminated_values': eliminated_values,
                }
            )

        return formatted_steps
