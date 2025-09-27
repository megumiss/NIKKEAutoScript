import re
from functools import cached_property

from module.base.decorator import del_cached_property
from module.base.timer import Timer
from module.base.utils import _area_offset, exec_file, mask_area
from module.event.assets import SHOP_MONEY_LACK
from module.handler.assets import CONFIRM_B, REWARD
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.shop.assets import *
from module.ui.assets import SHOP_CHECK
from module.ui.page import page_shop
from module.ui.ui import UI


class NotEnoughMoneyError(Exception):
    pass


class PurchaseTimeTooLong(Exception):
    pass


class Refresh(Exception):
    pass


class ProductQueueIsEmpty(Exception):
    pass


class Product:
    def __init__(self, name, count, button):
        self.name = name
        self.timer = Timer(0, count=count - 1).start()
        self.button: Button = button

    def __str__(self):
        return f'Product: ({self.name}, count: {self.timer.count + 1})'


class ShopBase(UI):
    def ensure_into_shop(self, button, check, skip_first_screenshot=True):
        # 确保进入指定商店页面
        logger.hr(f'{check.name.split("_")[:1][0]} SHOP', 2)
        confirm_timer = Timer(2, 3).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 检查是否已进入目标页面
            if self.appear(check, offset=(5, 5)) and confirm_timer.reached():
                break

            # 点击进入商店按钮
            if click_timer.reached() and self.appear_then_click(button, offset=(5, 5), interval=5):
                click_timer.reset()
                confirm_timer.reset()
                continue

    def handle_purchase(self, button=None, skip_first_screenshot=True):
        """
        处理购买逻辑。
        """
        self.device.click_record_clear()
        _confirm_timer = Timer(1, 2).start()
        click_timer = Timer(0.3)
        max_clicks = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 检查是否余额不足
            if self.appear(NO_MONEY, offset=(5, 5), static=False) and NO_MONEY.match_appear_on(self.device.image):
                raise NotEnoughMoneyError

            # 资金不足
            if self.appear(SHOP_MONEY_LACK, offset=30, static=False):
                raise NotEnoughMoneyError

            # 检查是否达到最大购买数量
            if (
                click_timer.reached()
                and max_clicks < 3
                and self.appear_then_click(MAX, offset=30, threshold=0.9, interval=1)
            ):
                max_clicks += 1
                self.device.sleep(0.3)
                click_timer.reset()
                continue

            # 点击确认购买按钮
            if click_timer.reached() and self.appear_then_click(BUY, offset=(30, 30), interval=3, static=False):
                click_timer.reset()
                continue

            # 处理奖励领取
            if click_timer.reached() and self.handle_reward(1):
                skip_first_screenshot = True
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()
                    self.handle_reward(1)
                    if self.appear(SHOP_CHECK, offset=(5, 5)) and _confirm_timer.reached():
                        max_clicks = 0
                        return

    def process_purchase(self, products: SelectedGrids, check_price=False, refresh=False, skip_first_screenshot=True):
        """
        处理商品购买流程。
        """
        timeout = Timer(2.7, 3).start()
        click_timer = Timer(0.3)
        product: Button = products.first_or_none().button
        logger.attr('PENDING PRODUCT LIST', [i.name for i in products])

        while 1:
            try:
                # 超时处理，移除当前商品并尝试刷新
                if timeout.reached():
                    timeout.reset()
                    products = products.delete([products.first_or_none()])
                    logger.attr('PENDING PRODUCT LIST', [i.name for i in products])
                    if products.first_or_none() is None:
                        if refresh and not self.refreshed:
                            raise Refresh
                        break
                    product = products.first_or_none().button

                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                # 检查是否在购买确认页面
                if self.appear(PURCHASE_CHECK, offset=(5, 5), interval=0.6, static=False):
                    timeout.reset()
                    self.handle_purchase()

                else:
                    # 检查商品是否可见并点击
                    if self.appear(
                        product, offset=(5, 5), threshold=0.9, interval=0.8, static=False
                    ) and product.match_appear_on(self.device.image, 6):
                        if check_price and product.name != ORNAMENT.name:
                            # 检查商品价格
                            area = _area_offset(product.button, (-50, 0, 50, 250))
                            img = self.device.image[area[1] : area[3], area[0] : area[2]]
                            super().__setattr__('_image', img)
                            if not self.credit_or_gratis:
                                skip_first_screenshot = True
                                self.device.image = mask_area(self.device.image, product.button)
                                continue
                        if click_timer.reached():
                            self.device.click(product)
                            click_timer.reset()
                            timeout.reset()
                            continue
            except Refresh:
                # 刷新商店逻辑
                if not self.refreshed:
                    click_timer = Timer(0.6)
                    while 1:
                        if skip_first_screenshot:
                            skip_first_screenshot = False
                        else:
                            self.device.screenshot()

                        # 点击刷新按钮
                        if (
                            not self.refreshed
                            and click_timer.reached()
                            and self.appear(REFRESH, offset=(5, 5), interval=1, static=False)
                        ):
                            x, y = REFRESH.location
                            self.device.click_minitouch(x - 80, y)
                            click_timer.reset()

                        # 确认刷新
                        if (
                            click_timer.reached()
                            and self.appear(GRATIS_REFRESH, offset=5, threshold=0.96, interval=1, static=False)
                            and self.appear_then_click(CONFIRM_B, offset=5, interval=1, static=False)
                        ):
                            while 1:
                                self.device.screenshot()

                                if click_timer.reached() and self.appear_then_click(
                                    CONFIRM_B, offset=(5, 5), interval=1, static=False
                                ):
                                    click_timer.reset()
                                    continue

                                if self.appear(SHOP_CHECK, offset=5) and SHOP_CHECK.appear_on(self.device.image, 25):
                                    break

                            # 更新商品列表
                            del self.__dict__['general_shop_priority']
                            products = self.general_shop_priority
                            product: Button = products.first_or_none().button
                            self.refreshed = True
                            timeout.reset()
                            break

                        # 取消刷新
                        if click_timer.reached() and self.appear_then_click(
                            CANCEL, offset=5, threshold=0.9, interval=2, static=False
                        ):
                            click_timer.reset()
                            while 1:
                                self.device.screenshot()
                                if click_timer.reached() and self.appear_then_click(CANCEL, offset=5, static=False):
                                    click_timer.reset()
                                if self.appear(SHOP_CHECK, offset=5) and SHOP_CHECK.appear_on(self.device.image, 25):
                                    break
                            self.refreshed = True
                            timeout.reset()
                            break

    def swipe_and_purchase(self, products: SelectedGrids):
        """
        滑动屏幕查找并购买商品，逐个处理每个商品。
        """
        click_timer = Timer(0.6)
        logger.attr('PENDING PRODUCT LIST', [i.name for i in products])

        # 遍历商品列表，每个商品单独处理
        for product in list(products):
            # 每次购买某个物品前先滚动到顶部
            self.ensure_sroll_to_top(x1=(505, 700), x2=(505, 1000), count=4, delay=0.5)
            logger.info(f'[Purchase Start] {product.name}')

            while 1:
                self.device.screenshot()

                # 检查是否在购买确认页面
                if self.appear(PURCHASE_CHECK, offset=10, static=False):
                    img = self.device.image
                    self.handle_purchase(product.button, skip_first_screenshot=False)
                    self.device.image = img
                    logger.info(f'[Purchased] {product.name}')
                    click_timer.reset()
                    continue

                # 检查当前商品是否可见
                if (
                    click_timer.reached()
                    and self.appear(BUY_ALL, offset=10)
                    and self.appear_then_click(product.button, offset=10, threshold=0.85, interval=1, static=False)
                ):
                    click_timer.reset()
                    continue

                # 结束当前商品的扫描，进入下一个商品
                if self.appear(END_LIST_CHECK, threshold=5):
                    logger.info(f'[Purchase done or not found] {product.name}')
                    break

                # 滑动屏幕继续查找
                self.ensure_sroll((505, 1000), (505, 700), speed=5, hold=0.5, count=1, delay=0.5, method='scroll')

            # 单个商品完成后，从待购买列表中移除
            # products = products.delete([product])
            # logger.attr('PENDING PRODUCT LIST', [i.name for i in products])

        # 全部完成
        logger.info('[All Products Processed]')

    def ensure_back(self, check: Button, skip_first_screenshot=True):
        # 确保返回到指定页面
        confirm_timer = Timer(1, count=1).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 点击取消按钮返回
            if click_timer.reached() and self.appear_then_click(CANCEL, offset=(30, 30), interval=1, static=False):
                confirm_timer.reset()
                click_timer.reset()
                continue

            # 检查是否已返回目标页面
            if self.appear(check, offset=(10, 10), static=False) and confirm_timer.reached():
                break


class Shop(ShopBase):
    @cached_property
    def assets(self) -> dict:
        return exec_file('./module/shop/assets.py')

    @cached_property
    def general_shop_priority(self) -> SelectedGrids:
        if self.config.GeneralShop_priority is None or not len(self.config.GeneralShop_priority.strip(' ')):
            priority = self.config.GENERAL_SHOP_PRIORITY
        else:
            priority = self.config.GeneralShop_priority

        priority = re.sub(r'\s+', '', priority).split('>')
        return SelectedGrids(
            [Product(i, self.config.GENERAL_SHOP_PRODUCT.get(i), self.assets.get(i)) for i in priority]
        )

    @cached_property
    def arena_shop_priority(self) -> SelectedGrids:
        priority = re.sub(r'\s+', '', self.config.ArenaShop_priority).split('>')
        return SelectedGrids([Product(i, self.config.ARENA_SHOP_PRODUCT.get(i), self.assets.get(i)) for i in priority])

    @property
    def credit_or_gratis(self) -> bool:
        if GRATIS_B.match(self._image, offset=(5, 5), threshold=0.96, static=False) and GRATIS_B.match_appear_on(
            self._image, threshold=5
        ):
            return True
        elif CREDIT.match(self._image, offset=(5, 5), threshold=0.96, static=False) and CREDIT.match_appear_on(
            self._image, threshold=5
        ):
            return True

    def run(self):
        self.ui_ensure(page_shop)
        if self.config.GeneralShop_enable:
            super().__setattr__('refreshed', False)
            self.ensure_into_shop(GOTO_GENERAL_SHOP, GENERAL_SHOP_CHECK)
            self.process_purchase(self.general_shop_priority, True, True)
        try:
            if self.config.ArenaShop_enable:
                self.ensure_into_shop(GOTO_ARENA_SHOP, ARENA_SHOP_CHECK)
                if self.config.ArenaShop_priority is None or not len(self.config.ArenaShop_priority.strip(' ')):
                    raise ProductQueueIsEmpty
                self.process_purchase(self.arena_shop_priority)
        except NotEnoughMoneyError:
            logger.error('The rest of money is not enough to buy this product')
            self.ensure_back(ARENA_SHOP_CHECK)
        except ProductQueueIsEmpty:
            logger.warning('There are no products included in the queue option')
        except PurchaseTimeTooLong:
            pass
        del_cached_property(self, 'general_shop_priority')
        del_cached_property(self, 'arena_shop_priority')
        self.config.task_delay(server_update=True)
