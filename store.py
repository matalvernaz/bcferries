"""Disk-backed persistence for scraped seasonal schedules.

The seasonal schedule pages on bcferries.com sit behind a Queue-it virtual
waiting room during holiday surges: the wall answers HTTP 200 with a JS bounce
page, so a naive scrape "succeeds" and yields nothing. With an in-memory cache
only, a container restart during a wall leaves the app with no timetables at
all and no way to get them back until the wall lifts.

Published timetables change roughly four times a year, so a long-lived on-disk
copy is a legitimate fallback rather than a hack: serving last week's timetable
for a route whose schedule period is still current is correct, and serving one
whose period has expired is handled by the caller's date-range warning.

The store degrades to memory-only if its directory is not writable, so the app
still runs without the volume mounted.
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Vancouver")

CACHE_DIR = os.environ.get("BCF_CACHE_DIR", "/app/data/schedules")

# A schedule this old is still served, but the health endpoint reports it as
# stale so the alerting path can fire. Two days covers an overnight wall plus
# the warmer's retry cycle without crying wolf.
STALE_AFTER = 2 * 24 * 3600

_lock = threading.Lock()
_memory = {}
_failures = {}
_writable = None


def _ensure_dir():
    """Create the cache directory if possible. Returns True when writable."""
    global _writable
    if _writable is not None:
        return _writable
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        probe = os.path.join(CACHE_DIR, ".write-probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.unlink(probe)
        _writable = True
    except OSError as e:
        print(f"[store] cache dir {CACHE_DIR} not writable ({e}); memory-only")
        _writable = False
    return _writable


def _path(route):
    return os.path.join(CACHE_DIR, f"{route}.json")


def load(route):
    """Return (fetched_at, schedule) for a route, or None if never stored."""
    with _lock:
        entry = _memory.get(route)
    if entry:
        return entry
    if not _ensure_dir():
        return None
    try:
        with open(_path(route)) as fh:
            blob = json.load(fh)
        entry = (blob["fetched_at"], blob["schedule"])
    except (OSError, ValueError, KeyError):
        return None
    with _lock:
        _memory[route] = entry
    return entry


def save(route, schedule):
    """Persist a schedule for a route. Callers must only pass a validated one."""
    fetched_at = time.time()
    with _lock:
        _memory[route] = (fetched_at, schedule)
        _failures.pop(route, None)
    if not _ensure_dir():
        return
    blob = {"fetched_at": fetched_at, "route": route, "schedule": schedule}
    try:
        # Write-then-rename so a crash mid-write can't leave a truncated file
        # that would then be treated as "never stored".
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=f".{route}-", suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(blob, fh)
        os.replace(tmp, _path(route))
    except OSError as e:
        print(f"[store] write failed for {route}: {e}")


def note_failure(route, cause):
    """Record why the last refresh attempt for a route did not yield data."""
    with _lock:
        _failures[route] = {"at": time.time(), "cause": cause}


def last_failure(route):
    with _lock:
        return _failures.get(route)


def _period_expired(date_range):
    """True when a schedule's published period has already ended.

    Fetch age alone is not enough: a stored copy pulled minutes ago can still
    describe a period that ended months back, which is the case most likely to
    show a departure time that no longer runs.
    """
    if not date_range or not date_range.get("to"):
        return None
    try:
        end = date_range["to"]
        return datetime.now(PACIFIC).date() > datetime(
            end["year"], end["month"], end["day"], tzinfo=PACIFIC
        ).date()
    except (KeyError, TypeError, ValueError):
        return None


def health():
    """Per-route freshness ledger for the /health/schedules endpoint."""
    if _ensure_dir():
        try:
            for name in os.listdir(CACHE_DIR):
                if name.endswith(".json"):
                    load(name[: -len(".json")])
        except OSError:
            pass
    now = time.time()
    routes = {}
    with _lock:
        known = set(_memory) | set(_failures)
        for route in sorted(known):
            entry = _memory.get(route)
            failure = _failures.get(route)
            age = None if entry is None else now - entry[0]
            date_range = None if entry is None else entry[1].get("dateRange")
            routes[route] = {
                "hasSchedule": entry is not None,
                "ageSeconds": None if age is None else int(age),
                "stale": entry is None or age > STALE_AFTER,
                "periodExpired": _period_expired(date_range),
                "dateRange": date_range,
                "lastFailureCause": None if failure is None else failure["cause"],
                "lastFailureAgeSeconds": (
                    None if failure is None else int(now - failure["at"])
                ),
            }
    degraded = sorted(
        r for r, v in routes.items() if v["stale"] or v["periodExpired"]
    )
    return {
        "cacheDir": CACHE_DIR,
        "cacheWritable": bool(_writable),
        "staleAfterSeconds": STALE_AFTER,
        "routesKnown": len(routes),
        "routesDegraded": degraded,
        "healthy": not degraded and bool(routes),
        "routes": routes,
    }
