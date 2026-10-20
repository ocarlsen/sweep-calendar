"""San José major-road sweeping (ArcGIS layer 516): parse the schedule text.

City DOT sweeps arterial, downtown and business-district streets at night.
Two fields describe a segment, per side of the road:

  APPPUBLICPOSTEDTIMES  what the posted sign says. On a Signed Route this is
                        what gets ticketed, so it is the source of truth.
  APPWEEKDAY + APPSCHEDULEDTIME  when the sweeper is scheduled. Used for
                        Non-Signed routes, as a "not ticketed" request.

Posted text mixes sweeping restrictions with unrelated ones ("No Stopping
Anytime", "2 Hour Parking", "Loading Zone Only", permit exceptions). Only
clauses shaped like a sweeping window are kept: optional "No Parking", days
(nth weekdays, weekdays, or "Any Day") and one same-day time range. Anything
else yields no rule for that side, and the side gets no calendar rather than
a guessed one. Two shapes are skipped on purpose because one weekly rule
can't express them: an overnight window (6pm-8am) and weekend-plus-nightly
mixes.
"""
import re
from datetime import time
from typing import Dict, List, Optional, Tuple

ALL_WEEKS = frozenset({1, 2, 3, 4, 5})
ORDINALS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}
DAYS = {d: i for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
SIDE = re.compile(r"\s*(North|South|East|West|Both) Sides?(?: Swept Only)?\s*:\s*(.*)$", re.I | re.S)
CLOCK = r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|noon)"
RANGE = re.compile(rf"({CLOCK})\s*(?:-|to)\s*({CLOCK})", re.I)
# Words that mark a non-sweeping restriction; "any day" is fine, "anytime" is not.
NOT_SWEEPING = ("stopping", "hour parking", "minute parking", "min max", "loading", "trolley",
                "permit", "except", "anytime", "sat-sun")

Window = Tuple[int, frozenset, time, time]  # weekday, weeks, start, end


def parse_clock(text: str) -> time:
    t = text.strip().lower()
    if t == "noon":
        return time(12)
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", t)
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    return time(hour % 12 + (12 if m.group(3) == "pm" else 0), minute)


def split_sides(text: str) -> Dict[str, str]:
    """'West Side: X, East Side: Y' -> {'West': 'X', 'East': 'Y'}; no prefix -> {'Both': text}."""
    text = re.sub(r"\([^)]*\)", "", text or "").strip()
    out: Dict[str, str] = {}
    for part in re.split(r"[,;]\s*(?=(?:North|South|East|West|Both) Sides?\b)", text):
        m = SIDE.match(part)
        if m:
            out[m.group(1).capitalize()] = m.group(2).strip()
        elif part.strip():
            out["Both"] = part.strip()
    return out


def parse_days(phrase: str) -> Optional[List[Tuple[int, frozenset]]]:
    """'1st and 3rd Monday of the Month' / 'Mondays and Thursdays' / 'Every Tuesday' / 'Any Day'."""
    s = re.sub(r"\bof the month\b|\bevery\b", " ", phrase.lower())
    if re.search(r"\bany ?day\b", s):
        return [(d, ALL_WEEKS) for d in range(7)]
    days = [DAYS[d] for d in re.findall(r"\b(" + "|".join(DAYS) + r")s?\b", s)]
    if not days:
        return None
    ords = frozenset(ORDINALS[o] for o in re.findall(r"\b(1st|2nd|3rd|4th|5th)\b", s))
    return [(d, ords or ALL_WEEKS) for d in days]


def parse_sign(clause: str) -> Optional[List[Window]]:
    """One side's posted text -> its sweeping windows, or None if it isn't one."""
    low = clause.lower()
    if not low.strip() or any(w in low for w in NOT_SWEEPING):
        return None
    m = RANGE.search(low)
    if not m:
        return None
    start, end = parse_clock(m.group(1)), parse_clock(m.group(2))
    if end <= start:
        return None  # overnight window: not expressible as one same-day rule
    rest = re.sub(r"^\s*no parking\s+|\bfrom\b", " ", low[:m.start()] + " " + low[m.end():])
    days = parse_days(rest)
    return [(d, w, start, end) for d, w in days] if days else None


def scheduled(weekday_text: str, window_text: str) -> Dict[str, List[Window]]:
    """Unsigned routes: sweep days per side x the scheduled window ('12am - 8am')."""
    m = RANGE.search(window_text or "")
    if not m:
        return {}
    start, end = parse_clock(m.group(1)), parse_clock(m.group(2))
    if end <= start:
        return {}
    out = {}
    for side, phrase in split_sides(weekday_text).items():
        days = parse_days(phrase)
        if days:
            out[side] = [(d, w, start, end) for d, w in days]
    return out


TIME_LIMIT = re.compile(r"\b(hour|minute|min)\b.*\bparking\b|\bparking\b.*\bmax\b", re.I)


def is_time_limit(clause: str) -> bool:
    """'2 Hour Parking', '30min Max Parking Anytime': parking is legal overnight, so the sweeper still matters."""
    return bool(TIME_LIMIT.search(clause or ""))


def posted(text: str) -> Dict[str, List[Window]]:
    out = {}
    for side, clause in split_sides(text).items():
        wins = parse_sign(clause)
        if wins:
            out[side] = wins
    return out


def unrecognized(text: str) -> List[str]:
    """Posted clauses that are neither a sweeping window, nor a known
    non-sweeping restriction, nor a shape we skip on purpose (an overnight
    window). A new phrasing from the city lands here instead of silently
    leaving a ticketed curb with no calendar."""
    out = []
    for clause in split_sides(text).values():
        low = clause.lower()
        if parse_sign(clause) or any(w in low for w in NOT_SWEEPING):
            continue
        m = RANGE.search(low)
        if m and parse_clock(m.group(2)) <= parse_clock(m.group(1)) and parse_days(low):
            continue  # overnight window: known, skipped on purpose
        out.append(clause)
    return out
