import requests as http_requests
from flask import Flask, jsonify, request, send_from_directory
from scraper import get_upcoming_sailings, get_sailings_for_date, get_schedule
from data import TERMINALS_LIST, ALL_CORRIDORS, REGIONS, VESSELS
import ais

app = Flask(__name__, static_folder="www")

# Start AIS vessel tracking listener
ais.start()


@app.route("/")
def index():
    return send_from_directory("www", "index.html")


@app.route("/terminal/<slug>")
def terminal_page(slug):
    return send_from_directory("www", "terminal.html")


@app.route("/route/<slug>")
def route_page(slug):
    return send_from_directory("www", "route.html")


@app.route("/map/<slug>")
def map_page(slug):
    return send_from_directory("www", "map.html")


@app.route("/css/<path:filename>")
def static_css(filename):
    return send_from_directory("www/css", filename)


@app.route("/js/<path:filename>")
def static_js(filename):
    return send_from_directory("www/js", filename)


@app.route("/api/v3/terminals")
def terminals_list():
    resp = jsonify(TERMINALS_LIST)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/v3/terminals/<slug>/corridors")
def terminal_corridors(slug):
    data = [c for c in ALL_CORRIDORS
            if c["slug"].startswith(slug) or f"-{slug}" in c["slug"]]
    resp = jsonify(data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/v3/regions")
def regions_list():
    resp = jsonify(REGIONS)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/v3/corridors")
def corridors_list():
    resp = jsonify(ALL_CORRIDORS)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/v3/routes/<route>/sailings/upcoming")
def upcoming(route):
    limit = request.args.get("limit", 7, type=int)
    days_ahead = request.args.get("days_ahead", 0, type=int)
    days_ahead = max(0, min(days_ahead, 7))
    if days_ahead >= 1:
        result = get_sailings_for_date(route.lower(), days_ahead)
    else:
        result = get_upcoming_sailings(route.lower(), limit=limit)
    result["sailings"][0] = result["sailings"][0][:limit]
    resp = jsonify(result)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/v3/routes/<route>/sailings/all")
def all_sailings(route):
    result = get_schedule(route.lower())
    resp = jsonify(result)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/v3/camera-time")
def camera_time():
    url = request.args.get("url", "")
    if not url.startswith("https://ccimg.bcferries.com/"):
        return jsonify({"error": "Invalid camera URL"}), 400
    try:
        r = http_requests.head(url, timeout=5)
        last_modified = r.headers.get("Last-Modified", "")
        return jsonify({"lastModified": last_modified})
    except Exception:
        return jsonify({"lastModified": ""}), 200


@app.route("/api/v3/vessels")
def vessels():
    """Return current AIS positions for vessels on given routes."""
    routes = request.args.get("routes", "").split(",")
    seen = set()
    result = []
    for route_code in routes:
        route_code = route_code.strip().lower()
        if not route_code:
            continue
        sailings = get_upcoming_sailings(route_code, limit=10)
        for s in sailings.get("sailings", [[]])[0]:
            vessel = s.get("vessel")
            if not vessel:
                continue
            code = vessel.get("code")
            if not code or code in seen:
                continue
            seen.add(code)
            pos = ais.get_vessel_position(code)
            if pos:
                result.append({
                    "code": code,
                    "name": VESSELS.get(code, {}).get("name", code),
                    "lat": pos["lat"],
                    "lng": pos["lng"],
                    "speed": round(pos["speed"], 1),
                    "heading": pos["heading"],
                })
    resp = jsonify(result)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/v3/routes/<route>/sailings/<weekday>")
def schedule(route, weekday):
    result = get_schedule(route.lower())
    resp = jsonify(result)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    if result["dateRange"] and result["dateRange"]["to"]:
        resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000)
