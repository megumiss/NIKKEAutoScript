from module.base.timer import Timer
from module.coop.coop import CoopIsUnavailable
from module.event.assets import TEMPLATE_SHOP_GEM
from module.event.base import ChallengeNotFoundError, EventSelectError, EventUnavailableError
from module.event.challenge import EventChallenge
from module.event.coop import EventCoop
from module.event.game import EventGame
from module.event.login import EventLogin
from module.event.reward import EventReward
from module.event.shop import EventShop
from module.event.story import EventStory
from module.logger import logger
from module.ui.assets import EVENT_SWITCH, MAIN_CHECK, SKIP
from module.ui.page import page_main


class Event(
    EventLogin,
    EventChallenge,
    EventReward,
    EventStory,
    EventCoop,
    EventShop,
    EventGame,
):
    def ensure_into_event(self, skip_first_screenshot=True):
        logger.hr('OPEN EVENT STORY', 2)
        click_timer = Timer(0.3)
        confirm_timer = Timer(30, count=20).start()
        event_timer = Timer(3, count=5)
        skip_click_timer = Timer(5)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 检查活动主页
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                if not event_timer.started():
                    event_timer.start()
                if event_timer.reached():
                    break
            else:
                event_timer.clear()

            # 切换活动
            if (
                click_timer.reached()
                and self.appear(MAIN_CHECK, offset=10)
                and self.appear_then_click(EVENT_SWITCH, offset=10, interval=3)
            ):
                click_timer.reset()
                confirm_timer.reset()
                continue

            # 在主页点击故事图标
            if (
                click_timer.reached()
                and self.appear(MAIN_CHECK, offset=10)
                and self.appear_then_click(self.event_assets.MAIN_GOTO_EVENT, offset=(50, 50), interval=5)
            ):
                click_timer.reset()
                logger.info('Open event story')
                continue

            # 跳过剧情
            if click_timer.reached() and self.appear_then_click(SKIP, offset=10, interval=2):
                click_timer.reset()
                confirm_timer.reset()
                continue

            # 跳过加载和第一次进入时的continue
            if (
                skip_click_timer.reached()
                and not self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and not self.appear(MAIN_CHECK, offset=10)
            ):
                self.device.click_minitouch(10, 10)
                skip_click_timer.reset()

            if confirm_timer.reached():
                logger.error('Event not found')
                raise EventUnavailableError

    def run(self):
        # self.team_up()
        # image = cv2.imread('1.png')
        # cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=image)
        # self.device.image  = image
        # self.appear_text('8')

        # 是否需要重新执行
        coop_reschedule = False

        try:
            self.ui_ensure(page_main)
            _ = self.event

            self.ensure_into_event()
            if self.config.Event_LoginStamp:
                self.login_stamp()
            if self.config.Event_Challenge:
                self.challenge()
            # 提前买票
            if self.config.StoryStage_HardTicket and self.config.EventInfo_StoryDifficulty == 'Hard':
                self.shop(TEMPLATE_SHOP_GEM)
                self.back_to_event()
            if self.config.StoryStage_AutoPush or self.config.StoryStage_Sweep:
                self.story()
            if self.config.Event_Coop:
                coop_reschedule = self.coop()
            if self.config.Event_Game:
                self.game()

            self.reward()

            if self.config.Event_Shop:
                self.shop()

        except EventSelectError:
            logger.error('The event stage/difficulty select wrong')
        except EventUnavailableError:
            logger.error('The event is no longer available')
        except ChallengeNotFoundError:
            logger.error('Challenge stage not found')
        except CoopIsUnavailable:
            pass

        # 若协同未开启则调整延迟时间
        if self.config.Event_Coop and coop_reschedule:
            self.config.Scheduler_ServerUpdate = '04:00, 16:00'
        self.config.task_delay(server_update=True)
