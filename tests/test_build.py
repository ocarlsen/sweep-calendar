import tempfile
import unittest
from unittest import mock
from datetime import datetime, time, timezone
from pathlib import Path

from parkcal import build
from parkcal.build import check_sanity, describe, schedule_lines, schedule_sections
from parkcal.model import BlockSide, City, DataError, Rule


def fri(*weeks):
    return describe(Rule(4, frozenset(weeks), time(6), time(8)))


class Describe(unittest.TestCase):
    def test_labels_read_like_signs(self):
        self.assertEqual(fri(1, 2, 3, 4, 5), "Every Fri 6am–8am")
        self.assertEqual(fri(1, 3), "1st & 3rd Fri 6am–8am")
        self.assertEqual(fri(1, 3, 5), "1st, 3rd & 5th Fri 6am–8am")
        # A real 1st-4th schedule (SF's split rows become every week in sf.py).
        self.assertEqual(fri(1, 2, 3, 4), "Every Fri except the 5th 6am–8am")


class ScheduleLines(unittest.TestCase):
    def rule(self, day, weeks=(1, 2, 3, 4, 5), start=3, end=6, advisory=False):
        return Rule(day, frozenset(weeks), time(start), time(end), advisory=advisory)

    def test_every_week_rules_with_shared_hours_collapse(self):
        self.assertEqual(schedule_lines([self.rule(d) for d in range(7)]), ["Every day 3am–6am"])
        self.assertEqual(schedule_lines([self.rule(3), self.rule(0)]), ["Every Mon & Thu 3am–6am"])

    def test_specific_weeks_and_different_hours_stay_separate(self):
        self.assertEqual(schedule_lines([self.rule(0, (1, 3)), self.rule(3, (1, 3))]),
                         ["1st & 3rd Mon 3am–6am", "1st & 3rd Thu 3am–6am"])
        self.assertEqual(schedule_lines([self.rule(1, start=6, end=8), self.rule(3)]),
                         ["Every Thu 3am–6am", "Every Tue 6am–8am"])
        self.assertEqual(schedule_lines([self.rule(1, advisory=True), self.rule(3)]),
                         ["Every Tue 3am–6am (not ticketed)", "Every Thu 3am–6am"])


class Sections(unittest.TestCase):
    def test_sweep_inside_a_posted_window_folds_into_it(self):
        # Almaden Ave West: posted every day 3-6am, swept Mon & Thu 3-6am, plus a monthly daytime sweep.
        posted = [Rule(d, frozenset({1, 2, 3, 4, 5}), time(3), time(6)) for d in range(7)]
        swept = [Rule(d, frozenset({1, 2, 3, 4, 5}), time(3), time(6), advisory=True) for d in (0, 3)]
        monthly = Rule(1, frozenset({3}), time(7), time(17), advisory=True)
        self.assertEqual(schedule_sections(posted + swept + [monthly]),
                         {"noParking": ["Every day 3am–6am · sweeper comes Mon & Thu"],
                          "sweeping": ["3rd Tue 7am–5pm"]})

    def test_no_note_when_sign_and_sweep_agree(self):
        posted = Rule(2, frozenset({1, 2, 3, 4, 5}), time(1), time(6))
        swept = Rule(2, frozenset({1, 2, 3, 4, 5}), time(1), time(6), advisory=True)
        self.assertEqual(schedule_sections([posted, swept]), {"noParking": ["Every Wed 1am–6am"], "sweeping": []})

    def test_nothing_to_fold(self):
        r = Rule(1, frozenset({3}), time(7), time(17), advisory=True)
        self.assertEqual(schedule_sections([r]), {"noParking": [], "sweeping": ["3rd Tue 7am–5pm"]})


def tiny_city(rules):
    side = BlockSide("b1", "Test St", "A - B", "Both sides", [(-122.0, 37.0), (-122.0, 37.001)], rules=rules)
    return City("t", "Test", "America/Los_Angeles", "https://example.com", lambda y: {}, lambda cache: [side])


class Today(unittest.TestCase):
    def test_today_is_the_cities_date_not_the_hosts(self):
        # 03:00 UTC on the 12th is still the evening of the 11th in Los Angeles.
        now = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)
        rule = Rule(0, frozenset({1, 2, 3, 4, 5}), time(8), time(10))
        with tempfile.TemporaryDirectory() as d:
            meta = build.build_city(tiny_city([rule]), Path(d), 30, now=now)
        self.assertEqual(meta["from"], "2026-11-11")


class Sanity(unittest.TestCase):
    def test_a_weekday_rule_with_no_weeks_is_refused(self):
        ok = Rule(0, frozenset({1}), time(8), time(10))
        check_sanity(tiny_city([ok]), [tiny_city([ok]).load(None)[0]])
        bad = Rule(0, frozenset(), time(8), time(10))
        city = tiny_city([bad])
        with self.assertRaises(DataError) as e:
            check_sanity(city, city.load(None))
        self.assertIn("no weeks", str(e.exception))


class Cli(unittest.TestCase):
    def test_an_unknown_city_is_an_argparse_error(self):
        with self.assertRaises(SystemExit) as e:
            build.main(["nowhere"])
        self.assertEqual(e.exception.code, 2)

    def test_no_cities_means_all_of_them(self):
        # The deploy runs the build with no city arguments.
        class Started(Exception):
            pass

        def first_city(city, *args, **kwargs):
            raise Started(city.slug)

        with mock.patch.object(build, "build_city", first_city):
            with self.assertRaises(Started) as e:
                build.main(["--out", "unused"])
        self.assertEqual(str(e.exception), sorted(build.CITIES)[0])

if __name__ == "__main__":
    unittest.main()
