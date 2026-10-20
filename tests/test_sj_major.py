import json
import unittest
from datetime import date, time
from pathlib import Path

from parkcal import geometry
from parkcal.cities import sj, sj_major as M
from parkcal.expand import feed_rules, windows

from .test_sf import ROWS as SF_ROWS

FIXTURE = Path(__file__).parent / "fixtures" / "sj_major_sample.json"
MAJOR = json.loads(FIXTURE.read_text())
SIDES = {s.id: s for s in sj.to_blocksides([], MAJOR)}
ALL = frozenset({1, 2, 3, 4, 5})


class Signs(unittest.TestCase):
    def test_sweeping_signs_parse(self):
        self.assertEqual(M.parse_sign("No Parking 1st and 3rd Thursday 6am-8am"),
                         [(3, frozenset({1, 3}), time(6), time(8))])
        self.assertEqual(M.parse_sign("Mondays and Thursdays 3am-6am"),
                         [(0, ALL, time(3), time(6)), (3, ALL, time(3), time(6))])
        self.assertEqual(len(M.parse_sign("No Parking Anyday from 3am-6am")), 7)
        self.assertEqual(M.parse_sign("No Parking Tuesday 1am to 6am"), [(1, ALL, time(1), time(6))])
        self.assertEqual(M.parse_sign("No Parking Wednesday 8am-12pm"), [(2, ALL, time(8), time(12))])

    def test_non_sweeping_signs_are_skipped(self):
        for clause in ["No Stopping Anytime", "2 Hour Parking", "30min Max Parking Anytime",
                       "Loading Zone Only", "No Parking Anytime", "Trolley Only",
                       "No Parking 8am-6pm except Sundays, Holidays, or Horace Mann Permit",
                       "No Parking 6am-10pm Sat-Sun, and 10pm-6am Any Day"]:
            self.assertIsNone(M.parse_sign(clause), clause)

    def test_overnight_sign_runs_into_the_next_morning(self):
        (w,) = M.parse_sign("No Parking 2nd and 4th Monday 6pm-8am")
        self.assertEqual(w, (0, frozenset({2, 4}), time(18), time(8)))

    def test_sides_split(self):
        text = "West Side: No Stopping Anytime, East Side: No Parking Mondays and Thursdays 3am-6am"
        self.assertEqual(set(M.posted(text)), {"East"})
        self.assertEqual(M.split_sides("Both Sides: No Parking Tuesday 1am-6am"),
                         {"Both": "No Parking Tuesday 1am-6am"})
        self.assertEqual(M.split_sides("West Side: X (Diagonal Spaces Parking Allowed)"), {"West": "X"})

    def test_unsigned_schedule(self):
        got = M.scheduled("West Side: Every Tuesday, East Side: Every Wednesday", "1am - 6am")
        self.assertEqual(got, {"West": [(1, ALL, time(1), time(6))], "East": [(2, ALL, time(1), time(6))]})
        self.assertEqual(set(M.scheduled("South Side Swept Only: Every Wednesday", "12am - 8am")), {"South"})


class Curbs(unittest.TestCase):
    def test_curb_for_agrees_with_sfs_published_sides(self):
        # SF publishes both the cardinal side and L/R for each row: an answer key.
        rows = [r for r in SF_ROWS if r.get("blockside") in ("North", "South", "East", "West") and "line" in r]
        right = sum(geometry.curb_for(r["line"]["coordinates"], r["blockside"]) == r["cnnrightleft"] for r in rows)
        self.assertGreater(right / len(rows), 0.95, (right, len(rows)))


class BothLayers(unittest.TestCase):
    def feature(self, layer_fields, side, line):
        base = {"CENTERLINEID": 9581, "ROADNAME": "Almaden Ave", "ROADFROM": "BALBACH ST",
                "ROADTO": "VIOLA AVE", "SIDEOFROAD": side}
        return {"geometry": {"type": "LineString", "coordinates": line}, "properties": {**base, **layer_fields}}

    def test_both_sides_merges_into_the_named_curbs(self):
        # The real case: residential "Both" (3rd Tue 7am-5pm) + major-road West sign (every day 3am-6am).
        line = [[-121.8885, 37.3285], [-121.8880, 37.3277]]
        res = self.feature({"ROUTETYPE": "Non-Signed Route", "APPWEEKDAYCODE": "Tuesday,3",
                            "APPSCHEDULEDTIME": "7am - 5pm"}, "Both", line)
        maj = self.feature({"ROUTETYPE": "Signed Route",
                            "APPPUBLICPOSTEDTIMES": "West Side: No Parking 3am-6am Any Day"}, "Both", line)
        sides = {s.id: s for s in sj.to_blocksides([res], [maj])}
        self.assertEqual(set(sides), {"9581W", "9581E"})
        west, east = sides["9581W"], sides["9581E"]
        self.assertEqual(len(west.rules), 8)  # 7 nightly + the monthly daytime sweep
        self.assertEqual([(r.weekday, sorted(r.weeks)) for r in east.rules], [(1, [3])])
        self.assertEqual({west.curb, east.curb}, {"L", "R"})


class Build(unittest.TestCase):
    def test_signed_sides_are_ticketed_and_offset(self):
        signed = [s for s in SIDES.values() if not all(r.advisory for r in s.rules)]
        self.assertTrue(signed)
        self.assertTrue(any(s.curb in ("L", "R") for s in signed))

    def test_every_fixture_side_has_rules_or_a_posted_note(self):
        self.assertTrue(all(s.rules or s.notes for s in SIDES.values()))
        self.assertTrue(any(s.notes and not s.rules for s in SIDES.values()))  # e.g. No Stopping curbs
        self.assertFalse(any(s.id.startswith("None") for s in SIDES.values()))

    def test_expands(self):
        with_rules = [s for s in SIDES.values() if s.rules][:20]
        for s in with_rules:
            self.assertTrue(windows(s, sj.CITY, date(2026, 11, 1), date(2026, 11, 30)), s.id)


class SignVsSchedule(unittest.TestCase):
    line = [[-121.8885, 37.3285], [-121.8880, 37.3277]]

    def row(self, posted, side="Both", weekday="Mondays and Thursdays", hours="1am - 6am",
            primary="Yes", cid=1):
        return {"geometry": {"type": "LineString", "coordinates": self.line},
                "properties": {"CENTERLINEID": cid, "ROADNAME": "Test Ave", "ROADFROM": "A ST",
                               "ROADTO": "B ST", "SIDEOFROAD": side, "ROUTETYPE": "Signed Route",
                               "APPPUBLICPOSTEDTIMES": posted, "APPWEEKDAY": weekday,
                               "APPSCHEDULEDTIME": hours, "PRIMARYSEGMENT": primary}}

    def sides(self, *rows):
        return {s.side.split()[0]: s for s in sj.to_blocksides([], list(rows))}

    def test_broader_sign_shows_the_sweep_days_without_extra_events(self):
        # Almaden Ave West: sign every day 3-6am, sweeper Mon & Thu 3-6am.
        west = self.sides(self.row("West Side: No Parking 3am-6am Any Day", "West", hours="3am - 6am"))["West"]
        ticketed = [r for r in west.rules if not r.advisory]
        sweeps = [r for r in west.rules if r.advisory]
        self.assertEqual((len(ticketed), sorted(r.weekday for r in sweeps)), (7, [0, 3]))
        self.assertEqual(feed_rules(west.rules), ticketed)  # sweep days are inside the posted window

    def test_narrower_sign_drops_the_other_curbs_days(self):
        # Julian St: North sign Monday only; the street-level schedule says Mon & Thu.
        north = self.sides(self.row("North Side: No Parking Monday 1am-6am, South Side: No Parking Thursday 1am-6am"))["North"]
        self.assertEqual(sorted((r.weekday, r.advisory) for r in north.rules), [(0, False), (0, True)])

    def test_time_limit_curb_gets_the_sweep_as_not_ticketed(self):
        east = self.sides(self.row("East Side: 2 Hour Parking", "East"))["East"]
        self.assertTrue(east.rules and all(r.advisory for r in east.rules))
        self.assertEqual(east.notes, ["Posted: 2 Hour Parking"])

    def test_outright_ban_gets_a_note_and_no_events(self):
        east = self.sides(self.row("East Side: Loading Zone Only", "East"))["East"]
        self.assertEqual((east.rules, east.notes), ([], ["Posted: Loading Zone Only"]))

    def test_primary_row_wins_a_conflict(self):
        primary = self.row("West Side: No Stopping Anytime", "West", primary="Yes")
        other = self.row("", "West", primary="No")
        other["properties"]["ROUTETYPE"] = "Non-Signed Route"
        west = self.sides(other, primary)["West"]
        self.assertEqual((west.rules, west.notes), ([], ["Posted: No Stopping Anytime"]))


if __name__ == "__main__":
    unittest.main()
