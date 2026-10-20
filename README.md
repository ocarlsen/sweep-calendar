# Sweep Calendar

Street sweeping no-parking windows as a calendar your phone subscribes to.
Pick your side of the block once; every sweep shows up in Calendar, holidays
handled, and the feed keeps itself current.

**Live:** https://parking.ocarlsen.com (San Francisco first; more cities planned)

## Why

Cities publish sweeping schedules as open data, and apps exist that push
reminders. What did not exist for San Francisco was the simplest version: a
plain `webcal://` feed for *your* curb that lives in the calendar you already
look at, with no app, account, or location tracking.

## How it works

```
city open data ──► city adapter ──► Rule model ──► RRULE feeds (.ics)
   (DataSF)          parkcal/cities/   parkcal/model    parkcal/ics
                                           │
                                           └──► map tiles (.json) ──► static site (web/)
```

- **One model, many cities.** A city adapter turns its data into `BlockSide`s
  with `Rule`s (weekday, which weeks of the month, hours, whether it runs on
  holidays) plus a holiday calendar. Everything downstream is city-agnostic.
  Adding a city is one module in `parkcal/cities/`.
- **Feeds are recurrence rules, not event lists.** Each rule becomes one
  `RRULE` (`FREQ=MONTHLY;BYDAY=1TU,3TU`), suspended holidays become `EXDATE`s,
  holiday-only sweeps become `RDATE`s, all in local time with a `VTIMEZONE` so
  daylight-saving changes don't shift the hours. 22,573 SF feeds, ~1 KB each.
- **Fully static.** A weekly GitHub Action pulls the data, runs the tests,
  regenerates every feed and deploys to GitHub Pages. No server to run.
- **Map without a backend.** Block sides are bucketed into ~1 km tiles; the
  page loads only the tiles in view, draws each curb offset to its side of
  the street, and geocodes addresses with OpenStreetMap Nominatim.

## Correctness

Getting this wrong costs someone a ticket, so the tests aim at the places it
would go wrong:

- **Independent cross-check.** Every generated feed is parsed and expanded by
  a separate iCalendar implementation (`icalendar` + `recurring-ical-events`)
  and must produce exactly the windows our own expander computes. Run once
  over all 22,573 SF block sides for a full year; the CI suite runs it on a
  202-side real-data sample. It caught a real bug: an RRULE mixing
  `BYDAY=FR` with `BYDAY=2WE` that the library (and likely some calendar apps)
  expands differently, so each rule is now its own event.
- **Holiday rules** reproduce SFMTA's published 2026 enforcement table:
  New Year's, Thanksgiving and Christmas suspend all sweeping; other holidays
  suspend daytime sweeping but keep night sweeping.
- **Edge cases:** 5th-weekday sweeps, DST transitions, split schedules
  ("2nd & 4th Tue" + "1st, 3rd, 5th Tue" = every Tuesday), and a geometry
  fallback for rows missing their cardinal side, validated against rows that
  have one.

Posted signs always control; the site says so.

## Run it

```sh
python3 -m parkcal.build --cache .cache --out site   # stdlib only, Python 3.9+
python3 -m http.server 8765 --directory site

python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Adding a city

Write `parkcal/cities/<slug>.py` exposing `CITY = City(...)` with a `load()`
returning `BlockSide`s and a `holidays(year)` returning the city's suspension
days, then register it in `parkcal/cities/__init__.py`. Candidates with
usable open data: San Jose, Chicago, Los Angeles.

## Data

San Francisco Public Works street sweeping schedule,
[DataSF yhqp-riqs](https://data.sf.gov/City-Infrastructure/Street-Sweeping-Schedule/yhqp-riqs).
Holiday policy from the
[SFMTA holiday enforcement schedule](https://www.sfmta.com/getting-around/drive-park/holiday-enforcement-schedule).
Map data © OpenStreetMap contributors.
