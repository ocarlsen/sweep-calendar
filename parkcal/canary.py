"""Canaries: checks against sources we don't control, run weekly in CI.

    python3 -m parkcal.canary

SFMTA holiday table. sf.holidays() computes San Francisco's holidays from
rules (MLK = 3rd Monday of January, observed dates, ...) and is tested
against the 2026 table. SFMTA republishes the table each year and can change
policy (add a holiday, enforce one it used to suspend). This reads the live
table and fails if any listed date disagrees with what the feeds use.

Exit 0 = all clear (silent but for one summary line); 1 = a disagreement or a
page we can no longer parse, which is itself worth a look.
"""
import html
import re
import sys
from datetime import date
from typing import Dict, List, Tuple

from .cities import sf
from .fetch import get
from .model import HolidayKind

SFMTA_URL = "https://www.sfmta.com/getting-around/drive-park/holiday-enforcement-schedule"
MIN_ROWS = 8  # a year's table has ~12; fewer means the page changed shape
ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def text(cell: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())


def parse_sfmta(page: str) -> Dict[date, Tuple[str, HolidayKind]]:
    """{date: (holiday name, kind)} from the enforcement table. "Nightly Street
    Sweeping: Not Enforced" means nothing is swept (FULL); "Enforced" with daytime
    programs off means only night sweeping runs (PARTIAL)."""
    out = {}
    for row in ROW.findall(page):
        cells = [text(c) for c in CELL.findall(row)]
        if len(cells) != 5 or not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", cells[0]):
            continue
        m, d, y = map(int, cells[0].split("/"))
        nightly = cells[3].lower()
        if nightly not in ("enforced", "not enforced"):
            raise ValueError(f"unexpected Nightly Street Sweeping value {cells[3]!r} on {cells[0]}")
        out[date(y, m, d)] = (cells[1], HolidayKind.FULL if nightly == "not enforced" else HolidayKind.PARTIAL)
    return out


def compare(published: Dict[date, Tuple[str, HolidayKind]]) -> List[str]:
    if len(published) < MIN_ROWS:
        return [f"parsed only {len(published)} rows (< MIN_ROWS={MIN_ROWS}); has the SFMTA page changed?"]
    ours: Dict[date, HolidayKind] = {}
    for year in {d.year for d in published} | {d.year + 1 for d in published}:
        ours.update(sf.holidays(year))
    problems = [f"{d} {name}: SFMTA says {kind.value}, feeds use {ours[d].value if d in ours else 'no holiday'}"
                for d, (name, kind) in sorted(published.items()) if ours.get(d) is not kind]
    # A year SFMTA lists in full must not have extra holidays on our side.
    for year in {d.year for d in published}:
        if sum(d.year == year for d in published) < MIN_ROWS:
            continue  # partial year ("early 2027"); only the listed dates are checked
        extra = sorted(d for d in ours if d.year == year and d not in published)
        problems += [f"{d}: feeds treat it as a holiday, SFMTA doesn't list it" for d in extra]
    return problems


def main() -> int:
    published = parse_sfmta(get(SFMTA_URL, timeout=60).decode("utf-8", "replace"))
    problems = compare(published)
    if problems:
        print("SFMTA holiday canary FAILED:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"SFMTA holiday canary ok: {len(published)} listed dates match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
