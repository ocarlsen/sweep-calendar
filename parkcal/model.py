"""City-agnostic schedule model.

Every city adapter turns its own data into these types; everything downstream
(expansion, ICS, site index) only ever sees these.
"""
from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple


class HolidayKind(Enum):
    # Nothing is swept anywhere (SF: New Year's, Thanksgiving, Christmas).
    FULL = "full"
    # Only rules marked on_holidays run; everything else is suspended.
    PARTIAL = "partial"


@dataclass(frozen=True)
class Rule:
    """One recurring no-parking window on one side of one block."""

    weekday: Optional[int]  # Monday=0 .. Sunday=6; None = holidays only
    weeks: FrozenSet[int]  # nth occurrence of the weekday in the month, 1..5
    start: time
    end: time
    on_holidays: bool = False  # still enforced on PARTIAL holidays
    # Swept, but not posted: the city asks you to move, nobody is ticketed
    # (most of San Jose's residential streets).
    advisory: bool = False

    @property
    def holiday_only(self) -> bool:
        return self.weekday is None


@dataclass
class BlockSide:
    id: str  # stable, URL-safe; becomes the feed filename
    street: str
    limits: str  # "Larkin St - Polk St"
    side: str  # human label, e.g. "North side"
    line: List[Tuple[float, float]]  # (lon, lat) pairs
    curb: str = ""  # "L"/"R" of the line's drawn direction, for map offsets
    rules: List[Rule] = field(default_factory=list)
    # Same-schedule pieces of this block split off by a minor street (see
    # merge.py). Their feed URLs stay valid and serve this side's calendar.
    joined: List["BlockSide"] = field(default_factory=list)

    @property
    def pieces(self) -> List["BlockSide"]:
        return [self, *self.joined]


@dataclass(frozen=True)
class City:
    slug: str  # "sf"
    name: str  # "San Francisco"
    tz: str  # IANA zone the schedule's wall-clock times are in
    source_url: str  # where residents can check the official data
    holidays: Callable[[int], Dict[date, HolidayKind]]
    load: Callable[..., List[BlockSide]]
    # Streets too minor to end a block ("Imperial Aly"); pieces split by one
    # with identical schedules are presented as one block side.
    minor_street: Callable[[str], bool] = lambda name: False
    # GeoJSON Feature of the city limits (simplified), used to draw coverage
    # and to tell "in this city" from "outside coverage" after geocoding.
    boundary: Optional[Path] = None
    # Appended to every event's description, e.g. a holiday caveat the data
    # cannot express.
    feed_note: str = ""
