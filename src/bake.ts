/**
 * Bake any 1D motion function into Spine keys.
 *
 * Keys are placed where an animator would put them: at segment boundaries, at every extreme (with flat tangents),
 * and then wherever the curve still deviates by more than the tolerance. Each segment is a cubic Hermite curve written
 * as a Spine Bezier (handles at 1/3 of the segment), so the Spine curve passes exactly through every key with the true
 * slope. Loops get identical value and slope at the first and last key, so they have no seam.
 */

export interface Motion1D {
  /** Seconds. */
  duration: number;
  /** True for cyclic motion; value(0) must equal value(duration). */
  loop: boolean;
  /** Value at time t (0 <= t <= duration). */
  value(t: number): number;
  /** Times where value or slope may be non-smooth; always become keys. */
  breaks?: number[];
}

/** A Spine timeline key. `curve` is [cx1, cy1, cx2, cy2] in absolute time/value units, as in Spine JSON. */
export interface Key {
  time: number;
  value: number;
  curve?: [number, number, number, number] | 'stepped';
}

export interface BakeOptions {
  /** Max allowed deviation, in value units (degrees, px, scale). */
  tolerance: number;
  /** Snap key times to this frame rate (0 = no snapping). */
  fps?: number;
  /** Safety cap on keys per channel. */
  maxKeys?: number;
  /**
   * Longest allowed curve segment, seconds. The Spine runtime plays every Bezier segment as 10 straight lines, so a
   * long segment turns into visible facets (speed steps every 1/10 of the segment). 0.2 s keeps each line ≈ 20 ms.
   */
  maxSegment?: number;
}

const H = 1e-4;

function slopeRight(f: (t: number) => number, t: number): number {
  return (-3 * f(t) + 4 * f(t + H) - f(t + 2 * H)) / (2 * H);
}
function slopeLeft(f: (t: number) => number, t: number): number {
  return (3 * f(t) - 4 * f(t - H) + f(t - 2 * H)) / (2 * H);
}

function snap(t: number, fps: number, duration: number): number {
  if (!fps) return t;
  return Math.min(duration, Math.max(0, Math.round(t * fps) / fps));
}

/** Times where the slope changes sign (local extremes), found by dense sampling plus bisection. */
export function findExtremes(m: Motion1D, samplesPerSecond = 480): number[] {
  const n = Math.max(64, Math.ceil(m.duration * samplesPerSecond));
  const dt = m.duration / n;
  const out: number[] = [];
  const d = (t: number) => (m.value(Math.min(m.duration, t + 1e-6)) - m.value(Math.max(0, t - 1e-6))) / 2e-6;
  let prevT = 0;
  let prevD = d(dt / 2);
  for (let i = 1; i < n; i++) {
    const t = (i + 0.5) * dt;
    const dv = d(t);
    if (prevD === 0 || Math.sign(dv) !== Math.sign(prevD)) {
      let lo = prevT + dt / 2;
      let hi = t;
      for (let k = 0; k < 50; k++) {
        const mid = (lo + hi) / 2;
        if (Math.sign(d(mid)) === Math.sign(prevD) && prevD !== 0) lo = mid;
        else hi = mid;
      }
      out.push((lo + hi) / 2);
    }
    prevT = t - dt / 2;
    prevD = dv;
  }
  return out;
}

interface RawKey {
  t: number;
  v: number;
  mIn: number;
  mOut: number;
}

/**
 * What the Spine runtime actually plays for a segment: it samples the Bezier at 10 evenly spaced curve parameters and
 * draws straight lines between them (CurveTimeline.setBezier, BEZIER_SIZE = 18). With handles at 1/3 the curve
 * parameter is linear in time, so the samples sit at 10 even time steps.
 */
function runtimePlayback(a: RawKey, b: RawKey, t: number): number {
  const u = ((t - a.t) / (b.t - a.t)) * 10;
  const k = Math.min(9, Math.max(0, Math.floor(u)));
  const t0 = a.t + ((b.t - a.t) * k) / 10;
  const t1 = a.t + ((b.t - a.t) * (k + 1)) / 10;
  const v0 = hermite(a, b, t0);
  const v1 = hermite(a, b, t1);
  return v0 + (v1 - v0) * (u - k);
}

function hermite(a: RawKey, b: RawKey, t: number): number {
  const dt = b.t - a.t;
  const s = (t - a.t) / dt;
  const s2 = s * s;
  const s3 = s2 * s;
  return (2 * s3 - 3 * s2 + 1) * a.v + (s3 - 2 * s2 + s) * dt * a.mOut + (-2 * s3 + 3 * s2) * b.v + (s3 - s2) * dt * b.mIn;
}

export function bake(m: Motion1D, opts: BakeOptions): Key[] {
  const fps = opts.fps ?? 30;
  const maxKeys = opts.maxKeys ?? 400;
  const D = m.duration;
  const f = (t: number) => m.value(Math.min(D, Math.max(0, t)));
  const minGap = fps ? 1 / fps - 1e-9 : 1e-4;

  // 1. candidate key times
  const candidates = [0, D, ...(m.breaks ?? []), ...findExtremes(m)].map((t) => snap(t, fps, D));
  let times = Array.from(new Set(candidates.map((t) => +t.toFixed(9)))).sort((a, b) => a - b);
  // keep breaks and ends, drop extremes that collapse onto a neighbour
  times = times.filter((t, i) => i === 0 || t - times[i - 1] >= minGap);
  if (times[times.length - 1] !== D) times.push(D);
  // split long segments: the runtime linearizes each Bezier into 10 pieces
  const maxSeg = opts.maxSegment ?? 0.2;
  if (maxSeg > 0) {
    const split: number[] = [times[0]];
    for (let i = 1; i < times.length; i++) {
      const a = times[i - 1];
      const b = times[i];
      const parts = Math.ceil((b - a) / maxSeg - 1e-9);
      for (let k = 1; k < parts; k++) {
        const t = snap(a + ((b - a) * k) / parts, fps, D);
        if (t - split[split.length - 1] >= minGap && b - t >= minGap) split.push(t);
      }
      split.push(b);
    }
    times = split;
  }

  const keyAt = (t: number, isFirst: boolean, isLast: boolean): RawKey => {
    const v = f(t);
    let mIn = t > 0 ? slopeLeft(f, t) : 0;
    let mOut = t < D ? slopeRight(f, t) : 0;
    if (m.loop) {
      // the loop seam: both ends share one slope so the cycle is C1-continuous
      if (isFirst || isLast) {
        const s = (slopeRight(f, 0) + slopeLeft(f, D)) / 2;
        mIn = s;
        mOut = s;
      }
    } else {
      if (isFirst) mIn = mOut;
      if (isLast) mOut = mIn;
    }
    // a smooth point (not a break) gets one tangent
    const isBreak = (m.breaks ?? []).some((b) => Math.abs(b - t) < 1e-6);
    if (!isBreak && !isFirst && !isLast) {
      const s = (mIn + mOut) / 2;
      mIn = s;
      mOut = s;
    }
    return { t, v, mIn, mOut };
  };

  let keys = times.map((t, i) => keyAt(t, i === 0, i === times.length - 1));
  if (m.loop) {
    const v = (keys[0].v + keys[keys.length - 1].v) / 2;
    keys[0].v = v;
    keys[keys.length - 1].v = v;
  }

  // 2. refine until every segment is within tolerance
  for (let guard = 0; guard < maxKeys; guard++) {
    let worst = { err: 0, seg: -1, t: 0 };
    for (let i = 0; i < keys.length - 1; i++) {
      const a = keys[i];
      const b = keys[i + 1];
      for (let k = 1; k < 40; k++) {
        const t = a.t + ((b.t - a.t) * k) / 40;
        // measure against the runtime's straight-line playback, not the ideal curve
        const err = Math.abs(runtimePlayback(a, b, t) - f(t));
        if (err > worst.err) worst = { err, seg: i, t };
      }
    }
    if (worst.err <= opts.tolerance || keys.length >= maxKeys) break;
    const a = keys[worst.seg];
    const b = keys[worst.seg + 1];
    let t = snap(worst.t, fps, D);
    if (t - a.t < minGap || b.t - t < minGap) {
      t = (a.t + b.t) / 2; // segment shorter than two frames: allow an off-frame key
      if (b.t - a.t < 1e-4) break;
    }
    keys.splice(worst.seg + 1, 0, keyAt(t, false, false));
  }

  // 3. write Spine keys
  return keys.map((a, i) => {
    const out: Key = { time: round(a.t), value: round(a.v) };
    const b = keys[i + 1];
    if (b) {
      const dt = b.t - a.t;
      const cy1 = a.v + (a.mOut * dt) / 3;
      const cy2 = b.v - (b.mIn * dt) / 3;
      const linear = Math.abs(cy1 - (a.v + (b.v - a.v) / 3)) < 1e-6 && Math.abs(cy2 - (a.v + (2 * (b.v - a.v)) / 3)) < 1e-6;
      if (!linear) out.curve = [round(a.t + dt / 3), round(cy1), round(b.t - dt / 3), round(cy2)];
    }
    return out;
  });
}

function round(x: number): number {
  return Math.round(x * 1e5) / 1e5;
}

/** Evaluate baked keys exactly as Spine does (Bezier in time/value space). Used by tests and the checker. */
export function evalKeys(keys: Key[], t: number): number {
  if (t <= keys[0].time) return keys[0].value;
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t > b.time) continue;
    if (a.curve === 'stepped') return a.value;
    if (!a.curve) return a.value + ((b.value - a.value) * (t - a.time)) / (b.time - a.time);
    const [cx1, cy1, cx2, cy2] = a.curve;
    // solve x(u) = t on the Bezier, then return y(u)
    let lo = 0;
    let hi = 1;
    for (let k = 0; k < 60; k++) {
      const u = (lo + hi) / 2;
      const x = bez(a.time, cx1, cx2, b.time, u);
      if (x < t) lo = u;
      else hi = u;
    }
    return bez(a.value, cy1, cy2, b.value, (lo + hi) / 2);
  }
  return keys[keys.length - 1].value;
}

function bez(p0: number, p1: number, p2: number, p3: number, u: number): number {
  const v = 1 - u;
  return v * v * v * p0 + 3 * v * v * u * p1 + 3 * v * u * u * p2 + u * u * u * p3;
}
