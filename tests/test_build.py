import unittest
from datetime import time

from parkcal.build import describe
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


if __name__ == "__main__":
    unittest.main()
