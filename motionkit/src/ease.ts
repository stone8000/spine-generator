/**
 * Easing functions on the unit interval: e(0) = 0, e(1) = 1. Pure math, no Spine knowledge.
 * They are evaluated analytically and later baked into Spine Bezier keys by bake.ts,
 * so any of them (including overshooting "back" eases) ends up as smooth editable curves.
 */

export type EaseName =
  | 'linear'
  | 'sineIn' | 'sineOut' | 'sineInOut'
  | 'quadIn' | 'quadOut' | 'quadInOut'
  | 'cubicIn' | 'cubicOut' | 'cubicInOut'
  | 'quartIn' | 'quartOut' | 'quartInOut'
  | 'expoIn' | 'expoOut'
  | 'backIn' | 'backOut' | 'backInOut'
  | 'snap'; // very late, very hard: holds, then hits (quint in)

export type EaseFn = (u: number) => number;

const BACK = 1.70158;

export const EASES: Record<EaseName, EaseFn> = {
  linear: (u) => u,
  sineIn: (u) => 1 - Math.cos((u * Math.PI) / 2),
  sineOut: (u) => Math.sin((u * Math.PI) / 2),
  sineInOut: (u) => (1 - Math.cos(Math.PI * u)) / 2,
  quadIn: (u) => u * u,
  quadOut: (u) => 1 - (1 - u) * (1 - u),
  quadInOut: (u) => (u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2),
  cubicIn: (u) => u * u * u,
  cubicOut: (u) => 1 - Math.pow(1 - u, 3),
  cubicInOut: (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2),
  quartIn: (u) => u * u * u * u,
  quartOut: (u) => 1 - Math.pow(1 - u, 4),
  quartInOut: (u) => (u < 0.5 ? 8 * u * u * u * u : 1 - Math.pow(-2 * u + 2, 4) / 2),
  expoIn: (u) => (u <= 0 ? 0 : Math.pow(2, 10 * u - 10)),
  expoOut: (u) => (u >= 1 ? 1 : 1 - Math.pow(2, -10 * u)),
  backIn: (u) => (BACK + 1) * u * u * u - BACK * u * u,
  backOut: (u) => 1 + (BACK + 1) * Math.pow(u - 1, 3) + BACK * Math.pow(u - 1, 2),
  backInOut: (u) => {
    const c = BACK * 1.525;
    return u < 0.5
      ? (Math.pow(2 * u, 2) * ((c + 1) * 2 * u - c)) / 2
      : (Math.pow(2 * u - 2, 2) * ((c + 1) * (u * 2 - 2) + c) + 2) / 2;
  },
  snap: (u) => u * u * u * u * u,
};

export function ease(name: EaseName | undefined): EaseFn {
  if (!name) return EASES.sineInOut;
  const fn = EASES[name];
  if (!fn) throw new Error(`Unknown ease "${name}". Use one of: ${Object.keys(EASES).join(', ')}`);
  return fn;
}

/**
 * Damped spring response from 1 back to 0: x(0) = 1, x'(0) = 0, settles toward 0.
 * `cycles` = how many visible wobbles; `damping` = damping ratio (0 < zeta < 1).
 */
export function springDecay(t: number, frequencyHz: number, damping: number): number {
  const w = 2 * Math.PI * frequencyHz;
  const z = Math.min(Math.max(damping, 0.01), 0.99);
  const wd = w * Math.sqrt(1 - z * z);
  return Math.exp(-z * w * t) * (Math.cos(wd * t) + ((z * w) / wd) * Math.sin(wd * t));
}
