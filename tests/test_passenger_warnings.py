"""Passenger restrictions survive both timetable and current-conditions paths."""

from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from test_schedule_durability import env, fixture, serve, GOOD_URL


def prepare(scraper, monkeypatch, day=30, hour=13):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, day, hour, 0, tzinfo=scraper.PACIFIC)

    monkeypatch.setattr(scraper, "datetime", Clock)
    serve(scraper, monkeypatch, fixture("bowen_passenger_restrictions.html"), GOOD_URL)
    monkeypatch.setattr(scraper, "get_current_conditions", lambda route: None)
    monkeypatch.setattr(scraper, "get_tomorrow_conditions", lambda route: None)


def at_1355(sailings):
    return next(s for s in sailings if s["scheduledDeparture"] == {"hour": 13, "minute": 55})


def test_bowen_today_warning_survives_cache_and_limit(env, monkeypatch):
    scraper, store = env
    prepare(scraper, monkeypatch)
    for _ in range(2):
        sailing = scraper.get_upcoming_sailings("bow-hsb", limit=1)["sailings"][0][0]
        assert sailing["scheduledDeparture"] == {"hour": 13, "minute": 55}
        assert "No passengers permitted" in sailing["warning"]
        assert "Dangerous goods only" in sailing["warning"]
    # Restarting must not reintroduce an unlabelled sailing from disk.
    store._memory.clear()
    assert "No passengers permitted" in at_1355(store.load("bow-hsb")[1]["sailings"][3])["warning"]


def test_future_date_and_weekly_schedule_keep_correct_weekday_restriction(env, monkeypatch):
    scraper, _ = env
    prepare(scraper, monkeypatch, day=29)
    wednesday = scraper.get_sailings_for_date("bow-hsb", 1)
    thursday = scraper.get_sailings_for_date("bow-hsb", 2)
    weekly = scraper.get_schedule("bow-hsb")
    assert "No passengers permitted" in at_1355(wednesday["sailings"][0])["warning"]
    assert at_1355(thursday["sailings"][0])["warning"] == ""
    assert "No passengers permitted" in at_1355(weekly["sailings"][3])["warning"]
    assert at_1355(weekly["sailings"][4])["warning"] == ""


def test_restriction_body_is_kept_without_a_red_heading(env):
    scraper, _ = env
    soup = BeautifulSoup(fixture("bowen_passenger_restrictions.html"), "html.parser")
    for heading in soup.select(".red-text"):
        heading.decompose()
    schedule = scraper._parse_seasonal_schedule(soup)
    assert at_1355(schedule["sailings"][3])["warning"] == "Dangerous goods only"


@pytest.mark.parametrize("tomorrow", [False, True])
@pytest.mark.parametrize("sailing_type", ["DG", "REG", None])
def test_live_feed_dg_flag_and_existing_operational_messages(env, tomorrow, sailing_type):
    scraper, _ = env
    row = {
        "dept": "HSB", "dest": "BOW", "dest1": "BOW", "vessel": "CAP",
        "sailingType": sailing_type,
        "departure": "2026-09-30 09:20:00",
        "scheduledDeparture": "2026-09-30 09:20:00",
        "scheduledArrival": "2026-09-30 09:40:00",
        "departureStatus": "Delayed 10 minutes",
        "delayComments": "Delayed 10 minutes",
    }
    if tomorrow:
        sailings = scraper.parse_cc_tomorrow("hsb-bow", [row])
    else:
        sailings, _ = scraper.parse_cc_today("hsb-bow", {"arrivalDepartures": {"route08": [row]}})
    warning = sailings[0]["warning"]
    assert "Delayed 10 minutes" in warning
    assert ("No passengers permitted" in warning) == (sailing_type == "DG")


def test_live_dg_departure_with_no_delay_comment_is_labelled_in_upcoming(env, monkeypatch):
    scraper, _ = env
    prepare(scraper, monkeypatch, hour=9)
    row = {
        "dept": "HSB", "dest": "BOW", "vessel": "CAP", "sailingType": "DG",
        "scheduledDeparture": "2026-09-30 09:20:00", "delayComments": None,
    }
    monkeypatch.setattr(scraper, "get_current_conditions", lambda route: {"arrivalDepartures": {"route08": [row]}})
    sailing = scraper.get_upcoming_sailings("hsb-bow", limit=1)["sailings"][0][0]
    assert "No passengers permitted" in sailing["warning"]
    assert "dangerous goods" in sailing["warning"]
