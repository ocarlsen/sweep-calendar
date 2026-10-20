import unittest
from datetime import date, time
from pathlib import Path

from parkcal import canary
from parkcal.build import check_sanity
from parkcal.cities import sf, sj
from parkcal.model import BlockSide, DataError, HolidayKind, Rule

PAGE = (Path(__file__).parent / "fixtures" / "sfmta_holidays_2026-10-19.html").read_text()


class SfmtaHolidays(unittest.TestCase):
    def test_parses_the_real_page(self):
        published = canary.parse_sfmta(PAGE)
        self.assertEqual(len(published), 13)
        self.assertEqual(published[date(2026, 11, 26)], ("Thanksgiving Day", HolidayKind.FULL))
        self.assertEqual(published[date(2026, 10, 12)][1], HolidayKind.PARTIAL)

    def test_current_rules_match(self):
        self.assertEqual(canary.compare(canary.parse_sfmta(PAGE)), [])

    def test_fires_on_a_policy_change(self):
        # Positive control: SFMTA starts suspending night sweeping on Veterans Day.
        changed = PAGE.replace(
            "<td>11/11/2026</td>\n<td>Veterans Day</td>\n<td>Enforced</td>\n<td>Enforced</td>",
            "<td>11/11/2026</td>\n<td>Veterans Day</td>\n<td>Enforced</td>\n<td>Not Enforced</td>")
        self.assertNotEqual(changed, PAGE)
        (problem,) = canary.compare(canary.parse_sfmta(changed))
        self.assertIn("2026-11-11", problem)

    def test_fires_on_an_added_holiday_and_a_broken_page(self):
        published = canary.parse_sfmta(PAGE)
        published[date(2026, 3, 31)] = ("Cesar Chavez Day", HolidayKind.PARTIAL)
        self.assertEqual(len(canary.compare(published)), 1)
        self.assertIn("MIN_ROWS", canary.compare({})[0])


class BuildSanity(unittest.TestCase):
    def side(self, i, line=True, rules=True):
        rule = [Rule(0, frozenset({1}), time(8), time(10))] if rules else []
        return BlockSide(str(i), "A St", "B St - C St", "North side",
                         [(-122.4, 37.7), (-122.4, 37.701)] if line else [], rules=rule)

    def test_current_floor_passes(self):
        check_sanity(sf.CITY, [self.side(i) for i in range(sf.CITY.min_block_sides)])

    def test_refuses_partial_data(self):
        with self.assertRaises(DataError) as e:
            check_sanity(sf.CITY, [self.side(i) for i in range(100)])
        self.assertIn("min_block_sides", str(e.exception))

    def test_refuses_missing_schedules_and_geometry(self):
        sides = [self.side(i) for i in range(sf.CITY.min_block_sides)]
        sides[0] = self.side(0, rules=False)
        sides[1:600] = [self.side(i, line=False) for i in range(1, 600)]
        with self.assertRaises(DataError) as e:
            check_sanity(sf.CITY, sides)
        self.assertIn("no schedule", str(e.exception))
        self.assertIn("MAX_MISSING_LINE", str(e.exception))


class UnrecognizedSigns(unittest.TestCase):
    def row(self, posted, routetype="Signed Route", weekday="Every Tuesday"):
        return {"geometry": {"type": "LineString", "coordinates": [[-121.89, 37.33], [-121.89, 37.331]]},
                "properties": {"CENTERLINEID": 1, "ROADNAME": "Test St", "ROADFROM": "A ST", "ROADTO": "B ST",
                               "SIDEOFROAD": "Both", "ROUTETYPE": routetype, "APPPUBLICPOSTEDTIMES": posted,
                               "APPWEEKDAY": weekday, "APPSCHEDULEDTIME": "1am - 6am", "PRIMARYSEGMENT": "Yes"}}

    def test_every_phrase_the_city_uses_today_is_recognized(self):
        import json
        rows = json.loads((Path(__file__).parent / "fixtures" / "sj_major_sample.json").read_text())
        sj.to_blocksides([], rows)  # raises DataError on anything unrecognized

    def test_a_new_phrasing_fails_the_build(self):
        # Positive control: a reworded sweeping sign the parser can't read.
        with self.assertRaises(DataError) as e:
            sj.to_blocksides([], [self.row("Both Sides: No Prkg Tue 1-6")])
        self.assertIn("No Prkg Tue 1-6", str(e.exception))
        with self.assertRaises(DataError):
            sj.to_blocksides([], [self.row("", "Non-Signed Route", weekday="Tues & Thurs")])

    def test_known_skips_pass(self):
        sj.to_blocksides([], [self.row("Both Sides: No Stopping Anytime"),
                              self.row("Both Sides: No Parking 2nd and 4th Monday 6pm-8am")])


if __name__ == "__main__":
    unittest.main()
