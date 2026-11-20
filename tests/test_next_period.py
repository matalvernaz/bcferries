"""Regression tests for reading the period after one with no timetable posted.

During the Otter Bay berth closure (2026-11-20 to 24) the Pender Island pages
had no timetable for the closure, but their date picker linked the next period,
Nov 25, 2026 to May 6, 2027. These tests cover fetching that period (the warmer
only, from the page's own link), choosing the period that covers each date,
and telling people why a route has no sailings, naming the island in the Gulf
Islands views. Fixtures are described in test_unposted_period.py.
"""

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ROUTE = "pob-swb"
PAGE_URL = "https://www.bcferries.com/routes-fares/schedules/seasonal/POB-SWB"
NEXT_URL = PAGE_URL + "?departureDateCode=R5_20261125_20270506"
NEXT_HREF = "/routes-fares/schedules/seasonal/POB-SWB?departureDateCode=R5_20261125_20270506"
BOWEN_URL = "https://www.bcferries.com/routes-fares/schedules/seasonal/BOW-HSB"
CLOSURE = {
    "from": {"year": 2026, "month": 11, "day": 20},
    "to": {"year": 2026, "month": 11, "day": 24},
}
FOLLOWING = {
    "from": {"year": 2026, "month": 11, "day": 25},
    "to": {"year": 2027, "month": 5, "day": 6},
}
# What the warmer's fetch of the following period yields: 60 sailings a week,
# eight of them on Wednesdays (Nov 25, 2026 is a Wednesday).
FOLLOWING_WEEK = [0, 12, 8, 8, 11, 6, 7, 8]


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
    """Make every "now" in the app a fixed moment on the coast."""
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(year, month, day, hour, 0, tzinfo=tz)

    monkeypatch.setattr(scraper, "datetime", FrozenDateTime)
    monkeypatch.setattr(store, "datetime", FrozenDateTime)


def serve_pages(scraper, monkeypatch, pages):
    """Answer each fetch from ``pages``, a function from URL to (body, final URL), and record the URLs."""
    calls = []

    def fake_fetch(url, **options):
        calls.append(url)
        return pages(url)

    monkeypatch.setattr(scraper, "_fetch_with_curl", fake_fetch)
    return calls


def closure_pages(url):
    """bcferries.com as it was during the closure, for Pender and for everything else."""
    if url == NEXT_URL:
        return fixture("seasonal_pob_swb_next.html"), NEXT_URL
    if url == PAGE_URL:
        return fixture("seasonal_pob_swb_unposted.html"), PAGE_URL
    return fixture("seasonal_bow_hsb.html"), BOWEN_URL


def age(store, route, seconds):
    """Make a stored copy look ``seconds`` older than it is."""
    with store._lock:
        fetched_at, stored = store._memory[route]
        store._memory[route] = (fetched_at - seconds, stored)


def test_the_warmer_fetches_the_following_period_from_the_pages_own_link(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    calls = serve_pages(scraper, monkeypatch, closure_pages)

    scraper.get_seasonal_schedule(ROUTE, background=True)

    assert calls == [PAGE_URL, NEXT_URL]
    held = store.load(ROUTE)[1]
    assert held["dateRange"] == CLOSURE
    assert held["next"]["dateRange"] == FOLLOWING
    assert held["next"]["href"] == NEXT_HREF
    assert [len(day) for day in held["next"]["sailings"]] == FOLLOWING_WEEK


def test_a_page_request_leaves_the_following_period_to_the_warmer(env, monkeypatch):
    """A person already waits on one fetch; the second goes to the warmer, which is woken for it."""
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    calls = serve_pages(scraper, monkeypatch, closure_pages)
    scraper._warm_now.clear()

    scraper.get_seasonal_schedule(ROUTE)

    assert calls == [PAGE_URL]
    assert "next" not in store.load(ROUTE)[1]
    assert scraper._warm_now.is_set(), "the warmer was not told there was a period to fetch"


def test_the_warmer_completes_a_fresh_copy_a_page_request_stored(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    calls = serve_pages(scraper, monkeypatch, closure_pages)
    scraper.get_seasonal_schedule(ROUTE)
    fetched_at = store.load(ROUTE)[0]

    scraper.get_seasonal_schedule(ROUTE, background=True)

    assert calls == [PAGE_URL, NEXT_URL], "the warmer refetched a fresh page or skipped the period after it"
    entry = store.load(ROUTE)
    assert entry[1]["next"]["dateRange"] == FOLLOWING
    assert entry[0] == fetched_at, "adding the next period made the closure page look freshly read"


def test_a_page_request_keeps_the_following_period_the_warmer_fetched(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    calls = serve_pages(scraper, monkeypatch, closure_pages)
    scraper.get_seasonal_schedule(ROUTE, background=True)
    age(store, ROUTE, scraper.UNPOSTED_RECHECK_SEC + 60)

    scraper.get_seasonal_schedule(ROUTE)

    assert calls == [PAGE_URL, NEXT_URL, PAGE_URL]
    assert store.load(ROUTE)[1]["next"]["dateRange"] == FOLLOWING


def test_a_following_period_that_comes_back_as_another_period_is_not_kept(env, monkeypatch):
    """If BC Ferries ignores the link and serves the closure again, that is not the next timetable."""
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    calls = serve_pages(
        scraper, monkeypatch, lambda url: (fixture("seasonal_pob_swb_unposted.html"), url))

    scraper.get_seasonal_schedule(ROUTE, background=True)

    assert calls == [PAGE_URL, NEXT_URL]
    assert "next" not in store.load(ROUTE)[1]


def test_a_date_picker_link_that_leaves_the_routes_page_is_not_followed(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    body = fixture("seasonal_pob_swb_unposted.html").replace(
        NEXT_HREF, "https://example.com/routes-fares/schedules/seasonal/POB-SWB?departureDateCode=x")
    calls = serve_pages(scraper, monkeypatch, lambda url: (body, PAGE_URL))

    scraper.get_seasonal_schedule(ROUTE, background=True)

    assert calls == [PAGE_URL]
    assert "next" not in store.load(ROUTE)[1]


def test_a_date_in_the_closure_has_no_sailings_and_says_why(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    serve_pages(scraper, monkeypatch, closure_pages)
    scraper.get_seasonal_schedule(ROUTE, background=True)

    result = scraper.get_sailings_for_date(ROUTE, 1)

    assert result["sailings"][0] == []
    assert result["dateRange"] == CLOSURE
    assert not result.get("scheduleWarning")
    assert len(result["notices"]) == 1
    notice = result["notices"][0]
    assert notice["text"].startswith(
        "BC Ferries has not posted a timetable for Nov 20 to Nov 24, 2026. "
        "This route will be operating a foot passenger water-taxi"
    ), notice["text"]
    assert {"text": "bcferriesprojects.ca/ob", "url": "https://www.bcferriesprojects.ca/ob"} in notice["links"]


def test_a_date_after_the_closure_reads_the_following_timetable(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    serve_pages(scraper, monkeypatch, closure_pages)
    scraper.get_seasonal_schedule(ROUTE, background=True)

    result = scraper.get_sailings_for_date(ROUTE, 4)

    assert result["dateRange"] == FOLLOWING
    assert len(result["sailings"][0]) == FOLLOWING_WEEK[3]
    assert result["sailings"][0][0]["messages"]["relativeDay"] == "Wednesday"
    assert not result.get("scheduleWarning")
    assert result["notices"] == []


def test_the_now_view_takes_each_day_from_the_period_that_covers_it(env, monkeypatch):
    """On the closure's last evening, tomorrow's sailings come from the timetable that starts tomorrow."""
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 24, hour=20)
    serve_pages(scraper, monkeypatch, closure_pages)
    scraper.get_seasonal_schedule(ROUTE, background=True)

    result = scraper.get_upcoming_sailings(ROUTE, limit=50)

    days = {sailing["messages"]["relativeDay"] for sailing in result["sailings"][0]}
    assert days == {"Tomorrow"}, f"sailings came from {days}"
    assert len(result["sailings"][0]) == FOLLOWING_WEEK[3]
    assert result["notices"][0]["text"].startswith("BC Ferries has not posted a timetable")


def test_the_gulf_islands_view_names_pender_in_its_notice(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    serve_pages(scraper, monkeypatch, closure_pages)

    result = scraper.get_sailings_for_date("sgi-swb", 1)

    islands = {sailing.get("from") for sailing in result["sailings"][0]}
    assert islands and "pob" not in islands, f"sailings from {islands}"
    texts = [notice["text"] for notice in result["notices"]]
    assert len(texts) == 1, texts
    assert texts[0].startswith(
        "Pender Island (Otter Bay): BC Ferries has not posted a timetable for Nov 20 to Nov 24, 2026."
    ), texts[0]


def no_live_feed(scraper, monkeypatch):
    """Cut the current-conditions feed off, so only the timetable pages answer."""
    def refuse():
        raise ConnectionError("no live feed in tests")

    monkeypatch.setattr(scraper, "_new_session", refuse)


@pytest.mark.parametrize("route", ["sgi-swb", "swb-sgi"])
def test_the_gulf_islands_live_view_names_pender_without_fetching_its_page(env, monkeypatch, route):
    """Both live merges: islands to Swartz Bay has its own branch, Swartz Bay to the islands goes through the feed's."""
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)

    def both_directions(url):
        if url.endswith("/SWB-POB"):
            return fixture("seasonal_pob_swb_unposted.html"), url
        return closure_pages(url)

    serve_pages(scraper, monkeypatch, both_directions)
    scraper.get_seasonal_schedule("pob-swb", background=True)
    scraper.get_seasonal_schedule("swb-pob", background=True)
    no_live_feed(scraper, monkeypatch)
    calls = serve_pages(scraper, monkeypatch, both_directions)

    result = scraper.get_upcoming_sailings(route)

    assert not [url for url in calls if "POB" in url], "the live view fetched Pender's page again"
    assert [notice["text"][:26] for notice in result["notices"]] == ["Pender Island (Otter Bay):"]


def test_a_timetable_whose_period_has_not_begun_shows_no_sailings(env, monkeypatch):
    """Showing a timetable before its first day would list sailings that may not run yet."""
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    sailing = {"scheduledDeparture": {"hour": 7, "minute": 0}, "exceptDates": [], "onlyOnDates": [], "warning": ""}
    store.save("bow-hsb", {
        "dateRange": {"from": dict(FOLLOWING["from"]), "to": dict(FOLLOWING["to"])},
        "sailings": [[]] + [[dict(sailing)] for _ in range(7)],
        "terminalCameras": None,
    })

    result = scraper.get_sailings_for_date("bow-hsb", 1)

    assert result["sailings"][0] == []


def test_the_now_view_leaves_the_stored_timetable_alone(env, monkeypatch):
    """Decorating the stored sailings in place stripped their "Except on" dates for every later reader.

    Only the timetable-only branch did that, which serves the slugs with no
    live feed, such as an island's own page. The Bowen fixture stands in for
    Mayne's.
    """
    scraper, store = env
    island_route = "pvb-swb"
    assert island_route not in scraper.ROUTES
    # A Monday before the 5:20 am sailing that runs "Except on Apr 21".
    freeze(monkeypatch, scraper, store, 2026, 11, 23, hour=4)
    serve_pages(scraper, monkeypatch, closure_pages)
    no_live_feed(scraper, monkeypatch)
    scraper.get_seasonal_schedule(island_route)

    scraper.get_upcoming_sailings(island_route, limit=50)

    stored = store.load(island_route)[1]["sailings"]
    excepted = [sailing for day in stored for sailing in day if sailing.get("exceptDates")]
    assert excepted, "the stored copy lost its Except on dates"
    decorated = [sailing for day in stored for sailing in day if "relativeDay" in sailing["messages"]]
    assert not decorated, f"{len(decorated)} stored sailings were decorated for one view"


def test_once_the_closure_ends_the_following_timetable_keeps_the_route_healthy(env, monkeypatch):
    scraper, store = env
    freeze(monkeypatch, scraper, store, 2026, 11, 21)
    serve_pages(scraper, monkeypatch, closure_pages)
    scraper.get_seasonal_schedule(ROUTE, background=True)

    freeze(monkeypatch, scraper, store, 2026, 11, 25)
    report = store.health()

    assert ROUTE not in report["routesDegraded"]
    assert report["routes"][ROUTE]["periodExpired"] is False
    assert report["routes"][ROUTE]["nextDateRange"] == FOLLOWING
