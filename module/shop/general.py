import re
from functools import cached_property

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import _area_offset
from module.handler.assets import CONFIRM_B
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.shop.assets import (
    CANCEL,
    CREDIT,
    GENERAL_SHOP_CHECK,
    GOTO_GENERAL_SHOP,
    GRATIS_B,
    GRATIS_REFRESH,
    ORNAMENT,
    REFRESH,
)
from module.shop.base import NotEnoughMoneyError, Product, ShiftyShopReplaced, ShopBase
from module.ui.assets import SHOP_CHECK


class GeneralShop(ShopBase):
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

    def check_product_price(self, product: Button) -> bool:
        if product.name == ORNAMENT.name:
            return True
        area = _area_offset(product.button, (-50, 0, 50, 250))
        super().__setattr__('_image', self.device.image[area[1] : area[3], area[0] : area[2]])
        return bool(self.credit_or_gratis)

    def refresh_shop(self, products: SelectedGrids, click_timer: Timer, skip_first_screenshot=True) -> SelectedGrids:
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
                self.device.click_xy(x - 80, y)
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
                return self.general_shop_priority

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
                return products

    def run_general_shop(self):
        super().__setattr__('refreshed', False)
        try:
            self.ensure_into_shop(GOTO_GENERAL_SHOP, GENERAL_SHOP_CHECK)
            self.process_purchase(self.general_shop_priority, True, True)
        except ShiftyShopReplaced:
            logger.warning('General shop replaced by shifty shop')
        except NotEnoughMoneyError:
            logger.error('The rest of money is not enough to buy this product')
            self.ensure_back(GENERAL_SHOP_CHECK)
