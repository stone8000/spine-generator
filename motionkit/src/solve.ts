/**
 * Screen-space retargeting.
 *
 * Presets describe how a body part should move ON SCREEN (e.g. "the head bobs 10 units down at frame 0").
 * Rigs differ: the head may sit under neck → chest → hip, a fist IK target may hang off the root, bones may point up
 * (rotated 90°) or be mirrored. This solver runs the real Spine runtime, applies every ancestor's motion first, and
 * computes the local offset each bone needs so its world position/rotation matches the screen target exactly.
 */
import { Skeleton, Physics, type Bone } from '@esotericsoftware/spine-core';
import type { Motion1D } from './bake.ts';
import { parseSkeleton, type SpineJson } from './spineJson.ts';

export type Channel = 'rotate' | 'x' | 'y' | 'scaleX' | 'scaleY' | 'shearX' | 'shearY';

/** Offset in the bone's local space, exactly like a Spine bone timeline (scale channels are multipliers around 1). */
export interface LocalTrack {
  bone: string;
  channel: Channel;
  motion: Motion1D;
}

/** Desired on-screen offset from the bone's setup world position (x, y) or world rotation (rotate, degrees CCW). */
export interface ScreenTrack {
  bone: string;
  channel: 'x' | 'y' | 'rotate';
  motion: Motion1D;
}

function applyLocal(bone: Bone, channel: Channel, v: number): void {
  const p = bone.getPose();
  switch (channel) {
    case 'rotate': p.rotation += v; break;
    case 'x': p.x += v; break;
    case 'y': p.y += v; break;
    case 'scaleX': p.scaleX *= v; break;
    case 'scaleY': p.scaleY *= v; break;
    case 'shearX': p.shearX += v; break;
    case 'shearY': p.shearY += v; break;
  }
}

function depth(bone: Bone): number {
  let d = 0;
  for (let b = bone.parent; b; b = b.parent) d++;
  return d;
}

/** Periodic or clamped Catmull-Rom interpolation over evenly spaced samples. */
export function sampled(values: number[], duration: number, loop: boolean): Motion1D {
  const n = values.length - 1; // samples at i * duration / n, i = 0..n
  const at = (i: number) => {
    if (loop) return values[((i % n) + n) % n];
    return values[Math.min(n, Math.max(0, i))];
  };
  return {
    duration,
    loop,
    value: (t) => {
      const x = (Math.min(Math.max(t, 0), duration) / duration) * n;
      const i = Math.min(n - 1, Math.floor(x));
      const s = x - i;
      const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
      return 0.5 * (2 * p1 + (-p0 + p2) * s + (2 * p0 - 5 * p1 + 4 * p2 - p3) * s * s + (-p0 + 3 * p1 - 3 * p2 + p3) * s * s * s);
    },
  };
}

/**
 * Turn screen tracks into local tracks for this rig. Local tracks passed in are applied as given (they also move
 * descendants). Returns the solved local tracks for every screen-driven bone/channel.
 */
export function solveScreen(
  json: SpineJson,
  screen: ScreenTrack[],
  local: LocalTrack[],
  duration: number,
  loop: boolean,
  rate = 240,
  ikGuard = true,
): LocalTrack[] {
  if (!screen.length) return [];
  const data = parseSkeleton({ ...json, animations: {} });
  const sk = new Skeleton(data);
  sk.setupPose();
  sk.updateWorldTransform(Physics.none);

  const find = (name: string) => {
    const b = sk.findBone(name);
    if (!b) throw new Error(`找不到骨骼 ${name}`);
    return b;
  };
  const setup = new Map<string, { x: number; y: number; rot: number }>();
  for (const b of sk.bones) {
    const p = b.getAppliedPose();
    setup.set(b.data.name, { x: p.worldX, y: p.worldY, rot: p.getWorldRotationX() });
  }

  const byBone = new Map<string, { x?: Motion1D; y?: Motion1D; rotate?: Motion1D }>();
  for (const s of screen) {
    const e = byBone.get(s.bone) ?? {};
    if (e[s.channel]) throw new Error(`骨骼 ${s.bone} 的畫面 ${s.channel} 被指定了兩次`);
    e[s.channel] = s.motion;
    byBone.set(s.bone, e);
  }
  const order = [...byBone.keys()].map(find).sort((a, b) => depth(a) - depth(b));
  for (const b of order) {
    const lt = local.find((l) => l.bone === b.data.name && ['x', 'y', 'rotate'].includes(l.channel) && byBone.get(b.data.name)![l.channel as 'x']);
    if (lt) throw new Error(`骨骼 ${b.data.name} 的 ${lt.channel} 同時有畫面目標與局部動作，請擇一`);
  }

  // IK reach guard: a 2-bone IK chain snaps (elbow pops straight or flips) when its target leaves the chain's reach.
  // For every screen-driven IK target, softly keep the target inside 95 % of the chain length.
  const ikChains = new Map<string, { root: Bone; reach: number }>();
  for (const c of json.constraints ?? []) {
    if (c.type !== 'ik' || !Array.isArray(c.bones) || c.bones.length !== 2) continue;
    const chain = c.bones.map(find);
    const reach = chain.reduce((sum: number, b: Bone) => sum + b.data.length * b.getAppliedPose().getWorldScaleX(), 0);
    if (reach > 0) ikChains.set(c.target, { root: chain[0], reach });
  }
  const guardReach = (bone: string, x: number, y: number): [number, number] => {
    const ik = ikGuard ? ikChains.get(bone) : undefined;
    if (!ik) return [x, y];
    const r = ik.root.getAppliedPose();
    const dx = x - r.worldX;
    const dy = y - r.worldY;
    const d = Math.hypot(dx, dy);
    const knee = 0.85 * ik.reach;
    const room = 0.1 * ik.reach;
    if (d <= knee) return [x, y];
    const k = (knee + room * Math.tanh((d - knee) / room)) / d; // smooth: linear below 85 %, never past 95 %
    return [r.worldX + dx * k, r.worldY + dy * k];
  };

  const n = Math.max(8, Math.round(duration * rate));
  const out = new Map<string, number[]>(); // `${bone}|${channel}` -> samples
  const push = (k: string, v: number) => {
    const arr = out.get(k) ?? [];
    arr.push(v);
    out.set(k, arr);
  };

  for (let i = 0; i <= n; i++) {
    const t = (duration * i) / n;
    sk.setupPose();
    for (const l of local) applyLocal(find(l.bone), l.channel, l.motion.value(t));
    for (const bone of order) {
      sk.updateWorldTransform(Physics.none);
      const want = byBone.get(bone.data.name)!;
      const s0 = setup.get(bone.data.name)!;
      const pose = bone.getPose();
      const applied = bone.getAppliedPose();
      // translation: target world position, keeping the inherited position on any axis without a target
      if (want.x || want.y) {
        const [tx, ty] = guardReach(
          bone.data.name,
          want.x ? s0.x + want.x.value(t) : applied.worldX,
          want.y ? s0.y + want.y.value(t) : applied.worldY,
        );
        const parent = bone.parent;
        let lx = tx;
        let ly = ty;
        if (parent) {
          const pp = parent.getAppliedPose();
          const det = pp.a * pp.d - pp.b * pp.c;
          const dx = tx - pp.worldX;
          const dy = ty - pp.worldY;
          lx = (pp.d * dx - pp.b * dy) / det;
          ly = (-pp.c * dx + pp.a * dy) / det;
        }
        const base = bone.data.setupPose;
        if (want.x || want.y) {
          push(`${bone.data.name}|x`, lx - base.x);
          push(`${bone.data.name}|y`, ly - base.y);
        }
        pose.x = lx;
        pose.y = ly;
      }
      if (want.rotate) {
        sk.updateWorldTransform(Physics.none);
        const inherited = bone.getAppliedPose().getWorldRotationX();
        const target = s0.rot + want.rotate.value(t);
        let delta = target - inherited;
        delta = ((delta + 540) % 360) - 180;
        // a mirrored chain turns local rotation the other way on screen
        const pp = bone.parent?.getAppliedPose();
        const sign = pp && pp.a * pp.d - pp.b * pp.c < 0 ? -1 : 1;
        pose.rotation += sign * delta;
        push(`${bone.data.name}|rotate`, pose.rotation - bone.data.setupPose.rotation);
      }
    }
  }

  const tracks: LocalTrack[] = [];
  for (const [k, values] of out) {
    const [bone, channel] = k.split('|') as [string, Channel];
    // drop channels the solver had to emit but which never move (e.g. y when only x was asked and nothing inherits)
    const range = Math.max(...values) - Math.min(...values);
    if (range < 1e-6 && Math.abs(values[0]) < 1e-6) continue;
    tracks.push({ bone, channel, motion: sampled(values, duration, loop) });
  }
  return tracks;
}
