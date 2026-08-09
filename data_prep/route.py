"""The D line as an ordered list of stations.

A stringline needs a y-axis, and the honest y-axis is distance along the route
rather than station number - it keeps the long express run between 145 St and
59 St looking long, which is exactly the stretch where the D makes up or loses
time.

The canonical order comes from the longest northbound D pattern in the
published schedule. Real trips deviate: some skip stops, some are cut short,
and patterns from 2021 do not always match the 2026 schedule. Anything that
does not appear in the canonical list is dropped and counted rather than
guessed at.
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

# The commute this whole thing exists to measure.
SEGMENT = ("D17N", "D05N")

# About one run in a hundred is physically impossible - stop times that put a
# train across 15 km of track in seconds. The real median works out at 27 km/h
# over this segment and the fastest believable express is nowhere near 60, so
# anything above that ceiling is a feed artefact rather than a fast train.
MAX_SEGMENT_KMH = 60

# Services that share track with the D somewhere between Norwood and Grand St.
OVERLAY_ROUTES = ["B", "F", "M", "A", "C"]


def _read(zf: zipfile.ZipFile, name: str):
    return csv.DictReader(io.TextIOWrapper(zf.open(name), "utf-8-sig"))


def _haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def canonical_stations() -> list[dict]:
    """Northbound D stations in order, with cumulative kilometres."""
    with zipfile.ZipFile(SCHEDULE) as zf:
        stops = {r["stop_id"]: r for r in _read(zf, "stops.txt")}
        trips = {r["trip_id"]: r for r in _read(zf, "trips.txt") if r["route_id"] == "D"}
        sequences = defaultdict(list)
        for row in _read(zf, "stop_times.txt"):
            if row["trip_id"] in trips:
                sequences[row["trip_id"]].append(row)

    northbound = {
        tid: rows
        for tid, rows in sequences.items()
        if rows and rows[0]["stop_id"].endswith("N")
    }
    if not northbound:
        raise RuntimeError("no northbound D pattern found in the schedule")

    longest = max(northbound.values(), key=len)
    longest.sort(key=lambda r: int(r["stop_sequence"]))

    stations, running = [], 0.0
    previous = None
    for row in longest:
        stop_id = row["stop_id"]
        parent = stops.get(stop_id[:-1], stops.get(stop_id))
        if parent is None:
            continue
        point = (float(parent["stop_lat"]), float(parent["stop_lon"]))
        if previous is not None:
            running += _haversine_km(previous, point)
        previous = point
        stations.append({"id": stop_id, "name": parent["stop_name"], "km": round(running, 4)})
    return stations


def station_index(stations: list[dict]) -> dict[str, int]:
    """Map both directions' stop ids onto the northbound running order.

    Southbound trips call at the same platforms under an "S" suffix. Folding
    them onto one axis lets both directions share a chart, which is what makes
    opposing traffic visible.
    """
    index = {}
    for i, station in enumerate(stations):
        base = station["id"][:-1]
        index[base + "N"] = i
        index[base + "S"] = i
    return index


def write_stations(destination: Path) -> list[dict]:
    stations = canonical_stations()
    destination.parent.mkdir(parents=True, exist_ok=True)
    index = station_index(stations)
    km = stations[index[SEGMENT[1]]]["km"] - stations[index[SEGMENT[0]]]["km"]
    payload = {
        "stations": stations,
        "segment": {
            "from": SEGMENT[0],
            "to": SEGMENT[1],
            "from_name": next(s["name"] for s in stations if s["id"] == SEGMENT[0]),
            "to_name": next(s["name"] for s in stations if s["id"] == SEGMENT[1]),
            "km": round(km, 2),
            "min_ride_minutes": round(km / MAX_SEGMENT_KMH * 60, 2),
        },
        "overlay_routes": OVERLAY_ROUTES,
    }
    destination.write_text(json.dumps(payload, indent=1))
    return stations


if __name__ == "__main__":
    stations = write_stations(ROOT / "site" / "data" / "stations.json")
    index = station_index(stations)
    print(f"{len(stations)} stations, {stations[-1]['km']:.1f} km end to end")
    a, b = index[SEGMENT[0]], index[SEGMENT[1]]
    print(f"segment: {stations[a]['name']} -> {stations[b]['name']}")
    print(f"         {stations[b]['km'] - stations[a]['km']:.1f} km, {b - a} stops")
