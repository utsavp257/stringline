"""Download the published subway schedule.

The realtime feeds identify stops by opaque ids like "D11N". Turning those into
"Fordham Rd" - and knowing what order they come in along a route - takes the
static GTFS bundle, which the MTA republishes whenever the schedule changes.

Run this occasionally. The realtime data is the perishable half; this is not.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import requests

URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"


def main() -> int:
    destination = Path(__file__).resolve().parent.parent / "static"
    destination.mkdir(parents=True, exist_ok=True)
    stamped = destination / f"gtfs_subway_{date.today():%Y%m%d}.zip"

    response = requests.get(URL, timeout=120)
    response.raise_for_status()
    stamped.write_bytes(response.content)

    current = destination / "gtfs_subway.zip"
    if current.exists() or current.is_symlink():
        current.unlink()
    current.symlink_to(stamped.name)

    print(f"wrote {stamped} ({len(response.content):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
