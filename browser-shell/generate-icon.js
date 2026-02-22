/**
 * Generates a minimal valid 16x16 ICO file for electron-builder.
 * Run once: node generate-icon.js
 * Replace assets/icon.ico with your real branded icon before shipping.
 */
'use strict';

const fs   = require('fs');
const path = require('path');

const W = 16, H = 16;
const XOR_SIZE = W * H * 4;               // BGRA pixels
const AND_STRIDE = Math.ceil(W / 32) * 4; // DWORD-aligned row bytes for AND mask
const AND_SIZE  = H * AND_STRIDE;
const DIB_SIZE  = 40 + XOR_SIZE + AND_SIZE;
const FILE_SIZE = 6 + 16 + DIB_SIZE;

const buf = Buffer.alloc(FILE_SIZE, 0);
let o = 0;

/* ICONDIR */
buf.writeUInt16LE(0, o); o += 2;          // reserved
buf.writeUInt16LE(1, o); o += 2;          // type: ICO
buf.writeUInt16LE(1, o); o += 2;          // image count

/* ICONDIRENTRY */
buf.writeUInt8(W,  o++);                  // width
buf.writeUInt8(H,  o++);                  // height
buf.writeUInt8(0,  o++);                  // colour count (0 = no palette)
buf.writeUInt8(0,  o++);                  // reserved
buf.writeUInt16LE(1,  o); o += 2;         // colour planes
buf.writeUInt16LE(32, o); o += 2;         // bits per pixel
buf.writeUInt32LE(DIB_SIZE, o); o += 4;   // size of image data
buf.writeUInt32LE(22,       o); o += 4;   // offset to image data (6+16=22)

/* BITMAPINFOHEADER */
buf.writeUInt32LE(40,      o); o += 4;    // biSize
buf.writeInt32LE (W,       o); o += 4;    // biWidth
buf.writeInt32LE (H * 2,   o); o += 4;   // biHeight (×2 for XOR+AND in ICO)
buf.writeUInt16LE(1,       o); o += 2;    // biPlanes
buf.writeUInt16LE(32,      o); o += 2;    // biBitCount
buf.writeUInt32LE(0,       o); o += 4;    // biCompression (BI_RGB)
buf.writeUInt32LE(XOR_SIZE,o); o += 4;   // biSizeImage
buf.writeInt32LE (0, o); o += 4;
buf.writeInt32LE (0, o); o += 4;
buf.writeUInt32LE(0, o); o += 4;
buf.writeUInt32LE(0, o); o += 4;

/* XOR data — bottom-up, #6c63ff (Intelli accent), fully opaque */
for (let y = H - 1; y >= 0; y--) {
  for (let x = 0; x < W; x++) {
    buf[o++] = 0xff; // B
    buf[o++] = 0x63; // G
    buf[o++] = 0x6c; // R
    buf[o++] = 0xff; // A
  }
}

/* AND mask — all zeros = opaque (buffer already zero-initialised) */

const outPath = path.join(__dirname, 'assets', 'icon.ico');
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, buf);
console.log('Written:', outPath, `(${buf.length} bytes)`);
