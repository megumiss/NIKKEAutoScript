import math

from module.base.timer import Timer
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
            self.check_squad()
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

        # 打开任务面板
        self.open_mission_board()
        # 当前序号的任务已结束，进入下一个
        if not self.appear(globals()[f'MISSION_{index}_CONFIG'], offset=10):
            return self.mission(index=index + 1)

        # 修改任务区域
        self.change_mission_sector(index=index)
        # 开始任务，点击箭头
        self.start_mission(index=index)
        # 放置队伍
        self.squad_play()
        # 领取奖励
        self.reward(index=index + 1)

        return self.mission(index=index + 1)

    def reward(self, skip_first_screenshot=True, index=1):
        logger.info('Reward receive')

        self.open_mission_board()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 任务完成按钮
            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear_then_click(
                globals()[f'MISSION_{index}_REWARD'], offset=10, interval=1
            ):
                continue

            # 任务票确定
            if self.appear(SURFACE_MISSION_REWARD_CHECK, offset=10) and self.appear_then_click(
                MISSION_REWARD_TECKET_1, offset=10, interval=1
            ):
                continue

            if self.handle_reward(interval=2):
                continue

            # 领取完成
            if self.appear(SURFACE_MISSION_CHECK, offset=10) and not self.appear(
                globals()[f'MISSION_{index}_DONE'], offset=10
            ):
                logger.info(f'Mission reward {index} done')
                self.device.sleep(3)
                break

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
        played_all = False
        squad_status = self.SQUAD_LISTS_STATUS.copy()
        # 每个队伍的放置冷却计时器
        squad_cooldowns = {1: None, 2: None, 3: None}

        # 找到中心点并计算队伍目标点
        left, right = self.get_squad_target_points()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 任务完成
            if self.appear(MISSION_DONE, offset=10):
                return

            # 检查是否所有队伍都已就位
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

            # 遍历三个队伍
            for squad in [1, 2, 3]:
                # 冷却检查
                if squad_cooldowns[squad] and not squad_cooldowns[squad].reached():
                    remain = squad_cooldowns[squad].remain()
                    logger.info(f'Squad {squad} cooling down ({remain:.1f}s left)')
                    continue

                target_points = right
                # 地面中没有队伍数字
                if (
                    not self.appear(globals()[f'SQUAD_{squad}_IN_SURFACE'], offset=(150, 100), threshold=0.95)
                    and not squad_status[f'SQUAD_{squad}']
                ):
                    # 选择队伍
                    squad_selected = False
                    while 1:
                        self.device.screenshot()

                        # 打开队伍侧边栏
                        if self.appear_then_click(SQUAD_EXPAND, offset=10, interval=1):
                            continue

                        # 点击队伍
                        if self.appear_then_click(globals()[f'SQUAD_{squad}'], offset=10, interval=1):
                            squad_checker = Timer(1, count=3)
                            # 检查队伍是否选中
                            while 1:
                                self.device.screenshot()

                                if self.appear(SQUAD_CLOSE, offset=10):
                                    if not squad_checker.started():
                                        squad_checker.start()
                                    if squad_checker.reached():
                                        squad_selected = True
                                        break
                                else:
                                    squad_checker.clear()

                        # 折叠队伍
                        if squad_selected and self.appear_then_click(SQUAD_FOLD, offset=10, interval=1):
                            continue
                        if squad_selected and self.appear(SQUAD_EXPAND, offset=10):
                            logger.info(f'Squad {squad} selected')
                            break

                    # 点击目标点，根据队伍序号指定目标点
                    # 先点击REGHT第一个点查看是否是小怪，是则换LEFT
                    self.device.click_minitouch(target_points[squad - 1])
                    self.device.sleep(0.5)
                    if self.appear(ENEMY_CLOSE, offset=10):
                        target_points = left
                        self.device.click_minitouch(target_points[squad - 1])

                    # 设置15秒冷却计时器
                    squad_cooldowns[squad] = Timer(15)
                    squad_cooldowns[squad].start()
                    logger.info(f'Squad {squad} placed — cooling down for 15s')
                else:
                    # 队伍就位了
                    squad_status[f'SQUAD_{squad}'] = True
                    logger.info(f'Squad {squad} already on target')

    def get_squad_target_points(self, skip_first_screenshot=True):
        """根据任务目标中心点获取队伍目标点，必然存在2种情况之一: 一个叹号在中心点（防御）/存在任务区域中心点空地"""
        logger.info('Finding mission center point')

        mission_center_point = (350, 580)
        mission_center_checker = Timer(5, count=5)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 防御任务小图标
            if self.appear(DEFENSE_START, offset=10, static=False):
                center_x, center_y = self.appear_location(DEFENSE_START, offset=10, static=False)
                if center_y < 800 and center_y > 300:
                    logger.info(f'Found mission center point {center_x},{center_y}')
                    mission_center_point = (center_x, center_y + 50)
                    break
            # 任务中心黑色空地
            if self.appear(SURFACE_MISSION_CENTER, offset=10, static=False):
                center_x, center_y = self.appear_location(SURFACE_MISSION_CENTER, offset=10, static=False)
                if center_y < 800 and center_y > 300:
                    logger.info(f'Found mission center point {center_x},{center_y}')
                    mission_center_point = (center_x, center_y)
                    break

            if not mission_center_checker.started():
                mission_center_checker.start()
            if mission_center_checker.reached():
                raise IsMissionCheckFailed

        # 计算队伍目标点，LEFT和RIGHT
        def hex_neighbors(x0, y0, a=125):
            d = math.sqrt(3) * a  # 中心间距
            # 按 “左上 → 左 → 左下 → 右下 → 右 → 右上” 顺序
            angles_deg = [120, 180, 240, 300, 0, 60]
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

                centers.append((round(x, 4), round(y, 4)))
            return centers

        # 所有目标点
        points = hex_neighbors(mission_center_point[0], mission_center_point[1])
        return [points[0], points[2], points[4]], [points[1], points[3], points[5]]

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

            if not self.appear(SURFACE_MISSION_CHECK, offset=10):
                logger.info('Closed mission board')
                break

    def run(self):
        self.ui_ensure(page_surface)
        self._run()
        self.config.task_delay(server_update=True)
