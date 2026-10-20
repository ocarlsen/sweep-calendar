import json
import unittest
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

from parkcal.cities import sf
from parkcal.expand import windows
from parkcal.merge import join_split_blocks
from parkcal.model import BlockSide, HolidayKind, Rule

FIXTURE = Path(__file__).parent / "fixtures" / "sf_sample.json"
ROWS = json.loads(FIXTURE.read_text())
SIDES = {s.id: s for s in sf.to_blocksides(ROWS)}


def side_with(*rules: Rule) -> BlockSide:
    return BlockSide("t", "Test St", "A St - B St", "North side", [], rules=list(rules))


def sweep_days(side, first, last):
    return [w.start.date() for w in windows(side, sf.CITY, first, last)]


class HolidayCalendar(unittest.TestCase):
    def test_matches_sfmta_2026_table(self):
        # Transcribed from sfmta.com/getting-around/drive-park/holiday-enforcement-schedule
        # ("Nightly Street Sweeping" column: Not Enforced = FULL).
        official = {
            date(2026, 1, 1): HolidayKind.FULL,
            date(2026, 1, 19): HolidayKind.PARTIAL,
            date(2026, 2, 16): HolidayKind.PARTIAL,
            date(2026, 5, 25): HolidayKind.PARTIAL,
            date(2026, 6, 19): HolidayKind.PARTIAL,
            date(2026, 7, 3): HolidayKind.PARTIAL,
            date(2026, 9, 7): HolidayKind.PARTIAL,
            date(2026, 10, 12): HolidayKind.PARTIAL,
            date(2026, 11, 11): HolidayKind.PARTIAL,
            date(2026, 11, 26): HolidayKind.FULL,
            date(2026, 11, 27): HolidayKind.PARTIAL,
            date(2026, 12, 25): HolidayKind.FULL,
        }
        self.assertEqual(sf.holidays(2026), official)
        self.assertEqual(sf.holidays(2027)[date(2027, 1, 1)], HolidayKind.FULL)


class Rules(unittest.TestCase):
    def test_nth_weekday_including_fifth(self):
        # Lower Great Hwy, Lincoln-Irving, west side: "Tue 1st, 3rd, 5th", 1-3pm.
        side = SIDES["8508000R"]
        self.assertEqual(
            sweep_days(side, date(2026, 11, 1), date(2026, 12, 31)),
            [date(2026, 11, 3), date(2026, 11, 17),
             date(2026, 12, 1), date(2026, 12, 15), date(2026, 12, 29)])
        w = windows(side, sf.CITY, date(2026, 11, 3), date(2026, 11, 3))[0]
        self.assertEqual((w.start.hour, w.end.hour), (13, 15))

    def test_daytime_sweep_suspended_on_holiday(self):
        side = side_with(Rule(0, frozenset({1, 2, 3, 4, 5}), time(8), time(10)))
        days = sweep_days(side, date(2026, 1, 12), date(2026, 1, 26))
        self.assertEqual(days, [date(2026, 1, 12), date(2026, 1, 26)])  # not MLK 1/19

    def test_night_sweep_runs_on_holiday_but_not_christmas(self):
        nightly = Rule(4, frozenset({1, 2, 3, 4, 5}), time(0), time(6), on_holidays=True)
        self.assertEqual(sweep_days(side_with(nightly), date(2026, 11, 27), date(2026, 11, 27)),
                         [date(2026, 11, 27)])  # day after Thanksgiving: enforced
        self.assertEqual(sweep_days(side_with(nightly), date(2026, 12, 25), date(2026, 12, 25)),
                         [])  # Christmas: nothing swept

    def test_holiday_only_rule(self):
        side = side_with(Rule(None, frozenset(), time(4), time(6)))
        self.assertEqual(sweep_days(side, date(2026, 10, 1), date(2026, 12, 31)),
                         [date(2026, 10, 12), date(2026, 11, 11), date(2026, 11, 27)])

    def test_dst_keeps_wall_clock(self):
        side = side_with(Rule(6, frozenset({1, 2, 3, 4, 5}), time(8), time(10)))
        before, after = windows(side, sf.CITY, date(2026, 10, 25), date(2026, 11, 1))
        self.assertEqual((before.start.hour, after.start.hour), (8, 8))
        self.assertNotEqual(before.start.utcoffset(), after.start.utcoffset())

    def test_overnight_window_ends_the_next_morning(self):
        # Keyed on the start day: 1st & 3rd Monday 8pm to Tuesday 5am, across a DST change.
        side = side_with(Rule(0, frozenset({1, 3}), time(20), time(5)))
        got = windows(side, sf.CITY, date(2026, 10, 1), date(2026, 11, 30))
        self.assertEqual([(w.start.date(), w.start.hour, w.end.date(), w.end.hour) for w in got],
                         [(date(2026, 10, 5), 20, date(2026, 10, 6), 5),
                          (date(2026, 10, 19), 20, date(2026, 10, 20), 5),
                          (date(2026, 11, 2), 20, date(2026, 11, 3), 5),
                          (date(2026, 11, 16), 20, date(2026, 11, 17), 5)])

    def test_back_to_back_windows_merge(self):
        side = side_with(Rule(0, frozenset({1}), time(0), time(2)),
                         Rule(0, frozenset({1}), time(2), time(6)))
        (w,) = windows(side, sf.CITY, date(2026, 11, 2), date(2026, 11, 2))
        self.assertEqual((w.start.hour, w.end.hour), (0, 6))


class Parsing(unittest.TestCase):
    def test_split_weeks_merge_into_one_rule(self):
        tue = lambda weeks: Rule(1, frozenset(weeks), time(6), time(8))
        self.assertEqual(sf.merge_weeks([tue({2, 4}), tue({1, 3, 5}), tue({2, 4})]),
                         [tue({1, 2, 3, 4, 5})])
        night = Rule(1, frozenset({1}), time(6), time(8), on_holidays=True)
        self.assertEqual(len(sf.merge_weeks([tue({2}), night])), 2)

    def test_rows_group_by_cnn_and_side(self):
        self.assertEqual(len(SIDES), len({(r["cnn"], r["cnnrightleft"]) for r in ROWS}))

    def test_geometry_side_agrees_with_published_side(self):
        # The fallback for rows missing `blockside` must agree with the rows
        # that have it, or users get pointed at the wrong curb.
        results = Counter(
            sf.side_from_geometry(r["line"]["coordinates"], r["cnnrightleft"]) == r["blockside"]
            for r in ROWS if r.get("blockside") and "line" in r)
        agree = results[True] / sum(results.values())
        self.assertGreater(agree, 0.9, results)

    def test_every_side_has_a_label(self):
        unknown = [s.id for s in SIDES.values() if s.side == "Side unknown" and s.line]
        self.assertEqual(unknown, [])


class JoinSplitBlocks(unittest.TestCase):
    def piece(self, id, limits, line, rules=None):
        rule = Rule(1, frozenset({1, 3}), time(9), time(11))
        return BlockSide(id, "Greenwich St", limits, "South side", line,
                         rules=rules or [rule])

    def test_joins_across_alley_with_same_schedule(self):
        # Shape of the real case: Greenwich St split at Imperial Aly.
        a = self.piece("1L", "Franklin St - Imperial Aly", [(-122.4260, 37.8000), (-122.4250, 37.8000)])
        b = self.piece("2L", "Imperial Aly - Gough St", [(-122.4250, 37.8000), (-122.4240, 37.8000)])
        (one,) = join_split_blocks([b, a], sf.minor_street)
        self.assertEqual(one.limits, "Franklin St - Gough St")
        self.assertEqual({p.id for p in one.pieces}, {"1L", "2L"})

    def test_keeps_real_cross_streets_and_different_schedules(self):
        a = self.piece("1L", "Franklin St - Gough St", [(-122.4260, 37.8000), (-122.4250, 37.8000)])
        b = self.piece("2L", "Gough St - Octavia St", [(-122.4250, 37.8000), (-122.4240, 37.8000)])
        self.assertEqual(len(join_split_blocks([a, b], sf.minor_street)), 2)
        fri = [Rule(4, frozenset({1, 3}), time(9), time(11))]
        c = self.piece("3L", "Franklin St - Imperial Aly", [(-122.4260, 37.8000), (-122.4250, 37.8000)])
        d = self.piece("4L", "Imperial Aly - Gough St", [(-122.4250, 37.8000), (-122.4240, 37.8000)], fri)
        self.assertEqual(len(join_split_blocks([c, d], sf.minor_street)), 2)

    def test_needs_touching_ends(self):
        a = self.piece("1L", "Franklin St - Imperial Aly", [(-122.4260, 37.8000), (-122.4250, 37.8000)])
        b = self.piece("2L", "Imperial Aly - Gough St", [(-122.4100, 37.8000), (-122.4090, 37.8000)])
        self.assertEqual(len(join_split_blocks([a, b], sf.minor_street)), 2)


if __name__ == "__main__":
    unittest.main()
