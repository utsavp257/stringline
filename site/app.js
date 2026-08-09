/* The D train, drawn as time.
   Two canvases: the plate (every train on one day) and the bands (every day at once).
   Day files arrive gzipped and are inflated in the browser, which keeps the repo
   small enough to serve as static files. */

const DATA = "data";
const NORTHBOUND = 0;

/* A stringline is only readable when an hour of time is worth roughly as much
   width as a few kilometres are worth height. Across a whole 19-hour day every
   train stands nearly vertical and the slope - the entire point - is lost. So
   the plate shows a window, and defaults to the one the commute lives in.

   A phone has about half the width, so it gets a shorter span rather than the
   same hours squeezed: five hours around the rush instead of eight. Whole day
   is left alone, since asking for the whole day is asking to see all of it. */
const WINDOWS = {
  morning:   { label: "Morning",   from: 300,  to: 780,  narrow: [420, 660] },   // 05:00–13:00, 07:00–11:00
  afternoon: { label: "Afternoon", from: 720,  to: 1200, narrow: [960, 1200] },  // 12:00–20:00, 16:00–20:00
  all:       { label: "Whole day", from: 300,  to: 1560 },                       // 05:00–26:00
};

/* How far a full end-to-end run must travel across the screen before the
   drawing is worth showing. Below this the trains stand near vertical, slope
   stops reading as speed, and the chart is decoration. Better to say so than
   to print something misleading.

   Set so a full day still fits a desktop, where it is dense but readable, and
   is refused on a phone, where it is not. On a typical day that puts desktop
   whole-day near 88px of travel and the same view on a phone near 24. */
const MIN_RUN_ADVANCE = 60;

/* Station names are long and a phone has no room for a column of them. These
   are short enough to fit and still be recognised. */
const SHORT_NAME = {
  "Coney Island-Stillwell Av": "Coney Is",
  "Bay Pkwy": "Bay Pkwy",
  "36 St": "36 St",
  "Atlantic Av-Barclays Ctr": "Atlantic",
  "Grand St": "Grand St",
  "W 4 St-Wash Sq": "W 4 St",
  "34 St-Herald Sq": "34 St",
  "59 St-Columbus Circle": "59 St",
  "125 St": "125 St",
  "145 St": "145 St",
  "161 St-Yankee Stadium": "161 St",
  "Fordham Rd": "Fordham",
  "Norwood-205 St": "Norwood",
};

/* Fewer labels on a phone, or they collide. */
const NARROW_LABELLED = new Set([
  "Coney Island-Stillwell Av", "Atlantic Av-Barclays Ctr", "W 4 St-Wash Sq",
  "34 St-Herald Sq", "125 St", "Fordham Rd", "Norwood-205 St",
]);

const CSS = getComputedStyle(document.documentElement);
const colour = (name) => CSS.getPropertyValue(name).trim();

const state = {
  stations: [],
  fromIdx: 0,
  toIdx: 0,
  index: null,
  period: null,
  day: null,
  overlay: null,
  showOverlay: false,
  dayCurve: null,
  pick: "worst",
  window: "morning",
  minRide: 0,
};

/* ── loading ──────────────────────────────────────────────── */

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → ${response.status}`);
  return response.json();
}

async function getGzJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → ${response.status}`);
  if (typeof DecompressionStream === "undefined") {
    throw new Error("This browser cannot inflate the day files.");
  }
  const stream = response.body.pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).json();
}

/* ── shaping ──────────────────────────────────────────────── */

/** Trips as polylines in (minute, kilometre) space. */
function polylines(payload) {
  const midnight = payload.midnight;
  const out = [];
  for (const trip of payload.trips) {
    const points = [];
    for (let i = 0; i < trip.s.length; i++) {
      const station = state.stations[trip.s[i]];
      if (!station) continue;
      points.push({
        m: (trip.t + trip.o[i] - midnight) / 60,
        km: station.km,
        idx: trip.s[i],
      });
    }
    if (points.length > 1) out.push({ route: trip.r, dir: trip.d, points });
  }
  return out;
}

/** Northbound runs over the commute segment, as [departMinute, arriveMinute].
 *  Runs faster than physically possible are feed artefacts, not fast trains. */
function segmentRuns(payload) {
  const midnight = payload.midnight;
  const runs = [];
  for (const trip of payload.trips) {
    if (trip.d !== NORTHBOUND) continue;
    const a = trip.s.indexOf(state.fromIdx);
    const b = trip.s.indexOf(state.toIdx);
    if (a === -1 || b === -1 || b <= a) continue;
    const depart = (trip.t + trip.o[a] - midnight) / 60;
    const arrive = (trip.t + trip.o[b] - midnight) / 60;
    if (arrive - depart < state.minRide) continue;
    runs.push([depart, arrive]);
  }
  runs.sort((p, q) => p[0] - q[0]);
  return runs;
}

/** Reach the platform at minute m — when do you get to Fordham Rd?
 *  Bucketed to match the bands, so the day and the term are drawn at the same
 *  resolution. Comparing a per-minute line against five-minute percentiles makes
 *  the day look wilder than it was. */
function journeyCurve(runs, from, to, bucket) {
  const perMinute = new Map();
  let i = 0;
  for (let m = from; m <= to; m++) {
    while (i < runs.length && runs[i][0] < m) i++;
    if (i >= runs.length) break;
    const [depart, arrive] = runs[i];
    if (depart - m > 120) continue;
    perMinute.set(m, arrive - m);
  }

  const grouped = new Map();
  for (const [m, value] of perMinute) {
    const key = m - (m % bucket);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(value);
  }

  const curve = new Map();
  for (const [key, values] of [...grouped].sort((a, b) => a[0] - b[0])) {
    values.sort((a, b) => a - b);
    curve.set(key, values[Math.floor(values.length / 2)]);
  }
  return curve;
}

/* ── formatting ───────────────────────────────────────────── */

const pad = (n) => String(n).padStart(2, "0");

function clock(minute) {
  const m = ((Math.round(minute) % 1440) + 1440) % 1440;
  return `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
}

function longDate(iso) {
  const [y, mo, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1, d)).toLocaleDateString("en-GB", {
    weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}

/* ── canvas plumbing ──────────────────────────────────────── */

function setupCanvas(canvas) {
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, w: rect.width, h: rect.height };
}

/* ── the plate ────────────────────────────────────────────── */

const plate = {
  canvas: document.getElementById("stringline"),
  readout: document.getElementById("plate-readout"),
  lines: [],
  overlayLines: [],
  runMinutes: 110,
  progress: 1,
  hover: null,
  geom: null,
};

const LABELLED = new Set([
  "Coney Island-Stillwell Av", "Bay Pkwy", "36 St", "Atlantic Av-Barclays Ctr",
  "Grand St", "W 4 St-Wash Sq", "34 St-Herald Sq", "59 St-Columbus Circle",
  "125 St", "145 St", "161 St-Yankee Stadium", "Fordham Rd", "Norwood-205 St",
]);

function plateGeometry(w, h, windowName = state.window) {
  const narrow = w < 620;
  // A phone still gets a station column, just a narrower one with short names.
  // Endpoint labels floated over the plot were unreadable against the trains.
  const pad = { left: narrow ? 76 : 168, right: 16, top: 18, bottom: 34 };
  const maxKm = state.stations[state.stations.length - 1].km;
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  const win = WINDOWS[windowName];
  const [from, to] = narrow && win.narrow ? win.narrow : [win.from, win.to];
  const span = to - from;
  return {
    pad, maxKm, plotW, plotH, narrow, from, to, span,
    advance: (plotW * plate.runMinutes) / span,
    x: (m) => pad.left + ((m - from) / span) * plotW,
    y: (km) => pad.top + plotH - (km / maxKm) * plotH,
    mAt: (px) => from + ((px - pad.left) / plotW) * span,
    kmAt: (py) => ((pad.top + plotH - py) / plotH) * maxKm,
  };
}

/** How long a full run takes on this day, measured rather than assumed. */
function typicalRunMinutes(lines) {
  const spans = lines
    .filter((l) => l.points.length > 20)
    .map((l) => l.points[l.points.length - 1].m - l.points[0].m)
    .sort((a, b) => a - b);
  return spans.length ? spans[Math.floor(spans.length / 2)] : 110;
}

function drawPlate() {
  const { ctx, w, h } = setupCanvas(plate.canvas);
  const g = plateGeometry(w, h);
  plate.geom = g;

  // Too little width for the slope to mean anything. Say so rather than draw
  // a wall of vertical lines that looks like data.
  const legible = g.advance >= MIN_RUN_ADVANCE && g.plotW > 180;
  plate.canvas.hidden = !legible;
  document.getElementById("plate-cramped").hidden = legible;
  updateWindowChips(w, h);
  if (!legible) return;

  ctx.clearRect(0, 0, w, h);

  // segment band — the commute this exists to measure
  const yTop = g.y(state.stations[state.toIdx].km);
  const yBottom = g.y(state.stations[state.fromIdx].km);
  ctx.fillStyle = "rgba(242, 92, 18, 0.06)";
  ctx.fillRect(g.pad.left, yTop, g.plotW, yBottom - yTop);

  // station gridlines
  ctx.lineWidth = 1;
  state.stations.forEach((station, i) => {
    const y = Math.round(g.y(station.km)) + 0.5;
    const keyStation = LABELLED.has(station.name);
    ctx.strokeStyle = keyStation ? colour("--grid-strong") : colour("--grid");
    ctx.beginPath();
    ctx.moveTo(g.pad.left, y);
    ctx.lineTo(w - g.pad.right, y);
    ctx.stroke();

    const isEnd = i === state.fromIdx || i === state.toIdx;
    const show = g.narrow ? NARROW_LABELLED.has(station.name) : keyStation;
    if (show) {
      ctx.fillStyle = isEnd ? colour("--d-train") : colour("--ink-3");
      ctx.font = `${isEnd ? "500 " : ""}10px ${CSS.getPropertyValue("--mono")}`;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      const name = g.narrow ? (SHORT_NAME[station.name] || station.name) : station.name;
      ctx.fillText(name, g.pad.left - 8, y);
    }
  });

  // hour gridlines
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const hourStep = g.to - g.from > 600 ? 120 : 60;
  for (let m = Math.ceil(g.from / 60) * 60; m <= g.to; m += hourStep) {
    const x = Math.round(g.x(m)) + 0.5;
    const major = hourStep === 120 || (m / 60) % 2 === 0;
    ctx.strokeStyle = major ? colour("--grid-strong") : colour("--grid");
    ctx.beginPath();
    ctx.moveTo(x, g.pad.top);
    ctx.lineTo(x, g.pad.top + g.plotH);
    ctx.stroke();
    if (major) {
      ctx.fillStyle = colour("--ink-3");
      ctx.font = `10px ${CSS.getPropertyValue("--mono")}`;
      ctx.fillText(clock(m), x, g.pad.top + g.plotH + 9);
    }
  }

  // clip to the drawing progress so the plate plots itself in
  ctx.save();
  ctx.beginPath();
  ctx.rect(g.pad.left, g.pad.top, g.plotW * plate.progress, g.plotH);
  ctx.clip();

  const stroke = (lines, style, width) => {
    ctx.strokeStyle = style;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (const line of lines) {
      ctx.beginPath();
      line.points.forEach((p, i) => {
        const x = g.x(p.m), y = g.y(p.km);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  };

  if (state.showOverlay && plate.overlayLines.length) {
    stroke(plate.overlayLines, "rgba(74, 108, 141, 0.5)", 0.9);
  }
  stroke(plate.lines.filter((l) => l.dir !== NORTHBOUND), colour("--d-train-dim"), 1);
  stroke(plate.lines.filter((l) => l.dir === NORTHBOUND), colour("--d-train"), 1.3);

  if (plate.hover) {
    stroke([plate.hover], colour("--ink"), 2.2);
  }

  ctx.restore();

  const pxPerHour = (g.plotW / (g.to - g.from)) * 60;
  document.getElementById("tb-scale").textContent =
    `1 h = ${pxPerHour.toFixed(0)} px · 1 km = ${(g.plotH / g.maxKm).toFixed(1)} px`;
  document.getElementById("tb-hours").textContent = `${clock(g.from)}–${clock(g.to)}`;
}

/** Grey out any window that would be too squeezed to read at this width. */
function updateWindowChips(w, h) {
  document.querySelectorAll("[data-window]").forEach((button) => {
    const g = plateGeometry(w, h, button.dataset.window);
    const usable = g.advance >= MIN_RUN_ADVANCE && g.plotW > 180;
    button.disabled = !usable;
    button.title = usable ? "" : "Too many hours to fit at this width";
  });
}

function animatePlate() {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) { plate.progress = 1; drawPlate(); return; }
  plate.progress = 0;
  const started = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - started) / 1100);
    plate.progress = 1 - Math.pow(1 - t, 3);
    drawPlate();
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/** The train nearest the cursor, measured where it actually is at that minute. */
function trainAt(minute, km) {
  let best = null, bestGap = Infinity;
  const all = state.showOverlay ? plate.lines.concat(plate.overlayLines) : plate.lines;
  for (const line of all) {
    const pts = line.points;
    if (minute < pts[0].m || minute > pts[pts.length - 1].m) continue;
    for (let i = 1; i < pts.length; i++) {
      if (pts[i].m < minute) continue;
      const a = pts[i - 1], b = pts[i];
      const span = b.m - a.m;
      const at = span <= 0 ? a.km : a.km + ((minute - a.m) / span) * (b.km - a.km);
      const gap = Math.abs(at - km);
      if (gap < bestGap) { bestGap = gap; best = { line, at, a, b }; }
      break;
    }
  }
  return bestGap < 1.6 ? best : null;
}

plate.canvas.addEventListener("pointermove", (event) => {
  if (!plate.geom || !plate.lines.length) return;
  const rect = plate.canvas.getBoundingClientRect();
  const g = plate.geom;
  const minute = g.mAt(event.clientX - rect.left);
  const km = g.kmAt(event.clientY - rect.top);
  const found = trainAt(minute, km);

  plate.hover = found ? found.line : null;
  drawPlate();

  if (!found) { plate.readout.hidden = true; return; }

  const heading = found.line.dir === NORTHBOUND ? "northbound" : "southbound";
  const speed = (found.b.km - found.a.km) / Math.max(found.b.m - found.a.m, 0.0001) * 60;
  const stopped = speed < 1.5;
  plate.readout.hidden = false;
  plate.readout.querySelector(".readout-train").textContent =
    `${found.line.route} train · ${heading}`;
  plate.readout.querySelector(".readout-detail").textContent = stopped
    ? `${clock(minute)} · standing at ${state.stations[found.a.idx].name}`
    : `${clock(minute)} · ${speed.toFixed(0)} km/h between ${state.stations[found.a.idx].name} and ${state.stations[found.b.idx].name}`;
});

plate.canvas.addEventListener("pointerleave", () => {
  plate.hover = null;
  plate.readout.hidden = true;
  drawPlate();
});

/* ── the bands ────────────────────────────────────────────── */

const bands = {
  canvas: document.getElementById("bandchart"),
  verdict: document.getElementById("verdict"),
  geom: null,
  hoverMinute: 8 * 60,
};

function bandGeometry(w, h) {
  const pad = { left: 46, right: 16, top: 14, bottom: 30 };
  // One axis for every period, fixed by the build. Rescaling per selection
  // would make Fall 2021 and Fall 2025 look identical when they are not.
  const axis = state.index.axis;
  const first = axis.first_minute;
  const last = axis.last_minute;
  const span = last - first;
  const top = axis.top_minutes;
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  return {
    pad, top, plotW, plotH, first, last,
    x: (m) => pad.left + ((m - first) / span) * plotW,
    y: (v) => pad.top + plotH - (v / top) * plotH,
    mAt: (px) => first + ((px - pad.left) / plotW) * span,
  };
}

function drawBands() {
  if (!state.period) return;
  const { ctx, w, h } = setupCanvas(bands.canvas);
  const g = bandGeometry(w, h);
  bands.geom = g;
  const curve = state.period.curve;

  ctx.clearRect(0, 0, w, h);

  // minutes axis
  ctx.lineWidth = 1;
  ctx.font = `10px ${CSS.getPropertyValue("--mono")}`;
  ctx.fillStyle = colour("--ink-3");
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let v = 0; v <= g.top; v += 15) {
    const y = Math.round(g.y(v)) + 0.5;
    ctx.strokeStyle = colour("--grid");
    ctx.beginPath();
    ctx.moveTo(g.pad.left, y);
    ctx.lineTo(w - g.pad.right, y);
    ctx.stroke();
    ctx.fillText(`${v}m`, g.pad.left - 8, y);
  }

  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let m = Math.ceil(g.first / 120) * 120; m <= g.last; m += 120) {
    const x = Math.round(g.x(m)) + 0.5;
    ctx.strokeStyle = colour("--grid-strong");
    ctx.beginPath();
    ctx.moveTo(x, g.pad.top);
    ctx.lineTo(x, g.pad.top + g.plotH);
    ctx.stroke();
    ctx.fillText(clock(m), x, g.pad.top + g.plotH + 8);
  }

  ctx.save();
  ctx.beginPath();
  ctx.rect(g.pad.left, g.pad.top, g.plotW, g.plotH);
  ctx.clip();

  const ribbon = (lo, hi, fill) => {
    ctx.fillStyle = fill;
    ctx.beginPath();
    curve.forEach((c, i) => {
      const x = g.x(c.m), y = g.y(c[hi]);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    for (let i = curve.length - 1; i >= 0; i--) {
      ctx.lineTo(g.x(curve[i].m), g.y(curve[i][lo]));
    }
    ctx.closePath();
    ctx.fill();
  };

  ribbon("p10", "p90", colour("--band-outer"));
  ribbon("p25", "p75", colour("--band-inner"));

  ctx.strokeStyle = colour("--band-median");
  ctx.lineWidth = 1.75;
  ctx.beginPath();
  curve.forEach((c, i) => {
    const x = g.x(c.m), y = g.y(c.p50);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // the selected day, drawn on top of every day
  if (state.dayCurve) {
    ctx.strokeStyle = colour("--d-train");
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    let started = false;
    for (const [m, v] of state.dayCurve) {
      const x = g.x(m), y = g.y(v);
      started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
    }
    ctx.stroke();
  }

  ctx.restore();

  if (bands.hoverMinute != null) {
    const x = Math.round(g.x(bands.hoverMinute)) + 0.5;
    ctx.strokeStyle = colour("--ink-3");
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, g.pad.top);
    ctx.lineTo(x, g.pad.top + g.plotH);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function atMinute(minute) {
  const curve = state.period.curve;
  if (!curve.length) return null;
  let best = curve[0];
  for (const c of curve) {
    if (Math.abs(c.m - minute) < Math.abs(best.m - minute)) best = c;
  }
  return best;
}

function writeVerdict(minute) {
  const point = atMinute(minute);
  if (!point) { bands.verdict.textContent = "Not enough days to say."; return; }
  const dayValue = state.dayCurve ? state.dayCurve.get(point.m) : null;
  const onTheDay = dayValue
    ? ` On the day drawn above it took <b>${Math.round(dayValue)} min</b>.`
    : "";
  bands.verdict.innerHTML =
    `Reach 34 St at <b>${clock(point.m)}</b> and you are at Fordham Rd by ` +
    `<b>${clock(point.m + point.p50)}</b> half the time — but one morning in ten, ` +
    `not until <b>${clock(point.m + point.p90)}</b>.` + onTheDay +
    ` <span class="muted">${point.n} days</span>`;
}

bands.canvas.addEventListener("pointermove", (event) => {
  if (!bands.geom) return;
  const rect = bands.canvas.getBoundingClientRect();
  const g = bands.geom;
  bands.hoverMinute = Math.max(g.first, Math.min(g.last, g.mAt(event.clientX - rect.left)));
  drawBands();
  writeVerdict(bands.hoverMinute);
});

/* ── day strip and terms ──────────────────────────────────── */

function renderDayStrip() {
  const strip = document.getElementById("daystrip");
  strip.innerHTML = "";
  // Daily p90 clusters tightly, so a zero baseline flattens every day into the
  // same bar. Anchoring at the term's best day makes the spread legible; the
  // caption says the baseline is the calmest day so the height still means something.
  const values = state.period.days.map((d) => d.p90);
  const floor = Math.min(...values);
  const worst = Math.max(...values);
  const range = Math.max(worst - floor, 1);
  for (const day of state.period.days) {
    const bar = document.createElement("button");
    bar.type = "button";
    bar.className = "daybar" + (day.reduced_service ? " reduced" : "");
    bar.style.height = `${8 + ((day.p90 - floor) / range) * 92}%`;
    bar.title = `${longDate(day.date)} — ${day.p90} min at worst`;
    bar.setAttribute("aria-label", `${longDate(day.date)}, ninetieth percentile ${day.p90} minutes`);
    bar.setAttribute("aria-pressed", String(state.day && state.day.date === day.date));
    bar.addEventListener("click", () => selectDay(day.date));
    strip.appendChild(bar);
  }
}

function renderTerms() {
  const holder = document.getElementById("terms");
  holder.innerHTML = "";
  for (const period of state.index.periods) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.textContent = period.label;
    button.setAttribute("aria-pressed", String(state.period && state.period.id === period.id));
    button.addEventListener("click", () => selectPeriod(period.id));
    holder.appendChild(button);
  }
}

function pickDay(kind) {
  const usable = state.period.days.filter((d) => !d.reduced_service);
  const pool = usable.length ? usable : state.period.days;
  if (kind === "worst") {
    return pool.reduce((a, b) => ((b.rush_worst ?? b.p90) > (a.rush_worst ?? a.p90) ? b : a)).date;
  }
  const middle = [...pool].sort((a, b) => a.median - b.median)[Math.floor(pool.length / 2)];
  return middle.date;
}

/* ── selection ────────────────────────────────────────────── */

async function selectDay(date, animate = true) {
  state.day = state.period.days.find((d) => d.date === date) || null;
  const payload = await getGzJson(`${DATA}/days/${date}.json.gz`);
  plate.lines = polylines(payload);
  plate.runMinutes = typicalRunMinutes(plate.lines);
  const curve = state.period.curve;
  const bucket = curve.length > 1 ? curve[1].m - curve[0].m : 5;
  state.dayCurve = journeyCurve(
    segmentRuns(payload), curve[0].m, curve[curve.length - 1].m, bucket);

  plate.overlayLines = [];
  state.overlay = null;
  if (state.showOverlay) await loadOverlay(date);

  document.getElementById("tb-day").textContent = longDate(date);
  document.getElementById("tb-trains").textContent =
    `${payload.trips.length} D · ${plate.lines.filter((l) => l.dir === NORTHBOUND).length} northbound`;

  document.querySelectorAll("[data-pick]").forEach((b) =>
    b.setAttribute("aria-pressed", String(pickDay(b.dataset.pick) === date)));

  renderDayStrip();
  animate ? animatePlate() : drawPlate();
  drawBands();
  writeVerdict(bands.hoverMinute);
}

async function loadOverlay(date) {
  state.overlay = await getGzJson(`${DATA}/days/${date}.overlay.json.gz`);
  plate.overlayLines = polylines(state.overlay);
}

async function selectPeriod(id) {
  state.period = await getJson(`${DATA}/periods/${id}.json`);
  document.getElementById("strip-term").textContent = state.period.label;
  renderTerms();
  await selectDay(pickDay(state.pick));
}

/* ── the ranking ──────────────────────────────────────────── */

function renderFacts(system, meta) {
  const routes = system.routes;
  const slowest = routes[0];
  const fastest = routes[routes.length - 1];
  const drop = (r) => r.kmh - r.kmh_slow_tenth;
  const longest = [...routes].sort((a, b) => b.end_to_end_minutes - a.end_to_end_minutes)[0];
  // Whichever route already carries the fastest tile does not get a second one.
  const widest = [...routes]
    .filter((r) => r.route !== fastest.route && r.route !== slowest.route)
    .sort((a, b) => drop(b) - drop(a))[0];
  const third = longest.route === slowest.route || longest.route === fastest.route
    ? { value: `−${drop(widest).toFixed(1)} km/h`, route: widest.route,
        label: `is how far the ${widest.route} falls on a bad run — the widest gap
                between a normal trip and the slowest tenth` }
    : { value: `${longest.end_to_end_minutes} min`, route: longest.route,
        label: `end to end on the ${longest.route}, the longest ride in the system —
                ${longest.km.toFixed(1)} km without leaving the train` };

  const tiles = [
    {
      value: `${slowest.kmh} km/h`,
      route: slowest.route,
      label: `is the slowest service in the city — ${meta[slowest.route].long_name},
              ${slowest.end_to_end_minutes} minutes end to end`,
    },
    {
      value: `${fastest.kmh} km/h`,
      route: fastest.route,
      label: `is the fastest — ${meta[fastest.route].long_name} — and also the least
              dependable, losing ${drop(fastest).toFixed(1)} km/h on its slowest tenth
              of runs`,
    },
    third,
  ];

  document.getElementById("facts").innerHTML = tiles.map((t) => `
    <div class="fact">
      <p class="fact-value">${t.value}</p>
      <p class="fact-label"><span class="bullet" style="background:${meta[t.route].colour}">${t.route}</span>${t.label}</p>
    </div>`).join("");
}

function renderRanking(system, meta) {
  const routes = system.routes;
  document.getElementById("service-count").textContent = `all ${routes.length} services`;
  document.getElementById("footnote-count").textContent = `all ${routes.length} services`;
  const fastest = Math.max(...routes.map((r) => r.kmh));
  const list = document.getElementById("ranking");
  list.innerHTML = routes.map((r) => {
    const width = (r.kmh / fastest) * 100;
    const slow = (r.kmh_slow_tenth / fastest) * 100;
    return `
      <li class="rank-row">
        <span class="bullet" style="background:${meta[r.route].colour}">${r.route}</span>
        <span class="rank-name">${meta[r.route].terminals.join(" – ")}</span>
        <span class="rank-bar">
          <span class="rank-fill" style="width:${width}%"></span>
          <span class="rank-slow" style="left:${slow}%" title="slowest tenth of runs: ${r.kmh_slow_tenth} km/h"></span>
        </span>
        <span class="rank-value">${r.kmh.toFixed(1)}<span class="unit"> km/h</span></span>
        <span class="rank-note">${r.end_to_end_minutes} min end to end</span>
      </li>`;
  }).join("");
}

/* ── the commute checker ──────────────────────────────────── */

const checker = {
  meta: null,
  route: null,
  data: null,
};

function fillStations(select, stations, selectedIndex) {
  select.innerHTML = stations
    .map((s, i) => `<option value="${i}"${i === selectedIndex ? " selected" : ""}>${s.name}</option>`)
    .join("");
  select.disabled = false;
}

/** Find the direction in which `from` comes before `to`. */
function orientation(data, fromName, toName) {
  for (const direction of Object.keys(data.directions)) {
    const stations = data.directions[direction].stations;
    const a = stations.findIndex((s) => s.name === fromName);
    const b = stations.findIndex((s) => s.name === toName);
    if (a !== -1 && b !== -1 && a < b) return { direction, a, b };
  }
  return null;
}

function describeJourney() {
  const answer = document.getElementById("answer");
  if (!checker.data) return;

  const fromSelect = document.getElementById("from-station");
  const toSelect = document.getElementById("to-station");
  const block = document.getElementById("block").value;
  const blockLabel = document.getElementById("block").selectedOptions[0].textContent.toLowerCase();

  const stations = checker.data.directions["0"].stations;
  const fromName = stations[Number(fromSelect.value)]?.name;
  const toName = stations[Number(toSelect.value)]?.name;

  if (!fromName || !toName || fromName === toName) {
    answer.innerHTML = `<p class="answer-empty">Pick two different stations.</p>`;
    return;
  }

  const found = orientation(checker.data, fromName, toName);
  if (!found) {
    answer.innerHTML = `<p class="answer-empty">The ${checker.route} does not run from
      ${fromName} to ${toName} in that order.</p>`;
    return;
  }

  const leg = checker.data.directions[found.direction];
  const pair = leg.pairs[`${found.a}-${found.b}`];
  const stats = pair && pair[block];

  if (!stats) {
    answer.innerHTML = `<p class="answer-empty">Not enough observed runs between
      ${fromName} and ${toName} ${blockLabel} to say anything honest.</p>`;
    return;
  }

  const wait = leg.waits[String(found.a)]?.[block];
  const km = (leg.stations[found.b].km - leg.stations[found.a].km).toFixed(1);
  const stops = found.b - found.a;
  const colour = checker.meta[checker.route].colour;

  answer.innerHTML = `
    <div class="answer-grid">
      <div class="answer-main">
        <p class="answer-line">
          <span class="bullet" style="background:${colour}">${checker.route}</span>
          ${fromName} <span class="arrow">→</span> ${toName}
        </p>
        <p class="answer-big">The ride takes <b>${stats.p50} min</b>.</p>
        <p class="answer-sub">One trip in ten takes <b>${stats.p90} min</b> or worse.
        ${wait ? `Trains at ${fromName} come every <b>${wait} min</b> on average, so budget that on top.` : ""}</p>
      </div>
      <dl class="answer-facts">
        <div><dt>Stops</dt><dd>${stops}</dd></div>
        <div><dt>Distance</dt><dd>${km} km</dd></div>
        <div><dt>Observed runs</dt><dd>${stats.n.toLocaleString()}</dd></div>
        <div><dt>Average speed</dt><dd>${(km / (stats.p50 / 60)).toFixed(1)} km/h</dd></div>
      </dl>
    </div>`;
}

async function selectRoute(route) {
  checker.route = route;
  checker.data = await getJson(`${DATA}/routes/${route}.json`);

  document.querySelectorAll("#bullets .bullet").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.route === route)));

  // The D opens on the commute this whole thing was built to answer; every
  // other line opens end to end.
  const stations = checker.data.directions["0"].stations;
  const named = (name, fallback) => {
    const found = stations.findIndex((s) => s.name === name);
    return found === -1 ? fallback : found;
  };
  const from = route === "D" ? named("34 St-Herald Sq", 0) : 0;
  const to = route === "D" ? named("Fordham Rd", stations.length - 1) : stations.length - 1;

  fillStations(document.getElementById("from-station"), stations, from);
  fillStations(document.getElementById("to-station"), stations, to);
  describeJourney();
}

function renderBullets(meta) {
  const holder = document.getElementById("bullets");
  holder.innerHTML = Object.keys(meta).map((route) => `
    <button type="button" class="bullet bullet-button" data-route="${route}"
            style="background:${meta[route].colour}" aria-pressed="false"
            title="${meta[route].long_name}">${route}</button>`).join("");
  holder.querySelectorAll(".bullet-button").forEach((button) =>
    button.addEventListener("click", () => selectRoute(button.dataset.route)));
}

/* ── boot ─────────────────────────────────────────────────── */

async function boot() {
  const [index, route, system, meta] = await Promise.all([
    getJson(`${DATA}/index.json`),
    getJson(`${DATA}/stations.json`),
    getJson(`${DATA}/system.json`),
    getJson(`${DATA}/routes_meta.json`),
  ]);
  state.index = index;

  checker.meta = meta;
  renderFacts(system, meta);
  renderRanking(system, meta);
  renderBullets(meta);
  ["from-station", "to-station", "block"].forEach((id) =>
    document.getElementById(id).addEventListener("change", describeJourney));
  selectRoute("D");
  state.stations = route.stations;
  state.fromIdx = route.stations.findIndex((s) => s.id === route.segment.from);
  state.toIdx = route.stations.findIndex((s) => s.id === route.segment.to);
  state.minRide = route.segment.min_ride_minutes;

  const terms = index.periods.filter((p) => p.id !== "all");
  const newest = terms.length ? terms[terms.length - 1] : index.periods[0];

  const all = index.periods.find((p) => p.id === "all");
  const rejectedPct = (100 * all.n_runs_rejected / (all.n_runs + all.n_runs_rejected)).toFixed(1);
  document.getElementById("coverage").innerHTML =
    `Every weekday from <strong>${longDate(index.data_from)}</strong> to ` +
    `<strong>${longDate(index.data_through)}</strong> — ${all.n_days} days of observed ` +
    `movements across ${terms.length} autumns, September to December. The segment is ` +
    `${index.segment.from} to ${index.segment.to}: ${index.segment.stops} stops, ` +
    `${index.segment.km} km, ${all.n_runs.toLocaleString()} northbound runs. ` +
    `A further ${rejectedPct}% were dropped as physically impossible — feed artefacts ` +
    `putting a train across 15 km in seconds.`;

  document.querySelectorAll("[data-pick]").forEach((button) => {
    button.addEventListener("click", () => {
      state.pick = button.dataset.pick;
      selectDay(pickDay(state.pick));
    });
  });

  document.querySelectorAll("[data-window]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.window === state.window));
    button.addEventListener("click", () => {
      state.window = button.dataset.window;
      document.querySelectorAll("[data-window]").forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.window === state.window)));
      animatePlate();
    });
  });

  document.getElementById("overlay-toggle").addEventListener("change", async (event) => {
    state.showOverlay = event.target.checked;
    if (state.showOverlay && state.day) await loadOverlay(state.day.date);
    drawPlate();
  });

  await selectPeriod(newest.id);
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { drawPlate(); drawBands(); }, 120);
});

boot().catch((error) => {
  document.getElementById("verdict").textContent = `Could not load the data: ${error.message}`;
  console.error(error);
});
