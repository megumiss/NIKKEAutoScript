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
    # 队伍目标点, 假设是固定点; REGHT和LEFT分别以上右和上左为1开始编号
    SQUAD_TARGET_POINT_REGHT = []
    SQUAD_TARGET_POINT_LEFT = []

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
        if self.appear(MISSION_1_CONFIG, offset=10):
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
                and not self.appear(MISSION_1_ACCEPT, offset=10)
                and self.appear_then_click(MISSION_1_START, offset=10, interval=1)
            ):
                continue

            # 任务弹窗关闭
            if not self.appear(SURFACE_MISSION_CHECK, offset=10):
                logger.info(f'Mission {index} start')
                self.device.sleep(3)
                break

            # 接任务
            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear_then_click(
                MISSION_1_ACCEPT, offset=10, interval=1
            ):
                logger.info(f'Mission {index} accept')
                continue

    def squad_play(self, skip_first_screenshot=True):
        logger.info('Play squad')

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 任务完成
            if self.appear(MISSION_DONE, offset=10):
                return True

            # 打开队伍侧边栏
            if self.appear_then_click(SQUAD_EXPAND, offset=10, interval=1):
                continue

            # 选择队伍
            if self.appear(SQUAD_FOLD, offset=10):
                # 频率30秒
                self.device.sleep(30)
                for squad in 3:
                    # 地面中没有队伍数字
                    if not self.appear(SQUAD_1_IN_SURFACE, offset=10) and self.appear_then_click(
                        SQUAD_1, offset=10, interval=1
                    ):
                        squad_checker = Timer(2, count=5)
                        # 检查队伍是否选中
                        while 1:
                            self.device.screenshot()

                            if self.appear(SQUAD_CLOSE, offset=10):
                                if not squad_checker.started():
                                    squad_checker.start()
                                if squad_checker.reached():
                                    break
                            else:
                                squad_checker.clear()

                        # 点击目标点，根据队伍序号指定目标点
                        # 先点击REGHT第一个点查看是否是小怪，是则换LEFT

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
            if self.appear(SECTOR_1_SELECTED, offset=10) and self.appear_then_click(
                SECTOR_SELECT_CONFIRM, offset=10, interval=1
            ):
                continue

            # 回到任务面板
            if selected and self.appear(MISSION_1_CONFIG, offset=10):
                break

            # 打开区域选择弹窗
            if not selected and self.appear_then_click(MISSION_1_CONFIG, offset=10, interval=1):
                continue

    def check_squad(self, skip_first_screenshot=True):
        logger.info('Open squad list')

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
                break
            # 打开队伍侧边栏
            if self.appear_then_click(SQUAD_EXPAND, offset=10, interval=1):
                continue

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
