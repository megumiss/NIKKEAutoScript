import os
import re
from functools import cached_property

from module.base.decorator import del_cached_property
from module.base.timer import Timer
from module.base.utils import _area_offset, exec_file, mask_area
from module.warehouse_stats.data import load_latest_counts, resolve_csv_path
from module.event.assets import SHOP_MONEY_LACK
from module.exception import GameStuckError
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


class ShiftyShopReplaced(Exception):
    pass


ARENA_CODE_MANUAL_PRODUCTS = {
    'IRON_CODE': 'd_m_t_r_code_manual',
    'ELECTRIC_CODE': 'z_e_u_s_code_manual',
    'WIND_CODE': 'a_n_m_i_code_manual',
    'WATER_CODE': 'p_s_i_d_code_manual',
    'FIRE_CODE': 'h_s_t_a_code_manual',
}


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
            self.ensure_sroll_to_top(x1=(505, 700), x2=(505, 1000), count=3, delay=1)
            logger.info(f'[Purchase Start] {product.name}')
            swipe_count = 0

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
                self.ensure_sroll((505, 1000), (505, 700), speed=5, count=1, delay=0.5, method='scroll')
                swipe_count += 1
                if swipe_count > 10:
                    logger.warning('Too many swipes, may game stuck here')
                    raise GameStuckError

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
        priority = self._parse_arena_shop_priority(self.config.ArenaShop_priority)
        return SelectedGrids([Product(i, self.config.ARENA_SHOP_PRODUCT.get(i), self.assets.get(i)) for i in priority])

    def _parse_arena_shop_priority(self, priority: str) -> list:
        priority = re.sub(r'\s+', '', priority or '')
        if not priority:
            return []
        return [item for item in priority.split('>') if item]

    def _read_arena_code_manual_counts(self) -> dict:
        csv_path = resolve_csv_path(self.config.WarehouseStats_CsvPath, config_name=self.config.config_name)
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f'Warehouse stats csv not found: {csv_path}')
        rows = load_latest_counts(csv_path)
        counts = {}
        missing = []
        for product_name, item_id in ARENA_CODE_MANUAL_PRODUCTS.items():
            row = rows.get(item_id)
            if row is None or row.get('count') in (None, ''):
                missing.append(item_id)
                continue
            try:
                counts[product_name] = int(row.get('count'))
            except (TypeError, ValueError):
                missing.append(item_id)
        if missing:
            raise ValueError(f'Warehouse stats code manual counts incomplete: {missing}')
        return counts

    def _auto_fill_arena_code_manual_priority(self):
        if not self.config.ArenaShop_AutoFillCodeManual:
            return

        try:
            threshold = int(self.config.ArenaShop_CodeManualBuyThreshold or 0)
        except (TypeError, ValueError):
            logger.warning(
                f'Arena shop code manual buy threshold is invalid: {self.config.ArenaShop_CodeManualBuyThreshold}'
            )
            return

        if threshold <= 0:
            logger.warning(f'Arena shop code manual buy threshold must be greater than 0: {threshold}')
            return

        try:
            counts = self._read_arena_code_manual_counts()
        except (FileNotFoundError, ValueError) as e:
            logger.warning(f'Arena shop auto code manual skipped: {e}')
            return

        code_products = set(ARENA_CODE_MANUAL_PRODUCTS.keys())
        manual_priority = []
        for index, product_name in enumerate(ARENA_CODE_MANUAL_PRODUCTS.keys()):
            shortage = threshold - counts.get(product_name, 0)
            if shortage > 0:
                manual_priority.append((product_name, shortage, index))

        manual_priority.sort(key=lambda item: (-item[1], item[2]))
        auto_products = [product_name for product_name, _, _ in manual_priority]
        current_priority = self._parse_arena_shop_priority(self.config.ArenaShop_priority)
        user_products = [product_name for product_name in current_priority if product_name not in code_products]
        merged_priority = auto_products + user_products
        new_priority = ' > '.join(merged_priority)

        logger.attr('ARENA CODE MANUAL THRESHOLD', threshold)
        logger.attr('ARENA CODE MANUAL COUNTS', counts)
        logger.attr('ARENA CODE MANUAL AUTO PRIORITY', auto_products)

        if new_priority == (self.config.ArenaShop_priority or ''):
            return

        logger.info(f'Update arena shop priority: {self.config.ArenaShop_priority or ""} -> {new_priority}')
        self.config.ArenaShop_priority = new_priority
        del_cached_property(self, 'arena_shop_priority')

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
            try:
                self.ensure_into_shop(GOTO_GENERAL_SHOP, GENERAL_SHOP_CHECK)
                self.process_purchase(self.general_shop_priority, True, True)
            except ShiftyShopReplaced:
                logger.warning('General shop replaced by shifty shop')
        try:
            if self.config.ArenaShop_enable:
                self._auto_fill_arena_code_manual_priority()
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
        self.config.task_delay(schedule=True)
