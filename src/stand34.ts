/**
 * Second character template: a standing figure in front three-quarter view (stand34). Built for performers who talk
 * with their hands — a stage hypnotist, a magician, a ringmaster — where the boxing guard template has nothing to say.
 *
 * Same contract as rigTemplate.ts: one canvas, one drawn pose, fixed layer names, joints on the canvas (px, y down).
 * What it adds:
 *  - Swap slots: one slot holds several drawings (head_idle / head_blink / head_spiral …, hand_r_pinch / hand_r_open …)
 *    painted at the same joint, and the motions key which one shows.
 *  - Props on their own bones (hat, glasses) so a hand can take them.
 *  - A tail chain and two coat tails that follow the body through physics.
 *  - Shoes that stay flat on the floor whatever the knee does (inherit: noRotationOrReflection).
 *
 * Sides are named by the screen: L = the screen-left limb, R = the screen-right limb. The figure turns toward screen
 * left (toward a board standing left of it), so R is the near side (drawn over the torso) and L the far side. Bones keep
 * the kit's names so seams / pose checks / budgets work unchanged: F (near) = R, B (far) = L.
 */
import { Skeleton, Physics } from '@esotericsoftware/spine-core';
import type { AlphaMap } from './autorig.ts';
import { parseSkeleton, type SpineJson } from './spineJson.ts';
import type { RecipeAnimation } from './recipe.ts';

export const STAND34_CANVAS = { width: 1024, height: 1536 } as const;
export const STAND34_ROOT = { x: 512, y: 1456 } as const;

export type Pt = { x: number; y: number };
export type Joints34 = Record<string, Pt>;

export interface Joint34 {
  id: string;
  label: string;
  x: number;
  y: number;
}

/** Defaults for a figure about 1400 px tall to the hat top, turned ~30° toward screen left. */
export const JOINTS34: Joint34[] = [
  { id: 'hip', label: '骨盆中心', x: 515, y: 870 },
  { id: 'waist', label: '腰', x: 515, y: 770 },
  { id: 'neckBase', label: '脖子底（兩肩中點）', x: 520, y: 480 },
  { id: 'headBase', label: '下巴後方（頭頸交界）', x: 515, y: 440 },
  { id: 'headTop', label: '頭頂（帽子底下）', x: 505, y: 210 },
  { id: 'hatBase', label: '帽子底中心', x: 510, y: 245 },
  { id: 'hatTop', label: '帽頂', x: 500, y: 60 },
  { id: 'glasses', label: '眼鏡中心', x: 455, y: 335 },
  { id: 'shoulderR', label: 'R 肩（近）', x: 615, y: 500 },
  { id: 'elbowR', label: 'R 手肘', x: 690, y: 650 },
  { id: 'wristR', label: 'R 手腕', x: 590, y: 585 },
  { id: 'shoulderL', label: 'L 肩（遠）', x: 425, y: 505 },
  { id: 'elbowL', label: 'L 手肘', x: 345, y: 650 },
  { id: 'wristL', label: 'L 手腕', x: 420, y: 765 },
  { id: 'hipR', label: 'R 腿根', x: 565, y: 890 },
  { id: 'kneeR', label: 'R 膝', x: 622, y: 1150 },
  { id: 'ankleR', label: 'R 腳踝', x: 600, y: 1400 },
  { id: 'toeR', label: 'R 鞋尖', x: 680, y: 1445 },
  { id: 'hipL', label: 'L 腿根', x: 465, y: 890 },
  { id: 'kneeL', label: 'L 膝', x: 402, y: 1150 },
  { id: 'ankleL', label: 'L 腳踝', x: 425, y: 1405 },
  { id: 'toeL', label: 'L 鞋尖', x: 335, y: 1448 },
  { id: 'tailRoot', label: '尾根', x: 600, y: 905 },
  { id: 'tail2', label: '尾巴第二節', x: 725, y: 985 },
  { id: 'tail3', label: '尾巴第三節', x: 805, y: 865 },
  { id: 'tailTip', label: '尾尖', x: 765, y: 740 },
  { id: 'coatRootL', label: 'L 燕尾根', x: 470, y: 800 },
  { id: 'coatTipL', label: 'L 燕尾尖', x: 450, y: 1010 },
  { id: 'coatRootR', label: 'R 燕尾根', x: 570, y: 800 },
  { id: 'coatTipR', label: 'R 燕尾尖', x: 605, y: 1010 },
];

export interface Part34 {
  /** Slot name. */
  slot: string;
  /** Layer files that can show in it, without .png; the first one present is the setup attachment. */
  files: string[];
  bone: string;
  required: boolean;
  label: string;
}

/** Draw order, back → front. */
export const PARTS34: Part34[] = [
  { slot: 'tail_3', files: ['tail_3'], bone: 'tail3', required: false, label: '尾巴末段' },
  { slot: 'tail_2', files: ['tail_2'], bone: 'tail2', required: false, label: '尾巴中段' },
  { slot: 'tail_1', files: ['tail_1'], bone: 'tail1', required: false, label: '尾巴根段' },
  { slot: 'coat_tail_l', files: ['coat_tail_l'], bone: 'coatL', required: false, label: 'L 燕尾' },
  { slot: 'coat_tail_r', files: ['coat_tail_r'], bone: 'coatR', required: false, label: 'R 燕尾' },
  { slot: 'arm_l_upper', files: ['arm_l_upper'], bone: 'upperArmB', required: true, label: 'L 上臂' },
  { slot: 'arm_l_lower', files: ['arm_l_lower'], bone: 'foreArmB', required: true, label: 'L 前臂' },
  { slot: 'hand_l', files: ['hand_l_hip', 'hand_l_point', 'hand_l_open'], bone: 'handB', required: true, label: 'L 手' },
  { slot: 'leg_l_upper', files: ['leg_l_upper'], bone: 'thighB', required: true, label: 'L 大腿' },
  { slot: 'leg_l_lower', files: ['leg_l_lower'], bone: 'shinB', required: true, label: 'L 小腿' },
  { slot: 'foot_l', files: ['foot_l'], bone: 'footB', required: true, label: 'L 鞋' },
  { slot: 'pelvis', files: ['pelvis'], bone: 'hip', required: true, label: '骨盆' },
  { slot: 'leg_r_upper', files: ['leg_r_upper'], bone: 'thighF', required: true, label: 'R 大腿' },
  { slot: 'leg_r_lower', files: ['leg_r_lower'], bone: 'shinF', required: true, label: 'R 小腿' },
  { slot: 'foot_r', files: ['foot_r'], bone: 'footF', required: true, label: 'R 鞋' },
  { slot: 'neck', files: ['neck'], bone: 'neck', required: false, label: '脖子' },
  { slot: 'torso', files: ['torso'], bone: 'chest', required: true, label: '上身' },
  { slot: 'head', files: ['head_idle', 'head_blink', 'head_spiral', 'head_grin', 'head_annoyed'], bone: 'head', required: true, label: '頭' },
  { slot: 'hat', files: ['hat'], bone: 'hat', required: false, label: '帽子' },
  { slot: 'glasses', files: ['glasses'], bone: 'glasses', required: false, label: '眼鏡' },
  { slot: 'arm_r_upper', files: ['arm_r_upper'], bone: 'upperArmF', required: true, label: 'R 上臂' },
  { slot: 'arm_r_lower', files: ['arm_r_lower'], bone: 'foreArmF', required: true, label: 'R 前臂' },
  { slot: 'hand_r', files: ['hand_r_pinch', 'hand_r_open', 'hand_r_grip', 'hand_r_hatheld'], bone: 'handF', required: true, label: 'R 手' },
];

export const STAND34_FILES = new Set(PARTS34.flatMap((p) => p.files));

/** True when a delivery is drawn to this template (any of its distinctive layer names). */
export function isStand34(layerNames: Iterable<string>): boolean {
  for (const n of layerNames) if (n.startsWith('head_') || n.startsWith('hand_r_') || n.startsWith('hand_l_') || n === 'foot_r') return true;
  return false;
}

interface Bone34 {
  name: string;
  parent: string | null;
  from: string | 'root';
  to?: string;
  length?: number;
  inherit?: string;
  color?: string;
}

export const BONES34: Bone34[] = [
  { name: 'root', parent: null, from: 'root' },
  { name: 'hip', parent: 'root', from: 'hip', to: 'waist', color: 'f2c94cff' },
  { name: 'chest', parent: 'hip', from: 'waist', to: 'neckBase' },
  { name: 'neck', parent: 'chest', from: 'neckBase', to: 'headBase' },
  { name: 'head', parent: 'neck', from: 'headBase', to: 'headTop' },
  { name: 'hat', parent: 'head', from: 'hatBase', to: 'hatTop', color: '9b51e0ff' },
  { name: 'glasses', parent: 'head', from: 'glasses', length: 24, color: '9b51e0ff' },
  { name: 'upperArmB', parent: 'chest', from: 'shoulderL', to: 'elbowL' },
  { name: 'foreArmB', parent: 'upperArmB', from: 'elbowL', to: 'wristL' },
  { name: 'handB', parent: 'foreArmB', from: 'wristL', length: 40 },
  { name: 'upperArmF', parent: 'chest', from: 'shoulderR', to: 'elbowR' },
  { name: 'foreArmF', parent: 'upperArmF', from: 'elbowR', to: 'wristR' },
  { name: 'handF', parent: 'foreArmF', from: 'wristR', length: 40 },
  { name: 'thighB', parent: 'hip', from: 'hipL', to: 'kneeL' },
  { name: 'shinB', parent: 'thighB', from: 'kneeL', to: 'ankleL' },
  { name: 'footB', parent: 'shinB', from: 'ankleL', to: 'toeL', inherit: 'noRotationOrReflection' },
  { name: 'thighF', parent: 'hip', from: 'hipR', to: 'kneeR' },
  { name: 'shinF', parent: 'thighF', from: 'kneeR', to: 'ankleR' },
  { name: 'footF', parent: 'shinF', from: 'ankleR', to: 'toeR', inherit: 'noRotationOrReflection' },
  { name: 'tail1', parent: 'hip', from: 'tailRoot', to: 'tail2', color: '9b51e0ff' },
  { name: 'tail2', parent: 'tail1', from: 'tail2', to: 'tail3', color: '9b51e0ff' },
  { name: 'tail3', parent: 'tail2', from: 'tail3', to: 'tailTip', color: '9b51e0ff' },
  { name: 'coatL', parent: 'hip', from: 'coatRootL', to: 'coatTipL', color: '9b51e0ff' },
  { name: 'coatR', parent: 'hip', from: 'coatRootR', to: 'coatTipR', color: '9b51e0ff' },
  { name: 'ik-fist-back', parent: 'root', from: 'wristL', color: 'ff3f00ff' },
  { name: 'ik-fist-front', parent: 'root', from: 'wristR', color: 'ff3f00ff' },
  { name: 'ik-foot-back', parent: 'root', from: 'ankleL', color: 'ff3f00ff' },
  { name: 'ik-foot-front', parent: 'root', from: 'ankleR', color: 'ff3f00ff' },
];

const LEG_SOFTNESS = 0.12;
const IK34: { name: string; bones: string[]; target: string; mid: string; soft?: number }[] = [
  { name: 'arm-back', bones: ['upperArmB', 'foreArmB'], target: 'ik-fist-back', mid: 'elbowL' },
  { name: 'arm-front', bones: ['upperArmF', 'foreArmF'], target: 'ik-fist-front', mid: 'elbowR' },
  { name: 'leg-back', bones: ['thighB', 'shinB'], target: 'ik-foot-back', mid: 'kneeL', soft: LEG_SOFTNESS },
  { name: 'leg-front', bones: ['thighF', 'shinF'], target: 'ik-foot-front', mid: 'kneeR', soft: LEG_SOFTNESS },
];

export const ROLES34: Record<string, string | string[]> = {
  root: 'root', hip: 'hip', body: 'hip', chest: 'chest', neck: 'neck', head: 'head', hat: 'hat', glasses: 'glasses',
  fistFront: 'ik-fist-front', fistBack: 'ik-fist-back', fists: ['ik-fist-front', 'ik-fist-back'],
  feet: ['ik-foot-front', 'ik-foot-back'], shoulders: ['upperArmF', 'upperArmB'],
  tail: ['tail1', 'tail2', 'tail3'], coat: ['coatL', 'coatR'],
};

/** The motions every stand34 delivery is tried with (Hypno Monkey's list, 2026-10-27). */
export const STAND34_ANIMS: RecipeAnimation[] = [
  { name: 'idle', preset: 'stand-idle' },
  { name: 'idle_fidget', preset: 'stand-fidget' },
  { name: 'gaze', preset: 'hypno-gaze' },
  { name: 'react_small', preset: 'hat-tip' },
  { name: 'gaze_dud', preset: 'shrug' },
  { name: 'win_big', preset: 'hat-raise' },
  { name: 'win_big_loop', preset: 'stand-cheer' },
  { name: 'win_big_end', preset: 'hat-on' },
];

export const STAND34_LABELS: Record<string, string> = {
  idle: '待機', idle_fidget: '待機小動作', gaze: '催眠一瞪', react_small: '小獎・點帽', gaze_dud: '丟空・聳肩', win_big: '大獎・摘帽', win_big_loop: '大獎循環', win_big_end: '戴回帽子',
};

/** Preview GIF order: how the motions chain in the game. */
export const STAND34_SEQUENCE = ['idle', 'gaze', 'idle', 'react_small', 'idle_fidget', 'gaze_dud', 'idle', 'win_big', 'win_big_loop', 'win_big_end', 'idle'];

export function defaultJoints34(c: { width: number; height: number } = STAND34_CANVAS): Joints34 {
  const sx = c.width / STAND34_CANVAS.width, sy = c.height / STAND34_CANVAS.height;
  return Object.fromEntries(JOINTS34.map((j) => [j.id, { x: j.x * sx, y: j.y * sy }]));
}

/** Centroid of the pixels opaque in both layers. */
function overlapCentroid(a: AlphaMap, b: AlphaMap, stride = 2): Pt | null {
  let sx = 0, sy = 0, n = 0;
  for (let y = 0; y < a.height; y += stride) for (let x = 0; x < a.width; x += stride) {
    const i = y * a.width + x;
    if (a.data[i] > 128 && b.data[i] > 128) { sx += x; sy += y; n++; }
  }
  return n >= 6 ? { x: sx / n, y: sy / n } : null;
}

function bbox(a: AlphaMap): { x0: number; y0: number; x1: number; y1: number; cx: number; cy: number } | null {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, sx = 0, sy = 0, n = 0;
  for (let y = 0; y < a.height; y += 2) for (let x = 0; x < a.width; x += 2) if (a.data[y * a.width + x] > 128) {
    if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y; sx += x; sy += y; n++;
  }
  return n ? { x0, y0, x1, y1, cx: sx / n, cy: sy / n } : null;
}

/**
 * Joints from the art where two neighbouring parts overlap (the joint sits in the middle of the shared, rounded
 * overlap); every joint the art can't place keeps the template default. Hand-placed joints.json beats this.
 */
export function guessJoints34(layers: Map<string, AlphaMap>): Joints34 {
  const first = layers.values().next().value;
  const canvas = first ? { width: first.width, height: first.height } : STAND34_CANVAS;
  const j = defaultJoints34(canvas);
  const get = (n: string) => layers.get(n) ?? (n === 'head' ? layers.get('head_idle') : n === 'hand_l' ? layers.get('hand_l_hip') : n === 'hand_r' ? layers.get('hand_r_pinch') : undefined);
  const set = (id: string, p: Pt | null) => { if (p) j[id] = { x: Math.round(p.x), y: Math.round(p.y) }; };
  const ov = (p: string, q: string) => { const a = get(p), b = get(q); return a && b ? overlapCentroid(a, b) : null; };
  set('elbowR', ov('arm_r_upper', 'arm_r_lower'));
  set('wristR', ov('arm_r_lower', 'hand_r'));
  set('elbowL', ov('arm_l_upper', 'arm_l_lower'));
  set('wristL', ov('arm_l_lower', 'hand_l'));
  set('kneeR', ov('leg_r_upper', 'leg_r_lower'));
  set('ankleR', ov('leg_r_lower', 'foot_r'));
  set('kneeL', ov('leg_l_upper', 'leg_l_lower'));
  set('ankleL', ov('leg_l_lower', 'foot_l'));
  set('tail2', ov('tail_1', 'tail_2'));
  set('tail3', ov('tail_2', 'tail_3'));
  set('hipR', ov('pelvis', 'leg_r_upper'));
  set('hipL', ov('pelvis', 'leg_l_upper'));
  set('shoulderR', ov('torso', 'arm_r_upper'));
  set('shoulderL', ov('torso', 'arm_l_upper'));
  set('waist', ov('torso', 'pelvis'));
  const head = get('head');
  const hb = head && bbox(head);
  const neck = get('neck');
  if (hb) {
    set('headTop', { x: hb.cx, y: hb.y0 });
    set('headBase', (neck && head && overlapCentroid(neck, head)) ?? { x: hb.cx, y: hb.y1 - 0.12 * (hb.y1 - hb.y0) });
  }
  const torso = get('torso');
  const tb = torso && bbox(torso);
  if (tb) set('neckBase', { x: tb.cx, y: tb.y0 + 0.06 * (tb.y1 - tb.y0) });
  const hat = get('hat');
  const hab = hat && bbox(hat);
  if (hab) { set('hatBase', { x: hab.cx, y: hab.y1 - 0.08 * (hab.y1 - hab.y0) }); set('hatTop', { x: hab.cx, y: hab.y0 }); }
  const gl = get('glasses');
  const gb = gl && bbox(gl);
  if (gb) set('glasses', { x: gb.cx, y: gb.cy });
  for (const [foot, toe] of [['foot_r', 'toeR'], ['foot_l', 'toeL']] as const) {
    const f = get(foot), b = f && bbox(f);
    if (b) set(toe, { x: toe === 'toeR' ? b.x1 : b.x0, y: b.y1 });
  }
  const tail3 = get('tail_3'), t3 = tail3 && bbox(tail3);
  if (t3) set('tailTip', { x: t3.cx, y: t3.y0 });
  for (const s of ['L', 'R'] as const) {
    const c = get(`coat_tail_${s.toLowerCase()}`), b = c && bbox(c);
    if (b) { set(`coatRoot${s}`, { x: b.cx, y: b.y0 + 0.1 * (b.y1 - b.y0) }); set(`coatTip${s}`, { x: b.cx, y: b.y1 }); }
  }
  if (layers.get('leg_r_upper') && layers.get('leg_l_upper')) j.hip = { x: Math.round((j.hipR.x + j.hipL.x) / 2), y: Math.round((j.hipR.y + j.hipL.y) / 2) };
  return j;
}

export function checkJoints34(j: Joints34): string[] {
  const out: string[] = [];
  if (j.neckBase.y <= j.headBase.y) out.push('脖子底在下巴後方上面，脖子會反折：把「脖子底」拖到兩肩之間');
  if (j.headTop.y >= j.headBase.y) out.push('頭頂點不在下巴後方上面：把「頭頂」拖到頭頂正中');
  if (j.hatTop.y >= j.hatBase.y) out.push('帽頂在帽底下面：檢查帽子兩點');
  const torso = Math.hypot(j.neckBase.x - j.waist.x, j.neckBase.y - j.waist.y);
  for (const id of ['shoulderR', 'shoulderL']) if (j[id].y - j.neckBase.y > 0.35 * torso) out.push(`${id === 'shoulderR' ? 'R' : 'L'} 肩比脖子底低太多，手臂會從胸口長出來：拖到肩膀頂端`);
  return out;
}

const DEG = 180 / Math.PI;
const r3 = (x: number) => Math.round(x * 1000) / 1000;
const normDeg = (d: number) => ((d + 540) % 360) - 180;

/** Target distance at which a softened 2-bone IK reaches `d` (inverts spine-core's softness curve). */
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
    if (reached(m) < d) lo = m; else hi = m;
  }
  return (lo + hi) / 2;
}

export interface Rig34 {
  json: SpineJson;
  roles: Record<string, string | string[]>;
  layers: string[];
  missing: string[];
  warnings: string[];
}

export function buildRig34(joints: Joints34, layerNames: string[], name = 'character', canvas: { width: number; height: number } = STAND34_CANVAS, order?: string[]): Rig34 {
  const have = new Set(layerNames);
  const root = { x: (STAND34_ROOT.x / STAND34_CANVAS.width) * canvas.width, y: (STAND34_ROOT.y / STAND34_CANVAS.height) * canvas.height };
  const toSpine = (p: Pt) => ({ x: p.x - root.x, y: root.y - p.y });
  const missing = PARTS34.filter((p) => p.required && !p.files.some((f) => have.has(f))).map((p) => p.files[0]);
  const warnings = checkJoints34(joints);
  const extra = [...have].filter((n) => !STAND34_FILES.has(n));
  if (extra.length) warnings.push(`這些圖層不在 stand34 模板裡，沒有使用：${extra.join('、')}`);

  const usedBones = new Set(PARTS34.filter((p) => p.files.some((f) => have.has(f))).map((p) => p.bone));
  const optionalBones = new Set(['hat', 'glasses', 'tail1', 'tail2', 'tail3', 'coatL', 'coatR']);
  const world = new Map<string, { x: number; y: number; rot: number; len: number }>();
  const bones: SpineJson[] = [];
  for (const b of BONES34) {
    if (optionalBones.has(b.name) && !usedBones.has(b.name) && !(b.name.startsWith('tail') && usedBones.has('tail1'))) continue;
    const from = b.from === 'root' ? { x: 0, y: 0 } : toSpine(joints[b.from]);
    let rot = 0, len = 0;
    if (b.to) {
      const to = toSpine(joints[b.to]);
      rot = Math.atan2(to.y - from.y, to.x - from.x) * DEG;
      len = Math.hypot(to.x - from.x, to.y - from.y);
      if (len < 2) warnings.push(`骨骼 ${b.name} 的兩個關節幾乎重疊`);
    } else if (b.length && b.parent) {
      rot = world.get(b.parent)!.rot;
      len = b.length;
    }
    world.set(b.name, { ...from, rot, len });
    const out: SpineJson = { name: b.name };
    if (b.parent) {
      const p = world.get(b.parent)!;
      const c = Math.cos(-p.rot / DEG), s = Math.sin(-p.rot / DEG);
      const dx = from.x - p.x, dy = from.y - p.y;
      out.parent = b.parent;
      const lx = c * dx - s * dy, ly = s * dx + c * dy;
      if (Math.abs(lx) > 1e-3) out.x = r3(lx);
      if (Math.abs(ly) > 1e-3) out.y = r3(ly);
      const lr = normDeg(rot - p.rot);
      if (Math.abs(lr) > 1e-3) out.rotation = r3(lr);
    }
    if (len) out.length = r3(len);
    if (b.inherit) out.inherit = b.inherit;
    if (b.color) out.color = b.color;
    bones.push(out);
  }

  // slots: one per part, every delivered drawing of that part as an attachment of it
  const center = toSpine({ x: canvas.width / 2, y: canvas.height / 2 });
  let parts = PARTS34.filter((p) => p.files.some((f) => have.has(f)));
  if (order?.length) {
    const rank = (p: Part34) => {
      const i = order.findIndex((n) => n === p.slot || p.files.includes(n));
      return i < 0 ? 1e9 + PARTS34.indexOf(p) : i;
    };
    parts = [...parts].sort((a, b) => rank(a) - rank(b));
  }
  const slots: SpineJson[] = [];
  const attachments: SpineJson = {};
  const layers: string[] = [];
  for (const p of parts) {
    const b = world.get(p.bone)!;
    const c = Math.cos(-b.rot / DEG), s = Math.sin(-b.rot / DEG);
    const dx = center.x - b.x, dy = center.y - b.y;
    const files = p.files.filter((f) => have.has(f));
    slots.push({ name: p.slot, bone: p.bone, attachment: files[0] });
    attachments[p.slot] = Object.fromEntries(files.map((f) => [f, { x: r3(c * dx - s * dy), y: r3(s * dx + c * dy), rotation: r3(normDeg(-b.rot)), width: canvas.width, height: canvas.height }]));
    layers.push(...files);
  }

  const json: SpineJson = {
    skeleton: { spine: '4.3.26', x: -canvas.width / 2, y: toSpine({ x: 0, y: canvas.height }).y, width: canvas.width, height: canvas.height, fps: 30, images: './images/', hash: name },
    bones,
    slots,
    constraints: IK34.map((k) => ({ name: k.name, type: 'ik', bones: k.bones, target: k.target, bendPositive: true })),
    skins: [{ name: 'default', attachments }],
    animations: {},
  };

  for (const [i, k] of IK34.entries()) {
    if (!k.soft) continue;
    const u = world.get(k.bones[0])!, l = world.get(k.bones[1])!, t = world.get(k.target)!;
    const reach = u.len + l.len, s = k.soft * reach;
    const d = Math.hypot(t.x - u.x, t.y - u.y);
    const td = softTarget(d, reach, s);
    json.constraints[i].softness = r3(s);
    const out = bones.find((b) => b.name === k.target)!;
    out.x = r3(u.x + ((t.x - u.x) * td) / d);
    out.y = r3(u.y + ((t.y - u.y) * td) / d);
  }
  for (const [i, k] of IK34.entries()) {
    const want = toSpine(joints[k.mid]);
    const err = (bend: boolean) => {
      json.constraints[i].bendPositive = bend;
      const sk = new Skeleton(parseSkeleton(json));
      sk.setupPose();
      sk.updateWorldTransform(Physics.none);
      const p = sk.findBone(k.bones[1])!.getAppliedPose();
      return Math.hypot(p.worldX - want.x, p.worldY - want.y);
    };
    const a = err(true), b = err(false);
    json.constraints[i].bendPositive = a <= b;
    if (Math.min(a, b) > 3) warnings.push(`${k.name} 的 IK 解出的位置和畫的關節差 ${Math.min(a, b).toFixed(1)} px，請檢查關節是否對齊`);
  }

  const roles: Record<string, string | string[]> = { ...ROLES34 };
  if (!usedBones.has('hat')) delete roles.hat;
  if (!usedBones.has('glasses')) delete roles.glasses;
  if (!usedBones.has('tail1')) delete roles.tail;
  const coat = ['coatL', 'coatR'].filter((n) => usedBones.has(n));
  if (coat.length) roles.coat = coat; else delete roles.coat;
  roles.hair = [...(usedBones.has('tail1') ? ['tail1', 'tail2', 'tail3'] : []), ...coat];
  // which drawings each swap slot has, for presets that key them
  for (const p of parts) if (p.files.length > 1) roles[`swap:${p.slot}`] = p.files.filter((f) => have.has(f));
  return { json, roles, layers, missing, warnings };
}
