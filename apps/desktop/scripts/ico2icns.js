/**
 * Convert Windows .ico to macOS .icns
 *
 * Icons in this project were exported as PNG-embedded .ico on some systems
 * and as BMP/DIB .ico on others. This script detects the format of each
 * entry and converts raw DIB (BITMAPINFOHEADER) payloads to PNG so the
 * resulting .icns is valid.
 *
 * PNG encoding is done by hand — no external dependencies.
 */

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const icoPath = path.resolve(__dirname, '../src/renderer/assets/icon.ico');
const icnsPath = path.resolve(__dirname, '../src/renderer/assets/icon.icns');

const ico = fs.readFileSync(icoPath);
const buf = Buffer.from(ico);

// ── PNG chunk helpers ─────────────────────────────────────────────────────────

function pngChunk(type, data) {
  const t = Buffer.from(type, 'ascii');
  const crc = crc32(Buffer.concat([t, data]));
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const c = Buffer.alloc(4);
  c.writeUInt32BE(crc, 0);
  return Buffer.concat([len, t, data, c]);
}

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c = TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

// Pre-compute CRC32 table
const TABLE = new Int32Array(256);
for (let n = 0; n < 256; n++) {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  TABLE[n] = c;
}

// ── DIB (BITMAPINFOHEADER + pixel array) → PNG ────────────────────────────────

function dibToPng(dibData) {
  // Parse BITMAPINFOHEADER (40 bytes)
  const biWidth = dibData.readInt32LE(4);
  // ICO DIB biHeight is the combined XOR + AND mask height.
  // For 32-bit images AND mask is empty, but biHeight is still 2×.
  const rawHeight = dibData.readInt32LE(8);
  const bottomUp = rawHeight > 0;
  const imageHeight = Math.abs(rawHeight) / 2;
  const biBitCount = dibData.readUInt16LE(14);
  const biCompression = dibData.readUInt32LE(16);

  // We only handle uncompressed 32-bit BGRA (the only variant in this project)
  if (biCompression !== 0 || biBitCount !== 32) {
    throw new Error(`Unsupported DIB: bpp=${biBitCount} compression=${biCompression}`);
  }

  const headerSize = dibData.readUInt32LE(0);
  const pixelData = dibData.slice(headerSize);

  // BGRA → RGBA, one row at a time
  const rowLen = biWidth * 4;
  const rows = [];
  for (let y = 0; y < imageHeight; y++) {
    // Positive biHeight = bottom-up, so read from the bottom
    const srcY = bottomUp ? imageHeight - 1 - y : y;
    const rowOff = srcY * rowLen;
    const row = Buffer.alloc(rowLen);
    for (let x = 0; x < biWidth; x++) {
      const p = rowOff + x * 4;
      row[x * 4] = pixelData[p + 2]; // R ← B
      row[x * 4 + 1] = pixelData[p + 1]; // G ← G
      row[x * 4 + 2] = pixelData[p]; // B ← R
      row[x * 4 + 3] = pixelData[p + 3]; // A ← A
    }
    rows.push(row);
  }

  // Build raw (uncompressed) image data with filter byte per row
  const rawChunks = rows.map((row) => Buffer.concat([Buffer.from([0]), row]));
  const raw = Buffer.concat(rawChunks);

  // Deflate
  const compressed = zlib.deflateSync(raw);

  // Assemble PNG
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(biWidth, 0);
  ihdr.writeUInt32BE(imageHeight, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // color type: RGBA
  ihdr[10] = 0; // compression
  ihdr[11] = 0; // filter
  ihdr[12] = 0; // interlace

  const iend = Buffer.alloc(0);

  return Buffer.concat([
    signature,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', compressed),
    pngChunk('IEND', iend),
  ]);
}

// ── Main ──────────────────────────────────────────────────────────────────────

console.log(`ICO: type=${buf.readUInt16LE(2)}, count=${buf.readUInt16LE(4)}`);

const entries = [];
for (let i = 0; i < buf.readUInt16LE(4); i++) {
  const off = 6 + i * 16;
  const w = buf.readUInt8(off) || 256;
  const h = buf.readUInt8(off + 1) || 256;
  const size = buf.readUInt32LE(off + 8);
  const offset = buf.readUInt32LE(off + 12);
  entries.push({ w, h, size, offset });
  console.log(`  entry: ${w}x${h}, ${size} bytes at offset ${offset}`);
}

const icnsTypeMap = { 1024: 'ic10', 512: 'ic09', 256: 'ic08', 128: 'ic07' };

// Process all matching entries (largest first)
entries.sort((a, b) => b.w - a.w);

const icnsEntries = [];
for (const entry of entries) {
  const typeKey = icnsTypeMap[entry.w];
  if (!typeKey) {
    console.log(`  skipping ${entry.w}x${entry.h} — no ICNS type`);
    continue;
  }

  const rawData = buf.slice(entry.offset, entry.offset + entry.size);
  const isPng =
    rawData[0] === 0x89 && rawData[1] === 0x50 && rawData[2] === 0x4e && rawData[3] === 0x47;

  let pngData;
  if (isPng) {
    pngData = rawData;
    console.log(`  ${entry.w}x${entry.h} → ${typeKey} (already PNG, ${pngData.length} bytes)`);
  } else {
    // DIB/BMP → PNG
    pngData = dibToPng(rawData);
    console.log(
      `  ${entry.w}x${entry.h} → ${typeKey} (converted DIB → PNG, ${pngData.length} bytes)`
    );
  }

  const entryHeader = Buffer.alloc(8);
  const typeCode =
    (typeKey.charCodeAt(0) << 24) |
    (typeKey.charCodeAt(1) << 16) |
    (typeKey.charCodeAt(2) << 8) |
    typeKey.charCodeAt(3);
  entryHeader.writeUInt32BE(typeCode >>> 0, 0);
  entryHeader.writeUInt32BE(8 + pngData.length, 4);

  icnsEntries.push(Buffer.concat([entryHeader, pngData]));
}

const icnsHeader = Buffer.alloc(8);
icnsHeader.writeUInt32BE(
  ('i'.charCodeAt(0) << 24) |
    ('c'.charCodeAt(0) << 16) |
    ('n'.charCodeAt(0) << 8) |
    's'.charCodeAt(0),
  0
);
const totalLen = 8 + icnsEntries.reduce((s, e) => s + e.length, 0);
icnsHeader.writeUInt32BE(totalLen, 4);

const icns = Buffer.concat([icnsHeader, ...icnsEntries]);
fs.writeFileSync(icnsPath, icns);
console.log(`Written ${totalLen} bytes to ${icnsPath}`);
