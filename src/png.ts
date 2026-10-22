/**
 * Minimal PNG reader for the command line: just the alpha channel, which is all the rig builder and the seam check
 * need. Handles what image tools export (8/16-bit gray, RGB, palette, gray+alpha, RGBA, tRNS); not interlaced files.
 */
import { inflateSync } from 'node:zlib';
import type { AlphaMap } from './autorig.ts';

const SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];

export function pngSize(buf: Uint8Array): { width: number; height: number } {
  const v = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  if (buf.length < 24 || SIGNATURE.some((b, i) => buf[i] !== b)) throw new Error('不是 PNG 檔');
  return { width: v.getUint32(16), height: v.getUint32(20) };
}

export function decodePngAlpha(buf: Uint8Array): AlphaMap {
  const v = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const { width, height } = pngSize(buf);
  let depth = 8, type = 6, interlace = 0;
  let palAlpha: Uint8Array | undefined;
  let key: number[] | undefined; // tRNS colour key for gray / RGB (sample values)
  const idat: Uint8Array[] = [];
  for (let p = 8; p + 8 <= buf.length;) {
    const len = v.getUint32(p);
    const kind = String.fromCharCode(buf[p + 4], buf[p + 5], buf[p + 6], buf[p + 7]);
    const body = buf.subarray(p + 8, p + 8 + len);
    if (kind === 'IHDR') {
      depth = body[8];
      type = body[9];
      interlace = body[12];
    } else if (kind === 'tRNS') {
      if (type === 3) palAlpha = body.slice();
      else {
        const k = new DataView(body.buffer, body.byteOffset, body.byteLength);
        key = Array.from({ length: body.length / 2 }, (_, i) => k.getUint16(i * 2));
      }
    } else if (kind === 'IDAT') idat.push(body);
    else if (kind === 'IEND') break;
    p += 12 + len;
  }
  if (interlace) throw new Error('是交錯（interlaced）PNG，請另存成一般 PNG');
  const channels = ({ 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 } as Record<number, number>)[type];
  if (!channels) throw new Error(`不支援的 PNG 色彩格式（${type}）`);
  const bpp = Math.max(1, (channels * depth) / 8); // bytes per pixel for filtering
  const stride = Math.ceil((width * channels * depth) / 8);
  const raw = inflateSync(Buffer.concat(idat));
  const rows = new Uint8Array(stride * height);
  let prev = new Uint8Array(stride);
  for (let y = 0; y < height; y++) {
    const f = raw[y * (stride + 1)];
    const src = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const row = rows.subarray(y * stride, (y + 1) * stride);
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? row[i - bpp] : 0, b = prev[i], c = i >= bpp ? prev[i - bpp] : 0;
      let x = src[i];
      if (f === 1) x += a;
      else if (f === 2) x += b;
      else if (f === 3) x += (a + b) >> 1;
      else if (f === 4) {
        const pa = Math.abs(b - c), pb = Math.abs(a - c), pc = Math.abs(a + b - 2 * c);
        x += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      row[i] = x;
    }
    prev = row;
  }
  const data = new Uint8Array(width * height);
  const sample = (row: number, i: number) => {
    // i-th sample of a row, at its own bit depth
    const o = y0(row);
    if (depth === 16) return (rows[o + i * 2] << 8) | rows[o + i * 2 + 1];
    if (depth === 8) return rows[o + i];
    const bit = i * depth;
    return (rows[o + (bit >> 3)] >> (8 - depth - (bit & 7))) & ((1 << depth) - 1);
  };
  const y0 = (row: number) => row * stride;
  const max = (1 << depth) - 1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let a = 255;
      if (type === 6 || type === 4) a = Math.round((sample(y, x * channels + channels - 1) / max) * 255);
      else if (type === 3) a = palAlpha?.[sample(y, x)] ?? 255;
      else if (key) {
        const same = key.every((k, c) => sample(y, x * channels + c) === k);
        a = same ? 0 : 255;
      }
      data[y * width + x] = a;
    }
  }
  return { width, height, data };
}
