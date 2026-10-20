"""Usage summary from GoatCounter, for Owen's AM brief.

    python3 -m parkcal.stats            # yesterday (Pacific), one line, or nothing
    python3 -m parkcal.stats --days 7   # the last 7 full days (the weekly brief)
    python3 -m parkcal.stats --json     # the numbers behind the line

Prints one line, e.g.
  🧹 Sweep Calendar (yesterday): 14 visits (linkedin.com 9, x.com 3) · 4 calendar adds · 1 city request
and prints NOTHING on a day with no visits and no events, so the brief stays
silent when there is nothing to say. Exits non-zero, with the reason on
stderr, when the token is missing or the API fails: a broken feed of numbers
must not look like a quiet day.

Token: ~/.config/sweep-calendar/goatcounter-token (read-only "Read
statistics" token for sweepcalendar.goatcounter.com), or GOATCOUNTER_TOKEN_FILE.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

API = "https://sweepcalendar.goatcounter.com/api/v0"
DASHBOARD = "https://sweepcalendar.goatcounter.com"
TZ = ZoneInfo("America/Los_Angeles")
TOKEN_FILE = Path(os.environ.get("GOATCOUNTER_TOKEN_FILE",
                                 Path.home() / ".config" / "sweep-calendar" / "goatcounter-token"))
# Events the site sends (web/app.js track()): "<action>/<city>".
CALENDAR_ADDS = ("subscribe", "google-calendar", "copy-calendar-url")


class StatsError(SystemExit):
    pass


def token() -> str:
    try:
        t = TOKEN_FILE.read_text().strip()
    except OSError as e:
        raise StatsError(f"stats: can't read GoatCounter token at {TOKEN_FILE}: {e.strerror}")
    if not t:
        raise StatsError(f"stats: GoatCounter token file {TOKEN_FILE} is empty")
    return t


def api(path: str, **params) -> dict:
    req = urllib.request.Request(f"{API}/{path}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {token()}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise StatsError(f"stats: GoatCounter API {path} returned HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise StatsError(f"stats: GoatCounter API {path} unreachable: {e}")


def window(days: int, today: date) -> Dict[str, str]:
    start = datetime.combine(today - timedelta(days=days), time(0), TZ)
    end = datetime.combine(today, time(0), TZ) - timedelta(seconds=1)
    return {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds")}


def collect(days: int, today: date) -> dict:
    span = window(days, today)
    hits = api("stats/hits", limit=100, **span).get("hits", [])
    pages = sum(h["count"] for h in hits if not h.get("event"))
    events: Dict[str, int] = {}
    for h in hits:
        if h.get("event"):
            action = h["path"].lstrip("/").split("/")[0]
            events[action] = events.get(action, 0) + h["count"]
    refs = api("stats/toprefs", limit=3, **span).get("stats") or []
    return {
        "days": days,
        "visits": pages,
        "referrers": [(r.get("name") or "direct", r.get("count", 0)) for r in refs if r.get("count")],
        "events": events,
    }


def line(s: dict) -> str:
    calendar = sum(s["events"].get(a, 0) for a in CALENDAR_ADDS)
    if not s["visits"] and not s["events"]:
        return ""
    def plural(n: int, one: str, many: str = None) -> str:
        return f"{n} {one if n == 1 else (many or one + 's')}"

    parts = [plural(s["visits"], "visit")
             + (f" ({', '.join(f'{n} {c}' for n, c in s['referrers'])})" if s["referrers"] else "")]
    if calendar:
        parts.append(plural(calendar, "calendar add"))
    for action, one, many in (("request-city", "city request", None),
                              ("search-outside", "search outside coverage", "searches outside coverage"),
                              ("report-problem", "problem report", None)):
        if s["events"].get(action):
            parts.append(plural(s["events"][action], one, many))
    when = "yesterday" if s["days"] == 1 else f"last {s['days']} days"
    return f"🧹 Sweep Calendar ({when}): " + " · ".join(parts) + f" · {DASHBOARD}"


def main(argv: List[str] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    s = collect(args.days, datetime.now(TZ).date())
    if args.json:
        print(json.dumps(s, indent=1))
    else:
        out = line(s)
        if out:
            print(out)


if __name__ == "__main__":
    main()
