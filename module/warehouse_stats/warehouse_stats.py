import os
from typing import Dict, List

from module.base.template import Template
from module.logger import logger
from module.ocr.ocr import Digit
from module.ui.page import page_inventory
from module.ui.ui import UI
from module.warehouse_stats.data import (
    flatten_groups,
    load_item_groups,
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

    # TODO: Adjust count area to actual inventory UI.
    ITEM_COUNT_AREA = (271, 557, 449, 588)

    # TODO: Adjust scroll positions to actual inventory list area.
    SCROLL_START = (360, 920)
    SCROLL_END = (360, 520)

    def run(self):
        logger.hr("Warehouse Stats", 2)
        try:
            self.ui_ensure(page_inventory)

            item_map_path = self.config.WarehouseStats_ItemMapPath
            csv_path = self.config.WarehouseStats_CsvPath
            scroll_times = int(self.config.WarehouseStats_ScrollTimes)

            groups = load_item_groups(item_map_path)
            items = flatten_groups(groups)
            if not items:
                logger.warning("WarehouseStats: No items configured, skip scan.")
                return

            results = self.scan_inventory(items, scroll_times=scroll_times)
            items_to_write = []
            for item in items:
                item_id = item.get("id")
                if item_id in results:
                    item = item.copy()
                    item["count"] = results[item_id]
                    items_to_write.append(item)

            rows = write_inventory_csv(csv_path, items_to_write)
            logger.info(f"WarehouseStats: Saved {rows} rows to {csv_path}")
        except Exception:
            logger.exception("WarehouseStats: Scan failed.")
        finally:
            self.config.task_delay(server_update=True)

    def scan_inventory(self, items: List[dict], scroll_times: int = 5) -> Dict[str, int]:
        templates = self._load_templates(items)
        results: Dict[str, int] = {}

        for page in range(max(scroll_times, 0) + 1):
            self.device.screenshot()

            for item in items:
                item_id = item.get("id")
                if not item_id or item_id in results:
                    continue
                if not item.get("scan", True):
                    continue

                template = templates.get(item_id)
                if template is None:
                    continue

                similarity = float(item.get("similarity", 0.88))
                sim, button = template.match_result(self.device.image, name=item_id)
                if sim < similarity:
                    continue

                self.device.click(button)
                self.device.sleep(0.2)
                results[item_id] = self._read_selected_count()

            if page < scroll_times:
                self._scroll_items_list()

        return results

    def _load_templates(self, items: List[dict]) -> Dict[str, Template]:
        templates: Dict[str, Template] = {}
        for item in items:
            item_id = item.get("id")
            path = item.get("template")
            if not item_id or not path:
                continue
            if not os.path.exists(path):
                logger.warning(f"WarehouseStats: template not found: {path}")
                continue
            templates[item_id] = Template(file=path)
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

    def _scroll_items_list(self):
        """
        Scroll inventory list.
        """
        self.device.swipe(self.SCROLL_START, self.SCROLL_END, method="scroll")
        self.device.sleep(0.3)
