import os
import re
from functools import cached_property

from module.base.decorator import del_cached_property
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.shop.assets import ARENA_SHOP_CHECK, GOTO_ARENA_SHOP
from module.shop.base import NotEnoughMoneyError, Product, ProductQueueIsEmpty, PurchaseTimeTooLong, ShopBase
from module.warehouse_stats.data import load_latest_counts, resolve_csv_path

ARENA_CODE_MANUAL_PRODUCTS = {
    'IRON_CODE': 'd_m_t_r_code_manual',
    'ELECTRIC_CODE': 'z_e_u_s_code_manual',
    'WIND_CODE': 'a_n_m_i_code_manual',
    'WATER_CODE': 'p_s_i_d_code_manual',
    'FIRE_CODE': 'h_s_t_a_code_manual',
}


class ArenaShop(ShopBase):
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

    def run_arena_shop(self):
        try:
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
