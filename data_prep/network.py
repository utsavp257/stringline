"""Every route in the system as an ordered list of stations.

route.py describes the one line this started with. This describes all of them,
because the question "which train is slowest" and the question "what about my
commute" both need every service, not just the D.

For each route and direction the canonical order is the longest pattern in the
published schedule. Real trips deviate constantly - short turns, skip-stops,
diversions - so a trip's stops are matched against the canonical list and
anything unrecognised is dropped and counted.
"""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEDULE = ROOT / "static" / "gtfs_subway.zip"

# Shuttles and skip-stop variants that are not services a rider picks by name.
# The Staten Island Railway goes with them: it is a separate railway with
# stations kilometres apart, so it outruns everything on the list without being
# comparable to any of it.
SKIP_ROUTES = {"FS", "GS", "H", "6X", "7X", "FX", "SI"}


def _read(zf: zipfile.ZipFile, name: str):
    return csv.DictReader(io.TextIOWrapper(zf.open(name), "utf-8-sig"))


def _km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def build_network() -> dict:
    """Canonical stations per route and direction, with cumulative distance."""
    with zipfile.ZipFile(SCHEDULE) as zf:
        stops = {r["stop_id"]: r for r in _read(zf, "stops.txt")}
        routes = {r["route_id"]: r for r in _read(zf, "routes.txt")}
        trips = {r["trip_id"]: r for r in _read(zf, "trips.txt")}
        patterns = defaultdict(list)
        for row in _read(zf, "stop_times.txt"):
            trip = trips.get(row["trip_id"])
            if trip:
                patterns[(trip["route_id"], trip["direction_id"], row["trip_id"])].append(row)

    longest = {}
    for (route, direction, _), rows in patterns.items():
        key = (route, direction)
        if key not in longest or len(rows) > len(longest[key]):
            longest[key] = rows

    network = {}
    for (route, direction), rows in longest.items():
        if route in SKIP_ROUTES:
            continue
        rows = sorted(rows, key=lambda r: int(r["stop_sequence"]))
        stations, running, previous = [], 0.0, None
        for row in rows:
            parent = stops.get(row["stop_id"][:-1]) or stops.get(row["stop_id"])
            if parent is None:
                continue
            point = (float(parent["stop_lat"]), float(parent["stop_lon"]))
            if previous is not None:
                running += _km(previous, point)
            previous = point
            stations.append({"id": row["stop_id"], "name": parent["stop_name"], "km": round(running, 3)})
        if len(stations) < 5:
            continue
        network.setdefault(route, {})[direction] = {
            "stations": stations,
            "km": round(running, 2),
            "long_name": routes.get(route, {}).get("route_long_name", route),
            "colour": routes.get(route, {}).get("route_color", ""),
        }

    return {r: d for r, d in network.items() if len(d) == 2}


def stop_lookup(network: dict) -> dict:
    """(route, direction, stop_id) -> position in that pattern.

    Both platform suffixes fold onto the same index so a trip is matched
    whichever way its stop ids are written.
    """
    lookup = {}
    for route, directions in network.items():
        for direction, info in directions.items():
            for i, station in enumerate(info["stations"]):
                base = station["id"][:-1]
                lookup[(route, direction, base + "N")] = i
                lookup[(route, direction, base + "S")] = i
    return lookup


if __name__ == "__main__":
    network = build_network()
    pairs = 0
    meta = {}
    for route, directions in sorted(network.items()):
        n = len(directions["0"]["stations"])
        pairs += sum(len(d["stations"]) * (len(d["stations"]) - 1) // 2 for d in directions.values())
        print(f"{route:>3}  {n:3} stations  {directions['0']['km']:5.1f} km  {directions['0']['long_name']}")
        meta[route] = {
            "long_name": directions["0"]["long_name"],
            "colour": "#" + (directions["0"]["colour"] or "6d6e71"),
            "km": directions["0"]["km"],
            "stations": n,
            "terminals": [
                directions["0"]["stations"][0]["name"],
                directions["0"]["stations"][-1]["name"],
            ],
        }
    print(f"\n{len(network)} routes, {pairs} ordered station pairs")
    # Only the small parts. The station lists live in the per-route files, which
    # the page fetches one at a time.
    (ROOT / "site" / "data" / "routes_meta.json").write_text(json.dumps(meta, indent=1))
