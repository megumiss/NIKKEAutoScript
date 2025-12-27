import traceback

from module.handler.assets import CONFIRM_B
from module.ui.assets import *


class Page:
    parent = None

    def __init__(self, check_button):
        self.check_button = check_button
        self.links = {}
        (filename, line_number, function_name, text) = traceback.extract_stack()[-2]
        self.name = text[: text.find('=')].strip()

    def __eq__(self, other):
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)

    def __str__(self):
        return self.name

    def link(self, button, destination):
        self.links[destination] = button


# Main
page_main = Page(MAIN_CHECK)

# Unknown
page_unknown = Page(None)
page_unknown.link(button=GOTO_MAIN, destination=page_main)

# Reward
page_reward = Page(REWARD_CHECK)
page_reward.link(button=REWARD_GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_REWARD, destination=page_reward)

# Destroy
page_destroy = Page(DESTROY_CHECK)
page_destroy.link(button=DESTROY_GOTO_REWARD, destination=page_reward)
page_reward.link(button=REWARD_GOTO_DESTROY, destination=page_destroy)

# friend
page_friend = Page(FRIEND_CHECK)
page_friend.link(button=FRIEND_GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_FRIEND, destination=page_friend)

# daily
page_daily = Page(DAILY_CHECK)
page_daily.link(button=DAILY_GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_DAILY, destination=page_daily)

# shop
page_shop = Page(SHOP_CHECK)
page_shop.link(button=GOTO_BACK, destination=page_main)
# page_shop.link(button=GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_SHOP, destination=page_shop)

# cash shop
page_cash_shop = Page(CASH_SHOP_CHECK)
page_cash_shop.link(button=GOTO_BACK, destination=page_main)
# page_cash_shop.link(button=GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_CASH_SHOP, destination=page_cash_shop)

# team
page_team = Page(TEAM_CHECK)
page_team.link(button=TEAM_GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_TEAM, destination=page_team)

# inventory
page_inventory = Page(INVENTORY_CHECK)
page_inventory.link(button=TEAM_GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_INVENTORY, destination=page_inventory)

# pass
page_pass = Page(PASS_CHECK)
page_pass.link(button=PASS_GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_PASS, destination=page_pass)

# conversation
page_conversation = Page(CONVERSATION_CHECK)
page_conversation.link(button=GOTO_BACK, destination=page_team)
page_conversation.link(button=GOTO_MAIN, destination=page_main)
page_team.link(button=TEAM_GOTO_CONVERSATION, destination=page_conversation)

# episode viewing
page_episode_viewing = Page(EPISODE_VIEWING_CHECK)
page_conversation.link(button=CONVERSATION_GOTO_EPISODE_VIEWING, destination=page_episode_viewing)
page_episode_viewing.link(button=EPISODE_VIEWING_GOTO_CONVERSATION, destination=page_conversation)

# ark
page_ark = Page(ARK_CHECK)
page_ark.link(button=GOTO_BACK, destination=page_main)
# page_ark.link(button=GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_ARK, destination=page_ark)

# tribe tower
page_tribe_tower = Page(TRIBE_TOWER_CHECK)
page_tribe_tower.link(button=GOTO_BACK, destination=page_ark)
page_tribe_tower.link(button=GOTO_MAIN, destination=page_main)
page_ark.link(button=ARK_GOTO_TRIBE_TOWER, destination=page_tribe_tower)

# interception
page_interception = Page(INTERCEPTION_CHECK)
page_interception.link(button=GOTO_BACK, destination=page_ark)
page_interception.link(button=GOTO_MAIN, destination=page_main)
page_ark.link(button=ARK_GOTO_INTERCEPTION, destination=page_interception)

# interception
page_special_interception = Page(SPECIAL_INTERCEPTION_CHECK)
page_special_interception.link(button=GOTO_BACK, destination=page_interception)
page_special_interception.link(button=GOTO_MAIN, destination=page_main)
# page_interception.link(button=INTERCEPTION_GOTO_SPECIAL_INTERCEPTION, destination=page_special_interception)

# simulation room
page_simulation_room = Page(SIMULATION_ROOM_CHECK)
page_simulation_room.link(button=GOTO_BACK, destination=page_ark)
page_simulation_room.link(button=GOTO_MAIN, destination=page_main)
page_ark.link(button=ARK_GOTO_SIMULATION_ROOM, destination=page_simulation_room)

# arena
page_arena = Page(ARENA_CHECK)
page_arena.link(button=GOTO_BACK, destination=page_ark)
page_arena.link(button=GOTO_MAIN, destination=page_main)
page_ark.link(button=ARK_GOTO_ARENA, destination=page_arena)

# rookie arena
page_rookie_arena = Page(ROOKIE_ARENA_CHECK)
page_rookie_arena.link(button=GOTO_BACK, destination=page_arena)
page_rookie_arena.link(button=GOTO_MAIN, destination=page_main)
page_arena.link(button=ARENA_GOTO_ROOKIE_ARENA, destination=page_rookie_arena)

# special arena
page_special_arena = Page(SPECIAL_ARENA_CHECK)
page_special_arena.link(button=GOTO_BACK, destination=page_arena)
page_special_arena.link(button=GOTO_MAIN, destination=page_main)
page_arena.link(button=ARENA_GOTO_SPECIAL_ARENA, destination=page_special_arena)

# outpost
page_outpost = Page(OUTPOST_CHECK)
page_outpost.link(button=GOTO_BACK, destination=page_main)
# page_outpost.link(button=GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_OUTPOST, destination=page_outpost)

# commission
page_commission = Page(COMMISSION_CHECK)
page_commission.link(button=COMMISSION_GOTO_OUTPOST, destination=page_outpost)
# page_outpost.link(button=GOTO_MAIN, destination=page_main)
page_outpost.link(button=OUTPOST_GOTO_COMMISSION, destination=page_commission)

# mailbox
page_mailbox = Page(MAILBOX_CHECK)
page_mailbox.link(button=MAILBOX_GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_MAILBOX, destination=page_mailbox)

# recruit
page_recruit = Page(RECRUIT_CHECK)
page_recruit.link(button=TEAM_GOTO_MAIN, destination=page_main)
page_main.link(button=MAIN_GOTO_RECRUIT, destination=page_recruit)

# ranking
page_ranking = Page(RANKING_CHECK)
page_ranking.link(button=GOTO_BACK, destination=page_ark)
page_ranking.link(button=GOTO_MAIN, destination=page_main)
page_ark.link(button=ARK_GOTO_RANKING, destination=page_ranking)

# facility
page_synchro_facility = Page(FACILITY_SYNCHRO)
page_synchro_facility.link(button=FACILITY_BACK, destination=page_outpost)
page_recycling_facility = Page(FACILITY_RECYCLING)
page_recycling_facility.link(button=FACILITY_BACK, destination=page_outpost)

# synchro
page_synchro = Page(SYNCHRO_CHECK)
page_outpost.link(button=OUTPOST_GOTO_SYNCHRO, destination=page_synchro_facility)
page_synchro_facility.link(button=FACILITY_ENTER, destination=page_synchro)
page_synchro.link(button=GOTO_BACK, destination=page_synchro_facility)
page_synchro_facility.link(button=FACILITY_BACK, destination=page_outpost)

# recycling
page_recycling = Page(RECYCLING_CHECK)
page_outpost.link(button=OUTPOST_GOTO_RECYCLING, destination=page_recycling_facility)
page_recycling_facility.link(button=FACILITY_ENTER, destination=page_recycling)
page_recycling.link(button=GOTO_BACK, destination=page_recycling_facility)
page_recycling_facility.link(button=FACILITY_BACK, destination=page_outpost)

# campaign select
page_campaign_select = Page(CAMPAIGN_SELECT_CHECK)
page_campaign_select.link(button=CAMPAIGN_SELECT_CLOSE, destination=page_main)
page_main.link(button=MAIN_GOTO_CAMPAIGN_SELECT, destination=page_campaign_select)

# surface
page_surface = Page(SURFACE_CHECK)
# surface back
page_surface_back = Page(SURFACE_BACK_CHECK)
page_surface.link(button=GOTO_BACK, destination=page_surface_back)
page_campaign_select.link(button=CAMPAIGN_SELECT_GOTO_SURFACE, destination=page_surface)

page_surface_back.link(button=CONFIRM_B, destination=page_main)
page_surface_back.link(button=SURFACE_BACK_CANCEL, destination=page_surface)

# breaking news
page_breaking_news = Page(BREAKING_NEWS_CHECK)
page_breaking_news.link(button=GOTO_BACK, destination=page_main)
page_main.link(button=MAIN_CHECK, destination=page_breaking_news)
