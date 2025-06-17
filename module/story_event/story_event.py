from module.ui.ui import UI

class StoryEvent(UI, ArenaBase):

    def run(self):
        self.ui_ensure(page_story_event)
        self.config.task_delay(server_update=True)
