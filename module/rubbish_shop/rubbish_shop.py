import re
from datetime import datetime, timedelta, timezone
from functools import cached_property

from module.base.decorator import del_cached_property
from module.base.timer import Timer
from module.base.utils import exec_file
from module.handler.assets import CONFIRM_A
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.ocr.ocr import Digit
from module.rubbish_shop.assets import *
from module.shop.shop import NotEnoughMoneyError, Product, PurchaseTimeTooLong, ShopBase
from module.ui.page import page_shop


class RubbishShop(ShopBase):
    @cached_property
    def assets(self) -> dict:
        return exec_file('./module/rubbish_shop/assets.py')

    @cached_property
    def rubbish_shop_priority(self) -> SelectedGrids:
        if self.config.RubbishShop_priority is None or not len(self.config.RubbishShop_priority.strip(' ')):
            priority = self.config.RUBBISH_SHOP_PRIORITY
        else:
            priority = self.config.RubbishShop_priority
        priority = re.sub(r'\s+', '', priority).split('>')
        return SelectedGrids(
            [Product(i, self.config.RUBBISH_SHOP_PRODUCT.get(i), self.assets.get(i)) for i in priority]
        )

    @cached_property
    def next_tuesday(self) -> datetime:
        local_now = datetime.now()
        remain = (1 - local_now.weekday()) % 7
        remain = remain + 7 if remain == 0 else remain
        diff = datetime.now(timezone.utc).astimezone().utcoffset() - timedelta(hours=8)
        return local_now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=remain) + diff

    @cached_property
    def broken_core(self) -> int:
        model_type = self.config.Optimization_OcrModelType
        BROKEN_CORE = Digit(
            [BROKEN_CORE_NUM.area],
            name='BROKEN_CORE',
            model_type=model_type,
            lang='num',
        )
        return int(BROKEN_CORE.ocr(self.device.image)['text'])

    @cached_property
    def currency(self) -> int:
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and not self.appear(CONFIRM_A, offset=5, static=False):
                self.device.click_minitouch(600, 600)
                click_timer.reset()
                continue

            if self.appear(CONFIRM_A, offset=5, static=False) and confirm_timer.reached():
                break

        result = self.broken_core
        logger.attr('Broken Core nums: ', result)
        skip_first_screenshot = True
        confirm_timer.reset()
        click_timer.reset()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear(CONFIRM_A, offset=5, static=False):
                self.device.click_minitouch(100, 100)
                click_timer.reset()
                continue

            if not self.appear(CONFIRM_A, offset=5, static=False) and confirm_timer.reached():
                break

        return result

    @cached_property
    def total_cost(self) -> int:
        cost = sum(
            [
                self.config.RUBBISH_SHOP_PRODUCT_COST.get(i)
                for i in self.config.RUBBISH_SHOP_PRODUCT_COST.keys()
                if i in list(map(lambda x: x.name, self.rubbish_shop_priority.grids))
            ]
        )
        logger.attr('Total Cost', cost)
        return cost

    def run(self):
        self.ui_ensure(page_shop)
        self.ensure_into_shop(GOTO_RUBBISH_SHOP, RUBBISH_SHOP_CHECK)
        try:
            if self.total_cost > self.currency:
                raise NotEnoughMoneyError
            self.purchase1(self.rubbish_shop_priority, skip_first_screenshot=True)
        except NotEnoughMoneyError:
            logger.error('The rest of money is not enough to buy these products')
            self.ensure_back(RUBBISH_SHOP_CHECK)
        except PurchaseTimeTooLong:
            pass
        except Exception as e:
            logger.error(e)
        del_cached_property(self, 'rubbish_shop_priority')
        self.config.task_delay(target=self.next_tuesday)
