#!/usr/bin/env node
'use strict';
// Rebuilds the KeepHarness brand assets: the icon is drawn from the geometry below (no input image),
// the splash is Gemini's original splash image with the new icon placed over its own.
//   node scripts/brand-assets.cjs <splash.jpeg>
// Needs Node 22.2+ and ffmpeg/ffprobe on PATH, nothing else. See desktop/build/README.md.
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const ROOT = path.resolve(__dirname, '..');
const BUILD = path.join(ROOT, 'desktop', 'build');
const WEB = path.join(ROOT, 'harness_ui', 'assets');

// The icon drawn in the 2624x1632 splash (centre, half-size) and how far the new one overlaps it.
const SPLASH_ICON = { cx: 1312, cy: 694.8, a: 284.2, cover: 2.5 };
const SPLASH_SIZE = { w: 1600, h: 1000, maxBytes: 400 * 1000 };
const LINUX_SIZES = [16, 24, 32, 48, 64, 128, 256, 512];
const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256];
const FAVICON_SIZES = [16, 32, 48];
const APPLE_TOUCH_SIZE = 180;
const ICNS_TYPES = { 128: 'ic07', 256: 'ic08', 512: 'ic09', 1024: 'ic10' };
const SMALL_MAX = 32; // up to this size the mark is thickened and snapped to the pixel grid

// ---- image I/O (ffmpeg decodes and encodes JPEG; PNG is written here with zlib) ----

function decode(file) {
  const dims = execFileSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=width,height', '-of', 'csv=p=0', file]).toString().trim();
  const [w, h] = dims.split(',').map(Number);
  const raw = execFileSync('ffmpeg', ['-v', 'error', '-i', file, '-f', 'rawvideo',
    '-pix_fmt', 'rgba', '-'], { maxBuffer: 1 << 30 });
  return { w, h, px: Float32Array.from(raw) };
}

function encodeJpeg(img, maxBytes) {
  const raw = Buffer.alloc(img.w * img.h * 3);
  for (let i = 0, o = 0; i < img.w * img.h * 4; i += 4) {
    for (let c = 0; c < 3; c++) raw[o++] = Math.min(255, Math.max(0, Math.round(img.px[i + c])));
  }
  for (let q = 2; q <= 12; q++) {
    const jpeg = execFileSync('ffmpeg', ['-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
      '-s', `${img.w}x${img.h}`, '-i', '-', '-frames:v', '1', '-q:v', String(q),
      '-huffman', 'optimal', '-f', 'mjpeg', '-'], { input: raw, maxBuffer: 1 << 28 });
    if (jpeg.length <= maxBytes) return jpeg;
  }
  throw new Error(`splash does not fit in ${maxBytes} bytes`);
}

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

function encodePng(img, { alpha = true } = {}) {
  const bpp = alpha ? 4 : 3, stride = img.w * bpp;
  const rows = [];
  for (let y = 0; y < img.h; y++) {
    const cur = new Uint8Array(stride);
    for (let x = 0; x < img.w; x++) {
      for (let c = 0; c < bpp; c++) {
        cur[x * bpp + c] = Math.min(255, Math.max(0, Math.round(img.px[(y * img.w + x) * 4 + c])));
      }
    }
    rows.push(cur);
  }
  const out = Buffer.alloc((stride + 1) * img.h);
  rows.forEach((cur, y) => {
    const prev = y ? rows[y - 1] : null;
    let best = null, bestCost = Infinity;
    for (let f = 0; f < 5; f++) {
      const line = new Uint8Array(stride);
      let cost = 0;
      for (let i = 0; i < stride; i++) {
        const a = i >= bpp ? cur[i - bpp] : 0, b = prev ? prev[i] : 0;
        const c = prev && i >= bpp ? prev[i - bpp] : 0;
        line[i] = (cur[i] - [0, a, b, (a + b) >> 1, paeth(a, b, c)][f]) & 255;
        cost += line[i] < 128 ? line[i] : 256 - line[i];
      }
      if (cost < bestCost) [best, bestCost] = [{ f, line }, cost];
    }
    out[y * (stride + 1)] = best.f;
    out.set(best.line, y * (stride + 1) + 1);
  });
  const chunk = (type, data) => {
    const head = Buffer.alloc(8), crc = Buffer.alloc(4);
    head.writeUInt32BE(data.length, 0);
    head.write(type, 4, 'ascii');
    crc.writeUInt32BE(zlib.crc32(Buffer.concat([head.subarray(4), data])), 0);
    return Buffer.concat([head, data, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(img.w, 0);
  ihdr.writeUInt32BE(img.h, 4);
  ihdr.set([8, alpha ? 6 : 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(out, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}

// ---- containers ----

function ico(entries) {
  const head = Buffer.alloc(6 + 16 * entries.length);
  head.writeUInt16LE(1, 2);
  head.writeUInt16LE(entries.length, 4);
  let offset = head.length;
  entries.forEach(({ size, png }, i) => {
    const at = 6 + 16 * i;
    head[at] = head[at + 1] = size >= 256 ? 0 : size;
    head.writeUInt16LE(1, at + 4); // colour planes
    head.writeUInt16LE(32, at + 6); // bits per pixel
    head.writeUInt32LE(png.length, at + 8);
    head.writeUInt32LE(offset, at + 12);
    offset += png.length;
  });
  return Buffer.concat([head, ...entries.map((e) => e.png)]);
}

function icns(entries) {
  const body = Buffer.concat(entries.map(({ type, png }) => {
    const head = Buffer.alloc(8);
    head.write(type, 0, 'ascii');
    head.writeUInt32BE(8 + png.length, 4);
    return Buffer.concat([head, png]);
  }));
  const head = Buffer.alloc(8);
  head.write('icns', 0, 'ascii');
  head.writeUInt32BE(8 + body.length, 4);
  return Buffer.concat([head, body]);
}

// ---- resampling: separable Lanczos-3 over a source box, widened when shrinking ----

const lanczos = (x) => {
  if (x === 0) return 1;
  if (Math.abs(x) >= 3) return 0;
  const px = Math.PI * x;
  return (3 * Math.sin(px) * Math.sin(px / 3)) / (px * px);
};

function axisWeights(outN, srcN, start, length) {
  const scale = length / outN, widen = Math.max(scale, 1);
  return Array.from({ length: outN }, (_, i) => {
    const centre = start + (i + 0.5) * scale;
    const idx = [], w = [];
    let sum = 0;
    for (let j = Math.ceil(centre - 3 * widen - 0.5); j <= Math.floor(centre + 3 * widen - 0.5); j++) {
      const weight = lanczos((j + 0.5 - centre) / widen);
      idx.push(Math.min(Math.max(j, 0), srcN - 1));
      w.push(weight);
      sum += weight;
    }
    return { idx, w: w.map((v) => v / sum) };
  });
}

function resize(img, outW, outH, box) {
  const wx = axisWeights(outW, img.w, box.x0, box.w), wy = axisWeights(outH, img.h, box.y0, box.h);
  const rows = new Set(wy.flatMap((v) => v.idx));
  const mid = new Float32Array(outW * img.h * 4);
  for (const y of rows) {
    for (let x = 0; x < outW; x++) {
      const { idx, w } = wx[x];
      for (let c = 0; c < 4; c++) {
        let acc = 0;
        for (let k = 0; k < idx.length; k++) acc += w[k] * img.px[(y * img.w + idx[k]) * 4 + c];
        mid[(y * outW + x) * 4 + c] = acc;
      }
    }
  }
  const px = new Float32Array(outW * outH * 4);
  for (let y = 0; y < outH; y++) {
    const { idx, w } = wy[y];
    for (let x = 0; x < outW; x++) {
      for (let c = 0; c < 4; c++) {
        let acc = 0;
        for (let k = 0; k < idx.length; k++) acc += w[k] * mid[(idx[k] * outW + x) * 4 + c];
        px[(y * outW + x) * 4 + c] = acc;
      }
    }
  }
  return { w: outW, h: outH, px };
}

// ---- the mark: one geometry description, written as SVG and rasterized with analytic anti-aliasing ----

// Sophia's "Design 2" (Gemini reference, 2048 px), measured in squircle widths from its top-left corner:
// a thick coral ring cut open around three solid teal nodes, joined in a triangle by thick teal bars.
const COLORS = { navy: '#071f43', teal: '#3ecdbe', coral: '#fc785d' };
const DESIGN = {
  corner: 0.43, // squircle corner radius, in half-widths
  ring: { cx: 0.5, cy: 0.5134, r: 0.3693, w: 0.0704 }, // coral ring: centre, centre-line radius, stroke width
  nodes: [[0.5, 0.1791], [0.2, 0.688], [0.8, 0.688]], // teal disc centres
  nodeR: 0.1023,
  gap: 0.0345, // navy gap between a node and the ring or bars around it
  barW: 0.055,
  box: { x: 0.075, y: 0.0723, size: 0.85 }, // crop of the mark-only variant
};
// Up to SMALL_MAX px the strokes are thickened and every edge snaps to the pixel grid, so the
// ring and the nodes stay crisp. The in-app symbol is thickened too but not snapped.
const THICK = { ring: 1.45, bar: 1.7, node: 1.12, minGap: 1 };

const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

// `size` px is the width of the squircle (the whole canvas, edge to edge); `crop` frames the mark only.
function geometry(size, { crop = false, thick = size <= SMALL_MAX, snap = size <= SMALL_MAX } = {}) {
  const d = DESIGN, unit = crop ? size / d.box.size : size;
  const at = (u, v) => (crop ? [(u - d.box.x) * unit, (v - d.box.y) * unit] : [u * unit, v * unit]);
  const round = (v) => (snap ? Math.round(v) : v);
  const mult = thick ? THICK : { ring: 1, bar: 1, node: 1, minGap: 0 };
  const [rcx, rcy] = at(d.ring.cx, d.ring.cy).map(round);
  const outer = round((d.ring.r + (d.ring.w * mult.ring) / 2) * unit), inner = round((d.ring.r - (d.ring.w * mult.ring) / 2) * unit);
  const nodeR = round(d.nodeR * mult.node * unit);
  const haloR = nodeR + Math.max(d.gap * unit, mult.minGap);
  const centres = d.nodes.map(([u, v]) => at(u, v).map(round));
  const [a, b, c] = centres;
  const barW = Math.max(round(d.barW * mult.bar * unit), snap ? 1 : 0);
  return {
    size,
    corner: d.corner * (size / 2),
    ring: { cx: rcx, cy: rcy, r: (outer + inner) / 2, w: outer - inner },
    bars: [[a, b], [a, c], [b, c]].map(([p, q]) => ({ x1: p[0], y1: p[1], x2: q[0], y2: q[1], w: barW })),
    nodes: centres.map(([x, y]) => ({ cx: x, cy: y, r: nodeR })),
    halos: centres.map(([x, y]) => ({ cx: x, cy: y, r: haloR })),
  };
}

const num = (v) => String(+v.toFixed(2));
// The SVG markup of a geometry. shape: 'rounded' (with the squircle) or 'none' (mark only).
function svgBody(g, shape, maskId) {
  const s = num(g.size), fill = (c) => `fill="${COLORS[c]}"`, stroke = (c, w) => `fill="none" stroke="${COLORS[c]}" stroke-width="${num(w)}"`;
  const circle = (o, extra) => `<circle cx="${num(o.cx)}" cy="${num(o.cy)}" r="${num(o.r)}" ${extra}/>`;
  const back = shape === 'rounded' ? `<rect width="${s}" height="${s}" rx="${num(g.corner)}" ${fill('navy')}/>` : '';
  const gaps = g.halos.map((h) => circle(h, 'fill="#000"')).join('');
  const bars = g.bars.map((b) => `<line x1="${num(b.x1)}" y1="${num(b.y1)}" x2="${num(b.x2)}" y2="${num(b.y2)}" ${stroke('teal', b.w)}/>`).join('');
  return `<defs><mask id="${maskId}" maskUnits="userSpaceOnUse" x="0" y="0" width="${s}" height="${s}"><rect width="${s}" height="${s}" fill="#fff"/>${gaps}</mask></defs>`
    + `${back}<g mask="url(#${maskId})">${circle(g.ring, stroke('coral', g.ring.w))}${bars}</g>${g.nodes.map((n) => circle(n, fill('teal'))).join('')}`;
}
const svgFile = (g, shape) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${num(g.size)} ${num(g.size)}" width="${num(g.size)}" height="${num(g.size)}"><title>KeepHarness</title>${svgBody(g, shape, 'gaps')}</svg>\n`;

// Signed distance (positive outside) to a rounded rectangle, a ring's centre line, a disc and a flat-ended bar.
function roundRect(x, y, cx, cy, a, r) {
  const qx = Math.abs(x - cx) - (a - r), qy = Math.abs(y - cy) - (a - r);
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
}
function barDistance(x, y, b) {
  const dx = b.x2 - b.x1, dy = b.y2 - b.y1, len = Math.hypot(dx, dy);
  const t = ((x - b.x1) * dx + (y - b.y1) * dy) / len, s = ((x - b.x1) * dy - (y - b.y1) * dx) / len;
  const qx = Math.abs(t - len / 2) - len / 2, qy = Math.abs(s) - b.w / 2;
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0);
}
const cover = (distance) => Math.min(1, Math.max(0, 0.5 - distance)); // one-pixel anti-aliased edge

// Rasterizes a geometry onto a square `canvas` with its top-left corner at (ox, oy) (fractions allowed).
function render(g, { canvas = g.size, ox = 0, oy = 0, shape = 'rounded' } = {}) {
  const px = new Float32Array(canvas * canvas * 4);
  const [navy, teal, coral] = [COLORS.navy, COLORS.teal, COLORS.coral].map(hexToRgb);
  for (let j = 0; j < canvas; j++) {
    for (let i = 0; i < canvas; i++) {
      const x = i + 0.5 - ox, y = j + 0.5 - oy;
      const out = [0, 0, 0, 0];
      const over = (colour, k) => {
        if (k <= 0) return;
        const alpha = k + out[3] * (1 - k);
        for (let n = 0; n < 3; n++) out[n] = (colour[n] * k + out[n] * out[3] * (1 - k)) / alpha;
        out[3] = alpha;
      };
      const inside = x >= 0 && y >= 0 && x <= g.size && y <= g.size;
      if (shape === 'rounded') over(navy, cover(roundRect(x, y, g.size / 2, g.size / 2, g.size / 2, g.corner)));
      else if (shape === 'square' && inside) over(navy, 1);
      const open = g.halos.reduce((acc, h) => acc * (1 - cover(Math.hypot(x - h.cx, y - h.cy) - h.r)), 1);
      over(coral, cover(Math.abs(Math.hypot(x - g.ring.cx, y - g.ring.cy) - g.ring.r) - g.ring.w / 2) * open);
      over(teal, (1 - g.bars.reduce((acc, b) => acc * (1 - cover(barDistance(x, y, b))), 1)) * open);
      for (const n of g.nodes) over(teal, cover(Math.hypot(x - n.cx, y - n.cy) - n.r));
      px.set([out[0], out[1], out[2], out[3] * 255], (j * canvas + i) * 4);
    }
  }
  return { w: canvas, h: canvas, px };
}

// ---- assets ----

// The icon Gemini drew in the splash is replaced by the final one, slightly larger so none of it shows.
function buildSplash(splash) {
  const side = 2 * (SPLASH_ICON.a + SPLASH_ICON.cover), pad = 8;
  const x0 = Math.floor(SPLASH_ICON.cx - side / 2) - pad, y0 = Math.floor(SPLASH_ICON.cy - side / 2) - pad;
  const canvas = Math.ceil(side) + 2 * pad + 2;
  const patch = render(geometry(side, { thick: false, snap: false }), { canvas, ox: SPLASH_ICON.cx - side / 2 - x0, oy: SPLASH_ICON.cy - side / 2 - y0 });
  for (let y = 0; y < canvas; y++) {
    for (let x = 0; x < canvas; x++) {
      const p = (y * canvas + x) * 4, s = ((y0 + y) * splash.w + x0 + x) * 4, alpha = patch.px[p + 3] / 255;
      for (let c = 0; c < 3; c++) splash.px[s + c] = patch.px[p + c] * alpha + splash.px[s + c] * (1 - alpha);
    }
  }
  // Scale to the target height and centre-crop the sides (source aspect 1.608 vs target 1.6).
  const boxW = (SPLASH_SIZE.w * splash.h) / SPLASH_SIZE.h;
  return resize(splash, SPLASH_SIZE.w, SPLASH_SIZE.h, { x0: (splash.w - boxW) / 2, y0: 0, w: boxW, h: splash.h });
}

function main([splashFile]) {
  if (!splashFile) throw new Error('usage: node scripts/brand-assets.cjs <splash.jpeg>');
  const write = (dir, name, data) => {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, name), data);
    console.log(`${path.relative(ROOT, path.join(dir, name))}  ${data.length} bytes`);
  };
  const pngs = new Map();
  const pngOf = (size) => {
    if (!pngs.has(size)) pngs.set(size, encodePng(render(geometry(size))));
    return pngs.get(size);
  };

  write(BUILD, 'keepharness-mark.svg', svgFile(geometry(1024), 'rounded'));
  write(BUILD, 'keepharness-mark-only.svg', svgFile(geometry(1024, { crop: true }), 'none'));
  // The in-app mark: the `keepharness` symbol of the shared sprite.
  const sprite = path.join(WEB, 'icons.svg');
  const symbol = `<symbol id="keepharness" viewBox="0 0 40 40">${svgBody(geometry(40, { crop: true, thick: true, snap: false }), 'none', 'keepharness-gaps')}</symbol>`;
  write(WEB, 'icons.svg', fs.readFileSync(sprite, 'utf8').replace(/<symbol id="keepharness"[\s\S]*?<\/symbol>/, () => symbol));
  write(BUILD, 'icon.png', pngOf(1024));
  for (const size of LINUX_SIZES) write(path.join(BUILD, 'icons'), `${size}x${size}.png`, pngOf(size));
  write(BUILD, 'icon.ico', ico(ICO_SIZES.map((size) => ({ size, png: pngOf(size) }))));
  write(BUILD, 'icon.icns', icns(Object.entries(ICNS_TYPES).map(([size, type]) => ({ type, png: pngOf(Number(size)) }))));
  write(WEB, 'favicon.ico', ico(FAVICON_SIZES.map((size) => ({ size, png: pngOf(size) }))));
  // iOS rounds the corners itself and paints transparency black, so this one is opaque and full-bleed.
  write(WEB, 'apple-touch-icon.png', encodePng(render(geometry(APPLE_TOUCH_SIZE), { shape: 'square' }), { alpha: false }));
  write(BUILD, 'splash.jpg', encodeJpeg(buildSplash(decode(splashFile)), SPLASH_SIZE.maxBytes));
}

main(process.argv.slice(2));
