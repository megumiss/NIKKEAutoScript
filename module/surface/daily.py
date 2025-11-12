from module.base.timer import Timer
from module.logger import logger
from module.surface.assets import *
from module.ui.page import page_surface
from module.ui.ui import UI


class NoOpportunityRemain(Exception):
    pass


class IsSquadValid(Exception):
    pass


class SurfaceDaily(UI):
    # 队伍图标
    SQUAD_LISTS = {'SQUAD_1': SQUAD_1_IN_HOSPITAL, 'SQUAD_2': SQUAD_2_IN_HOSPITAL, 'SQUAD_3': SQUAD_3_IN_HOSPITAL}
    # 队伍目标点，固定点，三队，间隔一个格子，REGHT和LEFT分别以上右和上左为1开始编号
    SQUAD_TARGET_POINT_REGHT = [(425, 605), (430, 775), (220, 685)]
    SQUAD_TARGET_POINT_LEFT = [(295, 605), (485, 685), (295, 775)]
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
            logger.warning('Squad 1/2/3 not valid')

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
        done = self.squad_play()
        # 任务结束
        if done:
            self.mission(index=index + 1)

        return self.mission(index=index + 1)

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

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 任务完成
            if self.appear(MISSION_DONE, offset=10):
                return True

            # 防御小图标
            if played_all and self.appear_then_click(DEFENSE_CONFIRM, offset=10, interval=1, static=False):
                continue

            # 防御开始
            if self.appear_then_click(DEFENSE_START, offset=10, interval=1, static=False):
                continue

            # 箱子
            if played_all and self.appear_then_click(SURFACE_BOX, offset=10, interval=1, static=False):
                continue

            for squad in [1, 2, 3]:
                target_points = self.SQUAD_TARGET_POINT_REGHT
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
                        target_points = self.SQUAD_TARGET_POINT_LEFT
                        self.device.click_minitouch(target_points[squad - 1])
                        # 假定队伍就位了
                        squad_status[f'SQUAD_{squad}'] = True
                else:
                    # 队伍就位了
                    squad_status[f'SQUAD_{squad}'] = True
                    logger.info(f'Squad {squad} allready on target')

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
        # self.config.task_delay(server_update=True)
