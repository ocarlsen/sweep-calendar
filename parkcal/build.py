"""Build the static site data for one or more cities.

    python3 -m parkcal.build --out site --days 365 [--cache .cache] [sf ...]

Writes, per city:
  <out>/<slug>/feeds/<blockside>.ics   one subscribable calendar per block side
  <out>/<slug>/tiles/<x>_<y>.json      block sides grouped on a lat/lon grid,
                                       so the map loads only what is in view
  <out>/<slug>/meta.json               name, bounds, tile size, build stamp
and <out>/cities.json listing them all, next to the static front end in web/.
"""
import argparse
import hashlib
import json
import math
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from . import ics
from .cities import CITIES
from .expand import Window, covered, holiday_table, windows
from .merge import join_split_blocks
from .model import BlockSide, City, DataError, Rule

WEB = Path(__file__).resolve().parent.parent / "web"  # static front end
NEXT_HORIZON = timedelta(days=62)  # enough to find the next 3 sweeps of a monthly rule
TILE_DEG = 0.01  # ~1.1 km north-south; a block is ~100-300 m
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def describe(rule: Rule, tag: bool = True) -> str:
    hours = f"{ics.hour_label(rule.start)}–{ics.hour_label(rule.end)}"
    if rule.holiday_only:
        return f"Holidays {hours}"
    if len(rule.weeks) == 5:
        days = f"Every {DAYS[rule.weekday]}"
    elif len(rule.weeks) == 4:
        (missing,) = {1, 2, 3, 4, 5} - rule.weeks
        days = f"Every {DAYS[rule.weekday]} except the {ORDINAL[missing]}"
    else:
        names = [ORDINAL[n] for n in sorted(rule.weeks)]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " & " + names[-1]
        days = f"{joined} {DAYS[rule.weekday]}"
    return f"{days} {hours}" + (" (not ticketed)" if tag and rule.advisory else "")


def schedule_lines(rules: List[Rule], tag: bool = True) -> List[str]:
    """Rules as the panel lists them. Every-week rules that share hours collapse
    into one line ("Every Mon & Thu 3am–6am", "Every day 3am–6am"); anything
    with specific weeks stays one line per rule, since "1st & 3rd Mon & Thu"
    would be ambiguous."""
    order = sorted(rules, key=lambda r: (r.weekday is None, r.start, r.weekday or 0))
    groups: Dict[tuple, List[Rule]] = {}
    lines: List[tuple] = []
    for r in order:
        if not r.holiday_only and len(r.weeks) == 5:
            key = (r.start, r.end, r.advisory)
            if key not in groups:
                groups[key] = []
                lines.append(("group", key))
            groups[key].append(r)
        else:
            lines.append(("rule", r))
    out = []
    for kind, item in lines:
        if kind == "rule":
            out.append(describe(item, tag))
            continue
        rs = groups[item]
        if len(rs) == 1:
            out.append(describe(rs[0], tag))
            continue
        start, end, advisory = item
        days = "day" if len(rs) == 7 else " & ".join(DAYS[r.weekday] for r in sorted(rs, key=lambda r: r.weekday))
        out.append(f"Every {days} {ics.hour_label(start)}–{ics.hour_label(end)}"
                   + (" (not ticketed)" if tag and advisory else ""))
    return out


def schedule_sections(rules: List[Rule]) -> Dict[str, List[str]]:
    """The panel's "No parking (posted)" and "Street sweeping (not ticketed)"
    lists. A scheduled sweep that falls inside a posted window isn't a second
    rule, it's which of those nights the sweeper actually comes, so it folds
    into the posted line: "Every day 3am–6am · sweeper comes Mon & Thu"."""
    posted = [r for r in rules if not r.advisory]
    inside = [r for r in rules if r.advisory and any(covered(r, t) for t in posted)]
    sweeping = [r for r in rules if r.advisory and r not in inside]
    lines = schedule_lines(posted, tag=False)
    # Only worth saying when the sweeper comes on fewer days than the sign covers.
    if {(r.weekday, r.weeks, r.start, r.end) for r in inside} == {
            (r.weekday, r.weeks, r.start, r.end) for r in posted}:
        inside = []
    if inside:
        if all(len(r.weeks) == 5 for r in inside):
            days = " & ".join(DAYS[d] for d in sorted({r.weekday for r in inside}))
        else:
            days = "; ".join(describe(Rule(r.weekday, r.weeks, r.start, r.end), tag=False).rsplit(" ", 1)[0]
                             for r in inside)
        same_hours = len({(r.start, r.end) for r in inside + posted}) == 1
        when = days if same_hours else "; ".join(schedule_lines(inside, tag=False))
        note = f"sweeper comes {when}"
        if len(lines) == 1:
            lines = [f"{lines[0]} · {note}"]
        else:
            lines.append(note[0].upper() + note[1:])
    return {"noParking": lines, "sweeping": schedule_lines(sweeping, tag=False)}


def tile_key(lon: float, lat: float) -> str:
    return f"{math.floor(lon / TILE_DEG)}_{math.floor(lat / TILE_DEG)}"


def summary(side: BlockSide, upcoming: List[Window]) -> dict:
    return {
        "id": side.id,
        "street": side.street,
        "limits": side.limits,
        "side": side.side,
        "schedule": schedule_lines(side.rules),  # one list, for compact views
        # The panel's two sections: what's posted (ticketed), and when the
        # sweeper is scheduled where nothing is posted.
        **schedule_sections(side.rules),
        "advisory": all(r.advisory for r in side.rules),
        "notes": side.notes,
        "hasFeed": bool(side.rules),
        "next": [[w.start.isoformat(timespec="minutes"), w.end.isoformat(timespec="minutes")]
                 for w in upcoming[:3]],
        "parts": [{"id": p.id, "curb": p.curb,
                   "line": [[round(x, 6), round(y, 6)] for x, y in p.line]}
                  for p in side.pieces if p.line],
    }


MAX_MISSING_LINE = 0.01  # share of block sides allowed without map geometry


def check_sanity(city: City, sides: List[BlockSide]) -> None:
    """Refuse to publish partial or malformed data. Every check names the
    knob, the value seen, and why, so a failed Monday run says what to look at."""
    problems = []
    if len(sides) < city.min_block_sides:
        problems.append(f"{len(sides)} block sides < min_block_sides={city.min_block_sides} "
                        "(source returned partial data?)")
    no_rules = [s.id for s in sides if not s.rules and not s.notes]  # note-only curbs are deliberate
    if no_rules:
        problems.append(f"{len(no_rules)} block sides with no schedule, e.g. {no_rules[:3]}")
    no_line = sum(1 for s in sides if not s.line)
    if sides and no_line / len(sides) > MAX_MISSING_LINE:
        problems.append(f"{no_line}/{len(sides)} block sides without geometry "
                        f"> MAX_MISSING_LINE={MAX_MISSING_LINE:.0%} (schema change?)")
    if problems:
        raise DataError(f"{city.slug}: refusing to publish:\n  " + "\n  ".join(problems))


def build_city(city: City, out: Path, days: int, cache: Path = None) -> dict:
    sides = join_split_blocks(
        city.load(cache / f"{city.slug}.json" if cache else None), city.minor_street)
    check_sanity(city, sides)

    root = out / city.slug
    if root.exists():
        shutil.rmtree(root)
    (root / "feeds").mkdir(parents=True)
    (root / "tiles").mkdir()
    today = datetime.now(timezone.utc).astimezone().date()
    last = today + timedelta(days=days)
    holidays = holiday_table(city, today, last)
    stamp = datetime.now(timezone.utc).replace(microsecond=0)

    tiles: Dict[str, list] = defaultdict(list)
    lons, lats = [], []
    for side in sides:
        if side.rules:  # a note-only curb ("Posted: No Stopping Anytime") has nothing to subscribe to
            feed = ics.calendar(side, city, today, last, stamp, holidays).encode()
            for piece in side.pieces:
                (root / "feeds" / f"{piece.id}.ics").write_bytes(feed)
        if side.line:
            upcoming = windows(side, city, today, today + NEXT_HORIZON, holidays)
            tiles[tile_key(*side.line[0])].append(summary(side, upcoming))
            for piece in side.pieces:
                lons += [p[0] for p in piece.line]
                lats += [p[1] for p in piece.line]

    for key, entries in tiles.items():
        (root / "tiles" / f"{key}.json").write_text(
            json.dumps(entries, separators=(",", ":")))

    meta = {
        "slug": city.slug,
        "name": city.name,
        "tz": city.tz,
        "source": city.source_url,
        "bounds": [min(lons), min(lats), max(lons), max(lats)],
        "tileDeg": TILE_DEG,
        "tiles": sorted(tiles),
        "blockSides": len(sides),
        "from": today.isoformat(),
        "to": last.isoformat(),
        "built": stamp.isoformat(),
    }
    (root / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cities", nargs="*", default=sorted(CITIES))
    ap.add_argument("--out", type=Path, default=Path("site"))
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--cache", type=Path, help="reuse/save raw city data here")
    args = ap.parse_args(argv)

    built = []
    for slug in args.cities:
        meta = build_city(CITIES[slug], args.out, args.days, args.cache)
        print(f"{slug}: {meta['blockSides']} block sides, {len(meta['tiles'])} tiles")
        city = CITIES[slug]
        entry = {k: meta[k] for k in ("slug", "name", "bounds")}
        if city.boundary:
            entry["boundary"] = json.loads(city.boundary.read_text())["geometry"]
        built.append(entry)
    (args.out / "cities.json").write_text(json.dumps(built, indent=1))
    shutil.copytree(WEB, args.out, dirs_exist_ok=True)
    # Version the front end's own assets by content, so any change gets a new
    # URL (a timestamp collided when two builds landed in the same minute).
    index = args.out / "index.html"
    html = index.read_text()
    for name, attr in (("app.js", "src"), ("style.css", "href")):
        digest = hashlib.sha256((args.out / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'{attr}="{name}"', f'{attr}="{name}?v={digest}"')
    index.write_text(html)


if __name__ == "__main__":
    main()
