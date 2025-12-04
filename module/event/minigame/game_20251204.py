import time

from module.conversation.assets import ANSWER_CHECK
from module.event.event_20251204.assets_game import *

from module.base.timer import Timer
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

    # 游戏逻辑处理
    while 1:
        self.device.screenshot()

        # 结束
        if click_timer.reached() and self.appear_then_click(MINI_GAME_BACK, offset=10, interval=2):
            logger.info('Event mini game done')
            click_timer.reset()
            continue

        # 关闭弹窗
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


class TenSumBeamSolver:
    def __init__(self, rows, cols, grid_data, beam_width=50):
        self.rows = rows
        self.cols = cols
        self.beam_width = beam_width
        self.grid = [row[:] for row in grid_data]

    def get_valid_moves(self, grid):
        """
        寻找所有和为10的矩形组合
        """
        moves = []
        rows, cols = self.rows, self.cols

        for r1 in range(rows):
            for c1 in range(cols):
                # 性能优化：如果起点已经是0，也可以作为矩形的一部分，
                # 但如果是单点检查且为0，则没意义（虽然由循环逻辑覆盖）

                for r2 in range(r1, rows):
                    for c2 in range(c1, cols):
                        # 计算当前矩形的和与非零个数
                        s, count = self.fast_sum(grid, r1, c1, r2, c2)

                        # 规则1：和为10 且 规则4：至少消掉一个数
                        if s == 10 and count > 0:
                            moves.append(((r1, c1, r2, c2), count))

                        # 剪枝：如果和已经超过10，因为数字都是正数，
                        # 再向右扩展(c2增加)只会更大，直接跳出当前内层循环
                        if s > 10:
                            break
        return moves

    def fast_sum(self, grid, r1, c1, r2, c2):
        """快速计算区域和，带剪枝"""
        s = 0
        c = 0
        for i in range(r1, r2 + 1):
            row_data = grid[i]
            for j in range(c1, c2 + 1):
                v = row_data[j]
                s += v
                if v > 0:
                    c += 1
                # 内部剪枝：一旦超过10，立即返回，不需要算完
                if s > 10:
                    return s, c
        return s, c

    def apply_move(self, grid, coords):
        """执行消除"""
        # 使用列表推导式复制，比 deepcopy 快
        new_grid = [row[:] for row in grid]
        r1, c1, r2, c2 = coords
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                new_grid[r][c] = 0
        return new_grid

    def solve(self):
        # 状态元组: (当前消除总数, 当前网格, 历史步骤列表)
        # history 存储格式: [(coords, count), ...]
        current_states = [(0, self.grid, [])]

        best_final_state = None

        print(f'开始计算 {self.rows}x{self.cols} 表格...')
        start_time = time.time()

        round_idx = 0
        while True:
            round_idx += 1
            next_states = []
            expanded_any = False

            # 遍历 Beam 中的所有优选状态
            for score, grid, history in current_states:
                moves = self.get_valid_moves(grid)

                if not moves:
                    # 如果当前分支走不下去了，检查是否是目前最好的结果
                    if best_final_state is None or score > best_final_state[0]:
                        best_final_state = (score, grid, history)
                    continue

                expanded_any = True

                # 尝试所有合法的下一步
                for coords, count in moves:
                    new_score = score + count
                    new_grid = self.apply_move(grid, coords)
                    new_history = history + [(coords, count)]
                    next_states.append((new_score, new_grid, new_history))

            if not expanded_any:
                break

            # --- Beam Search 核心 ---
            # 1. 按消除数量倒序排（优先选消除多的）
            next_states.sort(key=lambda x: x[0], reverse=True)

            # 2. 去重（防止不同顺序达到相同盘面的重复计算）
            unique_states = []
            seen_grids = set()

            for state in next_states:
                # 将 grid 转为 tuple 以便 hash
                grid_tuple = tuple(tuple(row) for row in state[1])
                if grid_tuple not in seen_grids:
                    seen_grids.add(grid_tuple)
                    unique_states.append(state)
                # 3. 截断，只保留前 N 个最好的
                if len(unique_states) >= self.beam_width:
                    break

            current_states = unique_states
            # 可选：打印进度
            # print(f"Round {round_idx}: 当前最高分 {current_states[0][0]}")

        end_time = time.time()
        print(f'计算结束，耗时 {end_time - start_time:.2f} 秒')

        # 如果所有分支都还没走完循环就结束了（极少情况），取当前最好的
        if best_final_state is None and current_states:
            best_final_state = current_states[0]

        return best_final_state


def parse_grid_string(input_str, rows, cols):
    """解析空格分隔的字符串为二维数组"""
    # 移除多余空格并分割
    nums = [int(x) for x in input_str.strip().split()]

    if len(nums) != rows * cols:
        raise ValueError(f'输入数据错误：字符串包含 {len(nums)} 个数字，但表格定义为 {rows}x{cols}={rows * cols} 个。')

    grid = []
    for r in range(rows):
        start = r * cols
        end = start + cols
        grid.append(nums[start:end])
    return grid


def print_final_grid(grid):
    print('\n最终剩余网格 (0代表已消除):')
    print('-' * (len(grid[0]) * 3 + 1))
    for row in grid:
        print('|' + '|'.join(f'{x:2}' if x != 0 else '  ' for x in row) + '|')
    print('-' * (len(grid[0]) * 3 + 1))


# ==========================================
#              使用示例
# ==========================================

# # 1. 定义表格尺寸
# ROWS = 14
# COLS = 8

# # 2. 定义输入字符串 (空格分隔，换行符不影响，只要总数对即可)
# # 这里模拟一个 4x6 的输入 (24个数字)
# input_data_str = """
# 8 7 2 8 1 3 3 6
# 7 1 8 4 8 7 1 3
# 6 7 2 7 3 9 1 4
# 5 5 1 3 2 4 9 9
# 2 7 7 3 6 1 4 4
# 8 6 3 3 4 7 5 2
# 5 1 7 2 9 6 9 9
# 6 8 1 4 9 2 4 2
# 8 8 5 7 4 3 9 1
# 4 8 6 1 7 6 3 5
# 8 6 3 2 6 4 3 8
# 8 3 9 5 1 1 6 6
# 5 6 1 3 5 1 9 2
# 5 6 5 2 2 8 9 7
# """

# try:
#     # 解析数据
#     grid_data = parse_grid_string(input_data_str, ROWS, COLS)

#     # 初始化求解器 (beam_width 越大越准但越慢，50-100 通常足够)
#     solver = TenSumBeamSolver(ROWS, COLS, grid_data, beam_width=100)

#     # 求解
#     best_score, final_grid, steps = solver.solve()

#     # 输出结果
#     print('=' * 40)
#     print(f'【结果报告】')
#     print(f'初始数字总数: {ROWS * COLS}')
#     print(f'消除数字总数: {best_score}')
#     print(f'剩余数字总数: {(ROWS * COLS) - best_score}')
#     print('=' * 40)

#     print(f'【详细步骤 ({len(steps)} 步)】')
#     # 输出所有步骤
#     for i, step in enumerate(steps):
#         coords, count = step
#         r1, c1, r2, c2 = coords
#         # 为了阅读方便，坐标显示为 (行,列)，从 0 开始
#         # 如果习惯 Excel 风格 (A1, B2)，也可以在这里转换
#         print(f'Step {i + 1:02d}: 框选 ({r1}, {c1}) -> ({r2}, {c2}) \t| 消除 {count} 个数字')

#     # 打印最终盘面
#     print_final_grid(final_grid)

# except ValueError as e:
#     print(f'错误: {e}')
# except Exception as e:
#     print(f'发生未知错误: {e}')
