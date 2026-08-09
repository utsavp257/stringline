"""Ride times for every station pair on every line.

Two questions need this. "Which train is slowest" needs every service measured
the same way. "What about my commute" needs any pair of stations a person might
actually travel between, not just the one this project started with.

Ride times are counted into per-pair histograms rather than kept as samples.
There are roughly 2.5 million pair observations in a single day and 435 days to
get through; histograms make that fit in memory and make percentiles exact to
the minute, which is finer than anyone needs.

A note on what is measured. This is the ride - doors closing at A to doors
opening at B. It is deliberately not the whole journey, because waiting is a
property of the station and the hour, not of the pair, and percentiles do not
add: the 90th percentile of a wait plus the 90th percentile of a ride is not
the 90th percentile of the two combined, it is worse than either. So waits are
measured separately per station and shown separately.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import tarfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_prep.network import build_network, stop_lookup

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data"
URL = "https://subwaydata.nyc/data/subwaydatanyc_{day}_csv.tar.xz"
TIMEOUT = 180
NEW_YORK = ZoneInfo("America/New_York")

# Blocks a rider would recognise, not equal slices of the clock.
BLOCKS = [
    ("early", 0, 7 * 60),
    ("am", 7 * 60, 10 * 60),
    ("midday", 10 * 60, 16 * 60),
    ("pm", 16 * 60, 19 * 60),
    ("evening", 19 * 60, 24 * 60),
]
BLOCK_INDEX = {name: i for i, (name, _, _) in enumerate(BLOCKS)}
MAX_RIDE = 180          # minutes; one bin each, anything longer lands in the last
MAX_SPEED_KMH = 60      # above this is a feed artefact, not a train
MIN_SAMPLES = 30        # below this a pair is not reported

NETWORK = build_network()
LOOKUP = stop_lookup(NETWORK)


def block_of(minute: int) -> int:
    for i, (_, lo, hi) in enumerate(BLOCKS):
        if lo <= minute < hi:
            return i
    return len(BLOCKS) - 1


class PairIndex:
    """Flat numbering for every (route, direction, from, to) pair."""

    def __init__(self, network):
        self.offset = {}
        self.meta = []
        total = 0
        for route in sorted(network):
            for direction in sorted(network[route]):
                n = len(network[route][direction]["stations"])
                self.offset[(route, direction)] = (total, n)
                total += n * n
        self.size = total

    def flat(self, route, direction, i, j):
        base, n = self.offset[(route, direction)]
        return base + i * n + j


PAIRS = PairIndex(NETWORK)


def fetch(day: date):
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
            tables[key] = list(csv.DictReader(io.TextIOWrapper(tar.extractfile(member), "utf-8")))
    return tables if len(tables) == 2 else None


SPEED_BINS = 120  # 0.5 km/h each, so 0 to 60
MAX_WAIT = 91     # minutes between trains; longer than this is a service gap, not a wait


class StationIndex:
    """Flat numbering for every (route, direction, position) platform."""

    def __init__(self, network):
        self.offset = {}
        total = 0
        for route in sorted(network):
            for direction in sorted(network[route]):
                n = len(network[route][direction]["stations"])
                self.offset[(route, direction)] = total
                total += n
        self.size = total

    def flat(self, route, direction, position):
        return self.offset[(route, direction)] + position


PLATFORMS = StationIndex(NETWORK)


def accumulate(day: date, tables, rides, waits, runs, speeds):
    """Fold one day into the running histograms."""
    trips = {r["trip_uid"]: r for r in tables["trips"] if r["route_id"] in NETWORK}
    calls = defaultdict(list)
    for row in tables["stop_times"]:
        trip = trips.get(row["trip_uid"])
        if trip is None:
            continue
        position = LOOKUP.get((trip["route_id"], trip["direction_id"], row["stop_id"]))
        if position is None:
            continue
        when = row["arrival_time"] or row["departure_time"]
        if when:
            calls[row["trip_uid"]].append((position, int(when)))

    # Local midnight, not UTC. Deriving it from the timestamps themselves lands
    # on UTC midnight and shifts every part of the day by the New York offset,
    # which quietly turns "morning rush" into four in the morning.
    midnight = int(datetime(day.year, day.month, day.day, tzinfo=NEW_YORK).timestamp())
    departures = defaultdict(list)

    for uid, stops in calls.items():
        if len(stops) < 2:
            continue
        trip = trips[uid]
        route, direction = trip["route_id"], trip["direction_id"]
        stations = NETWORK[route][direction]["stations"]
        stops.sort(key=lambda s: s[1])

        positions = np.fromiter((s[0] for s in stops), dtype=np.int32)
        seconds = np.fromiter((s[1] for s in stops), dtype=np.int64)

        # Every ordered pair the trip actually served, at once.
        upper = np.triu_indices(len(positions), k=1)
        i, j = positions[upper[0]], positions[upper[1]]
        ride = (seconds[upper[1]] - seconds[upper[0]]) / 60.0

        forward = j > i
        if not forward.any():
            continue
        i, j, ride = i[forward], j[forward], ride[forward]

        km = np.fromiter((stations[b]["km"] - stations[a]["km"] for a, b in zip(i, j)), dtype=np.float64)
        plausible = (ride > 0) & (km / np.maximum(ride / 60.0, 1e-9) <= MAX_SPEED_KMH)
        i, j, ride = i[plausible], j[plausible], ride[plausible]
        if len(i) == 0:
            continue

        start_minutes = ((seconds[upper[0]][forward][plausible] - midnight) // 60) % 1440
        blocks = np.fromiter((block_of(int(m)) for m in start_minutes), dtype=np.int32)

        base, n = PAIRS.offset[(route, direction)]
        flat = base + i * n + j
        bins = np.minimum(ride.astype(np.int32), MAX_RIDE - 1)
        np.add.at(rides, (flat, blocks, bins), 1)

        # How fast this train actually moved over whatever it ran. Using only
        # end-to-end trips would ignore every short turn, and on some routes
        # that is most of the service.
        span_km = stations[int(positions.max())]["km"] - stations[int(positions.min())]["km"]
        span_hours = (seconds.max() - seconds.min()) / 3600.0
        if span_km > 3 and span_hours > 0:
            kmh = span_km / span_hours
            if 0 < kmh <= MAX_SPEED_KMH:
                speeds[route][min(int(kmh * 2), SPEED_BINS - 1)] += 1

        runs[(route, direction)] += 1
        for position, second in stops:
            departures[(route, direction, position)].append((second - midnight) // 60)

    # Headways: the gap between one train and the next at a platform. Counted
    # into a fixed histogram rather than collected, because keeping every gap
    # across 435 days is tens of millions of integers that never get freed.
    for (route, direction, position), minutes in departures.items():
        minutes.sort()
        flat = PLATFORMS.flat(route, direction, position)
        for a, b in zip(minutes, minutes[1:]):
            gap = b - a
            if 0 < gap < MAX_WAIT:
                waits[flat, block_of(a % 1440), gap] += 1


def percentiles_from_histogram(counts: np.ndarray, wanted=(50, 90)) -> dict:
    total = counts.sum()
    if total < MIN_SAMPLES:
        return {}
    cumulative = np.cumsum(counts)
    out = {"n": int(total)}
    for p in wanted:
        out[f"p{p}"] = int(np.searchsorted(cumulative, total * p / 100.0))
    return out


def median_wait(waits, flat: int, block: int) -> int | None:
    counts = waits[flat, block]
    total = int(counts.sum())
    if total < MIN_SAMPLES:
        return None
    return int(np.searchsorted(np.cumsum(counts), total * 0.5))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ranges", nargs="+",
        help="date ranges as FIRST:LAST, e.g. 2025-09-01:2025-12-31. Several accumulate together.")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    spans = []
    for span in args.ranges:
        first, _, last = span.partition(":")
        spans.append((date.fromisoformat(first), date.fromisoformat(last or first)))

    rides = np.zeros((PAIRS.size, len(BLOCKS), MAX_RIDE), dtype=np.int32)
    waits = np.zeros((PLATFORMS.size, len(BLOCKS), MAX_WAIT), dtype=np.int32)
    runs = defaultdict(int)
    speeds = defaultdict(lambda: np.zeros(SPEED_BINS, dtype=np.int64))

    days = []
    for first, last in spans:
        day = first
        while day <= last:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
    days.sort()

    print(f"{len(days)} weekdays, {PAIRS.size:,} pair slots, "
          f"{rides.nbytes / 1e6:.0f} MB of histogram", flush=True)

    # Downloads run in parallel, but only a chunk at a time. A day parses to a
    # few hundred megabytes of Python objects, so queuing all of them at once
    # and holding the results costs more memory than the machine has.
    done = 0
    chunk = max(args.workers * 2, 4)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for start in range(0, len(days), chunk):
            batch = days[start:start + chunk]
            for day, tables in zip(batch, pool.map(fetch, batch)):
                if tables is None:
                    continue
                accumulate(day, tables, rides, waits, runs, speeds)
                del tables
                done += 1
            print(f"  {done}/{len(days)} days", flush=True)

    write_outputs(rides, waits, runs, speeds, days, done)
    return 0


def write_outputs(rides, waits, runs, speeds, days, days_used):
    (OUT / "routes").mkdir(parents=True, exist_ok=True)
    summary = []

    for route in sorted(NETWORK):
        payload = {"route": route, "directions": {}}
        for direction in sorted(NETWORK[route]):
            stations = NETWORK[route][direction]["stations"]
            base, n = PAIRS.offset[(route, direction)]
            pairs = {}
            for i in range(n):
                for j in range(i + 1, n):
                    flat = base + i * n + j
                    blocks = {}
                    for name, index in BLOCK_INDEX.items():
                        stats = percentiles_from_histogram(rides[flat, index])
                        if stats:
                            blocks[name] = stats
                    if blocks:
                        pairs[f"{i}-{j}"] = blocks
            payload["directions"][direction] = {
                "stations": [{"id": s["id"], "name": s["name"], "km": s["km"]} for s in stations],
                "km": NETWORK[route][direction]["km"],
                "long_name": NETWORK[route][direction]["long_name"],
                "pairs": pairs,
                "waits": {
                    f"{position}": headways
                    for position in range(n)
                    if (headways := {
                        name: median
                        for name, index in BLOCK_INDEX.items()
                        if (median := median_wait(waits, PLATFORMS.flat(route, direction, position), index))
                    })
                },
            }
        (OUT / "routes" / f"{route}.json").write_text(json.dumps(payload, separators=(",", ":")))

        # Speed measured across every run the service made, short turns included.
        direction = sorted(NETWORK[route])[0]
        n = PAIRS.offset[(route, direction)][1]
        km = NETWORK[route][direction]["km"]
        histogram = speeds[route]
        total = int(histogram.sum())
        if total >= MIN_SAMPLES:
            cumulative = np.cumsum(histogram)
            median_kmh = float(np.searchsorted(cumulative, total * 0.5)) / 2
            slow_kmh = float(np.searchsorted(cumulative, total * 0.1)) / 2
            summary.append({
                "route": route,
                "long_name": NETWORK[route][direction]["long_name"],
                "km": km,
                "stations": n,
                "kmh": round(median_kmh, 1),
                "kmh_slow_tenth": round(slow_kmh, 1),
                "end_to_end_minutes": round(km / median_kmh * 60) if median_kmh else None,
                "runs": total,
            })

    summary.sort(key=lambda r: r["kmh"])
    (OUT / "system.json").write_text(json.dumps({
        "days_used": days_used,
        "first": str(days[0]),
        "last": str(days[-1]),
        "blocks": [{"id": b[0], "from": b[1], "to": b[2]} for b in BLOCKS],
        "routes": summary,
    }, indent=1))

    print(f"\nwrote {len(summary)} route files from {days_used} days")
    for r in summary[:6]:
        print(f"  {r['route']:>3}  {r['kmh']:5.1f} km/h  {r['end_to_end_minutes']:3} min end to end  "
              f"({r['runs']:,} runs)")


if __name__ == "__main__":
    sys.exit(main())
