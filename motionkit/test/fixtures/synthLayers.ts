/** Procedural stand-in for an image agent's layered output, drawn on the template canvas at the template joints. */
import { CANVAS, defaultJoints, type Joints } from '../../src/rigTemplate.ts';
import type { AlphaMap } from '../../src/autorig.ts';

type Pt = { x: number; y: number };

function blank(): AlphaMap {
  return { width: CANVAS.width, height: CANVAS.height, data: new Uint8Array(CANVAS.width * CANVAS.height) };
}
function capsule(a: AlphaMap, p: Pt, q: Pt, r: number, value = 255) {
  const x0 = Math.max(0, Math.floor(Math.min(p.x, q.x) - r)), x1 = Math.min(a.width - 1, Math.ceil(Math.max(p.x, q.x) + r));
  const y0 = Math.max(0, Math.floor(Math.min(p.y, q.y) - r)), y1 = Math.min(a.height - 1, Math.ceil(Math.max(p.y, q.y) + r));
  const dx = q.x - p.x, dy = q.y - p.y, L2 = dx * dx + dy * dy || 1;
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const t = Math.max(0, Math.min(1, ((x - p.x) * dx + (y - p.y) * dy) / L2));
      const ex = p.x + t * dx - x, ey = p.y + t * dy - y;
      if (ex * ex + ey * ey <= r * r) a.data[y * a.width + x] = value;
    }
  }
}
const disc = (a: AlphaMap, c: Pt, r: number, value = 255) => capsule(a, c, c, r, value);
const along = (p: Pt, q: Pt, t: number): Pt => ({ x: p.x + (q.x - p.x) * t, y: p.y + (q.y - p.y) * t });

/**
 * 'good': every part has rounded ends that overlap its neighbours, hidden areas painted.
 * 'bad': the torso is not painted under the front shoulder, and the head is cut flat at the chin with no neck layer.
 */
export function synthLayers(kind: 'good' | 'bad', j: Joints = defaultJoints()): Map<string, AlphaMap> {
  const L = new Map<string, AlphaMap>();
  const add = (name: string, draw: (a: AlphaMap) => void) => {
    const a = blank();
    draw(a);
    L.set(name, a);
  };
  add('torso', (a) => {
    capsule(a, j.waist, j.neckBase, 82);
    // like real art: the torso stops at the shoulder line; the neck is its own part (or the head paints down into it)
    for (let y = 0; y < j.neckBase.y - 12; y++) a.data.fill(0, y * a.width, (y + 1) * a.width);
    if (kind === 'bad') disc(a, j.shoulderF, 34, 0); // forgot to paint behind the front arm
  });
  add('pelvis', (a) => {
    capsule(a, j.hipB, j.hipF, 62);
    capsule(a, j.hip, j.waist, 60);
  });
  if (kind === 'good') add('neck', (a) => capsule(a, along(j.neckBase, j.headBase, -0.6), along(j.neckBase, j.headBase, 1.6), 30));
  add('head', (a) => {
    const c = along(j.headBase, j.headTop, 0.55);
    disc(a, c, 0.55 * Math.hypot(j.headTop.x - j.headBase.x, j.headTop.y - j.headBase.y));
    if (kind === 'good') capsule(a, c, along(j.headTop, j.headBase, 1.35), 34); // painted down into the neck
    else {
      // flat cut at the chin: clear everything below the head-base line
      const dx = j.headTop.x - j.headBase.x, dy = j.headTop.y - j.headBase.y;
      for (let y = 0; y < a.height; y++) for (let x = 0; x < a.width; x++) {
        if ((x - j.headBase.x) * dx + (y - j.headBase.y) * dy < 0) a.data[y * a.width + x] = 0;
      }
    }
  });
  add('hair_front', (a) => capsule(a, j.hairRoot, j.hairTip, 18));
  for (const [s, k] of [['F', 'front'], ['B', 'back']] as const) {
    add(`arm_${k}_upper`, (a) => capsule(a, j[`shoulder${s}`], j[`elbow${s}`], 30));
    add(`arm_${k}_lower`, (a) => capsule(a, j[`elbow${s}`], j[`wrist${s}`], 25));
    add(`fist_${k}`, (a) => disc(a, along(j[`elbow${s}`], j[`wrist${s}`], 1.18), 36));
    add(`leg_${k}_upper`, (a) => capsule(a, j[`hip${s}`], j[`knee${s}`], 42));
    add(`leg_${k}_lower`, (a) => {
      capsule(a, j[`knee${s}`], j[`ankle${s}`], 33);
      capsule(a, j[`ankle${s}`], { x: j[`ankle${s}`].x + 60, y: j[`ankle${s}`].y + 20 }, 22);
    });
  }
  return L;
}
