/**
 * Spine 4.3 skeleton JSON helpers (browser-safe: no Node APIs; file I/O lives in io.ts), plus the setup-pose world transforms needed to convert
 * screen-space ("world") motion into each bone's local axes.
 */
import {
  SkeletonJson, Skeleton, Physics, AnimationState, AnimationStateData, type AttachmentLoader, type SkeletonData,
} from '@esotericsoftware/spine-core';
import type { Key } from './bake.ts';

// biome-ignore lint: Spine JSON is schemaless here
export type SpineJson = any;

/** Attachment loader that creates nothing: we only need bones, constraints and animations. */
const nullLoader = {
  newRegionAttachment: () => null,
  newMeshAttachment: () => null,
  newBoundingBoxAttachment: () => null,
  newPathAttachment: () => null,
  newPointAttachment: () => null,
  newClippingAttachment: () => null,
} as unknown as AttachmentLoader;


/** Throws with an actionable message if the file is not Spine 4.3 data. */
export function assertSpine43(json: SpineJson, file = 'skeleton'): void {
  const v: string | undefined = json?.skeleton?.spine;
  const legacy = ['ik', 'transform', 'path', 'physics'].filter((k) => Array.isArray(json?.[k]));
  if (legacy.length) {
    throw new Error(
      `${file}: 偵測到 4.2 以前的約束格式（${legacy.join(', ')}）。Spine 4.3 runtime 只讀 "constraints"，舊格式的 IK / Physics 會被忽略。請用 Spine 4.3 編輯器重新匯出 JSON。`,
    );
  }
  if (v && !/^4\.3\./.test(v)) {
    throw new Error(`${file}: 匯出版本是 ${v}，本工具使用 Spine 4.3 runtime，major.minor 必須一致。請用 Spine 4.3 編輯器重新匯出 JSON。`);
  }
  if (!Array.isArray(json?.bones) || json.bones.length === 0) throw new Error(`${file}: 找不到 bones，這不是 Spine 骨架 JSON。`);
}

/** Parse with the real Spine 4.3 runtime. Deform timelines are dropped (they need mesh attachments and never move bones). */
export function parseSkeleton(json: SpineJson): SkeletonData {
  const copy = JSON.parse(JSON.stringify(json));
  for (const anim of Object.values<SpineJson>(copy.animations ?? {})) delete anim.attachments;
  return new SkeletonJson(nullLoader).readSkeletonData(copy);
}

export interface BoneFrame {
  name: string;
  parent: string | null;
  /** Parent's world 2x2 matrix [a b; c d] in the setup pose (identity for the root). */
  parentMatrix: [number, number, number, number];
  worldX: number;
  worldY: number;
  worldRotation: number;
  length: number;
}

export function setupFrames(json: SpineJson): Map<string, BoneFrame> {
  const data = parseSkeleton(json);
  const sk = new Skeleton(data);
  sk.setupPose();
  sk.updateWorldTransform(Physics.none);
  const out = new Map<string, BoneFrame>();
  for (const bone of sk.bones) {
    const p = bone.getAppliedPose();
    const parent = bone.parent;
    const pp = parent ? parent.getAppliedPose() : null;
    out.set(bone.data.name, {
      name: bone.data.name,
      parent: parent ? parent.data.name : null,
      parentMatrix: pp ? [pp.a, pp.b, pp.c, pp.d] : [1, 0, 0, 1],
      worldX: p.worldX,
      worldY: p.worldY,
      worldRotation: p.getWorldRotationX(),
      length: bone.data.length,
    });
  }
  return out;
}

/** World-space offset (dx, dy) -> the bone's local translate offset, using the parent's setup matrix. */
export function worldToLocalDelta(m: [number, number, number, number], dx: number, dy: number): [number, number] {
  const [a, b, c, d] = m;
  const det = a * d - b * c;
  if (Math.abs(det) < 1e-12) return [dx, dy];
  return [(d * dx - b * dy) / det, (-c * dx + a * dy) / det];
}

/** +1 normally, -1 when the parent is mirrored (world rotation direction flips). */
export function rotationSign(m: [number, number, number, number]): number {
  return m[0] * m[3] - m[1] * m[2] < 0 ? -1 : 1;
}

export type TimelineName = 'rotate' | 'translatex' | 'translatey' | 'scalex' | 'scaley' | 'shearx' | 'sheary';

export interface BakedTrack {
  bone: string;
  timeline: TimelineName;
  keys: Key[];
}

export interface EventKey {
  name: string;
  time: number;
}

/** Write (or replace) one animation. Other animations, skins, slots and constraints are left untouched. */
export function writeAnimation(json: SpineJson, name: string, tracks: BakedTrack[], events: EventKey[] = []): void {
  json.animations ??= {};
  const anim: SpineJson = {};
  if (tracks.length) {
    anim.bones = {};
    for (const t of tracks) {
      anim.bones[t.bone] ??= {};
      if (anim.bones[t.bone][t.timeline]) throw new Error(`動畫 ${name}：骨骼 ${t.bone} 的 ${t.timeline} 被指定了兩次`);
      anim.bones[t.bone][t.timeline] = t.keys.map((k) => {
        const o: SpineJson = {};
        if (k.time !== 0) o.time = k.time;
        o.value = k.value;
        if (k.curve) o.curve = k.curve;
        return o;
      });
    }
  }
  if (events.length) {
    json.events ??= {};
    for (const e of events) json.events[e.name] ??= {};
    anim.events = [...events].sort((a, b) => a.time - b.time).map((e) => (e.time ? { time: e.time, name: e.name } : { name: e.name }));
  }
  json.animations[name] = anim;
}

export interface PhysicsSpec {
  name: string;
  bone: string;
  /** Which bone properties physics drives (1 = fully). */
  rotate?: number;
  x?: number;
  y?: number;
  inertia?: number;
  strength?: number;
  damping?: number;
  mass?: number;
  gravity?: number;
  wind?: number;
  mix?: number;
  fps?: number;
  /** Simulate, then bake the result into rotate keys instead of keeping a runtime constraint. Not written to JSON. */
  bake?: boolean;
}

/** Add or replace physics constraints (Spine 4.3 unified "constraints" list). */
export function upsertPhysics(json: SpineJson, specs: PhysicsSpec[]): void {
  json.constraints ??= [];
  const names = new Set(specs.map((s) => s.name));
  json.constraints = json.constraints.filter((c: SpineJson) => !names.has(c.name));
  for (const { bake: _bake, ...s } of specs) {
    json.constraints.push({ type: 'physics', fps: 60, ...s });
  }
}

/** Sample an animation with the real runtime. Returns per-bone world positions and rotations at `rate` Hz. */
export function sampleAnimation(
  data: SkeletonData,
  animation: string,
  opts: { rate?: number; seconds?: number; warmUp?: number; loop?: boolean } = {},
): { times: number[]; bones: Map<string, { x: number[]; y: number[]; rot: number[]; localRot: number[]; sx: number[]; sy: number[] }> } {
  const anim = data.findAnimation(animation);
  if (!anim) throw new Error(`找不到動畫 ${animation}`);
  const rate = opts.rate ?? 120;
  const seconds = opts.seconds ?? anim.duration;
  const warmUp = opts.warmUp ?? 0;
  const sk = new Skeleton(data);
  const state = new AnimationState(new AnimationStateData(data));
  state.setAnimation(0, animation, opts.loop ?? true); // one-shots must hold their last frame, not wrap to frame 0
  sk.setupPose();
  const dt = 1 / rate;
  const steps = Math.round(seconds * rate);
  const bones = new Map<string, { x: number[]; y: number[]; rot: number[]; localRot: number[]; sx: number[]; sy: number[] }>();
  for (const b of sk.bones) bones.set(b.data.name, { x: [], y: [], rot: [], localRot: [], sx: [], sy: [] });
  const times: number[] = [];
  const warmSteps = Math.round(warmUp * rate);
  for (let i = -warmSteps; i <= steps; i++) {
    if (i > -warmSteps) {
      state.update(dt);
      sk.update(dt);
    }
    state.apply(sk);
    sk.updateWorldTransform(Physics.update);
    if (i < 0) continue;
    times.push(i * dt);
    for (const b of sk.bones) {
      const p = b.getAppliedPose();
      const rec = bones.get(b.data.name)!;
      rec.x.push(p.worldX);
      rec.y.push(p.worldY);
      const worldRot = p.getWorldRotationX();
      rec.rot.push(worldRot);
      rec.sx.push(p.getWorldScaleX());
      rec.sy.push(p.getWorldScaleY());
      // Local rotation implied by the final world matrices. Physics writes the world transform directly and leaves
      // the stored local rotation untouched, so read the bone's world direction back in its parent's frame.
      const parent = b.parent?.getAppliedPose();
      if (parent) {
        const ux = Math.cos((worldRot * Math.PI) / 180);
        const uy = Math.sin((worldRot * Math.PI) / 180);
        const det = parent.a * parent.d - parent.b * parent.c;
        const vx = (parent.d * ux - parent.b * uy) / det;
        const vy = (-parent.c * ux + parent.a * uy) / det;
        rec.localRot.push((Math.atan2(vy, vx) * 180) / Math.PI);
      } else {
        rec.localRot.push(worldRot);
      }
    }
  }
  return { times, bones };
}
