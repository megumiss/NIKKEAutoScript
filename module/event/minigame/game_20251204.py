import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

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
    solver = TenSumBeamSolverMultiThread(
        complex_grid=grid, beam_width=self.config.Event_GameTenBeam, use_multiprocessing=True
    )
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
                (start_pt[0] - 20, start_pt[1] - 20),
                (end_pt[0] + 20, end_pt[1] + 20),
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


def _expand_state_worker(state_data, rows, cols, best_score):
    _, score, grid, history = state_data

    remaining_digits = np.sum(grid > 0)
    # 基础收益检查：如果把剩下的全消了也赢不了 best_score，才退出
    # 注意：如果您的目标只是"尽可能多消"，而不是"超过某个分"，可以注释掉下面这行
    # if score + remaining_digits <= best_score: return None

    moves = _get_valid_moves_fast_worker(grid, rows, cols)
    if not moves:
        return [('TERMINAL', score, grid, history)]

    # 1. 计算所有移动的优先级
    scored_moves = []
    for r1, c1, r2, c2, count in moves:
        priority = _calculate_move_priority_worker(grid, r1, c1, r2, c2, count, rows, cols)
        scored_moves.append((priority, r1, c1, r2, c2, count))

    # 2. 排序并取 Top K (稍微放宽到 25 以防漏掉好解)
    scored_moves.sort(key=lambda x: x[0], reverse=True)
    top_moves = scored_moves[:25]

    new_states = []
    for _, r1, c1, r2, c2, count in top_moves:
        new_score = score + count
        new_grid = grid.copy()
        new_grid[r1 : r2 + 1, c1 : c2 + 1] = 0

        # 计算惩罚，但不再用于"枪毙"状态，只用于降低排名
        penalty = _analyze_grid_state(new_grid, rows, cols)

        new_remaining_digits = remaining_digits - count

        # f_score = 当前分 + 未来潜力 - 状态风险
        # 即使 penalty 很高，只要它是目前唯一的路径，我们也得走
        new_f_score = new_score + new_remaining_digits - (penalty / 50.0)

        new_history = history + [(r1, c1, r2, c2, count)]
        new_states.append((new_f_score, new_score, new_grid, new_history))

    return new_states


def _calculate_move_priority_worker(grid, r1, c1, r2, c2, count, rows, cols):
    """
    计算移动优先级（优化版）
    """
    # 1. 基础分：不再单纯乘以100，降低单纯消除数量的权重
    # 我们希望算法去寻找那些"解构性"的移动，而不仅仅是"得分"的移动
    score = count * 20

    # 2. 形状奖励：更强力地鼓励方块消除，严厉惩罚长条消除
    # 长条消除最容易切断连通性
    width = c2 - c1 + 1
    height = r2 - r1 + 1
    min_side = min(width, height)
    max_side = max(width, height)

    aspect_ratio = max_side / min_side
    if aspect_ratio == 1:
        score += 80  # 完美正方形
    elif aspect_ratio < 1.5:
        score += 40  # 接近正方形
    else:
        score -= aspect_ratio * 30  # 长条形惩罚

    # 3. 边缘优先策略（剥洋葱法）
    # 优先消除边缘的数字，把中间的留给后面，这样不容易产生孤岛
    center_r = rows / 2.0
    center_c = cols / 2.0
    avg_r = (r1 + r2) / 2.0
    avg_c = (c1 + c2) / 2.0

    # 归一化距离
    dist_r = abs(avg_r - center_r) / (rows / 2.0)
    dist_c = abs(avg_c - center_c) / (cols / 2.0)

    # 越靠边，分数越高
    score += (dist_r + dist_c) * 40

    # 4. 模拟移动后的连通性检测（这是计算瓶颈，但也是智能核心）
    # 为了性能，我们不做全图BFS，只做局部快速检查

    # 构造一个临时的掩码，表示消除后的空洞
    # 检查这个空洞周边的数字是否被"切断"了
    # 这里是一个简化的启发式逻辑，替代昂贵的全图BFS

    # 获取消除区域周边的数字数量
    perimeter_contacts = 0
    masked_grid = grid.copy()
    masked_grid[r1 : r2 + 1, c1 : c2 + 1] = 0

    # 检查消除区域的四条边之外是否有数字
    # 如果四边都有数字，说明我们在网格中间挖了一个洞，这很危险
    sides_contact = 0
    if r1 > 0 and np.any(grid[r1 - 1, c1 : c2 + 1] > 0):
        sides_contact += 1
    if r2 < rows - 1 and np.any(grid[r2 + 1, c1 : c2 + 1] > 0):
        sides_contact += 1
    if c1 > 0 and np.any(grid[r1 : r2 + 1, c1 - 1] > 0):
        sides_contact += 1
    if c2 < cols - 1 and np.any(grid[r1 : r2 + 1, c2 + 1] > 0):
        sides_contact += 1

    if sides_contact >= 3:
        score -= 150  # 严厉惩罚在中间挖洞的行为
    elif sides_contact == 2:
        score -= 50

    # 5. 数字稀缺性奖励
    # 如果消除了场上很多的数字（比如1），给予惩罚（保留火种）
    # 如果消除了场上很少的数字（比如9），给予奖励（清理难点）
    # (这一步可以在外部做，也可以简单实现)
    # 假设 vals 是消除的数字列表
    roi = grid[r1 : r2 + 1, c1 : c2 + 1]
    vals = roi[roi > 0]
    # 这里简单判定：如果包含大数字(7,8,9)，提权
    high_val_count = np.sum(vals >= 7)
    score += high_val_count * 15

    return score


def _get_valid_moves_fast_worker(grid, rows, cols):
    """全局函数：快速查找有效移动（用于多进程）"""
    moves = []
    p_sum = np.pad(grid, ((1, 0), (1, 0)), 'constant').cumsum(axis=0).cumsum(axis=1)
    p_count = np.pad((grid > 0).astype(np.int32), ((1, 0), (1, 0)), 'constant').cumsum(axis=0).cumsum(axis=1)

    for r1 in range(rows):
        for c1 in range(cols):
            for r2 in range(r1, rows):
                for c2 in range(c1, cols):
                    pr2, pc2 = r2 + 1, c2 + 1
                    pr1, pc1 = r1, c1
                    current_sum = p_sum[pr2, pc2] - p_sum[pr1, pc2] - p_sum[pr2, pc1] + p_sum[pr1, pc1]
                    if current_sum > 10:
                        break
                    if current_sum == 10:
                        count = p_count[pr2, pc2] - p_count[pr1, pc2] - p_count[pr2, pc1] + p_count[pr1, pc1]
                        if count > 0:
                            moves.append((r1, c1, r2, c2, count))
    return moves


def _analyze_grid_state(grid, rows, cols):
    """
    分析网格状态（温和版），避免因OCR小误差导致全盘放弃
    """
    penalty = 0

    # --- 1. 孤岛/连通性分析 (BFS) ---
    visited = np.zeros((rows, cols), dtype=bool)
    visited[grid == 0] = True

    for r in range(rows):
        for c in range(cols):
            if not visited[r, c] and grid[r, c] > 0:
                stack = [(r, c)]
                visited[r, c] = True
                island_sum = 0
                island_count = 0  # 统计孤岛大小

                while stack:
                    curr_r, curr_c = stack.pop()
                    val = grid[curr_r, curr_c]
                    island_sum += val
                    island_count += 1

                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nr, nc = curr_r + dr, curr_c + dc
                        if 0 <= nr < rows and 0 <= nc < cols:
                            if not visited[nr, nc] and grid[nr, nc] > 0:
                                visited[nr, nc] = True
                                stack.append((nr, nc))

                # --- 关键修改点 ---
                remainder = island_sum % 10
                if remainder != 0:
                    if island_count < 5:
                        # 小孤岛确实没救了，重罚
                        penalty += 1000
                    else:
                        # 大孤岛可能是OCR识别错了一个数字，或者是局面复杂
                        # 不要直接判死刑，给一个轻微的"不完美惩罚"
                        # 这样算法会倾向于去消除那些能让余数归零的数字，而不是直接放弃
                        penalty += 50

                # 只有单个数字且不是10，绝对死局
                if island_count == 1 and remainder != 0:
                    penalty += 5000

    # --- 2. 数字供需平衡分析 ---
    counts = np.bincount(grid[grid > 0].flatten(), minlength=11)
    for i in range(1, 5):
        diff = abs(counts[i] - counts[10 - i])
        penalty += diff * 10  # 稍微降低权重

    if counts[5] % 2 != 0:
        penalty += 10

    return penalty


class TenSumBeamSolverMultiThread:
    def __init__(self, complex_grid, beam_width=200, use_multiprocessing=True):
        """
        Args:
            complex_grid: recognize_digit_grid_robust 返回的二维数组
            beam_width: 搜索宽度（建议至少200以避免局部最优）
            use_multiprocessing: True=多进程(推荐), False=多线程
        """
        self.beam_width = beam_width
        # 自动获取 CPU 核心数
        self.num_workers = multiprocessing.cpu_count()
        self.use_multiprocessing = use_multiprocessing
        self.rows = len(complex_grid)
        self.cols = len(complex_grid[0]) if self.rows > 0 else 0

        # 数据预处理
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
        self.total_initial_digits = np.sum(raw_matrix > 0)
        self.best_global_score = 0

        # 线程安全的锁（仅用于多线程模式）
        self.score_lock = Lock() if not use_multiprocessing else None

        mode = 'multiprocessing' if use_multiprocessing else 'multithreading'
        logger.info(
            f'TenSumBeamSolver initialized: beam_width={self.beam_width}, '
            f'num_workers={self.num_workers} (CPU cores: {multiprocessing.cpu_count()}), mode={mode}'
        )

    def _get_valid_moves_fast(self, grid: np.ndarray):
        """实例方法：快速查找有效移动（增强版）"""
        return _get_valid_moves_fast_worker(grid, self.rows, self.cols)

    def _calculate_move_priority(self, grid, r1, c1, r2, c2, count):
        """
        计算移动的优先级（用于打破贪心陷阱）
        返回值越大越优先
        """
        # 1. 基础分数：消除的数字数量
        score = count * 100

        # 2. 位置惩罚：优先消除边缘（避免制造孤岛）
        center_r = self.rows / 2
        center_c = self.cols / 2
        avg_r = (r1 + r2) / 2
        avg_c = (c1 + c2) / 2
        distance_from_center = abs(avg_r - center_r) + abs(avg_c - center_c)
        score += distance_from_center * 2  # 边缘加分

        # 3. 形状奖励：方形 > 长条形（减少碎片化）
        width = c2 - c1 + 1
        height = r2 - r1 + 1
        aspect_ratio = max(width, height) / min(width, height)
        score -= aspect_ratio * 5  # 越方正越好

        # 4. 密度奖励：优先选择数字密集的区域
        roi = grid[r1 : r2 + 1, c1 : c2 + 1]
        total_cells = (r2 - r1 + 1) * (c2 - c1 + 1)
        density = count / total_cells
        score += density * 50

        # 5. 连通性检查：消除后不应制造孤立区域
        test_grid = grid.copy()
        test_grid[r1 : r2 + 1, c1 : c2 + 1] = 0
        # 简单检查：周围是否还有数字
        has_neighbors = False
        for dr in [-1, 0, 1]:
            for dc in [-1, 0, 1]:
                for rr in range(r1, r2 + 2):
                    for cc in range(c1, c2 + 2):
                        nr, nc = rr + dr, cc + dc
                        if 0 <= nr < self.rows and 0 <= nc < self.cols:
                            if test_grid[nr, nc] > 0:
                                has_neighbors = True
                                break
        if not has_neighbors and np.sum(test_grid > 0) > 0:
            score -= 100  # 严重惩罚制造孤岛的移动

        return score

    def _expand_state_thread(self, state_data):
        """线程模式的状态扩展（带锁）"""
        _, score, grid, history = state_data

        remaining_digits = np.sum(grid > 0)
        potential_max_score = score + remaining_digits

        with self.score_lock:
            current_best = self.best_global_score

        if potential_max_score <= current_best:
            return None

        moves = self._get_valid_moves_fast(grid)

        if not moves:
            with self.score_lock:
                if score > self.best_global_score:
                    self.best_global_score = score
                    return [('TERMINAL', score, grid, history)]
            return None

        # 对移动进行智能排序
        scored_moves = []
        for r1, c1, r2, c2, count in moves:
            priority = self._calculate_move_priority(grid, r1, c1, r2, c2, count)
            scored_moves.append((priority, r1, c1, r2, c2, count))

        scored_moves.sort(reverse=True)
        top_moves = scored_moves[: min(20, len(scored_moves))]

        new_states = []
        for priority, r1, c1, r2, c2, count in top_moves:
            new_score = score + count
            new_grid = grid.copy()
            new_grid[r1 : r2 + 1, c1 : c2 + 1] = 0
            new_remaining_digits = remaining_digits - count
            new_f_score = new_score + new_remaining_digits
            new_history = history + [(r1, c1, r2, c2, count)]
            new_states.append((new_f_score, new_score, new_grid, new_history))

        return new_states

    def solve(self):
        """执行优化的 Beam Search（支持多进程/多线程）"""
        initial_f_score = self.total_initial_digits
        current_states = [(initial_f_score, 0, self.initial_grid, [])]
        best_final_state = None

        start_time = time.time()

        # 性能统计
        parallel_time = 0
        serial_time = 0
        parallel_iterations = 0
        serial_iterations = 0

        # 根据模式选择执行器
        ExecutorClass = ProcessPoolExecutor if self.use_multiprocessing else ThreadPoolExecutor

        iteration = 0
        with ExecutorClass(max_workers=self.num_workers) as executor:
            while True:
                iteration += 1
                iter_start = time.time()
                next_states = []
                terminal_states = []

                # 小批次优化：当状态数很少时，不使用并行
                if len(current_states) < self.num_workers:
                    serial_iterations += 1
                    for state in current_states:
                        if self.use_multiprocessing:
                            result = _expand_state_worker(state, self.rows, self.cols, self.best_global_score)
                        else:
                            result = self._expand_state_thread(state)

                        if result:
                            for s in result:
                                if s[0] == 'TERMINAL':
                                    terminal_states.append(s)
                                else:
                                    next_states.append(s)
                    serial_time += time.time() - iter_start
                else:
                    parallel_iterations += 1
                    # 并行处理
                    if self.use_multiprocessing:
                        # 多进程：使用全局函数
                        futures = [
                            executor.submit(_expand_state_worker, state, self.rows, self.cols, self.best_global_score)
                            for state in current_states
                        ]
                    else:
                        # 多线程：使用实例方法
                        futures = [executor.submit(self._expand_state_thread, state) for state in current_states]

                    for future in futures:
                        result = future.result()
                        if result:
                            for s in result:
                                if s[0] == 'TERMINAL':
                                    terminal_states.append(s)
                                else:
                                    next_states.append(s)
                    parallel_time += time.time() - iter_start

                # 处理终止状态
                if terminal_states:
                    best_terminal = max(terminal_states, key=lambda x: x[1])
                    if best_final_state is None or best_terminal[1] > best_final_state[0]:
                        best_final_state = (best_terminal[1], best_terminal[2], best_terminal[3])
                        self.best_global_score = best_terminal[1]

                if not next_states:
                    break

                # 排序与去重
                sort_start = time.time()
                next_states.sort(key=lambda x: (x[0], x[1]), reverse=True)
                unique_states = []
                seen_grids = set()

                for state in next_states:
                    grid_bytes = state[2].tobytes()
                    if grid_bytes not in seen_grids:
                        seen_grids.add(grid_bytes)
                        unique_states.append(state)
                    if len(unique_states) >= self.beam_width:
                        break

                current_states = unique_states

                if iteration % 10 == 0:
                    logger.debug(f'Iteration {iteration}: {len(current_states)} states in beam')

        if best_final_state is None and current_states:
            current_states.sort(key=lambda x: x[1], reverse=True)
            best_final_state = (current_states[0][1], current_states[0][2], current_states[0][3])

        final_score, _, raw_steps = best_final_state
        total_time = time.time() - start_time

        # 详细性能报告
        parallel_ratio = (parallel_time / total_time * 100) if total_time > 0 else 0
        serial_ratio = (serial_time / total_time * 100) if total_time > 0 else 0

        logger.info(
            f'Calculation finished: {total_time:.3f}s, '
            f'Total eliminated: {final_score}, Best score: {self.best_global_score}, '
            f'Iterations: {iteration}'
        )
        logger.info(
            f'Performance breakdown: '
            f'Parallel={parallel_iterations} iters ({parallel_time:.3f}s, {parallel_ratio:.1f}%), '
            f'Serial={serial_iterations} iters ({serial_time:.3f}s, {serial_ratio:.1f}%)'
        )

        if parallel_iterations > 0:
            theoretical_speedup = self.num_workers
            actual_speedup = (
                total_time / (total_time - parallel_time + parallel_time / self.num_workers) if parallel_time > 0 else 1
            )
            efficiency = (actual_speedup / theoretical_speedup * 100) if theoretical_speedup > 0 else 0
            logger.info(
                f'Parallel efficiency: {efficiency:.1f}% '
                f'(theoretical {theoretical_speedup}x, actual ~{actual_speedup:.2f}x on parallel portion)'
            )

        return self._format_output(raw_steps)

    def _format_output(self, raw_steps: list):
        """格式化输出"""
        formatted_steps = []
        replay_grid = self.initial_grid.copy()

        for r1, c1, r2, c2, count in raw_steps:
            start_x, start_y = self.coord_map.get((r1, c1), (0, 0))
            end_x, end_y = self.coord_map.get((r2, c2), (0, 0))
            roi = replay_grid[r1 : r2 + 1, c1 : c2 + 1]
            eliminated_values = roi[roi > 0].tolist()
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
