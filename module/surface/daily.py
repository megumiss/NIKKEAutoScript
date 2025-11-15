import math
import random

from module.base.timer import Timer
from module.base.utils import point2str
from module.logger import logger
from module.surface.assets import *
from module.ui.page import page_surface
from module.ui.ui import UI


class NoOpportunityRemain(Exception):
    pass


class IsSquadValid(Exception):
    pass


class IsMissionCheckFailed(Exception):
    pass


class SurfaceDaily(UI):
    # 队伍图标
    SQUAD_LISTS = {'SQUAD_1': SQUAD_1_IN_HOSPITAL, 'SQUAD_2': SQUAD_2_IN_HOSPITAL, 'SQUAD_3': SQUAD_3_IN_HOSPITAL}
    # 队伍目标点，固定点，三队，间隔一个格子，REGHT和LEFT分别以上右和上左开始编号，以左上为1开始编号
    # SQUAD_TARGET_POINT_REGHT = [(425, 605), (430, 775), (220, 685)]  # 246
    # SQUAD_TARGET_POINT_LEFT = [(295, 605), (485, 685), (295, 775)]  # 135
    # 队伍放置状态
    SQUAD_LISTS_STATUS = {'SQUAD_1': False, 'SQUAD_2': False, 'SQUAD_3': False}

    def _run(self):
        try:
            # 检查队伍
            # self.check_squad()
            # 开始任务
            self.mission()
        except NoOpportunityRemain:
            self.close_mission_board()
            logger.warning('The mission has no remaining opportunities')
        except IsSquadValid:
            logger.error('Squad 1/2/3 not valid')
        except IsMissionCheckFailed:
            logger.error('Mission check error in surface')

    def mission(self, index=1):
        logger.hr('Start a mission', 2)
        index = (index - 1) % 3 + 1

        # 打开任务面板
        self.open_mission_board()
        # 当前序号的任务已结束，进入下一个
        if not self.appear(globals()[f'MISSION_{index}_CONFIG'], offset=10):
            return self.mission(index=index + 1)

        # # 修改任务区域
        self.change_mission_sector(index=index)
        # 开始任务，点击箭头
        self.start_mission(index=index)
        # 放置队伍
        self.squad_play()
        # 领取奖励
        self.reward(index=index)

        return self.mission(index=index + 1)

    def reward(self, skip_first_screenshot=True, index=1):
        logger.info('Reward receive')
        board_checker = Timer(1, count=3)

        self.open_mission_board()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 领取完成
            if self.appear(SURFACE_MISSION_CHECK, offset=10) and (
                self.appear(globals()[f'MISSION_{index}_DONE'], offset=10)
                or self.appear(globals()[f'MISSION_{index}_ACCEPT'], offset=10)
            ):
                if not board_checker.started():
                    board_checker.start()
                if board_checker.reached():
                    logger.info(f'Mission reward {index} done')
                    break
            else:
                board_checker.clear()

            # 任务完成按钮
            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear_then_click(
                globals()[f'MISSION_{index}_REWARD'], offset=10, interval=1
            ):
                continue

            # 任务票确定
            if self.appear(SURFACE_MISSION_REWARD_CHECK, offset=10) and self.appear_then_click(
                MISSION_REWARD_TECKET_1, offset=10, interval=3
            ):
                continue

            if self.handle_reward(interval=2):
                continue

            if self.appear_then_click(MISSION_REWARD_CONFIRM, offset=10, interval=1):
                continue

        self.close_mission_board()

    def start_mission(self, skip_first_screenshot=True, index=1):
        logger.info('Mission start')

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 任务开始箭头
            if (
                self.appear(SURFACE_MISSION_CHECK, offset=10)
                and not self.appear(globals()[f'MISSION_{index}_ACCEPT'], offset=10)
                and self.appear_then_click(globals()[f'MISSION_{index}_START'], offset=10, interval=1)
            ):
                continue

            # 任务弹窗关闭
            if not self.appear(SURFACE_MISSION_CHECK, offset=10):
                logger.info(f'Mission {index} start')
                self.device.sleep(3)
                break

            # 接任务
            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear_then_click(
                globals()[f'MISSION_{index}_ACCEPT'], offset=10, interval=1
            ):
                logger.info(f'Mission {index} accept')
                continue

    def squad_play(self, skip_first_screenshot=True):
        logger.info('Play squad')
        self.device.stuck_record_clear()

        played_all = False
        squad_status = self.SQUAD_LISTS_STATUS.copy()

        # 每个队伍的放置冷却计时器
        squad_cooldowns = {1: None, 2: None, 3: None}

        # 获取左右两组目标点，默认选择右
        left, right, marks = self.get_squad_target_points()
        logger.info(f'Found squad target point, [LEFT]: {left}, [RIGHT]: {right}')
        target_points = right
        # 有三个感叹号，判断是LEFT还是RIGHT，队伍目标点取相反
        if marks and len(marks) == 3 and self.classify_triangle(marks) == 'RIGHT':
            logger.info('Change squad target point to LEFT')
            target_points = left

        # 队伍数字图标的判断范围
        SQUAD_POS_TOL = 75
        # 队伍数字图标的坐标偏移
        SQUAD_POS_OFFSET = (-70, 60)
        # 队伍小箭头的判断范围
        SQUAD_ARROW_POS_TOL = 50
        # 队伍小箭头的坐标偏移
        SQUAD_ARROW_POS_OFFSET = (-110, 0)

        def is_squad_at_target(squad_point, target_points, pos_offset, pos_tol):
            """
            判断偏移后的队伍坐标是否落在 target_points 中任意一个目标点的容差范围内
            返回: (is_match, index)
            """
            ox = squad_point[0] + pos_offset[0]
            oy = squad_point[1] + pos_offset[1]
            for idx, (tx, ty) in enumerate(target_points):
                # 容差范围判断（矩形）
                if tx - pos_tol <= ox <= tx + pos_tol and ty - pos_tol <= oy <= ty + pos_tol:
                    return True, idx
            return False, None

        # 数字图标
        def detect_squad_position_from_num(squad):
            """
            检查队伍数字图标是否在目标点附近
            返回: (matched: bool, index: int | None)
            """
            squad_loc = self.appear_location(globals()[f'SQUAD_{squad}_IN_SURFACE'], offset=(150, 100), static=False)
            if squad_loc:
                match, idx = is_squad_at_target(squad_loc, target_points, SQUAD_POS_OFFSET, SQUAD_POS_TOL)
                if match:
                    logger.info(f'[Check] Squad {squad} number icon at target #{idx + 1}')
                    return True, idx

            return False, None

        # 箭头检测
        def detect_squad_position_from_arrow(squad):
            """
            检查队伍队伍箭头是否在目标点附近
            返回: (matched: bool, index: int | None)
            """
            arrow_loc = self.appear_location(SQUAD_POINTING_ARROW, offset=10, static=False)
            if arrow_loc:
                match, idx = is_squad_at_target(arrow_loc, target_points, SQUAD_ARROW_POS_OFFSET, SQUAD_ARROW_POS_TOL)
                if match:
                    logger.info(f'[Check] Squad {squad} arrow icon at target #{idx + 1}')
                    return True, idx

            return False, None

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 任务完成
            if self.appear(MISSION_DONE_1, offset=10) or self.appear(MISSION_DONE_2, offset=100):
                return

            # 是否所有队伍已就位
            if not played_all and all(squad_status.values()):
                played_all = True
                logger.info('All squads deployed')

            # 防御开始
            if played_all and self.appear_then_click(DEFENSE_CONFIRM, offset=10, interval=1, static=False):
                continue

            # 防御小图标
            if played_all and self.appear_then_click(DEFENSE_START, offset=10, interval=1, static=False):
                continue

            # 箱子
            if played_all and self.appear_then_click(SURFACE_BOX, offset=10, interval=1, static=False):
                continue

            # 自动放置开启或者或者有队伍没放置完成
            if self.config.SurfaceDaily_AutoPlace and not played_all:
                # 遍历三个队伍
                for squad in random.sample([1, 2, 3], 3):
                    # 冷却检查
                    if squad_cooldowns[squad] and not squad_cooldowns[squad].reached():
                        continue

                    # 获取队伍所在位置
                    matched, idx = detect_squad_position_from_num(squad)
                    if matched:
                        squad_status[f'SQUAD_{squad}'] = True
                        logger.info(f'Squad {squad} already at target point #{idx + 1}')
                        continue

                    # 队伍未放置，需要执行放置逻辑
                    logger.info(f'Squad {squad} playing')

                    # 先取消队伍选择
                    while 1:
                        self.device.screenshot()
                        if not self.appear(SQUAD_CLOSE, offset=10):
                            break
                        if self.appear_then_click(SQUAD_CLOSE, offset=10, interval=1):
                            continue

                    # 选择队伍
                    squad_selected = False
                    while 1:
                        self.device.screenshot()

                        # 再次检测队伍是否就位
                        matched, idx = detect_squad_position_from_arrow(squad)
                        if matched:
                            squad_status[f'SQUAD_{squad}'] = True
                            logger.info(f'Squad {squad} reached target during selection — skip placement')
                            break
                        matched, idx = detect_squad_position_from_num(squad)
                        if matched:
                            squad_status[f'SQUAD_{squad}'] = True
                            logger.info(f'Squad {squad} reached target during selection — skip placement')
                            break

                        # 放置实在太耗时，任务可能已经完成
                        if self.appear(MISSION_DONE_1, offset=10) or self.appear(MISSION_DONE_2, offset=100):
                            while 1:
                                self.device.screenshot()
                                # 折叠队伍
                                if self.appear_then_click(SQUAD_FOLD, offset=10, interval=1):
                                    continue
                                if self.appear(SQUAD_EXPAND, offset=10):
                                    break
                            return

                        if squad_selected and self.appear(SQUAD_EXPAND, offset=10):
                            logger.info(f'Squad {squad} selected')
                            break

                        # 打开队伍侧边栏
                        if self.appear_then_click(SQUAD_EXPAND, offset=10, interval=1):
                            continue

                        # 折叠队伍
                        if squad_selected and self.appear_then_click(SQUAD_FOLD, offset=10, interval=1):
                            continue

                        # 下方出现队伍弹窗，并且出现队伍上方的箭头
                        if self.appear(SQUAD_CLOSE, offset=10) or self.appear(
                            SQUAD_POINTING_ARROW, offset=10, static=False
                        ):
                            squad_selected = True
                            continue

                        # 点击队伍
                        if self.appear_then_click(
                            globals()[f'SQUAD_{squad}'], offset=10, click_offset=(20, 20), interval=1
                        ):
                            continue

                    self.device.screenshot()
                    # 点击目标点前最后再检查一次
                    matched, idx = detect_squad_position_from_arrow(squad)
                    if matched:
                        squad_status[f'SQUAD_{squad}'] = True
                        logger.info(f'Squad {squad} reached target during selection — skip placement')
                        break
                    matched, idx = detect_squad_position_from_num(squad)
                    if matched:
                        squad_status[f'SQUAD_{squad}'] = True
                        logger.info(f'Squad {squad} reached target during selection — skip placement')
                        break

                    # 点击目标点
                    self.device.click_minitouch(target_points[squad - 1][0], target_points[squad - 1][1])
                    logger.info(
                        'Click %s @ %s'
                        % (point2str(target_points[squad - 1][0], target_points[squad - 1][1]), f'SQUAD POINT {squad}')
                    )
                    # self.device.sleep(0.5)
                    # 点击到的是小怪会有小怪弹窗
                    # if self.appear(ENEMY_CLOSE, offset=10):
                    #     target_points = left
                    #     self.device.click_minitouch(target_points[squad - 1][0], target_points[squad - 1][1])
                    #     logger.info(
                    #         'Click %s @ %s'
                    #         % (point2str(target_points[squad - 1][0], target_points[squad - 1][1]), f'LEFT POINT {squad}')
                    #     )

                    # 冷却 30 秒
                    squad_cooldowns[squad] = Timer(30)
                    squad_cooldowns[squad].start()
                    logger.info(f'Squad {squad} placed — cooling down for 30s')

    def get_squad_target_points(self, skip_first_screenshot=True):
        """根据任务目标中心点获取队伍目标点"""
        logger.info('Finding mission center point')
        marks = None

        mission_center_point = (350, 580)
        mission_center_checker = Timer(5, count=5)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 获取任务感叹号数量，数量为1时直接返回坐标，数量为3时计算坐标
            if self.appear(SURFACE_MISSION_MARK, offset=10, static=False):
                marks = TEMPLATE_MISSION_MARK.match_multi(self.device.image, name='MISSION_MARK')
                if len(marks) == 1:
                    # 防御或者箱子的感叹号
                    mission_center_point = (marks[0].location[0], marks[0].location[1] + 65)
                    logger.info(f'Found mission center point: ({mission_center_point[0]}, {mission_center_point[1]})')
                    break
                elif len(marks) == 3:
                    # 三个叹号即三个小怪，根据叹号坐标获取中心点
                    center = self.calc_hex_center_from_marks(marks)
                    mission_center_point = (center[0], center[1] + 65)
                    logger.info(f'Found mission center point: ({mission_center_point[0]}, {mission_center_point[1]})')
                    break
                else:
                    raise IsMissionCheckFailed

            if not mission_center_checker.started():
                mission_center_checker.start()
            if mission_center_checker.reached():
                raise IsMissionCheckFailed

        # 计算队伍目标点，LEFT和RIGHT
        def hex_neighbors(x0, y0, a=60):
            d = math.sqrt(3) * a  # 中心间距
            # 从左上开始顺时针顺序
            angles_deg = [240, 300, 0, 60, 120, 180]
            centers = []
            for angle in angles_deg:
                rad = math.radians(angle)
                x = x0 + d * math.cos(rad)
                y = y0 + d * math.sin(rad)

                # 边界限制
                if x < 0:
                    x = 10
                elif x > 720:
                    x = 710

                centers.append((int(round(x, 4)), int(round(y, 4))))
            return centers

        # 所有目标点
        points = hex_neighbors(mission_center_point[0], mission_center_point[1])
        return (
            [
                (points[0][0] - 15, points[0][1]),
                (points[2][0] + 30, points[2][1]),
                (points[4][0] - 15, points[4][1]),
            ],
            [
                (points[1][0] + 15, points[1][1]),
                (points[3][0] + 15, points[3][1]),
                (points[5][0] - 30, points[5][1]),
            ],
            marks,
        )

    def change_mission_sector(self, skip_first_screenshot=True, index=1):
        logger.info('Change mission sector')

        selected = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 选择区域1
            if self.appear(SECTOR_SELECT_CHECK, offset=10) and self.appear_then_click(
                SECTOR_1_NOT_SELECTED, offset=10, interval=1
            ):
                selected = True
                continue

            # 确定
            if self.appear(SECTOR_1_NOT_SELECTED, offset=10) and self.appear_then_click(
                SECTOR_SELECT_CONFIRM, offset=10, interval=1
            ):
                continue

            # 回到任务面板
            if selected and self.appear(globals()[f'MISSION_{index}_CONFIG'], offset=10):
                break

            # 打开区域选择弹窗
            if not selected and self.appear_then_click(globals()[f'MISSION_{index}_CONFIG'], offset=10, interval=1):
                continue

    def check_squad(self, skip_first_screenshot=True):
        logger.info('Open squad list')

        checked = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 队伍侧边栏已打开
            if self.appear(SQUAD_FOLD, offset=10):
                for squad, hospita_check in self.SQUAD_LISTS.items():
                    if not self.appear(globals()[squad], offset=10) or self.appear(hospita_check, offset=10):
                        raise IsSquadValid
                checked = True
            # 打开队伍侧边栏
            if self.appear_then_click(SQUAD_EXPAND, offset=10, interval=1):
                continue

            # 折叠队伍
            if checked and self.appear_then_click(SQUAD_FOLD, offset=10, interval=1):
                break
            # 队伍已折叠
            if checked and self.appear(SQUAD_EXPAND, offset=10):
                break

    def open_mission_board(self, skip_first_screenshot=True):
        logger.info('Open mission board')

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 没次数
            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear(MISSION_REMAIN_0, offset=10):
                raise NoOpportunityRemain

            # 打开任务弹窗
            if not self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear_then_click(
                QUEST, offset=(30, 30), interval=2
            ):
                continue

            # 任务弹窗加载完成
            if (
                self.appear(SURFACE_MISSION_CHECK, offset=10)
                and self.appear(SURFACE_MISSION_LOAD_CHECK, offset=10)
                and not self.appear(MISSION_REMAIN_0, offset=10)
            ):
                logger.info('Opened mission board')
                break

    def close_mission_board(self):
        logger.info('Close mission board')

        while 1:
            self.device.screenshot()

            # 关闭任务弹窗
            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear_then_click(
                SURFACE_MISSION_CLOSE, offset=(30, 30), interval=1
            ):
                continue

            # 关闭下方的队伍选择框
            if self.appear_then_click(SQUAD_CLOSE, offset=(30, 30), interval=1):
                continue

            if not self.appear(SURFACE_MISSION_CHECK, offset=10):
                logger.info('Closed mission board')
                break

    def calc_hex_center_from_marks(self, marks):
        """
        根据 marks 中三个点的 .location 坐标，计算中心点坐标（取整）
        :param marks: 对象列表，每个对象有属性 .location = (x, y)
        :return: (cx, cy)
        """
        if len(marks) != 3:
            raise ValueError('必须提供三个 marks 对象')

        # 提取坐标
        points = [m.location for m in marks]

        # 计算重心
        cx = sum(p[0] for p in points) / 3
        cy = sum(p[1] for p in points) / 3

        # 取整
        return (int(round(cx)), int(round(cy)))

    def classify_triangle(self, marks, tol_deg=20):
        """
        根据三个 marks 的坐标判断属于哪一组三角形（left 或 right）

        :param marks: 对象列表，每个对象有 .location = (x, y)
        :param tol_deg: 容差角度，默认 20°
        :return: "left" 或 "right" 或 "unknown"
        """
        if len(marks) != 3:
            raise ValueError('需要正好三个 marks')

        # 提取坐标
        points = [m.location for m in marks]

        # 计算质心（近似中心）
        x0 = sum(p[0] for p in points) / 3
        y0 = sum(p[1] for p in points) / 3

        # 任取一个点计算角度
        x, y = points[0]
        ang = math.degrees(math.atan2(y - y0, x - x0))
        if ang < 0:
            ang += 360

        # 角度模120
        mod = ang % 120

        # 判断靠近哪一组
        if abs(mod - 0) < tol_deg or abs(mod - 120) < tol_deg:
            return 'LEFT'
        elif abs(mod - 60) < tol_deg:
            return 'RIGHT'
        else:
            return 'unknown'

    def run(self):
        self.ui_ensure(page_surface)
        self._run()
        self.close_mission_board()
        self.config.task_delay(server_update=True)
