# The slowest train in New York

A single page about how fast New York's subway actually moves, built from
observed train movements rather than the timetable.

It answers three questions, in order of how many people care about them:

1. **Which line is slowest?** Every service measured the same way.
2. **What about my trip?** Any pair of stations on any line.
3. **What about the writer's trip?** One commute, in full detail, as a
   time-space diagram.

## Why the data has to be borrowed

The MTA publishes realtime feeds, but they only look forward. A trip update
lists the stops a train has yet to reach and drops each one as it passes, so by
the time a train finishes its run the feed has forgotten the whole journey.
There is no official archive of where trains actually were, and nothing to
backfill from.

[subwaydata.nyc](https://subwaydata.nyc/) has been recording those feeds since
April 2021 and publishes each day as two CSVs: one row per trip, one row per
station call with observed arrival and departure. That archive is the entire
basis of this project. Station names, route ordering and distances come from
the MTA's published schedule bundle.

A collector was written before that archive was found. It worked, and it was
thrown away, because a dataset that already spans five years beats one that
starts today.

### The trap in the source data

`arrival_time` and `departure_time` are real Unix timestamps.
`trips.start_time` is not — it encodes the scheduled origin as local
wall-clock but stores it as though the clock were UTC. On a sampled day the
error was exactly 14400 seconds on all 8,361 trips, confirmed against both the
`trip_id` origin prefix and the `vehicle_id` label. Anything that derives a
time axis from `start_time` is silently four hours wrong.

Everything here derives timing from observed stop times only.

## What gets measured

### Speed, for the ranking

Per run, not per scheduled trip: the distance between the first and last
station a train actually served, over the time it took. Short turns count.
Measuring only end-to-end trips was tried first and left three services with
no reading at all, because on some lines short turns are most of the service.

Distances are great-circle between station coordinates, so they run short of
real track length and every speed reads slightly low. The bias applies equally
to all services, so the ranking holds even though the absolute figures are
conservative.

### Ride times, for the commute checker

For every ordered pair of stations on a line — 34,602 of them — the observed
time from one to the other, split into five parts of the day. Counted into
per-pair histograms rather than kept as samples: a single day holds roughly 2.5
million pair observations and there are 436 days.

### Waits, separately and on purpose

Headway is the gap between one train and the next at a platform, measured per
station, per direction, per part of the day.

Waiting is reported **separately from riding**, and this is a deliberate
refusal rather than an omission. Percentiles do not add. The 90th percentile of
a wait plus the 90th percentile of a ride is not the 90th percentile of the two
together — it is worse than either, because it assumes both go badly at once.
Publishing one fused number for arbitrary station pairs would overstate every
result on the page.

### The one exception

For the writer's own commute — 34 St-Herald Sq to Fordham Rd on the D, 14
stops, 15.5 km — the fusion is done properly. Because the pair is fixed, the
simulation can walk the clock minute by minute: arrive on the platform at 8:07,
catch whatever comes next, and record when it reaches Fordham Rd. That produces
a genuine wait-plus-ride distribution, and it is why the personal section can
say "you are there by 08:40 half the time" while the general checker cannot.

## Filtering, and what is thrown away

- **Physically impossible runs.** About 0.8% of segment runs imply speeds a
  train cannot reach, up to 6,994 km/h, against a real median of 27. Anything
  implying over 60 km/h is dropped as a feed artefact. The count is shown on
  the page.
- **Reduced-service days.** Holidays run a thinner timetable. They are found by
  comparing a day's run count to the term median rather than by a hardcoded
  calendar, which correctly catches Labor Day, Thanksgiving, Christmas Eve and
  Day, and New Year's Eve. They are excluded from the bands but can still be
  drawn individually.
- **Weekends**, entirely. This is about commuting.
- **Thin buckets.** A five-minute bucket backed by fewer than five days is not
  drawn at all rather than drawn as a confident thin line.

## Drawing decisions

**One axis for every period.** The term picker exists to compare autumns, so
the y-axis is fixed once at build time and stored in `index.json`. Scaling each
period to its own worst value would redraw the axis on every click and make
Fall 2021 look identical to Fall 2025 when it was measurably worse.

**A window, not a whole day.** A stringline is only readable when an hour of
time is worth roughly as much width as a few kilometres are worth height.
Across 19 hours every train stands nearly vertical and slope — the entire point
of the drawing — stops meaning anything.

**Both curves at the same resolution.** The selected day is bucketed to five
minutes exactly like the percentile bands. Drawing a per-minute line against
five-minute percentiles makes the day look wilder than it was.

**Other services as one series, not five.** B, D, F and M are all orange in the
MTA's palette and A and C are both blue, so official colours cannot tell the
overlay routes apart. They are drawn as a single de-emphasised layer instead,
which is also the truer point: the argument is that other traffic is in the
way, not which train it is.

## Shape of the thing

Static files and a fetch. No server, no database.

```
data_prep/network.py       every route, ordered, with distances
data_prep/route.py         the D in particular, and the commute segment
data_prep/build_days.py    one day of D movements, ~48 KB, for the diagram
data_prep/build_periods.py the commute simulation and its percentile bands
data_prep/build_stats.py   whole-network speeds, ride times, headways
data_prep/fetch_static.py  the published schedule

site/data/index.json       coverage, the shared axis, what a term contains
site/data/system.json      the speed ranking
site/data/routes/<id>.json per-pair ride times and headways, fetched on demand
site/data/days/<date>.json.gz  one day of trips, fetched on demand
```

Day files are gzipped and inflated in the browser, which keeps a five-year
corpus inside a repository that can be served as static files.

## Keeping it current

Two scheduled jobs, because they cost wildly different amounts.

`daily.yml` picks up newly published days, rebuilds the commute aggregates and
commits. It looks a fortnight back each run so a few failed nights heal
themselves. It takes seconds.

`rebuild.yml` recomputes the whole-network statistics, which requires
re-reading every day in the corpus. That is a few hundred megabytes from
someone else's server, so it runs monthly rather than nightly.

The page renders its own coverage line from `index.json` — dates, day counts,
missing days — so the disclosure cannot drift out of date as the jobs run. That
matters most while a term is in progress and the page is changing under the
reader.

## Deliberately not built

No realtime mode. No map. No trip planner. No accounts. The page is a set of
static files, and every number on it can be traced back to a train that was
observed somewhere at some minute.

## The Staten Island Railway

Excluded from the ranking. It is a separate railway rather than a subway line:
its stations sit kilometres apart, so it recorded 31 km/h against a subway
range of 21 to 29.5 and topped the list without being comparable to anything on
it. It also carried the widest spread in the system, losing 15.5 km/h on its
slowest tenth of runs — more than three times any subway line — because it
alternates express and local patterns over the same track.

Both figures are true and neither is a fair comparison, which is the reason it
is gone.
