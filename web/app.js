// Sweep Calendar front end: find a block side on the map, subscribe to its feed.
// Data is static: <city>/meta.json, <city>/tiles/<x>_<y>.json, <city>/feeds/<id>.ics.
"use strict";

const MIN_ZOOM = 16;        // below this, block sides are too dense to tap
const OFFSET_M = 5;         // draw each curb this many meters off the centerline
const NEARBY = 6;

const $ = (id) => document.getElementById(id);
const state = { city: null, tiles: new Map(), pending: new Map(), layers: new Map(), selected: null };

const map = L.map("map", { zoomControl: true, attributionControl: true });
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
const sideLayer = L.layerGroup().addTo(map);
let pin = null;

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ---- geometry -------------------------------------------------------------

const M_PER_DEG = 111320;

function toXY([lon, lat], lat0) {
  return [lon * M_PER_DEG * Math.cos(lat0 * Math.PI / 180), lat * M_PER_DEG];
}

// Shift a polyline sideways so the two curbs of one street don't overlap.
function offsetLine(line, curb) {
  if (line.length < 2 || !curb) return line.map(([x, y]) => [y, x]);
  const lat0 = line[0][1], k = Math.cos(lat0 * Math.PI / 180);
  const sign = curb === "R" ? 1 : -1;
  const pts = line.map((p) => toXY(p, lat0));
  return pts.map((p, i) => {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
    const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
    const nx = (dy / len) * sign * OFFSET_M, ny = (-dx / len) * sign * OFFSET_M;
    return [(p[1] + ny) / M_PER_DEG, (p[0] + nx) / (M_PER_DEG * k)];
  });
}

// A block side is one or more pieces (see parkcal/merge.py); draw them as one
// multi-polyline so they highlight and select together.
function shape(s) { return s.parts.map((p) => offsetLine(p.line, p.curb)); }

function distanceToSide(pt, s) {
  return Math.min(...s.parts.map((p) => distanceToLine(pt, p.line)));
}

function distanceToLine(pt, line) {
  const lat0 = pt[1], p = toXY(pt, lat0);
  let best = Infinity;
  for (let i = 0; i < line.length - 1; i++) {
    const a = toXY(line[i], lat0), b = toXY(line[i + 1], lat0);
    const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2));
    best = Math.min(best, Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy));
  }
  return best;
}

// ---- data ----------------------------------------------------------------

// meta.json and cities.json are revalidated on every load; tiles carry the
// build stamp in their URL, so a weekly rebuild never mixes old and new data.
async function getJSON(url, init = {}) {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

function tileKeysAround(bounds, pad = 1) {
  const d = state.city.tileDeg, have = new Set(state.city.tiles), keys = [];
  const x0 = Math.floor(bounds.getWest() / d) - pad, x1 = Math.floor(bounds.getEast() / d) + pad;
  const y0 = Math.floor(bounds.getSouth() / d) - pad, y1 = Math.floor(bounds.getNorth() / d) + pad;
  for (let x = x0; x <= x1; x++)
    for (let y = y0; y <= y1; y++) if (have.has(`${x}_${y}`)) keys.push(`${x}_${y}`);
  return keys;
}

// Tile fetches are shared promises, so a pan and a search racing for the
// same tile both wait on one request instead of one seeing it half-loaded.
async function loadTiles(keys) {
  for (const k of keys)
    if (!state.pending.has(k))
      state.pending.set(k, getJSON(`${state.city.slug}/tiles/${k}.json?v=${state.city.built}`)
        .then((sides) => state.tiles.set(k, sides)));
  await Promise.all(keys.map((k) => state.pending.get(k)));
}

function tileOf(s) {
  const d = state.city.tileDeg, [lon, lat] = s.parts[0].line[0];
  return `${Math.floor(lon / d)}_${Math.floor(lat / d)}`;
}

function sidesIn(keys) {
  return keys.flatMap((k) => state.tiles.get(k) || []);
}

// ---- map drawing ---------------------------------------------------------

function styleFor(id) {
  const hi = state.selected && state.selected.id === id;
  return { color: cssVar(hi ? "--side-hi" : "--side"), weight: hi ? 7 : 4, opacity: hi ? 1 : 0.75, lineCap: "round" };
}

async function redraw() {
  if (!state.city) return;
  if (map.getZoom() < MIN_ZOOM) {
    sideLayer.clearLayers();
    state.layers.clear();
    setStatus("Zoom in to your block to see each side of the street.");
    return;
  }
  const keys = tileKeysAround(map.getBounds());
  await loadTiles(keys);
  const view = map.getBounds().pad(0.2);
  const wanted = new Set();
  for (const s of sidesIn(keys)) {
    if (!s.parts.some((p) => p.line.some(([x, y]) => view.contains([y, x])))) continue;
    wanted.add(s.id);
    if (state.layers.has(s.id)) continue;
    const layer = L.polyline(shape(s), styleFor(s.id))
      .on("click", () => select(s))
      .bindTooltip(`${s.street} · ${s.side}`, { sticky: true });
    layer.addTo(sideLayer);
    state.layers.set(s.id, layer);
  }
  for (const [id, layer] of state.layers)
    if (!wanted.has(id) && !(state.selected && state.selected.id === id)) {
      sideLayer.removeLayer(layer);
      state.layers.delete(id);
    }
  setStatus("Tap the line on your side of the street.");
}

// ---- panel -----------------------------------------------------------------

function setStatus(text) { $("status").textContent = text; }

function fmtWindow([start, end]) {
  const tz = state.city.tz;
  const s = new Date(start), e = new Date(end);
  const day = s.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: tz });
  const hr = (d) => d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz })
    .replace(":00", "").replace(" ", "").toLowerCase();
  return `${day}, ${hr(s)}–${hr(e)}`;
}

function feedPath(s) { return `${state.city.slug}/feeds/${s.id}.ics`; }

function feedURL(s, scheme) {
  const base = new URL(feedPath(s), location.href);
  return scheme === "webcal" ? base.href.replace(/^https?:/, "webcal:") : base.href;
}

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "on") for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
    else if (v !== undefined) n.setAttribute(k, v);
  }
  n.append(...kids);
  return n;
}

function select(s, { fly = false } = {}) {
  const prev = state.selected;
  state.selected = s;
  if (prev && state.layers.has(prev.id)) state.layers.get(prev.id).setStyle(styleFor(prev.id));
  if (!state.layers.has(s.id)) {
    const layer = L.polyline(shape(s), styleFor(s.id)).addTo(sideLayer)
      .on("click", () => select(s));
    state.layers.set(s.id, layer);
  }
  const layer = state.layers.get(s.id);
  layer.setStyle(styleFor(s.id)).bringToFront();
  if (fly) map.flyToBounds(layer.getBounds(), { maxZoom: 18, padding: [60, 60] });
  history.replaceState(null, "", `#${state.city.slug}/${tileOf(s)}/${s.id}`);
  showDetail(s);
}

function showDetail(s) {
  const d = $("detail");
  const copy = el("button", { type: "button", class: "ghost", on: { click: async () => {
    await navigator.clipboard.writeText(feedURL(s, "https"));
    copy.textContent = "Copied";
    setTimeout(() => (copy.textContent = "Copy link"), 1500);
  } } }, "Copy link");
  d.replaceChildren(
    $("nearby").childElementCount ? el("button", { type: "button", class: "back", on: { click: showNearby } }, "‹ Nearby block sides") : "",
    el("h2", {}, s.street),
    el("p", { class: "limits" }, s.limits),
    el("span", { class: "pill" }, s.side),
    el("h3", {}, "No parking"),
    el("ul", {}, ...s.schedule.map((t) => el("li", {}, t))),
    el("h3", {}, "Next sweeps"),
    el("ul", {}, ...(s.next.length ? s.next.map((w) => el("li", {}, fmtWindow(w))) : [el("li", {}, "None in the next two months")])),
    el("div", { class: "actions" },
      el("a", { class: "btn", href: feedURL(s, "webcal") }, "Subscribe in Calendar"),
      el("div", { class: "row" },
        el("a", { class: "btn ghost", href: `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(feedURL(s, "webcal"))}`, target: "_blank", rel: "noopener" }, "Google Calendar"),
        copy)),
    el("p", { class: "hint" },
      "On iPhone, Subscribe opens Calendar directly. iOS hides alerts on subscribed calendars by default; ",
      "turn off Remove Alerts in the subscription's settings to get a reminder the day before."),
  );
  $("intro").hidden = true;
  $("nearby").hidden = true;
  d.hidden = false;
  if (window.matchMedia("(max-width: 899px)").matches) d.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showNearby() {
  $("detail").hidden = true;
  $("nearby").hidden = false;
}

async function nearbyFrom(lat, lon, label) {
  if (pin) pin.remove();
  pin = L.circleMarker([lat, lon], { radius: 7, color: cssVar("--accent"), fillOpacity: 1 }).addTo(map);
  map.setView([lat, lon], 18);
  const pt = [lon, lat];
  const keys = tileKeysAround(L.latLngBounds([lat, lon], [lat, lon]), 1);
  await loadTiles(keys);
  const nearbyList = sidesIn(keys)
    .map((s) => ({ s, d: distanceToSide(pt, s) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, NEARBY);
  const list = $("nearby");
  list.replaceChildren(
    el("p", { class: "nearby-h" }, `Near ${label}`),
    ...nearbyList.map(({ s, d }) => el("li", {},
      el("button", { type: "button", on: {
        click: () => select(s),
        mouseenter: () => state.layers.get(s.id)?.setStyle({ weight: 7 }),
        mouseleave: () => state.layers.get(s.id)?.setStyle(styleFor(s.id)),
      } },
        el("div", { class: "st" }, `${s.street} · ${s.side}`),
        el("div", { class: "sub" }, `${s.limits} · ${s.schedule.join("; ")} · ${Math.round(d)} m`)))));
  $("intro").hidden = true;
  $("detail").hidden = true;
  list.hidden = false;
  setStatus(nearbyList.length ? "Pick your side of the street below or on the map." : "No sweeping data near there.");
}

// ---- search ----------------------------------------------------------------

async function geocode(q) {
  const [w, s, e, n] = state.city.bounds;
  const url = new URL("https://nominatim.openstreetmap.org/search");
  url.search = new URLSearchParams({
    q: `${q}, ${state.city.name}`, format: "jsonv2", limit: "1",
    viewbox: `${w},${n},${e},${s}`, bounded: "1",
  });
  const hits = await getJSON(url);
  return hits[0];
}

$("search").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("q").value.trim();
  if (!q) return;
  setStatus("Searching…");
  try {
    const hit = await geocode(q);
    if (!hit) return setStatus(`Couldn't find “${q}” in ${state.city.name}.`);
    await nearbyFrom(+hit.lat, +hit.lon, q);
  } catch (err) {
    setStatus("Search is unavailable right now. Zoom the map to your block instead.");
  }
});

$("locate").addEventListener("click", () => {
  if (!navigator.geolocation) return setStatus("Location isn't available in this browser.");
  setStatus("Finding you…");
  navigator.geolocation.getCurrentPosition(
    (p) => nearbyFrom(p.coords.latitude, p.coords.longitude, "you"),
    () => setStatus("Couldn't get your location."),
    { enableHighAccuracy: true, timeout: 10000 });
});

map.on("moveend", redraw);

// ---- boot ------------------------------------------------------------------

async function openCity(slug) {
  state.city = await getJSON(`${slug}/meta.json`, { cache: "no-cache" });
  state.tiles.clear(); state.pending.clear(); state.layers.clear(); sideLayer.clearLayers();
  const [w, s, e, n] = state.city.bounds;
  map.fitBounds([[s, w], [n, e]]);
  $("source").replaceChildren("Data: ",
    el("a", { href: state.city.source, rel: "noopener" }, `${state.city.name} open data`),
    `. Built ${new Date(state.city.built).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}.`);
}

(async function boot() {
  const cities = await getJSON("cities.json", { cache: "no-cache" });
  const pick = $("city");
  pick.replaceChildren(...cities.map((c) => el("option", { value: c.slug }, c.name)));
  pick.hidden = cities.length < 2;
  const [slug, tile, sideId] = location.hash.slice(1).split("/");
  await openCity(cities.some((c) => c.slug === slug) ? slug : cities[0].slug);
  pick.value = state.city.slug;
  pick.addEventListener("change", () => openCity(pick.value));
  if (sideId && state.city.tiles.includes(tile)) {
    await loadTiles([tile]);  // deep link: #<city>/<tile>/<side>
    const s = state.tiles.get(tile).find((x) => x.parts.some((p) => p.id === sideId));
    if (s) select(s, { fly: true });
  }
})();
