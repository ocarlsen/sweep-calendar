"""Expand a block side's rules into concrete no-parking windows."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List
from zoneinfo import ZoneInfo

from .holidays import week_of_month
from .model import BlockSide, City, HolidayKind, Rule


@dataclass(frozen=True)
class Window:
    start: datetime  # tz-aware, city local time
    end: datetime


def holiday_table(city: City, first: date, last: date) -> Dict[date, HolidayKind]:
    # One year of margin: an observed holiday can fall in the previous
    # calendar year (New Year's Day on a Saturday is observed Dec 31).
    table: Dict[date, HolidayKind] = {}
    for year in range(first.year, last.year + 2):
        table.update(city.holidays(year))
    return table


def applies(rule: Rule, day: date, holiday) -> bool:
    if holiday is HolidayKind.FULL:
        return False
    if rule.holiday_only:
        return holiday is HolidayKind.PARTIAL
    if day.weekday() != rule.weekday or week_of_month(day) not in rule.weeks:
        return False
    return holiday is None or rule.on_holidays


def windows(side: BlockSide, city: City, first: date, last: date,
            holidays: Dict[date, HolidayKind] = None) -> List[Window]:
    """No-parking windows on `side` for every day in [first, last], with
    overlapping or back-to-back windows on the same day merged."""
    tz = ZoneInfo(city.tz)
    holidays = holiday_table(city, first, last) if holidays is None else holidays
    out: List[Window] = []
    day = first
    while day <= last:
        spans = sorted(
            (datetime.combine(day, r.start, tz), datetime.combine(day, r.end, tz))
            for r in side.rules if applies(r, day, holidays.get(day))
        )
        for start, end in spans:
            if out and start <= out[-1].end:
                out[-1] = Window(out[-1].start, max(out[-1].end, end))
            else:
                out.append(Window(start, end))
        day += timedelta(days=1)
    return out
