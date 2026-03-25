import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from module.base.button import Button
from module.base.langs import Langs
from module.base.utils import crop
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.ui.assets import INVENTORY_CHECK
from module.ui.page import page_inventory
from module.ui.ui import UI
from module.warehouse_stats.assets import *
from module.warehouse_stats.data import (
    SCAN_METHOD_DIRECT,
    SCAN_METHOD_OPEN_DETAIL,
    flatten_groups,
    load_item_groups,
    resolve_item_asset,
    resolve_item_prefix,
    write_inventory_csv,
)


class WarehouseStats(UI):
    """
    仓库物品统计。

    流程：
    1) 打开仓库页
    2) 按物品配置选择“固定网格直读”或“点击详情 OCR”识别
    3) 滚动翻页重复扫描
    4) 写入 CSV
    """

    # ===== 720x1280 下的固定分割参数（5列 x 7行）=====
    # 每页可见列数
    GRID_COLS = 5
    # 每页可见行数
    GRID_ROWS = 7
    # 第一个格子（第一行第一列）左上角 X
    GRID_START_X = 118
    # 第一个格子（第一行第一列）左上角 Y
    GRID_START_Y = 291
    # 单个格子宽度
    GRID_CELL_WIDTH = 91
    # 单个格子高度
    GRID_CELL_HEIGHT = 94
    # 相邻两列格子左上角 X 间距（含空隙）
    GRID_STEP_X = 115
    # 相邻两行格子左上角 Y 间距（含空隙）
    GRID_STEP_Y = 115

    # Grid valid viewport bounds (full cell must be inside this area)
    GRID_VIEW_X1 = GRID_START_X
    GRID_VIEW_Y1 = GRID_START_Y
    GRID_VIEW_X2 = GRID_START_X + (GRID_COLS - 1) * GRID_STEP_X + GRID_CELL_WIDTH + 10
    GRID_VIEW_Y2 = GRID_START_Y + (GRID_ROWS - 1) * GRID_STEP_Y + GRID_CELL_HEIGHT + 10

    # 数量前缀模板匹配阈值（越大越严格）
    GRID_PREFIX_SIMILARITY = 0.78
    # 物品模板匹配阈值（越大越严格）
    GRID_ITEM_SIMILARITY = 0.86
    # 翻页后锚点匹配阈值
    GRID_ANCHOR_THRESHOLD = 0.82

    # 调试图输出目录（会自动创建）
    DEBUG_IMAGE_DIR = './data/warehouse_stats/debug'
    # 是否保存调试图
    DEBUG_SAVE_IMAGE = True

    def inventory_item_num_direct(
        self, image, area: Tuple[int, int, int, int], item_name: Optional[str] = None
    ) -> Optional[int]:
        # 直读识别：格子局部数字 OCR
        self_image = self.device.image
        try:
            self.device.image = image
            model_type = self.config.Optimization_OcrModelType
            item_num = Ocr(
                [area],
                text_color=(248, 252, 254),
                text_color_tolerance=(90, 10, 40),
                text_color_preprocess=(0.7, 20, 0.6),
                name=str(item_name or 'INVENTORY_ITEM'),
                model_type=model_type,
                lang='ch',
            )
            text = item_num.ocr(self.device.image).get('text', '')
            has_suffix_k, has_suffix_m = self._match_num_suffix_flags(image=image, area=area)
            value = self._parse_direct_count_text(text=text, has_suffix_k=has_suffix_k, has_suffix_m=has_suffix_m)
            if value is not None:
                return value
            return self._parse_direct_count_by_digit_templates(
                image=image,
                area=area,
                has_suffix_k=has_suffix_k,
                has_suffix_m=has_suffix_m,
                item_name=item_name,
            )
        finally:
            self.device.image = self_image

    def _parse_direct_count_text(
        self,
        text: str,
        has_suffix_k: bool = False,
        has_suffix_m: bool = False,
    ) -> Optional[int]:
        # Supports x1 / x444 / x124K / x1.2M
        if not text:
            return None
        normalized = self._normalize_ocr_text_common(text).replace(' ', '').replace(',', '').strip()

        match = re.search(r'[xX]\s*([0-9]+(?:\.[0-9]+)?)\s*([kKmM]?)', normalized)
        if match is None:
            match = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*([kKmM]?)', normalized)
        if match is None:
            return None

        unit = match.group(2)
        if has_suffix_m:
            unit = 'M'
        elif has_suffix_k:
            unit = 'K'
        return self._parse_scaled_number(match.group(1), unit)

    def _parse_direct_count_by_digit_templates(
        self,
        image,
        area: Tuple[int, int, int, int],
        has_suffix_k: Optional[bool] = None,
        has_suffix_m: Optional[bool] = None,
        item_name: Optional[str] = None,
    ) -> Optional[int]:
        """
        Fallback for direct OCR:
        Match TEMPLATE_ITEM_NUM_0~9 in number area, sort by x, and concatenate digits.
        """
        area_image = crop(image, area)
        templates = [
            TEMPLATE_ITEM_NUM_0,
            TEMPLATE_ITEM_NUM_1,
            TEMPLATE_ITEM_NUM_2,
            TEMPLATE_ITEM_NUM_3,
            TEMPLATE_ITEM_NUM_4,
            TEMPLATE_ITEM_NUM_5,
            TEMPLATE_ITEM_NUM_6,
            TEMPLATE_ITEM_NUM_7,
            TEMPLATE_ITEM_NUM_8,
            TEMPLATE_ITEM_NUM_9,
        ]

        matches: List[Tuple[int, int, int, int]] = []
        for digit, template in enumerate(templates):
            try:
                points = template.match_multi(
                    area_image,
                    similarity=0.80,
                    threshold=3,
                    name=f'{item_name or "INVENTORY_ITEM"}_NUM_{digit}',
                )
            except Exception:
                continue
            for button in points:
                x1, y1, _, _ = button.area
                matches.append((x1, y1, digit, len(matches)))

        if not matches:
            return None

        matches.sort(key=lambda x: (x[0], x[1], x[3]))
        digits: List[str] = []
        last_x: Optional[int] = None
        for x1, _, digit, _ in matches:
            if last_x is not None and abs(x1 - last_x) <= 3:
                continue
            digits.append(str(digit))
            last_x = x1

        if not digits:
            return None

        if has_suffix_k is None or has_suffix_m is None:
            detected_k, detected_m = self._match_num_suffix_flags(image=image, area=area)
            if has_suffix_k is None:
                has_suffix_k = detected_k
            if has_suffix_m is None:
                has_suffix_m = detected_m

        value = int(''.join(digits))
        if has_suffix_m:
            value *= 1000000
        elif has_suffix_k:
            value *= 1000
        logger.info(
            f'WarehouseStats: [DirectTemplateFallback] item={item_name or "INVENTORY_ITEM"}, '
            f'digits={"".join(digits)}, value={value}'
        )
        return value

    def _match_num_suffix_flags(self, image, area: Tuple[int, int, int, int]) -> Tuple[bool, bool]:
        has_suffix_k = self._match_num_suffix_k(image=image, area=area)
        has_suffix_m = self._match_num_suffix_m(image=image, area=area)
        if has_suffix_k and has_suffix_m:
            logger.debug('WarehouseStats: both K and M suffix matched, prefer M multiplier.')
        return has_suffix_k, has_suffix_m

    def _match_num_suffix(self, image, area: Tuple[int, int, int, int], button: Optional[Button]) -> bool:
        if button is None:
            return False

        area_image = crop(image, area)
        previous_image = self.device.image
        self.device.image = area_image
        try:
            return self.appear(button, offset=10, threshold=0.6, static=False)
        finally:
            self.device.image = previous_image

    def _match_num_suffix_k(self, image, area: Tuple[int, int, int, int]) -> bool:
        return self._match_num_suffix(image=image, area=area, button=ITEM_NUM_SUFFIX_K)

    def _match_num_suffix_m(self, image, area: Tuple[int, int, int, int]) -> bool:
        return self._match_num_suffix(image=image, area=area, button=ITEM_NUM_SUFFIX_M)

    def _normalize_ocr_text_common(self, text: str) -> str:
        normalized = str(text or '').replace('\n', '')
        normalized = (
            normalized.replace('\uff08', '(')
            .replace('\uff09', ')')
            .replace('\uff1a', ':')
            .replace('\uff1b', ':')
            .replace('\uff0c', ',')
            .replace('\u3002', '.')
            .replace('\u3001', ',')
            .replace('\u00d7', 'x')
            .replace('\uff58', 'x')
            .replace('\uff38', 'X')
            .replace('\uff10', '0')
            .replace('\uff11', '1')
            .replace('\uff12', '2')
            .replace('\uff13', '3')
            .replace('\uff14', '4')
            .replace('\uff15', '5')
            .replace('\uff16', '6')
            .replace('\uff17', '7')
            .replace('\uff18', '8')
            .replace('\uff19', '9')
        )

        trans_table = str.maketrans(
            {
                'o': '0',
                'O': '0',
                'Q': '0',
                'D': '0',
                'I': '1',
                'l': '1',
                '|': '1',
                '!': '1',
                'i': '1',
                'Z': '2',
                'z': '2',
                'S': '5',
                's': '5',
                'B': '8',
                'k': 'K',
                'm': 'M',
            }
        )
        return normalized.translate(trans_table)

    def _ocr_num_area(self, image, area: Tuple[int, int, int, int], item_name: Optional[str] = None) -> Optional[int]:
        # Backward compatibility wrapper
        return self.inventory_item_num_direct(image=image, area=area, item_name=item_name)

    def inventory_item_num_detail(self, item_id: str, area, item_name: Optional[str] = None) -> Dict[str, int]:
        # Detail OCR: read the count text from item detail panel.
        model_type = self.config.Optimization_OcrModelType
        item_num = Ocr(
            [area],
            text_color=(151, 151, 151),
            # text_color_tolerance=(80, 10, 40),
            # text_color_preprocess=(0.7, 20, 0.6),
            name=str(item_name or item_id),
            model_type=model_type,
            lang='ch',
        )

        text = item_num.ocr(self.device.image).get('text', '')
        text = self._normalize_ocr_text_common(text)
        text = self._process_detail_text_by_item(item_id=item_id, text=text)
        return self._parse_detail_counts_by_item(item_id=item_id, text=text)

    def _process_detail_text_by_item(self, item_id: str, text: str) -> str:
        # Item-specific text preprocessing hook.
        normalized = self._normalize_ocr_text_common(text)
        if item_id.endswith('gem'):
            return normalized
        if item_id.endswith('_MOLD') or item_id == 'MOLD':
            return normalized
        return normalized

    def _parse_detail_counts_by_item(self, item_id: str, text: str) -> Dict[str, int]:
        # 1) GEM 家族：总数 + 免费 + 付费
        if item_id.endswith('gem'):
            free = self._extract_named_value(text, '免费')
            paid = self._extract_named_value(text, '付费')
            # 页面字段名 OCR 丢失时，回退到按出现顺序取数字：
            # 目标文本通常为“持有数（免费：xxx，付费：yyy）”
            numbers = self._extract_all_numbers(text)
            if free is None and len(numbers) >= 1:
                free = numbers[0]
            if paid is None and len(numbers) >= 2:
                paid = numbers[1]

            # GEM 总数由免费 + 付费组成
            total: Optional[int] = None
            if free is not None and paid is not None:
                total = free + paid

            total_id, free_id, paid_id = ('gem', 'free_gem', 'paid_gem')
            result: Dict[str, int] = {}
            if total is not None:
                result[total_id] = total
            if free is not None:
                result[free_id] = free
            if paid is not None:
                result[paid_id] = paid
            return result

        # 2) MOLD：只取 "/" 前面的当前数量
        if item_id.endswith('_MOLD') or item_id == 'MOLD':
            match = re.search(r'[:]\s*([0-9]+)\s*/\s*[0-9]+', text)
            if match is None:
                match = re.search(r'([0-9]+)\s*/\s*[0-9]+', text)
            if match:
                return {item_id: int(match.group(1))}
            return {}

        # 3) 默认详情：取“拥有数: 数量”
        value = self._extract_default_detail_value(text)
        if value is None:
            return {}
        return {item_id: value}

    def _extract_named_value(self, text: str, label: str) -> Optional[int]:
        match = re.search(rf'{label}[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*([kKmM]?)', text)
        if match:
            return self._parse_scaled_number(match.group(1), match.group(2))
        return None

    def _extract_all_numbers(self, text: str) -> List[int]:
        values: List[int] = []
        for value_text, unit_text in re.findall(r'([0-9]+(?:\.[0-9]+)?)\s*([kKmM]?)', text):
            value = self._parse_scaled_number(value_text, unit_text)
            if value is not None:
                values.append(value)
        return values

    def _parse_scaled_number(self, value_text: str, unit_text: str = '') -> Optional[int]:
        try:
            value = float(value_text)
        except (TypeError, ValueError):
            return None

        unit = str(unit_text or '').upper()
        if unit == 'K':
            value *= 1000
        elif unit == 'M':
            value *= 1000000
        return int(round(value))

    def _extract_default_detail_value(self, text: str) -> Optional[int]:
        pattern = f'{Langs.FAVORITE_ITEM_NUM}[:\\uFF1A;；]\\s*([0-9]+(?:\\.[0-9]+)?)\\s*([kKmM]?)'
        match = re.search(pattern, text)
        if match:
            return self._parse_scaled_number(match.group(1), match.group(2))

        # fallback: “拥有数”关键字丢失时，取第一个数字
        fallback = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*([kKmM]?)', text)
        if fallback:
            return self._parse_scaled_number(fallback.group(1), fallback.group(2))
        return None

    def run(self):
        logger.hr('Warehouse Stats', 2)
        try:
            # 确保在仓库页
            # self.ui_ensure(page_inventory)
            item_map_path = self.config.WarehouseStats_ItemMapPath
            csv_path = self.config.WarehouseStats_CsvPath

            # 读取配置中的待识别物品清单
            groups = load_item_groups(item_map_path)
            items = flatten_groups(groups)
            if not items:
                logger.warning('WarehouseStats: No items configured, skip scan.')
                return

            # 扫描并回填 count
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
        item_name_map: Dict[str, str] = {}

        # Scan routing:
        # Keep UI group display unchanged, but allow scanner to process items by scan_page.
        pages: List[str] = []
        page_pending_direct: Dict[str, Dict[str, Button]] = {}
        page_pending_detail: Dict[str, Dict[str, Button]] = {}
        for item in items:
            item_id = item.get('id')
            if not item_id or not item.get('scan', True):
                continue
            item_name_map[item_id] = str(item.get('display_name') or item.get('name') or item_id)
            button = templates.get(item_id)
            if button is None:
                continue

            page_key = str(item.get('scan_page', 'default')).strip() or 'default'
            if page_key not in page_pending_direct:
                page_pending_direct[page_key] = {}
                page_pending_detail[page_key] = {}
                pages.append(page_key)

            scan_method = item.get('scan_method', SCAN_METHOD_DIRECT)
            if scan_method == SCAN_METHOD_OPEN_DETAIL:
                page_pending_detail[page_key][item_id] = button
            else:
                page_pending_direct[page_key][item_id] = button

        if not pages:
            return results

        # Process each scan page independently.
        for page_key in pages:
            pending_direct = page_pending_direct.get(page_key, {})
            pending_detail = page_pending_detail.get(page_key, {})
            if not pending_direct and not pending_detail:
                continue

            if not self._switch_inventory_scan_page(page_key):
                logger.warning(f'WarehouseStats: Skip scan_page="{page_key}" due to switch failure.')
                continue

            logger.info(
                f'WarehouseStats: scanning page="{page_key}", '
                f'direct={len(pending_direct)}, detail={len(pending_detail)}'
            )
            # screenshot -> split -> direct first -> detail then -> scroll
            self._scan_inventory_by_page(
                pending_direct=pending_direct,
                pending_detail=pending_detail,
                results=results,
                item_name_map=item_name_map,
            )

        return results

    def _switch_inventory_scan_page(self, page_key: str) -> bool:
        """
        Switch inventory page before scanning.
        - default/main: no-op
        - other page key: try matching a button constant by convention.
          Candidates: INVENTORY_PAGE_{KEY}, INVENTORY_{KEY}, {KEY}
        """
        key = str(page_key or '').strip()
        if key == '' or key.lower() in ('default', 'main'):
            return True

        custom_switch = getattr(self, f'_switch_inventory_scan_page_{key.lower()}', None)
        if callable(custom_switch):
            try:
                return bool(custom_switch())
            except Exception:
                logger.exception(f'WarehouseStats: custom switch failed for scan_page="{key}".')
                return False

        upper = key.upper()
        candidate_names = (
            f'INVENTORY_PAGE_{upper}',
            f'INVENTORY_{upper}',
            upper,
        )

        button = None
        for name in candidate_names:
            obj = globals().get(name)
            if isinstance(obj, Button):
                button = obj
                break

        if button is None:
            logger.warning(
                f'WarehouseStats: scan_page="{key}" has no switch button '
                f'(tried: {", ".join(candidate_names)}).'
            )
            return False

        for _ in range(3):
            self.device.screenshot()
            self.appear_then_click(button, offset=10, interval=1, static=False)
        self.device.sleep((0.6, 0.9))
        return True

    def _scan_inventory_by_page(
        self,
        pending_direct: Dict[str, Button],
        pending_detail: Dict[str, Button],
        results: Dict[str, int],
        item_name_map: Dict[str, str],
    ) -> None:
        if not hasattr(self, '_debug_run_id'):
            self._debug_run_id = datetime.now().strftime('%Y%m%d_%H%M%S')

        max_pages = max(20, int(getattr(self.config, 'WarehouseStats_ScrollTimes', 5)) * 20)
        page_index = 0
        grid_origin = (self.GRID_START_X, self.GRID_START_Y)
        anchor_button: Optional[Button] = None

        while page_index < max_pages and (pending_direct or pending_detail):
            self.device.screenshot()
            image = self.device.image.copy()
            drop_anchor_row = False

            # 翻页后，先找锚点位置，再重算当前页第一行起点
            if anchor_button is not None:
                if self.appear(anchor_button, offset=10, threshold=self.GRID_ANCHOR_THRESHOLD, static=False):
                    x1, y1, _, _ = anchor_button.button
                    grid_origin = (x1, y1)
                    drop_anchor_row = True
                    logger.info(f'WarehouseStats: Grid anchor aligned at ({x1}, {y1})')
                else:
                    logger.warning('WarehouseStats: Grid anchor not found, fallback to fixed grid origin.')
                    grid_origin = (self.GRID_START_X, self.GRID_START_Y)

            cells = self._split_inventory_cells(
                image=image,
                origin=grid_origin,
                page_index=page_index,
                drop_anchor_row=drop_anchor_row,
            )

            pending_all: Dict[str, Button] = {}
            pending_all.update(pending_direct)
            pending_all.update(pending_detail)

            detail_targets: List[Tuple[str, Tuple[int, int, int, int]]] = []
            detail_seen = set()
            page_results: Dict[str, int] = {}

            # 第一步：本页先 direct 识别；detail 只登记目标，不发生点击。
            for cell in cells:
                if not pending_all:
                    break

                item_id = self._match_pending_item_in_cell(cell_image=cell['cell'], pending=pending_all)
                if not item_id:
                    continue

                if item_id in pending_direct:
                    prefix_xy = self._match_num_prefix_xy(masked_cell_image=cell['masked'])
                    if prefix_xy is None:
                        continue

                    num_area = self._build_num_area(prefix_xy=prefix_xy, cell_area=cell['area'], image=image)
                    if num_area is None:
                        continue

                    count = self.inventory_item_num_direct(
                        image=image,
                        area=num_area,
                        item_name=item_name_map.get(item_id, item_id),
                    )
                    if count is None:
                        continue

                    results[item_id] = count
                    page_results[item_id] = count
                    pending_direct.pop(item_id, None)
                    pending_all.pop(item_id, None)
                    item_name = item_name_map.get(item_id, item_id)
                    logger.info(
                        f'WarehouseStats: [Direct] Found item={item_id}({item_name}), count={count}, area={num_area}'
                    )
                    continue

                if item_id in pending_detail and item_id not in detail_seen:
                    detail_seen.add(item_id)
                    detail_targets.append((item_id, cell['area']))

            # 第二步：本页再按顺序点击 detail 物品并识别详情。
            for item_id, area in detail_targets:
                if item_id not in pending_detail:
                    continue

                cell_button = Button(area=area, color=(0, 0, 0), button=area, name=f'INVENTORY_CELL_{item_id}')
                detail_counts = self._detail_step_scan_counts(
                    item_id=item_id,
                    item_name=item_name_map.get(item_id, item_id),
                    primary_button=cell_button,
                    fallback_button=pending_detail[item_id],
                )
                if not detail_counts:
                    continue

                for detail_item_id, detail_count in detail_counts.items():
                    results[detail_item_id] = detail_count
                    page_results[detail_item_id] = detail_count
                    pending_detail.pop(detail_item_id, None)
                    pending_direct.pop(detail_item_id, None)

                detail_log = ', '.join(
                    f'{detail_item_id}({item_name_map.get(detail_item_id, detail_item_id)})={detail_count}'
                    for detail_item_id, detail_count in detail_counts.items()
                )
                logger.info(f'WarehouseStats: [Detail] Found item={item_id}, counts={detail_log}')

            self._log_page_item_counts(page_index=page_index, page_counts=page_results, item_name_map=item_name_map)

            if not pending_direct and not pending_detail:
                break

            if self.appear(INVENTORY_BOTTOM_CHECK, offset=10) or self.appear(INVENTORY_BOTTOM_CHECK_2, offset=10):
                logger.info('WarehouseStats: Reached bottom of inventory list.')
                break

            if not cells:
                logger.warning('WarehouseStats: No valid grid cells on current page, skip anchor update once.')
                anchor_button = None
                self.ensure_sroll((450, 950), (450, 400), speed=5, count=1, delay=1, method='scroll')
                page_index += 1
                continue

            anchor_cell = self._get_anchor_cell(cells)
            anchor_button = self._build_anchor_button(image=image, area=anchor_cell['area'])

            # 本页完成后再向下滚动一页
            self.ensure_sroll((450, 950), (450, 400), speed=5, count=1, delay=1, method='scroll')
            page_index += 1

        if pending_direct:
            logger.info(f'WarehouseStats: Direct scan incomplete. pending={len(pending_direct)}')
        if pending_detail:
            logger.info(f'WarehouseStats: Open-detail scan incomplete. pending={len(pending_detail)}')

    def _detail_step_enter_detail(self, button: Button, max_retry: int = 6) -> bool:
        for _ in range(max_retry):
            self.device.screenshot()
            if self.appear(INVENTORY_ITEM_CLOSE, offset=10, static=False):
                return True

            template_file = getattr(button, 'file', None)
            if template_file:
                self.appear_then_click(button, offset=10, interval=1, static=False)
            else:
                # Dynamic cell button has no template file; click directly by area center.
                self.device.click(button)
        return False

    def _detail_step_get_num_area(self) -> Optional[Tuple[int, int, int, int]]:
        self.device.screenshot()
        owner_loc = self.appear_location(INVENTORY_ITEM_CLOSE, offset=10, static=False)
        if owner_loc is None:
            return None
        return 720 - owner_loc[0], owner_loc[1] + 240, owner_loc[0], owner_loc[1] + 270

    def _detail_step_leave_detail(self, max_retry: int = 6) -> bool:
        for _ in range(max_retry):
            self.device.screenshot()
            if self.appear_then_click(INVENTORY_ITEM_CLOSE, offset=10, interval=1, static=False):
                continue
            if self.appear(INVENTORY_CHECK, offset=10):
                return True
        return False

    def _detail_step_scan_counts(
        self, item_id: str, item_name: Optional[str], primary_button: Button, fallback_button: Optional[Button] = None
    ) -> Dict[str, int]:
        entered = self._detail_step_enter_detail(primary_button)
        if not entered and fallback_button is not None:
            entered = self._detail_step_enter_detail(fallback_button)
        if not entered:
            return {}

        try:
            area = self._detail_step_get_num_area()
            if area is None:
                return {}
            return self.inventory_item_num_detail(item_id=item_id, area=area, item_name=item_name)
        finally:
            self._detail_step_leave_detail()

    def _split_inventory_cells(
        self,
        image,
        origin: Tuple[int, int],
        page_index: int = 0,
        drop_anchor_row: bool = False,
    ) -> List[dict]:
        """
        将当前页按固定网格分割为多个待识别格子：
        1) cell: 仅格子裁剪图（用于匹配物品模板）
        2) masked: 保留该格子，其余区域全黑（用于匹配数量前缀模板）
        """
        x0, y0 = origin
        cells: List[dict] = []
        h, w = image.shape[:2]

        for row in range(self.GRID_ROWS):
            for col in range(self.GRID_COLS):
                x1 = x0 + col * self.GRID_STEP_X
                y1 = y0 + row * self.GRID_STEP_Y
                x2 = x1 + self.GRID_CELL_WIDTH
                y2 = y1 + self.GRID_CELL_HEIGHT

                area = (x1, y1, x2, y2)
                if drop_anchor_row and row == 0:
                    continue
                if not self._is_cell_fully_visible(area):
                    continue

                cell_image = crop(image, area)

                # 构造“只保留当前格子，其余位置涂黑”的整图
                masked = np.zeros_like(image)
                ix1, iy1 = max(0, x1), max(0, y1)
                ix2, iy2 = min(w, x2), min(h, y2)
                if ix1 < ix2 and iy1 < iy2:
                    masked[iy1:iy2, ix1:ix2] = image[iy1:iy2, ix1:ix2]

                cells.append(
                    {
                        'row': row,
                        'col': col,
                        'area': area,
                        'cell': cell_image,
                        'masked': masked,
                    }
                )

                # 调试图：保存分割后的格子图与涂黑后的整图
                self._save_debug_image(
                    f'page_{page_index:03d}_r{row}_c{col}_cell.png',
                    cell_image,
                )
                self._save_debug_image(
                    f'page_{page_index:03d}_r{row}_c{col}_masked.png',
                    masked,
                )

        return cells

    def _is_cell_fully_visible(self, area: Tuple[int, int, int, int]) -> bool:
        x1, y1, x2, y2 = area
        return (
            x1 >= self.GRID_VIEW_X1 and y1 >= self.GRID_VIEW_Y1 and x2 <= self.GRID_VIEW_X2 and y2 <= self.GRID_VIEW_Y2
        )

    def _get_anchor_cell(self, cells: List[dict]) -> dict:
        # 锚点固定选用：最后一行第一个格子
        if not cells:
            raise ValueError('WarehouseStats: cannot build anchor from empty cell list.')

        target_row = max(cell['row'] for cell in cells)
        row_cells = [cell for cell in cells if cell['row'] == target_row]
        first_col_cell = [cell for cell in row_cells if cell['col'] == 0]
        if first_col_cell:
            return first_col_cell[0]
        return sorted(row_cells, key=lambda cell: cell['col'])[0]

    def _build_anchor_button(self, image, area: Tuple[int, int, int, int]) -> Button:
        # 用当前页锚点格子的实际图像动态构造 Button，供下一页 appear() 定位
        anchor = Button(area=area, color=(0, 0, 0), button=area, name='INVENTORY_GRID_ANCHOR')
        anchor.load_color(image)
        anchor._match_init = True
        anchor._button_offset = area
        return anchor

    def _match_pending_item_in_cell(self, cell_image, pending: Dict[str, Button]) -> Optional[str]:
        # 在单个格子内，从 pending 里选相似度最高的物品模板
        best_item_id = None
        best_similarity = 0.0

        for item_id, button in pending.items():
            try:
                button.ensure_template()
                sim_map = cv2.matchTemplate(button.image, cell_image, cv2.TM_CCOEFF_NORMED)
                _, similarity, _, _ = cv2.minMaxLoc(sim_map)
            except Exception:
                continue

            if similarity > best_similarity:
                best_similarity = similarity
                best_item_id = item_id

        if best_item_id and best_similarity >= self.GRID_ITEM_SIMILARITY:
            return best_item_id
        return None

    def _match_num_prefix_xy(self, masked_cell_image) -> Optional[Tuple[int, int]]:
        self_image = self.device.image
        self.device.image = masked_cell_image
        # 在“当前格子整图掩码图”中匹配数量前缀模板，返回匹配左上角 (x, y)
        if self.appear(ITEM_NUM_PREFIX, offset=10, threshold=0.6, static=False):
            x, y = self.appear_location(ITEM_NUM_PREFIX, offset=10, threshold=0.6, static=False)
            self.device.image = self_image
            return x, y

        self.device.image = self_image
        return None

    def _build_num_area(
        self,
        prefix_xy: Tuple[int, int],
        cell_area: Tuple[int, int, int, int],
        image,
    ) -> Optional[Tuple[int, int, int, int]]:
        # 规则：(x-10, y-10, 格子右下角x, 格子右下角y)
        x, y = prefix_xy
        _, _, cell_x2, _ = cell_area
        x1, y1 = x - 10, y - 17
        x2, y2 = cell_x2, y + 8

        # 边界保护，防止越界
        h, w = image.shape[:2]
        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(0, min(w, x2))
        y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return int(x1), int(y1), int(x2), int(y2)

    def _log_page_item_counts(
        self,
        page_index: int,
        page_counts: Dict[str, int],
        item_name_map: Dict[str, str],
    ) -> None:
        if not page_counts:
            logger.info(f'WarehouseStats: Page {page_index + 1} recognized 0 items.')
            return
        summary = ', '.join(
            f'{item_name_map.get(item_id, item_id)}({item_id})={count}' for item_id, count in page_counts.items()
        )
        logger.info(f'WarehouseStats: Page {page_index + 1} results: {summary}')

    def _save_debug_image(self, filename: str, image) -> None:
        if not self.DEBUG_SAVE_IMAGE:
            return
        try:
            run_id = getattr(self, '_debug_run_id', 'manual')
            output_dir = os.path.join(self.DEBUG_IMAGE_DIR, run_id)
            os.makedirs(output_dir, exist_ok=True)
            path = os.path.join(output_dir, filename)
            if image is None:
                return
            # 项目内图片通常是 RGB，保存前转为 BGR，便于 OpenCV 正常显示颜色
            if len(image.shape) == 3 and image.shape[2] == 3:
                image_to_save = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            else:
                image_to_save = image
            cv2.imwrite(path, image_to_save)
        except Exception:
            logger.exception(f'WarehouseStats: save debug image failed: {filename}')

    def _load_templates(self, items: List[dict]) -> Dict[str, Button]:
        # 读取配置物品对应的模板按钮
        templates: Dict[str, Button] = {}
        for item in items:
            item_id = item.get('id')
            if not item_id:
                continue

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
        self.device.screenshot()
        ocr = Digit(self.ITEM_COUNT_AREA, model_type=self.config.Optimization_OcrModelType)
        result = ocr.ocr(self.device.image)
        try:
            return int(result)
        except Exception:
            return 0
