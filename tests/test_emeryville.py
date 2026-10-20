import json
import unittest
from datetime import date, time
from pathlib import Path

from parkcal.cities import emeryville as E
from parkcal.expand import windows
from parkcal.model import DataError

# The whole layer is small (776 segments), so the fixture is all of it.
FEATURES = json.loads((Path(__file__).parent / "fixtures" / "emeryville_all.json").read_text())
SIDES = {s.id: s for s in E.to_blocksides(FEATURES)}


def row(**props):
    base = {"OBJECTID": 1, "GlobalID": "{ABCDEF12-0000}", "StreetName": "TEST ST", "RouteClass": "Residential",
            "DayOfWeek": "Thursday", "DayOfMonth": "1st", "TimeOfDay": 2}
    return {"geometry": {"type": "LineString", "coordinates": [[-122.29, 37.83], [-122.288, 37.83]]},
            "properties": {**base, **props}}


class Codes(unittest.TestCase):
    def test_not_swept_segments_are_skipped(self):
        self.assertEqual(E.to_blocksides([row(RouteClass="Not Swept")]), [])
        self.assertEqual(E.to_blocksides([row(RouteClass=None, DayOfWeek=None)]), [])

    def test_overnight_commercial_route(self):
        (side,) = E.to_blocksides([row(RouteClass="Commercial", DayOfWeek="Monday",
                                       DayOfMonth="1st & 3rd", TimeOfDay=1)])
        (rule,) = side.rules
        self.assertEqual((rule.weekday, sorted(rule.weeks), rule.start, rule.end, rule.overnight),
                         (0, [1, 3], time(20), time(5), True))
        (first,) = windows(side, E.CITY, date(2026, 11, 2), date(2026, 11, 2))
        self.assertEqual((first.start.hour, first.end.date(), first.end.hour), (20, date(2026, 11, 3), 5))

    def test_first_or_second_splits_by_side(self):
        # A centerline with no curb lines beside it, east-west: north on the 1st Thursday.
        sides = {s.side: s for s in E.to_blocksides([row(DayOfMonth="1st or 2nd")])}
        self.assertEqual(set(sides), {"North side", "South side"})
        self.assertEqual(sides["North side"].rules[0].weeks, frozenset({1}))
        self.assertEqual(sides["South side"].rules[0].weeks, frozenset({2}))
        self.assertNotEqual(sides["North side"].curb, sides["South side"].curb)
        # North-south: EAST on the 1st Thursday (the city's curb lines; not west).
        ns = row(DayOfMonth="1st or 2nd")
        ns["geometry"]["coordinates"] = [[-122.29, 37.83], [-122.29, 37.832]]
        by_side = {s.side: s.rules[0].weeks for s in E.to_blocksides([ns])}
        self.assertEqual(by_side, {"East side": frozenset({1}), "West side": frozenset({2})})

    def test_unknown_code_fails_the_build(self):
        with self.assertRaises(DataError):
            E.to_blocksides([row(DayOfMonth="2nd & 4th")])


class RealLayer(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(len(SIDES), 358)
        self.assertEqual(sum(s.side == "Both sides" for s in SIDES.values()), 181)
        self.assertTrue(all(s.rules for s in SIDES.values()))

    def test_city_curb_lines_set_the_side_rule(self):
        # Measured: every full-length curb line beside a '1st or 2nd' centerline
        # is north/east on the 1st Thursday and south/west on the 2nd.
        import math
        centers = [f for f in FEATURES if f.get("geometry") and E.swept(f["properties"])
                   and f["properties"]["DayOfMonth"] == E.SPLIT]
        curbs = [f for f in FEATURES if f.get("geometry") and E.swept(f["properties"])
                 and f["properties"].get("RouteType") == E.CURB_LINE]
        checked = 0
        for c in centers:
            cx, cy = E.midpoint(c["geometry"]["coordinates"])
            ew = abs(abs(E.normal_angle(c["geometry"]["coordinates"], "R")) - 90) < 45
            for k in curbs:
                line = k["geometry"]["coordinates"]
                x, y = E.midpoint(line)
                own_block = (k["properties"]["StreetName"] == c["properties"]["StreetName"]
                             and E._dist_m((x, y), [(cx, cy), (cx, cy)]) <= 25)
                if not own_block or E._dist_m(line[0], [line[-1], line[-1]]) < 20:
                    continue  # another block's curb, or a corner stub
                where = ("North" if y > cy else "South") if ew else ("East" if x > cx else "West")
                self.assertEqual(where in ("North", "East"), k["properties"]["DayOfMonth"] == "1st")
                checked += 1
        self.assertGreater(checked, 120)

    def test_no_curb_contradicts_the_rule(self):
        for s in SIDES.values():
            if s.side != "Both sides":
                first = s.side.split()[0] in ("North", "East")
                self.assertEqual(first, s.rules[0].weeks == frozenset({1}), s.id)

    def test_most_segments_find_their_cross_streets(self):
        with_both = sum(" - " in s.limits for s in SIDES.values())
        self.assertGreater(with_both / len(SIDES), 0.7)


if __name__ == "__main__":
    unittest.main()
