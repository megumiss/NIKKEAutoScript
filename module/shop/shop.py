from module.base.decorator import del_cached_property
from module.shop.arena import ARENA_CODE_MANUAL_PRODUCTS, ArenaShop
from module.shop.base import (
    NotEnoughMoneyError,
    Product,
    ProductQueueIsEmpty,
    PurchaseTimeTooLong,
    Refresh,
    ShiftyShopReplaced,
    ShopBase,
)
from module.shop.body_label import BodyLabelShop
from module.shop.general import GeneralShop
from module.ui.page import page_shop

__all__ = [
    'ARENA_CODE_MANUAL_PRODUCTS',
    'NotEnoughMoneyError',
    'Product',
    'ProductQueueIsEmpty',
    'PurchaseTimeTooLong',
    'Refresh',
    'ShiftyShopReplaced',
    'ShopBase',
    'Shop',
]


class Shop(GeneralShop, ArenaShop, BodyLabelShop):
    def run(self):
        self.ui_ensure(page_shop)
        if self.config.GeneralShop_enable:
            self.run_general_shop()
        if self.config.BodyLabelShop_enable:
            self.run_body_label_shop()
        if self.config.ArenaShop_enable:
            self.run_arena_shop()

        del_cached_property(self, 'general_shop_priority')
        del_cached_property(self, 'arena_shop_priority')
        self.config.task_delay(schedule=True)
