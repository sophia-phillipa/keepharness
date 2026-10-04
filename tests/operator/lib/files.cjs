// Attachment samples written to a temporary folder: image, PDF, text, CSV, a Unicode
// name, an empty file and a sparse file just over the 100 MiB per-file limit.
"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");

function crc32(buffer) {
  let crc = ~0;
  for (const byte of buffer) {
    crc ^= byte;
    for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return ~crc >>> 0;
}

function png(width, height, rgb) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const out = Buffer.alloc(12 + data.length);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc32(body), 8 + data.length);
    return out;
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // truecolor
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(width * 3).map((_, i) => rgb[i % 3])]);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

function pdf(text) {
  const stream = `BT /F1 18 Tf 40 760 Td (${text}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let body = "%PDF-1.4\n";
  const offsets = [];
  objects.forEach((object, i) => {
    offsets.push(body.length);
    body += `${i + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = body.length;
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` + offsets.map((o) => String(o).padStart(10, "0") + " 00000 n \n").join("");
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, "latin1");
}

function writeSamples() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "keepharness-operator-files-"));
  const files = {
    image: ["operator-photo.png", png(96, 64, [47, 111, 222])],
    pdf: ["operator-brief.pdf", pdf("Operator fixture PDF")],
    text: ["operator-notes.txt", "Operator notes\nline two\n"],
    csv: ["operator-data.csv", "name,value\nalpha,1\nbeta,2\n"],
    unicode: ["relatório-ñandú-日本.txt", "Unicode file name\n"],
    empty: ["operator-empty.txt", ""],
  };
  const paths = { dir };
  for (const [key, [name, content]] of Object.entries(files)) {
    paths[key] = path.join(dir, name);
    fs.writeFileSync(paths[key], content);
  }
  // Sparse: 100 MiB + 1 byte on disk without writing the bytes.
  paths.oversize = path.join(dir, "operator-oversize.bin");
  fs.closeSync(fs.openSync(paths.oversize, "w"));
  fs.truncateSync(paths.oversize, 100 * 1024 * 1024 + 1);
  paths.cleanup = () => fs.rmSync(dir, { recursive: true, force: true });
  return paths;
}

module.exports = { writeSamples };
