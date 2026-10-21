import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bake, evalKeys, type Motion1D } from '../src/bake.ts';
import { EASES } from '../src/ease.ts';

const sine = (A: number, T: number, peakAt: number): Motion1D => ({
  duration: T,
  loop: true,
  value: (t) => A * Math.cos((2 * Math.PI * (t - peakAt)) / T),
});

function maxErr(m: Motion1D, keys: ReturnType<typeof bake>): number {
  let e = 0;
  for (let i = 0; i <= 2000; i++) {
    const t = (m.duration * i) / 2000;
    e = Math.max(e, Math.abs(evalKeys(keys, t) - m.value(t)));
  }
  return e;
}

test('sine loop at any phase stays within tolerance with few keys', () => {
  for (const peak of [0, 0.1, 0.37, 1.0, 1.73]) {
    const m = sine(10, 2, peak);
    const keys = bake(m, { tolerance: 0.05, fps: 30 });
    assert.ok(maxErr(m, keys) <= 0.06, `peak ${peak}: err ${maxErr(m, keys)}`);
    assert.ok(keys.length <= 13, `peak ${peak}: ${keys.length} keys`);
    // no curve segment longer than 0.2 s: the Spine runtime plays each segment as 10 straight lines
    for (let i = 1; i < keys.length; i++) assert.ok(keys[i].time - keys[i - 1].time <= 0.2 + 1e-9, `segment ${keys[i - 1].time}-${keys[i].time}`);
    assert.equal(keys[0].time, 0);
    assert.equal(keys[keys.length - 1].time, 2);
  }
});

test('loop seam: same value and same slope at both ends', () => {
  const m = sine(10, 2, 0.37);
  const keys = bake(m, { tolerance: 0.05, fps: 30 });
  const first = keys[0];
  const last = keys[keys.length - 1];
  assert.equal(first.value, last.value);
  const prev = keys[keys.length - 2];
  const [cx1, cy1] = first.curve as number[];
  const [, , cx2, cy2] = prev.curve as number[];
  const slopeStart = (cy1 - first.value) / (cx1 - first.time);
  const slopeEnd = (last.value - cy2) / (last.time - cx2);
  assert.ok(Math.abs(slopeStart - slopeEnd) < 1e-3, `${slopeStart} vs ${slopeEnd}`);
});

test('extremes become keys with flat tangents', () => {
  const m = sine(10, 2, 0.5); // max exactly on frame 15
  const keys = bake(m, { tolerance: 0.05, fps: 30 });
  const k = keys.find((x) => Math.abs(x.time - 0.5) < 1e-9);
  assert.ok(k, 'key at the maximum');
  const [, cy1] = k!.curve as number[];
  assert.ok(Math.abs(cy1 - k!.value) < 1e-3, 'flat outgoing handle at the extreme');
});

test('one-shot spring settle (with a kink at the impact) stays within tolerance', () => {
  const m: Motion1D = {
    duration: 1,
    loop: false,
    breaks: [0.2],
    // hit the target hard at 0.2 s, then overshoot and wobble back (continuous position, kinked velocity)
    value: (t) => {
      if (t < 0.2) return 40 * EASES.quartIn(t / 0.2);
      const s = t - 0.2;
      const w = 2 * Math.PI * 4;
      return 40 + 8 * Math.exp(-0.35 * w * s) * Math.sin(w * Math.sqrt(1 - 0.35 * 0.35) * s);
    },
  };
  const keys = bake(m, { tolerance: 0.1, fps: 30 });
  assert.ok(maxErr(m, keys) <= 0.15, `err ${maxErr(m, keys)}`);
  assert.ok(keys.some((k) => Math.abs(k.time - 0.2) < 1e-9), 'impact time is a key');
});
