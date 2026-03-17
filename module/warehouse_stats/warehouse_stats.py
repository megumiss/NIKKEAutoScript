import os
import re
from typing import Dict, List

import cv2

from module.base.button import Button
from module.base.langs import Langs
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.ui.assets import INVENTORY_CHECK
from module.ui.page import page_inventory
from module.ui.ui import UI
from module.warehouse_stats.assets import *
from module.warehouse_stats.data import (
    flatten_groups,
    load_item_groups,
    resolve_item_asset,
    resolve_item_prefix,
    write_inventory_csv,
)


class WarehouseStats(UI):
    """
    Inventory statistics (rough implementation).

    Flow (simplified):
    1) Open inventory
    2) Match configured item templates and click
    3) OCR item count
    4) Scroll and repeat
    5) Write results to CSV
    """

    def inventory_item_num(self, area):
        model_type = self.config.Optimization_OcrModelType
        ITEM_NUM = Ocr(
            [area],
            text_color=((248, 252, 254)),
            text_color_tolerance=(80, 10, 40),
            name='INVENTORY_ITEM',
            model_type=model_type,
            lang='ch',
        )

        text = ITEM_NUM.ocr(self.device.image)['text']
        # text = ITEM_NUM.ocr(cv2.imread('D:\\PCR\\1773557710499_d.png'))['text']
        match = re.search(rf'{Langs.FAVORITE_ITEM_NUM}[:：]\s*(\d+)', text)
        if match:
            return int(match.group(1))

        return 0

    def run(self):
        logger.hr('Warehouse Stats', 2)
        try:
            self.ui_ensure(page_inventory)
            # items = TEMPLATE_ITEM_NUM_PREFIX.match_multi(self.device.image, threshold=0.95, name='ITEM_NUM_PREFIX')

            # 读取配置与物品映射
            item_map_path = self.config.WarehouseStats_ItemMapPath
            csv_path = self.config.WarehouseStats_CsvPath

            groups = load_item_groups(item_map_path)
            items = flatten_groups(groups)
            if not items:
                logger.warning('WarehouseStats: No items configured, skip scan.')
                return

            # 扫描背包并写入 CSV
            results = self.scan_inventory(items)
            items_to_write = []
            for item in items:
                item_id = item.get('id')
                if item_id in results:
                    item = item.copy()
                    item['count'] = results[item_id]
                    items_to_write.append(item)

            rows = write_inventory_csv(csv_path, items_to_write)
            logger.info(f'WarehouseStats: Saved {rows} rows to {csv_path}')
        except Exception:
            logger.exception('WarehouseStats: Scan failed.')
        finally:
            self.config.task_delay(server_update=True)

    def scan_inventory(self, items: List[dict]) -> Dict[str, int]:
        templates = self._load_templates(items)
        results: Dict[str, int] = {}

        # 仅保留需要扫描且模板可用的物品，减少无意义识别
        pending: Dict[str, Button] = {}
        for item in items:
            item_id = item.get('id')
            if not item_id or not item.get('scan', True):
                continue
            button = templates.get(item_id)
            if button is None:
                continue
            pending[item_id] = button

        while 1:
            # 每轮先截图，再在当前页面进行匹配
            self.device.screenshot()
            # 识别结束
            if not pending:
                break

            remaining: Dict[str, Button] = {}
            for item_id, button in pending.items():
                # 已经识别过，跳过
                if results.get(item_id):
                    continue
                # 没有识别到物品，跳过
                if not self.appear(button, offset=10, static=False):
                    remaining[item_id] = button
                    continue
                else:
                    # 识别到物品，打开详情
                    logger.info(f'WarehouseStats: Found item: {item_id}')
                    while 1:
                        self.device.screenshot()
                        if self.appear(INVENTORY_ITEM_CLOSE, offset=10, static=False):
                            break
                        if self.appear_then_click(button, offset=10, interval=1, static=False):
                            continue
                    # 识别范围
                    owner_loc = self.appear_location(INVENTORY_ITEM_CLOSE, offset=10, static=False)
                    # 识别
                    results[item_id] = self.inventory_item_num(
                        (720 - owner_loc[0], owner_loc[1] + 200, owner_loc[0], owner_loc[1] + 270)
                    )
                    # 关闭详情
                    while 1:
                        self.device.screenshot()
                        if self.appear_then_click(INVENTORY_ITEM_CLOSE, offset=10, interval=1, static=False):
                            continue
                        if self.appear(INVENTORY_CHECK, offset=10):
                            break

            # 没有要识别的物品了
            pending = remaining
            if not pending:
                break

            # 判断是否已到最下方
            if self.appear(INVENTORY_BOTTOM_CHECK, offset=10):
                logger.info('WarehouseStats: Reached bottom of inventory list.')
                break

            # 滑动
            self.ensure_sroll((450, 950), (450, 250), speed=5, count=1, delay=0.3, method='scroll')

        return results

    def _load_templates(self, items: List[dict]) -> Dict[str, Button]:
        templates: Dict[str, Button] = {}
        for item in items:
            item_id = item.get('id')
            if not item_id:
                continue
            # name 为模板前缀，直接从 assets 取 {name}_TEMPLATE
            prefix = resolve_item_prefix(item)
            asset = resolve_item_asset(prefix, 'TEMPLATE')
            path = getattr(asset, 'file', '') if asset else ''
            if not path:
                logger.warning(f'WarehouseStats: template asset not found: {prefix}_TEMPLATE')
                continue
            if not os.path.exists(path):
                logger.warning(f'WarehouseStats: template file not found: {path}')
                continue
            templates[item_id] = asset
        return templates

    def _read_selected_count(self) -> int:
        """
        OCR count from selected item panel.
        This is a rough placeholder; adjust ITEM_COUNT_AREA for accuracy.
        """
        self.device.screenshot()
        ocr = Digit(self.ITEM_COUNT_AREA, model_type=self.config.Optimization_OcrModelType)
        result = ocr.ocr(self.device.image)
        try:
            return int(result)
        except Exception:
            return 0
