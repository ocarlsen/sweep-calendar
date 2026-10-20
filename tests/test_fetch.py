import unittest
import urllib.error
from unittest import mock

from parkcal import fetch


class Ok:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b"data"


class Retries(unittest.TestCase):
    def test_retries_a_timeout_then_succeeds(self):
        calls, waits = [], []
        def urlopen(req, timeout):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.URLError(TimeoutError("timed out"))
            return Ok()
        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(fetch.get("https://x", sleep=waits.append), b"data")
        self.assertEqual((len(calls), waits), (3, [5, 15]))

    def test_gives_up_after_the_last_attempt(self):
        waits = []
        def urlopen(req, timeout):
            raise urllib.error.URLError(TimeoutError("timed out"))
        with mock.patch("urllib.request.urlopen", urlopen), self.assertRaises(urllib.error.URLError):
            fetch.get("https://x", sleep=waits.append)
        self.assertEqual(waits, [5, 15, 45])

    def test_client_errors_are_not_retried(self):
        waits = []
        def urlopen(req, timeout):
            raise urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", urlopen), self.assertRaises(urllib.error.HTTPError):
            fetch.get("https://x", sleep=waits.append)
        self.assertEqual(waits, [])


if __name__ == "__main__":
    unittest.main()
