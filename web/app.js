// Sweep Calendar front end: find a block side on the map, subscribe to its feed.
// Data is static: <city>/meta.json, <city>/tiles/<x>_<y>.json, <city>/feeds/<id>.ics.
"use strict";

const MIN_ZOOM = 16;        // below this, block sides are too dense to tap
const OFFSET_M = 5;         // draw each curb this many meters off the centerline
const NEARBY = 6;
const NEAR_SIDE_M = 200;    // farther than this from any block side: no schedule here
const JUST_OUTSIDE_M = 3000; // closer than this to a city's edge reads "just outside"
const REPO = "https://github.com/ocarlsen/sweep-calendar";

const $ = (id) => document.getElementById(id);
const state = { cities: [], city: null, tiles: new Map(), pending: new Map(), layers: new Map(), selected: null };

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
    if ($("notice").hidden) setStatus("Zoom in to your block to see each side of the street.");
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
  if ($("notice").hidden) setStatus("Tap the line on your side of the street.");
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
    el("h3", {}, s.advisory ? "Street sweeping" : "No parking"),
    el("ul", {}, ...s.schedule.map((t) => el("li", {}, t))),
    el("h3", {}, "Next sweeps"),
    el("ul", {}, ...(s.next.length ? s.next.map((w) => el("li", {}, fmtWindow(w))) : [el("li", {}, "None in the next two months")])),
    el("div", { class: "actions" },
      el("a", { class: "btn", href: feedURL(s, "webcal") }, "Subscribe in Calendar"),
      el("div", { class: "row" },
        el("a", { class: "btn ghost", href: `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(feedURL(s, "webcal"))}`, target: "_blank", rel: "noopener" }, "Google Calendar"),
        copy)),
    s.advisory ? el("p", { class: "hint" },
      "This block isn't posted, so there are no tickets, but the city asks you to move your car so the curb gets swept.") : "",
    el("p", { class: "hint" },
      "iPhone: Subscribe opens Calendar. iOS hides alerts on subscribed calendars by default; turn off ",
      "Remove Alerts in the subscription's settings to get a reminder the day before. ",
      "Android: tap Google Calendar. Outlook and others: Copy link, then add a calendar from the internet."),
    el("p", { class: "hint" },
      "Sign says something different? ",
      el("a", { href: problemURL(s), target: "_blank", rel: "noopener" }, "Report a problem")),
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
    .filter(({ d }) => d <= NEAR_SIDE_M)
    .sort((a, b) => a.d - b.d)
    .slice(0, NEARBY);
  if (!nearbyList.length) {
    $("nearby").hidden = true; $("detail").hidden = true; $("intro").hidden = false;
    return notice("info", "No sweeping schedule found here.",
      "That doesn't mean parking is unrestricted. Check the posted signs.");
  }
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
  setStatus("Pick your side of the street below or on the map.");
}

// ---- coverage --------------------------------------------------------------


function polygons(geom) {
  return geom.type === "MultiPolygon" ? geom.coordinates : [geom.coordinates];
}

// Inside a polygon = inside its outer ring and outside its holes (San Jose
// has unincorporated county pockets within its limits).
function inArea(pt, geom) {
  return polygons(geom).some(([outer, ...holes]) => inRing(pt, outer) && !holes.some((h) => inRing(pt, h)));
}

function inRing([x, y], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function cityArea(c) {
  if (c.boundary) return c.boundary;
  const [w, s, e, n] = c.bounds;  // no boundary shipped: fall back to the data's extent
  return { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] };
}

// Which covered city a point is in, or the nearest one and how far outside it.
function locate(pt) {
  let nearest = null;
  for (const c of state.cities) {
    const area = cityArea(c);
    if (inArea(pt, area)) return { city: c, inside: true, d: 0 };
    const d = Math.min(...polygons(area).flat().map((r) => distanceToLine(pt, r)));
    if (!nearest || d < nearest.d) nearest = { city: c, inside: false, d };
  }
  return nearest;
}

function requestURL(place) {
  const title = place ? `Add ${place}` : "Add my city";
  const body = `I'd use Sweep Calendar in ${place || "[your city]"}.\n\n` +
    "(Please don't post your home address here; issues are public.)";
  return `${REPO}/issues/new?${new URLSearchParams({ title, body, labels: "city-request" })}`;
}

// A schedule that disagrees with the curb becomes a public, prefilled issue
// carrying everything needed to find the block; no personal data.
function problemURL(s) {
  const page = new URL(`#${state.city.slug}/${tileOf(s)}/${s.id}`, location.href.split("#")[0]).href;
  const title = `Schedule problem: ${s.street}, ${s.limits} (${s.side}), ${state.city.name}`;
  const body = [
    `Block: ${s.street}, ${s.limits}, ${s.side} (${state.city.slug}/${s.id})`,
    `Shown on the site: ${s.schedule.join("; ")}`,
    `Link: ${page}`,
    "",
    "What does the posted sign say? (A photo of the sign helps.)",
    "",
  ].join("\n");
  return `${REPO}/issues/new?${new URLSearchParams({ title, body, labels: "data-problem" })}`;
}

function cityButtons(prefix = "") {
  return state.cities.map((c) => el("button", {
    type: "button", class: "ghost", on: { click: () => goToCity(c.slug) },
  }, `${prefix}${c.name}`));
}

// The notice sits between the search box and the map, so it is the first
// thing seen on a phone. kind: "warn" (outside coverage) or "info".
function notice(kind, title, text, actions = []) {
  const n = $("notice");
  n.className = `notice ${kind}`;
  n.replaceChildren(
    el("button", { type: "button", class: "close", "aria-label": "Dismiss", on: { click: clearNotice } }, "×"),
    el("p", { class: "notice-h" }, title),
    text ? el("p", {}, text) : "",
    actions.length ? el("div", { class: "notice-actions" }, ...actions) : "");
  n.hidden = false;
  setStatus("");
}

function clearNotice() { $("notice").hidden = true; }

function coveredList() {
  const names = state.cities.map((c) => c.name);
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

function placeName(hit) {
  const a = (hit && hit.address) || {};
  return a.city || a.town || a.village || a.hamlet || a.suburb || a.county || "";
}

function showOutside(lat, lon, where, place) {
  if (pin) pin.remove();
  pin = L.circleMarker([lat, lon], { radius: 8, color: cssVar("--muted"), fillColor: cssVar("--muted"), fillOpacity: 0.9 }).addTo(map);
  map.setView([lat, lon], 14);
  const title = where.d < JUST_OUTSIDE_M
    ? `${place ? `That's in ${place}, j` : "J"}ust outside ${where.city.name}.`
    : `${place || "That address"} isn't covered yet.`;
  notice("warn", title, `Sweep Calendar covers ${coveredList()}.`, [
    ...cityButtons("Go to "),
    el("a", { class: "btn ghost", href: requestURL(place), target: "_blank", rel: "noopener" },
      place ? `Request ${place}` : "Request your city"),
  ]);
  $("nearby").hidden = true; $("detail").hidden = true; $("intro").hidden = false;
}

// Route a found point: into the right city's block sides, or to the notice.
async function arrive(lat, lon, label, hit, { quiet = false } = {}) {
  const where = locate([lon, lat]);
  if (quiet && !where.inside) return;  // auto-locate: never greet a visitor with "not covered"
  if (!where.inside) {
    // Within a few blocks of the edge, real block sides beat a polygon test.
    if (where.d < NEAR_SIDE_M && where.city.slug === state.city.slug) return nearbyFrom(lat, lon, label);
    return showOutside(lat, lon, where, placeName(hit));
  }
  if (where.city.slug !== state.city.slug) {
    await openCity(where.city.slug, { fit: false });
    if (!quiet) notice("info", `Switched to ${where.city.name}.`, "");
  } else {
    clearNotice();
  }
  await nearbyFrom(lat, lon, label);
}

// ---- search ----------------------------------------------------------------

let lastCall = 0;
async function nominatim(params) {
  // Nominatim's usage policy: at most one request per second.
  const wait = lastCall + 1100 - Date.now();
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastCall = Date.now();
  const url = new URL("https://nominatim.openstreetmap.org/search");
  url.search = new URLSearchParams({ format: "jsonv2", limit: "1", addressdetails: "1", countrycodes: "us", ...params });
  return (await getJSON(url))[0];
}

// Covered cities first (current one leading), each bounded to its own area so
// "1000 Valencia St" means San Francisco's; then anywhere in the US, so an
// address we don't cover is found and named rather than reported missing.
async function geocode(q) {
  const order = [state.city, ...state.cities.filter((c) => c.slug !== state.city.slug)];
  for (const c of order) {
    const [w, s, e, n] = c.bounds;
    const hit = await nominatim({ q, viewbox: `${w},${n},${e},${s}`, bounded: "1" });
    if (hit) return hit;
  }
  return nominatim({ q });
}

$("search").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("q").value.trim();
  if (!q) return;
  clearNotice();
  setStatus("Searching…");
  try {
    const hit = await geocode(q);
    if (!hit) {
      return notice("info", `Couldn't find “${q}”.`,
        "Try a street number and name, like 1000 Valencia St, or zoom the map to your block.");
    }
    await arrive(+hit.lat, +hit.lon, q, hit);
  } catch (err) {
    setStatus("Search is unavailable right now. Zoom the map to your block instead.");
  }
});

$("locate").addEventListener("click", () => {
  if (!navigator.geolocation) return setStatus("Location isn't available in this browser.");
  setStatus("Finding you…");
  navigator.geolocation.getCurrentPosition(
    (p) => arrive(p.coords.latitude, p.coords.longitude, "you"),
    () => setStatus("Couldn't get your location."),
    { enableHighAccuracy: true, timeout: 10000 });
});

map.on("moveend", redraw);

// ---- cities ------------------------------------------------------------------

const outlineLayer = L.layerGroup().addTo(map);

function drawOutlines() {
  outlineLayer.clearLayers();
  for (const c of state.cities) {
    if (!c.boundary) continue;
    L.geoJSON(c.boundary, {
      interactive: false,
      style: { color: cssVar("--accent"), weight: 2.5, opacity: 0.85, dashArray: "8 6",
               fillColor: cssVar("--accent"), fillOpacity: 0.05 },
    }).addTo(outlineLayer);
  }
}

const REQUEST = "__request__";

// A plain <select> scales past a handful of cities and is native on phones.
// Its last option opens the city-request issue and snaps back.
function renderPicker() {
  const pick = el("select", { id: "city", "aria-label": "City", on: { change: () => {
    if (pick.value === REQUEST) {
      window.open(requestURL(""), "_blank", "noopener");
      pick.value = state.city.slug;
    } else {
      goToCity(pick.value);
    }
  } } },
    ...state.cities.map((c) => el("option", { value: c.slug }, c.name)),
    el("option", { value: REQUEST }, "Request a city…"));
  pick.value = state.city.slug;
  $("cities").replaceChildren(el("label", { for: "city", class: "city-label" }, "Sweeping schedules for"), pick);
}

async function goToCity(slug) {
  clearNotice();
  if (pin) { pin.remove(); pin = null; }
  await openCity(slug);
}

async function openCity(slug, { fit = true } = {}) {
  if (!state.city || state.city.slug !== slug) {
    state.city = await getJSON(`${slug}/meta.json`, { cache: "no-cache" });
    state.tiles.clear(); state.pending.clear(); state.layers.clear(); sideLayer.clearLayers();
    state.selected = null;
  }
  renderPicker();
  if (fit) {
    const [w, s, e, n] = state.city.bounds;
    map.fitBounds([[s, w], [n, e]]);
  }
  $("source").replaceChildren("Data: ",
    el("a", { href: state.city.source, rel: "noopener" }, `${state.city.name} open data`),
    `. Built ${new Date(state.city.built).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}.`);
}

(async function boot() {
  state.cities = await getJSON("cities.json", { cache: "no-cache" });
  drawOutlines();
  const [slug, tile, sideId] = location.hash.slice(1).split("/");
  await openCity(state.cities.some((c) => c.slug === slug) ? slug : state.cities[0].slug);
  if (sideId && state.city.tiles.includes(tile)) {
    await loadTiles([tile]);  // deep link: #<city>/<tile>/<side>
    const s = state.tiles.get(tile).find((x) => x.parts.some((p) => p.id === sideId));
    if (s) return select(s, { fly: true });
  }
  autoLocate();
})();

// Start where the visitor is. The browser asks permission once; a refusal,
// a timeout, or a location outside every covered city leaves the city view.
function autoLocate() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    (p) => { if (!pin && $("q").value === "") arrive(p.coords.latitude, p.coords.longitude, "you", null, { quiet: true }); },
    () => {},
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 10 * 60 * 1000 });
}
