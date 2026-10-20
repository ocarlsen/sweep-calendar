"""Emeryville: Street Sweeping Routes (city ArcGIS, "Authoritative view").

One polyline per street segment, coded fields (domains from the layer):
  RouteType / RouteClass  Commercial, Residential, Mixed Use, Not Swept (skipped)
  DayOfWeek               Monday / Thursday
  DayOfMonth              "1st & 3rd", "1st", "2nd", or "1st or 2nd"
  TimeOfDay               1 = 8pm-5am (overnight), 2 = 8am-noon

All of it is ticketed: the municipal code (§4-9.15, 2010 ordinance) makes a
street-sweeping no-parking zone one that is "properly posted", and parking
there during the posted days and hours unlawful.

"1st or 2nd" (the Triangle) means each side is swept once a month on a
different Thursday. The layer says which, as geometry: next to each such
centerline (RouteType 2) the city draws a line along each CURB (RouteType 3,
3-5 m off, DayOfMonth "1st" or "2nd"). Measured over the whole layer
(2026-10-20), all 139 curb lines agree: 1st Thursday = north or east curb,
2nd Thursday = south or west curb. (A search snippet said north/west; the
city's own geometry disproves it.) So curb lines are used as drawn, the
centerlines they duplicate are dropped, and a centerline without curb lines
is split by that measured rule.

The layer has no cross-street fields, so a segment's limits come from the
other streets that pass within a few meters of its two ends.
"""
import json
import math
import urllib.parse
from datetime import date, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..fetch import get
from ..geometry import curb_for, normal_angle
from ..model import BlockSide, City, DataError, HolidayKind, Rule
from .sj import title  # 'SAN PABLO AV' -> 'San Pablo Av'

LAYER = ("https://services3.arcgis.com/ljOdqLVbHpS7dOJQ/arcgis/rest/services/"
         "Street_Sweeping_Routes_Authoritative_view/FeatureServer/0")
DAYS = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4}
WEEKS = {"1st & 3rd": frozenset({1, 3}), "1st": frozenset({1}), "2nd": frozenset({2})}
TIMES = {1: (time(20), time(5)), 2: (time(8), time(12))}
SPLIT = "1st or 2nd"


def holidays(year: int) -> Dict[date, HolidayKind]:
    return {}  # no published sweeping holiday policy found


def fetch(cache: Optional[Path] = None) -> List[dict]:
    if cache and cache.exists():
        return json.loads(cache.read_text())
    q = urllib.parse.urlencode(dict(where="1=1", outFields="*", outSR=4326, f="geojson",
                                    resultRecordCount=5000))
    feats = json.loads(get(f"{LAYER}/query?{q}"))["features"]
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(feats))
    return feats


def swept(p: dict) -> bool:
    return (p.get("RouteClass") not in (None, "Not Swept") and p.get("DayOfWeek") in DAYS
            and p.get("TimeOfDay") in TIMES and (p.get("DayOfMonth") in WEEKS or p.get("DayOfMonth") == SPLIT))


NEAR_M = 8  # another street passing this close to a segment's end is its cross street
CURB_M = 25  # a curb line this close to a '1st or 2nd' centerline belongs to it
CURB_LINE = 3  # RouteType of the city's per-curb lines


def _dist_m(pt, line) -> float:
    k = math.cos(math.radians(pt[1])) * 111_320
    px, py = pt[0] * k, pt[1] * 111_320
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(line, line[1:]):
        ax, ay, bx, by = x0 * k, y0 * 111_320, x1 * k, y1 * 111_320
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ((dx * dx + dy * dy) or 1)))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


def limits(line, street: str, others: List[Tuple[str, list]]) -> str:
    """Cross streets at each end: any other street (swept or not) whose line
    passes within NEAR_M of that end, which also catches T-junctions."""
    ends = []
    for pt in (line[0], line[-1]):
        near = sorted({name for name, other in others
                       if name and name != street and _dist_m(pt, other) <= NEAR_M})
        ends.append(near[0] if near else "")
    a, b = ends
    if a and b and a != b:
        return f"{a} - {b}"
    return f"near {a or b}" if (a or b) else ""


def split_curbs(line) -> List[Tuple[str, frozenset]]:
    """A '1st or 2nd' centerline with no curb lines beside it: 1st Thursday on
    the north/east curb, 2nd on the south/west (the rule the city's curb lines
    follow everywhere). The pair depends on which way the street runs."""
    a = normal_angle(line, "R")
    if a is None:
        return []
    runs_east_west = abs(abs(a) - 90) < 45  # the curb normal points north or south
    first, second = ("North", "South") if runs_east_west else ("East", "West")
    return [(first, frozenset({1})), (second, frozenset({2}))]


def midpoint(line):
    return sum(p[0] for p in line) / len(line), sum(p[1] for p in line) / len(line)


def curb_lines_beside(center: dict, curbs: List[dict]) -> List[dict]:
    """The city's per-curb lines drawn alongside a '1st or 2nd' centerline."""
    m = midpoint(center["geometry"]["coordinates"])
    name = center["properties"].get("StreetName")
    return [f for f in curbs if f["properties"].get("StreetName") == name
            and _dist_m(midpoint(f["geometry"]["coordinates"]), center["geometry"]["coordinates"]) <= CURB_M]


def curb_side(curb: dict, center: Optional[dict]) -> str:
    """Which curb a curb line is. Its week decides, by the rule every
    full-length curb line in the layer follows (1st = north/east); the
    street's direction comes from its centerline when there is one, since
    corner stubs a few meters long point every which way."""
    ref = (center or curb)["geometry"]["coordinates"]
    (first, _), (second, _) = split_curbs(ref)
    return first if curb["properties"]["DayOfMonth"] == "1st" else second


def to_blocksides(features: List[dict]) -> List[BlockSide]:
    feats = [f for f in features if f.get("geometry") and f["geometry"].get("type") == "LineString"]
    # A swept route with a code we don't know must not vanish silently.
    unknown = sorted({(p.get("RouteClass"), p.get("DayOfWeek"), p.get("DayOfMonth"), p.get("TimeOfDay"))
                      for p in (f["properties"] for f in feats)
                      if p.get("RouteClass") not in (None, "Not Swept") and not swept(p)})
    if unknown:
        raise DataError("emeryville: route codes the parser doesn't know (RouteClass, DayOfWeek, "
                        "DayOfMonth, TimeOfDay):\n  " + "\n  ".join(map(repr, unknown)))
    others = [(title(f["properties"].get("StreetName")), f["geometry"]["coordinates"]) for f in feats]
    swept_feats = [f for f in feats if swept(f["properties"])]
    centers = [f for f in swept_feats if f["properties"]["DayOfMonth"] == SPLIT]
    curbs = [f for f in swept_feats if f["properties"].get("RouteType") == CURB_LINE]
    beside = {id(c): curb_lines_beside(c, curbs) for c in centers}
    center_of = {id(k): c for c in centers for k in beside[id(c)]}

    sides: Dict[str, BlockSide] = {}
    for f in swept_feats:
        p = f["properties"]
        if p["DayOfMonth"] == SPLIT and beside[id(f)]:
            continue  # the city's curb lines beside it say the same thing, per curb
        line = [tuple(pt) for pt in f["geometry"]["coordinates"]]
        street = title(p.get("StreetName"))
        start, end = TIMES[p["TimeOfDay"]]
        base = (p.get("GlobalID") or str(p["OBJECTID"])).strip("{}").split("-")[0].lower()
        if p["DayOfMonth"] == SPLIT:
            # (cardinal, weeks, curb offset): offset this centerline toward each curb
            curbs_here = [(c, w, curb_for(line, c)) for c, w in split_curbs(line)]
        elif p.get("RouteType") == CURB_LINE:
            # drawn on the curb already: no offset
            curbs_here = [(curb_side(f, center_of.get(id(f))), WEEKS[p["DayOfMonth"]], "")]
        else:
            curbs_here = [("Both", WEEKS[p["DayOfMonth"]], "")]
        for cardinal, weeks, offset in curbs_here:
            sid = f"{base}{cardinal[0]}"
            side = sides.get(sid)
            if side is None:
                side = sides[sid] = BlockSide(
                    id=sid, street=street, limits=limits(line, street, others),
                    side="Both sides" if cardinal == "Both" else f"{cardinal} side",
                    line=line, curb=offset)
            rule = Rule(DAYS[p["DayOfWeek"]], weeks, start, end)
            if rule not in side.rules:
                side.rules.append(rule)
    return list(sides.values())


def load(cache: Optional[Path] = None) -> List[BlockSide]:
    return to_blocksides(fetch(cache))


CITY = City(
    slug="emeryville",
    name="Emeryville",
    tz="America/Los_Angeles",
    source_url="https://services3.arcgis.com/ljOdqLVbHpS7dOJQ/arcgis/rest/services/"
               "Street_Sweeping_Routes_Authoritative_view/FeatureServer/0",
    holidays=holidays,
    load=load,
    boundary=Path(__file__).parent / "boundaries" / "emeryville.geojson",
    min_block_sides=320,  # 358 on 2026-10-20
)
