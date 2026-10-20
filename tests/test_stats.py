import unittest
from datetime import date
from unittest import mock

from parkcal import stats


class Line(unittest.TestCase):
    def test_quiet_day_prints_nothing(self):
        self.assertEqual(stats.line({"days": 1, "visits": 0, "referrers": [], "events": {}}), "")

    def test_busy_day(self):
        s = {"days": 1, "visits": 14, "referrers": [("linkedin.com", 9), ("x.com", 3)],
             "events": {"subscribe": 2, "google-calendar": 1, "copy-calendar-url": 1,
                        "request-city": 1, "search-outside": 2, "search-covered": 5}}
        self.assertEqual(stats.line(s),
                         "🧹 Sweep Calendar (yesterday): 14 visits (linkedin.com 9, x.com 3) · 4 calendar adds"
                         " · 1 city request · 2 searches outside coverage · https://sweepcalendar.goatcounter.com")

    def test_weekly_label(self):
        self.assertIn("(last 7 days)", stats.line({"days": 7, "visits": 1, "referrers": [], "events": {}}))


class Collect(unittest.TestCase):
    def test_splits_pages_from_events_and_uses_a_pacific_day(self):
        calls = []

        def fake_api(path, **params):
            calls.append((path, params))
            if path == "stats/hits":
                return {"hits": [{"path": "/index.html", "count": 5, "event": False},
                                 {"path": "subscribe/sf", "count": 2, "event": True},
                                 {"path": "subscribe/sj", "count": 1, "event": True}]}
            return {"stats": [{"name": "linkedin.com", "count": 4}]}

        with mock.patch.object(stats, "api", fake_api):
            s = stats.collect(1, date(2026, 10, 21))
        self.assertEqual((s["visits"], s["events"], s["referrers"]), (5, {"subscribe": 3}, [("linkedin.com", 4)]))
        self.assertEqual(calls[0][1]["start"], "2026-10-20T00:00:00-07:00")
        self.assertEqual(calls[0][1]["end"], "2026-10-20T23:59:59-07:00")

    def test_missing_token_fails_loudly(self):
        with mock.patch.object(stats, "TOKEN_FILE", stats.Path("/nonexistent/token")):
            with self.assertRaises(stats.StatsError) as e:
                stats.token()
        self.assertIn("can't read GoatCounter token", str(e.exception))


if __name__ == "__main__":
    unittest.main()
