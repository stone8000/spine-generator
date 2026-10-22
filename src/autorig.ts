/**
 * Layers + joints → Spine 4.3 skeleton that the motion presets drive directly.
 * Pure computation (no DOM, no Node APIs): the studio and the tests share it.
 */
import { Skeleton, Physics } from '@esotericsoftware/spine-core';
import { CANVAS, PARTS, JOINTS, TEMPLATE_ROLES, defaultJoints, toSpine, type Joints, type JointId, type CanvasSize } from './rigTemplate.ts';
import { parseSkeleton, type SpineJson } from './spineJson.ts';

/** One layer's alpha channel, row-major, y down (as decoded from a PNG). */
export interface AlphaMap {
  width: number;
  height: number;
  data: Uint8Array;
}

const DEG = 180 / Math.PI;
const rad = (d: number) => d / DEG;

// ---------------------------------------------------------------- joint guessing
interface Axis {
  centroid: { x: number; y: number };
  ends: [{ x: number; y: number }, { x: number; y: number }];
  bbox: { x0: number; y0: number; x1: number; y1: number };
}

/** Principal axis of a layer's opaque pixels; ends at the 2nd / 98th percentile along the axis. */
export function layerAxis(a: AlphaMap, stride = 3): Axis | null {
  const xs: number[] = [];
  const ys: number[] = [];
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let y = 0; y < a.height; y += stride) {
    for (let x = 0; x < a.width; x += stride) {
      if (a.data[y * a.width + x] > 128) {
        xs.push(x); ys.push(y);
        if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y;
      }
    }
  }
  if (xs.length < 4) return null;
  const n = xs.length;
  const cx = xs.reduce((s, v) => s + v, 0) / n;
  const cy = ys.reduce((s, v) => s + v, 0) / n;
  let sxx = 0, syy = 0, sxy = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - cx, dy = ys[i] - cy;
    sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
  }
  const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const ux = Math.cos(theta), uy = Math.sin(theta);
  const proj = xs.map((x, i) => (x - cx) * ux + (ys[i] - cy) * uy).sort((p, q) => p - q);
  const lo = proj[Math.floor(n * 0.02)];
  const hi = proj[Math.floor(n * 0.98)];
  return {
    centroid: { x: cx, y: cy },
    ends: [{ x: cx + ux * lo, y: cy + uy * lo }, { x: cx + ux * hi, y: cy + uy * hi }],
    bbox: { x0, y0, x1, y1 },
  };
}

const dist = (p: { x: number; y: number }, q: { x: number; y: number }) => Math.hypot(p.x - q.x, p.y - q.y);
const mid = (p: { x: number; y: number }, q: { x: number; y: number }) => ({ x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 });
const lerp = (p: { x: number; y: number }, q: { x: number; y: number }, t: number) => ({ x: p.x + (q.x - p.x) * t, y: p.y + (q.y - p.y) * t });

/** [end nearer `ref`, end further from `ref`] */
function orient(ax: Axis, ref: { x: number; y: number }): [{ x: number; y: number }, { x: number; y: number }] {
  const [a, b] = ax.ends;
  return dist(a, ref) <= dist(b, ref) ? [a, b] : [b, a];
}

/** Centroid of the pixels opaque in both layers (where two parts overlap at their shared joint). */
function overlapCentroid(a: AlphaMap, b: AlphaMap, stride = 2): { x: number; y: number } | null {
  let sx = 0, sy = 0, n = 0;
  for (let y = 0; y < a.height; y += stride) {
    for (let x = 0; x < a.width; x += stride) {
      const i = y * a.width + x;
      if (a.data[i] > 128 && b.data[i] > 128) {
        sx += x; sy += y; n++;
      }
    }
  }
  return n >= 6 ? { x: sx / n, y: sy / n } : null;
}

/**
 * Where a limb's root joint (shoulder, hip) sits, walking in from the layer's end: past the rounded cap (half the
 * limb's width there) and past the part painted into the body. The spec asks for ~40 px of that; image agents paint
 * somewhat more or less, so assume 30 and let the user drag.
 */
function rootJoint(a: AlphaMap, end: { x: number; y: number }, other: { x: number; y: number }, canvas: CanvasSize) {
  if (dist(end, other) < 8) return end;
  // the axis end is a percentile, a little inside the layer: find the real tip first
  const from = lerp(end, other, -0.2);
  const bins = 60;
  const w = profile(a, from, other, bins);
  const first = Math.max(0, w.findIndex((v) => v > 0));
  const tip = lerp(from, other, first / bins);
  const r = Math.max(...w.slice(first, first + 18)) / 2; // full width is reached one radius in from a rounded end
  const tucked = 30 * (canvas.height / CANVAS.height);
  return lerp(tip, other, Math.min(0.45, (r + tucked) / dist(tip, other)));
}

/** Rough joint positions from the layers. Missing layers keep the template defaults. */
export function guessJoints(layers: Map<string, AlphaMap>): Joints {
  const first = layers.values().next().value;
  const canvas: CanvasSize = first ? { width: first.width, height: first.height } : CANVAS;
  const j = defaultJoints(canvas);
  const round = (p: { x: number; y: number }) => ({ x: Math.round(Math.min(canvas.width, Math.max(0, p.x))), y: Math.round(Math.min(canvas.height, Math.max(0, p.y))) });
  const axes = new Map<string, Axis>();
  for (const [name, a] of layers) {
    const ax = layerAxis(a);
    if (ax) axes.set(name, ax);
  }
  const torso = axes.get('torso');
  const pelvis = axes.get('pelvis');
  const head = axes.get('head');
  const body = torso?.centroid ?? j.neckBase;
  if (torso) {
    // the torso overlaps into the pelvis: the waist is the middle of that overlap band
    const bottom = torso.bbox.y1 - 0.06 * (torso.bbox.y1 - torso.bbox.y0);
    j.waist = round({ x: torso.centroid.x, y: pelvis ? (torso.bbox.y1 + pelvis.bbox.y0) / 2 : bottom });
    j.neckBase = round({ x: torso.centroid.x, y: torso.bbox.y0 + 0.06 * (torso.bbox.y1 - torso.bbox.y0) });
  }
  const headMap = layers.get('head');
  const neckMap = layers.get('neck');
  if (head && headMap) {
    const [base, top] = orient(head, body);
    // with a neck layer, the head turns where the neck goes into it; without one, artists paint the head down into
    // the neck and the head base is where the outline widens into the jaw (a tilted face can pull that to the chin)
    const tuck = neckMap ? overlapCentroid(neckMap, headMap) : null;
    j.headBase = round(tuck ?? widening(headMap, base, top, 0.55) ?? lerp(base, top, 0.12));
    j.headTop = round(top);
  }
  // two neighbouring parts both rounded at their shared joint overlap around it: that overlap's centre is the joint,
  // however much extra the artist painted. The ends of the principal axes are the fallback when parts don't touch.
  const overlap = (p: string, q: string) => {
    const a = layers.get(p), b = layers.get(q);
    return a && b ? overlapCentroid(a, b) : null;
  };
  for (const side of ['F', 'B'] as const) {
    const key = side === 'F' ? 'front' : 'back';
    const up = axes.get(`arm_${key}_upper`);
    const low = axes.get(`arm_${key}_lower`);
    const fist = axes.get(`fist_${key}`);
    let elbowOut: { x: number; y: number } | null = null;
    if (up) {
      // orient by the neck base, not the torso centre: in a guard pose the elbow hangs beside the torso's middle
      const [s, e] = orient(up, j.neckBase);
      j[`shoulder${side}`] = round(rootJoint(layers.get(`arm_${key}_upper`)!, s, e, canvas));
      elbowOut = e;
    }
    if (low) {
      const [s, e] = orient(low, elbowOut ?? body);
      j[`elbow${side}`] = round(overlap(`arm_${key}_upper`, `arm_${key}_lower`) ?? (elbowOut ? mid(elbowOut, s) : s));
      j[`wrist${side}`] = round(overlap(`arm_${key}_lower`, `fist_${key}`) ?? (fist ? lerp(e, fist.centroid, 0.25) : e));
    } else if (elbowOut) {
      j[`elbow${side}`] = round(elbowOut);
    }
    const thigh = axes.get(`leg_${key}_upper`);
    const shin = axes.get(`leg_${key}_lower`);
    let kneeOut: { x: number; y: number } | null = null;
    if (thigh) {
      const [s, e] = orient(thigh, pelvis?.centroid ?? body);
      j[`hip${side}`] = round(rootJoint(layers.get(`leg_${key}_upper`)!, s, e, canvas));
      kneeOut = e;
    }
    if (shin) {
      const [s, e] = orient(shin, kneeOut ?? body);
      const knee = overlap(`leg_${key}_upper`, `leg_${key}_lower`) ?? (kneeOut ? mid(kneeOut, s) : s);
      j[`knee${side}`] = round(knee);
      j[`ankle${side}`] = round(lerp(knee, e, 0.85));
    }
  }
  // the hip joint sits between the two thigh roots (the pelvis centroid is pulled up toward the waist)
  if (axes.get('leg_front_upper') && axes.get('leg_back_upper')) j.hip = round(mid(j.hipF, j.hipB));
  else if (pelvis) j.hip = round(pelvis.centroid);
  const hair = axes.get('hair_front');
  if (hair) {
    const [r, t] = orient(hair, head?.centroid ?? body);
    j.hairRoot = round(r);
    j.hairTip = round(t);
  }
  return j;
}

/**
 * Walking from `from` toward `to`, the first point where the layer's width (perpendicular to that line) reaches
 * `fraction` of its maximum width.
 */
function widening(a: AlphaMap, from: { x: number; y: number }, to: { x: number; y: number }, fraction: number) {
  const L = Math.hypot(to.x - from.x, to.y - from.y);
  if (L < 8) return null;
  const ux = (to.x - from.x) / L, uy = (to.y - from.y) / L;
  const bins = 50;
  const width = profile(a, from, to, bins);
  const maxW = Math.max(...width);
  const k = width.findIndex((w) => w >= fraction * maxW);
  if (k <= 0) return null;
  const s = ((k + 0.5) / bins) * L;
  return { x: from.x + ux * s, y: from.y + uy * s };
}

/** The layer's width perpendicular to the line from → to, in `bins` slices along it. */
function profile(a: AlphaMap, from: { x: number; y: number }, to: { x: number; y: number }, bins: number): number[] {
  const L = Math.hypot(to.x - from.x, to.y - from.y);
  const ux = (to.x - from.x) / L, uy = (to.y - from.y) / L;
  const lo = new Array(bins).fill(Infinity), hi = new Array(bins).fill(-Infinity);
  for (let y = 0; y < a.height; y += 2) {
    for (let x = 0; x < a.width; x += 2) {
      if (a.data[y * a.width + x] <= 128) continue;
      const s = (x - from.x) * ux + (y - from.y) * uy;
      if (s < 0 || s >= L) continue;
      const k = Math.floor((s / L) * bins);
      const p = -(x - from.x) * uy + (y - from.y) * ux;
      if (p < lo[k]) lo[k] = p;
      if (p > hi[k]) hi[k] = p;
    }
  }
  return lo.map((v, k) => (hi[k] >= v ? hi[k] - v : 0));
}


// ---------------------------------------------------------------- rig building
interface BoneSpec {
  name: string;
  parent: string | null;
  from: JointId | 'root';
  to?: JointId;
  /** For bones without a `to`: direction follows the parent, with this length. */
  length?: number;
  color?: string;
}

const BONES: BoneSpec[] = [
  { name: 'root', parent: null, from: 'root' },
  { name: 'hip', parent: 'root', from: 'hip', to: 'waist', color: 'f2c94cff' },
  { name: 'chest', parent: 'hip', from: 'waist', to: 'neckBase' },
  { name: 'neck', parent: 'chest', from: 'neckBase', to: 'headBase' },
  { name: 'head', parent: 'neck', from: 'headBase', to: 'headTop' },
  { name: 'hairF', parent: 'head', from: 'hairRoot', to: 'hairTip', color: '9b51e0ff' },
  { name: 'upperArmB', parent: 'chest', from: 'shoulderB', to: 'elbowB' },
  { name: 'foreArmB', parent: 'upperArmB', from: 'elbowB', to: 'wristB' },
  { name: 'handB', parent: 'foreArmB', from: 'wristB', length: 30 },
  { name: 'upperArmF', parent: 'chest', from: 'shoulderF', to: 'elbowF' },
  { name: 'foreArmF', parent: 'upperArmF', from: 'elbowF', to: 'wristF' },
  { name: 'handF', parent: 'foreArmF', from: 'wristF', length: 30 },
  { name: 'thighB', parent: 'hip', from: 'hipB', to: 'kneeB' },
  { name: 'shinB', parent: 'thighB', from: 'kneeB', to: 'ankleB' },
  { name: 'thighF', parent: 'hip', from: 'hipF', to: 'kneeF' },
  { name: 'shinF', parent: 'thighF', from: 'kneeF', to: 'ankleF' },
  { name: 'ik-fist-back', parent: 'root', from: 'wristB', color: 'ff3f00ff' },
  { name: 'ik-fist-front', parent: 'root', from: 'wristF', color: 'ff3f00ff' },
  { name: 'ik-foot-back', parent: 'root', from: 'ankleB', color: 'ff3f00ff' },
  { name: 'ik-foot-front', parent: 'root', from: 'ankleF', color: 'ff3f00ff' },
];

/**
 * Leg IK softness, as a fraction of the leg length. Image agents draw legs almost straight (99 %+ of their reach), so
 * the hip rising a few px in a bounce would snap a hard IK knee straight. Softness bends it smoothly instead.
 */
const LEG_SOFTNESS = 0.15;

const IK_CHAINS: { name: string; bones: string[]; target: string; mid: JointId; soft?: number }[] = [
  { name: 'arm-back', bones: ['upperArmB', 'foreArmB'], target: 'ik-fist-back', mid: 'elbowB' as JointId },
  { name: 'arm-front', bones: ['upperArmF', 'foreArmF'], target: 'ik-fist-front', mid: 'elbowF' as JointId },
  { name: 'leg-back', bones: ['thighB', 'shinB'], target: 'ik-foot-back', mid: 'kneeB' as JointId, soft: LEG_SOFTNESS },
  { name: 'leg-front', bones: ['thighF', 'shinF'], target: 'ik-foot-front', mid: 'kneeF' as JointId, soft: LEG_SOFTNESS },
];

export interface RigResult {
  json: SpineJson;
  roles: Record<string, string | string[]>;
  /** Layers used, in draw order. */
  layers: string[];
  missing: string[];
  warnings: string[];
}

/**
 * Build the skeleton. `layerNames` are the PNG names the user supplied (without .png). Every layer is a full-canvas
 * image placed exactly where it was drawn; Spine's packer strips the transparent margins when building the atlas.
 */
export interface RigOptions {
  /**
   * Draw order of this delivery, back → front (layer names without .png), e.g. from the art's `layers.json`. Use it
   * when the drawing overlaps differently from the template, like a rear glove guarding in front of the chest.
   * Layers it doesn't list keep their template place, right after the listed layer before them.
   */
  order?: string[];
  /** The layers' alpha, for the checks that compare joints with the art (head top). */
  alphas?: Map<string, AlphaMap>;
}

/**
 * Sanity checks on the joints, guessed or dragged: the mistakes that make a rig look broken although every bone
 * builds (a neck folded back, a head turned sideways, a shoulder down at the elbow).
 */
export function checkJoints(j: Joints, alphas?: Map<string, AlphaMap>): string[] {
  const out: string[] = [];
  const dist = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);
  if (j.neckBase.y <= j.headBase.y) out.push('脖子底（兩肩中點）在下巴後方的上面，脖子會反折：把「脖子底」拖到兩肩之間、「下巴後方」拖到頭和脖子交界');
  const tilt = (Math.atan2(Math.abs(j.headTop.x - j.headBase.x), j.headBase.y - j.headTop.y) * 180) / Math.PI;
  if (j.headTop.y >= j.headBase.y || tilt > 50) out.push(`頭頂點不在下巴後方的上面（頭軸歪了 ${Math.round(tilt)}°），頭會繞錯方向轉：把「頭頂」拖到頭頂正中`);
  const head = alphas?.get('head');
  if (head) {
    let top = -1, bottom = -1;
    for (let y = 0; y < head.height && top < 0; y += 2) for (let x = 0; x < head.width; x += 2) if (head.data[y * head.width + x] > 128) { top = y; break; }
    for (let y = head.height - 1; y >= 0 && bottom < 0; y -= 2) for (let x = 0; x < head.width; x += 2) if (head.data[y * head.width + x] > 128) { bottom = y; break; }
    if (top >= 0 && j.headTop.y - top > 0.25 * (bottom - top)) out.push(`頭頂點比頭的最上緣低了 ${Math.round(j.headTop.y - top)} px：把「頭頂」拖到頭頂（含帽子）`);
  }
  const torso = dist(j.neckBase, j.waist);
  for (const [id, label] of [['shoulderF', '前肩'], ['shoulderB', '後肩']] as const) {
    if (j[id].y - j.neckBase.y > 0.35 * torso) out.push(`${label}點比脖子底低了 ${Math.round(j[id].y - j.neckBase.y)} px，手臂會從胸口長出來：把它拖到肩膀頂端`);
  }
  return out;
}

export function buildRig(joints: Joints, layerNames: string[], name = 'character', canvas: CanvasSize = CANVAS, opts: RigOptions = {}): RigResult {
  const have = new Set(layerNames);
  const missing = PARTS.filter((p) => p.required && !have.has(p.name)).map((p) => p.name);
  const warnings: string[] = checkJoints(joints, opts.alphas);
  const hasHair = [...have].some((n) => n.startsWith('hair_front'));
  const pt = (id: JointId | 'root') => (id === 'root' ? { x: 0, y: 0 } : toSpine(joints[id], canvas));
  const world = new Map<string, { x: number; y: number; rot: number; len: number }>();
  const bones: SpineJson[] = [];
  for (const b of BONES) {
    if (b.name === 'hairF' && !hasHair) continue;
    const from = pt(b.from);
    let rot = 0;
    let len = 0;
    if (b.to) {
      const to = pt(b.to);
      rot = Math.atan2(to.y - from.y, to.x - from.x) * DEG;
      len = Math.hypot(to.x - from.x, to.y - from.y);
      if (len < 2) warnings.push(`骨骼 ${b.name} 的兩個關節幾乎重疊，請在關節對齊步驟分開它們`);
    } else if (b.length && b.parent) {
      rot = world.get(b.parent)!.rot;
      len = b.length;
    }
    world.set(b.name, { ...from, rot, len });
    const out: SpineJson = { name: b.name };
    if (b.parent) {
      const p = world.get(b.parent)!;
      const c = Math.cos(rad(-p.rot)), s = Math.sin(rad(-p.rot));
      const dx = from.x - p.x, dy = from.y - p.y;
      out.parent = b.parent;
      const lx = c * dx - s * dy, ly = s * dx + c * dy;
      if (Math.abs(lx) > 1e-3) out.x = r3(lx);
      if (Math.abs(ly) > 1e-3) out.y = r3(ly);
      const lr = normDeg(rot - p.rot);
      if (Math.abs(lr) > 1e-3) out.rotation = r3(lr);
    }
    if (len) out.length = r3(len);
    if (b.color) out.color = b.color;
    bones.push(out);
  }

  // slots + full-canvas region attachments, placed so every pixel lands where it was drawn
  const center = toSpine({ x: canvas.width / 2, y: canvas.height / 2 }, canvas);
  const used = PARTS.flatMap((p) => {
    if (p.name === 'hair_front') return [...have].filter((n) => n.startsWith('hair_front')).sort().map((n) => ({ name: n, bone: 'hairF' }));
    return have.has(p.name) ? [{ name: p.name, bone: p.bone }] : [];
  });
  if (opts.order?.length) {
    const unknown = opts.order.filter((n) => !used.some((u) => u.name === n));
    if (unknown.length) warnings.push(`疊圖順序裡有沒交的圖層：${unknown.join('、')}（略過）`);
    const listed = opts.order.flatMap((n) => used.filter((u) => u.name === n));
    const rest = used.filter((u) => !opts.order!.includes(u.name));
    const sorted = [...listed];
    for (const u of rest) {
      // after the listed layer that precedes it in the template, or first when none does
      const before = used.slice(0, used.indexOf(u)).reverse().find((p) => sorted.includes(p));
      sorted.splice(before ? sorted.indexOf(before) + 1 : 0, 0, u);
    }
    used.splice(0, used.length, ...sorted);
  }
  const extra = [...have].filter((n) => !PARTS.some((p) => p.name === n) && !n.startsWith('hair_front'));
  if (extra.length) warnings.push(`這些圖層不在模板裡，沒有使用：${extra.join('、')}（請改成模板的檔名）`);
  const slots: SpineJson[] = [];
  const attachments: SpineJson = {};
  for (const u of used) {
    const b = world.get(u.bone)!;
    const c = Math.cos(rad(-b.rot)), s = Math.sin(rad(-b.rot));
    const dx = center.x - b.x, dy = center.y - b.y;
    slots.push({ name: u.name, bone: u.bone, attachment: u.name });
    attachments[u.name] = { [u.name]: { x: r3(c * dx - s * dy), y: r3(s * dx + c * dy), rotation: r3(normDeg(-b.rot)), width: canvas.width, height: canvas.height } };
  }

  const json: SpineJson = {
    skeleton: { spine: '4.3.26', x: -canvas.width / 2, y: toSpine({ x: 0, y: canvas.height }, canvas).y, width: canvas.width, height: canvas.height, fps: 30, images: './images/' },
    bones,
    slots,
    constraints: IK_CHAINS.map((k) => ({ name: k.name, type: 'ik', bones: k.bones, target: k.target, bendPositive: true })),
    skins: [{ name: 'default', attachments }],
    animations: {},
  };
  json.skeleton.hash = name;

  // softened chains: push the target out along the chain so the softened solve still lands on the drawn joint
  for (const [i, k] of IK_CHAINS.entries()) {
    if (!k.soft) continue;
    const u = world.get(k.bones[0])!, l = world.get(k.bones[1])!, t = world.get(k.target)!;
    const reach = u.len + l.len;
    const s = k.soft * reach;
    const d = Math.hypot(t.x - u.x, t.y - u.y);
    const td = softTarget(d, reach, s);
    json.constraints[i].softness = r3(s);
    const out = bones.find((b) => b.name === k.target)!;
    out.x = r3(u.x + ((t.x - u.x) * td) / d);
    out.y = r3(u.y + ((t.y - u.y) * td) / d);
  }

  // pick each IK chain's bend direction so the solved elbow / knee lands on the drawn one
  for (const [i, k] of IK_CHAINS.entries()) {
    const want = pt(k.mid);
    const err = (bend: boolean) => {
      json.constraints[i].bendPositive = bend;
      const sk = new Skeleton(parseSkeleton(json));
      sk.setupPose();
      sk.updateWorldTransform(Physics.none);
      const p = sk.findBone(k.bones[1])!.getAppliedPose();
      return Math.hypot(p.worldX - want.x, p.worldY - want.y);
    };
    const a = err(true);
    const b = err(false);
    json.constraints[i].bendPositive = a <= b;
    if (Math.min(a, b) > 3) warnings.push(`${k.name} 的 IK 解出的位置和畫的關節差 ${Math.min(a, b).toFixed(1)} px，請檢查關節是否對齊`);
  }

  const roles: Record<string, string | string[]> = { ...TEMPLATE_ROLES };
  if (hasHair) {
    roles.hair = ['hairF'];
    roles.chain = ['hairF'];
  }
  return { json, roles, layers: used.map((u) => u.name), missing, warnings };
}

/** Target distance at which a 2-bone IK with this softness reaches `d` (inverts spine-core's softness curve). */
function softTarget(d: number, reach: number, s: number): number {
  const reached = (td: number) => {
    const sd = td - reach + s;
    if (sd <= 0) return td;
    const p = Math.min(1, sd / (2 * s)) - 1;
    return td - (sd - s * (1 - p * p));
  };
  if (reached(d) >= d - 1e-9) return d;
  let lo = d, hi = reach + s;
  for (let i = 0; i < 60; i++) {
    const m = (lo + hi) / 2;
    if (reached(m) < d) lo = m;
    else hi = m;
  }
  return (lo + hi) / 2;
}

function r3(x: number) {
  return Math.round(x * 1000) / 1000;
}
function normDeg(d: number) {
  return ((d + 540) % 360) - 180;
}

/** Joint list for UIs (labels + which joints connect, for drawing the guide lines). */
export const JOINT_LINKS: [JointId, JointId][] = BONES.filter((b) => b.to && b.from !== 'root').map((b) => [b.from as JointId, b.to!]);
export { JOINTS };
