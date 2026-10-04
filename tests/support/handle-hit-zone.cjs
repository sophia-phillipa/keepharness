// Measures what a pointer can actually grab around each resize handle (WCAG 2.5.8, 24 px targets).
// A handle may be drawn thin, as long as its invisible hit area (a ::before) is at least 24 px in both
// directions and never sits on another control. The zone is the bounding box of the points near the
// handle where elementFromPoint returns the handle (a pseudo-element resolves to its element), so what is
// measured is what a click reaches; a control is covered when, with the handle made transparent to the
// pointer, the point inside the zone lands on that control.
//   const zones = await handleHitZones(page, "#activity-panel-resize");
// returns, for each visible match, { name, zone: { left, top, right, bottom, width, height } | null,
// covers: [{ name, width, height }] } (a cover is listed when it spans more than 2 px both ways).
const HANDLES = '[role="separator"], .workspace-resize, .run-console-resizer';
const REACH = 30; // how far beyond the drawn handle the search looks, in px
const FIND_STEP = 24; // sampling step along the handle's length while finding the zone
const SCAN_STEP = 3; // sampling step over the zone while looking for covered controls
const CONTROLS = 'button, a[href], input, select, textarea, summary, [role="tab"]';

const MEASURE = `(selector, controlSelector, reach, findStep, scanStep) => {
  const out = [];
  const maxX = document.documentElement.clientWidth - 1, maxY = window.innerHeight - 1;
  const span = (from, to, step) => { const v = []; for (let p = from; p < to; p += step) v.push(p); v.push(to - 1); return v; };
  for (const handle of document.querySelectorAll(selector)) {
    if (!handle.checkVisibility()) continue;
    const r = handle.getBoundingClientRect();
    const wide = r.width >= r.height; // a row handle runs across; a column handle runs down
    const owns = (x, y) => x >= 0 && y >= 0 && x <= maxX && y <= maxY && handle.contains(document.elementFromPoint(x, y));
    const xs = wide ? span(Math.ceil(r.left) + 1, Math.floor(r.right), findStep) : span(Math.floor(r.left) - reach, Math.ceil(r.right) + reach, 1);
    const ys = wide ? span(Math.floor(r.top) - reach, Math.ceil(r.bottom) + reach, 1) : span(Math.ceil(r.top) + 1, Math.floor(r.bottom), findStep);
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (const x of xs) for (const y of ys) if (owns(x, y)) { left = Math.min(left, x); right = Math.max(right, x + 1); top = Math.min(top, y); bottom = Math.max(bottom, y + 1); }
    const name = handle.getAttribute("aria-label") || handle.id || handle.className;
    if (!Number.isFinite(left)) { out.push({ name, zone: null, covers: [] }); continue; }
    const seen = new Map();
    const saved = handle.style.pointerEvents;
    const points = [];
    for (const x of span(left, right, scanStep)) for (const y of span(top, bottom, scanStep)) if (owns(x, y)) points.push([x, y]);
    handle.style.pointerEvents = "none";
    for (const [x, y] of points) {
      const control = document.elementFromPoint(x, y)?.closest(controlSelector);
      if (!control || handle.contains(control) || control.contains(handle)) continue;
      const box = seen.get(control) || { left: x, right: x, top: y, bottom: y };
      box.left = Math.min(box.left, x); box.right = Math.max(box.right, x); box.top = Math.min(box.top, y); box.bottom = Math.max(box.bottom, y);
      seen.set(control, box);
    }
    handle.style.pointerEvents = saved;
    const covers = [...seen].map(([control, b]) => ({ name: control.getAttribute("aria-label") || control.id || control.className || control.tagName, width: b.right - b.left, height: b.bottom - b.top })).filter((c) => c.width > 2 && c.height > 2);
    out.push({ name, zone: { left, top, right, bottom, width: right - left, height: bottom - top }, covers });
  }
  return out;
}`;

async function handleHitZones(page, selector = HANDLES) {
  return page.evaluate(`(${MEASURE})(${JSON.stringify(selector)}, ${JSON.stringify(CONTROLS)}, ${REACH}, ${FIND_STEP}, ${SCAN_STEP})`);
}

// Every handle's hit area is at least 24 px both ways and covers no control.
function assertHitAreas(zones) {
  const assert = require("node:assert/strict");
  assert(zones.length, "no resize handle found");
  for (const { name, zone, covers } of zones) {
    assert(zone && zone.width >= 24 && zone.height >= 24, `${name} hit area ${JSON.stringify(zone)}`);
    assert.deepEqual(covers, [], `${name} hit area covers controls`);
  }
}

module.exports = { handleHitZones, assertHitAreas };
