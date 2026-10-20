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
import json
import math
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from . import ics
from .cities import CITIES
from .expand import Window, holiday_table, windows
from .merge import join_split_blocks
from .model import BlockSide, City, Rule

WEB = Path(__file__).resolve().parent.parent / "web"  # static front end
NEXT_HORIZON = timedelta(days=62)  # enough to find the next 3 sweeps of a monthly rule
TILE_DEG = 0.01  # ~1.1 km north-south; a block is ~100-300 m
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def describe(rule: Rule) -> str:
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
    return f"{days} {hours}"


def tile_key(lon: float, lat: float) -> str:
    return f"{math.floor(lon / TILE_DEG)}_{math.floor(lat / TILE_DEG)}"


def summary(side: BlockSide, upcoming: List[Window]) -> dict:
    order = sorted(side.rules, key=lambda r: (r.weekday is None, r.weekday or 0, r.start))
    return {
        "id": side.id,
        "street": side.street,
        "limits": side.limits,
        "side": side.side,
        "schedule": [describe(r) for r in order],
        "next": [[w.start.isoformat(timespec="minutes"), w.end.isoformat(timespec="minutes")]
                 for w in upcoming[:3]],
        "parts": [{"id": p.id, "curb": p.curb,
                   "line": [[round(x, 6), round(y, 6)] for x, y in p.line]}
                  for p in side.pieces if p.line],
    }


def build_city(city: City, out: Path, days: int, cache: Path = None) -> dict:
    root = out / city.slug
    if root.exists():
        shutil.rmtree(root)
    (root / "feeds").mkdir(parents=True)
    (root / "tiles").mkdir()

    sides = join_split_blocks(
        city.load(cache / f"{city.slug}.json" if cache else None), city.minor_street)
    today = datetime.now(timezone.utc).astimezone().date()
    last = today + timedelta(days=days)
    holidays = holiday_table(city, today, last)
    stamp = datetime.now(timezone.utc).replace(microsecond=0)

    tiles: Dict[str, list] = defaultdict(list)
    lons, lats = [], []
    for side in sides:
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
        built.append({k: meta[k] for k in ("slug", "name", "bounds")})
    (args.out / "cities.json").write_text(json.dumps(built, indent=1))
    shutil.copytree(WEB, args.out, dirs_exist_ok=True)
    # Version the front end's own assets so a deploy never pairs new data with
    # a cached old app.js.
    index = args.out / "index.html"
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    index.write_text(index.read_text()
                     .replace('src="app.js"', f'src="app.js?v={version}"')
                     .replace('href="style.css"', f'href="style.css?v={version}"'))


if __name__ == "__main__":
    main()
