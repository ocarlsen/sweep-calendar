"""Minimal RFC 5545 writer: just what a subscribed no-parking feed needs.

Each block side becomes a handful of recurring events rather than one event
per sweep: each rule is one WEEKLY or MONTHLY RRULE ("BYDAY=1TU,3TU"),
holidays that suspend a sweep become EXDATEs, and holiday-only sweeps become
RDATEs. Times are local (TZID) so the recurrence
survives daylight-saving changes.
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterator, List
from zoneinfo import ZoneInfo

from .expand import applies, holiday_table
from .holidays import week_of_month
from .model import BlockSide, City, HolidayKind, Rule

PRODID = "-//parking.ocarlsen.com//Parking Calendar//EN"
DOMAIN = "parking.ocarlsen.com"
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold to 75-octet lines, never splitting a UTF-8 sequence."""
    out, chunk = [], ""
    for ch in line:
        limit = 75 if not out else 74  # continuation lines start with a space
        if len((chunk + ch).encode()) > limit:
            out.append(chunk)
            chunk = ""
        chunk += ch
    out.append(chunk)
    return "\r\n ".join(out)


def hour_label(t: time) -> str:
    h = t.hour % 12 or 12
    return f"{h}{'am' if t.hour < 12 else 'pm'}"


def utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def local(day: date, t: time) -> str:
    return f"{day:%Y%m%d}T{t:%H%M%S}"


def vtimezone(tz: str, year: int) -> List[str]:
    """VTIMEZONE for a zone on the US daylight-saving rule (2nd Sunday of
    March to 1st Sunday of November, since 2007). Offsets come from zoneinfo;
    the transition rule is US-only, which holds while every city is."""
    z = ZoneInfo(tz)

    def offset(month: int) -> str:
        mins = int(datetime(year, month, 1, tzinfo=z).utcoffset().total_seconds() // 60)
        sign = "-" if mins < 0 else "+"
        return f"{sign}{abs(mins) // 60:02d}{abs(mins) % 60:02d}"

    std, dst = offset(1), offset(7)
    if std == dst:
        return ["BEGIN:VTIMEZONE", f"TZID:{tz}", "BEGIN:STANDARD",
                "DTSTART:19700101T000000", f"TZOFFSETFROM:{std}", f"TZOFFSETTO:{std}",
                "END:STANDARD", "END:VTIMEZONE"]
    return [
        "BEGIN:VTIMEZONE", f"TZID:{tz}",
        "BEGIN:DAYLIGHT", "DTSTART:20070311T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
        f"TZOFFSETFROM:{std}", f"TZOFFSETTO:{dst}", "END:DAYLIGHT",
        "BEGIN:STANDARD", "DTSTART:20071104T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
        f"TZOFFSETFROM:{dst}", f"TZOFFSETTO:{std}", "END:STANDARD",
        "END:VTIMEZONE",
    ]


def days(first: date, last: date) -> Iterator[date]:
    while first <= last:
        yield first
        first += timedelta(days=1)


def scheduled(rule: Rule, day: date) -> bool:
    """Would the rule's recurrence land on `day`, ignoring holidays?"""
    return day.weekday() == rule.weekday and week_of_month(day) in rule.weeks


def calendar(side: BlockSide, city: City, first: date, last: date,
             stamp: datetime, holidays: Dict[date, HolidayKind] = None) -> str:
    holidays = holiday_table(city, first, last) if holidays is None else holidays
    where = f"{side.street}, {side.limits} ({side.side})"
    tzid = f"TZID={city.tz}"
    until = utc(datetime.combine(last, time(23, 59, 59), ZoneInfo(city.tz)))

    lines: List[str] = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(f'No parking: {side.street} ({side.side})')}",
        f"X-WR-TIMEZONE:{city.tz}",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D", "X-PUBLISHED-TTL:P1D",
        *vtimezone(city.tz, first.year),
    ]

    def event(uid: str, start: time, end: time, dtstart: date, extra: List[str]):
        hours = f"{hour_label(start)}–{hour_label(end)}"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{city.slug}-{side.id}-{uid}@{DOMAIN}",
            f"DTSTAMP:{utc(stamp)}",
            f"DTSTART;{tzid}:{local(dtstart, start)}",
            f"DTEND;{tzid}:{local(dtstart, end)}",
            *extra,
            f"SUMMARY:{escape(f'No parking {hours}: street sweeping')}",
            f"LOCATION:{escape(where)}",
            f"DESCRIPTION:{escape(f'Posted signs control. Source: {city.source_url}')}",
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-PT12H",
            f"DESCRIPTION:{escape(f'Move your car: sweeping {hours}')}",
            "END:VALARM",
            "END:VEVENT",
        ])

    covered = set()  # (day, start, end) already produced by a weekday rule
    weekly = sorted((r for r in side.rules if not r.holiday_only),
                    key=lambda r: (r.weekday, r.start))
    for r in weekly:
        hits = [d for d in days(first, last) if scheduled(r, d)]
        if not hits:
            continue
        skipped = [d for d in hits if not applies(r, d, holidays.get(d))]
        covered.update((d, r.start, r.end) for d in hits if d not in skipped)
        # One weekday per event, the RRULE shapes every client handles.
        if len(r.weeks) == 5:
            rrule = f"FREQ=WEEKLY;BYDAY={DAYS[r.weekday]}"
        else:
            rrule = "FREQ=MONTHLY;BYDAY=" + ",".join(
                f"{n}{DAYS[r.weekday]}" for n in sorted(r.weeks))
        extra = [f"RRULE:{rrule};UNTIL={until}"]
        extra += [f"EXDATE;{tzid}:{local(d, r.start)}" for d in skipped]
        event(f"{DAYS[r.weekday]}{''.join(map(str, sorted(r.weeks)))}"
              f"-{r.start:%H}{r.end:%H}", r.start, r.end, hits[0], extra)

    for r in side.rules:
        if not r.holiday_only:
            continue
        dates = [d for d in days(first, last)
                 if applies(r, d, holidays.get(d)) and (d, r.start, r.end) not in covered]
        if dates:
            extra = [f"RDATE;{tzid}:{local(d, r.start)}" for d in dates[1:]]
            event(f"hol{r.start:%H}{r.end:%H}", r.start, r.end, dates[0], extra)

    lines.append("END:VCALENDAR")
    return "".join(fold(l) + "\r\n" for l in lines)
