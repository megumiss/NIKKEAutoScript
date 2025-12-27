from module.base.button import filter_buttons_in_area, merge_buttons
from module.base.decorator import Config
from module.base.timer import Timer
from module.coop.assets import (
    COOP_CHECK,
)
from module.coop.coop import Coop, NoOpportunityRemain
from module.event.assets import TEMPLATE_COOP_COMING_SOON, TEMPLATE_COOP_ICON
from module.event.base import EventBase
from module.logger import logger
from module.ui.assets import GOTO_BACK


class EventCoop(EventBase):
    def back_to_event_from_coop(self):
        logger.info('Back to event from coop')
        click_timer = Timer(0.3)
        event_timer = Timer(3, count=5)

        # 回到活动主页
        while 1:
            self.device.screenshot()

            if not self.appear(self.event_assets.COOP_SELECT_CHECK, offset=(30, 30)):
                if not event_timer.started():
                    event_timer.start()
                if event_timer.reached():
                    break
            else:
                event_timer.clear()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.COOP_SELECT_CHECK, offset=(30, 30))
                and self.appear_then_click(GOTO_BACK, offset=10, interval=2)
            ):
                click_timer.reset()
                continue

    @Config.when(EVENT_TYPE=1)
    def coop(self, skip_first_screenshot=True):
        """进入协同作战页面"""
        logger.hr('EVENT COOP START', 2)

        # 检查协同作战是否结束
        confirm_timer = Timer(10, count=10)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 协同选择/协同主页
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)) and not self.appear(
                self.event_assets.COOP_ENTER, offset=10
            ):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    logger.warning('Coop allready closed')
                    return False
            else:
                break

        # 走到协同作战
        click_timer = Timer(0.3)
        confirm_timer = Timer(1, count=3)

        direct = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.COOP_ENTER, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            # 最后阶段的协同未开启
            if self.appear(self.event_assets.LAST_COOP_LOCK, offset=10):
                logger.warning('Last Coop is not enabled')
                return True

            # 协同未在开启时间
            if self.appear(self.event_assets.COOP_LOCK, offset=10):
                logger.warning('Coop is not enabled')
                self.back_to_event_from_coop()
                return True

            # 协同选择/协同主页
            if self.appear(self.event_assets.COOP_SELECT_CHECK, offset=10) or self.appear(COOP_CHECK, offset=10):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    # 直接进入了协同主页
                    if self.appear(COOP_CHECK, offset=10):
                        direct = True
                    break
            else:
                confirm_timer.clear()

        if not direct:
            # 查找所有的协同图标，去重
            coop_icons = TEMPLATE_COOP_ICON.match_multi(self.device.image, name='COOP_ICON')
            if coop_icons:
                # 合并重复
                coop_icons = merge_buttons(coop_icons, x_threshold=30, y_threshold=30)
            else:
                logger.warning('Not found any coop in event')
                self.back_to_event_from_coop()
                return True
            # 查找所有coming soon的协同，去重
            coop_comings = TEMPLATE_COOP_COMING_SOON.match_multi(self.device.image, name='COOP_COMING_SOON')
            if coop_comings:
                # 合并重复
                coop_comings = merge_buttons(coop_comings, x_threshold=30, y_threshold=30)
            # 检查数量是否一致，一致则所有协同未开启
            if len(coop_icons) == len(coop_comings):
                logger.warning('Not found enabled coop in event')
                self.back_to_event_from_coop()
                return True
            else:
                # 不一致则通过coming soon过滤掉没开启的协同，过滤后的即为开启
                enabled_coop_icons = set()
                for cbtn in coop_comings:
                    # 用横坐标范围去筛选 coop_icons
                    icons = filter_buttons_in_area(coop_icons, x_range=(cbtn.area[0], cbtn.area[2]))
                    enabled_coop_icons.update(icons)

                # coop_icons 里减去 matched 的，就是额外的按钮
                enabled = [btn for btn in coop_icons if btn not in enabled_coop_icons]

        # 进入协同作战界面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 选择协同
            if click_timer.reached() and self.appear(self.event_assets.COOP_SELECT_CHECK, offset=10):
                self.device.click(enabled[0], click_offset=(0, 50))
                click_timer.reset()
                continue

            # 协同主页
            if self.appear(COOP_CHECK, offset=10):
                break

        _coop = Coop(self.config, self.device)
        if _coop.free_opportunity_remain and not _coop.dateline:
            try:
                _coop.start_coop()
            except NoOpportunityRemain:
                logger.info('There are no free opportunities')
                pass
        else:
            logger.info('There are no coop free opportunities')

        # 回到活动主页
        self.back_to_event()
        self.back_to_event_from_coop()
        return False

    @Config.when(EVENT_TYPE=(2, 3))
    def coop(self):
        logger.hr('EVENT COOP START', 2)
        logger.info('Small event, skip coop')
