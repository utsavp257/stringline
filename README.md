# stringline

How fast New York's subway actually moves, measured from where the trains were
rather than where the timetable said they would be.

One page, three questions:

- **Which line is slowest?** Every subway service, measured the same way.
- **What about your trip?** Any pair of stations on any line.
- **What about mine?** One commute — 34 St-Herald Sq to Fordham Rd on the D —
  drawn as a time-space diagram, where time runs left to right, stations run
  bottom to top, and every line is one train. Slope is speed, so a line gone
  flat is a train that has stopped.

## The data

Observed movements come from [subwaydata.nyc](https://subwaydata.nyc/), which
has recorded the MTA's realtime feeds continuously since April 2021.

This is a reconstruction, not an official record. The MTA's feeds only look
forward — a trip update lists the stops a train has yet to reach and drops each
one as it passes, so a finished trip leaves no trace. There is no official
archive of where trains actually were. The only way to know is to have been
watching, which is what subwaydata.nyc has been doing.

Station names, route order and distances come from the MTA's published
schedule bundle.

### One trap worth knowing

`arrival_time` and `departure_time` are real Unix timestamps.
`trips.start_time` is not. It encodes the scheduled origin as local wall-clock
but stores it as though the clock were UTC, so reading it as an instant is off
by the whole New York offset — four hours in summer, on every row. On a sampled
day the error was exactly 14400 seconds for all 8,361 trips.

Use the first observed stop time when you need the moment a trip began. Treat
`start_time` as a schedule label, not a time.

## Building it

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python data_prep/fetch_static.py    # the published schedule
.venv/bin/python data_prep/network.py         # every route, ordered

# The commute: one file per day, about 48 KB, for the diagram
.venv/bin/python data_prep/build_days.py 2025-09-01 2025-12-31
.venv/bin/python data_prep/route.py
.venv/bin/python data_prep/build_periods.py

# The whole network: speeds, ride times, headways
.venv/bin/python data_prep/build_stats.py 2025-09-01:2025-12-31
```

`build_stats.py` takes several date ranges at once and accumulates them
together, which is how the five autumns are built:

```sh
.venv/bin/python data_prep/build_stats.py \
  2021-09-01:2021-12-31 2022-09-01:2022-12-31 2023-09-01:2023-12-31 \
  2024-09-01:2024-12-31 2025-09-01:2025-12-31
```

Then serve `site/` with anything static:

```sh
cd site && python3 -m http.server 8787
```

## Keeping it current

`.github/workflows/daily.yml` picks up newly published days and rebuilds the
commute aggregates. It looks a fortnight back each run so a missed night heals
itself.

`.github/workflows/rebuild.yml` recomputes the whole-network statistics, which
means re-reading every day in the corpus — a few hundred megabytes from someone
else's server. It runs monthly, and by hand when a term ends.

## Layout

```
data_prep/network.py        every route, ordered, with distances
data_prep/route.py          the D, and the commute segment
data_prep/build_days.py     one day of movements for the diagram
data_prep/build_periods.py  the commute simulation and its percentile bands
data_prep/build_stats.py    whole-network speeds, ride times, headways
data_prep/fetch_static.py   the published schedule
site/                       the page; static files and a fetch
docs/design/                what this is and why it is shaped this way
```

Day files are gzipped and inflated in the browser, which is what keeps a
five-year corpus small enough to serve as static files.

## Credit

Observed movement data by [James Fennell](https://github.com/jamespfennell/subwaydata.nyc).
Schedule data published by the Metropolitan Transportation Authority.
