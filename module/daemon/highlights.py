from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import crop, point2str
from module.conversation.assets import COMMUNICATE_NIKKE_AVATAR, DETAIL_CHECK
from module.daemon.assets import *
from module.daemon.daemon_base import DaemonBase
from module.logger import logger
from module.ui.assets import SKIP
from module.ui.ui import UI


class Highlights(UI, DaemonBase):
    def run(self):
        logger.hr('Highlights start', 1)
        timeout = Timer(600, count=10)
        click_timer = Timer(0.3)

        next_nikke = False
        while 1:
            self.device.screenshot()

            # 花絮红点
            if click_timer.reached() and self.appear(HIGHLIGHTS_RED, offset=(10, 10), threshold=0.9):
                close = False
                while 1:
                    self.device.screenshot()

                    # 点击红点，进入鉴赏列表
                    if self.appear_then_click(HIGHLIGHTS_RED, offset=(10, 10), threshold=0.9, interval=1):
                        click_timer.reset()
                        continue
                    # 回到咨询页面
                    if close and self.appear(DETAIL_CHECK, offset=10, threshold=0.85):
                        next_nikke = True
                        logger.info('Back to conversation page')
                        break
                    # 咨询头像有红点，鉴赏列表上方没红点
                    if (
                        self.appear(HIGHLIGHTS_LIST, offset=10)
                        and not self.appear(HIGHLIGHTS_LIST_RED_CHECK, offset=10)
                        and self.appear_then_click(HIGHLIGHTS_LIST_CLOSE, offset=30, interval=1)
                    ):
                        close = True
                        logger.info('Highlights allready done, close list')
                        click_timer.reset()
                        continue

                    # 红点消失，播放花絮
                    if not self.appear(HIGHLIGHTS_RED, offset=(10, 10), threshold=0.9) and self.appear(
                        HIGHLIGHTS_LIST_RED_CHECK, offset=10
                    ):
                        # 寻找一个鉴赏
                        if self.appear(HIGHLIGHTS_LIST, offset=10):
                            self.device.sleep(0.5)
                            if self.appear_then_click(HIGHLIGHTS_LIST_RED, offset=(-10, -30, 10, 500), interval=1):
                                # 鉴赏开始
                                self.play()
                                click_timer.reset()
                                break
                            else:
                                # 如果点开nikke没有发现红点，需要滑动到底部
                                self.ensure_sroll_to_bottom()
                    else:
                        click_timer.reset()
                        continue

            # 没有红点，切换nikke
            if (
                self.appear(DETAIL_CHECK, offset=10, threshold=0.85)
                and not self.appear(HIGHLIGHTS_RED, offset=(10, 10), threshold=0.9)
            ) or next_nikke:
                self.switch_to_next()
                next_nikke = False

            if not timeout.started():
                timeout.start()
            if timeout.reached():
                break
            else:
                timeout.clear()

    def play(self):
        logger.hr('Highlights a nikke', 2)
        confirm_timer = Timer(1, count=3)
        click_timer = Timer(0.3)

        skip = False
        while 1:
            self.device.screenshot()

            # SKIP
            if click_timer.reached() and self.appear_then_click(SKIP, offset=10, interval=1):
                skip = True
                click_timer.reset()
                continue

            # 领取
            if self.handle_reward(interval=1):
                click_timer.reset()
                continue

            # 回到咨询页面
            if skip and self.appear(DETAIL_CHECK, offset=10, threshold=0.85):
                logger.info('Back to conversation page')
                break

            # 关闭鉴赏列表
            if (
                skip
                and self.appear(HIGHLIGHTS_LIST, offset=10)
                and not self.appear(PLAY_HIGHLIGHTS, offset=30)
                and self.appear_then_click(HIGHLIGHTS_LIST_CLOSE, offset=30, interval=1)
            ):
                logger.info('Highlights end, close list')
                click_timer.reset()
                continue

            # 下一个花絮超过1秒
            if self.appear(PLAY_HIGHLIGHTS, offset=30):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached() and self.appear_then_click(PLAY_HIGHLIGHTS, offset=30, interval=1):
                    continue
            else:
                confirm_timer.clear()

    def switch_to_next(self):
        logger.info('Switch to next nikke')

        self.device.sleep(0.5)
        tmp_image = self.device.image
        logger.info('Click %s @ %s' % (point2str(690, 560), 'NEXT_NIKKE'))
        self.device.click_minitouch(690, 560)
        # 比较头像是否变化
        switch_timer = Timer(3, count=3).start()
        while 1:
            self.device.screenshot()
            avatar = Button(
                COMMUNICATE_NIKKE_AVATAR.area,
                None,
                button=COMMUNICATE_NIKKE_AVATAR.area,
            )
            avatar._match_init = True
            avatar.image = crop(tmp_image, COMMUNICATE_NIKKE_AVATAR.area)
            if not self.appear(avatar, offset=5, threshold=0.95):
                break
            if switch_timer.reached():
                logger.info('Click %s @ %s' % (point2str(690, 560), 'NEXT_NIKKE'))
                self.device.click_minitouch(690, 560)
                switch_timer.reset()


if __name__ == '__main__':
    b = Highlights('nkas', task='Highlights')
    b.run()
