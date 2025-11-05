import re
from functools import cached_property

from module.base.decorator import del_cached_property
from module.base.timer import Timer
from module.base.utils import exec_file
from module.config.delay import next_tuesday_or_friday
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
    def rubbish_shop_core_priority(self) -> SelectedGrids:
        """获取垃圾商店商品的优先级列表"""
        if self.config.RubbishShop_priority is None or not len(self.config.RubbishShop_priority.strip(' ')):
            priority = self.config.RUBBISH_SHOP_CORE_PRIORITY
        else:
            priority = self.config.RubbishShop_priority
        priority = re.sub(r'\s+', '', priority).split('>')
        return SelectedGrids(
            [Product(i, self.config.RUBBISH_SHOP_CORE_PRODUCT.get(i), self.assets.get(i)) for i in priority]
        )

    @cached_property
    def rubbish_shop_bone_priority(self) -> SelectedGrids:
        """获取骨头货币的优先级列表"""
        if self.config.RubbishShop_bonePriority is None or not len(self.config.RubbishShop_bonePriority.strip(' ')):
            priority = self.config.RUBBISH_SHOP_BONE_PRIORITY
        else:
            priority = self.config.RubbishShop_bonePriority
        priority = re.sub(r'\s+', '', priority).split('>')
        return SelectedGrids(
            [Product(i, self.config.RUBBISH_SHOP_BONE_PRODUCT.get(i), self.assets.get(i)) for i in priority]
        )

    @cached_property
    def broken_core(self) -> int:
        """破碎核心数量"""
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
        """
        不再使用
        获取破碎核心的数量
        """
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 点击屏幕以显示货币数量
            if click_timer.reached() and not self.appear(CONFIRM_A, offset=5, static=False):
                self.device.click_minitouch(600, 600)
                click_timer.reset()
                continue

            # 确认货币数量已显示
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

            # 点击屏幕以关闭货币显示
            if click_timer.reached() and self.appear(CONFIRM_A, offset=5, static=False):
                self.device.click_minitouch(100, 100)
                click_timer.reset()
                continue

            # 确认货币显示已关闭
            if not self.appear(CONFIRM_A, offset=5, static=False) and confirm_timer.reached():
                break

        return result

    @cached_property
    def total_cost(self) -> int:
        """
        不再使用
        计算当前优先级商品的总花费
        """
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

        # 商品列表
        shop_priority_list = [
            ('rubbish_shop_core_priority', RUBBISH_SHOP_CHECK),
            ('rubbish_shop_bone_priority', RUBBISH_SHOP_CHECK),
        ]

        for priority_attr, check_btn in shop_priority_list:
            try:
                priority = getattr(self, priority_attr)
                # 检查货币是否足够
                # if self.total_cost > self.currency:
                #     logger.error(f'Not enough currency for {priority_attr}')
                #     self.ensure_back(check_btn)
                #     continue
                self.swipe_and_purchase(priority)
            except NotEnoughMoneyError:
                logger.error(f'Not enough money to buy products in {priority_attr}')
                self.ensure_back(check_btn)
                continue
            except PurchaseTimeTooLong:
                logger.warning(f'Purchase timeout for {priority_attr}')
                continue
            except Exception as e:
                logger.error(e)
                continue
            finally:
                del_cached_property(self, priority_attr)

        self.config.task_delay(target=next_tuesday_or_friday())
