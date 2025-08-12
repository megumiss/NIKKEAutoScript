from functools import cached_property

from module.base.timer import Timer
from module.base.utils import _area_offset, crop, point2str
from module.exception import GamePageUnknownError, GameStuckError, OperationFailed
from module.handler.assets import CONFIRM_B
from module.logger import logger
from module.simulation_room.assets import *
from module.tribe_tower.assets import BACK
from module.ui.assets import ARK_GOTO_SIMULATION_ROOM, GOTO_BACK, SIMULATION_ROOM_CHECK
from module.ui.page import page_ark
from module.ui.ui import UI


class Overclock(UI):
    def get_next_event(self):
        for i in ENEMY_EVENT_CHECK.match_several(self.device.image, offset=5, threshold=0.95, static=False)[:3]:
            area = _area_offset(i.get('area'), (-45, -100, -14, -90))
            img = crop(self.device.image, area)
            if NORMAL_CHECK.match(img, threshold=0.75, static=False):
                NORMAL_CHECK._button_offset = area
            elif HARD_CHECK.match(img, threshold=0.75, static=False):
                HARD_CHECK._button_offset = area

        if NORMAL_CHECK._button_offset:
            from module.simulation_room.event import EnemyEvent

            EnemyEvent(button=NORMAL_CHECK.location, config=self.config, device=self.device).run()
            NORMAL_CHECK._button_offset = None
            return

        if self.appear(HEALING_EVENT_CHECK, offset=(30, 30), static=False):
            from module.simulation_room.event import HealingEvent

            HealingEvent(button=NORMAL_CHECK.location, config=self.config, device=self.device).run()
            return

        if self.appear(IMPROVEMENT_EVENT_CHECK, offset=(30, 30), static=False):
            from module.simulation_room.event import ImprovementEvent

            ImprovementEvent(
                button=IMPROVEMENT_EVENT_CHECK.location,
                config=self.config,
                device=self.device,
            ).run()
            return

        if self.appear(RANDOM_EVENT_CHECK, offset=(30, 30), static=False):
            from module.simulation_room.event import RandomEvent

            RandomEvent(
                button=RANDOM_EVENT_CHECK.location,
                config=self.config,
                device=self.device,
            ).run()
            return

        if self.appear(BOSS_EVENT_CHECK, offset=(30, 30), static=False):
            from module.simulation_room.event import EnemyEvent

            logger.hr('Start the boss event', 2)
            EnemyEvent(button=BOSS_EVENT_CHECK.location, config=self.config, device=self.device).run()
            return

        if HARD_CHECK._button_offset:
            from module.simulation_room.event import EnemyEvent

            EnemyEvent(button=HARD_CHECK.location, config=self.config, device=self.device).run()
            HARD_CHECK._button_offset = None
            return

    def get_effect(self):
        for x in range(3):
            for i in [EPIC_CHECK, SSR_CHECK, SR_CHECK, R_CHECK]:
                if self.appear(i, offset=(10, 10), static=False):
                    return i.location
            self.device.screenshot()

    def choose_effect(self, skip_first_screenshot=True):
        logger.hr('Choose an effect', 3)
        confirm_timer = Timer(2, count=2).start()
        click_timer = Timer(0.3)
        click_timer_2 = Timer(6)

        if not self.appear(MAX_EFFECT_COUNT, offset=(10, 10), static=False, threshold=0.96):
            button = self.get_effect()
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer_2.reached():
                    confirm_timer.reset()
                    click_timer_2.reset()
                    self.device.click_minitouch(*button)
                    logger.info('Click %s @ %s' % (point2str(*button), 'EFFECT'))
                    self.device.sleep(0.6)

                if click_timer.reached() and self.appear_then_click(
                    CONFIRM_B, offset=(30, 30), interval=6, static=False
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if (
                    not self.appear(SELECT_REWARD_EFFECT_CHECK, offset=(30, 30), static=False)
                    and confirm_timer.reached()
                ):
                    break
        else:
            logger.warning('The own effect count has already reached its limit')
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer.reached() and self.appear_then_click(
                    NOT_CHOOSE, offset=(30, 30), interval=5, static=False
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear(SKIP_CHECK, offset=(30, 30), interval=5, static=False):
                    self.device.click_minitouch(530, 800)
                    logger.info('Click %s @ %s' % (point2str(530, 800), 'SKIP'))
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if (
                    not self.appear(SELECT_REWARD_EFFECT_CHECK, offset=(30, 30), static=False)
                    and confirm_timer.reached()
                ):
                    return

        if self.appear(REPEATED_EFFECT_CHECK, offset=(5, 5), static=False):
            logger.warning('The selected effect has been in the own effect list')
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer.reached() and self.appear_then_click(CANCEL, offset=(30, 30), interval=5, static=False):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(
                    NOT_CHOOSE, offset=(30, 30), interval=5, static=False
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear(SKIP_CHECK, offset=(30, 30), interval=5, static=False):
                    self.device.click_minitouch(530, 800)
                    logger.info('Click %s @ %s' % (point2str(530, 800), 'SKIP'))
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if (
                    not self.appear(REPEATED_EFFECT_CHECK, offset=(30, 30), static=False)
                    and not self.appear(SELECT_REWARD_EFFECT_CHECK, offset=(30, 30), static=False)
                    and confirm_timer.reached()
                ):
                    return
        elif self.appear(MAX_EFFECT_COUNT_CHECK, offset=(5, 5), static=False):
            logger.warning('The own effect count has already reached its limit')
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer.reached() and self.appear_then_click(CANCEL, offset=(30, 30), interval=5, static=False):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(
                    NOT_CHOOSE, offset=(30, 30), interval=5, static=False
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear(SKIP_CHECK, offset=(30, 30), interval=5, static=False):
                    self.device.click_minitouch(530, 800)
                    logger.info('Click %s @ %s' % (point2str(530, 800), 'SKIP'))
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if (
                    not self.appear(MAX_EFFECT_COUNT_CHECK, offset=(30, 30), static=False)
                    and not self.appear(SELECT_REWARD_EFFECT_CHECK, offset=(30, 30), static=False)
                    and confirm_timer.reached()
                ):
                    return

    def handle_failed(self, skip_first_screenshot=True):
        timeout = Timer(10).start()
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(BACK, offset=(5, 5), interval=5):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if timeout.reached():
                raise GameStuckError

            if self.appear(GOTO_BACK, offset=(30, 30)):
                return

            elif self.appear(RESET_TIME_IN, offset=(30, 30), interval=2):
                self.device.click_minitouch(50, 200)
                continue

    def ensure_into_simulation(self, skip_first_screenshot=True):
        logger.info('Open simulation room')
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 模拟室主页
            if self.appear(SIMULATION_ROOM_CHECK, offset=(30, 30)):
                break

            if click_timer.reached() and self.appear_then_click(ARK_GOTO_SIMULATION_ROOM, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

            if self.appear(SIMULATION_CHECK, offset=(30, 30)) or self.appear(RESET_TIME_IN, offset=(30, 30)):
                raise GamePageUnknownError

        click_timer.reset()
        while 1:
            self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(START_SIMULATION, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(START_SIMULATION_CONFIRM, offset=(30, 30), interval=2):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(START_SIMULATION_CONFIRM, offset=(5, 5), static=False):
                click_timer.reset()
                continue

            if self.appear(SELECT_REWARD_EFFECT_CHECK, offset=(30, 30), interval=5, static=False):
                break

            if self.appear(SIMULATION_CHECK, offset=(30, 30), threshold=0.86):
                logger.hr(f'Area {self.region_label.get(self.current_region)}', 2)
                break

    def ensure_into_next_region(self, skip_first_screenshot=True):
        self.current_region += 1
        logger.hr(f'Area {self.region_label.get(self.current_region)}', 2)

        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(GOTO_NEXT_REGION, offset=(30, 30), interval=2):
                click_timer.reset()
                continue

            if not self.appear(END_SIMULATION, offset=(5, 5)) and confirm_timer.reached():
                break

    def end_simulation(self, skip_first_screenshot=True):
        logger.info('already arrived the end area')
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(
                END_SIMULATION, offset=(30, 30), interval=3, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(END_SIMULATION_CHECK, offset=(30, 30), static=False):
                self.device.click_minitouch(520, 800)
                logger.info('Click %s @ %s' % (point2str(520, 800), 'END_SIMULATION_CONFIRM'))
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(CHOOSE_INITIAL_EFFECT_CHECK, offset=(10, 10), static=False):
                if self.appear_then_click(NOT_CHOOSE_INITIAL_EFFECT, offset=(10, 10), interval=3, static=False):
                    self.appear_then_click(CONFIRM_B, offset=(30, 30), interval=1, static=False)
                    click_timer.reset()
                    continue

            if click_timer.reached() and self.appear(SKIP_CHECK, offset=(30, 30), interval=5, static=False):
                self.device.click_minitouch(530, 800)
                logger.info('Click %s @ %s' % (point2str(530, 800), 'SKIP'))
                click_timer.reset()
                continue

            if self.appear(SIMULATION_ROOM_CHECK, offset=(5, 5)):
                break

    def _run(self):
        while 1:
            self.get_next_event()
            if self.appear(SELECT_REWARD_EFFECT_CHECK, offset=(5, 5), interval=5, static=False):
                self.choose_effect()
            if self.appear(END_SIMULATION, offset=(5, 5), interval=5, static=False):
                if self.current_region != self.region.get(self.ending_area):
                    self.ensure_into_next_region()
                else:
                    self.end_simulation()
                    return

    def run(self):
        try:
            if not self.appear(SIMULATION_ROOM_CHECK, offset=(30, 30)):
                self.ui_ensure(page_ark)
            self.ensure_into_simulation()
            self._run()
        except GamePageUnknownError:
            logger.error('The simulation has already been started')
            logger.critical('Please end the current simulation and restart it')
            self.handle_failed()
        except OperationFailed:
            logger.warning('failed to overcome the current battle, will skip simulation task')
            self.handle_failed()
        self.config.task_delay(server_update=True)
