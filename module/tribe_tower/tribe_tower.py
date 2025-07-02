from functools import cached_property

from module.base.timer import Timer
from module.base.utils import point2str
from module.exception import OperationFailed
from module.logger import logger
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, PAUSE
from module.tribe_tower.assets import *
from module.ui.assets import GOTO_BACK, MAIN_CHECK, TRIBE_TOWER_CHECK
from module.ui.page import page_tribe_tower
from module.ui.ui import UI


class NoOpportunityRemain(Exception):
    pass


class NoAvailableCompany(Exception):
    pass


class TribeTower(UI):
    opportunity = None
    company = []

    @property
    def _opportunity(self) -> int:
        for x in range(3):
            for index, i in enumerate([OPPORTUNITY_0, OPPORTUNITY_1, OPPORTUNITY_2, OPPORTUNITY_3]):
                if self.appear(i, offset=(5, 5), threshold=0.96, static=False):
                    self.opportunity = index
                    return self.opportunity
            self.device.screenshot()
        raise NoOpportunityRemain

    @cached_property
    def available_company(self) -> list[dict]:
        for x in range(3):
            for index, i in enumerate([ELYSION_CHECK, MISSILIS_CHECK, TETRA_CHECK, PILGRIM_CHECK]):
                if self.appear(i, offset=5, threshold=0.89, static=False):
                    self.company.append({'name': i.name.split('_')[0], 'button': i.location})
            if len(self.company):
                return self.company
            self.device.screenshot()
        raise NoAvailableCompany

    def _run(self):
        while len(self.available_company):
            try:
                self.ensure_into_tribe_tower()
            except NoOpportunityRemain:
                logger.warning('The current tribe tower has no remaining opportunities')
                self.ensure_back()
                self.available_company.remove(self.available_company[0])
                continue

        if not self.config.Overcome_OnlyToCompleteDailyMission:
            logger.warning('All the available companies have tried')

    def ensure_into_tribe_tower(self, skip_first_screenshot=True):
        logger.hr(f'{self.available_company[0].get("name")} TOWER', 2)
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.handle_paid_gift():
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(TRIBE_TOWER_CHECK, offset=(30, 30), interval=5):
                button = self.available_company[0].get('button')
                self.device.click_minitouch(*button)
                logger.info('Click %s @ %s' % (point2str(*button), 'TOWER'))
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(TRIBE_TOWER_DETAILED_CHECK, offset=(30, 30)) and confirm_timer.reached():
                break
        logger.attr('Opportunity', self._opportunity)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.handle_paid_gift():
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(TRIBE_TOWER_DETAILED_CHECK, offset=(30, 30), interval=5):
                self.device.click_minitouch(360, 560)
                logger.info('Click %s @ %s' % (point2str(360, 560), 'STAGE'))
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(STAGE_INFO_CHECK, offset=(30, 30), static=False) and confirm_timer.reached():
                break
        self.try_to_overcome_current_stage()

    def try_to_overcome_current_stage(self, skip_first_screenshot=True):
        logger.hr(f'OVERCOME STAGE', 3)
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        try:
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer.reached() and self.handle_paid_gift():
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear(FIGHT, offset=5, interval=5):
                    if FIGHT.match_appear_on(self.device.image, 10):
                        self.device.click(FIGHT)
                        confirm_timer.reset()
                        click_timer.reset()
                        continue

                    raise NoOpportunityRemain

                if click_timer.reached() and self.appear_then_click(
                    AUTO_SHOOT, offset=(30, 30), interval=5, threshold=0.8
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(
                    AUTO_BURST, offset=(30, 30), interval=5, threshold=0.8
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                # 红圈
                if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                    if self.handle_red_circles():
                        continue

                if click_timer.reached() and self.appear_then_click(NEXT_STAGE, offset=(30, 30)):
                    self.device.sleep(5)
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if (
                    click_timer.reached()
                    and not self.appear(NEXT_STAGE, offset=(30, 30))
                    and self.appear_then_click(END_CHECK, offset=(30, 30))
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if self.appear(OPERATION_FAILED, offset=(30, 30)):
                    raise OperationFailed

                if click_timer.reached() and self.appear(PAUSE, offset=(30, 30)):
                    if self.config.Overcome_OnlyToCompleteDailyMission:
                        self.ensure_failed()
                    confirm_timer.reset()
                    click_timer.reset()
                    self.device.sleep(5)
                    continue

                if (
                    self.appear(TRIBE_TOWER_DETAILED_CHECK, offset=(30, 30), interval=6)
                    and self.appear(GOTO_BACK, offset=(30, 30))
                    and confirm_timer.reached()
                ):
                    raise NoOpportunityRemain

        except OperationFailed:
            if not self.config.Overcome_OnlyToCompleteDailyMission:
                logger.warning('failed to overcome the current stage, will try the other tribe tower')
                self.available_company.remove(self.available_company[0])
            else:
                self.available_company.clear()
            self.ensure_back()
            return

    def ensure_back(self, skip_first_screenshot=True):
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.handle_paid_gift():
                confirm_timer.reset()
                click_timer.reset()
                continue

            if (
                self.appear(TRIBE_TOWER_CHECK, offset=5) or self.appear(MAIN_CHECK, offset=5, static=False)
            ) and confirm_timer.reached():
                return

            if click_timer.reached() and self.appear_then_click(BACK, offset=(5, 5), interval=6):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(GOTO_BACK, offset=(30, 30), interval=6):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(FIGHT, offset=5):
                self.device.click_minitouch(1, 1)
                confirm_timer.reset()
                click_timer.reset()
                continue

    def ensure_failed(self, skip_first_screenshot=True):
        logger.info("abandon the current attempt to overcome it, because it's set up this way")
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(PAUSE, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(ABANDON, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

            if self.appear(OPERATION_FAILED, offset=(30, 30)):
                raise OperationFailed

    def run(self):
        self.ui_ensure(page_tribe_tower)
        self._run()
        self.config.task_delay(server_update=True)
