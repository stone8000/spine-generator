/**
 * Motion presets. Character presets are written in screen terms (forward / up / lean) so the same preset works on any
 * rig and either facing; solve.ts converts them into each bone's local keys.
 *
 * Conventions: Spine axes (x right, y up, rotation counter-clockwise). `facing` = +1 when the character faces right.
 * "lean forward" = the top of the bone tips toward the facing direction.
 */
import type { Motion1D } from './bake.ts';
import { wave, poses, strike, shake, delayed, padTo, scaled } from './motions.ts';
import { EASES, springDecay } from './ease.ts';
import type { LocalTrack, ScreenTrack } from './solve.ts';
import type { EventKey, PhysicsSpec } from './spineJson.ts';

export interface PresetContext {
  facing: 1 | -1;
  /** Character scale relative to the Iron Jaw measurement (1 = character about 580 units tall). */
  size: number;
  fps: number;
  param<T>(name: string, fallback: T): T;
  role(name: string): string;
  roleOpt(name: string): string | undefined;
  roleList(name: string): string[];
  /** Setup-pose world rotation of a bone, degrees. */
  worldRotation(bone: string): number;
  /** When `target` drives a 2-bone IK chain: the chain's root and the target in the setup pose (world), and its reach. */
  ikChain(target: string): { root: { x: number; y: number }; mid: { x: number; y: number }; target: { x: number; y: number }; lengths: [number, number]; reach: number } | undefined;
}

export interface PresetOutput {
  duration: number;
  loop: boolean;
  screen: ScreenTrack[];
  local: LocalTrack[];
  events: EventKey[];
  physics: PhysicsSpec[];
}

export interface PresetDef {
  id: string;
  title: string;
  kind: 'loop' | 'once';
  summary: string;
  roles: { name: string; required: boolean; list?: boolean; desc: string }[];
  params: { name: string; default: unknown; desc: string }[];
  build(c: PresetContext): PresetOutput;
}

const empty = (duration: number, loop: boolean): PresetOutput => ({ duration, loop, screen: [], local: [], events: [], physics: [] });

/** Helpers for loop presets authored on a 60-step cycle (the Iron Jaw measurement grid). */
function cycle(c: PresetContext, T: number, k: number) {
  const at = (step: number) => (((step % 60) + 60) % 60) / 60 * T;
  const s = c.size * k;
  return {
    /** forward-most at `step` (screen x). */
    fwd: (A: number, step: number): Motion1D => scaled(wave({ amplitude: A * s, period: T, peakAt: at(step) }), c.facing),
    /** lowest at `step` (screen y). */
    down: (A: number, step: number): Motion1D => wave({ amplitude: A * s, period: T, peakAt: at(step + 30) }),
    /** most forward lean at `step` (screen rotation). */
    lean: (A: number, step: number): Motion1D => scaled(wave({ amplitude: A * k, period: T, peakAt: at(step) }), -c.facing),
    at,
  };
}

/** True when the bone's local X axis points mostly up/down on screen (common for spines and torsos). */
function pointsVertically(c: PresetContext, bone: string): boolean {
  const r = (c.worldRotation(bone) * Math.PI) / 180;
  return Math.abs(Math.sin(r)) > Math.abs(Math.cos(r));
}

function chainPhysics(chain: string[], prefix: string, o: { inertia: number; strength: number; damping: number }): PhysicsSpec[] {
  return chain.map((bone) => ({ name: `${prefix}-${bone}`, bone, rotate: 1, inertia: o.inertia, strength: o.strength, damping: o.damping, mass: 1, mix: 1 }));
}

/**
 * Hair / ribbon chains follow the body through Spine physics. 'keys' simulates it and bakes the follow-through into
 * editable rotate keys; 'physics' keeps the physics constraints for the runtime.
 */
function hairFollow(c: PresetContext, out: PresetOutput) {
  const hair = c.roleList('hair');
  const mode = c.param<string>('hair', 'keys');
  if (hair.length && mode !== 'none') {
    out.physics.push(...chainPhysics(hair, 'hair', { inertia: 0.6, strength: 140, damping: 0.85 }).map((p) => ({ ...p, bake: mode === 'keys' })));
  }
}
const HAIR_ROLE = { name: 'hair', required: false, list: true, desc: '頭髮／飄帶骨骼鏈，由根到尾（會跟著身體甩動）' };

/** Overshoot that starts at 0 with velocity in `dir`, first peak = amount (used for hits and landings). */
function overshoot(amount: number, frequency: number, damping: number): (s: number) => number {
  const w = 2 * Math.PI * frequency;
  const z = damping;
  const wd = w * Math.sqrt(1 - z * z);
  const tPeak = Math.atan2(wd, z * w) / wd;
  const k = amount / (Math.exp(-z * w * tPeak) * Math.sin(wd * tPeak));
  return (s) => k * Math.exp(-z * w * s) * Math.sin(wd * s);
}

function fn(duration: number, value: (t: number) => number, breaks: number[] = [], loop = false): Motion1D {
  return { duration, loop, value, breaks };
}

const HAIR_PARAM = { name: 'hair', default: 'keys', desc: "頭髮鏈：'keys'（模擬 Spine 物理後烘成可編輯的關鍵影格）、'physics'（保留 runtime 物理約束）、'none'" };

export const PRESETS: PresetDef[] = [
  {
    id: 'fighter-idle-bounce',
    title: '格鬥待機・彈跳型（Iron Jaw 左角色實測）',
    kind: 'loop',
    summary: '原地上下彈跳、雙拳交替。數值取自 Iron Jaw 左角色的畫面量測（週期 1.99 秒、正弦吻合度 R² 0.998）。',
    roles: [
      { name: 'hip', required: true, desc: '上身根骨（髖）' },
      { name: 'head', required: false, desc: '頭' },
      { name: 'shoulderBack', required: false, desc: '後側肩膀' },
      { name: 'fistFront', required: false, desc: '前手拳（建議用 IK 目標骨）' },
      { name: 'fistBack', required: false, desc: '後手拳（建議用 IK 目標骨）' },
      { name: 'hair', required: false, list: true, desc: '頭髮骨骼鏈，由根到尾' },
    ],
    params: [
      { name: 'period', default: 2, desc: '循環秒數（實測 1.99）' },
      { name: 'intensity', default: 1, desc: '動作幅度倍率' },
      HAIR_PARAM,
    ],
    build(c) {
      const T = c.param('period', 2);
      const k = c.param('intensity', 1);
      const { fwd, down, lean, at } = cycle(c, T, k);
      const out = empty(T, true);
      const hip = c.role('hip');
      out.screen.push(
        { bone: hip, channel: 'x', motion: fwd(4.2, 3) },
        { bone: hip, channel: 'y', motion: down(9.0, 0) },
        { bone: hip, channel: 'rotate', motion: lean(1.1, 3) },
      );
      const head = c.roleOpt('head');
      if (head) {
        out.screen.push(
          { bone: head, channel: 'x', motion: fwd(8.8, 2) },
          { bone: head, channel: 'y', motion: down(10.5, 0) },
          { bone: head, channel: 'rotate', motion: lean(4.3, 59) },
        );
      }
      const sb = c.roleOpt('shoulderBack');
      if (sb) out.screen.push({ bone: sb, channel: 'rotate', motion: lean(2.8, 36) });
      const ff = c.roleOpt('fistFront');
      if (ff) {
        out.screen.push(
          { bone: ff, channel: 'x', motion: fwd(15.0, 40) },
          { bone: ff, channel: 'y', motion: down(15.8, 4) },
        );
      }
      const fb = c.roleOpt('fistBack');
      if (fb) {
        out.screen.push(
          { bone: fb, channel: 'x', motion: fwd(12.7, 4) },
          { bone: fb, channel: 'y', motion: down(15.4, 39) },
        );
      }
      hairFollow(c, out);
      return out;
    },
  },
  {
    id: 'fighter-idle-weave',
    title: '格鬥待機・閃身型（Iron Jaw 右角色實測）',
    kind: 'loop',
    summary: '左右閃身，往前時壓低、往後時抬起。數值取自 Iron Jaw 右角色的畫面量測。',
    roles: [
      { name: 'hip', required: true, desc: '上身根骨（髖）' },
      { name: 'head', required: false, desc: '頭' },
      { name: 'fistFront', required: false, desc: '前手拳（IK 目標骨）：跟著身體移動' },
      { name: 'fistBack', required: false, desc: '後手拳（IK 目標骨）：跟著身體移動' },
      { name: 'hair', required: false, list: true, desc: '頭髮骨骼鏈' },
    ],
    params: [
      { name: 'period', default: 2, desc: '循環秒數（實測 1.99）' },
      { name: 'intensity', default: 1, desc: '動作幅度倍率' },
      HAIR_PARAM,
    ],
    build(c) {
      const T = c.param('period', 2);
      const k = c.param('intensity', 1);
      const { fwd, down, lean, at } = cycle(c, T, k);
      const out = empty(T, true);
      const hip = c.role('hip');
      out.screen.push(
        { bone: hip, channel: 'x', motion: fwd(10.7, 30) },
        { bone: hip, channel: 'y', motion: down(1.8, 15) },
      );
      const head = c.roleOpt('head');
      if (head) {
        out.screen.push(
          { bone: head, channel: 'x', motion: fwd(13.0, 31) },
          { bone: head, channel: 'y', motion: down(8.2, 29) },
          { bone: head, channel: 'rotate', motion: lean(4.3, 34) },
        );
      }
      for (const r of ['fistFront', 'fistBack']) {
        const f = c.roleOpt(r);
        if (f) {
          out.screen.push(
            { bone: f, channel: 'x', motion: fwd(9.7, 30) },
            { bone: f, channel: 'y', motion: down(1.7, 13) },
          );
        }
      }
      hairFollow(c, out);
      return out;
    },
  },
  {
    id: 'breathe',
    title: '呼吸待機',
    kind: 'loop',
    summary: '安靜角色的呼吸：胸口擴張、肩膀晚 3 幀抬起、頭晚 5 幀微仰。適合非戰鬥角色、NPC、吉祥物。',
    roles: [
      { name: 'chest', required: true, desc: '胸口骨（會縮放）' },
      { name: 'shoulders', required: false, list: true, desc: '左右肩' },
      { name: 'head', required: false, desc: '頭' },
    ],
    params: [
      { name: 'period', default: 3.2, desc: '一次呼吸的秒數（2.4–4 秒自然）' },
      { name: 'intensity', default: 1, desc: '幅度倍率' },
    ],
    build(c) {
      const T = c.param('period', 3.2);
      const k = c.param('intensity', 1);
      const out = empty(T, true);
      const inhale = 0.4 * T;
      const chest = c.role('chest');
      const vertical = pointsVertically(c, chest);
      const lengthen = wave({ amplitude: 0.025 * k, period: T, peakAt: inhale, offset: 1 });
      const widen = wave({ amplitude: 0.01 * k, period: T, peakAt: inhale, offset: 1 });
      out.local.push(
        { bone: chest, channel: vertical ? 'scaleX' : 'scaleY', motion: lengthen },
        { bone: chest, channel: vertical ? 'scaleY' : 'scaleX', motion: widen },
      );
      for (const sh of c.roleList('shoulders')) {
        out.screen.push({ bone: sh, channel: 'y', motion: wave({ amplitude: 3 * c.size * k, period: T, peakAt: inhale + 3 / 30 }) });
      }
      const head = c.roleOpt('head');
      if (head) out.screen.push({ bone: head, channel: 'rotate', motion: scaled(wave({ amplitude: 0.8 * k, period: T, peakAt: inhale + 5 / 30 }), c.facing) });
      return out;
    },
  },
  {
    id: 'chain-sway',
    title: '甩尾跟隨（頭髮、尾巴、披風、緞帶）',
    kind: 'loop',
    summary: '沿骨骼鏈傳遞的波：每一節比上一節晚幾幀、幅度略大，形成自然的甩動。也可改用 Physics。',
    roles: [{ name: 'chain', required: true, list: true, desc: '骨骼鏈，由根到尾' }],
    params: [
      { name: 'amplitude', default: 6, desc: '第一節擺動角度（度）' },
      { name: 'period', default: 2, desc: '循環秒數' },
      { name: 'lagFrames', default: 3, desc: '每一節延遲幾幀（@30）' },
      { name: 'gain', default: 1.2, desc: '每一節幅度倍率' },
      { name: 'mode', default: 'keys', desc: "'keys'（數學波）、'bake'（模擬物理後烘成關鍵影格）、'physics'（保留 runtime 物理）" },
    ],
    build(c) {
      const T = c.param('period', 2);
      const A = c.param('amplitude', 6);
      const out = empty(T, true);
      const chain = c.roleList('chain');
      if (!chain.length) throw new Error('chain-sway 需要 chain 角色（骨骼鏈）');
      const mode = c.param<string>('mode', 'keys');
      if (mode === 'physics' || mode === 'bake') {
        // the root link is keyed; every link after it is driven by physics and trails naturally
        out.local.push({ bone: chain[0], channel: 'rotate', motion: wave({ amplitude: A, period: T }) });
        out.physics.push(...chainPhysics(chain.slice(1), 'sway', { inertia: 0.6, strength: 120, damping: 0.85 }).map((p) => ({ ...p, bake: mode === 'bake' })));
      } else {
        const lag = c.param('lagFrames', 3) / 30;
        const gain = c.param('gain', 1.2);
        chain.forEach((bone, i) => {
          out.local.push({ bone, channel: 'rotate', motion: wave({ amplitude: A * Math.pow(gain, i), period: T, peakAt: i * lag }) });
        });
      }
      return out;
    },
  },
  {
    id: 'punch',
    title: '出拳（預備 → 出手 → 過衝 → 停留 → 收回）',
    kind: 'once',
    summary: '時序取自 Iron Jaw 右角色出拳：蓄力 0.27 s、出手 0.07 s、停留 0.27 s、收回 0.25 s；身體與頭依序跟隨。蓄力轉出手時發出 swing 事件（揮拳音效），出拳最遠點發出 impact 事件（打擊音效、震屏）。',
    roles: [
      { name: 'fist', required: true, desc: '出拳的手（建議 IK 目標骨）' },
      { name: 'hip', required: false, desc: '上身根骨' },
      { name: 'head', required: false, desc: '頭' },
      HAIR_ROLE,
    ],
    params: [
      { name: 'reach', default: 130, desc: '出拳距離（單位，會乘上 size）' },
      { name: 'windup', default: 35, desc: '蓄力往後拉的距離' },
      { name: 'lean', default: 6, desc: '身體前傾角度' },
      HAIR_PARAM,
    ],
    build(c) {
      const sp = 1; // overall speed is the animation-level timeScale, shared by every preset
      const s = c.size;
      const reach = c.param('reach', 130) * s;
      const windup = c.param('windup', 35) * s;
      const leanDeg = c.param('lean', 6);
      const tA = 0.27 / sp, tS = 0.067 / sp, hold = 0.27 / sp, tRec = 0.25 / sp;
      const tI = tA + tS;
      const total = tI + hold + tRec;
      const out = empty(total, false);
      const fist = c.role('fist');
      const F = c.facing;
      out.screen.push({
        bone: fist,
        channel: 'x',
        motion: strike({
          anticipation: { value: -windup * F, time: tA, ease: 'sineOut' },
          action: { value: reach * F, time: tS, ease: 'quartIn' },
          overshoot: { amount: 0.06 * reach, frequency: 6, damping: 0.45 },
          hold,
          recover: { time: tRec, ease: 'sineInOut' },
        }),
      });
      out.screen.push({
        bone: fist,
        channel: 'y',
        motion: poses([
          { time: 0, value: 0 },
          { time: tA, value: 0.1 * reach, ease: 'sineOut' },
          { time: tI, value: -0.03 * reach, ease: 'quartIn' },
          { time: tI + hold, value: -0.03 * reach, ease: 'sineInOut' },
          { time: total, value: 0, ease: 'sineInOut' },
        ]),
      });
      const hip = c.roleOpt('hip');
      if (hip) {
        out.screen.push({
          bone: hip,
          channel: 'rotate',
          motion: strike({
            anticipation: { value: 0.5 * leanDeg * F, time: tA, ease: 'sineOut' },
            action: { value: -leanDeg * F, time: tS, ease: 'quartIn' },
            overshoot: { amount: 0.25 * leanDeg, frequency: 5, damping: 0.5 },
            hold,
            recover: { time: tRec },
          }),
        });
        out.screen.push({
          bone: hip,
          channel: 'x',
          motion: strike({
            anticipation: { value: -0.08 * reach * F, time: tA, ease: 'sineOut' },
            action: { value: 0.16 * reach * F, time: tS, ease: 'quartIn' },
            hold,
            recover: { time: tRec },
          }),
        });
      }
      const head = c.roleOpt('head');
      if (head) {
        // follow-through: the head arrives 2 frames after the body and swings a little further
        const lag = 2 / 30 / sp;
        const m = strike({
          anticipation: { value: 0.7 * leanDeg * F, time: tA, ease: 'sineOut' },
          action: { value: -1.3 * leanDeg * F, time: tS, ease: 'cubicIn' },
          overshoot: { amount: 0.4 * leanDeg, frequency: 4, damping: 0.4 },
          hold: hold - lag,
          recover: { time: tRec },
        });
        out.screen.push({ bone: head, channel: 'rotate', motion: padTo(delayed(m, lag), total) });
      }
      // 'swing' = the wind-up turns into the strike (whoosh sound); 'impact' = full extension (hit sound, screen shake)
      out.events.push({ name: 'swing', time: round3(tA) }, { name: 'impact', time: round3(tI) });
      hairFollow(c, out);
      return out;
    },
  },
  {
    id: 'hit-react',
    title: '受擊反應',
    kind: 'once',
    summary: '被打退：身體瞬間後退再以彈簧回位，頭晚 2 幀甩得更大，附衰減抖動。開頭發出 hit 事件。',
    roles: [
      { name: 'hip', required: true, desc: '上身根骨' },
      { name: 'head', required: false, desc: '頭' },
      HAIR_ROLE,
    ],
    params: [
      { name: 'force', default: 40, desc: '後退距離' },
      { name: 'tilt', default: 14, desc: '頭後仰角度' },
      { name: 'duration', default: 0.9, desc: '總長（秒）' },
      HAIR_PARAM,
    ],
    build(c) {
      const D = c.param('duration', 0.9);
      const force = c.param('force', 40) * c.size;
      const tilt = c.param('tilt', 14);
      const F = c.facing;
      const out = empty(D, false);
      const hip = c.role('hip');
      const knock = (t: number, rise: number, f: number, z: number) => (t < rise ? EASES.expoOut(t / rise) : springDecay(t - rise, f, z));
      out.screen.push({ bone: hip, channel: 'x', motion: fn(D, (t) => -F * force * knock(t, 0.08, 2.5, 0.45), [0.08]) });
      out.screen.push({ bone: hip, channel: 'rotate', motion: fn(D, (t) => F * 0.3 * tilt * knock(t, 0.08, 2.5, 0.45), [0.08]) });
      out.screen.push({ bone: hip, channel: 'y', motion: padTo(shake({ amplitude: 0.12 * force, frequency: 16, duration: 0.4, envelope: 'decay', seed: 3 }), D) });
      const head = c.roleOpt('head');
      if (head) {
        const lag = 2 / 30;
        out.screen.push({ bone: head, channel: 'rotate', motion: fn(D, (t) => (t < lag ? 0 : F * tilt * knock(t - lag, 0.08, 3.2, 0.35)), [lag, lag + 0.08]) });
      }
      out.events.push({ name: 'hit', time: 0 });
      hairFollow(c, out);
      return out;
    },
  },
  {
    id: 'jump',
    title: '跳躍（蹲 → 起跳拉長 → 滯空 → 落地壓扁 → 回穩）',
    kind: 'once',
    summary: '經典擠壓與伸展。起跳與落地發出 takeoff / land 事件。',
    roles: [
      { name: 'root', required: true, desc: '整體位移用的骨（通常是 root 或 hip）' },
      { name: 'body', required: false, desc: '做擠壓伸展的骨（通常是 hip 或 body）' },
      { name: 'feet', required: false, list: true, desc: '雙腳 IK 目標骨：下蹲、落地時踩住地面，膝蓋才會彎' },
      { name: 'chest', required: false, desc: '胸（有雙腳 IK 時改由胸做擠壓伸展，腿不跟著縮）' },
      HAIR_ROLE,
    ],
    params: [
      { name: 'height', default: 120, desc: '跳躍高度' },
      { name: 'crouch', default: 25, desc: '下蹲深度' },
      HAIR_PARAM,
    ],
    build(c) {
      const h = c.param('height', 120) * c.size;
      const cr = c.param('crouch', 25) * c.size;
      // rise decelerates to zero speed at the apex and the fall starts from zero speed: no hitch at the top
      const tC = 0.18, tUp = 0.45, tLand = 0.8, D = 1.2;
      const out = empty(D, false);
      const land = overshoot(-0.35 * cr, 3, 0.45);
      const rootY = (t: number) => {
        if (t < tC) return -cr * EASES.sineInOut(t / tC);
        if (t < tUp) return -cr + (h + cr) * EASES.cubicOut((t - tC) / (tUp - tC));
        if (t < tLand) return h * (1 - EASES.quadIn((t - tUp) / (tLand - tUp)));
        return land(t - tLand);
      };
      out.screen.push({ bone: c.role('root'), channel: 'y', motion: fn(D, rootY, [tC, tUp, tLand]) });
      // Foot IK targets ride with the root, so the crouch and the landing sank the feet into the floor and the legs never
      // bent. Hold them on the ground while the body is below its rest height; they leave it at the moment the root
      // passes rest height on the way up, and land at tLand.
      const feet = c.roleList('feet');
      if (feet.length) {
        // root crosses rest height on the way up where cubicOut((t - tC) / (tUp - tC)) = cr / (h + cr)
        const tOff = tC + (tUp - tC) * (1 - Math.cbrt(1 - cr / (h + cr)));
        const footY = (t: number) => (t > tOff && t < tLand ? rootY(t) : 0);
        for (const f of feet) out.screen.push({ bone: f, channel: 'y', motion: fn(D, footY, [tOff, tLand]) });
      }
      // with planted feet the legs carry the crouch; squashing the hip would also shrink the thighs under it (they
      // inherit its scale) and straighten the knees back out, so the upper body squashes instead
      const body = feet.length ? c.roleOpt('chest') ?? c.roleOpt('body') : c.roleOpt('body');
      if (body) {
        const vertical = pointsVertically(c, body);
        const sq = (t: number) => {
          if (t < tC) return -0.1 * EASES.sineInOut(t / tC);
          if (t < tUp) return -0.1 + 0.18 * EASES.cubicOut(Math.min(1, (t - tC) / 0.1)) - 0.08 * EASES.sineInOut(Math.max(0, (t - tC - 0.1) / (tUp - tC - 0.1)));
          if (t < tLand) return 0;
          return -0.12 * springDecay(t - tLand, 3, 0.4) * Math.min(1, (t - tLand) / 0.02);
        };
        const along = fn(D, (t) => 1 + sq(t), [tC, tC + 0.1, tUp, tLand]);
        const across = fn(D, (t) => 1 - 0.6 * sq(t), [tC, tC + 0.1, tUp, tLand]);
        out.local.push(
          { bone: body, channel: vertical ? 'scaleX' : 'scaleY', motion: along },
          { bone: body, channel: vertical ? 'scaleY' : 'scaleX', motion: across },
        );
      }
      out.events.push({ name: 'takeoff', time: tC }, { name: 'land', time: tLand });
      hairFollow(c, out);
      return out;
    },
  },
  {
    id: 'celebrate',
    title: '勝利慶祝（循環）',
    kind: 'loop',
    summary: '雙手高舉上下揮動、身體兩次彈跳、頭後仰。手用 IK 目標骨在畫面上移動，不受骨架方向影響。',
    roles: [
      { name: 'hip', required: true, desc: '上身根骨' },
      { name: 'fists', required: false, list: true, desc: '雙手 IK 目標骨' },
      { name: 'head', required: false, desc: '頭' },
      HAIR_ROLE,
    ],
    params: [
      { name: 'angle', default: 85, desc: '前手前臂舉起的角度：從水平往前算起（手是 IK 目標骨時）' },
      { name: 'elbow', default: 40, desc: '前手上臂的角度：從水平往前算起。40° 時拳套到頭頂高度；調低（負的是手肘朝下）手臂就不會經過臉，但看起來不像慶祝（手是 IK 目標骨時）' },
      { name: 'angleBack', default: 95, desc: '後手前臂的角度（手是 IK 目標骨時）' },
      { name: 'elbowBack', default: 60, desc: '後手上臂的角度：比前手高一點，拳套從頭後面露出來（手是 IK 目標骨時）' },
      { name: 'swing', default: 180, desc: '上臂最多離開畫的姿勢幾度（180 = 不限制）：畫成手肘外張或下垂、肩膀端沒有多畫的角色，轉太多會露出切口、看起來手和身體分離，這時調小（手是 IK 目標骨時）' },
      { name: 'lift', default: 120, desc: '雙手抬高（手不是 IK 目標骨時才用）' },
      { name: 'bounce', default: 12, desc: '身體彈跳幅度' },
      HAIR_PARAM,
    ],
    build(c) {
      const D = 1.2;
      const out = empty(D, true);
      const s = c.size;
      out.screen.push({ bone: c.role('hip'), channel: 'y', motion: wave({ amplitude: c.param('bounce', 12) * s, period: 0.6, peakAt: 0.15, duration: D }) });
      const angle = c.param('angle', 85);
      const elbowAngle = c.param('elbow', 40);
      const swing = c.param('swing', 180);
      c.roleList('fists').forEach((f, i) => {
        const peakAt = 0.2 + (i * 2) / 30; // the far fist trails by 2 frames; a peak between key frames makes the runtime's 10-line curve segments jolt
        const chain = c.ikChain(f);
        // keep the upper arm within `swing` of how it was drawn: the shoulder end is only painted to cover the torso
        // around the drawn angle, so a big turn (a rear arm drawn hanging out to the side) shows its cut edge
        const drawn = chain && (Math.atan2(chain.mid.y - chain.root.y, (chain.mid.x - chain.root.x) * c.facing) * 180) / Math.PI;
        // the far arm goes a little higher, so its glove shows over the back of the head instead of stacking on the near one
        const want = i ? c.param('elbowBack', 60) : elbowAngle;
        const off = drawn === undefined ? 0 : ((want - drawn + 540) % 360) - 180;
        const foreAngle = i ? c.param('angleBack', 95) : angle;
        const elbowPose = drawn === undefined ? want : drawn + Math.max(-swing, Math.min(swing, off));
        // Pose each arm by its two angles (upper arm `elbow`, forearm `angle`, from forward) and solve the fist target
        // from them, so the glove ends up at the same place on any rig: at the defaults, at the top of the head with the
        // elbow forward. A side-on character's front arm crosses the face on the way up; a negative `elbow` keeps it
        // below the jaw, but then it no longer reads as a celebration. The IK only lands the elbow there when it bends
        // to the same side as in the setup pose, so a chain that bends the other way keeps the plain lift.
        const posed = chain && (() => {
          const [l1, l2] = chain.lengths;
          const at = (ua: number, fa: number) => {
            const u = (ua * Math.PI) / 180, w = (fa * Math.PI) / 180;
            const ex = chain.root.x + Math.cos(u) * l1 * c.facing, ey = chain.root.y + Math.sin(u) * l1;
            return { ex, ey, x: ex + Math.cos(w) * l2 * c.facing, y: ey + Math.sin(w) * l2 };
          };
          const side = (p: { x: number; y: number }, q: { x: number; y: number }) => Math.sign((q.x - chain.root.x) * (p.y - chain.root.y) - (q.y - chain.root.y) * (p.x - chain.root.x));
          const p = at(elbowPose, foreAngle);
          return side(chain.mid, chain.target) === side({ x: p.ex, y: p.ey }, p) ? at : undefined;
        })();
        if (chain && !posed && Math.abs(off) > swing) {
          // the capped pose would flip the elbow: leave the arm as drawn and only pump it
          out.screen.push({ bone: f, channel: 'y', motion: wave({ amplitude: 0.06 * chain.reach, period: 0.6, peakAt, duration: D }) });
          return;
        }
        if (!chain || !posed) {
          out.screen.push({ bone: f, channel: 'y', motion: wave({ amplitude: 25 * s, period: 0.6, peakAt, offset: c.param('lift', 120) * s, duration: D }) });
          return;
        }
        // the pump: the forearm swings 10° and the upper arm 5° around the pose, together
        const fore = wave({ amplitude: 10, period: 0.6, peakAt, offset: foreAngle, duration: D });
        const upper = wave({ amplitude: 5, period: 0.6, peakAt, offset: elbowPose, duration: D });
        const fist = (t: number) => posed(upper.value(t), fore.value(t));
        out.screen.push({ bone: f, channel: 'x', motion: fn(D, (t) => fist(t).x - chain.target.x, [], true) });
        out.screen.push({ bone: f, channel: 'y', motion: fn(D, (t) => fist(t).y - chain.target.y, [], true) });
      });
      const head = c.roleOpt('head');
      if (head) out.screen.push({ bone: head, channel: 'rotate', motion: wave({ amplitude: 3, period: 0.6, peakAt: 0.25, offset: 6 * c.facing, duration: D }) });
      hairFollow(c, out);
      return out;
    },
  },
  {
    id: 'symbol-land',
    title: '符號落定（squash + 回彈）',
    kind: 'once',
    summary: '符號由上落下，衝過停止點 0.11 個符號高再彈回（同 Iron Jaw 轉輪實測），落地瞬間壓扁。發出 land 事件。',
    roles: [{ name: 'symbol', required: true, desc: '符號的根骨' }],
    params: [
      { name: 'symbolSize', default: 98, desc: '符號高度（單位）' },
      { name: 'drop', default: 0.5, desc: '從多高落下（符號高的倍數）' },
    ],
    build(c) {
      const S = c.param('symbolSize', 98);
      const drop = c.param('drop', 0.5) * S;
      const tI = 0.12, D = 0.6;
      const out = empty(D, false);
      const bounce = overshoot(-0.11 * S, 4, 0.5);
      const sym = c.role('symbol');
      out.screen.push({ bone: sym, channel: 'y', motion: fn(D, (t) => (t < tI ? drop * (1 - EASES.quadIn(t / tI)) : bounce(t - tI)), [tI]) });
      const sq = (t: number) => (t < tI ? 0.03 * EASES.quadIn(t / tI) : -0.1 * springDecay(t - tI, 4, 0.45) * Math.min(1, (t - tI) / 0.025) + 0.03 * Math.max(0, 1 - (t - tI) / 0.025));
      out.local.push(
        { bone: sym, channel: 'scaleY', motion: fn(D, (t) => 1 + sq(t), [tI, tI + 0.025]) },
        { bone: sym, channel: 'scaleX', motion: fn(D, (t) => 1 - 0.6 * sq(t), [tI, tI + 0.025]) },
      );
      out.events.push({ name: 'land', time: tI });
      return out;
    },
  },
  {
    id: 'symbol-win',
    title: '符號得獎（彈出 + 搖晃回穩）',
    kind: 'once',
    summary: '放大到 1.22 倍再以彈簧回到 1，同時搖晃 ±5° 衰減。在最大時發出 win 事件。',
    roles: [{ name: 'symbol', required: true, desc: '符號的根骨' }],
    params: [
      { name: 'pop', default: 0.22, desc: '彈出放大量（0.22 = 122%）' },
      { name: 'wobble', default: 5, desc: '搖晃角度' },
    ],
    build(c) {
      const D = 1.0, tP = 0.1;
      const pop = c.param('pop', 0.22);
      const out = empty(D, false);
      const sym = c.role('symbol');
      const sc = fn(D, (t) => (t < tP ? 1 + pop * EASES.cubicOut(t / tP) : 1 + pop * springDecay(t - tP, 3.2, 0.32)), [tP]);
      out.local.push({ bone: sym, channel: 'scaleX', motion: sc }, { bone: sym, channel: 'scaleY', motion: sc });
      const wob = overshoot(c.param('wobble', 5), 3.5, 0.3);
      out.local.push({ bone: sym, channel: 'rotate', motion: fn(D, (t) => wob(t)) });
      out.events.push({ name: 'win', time: tP });
      return out;
    },
  },
  {
    id: 'symbol-win-loop',
    title: '符號得獎循環（脈動）',
    kind: 'loop',
    summary: '得獎後持續脈動：0.8 秒一次放大到 1.06，搭配 ±1.5° 慢搖。',
    roles: [{ name: 'symbol', required: true, desc: '符號的根骨' }],
    params: [{ name: 'pulse', default: 0.06, desc: '脈動放大量' }],
    build(c) {
      const D = 1.6;
      const p = c.param('pulse', 0.06);
      const out = empty(D, true);
      const sym = c.role('symbol');
      const sc = wave({ amplitude: p / 2, period: 0.8, peakAt: 0.4, offset: 1 + p / 2, duration: D });
      out.local.push({ bone: sym, channel: 'scaleX', motion: sc }, { bone: sym, channel: 'scaleY', motion: sc });
      out.local.push({ bone: sym, channel: 'rotate', motion: wave({ amplitude: 1.5, period: 1.6, peakAt: 0.4 }) });
      return out;
    },
  },
  {
    id: 'symbol-idle',
    title: '符號待機（漂浮）',
    kind: 'loop',
    summary: '高價符號在輪上的輕微漂浮：上下 ±2.5% 符號高、2 秒一次，旋轉 ±1°、4 秒一次。',
    roles: [{ name: 'symbol', required: true, desc: '符號的根骨' }],
    params: [{ name: 'symbolSize', default: 98, desc: '符號高度（單位）' }],
    build(c) {
      const D = 4;
      const S = c.param('symbolSize', 98);
      const out = empty(D, true);
      const sym = c.role('symbol');
      out.screen.push({ bone: sym, channel: 'y', motion: wave({ amplitude: 0.025 * S, period: 2, peakAt: 0.5, duration: D }) });
      out.local.push({ bone: sym, channel: 'rotate', motion: wave({ amplitude: 1, period: 4, peakAt: 1 }) });
      return out;
    },
  },
  {
    id: 'tease-shake',
    title: '預告顫動（Scatter 期待感）',
    kind: 'once',
    summary: '越抖越強並慢慢脹大，最後 0.15 秒收回，用在差一個 Scatter 時。開頭發出 tease 事件。',
    roles: [{ name: 'symbol', required: true, desc: '符號的根骨' }],
    params: [
      { name: 'symbolSize', default: 98, desc: '符號高度（單位）' },
      { name: 'duration', default: 1.5, desc: '總長（秒），配合轉輪預告停輪的延長時間' },
    ],
    build(c) {
      const D = c.param('duration', 1.5);
      const S = c.param('symbolSize', 98);
      const out = empty(D, false);
      const sym = c.role('symbol');
      out.screen.push({ bone: sym, channel: 'x', motion: shake({ amplitude: 0.04 * S, frequency: 14, duration: D, envelope: 'grow', seed: 7 }) });
      const tRel = D - 0.15;
      const sc = poses([
        { time: 0, value: 1 },
        { time: tRel, value: 1.06, ease: 'sineIn' },
        { time: D, value: 1, ease: 'cubicOut' },
      ]);
      out.local.push({ bone: sym, channel: 'scaleX', motion: sc }, { bone: sym, channel: 'scaleY', motion: sc });
      out.events.push({ name: 'tease', time: 0 });
      return out;
    },
  },
  {
    id: 'text-slam',
    title: '大字砸入（Big Win 標題）',
    kind: 'once',
    summary: '從 2.4 倍在 0.1 秒內砸到 0.92 倍，再彈簧回到 1，同時從 −8° 轉正。對應 Iron Jaw Big Win 的打擊幀。發出 slam 事件。',
    roles: [{ name: 'title', required: true, desc: '標題文字的根骨' }],
    params: [
      { name: 'from', default: 2.4, desc: '起始縮放' },
      { name: 'tilt', default: 8, desc: '起始傾斜角度' },
    ],
    build(c) {
      const D = 0.9, tI = 0.1;
      const from = c.param('from', 2.4);
      const out = empty(D, false);
      const b = c.role('title');
      const sc = fn(D, (t) => (t < tI ? from + (0.92 - from) * EASES.quartIn(t / tI) : 1 - 0.08 * springDecay(t - tI, 4, 0.4)), [tI]);
      out.local.push({ bone: b, channel: 'scaleX', motion: sc }, { bone: b, channel: 'scaleY', motion: sc });
      const tilt = c.param('tilt', 8);
      const swing = overshoot(0.15 * tilt, 4, 0.4); // keeps turning past upright, then settles
      out.local.push({ bone: b, channel: 'rotate', motion: fn(D, (t) => (t < tI ? -tilt * (1 - EASES.quartIn(t / tI)) : swing(t - tI)), [tI]) });
      out.events.push({ name: 'slam', time: tI });
      return out;
    },
  },
];

function round3(x: number): number {
  return Math.round(x * 1000) / 1000;
}

export function findPreset(id: string): PresetDef {
  const p = PRESETS.find((x) => x.id === id);
  if (!p) throw new Error(`沒有預設 "${id}"。可用：${PRESETS.map((x) => x.id).join(', ')}`);
  return p;
}
