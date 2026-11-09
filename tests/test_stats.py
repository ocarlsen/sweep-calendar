import io
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

    def test_referrers_failure_keeps_the_counts(self):
        def fake_api(path, **params):
            if path == "stats/toprefs":
                raise stats.StatsError("stats: GoatCounter API stats/toprefs returned HTTP 404")
            return {"hits": [{"path": "/index.html", "count": 5, "event": False},
                             {"path": "subscribe/sf", "count": 1, "event": True}]}

        with mock.patch.object(stats, "api", fake_api):
            s = stats.collect(1, date(2026, 11, 2))
        self.assertEqual((s["visits"], s["events"], s["referrers"]), (5, {"subscribe": 1}, []))
        self.assertEqual(stats.line(s),
                         "🧹 Sweep Calendar (yesterday): 5 visits (referrers unavailable: GoatCounter API"
                         " stats/toprefs returned HTTP 404) · 1 calendar add · https://sweepcalendar.goatcounter.com")

    def test_hits_failure_still_fails_loudly(self):
        def fake_api(path, **params):
            raise stats.StatsError("stats: GoatCounter API stats/hits returned HTTP 500")

        with mock.patch.object(stats, "api", fake_api):
            with self.assertRaises(stats.StatsError):
                stats.collect(1, date(2026, 11, 2))

    def test_missing_token_fails_loudly(self):
        with mock.patch.object(stats, "TOKEN_FILE", stats.Path("/nonexistent/token")):
            with self.assertRaises(stats.StatsError) as e:
                stats.token()
        self.assertIn("can't read GoatCounter token", str(e.exception))



class Api(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(stats, "token", return_value="t")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_once_on_404_then_succeeds(self):
        err = stats.urllib.error.HTTPError("url", 404, "msg", hdrs=None, fp=None)
        ok = io.BytesIO(b'{"hits": []}')
        with mock.patch.object(stats.urllib.request, "urlopen", side_effect=[err, ok]) as m_open, \
             mock.patch("time.sleep") as m_sleep:
            result = stats.api("stats/hits")
        self.assertEqual(result, {"hits": []})
        self.assertEqual(m_open.call_count, 2)
        m_sleep.assert_called_once_with(2)

    def test_exhausts_retries_on_persistent_404(self):
        err = stats.urllib.error.HTTPError("url", 404, "msg", hdrs=None, fp=None)
        with mock.patch.object(stats.urllib.request, "urlopen", side_effect=[err, err, err]) as m_open, \
             mock.patch("time.sleep"):
            with self.assertRaises(stats.StatsError) as cm:
                stats.api("stats/hits")
        self.assertEqual(str(cm.exception), "stats: GoatCounter API stats/hits returned HTTP 404")
        self.assertEqual(m_open.call_count, 3)

    def test_exhausts_retries_on_persistent_urlerror(self):
        err = stats.urllib.error.URLError("boom")
        with mock.patch.object(stats.urllib.request, "urlopen", side_effect=[err, err, err]) as m_open, \
             mock.patch("time.sleep"):
            with self.assertRaises(stats.StatsError) as cm:
                stats.api("stats/hits")
        self.assertTrue(str(cm.exception).startswith("stats: GoatCounter API stats/hits unreachable:"))
        self.assertEqual(m_open.call_count, 3)

    def test_retries_429_5xx_and_timeouts_and_reports_the_last_error(self):
        http = lambda code: stats.urllib.error.HTTPError("url", code, "msg", hdrs=None, fp=None)
        with mock.patch.object(stats.urllib.request, "urlopen",
                               side_effect=[http(429), TimeoutError("slow"), http(500)]) as m_open, \
             mock.patch("time.sleep") as m_sleep:
            with self.assertRaises(stats.StatsError) as cm:
                stats.api("stats/hits")
        self.assertEqual(str(cm.exception), "stats: GoatCounter API stats/hits returned HTTP 500")
        self.assertEqual(m_open.call_count, 3)
        self.assertEqual([c.args for c in m_sleep.call_args_list], [(2,), (5,)])

    def test_retries_500_then_succeeds(self):
        err = stats.urllib.error.HTTPError("url", 500, "msg", hdrs=None, fp=None)
        with mock.patch.object(stats.urllib.request, "urlopen",
                               side_effect=[err, io.BytesIO(b'{"stats": []}')]), \
             mock.patch("time.sleep"):
            self.assertEqual(stats.api("stats/toprefs"), {"stats": []})

    def test_non_retryable_401_raises_immediately(self):
        err = stats.urllib.error.HTTPError("url", 401, "msg", hdrs=None, fp=None)
        with mock.patch.object(stats.urllib.request, "urlopen", side_effect=[err]) as m_open, \
             mock.patch("time.sleep") as m_sleep:
            with self.assertRaises(stats.StatsError):
                stats.api("stats/hits")
        self.assertEqual(m_open.call_count, 1)
        m_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
