import re

from module.base.timer import Timer
from module.base.utils import point2str
from module.commission.assets import *
from module.logger import logger
from module.ocr.ocr import Digit
from module.ui.assets import COMMISSION_CHECK
from module.ui.page import page_commission
from module.ui.ui import UI


class NoOpportunity(Exception):
    pass


class Commission(UI):
    @property
    def shop_delay_list(self) -> list[str]:
        """
        妮姬列表，格式: nikke1 > nikke2 > nikke3
        """
        priority = self.config.CollectionItems_Priority
        priority = re.sub(r'\s+', '', priority).split('>')
        return [i for i in priority if i]

    def get_item_button(self, item: str):
        """
        根据选项名称获取对应的按钮
        """
        button_name = f'FAVORITE_ITEM_{item.upper()}'
        try:
            return globals()[button_name]
        except KeyError:
            logger.error(f"Button asset '{button_name}' not found for option '{item}'")
            raise

    @property
    def favorite_item_num(self):
        model_type = self.config.Optimization_OcrModelType
        ITEM_NUM = Digit(
            [ITEM_SELECTED_NUM.area],
            name='OPPORTUNITY_REMAIN',
            model_type=model_type,
            lang='ch',
        )

        return int(ITEM_NUM.ocr(self.device.image)['text'])

    def receive(self):
        logger.hr('Receive commission', 2)
        confirm_timer = Timer(3, count=5)
        click_timer = Timer(0.3)

        while 1:
            self.device.screenshot()

            if self.handle_reward(interval=1):
                click_timer.reset()
                continue

            # 全部领取
            if click_timer.reached() and self.appear_then_click(CLAIM, threshold=20, interval=1):
                click_timer.reset()
                continue

            # 等待领取完毕
            if self.appear(COMMISSION_CHECK, offset=10) and self.appear(CLAIM_DONE, threshold=20):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    # 需要派遣
                    if self.appear(DISPATCH, threshold=10):
                        logger.info('Receive commission done')
                        break
                    # 没次数/进行中
                    if self.appear(DISPATCH_DONE, threshold=10):
                        raise NoOpportunity
            else:
                confirm_timer.clear()

    def select_favorite(self, skip_first_screenshot=True):
        logger.hr('Select favorite item', 2)
        click_timer = Timer(0.3)

        # 获取当前收藏品数量
        num = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(COMMISSION_CHECK, offset=10) and self.appear_then_click(
                ITEM_SELECTED, offset=10, interval=1
            ):
                click_timer.reset()
                continue

            if self.appear(ITEM_SELECTED_NUM_CHECK, offset=100):
                self.device.sleep(0.5)
                num = self.favorite_item_num

                # 关闭收藏品窗口
                while 1:
                    self.device.screenshot()

                    if not self.appear(ITEM_SELECTED_NUM_CHECK, offset=10) and self.appear(COMMISSION_CHECK, offset=10):
                        click_timer.reset()
                        break
                    if self.appear(ITEM_SELECTED_NUM_CHECK, offset=100):
                        logger.info('Click %s @ %s' % (point2str(10, 10), 'CLOSE_ITEM'))
                        self.device.click_minitouch(10, 10)
                        self.device.sleep(0.5)
                        click_timer.reset()
                        continue

                click_timer.reset()
                break

        if num >= 160:
            # 更换收藏品
            logger.info(f'Current favorite item num: {num}, need reselect')
            # 打开收藏品列表
            while 1:
                self.device.screenshot()

                if self.appear(COMMISSION_CHECK, offset=10) and self.appear_then_click(ITEM_SELECT, offset=10):
                    click_timer.reset()
                    continue
                if self.appear(ITEM_LIST_CHECK, offset=10) and self.appear(ITEM_LIST_SELECT_CONFIRM, threshold=10):
                    click_timer.reset()
                    break

            # 循环判断超出数量的是哪个收藏品
            current = None
            for item in self.shop_delay_list:
                if self.appear(self.get_item_button(item), offset=10, static=False):
                    current = item
                    logger.info(f'Current favorite item: {current}')
                    break
            if not current:
                logger.warning('Current favorite item can not judge')
            else:
                nikke_list = self.shop_delay_list.copy()
                # 只有多个候选时才移除
                if len(nikke_list) > 1 and current in nikke_list:
                    nikke_list.remove(current)
                    logger.info(f'Remove current favorite item: {current}')
                    self.config.CollectionItems_Priority = ' > '.join(nikke_list)
                else:
                    logger.info(f'Keep current favorite item: {current}')

            # 滑动到列表开始位置
            self.ensure_sroll((620, 400), (620, 900), speed=30, count=3, delay=0.3, method='swipe')
            # 选择新的收藏品
            select_times = 0
            while 1:
                self.device.screenshot()

                # 派遣主页
                if self.appear(COMMISSION_CHECK, offset=10):
                    break

                # 派遣选择
                if (
                    select_times > 2
                    and self.appear(ITEM_LIST_CHECK, offset=10)
                    and self.appear_then_click(ITEM_LIST_SELECT_CONFIRM, threshold=10)
                ):
                    click_timer.reset()
                    continue

                # 选择收藏品（这里取的是删除旧收藏品之后的第一个）
                if self.appear(ITEM_LIST_CHECK, offset=10) and nikke_list:
                    if self.appear_then_click(self.get_item_button(nikke_list[0]), offset=10, click_offset=(150, 0)):
                        logger.info(f'Select new favorite item: {nikke_list[0]}')
                        select_times += 1
                        click_timer.reset()
                        continue

                # 未找到收藏品，进行滑动
                self.device.sleep(0.5)
                self.ensure_sroll((620, 1000), (620, 700), speed=5, hold=1, count=1, delay=0.5, method='scroll')
        else:
            logger.info(f'Current favorite item num: {num}, skip reselect')

    def dispatch(self, skip_first_screenshot=True):
        logger.hr('Dispatch commission', 2)
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 派遣
            if (
                click_timer.reached()
                and self.appear(COMMISSION_CHECK, offset=10)
                and self.appear_then_click(DISPATCH, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 派遣确认
            if (
                click_timer.reached()
                and self.appear(ITEM_LIST_CHECK, offset=10)  # 复用
                and self.appear_then_click(DISPATCH_CONFIRM, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            if self.appear(COMMISSION_CHECK, offset=(10, 10)) and self.appear(DISPATCH_DONE, threshold=10):
                logger.info('Dispatch commission done')
                break

    def run(self):
        logger.hr('Dispatch and claim commission')
        self.ui_ensure(page_commission)
        try:
            # 领取
            self.receive()
            # 处理收藏品
            if self.config.CollectionItems_Enable and self.config.CollectionItems_Priority:
                self.select_favorite()
            # 派遣
            self.dispatch()
        except NoOpportunity:
            logger.warning('Commission running or allready done')

        self.config.task_delay(server_update=True)
