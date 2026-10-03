#!/usr/bin/env node
'use strict';
// Rebuilds the KeepHarness brand assets from the two approved Gemini images:
//   node scripts/brand-assets.cjs <logo.jpeg> <splash.jpeg>
// Needs Node 22.2+ and ffmpeg/ffprobe on PATH, nothing else. See desktop/build/README.md.
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const ROOT = path.resolve(__dirname, '..');
const BUILD = path.join(ROOT, 'desktop', 'build');
const WEB = path.join(ROOT, 'harness_ui', 'assets');

// The squircle of the 2048 px logo (centre, half-size, corner radius in source px), fitted to its
// sub-pixel edge. The checkerboard and drop shadow around it are painted into the JPEG.
const LOGO_SHAPE = { cx: 1024.1, cy: 1024.3, a: 819.4, r: 352 };
const INSET = 4; // source px next to the edge that are re-sampled from further inside
// The icon drawn in the 2624x1632 splash (centre, half-size) and how far the new one overlaps it.
const SPLASH_ICON = { cx: 1312, cy: 694.8, a: 284.2, cover: 2.5 };
const SPLASH_SIZE = { w: 1600, h: 1000, maxBytes: 400 * 1000 };
const LINUX_SIZES = [16, 24, 32, 48, 64, 128, 256, 512];
const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256];
const FAVICON_SIZES = [16, 32, 48];
const APPLE_TOUCH_SIZE = 180;
const ICNS_TYPES = { 128: 'ic07', 256: 'ic08', 512: 'ic09', 1024: 'ic10' };
const SMALL_MAX = 32; // up to this size the symbol is enlarged and sharpened to stay legible
const SMALL_ZOOM = 1.2;
const SMALL_SHARPEN = 0.7;

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

// ---- the squircle ----

// Signed distance (positive outside) to a rounded rectangle, plus the outward normal.
function roundRect(x, y, cx, cy, a, r) {
  const dx = x - cx, dy = y - cy;
  const qx = Math.abs(dx) - (a - r), qy = Math.abs(dy) - (a - r);
  const sx = dx < 0 ? -1 : 1, sy = dy < 0 ? -1 : 1;
  if (qx > 0 && qy > 0) {
    const len = Math.hypot(qx, qy);
    return { sd: len - r, nx: (sx * qx) / len, ny: (sy * qy) / len };
  }
  return qx > qy ? { sd: qx - r, nx: sx, ny: 0 } : { sd: qy - r, nx: 0, ny: sy };
}

function bilinear(img, x, y) {
  const fx = x - 0.5, fy = y - 0.5, x0 = Math.floor(fx), y0 = Math.floor(fy);
  const tx = fx - x0, ty = fy - y0, out = [0, 0, 0, 255];
  for (let c = 0; c < 3; c++) {
    const at = (xx, yy) => img.px[(Math.min(Math.max(yy, 0), img.h - 1) * img.w + Math.min(Math.max(xx, 0), img.w - 1)) * 4 + c];
    out[c] = (1 - ty) * ((1 - tx) * at(x0, y0) + tx * at(x0 + 1, y0)) + ty * ((1 - tx) * at(x0, y0 + 1) + tx * at(x0 + 1, y0 + 1));
  }
  return out;
}

// Everything outside the squircle (checkerboard, shadow) and the band next to its edge is replaced
// with the colour found INSET px inside, so no resampling kernel can pull in background.
function cleanSource(img) {
  const { cx, cy, a, r } = LOGO_SHAPE;
  const px = Float32Array.from(img.px);
  for (let y = 0; y < img.h; y++) {
    for (let x = 0; x < img.w; x++) {
      const { sd, nx, ny } = roundRect(x + 0.5, y + 0.5, cx, cy, a, r);
      if (sd <= -INSET) continue;
      const from = bilinear(img, x + 0.5 - nx * (sd + INSET), y + 0.5 - ny * (sd + INSET));
      px.set(from, (y * img.w + x) * 4);
    }
  }
  return { w: img.w, h: img.h, px };
}

// Renders the cleaned logo as a `size` px squircle at (ox, oy) of a square `canvas`, with an
// analytic one-pixel anti-aliased edge. `zoom` enlarges the symbol inside the same outline.
function renderIcon(clean, { size, canvas = size, ox = 0, oy = 0, zoom = 1 }) {
  const k = size / (2 * LOGO_SHAPE.a), kz = k * zoom;
  const dcx = ox + size / 2, dcy = oy + size / 2;
  const out = resize(clean, canvas, canvas, {
    x0: LOGO_SHAPE.cx - dcx / kz, y0: LOGO_SHAPE.cy - dcy / kz, w: canvas / kz, h: canvas / kz,
  });
  for (let y = 0; y < canvas; y++) {
    for (let x = 0; x < canvas; x++) {
      const { sd } = roundRect(x + 0.5, y + 0.5, dcx, dcy, size / 2, LOGO_SHAPE.r * k);
      out.px[(y * canvas + x) * 4 + 3] = 255 * Math.min(1, Math.max(0, 0.5 - sd));
    }
  }
  return out;
}

// Unsharp mask on the colour channels (3x3 binomial blur); alpha is left alone.
function sharpen(img, amount) {
  const { w, h, px } = img, blur = new Float32Array(px.length);
  const k = [1, 2, 1];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      for (let c = 0; c < 3; c++) {
        let acc = 0;
        for (let j = 0; j < 3; j++) {
          for (let i = 0; i < 3; i++) {
            const xx = Math.min(Math.max(x + i - 1, 0), w - 1), yy = Math.min(Math.max(y + j - 1, 0), h - 1);
            acc += k[i] * k[j] * px[(yy * w + xx) * 4 + c];
          }
        }
        blur[(y * w + x) * 4 + c] = acc / 16;
      }
    }
  }
  for (let i = 0; i < px.length; i += 4) {
    for (let c = 0; c < 3; c++) px[i + c] += amount * (px[i + c] - blur[i + c]);
  }
  return img;
}

// ---- assets ----

function iconAt(clean, size) {
  const small = size <= SMALL_MAX;
  const img = renderIcon(clean, { size, zoom: small ? SMALL_ZOOM : 1 });
  return small ? sharpen(img, SMALL_SHARPEN) : img;
}

// The icon drawn in the splash is replaced by the final one, slightly larger so none of it shows.
function buildSplash(splash, clean) {
  const side = 2 * (SPLASH_ICON.a + SPLASH_ICON.cover), pad = 8;
  const x0 = Math.floor(SPLASH_ICON.cx - side / 2) - pad, y0 = Math.floor(SPLASH_ICON.cy - side / 2) - pad;
  const canvas = Math.ceil(side) + 2 * pad + 2;
  const patch = renderIcon(clean, { size: side, canvas, ox: SPLASH_ICON.cx - side / 2 - x0, oy: SPLASH_ICON.cy - side / 2 - y0 });
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

function main([logoFile, splashFile]) {
  if (!logoFile || !splashFile) throw new Error('usage: node scripts/brand-assets.cjs <logo.jpeg> <splash.jpeg>');
  const write = (dir, name, data) => {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, name), data);
    console.log(`${path.relative(ROOT, path.join(dir, name))}  ${data.length} bytes`);
  };
  const clean = cleanSource(decode(logoFile));
  const pngs = new Map();
  const pngOf = (size) => {
    if (!pngs.has(size)) pngs.set(size, encodePng(iconAt(clean, size)));
    return pngs.get(size);
  };

  write(BUILD, 'icon.png', pngOf(1024));
  for (const size of LINUX_SIZES) write(path.join(BUILD, 'icons'), `${size}x${size}.png`, pngOf(size));
  write(BUILD, 'icon.ico', ico(ICO_SIZES.map((size) => ({ size, png: pngOf(size) }))));
  write(BUILD, 'icon.icns', icns(Object.entries(ICNS_TYPES).map(([size, type]) => ({ type, png: pngOf(Number(size)) }))));
  write(WEB, 'favicon.ico', ico(FAVICON_SIZES.map((size) => ({ size, png: pngOf(size) }))));
  // iOS rounds the corners itself and paints transparency black, so this one is opaque and full-bleed.
  write(WEB, 'apple-touch-icon.png', encodePng(renderIcon(clean, { size: APPLE_TOUCH_SIZE }), { alpha: false }));
  write(BUILD, 'splash.jpg', encodeJpeg(buildSplash(decode(splashFile), clean), SPLASH_SIZE.maxBytes));
}

main(process.argv.slice(2));
