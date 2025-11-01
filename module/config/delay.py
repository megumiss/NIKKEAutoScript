from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))  # 北京时区

def next_tuesday() -> datetime:
    """
    返回北京时间下个周二4:00时，本地时区的 naive datetime（不带时区）。
    
    例如：
    目标是 北京时间 11-04 04:00 (UTC+8)。
    本地时区是 UTC-6。
    这个时间点在本地是 11-03 14:00 (UTC-6)。
    函数将返回 naive datetime: datetime(2025, 11, 3, 14, 0, 0)
    """
    utc_now = datetime.now(timezone.utc)
    beijing_now = utc_now.astimezone(BEIJING_TZ)

    # 计算到下一个周二 (weekday 1) 的天数
    days_ahead = (1 - beijing_now.weekday() + 7) % 7
    
    # 目标日期的 04:00（北京时间）
    target_beijing = beijing_now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)

    # 如果计算出的时间点在当前时间之前或等于当前时间
    # (例如：今天是周二 10:00，days_ahead=0，target_beijing 是【今天】04:00)
    # 则需要推到下周二
    if target_beijing <= beijing_now:
        target_beijing += timedelta(days=7)

    # 1. 将北京时间点(aware)转换为本地时区(aware)
    local_time_aware = target_beijing.astimezone(None)
    
    # 2. 移除时区信息，使其变为 naive 并返回
    return local_time_aware.replace(tzinfo=None)

def next_month() -> datetime:
    """
    返回下个月 1 日 04:00（北京时间）时，本地时区的 naive datetime（不带时区）。

    例如：
    目标是 北京时间 11-01 04:00 (UTC+8)。
    本地时区是 UTC-6。
    这个时间点在本地是 10-31 14:00 (UTC-6)。
    函数将返回 naive datetime: datetime(2025, 10, 31, 14, 0, 0)
    """
    utc_now = datetime.now(timezone.utc)
    beijing_now = utc_now.astimezone(BEIJING_TZ)

    next_month_val = beijing_now.month % 12 + 1
    next_year = beijing_now.year + 1 if next_month_val == 1 else beijing_now.year

    # 下个月 1 日 04:00（北京时间）
    target_beijing = beijing_now.replace(
        year=next_year,
        month=next_month_val,
        day=1,
        hour=4,
        minute=0,
        second=0,
        microsecond=0,
    )

    # 1. 将北京时间点(aware)转换为本地时区(aware)
    local_time_aware = target_beijing.astimezone(None)
    
    # 2. 移除时区信息，使其变为 naive 并返回
    return local_time_aware.replace(tzinfo=None)
