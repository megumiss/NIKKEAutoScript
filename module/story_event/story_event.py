from module.ui.ui import UI
from module.ui.assets import GOTO_BACK, MAIN_CHECK
from module.ui.page import *

class StoryEvent(UI):

    def run(self):
        # self.ui_ensure(page_event)
        self.ui_ensure(page_story_1)
        self.config.task_delay(server_update=True)
