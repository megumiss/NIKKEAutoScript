import os
from datetime import datetime
from functools import cached_property
from typing import List, Optional, Tuple

import numpy as np

from module.base.timer import Timer
from module.base.utils import point2str
from module.interception.assets import *
from module.interception.data import append_interception_stone_record
from module.logger import logger
from module.ocr.ocr import Digit
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, PAUSE
from module.ui.assets import INTERCEPTION_CHECK
from module.ui.page import page_interception
from module.ui.ui import UI
from module.warehouse_stats.assets import CUSTOM_MODULE_TEMPLATE, ITEM_NUM_PREFIX
from module.warehouse_stats.warehouse_stats import WarehouseStats


class NoOpportunity(Exception):
    pass


class Interception(UI):
    # 扩展石头附近区域（基于 CUSTOM_MODULE_TEMPLATE 匹配框），用于隔离单个掉落位
    STONE_ROI_EXPAND_LEFT = 80
    STONE_ROI_EXPAND_TOP = 70
    STONE_ROI_EXPAND_RIGHT = 140
    STONE_ROI_EXPAND_BOTTOM = 120

    # 匹配阈值与去重参数
    STONE_TEMPLATE_THRESHOLD = 0.72
    STONE_TEMPLATE_DEDUP_DISTANCE = 45
    STONE_NUM_PREFIX_THRESHOLD = 0.60
    STONE_NUM_PREFIX_DEDUP_DISTANCE = 12
    STONE_NUM_PREFIX_MAX_DISTANCE = 220

    @property
    def battle_quickly_level(self):
        model_type = self.config.Optimization_OcrModelType
        LEVEL = Digit(
            [BATTLE_QUICKLY_LEVEL.area],
            name='BATTLE_QUICKLY_LEVEL',
            model_type=model_type,
            lang='ch',
        )

        level = int(LEVEL.ocr(self.device.image)['text'])
        if level == 1:
            level = 7
            logger.info('Replace quickly level 1 -> 7')
        if level == 0:
            level = 9
            logger.info('Replace quickly level 0 -> 9')

        return level

    @cached_property
    def teams(self):
        return [TEAM_1, TEAM_2, TEAM_3, TEAM_4, TEAM_5]

    def get_boss_button(self, boss: str):
        """
        根据选项名称获取对应的按钮
        示例：
          "Kraken" → KRAKEN
        """
        button_name = boss.upper()
        try:
            return globals()[button_name]
        except KeyError:
            logger.error(f"Button asset '{button_name}' not found for option '{boss}'")
            raise

    def _run(self, skip_first_screenshot=True):
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=(10, 30)):
                break

            if click_timer.reached() and self.appear(
                self.get_boss_button(self.config.Interception_Boss), offset=10, interval=1
            ):
                logger.info('Click %s @ CHALLANGE' % point2str(360, 1030))
                self.device.click_minitouch(360, 1030)
                # self.device.sleep(1)
                click_timer.reset()
                continue

            if (
                self.appear(KRAKEN, offset=10)
                or self.appear(HARVESTER, offset=10)
                or self.appear(INDIVILIA, offset=10)
                or self.appear(MIRRORCONTAINER, offset=10)
                or self.appear(ULTRA, offset=10)
            ) and not self.appear(self.get_boss_button(self.config.Interception_Boss), offset=10):
                logger.info('Click %s @ SWITCH' % point2str(580, 960))
                self.device.click_minitouch(580, 960)
                self.device.sleep(0.5)
                click_timer.reset()
                continue

        self.device.click_record_clear()
        self.device.stuck_record_clear()
        self.device.sleep(0.5)

        end_fighting = False
        if self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=(10, 30)) and not BATTLE.match_appear_on(
            self.device.image, 25
        ):
            end_fighting = True
        # 使用的队伍
        teamindex = getattr(self.config, f'InterceptionTeam_{self.config.Interception_Boss}') - 1
        load_check_timer = Timer(1, count=3)
        while 1:
            self.device.screenshot()

            # 切换队伍
            if (
                click_timer.reached()
                and self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=(10, 30))
                and self.appear_then_click(self.teams[teamindex], threshold=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 达到目标等级才快速战斗
            if (
                self.appear(BATTLE_QUICKLY, threshold=5)
                and (
                    self.config.Interception_AchieveLevel == 1
                    or self.battle_quickly_level >= self.config.Interception_AchieveLevel
                )
                and click_timer.reached()
                and self.appear_then_click(BATTLE_QUICKLY, threshold=10)
            ):
                end_fighting = False
                self.device.sleep(1)
                click_timer.reset()
                continue

            #  开启了只快速战斗
            if (
                self.config.Interception_QuickBattleOnly
                and self.appear(BATTLE, threshold=25)
                and not self.appear(BATTLE_QUICKLY, threshold=5)
            ):
                logger.warning(f'Quick battle only: {self.config.Interception_QuickBattleOnly}, skip battle')
                return

            if click_timer.reached() and self.appear_then_click(BATTLE, threshold=25, interval=1):
                end_fighting = False
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=(5, 5), threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=(5, 5), threshold=0.9, interval=5):
                click_timer.reset()
                continue

            # 红圈
            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                if self.handle_red_circles():
                    continue

            if click_timer.reached() and self.appear(END_FIGHTING, offset=30):
                saved_path = self.save_drop_image(self.device.image, self.config.Interception_DropScreenshotPath)
                if saved_path:
                    logger.info(f'Save drop image to: {saved_path}')
                stone_count = self.recognize_drop_stone_count(self.device.image)
                self.write_drop_stone_record(stone_count=stone_count, screenshot_path=saved_path or '')

                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=30):
                        click_timer.reset()
                        break
                    if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                        click_timer.reset()
                        continue
                end_fighting = True
                click_timer.reset()
                continue

            if end_fighting:
                if self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=(10, 30)):
                    if not load_check_timer.started():
                        load_check_timer.start()
                    if load_check_timer.reached() and not self.appear(BATTLE, threshold=25):
                        logger.info('There are no free opportunities')
                        raise NoOpportunity
                else:
                    load_check_timer.clear()

    def save_drop_image(self, image, base_path):
        """
        保存掉落截图到日期子文件夹，并按当天次数自动编号
        兼容 Linux/Windows
        Args:
            image: OpenCV 格式图片 (numpy.ndarray)
            base_path: 基础保存路径
        Returns:
            save_path: 保存的完整文件路径
        """
        if not base_path:
            return None

        # 按日期生成子文件夹
        today_str = datetime.now().strftime('%Y-%m-%d')
        date_dir = os.path.join(base_path, self.config.config_name, today_str)

        # 创建目录
        os.makedirs(date_dir, exist_ok=True)

        # 按当天已有数量生成编号
        existing_files = [f for f in os.listdir(date_dir) if f.lower().endswith('.png')]
        file_index = len(existing_files) + 1

        # 生成文件路径
        filename = f'drop_{file_index}.png'
        save_path = os.path.join(date_dir, filename)

        # 保存图片
        from module.base.utils import save_image

        save_image(image, save_path)
        return save_path

    def recognize_drop_stone_count(self, image) -> int:
        module_areas = self._match_custom_module_areas(image)
        if not module_areas:
            logger.info('InterceptionStats: CUSTOM_MODULE not found in drop image, stone_count=0')
            return 0

        total = 0
        used_prefix_centers: List[Tuple[int, int]] = []

        for area in module_areas:
            roi = self._expand_area_with_bounds(area, image)
            masked = self._mask_outside_area(image, roi)
            module_center = self._area_center(area)

            prefix_candidates = self._match_item_num_prefix_centers(masked)
            prefix_center = self._pick_prefix_for_module(
                module_center=module_center,
                prefix_candidates=prefix_candidates,
                used_centers=used_prefix_centers,
            )
            if prefix_center is None:
                logger.debug(
                    f'InterceptionStats: no ITEM_NUM_PREFIX found for CUSTOM_MODULE area={area}, roi={roi}, skip.'
                )
                continue

            num_area = self._build_num_area_from_prefix(prefix_center, masked)
            if num_area is None:
                logger.debug(f'InterceptionStats: invalid num area from prefix={prefix_center}, skip.')
                continue

            value = WarehouseStats._parse_direct_count_by_digit_templates(
                self,
                image=masked,
                area=num_area,
                has_suffix_k=False,
                has_suffix_m=False,
                item_name='INTERCEPTION_STONE',
            )
            if value is None:
                logger.debug(
                    f'InterceptionStats: digit template parse failed, module_area={area}, '
                    f'prefix={prefix_center}, num_area={num_area}'
                )
                continue

            # 异常拦截石头每个掉落位显示单数字
            if value > 9:
                logger.debug(f'InterceptionStats: parsed multi-digit value={value}, keep first digit only.')
                value = int(str(value)[0])

            total += max(0, int(value))
            used_prefix_centers.append(prefix_center)
            logger.debug(
                f'InterceptionStats: parsed one module, area={area}, prefix={prefix_center}, '
                f'num_area={num_area}, value={value}, running_total={total}'
            )

        logger.info(f'InterceptionStats: recognized CUSTOM_MODULE count={len(module_areas)}, stone_total={total}')
        return total

    def _match_custom_module_areas(self, image) -> List[Tuple[int, int, int, int]]:
        areas: List[Tuple[int, int, int, int]] = []
        try:
            matches = CUSTOM_MODULE_TEMPLATE.match_several(
                image.copy(),
                offset=20,
                threshold=self.STONE_TEMPLATE_THRESHOLD,
                static=False,
            )
        except Exception:
            logger.exception('InterceptionStats: CUSTOM_MODULE_TEMPLATE match failed.')
            return []

        for item in matches:
            area = item.get('area') if isinstance(item, dict) else None
            if not area:
                continue
            areas.append(tuple(map(int, area)))

        deduped = self._dedupe_areas(areas, min_distance=self.STONE_TEMPLATE_DEDUP_DISTANCE)
        deduped.sort(key=lambda a: (a[1], a[0]))
        return deduped

    def _match_item_num_prefix_centers(self, image) -> List[Tuple[int, int]]:
        centers: List[Tuple[int, int]] = []
        try:
            matches = ITEM_NUM_PREFIX.match_several(
                image.copy(),
                offset=10,
                threshold=self.STONE_NUM_PREFIX_THRESHOLD,
                static=False,
            )
        except Exception:
            logger.exception('InterceptionStats: ITEM_NUM_PREFIX match failed.')
            return []

        for item in matches:
            area = item.get('area') if isinstance(item, dict) else None
            if not area:
                continue
            x1, y1, x2, y2 = map(int, area)
            centers.append(((x1 + x2) // 2, (y1 + y2) // 2))

        return self._dedupe_points(centers, min_distance=self.STONE_NUM_PREFIX_DEDUP_DISTANCE)

    def _pick_prefix_for_module(
        self,
        module_center: Tuple[int, int],
        prefix_candidates: List[Tuple[int, int]],
        used_centers: List[Tuple[int, int]],
    ) -> Optional[Tuple[int, int]]:
        if not prefix_candidates:
            return None

        mcx, mcy = module_center
        ranked: List[Tuple[int, Tuple[int, int]]] = []
        for px, py in prefix_candidates:
            if self._contains_near_point(used_centers, (px, py), min_distance=self.STONE_NUM_PREFIX_DEDUP_DISTANCE):
                continue
            distance = (px - mcx) * (px - mcx) + (py - mcy) * (py - mcy)
            if distance > self.STONE_NUM_PREFIX_MAX_DISTANCE * self.STONE_NUM_PREFIX_MAX_DISTANCE:
                continue
            ranked.append((distance, (px, py)))

        if not ranked:
            return None
        ranked.sort(key=lambda x: x[0])
        return ranked[0][1]

    def _build_num_area_from_prefix(self, prefix_center: Tuple[int, int], image) -> Optional[Tuple[int, int, int, int]]:
        x, y = prefix_center
        x1 = x - 10
        y1 = y - 17
        x2 = x + 18
        y2 = y + 10

        h, w = image.shape[:2]
        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(0, min(w, x2))
        y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return int(x1), int(y1), int(x2), int(y2)

    def _expand_area_with_bounds(self, area: Tuple[int, int, int, int], image) -> Tuple[int, int, int, int]:
        x1, y1, x2, y2 = area
        h, w = image.shape[:2]

        ex1 = max(0, x1 - self.STONE_ROI_EXPAND_LEFT)
        ey1 = max(0, y1 - self.STONE_ROI_EXPAND_TOP)
        ex2 = min(w, x2 + self.STONE_ROI_EXPAND_RIGHT)
        ey2 = min(h, y2 + self.STONE_ROI_EXPAND_BOTTOM)
        return int(ex1), int(ey1), int(ex2), int(ey2)

    def _mask_outside_area(self, image, area: Tuple[int, int, int, int]):
        x1, y1, x2, y2 = area
        masked = np.zeros_like(image)
        masked[y1:y2, x1:x2] = image[y1:y2, x1:x2]
        return masked

    def _dedupe_areas(
        self, areas: List[Tuple[int, int, int, int]], min_distance: int
    ) -> List[Tuple[int, int, int, int]]:
        deduped: List[Tuple[int, int, int, int]] = []
        for area in areas:
            cx, cy = self._area_center(area)
            duplicated = False
            for kept in deduped:
                kx, ky = self._area_center(kept)
                if (cx - kx) * (cx - kx) + (cy - ky) * (cy - ky) <= min_distance * min_distance:
                    duplicated = True
                    break
            if not duplicated:
                deduped.append(area)
        return deduped

    def _dedupe_points(self, points: List[Tuple[int, int]], min_distance: int) -> List[Tuple[int, int]]:
        deduped: List[Tuple[int, int]] = []
        for point in points:
            if self._contains_near_point(deduped, point, min_distance=min_distance):
                continue
            deduped.append(point)
        return deduped

    def _contains_near_point(self, points: List[Tuple[int, int]], target: Tuple[int, int], min_distance: int) -> bool:
        tx, ty = target
        for px, py in points:
            if (tx - px) * (tx - px) + (ty - py) * (ty - py) <= min_distance * min_distance:
                return True
        return False

    def _area_center(self, area: Tuple[int, int, int, int]) -> Tuple[int, int]:
        x1, y1, x2, y2 = area
        return (x1 + x2) // 2, (y1 + y2) // 2

    def write_drop_stone_record(self, stone_count: int, screenshot_path: str = '') -> None:
        csv_path = self.config.InterceptionDropStats_CsvPath
        ok = append_interception_stone_record(
            csv_path=csv_path,
            config_name=self.config.config_name,
            boss=self.config.Interception_Boss,
            stone_count=stone_count,
            screenshot_path=screenshot_path,
        )
        if ok:
            logger.info(
                f'InterceptionStats: record saved, boss={self.config.Interception_Boss}, '
                f'stone_count={stone_count}, csv={csv_path}'
            )

    def run(self):
        self.ui_ensure(page_interception)
        try:
            self._run()
        except NoOpportunity:
            pass
        self.config.task_delay(server_update=True)
