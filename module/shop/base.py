from functools import cached_property

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import exec_file, mask_area
from module.event.assets import SHOP_MONEY_LACK
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.shop.assets import BUY, CANCEL, MAX, NO_MONEY, PURCHASE_CHECK, SHIFTY_SHOP_CHECK
from module.ui.assets import SHOP_CHECK
from module.ui.ui import UI


class NotEnoughMoneyError(Exception):
    pass


class PurchaseTimeTooLong(Exception):
    pass


class Refresh(Exception):
    pass


class ProductQueueIsEmpty(Exception):
    pass


class ShiftyShopReplaced(Exception):
    pass


class Product:
    def __init__(self, name, count, button):
        self.name = name
        self.timer = Timer(0, count=count - 1).start()
        self.button: Button = button

    def __str__(self):
        return f'Product: ({self.name}, count: {self.timer.count + 1})'


class ShopBase(UI):
    @cached_property
    def assets(self) -> dict:
        return exec_file('./module/shop/assets.py')

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
            # 谢芙蒂占领
            if self.appear(SHIFTY_SHOP_CHECK, offset=10) and confirm_timer.reached():
                raise ShiftyShopReplaced

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
                    try:
                        self.handle_purchase()
                    except NotEnoughMoneyError:
                        # 余额不足只跳过当前商品，避免中断后续商品（含免费商品）的购买
                        logger.warning(f'Not enough money to buy {products.first_or_none().name}, skip')
                        products = products.delete([products.first_or_none()])
                        logger.attr('PENDING PRODUCT LIST', [i.name for i in products])
                        self.ensure_back(SHOP_CHECK)
                        if products.first_or_none() is None:
                            if refresh and not self.refreshed:
                                raise Refresh
                            break
                        product = products.first_or_none().button
                        timeout.reset()
                        continue

                else:
                    # 检查商品是否可见并点击
                    if self.appear(
                        product, offset=(5, 5), threshold=0.9, interval=0.8, static=False
                    ) and product.match_appear_on(self.device.image, 20):
                        if check_price and not self.check_product_price(product):
                            skip_first_screenshot = True
                            self.device.image = mask_area(self.device.image, product.button)
                            continue
                        if click_timer.reached():
                            self.device.click(product)
                            click_timer.reset()
                            timeout.reset()
                            continue
            except Refresh:
                if not self.refreshed:
                    click_timer = Timer(0.6)
                    products = self.refresh_shop(products, click_timer, skip_first_screenshot)
                    skip_first_screenshot = False
                    if products.first_or_none() is not None:
                        product = products.first_or_none().button
                    self.refreshed = True
                    timeout.reset()

    def check_product_price(self, product: Button) -> bool:
        raise NotImplementedError

    def refresh_shop(self, products: SelectedGrids, click_timer: Timer, skip_first_screenshot=True) -> SelectedGrids:
        raise NotImplementedError

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
