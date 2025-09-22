from module.base.decorator import run_once
from module.base.timer import Timer
from module.exception import GameNotRunningError, GamePageUnknownError, GameStart
from module.handler.assets import *
from module.handler.info_handle import InfoHandler
from module.logger import logger
from module.ui.assets import GOTO_MAIN
from module.ui.page import (
    Page,
    page_arena,
    page_ark,
    page_cash_shop,
    page_commission,
    page_conversation,
    page_daily,
    page_destroy,
    page_friend,
    page_interception,
    page_inventory,
    page_mailbox,
    page_main,
    page_outpost,
    page_pass,
    page_recruit,
    page_reward,
    page_rookie_arena,
    page_shop,
    page_simulation_room,
    page_special_arena,
    page_special_interception,
    page_team,
    page_tribe_tower,
    page_unknown,
)


class UI(InfoHandler):
    ui_pages = [
        page_unknown,
        page_main,
        page_reward,
        page_destroy,
        page_friend,
        page_daily,
        page_shop,
        page_cash_shop,
        page_team,
        page_inventory,
        page_pass,
        page_conversation,
        page_ark,
        page_tribe_tower,
        page_simulation_room,
        page_arena,
        page_rookie_arena,
        page_special_arena,
        page_outpost,
        page_commission,
        page_mailbox,
        page_interception,
        page_special_interception,
        page_recruit,
    ]

    def ui_page_appear(self, page: Page):
        """
        Args:
            page: Page
        """
        return self.appear(page.check_button, offset=(30, 30))

    def ui_get_current_page(self, skip_first_screenshot=True):
        logger.info('UI get current page')

        @run_once
        def app_check():
            if not self.device.app_is_running():
                raise GameNotRunningError('Game not running')

        @run_once
        def rotation_check():
            self.device.get_orientation()

        timeout = Timer(30, count=20).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
                if not hasattr(self.device, 'image') or self.device.image is None:
                    self.device.screenshot()
            else:
                self.device.screenshot()

            # End
            if timeout.reached():
                break

            # Known pages
            for page in self.ui_pages:
                if page.check_button is None:
                    continue
                if self.ui_page_appear(page=page):
                    logger.attr('UI', page.name)
                    self.ui_current = page
                    return page

            # Unknown page but able to handle
            logger.info('Unknown ui page')
            # 加载画面
            if self.appear_then_click(GOTO_MAIN, offset=(30, 30), interval=2):
                timeout.reset()
                continue
            if self.ui_additional():
                timeout.reset()
                continue

            app_check()
            rotation_check()

        # Unknown page, need manual switching
        logger.warning('Unknown ui page')
        if self.config.Client_Platform == 'adb':
            logger.attr('CLENT_PLATFORM', self.config.Client_Platform)
            logger.attr('EMULATOR_SCREENSHOT_METHOD', self.config.Emulator_ScreenshotMethod)
            logger.attr('EMULATOR_CONTROL_METHOD', self.config.Emulator_ControlMethod)
            logger.attr(
                'SERVER',
                'intl' if 'proximabeta' in self.config.Emulator_PackageName else 'tw',
            )
            logger.attr('LANGUAGE', self.config.Emulator_Language)
            logger.warning('Supported page: Any page with a "HOME" button on the bottom-left')
        if self.config.Client_Platform == 'win':
            logger.attr('CLENT_PLATFORM', self.config.Client_Platform)
            logger.attr('CLIENT', self.config.PCClientInfo_Client)
            # logger.attr('SERVER', )
            logger.attr('LANGUAGE', self.config.PCClientInfo_Language)
            logger.warning('Please check Launcher/Game title and process in correct')
        logger.warning('Starting from current page is not supported')
        logger.warning(f'Supported page: {[str(page) for page in self.ui_pages]}')
        logger.critical('Please switch to a supported page before starting NKAS')
        raise GamePageUnknownError

    def ui_goto(self, destination, offset=(30, 30), confirm_wait=0, skip_first_screenshot=True):
        """
        Args:
            destination (Page):
            offset:
            confirm_wait:
            skip_first_screenshot:
        """
        # Reset connection
        for page in self.ui_pages:
            page.parent = None

        # Create connection
        visited = [destination]
        visited = set(visited)

        """
            这段代码最开始会从所有页面的链接中找出去往 destination 的页面，将其添加到 new
            例如 destination 为 page_reward，这样添加到 new 的页面为 page_main
            然后 visited 和 new 的长度不相等，继续循环
            继续执行会找出所有去往 page_main 的页面，直到可去往 page_main 的页面都在 visited 中
            总之，这样会找出去往的目标页的页面，接着找出去往这个页面的页面，遍历完所有支持页面直到所有关联页面都被添加
        """
        while 1:
            new = visited.copy()

            for page in visited:
                for link in self.ui_pages:
                    if link in visited:
                        continue
                    if page in link.links:
                        link.parent = page
                        new.add(link)

            if len(new) == len(visited):
                break

            visited = new

        logger.hr(f'UI goto {destination}')
        confirm_timer = Timer(confirm_wait, count=int(confirm_wait // 0.5)).start()

        while 1:
            # GOTO_MAIN.clear_offset()
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # Destination page
            if self.appear(destination.check_button, offset=offset):
                if confirm_timer.reached():
                    logger.info(f'Page arrive: {destination}')
                    break
            else:
                confirm_timer.reset()

            # if self.handle_event():
            #     continue

            # Other pages
            clicked = False
            for page in visited:
                # 跳过 page_unknown 和 destination
                if not page.parent or not page.check_button:
                    continue

                if self.appear(page.check_button, offset=offset, interval=4):
                    logger.info(f'Page switch: {page} -> {page.parent}')
                    button = page.links[page.parent]
                    self.device.click(button)
                    # self.ui_button_interval_reset(button)
                    confirm_timer.reset()
                    clicked = True
                    break

            if clicked:
                continue

    def ui_ensure(self, destination, confirm_wait=0, skip_first_screenshot=True):
        logger.hr('UI ensure')
        self.ui_get_current_page(skip_first_screenshot=skip_first_screenshot)
        if self.ui_current == destination:
            logger.info('Already at %s' % destination)
            return False
        else:
            logger.info('Goto %s' % destination)
            self.ui_goto(destination, confirm_wait=confirm_wait, skip_first_screenshot=True)
            return True

    def ui_additional(self):
        """
        Handle all annoying popups during UI switching.

        Args:
            get_ship:
        """
        # TODO SKIP, 战斗, 公告, etc.

        # 指挥官等级升级
        if self.handle_level_up():
            return True

        # ----- REWARD -----
        if self.handle_reward():
            return True

        # 礼包
        if self.handle_paid_gift():
            return True

        if self.handle_announcement():
            return True

        # Daily Login, Memories Spring, Monthly Card, etc.
        if self.handle_login_reward():
            return True

        if self.handle_server():
            raise GameStart

        if self.handle_download():
            raise GameStart

        # 系统错误
        if self.handle_system_error():
            return True

        if self.handle_system_maintenance():
            return True

        if self.appear(LOGIN_PAGE_CHECK, offset=(30, 30)):
            raise GameStart

        if self.appear(LOGO_PAGE_CHECK, offset=(30, 30)):
            raise GameStart

        # if self.handle_event():
        #     return True

        if self.handle_login():
            return True

        """
           CONFIRM_A 按钮为平面或立体，'确认'没有阴影
           CONFIRM_B 按钮为立体，'确认'有阴影
           CONFIRM_B 和 CONFIRM_C 相似
        """

        # 未知弹窗的确认
        if self.appear_then_click(CONFIRM_A, offset=(30, 30), interval=3, static=False):
            return True

        if self.appear_then_click(CONFIRM_B, offset=(30, 30), interval=3, static=False):
            return True

        if self.appear_then_click(CONFIRM_C, offset=(30, 30), interval=3, static=False):
            return True

    def ui_goto_main(self):
        return self.ui_ensure(destination=page_main)
