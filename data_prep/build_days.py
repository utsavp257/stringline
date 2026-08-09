"""Turn a day of observed movements into something a browser can load.

subwaydata.nyc publishes about 16 MB of CSV per day covering the whole system.
The page needs one line's worth, so each day is reduced to two small files:

  <date>.json.gz          the D, roughly 27 KB
  <date>.overlay.json.gz  B, F, M, A and C where they touch the D's stations

Trips are stored as a start time plus offsets, and stations as indices into the
canonical route, which is most of where the size goes. Raw CSVs are deleted
once reduced; they can always be fetched again.

Times come from observed arrivals and departures only. trips.start_time is not
a real timestamp - see the note in fetch_archive - and is never read here.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import tarfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_prep.route import OVERLAY_ROUTES, canonical_stations, station_index

ROOT = Path(__file__).resolve().parent.parent
DAYS = ROOT / "site" / "data" / "days"
URL = "https://subwaydata.nyc/data/subwaydatanyc_{day}_csv.tar.xz"
NEW_YORK = ZoneInfo("America/New_York")
TIMEOUT = 180

STATIONS = canonical_stations()
INDEX = station_index(STATIONS)


def weekdays(first: date, last: date):
    day = first
    while day <= last:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _fetch(day: date) -> dict[str, list[dict]] | None:
    """Download one day and return its two CSVs parsed, or None if missing."""
    try:
        response = requests.get(URL.format(day=day), timeout=TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        print(f"{day}: unavailable ({exc})", file=sys.stderr)
        return None

    tables = {}
    with tarfile.open(fileobj=io.BytesIO(response.content)) as tar:
        for member in tar.getmembers():
            if member.name.endswith("_trips.csv"):
                key = "trips"
            elif member.name.endswith("_stop_times.csv"):
                key = "stop_times"
            else:
                continue
            handle = tar.extractfile(member)
            tables[key] = list(csv.DictReader(io.TextIOWrapper(handle, "utf-8")))
    return tables if "trips" in tables and "stop_times" in tables else None


def _compact(tables, routes: set[str]) -> tuple[list, int]:
    """Reduce to one record per trip, keeping only canonical-route stations."""
    keep = {r["trip_uid"]: r for r in tables["trips"] if r["route_id"] in routes}
    calls = defaultdict(list)
    dropped = 0
    for row in tables["stop_times"]:
        if row["trip_uid"] not in keep:
            continue
        position = INDEX.get(row["stop_id"])
        if position is None:
            dropped += 1
            continue
        when = row["arrival_time"] or row["departure_time"]
        if when:
            calls[row["trip_uid"]].append((position, int(when)))

    trips = []
    for uid, stops in calls.items():
        if len(stops) < 2:
            continue
        stops.sort(key=lambda s: s[1])
        origin = stops[0][1]
        trips.append(
            {
                "r": keep[uid]["route_id"],
                "d": int(keep[uid]["direction_id"]),
                "t": origin,
                "s": [s[0] for s in stops],
                "o": [s[1] - origin for s in stops],
            }
        )
    trips.sort(key=lambda t: t["t"])
    return trips, dropped


def _write(path: Path, payload: dict) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, separators=(",", ":")).encode()
    with gzip.open(path, "wb", compresslevel=9) as handle:
        handle.write(blob)
    return path.stat().st_size


def build_day(day: date, force: bool = False) -> dict | None:
    main = DAYS / f"{day}.json.gz"
    overlay = DAYS / f"{day}.overlay.json.gz"
    if main.exists() and overlay.exists() and not force:
        return {"date": str(day), "cached": True}

    tables = _fetch(day)
    if tables is None:
        return None

    midnight = int(datetime(day.year, day.month, day.day, tzinfo=NEW_YORK).timestamp())

    d_trips, dropped = _compact(tables, {"D"})
    other, _ = _compact(tables, set(OVERLAY_ROUTES))

    _write(main, {"date": str(day), "midnight": midnight, "trips": d_trips})
    _write(overlay, {"date": str(day), "midnight": midnight, "trips": other})

    print(f"{day}: {len(d_trips)} D trips, {len(other)} overlay, {dropped} calls off-route")
    return {"date": str(day), "d_trips": len(d_trips), "overlay_trips": len(other), "dropped": dropped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=date.fromisoformat)
    parser.add_argument("last", type=date.fromisoformat)
    parser.add_argument("--workers", type=int, default=4, help="parallel downloads")
    parser.add_argument("--force", action="store_true", help="rebuild days already on disk")
    args = parser.parse_args()

    wanted = list(weekdays(args.first, args.last))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda d: build_day(d, args.force), wanted))

    built = [r for r in results if r]
    print(f"\n{len(built)} of {len(wanted)} weekdays available")
    return 0


if __name__ == "__main__":
    sys.exit(main())
