/**
 * Recipe → animations. A recipe maps the rig's bones to roles and lists the animations to build, each from a preset
 * and/or custom tracks. Output is written into a copy of the skeleton JSON.
 */
import { bake, type Motion1D } from './bake.ts';
import { wave, poses, strike, spring, shake, padTo, type PosePoint, type StrikeParams } from './motions.ts';
import { findPreset, type PresetContext, type PresetOutput } from './presets.ts';
import { solveScreen, sampled, type Channel, type LocalTrack, type ScreenTrack } from './solve.ts';
import {
  assertSpine43, setupFrames, writeAnimation, upsertPhysics, parseSkeleton, sampleAnimation,
  type SpineJson, type BakedTrack, type TimelineName, type EventKey,
} from './spineJson.ts';

export interface CustomTrack {
  /** Bone name, or `role` to look it up in the recipe's role map. */
  bone?: string;
  role?: string;
  channel: Channel;
  /** 'local' = Spine bone offset (default). 'screen' = on-screen offset, solved for this rig (x, y, rotate only). */
  space?: 'local' | 'screen';
  type: 'wave' | 'poses' | 'strike' | 'spring' | 'shake';
  // wave
  amplitude?: number;
  period?: number;
  peakAt?: number;
  offset?: number;
  // poses
  points?: PosePoint[];
  // strike
  anticipation?: StrikeParams['anticipation'];
  action?: StrikeParams['action'];
  overshoot?: StrikeParams['overshoot'];
  hold?: number;
  recover?: StrikeParams['recover'];
  rest?: number;
  // spring
  from?: number;
  to?: number;
  frequency?: number;
  damping?: number;
  delay?: number;
  // spring / shake
  duration?: number;
  envelope?: 'decay' | 'grow' | 'flat';
  seed?: number;
}

export interface RecipeAnimation {
  name: string;
  preset?: string;
  params?: Record<string, unknown>;
  tracks?: CustomTrack[];
  events?: EventKey[];
  /** Custom-only animations: total seconds and whether it loops. */
  duration?: number;
  loop?: boolean;
  facing?: 1 | -1;
  /** Overall speed: 2 = twice as fast (half the length). Event times follow. */
  timeScale?: number;
  /** Overall amplitude: scales every motion around its rest value (0 for moves and turns, 1 for scale). */
  amplitude?: number;
}

export interface Recipe {
  fps?: number;
  facing?: 1 | -1;
  /** Character scale relative to the Iron Jaw measurement (1 ≈ character 580 units tall). */
  size?: number;
  /** Alternative to size: character height in skeleton units. */
  characterHeight?: number;
  /** Longest curve segment in seconds (default 0.2). The Spine runtime plays each segment as 10 straight lines. */
  maxSegment?: number;
  /** Keep screen-driven IK targets inside 95 % of their chain's reach so elbows/knees never snap (default true). */
  ikGuard?: boolean;
  roles: Record<string, string | string[]>;
  animations: RecipeAnimation[];
}

const TIMELINE: Record<Channel, TimelineName> = {
  rotate: 'rotate', x: 'translatex', y: 'translatey', scaleX: 'scalex', scaleY: 'scaley', shearX: 'shearx', shearY: 'sheary',
};

const TOLERANCE: Record<Channel, number> = { rotate: 0.05, x: 0.1, y: 0.1, scaleX: 0.0005, scaleY: 0.0005, shearX: 0.05, shearY: 0.05 };

function isScale(ch: Channel): boolean {
  return ch === 'scaleX' || ch === 'scaleY';
}

/** Repeat a loop motion to fill `duration` (must be a whole number of its cycles). */
function fitLoop(m: Motion1D, duration: number, label: string): Motion1D {
  if (Math.abs(m.duration - duration) < 1e-9) return m;
  const n = duration / m.duration;
  if (!m.loop || Math.abs(n - Math.round(n)) > 1e-6) {
    throw new Error(`${label}：長度 ${m.duration}s 無法填滿循環 ${duration}s（必須是整數倍）`);
  }
  return { duration, loop: true, value: (t) => m.value(t % m.duration), breaks: (m.breaks ?? []).flatMap((b) => Array.from({ length: Math.round(n) }, (_, i) => b + i * m.duration)) };
}

function customMotion(tr: CustomTrack, fps: number): Motion1D {
  switch (tr.type) {
    case 'wave':
      if (tr.amplitude === undefined || !tr.period) throw new Error('wave 需要 amplitude 與 period');
      return wave({ amplitude: tr.amplitude, period: tr.period, peakAt: tr.peakAt, offset: tr.offset });
    case 'poses':
      if (!tr.points?.length) throw new Error('poses 需要 points');
      return poses(tr.points, { loop: false });
    case 'strike':
      if (!tr.anticipation || !tr.action) throw new Error('strike 需要 anticipation 與 action');
      return strike({ rest: tr.rest, anticipation: tr.anticipation, action: tr.action, overshoot: tr.overshoot, hold: tr.hold, recover: tr.recover });
    case 'spring':
      if (tr.from === undefined || tr.to === undefined || !tr.duration) throw new Error('spring 需要 from、to、duration');
      return spring({ from: tr.from, to: tr.to, frequency: tr.frequency, damping: tr.damping, delay: tr.delay, duration: tr.duration });
    case 'shake':
      if (tr.amplitude === undefined || !tr.frequency || !tr.duration) throw new Error('shake 需要 amplitude、frequency、duration');
      return shake({ amplitude: tr.amplitude, frequency: tr.frequency, duration: tr.duration, envelope: tr.envelope, seed: tr.seed });
  }
  void fps;
  throw new Error(`未知的 type：${(tr as CustomTrack).type}`);
}

function retime(m: Motion1D, k: number): Motion1D {
  if (k === 1) return m;
  return { duration: m.duration / k, loop: m.loop, breaks: (m.breaks ?? []).map((b) => b / k), value: (t) => m.value(Math.min(m.duration, t * k)) };
}

/** Apply the animation-level speed and amplitude to everything a preset produced. */
function retimeAndScale(out: PresetOutput, timeScale: number, amplitude: number): PresetOutput {
  if (!(timeScale > 0)) throw new Error('timeScale 必須大於 0');
  if (timeScale === 1 && amplitude === 1) return out;
  const amp = (m: Motion1D, ch: Channel): Motion1D =>
    amplitude === 1 ? m : { ...m, value: isScale(ch) ? (t) => 1 + (m.value(t) - 1) * amplitude : (t) => m.value(t) * amplitude };
  return {
    ...out,
    duration: out.duration / timeScale,
    screen: out.screen.map((s) => ({ ...s, motion: amp(retime(s.motion, timeScale), s.channel) })),
    local: out.local.map((l) => ({ ...l, motion: amp(retime(l.motion, timeScale), l.channel) })),
    events: out.events.map((e) => ({ ...e, time: Math.round((e.time / timeScale) * 1000) / 1000 })),
  };
}

export interface CompileResult {
  json: SpineJson;
  summary: { name: string; duration: number; loop: boolean; timelines: number; keys: number; events: string[]; physics: number; bakedPhysics: number }[];
}

export function compileRecipe(skeleton: SpineJson, recipe: Recipe): CompileResult {
  assertSpine43(skeleton);
  const json = JSON.parse(JSON.stringify(skeleton));
  const frames = setupFrames(json);
  const fps = recipe.fps ?? 30;
  const size = recipe.size ?? (recipe.characterHeight ? recipe.characterHeight / 580 : 1);
  const boneExists = (name: string) => {
    if (!frames.has(name)) throw new Error(`骨架裡沒有骨骼 "${name}"`);
    return name;
  };
  const roleList = (name: string): string[] => {
    const v = recipe.roles?.[name];
    if (v === undefined) return [];
    return (Array.isArray(v) ? v : [v]).map(boneExists);
  };
  const summary: CompileResult['summary'] = [];

  for (const anim of recipe.animations) {
    const facing = anim.facing ?? recipe.facing ?? 1;
    const params = anim.params ?? {};
    const ctx: PresetContext = {
      facing,
      size,
      fps,
      param: <T,>(n: string, fallback: T): T => (params[n] !== undefined ? (params[n] as T) : fallback),
      role: (n) => {
        const l = roleList(n);
        if (!l.length) throw new Error(`動畫 ${anim.name}：預設需要角色 "${n}"，請在 recipe 的 roles 指定骨骼`);
        return l[0];
      },
      roleOpt: (n) => roleList(n)[0],
      roleList,
      worldRotation: (b) => frames.get(boneExists(b))!.worldRotation,
      ikChain: (t) => {
        const k = (json.constraints ?? []).find((c: SpineJson) => c.type === 'ik' && c.target === t && c.bones?.length === 2);
        if (!k) return undefined;
        const [a, b] = k.bones.map((n: string) => frames.get(n)!);
        const tf = frames.get(t)!;
        return { root: { x: a.worldX, y: a.worldY }, mid: { x: b.worldX, y: b.worldY }, target: { x: tf.worldX, y: tf.worldY }, lengths: [a.length, b.length], reach: a.length + b.length };
      },
    };

    let out: PresetOutput = { duration: anim.duration ?? 0, loop: anim.loop ?? false, screen: [], local: [], events: [], physics: [] };
    if (anim.preset) out = findPreset(anim.preset).build(ctx);
    for (const tr of anim.tracks ?? []) {
      const bone = tr.bone ? boneExists(tr.bone) : tr.role ? ctx.role(tr.role) : '';
      if (!bone) throw new Error(`動畫 ${anim.name}：自訂軌需要 bone 或 role`);
      const motion = customMotion(tr, fps);
      if (!anim.preset && !anim.duration) out.duration = Math.max(out.duration, motion.duration);
      if (!anim.preset && anim.loop === undefined && motion.loop) out.loop = true;
      if (tr.space === 'screen') {
        if (!['x', 'y', 'rotate'].includes(tr.channel)) throw new Error('screen 空間只支援 x、y、rotate');
        out.screen.push({ bone, channel: tr.channel as ScreenTrack['channel'], motion });
      } else {
        out.local.push({ bone, channel: tr.channel, motion });
      }
    }
    out.events.push(...(anim.events ?? []));
    if (!out.duration) throw new Error(`動畫 ${anim.name}：沒有任何動作`);
    out = retimeAndScale(out, anim.timeScale ?? 1, anim.amplitude ?? 1);

    const D = out.duration;
    const fit = (m: Motion1D, label: string) => (out.loop ? fitLoop(m, D, label) : padTo(m, D));
    const screen = out.screen.map((s) => ({ ...s, motion: fit(s.motion, `${anim.name}/${s.bone}/${s.channel}`) }));
    const local = out.local.map((l) => ({ ...l, motion: fit(l.motion, `${anim.name}/${l.bone}/${l.channel}`) }));
    const solved = solveScreen(json, screen, local, D, out.loop, 240, recipe.ikGuard ?? true);

    // merge everything that lands on the same bone channel; a solved channel already includes any local track the
    // solver applied on that bone, so the local one must not be added again
    const solvedKeys = new Set(solved.map((s) => `${s.bone}|${s.channel}`));
    const merged = new Map<string, LocalTrack[]>();
    for (const tr of [...local.filter((l) => !solvedKeys.has(`${l.bone}|${l.channel}`)), ...solved]) {
      const k = `${tr.bone}|${tr.channel}`;
      merged.set(k, [...(merged.get(k) ?? []), tr]);
    }
    const baked: BakedTrack[] = [];
    let keyCount = 0;
    for (const [k, list] of merged) {
      const [bone, channel] = k.split('|') as [string, Channel];
      const motion: Motion1D = {
        duration: D,
        loop: out.loop,
        breaks: list.flatMap((l) => l.motion.breaks ?? []),
        value: isScale(channel)
          ? (t) => 1 + list.reduce((s, l) => s + (l.motion.value(t) - 1), 0)
          : (t) => list.reduce((s, l) => s + l.motion.value(t), 0),
      };
      const keys = bake(motion, { tolerance: TOLERANCE[channel] * (isScale(channel) || channel === 'rotate' ? 1 : Math.max(size, 0.25)), fps, maxSegment: recipe.maxSegment });
      keyCount += keys.length;
      baked.push({ bone, timeline: TIMELINE[channel], keys });
    }
    writeAnimation(json, anim.name, baked, out.events);

    // physics to bake: simulate it with the real runtime on a scratch copy, then store the result as rotate keys
    const toBake = out.physics.filter((p) => p.bake);
    if (toBake.length) {
      const scratch = JSON.parse(JSON.stringify(json));
      upsertPhysics(scratch, toBake);
      const data = parseSkeleton(scratch);
      const warmUp = out.loop ? D * Math.ceil(6 / D) : 0; // whole loops, so the recorded window starts at t = 0
      const s = sampleAnimation(data, anim.name, { rate: 240, seconds: D, warmUp, loop: out.loop });
      for (const p of toBake) {
        const setupRot = data.findBone(p.bone)!.setupPose.rotation;
        const values = s.bones.get(p.bone)!.localRot.map((r) => ((r - setupRot + 540) % 360) - 180);
        const keys = bake(sampled(values, D, out.loop), { tolerance: 0.1, fps, maxSegment: recipe.maxSegment });
        keyCount += keys.length;
        const i = baked.findIndex((b) => b.bone === p.bone && b.timeline === 'rotate');
        if (i >= 0) {
          keyCount -= baked[i].keys.length;
          baked[i] = { bone: p.bone, timeline: 'rotate', keys };
        } else {
          baked.push({ bone: p.bone, timeline: 'rotate', keys });
        }
      }
      writeAnimation(json, anim.name, baked, out.events);
    }
    const keep = out.physics.filter((p) => !p.bake);
    if (keep.length) upsertPhysics(json, keep);
    summary.push({ name: anim.name, duration: D, loop: out.loop, timelines: baked.length, keys: keyCount, events: out.events.map((e) => `${e.name}@${e.time}s`), physics: keep.length, bakedPhysics: toBake.length });
  }
  return { json, summary };
}
