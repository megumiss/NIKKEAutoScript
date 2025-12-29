from functools import cached_property

from module.base.button import filter_buttons_in_area, merge_buttons
from module.base.timer import Timer
from module.base.utils import sort_buttons_by_location
from module.event.assets import *
from module.event.base import EventBase
from module.logger import logger


class EventShop(EventBase):
    @cached_property
    def shop_delay_list(self) -> list[str]:
        """
        商店延迟购买列表
        """
        return [line.strip() for line in self.config.Event_ShopDelayList.split('\n') if line.strip()]

    def get_shop_item_button(self, item: str):
        """
        根据选项名称获取对应的按钮
        示例：
          "TITLE" → SHOP_ITEM_TITLE
        """
        button_name = f'SHOP_ITEM_{item}'
        try:
            return globals()[button_name]
        except KeyError:
            logger.error(f"Button asset '{button_name}' not found for option '{item}'")
            raise

    def shop(self, target_button=None, skip_first_screenshot=True):
        """
        Args:
            target_button (Button): TEMPLATE_SHOP_MONEY or TEMPLATE_SHOP_GEM.
                                    Defaults to self.event_assets.TEMPLATE_SHOP_MONEY if None.
        """
        if target_button is None:
            target_button = self.event_assets.TEMPLATE_SHOP_MONEY

        logger.hr(f'START EVENT SHOP: {target_button.name}', 2)
        click_timer = Timer(0.3)
        restart_flag = False
        delay_list = self.shop_delay_list
        # 钻石
        is_gem_mode = 'TEMPLATE_SHOP_GEM' == target_button.name

        # 进入商店页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.SHOP, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            # 商店页面
            if self.appear(EVENT_SHOP_CHECK, offset=(30, 30)) and self.appear(
                self.event_assets.SHOP_ITEM_LOAD_CHECK, threshold=0.65, offset=(30, 30)
            ):
                logger.info('Open event shop')
                break
        self.ui_wait_loading()

        # 跳过第一个物品
        skip_item_first = False
        while 1:
            # 滑动到商店最上方
            if restart_flag:
                logger.info('Scroll to shop top')
                self.ensure_sroll_to_top((360, 600), (360, 900), speed=30, count=3, delay=2)
                restart_flag = False

            self.device.screenshot()
            # 当前页所有商品，阈值较低可能会重复
            items = target_button.match_multi(self.device.image, similarity=0.65, name='SHOP_ITEM')
            # 合并重复的商品
            items = merge_buttons(items, x_threshold=30, y_threshold=30)
            # 过滤掉非商店区域的商品
            items = filter_buttons_in_area(items, y_range=(620, 1280))
            # 按照坐标排序
            items = sort_buttons_by_location(items)
            logger.info(f'Find items ({target_button.name}): {len(items)}')

            # SOLD_OUT的商品
            sold_outs = TEMPLATE_SOLD_OUT.match_multi(self.device.image, similarity=0.7, name='SOLD_OUT')
            logger.info(f'Find slod out items: {len(sold_outs)}')
            # 过滤掉所有SOLD_OUT的商品
            items = self.filter_sold_out_items(items, sold_outs)
            # 如果第一个物品为要推迟购买的物品，在购买列表中删除
            if skip_item_first and items:
                items = items[1:]
                skip_item_first = False

            logger.info(f'Find vaild items: {len(items)}')
            if items:
                while 1:
                    self.device.screenshot()

                    # 购买第一个商品
                    if click_timer.reached() and self.appear(EVENT_SHOP_CHECK, offset=(30, 30)):
                        self.device.click(items[0])
                        click_timer.reset()
                        continue

                    # 商品弹窗
                    if self.appear(SHOP_ITEM_CHECK, offset=100):
                        click_timer.reset()
                        break

                quit = False
                confirm_timer = Timer(1, count=3)
                while 1:
                    self.device.screenshot()

                    # 退出
                    if self.appear(EVENT_SHOP_CHECK, offset=(30, 30)):
                        if not confirm_timer.started():
                            confirm_timer.start()
                        if confirm_timer.reached():
                            if quit:
                                logger.info('Money/Gem not enough, quiting')
                                return
                            else:
                                logger.info('Item purchase completed, goto next')
                                break
                    else:
                        confirm_timer.clear()

                    # 使用钻石购买时跳过胶卷
                    if (
                        is_gem_mode
                        and self.appear(SHOP_ITEM_CHECK, offset=100)
                        and self.appear(SHOP_ITEM_MEMORY_FILM, offset=30)
                    ):
                        logger.info('Skip FILM purchase in Gem mode')
                        skip_item_first = True
                        # 取消购买弹窗
                        if click_timer.reached() and self.appear_then_click(SHOP_CANCEL, offset=30, interval=1):
                            click_timer.reset()
                            continue

                    # 商品在延迟购买列表中，跳过，返回商店主页
                    if not is_gem_mode:
                        for i, item in enumerate(delay_list[:]):
                            if self.appear(SHOP_ITEM_CHECK, offset=100) and self.appear(
                                self.get_shop_item_button(item), offset=10
                            ):
                                logger.info(f'Skip item purchase: {item}')
                                skip_item_first = True
                                # 取消购买弹窗
                                if click_timer.reached() and self.appear_then_click(SHOP_CANCEL, offset=30, interval=1):
                                    click_timer.reset()
                                    continue

                    # 商品是红球并且称号没买，重新进入商店
                    if (
                        delay_list
                        and self.appear(SHOP_ITEM_CHECK, offset=100)
                        and self.appear(SHOP_ITEM_RED_CIRCLE, offset=10)
                    ):
                        logger.info('Delaylist not empty, restart shop to purchase')
                        delay_list.clear()
                        restart_flag = True
                        # 取消购买弹窗
                        if click_timer.reached() and self.appear_then_click(SHOP_CANCEL, offset=30, interval=1):
                            click_timer.reset()
                            continue

                    # 取消
                    if (
                        (quit or restart_flag)
                        and click_timer.reached()
                        and self.appear_then_click(SHOP_CANCEL, offset=30, interval=1)
                    ):
                        click_timer.reset()
                        continue

                    # 资金不足
                    if self.appear(SHOP_MONEY_LACK, offset=30):
                        quit = True
                        continue

                    # 点击max
                    if click_timer.reached() and self.appear_then_click(
                        SHOP_BUY_MAX, offset=30, threshold=0.99, interval=1
                    ):
                        click_timer.reset()
                        continue

                    # 购买
                    if click_timer.reached() and self.appear_then_click(SHOP_BUY, offset=100, interval=1):
                        click_timer.reset()
                        continue

                    # 点击领取
                    if click_timer.reached() and self.appear_then_click(RECEIVE, offset=30, interval=1, static=False):
                        self.device.sleep(0.5)
                        click_timer.reset()
                        continue
            else:
                # 当前页全部购买完成
                # 钻石模式只检查第一页
                if is_gem_mode:
                    logger.info('Gem shop check finished (No scroll)')
                    break

                # 滚动到下一页
                logger.info('Scroll to next page')
                self.device.stuck_record_clear()
                self.device.click_record_clear()
                self.ensure_sroll((360, 1100), (360, 480), speed=5, count=1, delay=3, method='scroll')

    def filter_sold_out_items(self, items, sold_outs):
        """
        根据售完标记的位置过滤商品列表
        """
        filtered_items = items.copy()

        for sold_out in sold_outs:
            so_x, so_y = sold_out.location
            to_remove = []

            for item in filtered_items:
                item_x, item_y = item.location

                x_in_range = so_x - 30 <= item_x <= so_x + 30
                y_in_range = so_y <= item_y <= so_y + 120

                if x_in_range and y_in_range:
                    to_remove.append(item)

            for item in to_remove:
                filtered_items.remove(item)

        return filtered_items
