"""Date helpers for the US holidays cities key their schedules off."""
from datetime import date, timedelta
from typing import Optional


def nth_weekday(year: int, month: int, weekday: int, n: int) -> Optional[date]:
    """The nth (1-based) `weekday` of the month, or None if the month lacks one."""
    first = date(year, month, 1)
    d = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    return d if d.month == month else None


def last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + month // 12, month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def observed(d: date) -> date:
    """Federal observance: Saturday moves to Friday, Sunday to Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def week_of_month(d: date) -> int:
    """Which occurrence of its weekday `d` is (the 2nd Tuesday -> 2)."""
    return (d.day - 1) // 7 + 1
