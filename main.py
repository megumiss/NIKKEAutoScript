import os
import re
import time
from datetime import datetime, timedelta
from functools import cached_property

import inflection

from module.config.config import NikkeConfig, TaskEnd
from module.config.utils import deep_get, deep_set
from module.exception import (
    GamePageUnknownError,
    RequestHumanTakeover,
    GameNotRunningError,
    GameStuckError,
    GameTooManyClickError,
    GameServerUnderMaintenance,
    GameStart,
)
from module.logger import logger
from module.notify import handle_notify


class NikkeAutoScript:
    def __init__(self, config_name="nkas"):
        logger.hr("Start", level=0)
        self.config_name = config_name

    @cached_property
    def config(self):
        try:
            config = NikkeConfig(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical("Request human takeover")
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self):
        try:
            from module.device.device import Device

            device = Device(config=self.config)
            return device
        except RequestHumanTakeover:
            logger.critical("Request human takeover")
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    def run(self, command):
        try:
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
            self.config.task_call("Restart")
            return True

        except (GameStuckError, GameTooManyClickError) as e:
            """
            当一直没有进行操作时或点击同一目标过多时，尝试重启游戏
            """
            logger.error(e)
            """
                在 Alas 中会将在raise前最后的截图和log写入./log/error 
            """
            self.save_error_log()
            logger.warning(
                f"Game stuck, {self.device.package} will be restarted in 10 seconds"
            )
            logger.warning("If you are playing by hand, please stop NKAS")
            self.config.task_call("Restart")
            self.device.sleep(10)
            return False
        except GamePageUnknownError:
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            # self.device.app_stop()
            if self.config.Notification_WhenDailyTaskCrashed:
                handle_notify(
                    self.config.Notification_OnePushConfig,
                    title="NKAS crashed",
                    content=f"<{self.config_name}> GamePageUnknownError",
                )
            exit(1)
        except GameServerUnderMaintenance as e:
            logger.error(e)
            self.device.app_stop()
            if self.config.Notification_WhenDailyTaskCrashed:
                handle_notify(
                    self.config.Notification_OnePushConfig,
                    title="NKAS crashed",
                    content=f"<{self.config_name}> GameServerUnderMaintenance",
                )
            exit(1)
        except RequestHumanTakeover:
            logger.critical("Request human takeover")
            if self.config.Notification_WhenDailyTaskCrashed:
                handle_notify(
                    self.config.Notification_OnePushConfig,
                    title="NKAS crashed",
                    content=f"<{self.config_name}> RequestHumanTakeover",
                )
            exit(1)
        except Exception as e:
            self.save_error_log()
            logger.exception(e)
            if self.config.Notification_WhenDailyTaskCrashed:
                handle_notify(
                    self.config.Notification_OnePushConfig,
                    title="NKAS crashed",
                    content=f"<{self.config_name}> Exception occured",
                )
            exit(1)

    def save_error_log(self):
        """
        Save last 60 screenshots in ./log/error/<timestamp>
        Save logs to ./log/error/<timestamp>/log.txt
        """

        from module.base.utils import save_image
        from module.handler.sensitive_info import handle_sensitive_logs

        if not os.path.exists("./log/error"):
            os.mkdir("./log/error")
        folder = f"./log/error/{int(time.time() * 1000)}"
        logger.warning(f"Saving error: {folder}")
        os.mkdir(folder)
        for data in self.device.screenshot_deque:
            image_time = datetime.strftime(data["time"], "%Y-%m-%d_%H-%M-%S-%f")
            # 遮挡个人消息
            # image = handle_sensitive_image(data['image'])
            image = data["image"]
            save_image(image, f"{folder}/{image_time}.png")
        with open(logger.log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            start = 0
            for index, line in enumerate(lines):
                line = line.strip(" \r\t\n")
                # 从最后一个任务截取
                if re.match("^═{15,}$", line):
                    start = index
            lines = lines[start - 2:]
            # 替换真实路径
            lines = handle_sensitive_logs(lines)
        with open(f"{folder}/log.txt", "w", encoding="utf-8") as f:
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
            logger.info("App is already running, goto main page")
            UI(self.config, device=self.device).ui_goto_main()
        else:
            logger.info("App is not running, start app and goto main page")
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

    def simulation_room(self):
        from module.simulation_room.simulation_room import SimulationRoom

        SimulationRoom(config=self.config, device=self.device).run()

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

    def blablalink(self):
        from module.blablalink.blablalink import Blablalink

        Blablalink(config=self.config).run()

    def tower_daemon(self):
        from module.daemon.tower_daemon import TowerDaemon

        TowerDaemon(config=self.config, device=self.device).run()

    def event_daemon(self):
        from module.event_daemon.event_daemon import EventDaemon

        EventDaemon(config=self.config, device=self.device).run()

    def story_event(self):
        from module.story_event.story_event import StoryEvent

        StoryEvent(config=self.config, device=self.device).run()

    def coop(self):
        from module.coop.coop import Coop

        Coop(config=self.config, device=self.device).run()

    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        future = future + timedelta(seconds=1)
        """
            记录开始等待任务时，配置文件的最后更改时间
        """
        self.config.start_watching()
        while 1:
            if datetime.now() > future:
                return True
            if self.stop_event is not None:
                if self.stop_event.is_set():
                    logger.info("Update event detected")
                    logger.info(f"[{self.config_name}] exited. Reason: Update")
                    exit(0)

            time.sleep(5)
            """
                在等待过程中持续对比配置文件的最后更改时间
            """
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
            """
                在 Alas 中每个任务都有共有属性 Scheduler
                调度器在运行任务时，是根据任务的 Scheduler.Command 调用对应方法
                在那之前，Alas 会调用 config.bind(task)
                将该任务的 Scheduler 属性覆盖到类变量
                在使用到共有属性时，只会使用该任务的设置
                例如在设置任务的下次运行时间时，会使用到 Scheduler.SuccessInterval
                interval = (self.Scheduler_SuccessInterval if success else self.Scheduler_FailureInterval)
                run.append(datetime.now() + ensure_delta(interval))
            """
            self.config.bind(task)
            from module.base.resource import release_resources

            if self.config.task.command != "NKAS":
                release_resources(next_task=task.command)

            if task.next_run > datetime.now():
                logger.info(f"Wait until {task.next_run} for task `{task.command}`")
                method = self.config.Optimization_WhenTaskQueueEmpty

                """
                    在等待任务的过程中可能会被人为修改运行时间
                    if not self.wait_until(task.next_run):
                        del self.__dict__['config']
                        continue

                    Returns:
                        bool: True if wait finished, False if config changed.
                """

                if method == "close_game":
                    logger.info("Close game during wait")
                    self.device.app_stop()
                    release_resources()
                    if not self.wait_until(task.next_run):
                        del self.__dict__["config"]
                        continue
                    self.run("start")
                elif method == "goto_main":
                    logger.info("Goto main page during wait")
                    self.run("goto_main")
                    release_resources()
                    if not self.wait_until(task.next_run):
                        del self.__dict__["config"]
                        continue
                elif method == "stay_there":
                    logger.info("Stay there during wait")
                    release_resources()
                    if not self.wait_until(task.next_run):
                        del self.__dict__["config"]
                        continue
            break

        return task.command

    def loop(self):
        logger.set_file_logger(self.config_name)
        logger.info(f"Start scheduler loop: {self.config_name}")
        is_first = True
        failure_record = {}

        while 1:
            # Check update event from GUI
            if self.stop_event is not None:
                if self.stop_event.is_set():
                    logger.info("Update event detected")
                    logger.info(f"Alas [{self.config_name}] exited.")
                    break
            
            task = self.get_next_task()
            _ = self.device

            if is_first and task == "Restart":
                logger.info("Skip task `Restart` at scheduler start")
                self.config.task_delay(server_update=True)
                del self.__dict__["config"]
                continue

            # Run
            logger.info(f"Scheduler: Start task `{task}`")
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            logger.hr(task, level=0)
            """

                https://inflection.readthedocs.io/en/latest/

                inflection.underscore('Restart') => 'restart'
                inflection.underscore('DeviceType') => 'device_type'

            """

            success = self.run(inflection.underscore(task))
            logger.info(f"Scheduler: End task `{task}`")
            is_first = False

            """
                记录某个任务出错的次数
            """
            failed = deep_get(failure_record, keys=task, default=0)
            failed = 0 if success else failed + 1
            deep_set(failure_record, keys=task, value=failed)
            if failed >= 3:
                logger.critical(f"Task `{task}` failed 3 or more times.")
                logger.critical(
                    "Possible reason #1: You haven't used it correctly. "
                    "Please read the help text of the options."
                )
                logger.critical(
                    "Possible reason #2: There is a problem with this task. "
                    "Please contact developer or try to fix it yourself."
                )
                logger.critical("Request human takeover")
                exit(1)

            if success:
                del self.__dict__["config"]
                continue
            # 出现错误时
            else:
                del self.__dict__["config"]
                continue


if __name__ == "__main__":
    nkas = NikkeAutoScript()
    nkas.loop()