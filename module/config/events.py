"""剧情活动列表：前两项分别供 Event、Event2 使用，其余保留为历史活动。"""

EVENTS = [
    {
        "event_id": "event_20261029",
        "event_name": "MARIAn",
        "event_type": 1,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",
        "story_difficulty": "Normal",
        "task_order": [
            "hard_ticket", "story", "login_stamp", "challenge",
            "game", "reward", "event_extend", "shop",
        ],
        "pending_finder": {
            "story_1_normal": {
                "mode": "grid",
                "area": (100, 400, 600, 1050),
                "rows": 6,
                "columns": 1,
                "vertical_direction": "bottom_to_top",
                "horizontal_direction": "right_to_left",
            },
        }
    },
    {
        "event_id": "event_20261008",
        "event_name": "LITTLE WITCH TRICK",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard",  # Normal, Hard
        # 可选：任务按列表顺序优先执行，未列出的任务按默认顺序补到末尾
        # "task_order": [
        #     "login_stamp", "challenge", "hard_ticket", "story",
        #     "game", "reward", "event_extend", "shop",
        # ],
        # pending_finder 按章节和难度配置：story_1_normal / story_1_hard / story_2_normal / story_2_hard
        # "pending_finder": {
        #     "story_1_normal": {
        #         "mode": "grid",
        #         "area": (100, 400, 600, 1050),
        #         "rows": 6,
        #         "columns": 1,
        #         "vertical_direction": "bottom_to_top",
        #         "horizontal_direction": "right_to_left",
        #     },
        # },
    },
    {
        "event_id": "event_20260917",
        "event_name": "COINRUSH SHOWDOWN",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard",  # Normal, Hard
        "pending_click_offset": (0, 0),
        # "pending_finder": {
        #     "story_2_normal": {
        #         "mode": "grid",
        #         "area": (230, 410, 500, 1045),
        #         "rows": 8,
        #         "columns": 1,
        #         "vertical_direction": "bottom_to_top",
        #         "horizontal_direction": "right_to_left",
        #     },
        # },
    },
    {
        "event_id": "event_20260903",
        "event_name": "GREAT VILLAIN UNION",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260813",
        "event_name": "PERSONA ON FRONTLINE",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard",  # Normal, Hard
        "pending_click_offset": (0, 0),
        # "pending_finder": {
        #     "story_1_normal": {
        #         "mode": "grid",
        #         "area": (25, 350, 325, 1175),
        #         "rows": 10,
        #         "columns": 1,
        #         "vertical_direction": "bottom_to_top",
        #         "horizontal_direction": "right_to_left",
        #     },
        # },
    },
    {
        "event_id": "event_20260723",
        "event_name": "PROJECT MATIS",
        # 大型活动
        "event_type": 3,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260702",
        "event_name": "WAVE TO YOU",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard",  # Normal, Hard
        "pending_click_offset": (0, 0),
        "pending_finder": {
            "story_2_hard": {
                "mode": "grid",
                "area": (100, 300, 620, 1050),
                "rows": 6,
                "columns": 2,
                "vertical_direction": "bottom_to_top",
                "horizontal_direction": "right_to_left",
            },
        },
    },
    {
        "event_id": "event_20260611",
        "event_name": "ARK RANGER",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard",  # Normal, Hard
        "pending_click_offset": (0, 0),
    },
    {
        "event_id": "event_20260528",
        "event_name": "Bitter Spice",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260514",
        "event_name": "B-SIDE IDOL",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260423",
        "event_name": "STAR ANIS",
        # 大型活动
        "event_type": 1,
        "mini_game": False,
        "mini_game_play": True,
        "extend": True,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard",  # Normal, Hard
        "pending_click_offset": (0, 0),
    },
    {
        "event_id": "event_20260409",
        "event_name": "Good World",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260319",
        "event_name": "2x2 Love",
        # 大型活动
        "event_type": 3,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260303",
        "event_name": "ENTER HEAVEN",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260212",
        "event_name": "Lycoris Recoil",
        # 大型活动
        "event_type": 1,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260129",
        "event_name": "Fatal Maid",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20251230",
        "event_name": "ARK GUARDIAN",
        # 大型活动
        "event_type": 3,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20260115",
        "event_name": "SIN EDITOR",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": False,
        "extend": False,
        "story_part": "Story_1",  # Story_1
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20251204",
        "event_name": "TERMINUS Ticket",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20251120",
        "event_name": "BLANK Ticket",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": False,
        "extend": False,
        "story_part": "Story_1",  # Story_1
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20251030",
        "event_name": "GODDESS FALL",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": False,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20250924",
        "event_name": "REBORN EVIL",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20251016",
        "event_name": "GO! NINJA THIEF!",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20250904",
        "event_name": "COINS IN RUSH",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20250807",
        "event_name": "ABSOLUTE",
        # story1为小型活动的大型活动
        "event_type": 3,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20250716",
        "event_name": "BOOM! THE GHOST!",
        # 大型活动
        "event_type": 1,
        "mini_game": True,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20250703l",
        "event_name": "OuteR: Automata",
        # 大型活动
        "event_type": 1,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20250703s",
        "event_name": "OVER THE HORIZON",
        # 小型活动
        "event_type": 2,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_1",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
    {
        "event_id": "event_20250612",
        "event_name": "Memories Teller",
        # 大型活动
        "event_type": 1,
        "mini_game": False,
        "mini_game_play": True,
        "extend": False,
        "story_part": "Story_2",  # Story_1, Story_2
        "story_difficulty": "Hard"  # Normal, Hard
    },
]
