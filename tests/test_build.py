import unittest
from datetime import time

from parkcal.build import describe, schedule_lines, schedule_sections
from parkcal.model import Rule


def fri(*weeks):
    return describe(Rule(4, frozenset(weeks), time(6), time(8)))


class Describe(unittest.TestCase):
    def test_labels_read_like_signs(self):
        self.assertEqual(fri(1, 2, 3, 4, 5), "Every Fri 6am–8am")
        self.assertEqual(fri(1, 3), "1st & 3rd Fri 6am–8am")
        self.assertEqual(fri(1, 3, 5), "1st, 3rd & 5th Fri 6am–8am")
        # SF lists "1st & 3rd" and "2nd & 4th" as separate rows; merged, that
        # is every Friday except a 5th one.
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


if __name__ == "__main__":
    unittest.main()
