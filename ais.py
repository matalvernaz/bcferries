"""AIS vessel tracking via aisstream.io WebSocket.

Runs a background thread that maintains a WebSocket connection to aisstream.io,
filters for BC Ferries vessels by MMSI, and caches the latest position data.
"""

import json
import math
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import websocket

from data import VESSEL_MMSI, TERMINALS

PACIFIC = ZoneInfo("America/Vancouver")

AIS_API_KEY = os.environ.get("AIS_API_KEY", "")

# Reverse lookup: MMSI -> vessel code
_MMSI_TO_CODE = {mmsi: code for code, mmsi in VESSEL_MMSI.items()}

# Latest position per vessel code: {code: {lat, lng, speed, heading, course, timestamp}}
_positions = {}
_lock = threading.Lock()

# Terminal coordinates (lat, lng)
TERMINAL_COORDS = {
    "tsa": (49.0063, -123.1324),
    "swb": (48.6886, -123.4108),
    "hsb": (49.3739, -123.2728),
    "nan": (49.1632, -123.8955),   # Departure Bay
    "duk": (49.1500, -123.8900),   # Duke Point
    "lng": (49.4317, -123.4733),
    "bow": (49.3836, -123.3356),   # Snug Cove
    "ful": (48.7700, -123.4533),   # Fulford Harbour
    "plh": (48.8537, -123.4444),   # Long Harbour (Salt Spring)
    "psb": (48.8868, -123.3194),   # Sturdies Bay (Galiano)
    "pvb": (48.8508, -123.3200),   # Village Bay (Mayne)
    "pob": (48.8155, -123.3048),   # Otter Bay (Pender)
    "cft": (48.8617, -123.6400),   # Crofton
    "ves": (48.8767, -123.5683),   # Vesuvius Bay
    "erl": (49.7472, -124.0069),   # Earls Cove
    "slt": (49.7814, -124.1811),   # Saltery Bay
    "btw": (48.5714, -123.4643),   # Brentwood Bay
    "mil": (48.6339, -123.5344),   # Mill Bay
    "pwr": (49.8375, -124.5250),   # Powell River (Westview)
    "cmx": (49.6911, -124.8783),   # Comox (Little River)
    "tex": (49.6989, -124.5650),   # Texada (Blubber Bay)
    "nah": (49.1683, -123.9267),   # Nanaimo Harbour
    "gab": (49.1833, -123.8700),   # Gabriola (Descanso Bay)
    "chm": (48.9250, -123.7133),   # Chemainus
    "pen": (48.9700, -123.6750),   # Penelakut
    "tht": (48.9850, -123.6717),   # Thetis
    "bky": (49.5197, -124.8336),   # Buckley Bay
    "dnm": (49.5278, -124.8083),   # Denman Island
    "dne": (49.5425, -124.7494),   # Denman Island East (Gravelly Bay)
    "hrn": (49.5303, -124.6706),   # Hornby Island (Shingle Spit)
    "cam": (50.0156, -125.2428),   # Campbell River
    "qdr": (50.0344, -125.2139),   # Quadra Island (Quathiaski Cove)
    "hrb": (50.0925, -125.1728),   # Heriot Bay
    "cor": (50.0622, -124.9900),   # Cortes Island (Whaletown)
    "mcn": (50.5906, -126.9511),   # Port McNeill
    "alr": (50.5889, -126.9286),   # Alert Bay
    "soi": (50.6281, -126.9183),   # Sointula
    "pph": (50.7225, -127.4878),   # Port Hardy (Bear Cove)
    "ppr": (54.3167, -130.3269),   # Prince Rupert
    "psk": (53.2533, -131.9978),   # Skidegate
    "alf": (53.2022, -131.9783),   # Alliford Bay
    "bec": (52.3756, -126.7650),   # Bella Coola
    "sgi": (48.8537, -123.4444),   # Alias for Long Harbour
}


def _haversine(lat1, lng1, lat2, lng2):
    """Distance in nautical miles between two lat/lng points."""
    R_NM = 3440.065  # Earth radius in nautical miles
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R_NM * math.asin(math.sqrt(a))


def get_vessel_position(vessel_code):
    """Get the latest cached position for a vessel, or None."""
    with _lock:
        pos = _positions.get(vessel_code)
        if not pos:
            return None
        # Stale if older than 10 minutes
        if time.time() - pos["timestamp"] > 600:
            return None
        return dict(pos)


def get_vessel_tracking(vessel_code, destination_terminal):
    """Get tracking info for a vessel heading to a destination.
    Returns a dict with descriptive text, ETA minutes, and status, or None."""
    pos = get_vessel_position(vessel_code)
    if not pos:
        return None

    dest_coords = TERMINAL_COORDS.get(destination_terminal)
    if not dest_coords:
        return None

    dist_nm = _haversine(pos["lat"], pos["lng"], dest_coords[0], dest_coords[1])
    speed = pos.get("speed", 0)

    vessel_name = None
    from data import VESSELS
    v = VESSELS.get(vessel_code)
    if v:
        vessel_name = v.get("name", vessel_code)

    # Determine status
    if speed < 1.0:
        # Vessel is stationary — likely in port
        # Check if it's near a known terminal
        nearest_terminal = None
        nearest_dist = float("inf")
        for code, coords in TERMINAL_COORDS.items():
            d = _haversine(pos["lat"], pos["lng"], coords[0], coords[1])
            if d < nearest_dist:
                nearest_dist = d
                nearest_terminal = code

        if nearest_dist < 0.5:  # Less than ~900m — in port
            terminal_name = TERMINALS.get(nearest_terminal, nearest_terminal)
            return {
                "status": "in_port",
                "text": f"{vessel_name} is in port at {terminal_name}",
                "distanceNm": round(dist_nm, 1),
                "speedKnots": round(speed, 1),
                "etaMinutes": None,
            }
        else:
            return {
                "status": "stationary",
                "text": f"{vessel_name} is stationary",
                "distanceNm": round(dist_nm, 1),
                "speedKnots": round(speed, 1),
                "etaMinutes": None,
            }

    # Vessel is moving — calculate ETA
    eta_minutes = int((dist_nm / speed) * 60)

    # Describe position relative to crossing
    if dist_nm < 1.0:
        desc = f"{vessel_name} is arriving at {TERMINALS.get(destination_terminal, destination_terminal)}"
    elif eta_minutes <= 5:
        desc = f"{vessel_name} is about {eta_minutes} minutes from {TERMINALS.get(destination_terminal, destination_terminal)}"
    else:
        desc = f"{vessel_name} is about {eta_minutes} minutes from {TERMINALS.get(destination_terminal, destination_terminal)}"

    return {
        "status": "underway",
        "text": desc,
        "distanceNm": round(dist_nm, 1),
        "speedKnots": round(speed, 1),
        "etaMinutes": eta_minutes,
    }


_msg_count = 0


def _on_message(ws, message):
    """Handle incoming AIS message from aisstream.io."""
    global _msg_count
    try:
        data = json.loads(message)
        msg_type = data.get("MessageType")
        if msg_type != "PositionReport":
            return

        meta = data.get("MetaData", {})
        mmsi = meta.get("MMSI")
        vessel_code = _MMSI_TO_CODE.get(mmsi)
        if not vessel_code:
            return

        report = data.get("Message", {}).get("PositionReport", {})
        lat = meta.get("latitude")
        lng = meta.get("longitude")
        if lat is None or lng is None:
            return

        speed = report.get("Sog", 0)
        with _lock:
            _positions[vessel_code] = {
                "lat": lat,
                "lng": lng,
                "speed": speed,
                "heading": report.get("TrueHeading", 0),
                "course": report.get("Cog", 0),
                "timestamp": time.time(),
            }

        _msg_count += 1
        if _msg_count <= 5 or _msg_count % 50 == 0:
            from data import VESSELS
            name = VESSELS.get(vessel_code, {}).get("name", vessel_code)
            print(f"[ais] #{_msg_count} {name}: {lat:.4f}, {lng:.4f} @ {speed:.1f}kn", flush=True)
    except Exception as e:
        print(f"[ais] Error processing message: {e}", flush=True)


def _on_error(ws, error):
    print(f"[ais] WebSocket error: {error}", flush=True)


def _on_close(ws, close_status_code, close_msg):
    print(f"[ais] WebSocket closed: {close_status_code} {close_msg}", flush=True)


def _on_open(ws):
    """Subscribe to BC Ferries vessel positions on connect."""
    mmsi_list = [str(m) for m in VESSEL_MMSI.values()]
    # aisstream.io format: [[lat_min, lng_min], [lat_max, lng_max]]
    subscribe = {
        "APIKey": AIS_API_KEY,
        "BoundingBoxes": [
            [[48.0, -132.5], [55.0, -123.0]]
        ],
        "FilterMessageTypes": ["PositionReport"],
        "FiltersShipMMSI": mmsi_list,
    }
    ws.send(json.dumps(subscribe))
    print(f"[ais] Subscribed to {len(mmsi_list)} vessels", flush=True)


def _run_ws():
    """Run WebSocket connection with auto-reconnect."""
    while True:
        try:
            ws = websocket.WebSocketApp(
                "wss://stream.aisstream.io/v0/stream",
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"[ais] Connection failed: {e}", flush=True)
        print("[ais] Reconnecting in 10 seconds...", flush=True)
        time.sleep(10)


def start():
    """Start the AIS listener in a background daemon thread."""
    t = threading.Thread(target=_run_ws, daemon=True)
    t.start()
    print("[ais] Background listener started", flush=True)
