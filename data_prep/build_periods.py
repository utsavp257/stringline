"""Aggregate days into the numbers the page actually answers with.

The question is "what time do I have to leave", and the honest answer is not
ride time. A rider does not board the median train; they show up at a platform
and wait for whatever comes. So for every minute of every day this walks
forward from the platform: if you reach 34 St at 8:07, which train do you
catch, and when does it put you at Fordham Rd?

That makes the wait and the ride one number, which is the number a person
actually experiences, and it means a thinned-out service shows up as lateness
even when every train that does run is on time.

Days with sharply reduced service - holidays, mostly - are detected by trip
count rather than by a hardcoded calendar, and excluded from the bands. They
stay available as individual days.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_prep.route import MAX_SEGMENT_KMH, SEGMENT, canonical_stations, station_index

ROOT = Path(__file__).resolve().parent.parent
DAYS = ROOT / "site" / "data" / "days"
PERIODS = ROOT / "site" / "data" / "periods"
NEW_YORK = ZoneInfo("America/New_York")

STATIONS = canonical_stations()
INDEX = station_index(STATIONS)
FROM_STOP = INDEX[SEGMENT[0]]
TO_STOP = INDEX[SEGMENT[1]]
SEGMENT_KM = STATIONS[TO_STOP]["km"] - STATIONS[FROM_STOP]["km"]
MIN_RIDE_MINUTES = SEGMENT_KM / MAX_SEGMENT_KMH * 60

NORTHBOUND = 0
# Before six the D runs every twenty-odd minutes and the journey is mostly
# waiting for the first train - true, but it swamps the scale for every hour
# anyone actually travels in.
FIRST_MINUTE = 6 * 60      # 06:00
LAST_MINUTE = 23 * 60 + 59  # 23:59
PERCENTILES = [10, 25, 50, 75, 90]
GIVE_UP_AFTER = 120  # minutes; beyond this we call it no service rather than a wait
BUCKET = 5           # minutes; one bucket is one "roughly when I leave"
MIN_DAYS = 5         # below this a bucket renders as absent, not as a thin confident line


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile. Small samples make interpolation dishonest."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered) + 0.5) - 1))
    return ordered[k]


def load_day(day: str) -> dict | None:
    path = DAYS / f"{day}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt") as handle:
        return json.load(handle)


def segment_runs(payload: dict) -> tuple[list[tuple[int, int]], int]:
    """Every northbound trip that served both ends, as (depart, arrive) minutes.

    Minutes are local wall-clock minutes past midnight, so an 8:15 train is at
    8:15 on both sides of the November clock change.

    Returns the runs and the number rejected as physically impossible.
    """
    midnight = payload["midnight"]
    runs, rejected = [], 0
    for trip in payload["trips"]:
        if trip["d"] != NORTHBOUND:
            continue
        stops = trip["s"]
        try:
            a = stops.index(FROM_STOP)
            b = stops.index(TO_STOP)
        except ValueError:
            continue
        if b <= a:
            continue
        depart = trip["t"] + trip["o"][a]
        arrive = trip["t"] + trip["o"][b]
        if (arrive - depart) / 60 < MIN_RIDE_MINUTES:
            rejected += 1
            continue
        runs.append(((depart - midnight) // 60, (arrive - midnight) // 60))
    runs.sort()
    return runs, rejected


def journeys(runs: list[tuple[int, int]]) -> dict[int, int]:
    """For each minute you could arrive at the platform, the total journey."""
    result = {}
    if not runs:
        return result
    i = 0
    for minute in range(FIRST_MINUTE, LAST_MINUTE + 1):
        while i < len(runs) and runs[i][0] < minute:
            i += 1
        if i >= len(runs):
            break
        depart, arrive = runs[i]
        if depart - minute > GIVE_UP_AFTER:
            continue
        result[minute] = arrive - minute
    return result


def summarise_day(day: str) -> dict | None:
    payload = load_day(day)
    if payload is None:
        return None
    runs, rejected = segment_runs(payload)
    walk = journeys(runs)
    if not walk:
        return None
    values = list(walk.values())
    rush = [v for m, v in walk.items() if 7 * 60 <= m <= 10 * 60]
    return {
        "date": day,
        "trips": len(payload["trips"]),
        "segment_runs": len(runs),
        "rejected_runs": rejected,
        "median": round(statistics.median(values), 1),
        "p90": round(percentile(values, 90), 1),
        "rush_median": round(statistics.median(rush), 1) if rush else None,
        "rush_worst": max(rush) if rush else None,
        "_journeys": walk,
    }


def build_period(period_id: str, label: str, days: list[str], note: str = "") -> dict | None:
    summaries = [s for s in (summarise_day(d) for d in days) if s]
    if not summaries:
        return None

    # Holidays run a thinner D than a school day. On a sampled term the three
    # lowest days were Thanksgiving, Labor Day and Christmas, all near 75% of a
    # normal day's service, so the cut sits at a fifth below typical.
    typical = statistics.median([s["segment_runs"] for s in summaries])
    for s in summaries:
        s["reduced_service"] = s["segment_runs"] < 0.8 * typical

    included = [s for s in summaries if not s["reduced_service"]]
    if not included:
        included = summaries

    # Percentiles per single minute are too jumpy to read, and a rider does not
    # aim for a minute anyway. Bucketing to five collapses the noise and asks a
    # question people actually ask: leave around twenty past, not at 8:23:00.
    by_bucket = defaultdict(list)
    days_per_bucket = defaultdict(set)
    for s in included:
        for minute, value in s["_journeys"].items():
            bucket = minute - minute % BUCKET
            by_bucket[bucket].append(value)
            days_per_bucket[bucket].add(s["date"])

    curve = []
    for bucket in range(FIRST_MINUTE, LAST_MINUTE + 1, BUCKET):
        values = by_bucket.get(bucket, [])
        if len(days_per_bucket[bucket]) < MIN_DAYS:
            continue
        entry = {"m": bucket, "n": len(days_per_bucket[bucket])}
        for p in PERCENTILES:
            entry[f"p{p}"] = round(percentile(values, p), 1)
        curve.append(entry)

    everything = [v for s in included for v in s["_journeys"].values()]
    for s in summaries:
        s.pop("_journeys", None)

    return {
        "id": period_id,
        "label": label,
        "note": note,
        "days": summaries,
        "n_days": len(summaries),
        "n_days_included": len(included),
        "n_days_reduced": sum(1 for s in summaries if s["reduced_service"]),
        "n_runs": sum(s["segment_runs"] for s in summaries),
        "n_runs_rejected": sum(s["rejected_runs"] for s in summaries),
        "curve": curve,
        "overall": {f"p{p}": round(percentile(everything, p), 1) for p in PERCENTILES},
    }


def discover_days() -> list[str]:
    return sorted(p.name.removesuffix(".json.gz") for p in DAYS.glob("*.json.gz") if ".overlay" not in p.name)


def fall_terms(days: list[str]) -> list[tuple[str, str, list[str]]]:
    grouped = defaultdict(list)
    for day in days:
        year, month = int(day[:4]), int(day[5:7])
        if 9 <= month <= 12:
            grouped[year].append(day)
    return [(f"fall-{y}", f"Fall {y}", sorted(grouped[y])) for y in sorted(grouped)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "site" / "data")
    args = parser.parse_args()

    available = discover_days()
    if not available:
        print("no days built yet", file=sys.stderr)
        return 1

    PERIODS.mkdir(parents=True, exist_ok=True)
    terms = fall_terms(available)
    written = []

    for period_id, label, days in terms:
        period = build_period(period_id, label, days, note="September through December")
        if period:
            (PERIODS / f"{period_id}.json").write_text(json.dumps(period, separators=(",", ":")))
            written.append(period)
            print(f"{label}: {period['n_days']} days ({period['n_days_reduced']} reduced), "
                  f"median {period['overall']['p50']} min, p90 {period['overall']['p90']} min")

    everything = build_period("all", "All terms", available, note="every September-December on record")
    if everything:
        (PERIODS / "all.json").write_text(json.dumps(everything, separators=(",", ":")))
        written.append(everything)
        print(f"All terms: {everything['n_days']} days, median {everything['overall']['p50']} min")

    # One axis for every period. Scaling each period to its own worst value
    # would redraw the y-axis on each click and quietly destroy the comparison
    # the picker exists to make - Fall 2021 really was worse than Fall 2025, and
    # that should show as a taller band, not an identical one.
    # Scaled to the worst band across every period, plus one gridline of
    # headroom for a bad day drawn on top. A single freak day would otherwise
    # set the ceiling and squash every band into the bottom third.
    worst_band = max(max(c["p90"] for c in p["curve"]) for p in written)
    axis_top = math.ceil(worst_band / 15) * 15 + 15

    index = {
        "generated_at": datetime.now(NEW_YORK).isoformat(timespec="seconds"),
        "axis": {
            "first_minute": min(p["curve"][0]["m"] for p in written),
            "last_minute": max(p["curve"][-1]["m"] for p in written),
            "top_minutes": axis_top,
        },
        "data_through": max(available),
        "data_from": min(available),
        "segment": {
            "from": STATIONS[FROM_STOP]["name"],
            "to": STATIONS[TO_STOP]["name"],
            "km": round(STATIONS[TO_STOP]["km"] - STATIONS[FROM_STOP]["km"], 1),
            "stops": TO_STOP - FROM_STOP,
        },
        "source": {
            "observed": "subwaydata.nyc",
            "schedule": "Metropolitan Transportation Authority",
        },
        "periods": [
            {
                "id": p["id"],
                "label": p["label"],
                "note": p["note"],
                "n_days": p["n_days"],
                "n_days_reduced": p["n_days_reduced"],
                "n_runs": p["n_runs"],
                "n_runs_rejected": p["n_runs_rejected"],
                "first": p["days"][0]["date"],
                "last": p["days"][-1]["date"],
                "overall": p["overall"],
            }
            for p in written
        ],
    }
    (args.out / "index.json").write_text(json.dumps(index, indent=1))
    print(f"\nindex written: {index['data_from']} to {index['data_through']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
