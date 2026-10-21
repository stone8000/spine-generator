/**
 * Motion primitives. Each returns a Motion1D (a pure function of time) that bake.ts turns into Spine keys.
 * Times are in seconds, values in the channel's unit (degrees, skeleton units, or scale multipliers).
 */
import type { Motion1D } from './bake.ts';
import { ease, springDecay, type EaseName } from './ease.ts';

/** Loop: offset + amplitude * cos(2π (t - peakAt) / period). The maximum happens at `peakAt`. */
export function wave(p: { amplitude: number; period: number; peakAt?: number; offset?: number; duration?: number }): Motion1D {
  const duration = p.duration ?? p.period;
  const cycles = duration / p.period;
  if (Math.abs(cycles - Math.round(cycles)) > 1e-6) {
    throw new Error(`wave: loop length ${duration}s must be a whole number of periods (${p.period}s)`);
  }
  const peakAt = p.peakAt ?? 0;
  const offset = p.offset ?? 0;
  return {
    duration,
    loop: true,
    value: (t) => offset + p.amplitude * Math.cos((2 * Math.PI * (t - peakAt)) / p.period),
  };
}

/** Sum of several loops with the same length (e.g. a slow sway plus a faster breath). */
export function sum(parts: Motion1D[], offset = 0): Motion1D {
  const duration = parts[0].duration;
  for (const m of parts) {
    if (Math.abs(m.duration - duration) > 1e-9) throw new Error('sum: all parts must have the same duration');
  }
  return {
    duration,
    loop: parts.every((m) => m.loop),
    value: (t) => offset + parts.reduce((s, m) => s + m.value(t), 0),
    breaks: parts.flatMap((m) => m.breaks ?? []),
  };
}

export interface PosePoint {
  /** Seconds from the start. */
  time: number;
  value: number;
  /** Ease used to arrive at this point from the previous one. */
  ease?: EaseName;
}

/** Pose-to-pose: hit each value at its time, easing between them. First point should be at time 0. */
export function poses(points: PosePoint[], opts: { loop?: boolean; duration?: number } = {}): Motion1D {
  const pts = [...points].sort((a, b) => a.time - b.time);
  if (pts[0].time !== 0) pts.unshift({ time: 0, value: pts[0].value });
  const duration = opts.duration ?? pts[pts.length - 1].time;
  if (opts.loop && Math.abs(pts[pts.length - 1].value - pts[0].value) > 1e-9) {
    pts.push({ time: duration, value: pts[0].value, ease: 'sineInOut' });
  }
  return {
    duration,
    loop: !!opts.loop,
    breaks: pts.map((p) => p.time).filter((t) => t > 0 && t < duration),
    value: (t) => {
      if (t <= pts[0].time) return pts[0].value;
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1];
        const b = pts[i];
        if (t <= b.time) {
          const u = (t - a.time) / (b.time - a.time);
          return a.value + (b.value - a.value) * ease(b.ease)(u);
        }
      }
      return pts[pts.length - 1].value;
    },
  };
}

export interface StrikeParams {
  /** Resting value. */
  rest?: number;
  /** Wind-up: value pulled away from the target, and how long it takes. */
  anticipation: { value: number; time: number; ease?: EaseName };
  /** The hit: value at full extension and how long the snap takes. */
  action: { value: number; time: number; ease?: EaseName };
  /** Past-the-target overshoot that wobbles back: peak amount (same units), wobble frequency, damping ratio. */
  overshoot?: { amount: number; frequency?: number; damping?: number };
  /** How long to stay at the target after the hit (the wobble plays during this). */
  hold?: number;
  /** Return to rest. */
  recover?: { time: number; ease?: EaseName };
}

/**
 * Anticipation → action → overshoot → settle → hold → recover.
 * Position is continuous everywhere; the velocity deliberately breaks at the impact (that is what makes a hit read).
 */
export function strike(p: StrikeParams): Motion1D {
  const rest = p.rest ?? 0;
  const tA = p.anticipation.time;
  const tI = tA + p.action.time; // impact
  const hold = p.hold ?? 0.25;
  const tH = tI + hold;
  const tR = tH + (p.recover?.time ?? 0.25);
  const easeA = ease(p.anticipation.ease ?? 'sineOut');
  const easeS = ease(p.action.ease ?? 'quartIn');
  const easeR = ease(p.recover?.ease ?? 'sineInOut');
  const dir = Math.sign(p.action.value - p.anticipation.value) || 1;
  const os = p.overshoot ?? { amount: 0 };
  const w = 2 * Math.PI * (os.frequency ?? 4);
  const z = Math.min(Math.max(os.damping ?? 0.4, 0.05), 0.95);
  const wd = w * Math.sqrt(1 - z * z);
  // overshoot term starts at 0 with forward velocity; scale so its first peak equals `amount`
  const tPeak = Math.atan2(wd, z * w) / wd;
  const peakShape = Math.exp(-z * w * tPeak) * Math.sin(wd * tPeak);
  const ampl = os.amount ? (dir * os.amount) / peakShape : 0;
  const settle = (s: number) => ampl * Math.exp(-z * w * s) * Math.sin(wd * s);
  const atHoldEnd = p.action.value + settle(hold);
  return {
    duration: tR,
    loop: false,
    breaks: [tA, tI, tH],
    value: (t) => {
      if (t <= tA) return rest + (p.anticipation.value - rest) * easeA(t / tA);
      if (t <= tI) return p.anticipation.value + (p.action.value - p.anticipation.value) * easeS((t - tA) / (tI - tA));
      if (t <= tH) return p.action.value + settle(t - tI);
      return atHoldEnd + (rest - atHoldEnd) * easeR((t - tH) / (tR - tH));
    },
  };
}

/** Spring from `from` to `to` (landing, pop-in, recoil). Starts after `delay` seconds; lasts `duration`. */
export function spring(p: { from: number; to: number; frequency?: number; damping?: number; delay?: number; duration: number }): Motion1D {
  const delay = p.delay ?? 0;
  return {
    duration: p.duration,
    loop: false,
    breaks: delay > 0 ? [delay] : [],
    value: (t) => (t < delay ? p.from : p.to + (p.from - p.to) * springDecay(t - delay, p.frequency ?? 3, p.damping ?? 0.35)),
  };
}

/**
 * Shake: three sine waves at unrelated frequencies under an envelope. Deterministic (same seed, same result).
 * envelope 'decay' = hit reaction, 'grow' = anticipation tease, 'flat' = loopable tremble.
 */
export function shake(p: { amplitude: number; frequency: number; duration: number; envelope?: 'decay' | 'grow' | 'flat'; seed?: number }): Motion1D {
  const env = p.envelope ?? 'decay';
  const seed = p.seed ?? 1;
  const ratios = [1, 1.37, 2.11];
  const weights = [1, 0.6, 0.35];
  const norm = weights.reduce((a, b) => a + b, 0);
  const freqs = ratios.map((r) => (env === 'flat' ? Math.max(1, Math.round(p.frequency * r * p.duration)) / p.duration : p.frequency * r));
  const phases = ratios.map((_, i) => ((Math.sin(seed * 12.9898 + i * 78.233) * 43758.5453) % 1) * 2 * Math.PI);
  return {
    duration: p.duration,
    loop: env === 'flat',
    value: (t) => {
      const u = t / p.duration;
      // every envelope starts and ends at 0 so the shake never pops in or out
      const attack = Math.min(1, u / 0.06);
      const release = Math.min(1, (1 - u) / 0.1);
      const e =
        env === 'decay' ? Math.pow(1 - u, 2) * (attack * attack * (3 - 2 * attack))
        : env === 'grow' ? Math.pow(u, 1.5) * (release * release * (3 - 2 * release))
        : 1; // 'flat': whole cycles of every frequency, so it is periodic by construction
      let s = 0;
      for (let i = 0; i < 3; i++) s += weights[i] * Math.sin(2 * Math.PI * freqs[i] * t + phases[i]);
      return (p.amplitude * e * s) / norm;
    },
  };
}

/** Delay a motion: holds its first value for `delay` seconds; total length grows by `delay`. */
export function delayed(m: Motion1D, delay: number): Motion1D {
  if (delay <= 0) return m;
  return {
    duration: m.duration + delay,
    loop: false,
    breaks: [delay, ...(m.breaks ?? []).map((b) => b + delay)],
    value: (t) => (t < delay ? m.value(0) : m.value(t - delay)),
  };
}

/** Extend a one-shot with a hold at its last value until `duration`. */
export function padTo(m: Motion1D, duration: number): Motion1D {
  if (duration <= m.duration + 1e-9) return m;
  return {
    duration,
    loop: false,
    breaks: [...(m.breaks ?? []), m.duration],
    value: (t) => m.value(Math.min(t, m.duration)),
  };
}

/** Scale a motion's values around a center (e.g. turn a unit-size preset into your rig's size). */
export function scaled(m: Motion1D, k: number, center = 0): Motion1D {
  return { ...m, value: (t) => center + (m.value(t) - center) * k };
}
