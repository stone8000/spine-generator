/**
 * Reel motion model, fitted to the Iron Jaw (Xposed Studios) gameplay capture analysed on 2026-10-20.
 *
 * Units: one symbol height (= one row) and seconds. Position 0 = where the strip rests before the spin;
 * positive = scrolled downward. Every phase joins the next with matching position and velocity, so the
 * strip never jumps or jerks.
 *
 *   press ─ wind-up (pull up) ─ accelerate ─ cruise ─ brake ─ overshoot peak = landTime ─ spring back ─ rest
 */

export interface ReelMotionConfig {
  /** How far the strip pulls UP before launching (measured 16.8 px of a 98 px row). */
  windUpDistance: number;
  /** Duration of the pull-up (measured 3 frames @29.74 fps). */
  windUpTime: number;
  /** Time from the top of the pull-up to full speed, constant acceleration. */
  accelTime: number;
  /** Nominal cruise speed in rows per second (fitted 22; the raw blurred frames read ~24). */
  speed: number;
  /** Time from the press until reel 1 reaches its overshoot peak (measured ~0.60 s). */
  firstLandTime: number;
  /** Gap between consecutive reels' land times (measured 0.22 s). */
  stagger: number;
  /** Braking time from cruise speed to 0, ending at the overshoot peak. */
  decelTime: number;
  /** Shape of the braking: speed falls as (1 - s/decelTime)^brakePower. 1 = constant deceleration; higher = holds speed
   *  longer, then bites harder near the end (the measured reel fits 1.75). */
  brakePower: number;
  /** How far past the stop row the strip travels (measured 10.9 px). */
  overshoot: number;
  /** Time to spring back from the overshoot to the stop row (cosine ease; measured within 1 px after 3 frames). */
  settleTime: number;
  /** Above this speed (rows/s) show the pre-blurred symbol textures. */
  blurSpeed: number;
  /** Minimum whole rows a reel scrolls, so every reel visibly spins. */
  minTravel: number;
}

/** Fitted to 15 measured frames of reel 1 (pull-up + landing): worst error 3.3 px, RMS 1.8 px on a 98 px row. */
export const IRON_JAW: ReelMotionConfig = {
  windUpDistance: 0.17,
  windUpTime: 0.1,
  accelTime: 0.12,
  speed: 22,
  firstLandTime: 0.6,
  stagger: 0.22,
  decelTime: 0.11,
  brakePower: 1.75,
  overshoot: 0.11,
  settleTime: 0.07,
  blurSpeed: 6,
  minTravel: 5,
};

export interface ReelPlan {
  index: number;
  /** Time of the overshoot peak (the moment the reel "lands" for sound and highlight cues). */
  landTime: number;
  /** Time the strip is fully at rest. */
  restTime: number;
  /** Whole rows scrolled. Use it to pick which strip position ends up visible. */
  distance: number;
  /** Cruise speed actually used, nudged from cfg.speed so the reel stops exactly on a row. */
  speed: number;
}

/** Seconds of "full-speed equivalent" travel between the press and the overshoot peak. */
function travelFactor(landTime: number, cfg: ReelMotionConfig): number {
  return landTime - cfg.windUpTime - cfg.accelTime / 2 - cfg.decelTime + cfg.decelTime / (cfg.brakePower + 1);
}

/** Plans every reel. `extraDelay[i]` pushes reel i (and every reel after it) later, e.g. for an anticipation slow-down. */
export function planReels(reelCount: number, cfg: ReelMotionConfig = IRON_JAW, extraDelay: number[] = []): ReelPlan[] {
  const plans: ReelPlan[] = [];
  let pushed = 0;
  for (let i = 0; i < reelCount; i++) {
    pushed += extraDelay[i] ?? 0;
    const landTime = cfg.firstLandTime + i * cfg.stagger + pushed;
    const brakeStart = landTime - cfg.decelTime;
    if (brakeStart < cfg.windUpTime + cfg.accelTime) {
      throw new Error(`reel ${i}: landTime ${landTime}s is too early for wind-up + acceleration + braking`);
    }
    const k = travelFactor(landTime, cfg);
    const natural = cfg.speed * k - cfg.windUpDistance - cfg.overshoot;
    const distance = Math.max(cfg.minTravel, Math.round(natural));
    const speed = (distance + cfg.windUpDistance + cfg.overshoot) / k;
    plans.push({ index: i, landTime, restTime: landTime + cfg.settleTime, distance, speed });
  }
  return plans;
}

/** Strip position (rows) of one reel at time t seconds after the press. */
export function reelPosition(t: number, plan: ReelPlan, cfg: ReelMotionConfig = IRON_JAW): number {
  const { windUpDistance: W, windUpTime: Tw, accelTime: Ta, decelTime: Td, brakePower: P, overshoot: O, settleTime: Ts } = cfg;
  const V = plan.speed;
  const brakeStart = plan.landTime - Td;
  if (t <= 0) return 0;
  if (t < Tw) {
    const u = t / Tw;
    return -W * (1 - (1 - u) * (1 - u)); // ease-out up to the top of the pull
  }
  if (t < Tw + Ta) {
    const s = t - Tw;
    return -W + (V * s * s) / (2 * Ta);
  }
  const accelEnd = -W + (V * Ta) / 2;
  if (t < brakeStart) return accelEnd + V * (t - Tw - Ta);
  const atBrake = accelEnd + V * (brakeStart - Tw - Ta);
  if (t < plan.landTime) {
    const r = 1 - (t - brakeStart) / Td; // 1 at brake start, 0 at the peak
    return atBrake + ((V * Td) / (P + 1)) * (1 - Math.pow(r, P + 1));
  }
  if (t < plan.restTime) {
    const u = (t - plan.landTime) / Ts;
    return plan.distance + (O * (1 + Math.cos(Math.PI * u))) / 2; // spring back with zero speed at both ends
  }
  return plan.distance;
}

/** Strip velocity (rows/s) of one reel at time t. */
export function reelVelocity(t: number, plan: ReelPlan, cfg: ReelMotionConfig = IRON_JAW): number {
  const { windUpDistance: W, windUpTime: Tw, accelTime: Ta, decelTime: Td, brakePower: P, overshoot: O, settleTime: Ts } = cfg;
  const V = plan.speed;
  const brakeStart = plan.landTime - Td;
  if (t <= 0 || t >= plan.restTime) return 0;
  if (t < Tw) return (-W * 2 * (1 - t / Tw)) / Tw;
  if (t < Tw + Ta) return (V * (t - Tw)) / Ta;
  if (t < brakeStart) return V;
  if (t < plan.landTime) return V * Math.pow(1 - (t - brakeStart) / Td, P);
  const u = (t - plan.landTime) / Ts;
  return (-O * Math.PI * Math.sin(Math.PI * u)) / (2 * Ts);
}

/** True while the reel moves fast enough that the blurred symbol textures should be shown. */
export function useBlurredSymbols(t: number, plan: ReelPlan, cfg: ReelMotionConfig = IRON_JAW): boolean {
  return Math.abs(reelVelocity(t, plan, cfg)) > cfg.blurSpeed;
}
