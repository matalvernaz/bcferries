"""Regression tests for the Queue-it wall and the schedule cache.

Fixtures are real captures, not hand-written HTML:

- ``queueit_waiting_room.html`` is the exact 2325-byte body bcferries.com
  served for ``/routes-fares/schedules/seasonal/BOW-HSB`` on 2026-09-06, when
  the schedules section was behind a Queue-it virtual waiting room. It answers
  HTTP 200, which is what made the failure silent.
- ``seasonal_bow_hsb.html`` is the two elements the parser reads, trimmed from
  a real capture of the same page taken while the wall was down. BOW-HSB is one
  of the 23 directions the current-conditions API has no data for, so it is the
  case that only the seasonal source can answer.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ROUTE = "bow-hsb"
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


def serve(scraper, monkeypatch, body, final_url):
    """Point the scraper's fetch at a canned response."""
    monkeypatch.setattr(scraper, "_fetch_with_curl", lambda url: (body, final_url))


def test_wall_response_is_not_treated_as_data(env, monkeypatch):
    scraper, _ = env
    serve(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)
    result = scraper.get_seasonal_schedule(ROUTE)
    assert result["dateRange"] is None
    assert sum(len(g) for g in result["sailings"]) == 0


def test_wall_response_does_not_evict_a_good_schedule(env, monkeypatch):
    scraper, store = env
    serve(scraper, monkeypatch, fixture("seasonal_bow_hsb.html"), GOOD_URL)
    good = scraper.get_seasonal_schedule(ROUTE)
    assert sum(len(g) for g in good["sailings"]) > 0

    # Expire the TTL so the next call refetches, then put the wall up.
    store._memory[ROUTE] = (time.time() - scraper.CACHE_TTL - 1, store._memory[ROUTE][1])
    serve(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)
    after = scraper.get_seasonal_schedule(ROUTE)

    assert sum(len(g) for g in after["sailings"]) > 0, "wall evicted the good schedule"
    assert after["dateRange"] == good["dateRange"]


def test_good_schedule_survives_a_restart(env, monkeypatch):
    scraper, store = env
    serve(scraper, monkeypatch, fixture("seasonal_bow_hsb.html"), GOOD_URL)
    good = scraper.get_seasonal_schedule(ROUTE)

    # Simulate a container recreate: process memory gone, disk intact.
    store._memory.clear()
    serve(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)
    after = scraper.get_seasonal_schedule(ROUTE)

    assert after["dateRange"] == good["dateRange"]
    assert [len(g) for g in after["sailings"]] == [len(g) for g in good["sailings"]]


def test_offdomain_redirect_is_detected_even_with_a_plausible_body(env, monkeypatch):
    """The wall's body carries no queue-it string, so the final URL is the signal."""
    scraper, _ = env
    body = fixture("queueit_waiting_room.html")
    assert "queue-it" not in body
    assert scraper._detect_interception(body, WALL_URL, None) is not None


def test_weekly_schedule_is_populated_for_a_cc_less_direction(env, monkeypatch):
    """bow-hsb has no current-conditions feed; the weekly grid must still fill."""
    scraper, _ = env
    serve(scraper, monkeypatch, fixture("seasonal_bow_hsb.html"), GOOD_URL)
    monkeypatch.setattr(scraper, "get_current_conditions", lambda route: None)

    result = scraper.get_schedule(ROUTE)
    populated = [i for i, g in enumerate(result["sailings"]) if g]

    assert populated == [1, 2, 3, 4, 5, 6, 7], f"expected all 7 weekdays, got {populated}"


def test_expired_schedule_is_served_with_a_warning(env, monkeypatch):
    """The fixture's period ended in 2025, so every date is now out of range."""
    scraper, _ = env
    serve(scraper, monkeypatch, fixture("seasonal_bow_hsb.html"), GOOD_URL)
    monkeypatch.setattr(scraper, "get_current_conditions", lambda route: None)

    result = scraper.get_schedule(ROUTE)

    assert sum(len(g) for g in result["sailings"]) > 0
    assert result.get("scheduleWarning"), "expired schedule served with no warning"


def test_realtime_data_still_overlays_todays_column(env, monkeypatch):
    scraper, _ = env
    serve(scraper, monkeypatch, fixture("seasonal_bow_hsb.html"), GOOD_URL)
    live = [{"scheduledDeparture": {"hour": 9, "minute": 5}, "realtime": True}]
    monkeypatch.setattr(scraper, "get_current_conditions", lambda route: {"stub": True})
    monkeypatch.setattr(scraper, "parse_cc_today", lambda route, data: (live, None))

    result = scraper.get_schedule(ROUTE)
    today = scraper.datetime.now(scraper.PACIFIC).isoweekday()

    assert result["sailings"][today] == live
    others = [i for i, g in enumerate(result["sailings"]) if g and i != today]
    assert others, "overlaying today must not wipe the rest of the week"


def test_repeated_walled_requests_do_not_refetch_every_time(env, monkeypatch):
    scraper, _ = env
    calls = []

    def counting_fetch(url):
        calls.append(url)
        return fixture("queueit_waiting_room.html"), WALL_URL

    monkeypatch.setattr(scraper, "_fetch_with_curl", counting_fetch)
    for _ in range(5):
        scraper.get_seasonal_schedule(ROUTE)

    assert len(calls) == 1, f"walled source hit {len(calls)} times for 5 requests"


def test_health_reports_degradation(env, monkeypatch):
    scraper, store = env
    serve(scraper, monkeypatch, fixture("queueit_waiting_room.html"), WALL_URL)
    scraper.get_seasonal_schedule(ROUTE)

    report = store.health()

    assert report["healthy"] is False
    assert ROUTE in report["routesDegraded"]
    assert report["routes"][ROUTE]["lastFailureCause"]


def test_health_flags_a_freshly_fetched_but_expired_period(env, monkeypatch):
    """The fixture's period ended in 2025; a recent fetch must not read healthy."""
    scraper, store = env
    serve(scraper, monkeypatch, fixture("seasonal_bow_hsb.html"), GOOD_URL)
    scraper.get_seasonal_schedule(ROUTE)

    report = store.health()

    assert report["routes"][ROUTE]["ageSeconds"] < 60
    assert report["routes"][ROUTE]["periodExpired"] is True
    assert ROUTE in report["routesDegraded"]
    assert report["healthy"] is False
