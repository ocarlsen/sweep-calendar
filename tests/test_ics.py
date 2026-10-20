"""The feeds use RRULE/EXDATE/RDATE; check that an independent iCalendar
implementation expands them to exactly the windows our own expander computes."""
import unittest
from datetime import date, datetime, time, timedelta, timezone

from parkcal import ics
from parkcal.cities import emeryville, sf, sj
from parkcal.expand import windows
from parkcal.model import BlockSide, Rule

from .test_sf import SIDES as SF_SIDES
from .test_sj import SIDES as SJ_SIDES
from .test_sj_major import SIDES as SJ_MAJOR_SIDES
from .test_emeryville import SIDES as EMV_SIDES

OVERNIGHT = BlockSide("night", "Test St", "A St - B St", "Both sides", [], rules=[
    Rule(0, frozenset({1, 3}), time(20), time(5)), Rule(3, frozenset({1, 2, 3, 4, 5}), time(22), time(0))])

CASES = ([(sf.CITY, s) for s in SF_SIDES.values()] + [(sf.CITY, OVERNIGHT)] + [(sj.CITY, s) for s in SJ_SIDES.values()]
         + [(sj.CITY, s) for s in SJ_MAJOR_SIDES.values() if s.rules]
         + [(emeryville.CITY, s) for s in EMV_SIDES.values()])

try:
    import icalendar
    import recurring_ical_events
except ImportError:  # dev-only dependencies; runtime is stdlib
    icalendar = None

FIRST, LAST = date(2026, 10, 19), date(2027, 10, 19)
STAMP = datetime(2026, 10, 19, tzinfo=timezone.utc)


def merged(spans):
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


class Format(unittest.TestCase):
    def test_lines_are_crlf_and_folded(self):
        for city, side in CASES:
            text = ics.calendar(side, city, FIRST, LAST, STAMP)
            self.assertTrue(text.endswith("\r\n"))
            for line in text.split("\r\n"):
                self.assertLessEqual(len(line.encode()), 75, line)

    def test_escape(self):
        self.assertEqual(ics.escape("a,b;c\\d\ne"), "a\\,b\\;c\\\\d\\ne")


@unittest.skipIf(icalendar is None, "pip install icalendar recurring-ical-events")
class CrossCheck(unittest.TestCase):
    def test_feeds_expand_to_expected_windows(self):
        checked = 0
        for city, side in CASES:
            feed = icalendar.Calendar.from_ical(ics.calendar(side, city, FIRST, LAST, STAMP))
            events = recurring_ical_events.of(feed).between(FIRST, LAST + timedelta(days=1))
            got = merged((e["DTSTART"].dt, e["DTEND"].dt) for e in events)
            want = [(w.start, w.end) for w in windows(side, city, FIRST, LAST)]
            self.assertEqual(got, want, side.id)
            checked += len(want)
        self.assertGreater(checked, 1000)


if __name__ == "__main__":
    unittest.main()
