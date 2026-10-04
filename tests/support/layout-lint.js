// layout-lint.js - measured layout lint for a rendered page. No dependencies.
// Usage (Playwright):  const report = await page.evaluate(fs.readFileSync(LINT_PATH, "utf8"));
// The file is one expression (an IIFE) so page.evaluate returns its JSON-safe result.
//
// Returns { meta, overlaps, layered, spills, beyond, fields }
//  overlaps  (a) pairs of visible interactive elements whose clipped rects intersect by > 2 px on both axes,
//                ancestor/descendant pairs excluded, and only when the point at the middle of the intersection
//                hits one of the two (elementFromPoint), so controls behind a modal or an inert region are ignored.
//                "layered" = the smaller one sits fully inside the larger (a deliberate overlay such as row
//                actions) and is listed separately for review.
//  spills    (b) elements whose scrollWidth/Height exceed clientWidth/Height by > 1 px, that are not scroll
//                containers (overflow auto/scroll) and have no effective ellipsis/line-clamp. kind=spill when the
//                content paints outside the box (overflow visible), kind=clipped when it is cut (hidden/clip).
//                Only the element whose own text or direct child exceeds its box is reported (not every ancestor).
//  beyond    (c) outermost elements whose rect leaves the nearest hidden/clip container or the viewport
//                horizontally (scroll/auto containers are by design and skipped; fully off-canvas is skipped).
//  fields    (d) inputs/textareas/selects: text (placeholder, value or selected option) measured with canvas
//                against the content box, minus any sibling icon/button rect that covers the content box;
//                reports padding-right vs the covered width (deficit > 0 means text runs under the icon).
(() => {
  const VW = document.documentElement.clientWidth;
  const VH = window.innerHeight;
  const INTERACTIVE = [
    "button", "a[href]", "input:not([type=hidden])", "select", "textarea", "summary",
    "[role=button]", "[role=option]", "[role=menuitem]", "[role=menuitemradio]", "[role=menuitemcheckbox]",
    "[role=tab]", "[role=switch]", "[role=checkbox]", "[role=radio]", "[class~=chip]", "[class*=-chip]",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  const r1 = (n) => Math.round(n * 10) / 10;
  const num = (v) => parseFloat(v) || 0;
  const cs = (el) => getComputedStyle(el);
  const box = (b) => ({ x: r1(b.l), y: r1(b.t), w: r1(b.r - b.l), h: r1(b.b - b.t) });

  function sel(el) {
    const parts = [];
    for (let n = el, d = 0; n && n.nodeType === 1 && d < 4; n = n.parentElement, d++) {
      let s = n.tagName.toLowerCase();
      if (n.id) { parts.unshift("#" + n.id); break; }
      const tid = n.getAttribute("data-testid");
      if (tid) s += `[data-testid="${tid}"]`;
      else if (n.classList.length) s += "." + [...n.classList].slice(0, 2).join(".");
      if (d === 0) {
        const al = n.getAttribute("aria-label");
        if (al) s += `[aria-label="${al.slice(0, 30)}"]`;
      }
      parts.unshift(s);
    }
    return parts.join(" > ");
  }
  const label = (el) =>
    (el.getAttribute("aria-label") || el.value || el.innerText || el.textContent || el.placeholder || "")
      .replace(/\s+/g, " ").trim().slice(0, 60);

  // Rect after clipping by overflow ancestors (honouring fixed/absolute containing blocks) and the viewport.
  // Returns { b: {l,t,r,b}, clipper } or null when the element is not visible.
  function visible(el) {
    const st = cs(el);
    if (st.display === "none" || st.visibility !== "visible" || +st.opacity === 0) return null;
    const rc = el.getBoundingClientRect();
    if (rc.width === 0 && rc.height === 0) return null;
    const closed = el.closest("details:not([open])");
    if (closed && !(el.tagName === "SUMMARY" && el.parentElement === closed) && !el.closest("details:not([open]) > summary")) return null;
    const b = { l: rc.left, t: rc.top, r: rc.right, b: rc.bottom };
    let mode = st.position === "fixed" ? "fixed" : st.position === "absolute" ? "abs" : "normal";
    let clipper = null;
    for (let p = el.parentElement; p && mode !== "fixed"; p = p.parentElement) {
      const ps = cs(p);
      if (ps.display === "none" || +ps.opacity === 0 || ps.contentVisibility === "hidden") return null;
      const isCB = ps.position !== "static" || ps.transform !== "none" || ps.filter !== "none";
      if (mode === "abs" && !isCB) continue;
      if (ps.overflowX !== "visible" || ps.overflowY !== "visible") {
        const pr = p.getBoundingClientRect();
        if (ps.overflowX !== "visible") { b.l = Math.max(b.l, pr.left); b.r = Math.min(b.r, pr.right); clipper = clipper || p; }
        if (ps.overflowY !== "visible") { b.t = Math.max(b.t, pr.top); b.b = Math.min(b.b, pr.bottom); clipper = clipper || p; }
      }
      mode = ps.position === "fixed" ? "fixed" : ps.position === "absolute" ? "abs" : "normal";
    }
    b.l = Math.max(b.l, 0); b.t = Math.max(b.t, 0); b.r = Math.min(b.r, VW); b.b = Math.min(b.b, VH);
    if (b.r - b.l <= 0 || b.b - b.t <= 0) return null;
    return { b, clipper };
  }

  // ---------- (a) interactive overlaps ----------
  const items = [];
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    if (el.closest("svg") && el.tagName.toLowerCase() !== "svg") continue;
    const v = visible(el);
    if (v) items.push({ el, b: v.b });
  }
  const overlaps = [];
  const layered = [];
  for (let i = 0; i < items.length; i++) {
    for (let j = i + 1; j < items.length; j++) {
      const a = items[i], c = items[j];
      if (a.el.contains(c.el) || c.el.contains(a.el)) continue;
      const w = Math.min(a.b.r, c.b.r) - Math.max(a.b.l, c.b.l);
      const h = Math.min(a.b.b, c.b.b) - Math.max(a.b.t, c.b.t);
      if (w <= 2 || h <= 2) continue;
      const areaA = (a.b.r - a.b.l) * (a.b.b - a.b.t), areaC = (c.b.r - c.b.l) * (c.b.b - c.b.t);
      const small = Math.min(areaA, areaC), big = Math.max(areaA, areaC);
      // Hit-test the middle of the intersection: a pair hidden behind a modal dialog, an inert
      // region or a pointer-events:none layer is not a visible overlap.
      const top = document.elementFromPoint(
        (Math.max(a.b.l, c.b.l) + Math.min(a.b.r, c.b.r)) / 2,
        (Math.max(a.b.t, c.b.t) + Math.min(a.b.b, c.b.b)) / 2,
      );
      if (!top || !(a.el.contains(top) || c.el.contains(top))) continue;
      const row = {
        a: sel(a.el), aLabel: label(a.el), aBox: box(a.b),
        b: sel(c.el), bLabel: label(c.el), bBox: box(c.b),
        overlap: { w: r1(w), h: r1(h) },
      };
      (w * h >= 0.98 * small && big >= 2 * small ? layered : overlaps).push(row);
    }
  }

  // ---------- (b) spills / clipped content, (c) beyond container ----------
  const spills = [];
  const beyond = [];
  const reportedBeyond = new Set();
  const range = document.createRange();
  function worstExcess(el, st) {
    const rc = el.getBoundingClientRect();
    const bl = num(st.borderLeftWidth), br = num(st.borderRightWidth), bt = num(st.borderTopWidth), bb = num(st.borderBottomWidth);
    const sbX = Math.max(0, el.offsetWidth - el.clientWidth - bl - br); // vertical scrollbar width
    const sbY = Math.max(0, el.offsetHeight - el.clientHeight - bt - bb);
    const pad = { l: rc.left + bl, r: rc.right - br - sbX, t: rc.top + bt, b: rc.bottom - bb - sbY };
    let x = 0, y = 0, who = "";
    const take = (r, what) => {
      if (r.width === 0 && r.height === 0) return;
      const ex = Math.max(r.right - pad.r, pad.l - r.left);
      const ey = Math.max(r.bottom - pad.b, pad.t - r.top);
      if (ex > x) { x = ex; who = what; }
      if (ey > y) { y = ey; who = who || what; }
    };
    for (const ch of el.children) {
      const cst = cs(ch);
      if (cst.display === "none" || cst.position === "fixed") continue;
      if (ch.getAttribute("role") === "separator" || /resize/.test(ch.className)) continue;
      take(ch.getBoundingClientRect(), sel(ch));
    }
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.nodeValue.trim()) continue;
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) take(r, "text");
    }
    return { x: r1(x), y: r1(y), who };
  }

  for (const el of document.body.querySelectorAll("*")) {
    const tag = el.tagName.toLowerCase();
    if (el.closest("svg") && tag !== "svg") continue;
    const st = cs(el);
    if (st.display === "inline" || st.display === "contents" || st.display === "none") continue;
    const v = visible(el);
    if (!v) continue;

    // (b) scroll overflow
    if (!["input", "textarea", "select", "svg", "img", "canvas", "video"].includes(tag) && !(el.clientWidth <= 2 && el.clientHeight <= 2)) {
      const xOver = el.scrollWidth > el.clientWidth + 1;
      const yOver = el.scrollHeight > el.clientHeight + 1;
      const xScroll = /(auto|scroll)/.test(st.overflowX);
      const yScroll = /(auto|scroll)/.test(st.overflowY);
      const ellipsisOK = st.textOverflow === "ellipsis" && st.overflowX !== "visible";
      const clamp = st.webkitLineClamp && st.webkitLineClamp !== "none";
      const x = xOver && !xScroll && !ellipsisOK;
      const y = yOver && !yScroll && !clamp;
      if (x || y) {
        const w = worstExcess(el, st);
        const realX = x && w.x > 1, realY = y && w.y > 1;
        if (realX || realY) {
          spills.push({
            sel: sel(el), text: label(el),
            kind: (realX ? st.overflowX : st.overflowY) === "visible" ? "spill" : "clipped",
            overflow: `${st.overflowX}/${st.overflowY}`, whiteSpace: st.whiteSpace, textOverflow: st.textOverflow,
            scroll: { w: el.scrollWidth, h: el.scrollHeight }, client: { w: el.clientWidth, h: el.clientHeight },
            excess: { x: realX ? w.x : 0, y: realY ? w.y : 0 }, culprit: w.who, box: box(v.b),
          });
        }
      }
    }

    // (c) beyond nearest hidden/clip container or the viewport (outermost offender only)
    const rc = el.getBoundingClientRect();
    if (rc.width === 0 || rc.height === 0) continue;
    let skip = false;
    for (let p = el.parentElement; p; p = p.parentElement) if (reportedBeyond.has(p)) { skip = true; break; }
    if (skip) continue;
    const clipper = v.clipper;
    if (clipper) {
      const cst = cs(clipper);
      if (/(auto|scroll)/.test(cst.overflowX) || st.position === "fixed") continue;
      if (cst.overflowX === "visible") continue;
      const pr = clipper.getBoundingClientRect();
      const ex = Math.max(rc.right - pr.right, pr.left - rc.left);
      if (ex > 1 && rc.left < pr.right && rc.right > pr.left) {
        beyond.push({ sel: sel(el), text: label(el), container: sel(clipper), kind: "cut by " + cst.overflowX + " container", excess: r1(ex), box: box({ l: rc.left, t: rc.top, r: rc.right, b: rc.bottom }) });
        reportedBeyond.add(el);
      }
    } else if ((rc.right > VW + 1 || rc.left < -1) && rc.right > 0 && rc.left < VW) {
      beyond.push({ sel: sel(el), text: label(el), container: "viewport", kind: "beyond viewport", excess: r1(Math.max(rc.right - VW, -rc.left)), box: box({ l: rc.left, t: rc.top, r: rc.right, b: rc.bottom }) });
      reportedBeyond.add(el);
    }
  }
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + 1) {
    beyond.unshift({ sel: "html", text: "", container: "viewport", kind: "page scrolls horizontally", excess: de.scrollWidth - de.clientWidth, box: box({ l: 0, t: 0, r: de.scrollWidth, b: de.scrollHeight }) });
  }

  // ---------- (d) fields: text vs padding vs sibling icons ----------
  const ctx = document.createElement("canvas").getContext("2d");
  const fields = [];
  const FIELDS = "input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea, select";
  for (const f of document.querySelectorAll(FIELDS)) {
    const v = visible(f);
    if (!v) continue;
    const st = cs(f), rc = f.getBoundingClientRect();
    if (rc.width <= 2 || rc.height <= 2) continue;
    const content = {
      l: rc.left + num(st.borderLeftWidth) + num(st.paddingLeft),
      r: rc.right - num(st.borderRightWidth) - num(st.paddingRight),
      t: rc.top + num(st.borderTopWidth) + num(st.paddingTop),
      b: rc.bottom - num(st.borderBottomWidth) - num(st.paddingBottom),
    };
    const tag = f.tagName.toLowerCase();
    const text = tag === "select" ? (f.selectedOptions[0]?.text || "") : (f.value || f.placeholder || "");
    const isPlaceholder = tag !== "select" && !f.value && !!f.placeholder;
    ctx.font = `${st.fontStyle} ${st.fontWeight} ${st.fontSize} ${st.fontFamily}`;
    const textW = ctx.measureText(text).width + num(st.letterSpacing) * text.length;
    const arrow = tag === "select" ? 20 : 0;
    const avail = content.r - content.l - arrow;
    // sibling icons / controls covering the content box
    let coveredRight = 0, coveredLeft = 0;
    const covers = [];
    for (const it of items) {
      if (it.el === f || it.el.contains(f) || f.contains(it.el)) continue;
      const w = Math.min(it.b.r, content.r) - Math.max(it.b.l, content.l);
      const h = Math.min(it.b.b, content.b) - Math.max(it.b.t, content.t);
      if (w <= 2 || h <= 2) continue;
      // Only what is painted over the field counts: a control behind a modal or another region is not on top of it.
      const top = document.elementFromPoint(Math.max(it.b.l, content.l) + w / 2, Math.max(it.b.t, content.t) + h / 2);
      if (!top || !it.el.contains(top)) continue;
      const fromRight = Math.max(0, content.r - it.b.l), fromLeft = Math.max(0, it.b.r - content.l);
      if (it.b.l + it.b.r > content.l + content.r) coveredRight = Math.max(coveredRight, fromRight); else coveredLeft = Math.max(coveredLeft, fromLeft);
      covers.push({ sel: sel(it.el), label: label(it.el), box: box(it.b), coversPx: r1(Math.min(fromRight, fromLeft)) });
    }
    const wraps = tag === "textarea" && !/(nowrap|pre\b)/.test(st.whiteSpace);
    const free = avail - coveredRight - coveredLeft;
    const textClipped = !wraps && text && textW > avail + 1;
    const textCovered = !wraps && text && covers.length > 0 && textW > free + 1;
    const deficit = r1(Math.max(coveredRight - num(st.paddingRight), coveredLeft - num(st.paddingLeft), 0));
    if (textClipped || textCovered || deficit > 0) {
      fields.push({
        sel: sel(f), tag, text: text.slice(0, 80), isPlaceholder, textWidth: r1(textW), availWidth: r1(avail),
        paddingRight: num(st.paddingRight), coveredRight: r1(coveredRight), coveredLeft: r1(coveredLeft),
        deficit, textClipped: !!textClipped, textRunsUnderIcon: !!textCovered, whiteSpace: st.whiteSpace,
        box: box({ l: rc.left, t: rc.top, r: rc.right, b: rc.bottom }), covers: covers.slice(0, 4),
      });
    }
  }

  return {
    meta: {
      url: location.pathname + location.search, vw: VW, vh: VH,
      palette: document.documentElement.dataset.palette || "", interactive: items.length,
      pageScrollX: de.scrollWidth - de.clientWidth, bodyScrollX: document.body.scrollWidth - document.body.clientWidth,
    },
    overlaps, layered, spills, beyond, fields,
  };
})()
