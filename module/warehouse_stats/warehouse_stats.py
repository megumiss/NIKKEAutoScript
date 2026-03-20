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
    2) 优先使用“固定网格 + 数量前缀模板 + OCR”识别
    3) 若仍有未识别物品，回退到“点击详情页 OCR”旧逻辑
    4) 滚动翻页重复扫描
    5) 写入 CSV
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
    GRID_VIEW_X2 = GRID_START_X + (GRID_COLS - 1) * GRID_STEP_X + GRID_CELL_WIDTH
    GRID_VIEW_Y2 = GRID_START_Y + (GRID_ROWS - 1) * GRID_STEP_Y + GRID_CELL_HEIGHT

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

    def inventory_item_num(self, area):
        # 旧逻辑：识别物品详情面板中的“持有数”
        model_type = self.config.Optimization_OcrModelType
        item_num = Ocr(
            [area],
            text_color=(248, 252, 254),
            text_color_tolerance=(80, 10, 40),
            name='INVENTORY_ITEM',
            model_type=model_type,
            lang='ch',
        )

        text = item_num.ocr(self.device.image)['text']
        # 同时兼容英文冒号 : 与全角冒号 ：
        pattern = f'{Langs.FAVORITE_ITEM_NUM}[:\\uFF1A]\\s*(\\d+)'
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
        return 0

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

        # pending: 仍需识别的物品模板映射（item_id -> Button模板）
        pending: Dict[str, Button] = {}
        for item in items:
            item_id = item.get('id')
            if not item_id or not item.get('scan', True):
                continue
            button = templates.get(item_id)
            if button is None:
                continue
            pending[item_id] = button

        if not pending:
            return results

        # 新识别方式（优先级高于旧逻辑）
        self._scan_inventory_grid(pending=pending, results=results)

        # 兜底：新方式没识别完时，走旧逻辑
        if pending:
            logger.info(f'WarehouseStats: Grid scan incomplete, fallback to legacy method. pending={len(pending)}')
            self._scan_inventory_legacy(pending=pending, results=results)

        return results

    def _scan_inventory_grid(self, pending: Dict[str, Button], results: Dict[str, int]) -> None:
        # 当前扫描任务的调试目录标识，避免多次运行互相覆盖
        if not hasattr(self, '_debug_run_id'):
            self._debug_run_id = datetime.now().strftime('%Y%m%d_%H%M%S')

        # 最大扫描页数保护，避免异常情况下无限循环
        max_pages = max(20, int(getattr(self.config, 'WarehouseStats_ScrollTimes', 5)) * 20)
        page_index = 0
        # 首次打开仓库时使用固定网格原点
        grid_origin = (self.GRID_START_X, self.GRID_START_Y)
        # 锚点按钮：用于翻页后对齐第一行位置
        anchor_button: Optional[Button] = None

        while page_index < max_pages and pending:
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

            # 将当前页按固定变量切为 5x7 格
            cells = self._split_inventory_cells(
                image=image,
                origin=grid_origin,
                page_index=page_index,
                drop_anchor_row=drop_anchor_row,
            )
            for cell in cells:
                if not pending:
                    break

                # 先在该格子内判断是哪一个物品（模板匹配）
                item_id = self._match_pending_item_in_cell(cell_image=cell['cell'], pending=pending)
                if not item_id:
                    continue

                # 用 TEMPLATE_ITEM_NUM_PREFIX 在“该格子涂黑图”中找数量前缀坐标
                prefix_xy = self._match_num_prefix_xy(masked_cell_image=cell['masked'])
                if prefix_xy is None:
                    continue

                # 按规则 (x-10, y-10, 格子右下x, 格子右下y) 生成 OCR 范围
                num_area = self._build_num_area(prefix_xy=prefix_xy, cell_area=cell['area'], image=image)
                if num_area is None:
                    continue

                # OCR 识别数量
                count = self._ocr_num_area(image=image, area=num_area)
                if count is None:
                    continue

                # 成功识别后从 pending 中移除，避免重复处理
                results[item_id] = count
                pending.pop(item_id, None)
                logger.info(f'WarehouseStats: [Grid] Found item={item_id}, count={count}, area={num_area}')

            if not pending:
                break

            if self.appear(INVENTORY_BOTTOM_CHECK, offset=10):
                logger.info('WarehouseStats: Reached bottom of inventory list.')
                break

            # 取“最后一行第一个格子”作为锚点，供下一页定位使用
            if not cells:
                logger.warning('WarehouseStats: No valid grid cells on current page, skip anchor update once.')
                anchor_button = None
                self.ensure_sroll((450, 950), (450, 400), speed=5, count=1, delay=1, method='scroll')
                page_index += 1
                continue

            anchor_cell = self._get_anchor_cell(cells)
            anchor_button = self._build_anchor_button(image=image, area=anchor_cell['area'])

            # 向下滚动一页
            self.ensure_sroll((450, 950), (450, 400), speed=5, count=1, delay=1, method='scroll')
            page_index += 1

    def _scan_inventory_legacy(self, pending: Dict[str, Button], results: Dict[str, int]) -> None:
        # 旧逻辑：逐个模板查找 -> 点击进入详情 -> OCR 持有数
        while 1:
            self.device.screenshot()
            if not pending:
                break

            remaining: Dict[str, Button] = {}
            for item_id, button in pending.items():
                if item_id in results:
                    continue

                if not self.appear(button, offset=10, static=False):
                    remaining[item_id] = button
                    continue

                # 找到目标物品，进入详情页
                logger.info(f'WarehouseStats: Found item: {item_id}')
                while 1:
                    self.device.screenshot()
                    if self.appear(INVENTORY_ITEM_CLOSE, offset=10, static=False):
                        break
                    if self.appear_then_click(button, offset=10, interval=1, static=False):
                        continue

                owner_loc = self.appear_location(INVENTORY_ITEM_CLOSE, offset=10, static=False)
                if owner_loc is None:
                    remaining[item_id] = button
                    continue

                # 详情页数量区域 OCR
                results[item_id] = self.inventory_item_num(
                    (720 - owner_loc[0], owner_loc[1] + 200, owner_loc[0], owner_loc[1] + 270)
                )

                # 关闭详情回到列表
                while 1:
                    self.device.screenshot()
                    if self.appear_then_click(INVENTORY_ITEM_CLOSE, offset=10, interval=1, static=False):
                        continue
                    if self.appear(INVENTORY_CHECK, offset=10):
                        break

            pending.clear()
            pending.update(remaining)
            if not pending:
                break

            if self.appear(INVENTORY_BOTTOM_CHECK, offset=10):
                logger.info('WarehouseStats: Reached bottom of inventory list.')
                break

            self.ensure_sroll((450, 950), (450, 250), speed=5, count=1, delay=0.3, method='scroll')

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
        # 在“当前格子整图掩码图”中匹配数量前缀模板，返回匹配左上角 (x, y)
        buttons = TEMPLATE_ITEM_NUM_PREFIX.match_multi(
            masked_cell_image,
            similarity=self.GRID_PREFIX_SIMILARITY,
            name='ITEM_NUM_PREFIX',
        )
        if not buttons:
            return None
        button = sorted(buttons, key=lambda b: (b.area[1], b.area[0]))[0]
        x1, y1, _, _ = button.button
        return int(x1), int(y1)

    def _build_num_area(
        self,
        prefix_xy: Tuple[int, int],
        cell_area: Tuple[int, int, int, int],
        image,
    ) -> Optional[Tuple[int, int, int, int]]:
        # 规则：(x-10, y-10, 格子右下角x, 格子右下角y)
        x, y = prefix_xy
        _, _, cell_x2, cell_y2 = cell_area
        x1, y1 = x - 10, y - 10
        x2, y2 = cell_x2, cell_y2

        # 边界保护，防止越界
        h, w = image.shape[:2]
        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(0, min(w, x2))
        y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return int(x1), int(y1), int(x2), int(y2)

    def _ocr_num_area(self, image, area: Tuple[int, int, int, int]) -> Optional[int]:
        # 数字 OCR：识别失败返回 None
        num_raw = crop(image, area)
        ocr = Digit(
            [area],
            model_type=self.config.Optimization_OcrModelType,
            name='INVENTORY_GRID_ITEM_NUM',
            text_color=(248, 252, 254),
            text_color_tolerance=(80, 10, 40),
        )
        num_preprocessed = ocr.pre_process(
            num_raw,
            text_color=(248, 252, 254),
            text_color_tolerance=(80, 10, 40),
        )
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self._save_debug_image(f'{ts}_num_raw_{area[0]}_{area[1]}_{area[2]}_{area[3]}.png', num_raw)
        self._save_debug_image(f'{ts}_num_preprocessed_{area[0]}_{area[1]}_{area[2]}_{area[3]}.png', num_preprocessed)

        text = ocr.ocr(image, show_log=False).get('text', '').strip()
        if text == '':
            return None
        try:
            return int(text)
        except Exception:
            return None

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
