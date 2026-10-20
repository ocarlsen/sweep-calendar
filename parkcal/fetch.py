"""HTTP GET with retries, for the city data sources and canaries.

City GIS servers time out now and then (San Jose's did on 2026-10-20 and
failed a deploy). One slow response shouldn't block a build, but a source
that stays down should still fail it, so: a few tries with backoff, then
raise the last error.
"""
import time
import urllib.error
import urllib.request

ATTEMPTS = 4
BACKOFF_S = (5, 15, 45)  # waits between attempts
USER_AGENT = "sweep-calendar (parking.ocarlsen.com)"


def get(url: str, timeout: float = 120, sleep=time.sleep) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            # A 4xx won't get better by waiting; anything else might.
            if isinstance(e, urllib.error.HTTPError) and e.code < 500:
                raise
            if attempt == ATTEMPTS - 1:
                raise
            print(f"fetch: {type(e).__name__} on attempt {attempt + 1}/{ATTEMPTS} for {url[:80]}; "
                  f"retrying in {BACKOFF_S[attempt]}s")
            sleep(BACKOFF_S[attempt])
    raise AssertionError("unreachable")
