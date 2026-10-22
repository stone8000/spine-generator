/**
 * Pose checks for a finished character, taken from the motion-research findings:
 *
 * - Usage: how far each animation actually turns each joint (after IK). With a delivery's `budget.json` (the angle
 *   each joint's art holds, measured by eye or by the research's `spinegen.budget`), joints turned past it are listed.
 *   The kit does not measure budgets itself: cut-out layers paint their joint ends past the joint, so a coverage
 *   test never opens and would report "no limit" for every joint.
 * - Flips: an elbow or knee bending to the other side of its drawn bend, or a layer drawn mirrored.
 * - First frame: how far frame 0 of each animation is from the drawn pose (an idle that does not start on the art
 *   jumps when it begins).
 */
import { Skeleton, AnimationState, AnimationStateData, Physics, type Bone } from '@esotericsoftware/spine-core';
import { parseSkeleton, type SpineJson } from './spineJson.ts';
import { SEAM_JOINTS } from './seams.ts';

/** Joints measured: every seam joint except the physics-driven hair. */
export const POSE_JOINTS = SEAM_JOINTS.filter((j) => j.bone !== 'hairF');

/** Two-bone chains whose bend side is checked (the rig's IK chains). */
export const BEND_CHAINS: { upper: string; lower: string; label: string }[] = [
  { upper: 'upperArmF', lower: 'foreArmF', label: '前手肘' },
  { upper: 'upperArmB', lower: 'foreArmB', label: '後手肘' },
  { upper: 'thighF', lower: 'shinF', label: '前膝' },
  { upper: 'thighB', lower: 'shinB', label: '後膝' },
];

/** Limit per direction in degrees; "+" is a counter-clockwise local turn (Spine, y up). null = no limit. */
export interface JointLimit {
  plus: number | null;
  minus: number | null;
}
export type AngleBudget = Record<string, JointLimit>;

const wrap = (a: number) => ((((a + 180) % 360) + 360) % 360) - 180;
const worldAngle = (b: Bone) => {
  const p = b.getAppliedPose();
  return (Math.atan2(p.c, p.a) * 180) / Math.PI;
};
/** A bone's turn against its parent, from world matrices (so it includes what IK did). */
const relAngle = (b: Bone) => (b.parent ? wrap(worldAngle(b) - worldAngle(b.parent)) : worldAngle(b));

/**
 * A delivery's budget.json. Joints by bone name (foreArmF) or label (前手肘); each direction a
 * number of degrees or { soft, hard } (the motion-research budget form, hard is used):
 *   { "joints": { "foreArmF": { "+": 40, "-": { "soft": 20, "hard": 30 } } } }
 */
export function parseBudget(file: unknown): AngleBudget {
  const obj = (file as { joints?: unknown })?.joints ?? file;
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('budget.json 要是 { "joints": { 骨名或關節名: { "+": 度數, "-": 度數 } } }');
  const out: AngleBudget = {};
  const deg = (v: unknown): number | null | undefined => {
    if (v === undefined) return undefined;
    if (v === null || typeof v === 'number') return v;
    const h = (v as { hard?: unknown; soft?: unknown }).hard ?? (v as { soft?: unknown }).soft;
    return typeof h === 'number' || h === null ? h : undefined;
  };
  for (const [key, v] of Object.entries(obj as Record<string, Record<string, unknown>>)) {
    const j = POSE_JOINTS.find((x) => x.bone === key || x.label === key);
    if (!j) throw new Error(`budget.json：不認得的關節「${key}」（用 ${POSE_JOINTS.map((x) => x.bone).join('、')}）`);
    if (!v || typeof v !== 'object') throw new Error(`budget.json：「${key}」要是 { "+": 度數, "-": 度數 }`);
    out[j.bone] = { plus: deg(v['+']) ?? null, minus: deg(v['-']) ?? null };
  }
  return out;
}

export interface FlipIssue {
  label: string;
  /** First time it happens, seconds. */
  time: number;
  /** Frames it lasts. */
  frames: number;
  kind: 'bend' | 'mirror';
}

export interface PoseUsage {
  animation: string;
  /** Per joint bone: the largest turn away from the drawn pose each way (degrees, + and − as positive numbers). */
  range: Record<string, { plus: number; minus: number }>;
  /** Frame 0: the joint furthest from the drawn pose and by how much. */
  firstFrame: { label: string; degrees: number };
  flips: FlipIssue[];
  /** Joints turned past their budget: label, direction, used and allowed degrees. */
  over: { label: string; bone: string; dir: '+' | '-'; used: number; allowed: number }[];
}

/** Plays every animation (IK on, physics off, like the seam check) and measures joints, frame 0 and flips. */
export function poseUsage(json: SpineJson, budget: AngleBudget = {}, fps = 30): PoseUsage[] {
  const data = parseSkeleton(json);
  const sk = new Skeleton(data);
  const joints = POSE_JOINTS.filter((j) => sk.findBone(j.bone)?.parent);
  const chains = BEND_CHAINS.filter((c) => sk.findBone(c.upper) && sk.findBone(c.lower));
  const bend = (c: (typeof chains)[number]) => wrap(worldAngle(sk.findBone(c.lower)!) - worldAngle(sk.findBone(c.upper)!));
  const det = (slotBone: string) => {
    const p = sk.findBone(slotBone)!.getAppliedPose();
    return p.a * p.d - p.b * p.c;
  };
  const slotBones = [...new Set<string>((json.slots ?? []).map((s: { bone: string }) => s.bone))].filter((b) => sk.findBone(b));

  sk.setupPose();
  sk.updateWorldTransform(Physics.none);
  const rest = new Map(joints.map((j) => [j.bone, relAngle(sk.findBone(j.bone)!)]));
  const restBend = new Map(chains.map((c) => [c.label, bend(c)]));
  const restDet = new Map(slotBones.map((b) => [b, Math.sign(det(b))]));
  const boneLabel = new Map(SEAM_JOINTS.map((j) => [j.bone, j.label]));

  const out: PoseUsage[] = [];
  for (const anim of data.animations) {
    const D = anim.duration;
    const n = Math.max(1, Math.round(D * fps));
    const state = new AnimationState(new AnimationStateData(data));
    const range: PoseUsage['range'] = Object.fromEntries(joints.map((j) => [j.bone, { plus: 0, minus: 0 }]));
    let firstFrame = { label: '—', degrees: 0 };
    const open = new Map<string, FlipIssue>();
    const flips: FlipIssue[] = [];
    for (let i = 0; i <= n; i++) {
      const t = (D * i) / n;
      sk.setupPose();
      state.setAnimation(0, anim.name, false);
      state.update(t);
      state.apply(sk);
      sk.updateWorldTransform(Physics.none);
      for (const j of joints) {
        const d = wrap(relAngle(sk.findBone(j.bone)!) - rest.get(j.bone)!);
        const r = range[j.bone];
        if (d > r.plus) r.plus = d;
        if (-d > r.minus) r.minus = -d;
        if (i === 0 && Math.abs(d) > firstFrame.degrees) firstFrame = { label: j.label, degrees: Math.abs(d) };
      }
      // a bend counts as flipped once it is past straight by more than 2°, so a straight arm wobbling is not one
      const now: [string, 'bend' | 'mirror', boolean][] = [
        ...chains.map((c): [string, 'bend', boolean] => {
          const b = bend(c), r = restBend.get(c.label)!;
          return [c.label, 'bend', Math.abs(r) > 2 && Math.sign(b) !== Math.sign(r) && Math.abs(b) > 2];
        }),
        ...slotBones.map((b): [string, 'mirror', boolean] => [`${boneLabel.get(b) ?? b}（${b}）`, 'mirror', Math.sign(det(b)) !== restDet.get(b)]),
      ];
      for (const [label, kind, on] of now) {
        const cur = open.get(label);
        if (on && cur) cur.frames++;
        else if (on) {
          const f: FlipIssue = { label, time: Math.round(t * 1000) / 1000, frames: 1, kind };
          open.set(label, f);
          flips.push(f);
        } else open.delete(label);
      }
    }
    const over: PoseUsage['over'] = [];
    for (const j of joints) {
      const lim = budget[j.bone];
      if (!lim) continue;
      const r = range[j.bone];
      if (lim.plus !== null && r.plus > lim.plus + 0.5) over.push({ label: j.label, bone: j.bone, dir: '+', used: r.plus, allowed: lim.plus });
      if (lim.minus !== null && r.minus > lim.minus + 0.5) over.push({ label: j.label, bone: j.bone, dir: '-', used: r.minus, allowed: lim.minus });
    }
    over.sort((a, b) => b.used - b.allowed - (a.used - a.allowed));
    out.push({ animation: anim.name, range, firstFrame, flips, over });
  }
  return out;
}
