from datetime import datetime, timedelta

import inflection

from module.config.events import EVENTS


class ManualConfig:
    SCHEDULER_PRIORITY = """
       Restart > Reward > DailyRecruit > Destruction > Mailbox >
       StepUpGift > DailyGift > WeeklyGift > MonthlyGift >
       Commission > EpisodeViewing > Recycling > Synchro > OutpostStory >
       Shop > RubbishShop > Conversation >
       Interception > RookieArena > SpecialArena > ChampionArena > SimulationRoom > Overclock > TribeTower >
       Daily > Event > Event2 > SoloRaid > UnionRaid > Coop > MissionPass > Liberation >
       SurfaceDaily >
       BlaDaily > BlaCDK > BlaExchange >
       WarehouseStats > SpecialArenaWatch >
       Notify >
       AutoTower > SemiCombat > Highlights > BlaCDKManual > ScreenRotate > UpdateHosts
       """

    GENERAL_SHOP_PRIORITY = """GRATIS"""

    # 执行时间管理页（module/webui/api/routes_schedule.py）
    # 整行置灰只读的任务（仅当时间字段对该任务完全不生效时使用）
    SCHEDULE_LOCKED_TASKS = set()
    # 不允许切换周期的任务：Restart 承担每日服务器刷新重启职责、Reward 是每日固定收获，
    # 两者仅执行时间可改；Restart 的启用状态额外被强制常开（override.yaml）
    SCHEDULE_CADENCE_LOCKED_TASKS = {
        'Restart', 'Reward',
    }

    ARENA_SHOP_PRIORITY = """"""

    GENERAL_SHOP_PRODUCT = {"GRATIS": 1, "CORE_DUST_CASE": 1, "ORNAMENT": 1}

    RUBBISH_SHOP_CORE_PRIORITY = """GEM > CORE_DUST_CASE"""

    RUBBISH_SHOP_CORE_PRODUCT = {
        "GEM": 1,
        "CORE_DUST_CASE": 2,
        "CREDIT_CASE": 3,
        "CREDIT": 1,
        "BATTLE_DATA_SET_CASE": 2,
        "GROWTH_SET": 4,
        "ORNAMENT": 1,
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
        "GROWTH_SET": 2700,
        "ORNAMENT": 500,
        "GENERAL_TICKET": 400,
        "ELYSION_TICKET": 600,
        "MISSILIS_TICKET": 600,
        "TETRA_TICKET": 600,
        "PILGRIM_TICKET": 600,
        "ABNORMAL_TICKET": 600,
    }

    RUBBISH_SHOP_BONE_PRIORITY = """"""

    RUBBISH_SHOP_BONE_PRODUCT = {
        "GOOD_TEAMWORK_BOX": 1,
        "MAINTENANCE_KIT_BOX_2": 1,
        "CURATED_MANUFACTURER_ARMS": 1
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

    MAATOUCH_FILEPATH_LOCAL = "./bin/MaaTouch/maatouchsync"
    MAATOUCH_FILEPATH_REMOTE = "/data/local/tmp/maatouchsync"

    EVENTS = EVENTS

    Error_ScreenshotLength = 1

    DEVICE_OVER_HTTP = False

    # 独立任务，不依赖游戏运行（无需初始化 device、不拉起游戏）
    INDEPENDENT_TASKS = ["BlaDaily", "BlaCDK", "BlaExchange", "ScreenRotate", "UpdateHosts", "Notify"]
    # 下划线形式供 run() 比对任务名，由上者派生，避免两份名单手工同步
    INDEPENDENT_TASKS_UNDER = [inflection.underscore(task) for task in INDEPENDENT_TASKS]
