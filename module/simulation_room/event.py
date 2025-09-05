from module.base.timer import Timer
from module.base.utils import point2str
from module.exception import OperationFailed
from module.handler.assets import CONFIRM_B
from module.logger import logger
from module.simulation_room.assets import *
from module.tribe_tower.assets import OPERATION_FAILED
from module.ui.ui import UI


class EventBase(UI):
    def __init__(self, button, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.button = button

    def get_effect(self):
        for x in range(3):
            for i in [EPIC_CHECK, SSR_CHECK, SR_CHECK, R_CHECK]:
                if self.appear(i, offset=(10, 10), static=False):
                    return i.location
            self.device.screenshot()

    def get_effect_list(self):
        for x in range(3):
            _ = []
            for i in [EPIC_CHECK, SSR_CHECK, SR_CHECK, R_CHECK]:
                if self.appear(i, offset=(10, 10), static=False):
                    _.append(i.location)
            if len(_):
                _.sort(key=lambda x: x[1])
                return _
            self.device.screenshot()


class EnemyEvent(EventBase):
    def run(self, skip_first_screenshot=True):
        logger.hr('Start a hostile event', 3)
        click_timer = Timer(0.3)

        skip = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached():
                if not self.appear(TARGET_HP_CHECK, offset=(30, 30)) and (
                    self.appear(ENEMY_CHECK, offset=(30, 30), static=False)
                    or self.appear(BOSS_EVENT_CHECK, offset=(30, 30), static=False)
                    or self.appear(SPECIAL_EVENT_CHECK, offset=(30, 30), static=False)
                ):
                    self.device.click_minitouch(*self.button)
                    logger.info('Click %s @ %s' % (point2str(*self.button), 'ENEMY_EVENT'))
                    click_timer.reset()
                    continue
                elif self.appear(TARGET_HP_CHECK, offset=(30, 30), interval=5):
                    self.device.click_minitouch(360, 840)
                    logger.info('Click %s @ %s' % (point2str(360, 840), 'ENEMY_EVENT'))
                    click_timer.reset()
                    continue

            if click_timer.reached() and self.appear_then_click(
                FIGHT_QUICKLY, offset=(30, 30), interval=5, threshold=0.85
            ):
                skip = True
                click_timer.reset()
                continue

            elif not skip and click_timer.reached() and self.appear_then_click(FIGHT, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=(30, 30), threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=(30, 30), threshold=0.9, interval=5):
                click_timer.reset()
                continue

            # 红圈
            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                if self.handle_red_circles():
                    continue

            if click_timer.reached() and self.appear(END_FIGHTING, offset=30):
                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=30):
                        click_timer.reset()
                        break
                    if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                        click_timer.reset()
                        continue
                click_timer.reset()
                continue

            if self.appear(OPERATION_FAILED, offset=(30, 30)):
                raise OperationFailed

            if self.appear(END_SIMULATION, offset=(5, 5), static=False) or self.appear(
                SELECT_REWARD_EFFECT_CHECK, offset=(5, 5), static=False
            ):
                self.device.sleep(0.8)
                break

            if click_timer.reached() and self.appear(PAUSE, offset=(30, 30)):
                click_timer.reset()
                self.device.sleep(5)
                continue

        logger.info('The hostile event ended')


class HealingEvent(EventBase):
    def run(self, skip_first_screenshot=True):
        logger.hr('Start a healing event', 3)
        confirm_timer = Timer(3, count=3).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(
                HEALING_EVENT_CHECK, offset=(30, 30), interval=5, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                HEALING_OPTION_CHECK, offset=(30, 30), interval=2, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(CONFIRM_B, offset=(30, 30), interval=2, static=False):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if (
                self.appear(RESET_TIME_IN, offset=10) or self.appear(OVERCLOCK_RESET_TIME_IN, offset=10)
            ) and confirm_timer.reached():
                break

        logger.info('The healing event ended')


class RandomEvent(EventBase):
    def run(self, skip_first_screenshot=True):
        logger.hr('Start a random event', 3)
        confirm_timer = Timer(2, count=3).start()
        click_timer = Timer(0.3)
        click_timer_2 = Timer(5)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(RANDOM_EVENT_CHECK, offset=(30, 30), static=False):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(NOT_CHOOSE, offset=(30, 30), static=False):
                confirm_timer.reset()
                click_timer.reset()
                break

            if click_timer.reached() and self.appear_then_click(
                RANDOM_OPTION_CHECK, offset=(30, 30), interval=5, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(CONFIRM_B, offset=(30, 30), interval=3, static=False):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(RANDOM_EVENT_CHOOSE_EFFECT, offset=(30, 30), static=False):
                logger.hr('Choose an effect', 3)
                skip_first_screenshot = True
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if (
                        self.appear(RANDOM_EVENT_CHOOSE_EFFECT, offset=(30, 30), static=False)
                        and click_timer_2.reached()
                    ):
                        button = self.get_effect()
                        confirm_timer.reset()
                        click_timer_2.reset()
                        self.device.click_minitouch(*button)
                        logger.info('Click %s @ %s' % (point2str(*button), 'EFFECT'))
                        self.device.sleep(0.6)

                    if click_timer.reached() and self.appear_then_click(
                        CONFIRM_B, offset=(30, 30), interval=2, static=False
                    ):
                        confirm_timer.reset()
                        click_timer.reset()
                        continue

                    if (
                        (
                            self.appear(RESET_TIME_IN, offset=(30, 30), static=False)
                            or self.appear(OVERCLOCK_RESET_TIME_IN, offset=(30, 30), static=False)
                        )
                        and (
                            self.appear(SIMULATION_CHECK, offset=(30, 30), static=False)
                            or self.appear(OVERCLOCK_SIMULATION_CHECK, offset=(30, 30), static=False)
                        )
                        and confirm_timer.reached()
                    ):
                        return

            if (
                self.appear(RANDOM_EVENT_REWARD_EFFECT, offset=(30, 30), static=False)
                or self.appear(MAX_EFFECT_COUNT_CHECK, offset=(30, 30), static=False)
                or self.appear(REPEATED_EFFECT_CHECK, offset=(30, 30), static=False)
            ):
                logger.hr('reward an effect', 3)
                skip_first_screenshot = True
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if self.appear(MAX_EFFECT_COUNT_CHECK, offset=(5, 5), static=False) or self.appear(
                        REPEATED_EFFECT_CHECK, offset=(5, 5), static=False
                    ):
                        if click_timer_2.reached():
                            button = self.get_effect_list()[-1]
                            confirm_timer.reset()
                            click_timer_2.reset()
                            self.device.click_minitouch(*button)
                            logger.info('Click %s @ %s' % (point2str(*button), 'EFFECT'))

                    if click_timer.reached() and self.appear_then_click(
                        CONFIRM_B, offset=(30, 30), interval=1, static=False
                    ):
                        confirm_timer.reset()
                        click_timer.reset()
                        continue

                    if (
                        (
                            self.appear(RESET_TIME_IN, offset=(30, 30), static=False)
                            or self.appear(OVERCLOCK_RESET_TIME_IN, offset=(30, 30), static=False)
                        )
                        and (
                            self.appear(SIMULATION_CHECK, offset=(30, 30), static=False)
                            or self.appear(OVERCLOCK_SIMULATION_CHECK, offset=(30, 30), static=False)
                        )
                        and confirm_timer.reached()
                    ):
                        return

            if (
                (
                    self.appear(RESET_TIME_IN, offset=(30, 30), static=False)
                    or self.appear(OVERCLOCK_RESET_TIME_IN, offset=(30, 30), static=False)
                )
                and (
                    self.appear(SIMULATION_CHECK, offset=(30, 30), static=False)
                    or self.appear(OVERCLOCK_SIMULATION_CHECK, offset=(30, 30), static=False)
                )
                and confirm_timer.reached()
            ):
                return

        if self.appear(NOT_CHOOSE, offset=(30, 30), static=False):
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer.reached() and self.appear_then_click(
                    NOT_CHOOSE, offset=(30, 30), interval=5, static=False
                ):
                    self.device.sleep(0.8)
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear(NOTHING, offset=(30, 30), interval=5, static=False):
                    self.device.click_minitouch(530, 800)
                    logger.info('Click %s @ %s' % (point2str(530, 800), 'SKIP'))
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(
                    CONFIRM_B, offset=(30, 30), interval=5, static=False
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if (
                    (
                        self.appear(RESET_TIME_IN, offset=(30, 30), static=False)
                        or self.appear(OVERCLOCK_RESET_TIME_IN, offset=(30, 30), static=False)
                    )
                    and (
                        self.appear(SIMULATION_CHECK, offset=(30, 30), static=False)
                        or self.appear(OVERCLOCK_SIMULATION_CHECK, offset=(30, 30), static=False)
                    )
                    and confirm_timer.reached()
                ):
                    return


class ImprovementEvent(EventBase):
    def run(self, skip_first_screenshot=True):
        logger.hr('Start an improvement event', 3)
        confirm_timer = Timer(2, count=3).start()
        click_timer = Timer(0.3)
        click_timer_2 = Timer(5)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(
                IMPROVEMENT_EVENT_CHECK, offset=(30, 30), interval=5, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(NOT_CHOOSE, offset=(30, 30), static=False):
                confirm_timer.reset()
                click_timer.reset()
                break

            if click_timer.reached() and self.appear_then_click(
                IMPROVEMENT_OPTION_CHECK, offset=(30, 30), interval=5, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(CONFIRM_B, offset=(30, 30), interval=3, static=False):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(RANDOM_EVENT_CHOOSE_EFFECT, offset=(30, 30), static=False):
                logger.hr('Choose an effect', 3)
                skip_first_screenshot = True
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if (
                        self.appear(RANDOM_EVENT_CHOOSE_EFFECT, offset=(30, 30), static=False)
                        and click_timer_2.reached()
                    ):
                        button = self.get_effect()
                        confirm_timer.reset()
                        click_timer_2.reset()
                        self.device.click_minitouch(*button)
                        logger.info('Click %s @ %s' % (point2str(*button), 'EFFECT'))
                        self.device.sleep(0.6)

                    if click_timer.reached() and self.appear_then_click(
                        CONFIRM_B, offset=(30, 30), interval=2, static=False
                    ):
                        confirm_timer.reset()
                        click_timer.reset()
                        continue

                    if (
                        (
                            self.appear(RESET_TIME_IN, offset=(30, 30), static=False)
                            or self.appear(OVERCLOCK_RESET_TIME_IN, offset=(30, 30), static=False)
                        )
                        and (
                            self.appear(SIMULATION_CHECK, offset=(30, 30), static=False)
                            or self.appear(OVERCLOCK_SIMULATION_CHECK, offset=(30, 30), static=False)
                        )
                        and confirm_timer.reached()
                    ):
                        return

            if (
                (
                    self.appear(RESET_TIME_IN, offset=(30, 30), static=False)
                    or self.appear(OVERCLOCK_RESET_TIME_IN, offset=(30, 30), static=False)
                )
                and (
                    self.appear(SIMULATION_CHECK, offset=(30, 30), static=False)
                    or self.appear(OVERCLOCK_SIMULATION_CHECK, offset=(30, 30), static=False)
                )
                and confirm_timer.reached()
            ):
                return

        if self.appear(NOT_CHOOSE, offset=(30, 30), static=False):
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer.reached() and self.appear_then_click(
                    NOT_CHOOSE, offset=(30, 30), interval=5, static=False
                ):
                    self.device.sleep(0.8)
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear(NOTHING, offset=(30, 30), interval=5, static=False):
                    self.device.click_minitouch(530, 800)
                    logger.info('Click %s @ %s' % (point2str(530, 800), 'SKIP'))
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(
                    CONFIRM_B, offset=(30, 30), interval=5, static=False
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if (
                    (
                        self.appear(RESET_TIME_IN, offset=(30, 30), static=False)
                        or self.appear(OVERCLOCK_RESET_TIME_IN, offset=(30, 30), static=False)
                    )
                    and (
                        self.appear(SIMULATION_CHECK, offset=(30, 30), static=False)
                        or self.appear(OVERCLOCK_SIMULATION_CHECK, offset=(30, 30), static=False)
                    )
                    and confirm_timer.reached()
                ):
                    return
