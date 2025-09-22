import os
from datetime import datetime
from functools import cached_property

from module.base.timer import Timer
from module.base.utils import point2str
from module.interception.assets import *
from module.logger import logger
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, PAUSE
from module.ui.assets import INTERCEPTION_CHECK
from module.ui.page import page_interception
from module.ui.ui import UI


class NoOpportunity(Exception):
    pass


class Interception(UI):
    @cached_property
    def teams(self):
        return [TEAM_1, TEAM_2, TEAM_3, TEAM_4, TEAM_5]

    def get_boss_button(self, boss: str):
        """
        根据选项名称获取对应的按钮
        示例：
          "Kraken" → KRAKEN
        """
        button_name = boss.upper()
        try:
            return globals()[button_name]
        except KeyError:
            logger.error(f"Button asset '{button_name}' not found for option '{boss}'")
            raise

    def _run(self, skip_first_screenshot=True):
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=10):
                break

            if click_timer.reached() and self.appear(
                self.get_boss_button(self.config.Interception_Boss), offset=10, interval=1
            ):
                logger.info('Click %s @ CHALLANGE' % point2str(360, 1030))
                self.device.click_minitouch(360, 1030)
                # self.device.sleep(1)
                click_timer.reset()
                continue

            if (
                self.appear(KRAKEN, offset=10)
                or self.appear(HARVESTER, offset=10)
                or self.appear(INDIVILIA, offset=10)
                or self.appear(MIRRORCONTAINER, offset=10)
                or self.appear(ULTRA, offset=10)
            ) and not self.appear(self.get_boss_button(self.config.Interception_Boss), offset=10):
                logger.info('Click %s @ SWITCH' % point2str(580, 960))
                self.device.click_minitouch(580, 960)
                self.device.sleep(0.5)
                click_timer.reset()
                continue

        self.device.click_record_clear()
        self.device.stuck_record_clear()
        self.device.sleep(0.5)

        end_fighting = False
        if self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=5) and not BATTLE.match_appear_on(self.device.image, 10):
            end_fighting = True
        # 使用的队伍
        teamindex = getattr(self.config, f'InterceptionTeam_{self.config.Interception_Boss}') - 1
        while 1:
            self.device.screenshot()

            # 切换队伍
            if (
                click_timer.reached()
                and self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=10)
                and self.appear_then_click(self.teams[teamindex], threshold=10, interval=1)
            ):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(BATTLE_QUICKLY, threshold=10):
                end_fighting = False
                self.device.sleep(1)
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(BATTLE, threshold=10, interval=1):
                end_fighting = False
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=(5, 5), threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=(5, 5), threshold=0.9, interval=5):
                click_timer.reset()
                continue

            # 红圈
            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                if self.handle_red_circles():
                    continue

            if click_timer.reached() and self.appear(END_FIGHTING, offset=30):
                saved_path = self.save_drop_image(self.device.image, self.config.Interception_DropScreenshotPath)
                if saved_path:
                    logger.info(f'Save drop image to: {saved_path}')

                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=30):
                        click_timer.reset()
                        break
                    if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                        click_timer.reset()
                        continue
                end_fighting = True
                click_timer.reset()
                continue

            if (
                end_fighting
                and self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=5)
                and not BATTLE.match_appear_on(self.device.image, 10)
            ):
                logger.info('There are no free opportunities')
                raise NoOpportunity

    def save_drop_image(self, image, base_path):
        """
        保存掉落截图到日期子文件夹，并按当天次数自动编号
        兼容 Linux/Windows
        Args:
            image: OpenCV 格式图片 (numpy.ndarray)
            base_path: 基础保存路径
        Returns:
            save_path: 保存的完整文件路径
        """
        if not base_path:
            return None

        # 按日期生成子文件夹
        today_str = datetime.now().strftime('%Y-%m-%d')
        date_dir = os.path.join(base_path, self.config.config_name, today_str)

        # 创建目录
        os.makedirs(date_dir, exist_ok=True)

        # 按当天已有数量生成编号
        existing_files = [f for f in os.listdir(date_dir) if f.lower().endswith('.png')]
        file_index = len(existing_files) + 1

        # 生成文件路径
        filename = f'drop_{file_index}.png'
        save_path = os.path.join(date_dir, filename)

        # 保存图片
        from module.base.utils import save_image

        save_image(image, save_path)
        return save_path

    def run(self):
        self.ui_ensure(page_interception)
        try:
            self._run()
        except NoOpportunity:
            pass
        self.config.task_delay(server_update=True)
