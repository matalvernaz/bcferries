"""Regression tests for a period BC Ferries has posted no timetable for.

From 2026-11-20 to 2026-11-24 the Otter Bay berth on Pender Island was closed
for repairs and a foot-passenger water taxi ran instead. BC Ferries' pages for
the four Pender routes showed that period with "Seasonal schedules have not
been posted for these dates", an Important box about the closure, and the next
period (Nov 25, 2026 to May 6, 2027) in the date picker. The app read the empty
page as a broken one, went on serving a one-day copy for Nov 19 that had ended,
and held /health/schedules at 503 until BcferriesSchedulesDegraded fired.

Fixtures are trimmed from real captures of POB-SWB taken on 2026-11-20:

- ``seasonal_pob_swb_unposted.html`` is both directions' sections of the page,
  Important box and date picker included.
- ``seasonal_pob_swb_next.html`` is the first direction's section of the page
  the date picker links to for Nov 25, 2026 to May 6, 2027.
"""

import os
import sys
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ROUTE = "pob-swb"
PAGE_URL = "https://www.bcferries.com/routes-fares/schedules/seasonal/POB-SWB"
NOT_POSTED_LINE = "Seasonal schedules have not been posted for these dates."
CLOSURE = {
    "from": {"year": 2026, "month": 11, "day": 20},
    "to": {"year": 2026, "month": 11, "day": 24},
}


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


def freeze(monkeypatch, scraper, store, year, month, day, hour=9):
    """Make every "now" in the app a fixed moment on the coast.

    The fixtures carry real 2026 dates, so without this the tests would start
    failing once those dates are in the past.
    """
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(year, month, day, hour, 0, tzinfo=tz)

    monkeypatch.setattr(scraper, "datetime", FrozenDateTime)
    monkeypatch.setattr(store, "datetime", FrozenDateTime)


def count_fetches(scraper, monkeypatch, body, final_url):
    """Point the scraper's fetch at a canned response and count the calls."""
    calls = []

    def counting_fetch(url, **options):
        calls.append(url)
        return body, final_url

    monkeypatch.setattr(scraper, "_fetch_with_curl", counting_fetch)
    return calls


def parse(scraper, html):
    return scraper._parse_seasonal_schedule(BeautifulSoup(html, "html.parser"))


def unposted_schedule():
    """What the store holds for a route once the closure page has been read."""
    return {
        "dateRange": {"from": dict(CLOSURE["from"]), "to": dict(CLOSURE["to"])},
        "sailings": [[], [], [], [], [], [], [], []],
        "terminalCameras": None,
        "notPosted": True,
    }


def plant(store, route, schedule, age_seconds):
    """Store a schedule as if it had been fetched a while ago."""
    store.save(route, schedule)
    with store._lock:
        fetched_at, stored = store._memory[route]
        store._memory[route] = (fetched_at - age_seconds, stored)


def one_day_copy(year, month, day):
    """The shape of copy the app held for Pender: one day, one sailing."""
    date = {"year": year, "month": month, "day": day}
    return {
        "dateRange": {"from": dict(date), "to": dict(date)},
        "sailings": [[], [], [], [], [{"scheduledDeparture": {"hour": 7, "minute": 0}}], [], [], []],
        "terminalCameras": None,
    }


def test_a_page_with_no_timetable_posted_is_read_as_exactly_that(env):
    scraper, _ = env

    schedule = parse(scraper, fixture("seasonal_pob_swb_unposted.html"))

    assert schedule["dateRange"] == CLOSURE
    assert schedule.get("notPosted") is True
    assert not any(schedule["sailings"])


def test_the_routes_important_box_comes_through_as_text_and_links(env):
    scraper, _ = env

    notice = parse(scraper, fixture("seasonal_pob_swb_unposted.html")).get("notice")

    assert notice, "the closure notice on the page was dropped"
    assert notice["text"].startswith(
        "This route will be operating a foot passenger water-taxi during the Otter Bay"
    )
    assert notice["text"].endswith("visit bcferriesprojects.ca/ob."), notice["text"]
    # A paragraph that is nothing but a link is kept as the link, not read out twice.
    assert "View water taxi schedule" not in notice["text"]
    links = {link["text"]: link["url"] for link in notice["links"]}
    assert links["bcferriesprojects.ca/ob"] == "https://www.bcferriesprojects.ca/ob"
    assert links["View water taxi schedule"].startswith(
        "https://www.bcferries.com/current-conditions/service-notices?serviceNoticeCode=8803400396552"
    )


def test_reading_a_page_as_unposted_does_not_depend_on_the_important_box(env):
    """The box is free text from BC Ferries' CMS; only their own "not posted" line counts."""
    scraper, _ = env
    soup = BeautifulSoup(fixture("seasonal_pob_swb_unposted.html"), "html.parser")
    for label in soup.find_all("b", string="Important"):
        label.parent.decompose()

    schedule = scraper._parse_seasonal_schedule(soup)

    assert schedule.get("notPosted") is True
    assert not schedule.get("notice")


def test_the_return_direction_cannot_stand_in_for_the_one_asked_for(env):
    """Each page carries both directions; only the first is the route that was asked for."""
    scraper, _ = env
    page = BeautifulSoup(fixture("seasonal_pob_swb_unposted.html"), "html.parser")
    posted = BeautifulSoup(fixture("seasonal_pob_swb_next.html"), "html.parser")
    return_direction = page.select_one("main").find_all("section", recursive=False)[1]
    return_direction.replace_with(posted.select_one("main").find("section", recursive=False))

    schedule = scraper._parse_seasonal_schedule(page)

    assert not any(schedule["sailings"]), "the other direction's sailings were read as this route's"
    assert schedule.get("notPosted") is True


def test_an_empty_page_without_bc_ferries_saying_so_is_still_a_failure(env, monkeypatch):
    """Guards the old rule: only BC Ferries' own words make an empty page acceptable."""
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    current = {
        "dateRange": {"from": {"year": 2026, "month": 11, "day": 1}, "to": {"year": 2027, "month": 3, "day": 31}},
        "sailings": [[], [{"scheduledDeparture": {"hour": 7, "minute": 0}}], [], [], [], [], [], []],
        "terminalCameras": None,
    }
    plant(store, ROUTE, current, age_seconds=scraper.CACHE_TTL + 60)
    body = fixture("seasonal_pob_swb_unposted.html").replace(NOT_POSTED_LINE, "")
    count_fetches(scraper, monkeypatch, body, PAGE_URL)

    result = scraper.get_seasonal_schedule(ROUTE)

    assert result["dateRange"] == current["dateRange"], "an unexplained empty page replaced a good timetable"
    assert store.last_failure(ROUTE)["cause"] == "page parsed to no sailings"


def test_the_unposted_period_replaces_an_ended_copy_and_reads_as_healthy(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 20)
    plant(store, ROUTE, one_day_copy(2026, 11, 19), age_seconds=20 * 60)
    count_fetches(scraper, monkeypatch, fixture("seasonal_pob_swb_unposted.html"), PAGE_URL)

    result = scraper.get_seasonal_schedule(ROUTE)

    assert result["dateRange"] == CLOSURE
    assert result.get("notPosted") is True
    assert store.load(ROUTE)[1]["dateRange"] == CLOSURE
    assert store.last_failure(ROUTE) is None
    report = store.health()
    assert report["healthy"] is True, report["routesDegraded"]
    assert report["routesNotPosted"] == [ROUTE]
    assert report["routes"][ROUTE]["notPosted"] is True


def test_an_unposted_period_is_checked_again_within_the_hour(env, monkeypatch):
    """BC Ferries says it is working on the timetable, so four hours is too long to wait."""
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    plant(store, ROUTE, unposted_schedule(), age_seconds=50 * 60)
    calls = count_fetches(scraper, monkeypatch, fixture("seasonal_pob_swb_unposted.html"), PAGE_URL)

    scraper.get_seasonal_schedule(ROUTE)

    assert calls == [PAGE_URL], "an unposted period read fifty minutes ago was not checked again"


def test_an_unposted_period_is_not_refetched_on_every_request(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    plant(store, ROUTE, unposted_schedule(), age_seconds=10 * 60)
    calls = count_fetches(scraper, monkeypatch, fixture("seasonal_pob_swb_unposted.html"), PAGE_URL)

    for _ in range(5):
        scraper.get_seasonal_schedule(ROUTE)

    assert calls == [], f"an unposted period read ten minutes ago was fetched {len(calls)} times"


def test_the_warmer_looks_again_every_hour_while_a_timetable_is_unposted(env, monkeypatch):
    """The warmer's pace has to match the re-check, or nothing looks unless someone visits."""
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    plant(store, ROUTE, unposted_schedule(), age_seconds=60)

    waiting = scraper._routes_waiting_for_a_timetable([ROUTE, "bow-hsb"])

    assert waiting == [ROUTE]
    assert scraper._next_warm_delay(2, 2, len(waiting)) == scraper.WARM_UNPOSTED_INTERVAL_SEC
    # Each pass has to find the copy old enough to fetch again, or it waits a second hour.
    assert scraper.UNPOSTED_RECHECK_SEC < scraper.WARM_UNPOSTED_INTERVAL_SEC < scraper.WARM_INTERVAL_SEC
    assert scraper._next_warm_delay(2, 2, 0) == scraper.WARM_INTERVAL_SEC
