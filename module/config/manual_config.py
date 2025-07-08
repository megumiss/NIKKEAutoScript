from datetime import datetime, timedelta


class ManualConfig:
    SCHEDULER_PRIORITY = """
       Restart > Reward > DailyRecruit > Destruction > Mailbox > 
       DailyGift > WeeklyGift > MonthlyGift > 
       Commission > Shop > RubbishShop > Conversation > Interception > RookieArena > SpecialArena > ChampionArena > SimulationRoom > TribeTower > 
       Daily > Event > Event2 > SoloRaid > Coop > MissionPass > Liberation > Blablalink > TowerDaemon > CombatDaemon > EventDaemon
       """

    GENERAL_SHOP_PRIORITY = """GRATIS > CORE_DUST_CASE > ORNAMENT"""

    RUBBISH_SHOP_PRIORITY = """
       GEM
       > CORE_DUST_CASE
       """

    ARENA_SHOP_PRIORITY = """"""

    GENERAL_SHOP_PRODUCT = {"GRATIS": 1, "CORE_DUST_CASE": 1, "ORNAMENT": 1}

    RUBBISH_SHOP_PRODUCT = {
        "GEM": 1,
        "CORE_DUST_CASE": 2,
        "CREDIT_CASE": 3,
        "BATTLE_DATA_SET_CASE": 2,
        "GENERAL_TICKET": 1,
        "ELYSION_TICKET": 1,
        "MISSILIS_TICKET": 1,
        "TETRA_TICKET": 1,
        "PILGRIM_TICKET": 1,
        "ABNORMAL_TICKET": 1,
    }

    RUBBISH_SHOP_PRODUCT_COST = {
        # 5x500
        "GEM": 2500,
        # 6x100, 5x575
        "CORE_DUST_CASE": 3475,
        # 5x100, 10x180, 12x30
        "CREDIT_CASE": 2660,
        # 6x100, 5x575
        "BATTLE_DATA_SET_CASE": 3475,
        "GENERAL_TICKET": 400,
        "ELYSION_TICKET": 600,
        "MISSILIS_TICKET": 600,
        "TETRA_TICKET": 600,
        "PILGRIM_TICKET": 600,
        "ABNORMAL_TICKET": 600,
    }

    ARENA_SHOP_PRODUCT = {
        "ELECTRIC_CODE": 1,
        "FIRE_CODE": 1,
        "IRON_CODE": 1,
        "WATER_CODE": 1,
        "WIND_CODE": 1,
        "MANUAL_SELECTION_BOX": 1,
        "ORNAMENT": 1,
        "WEAPON": 1,
    }

    FORWARD_PORT_RANGE = (20000, 21000)

    BUTTON_OFFSET = 30
    BUTTON_MATCH_SIMILARITY = 0.74
    COLOR_SIMILAR_THRESHOLD = 10

    WAIT_BEFORE_SAVING_SCREEN_SHOT = 1

    ASSETS_FOLDER = "./assets"

    DROIDCAST_FILEPATH_LOCAL = "./bin/DroidCast/DroidCast_raw-release-1.0.apk"
    DROIDCAST_FILEPATH_REMOTE = "/data/local/tmp/DroidCast_raw.apk"

    DROIDCAST_RAW_FILEPATH_LOCAL = "./bin/DroidCast/DroidCastS-release-1.1.5.apk"
    DROIDCAST_RAW_FILEPATH_REMOTE = "/data/local/tmp/DroidCastS.apk"

    EVENTS = [
        {
            "event_id": "event_20250703s",
            "event_name": "OVER THE HORIZON",
            # small evnet
            "event_type": 2
        },
        {
            "event_id": "event_20250703l",
            "event_name": "OuteR: Automata",
            # large evnet
            "event_type": 1
        },
        {
            "event_id": "event_20250612",
            "event_name": "Memories Teller",
            # large evnet
            "event_type": 1
        },
    ]

    Error_ScreenshotLength = 1

    @property
    def SERVER(self):
        return "cn"
