import copy
import os
import re
import subprocess
import tempfile
import threading
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import store
from data import ROUTES, VESSELS, TERMINALS, WEEKDAYS, MONTHS, MONTH_NAMES, ISLAND_SHORT_NAMES, SGI_RETURN_TERMINALS, SWB_SGI_RETURN_TERMINALS, SGI_CAMERAS, EXTRA_CAMERAS
from ais import get_vessel_tracking

PACIFIC = ZoneInfo("America/Vancouver")

CC_API = "https://cc.bcferries.com"
BCF_BASE = "https://www.bcferries.com"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _new_session():
    """Create a fresh requests session for CC API calls."""
    s = requests.Session()
    s.headers["User-Agent"] = _UA
    s.max_redirects = 20
    return s


def _fetch_with_curl(url):
    """Fetch a bcferries.com page using curl with a temporary cookie jar
    so Queue-it waiting-room redirects are handled without stale cookies
    causing redirect loops.

    Returns (body, final_url). The final URL is load-bearing: the Queue-it
    waiting room answers HTTP 200 with a body that names neither Queue-it nor
    itself, so the off-domain redirect is the only reliable tell.
    """
    jar = body_file = None
    try:
        fd, jar = tempfile.mkstemp(prefix="bcf-", suffix=".jar")
        os.close(fd)
        fd, body_file = tempfile.mkstemp(prefix="bcf-body-", suffix=".html")
        os.close(fd)
        cmd = [
            "curl", "-s", "-L",
            "-c", jar, "-b", jar,
            "-H", f"User-Agent: {_UA}",
            "-o", body_file,
            "-w", "%{url_effective}",
            url,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        with open(body_file, encoding="utf-8", errors="replace") as fh:
            return fh.read(), result.stdout.strip()
    except Exception as e:
        print(f"Schedule scrape error for {url}: {e}")
        return "", ""
    finally:
        for path in (jar, body_file):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


CACHE_TTL = 4 * 3600  # 4 hours
# How long to wait before retrying a source that answered with an interception
# page. Without this, refusing to cache the bad result would turn every request
# into a fresh 30s curl against a wall that is not going to move.
FAILURE_RETRY_TTL = 15 * 60

_ALLOWED_FETCH_HOST_SUFFIX = "bcferries.com"


def _detect_interception(html, final_url, soup):
    """Return a reason string if a fetch did not reach the real schedule page.

    Ordered by reliability: an empty body, then a redirect off bcferries.com
    (the Queue-it waiting room, and any future wall that works the same way),
    then a body that carries none of the DOM the parser needs.
    """
    if not html or not html.strip():
        return "empty response"

    host = urlparse(final_url or "").hostname or ""
    if host and not (
        host == _ALLOWED_FETCH_HOST_SUFFIX
        or host.endswith("." + _ALLOWED_FETCH_HOST_SUFFIX)
    ):
        return f"redirected off-domain to {host}"

    if soup is not None:
        if soup.select_one(".seasonal-schedule-wrapper") is None and (
            soup.select_one('[href="#dateRangeModal"]') is None
        ):
            return "response carries no schedule markup"

    return None


def _is_usable_schedule(schedule):
    """A schedule worth persisting: it knows its period and has sailings."""
    return bool(
        schedule
        and schedule.get("dateRange")
        and any(schedule.get("sailings") or [])
    )


def _fmt_time(hour, minute):
    if hour == 0:
        return f"12:{minute:02d} am"
    elif hour < 12:
        return f"{hour}:{minute:02d} am"
    elif hour == 12:
        return f"12:{minute:02d} pm"
    else:
        return f"{hour - 12}:{minute:02d} pm"


def _parse_time_text(text):
    """Parse '5:15 am' style time to {hour, minute}."""
    m = re.match(r"(\d+):(\d\d)\s*(am|pm)", text.strip().lower())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    if m.group(3) == "pm" and hour < 12:
        hour += 12
    if m.group(3) == "am" and hour == 12:
        hour = 0
    return {"hour": hour, "minute": minute}


def _parse_except_dates(text):
    """Parse 'Except on Apr 6, May 18, Aug 3 & Mar 29' into a set of (month, day) tuples."""
    m = re.match(r"Except on (.+)", text, re.IGNORECASE)
    if not m:
        return set()
    dates_text = m.group(1).replace("&", ",")
    dates = set()
    for part in dates_text.split(","):
        part = part.strip()
        dm = re.match(r"([A-Za-z]+)\s+(\d+)", part)
        if dm:
            month = MONTHS.get(dm.group(1).lower()[:3])
            day = int(dm.group(2))
            if month:
                dates.add((month, day))
    return dates


def _parse_only_on_dates(text):
    """Parse 'Only on Apr 6, 20, May 4 & 18' into a set of (month, day) tuples.
    A month carries forward for bare day numbers until a new month is named."""
    m = re.match(r"Only on (.+)", text, re.IGNORECASE)
    if not m:
        return set()
    dates_text = m.group(1).replace("&", ",")
    dates = set()
    current_month = None
    for part in re.split(r",\s*", dates_text):
        part = part.strip()
        dm = re.match(r"([A-Za-z]+)\s+(\d+)", part)
        if dm:
            current_month = MONTHS.get(dm.group(1).lower()[:3])
            if current_month:
                dates.add((current_month, int(dm.group(2))))
        elif current_month and re.match(r"^\d+$", part):
            dates.add((current_month, int(part)))
    return dates


def _parse_duration(text):
    """Parse '1h 20m' or '20m' style duration."""
    dur = {"hour": 0, "minute": 0}
    h = re.search(r"(\d+)h", text)
    m = re.search(r"(\d+)m", text)
    if h:
        dur["hour"] = int(h.group(1))
    if m:
        dur["minute"] = int(m.group(1))
    return dur


def _parse_date_range(text):
    """Parse 'Jan 1, 2026 - Mar 31, 2026' style date range."""
    m = re.match(
        r"(\w{3})\s+(\d+),\s+(\d{4})\s*-\s*(\w{3})\s+(\d+),\s+(\d{4})",
        text.strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    return {
        "from": {
            "year": int(m.group(3)),
            "month": MONTHS.get(m.group(1).lower(), 1),
            "day": int(m.group(2)),
        },
        "to": {
            "year": int(m.group(6)),
            "month": MONTHS.get(m.group(4).lower(), 1),
            "day": int(m.group(5)),
        },
    }


def _relative_time(sailing_dt):
    """Generate human-friendly relative time string."""
    now = datetime.now(PACIFIC)
    diff = sailing_dt - now
    total_minutes = int(diff.total_seconds() / 60)
    if total_minutes < 0:
        return "departed"
    if total_minutes < 60:
        return f"in {total_minutes} minute{'s' if total_minutes != 1 else ''}"
    hours = total_minutes // 60
    mins = total_minutes % 60
    if hours >= 24:
        days = hours // 24
        remaining_hours = hours % 24
        parts = []
        parts.append(f"{days} day{'s' if days != 1 else ''}")
        if remaining_hours:
            parts.append(f"{remaining_hours} hour{'s' if remaining_hours != 1 else ''}")
        return "in " + ", ".join(parts)
    parts = []
    parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return "in " + ", ".join(parts)


def _sailing_datetime(dep, days_ahead=0):
    """Build a datetime from a {hour, minute} dict, optionally days in future."""
    now = datetime.now(PACIFIC)
    dt = now.replace(hour=dep["hour"], minute=dep["minute"], second=0, microsecond=0, tzinfo=PACIFIC)
    dt += timedelta(days=days_ahead)
    return dt


def _capacity_text(full_pct):
    """Format capacity percentage, or None if not available."""
    if full_pct is None:
        return None
    try:
        val = int(full_pct)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    return f"{val} percent full"


def get_current_conditions(route):
    """Fetch real-time sailing data from the CC API for routes in the routes map."""
    route_info = ROUTES.get(route)
    if not route_info:
        return None

    url = f"{CC_API}/api/currentconditions/1.0/route/{route_info['from']}/route{route_info['id']}"
    try:
        resp = _new_session().get(url, timeout=15)
        return resp.json()
    except Exception as e:
        print(f"CC API error for {route}: {e}")
        return None


def get_tomorrow_conditions(route):
    """Fetch tomorrow's schedule from the CC API."""
    route_info = ROUTES.get(route)
    if not route_info:
        return None

    url = f"{CC_API}/api/currentconditions/1.0/sc/route/{route_info['from']}/{route_info['id']}"
    try:
        resp = _new_session().get(url, timeout=15)
        return resp.json()
    except Exception as e:
        print(f"CC API tomorrow error for {route}: {e}")
        return None


def get_seasonal_schedule(route):
    """Scrape the seasonal schedule page for a route, with caching.

    A stored schedule is only ever replaced by a better one. A fetch that was
    intercepted, or that parsed to nothing, leaves the stored copy alone and is
    reported through the health ledger \u2014 the source going behind a wall must
    never cost us a timetable we already have.
    """
    cached = store.load(route)
    if cached and (time.time() - cached[0]) < CACHE_TTL:
        return cached[1]

    failure = store.last_failure(route)
    if failure and (time.time() - failure["at"]) < FAILURE_RETRY_TTL:
        return cached[1] if cached else empty_schedule()

    url = f"{BCF_BASE}/routes-fares/schedules/seasonal/{route.upper()}"
    html, final_url = _fetch_with_curl(url)
    soup = BeautifulSoup(html, "html.parser") if html else None

    reason = _detect_interception(html, final_url, soup)
    if not reason:
        schedule = _parse_seasonal_schedule(soup)
        if not _is_usable_schedule(schedule):
            reason = "page parsed to no sailings"

    if reason:
        served = "serving stored copy" if cached else "no stored copy to fall back on"
        print(f"[schedule-blocked] {route.upper()}: {reason} \u2014 {served}")
        store.note_failure(route, reason)
        return cached[1] if cached else empty_schedule()

    def _fmt_date(d):
        return f"{MONTH_NAMES[d['month']]} {d['day']}, {d['year']}"

    if cached and cached[1].get("dateRange"):
        old_to = cached[1]["dateRange"]["to"]
        new_to = schedule["dateRange"]["to"]
        if old_to != new_to:
            print(
                f"[schedule-change] {route.upper()}: date range changed "
                f"from {_fmt_date(cached[1]['dateRange']['from'])}\u2013{_fmt_date(old_to)} "
                f"to {_fmt_date(schedule['dateRange']['from'])}\u2013{_fmt_date(new_to)}"
            )
    else:
        print(
            f"[schedule-loaded] {route.upper()}: "
            f"{_fmt_date(schedule['dateRange']['from'])}\u2013{_fmt_date(schedule['dateRange']['to'])}"
        )

    store.save(route, schedule)
    return schedule


def _parse_seasonal_schedule(soup):
    """Parse the seasonal schedule HTML into a schedule dict."""
    schedule = empty_schedule()

    date_range_el = soup.select_one('[href="#dateRangeModal"]')
    if date_range_el:
        schedule["dateRange"] = _parse_date_range(date_range_el.get_text())

    wrapper = soup.select_one(".seasonal-schedule-wrapper")
    if not wrapper:
        return schedule

    table = wrapper.select_one("table")
    if not table:
        return schedule

    current_day = 0
    rows = table.select("tr")

    for row in rows:
        day_attr = row.get("data-schedule-day")
        if day_attr:
            day_name = re.match(r"([a-z]+day)", day_attr.lower())
            if day_name:
                current_day = WEEKDAYS.get(day_name.group(1), 0)
            continue

        cells = row.select("td")
        if len(cells) < 2:
            continue

        depart = _parse_time_text(cells[1].get_text())
        if not depart:
            continue

        arrive = _parse_time_text(cells[2].get_text()) if len(cells) > 2 else None
        duration = _parse_duration(cells[3].get_text()) if len(cells) > 3 else None
        # If the page has no explicit duration column, derive it from dep→arr
        if depart and arrive and (duration is None or (duration["hour"] == 0 and duration["minute"] == 0)):
            diff = (arrive["hour"] * 60 + arrive["minute"]) - (depart["hour"] * 60 + depart["minute"])
            if diff < 0:
                diff += 24 * 60  # overnight sailing
            if diff > 0:
                duration = {"hour": diff // 60, "minute": diff % 60}

        warning_header = ""
        warning_body = ""
        red_text = cells[1].select_one(".red-text")
        if red_text:
            warning_header = red_text.get_text().strip()
        black_text = cells[1].select_one(".text-black")
        if black_text:
            warning_body = black_text.get_text().strip()

        warning = ""
        except_dates = set()
        only_dates = set()
        if warning_header and warning_body:
            warning = f"{warning_header}: {warning_body}"
        elif warning_header:
            warning = warning_header
        for text in (warning_header, warning_body, warning):
            except_dates |= _parse_except_dates(text)
            only_dates |= _parse_only_on_dates(text)

        sailing = {
            "messages": {"friendlyTime": _fmt_time(depart["hour"], depart["minute"])},
            "realtime": False,
            "scheduledDeparture": depart,
            "scheduledArrival": arrive,
            "scheduledDuration": duration,
            "actualDeparture": None,
            "actualArrival": None,
            "estimatedArrival": None,
            "delay": None,
            "vessel": None,
            "full": None,
            "carFull": None,
            "oversizeFull": None,
            "from": None,
            "destinations": [],
            "transferAt": None,
            "warning": "",
            "exceptDates": list(except_dates),
            "onlyOnDates": list(only_dates),
        }

        if current_day and current_day <= 7:
            schedule["sailings"][current_day].append(sailing)

    return schedule


def parse_cc_today(route, cc_data):
    """Parse today's current conditions API data into sailings list."""
    route_info = ROUTES[route]
    route_key = f"route{route_info['id']}"
    sailings = []

    if not cc_data or "arrivalDepartures" not in cc_data:
        return sailings, None

    departures = cc_data.get("arrivalDepartures", {}).get(route_key, [])
    for sd in departures:
        vessel_code = sd.get("vessel", "").lower()

        sailing = {
            "realtime": True,
            "scheduledDeparture": None,
            "scheduledArrival": None,
            "scheduledDuration": None,
            "actualDeparture": None,
            "actualArrival": None,
            "estimatedArrival": None,
            "delay": None,
            "vessel": VESSELS.get(vessel_code, {"code": vessel_code}),
            "full": sd.get("vesselFullPercent"),
            "carFull": sd.get("uhFullPercent"),
            "oversizeFull": sd.get("osFullPercent"),
            "from": sd.get("dept", "").lower(),
            "destinations": [sd.get("dest", "").lower()],
            "transferAt": sd.get("transferDept"),
            "warning": sd.get("delayComments") or "",
        }

        try:
            if sd.get("scheduledDeparture"):
                dt = datetime.strptime(sd["scheduledDeparture"], "%Y-%m-%d %H:%M:%S")
                sailing["scheduledDeparture"] = {"hour": dt.hour, "minute": dt.minute}

            if sd.get("scheduledArrival"):
                dt = datetime.strptime(sd["scheduledArrival"], "%Y-%m-%d %H:%M:%S")
                sailing["scheduledArrival"] = {"hour": dt.hour, "minute": dt.minute}

            if sailing["scheduledDeparture"] and sailing["scheduledArrival"]:
                dep_dt = datetime.strptime(sd["scheduledDeparture"], "%Y-%m-%d %H:%M:%S")
                arr_dt = datetime.strptime(sd["scheduledArrival"], "%Y-%m-%d %H:%M:%S")
                diff = arr_dt - dep_dt
                sailing["scheduledDuration"] = {
                    "hour": int(diff.total_seconds()) // 3600,
                    "minute": (int(diff.total_seconds()) % 3600) // 60,
                }
        except ValueError:
            pass

        try:
            if sd.get("actualDepartureTs"):
                dt = datetime.strptime(sd["actualDepartureTs"], "%Y-%m-%d %H:%M:%S")
                sailing["actualDeparture"] = {"hour": dt.hour, "minute": dt.minute}
        except ValueError:
            pass

        try:
            if sd.get("actualArrivalTs"):
                ts = sd["actualArrivalTs"]
                if "ETA" in ts:
                    ts = ts.replace("ETA: ", "")
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                    sailing["estimatedArrival"] = {"hour": dt.hour, "minute": dt.minute}
                else:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                    sailing["actualArrival"] = {"hour": dt.hour, "minute": dt.minute}
        except ValueError:
            pass

        if sd.get("delayMin") and int(sd["delayMin"]) > 0:
            sailing["delay"] = {"hour": 0, "minute": int(sd["delayMin"])}

        for key in ("dest2", "dest3", "dest4", "dest5", "dest6"):
            if sd.get(key):
                sailing["destinations"].append(sd[key].lower())

        sailings.append(sailing)

    cameras = None
    cams = cc_data.get("cams", {}).get(route_key)
    if cams:
        cameras = {
            "outsideTerminal": cams.get("trafficOutsideTerminalCam"),
            "toDestination": cams.get("trafficToDestCam"),
        }

    return sailings, cameras


def parse_cc_tomorrow(route, cc_data):
    """Parse tomorrow's CC API data into sailings list."""
    sailings = []
    if not isinstance(cc_data, list):
        return sailings

    for sd in cc_data:
        vessel_code = sd.get("vessel", "").lower()
        sailing = {
            "realtime": True,
            "scheduledDeparture": None,
            "scheduledArrival": None,
            "scheduledDuration": None,
            "actualDeparture": None,
            "actualArrival": None,
            "estimatedArrival": None,
            "delay": None,
            "vessel": VESSELS.get(vessel_code, {"code": vessel_code}),
            "full": sd.get("vesselFullPercent"),
            "carFull": sd.get("uhFullPercent"),
            "oversizeFull": sd.get("osFullPercent"),
            "from": sd.get("dept", "").lower(),
            "destinations": [sd.get("dest1", "").lower()],
            "transferAt": None,
            "warning": sd.get("departureStatus") or "",
        }

        if sd.get("departure"):
            dt = datetime.strptime(sd["departure"], "%Y-%m-%d %H:%M:%S")
            sailing["scheduledDeparture"] = {"hour": dt.hour, "minute": dt.minute}

        for key in ("dest2", "dest3", "dest4", "dest5", "dest6"):
            if sd.get(key):
                sailing["destinations"].append(sd[key].lower())

        sailings.append(sailing)

    return sailings


def _is_excluded(sailing, target_date):
    """Check if a sailing is excluded on a given date.
    Handles both 'Except on' (blacklist) and 'Only on' (whitelist) annotations."""
    for md in sailing.get("exceptDates", []):
        if md[0] == target_date.month and md[1] == target_date.day:
            return True
    only_dates = sailing.get("onlyOnDates", [])
    if only_dates:
        if not any(md[0] == target_date.month and md[1] == target_date.day for md in only_dates):
            return True
    return False


def _check_schedule_validity(date_range, target_date):
    """Check if a target date falls outside the schedule's date range.
    Returns a warning string if outside, None if within or unknown."""
    if not date_range or not date_range.get("to"):
        return None
    try:
        end = datetime(
            date_range["to"]["year"],
            date_range["to"]["month"],
            date_range["to"]["day"],
            tzinfo=PACIFIC,
        ).date()
        if target_date > end:
            month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            end_str = f"{month_names[date_range['to']['month']]} {date_range['to']['day']}, {date_range['to']['year']}"
            return f"The current schedule ends {end_str}. Sailings shown may not be accurate for this date."
    except (ValueError, KeyError):
        pass
    return None


def _strip_except_text(warning):
    """Remove 'Except on ...' and 'Only on ...' text — only useful for backend
    filtering, not for display to the user."""
    warning = re.sub(r"Except on .+", "", warning, flags=re.IGNORECASE)
    warning = re.sub(r"Only on .+", "", warning, flags=re.IGNORECASE)
    return warning.rstrip(": ").strip()


# Terminals that appear as alternating destinations on multi-stop routes where
# the specific destination is ambiguous from the corridor name alone.
_AMBIGUOUS_TERMINALS = {"tht", "kpr", "alr", "soi"}


def _add_extras_to_messages(sailing):
    """Add capacity and island stops to sailing messages if available."""
    cap = _capacity_text(sailing.get("full"))
    if cap:
        sailing["messages"]["capacity"] = cap

    dests = sailing.get("destinations", [])
    # SGI routes: list all island stops alphabetically
    seen = set()
    unique = []
    for d in dests:
        if d not in seen and d in ISLAND_SHORT_NAMES:
            seen.add(d)
            unique.append(ISLAND_SHORT_NAMES[d])
    if unique:
        sailing["messages"]["stops"] = ", ".join(sorted(unique))
        return

    # Non-SGI multi-stop routes (e.g. Chemainus↔Thetis/Penelakut,
    # Port McNeill↔Alert Bay/Sointula): show which terminal this sailing goes to.
    if dests and dests[0] in _AMBIGUOUS_TERMINALS:
        dest_name = TERMINALS.get(dests[0])
        if dest_name:
            sailing["messages"]["toIsland"] = dest_name


def get_upcoming_sailings(route, limit=7):
    """Get the next `limit` upcoming sailings for a route, combining today + tomorrow."""
    now = datetime.now(PACIFIC)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    result = empty_schedule()

    if route == "sgi-tsa":
        # Special case: merge sailings from all island terminals
        all_today = []
        all_tomorrow = []
        for terminal in SGI_RETURN_TERMINALS:
            url = f"{CC_API}/api/currentconditions/1.0/route/{terminal['from']}/route{terminal['id']}"
            try:
                resp = _new_session().get(url, timeout=15)
                cc_data = resp.json()
            except Exception:
                continue
            # Use a temporary route entry for parsing
            tmp_route = f"{terminal['from']}-tsa"
            tmp_info = {"from": terminal["from"], "to": "tsa", "id": terminal["id"]}
            ROUTES[tmp_route] = tmp_info
            today_s, _ = parse_cc_today(tmp_route, cc_data)
            # Only keep sailings going to TSA
            for s in today_s:
                if "tsa" in s.get("destinations", []):
                    s["from"] = terminal["from"]
                    all_today.append(s)
            # Tomorrow
            tmr_url = f"{CC_API}/api/currentconditions/1.0/sc/route/{terminal['from']}/{terminal['id']}"
            try:
                resp = _new_session().get(tmr_url, timeout=15)
                tmr_data = resp.json()
            except Exception:
                tmr_data = []
            tmr_s = parse_cc_tomorrow(tmp_route, tmr_data)
            for s in tmr_s:
                if "tsa" in s.get("destinations", []):
                    s["from"] = terminal["from"]
                    all_tomorrow.append(s)
            del ROUTES[tmp_route]

        # Sort by departure time and filter upcoming
        for s in sorted(all_today, key=lambda x: (x.get("scheduledDeparture", {}).get("hour", 0), x.get("scheduledDeparture", {}).get("minute", 0))):
            dep = s.get("scheduledDeparture")
            if dep:
                sailing_dt = _sailing_datetime(dep)
                if sailing_dt >= now:
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Today",
                    }
                    _add_extras_to_messages(s)
                    # Add departure island to messages
                    from_name = ISLAND_SHORT_NAMES.get(s["from"])
                    if from_name:
                        s["messages"]["fromIsland"] = from_name
                    result["sailings"][0].append(s)

        if len(result["sailings"][0]) < limit:
            for s in sorted(all_tomorrow, key=lambda x: (x.get("scheduledDeparture", {}).get("hour", 0), x.get("scheduledDeparture", {}).get("minute", 0))):
                dep = s.get("scheduledDeparture")
                if dep:
                    sailing_dt = _sailing_datetime(dep, days_ahead=1)
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Tomorrow",
                    }
                    _add_extras_to_messages(s)
                    from_name = ISLAND_SHORT_NAMES.get(s["from"])
                    if from_name:
                        s["messages"]["fromIsland"] = from_name
                    result["sailings"][0].append(s)

        result["extraCameras"] = SGI_CAMERAS

        # Pull dateRange from one island's seasonal schedule
        sgi_sched = get_seasonal_schedule(f"{SGI_RETURN_TERMINALS[0]['from']}-tsa")
        result["dateRange"] = sgi_sched["dateRange"]

        # If still not enough, fall back to seasonal schedules from each island
        if len(result["sailings"][0]) < limit:
            tomorrow_weekday = (now + timedelta(days=1)).isoweekday()
            if tomorrow_weekday == 8:
                tomorrow_weekday = 7
            island_sailings = []
            for terminal in SGI_RETURN_TERMINALS:
                route_code = f"{terminal['from']}-tsa"
                schedule = get_seasonal_schedule(route_code)
                for s in schedule["sailings"][tomorrow_weekday]:
                    s = copy.deepcopy(s)
                    s["from"] = terminal["from"]
                    island_sailings.append(s)
            island_sailings.sort(key=lambda x: (x.get("scheduledDeparture", {}).get("hour", 0), x.get("scheduledDeparture", {}).get("minute", 0)))
            for s in island_sailings:
                dep = s["scheduledDeparture"]
                if dep:
                    sailing_dt = _sailing_datetime(dep, days_ahead=1)
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Tomorrow",
                    }
                    from_name = ISLAND_SHORT_NAMES.get(s["from"])
                    if from_name:
                        s["messages"]["fromIsland"] = from_name
                    result["sailings"][0].append(s)
                    if len(result["sailings"][0]) >= limit:
                        break

    elif route == "sgi-swb":
        # Special case: merge sailings from individual SGI island terminals to SWB
        all_today = []
        all_tomorrow = []
        for terminal in SWB_SGI_RETURN_TERMINALS:
            url = f"{CC_API}/api/currentconditions/1.0/route/{terminal['from']}/route{terminal['id']}"
            try:
                resp = _new_session().get(url, timeout=15)
                cc_data = resp.json()
            except Exception:
                continue
            tmp_route = f"{terminal['from']}-swb"
            tmp_info = {"from": terminal["from"], "to": "swb", "id": terminal["id"]}
            ROUTES[tmp_route] = tmp_info
            today_s, _ = parse_cc_today(tmp_route, cc_data)
            for s in today_s:
                if "swb" in s.get("destinations", []):
                    s["from"] = terminal["from"]
                    all_today.append(s)
            tmr_url = f"{CC_API}/api/currentconditions/1.0/sc/route/{terminal['from']}/{terminal['id']}"
            try:
                resp = _new_session().get(tmr_url, timeout=15)
                tmr_data = resp.json()
            except Exception:
                tmr_data = []
            tmr_s = parse_cc_tomorrow(tmp_route, tmr_data)
            for s in tmr_s:
                if "swb" in s.get("destinations", []):
                    s["from"] = terminal["from"]
                    all_tomorrow.append(s)
            del ROUTES[tmp_route]

        for s in sorted(all_today, key=lambda x: (x.get("scheduledDeparture", {}).get("hour", 0), x.get("scheduledDeparture", {}).get("minute", 0))):
            dep = s.get("scheduledDeparture")
            if dep:
                sailing_dt = _sailing_datetime(dep)
                if sailing_dt >= now:
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Today",
                    }
                    _add_extras_to_messages(s)
                    from_name = ISLAND_SHORT_NAMES.get(s["from"])
                    if from_name:
                        s["messages"]["fromIsland"] = from_name
                    result["sailings"][0].append(s)

        if len(result["sailings"][0]) < limit:
            for s in sorted(all_tomorrow, key=lambda x: (x.get("scheduledDeparture", {}).get("hour", 0), x.get("scheduledDeparture", {}).get("minute", 0))):
                dep = s.get("scheduledDeparture")
                if dep:
                    sailing_dt = _sailing_datetime(dep, days_ahead=1)
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Tomorrow",
                    }
                    _add_extras_to_messages(s)
                    from_name = ISLAND_SHORT_NAMES.get(s["from"])
                    if from_name:
                        s["messages"]["fromIsland"] = from_name
                    result["sailings"][0].append(s)

        swb_sched = get_seasonal_schedule(f"swb-{SWB_SGI_RETURN_TERMINALS[0]['from']}")
        result["dateRange"] = swb_sched["dateRange"]
        result["extraCameras"] = SGI_CAMERAS

        if len(result["sailings"][0]) < limit:
            tomorrow_weekday = (now + timedelta(days=1)).isoweekday()
            if tomorrow_weekday == 8:
                tomorrow_weekday = 7
            island_sailings = []
            for terminal in SWB_SGI_RETURN_TERMINALS:
                route_code = f"{terminal['from']}-swb"
                schedule = get_seasonal_schedule(route_code)
                for s in schedule["sailings"][tomorrow_weekday]:
                    s = copy.deepcopy(s)
                    s["from"] = terminal["from"]
                    island_sailings.append(s)
            island_sailings.sort(key=lambda x: (x.get("scheduledDeparture", {}).get("hour", 0), x.get("scheduledDeparture", {}).get("minute", 0)))
            for s in island_sailings:
                dep = s["scheduledDeparture"]
                if dep:
                    sailing_dt = _sailing_datetime(dep, days_ahead=1)
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Tomorrow",
                    }
                    from_name = ISLAND_SHORT_NAMES.get(s["from"])
                    if from_name:
                        s["messages"]["fromIsland"] = from_name
                    result["sailings"][0].append(s)
                    if len(result["sailings"][0]) >= limit:
                        break

    elif route in ROUTES:
        # Use real-time CC API
        cc_data = get_current_conditions(route)
        today_sailings, cameras = parse_cc_today(route, cc_data)
        result["terminalCameras"] = cameras
        # SGI combined routes don't have their own seasonal page; use an individual
        # island page for the dateRange only.
        if route in ("tsa-sgi", "swb-sgi"):
            from_code = route.split("-")[0]
            island_code = SWB_SGI_RETURN_TERMINALS[0]["from"] if route == "swb-sgi" else SGI_RETURN_TERMINALS[0]["from"]
            seasonal = get_seasonal_schedule(f"{from_code}-{island_code}")
        else:
            seasonal = get_seasonal_schedule(route)
        result["dateRange"] = seasonal["dateRange"]

        # Filter to upcoming only and add messages
        for s in today_sailings:
            dep = s.get("scheduledDeparture")
            if dep:
                sailing_dt = _sailing_datetime(dep)
                if sailing_dt >= now:
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Today",
                    }
                    _add_extras_to_messages(s)
                    result["sailings"][0].append(s)

        # If CC API returned nothing for today, fall back to seasonal schedule
        if len(result["sailings"][0]) == 0:
            today_weekday = now.isoweekday()
            schedule = get_seasonal_schedule(route)
            day_sailings = schedule["sailings"][today_weekday]
            if not day_sailings:
                for fallback_day in [1, 2, 3, 4, 5, 6, 7]:
                    if schedule["sailings"][fallback_day]:
                        day_sailings = schedule["sailings"][fallback_day]
                        break
            for s in day_sailings:
                dep = s["scheduledDeparture"]
                if dep and not _is_excluded(s, today):
                    sailing_dt = _sailing_datetime(dep)
                    if sailing_dt >= now:
                        s = copy.deepcopy(s)
                        s["messages"] = {
                            "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                            "relativeTime": _relative_time(sailing_dt),
                            "relativeDay": "Today",
                        }
                        result["sailings"][0].append(s)

        # Build a duration lookup from today's sailings so we can fill in
        # duration for tomorrow sailings when the CC tomorrow API lacks it.
        today_duration_by_time = {
            (s["scheduledDeparture"]["hour"], s["scheduledDeparture"]["minute"]): s["scheduledDuration"]
            for s in result["sailings"][0]
            if s.get("scheduledDeparture") and s.get("scheduledDuration")
        }

        # If we need more, get tomorrow from CC API.
        # cc_tmr_times tracks what we've added so we can (a) dedup within the
        # CC tomorrow response itself (the CC API sometimes returns the same
        # departure time twice for multi-stop routes) and (b) avoid re-adding
        # the same time from the seasonal fallback below.  We intentionally do
        # NOT seed it from today's sailings — same clock time on different days
        # is a different sailing.
        cc_tmr_times = set()
        if len(result["sailings"][0]) < limit:
            tomorrow_data = get_tomorrow_conditions(route)
            tomorrow_sailings = parse_cc_tomorrow(route, tomorrow_data)
            for s in tomorrow_sailings:
                dep = s.get("scheduledDeparture")
                if dep:
                    key = (dep["hour"], dep["minute"])
                    if key in cc_tmr_times:
                        continue
                    cc_tmr_times.add(key)
                    # CC tomorrow API has no arrival time; copy duration from
                    # the matching today sailing (same route, same time = same trip).
                    if s.get("scheduledDuration") is None and key in today_duration_by_time:
                        s["scheduledDuration"] = today_duration_by_time[key]
                    sailing_dt = _sailing_datetime(dep, days_ahead=1)
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Tomorrow",
                    }
                    _add_extras_to_messages(s)
                    result["sailings"][0].append(s)

        # If still not enough, fall back to seasonal schedule for tomorrow.
        # Dedup only against CC tomorrow times (not today's), since same
        # clock time on a different day is a distinct sailing.
        if len(result["sailings"][0]) < limit:
            tomorrow_weekday = (now + timedelta(days=1)).isoweekday()
            if tomorrow_weekday == 8:
                tomorrow_weekday = 7
            schedule = get_seasonal_schedule(route)
            tmr_sailings = schedule["sailings"][tomorrow_weekday]
            if not tmr_sailings:
                for fallback_day in [1, 2, 3, 4, 5, 6, 7]:
                    if schedule["sailings"][fallback_day]:
                        tmr_sailings = schedule["sailings"][fallback_day]
                        break
            for s in tmr_sailings:
                dep = s.get("scheduledDeparture")
                if dep and (dep["hour"], dep["minute"]) not in cc_tmr_times and not _is_excluded(s, tomorrow):
                    s = copy.deepcopy(s)
                    sailing_dt = _sailing_datetime(dep, days_ahead=1)
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Tomorrow",
                    }
                    result["sailings"][0].append(s)
                    if len(result["sailings"][0]) >= limit:
                        break

    else:
        # Use seasonal schedule
        schedule = get_seasonal_schedule(route)
        result["dateRange"] = schedule["dateRange"]
        if route in EXTRA_CAMERAS:
            result["terminalCameras"] = EXTRA_CAMERAS[route]

        today_weekday = now.isoweekday()  # 1=Monday, 7=Sunday
        tomorrow_weekday = (now + timedelta(days=1)).isoweekday()
        if tomorrow_weekday == 8:
            tomorrow_weekday = 7

        today_sailings = schedule["sailings"][today_weekday]
        if not today_sailings:
            for fallback_day in [1, 2, 3, 4, 5, 6, 7]:
                if schedule["sailings"][fallback_day]:
                    today_sailings = schedule["sailings"][fallback_day]
                    break
        for s in today_sailings:
            dep = s["scheduledDeparture"]
            if dep and not _is_excluded(s, today):
                sailing_dt = _sailing_datetime(dep)
                if sailing_dt >= now:
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Today",
                    }
                    result["sailings"][0].append(s)

        if len(result["sailings"][0]) < limit:
            tomorrow_sailings = schedule["sailings"][tomorrow_weekday]
            if not tomorrow_sailings:
                for fallback_day in [1, 2, 3, 4, 5, 6, 7]:
                    if schedule["sailings"][fallback_day]:
                        tomorrow_sailings = schedule["sailings"][fallback_day]
                        break
            for s in tomorrow_sailings:
                dep = s["scheduledDeparture"]
                if dep and not _is_excluded(s, tomorrow):
                    sailing_dt = _sailing_datetime(dep, days_ahead=1)
                    s["messages"] = {
                        "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                        "relativeTime": _relative_time(sailing_dt),
                        "relativeDay": "Tomorrow",
                    }
                    result["sailings"][0].append(s)

    result["sailings"][0] = result["sailings"][0][:limit]
    # Strip internal fields and enrich with vessel tracking
    for s in result["sailings"][0]:
        s.pop("exceptDates", None)
        s.pop("onlyOnDates", None)
        s["warning"] = _strip_except_text(s.get("warning", ""))
        # Add live vessel tracking for realtime sailings with a known vessel
        vessel = s.get("vessel")
        if vessel and s.get("realtime"):
            vessel_code = vessel.get("code")
            dests = s.get("destinations", [])
            if vessel_code and dests:
                tracking = get_vessel_tracking(vessel_code, dests[0])
                if tracking:
                    s["vesselTracking"] = tracking
    return result


def get_sailings_for_date(route, days_ahead):
    """Get all sailings for a specific future date using seasonal schedules."""
    now = datetime.now(PACIFIC)
    target_date = now.date() + timedelta(days=days_ahead)
    target_weekday = target_date.isoweekday()
    if target_weekday == 8:
        target_weekday = 7
    result = empty_schedule()

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_label = day_names[target_weekday - 1]

    if route in ("sgi-tsa", "tsa-sgi", "sgi-swb", "swb-sgi"):
        # SGI routes: merge schedules from individual island pages
        if route == "sgi-tsa":
            island_routes = [(t["from"], "tsa") for t in SGI_RETURN_TERMINALS]
        elif route == "tsa-sgi":
            island_routes = [("tsa", t["from"]) for t in SGI_RETURN_TERMINALS]
        elif route == "sgi-swb":
            island_routes = [(t["from"], "swb") for t in SWB_SGI_RETURN_TERMINALS]
        else:  # swb-sgi
            island_routes = [("swb", t["from"]) for t in SWB_SGI_RETURN_TERMINALS]

        island_sailings = []
        sgi_date_range = None
        for from_code, to_code in island_routes:
            route_code = f"{from_code}-{to_code}"
            schedule = get_seasonal_schedule(route_code)
            if not sgi_date_range and schedule.get("dateRange"):
                sgi_date_range = schedule["dateRange"]
            for s in schedule["sailings"][target_weekday]:
                s = copy.deepcopy(s)
                if route in ("sgi-tsa", "sgi-swb"):
                    s["from"] = from_code
                island_sailings.append(s)
        island_sailings.sort(key=lambda x: (x.get("scheduledDeparture", {}).get("hour", 0), x.get("scheduledDeparture", {}).get("minute", 0)))

        # Deduplicate outbound sailings (same ferry appears on multiple island pages)
        seen_times = set()
        for s in island_sailings:
            dep = s["scheduledDeparture"]
            if dep and not _is_excluded(s, target_date):
                time_key = (dep["hour"], dep["minute"])
                if route in ("tsa-sgi", "swb-sgi") and time_key in seen_times:
                    continue
                if route in ("tsa-sgi", "swb-sgi"):
                    seen_times.add(time_key)
                sailing_dt = _sailing_datetime(dep, days_ahead=days_ahead)
                s["messages"] = {
                    "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                    "relativeTime": _relative_time(sailing_dt),
                    "relativeDay": day_label,
                }
                if route in ("sgi-tsa", "sgi-swb"):
                    from_name = ISLAND_SHORT_NAMES.get(s.get("from"))
                    if from_name:
                        s["messages"]["fromIsland"] = from_name
                s.pop("exceptDates", None)
                s.pop("onlyOnDates", None)
                s["warning"] = _strip_except_text(s.get("warning", ""))
                result["sailings"][0].append(s)
        result["extraCameras"] = SGI_CAMERAS
        warning = _check_schedule_validity(sgi_date_range, target_date)
        if warning:
            result["scheduleWarning"] = warning
    else:
        schedule = get_seasonal_schedule(route)
        result["dateRange"] = schedule["dateRange"]
        day_sailings = schedule["sailings"][target_weekday]
        if not day_sailings:
            # Fall back to any weekday that has data (e.g. when schedule only
            # covers the last day or two of a period, Monday may be empty too)
            for fallback_day in [1, 2, 3, 4, 5, 6, 7]:
                if schedule["sailings"][fallback_day]:
                    day_sailings = schedule["sailings"][fallback_day]
                    break
        for s in day_sailings:
            s = copy.deepcopy(s)
            dep = s["scheduledDeparture"]
            if dep and not _is_excluded(s, target_date):
                sailing_dt = _sailing_datetime(dep, days_ahead=days_ahead)
                s["messages"] = {
                    "friendlyTime": _fmt_time(dep["hour"], dep["minute"]),
                    "relativeTime": _relative_time(sailing_dt),
                    "relativeDay": day_label,
                }
                s.pop("exceptDates", None)
                s.pop("onlyOnDates", None)
                s["warning"] = _strip_except_text(s.get("warning", ""))
                result["sailings"][0].append(s)
        warning = _check_schedule_validity(schedule["dateRange"], target_date)
        if warning:
            result["scheduleWarning"] = warning

    return result


def get_schedule(route):
    """Get the full weekly schedule for a route.

    The seasonal timetable is the baseline for all seven days; the
    current-conditions feed then overlays today with live capacity and delays.
    The overlay is conditional because the feed only publishes departures from
    the major terminal of each pair — for the return leg of a minor route it
    returns nothing, and an unconditional overlay would blank out today.
    """
    schedule = get_seasonal_schedule(route)
    weekly = empty_schedule()
    weekly["dateRange"] = schedule.get("dateRange")
    weekly["sailings"] = copy.deepcopy(schedule.get("sailings") or weekly["sailings"])
    weekly["terminalCameras"] = schedule.get("terminalCameras")

    if route in ROUTES:
        cc_data = get_current_conditions(route)
        today_sailings, cameras = parse_cc_today(route, cc_data)
        if cameras:
            weekly["terminalCameras"] = cameras
        if today_sailings:
            for s in today_sailings:
                dep = s.get("scheduledDeparture")
                if dep:
                    s["messages"] = {"friendlyTime": _fmt_time(dep["hour"], dep["minute"])}
            weekly["sailings"][datetime.now(PACIFIC).isoweekday()] = today_sailings

    warning = _check_schedule_validity(
        weekly["dateRange"], datetime.now(PACIFIC).date()
    )
    if warning:
        weekly["scheduleWarning"] = warning
    return weekly


def empty_schedule():
    return {
        "dateRange": None,
        "sailings": [[], [], [], [], [], [], [], []],
        "terminalCameras": None,
    }


# Warmer pacing: one route at a time with a gap between fetches, so filling all
# 60 directions is spread over minutes rather than hammering the source.
WARM_GAP_SEC = 5
WARM_INTERVAL_SEC = 6 * 3600
WARM_RETRY_INTERVAL_SEC = 20 * 60


def warm_schedules():
    """Fetch and persist the seasonal schedule for every route direction.

    Returns the number of directions that have a stored schedule afterwards.
    Without this the store only fills for routes someone happens to visit, so a
    wall that outlasts a restart would leave most of the site empty.
    """
    routes = sorted(ROUTES)
    for route in routes:
        try:
            get_seasonal_schedule(route)
        except Exception as e:
            print(f"[warm] {route}: {e}")
        time.sleep(WARM_GAP_SEC)
    stored = sum(1 for route in routes if store.load(route))
    print(f"[warm] {stored}/{len(routes)} directions have a stored schedule")
    return stored


def _warm_loop():
    while True:
        stored = warm_schedules()
        # Retry sooner while coverage is incomplete — that means the source is
        # walled or flaky, and we want the timetables the moment it recovers.
        complete = stored >= len(ROUTES)
        time.sleep(WARM_INTERVAL_SEC if complete else WARM_RETRY_INTERVAL_SEC)


def start_warmer():
    """Start the schedule warmer in a background daemon thread."""
    t = threading.Thread(target=_warm_loop, daemon=True)
    t.start()
