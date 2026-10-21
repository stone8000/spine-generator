/**
 * Seam check: play each animation with the real layers and find joints where background shows through.
 *
 * A gap is background showing where the parent part was meant to be painted. For each joint, the parent layer's
 * intended outline (its alpha with notches up to ~40 px closed) is rendered moving with the parent; cells inside that
 * outline but covered by no layer are exposed. Exposure near the joint is compared with the rest pose. The inside of
 * an elbow or a fist passing the chest lies outside the parent's own outline, so it is never reported. Joints that are
 * not drawn even at rest (a part missing or cut off) are reported separately.
 *
 * Rendering is done in software on a coarse grid (no canvas needed), so Node tests and the browser share it.
 */
import { Skeleton, AnimationState, AnimationStateData, Physics, type SkeletonData } from '@esotericsoftware/spine-core';
import type { AlphaMap } from './autorig.ts';
import { parseSkeleton, type SpineJson } from './spineJson.ts';

export interface RenderLayer {
  name: string;
  bone: string;
  alpha: AlphaMap;
  att: { x: number; y: number; rotation: number; scaleX: number; scaleY: number; width: number; height: number };
  /** Opaque bounding box in the alpha map's pixels, to limit the rasterization work. */
  box: { x0: number; y0: number; x1: number; y1: number };
}

export function opaqueBox(a: AlphaMap): RenderLayer['box'] {
  let x0 = a.width, y0 = a.height, x1 = -1, y1 = -1;
  for (let y = 0; y < a.height; y++) {
    const row = y * a.width;
    for (let x = 0; x < a.width; x++) {
      if (a.data[row + x] > 128) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  return { x0, y0, x1, y1 };
}

/** Region attachments of the default skin, in draw order, paired with their alpha maps. */
export function layersFromRig(json: SpineJson, alphas: Map<string, AlphaMap>): RenderLayer[] {
  const skin = (json.skins ?? []).find((s: SpineJson) => s.name === 'default') ?? json.skins?.[0];
  const out: RenderLayer[] = [];
  for (const slot of json.slots ?? []) {
    const attName = slot.attachment;
    const att = skin?.attachments?.[slot.name]?.[attName];
    const alpha = alphas.get(attName) ?? alphas.get(att?.path ?? '');
    if (!att || !alpha || (att.type && att.type !== 'region')) continue;
    out.push({
      name: attName,
      bone: slot.bone,
      alpha,
      att: { x: att.x ?? 0, y: att.y ?? 0, rotation: att.rotation ?? 0, scaleX: att.scaleX ?? 1, scaleY: att.scaleY ?? 1, width: att.width, height: att.height },
      box: opaqueBox(alpha),
    });
  }
  return out;
}

export interface Grid {
  x0: number;
  y0: number;
  cell: number;
  w: number;
  h: number;
}

/** Coverage mask: 1 where any layer is opaque at the cell center. Row 0 is the bottom row (Spine y up). */
export function renderMask(sk: Skeleton, layers: RenderLayer[], g: Grid, out = new Uint8Array(g.w * g.h)): Uint8Array {
  out.fill(0);
  for (const L of layers) {
    if (L.box.x1 < 0) continue; // empty layer
    const p = sk.findBone(L.bone)?.getAppliedPose();
    if (!p) continue;
    const r = (L.att.rotation * Math.PI) / 180;
    const cr = Math.cos(r), sr = Math.sin(r);
    // M = B · R(rot) · S ; Q = P + B · (x, y)
    const m00 = (p.a * cr + p.b * sr) * L.att.scaleX, m01 = (-p.a * sr + p.b * cr) * L.att.scaleY;
    const m10 = (p.c * cr + p.d * sr) * L.att.scaleX, m11 = (-p.c * sr + p.d * cr) * L.att.scaleY;
    const qx = p.worldX + p.a * L.att.x + p.b * L.att.y;
    const qy = p.worldY + p.c * L.att.x + p.d * L.att.y;
    const det = m00 * m11 - m01 * m10;
    if (Math.abs(det) < 1e-9) continue;
    const i00 = m11 / det, i01 = -m01 / det, i10 = -m10 / det, i11 = m00 / det;
    const W = L.att.width, H = L.att.height, A = L.alpha;
    const sxImg = A.width / W, syImg = A.height / H;
    let wx0 = Infinity, wy0 = Infinity, wx1 = -Infinity, wy1 = -Infinity;
    for (const [ix, iy] of [[L.box.x0, L.box.y0], [L.box.x1 + 1, L.box.y0], [L.box.x0, L.box.y1 + 1], [L.box.x1 + 1, L.box.y1 + 1]]) {
      const u = ix / sxImg - W / 2, v = H / 2 - iy / syImg;
      const wx = qx + m00 * u + m01 * v, wy = qy + m10 * u + m11 * v;
      if (wx < wx0) wx0 = wx;
      if (wx > wx1) wx1 = wx;
      if (wy < wy0) wy0 = wy;
      if (wy > wy1) wy1 = wy;
    }
    const c0 = Math.max(0, Math.floor((wx0 - g.x0) / g.cell)), c1 = Math.min(g.w - 1, Math.ceil((wx1 - g.x0) / g.cell));
    const r0 = Math.max(0, Math.floor((wy0 - g.y0) / g.cell)), r1 = Math.min(g.h - 1, Math.ceil((wy1 - g.y0) / g.cell));
    for (let row = r0; row <= r1; row++) {
      const wy = g.y0 + (row + 0.5) * g.cell - qy;
      for (let col = c0; col <= c1; col++) {
        const k = row * g.w + col;
        if (out[k]) continue;
        const wx = g.x0 + (col + 0.5) * g.cell - qx;
        const u = i00 * wx + i01 * wy, v = i10 * wx + i11 * wy;
        const ix = Math.floor((u + W / 2) * sxImg), iy = Math.floor((H / 2 - v) * syImg);
        if (ix < 0 || iy < 0 || ix >= A.width || iy >= A.height) continue;
        if (A.data[iy * A.width + ix] > 128) out[k] = 1;
      }
    }
  }
  return out;
}

/** Separable square max (dilate) or min (erode) filter on a 0/1 mask. */
function filter(src: Uint8Array, w: number, h: number, r: number, max: boolean): Uint8Array {
  const tmp = new Uint8Array(w * h);
  const out = new Uint8Array(w * h);
  const edge = max ? 0 : 1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = edge;
      for (let d = -r; d <= r; d++) {
        const xx = x + d;
        const s = xx < 0 || xx >= w ? edge : src[y * w + xx];
        if (max ? s > v : s < v) v = s;
      }
      tmp[y * w + x] = v;
    }
  }
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = edge;
      for (let d = -r; d <= r; d++) {
        const yy = y + d;
        const s = yy < 0 || yy >= h ? edge : tmp[yy * w + x];
        if (max ? s > v : s < v) v = s;
      }
      out[y * w + x] = v;
    }
  }
  return out;
}

/**
 * The part's intended outline: majority-pool the alpha map by `f`, close notches up to `notchPx`, then shrink by one
 * cell so the outline lies strictly inside the drawing (otherwise its edge flickers against the real alpha as the part
 * rotates and reads as a false gap).
 */
export function intendedOutline(a: AlphaMap, f: number, notchPx: number): AlphaMap {
  const pad = Math.max(1, Math.round(notchPx / f)) + 2;
  const w = Math.ceil(a.width / f) + 2 * pad, h = Math.ceil(a.height / f) + 2 * pad;
  const count = new Uint16Array(w * h);
  for (let y = 0; y < a.height; y++) {
    const row = y * a.width, my = (Math.floor(y / f) + pad) * w + pad;
    for (let x = 0; x < a.width; x++) if (a.data[row + x] > 128) count[my + Math.floor(x / f)]++;
  }
  const m = new Uint8Array(w * h);
  const half = (f * f) / 2;
  for (let i = 0; i < m.length; i++) m[i] = count[i] >= half ? 1 : 0;
  const r = pad - 2;
  const closed = filter(filter(filter(m, w, h, r, true), w, h, r, false), w, h, 1, false);
  // crop the padding back off so the map still covers exactly the original image
  const cw = Math.ceil(a.width / f), ch = Math.ceil(a.height / f);
  const data = new Uint8Array(cw * ch);
  for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) data[y * cw + x] = closed[(y + pad) * w + x + pad] ? 255 : 0;
  return { width: cw, height: ch, data };
}

/** Joints checked: the origin of each bone where two layers meet. */
export const SEAM_JOINTS: { bone: string; label: string }[] = [
  { bone: 'chest', label: '腰' },
  { bone: 'neck', label: '脖子根' },
  { bone: 'head', label: '頭頸交界' },
  { bone: 'hairF', label: '前髮根' },
  { bone: 'upperArmF', label: '前肩' },
  { bone: 'upperArmB', label: '後肩' },
  { bone: 'foreArmF', label: '前手肘' },
  { bone: 'foreArmB', label: '後手肘' },
  { bone: 'handF', label: '前手腕' },
  { bone: 'handB', label: '後手腕' },
  { bone: 'thighF', label: '前腿根' },
  { bone: 'thighB', label: '後腿根' },
  { bone: 'shinF', label: '前膝' },
  { bone: 'shinB', label: '後膝' },
];

export interface SeamIssue {
  joint: string;
  bone: string;
  /** Seconds into the animation where the gap is largest. */
  time: number;
  /** Exposed area (beyond the rest pose), canvas px². */
  area: number;
  /** Where (Spine units), for highlighting. */
  x: number;
  y: number;
  /** True when the joint is not drawn even in the rest pose (part missing or cut off). */
  rest?: boolean;
}

export interface SeamReport {
  animation: string;
  duration: number;
  frames: number;
  issues: SeamIssue[];
}

export interface SeamOptions {
  animations?: string[];
  /** Grid cell size in canvas px. */
  cell?: number;
  /** How large a notch in a parent layer still counts as "meant to be painted" (canvas px). */
  notch?: number;
  /** Radius around each joint that is checked, canvas px. */
  radius?: number;
  /** Report a joint when the exposed area grows by more than this many px² over the rest pose. */
  threshold?: number;
  fps?: number;
}

export function checkSeams(json: SpineJson, alphas: Map<string, AlphaMap>, opts: SeamOptions = {}): SeamReport[] {
  const data: SkeletonData = parseSkeleton(json);
  const layers = layersFromRig(json, alphas);
  const cell = opts.cell ?? 4;
  const radius = opts.radius ?? 56;
  const threshold = opts.threshold ?? 120;
  const fps = opts.fps ?? 30;
  const notch = opts.notch ?? 40;
  const sk = new Skeleton(data);
  const joints = SEAM_JOINTS.filter((j) => data.findBone(j.bone));

  // each joint's parent group: the layers on the nearest ancestor bone that has any, as intended outlines
  const outlineCache = new Map<string, RenderLayer>();
  const outlineOf = (L: RenderLayer) => {
    let o = outlineCache.get(L.name);
    if (!o) {
      const alpha = intendedOutline(L.alpha, cell, notch);
      o = { ...L, alpha, box: opaqueBox(alpha) };
      outlineCache.set(L.name, o);
    }
    return o;
  };
  const parentOutlines = new Map<string, RenderLayer[]>();
  for (const j of joints) {
    let b = data.findBone(j.bone)!.parent;
    let group: RenderLayer[] = [];
    while (b && !group.length) {
      const name = b.name;
      group = layers.filter((L) => L.bone === name);
      b = b.parent;
    }
    parentOutlines.set(j.bone, group.map(outlineOf));
  }

  const rc = Math.ceil(radius / cell);
  const size = 2 * rc + 1;
  const union = new Uint8Array(size * size);
  const intended = new Uint8Array(size * size);
  const exposed = (bone: string) => {
    const p = sk.findBone(bone)!.getAppliedPose();
    const g: Grid = { x0: p.worldX - (rc + 0.5) * cell, y0: p.worldY - (rc + 0.5) * cell, cell, w: size, h: size };
    renderMask(sk, layers, g, union);
    renderMask(sk, parentOutlines.get(bone)!, g, intended);
    let n = 0;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const dx = x - rc, dy = y - rc;
        if (dx * dx + dy * dy > rc * rc) continue;
        const k = y * size + x;
        if (intended[k] && !union[k]) n++;
      }
    }
    return { n, x: p.worldX, y: p.worldY };
  };

  // rest pose: baseline exposure per joint, and whether each joint is drawn at all
  sk.setupPose();
  sk.updateWorldTransform(Physics.none);
  const base = new Map(joints.map((j) => [j.bone, exposed(j.bone).n]));
  const restIssues: SeamIssue[] = [];
  const pin = new Uint8Array(25);
  for (const j of joints) {
    const p = sk.findBone(j.bone)!.getAppliedPose();
    renderMask(sk, layers, { x0: p.worldX - 7.5, y0: p.worldY - 7.5, cell: 3, w: 5, h: 5 }, pin); // 15×15 px around the joint
    const uncovered = pin.reduce((s, v) => s + (v ? 0 : 1), 0);
    if (uncovered > 3) {
      restIssues.push({ joint: j.label, bone: j.bone, time: 0, area: uncovered * 9, x: p.worldX, y: p.worldY, rest: true });
    } else if (base.get(j.bone)! * cell * cell > threshold) {
      // background already shows inside the parent's outline before anything moves (a hidden area left unpainted)
      restIssues.push({ joint: j.label, bone: j.bone, time: 0, area: base.get(j.bone)! * cell * cell, x: p.worldX, y: p.worldY, rest: true });
    }
  }

  const reports: SeamReport[] = [];
  for (const anim of data.animations) {
    if (opts.animations && !opts.animations.includes(anim.name)) continue;
    const D = anim.duration;
    const n = Math.max(1, Math.round(D * fps));
    const state = new AnimationState(new AnimationStateData(data));
    const worst = new Map<string, SeamIssue>(restIssues.map((i) => [i.bone, i]));
    for (let i = 0; i <= n; i++) {
      const t = (D * i) / n;
      sk.setupPose();
      state.setAnimation(0, anim.name, false);
      state.update(t);
      state.apply(sk);
      sk.updateWorldTransform(Physics.none);
      for (const j of joints) {
        if (worst.get(j.bone)?.rest) continue;
        const e = exposed(j.bone);
        const extra = (e.n - base.get(j.bone)!) * cell * cell;
        if (extra > threshold && extra > (worst.get(j.bone)?.area ?? 0)) {
          worst.set(j.bone, { joint: j.label, bone: j.bone, time: Math.round(t * 1000) / 1000, area: extra, x: e.x, y: e.y });
        }
      }
    }
    reports.push({ animation: anim.name, duration: D, frames: n + 1, issues: [...worst.values()].sort((a, b) => Number(!!b.rest) - Number(!!a.rest) || b.area - a.area) });
  }
  return reports;
}
