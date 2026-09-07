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


def test_waiting_room_is_passed_by_following_its_js_redirect(env, monkeypatch):
    """curl cannot execute the room's JS navigation; taking that hop gets in."""
    scraper, _ = env
    calls = []

    def fake_curl(url, jar):
        calls.append(url)
        if "bcferries.com" in url and not calls[:-1]:
            return fixture("queueit_waiting_room.html"), WALL_URL
        if "queue-it.net" in url:
            return fixture("seasonal_bow_hsb.html"), GOOD_URL
        return fixture("queueit_waiting_room.html"), WALL_URL

    monkeypatch.setattr(scraper, "_curl_once", fake_curl)
    html, final = scraper._fetch_with_curl(
        "https://www.bcferries.com/routes-fares/schedules/seasonal/BOW-HSB")

    assert scraper._detect_interception(html, final, None) is None
    assert len(calls) == 2, f"expected the room hop to be taken, got {calls}"
    assert "queue-it.net" in calls[1]


def test_queue_hop_target_is_decoded_from_the_room_body(env):
    scraper, _ = env
    body = fixture("queueit_waiting_room.html")
    hop = scraper._queue_redirect_target(body, WALL_URL)

    assert hop is not None
    assert hop.startswith("https://bcferries.queue-it.net/")
    # The encoded target must come back decoded, not double-escaped.
    assert "e=roomschedules" in hop
    assert "%3D" not in hop


def test_stale_acceptance_cookie_is_discarded_and_retried(env, monkeypatch, tmp_path):
    """A jar that lands us back in the room must be thrown away, not reused."""
    scraper, _ = env
    jar = tmp_path / "queueit.jar"
    jar.write_text("stale")
    monkeypatch.setattr(scraper, "QUEUE_JAR", str(jar))
    seen = []

    def fake_curl(url, j):
        seen.append(url)
        # Fail the room hop on the first attempt, succeed on the second.
        if "queue-it.net" in url:
            if len([u for u in seen if "queue-it.net" in u]) == 1:
                return fixture("queueit_waiting_room.html"), WALL_URL
            return fixture("seasonal_bow_hsb.html"), GOOD_URL
        return fixture("queueit_waiting_room.html"), WALL_URL

    monkeypatch.setattr(scraper, "_curl_once", fake_curl)
    html, final = scraper._fetch_with_curl(
        "https://www.bcferries.com/routes-fares/schedules/seasonal/BOW-HSB")

    assert scraper._detect_interception(html, final, None) is None
    assert not jar.exists() or jar.read_text() != "stale"


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


def test_sgi_fallback_does_not_pollute_the_stored_schedule(env, monkeypatch):
    """The Gulf Islands fallback stamps a departure terminal onto each sailing.

    Doing that in place would edit the stored schedule, so every later reader of
    that island's timetable would see another island's terminal code.
    """
    scraper, store = env
    serve(scraper, monkeypatch, fixture("seasonal_bow_hsb.html"), GOOD_URL)
    monkeypatch.setattr(scraper, "get_current_conditions", lambda route: None)
    monkeypatch.setattr(scraper, "get_tomorrow_conditions", lambda route: None)

    island_route = f"{scraper.SGI_RETURN_TERMINALS[0]['from']}-tsa"
    scraper.get_upcoming_sailings("sgi-tsa", limit=7)

    stored = store.load(island_route)
    assert stored, "expected the island's seasonal schedule to be stored"
    # The parser emits "from": None on every sailing, so a real terminal code
    # is the tell that a caller wrote through to the stored copy.
    stamped = [
        s.get("from") for day in stored[1]["sailings"] for s in day if s.get("from")
    ]
    assert not stamped, f"{len(stamped)} stored sailings stamped with {set(stamped)}"


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
