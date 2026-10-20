import json
import unittest
from datetime import date, time
from pathlib import Path

from parkcal.cities import sj
from parkcal.expand import windows

FIXTURE = Path(__file__).parent / "fixtures" / "sj_sample.json"
FEATURES = json.loads(FIXTURE.read_text())
SIDES = {s.id: s for s in sj.to_blocksides(FEATURES)}


class Parsing(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(sj.parse_windows("7am - 5pm"), [(time(7), time(17))])
        self.assertEqual(sj.parse_windows("8AM to NOON;  then 1PM to 4:30PM     "),
                         [(time(8), time(12)), (time(13), time(16, 30))])
        self.assertEqual(sj.parse_windows("7AM to 12PM; then 12:30PM to 5PM"),
                         [(time(7), time(12)), (time(12, 30), time(17))])
        self.assertEqual(sj.parse_windows("12am - 8am"), [(time(0), time(8))])

    def test_per_side_days(self):
        row = {"APPWEEKDAYCODE": "Monday,Wednesday,1,2",
               "APPWEEKDAY": "West Side: 1st Monday of the Month; East Side: 2nd Wednesday of the Month"}
        self.assertEqual(sj.parse_day({**row, "SIDEOFROAD": "West"}), (0, frozenset({1})))
        self.assertEqual(sj.parse_day({**row, "SIDEOFROAD": "East"}), (2, frozenset({2})))

    def test_signed_vs_unsigned(self):
        base = {"APPWEEKDAYCODE": "Thursday,4", "APPSCHEDULEDTIME": "7am - 5pm"}
        unsigned = sj.rules_for({**base, "ROUTETYPE": "Non-Signed Route", "APPPUBLICPOSTEDTIMES": ""})
        self.assertEqual([(r.start, r.end, r.advisory) for r in unsigned], [(time(7), time(17), True)])
        signed = sj.rules_for({**base, "ROUTETYPE": "Signed Route",
                               "APPPUBLICPOSTEDTIMES": "9AM to NOON;  then 1PM to 4PM"})
        self.assertEqual([(r.start, r.end, r.advisory) for r in signed],
                         [(time(9), time(12), False), (time(13), time(16), False)])

    def test_names(self):
        self.assertEqual(sj.title("LITTLE ROCK DR"), "Little Rock Dr")
        self.assertEqual(sj.title("EOS"), "End of street")
        self.assertEqual(sj.title("McKee Rd"), "McKee Rd")

    def test_every_fixture_row_parses_and_medians_are_skipped(self):
        rows = [f for f in FEATURES if f["properties"]["ROUTETYPE"] != "Median Island"]
        self.assertTrue(all(s.rules for s in SIDES.values()))
        self.assertLessEqual(len(SIDES), len(rows))
        self.assertTrue(any(f["properties"]["ROUTETYPE"] == "Median Island" for f in FEATURES))


class Schedule(unittest.TestCase):
    def test_nth_weekday_monthly(self):
        side = sj.to_blocksides([{
            "geometry": {"type": "LineString", "coordinates": [[-121.9, 37.3], [-121.9, 37.301]]},
            "properties": {"ROUTETYPE": "Non-Signed Route", "SIDEOFROAD": "Both", "CENTERLINEID": 1,
                           "ROADNAME": "Test St", "ROADFROM": "A ST", "ROADTO": "EOS",
                           "APPWEEKDAYCODE": "Thursday,4", "APPSCHEDULEDTIME": "7am - 5pm"}}])[0]
        days = [w.start.date() for w in windows(side, sj.CITY, date(2026, 11, 1), date(2027, 1, 31))]
        self.assertEqual(days, [date(2026, 11, 26), date(2026, 12, 24), date(2027, 1, 28)])
        self.assertEqual(side.limits, "A St - End of street")


if __name__ == "__main__":
    unittest.main()
