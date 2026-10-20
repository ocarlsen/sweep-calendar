"""San José: residential (ArcGIS layer 515) and major-road (layer 516) sweeping.

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

Major roads (layer 516, swept at night by City DOT) are parsed in sj_major.py:
the posted sign on Signed Routes, the sweep schedule on Non-Signed ones, each
side of the road separately where the city gives sides their own days.

Holidays: GreenWaste (the residential contractor) delays service one day when
the collection day falls on or after New Year's Day, Thanksgiving or
Christmas that week, and sweeping follows collection. The data has no
holiday field, so the feeds say so rather than guess a shifted date.
"""
import json
import re
import urllib.parse
from datetime import date, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..fetch import get
from ..expand import covered
from ..geometry import curb_for
from . import sj_major
from ..model import BlockSide, City, DataError, HolidayKind, Rule

SERVICE = "https://geo.sanjoseca.gov/server/rest/services/OPN/OPN_OpenDataService/MapServer"
LAYERS = {"residential": 515, "major": 516}
SOURCE_URL = "https://data.sanjoseca.gov/dataset/residential-street-sweeping-routes"
WEEKDAYS = {d: i for i, d in enumerate(
    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}
SIDES = {"Both": "Both sides", "North": "North side", "South": "South side",
         "East": "East side", "West": "West side"}
PAGE = 2000
TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)|noon", re.I)


def holidays(year: int) -> Dict[date, HolidayKind]:
    return {}  # see module docstring: noted in each event, not modeled


def fetch_layer(layer: int) -> List[dict]:
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode(dict(
            where="1=1", outFields="*", outSR=4326, f="geojson", orderByFields="OBJECTID",
            resultOffset=offset, resultRecordCount=PAGE))
        page = json.loads(get(f"{SERVICE}/{layer}/query?{q}"))["features"]
        feats += page
        if len(page) < PAGE:
            break
        offset += PAGE
    return feats


def fetch(cache: Optional[Path] = None) -> Dict[str, List[dict]]:
    """{'residential': [...], 'major': [...]} features. A cache from before the
    major-road layer (a bare list) is ignored and refetched."""
    if cache and cache.exists():
        data = json.loads(cache.read_text())
        if isinstance(data, dict):
            return data
    data = {name: fetch_layer(layer) for name, layer in LAYERS.items()}
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    return data


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


def lines_of(g: dict) -> List[tuple]:
    lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
    return [tuple(pt) for line in lines for pt in line]


def to_blocksides(features: List[dict], major: List[dict] = ()) -> List[BlockSide]:
    sides: Dict[str, BlockSide] = {}

    def side_for(p: dict, f: dict, side_key: str) -> BlockSide:
        sid = f"{p['CENTERLINEID']}{side_key[0]}"
        if sid not in sides:
            line = lines_of(f["geometry"])
            sides[sid] = BlockSide(
                id=sid,
                street=title(p["ROADNAME"]),
                limits=f"{title(p['ROADFROM'])} - {title(p['ROADTO'])}",
                side=SIDES.get(side_key, f"{side_key} side"),
                line=line,
                curb="" if side_key == "Both" else curb_for(line, side_key),
            )
        return sides[sid]

    def add(side: BlockSide, rules: List[Rule]) -> None:
        for r in rules:
            if r not in side.rules:
                side.rules.append(r)

    for f in features:
        p = f["properties"]
        if p["ROUTETYPE"] == "Median Island" or not f.get("geometry") or p.get("CENTERLINEID") is None:
            continue
        add(side_for(p, f, p.get("SIDEOFROAD") or "Both"), rules_for(p))

    # Primary rows first; a non-primary duplicate may only fill a side the
    # primary row left empty (S King Rd: primary says West is No Stopping, a
    # second route row says both sides are swept).
    unknown = sorted({c for f in major if f["properties"]["ROUTETYPE"] == "Signed Route"
                      for c in sj_major.unrecognized(f["properties"].get("APPPUBLICPOSTEDTIMES"))}
                     | {f["properties"].get("APPWEEKDAY") or "" for f in major
                        if f["properties"]["ROUTETYPE"] == "Non-Signed Route"
                        and not sj_major.scheduled(f["properties"].get("APPWEEKDAY"),
                                                   f["properties"].get("APPSCHEDULEDTIME"))})
    if unknown:
        raise DataError("sj: major-road schedule text the parser doesn't recognize (add it to "
                        "sj_major.py or NOT_SWEEPING):\n  " + "\n  ".join(repr(c) for c in unknown))
    from_primary = set()
    major = sorted(major, key=lambda f: f["properties"].get("PRIMARYSEGMENT") != "Yes")
    for f in major:
        p = f["properties"]
        if (p["ROUTETYPE"] == "Median Island" or not f.get("geometry")
                or p.get("CENTERLINEID") is None):
            continue
        primary = p.get("PRIMARYSEGMENT") == "Yes"
        signed = p["ROUTETYPE"] == "Signed Route"
        sched = sj_major.scheduled(p.get("APPWEEKDAY"), p.get("APPSCHEDULEDTIME"))
        signs = sj_major.split_sides(p.get("APPPUBLICPOSTEDTIMES")) if signed else {}
        sweeps = sj_major.posted(p.get("APPPUBLICPOSTEDTIMES")) if signed else {}
        row_side = p.get("SIDEOFROAD") or "Both"
        keys = [row_side] if row_side != "Both" else sorted(set(signs) | set(sched) or {"Both"})
        for k in keys:
            if (p["CENTERLINEID"], k) in from_primary and not primary:
                continue
            pick = lambda d: d.get(k) or d.get("Both") or []
            schedule = [Rule(d, w, a, b, advisory=True) for d, w, a, b in pick(sched)]
            if not signed:
                rules, note = schedule, None
            elif pick(sweeps):
                rules = [Rule(d, w, a, b) for d, w, a, b in pick(sweeps)]
                # The street-level schedule only belongs on this curb where it
                # sits inside this curb's own posted window.
                rules += [r for r in schedule if any(covered(r, t) for t in rules)]
                note = None
            else:
                clause = signs.get(k) or signs.get("Both") or ""
                # A time limit allows overnight parking, so the sweep matters;
                # an outright ban (No Stopping, loading zone) means no sweep event.
                rules = schedule if sj_major.is_time_limit(clause) else []
                note = clause or None
            if not rules and not note:
                continue
            if primary:
                from_primary.add((p["CENTERLINEID"], k))
            side = side_for(p, f, k)
            add(side, rules)
            if note and f"Posted: {note}" not in side.notes:
                side.notes.append(f"Posted: {note}")
    return split_both(sides)


OPPOSITE = {"North": "South", "South": "North", "East": "West", "West": "East"}


def split_both(sides: Dict[str, BlockSide]) -> List[BlockSide]:
    """A street can be in both layers: residential says "Both sides", major
    roads names one or two curbs (Almaden Ave, Balbach-Viola: Both = 3rd Tue
    7am-5pm, West = every day 3am-6am). Drawing "Both" over the West curb would
    hide half of each schedule, so the Both rules move onto each real curb
    (creating the opposite curb if only one is named) and Both goes away."""
    by_line: Dict[str, Dict[str, BlockSide]] = {}
    for sid, side in sides.items():
        by_line.setdefault(sid[:-1], {})[side.side.split()[0]] = side
    for curbs in by_line.values():
        both = curbs.get("Both")
        named = [k for k in curbs if k != "Both"]
        if not both or not named:
            continue
        for k in named:
            other = OPPOSITE.get(k)
            if other and other not in curbs:
                # Same street and limits as its partner curb, so the two read alike.
                curbs[other] = sides[both.id[:-1] + other[0]] = BlockSide(
                    id=both.id[:-1] + other[0], street=curbs[k].street, limits=curbs[k].limits,
                    side=SIDES[other], line=both.line, curb=curb_for(both.line, other))
        for k, side in curbs.items():
            if k != "Both":
                side.rules += [r for r in both.rules if r not in side.rules]
                side.notes += [n for n in both.notes if n not in side.notes]
        del sides[both.id]
    return list(sides.values())


def load(cache: Optional[Path] = None) -> List[BlockSide]:
    data = fetch(cache)
    return to_blocksides(data["residential"], data["major"])


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
