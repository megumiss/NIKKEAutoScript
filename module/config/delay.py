from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))  # 北京时区


def _beijing_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(BEIJING_TZ)


def _to_local_naive(beijing_time: datetime) -> datetime:
    """
    将一个北京时间(aware)转换为本地时区的 naive datetime（不带时区）。

    例如：
    目标是 北京时间 11-04 04:00 (UTC+8)。
    本地时区是 UTC-6。
    这个时间点在本地是 11-03 14:00 (UTC-6)。
    函数将返回 naive datetime: datetime(2025, 11, 3, 14, 0, 0)
    """
    return beijing_time.astimezone(None).replace(tzinfo=None)


def _parse_time(time_str: str) -> tuple:
    h, m = [int(x) for x in time_str.strip().split(':')]
    return h, m


def next_weekday(days, time_str: str = '04:00') -> datetime:
    """
    返回北京时间下个“星期几 HH:mm”时，本地时区的 naive datetime（不带时区）。
    多个星期几取离当前最近的一个。

    Args:
        days (str, int): 星期几，1=周一 ... 7=周日，如 '2' 或 '2, 5'
        time_str (str): 形如 '04:00'
    """
    if isinstance(days, int):
        days = str(days)
    weekdays = sorted({int(d) for d in str(days).replace(' ', '').split(',') if d})
    h, m = _parse_time(time_str)

    beijing_now = _beijing_now()
    candidates = []
    for day in weekdays:
        # 配置里 1=周一，python weekday() 0=周一
        days_ahead = (day - 1 - beijing_now.weekday() + 7) % 7
        target = beijing_now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=days_ahead)
        # 计算出的时间点在过去（例如今天就是该星期但时间已过）则推到下周
        if target <= beijing_now:
            target += timedelta(days=7)
        candidates.append(target)

    return _to_local_naive(min(candidates))


def next_month_day(day, time_str: str = '04:00') -> datetime:
    """
    返回北京时间下个“每月 N 日 HH:mm”时，本地时区的 naive datetime（不带时区）。
    本月的该时间点已过则取下个月。

    Args:
        day (str, int): 每月第几天，1-28
        time_str (str): 形如 '04:00'
    """
    day = int(day)
    h, m = _parse_time(time_str)

    beijing_now = _beijing_now()
    target = beijing_now.replace(day=day, hour=h, minute=m, second=0, microsecond=0)
    if target <= beijing_now:
        next_month_val = beijing_now.month % 12 + 1
        next_year = beijing_now.year + 1 if next_month_val == 1 else beijing_now.year
        target = target.replace(year=next_year, month=next_month_val)

    return _to_local_naive(target)
