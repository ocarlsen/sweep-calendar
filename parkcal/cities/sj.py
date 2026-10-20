"""San José: residential street sweeping routes (ArcGIS layer 515).

Residential streets are swept once a month on a fixed nth weekday
(APPWEEKDAYCODE "Thursday,4" = 4th Thursday), both sides the same day.
What that means for parking depends on ROUTETYPE:

  Signed Route      posted no-parking hours (APPPUBLICPOSTEDTIMES), ticketed,
                    usually two windows around a lunch break
  Non-Signed Route  the city asks you to move your car during the sweep
                    window (APPSCHEDULEDTIME, typically 7am-5pm); no tickets
  Median Island     no curb parking; skipped

Both from sanjoseca.gov (Transportation > Street Sweeping): "Vehicles parked
in areas that have been posted for no parking during sweeping times will be
issued a parking citation", and residents are asked to move cars on the
scheduled sweep day.

Not covered yet: major-road sweeping (layer 516), whose posted signs often
differ from the sweep schedule and mix in unrelated restrictions.

Holidays: GreenWaste (the residential contractor) delays service one day when
the collection day falls on or after New Year's Day, Thanksgiving or
Christmas that week, and sweeping follows collection. The data has no
holiday field, so the feeds say so rather than guess a shifted date.
"""
import json
import re
import urllib.parse
import urllib.request
from datetime import date, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..model import BlockSide, City, HolidayKind, Rule

LAYER = "https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer/515"
SOURCE_URL = "https://data.sanjoseca.gov/dataset/residential-street-sweeping-routes"
WEEKDAYS = {d: i for i, d in enumerate(
    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}
SIDES = {"Both": "Both sides", "North": "North side", "South": "South side",
         "East": "East side", "West": "West side"}
PAGE = 2000
TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)|noon", re.I)


def holidays(year: int) -> Dict[date, HolidayKind]:
    return {}  # see module docstring: noted in each event, not modeled


def fetch(cache: Optional[Path] = None) -> List[dict]:
    if cache and cache.exists():
        return json.loads(cache.read_text())
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode(dict(
            where="1=1", outFields="*", outSR=4326, f="geojson", orderByFields="OBJECTID",
            resultOffset=offset, resultRecordCount=PAGE))
        with urllib.request.urlopen(f"{LAYER}/query?{q}", timeout=120) as r:
            page = json.load(r)["features"]
        feats += page
        if len(page) < PAGE:
            break
        offset += PAGE
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(feats))
    return feats


def parse_time(text: str) -> time:
    m = TIME.fullmatch(text.strip())
    if not m:
        raise ValueError(f"unparsed time {text!r}")
    if m.group(0).lower() == "noon":
        return time(12)
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    return time(hour % 12 + (12 if ampm == "pm" else 0), minute)


def parse_windows(text: str) -> List[Tuple[time, time]]:
    """'7am - 5pm' or '8AM to NOON;  then 1PM to 4PM' -> [(start, end), ...]"""
    out = []
    for part in text.split(";"):
        part = re.sub(r"^\s*then\s+", "", part.strip(), flags=re.I)
        if part:
            a, b = re.split(r"\s*(?:-|to)\s*", part, maxsplit=1, flags=re.I)
            out.append((parse_time(a), parse_time(b)))
    return out


SIDE_PHRASE = re.compile(r"(North|South|East|West) Side: (\d)\w\w (\w+day) of the Month", re.I)


def parse_day(p: dict) -> Tuple[int, frozenset]:
    """APPWEEKDAYCODE 'Thursday,4' -> (3, {4}). A few rows give each side its
    own day ('West Side: 1st Monday of the Month; East Side: 2nd Wednesday of
    the Month', one row per SIDEOFROAD); take this row's side's phrase."""
    for side, n, day in SIDE_PHRASE.findall(p.get("APPWEEKDAY") or ""):
        if side.lower() == (p.get("SIDEOFROAD") or "").lower():
            return WEEKDAYS[day.capitalize()], frozenset({int(n)})
    day, *weeks = [x.strip() for x in p["APPWEEKDAYCODE"].split(",")]
    return WEEKDAYS[day], frozenset(int(w) for w in weeks)


def rules_for(p: dict) -> List[Rule]:
    weekday, weeks = parse_day(p)
    signed = p["ROUTETYPE"] == "Signed Route"
    posted = (p.get("APPPUBLICPOSTEDTIMES") or "").strip()
    windows = parse_windows(posted if signed and posted else p["APPSCHEDULEDTIME"])
    return [Rule(weekday, weeks, a, b, advisory=not (signed and posted)) for a, b in windows]


def title(name: str) -> str:
    """'LITTLE ROCK DR' -> 'Little Rock Dr' (ROADFROM/ROADTO are often upper
    case); 'EOS' is the data's code for the end of the street."""
    if (name or "").strip() == "EOS":
        return "End of street"
    return " ".join(w.capitalize() if w.isupper() else w for w in (name or "").split())


def to_blocksides(features: List[dict]) -> List[BlockSide]:
    sides: Dict[str, BlockSide] = {}
    for f in features:
        p = f["properties"]
        if p["ROUTETYPE"] == "Median Island" or not f.get("geometry"):
            continue
        side_key = p.get("SIDEOFROAD") or "Both"
        sid = f"{p['CENTERLINEID']}{side_key[0]}"
        side = sides.get(sid)
        if side is None:
            g = f["geometry"]
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            side = sides[sid] = BlockSide(
                id=sid,
                street=title(p["ROADNAME"]),
                limits=f"{title(p['ROADFROM'])} - {title(p['ROADTO'])}",
                side=SIDES.get(side_key, f"{side_key} side"),
                line=[tuple(pt) for line in lines for pt in line],
            )
        for r in rules_for(p):
            if r not in side.rules:
                side.rules.append(r)
    return list(sides.values())


def load(cache: Optional[Path] = None) -> List[BlockSide]:
    return to_blocksides(fetch(cache))


CITY = City(
    slug="sj",
    name="San Jose",
    tz="America/Los_Angeles",
    source_url=SOURCE_URL,
    holidays=holidays,
    load=load,
    boundary=Path(__file__).parent / "boundaries" / "sj.geojson",
    min_block_sides=17_000,  # 18,615 on 2026-10-19
    feed_note=("In weeks with New Year's Day, Thanksgiving or Christmas, "
               "residential sweeping can run one day later."),
)
