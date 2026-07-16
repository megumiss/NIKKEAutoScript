from functools import cached_property

from module.base.button import Button, filter_buttons_in_area, merge_buttons
from module.base.decorator import Config
from module.base.timer import Timer
from module.base.utils import crop, sort_buttons_by_location
from module.conversation.assets import ANSWER_CHECK
from module.event.assets import *
from module.event.base import EventBase
from module.event.challenge import CHALLENGE_QUICKLY_DISABLE
from module.logger import logger
from module.simulation_room.assets import END_FIGHTING, FIGHT, FIGHT_QUICKLY
from module.tribe_tower.assets import NEXT_STAGE
from module.ui.assets import (
    FIGHT_CLOSE,
    FIGHT_QUICKLY_CHECK,
    FIGHT_QUICKLY_DISABLE,
    FIGHT_QUICKLY_FIGHT,
    FIGHT_QUICKLY_MAX,
    FIGHT_QUICKLY_MIN,
    SKIP,
)


class EventStory(EventBase):
    def STORY_STAGE_11(self, story):
        stages = {
            'story_1_normal': self.event_assets.STORY_1_NORMAL_STAGE_11,
            'story_1_normal_clear': self.event_assets.STORY_1_NORMAL_STAGE_11_CLEAR,
            'story_1_hard': self.event_assets.STORY_1_HARD_STAGE_11,
            'story_1_hard_clear': self.event_assets.STORY_1_HARD_STAGE_11_CLEAR,
            'story_2_normal': self.event_assets.STORY_2_NORMAL_STAGE_11,
            'story_2_normal_clear': self.event_assets.STORY_2_NORMAL_STAGE_11_CLEAR,
            'story_2_hard': self.event_assets.STORY_2_HARD_STAGE_11,
            'story_2_hard_clear': self.event_assets.STORY_2_HARD_STAGE_11_CLEAR,
        }
        return stages[story]

    def STORY_STAGE_12(self, story):
        stages = {
            'story_1_normal': self.event_assets.STORY_1_NORMAL_STAGE_12,
            'story_1_normal_clear': self.event_assets.STORY_1_NORMAL_STAGE_12_CLEAR,
            'story_1_hard': self.event_assets.STORY_1_HARD_STAGE_12,
            'story_1_hard_clear': self.event_assets.STORY_1_HARD_STAGE_12_CLEAR,
            'story_2_normal': self.event_assets.STORY_2_NORMAL_STAGE_12,
            'story_2_normal_clear': self.event_assets.STORY_2_NORMAL_STAGE_12_CLEAR,
            'story_2_hard': self.event_assets.STORY_2_HARD_STAGE_12,
            'story_2_hard_clear': self.event_assets.STORY_2_HARD_STAGE_12_CLEAR,
        }
        return stages[story]

    def get_story_stage_pending_buttons(self, story: str) -> list:
        """
        Dynamically loads pending stage buttons from event assets.
        Searches for attributes starting with a prefix, like:
        - STORY_1_NORMAL_STAGE_PENDING
        - STORY_1_NORMAL_STAGE_PENDING_2
        """
        prefix = f'{story.upper()}_STAGE_PENDING'
        buttons = []

        for attr_name in dir(self.event_assets):
            if attr_name.startswith(prefix):
                suffix = attr_name[len(prefix) :]
                if suffix == '' or (suffix.startswith('_') and suffix[1:].isdigit()):
                    buttons.append(getattr(self.event_assets, attr_name))

        if not buttons:
            logger.warning(f"No pending stage buttons found for story '{story}' using prefix '{prefix}'")
        else:
            logger.info(f"Found {len(buttons)} pending stage buttons for story '{story}'")

        return buttons

    def find_pending_stage_by_grid(self, open_story):
        finder = self.event.pending_finder
        x1, y1, x2, y2 = finder['area']
        rows = finder['rows']
        columns = finder['columns']
        # 上下方向决定行顺序，左右方向决定每行内的点顺序。
        row_indices = range(rows) if finder['vertical_direction'] == 'top_to_bottom' else range(rows - 1, -1, -1)
        column_indices = (
            range(columns) if finder['horizontal_direction'] == 'left_to_right' else range(columns - 1, -1, -1)
        )
        cell_width = (x2 - x1) / columns
        cell_height = (y2 - y1) / rows
        # 配置区域均分后，按指定方向生成待点击的中心点。
        points = [
            (int(x1 + (column + 0.5) * cell_width), int(y1 + (row + 0.5) * cell_height))
            for row in row_indices
            for column in column_indices
        ]
        shift_x = 0
        shift_y = 0
        height, width = self.device.image.shape[:2]

        logger.info(
            f'Grid pending finder: {len(points)} points, order={finder["vertical_direction"]}/'
            f'{finder["horizontal_direction"]}'
        )
        for index, (base_x, base_y) in enumerate(points, start=1):
            point = base_x + shift_x, base_y + shift_y
            if not 0 <= point[0] < width or not 0 <= point[1] < height:
                logger.warning(f'Grid pending point {index} out of screen after shift: {point}')
                return 'aborted'

            # 保存点击前中心点附近 30x30 图块，供恢复列表后计算地图偏移。
            anchor_area = point[0] - 15, point[1] - 15, point[0] + 15, point[1] + 15
            anchor = Button(area=anchor_area, color=(0, 0, 0), button=anchor_area, name=f'PENDING_GRID_ANCHOR_{index}')
            anchor.load_color(self.device.image)
            anchor._match_init = True
            logger.info(f'Click grid pending point {index}/{len(points)}: {point}')
            self.device.click(anchor)

            # 点击后只接受“正常关卡且快速战斗不可用”为 pending。
            result = 'timeout'
            for _ in range(10):
                self.device.screenshot()
                if self.appear(self.event_assets.STORY_STAGE_CHECK, offset=30):
                    if not self.appear(FIGHT_QUICKLY, threshold=20):
                        logger.info(f'Grid pending stage found at {point}')
                        return 'found'
                    result = 'normal_stage'
                    break
                if self.appear(STORY_STAGE_NOT_UNLOCKED, offset=30):
                    result = 'not_unlocked'
                    break
                if self.appear(STORY_STAGE_REPEAT_FORBIDDEN, offset=30):
                    result = 'repeat_forbidden'
                    break

            if result == 'normal_stage':
                # 点到可快速战斗的普通关卡时，关闭弹窗后继续扫描。
                while 1:
                    self.device.screenshot()
                    if self.appear_then_click(FIGHT_CLOSE, offset=30, interval=1):
                        continue
                    if not self.appear(FIGHT_CLOSE, offset=30):
                        break
            elif result in ('not_unlocked', 'repeat_forbidden'):
                logger.info(f'Grid pending point {index}: {result}, wait for dialog to close')
                while 1:
                    self.device.screenshot()
                    if not self.appear(STORY_STAGE_NOT_UNLOCKED, offset=30) and not self.appear(
                        STORY_STAGE_REPEAT_FORBIDDEN, offset=30
                    ):
                        logger.info(f'Grid pending point {index}: {result}, dialog closed')
                        break

            # 在原点击点周围搜索锚点图块，并将所得位移应用到后续中心点。
            # search_area = point[0] - 120, point[1] - 120, point[0] + 120, point[1] + 120
            # if not anchor.match(crop(self.device.image, search_area), threshold=0.9, static=False):
            #     logger.warning(f'Grid pending point {index}: unable to match 30x30 anchor after dialog')
            #     return 'aborted'
            # matched_x = search_area[0] + anchor.location[0]
            # matched_y = search_area[1] + anchor.location[1]
            # shift_x += matched_x - point[0]
            # shift_y += matched_y - point[1]
            # logger.info(f'Grid pending point {index}: screen shift=({shift_x}, {shift_y})')

        logger.info('Grid pending finder exhausted all points')
        return 'exhausted'

    @cached_property
    def team_nikke_locations(self):
        """
        nikke队伍坐标列表倒序
        """
        return [(610, 360), (485, 360), (360, 360), (235, 360), (115, 360)]

    @Config.when(EVENT_TYPE=(1, 3))
    def story(self, skip_first_screenshot=True):
        logger.hr('START EVENT STORY', 2)
        click_timer = Timer(0.3)

        logger.info('Finding opened event story')
        open_story = 'story_1_normal'
        # 判断并进入最新的关卡列表
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 检查story2是否开启，未开启则进入1
            if self.appear(self.event_assets.EVENT_GOTO_STORY_1, offset=10) and self.appear(
                self.event_assets.EVENT_GOTO_STORY_2_LOCKED, offset=10
            ):
                logger.info('Find opened event story 1')
                if self.config.EventInfo_StoryPart == 'Story_2':
                    logger.error('The event stage/difficulty select wrong')
                    self.back_to_event()
                    return

                # 进入story1
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.EVENT_GOTO_STORY_1, offset=10, interval=5
                    ):
                        click_timer.reset()
                        continue

                    # story1主页
                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.STORY_1_CHECK, offset=(10, 10), interval=3
                    ):
                        click_timer.reset()
                        continue

                    # story1列表页面
                    if not self.appear(self.event_assets.EVENT_GOTO_STORY_1, offset=10) and self.appear(
                        self.event_assets.STORY_1_NORMAL, threshold=20
                    ):
                        click_timer.reset()
                        break
                logger.info('Open event story 1')
                break

            # 检查story2是否开启，开启则进入2
            if (
                self.appear(self.event_assets.EVENT_GOTO_STORY_1, offset=10)
                and not self.appear(self.event_assets.EVENT_GOTO_STORY_2_LOCKED, offset=10)
            ) or self.appear(self.event_assets.EVENT_GOTO_STORY_2, offset=10):
                logger.info('Find opened event story 2')
                if self.config.EventInfo_StoryPart == 'Story_1':
                    logger.error('The event stage/difficulty select wrong')
                    self.back_to_event()
                    return

                # 进入story2，story2更新后需要重新截图
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.EVENT_GOTO_STORY_2, offset=10, interval=5
                    ):
                        click_timer.reset()
                        continue

                    # story2主页
                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.STORY_2_CHECK, offset=10, interval=3
                    ):
                        click_timer.reset()
                        continue

                    # story2困难解锁
                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.STORY_2_HARD_UNLOCK, offset=10, interval=1
                    ):
                        open_story = 'story_2_hard'
                        click_timer.reset()
                        continue

                    # story2普通难度列表页面
                    if not self.appear(self.event_assets.EVENT_GOTO_STORY_2, offset=10) and self.appear(
                        self.event_assets.STORY_2_NORMAL, threshold=10
                    ):
                        click_timer.reset()
                        break

                    # story2困难难度列表页面
                    if not self.appear(self.event_assets.EVENT_GOTO_STORY_2, offset=10) and self.appear(
                        self.event_assets.STORY_2_HARD, threshold=10
                    ):
                        click_timer.reset()
                        break
                self.device.sleep(2)
                logger.info('Open event story 2')

                self.device.screenshot()
                # 困难难度关闭
                if self.appear(self.event_assets.STORY_2_NORMAL, threshold=10) and self.appear(
                    self.event_assets.STORY_2_HARD_LOCKED, offset=10
                ):
                    logger.info('Find difficulty normal opened')
                    if self.config.EventInfo_StoryDifficulty == 'Hard':
                        logger.error('The event stage/difficulty select wrong')
                        self.back_to_event()
                        return
                    open_story = 'story_2_normal'
                    logger.info('Open event story 2 normal')
                    break

                # 困难难度开启，当前页面是普通
                if self.appear(self.event_assets.STORY_2_NORMAL, threshold=10) and not self.appear(
                    self.event_assets.STORY_2_HARD_LOCKED, offset=10
                ):
                    open_story = 'story_2_hard'

                # 困难难度开启，当前页面是困难
                if self.appear(self.event_assets.STORY_2_HARD, threshold=10):
                    open_story = 'story_2_hard'

                if open_story == 'story_2_hard':
                    logger.info('Find difficulty hard opened')
                    if self.config.EventInfo_StoryDifficulty == 'Normal':
                        logger.error('The event stage/difficulty select wrong')
                        self.back_to_event()
                        return

                    while 1:
                        if skip_first_screenshot:
                            skip_first_screenshot = False
                        else:
                            self.device.screenshot()

                        # story2困难难度切换
                        if click_timer.reached() and self.appear_then_click(
                            self.event_assets.STORY_2_HARD_HIDDEN, threshold=10
                        ):
                            click_timer.reset()
                            continue

                        # story2困难难度列表页面
                        if self.appear(self.event_assets.STORY_2_HARD, threshold=10):
                            click_timer.reset()
                            break

                    logger.info('Open event story 2 hard')
                    break

        # 关卡处理
        self.device.sleep(2)
        self.device.screenshot()
        # 推图
        if self.config.StoryStage_AutoPush:
            logger.info(f'Start checking push stage for {open_story}')
            self.find_and_push_stage(open_story)

        # 扫荡，检查倒数第二关
        if self.config.StoryStage_Sweep:
            logger.info(f'Start sweeping: {open_story}')
            # self.ensure_sroll_to_bottom(x1=(680, 800), x2=(680, 460), count=3)
            self.find_and_sweep_stage(open_story)

        # 回到活动主页
        self.back_to_event()

    @Config.when(EVENT_TYPE=2)
    def story(self, skip_first_screenshot=True):
        logger.hr('START EVENT STORY', 2)
        click_timer = Timer(0.3)

        open_story = 'story_1_normal'
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 进入关卡列表
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                # story主页
                if click_timer.reached() and self.appear_then_click(
                    self.event_assets.STORY_1_CHECK, offset=(10, 10), interval=3
                ):
                    click_timer.reset()
                    continue

                # story困难解锁，困难更新后需要重新截图
                if click_timer.reached() and self.appear_then_click(
                    self.event_assets.STORY_1_HARD_UNLOCK, offset=10, interval=1
                ):
                    click_timer.reset()
                    continue

                # story普通难度列表页面
                if not self.appear_then_click(self.event_assets.STORY_1_CHECK, offset=(10, 10)) and self.appear(
                    self.event_assets.STORY_1_NORMAL, threshold=20
                ):
                    click_timer.reset()
                    break

                # story困难难度列表页面，困难更新后需要重新截图
                if not self.appear_then_click(self.event_assets.STORY_1_CHECK, offset=(10, 10)) and self.appear(
                    self.event_assets.STORY_1_HARD, threshold=10
                ):
                    click_timer.reset()
                    break

            self.device.sleep(1)
            self.device.screenshot()
            # 困难难度关闭
            if self.appear(self.event_assets.STORY_1_NORMAL, threshold=20) and self.appear(
                self.event_assets.STORY_1_HARD_LOCKED, offset=10
            ):
                logger.info('Find difficulty normal opened')
                if self.config.EventInfo_StoryDifficulty == 'Hard':
                    logger.error('The event stage/difficulty select wrong')
                    self.back_to_event()
                    return
                open_story = 'story_1_normal'
                logger.info('Open event story normal')
                break

            # 困难难度开启，当前页面是普通
            if self.appear(self.event_assets.STORY_1_NORMAL, threshold=20) and not self.appear(
                self.event_assets.STORY_1_HARD_LOCKED, offset=10
            ):
                open_story = 'story_1_hard'

            # 困难难度开启，当前页面是困难
            if self.appear(self.event_assets.STORY_1_HARD, threshold=10):
                open_story = 'story_1_hard'

            if open_story == 'story_1_hard':
                logger.info('Find difficulty hard opened')
                if self.config.EventInfo_StoryDifficulty == 'Normal':
                    logger.error('The event stage/difficulty select wrong')
                    self.back_to_event()
                    return

                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    # story困难难度切换
                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.STORY_1_HARD_HIDDEN, threshold=10
                    ):
                        click_timer.reset()
                        continue

                    # story困难难度列表页面
                    if self.appear(self.event_assets.STORY_1_HARD, threshold=10):
                        click_timer.reset()
                        break

                logger.info('Open event story hard')
                break

        self.device.sleep(2)
        self.device.screenshot()
        # 推图
        if self.config.StoryStage_AutoPush:
            logger.info(f'Start checking push stage for {open_story}')
            self.find_and_push_stage(open_story)

        # 扫荡，检查倒数第二关
        if self.config.StoryStage_Sweep:
            logger.info(f'Start sweeping: {open_story}')
            # self.ensure_sroll_to_bottom(x1=(680, 800), x2=(680, 460), count=3)
            self.find_and_sweep_stage(open_story)

        # 回到活动主页
        self.back_to_event()

    def find_and_push_stage(self, open_story):
        grid_mode = self.event.pending_finder is not None and self.event.pending_finder['mode'] == 'grid'
        pending_buttons = [] if grid_mode else self.get_story_stage_pending_buttons(open_story)
        has_pending_stage = grid_mode
        if not grid_mode and pending_buttons:
            for button in pending_buttons:
                if self.appear_with_flip(button, offset=30, threshold=0.9, color_threshold=20, static=False):
                    has_pending_stage = True
                    break
        # 如果最后一关没有clear
        if (
            not self.appear(self.STORY_STAGE_12(open_story), offset=80, threshold=0.9)
            and not self.appear(self.STORY_STAGE_12(f'{open_story}_clear'), offset=80, threshold=0.9)
            and has_pending_stage
        ):
            if grid_mode:
                logger.info('Grid pending finder enabled, start pushing loop')
            else:
                logger.info('Pending stage found, start pushing loop')
            # 判断有票和组队状态
            found = False
            while 1:
                self.device.screenshot()

                # 打开关卡
                if grid_mode:
                    if not found and not self.appear(self.event_assets.STORY_STAGE_CHECK, offset=30):
                        result = self.find_pending_stage_by_grid(open_story)
                        if result == 'found':
                            logger.info(f'Grid pending finder {result}')
                            found = True
                            continue
                else:
                    was_clicked = False
                    for button in pending_buttons:
                        if self.appear_with_flip_then_click(
                            button,
                            offset=30,
                            click_offset=self.event.pending_click_offset,
                            threshold=0.9,
                            color_threshold=20,
                            interval=5,
                            static=False,
                        ):
                            logger.info(f'Click pending stage {button.name} to enter')
                            was_clicked = True
                            break
                    if was_clicked:
                        continue

                # 组队
                if self.appear(self.event_assets.STORY_STAGE_CHECK, offset=30) and self.appear(
                    STAGE_TEAM_NOT_SELECT, offset=30
                ):
                    logger.info('Team up before story push')
                    while 1:
                        self.device.screenshot()

                        if self.appear(TEAM_NIKKE_NOT_SELECT_5, offset=30):
                            break
                        if self.appear_then_click(STAGE_TEAM_NOT_SELECT, offset=30, interval=1):
                            continue

                    self.team_up()
                    continue

                # 没票退出
                if (
                    self.appear(self.event_assets.STORY_STAGE_CHECK, offset=30)
                    and not self.appear(FIGHT, threshold=20)
                    and self.appear_then_click(FIGHT_CLOSE, offset=10, interval=1)
                ):
                    logger.warning('Story push done, no ticket')
                    # 没票直接退出
                    self.back_to_event()
                    return

                # 进入战斗
                if (
                    self.appear(self.event_assets.STORY_STAGE_CHECK, offset=30)
                    and not self.appear(FIGHT_QUICKLY, threshold=10)
                    and self.appear_then_click(FIGHT, threshold=20, interval=5)
                ):
                    continue

                # 剧情选项
                if self.appear_then_click(ANSWER_CHECK, offset=100, interval=3):
                    continue
                # 跳过剧情
                if self.appear_then_click(SKIP, offset=(150, 10), interval=1):
                    continue
                # 剧情推完提示
                if self.appear_then_click(STAGE_COMPLETE_CONFIRM, offset=10, interval=1):
                    continue

                # 下一关卡
                if self.appear_then_click(NEXT_STAGE, offset=(100, 30), interval=1):
                    self.device.stuck_record_clear()
                    self.device.click_record_clear()
                    continue

                # 点击区域跳转
                if self.appear_then_click(FIELD_CHANGE, offset=30, interval=1):
                    continue

                # 回到活动主页
                if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                    logger.info('Returned to event page, restart story loop')
                    return self.story()

                # 战斗结束，但是没有找到下一关卡
                if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                    logger.info('Story push done, fighting ended')
                    continue

                # 关卡推完回到了关卡列表
                if self.appear(self.STORY_STAGE_12(open_story), offset=80, threshold=0.9) and self.appear(
                    self.STORY_STAGE_12(f'{open_story}_clear'), offset=80, threshold=0.9
                ):
                    logger.warning('Story push done, stage 12 cleared')
                    # self.back_to_event()
                    return
        else:
            logger.info('No pending stage found or Stage 12 cleared, check sweep')

    def find_and_sweep_stage(self, open_story):
        click_timer = Timer(0.3)
        if self.appear(self.STORY_STAGE_11(open_story), offset=80, threshold=0.9) and self.appear(
            self.STORY_STAGE_11(f'{open_story}_clear'), offset=80, threshold=0.9
        ):
            max_clicks = 0
            while 1:
                self.device.screenshot()

                # 战斗结束
                if click_timer.reached() and self.appear(END_FIGHTING, offset=30):
                    while 1:
                        self.device.screenshot()
                        if not self.appear(END_FIGHTING, offset=30):
                            click_timer.reset()
                            break
                        if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                            click_timer.reset()
                            continue
                    break

                # 关卡检查
                if (
                    click_timer.reached()
                    and self.appear(self.STORY_STAGE_11(open_story), offset=80, threshold=0.9)
                    and self.appear_then_click(self.STORY_STAGE_11(f'{open_story}'), offset=80, threshold=0.9)
                ):
                    self.device.sleep(0.5)
                    click_timer.reset()
                    continue

                # 快速战斗
                if (
                    click_timer.reached()
                    and self.appear(self.event_assets.STORY_STAGE_CHECK, offset=30)
                    and self.appear_then_click(FIGHT_QUICKLY, threshold=20, interval=1)
                ):
                    click_timer.reset()
                    continue

                # 票max
                if (
                    click_timer.reached()
                    and max_clicks < 3
                    and self.appear(FIGHT_QUICKLY_CHECK, offset=10)
                    and self.appear_then_click(FIGHT_QUICKLY_MAX, offset=30, threshold=0.99, interval=1)
                ):
                    max_clicks += 1
                    self.device.sleep(0.3)
                    click_timer.reset()
                    continue

                # 进行战斗
                if (
                    click_timer.reached()
                    and self.appear(FIGHT_QUICKLY_CHECK, offset=10)
                    and self.appear(FIGHT_QUICKLY_MIN, offset=30, threshold=0.99)
                    and self.appear_then_click(FIGHT_QUICKLY_FIGHT, threshold=20, interval=1)
                ):
                    click_timer.reset()
                    continue

                # 没票
                if (
                    click_timer.reached()
                    and self.appear(self.event_assets.STORY_STAGE_CHECK, offset=10)
                    and self.appear(CHALLENGE_QUICKLY_DISABLE, threshold=10)
                    and self.appear_then_click(FIGHT_CLOSE, offset=10, interval=1)
                ):
                    break
        else:
            logger.info('Stage 11 not cleared')
            return
        logger.info('Stage 11 clear done')

    def team_up(self):
        logger.hr('Team up before story push', 2)

        # 点击自动编队
        while 1:
            self.device.screenshot()

            # 第五个位置有妮姬
            if not self.appear(TEAM_NIKKE_NOT_SELECT_5, offset=10, interval=1):
                logger.info('Team up auto selected')
                break
            # 自动编队
            if self.appear_then_click(TEAM_NIKKE_AUTO, offset=10, interval=1):
                continue

        # 替换加成nikke
        if self.appear(BOUNS_100_CHECK, offset=10):
            logger.info('Team up 100% bouns nikke selected')
        else:
            # 滑动到第二行
            self.ensure_sroll((360, 900), (360, 600), speed=30, count=1, delay=0.5)

            # 找到所有的加成nikke
            self.device.screenshot()
            nikkes = TEMPLATE_BOUNS_PER.match_multi(self.device.image, similarity=0.75, name='BOUNS_PER')
            # 合并重复的nikkke
            nikkes = merge_buttons(nikkes, x_threshold=30, y_threshold=30)
            # 过滤掉非列表区域的nikke
            nikkes = filter_buttons_in_area(nikkes, y_range=(620, 1150))
            # 按照坐标排序
            nikkes = sort_buttons_by_location(nikkes)
            logger.info(f'Find bouns nikkes: {len(nikkes)}')
            # 如果有8个，去掉第一个
            if len(nikkes) == 8:
                nikkes = nikkes[1:]
                logger.info('Delete first bouns nikke')

            # 队伍某个位置已经放置了加成nikke的检查button
            check_buttons = [
                TEAM_NIKKE_BOUNS_CHECK_5,
                TEAM_NIKKE_BOUNS_CHECK_4,
                TEAM_NIKKE_BOUNS_CHECK_3,
                TEAM_NIKKE_BOUNS_CHECK_2,
                TEAM_NIKKE_BOUNS_CHECK_1,
            ]
            for nikke in nikkes:
                self.device.screenshot()

                # 100%加成
                if self.appear(BOUNS_100_CHECK, offset=10):
                    logger.info('Team up 100% bouns nikke selected')
                    break

                # 如果某个位置没有放置加成nikke，先取消这个nikke，再放置一个加成nikke
                for index, button in enumerate(check_buttons):
                    if not self.appear(button, offset=(10, 10)):
                        # 取消该位置选择的nikke
                        while 1:
                            self.device.screenshot()

                            if self.appear(TEAM_NIKKE_NOT_SELECT, offset=(500, 10), threshold=0.6):
                                break
                            # 要取消的nikke序号

                            self.device.click_minitouch(
                                self.team_nikke_locations[index][0], self.team_nikke_locations[index][1]
                            )
                            self.device.sleep(0.3)

                        # 放置一个加成nikke
                        self.device.click(nikke)
                        self.device.sleep(0.3)
                        break

        # 储存队伍
        while 1:
            self.device.screenshot()

            # 回到关卡弹出界面
            if self.appear(self.event_assets.STORY_STAGE_CHECK, offset=30):
                break
            # 保存队伍
            if self.appear_then_click(SAVE_TEAM, offset=30, interval=1):
                continue
