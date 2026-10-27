import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync, rmSync, cpSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';
import { decodePngAlpha } from '../src/png.ts';
import { layerName, parseOrder } from '../src/character.ts';

const root = fileURLToPath(new URL('..', import.meta.url));
const bin = join(root, 'bin', 'spine-motion.js');
const run = (...args: string[]) => spawnSync(process.execPath, [bin, ...args], { encoding: 'utf8' });

// ---- a tiny PNG encoder, so the decoder is checked against every filter and colour type
function crc32(b: Uint8Array): number {
  let c = ~0;
  for (const x of b) {
    c ^= x;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}
function chunk(kind: string, body: Uint8Array): Buffer {
  const out = Buffer.alloc(12 + body.length);
  out.writeUInt32BE(body.length, 0);
  out.write(kind, 4, 'latin1');
  out.set(body, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + body.length)), 8 + body.length);
  return out;
}
/** rows: raw samples per row (already packed); every row gets the next filter type 0..4 in turn. */
function png(w: number, h: number, type: number, depth: number, bpp: number, rows: Uint8Array[], extra: Buffer[] = []): Uint8Array {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = depth;
  ihdr[9] = type;
  const parts: number[] = [];
  let prev: Uint8Array = new Uint8Array(rows[0].length);
  rows.forEach((row, y) => {
    const f = y % 5;
    parts.push(f);
    for (let i = 0; i < row.length; i++) {
      const a = i >= bpp ? row[i - bpp] : 0, b = prev[i], c = i >= bpp ? prev[i - bpp] : 0;
      const pa = Math.abs(b - c), pb = Math.abs(a - c), pc = Math.abs(a + b - 2 * c);
      const pred = [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? b : c][f];
      parts.push((row[i] - pred) & 255);
    }
    prev = row;
  });
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), ...extra, chunk('IDAT', deflateSync(Buffer.from(parts))), chunk('IEND', new Uint8Array())]);
}

test('PNG alpha decoding: RGBA, gray+alpha, 16-bit, palette with tRNS, RGB without alpha', () => {
  const W = 7, H = 6;
  const alpha = (x: number, y: number) => (x * 37 + y * 91) % 256;
  const rgba = Array.from({ length: H }, (_, y) => Uint8Array.from({ length: W * 4 }, (_, i) => (i % 4 === 3 ? alpha(i >> 2, y) : (i * 13 + y) % 256)));
  assert.deepEqual([...decodePngAlpha(png(W, H, 6, 8, 4, rgba)).data], Array.from({ length: W * H }, (_, i) => alpha(i % W, Math.floor(i / W))));
  const ga = Array.from({ length: H }, (_, y) => Uint8Array.from({ length: W * 2 }, (_, i) => (i % 2 ? alpha(i >> 1, y) : 50)));
  assert.deepEqual([...decodePngAlpha(png(W, H, 4, 8, 2, ga)).data], Array.from({ length: W * H }, (_, i) => alpha(i % W, Math.floor(i / W))));
  const rgba16 = Array.from({ length: H }, (_, y) => Uint8Array.from({ length: W * 8 }, (_, i) => (i % 8 === 6 ? alpha(i >> 3, y) : i % 8 === 7 ? 0 : 9)));
  assert.deepEqual([...decodePngAlpha(png(W, H, 6, 16, 8, rgba16)).data], Array.from({ length: W * H }, (_, i) => Math.round((alpha(i % W, Math.floor(i / W)) * 256) / 65535 * 255)));
  const pal = Array.from({ length: H }, (_, y) => Uint8Array.from({ length: W }, (_, x) => (x + y) % 3));
  const plte = chunk('PLTE', new Uint8Array(9));
  const trns = chunk('tRNS', Uint8Array.from([0, 128]));
  assert.deepEqual([...decodePngAlpha(png(W, H, 3, 8, 1, pal, [plte, trns])).data], Array.from({ length: W * H }, (_, i) => [0, 128, 255][(i % W + Math.floor(i / W)) % 3]));
  const rgb = Array.from({ length: H }, () => new Uint8Array(W * 3).fill(200));
  assert.ok(decodePngAlpha(png(W, H, 2, 8, 3, rgb)).data.every((a) => a === 255));
});

test('the layer names and layers.json read the way the studio reads them', () => {
  assert.equal(layerName('Arm-Front Upper.PNG'), 'arm_front_upper');
  assert.deepEqual(parseOrder({ order: ['torso.png', 'Head.png'] }), ['torso', 'head']);
  assert.deepEqual(parseOrder(['neck.png']), ['neck']);
  assert.throws(() => parseOrder({ layers: [] }), /order/);
});

test('character: one command from a layers folder to a Spine json, report and preview', () => {
  const dir = mkdtempSync(join(tmpdir(), 'smk-char-'));
  try {
    const layers = join(dir, 'monkey', 'layers');
    cpSync(join(root, 'examples', 'characters', 'bandit-monkey', 'layers'), layers, { recursive: true });
    cpSync(join(root, 'examples', 'characters', 'bandit-monkey', 'joints_manual.json'), join(dir, 'monkey', 'joints_manual.json'));
    const r = run('character', layers, '--no-gif');
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stdout, /layers\.json/); // picked up the delivery's draw order
    assert.match(r.stdout, /joints_manual\.json/); // and the hand-set joints next to the folder
    const out = join(dir, 'monkey', 'monkey_spine'); // named after the folder above "layers"
    for (const f of ['monkey.json', '報告.md', '預覽.html', 'joints.json']) assert.ok(existsSync(join(out, f)), f);
    assert.equal(readdirSync(join(out, 'images')).length, 14);
    const json = JSON.parse(readFileSync(join(out, 'monkey.json'), 'utf8'));
    assert.deepEqual(Object.keys(json.animations), ['idle', 'idle_weave', 'punch', 'hit', 'jump', 'celebrate']);
    assert.equal(json.skeleton.images, './images/');
    // the rear glove is drawn in front of the torso, as layers.json says
    const slots = json.slots.map((s: { name: string }) => s.name);
    assert.ok(slots.indexOf('fist_back') > slots.indexOf('torso'));
    const report = readFileSync(join(out, '報告.md'), 'utf8');
    assert.match(report, /\| punch \| [\d.]+ s \| 100 \| ✓ \|/);
    assert.ok(!existsSync(join(out, 'frames.json')));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('character: a delivery missing a required layer is refused with the file name', () => {
  const dir = mkdtempSync(join(tmpdir(), 'smk-char-'));
  try {
    cpSync(join(root, 'examples', 'characters', 'boxer', 'layers'), dir, { recursive: true });
    rmSync(join(dir, 'torso.png'));
    const r = run('character', dir, '--no-gif', '-o', join(dir, 'out'));
    assert.equal(r.status, 1);
    assert.match(r.stderr, /torso\.png/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('bandit monkey is the reference character: the delivery folder rebuilds monkey.json exactly (Stone, 2026-10-27)', () => {
  // 「說到大盜猴就是要達到這一模一樣的效果」: layers + layers.json + joints_manual.json + celebrate.params.json +
  // motion.params.json must give back the approved skeleton and all six animations to the last digit. If a kit change
  // breaks this, the change moved the monkey: ask Stone before touching monkey.json or these params.
  const dir = mkdtempSync(join(tmpdir(), 'smk-golden-'));
  try {
    const src = join(root, 'examples', 'characters', 'bandit-monkey');
    const monkey = join(dir, 'monkey');
    cpSync(join(src, 'layers'), join(monkey, 'layers'), { recursive: true });
    for (const f of ['joints_manual.json', 'celebrate.params.json', 'motion.params.json']) cpSync(join(src, f), join(monkey, f));
    const r = run('character', join(monkey, 'layers'), '--no-gif');
    assert.equal(r.status, 0, r.stderr);
    const got = JSON.parse(readFileSync(join(monkey, 'monkey_spine', 'monkey.json'), 'utf8'));
    const want = JSON.parse(readFileSync(join(src, 'monkey.json'), 'utf8'));
    for (const k of ['bones', 'slots', 'constraints', 'skins']) assert.deepEqual(got[k], want[k], k);
    assert.deepEqual(Object.keys(got.animations), Object.keys(want.animations));
    for (const a of Object.keys(want.animations)) assert.deepEqual(got.animations[a], want.animations[a], `animation ${a} moved`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
