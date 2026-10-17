import re
from functools import cached_property

from module.base.timer import Timer
from module.base.utils import crop
from module.event.assets import SHOP_MONEY_LACK
from module.logger import logger
from module.ocr.shop import ShopQuantityOcr
from module.shop.assets import (
    BODY_LABEL_ABNORMAL,
    BODY_LABEL_ATTACKER,
    BODY_LABEL_DEFENDER,
    BODY_LABEL_DISCOUNT,
    BODY_LABEL_ELYSION,
    BODY_LABEL_MINUS,
    BODY_LABEL_MISSILIS,
    BODY_LABEL_PILGRIM,
    BODY_LABEL_PLUS,
    BODY_LABEL_QUANTITY,
    BODY_LABEL_REMAINING,
    BODY_LABEL_SHOP_CHECK,
    BODY_LABEL_SUPPORTER,
    BODY_LABEL_TETRA,
    BUY,
    GOTO_BODY_LABEL_SHOP,
    NO_MONEY,
    PURCHASE_CHECK,
)
from module.shop.base import NotEnoughMoneyError, PurchaseTimeTooLong, ShopBase
from module.ui.assets import SHOP_CHECK

CONSOLE_PRODUCTS = {
    'ABNORMAL': BODY_LABEL_ABNORMAL,
    'ELYSION': BODY_LABEL_ELYSION,
    'MISSILIS': BODY_LABEL_MISSILIS,
    'PILGRIM': BODY_LABEL_PILGRIM,
    'TETRA': BODY_LABEL_TETRA,
    'ATTACKER': BODY_LABEL_ATTACKER,
    'DEFENDER': BODY_LABEL_DEFENDER,
    'SUPPORTER': BODY_LABEL_SUPPORTER,
}


class BodyLabelShop(ShopBase):
    @cached_property
    def quantity_ocr(self) -> ShopQuantityOcr:
        return ShopQuantityOcr(model_type=self.config.Optimization_OcrModelType)

    def handle_body_label_purchase(self, count, skip_first_screenshot=True):
        self.device.click_record_clear()
        confirm_timer = Timer(1, 3).start()
        purchased = False
        quantity_timeout = Timer(20).start()
        last_quantity = None
        waiting_for_update = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(NO_MONEY, offset=10, static=False) and NO_MONEY.match_appear_on(self.device.image):
                raise NotEnoughMoneyError

            if self.appear(SHOP_MONEY_LACK, offset=30, static=False):
                raise NotEnoughMoneyError

            if not purchased and quantity_timeout.reached():
                raise PurchaseTimeTooLong(f'Quantity adjustment timed out: target={count}, current={last_quantity}')

            if not purchased:
                quantity = self.quantity_ocr.ocr(crop(self.device.image, BODY_LABEL_QUANTITY.area))
                if quantity is None:
                    continue
                current, maximum = quantity
                if current != last_quantity:
                    logger.attr('BODY LABEL DIALOG COUNT', f'{current}/{maximum}, target={count}')
                    last_quantity = current
                    waiting_for_update = False
                if current == count:
                    if self.appear_then_click(BUY, offset=(30, 30), interval=3, static=False):
                        purchased = True
                    continue
                # 上一次点击未回显时只重新识别，不能继续叠加点击。
                if waiting_for_update:
                    continue
                button = BODY_LABEL_MINUS if current > count else BODY_LABEL_PLUS
                if self.appear_then_click(button, offset=10, interval=0.3):
                    waiting_for_update = True
                    self.device.sleep(0.3)
                    continue

            if self.handle_reward(1):
                skip_first_screenshot = True
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()
                    self.handle_reward(1)
                    if self.appear(SHOP_CHECK, offset=10) and confirm_timer.reached():
                        return

    def process_body_label_purchase(self, products, count, skip_first_screenshot=True):
        timeout = Timer(3, 3).start()
        purchase_count = None
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.info('BodyLabelShop: no selected discounted console available')
                return

            if purchase_count is not None and self.appear(PURCHASE_CHECK, offset=10, static=False):
                self.handle_body_label_purchase(purchase_count)
                return

            if not self.appear(BODY_LABEL_DISCOUNT, offset=10):
                continue

            # 仅匹配首格图标；全屏搜索会命中下方的原价商品。
            if not self.appear_any(products, offset=10):
                continue

            if purchase_count is None:
                remaining = self.quantity_ocr.ocr(crop(self.device.image, BODY_LABEL_REMAINING.area))
                if remaining is None:
                    logger.warning('BodyLabelShop: remaining quantity unreadable, skip purchase')
                    return
                logger.attr('BODY LABEL REMAINING', f'{remaining[0]}/{remaining[1]}')
                if remaining[0] == 0:
                    logger.info('BodyLabelShop: daily discounted console sold out')
                    return
                purchased_today = remaining[1] - remaining[0]
                logger.attr('BODY LABEL PURCHASED TODAY', purchased_today)
                if purchased_today >= count:
                    logger.info(f'BodyLabelShop: daily purchase target reached: {purchased_today}/{count}')
                    return
                purchase_count = min(count - purchased_today, remaining[0])
                logger.attr('BODY LABEL PURCHASE COUNT', purchase_count)

            if self.appear_then_click_any(products, offset=10, interval=1):
                timeout.reset()
                continue

    def run_body_label_shop(self):
        priority = re.sub(r'\s+', '', self.config.BodyLabelShop_priority or '')
        selected = [name for name in priority.split('>') if name]
        if not selected:
            logger.info('BodyLabelShop: no console types selected, skip')
            return
        if set(selected) - CONSOLE_PRODUCTS.keys():
            logger.warning(f'BodyLabelShop: invalid console types: {selected}')
            return
        count_text = str(self.config.BodyLabelShop_BuyCount)
        if re.fullmatch(r'[1-9]|10', count_text) is None:
            logger.warning(f'BodyLabelShop: purchase quantity must be 1-10: {count_text!r}')
            return
        if self.config.Client_Language != 'zh-CN':
            logger.warning('BodyLabelShop: only the zh-CN game UI is supported, skip')
            return
        try:
            self.ensure_into_shop(GOTO_BODY_LABEL_SHOP, BODY_LABEL_SHOP_CHECK)
            self.process_body_label_purchase([CONSOLE_PRODUCTS[name] for name in selected], int(count_text))
        except NotEnoughMoneyError:
            logger.error('The rest of body labels is not enough to buy this product')
            self.ensure_back(BODY_LABEL_SHOP_CHECK)
        except PurchaseTimeTooLong as e:
            logger.warning(f'BodyLabelShop: {e}')
            self.ensure_back(BODY_LABEL_SHOP_CHECK)
