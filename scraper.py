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
from urllib.parse import unquote, urljoin, urlparse
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


QUEUE_JAR = store.cache_path("queueit.jar")

# The bounce page hands the next URL to the browser as
# document.location.href = decodeURIComponent('...'), which curl will not
# execute. Following it once is what earns the QueueITAccepted cookie.
_QUEUE_REDIRECT_RE = re.compile(r"decodeURIComponent\('([^']+)'\)")

# One shared cookie jar, so curl processes can't interleave writes to it.
_fetch_lock = threading.Lock()

# How long one fetch may take, counting every curl it needs. A person waiting
# on a page gets the short limit; the warmer, which nobody is waiting on, gets
# a long one. On 2026-11-12 (a new season starting the day after Remembrance
# Day) bcferries.com took 42 seconds to start sending TSA-SWB and 83 for
# SWB-TSA, so a single 30-second limit could not refresh the two busiest pages
# at all.
REQUEST_FETCH_TIMEOUT_SEC = 30
WARMER_FETCH_TIMEOUT_SEC = 180
# The least a single curl is given once a fetch has used up its time, so the
# last hop of a nearly finished fetch still gets a try.
MIN_CURL_TIMEOUT_SEC = 1

# The clock a fetch's time budget is measured on. A name of its own so a test
# can stand in a clock that jumps.
_monotonic = time.monotonic

# How long a page request that already has a stored copy waits for the fetch
# lock before serving that copy. The warmer can now hold the lock for minutes,
# and a person should not sit behind it for a refresh they can do without.
REQUEST_LOCK_WAIT_SEC = 5


def _curl_once(url, jar, timeout=REQUEST_FETCH_TIMEOUT_SEC):
    """Single curl fetch through a cookie jar. Returns (body, final_url)."""
    body_file = None
    try:
        fd, body_file = tempfile.mkstemp(prefix="bcf-body-", suffix=".html")
        os.close(fd)
        result = subprocess.run(
            [
                "curl", "-s", "-L",
                "-c", jar, "-b", jar,
                "-H", f"User-Agent: {_UA}",
                "-o", body_file,
                "-w", "%{url_effective}",
                url,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        with open(body_file, encoding="utf-8", errors="replace") as fh:
            return fh.read(), result.stdout.strip()
    finally:
        if body_file:
            try:
                os.unlink(body_file)
            except OSError:
                pass


def _queue_redirect_target(html, final_url):
    """The URL the waiting room's JS would navigate to, or None."""
    m = _QUEUE_REDIRECT_RE.search(html or "")
    if not m:
        return None
    origin = urlparse(final_url)
    if not origin.hostname:
        return None
    return urljoin(f"{origin.scheme}://{origin.hostname}", unquote(m.group(1)))


def _fetch_with_curl(url, timeout=REQUEST_FETCH_TIMEOUT_SEC, lock_wait=None):
    """Fetch a bcferries.com page, passing the Queue-it waiting room if present.

    Returns (body, final_url). The final URL is load-bearing: the waiting room
    answers HTTP 200 with a body that names neither Queue-it nor itself, so the
    off-domain redirect is the only reliable tell that we did not arrive.

    Passing the room takes one extra hop, which curl will not take on its own
    because the waiting room navigates via JavaScript. That hop yields a
    QueueITAccepted cookie, so the jar is persistent and later fetches go
    straight through. A jar whose cookie has gone stale would land us back in
    the room, so that case discards it and retries once from scratch.

    ``timeout`` is the limit for the whole fetch, shared by every curl it
    takes (up to four: the page and the room's hop, twice). Without that, four
    slow curls in a row would hold the lock for four times the limit while
    everyone else waited. ``lock_wait`` is how many seconds to wait for another
    fetch to finish; None waits as long as it takes. When that wait runs out
    nothing is fetched and the result is (None, None), which means "busy", not
    "the source failed".
    """
    if not _fetch_lock.acquire(timeout=-1 if lock_wait is None else lock_wait):
        return None, None
    deadline = _monotonic() + timeout

    def time_left():
        return max(MIN_CURL_TIMEOUT_SEC, deadline - _monotonic())

    try:
        for attempt in (1, 2):
            html, final_url = _curl_once(url, QUEUE_JAR, timeout=time_left())
            if not _detect_interception(html, final_url, None):
                return html, final_url

            hop = _queue_redirect_target(html, final_url)
            if hop:
                html, final_url = _curl_once(hop, QUEUE_JAR, timeout=time_left())
                if not _detect_interception(html, final_url, None):
                    return html, final_url

            if attempt == 1:
                # Stale acceptance cookie, or the room moved us on to a
                # real queue. Start clean before giving up.
                try:
                    os.unlink(QUEUE_JAR)
                except OSError:
                    pass
        return html, final_url
    except Exception as e:
        print(f"Schedule scrape error for {url}: {e}")
        return "", ""
    finally:
        _fetch_lock.release()


CACHE_TTL = 4 * 3600  # 4 hours
# A stored copy whose period has ended is checked again this often instead of
# every four hours. Not on every request: the source can go on publishing an
# ended period for a while, and that must not turn into a fetch per page view.
ENDED_PERIOD_RECHECK_SEC = 15 * 60
# A stored period BC Ferries has posted no timetable for is checked again this
# often, since their page says they are working on it. Shorter than the
# warmer's hourly pace while such a period is held (WARM_UNPOSTED_INTERVAL_SEC),
# so every pass finds the copy due rather than skipping it for another hour.
UNPOSTED_RECHECK_SEC = 45 * 60
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
    """A schedule worth persisting: it knows its period, and it either has
    sailings or is BC Ferries saying in so many words that it has posted none
    for that period. Any other empty page is a failure, so a broken or walled
    page can never cost us a timetable already held."""
    if not schedule or not schedule.get("dateRange"):
        return False
    if schedule.get("notPosted"):
        return True
    return any(schedule.get("sailings") or [])


# BC Ferries' own words on a period it has no timetable for yet, as on the
# Pender Island pages during the Otter Bay berth closure of 2026-11-20 to 24:
# "Seasonal schedules have not been posted for these dates. We're working to
# get them ready, try again soon."
_NOT_POSTED_RE = re.compile(r"schedules have not been posted", re.IGNORECASE)

# The most of a route notice worth keeping. They run to a few sentences; this
# only stops a page that has gone wrong from filling the screen.
NOTICE_MAX_CHARS = 1200


def _clean_text(text):
    """Collapse a page's whitespace and zero-width spaces into plain text."""
    return re.sub(r"\s+", " ", (text or "").replace("​", "")).strip()


def _says_not_posted(direction):
    """True when this part of the page carries BC Ferries' "not posted" line."""
    return direction.find(string=_NOT_POSTED_RE) is not None


def _parse_periods(soup):
    """The periods the page's date picker offers, each with BC Ferries' own
    link to it. The picker is the only place the page names the period after
    the one it shows, and following its link, rather than building one, keeps
    working if BC Ferries changes how those links are put together."""
    picker = soup.select_one("#dateRangeModal")
    if picker is None:
        return []
    periods = []
    for link in picker.select("a[href]"):
        date_range = _parse_date_range(link.get_text(" ", strip=True))
        if date_range is None:
            continue
        periods.append({"dateRange": date_range, "href": link["href"].strip()})
    return periods


def _parse_route_notice(container):
    """BC Ferries' Important box for the route, as {"text", "links"}, or None.

    It sits in the content slot just before the direction's timetable and is
    empty on almost every page; during the Otter Bay closure it explained the
    water taxi. It is free text from their CMS with no class of its own, so
    this is best effort and nothing else depends on it: a box that can't be
    read just isn't shown.
    """
    if container is None:
        return None
    slot = container.find_previous_sibling("section")
    if slot is None:
        return None
    marked = slot.select_one(".bcf-icon-notice-outline") or slot.find("b", string="Important")
    if marked is None:
        return None

    sentences = []
    for paragraph in slot.find_all("p"):
        text = _clean_text(paragraph.get_text())
        link_text = _clean_text("".join(link.get_text() for link in paragraph.find_all("a")))
        # A paragraph that is only a link is kept as the link below, so it
        # isn't read out twice.
        if not text or text == link_text:
            continue
        sentences.append(text)
    text = " ".join(sentences)[:NOTICE_MAX_CHARS]
    if not text:
        return None

    links = []
    for anchor in slot.find_all("a", href=True):
        url = urljoin(BCF_BASE + "/", anchor["href"].strip())
        if urlparse(url).scheme != "https":
            continue
        if any(link["url"] == url for link in links):
            continue
        label = _clean_text(anchor.get_text()) or url
        links.append({"text": label, "url": url})
    return {"text": text, "links": links}


def _spoken_range(date_range):
    """A period the way it reads aloud: "Nov 20 to Nov 24, 2026", with the
    first year too when the period crosses into a new one."""
    start = date_range["from"]
    end = date_range["to"]
    first = f"{MONTH_NAMES[start['month']]} {start['day']}"
    if start["year"] != end["year"]:
        first += f", {start['year']}"
    last = f"{MONTH_NAMES[end['month']]} {end['day']}, {end['year']}"
    return f"{first} to {last}"


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


# "sgi" is a pseudo-terminal covering the Southern Gulf Islands as a group, so
# these four have no seasonal page of their own \u2014 the callers that serve them
# merge the four real island pages instead. Fetching them yields a page with no
# timetable, which would otherwise be recorded as a permanent health failure.
PSEUDO_TERMINAL_ROUTES = frozenset({"sgi-tsa", "tsa-sgi", "sgi-swb", "swb-sgi"})


def _island_route_codes():
    """The per-island slugs that stand in for the pseudo-terminal routes."""
    codes = []
    for terminal in SGI_RETURN_TERMINALS:
        codes += [f"{terminal['from']}-tsa", f"tsa-{terminal['from']}"]
    for terminal in SWB_SGI_RETURN_TERMINALS:
        codes += [f"{terminal['from']}-swb", f"swb-{terminal['from']}"]
    return codes


def warmable_routes():
    """Every route slug with a seasonal page worth keeping on disk."""
    routes = set(ROUTES) - PSEUDO_TERMINAL_ROUTES
    return sorted(routes | set(_island_route_codes()))


# Set when a page request stores a period with no timetable posted, so the
# warmer fetches the period after it now instead of at its next pass.
_warm_now = threading.Event()


def _as_date(day):
    """A {year, month, day} dict from a page, as a date, or None."""
    try:
        return datetime(day["year"], day["month"], day["day"]).date()
    except (KeyError, TypeError, ValueError):
        return None


def _following_period(schedule):
    """The period the date picker lists straight after the one a schedule
    covers, or None."""
    current = schedule.get("dateRange") or {}
    current_end = _as_date(current.get("to"))
    if current_end is None:
        return None
    later = []
    for period in schedule.get("periods") or []:
        start = _as_date(period["dateRange"].get("from"))
        if start is not None and start > current_end:
            later.append((start, period))
    if not later:
        return None
    later.sort(key=lambda pair: pair[0])
    return later[0][1]


def _period_url(route, href):
    """The full address of a date picker link, or None unless it stays on this
    route's own timetable page at bcferries.com."""
    url = urljoin(BCF_BASE + "/", href)
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname != urlparse(BCF_BASE).hostname:
        return None
    if parts.path.rstrip("/").lower() != f"/routes-fares/schedules/seasonal/{route}".lower():
        return None
    return url


def _same_period(held, period):
    """True when ``held`` is the stored copy of the picker's ``period``."""
    return bool(
        held
        and held.get("href") == period["href"]
        and held.get("dateRange") == period["dateRange"]
    )


def _fetch_following_period(route, period):
    """Fetch and read the period after the one a route's page shows, or None.

    Only the warmer calls this: nobody is waiting on it, and a page request
    already waits on one fetch. The page has to come back for the period the
    link names; BC Ferries answering with some other period is not the next
    timetable, however good it looks.
    """
    url = _period_url(route, period["href"])
    if url is None:
        print(
            f"[schedule-next-blocked] {route.upper()}: the date picker's link "
            f"leaves the route's own page, not followed"
        )
        return None
    html, final_url = _fetch_with_curl(url, timeout=WARMER_FETCH_TIMEOUT_SEC)
    soup = BeautifulSoup(html, "html.parser") if html else None
    reason = _detect_interception(html, final_url, soup)
    following = None
    if not reason:
        following = _parse_seasonal_schedule(soup)
        if following.get("dateRange") != period["dateRange"]:
            reason = "page came back for another period"
        elif not _is_usable_schedule(following):
            reason = "page parsed to no sailings"
    if reason:
        print(
            f"[schedule-next-blocked] {route.upper()}: {reason} for "
            f"{_spoken_range(period['dateRange'])}"
        )
        return None
    print(f"[schedule-next] {route.upper()}: {_spoken_range(period['dateRange'])} loaded")
    held = {
        "href": period["href"],
        "fetchedAt": time.time(),
        "dateRange": following["dateRange"],
        "sailings": following["sailings"],
    }
    if following.get("notPosted"):
        held["notPosted"] = True
    if following.get("notice"):
        held["notice"] = following["notice"]
    return held


def _following_timetable(route, schedule, previous, fetch):
    """The period after an unposted one, for storing under "next", or None.

    A copy already held in ``previous`` is kept while the picker still lists
    the same period, so a page request's refresh doesn't drop what the warmer
    fetched. Only with ``fetch`` (the warmer) is the page fetched, when there
    is no such copy or it is older than the cache time; if that fetch fails,
    the copy held is kept.
    """
    period = _following_period(schedule)
    if period is None:
        return None
    held = (previous or {}).get("next")
    if not _same_period(held, period):
        held = None
    if not fetch:
        return held
    if held and time.time() - held.get("fetchedAt", 0) < CACHE_TTL:
        return held
    return _fetch_following_period(route, period) or held


def _following_period_due(schedule):
    """True when a stored unposted period lacks a current copy of the period
    after it."""
    if not schedule.get("notPosted"):
        return False
    period = _following_period(schedule)
    if period is None:
        return False
    held = schedule.get("next")
    if not _same_period(held, period):
        return True
    return time.time() - held.get("fetchedAt", 0) >= CACHE_TTL


def get_seasonal_schedule(route, background=False):
    """Scrape the seasonal schedule page for a route, with caching.

    A stored schedule is only ever replaced by a better one. A fetch that was
    intercepted, or that parsed to nothing, leaves the stored copy alone and is
    reported through the health ledger \u2014 the source going behind a wall must
    never cost us a timetable we already have.

    ``background`` is for the warmer, which nobody is waiting on. It gives a
    slow source minutes instead of seconds, and it tries even when a page
    request has just given up on the same route. It is also the only caller
    that fetches the period after one BC Ferries has posted no timetable for.
    """
    if route in PSEUDO_TERMINAL_ROUTES:
        return empty_schedule()

    cached = store.load(route)
    if cached:
        # A copy whose period is over is not fresh however recently it was
        # fetched. Treating it as fresh for four hours is what left TSA-SWB on
        # a one-day holiday timetable for most of 2026-11-12.
        if store.period_ended(cached[1]):
            fresh_for = ENDED_PERIOD_RECHECK_SEC
        elif cached[1].get("notPosted"):
            # BC Ferries says it is still working on this timetable.
            fresh_for = UNPOSTED_RECHECK_SEC
        else:
            fresh_for = CACHE_TTL
        if (time.time() - cached[0]) < fresh_for:
            if background and _following_period_due(cached[1]):
                return _complete_with_following_period(route, cached)
            return cached[1]

    # The pause after a failure spares a person a 30-second wait against a
    # wall on every request. The warmer has its own pacing and no one waiting,
    # so a page request's failure must not lock it out as well.
    failure = store.last_failure(route)
    if not background and failure and (time.time() - failure["at"]) < FAILURE_RETRY_TTL:
        return cached[1] if cached else empty_schedule()

    url = f"{BCF_BASE}/routes-fares/schedules/seasonal/{route.upper()}"
    if background:
        html, final_url = _fetch_with_curl(url, timeout=WARMER_FETCH_TIMEOUT_SEC)
    else:
        # With a copy in hand, don't queue behind someone else's slow fetch.
        lock_wait = REQUEST_LOCK_WAIT_SEC if cached else None
        html, final_url = _fetch_with_curl(
            url, timeout=REQUEST_FETCH_TIMEOUT_SEC, lock_wait=lock_wait)
        if html is None:
            # Busy, not failed: nothing is recorded against the source.
            return cached[1] if cached else empty_schedule()
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

    already_known = (
        cached
        and cached[1].get("notPosted")
        and cached[1].get("dateRange") == schedule["dateRange"]
    )
    if schedule.get("notPosted") and not already_known:
        print(
            f"[schedule-not-posted] {route.upper()}: BC Ferries has not posted a "
            f"timetable for {_spoken_range(schedule['dateRange'])}; no sailings "
            f"are shown for those dates"
        )

    if schedule.get("notPosted"):
        previous = cached[1] if cached else None
        following = _following_timetable(route, schedule, previous, fetch=background)
        if following:
            schedule["next"] = following
        elif not background:
            _warm_now.set()

    store.save(route, schedule)
    return schedule


def _complete_with_following_period(route, cached):
    """Add the period after an unposted one to a copy that is otherwise fresh.

    A page request stores the unposted period without it and wakes the
    warmer, which lands here. The page itself is not read again, so the copy
    keeps its read time and is re-checked on the usual schedule.
    """
    fetched_at, schedule = cached
    following = _following_timetable(route, schedule, schedule, fetch=True)
    if following is None or following is schedule.get("next"):
        return schedule
    completed = dict(schedule)
    completed["next"] = following
    store.save(route, completed, fetched_at=fetched_at)
    return completed


def _parse_seasonal_schedule(soup):
    """Parse the seasonal schedule HTML into a schedule dict.

    The page carries both directions of the route, each in its own
    ``.seasonalSchedulesContainer``, and the first is the one that was asked
    for. Reading the whole page let the return direction's timetable stand in
    whenever this one had none, which is exactly what happens when BC Ferries
    has posted nothing for these dates. A page without the containers (older
    captures, trimmed fixtures) is read whole, as before.
    """
    schedule = empty_schedule()

    date_range_el = soup.select_one('[href="#dateRangeModal"]')
    if date_range_el:
        schedule["dateRange"] = _parse_date_range(date_range_el.get_text())

    container = soup.select_one(".seasonalSchedulesContainer")
    direction = container if container is not None else soup

    periods = _parse_periods(soup)
    if periods:
        schedule["periods"] = periods

    notice = _parse_route_notice(container)
    if notice:
        schedule["notice"] = notice

    wrapper = direction.select_one(".seasonal-schedule-wrapper")
    table = wrapper.select_one("table") if wrapper else None
    if table:
        _read_timetable(table, schedule["sailings"])

    if not any(schedule["sailings"]) and _says_not_posted(direction):
        schedule["notPosted"] = True

    return schedule


def _read_timetable(table, sailings):
    """Add each sailing in a seasonal timetable table to ``sailings``, which
    holds one list per weekday, Monday at 1."""
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
            warning_header = red_text.get_text(" ", strip=True)
        black_text = cells[1].select_one(".text-black")
        if black_text:
            warning_body = black_text.get_text(" ", strip=True)

        warning = ""
        except_dates = set()
        only_dates = set()
        if warning_header and warning_body:
            warning = f"{warning_header}: {warning_body}"
        elif warning_header:
            warning = warning_header
        else:
            warning = warning_body
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
            "warning": warning,
            "exceptDates": list(except_dates),
            "onlyOnDates": list(only_dates),
        }

        if current_day and current_day <= 7:
            sailings[current_day].append(sailing)


def _cc_warning(sailing, field):
    """The live feed flags dangerous-goods trips separately from delay text."""
    warning = (sailing.get(field) or "").strip()
    if (sailing.get("sailingType") or "").strip().upper() == "DG":
        restriction = "No passengers permitted - dangerous goods sailing."
        return f"{restriction} {warning}".strip()
    return warning


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
            "warning": _cc_warning(sd, "delayComments"),
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
            "warning": _cc_warning(sd, "departureStatus"),
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


def _period_for_date(schedule, target_date):
    """The part of a route's stored timetable that covers ``target_date``, or
    None when nothing does.

    A route holds the period its page showed and, when BC Ferries posted no
    timetable for that one, the period after it under "next". A date from the
    later period's first day on is read from it. A date before the first day of
    the period shown is not covered: showing a timetable early lists sailings
    that may not run yet. A date after the last day keeps the old behaviour,
    the timetable served with a warning that it has ended.
    """
    if not schedule:
        return None
    following = schedule.get("next")
    if following:
        start = _as_date((following.get("dateRange") or {}).get("from"))
        if start is not None and target_date >= start:
            return following
    date_range = schedule.get("dateRange")
    if date_range:
        start = _as_date(date_range.get("from"))
        if start is not None and target_date < start:
            return None
    return schedule


def _sailings_on(schedule, target_date, fall_back=True):
    """Copies of the timetable's sailings for ``target_date``'s weekday, from
    the period that covers that date.

    Copies, because callers decorate each sailing for display, and decorating
    the stored ones in place stripped their "Except on" dates for every later
    reader. ``fall_back`` keeps the old rule for a weekday the period has no
    rows for: use the first weekday that has some. The Gulf Islands merges
    never did that, so they pass False.
    """
    period = _period_for_date(schedule, target_date)
    if period is None or period.get("notPosted"):
        return []
    grid = period.get("sailings") or []
    weekday = target_date.isoweekday()
    day_sailings = grid[weekday] if weekday < len(grid) else []
    if not day_sailings and fall_back:
        for fallback_day in [1, 2, 3, 4, 5, 6, 7]:
            if fallback_day < len(grid) and grid[fallback_day]:
                day_sailings = grid[fallback_day]
                break
    return copy.deepcopy(day_sailings)


def _range_on(schedule, target_date):
    """The dateRange of the period covering ``target_date``, or the one the
    page showed when none does."""
    period = _period_for_date(schedule, target_date)
    if period is None:
        return (schedule or {}).get("dateRange")
    return period.get("dateRange")


def _period_notice(period, where=None):
    """What to tell people about one period of a route, or None.

    That is BC Ferries having posted no timetable for it, and the route's
    Important box when the page had one. ``where`` names the island in the
    Gulf Islands views, where "this route" alone would not say which.
    """
    if not period:
        return None
    parts = []
    if period.get("notPosted") and period.get("dateRange"):
        parts.append(
            f"BC Ferries has not posted a timetable for {_spoken_range(period['dateRange'])}."
        )
    box = period.get("notice") or {}
    if box.get("text"):
        parts.append(box["text"])
    if not parts:
        return None
    text = " ".join(parts)
    if where:
        text = f"{where}: {text}"
    return {"text": text, "links": list(box.get("links") or [])}


def _notices_on(schedule, dates, where=None):
    """The notices for the periods covering ``dates``, each once."""
    notices = []
    for target_date in dates:
        notice = _period_notice(_period_for_date(schedule, target_date), where)
        if notice and notice not in notices:
            notices.append(notice)
    return notices


def _island_routes(route):
    """For a Gulf Islands route, each island's code with the route slug its own
    page is kept under."""
    if route == "sgi-tsa":
        return [(t["from"], f"{t['from']}-tsa") for t in SGI_RETURN_TERMINALS]
    if route == "tsa-sgi":
        return [(t["from"], f"tsa-{t['from']}") for t in SGI_RETURN_TERMINALS]
    if route == "sgi-swb":
        return [(t["from"], f"{t['from']}-swb") for t in SWB_SGI_RETURN_TERMINALS]
    if route == "swb-sgi":
        return [(t["from"], f"swb-{t['from']}") for t in SWB_SGI_RETURN_TERMINALS]
    return []


def _island_notices(route, dates):
    """The notices from each island's page in a Gulf Islands view, each naming
    its island.

    Read from the store, not fetched: the warmer keeps these pages, and the
    live view should not wait on more fetches for a notice.
    """
    notices = []
    for island, island_route in _island_routes(route):
        entry = store.load(island_route)
        if entry is None:
            continue
        where = TERMINALS.get(island, island.upper())
        for notice in _notices_on(entry[1], dates, where):
            if notice not in notices:
                notices.append(notice)
    return notices


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
        result["dateRange"] = _range_on(sgi_sched, today)
        result["notices"] = _island_notices(route, [today, tomorrow])

        # If still not enough, fall back to seasonal schedules from each island
        if len(result["sailings"][0]) < limit:
            island_sailings = []
            for terminal in SGI_RETURN_TERMINALS:
                route_code = f"{terminal['from']}-tsa"
                schedule = get_seasonal_schedule(route_code)
                for s in _sailings_on(schedule, tomorrow, fall_back=False):
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
        result["dateRange"] = _range_on(swb_sched, today)
        result["extraCameras"] = SGI_CAMERAS
        result["notices"] = _island_notices(route, [today, tomorrow])

        if len(result["sailings"][0]) < limit:
            island_sailings = []
            for terminal in SWB_SGI_RETURN_TERMINALS:
                route_code = f"{terminal['from']}-swb"
                schedule = get_seasonal_schedule(route_code)
                for s in _sailings_on(schedule, tomorrow, fall_back=False):
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
        result["dateRange"] = _range_on(seasonal, today)
        if route in ("tsa-sgi", "swb-sgi"):
            result["notices"] = _island_notices(route, [today, tomorrow])
        else:
            result["notices"] = _notices_on(seasonal, [today, tomorrow])

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
            schedule = get_seasonal_schedule(route)
            day_sailings = _sailings_on(schedule, today)
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
            schedule = get_seasonal_schedule(route)
            tmr_sailings = _sailings_on(schedule, tomorrow)
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
        result["dateRange"] = _range_on(schedule, today)
        result["notices"] = _notices_on(schedule, [today, tomorrow])
        if route in EXTRA_CAMERAS:
            result["terminalCameras"] = EXTRA_CAMERAS[route]

        today_sailings = _sailings_on(schedule, today)
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
            tomorrow_sailings = _sailings_on(schedule, tomorrow)
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
        notices = []
        for from_code, to_code in island_routes:
            route_code = f"{from_code}-{to_code}"
            schedule = get_seasonal_schedule(route_code)
            island_range = _range_on(schedule, target_date)
            if not sgi_date_range and island_range:
                sgi_date_range = island_range
            island = from_code if route in ("sgi-tsa", "sgi-swb") else to_code
            where = TERMINALS.get(island, island.upper())
            for notice in _notices_on(schedule, [target_date], where):
                if notice not in notices:
                    notices.append(notice)
            for s in _sailings_on(schedule, target_date, fall_back=False):
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
        result["notices"] = notices
        warning = _check_schedule_validity(sgi_date_range, target_date)
        if warning:
            result["scheduleWarning"] = warning
    else:
        schedule = get_seasonal_schedule(route)
        result["dateRange"] = _range_on(schedule, target_date)
        result["notices"] = _notices_on(schedule, [target_date])
        # Falls back to any weekday that has data (e.g. when schedule only
        # covers the last day or two of a period, Monday may be empty too)
        day_sailings = _sailings_on(schedule, target_date)
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
        warning = _check_schedule_validity(result["dateRange"], target_date)
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
    weekly["notices"] = _notices_on(schedule, [datetime.now(PACIFIC).date()])

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
# While any route holds a period BC Ferries has posted no timetable for. Those
# routes are covered (there is nothing more to fetch), but BC Ferries says the
# timetable is coming, and without this nobody would look for it unless
# someone happened to open the page.
WARM_UNPOSTED_INTERVAL_SEC = 3600


def warm_schedules():
    """Fetch and persist the seasonal schedule for every route direction.

    Returns (current, total), where current counts the directions holding a
    schedule whose period has not ended. Without this the store only fills for
    routes someone happens to visit, so a wall that outlasts a restart would
    leave most of the site empty.
    """
    routes = warmable_routes()
    for route in routes:
        try:
            get_seasonal_schedule(route, background=True)
        except Exception as e:
            print(f"[warm] {route}: {e}")
        time.sleep(WARM_GAP_SEC)
    stored = 0
    current = 0
    for route in routes:
        entry = store.load(route)
        if not entry:
            continue
        stored += 1
        if not store.period_ended(entry[1]):
            current += 1
    waiting = _routes_waiting_for_a_timetable(routes)
    print(
        f"[warm] {stored}/{len(routes)} directions have a stored schedule, "
        f"{current} of them for a period that has not ended, "
        f"{len(waiting)} waiting on BC Ferries to post a timetable"
    )
    return current, len(routes)


def _routes_waiting_for_a_timetable(routes):
    """The routes whose stored period BC Ferries has posted no timetable for."""
    waiting = []
    for route in routes:
        entry = store.load(route)
        if entry and entry[1].get("notPosted"):
            waiting.append(route)
    return waiting


def _next_warm_delay(current, total, waiting=0):
    """Seconds until the next warm pass.

    Retry sooner while coverage is incomplete — that means the source is
    walled or flaky, and we want the timetables the moment it recovers. A
    stored schedule whose period has ended counts as missing: it is the one
    most likely to show a departure that no longer runs. A period with no
    timetable posted counts as covered, but brings the next pass forward to
    the hour, so the timetable is picked up soon after BC Ferries posts it.
    """
    if current < total:
        return WARM_RETRY_INTERVAL_SEC
    if waiting:
        return WARM_UNPOSTED_INTERVAL_SEC
    return WARM_INTERVAL_SEC


def _warm_loop():
    while True:
        current, total = warm_schedules()
        waiting = _routes_waiting_for_a_timetable(warmable_routes())
        # Sleep until the next pass is due, or until a page request has stored
        # a period with no timetable posted and wants the one after it fetched.
        _warm_now.wait(timeout=_next_warm_delay(current, total, len(waiting)))
        _warm_now.clear()


def start_warmer():
    """Start the schedule warmer in a background daemon thread."""
    t = threading.Thread(target=_warm_loop, daemon=True)
    t.start()
