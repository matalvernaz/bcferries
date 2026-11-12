"""Regression tests for a slow source and for a stored period that has ended.

On 2026-11-12, the day after Remembrance Day and the first day of a new
season, bcferries.com took 42 seconds to start sending the TSA-SWB page and 83
for SWB-TSA. Every fetch gave up at 30 seconds. TSA-SWB was left holding a
one-day holiday timetable (11 November only) that ended at midnight, the warmer
counted that copy as stored and went back to its six-hour cycle, and
/health/schedules answered 503 for the rest of the day.
"""

import os
import sys
import threading
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ROUTE = "bow-hsb"
OTHER_ROUTE = "hsb-bow"
WALL_URL = "https://bcferries.queue-it.net/?c=bcferries&e=roomschedules"
GOOD_URL = "https://www.bcferries.com/routes-fares/schedules/seasonal/BOW-HSB"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fresh scraper + store bound to a throwaway cache dir."""
    monkeypatch.setenv("BCF_CACHE_DIR", str(tmp_path / "schedules"))
    for mod in ("store", "scraper"):
        sys.modules.pop(mod, None)
    import scraper
    import store
    return scraper, store


def day_offset(store, days):
    """Today's date on the coast, moved by a number of days."""
    return datetime.now(store.PACIFIC).date() + timedelta(days=days)


def plant(store, route, period_end, age_seconds):
    """Store a one-day schedule for ``route`` as if it was fetched a while ago.

    One day is the shape that caused the outage: the page served on a holiday
    carries a period that starts and ends on that holiday.
    """
    day = {"year": period_end.year, "month": period_end.month, "day": period_end.day}
    schedule = {
        "dateRange": {"from": dict(day), "to": dict(day)},
        "sailings": [[], [{"scheduledDeparture": {"hour": 7, "minute": 0}}], [], [], [], [], [], []],
        "terminalCameras": None,
    }
    store.save(route, schedule)
    with store._lock:
        fetched_at, stored = store._memory[route]
        store._memory[route] = (fetched_at - age_seconds, stored)
    return schedule


def count_fetches(scraper, monkeypatch, body, final_url):
    """Point the scraper's fetch at a canned response and count the calls."""
    calls = []

    def counting_fetch(url, **options):
        calls.append(options)
        return body, final_url

    monkeypatch.setattr(scraper, "_fetch_with_curl", counting_fetch)
    return calls


def test_an_ended_period_is_checked_again_within_minutes_not_hours(env, monkeypatch):
    scraper, store = env
    plant(store, ROUTE, day_offset(store, -1), age_seconds=20 * 60)
    calls = count_fetches(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)

    scraper.get_seasonal_schedule(ROUTE)

    assert len(calls) == 1, (
        "a copy whose period ended yesterday was served as fresh because it "
        "is under four hours old"
    )


def test_an_ended_period_is_not_refetched_on_every_request(env, monkeypatch):
    """The source may still be publishing the ended period; don't hammer it."""
    scraper, store = env
    plant(store, ROUTE, day_offset(store, -1), age_seconds=60)
    calls = count_fetches(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)

    for _ in range(5):
        scraper.get_seasonal_schedule(ROUTE)

    assert len(calls) == 0, f"an ended period fetched a minute ago was refetched {len(calls)} times"


def test_a_current_period_still_waits_the_full_cache_time(env, monkeypatch):
    scraper, store = env
    plant(store, ROUTE, day_offset(store, 30), age_seconds=20 * 60)
    calls = count_fetches(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)

    scraper.get_seasonal_schedule(ROUTE)

    assert len(calls) == 0, "a current schedule twenty minutes old was refetched"


def test_the_warmer_gives_a_slow_source_longer_than_a_page_request_does(env, monkeypatch):
    scraper, _ = env
    timeouts = []

    def fake_curl(url, jar, timeout=None):
        timeouts.append(timeout)
        return fixture("seasonal_bow_hsb.html"), GOOD_URL

    monkeypatch.setattr(scraper, "_curl_once", fake_curl)

    scraper.get_seasonal_schedule(ROUTE)
    scraper.get_seasonal_schedule(OTHER_ROUTE, background=True)

    request_timeout, warmer_timeout = timeouts
    assert request_timeout is not None and request_timeout <= 30, (
        f"a person waiting on a page was made to wait {request_timeout} seconds"
    )
    assert warmer_timeout >= 120, (
        f"the warmer gave up after {warmer_timeout} seconds; the source took 83 on 2026-11-12"
    )


def test_one_fetch_shares_a_single_time_limit_across_all_its_curls(env, monkeypatch):
    """Four slow curls in a row must not hold the fetch lock for four limits."""
    scraper, _ = env
    clock = [1000.0]
    given = []

    def slow_room(url, jar, timeout=None):
        given.append(timeout)
        clock[0] += 100
        return fixture("queueit_waiting_room.html"), WALL_URL

    monkeypatch.setattr(scraper, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(scraper, "_curl_once", slow_room)

    scraper._fetch_with_curl(GOOD_URL, timeout=180)

    assert given == [180, 80, 1, 1], (
        f"curls were given {given} seconds; a 180-second fetch held the lock for {clock[0] - 1000:.0f}"
    )


def test_the_warmer_tries_again_after_a_page_request_gave_up(env, monkeypatch):
    """A person's 30-second attempt failing must not lock the warmer out for 15 minutes."""
    scraper, store = env
    plant(store, ROUTE, day_offset(store, -1), age_seconds=20 * 60)
    store.note_failure(ROUTE, "empty response")
    calls = count_fetches(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)

    scraper.get_seasonal_schedule(ROUTE, background=True)

    assert len(calls) == 1, "the warmer skipped a route because a page request had just failed on it"


def test_the_warmer_counts_an_ended_period_as_missing(env, monkeypatch):
    scraper, store = env
    plant(store, ROUTE, day_offset(store, -1), age_seconds=60)
    plant(store, OTHER_ROUTE, day_offset(store, 30), age_seconds=60)
    count_fetches(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)
    monkeypatch.setattr(scraper, "warmable_routes", lambda: [ROUTE, OTHER_ROUTE])
    monkeypatch.setattr(scraper, "WARM_GAP_SEC", 0)

    current, total = scraper.warm_schedules()

    assert (current, total) == (1, 2), (
        f"{current} of {total} counted as covered while one route's period had ended"
    )
    assert scraper._next_warm_delay(current, total) == scraper.WARM_RETRY_INTERVAL_SEC, (
        "the warmer went back to its long cycle with an ended period still in the store"
    )


def test_the_warmer_takes_its_long_cycle_once_every_period_is_current(env):
    scraper, _ = env

    assert scraper._next_warm_delay(64, 64) == scraper.WARM_INTERVAL_SEC


def test_a_page_request_with_a_stored_copy_does_not_wait_behind_a_slow_fetch(env, monkeypatch):
    """The warmer can now hold the shared fetch lock for minutes."""
    scraper, store = env
    planted = plant(store, ROUTE, day_offset(store, 30), age_seconds=5 * 3600)
    monkeypatch.setattr(scraper, "REQUEST_LOCK_WAIT_SEC", 0.05, raising=False)
    fetches = []

    def fake_curl(url, jar, timeout=None):
        fetches.append(url)
        return fixture("seasonal_bow_hsb.html"), GOOD_URL

    monkeypatch.setattr(scraper, "_curl_once", fake_curl)
    served = []

    def ask_for_the_page():
        served.append(scraper.get_seasonal_schedule(ROUTE))

    # Stand in for the warmer being part-way through a slow fetch.
    scraper._fetch_lock.acquire()
    person = threading.Thread(target=ask_for_the_page, daemon=True)
    try:
        person.start()
        person.join(timeout=2)
        still_waiting = person.is_alive()
        fetches_while_locked = list(fetches)
    finally:
        scraper._fetch_lock.release()
        person.join(timeout=5)

    assert not still_waiting, "a page request sat behind another fetch although a stored copy was available"
    assert fetches_while_locked == []
    assert served and served[0]["dateRange"] == planted["dateRange"]
    assert store.last_failure(ROUTE) is None, "the lock being busy was recorded as the source failing"


def test_a_first_ever_request_still_waits_for_the_lock(env, monkeypatch):
    """With nothing stored there is nothing to serve, so waiting is right."""
    scraper, store = env
    monkeypatch.setattr(scraper, "REQUEST_LOCK_WAIT_SEC", 0.05, raising=False)

    def fake_curl(url, jar, timeout=None):
        return fixture("seasonal_bow_hsb.html"), GOOD_URL

    monkeypatch.setattr(scraper, "_curl_once", fake_curl)
    served = []

    def ask_for_the_page():
        served.append(scraper.get_seasonal_schedule(ROUTE))

    scraper._fetch_lock.acquire()
    person = threading.Thread(target=ask_for_the_page, daemon=True)
    try:
        person.start()
        person.join(timeout=0.5)
        waited = person.is_alive()
    finally:
        scraper._fetch_lock.release()
        person.join(timeout=5)

    assert waited, "a request with no stored copy gave up on the lock and served an empty schedule"
    assert served and served[0].get("dateRange"), "the request did not fetch once the lock was free"
