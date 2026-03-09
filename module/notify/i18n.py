I18N_NOTIFY = {
    'crashed': {
        'zh-CN': '[NKAS通知] 实例 {config_name} 出现异常',
        'en-US': '[NKAS Notification] Instance {config_name} crashed',
        'ja-JP': '[NKAS通知] インスタンス {config_name} がクラッシュしました',
    },
    'GamePageUnknownError': {
        'zh-CN': '游戏页面未知',
        'en-US': 'Game Page Unknown',
        'ja-JP': 'ゲームページが不明です',
    },
    'GameServerUnderMaintenance': {
        'zh-CN': '游戏服务器正在维护',
        'en-US': 'Game Server Under Maintenance',
        'ja-JP': 'ゲームサーバーはメンテナンス中です',
    },
    'RequestHumanTakeover': {
        'zh-CN': '请求人工接管',
        'en-US': 'Request Human Takeover',
        'ja-JP': '手動操作が必要です',
    },
    'ExceptionOccurred': {
        'zh-CN': '发生异常',
        'en-US': 'Exception Occurred',
        'ja-JP': '例外が発生しました',
    },
    'AccountError': {
        'zh-CN': '登录失败',
        'en-US': 'Login failed',
        'ja-JP': 'ログインに失敗しました',
    },
    'ScreenResolutionNotEnough': {
        'zh-CN': '屏幕分辨率不足',
        'en-US': 'Screen resolution not enough',
        'ja-JP': '画面解像度が不足しています',
    },
    'TaskFailedThreeTimes': {
        'zh-CN': '任务 `{task}` 失败3次或以上。',
        'en-US': 'Task `{task}` failed 3 or more times.',
        'ja-JP': 'タスク `{task}` が3回以上失敗しました。',
    },
    'DailyTaskCompleted': {
        'title': {
            'zh-CN': '[NKAS通知] 实例 {config_name} 任务完成',
            'en-US': '[NKAS Notification] Instance {config_name} tasks completed',
            'ja-JP': '[NKAS通知] インスタンス {config_name} タスク完了',
        },
        'content': {
            'zh-CN': '任务已全部完成！',
            'en-US': 'All tasks completed!',
            'ja-JP': 'すべてのタスクが完了しました！',
        },
    },
    'SpecialArenaRankChanged': {
        'title': {
            'zh-CN': '[NKAS通知] 实例 {config_name} 特殊竞技场段位变化',
            'en-US': '[NKAS Notification] Instance {config_name} Special Arena rank changed',
            'ja-JP': '[NKAS通知] インスタンス {config_name} 特殊アリーナランク変動',
        },
        'content': {
            'zh-CN': '特殊竞技场段位变化：{old_rank} -> {new_rank}',
            'en-US': 'Special Arena rank changed: {old_rank} -> {new_rank}',
            'ja-JP': '特殊アリーナランク変動: {old_rank} -> {new_rank}',
        },
    },
    'Recruit': {
        'title': {
            'zh-CN': '[NKAS通知] 实例 {config_name} 招募',
            'en-US': '[NKAS Notification] Instance {config_name} Recruit',
            'ja-JP': '[NKAS通知] インスタンス {config_name} 募集',
        },
        'content': {
            'zh-CN': '{recruit_type} 获得SSR！',
            'en-US': '{recruit_type} SSR got!',
            'ja-JP': '{recruit_type} でSSRを獲得しました！',
        },
    },
    'RecruitType': {
        'EventFree': {'zh-CN': '活动免费', 'en-US': 'Event Free', 'ja-JP': 'イベント無料'},
        '150Gem': {'zh-CN': '150钻', 'en-US': '150 Gem', 'ja-JP': '150ジュエル'},
        'SocialPoint': {'zh-CN': '友情点', 'en-US': 'Social Point', 'ja-JP': 'ソーシャルポイント'},
    },
}


def get_text(key: str, lang: str, **kwargs) -> str:
    """
    获取翻译后的文本。
    支持使用 . 分割的 key 来访问嵌套字典。
    """
    keys = key.split('.')
    try:
        node = I18N_NOTIFY
        for k in keys:
            node = node[k]

        if not isinstance(node, dict):
            return key

        # 默认语言回退: lang -> en-US -> key
        text = node.get(lang)
        if not isinstance(text, str):
            text = node.get('en-US')

        if not isinstance(text, str):
            return key

        if kwargs:
            return text.format(**kwargs)
        return text
    except (KeyError, AttributeError):
        # 如果找不到key或节点不是字典，返回key本身
        return key
