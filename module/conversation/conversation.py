from module.base.langs import Langs
from module.base.timer import Timer
from module.base.utils import (
    _area_offset,
    crop,
    find_center,
    point2str,
)
from module.conversation.assets import *
from module.conversation.dialogue import Dialogue
from module.handler.assets import AUTO_CLICK_CHECK, CONFIRM_B
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.ui.assets import CONVERSATION_CHECK, GOTO_BACK, SKIP
from module.ui.page import page_conversation
from module.ui.ui import UI


class ChooseNextNIKKETooLong(Exception):
    pass


class NoOpportunitiesRemain(Exception):
    pass


class ConversationQueueIsEmpty(Exception):
    pass


class ConversationFavouriteDone(Exception):
    pass


class Conversation(UI):
    _confirm_timer = Timer(4, count=30)

    @property
    def opportunity_remain(self):
        model_type = self.config.Optimization_OcrModelType
        OPPORTUNITY_REMAIN = Digit(
            [OPPORTUNITY.area],
            name='OPPORTUNITY_REMAIN',
            model_type=model_type,
            lang='ch',
        )

        return int(OPPORTUNITY_REMAIN.ocr(self.device.image)['text'])

    @property
    def nikke_name(self) -> str:
        model_type = self.config.Optimization_OcrModelType
        NIKKE_NAME = Ocr(
            [COMMUNICATE_NIKKE_NAME.area],
            name='NIKKE_NAME',
            model_type=model_type,
            lang='ch',
        )

        return NIKKE_NAME.ocr(self.device.image)['text']

    @property
    def logs_quantity(self) -> int:
        model_type = self.config.Optimization_OcrModelType
        LOGS_QUANTITY = Digit(
            [COMMUNICATE_LOGS_QUANTITY.area],
            name='LOGS_QUANTITY',
            model_type=model_type,
            lang='ch',
        )

        return int(LOGS_QUANTITY.ocr(self.device.image)['text'])

    def answer_text(self, button: Button) -> str:
        area = _area_offset(button.area, (-515, -15, -15, 15))
        model_type = self.config.Optimization_OcrModelType
        ANSWER = Ocr(
            [area],
            name='ANSWER',
            model_type=model_type,
            lang='ch',
        )

        return ANSWER.ocr(self.device.image)['text']

    def get_next_target(self, skip_first_screenshot=True):
        # 是否进入到某个角色
        if DETAIL_CHECK.match(self.device.image, threshold=0.71) and GIFT.match_appear_on(
            self.device.image, threshold=10
        ):
            # 没有次数
            if OPPORTUNITY_B.match(self.device.image, offset=5, threshold=0.96, static=False):
                logger.warning('There are no remaining opportunities')
                raise NoOpportunitiesRemain

            # 只咨询收藏
            if self.config.Conversation_OnlyFavourite and not self.appear(
                FAVOURITE, offset=10, threshold=0.9, static=False
            ):
                logger.info('All favorite nikke consultations done')
                raise ConversationFavouriteDone

            # 跳过咨询：咨询已完成 / 好感度满且未开启OnlyLogsNotMax)/ 开启OnlyLogsNotMax且日志已满，默认忽略好感
            if (
                self.appear(COMMUNICATE_DONE, offset=5, threshold=0.95)  # 1. 咨询已完成
                or (
                    self.appear(RANK_MAX_CHECK, offset=5, threshold=0.95)  # 2. 好感度满
                    and not self.config.Conversation_OnlyLogsNotMax  # 且未开启OnlyLogsNotMax
                )
                or (
                    self.config.Conversation_OnlyLogsNotMax  # 3. 开启OnlyLogsNotMax
                    and self.logs_quantity >= 20  # 且日志已满
                )
            ):
                if self._confirm_timer.reached():
                    logger.warning('Perhaps all selected NIKKE already had a conversation')
                    raise ChooseNextNIKKETooLong
                # 下一个
                self.device.sleep(0.5)
                tmp_image = self.device.image
                logger.info('Click %s @ %s' % (point2str(690, 560), 'NEXT_NIKKE'))
                self.device.click_minitouch(690, 560)
                # 比较头像是否变化
                confirm_timer = Timer(3, count=3).start()
                while 1:
                    self.device.screenshot()
                    avatar = Button(
                        COMMUNICATE_NIKKE_AVATAR.area,
                        None,
                        button=COMMUNICATE_NIKKE_AVATAR.area,
                    )
                    avatar._match_init = True
                    avatar.image = crop(tmp_image, COMMUNICATE_NIKKE_AVATAR.area)
                    if not self.appear(avatar, offset=5, threshold=0.95):
                        break
                    if confirm_timer.reached():
                        logger.info('Click %s @ %s' % (point2str(690, 560), 'NEXT_NIKKE'))
                        self.device.click_minitouch(690, 560)
                        confirm_timer.reset()
            else:
                self._confirm_timer.reset()
                self.device.stuck_record_clear()
                self.device.click_record_clear()
                return
        else:
            try:
                if not self.opportunity_remain:
                    logger.warning('There are no remaining opportunities')
                    raise NoOpportunitiesRemain
                if CONVERSATION_CHECK.match(self.device.image, offset=5):
                    r = [
                        i.get('area')
                        for i in FAVOURITE_CHECK.match_several(self.device.image, threshold=0.71, static=False)
                    ]
                    r.sort(key=lambda x: x[1])
                    if len(r) > 0:
                        self.device.click_minitouch(*find_center(r[0]))
                    else:
                        self.device.click_minitouch(380, 450)
                    # TODO
                    self.device.sleep(2)
            except Exception:
                pass

        self.device.screenshot()
        self.get_next_target()

    def communicate(self):
        logger.hr('Start a conversation')
        self.get_next_target()
        self.ensure_wait_to_answer(self.nikke_name)

    def ensure_wait_to_answer(self, nikke: str, skip_first_screenshot=True):
        logger.info(f'Communicate NIKKE {nikke}')
        confirm_timer = Timer(1.6, count=2).start()
        click_timer = Timer(0.9)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # if click_timer.reached() \
            #         and COMMUNICATE_QUICKLY.match_appear_on(self.device.image, threshold=6) \
            #         and self.appear_then_click(COMMUNICATE_QUICKLY, offset=5, interval=3):
            #     confirm_timer.reset()
            #     click_timer.reset()
            #     continue

            # 咨询
            if (
                click_timer.reached()
                and COMMUNICATE.match_appear_on(self.device.image, threshold=6)
                and self.appear_then_click(COMMUNICATE, offset=5, interval=3)
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue
            # 咨询确认
            if self.appear(CONFIRM_B, offset=(5, 5), static=False):
                x, y = CONFIRM_B.location
                self.device.click_minitouch(x - 75, y)
                confirm_timer.reset()
                click_timer.reset()
                continue
            # 出现选项
            if self.appear(ANSWER_CHECK, offset=1, threshold=0.9, static=False):
                self.answer(nikke)
            elif (
                not COMMUNICATE.match_appear_on(self.device.image, threshold=6)
                and self.appear(DETAIL_CHECK, offset=(5, 5), static=False)
                and GIFT.match_appear_on(self.device.image, threshold=10)
                and confirm_timer.reached()
            ):
                return self.communicate()
            # 点击对话
            if self.appear(AUTO_CLICK_CHECK, offset=(30, 30), interval=0.3):
                self.device.click_minitouch(100, 100)
                logger.info('Click %s @ %s' % (point2str(100, 100), 'WAIT_TO_ANSWER'))
                click_timer.reset()
                continue

            if click_timer.reached() and not GIFT.match_appear_on(self.device.image, threshold=10):
                self.device.click_minitouch(100, 100)
                click_timer.reset()
                continue

    def answer(self, nikke: str, skip_first_screenshot=True):
        click_timer = Timer(0.5)
        answer_true_exist = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(DETAIL_CHECK, offset=(30, 30), static=False) and GIFT.match_appear_on(
                self.device.image, threshold=10
            ):
                break
            if click_timer.reached() and self.appear_then_click(
                RANK_INCREASE_COMFIRM, offset=10, static=False, interval=1
            ):
                click_timer.reset()
                continue
            if click_timer.reached() and self.appear_then_click(SKIP, offset=5, static=False):
                click_timer.reset()
                continue

            # 有红心为正确答案
            if click_timer.reached() and TEMPLATE_ANSWER_TRUE.match(self.device.image):
                answer_true_exist = True
                _, button = TEMPLATE_ANSWER_TRUE.match_result(self.device.image, name='ANSWER_TRUE')
                logger.info('Click %s @ %s' % (point2str(*button.location), 'ANSWER_TRUE'))
                self.device.click(button)
                click_timer.reset()
                continue
            # 识别答案
            if click_timer.reached() and not answer_true_exist:
                answers = TEMPLATE_ANSWER_CHECK.match_multi(self.device.image, similarity=0.9)
                if len(answers) > 1:
                    # 提取选项文本
                    answer_list = [self.answer_text(answer) for answer in answers]

                    # 获取正确答案
                    dialogue = Dialogue(f'./module/conversation/{Langs.DIALOGUE_JSON}')
                    right_answer = dialogue.get_answer(nikke, answer_list)
                    index = answer_list.index(right_answer)

                    # 点击对应选项
                    logger.info(
                        'Click %s @ %s',
                        point2str(*answers[index].location),
                        answer_list[index],
                    )
                    self.device.click(answers[index])
                    click_timer.reset()
                    continue
                else:
                    # 点击单个选项/找不到正确答案
                    if click_timer.reached() and self.appear_then_click(ANSWER_CHECK, offset=100, interval=3):
                        click_timer.reset()
                        continue
                    # 点击对话
                    if self.appear(AUTO_CLICK_CHECK, offset=(30, 30), interval=0.3):
                        self.device.click_minitouch(100, 100)
                        logger.info('Click %s @ %s' % (point2str(100, 100), 'WAIT_TO_ANSWER'))
                        click_timer.reset()
                        continue

        self.device.sleep(2.5)
        # return self.communicate()
        # self.ensure_back()

    def ensure_back(self, skip_first_screenshot=True):
        confirm_timer = Timer(2, count=3).start()
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(
                RANK_INCREASE_COMFIRM, offset=10, static=False, interval=1
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if (
                click_timer.reached()
                and not self.appear(CONVERSATION_CHECK, offset=(10, 10))
                and self.appear_then_click(GOTO_BACK, offset=(10, 10), interval=3)
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(CONVERSATION_CHECK, offset=(10, 10)) and confirm_timer.reached():
                break

        return self.communicate()

    def ensure_opportunity_remain(self):
        if self.opportunity_remain:
            return True

    def run(self):
        self.ui_ensure(page_conversation, confirm_wait=2)
        if self.ensure_opportunity_remain():
            self._confirm_timer.reset().start()
            try:
                self.communicate()
            except ChooseNextNIKKETooLong as e:
                logger.error(e)
            except NoOpportunitiesRemain as e:
                logger.error(e)
            except ConversationQueueIsEmpty as e:
                logger.error(e)
            except ConversationFavouriteDone:
                pass
        else:
            logger.info('There are no opportunities remaining')
        self.config.task_delay(server_update=True)
