import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from functools import cached_property

import inflection

from module.base.decorator import del_cached_property
from module.config.config import NikkeConfig, TaskEnd
from module.config.utils import deep_get, deep_set
from module.exception import (
    AccountError,
    GameNotRunningError,
    GamePageUnknownError,
    GameServerUnderMaintenance,
    GameStart,
    GameStuckError,
    GameTooManyClickError,
    RequestHumanTakeover,
    ScreenshotError,
)
from module.logger import logger
from module.notify import handle_notify

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')


class NikkeAutoScript:
    stop_event: threading.Event = None

    def __init__(self, config_name='nkas'):
        logger.hr('Start', level=0)

        # 路径检查
        script_path = os.path.abspath(sys.argv[0])
        logger.info(f'[Script Path]: {script_path}')
        try:
            script_path.encode('ascii')
        except UnicodeEncodeError:
            logger.error('脚本路径包含非英文字符，请切换到英文路径下')
            logger.error('Script path contains non-ASCII characters. Please move the script to an English-only path.')
            sys.exit(1)

        self.config_name = config_name
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}

    @cached_property
    def config(self):
        try:
            config = NikkeConfig(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self):
        try:
            if self.config.Client_Platform == 'win':
                from module.device.win.device import Device

                device = Device(config=self.config)
            if self.config.Client_Platform == 'adb':
                from module.device.adb.device import Device

                device = Device(config=self.config)

            return device
        except RequestHumanTakeover:
            # 设置屏幕方向
            if self.config.Client_Platform == 'win' and self.config.PCClient_ScreenRotate:
                self.device.screen_rotate()
            logger.critical('Request human takeover')
            exit(1)
        except AccountError:
            # 设置屏幕方向
            if self.config.Client_Platform == 'win' and self.config.PCClient_ScreenRotate:
                self.device.screen_rotate()
            logger.critical('Account or password setting error')
            exit(1)
        except Exception as e:
            # 设置屏幕方向
            if self.config.Client_Platform == 'win' and self.config.PCClient_ScreenRotate:
                self.device.screen_rotate()
            logger.exception(e)
            exit(1)

    def run(self, command, skip_first_screenshot=False):
        try:
            # 妮游社任务不需要device
            if command not in self.config.INDEPENDENT_TASKS_UNDER and not skip_first_screenshot:
                self.device.screenshot()
            self.__getattribute__(command)()
            return True
        except TaskEnd:
            return True
        except GameStart:
            self.start()
            return True
        except GameNotRunningError as e:
            logger.warning(e)
            self.config.task_call('Restart')
            return False
        except (GameStuckError, GameTooManyClickError, ScreenshotError) as e:
            logger.error(e)
            self.save_error_log()
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop NKAS')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GamePageUnknownError:
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            # self.device.app_stop()
            logger.critical('Game page unknown')
            self.save_error_log()
            if self.config.Notification_WhenDailyTaskCrashed:
                handle_notify(
                    self.config.Notification_OnePushConfig,
                    title=f'NKAS <{self.config_name}> crashed',
                    content=f'<{self.config_name}> GamePageUnknownError',
                )
            # 设置屏幕方向
            if self.config.PCClient_ScreenRotate:
                self.device.screen_rotate()
            exit(1)
        except GameServerUnderMaintenance as e:
            logger.error(e)
            self.device.app_stop()
            if self.config.Notification_WhenDailyTaskCrashed:
                handle_notify(
                    self.config.Notification_OnePushConfig,
                    title=f'NKAS <{self.config_name}> crashed',
                    content=f'<{self.config_name}> GameServerUnderMaintenance',
                )
            # 设置屏幕方向
            if self.config.PCClient_ScreenRotate:
                self.device.screen_rotate()
            exit(1)
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            if self.config.Notification_WhenDailyTaskCrashed:
                handle_notify(
                    self.config.Notification_OnePushConfig,
                    title=f'NKAS <{self.config_name}> crashed',
                    content=f'<{self.config_name}> RequestHumanTakeover',
                )
            # 设置屏幕方向
            if self.config.PCClient_ScreenRotate:
                self.device.screen_rotate()
            exit(1)
        except Exception as e:
            logger.exception(e)
            self.save_error_log()
            if self.config.Notification_WhenDailyTaskCrashed:
                handle_notify(
                    self.config.Notification_OnePushConfig,
                    title=f'NKAS <{self.config_name}> crashed',
                    content=f'<{self.config_name}> Exception occured',
                )
            # 设置屏幕方向
            if self.config.PCClient_ScreenRotate:
                self.device.screen_rotate()
            exit(1)

    def save_error_log(self):
        """
        Save last 60 screenshots in ./log/error/<timestamp>
        Save logs to ./log/error/<timestamp>/log.txt
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import handle_sensitive_logs

        if not os.path.exists('./log/error'):
            os.mkdir('./log/error')
        folder = f'./log/error/{int(time.time() * 1000)}'
        logger.warning(f'Saving error: {folder}')
        os.mkdir(folder)
        for data in self.device.screenshot_deque:
            image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
            # 遮挡个人消息
            # image = handle_sensitive_image(data['image'])
            image = data['image']
            save_image(image, f'{folder}/{image_time}.png')
        with open(logger.log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            start = 0
            for index, line in enumerate(lines):
                line = line.strip(' \r\t\n')
                # 从最后一个任务截取
                if re.match('^═{15,}$', line):
                    start = index
            lines = lines[start - 2 :]
            # 替换真实路径
            lines = handle_sensitive_logs(lines)
        with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
            f.writelines(lines)

    def restart(self):
        from module.handler.login import LoginHandler

        LoginHandler(self.config, device=self.device).app_restart()

    def start(self):
        from module.handler.login import LoginHandler

        LoginHandler(self.config, device=self.device).app_start()

    def goto_main(self):
        from module.handler.login import LoginHandler
        from module.ui.ui import UI

        if self.device.app_is_running():
            logger.info('App is already running, goto main page')
            UI(self.config, device=self.device).ui_goto_main()
        else:
            logger.info('App is not running, start app and goto main page')
            LoginHandler(self.config, device=self.device).app_start()
            UI(self.config, device=self.device).ui_goto_main()

    def reward(self):
        from module.reward.reward import Reward

        Reward(config=self.config, device=self.device).run()

    def daily_recruit(self):
        from module.daily_recruit.daily_recruit import DailyRecruit

        DailyRecruit(config=self.config, device=self.device).run()

    def destruction(self):
        from module.destruction.destruction import Destruction

        Destruction(config=self.config, device=self.device).run()

    def commission(self):
        from module.commission.commission import Commission

        Commission(config=self.config, device=self.device).run()

    def conversation(self):
        from module.conversation.conversation import Conversation

        Conversation(config=self.config, device=self.device).run()

    def rookie_arena(self):
        from module.rookie_arena.rookie_arena import RookieArena

        RookieArena(config=self.config, device=self.device).run()

    def special_arena(self):
        from module.special_arena.special_arena import SpecialArena

        SpecialArena(config=self.config, device=self.device).run()

    def champion_arena(self):
        from module.champion_arena.champion_arena import ChampionArena

        ChampionArena(config=self.config, device=self.device).run()

    def simulation_room(self):
        from module.simulation_room.simulation_room import SimulationRoom

        SimulationRoom(config=self.config, device=self.device).run()

    def overclock(self):
        from module.simulation_room.overclock import Overclock

        Overclock(config=self.config, device=self.device).run()

    def tribe_tower(self):
        from module.tribe_tower.tribe_tower import TribeTower

        TribeTower(config=self.config, device=self.device).run()

    def shop(self):
        from module.shop.shop import Shop

        Shop(config=self.config, device=self.device).run()

    def rubbish_shop(self):
        from module.rubbish_shop.rubbish_shop import RubbishShop

        RubbishShop(config=self.config, device=self.device).run()

    def daily(self):
        from module.daily.daily import Daily

        Daily(config=self.config, device=self.device).run()

    def mission_pass(self):
        from module.mission_pass.mission_pass import MissionPass

        MissionPass(config=self.config, device=self.device).run()

    def liberation(self):
        from module.liberation.liberation import Liberation

        Liberation(config=self.config, device=self.device).run()

    def step_up_gift(self):
        from module.gift.gift import StepUpGift

        StepUpGift(config=self.config, device=self.device).run()

    def daily_gift(self):
        from module.gift.gift import DailyGift

        DailyGift(config=self.config, device=self.device).run()

    def weekly_gift(self):
        from module.gift.gift import WeeklyGift

        WeeklyGift(config=self.config, device=self.device).run()

    def monthly_gift(self):
        from module.gift.gift import MonthlyGift

        MonthlyGift(config=self.config, device=self.device).run()

    def mailbox(self):
        from module.mailbox.mailbox import Mailbox

        Mailbox(config=self.config, device=self.device).run()

    def interception(self):
        from module.interception.interception import Interception

        Interception(config=self.config, device=self.device).run()

    def bla_daily(self):
        from module.blablalink.blablalink import Blablalink

        Blablalink(config=self.config).run('daily')

    def bla_cdk(self):
        from module.blablalink.blablalink import Blablalink

        Blablalink(config=self.config).run('cdk')

    def bla_cdk_manual(self):
        from module.blablalink.blablalink import Blablalink

        Blablalink(config=self.config).cdk_manual()

    def bla_exchange(self):
        from module.blablalink.blablalink import Blablalink

        Blablalink(config=self.config).run('exchange')

    def auto_tower(self):
        from module.daemon.auto_tower import AutoTower

        AutoTower(config=self.config, device=self.device).run()

    def highlights(self):
        from module.daemon.highlights import Highlights

        Highlights(config=self.config, device=self.device, task='Highlights').run()

    def semi_combat(self):
        from module.daemon.semi_combat import SemiCombat

        SemiCombat(config=self.config, device=self.device, task='SemiCombat').run()

    def screen_rotate(self):
        from module.daemon.screen_rotate import ScreenRotate

        ScreenRotate(config=self.config).run()

    def event(self):
        from module.event.event import Event

        Event(config=self.config, device=self.device).run()

    def event2(self):
        from module.event.event import Event

        Event(config=self.config, device=self.device).run()

    def coop(self):
        from module.coop.coop import Coop

        Coop(config=self.config, device=self.device).run()

    def solo_raid(self):
        from module.solo_raid.solo_raid import SoloRaid

        SoloRaid(config=self.config, device=self.device).run()

    def union_raid(self):
        from module.union_raid.union_raid import UnionRaid

        UnionRaid(config=self.config, device=self.device).run()

    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            if datetime.now() > future:
                return True
            if self.stop_event is not None:
                if self.stop_event.is_set():
                    logger.info('Update event detected')
                    logger.info(f'[{self.config_name}] exited. Reason: Update')
                    exit(0)

            time.sleep(5)

            if self.config.should_reload():
                return False

    def get_next_task(self):
        """
        Returns:
            str: Name of the next task.
        """
        while 1:
            task = self.config.get_next()
            self.config.task = task
            self.config.bind(task)

            from module.base.resource import release_resources

            if self.config.task.command != 'NKAS':
                release_resources(next_task=task.command)

            if task.next_run > datetime.now():
                logger.info(f'Wait until {task.next_run} for task `{task.command}`')
                self.is_first_task = False
                method = self.config.Optimization_WhenTaskQueueEmpty
                if method == 'close_game':
                    logger.info('Close game during wait')
                    # 只运行妮游社任务时不会初始化device，不需要操作游戏
                    if 'device' in self.__dict__:
                        # 关闭游戏
                        self.device.app_stop()
                        self.device.sleep(1)
                        # 关闭启动器
                        if self.config.Client_Platform == 'win':
                            self.device.app_stop('Launcher')
                    release_resources()
                    if self.config.Client_Platform == 'win':
                        # 设置屏幕方向
                        if self.config.PCClient_ScreenRotate:
                            self.device.screen_rotate()
                        del_cached_property(self, 'device')
                    # self.device.release_during_wait()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
                    if task.command != 'Restart':
                        self.config.task_call('Restart')
                        del_cached_property(self, 'config')
                        continue
                elif method == 'goto_main':
                    logger.info('Goto main page during wait')
                    # 只运行妮游社任务时不会初始化device，不需要操作游戏
                    if 'device' in self.__dict__:
                        self.run('goto_main')
                    release_resources()
                    # self.device.release_during_wait()
                    # 设置屏幕方向
                    if self.config.Client_Platform == 'win' and self.config.PCClient_ScreenRotate:
                        self.device.screen_rotate()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
                elif method == 'stay_there':
                    logger.info('Stay there during wait')
                    release_resources()
                    # self.device.release_during_wait()
                    # 设置屏幕方向
                    if self.config.Client_Platform == 'win' and self.config.PCClient_ScreenRotate:
                        self.device.screen_rotate()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
                else:
                    logger.warning(f'Invalid Optimization_WhenTaskQueueEmpty: {method}, fallback to stay_there')
                    release_resources()
                    # self.device.release_during_wait()
                    # 设置屏幕方向
                    if self.config.Client_Platform == 'win' and self.config.PCClient_ScreenRotate:
                        self.device.screen_rotate()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
            break

        return task.command

    def loop(self):
        logger.set_file_logger(self.config_name)
        logger.info(f'Start scheduler loop: {self.config_name}')

        while 1:
            # Check update event from GUI
            if self.stop_event is not None:
                if self.stop_event.is_set():
                    logger.info('Update event detected')
                    logger.info(f'NKAS [{self.config_name}] exited.')
                    break
            # Check game server maintenance
            # self.checker.wait_until_available()
            # if self.checker.is_recovered():
            #     # There is an accidental bug hard to reproduce
            #     # Sometimes, config won't be updated due to blocking
            #     # even though it has been changed
            #     # So update it once recovered
            #     del_cached_property(self, 'config')
            #     logger.info('Server or network is recovered. Restart game client')
            #     self.config.task_call('Restart')
            # Get task
            task = self.get_next_task()
            # 妮游社任务不需要device，不需要操作游戏
            if task not in self.config.INDEPENDENT_TASKS:
                # Init device and change server
                _ = self.device
                self.device.config = self.config
                # Skip first restart
                if self.is_first_task and task == 'Restart':
                    logger.info('Skip task `Restart` at scheduler start')
                    self.config.task_delay(server_update=True)
                    del_cached_property(self, 'config')
                    continue

                # Run
                logger.info(f'Scheduler: Start task `{task}`')
                self.device.stuck_record_clear()
                self.device.click_record_clear()

            logger.hr(task, level=0)
            success = self.run(inflection.underscore(task), skip_first_screenshot=(task == 'Restart'))
            logger.info(f'Scheduler: End task `{task}`')
            self.is_first_task = False

            # Check failures
            failed = deep_get(self.failure_record, keys=task, default=0)
            failed = 0 if success else failed + 1
            deep_set(self.failure_record, keys=task, value=failed)
            if failed >= 3:
                logger.critical(f'Task `{task}` failed 3 or more times.')
                logger.critical(
                    "Possible reason #1: You haven't used it correctly. Please read the help text of the options."
                )
                logger.critical(
                    'Possible reason #2: There is a problem with this task. '
                    'Please contact developers or try to fix it yourself.'
                )
                logger.critical('Request human takeover')
                if self.config.Notification_WhenDailyTaskCrashed:
                    handle_notify(
                        self.config.Notification_OnePushConfig,
                        title=f'NKAS <{self.config_name}> crashed',
                        content=f'<{self.config_name}> RequestHumanTakeover\nTask `{task}` failed 3 or more times.',
                    )
                # 设置屏幕方向
                if self.config.Client_Platform == 'win' and self.config.PCClient_ScreenRotate:
                    self.device.screen_rotate()
                exit(1)

            if success:
                del_cached_property(self, 'config')
                continue
            # elif self.config.Error_HandleError:
            else:
                # self.config.task_delay(success=False)
                del_cached_property(self, 'config')
                # self.checker.check_now()
                continue
            # else:
            #     break


if __name__ == '__main__':
    nkas = NikkeAutoScript()
    nkas.loop()
