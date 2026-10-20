"""San Francisco: Public Works street sweeping schedule (DataSF yhqp-riqs).

Column meanings come from the dataset's own metadata:
  cnnrightleft  side of the CNN street segment swept (L/R)
  blockside     cardinal side (North, SouthEast, ...); null on ~2% of rows
  weekday       Mon..Sun, or "Holiday" for rows that run only on holidays
  week1..week5  1 = swept on the nth occurrence of that weekday in the month
  fromhour/tohour  24h wall clock, whole hours
  holidays      1 = swept on holidays

Holiday policy, from SFMTA's enforcement calendar
(sfmta.com/getting-around/drive-park/holiday-enforcement-schedule):
New Year's, Thanksgiving and Christmas suspend all sweeping. Every other
listed holiday suspends daytime sweeping but enforces night sweeping, which
is what the `holidays` flag marks.
"""
import json
import math
from datetime import date, time, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..fetch import get
from ..holidays import last_weekday, nth_weekday, observed
from ..model import BlockSide, City, HolidayKind, Rule

DATASET = "https://data.sf.gov/resource/yhqp-riqs.json"
SOURCE_URL = "https://data.sf.gov/City-Infrastructure/Street-Sweeping-Schedule/yhqp-riqs"
WEEKDAYS = {"Mon": 0, "Tues": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
CARDINALS = ["East", "NorthEast", "North", "NorthWest", "West", "SouthWest", "South", "SouthEast"]


def holidays(year: int) -> Dict[date, HolidayKind]:
    MON, THU = 0, 3
    full = [
        observed(date(year, 1, 1)),
        nth_weekday(year, 11, THU, 4),
        observed(date(year, 12, 25)),
    ]
    thanksgiving = full[1]
    partial = [
        nth_weekday(year, 1, MON, 3),  # Martin Luther King Jr. Day
        nth_weekday(year, 2, MON, 3),  # Presidents' Day
        last_weekday(year, 5, MON),  # Memorial Day
        observed(date(year, 6, 19)),  # Juneteenth
        observed(date(year, 7, 4)),  # Independence Day
        nth_weekday(year, 9, MON, 1),  # Labor Day
        nth_weekday(year, 10, MON, 2),  # Indigenous Peoples Day
        observed(date(year, 11, 11)),  # Veterans Day
        thanksgiving + timedelta(days=1),  # Day after Thanksgiving
    ]
    out = {d: HolidayKind.PARTIAL for d in partial}
    out.update({d: HolidayKind.FULL for d in full})
    return out


def fetch(cache: Optional[Path] = None) -> List[dict]:
    if cache and cache.exists():
        return json.loads(cache.read_text())
    raw = get(f"{DATASET}?$limit=1000000")
    rows = json.loads(raw)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    return rows


def parse_rule(row: dict) -> Rule:
    weekday = None if row["weekday"] == "Holiday" else WEEKDAYS[row["weekday"]]
    weeks = frozenset(n for n in range(1, 6) if row.get(f"week{n}") == "1")
    return Rule(
        weekday=weekday,
        weeks=weeks,
        start=time(int(row["fromhour"])),
        end=time(int(row["tohour"])),
        on_holidays=row.get("holidays") == "1",
    )


def side_from_geometry(line: List[List[float]], rightleft: str) -> Optional[str]:
    """Cardinal side for rows missing `blockside`: the normal to the segment's
    overall direction, to the right or left of how the CNN line is drawn."""
    if len(line) < 2:
        return None
    (x0, y0), (x1, y1) = line[0], line[-1]
    dx, dy = (x1 - x0) * math.cos(math.radians(y0)), y1 - y0
    nx, ny = (dy, -dx) if rightleft == "R" else (-dy, dx)
    if nx == 0 and ny == 0:
        return None
    octant = round(math.degrees(math.atan2(ny, nx)) / 45) % 8
    return CARDINALS[octant]


def side_label(cardinal: Optional[str]) -> str:
    if not cardinal:
        return "Side unknown"
    return cardinal.capitalize() + " side"  # "NorthEast" -> "Northeast side"


def to_blocksides(rows: Iterable[dict]) -> List[BlockSide]:
    sides: Dict[str, BlockSide] = {}
    for row in rows:
        sid = f"{row['cnn']}{row['cnnrightleft']}"
        side = sides.get(sid)
        if side is None:
            line = [tuple(p) for p in row.get("line", {}).get("coordinates", [])]
            cardinal = row.get("blockside") or side_from_geometry(line, row["cnnrightleft"])
            side = sides[sid] = BlockSide(
                id=sid,
                street=row["corridor"],
                limits=" ".join(row.get("limits", "").split()),
                side=side_label(cardinal),
                line=line,
                curb=row["cnnrightleft"],
            )
        side.rules.append(parse_rule(row))
    for side in sides.values():
        side.rules = merge_weeks(side.rules)
    return list(sides.values())


def merge_weeks(rules: List[Rule]) -> List[Rule]:
    """SF often splits one schedule across rows ("2nd & 4th Tue" plus
    "1st, 3rd, 5th Tue" at the same hours is every Tuesday). Union the weeks
    of rules that differ only in weeks, and drop empty or duplicate rows."""
    weeks: Dict[tuple, set] = {}
    for r in rules:
        if r.weeks or r.holiday_only:
            weeks.setdefault((r.weekday, r.start, r.end, r.on_holidays), set()).update(r.weeks)
    return [Rule(wd, frozenset(w), start, end, on_hol)
            for (wd, start, end, on_hol), w in weeks.items()]


# Street types that split a block in the centerline data without bounding it.
# "Way" is left out: Lincoln Way and Junipero Serra Way are major streets.
MINOR_TYPES = {"Aly", "Pl", "Ln", "Ct", "Ter", "Walk", "Walkway", "Path", "Row", "Stps"}


def minor_street(name: str) -> bool:
    words = name.split()
    return bool(words) and words[-1] in MINOR_TYPES


def load(cache: Optional[Path] = None) -> List[BlockSide]:
    return to_blocksides(fetch(cache))


CITY = City(
    slug="sf",
    name="San Francisco",
    tz="America/Los_Angeles",
    source_url=SOURCE_URL,
    holidays=holidays,
    load=load,
    minor_street=minor_street,
    boundary=Path(__file__).parent / "boundaries" / "sf.geojson",
    min_block_sides=20_000,  # 21,916 on 2026-10-19
)
