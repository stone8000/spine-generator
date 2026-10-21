// Run: node --test reelMotion.test.ts   (Node 22.18+ / 23.6+ strips the types natively)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { IRON_JAW, planReels, reelPosition, reelVelocity, useBlurredSymbols } from './reelMotion.ts';

const FPS = 29.74; // capture frame rate of the analysed video
const ROW_PX = 98; // one symbol row in the capture
const plans = planReels(5);

test('reels land 0.22 s apart, starting 0.60 s after the press', () => {
  assert.deepEqual(plans.map((p) => +p.landTime.toFixed(2)), [0.6, 0.82, 1.04, 1.26, 1.48]);
});

test('each reel stops exactly on a whole row', () => {
  for (const p of plans) {
    assert.equal(Number.isInteger(p.distance), true);
    assert.ok(Math.abs(reelPosition(p.restTime + 0.01, p) - p.distance) < 1e-12);
  }
});

test('cruise speed is nudged by less than 6 % to land on a row', () => {
  for (const p of plans) assert.ok(Math.abs(p.speed / IRON_JAW.speed - 1) < 0.06, `reel ${p.index} speed ${p.speed}`);
});

test('pull-up reaches -0.17 rows and overshoot reaches +0.11 rows', () => {
  const p = plans[0];
  let min = 0;
  let max = -Infinity;
  for (let t = 0; t <= p.restTime + 0.05; t += 0.0005) {
    const x = reelPosition(t, p);
    if (t < 0.3) min = Math.min(min, x);
    if (t > p.landTime - 0.05) max = Math.max(max, x - p.distance);
  }
  assert.ok(Math.abs(min + IRON_JAW.windUpDistance) < 1e-3, `min ${min}`);
  assert.ok(Math.abs(max - IRON_JAW.overshoot) < 1e-3, `overshoot ${max}`);
});

test('position and velocity are continuous at every phase boundary', () => {
  const eps = 1e-7;
  for (const p of plans) {
    const cuts = [IRON_JAW.windUpTime, IRON_JAW.windUpTime + IRON_JAW.accelTime, p.landTime - IRON_JAW.decelTime, p.landTime, p.restTime];
    for (const c of cuts) {
      assert.ok(Math.abs(reelPosition(c - eps, p) - reelPosition(c + eps, p)) < 1e-5, `pos jump reel ${p.index} at ${c}`);
      assert.ok(Math.abs(reelVelocity(c - eps, p) - reelVelocity(c + eps, p)) < 1e-3, `velocity jump reel ${p.index} at ${c}`);
    }
  }
});

test('velocity matches the numeric derivative of position', () => {
  const p = plans[2];
  for (let t = 0.005; t < p.restTime; t += 0.013) {
    const h = 1e-6;
    const num = (reelPosition(t + h, p) - reelPosition(t - h, p)) / (2 * h);
    assert.ok(Math.abs(num - reelVelocity(t, p)) < 1e-3, `t=${t}`);
  }
});

test('blurred textures only while fast; sharp at rest and during the bounce', () => {
  const p = plans[0];
  assert.equal(useBlurredSymbols(0.05, p), false);
  assert.equal(useBlurredSymbols(0.4, p), true);
  assert.equal(useBlurredSymbols(p.landTime + 0.02, p), false);
});

// Measured offsets of reel 1 around its landing (capture frames, px relative to the rest row), spin at 21.25 s
const MEASURED_STOP = [-223.73, -135.95, -59.09, -12.3, 4.33, 10.93, 7.2, 3.34, 0.84, -0.23, -0.28, 0.1]; // peak = index 5
const MEASURED_START = [0, -8.04, -14.2, -16.75, -9.1, 27.22, 80.83];

test('model tracks the measured landing within 6 px for the last 3 frames before and 4 after the peak', () => {
  const p = plans[0];
  const rows: string[] = [];
  let worst = 0;
  MEASURED_STOP.forEach((m, i) => {
    const t = p.landTime + (i - 5) / FPS;
    const model = (reelPosition(t, p) - p.distance) * ROW_PX;
    rows.push(`frame ${i - 5 >= 0 ? '+' : ''}${i - 5}: measured ${m.toFixed(1)} px, model ${model.toFixed(1)} px`);
    if (i >= 2 && i <= 9) worst = Math.max(worst, Math.abs(model - m));
  });
  console.log('landing\n  ' + rows.join('\n  '));
  assert.ok(worst < 6, `worst ${worst.toFixed(2)} px`);
});

test('model tracks the measured pull-up and launch within 6 px', () => {
  const p = plans[0];
  const rows: string[] = [];
  let worst = 0;
  MEASURED_START.forEach((m, i) => {
    const model = reelPosition(i / FPS, p) * ROW_PX;
    rows.push(`frame ${i}: measured ${m.toFixed(1)} px, model ${model.toFixed(1)} px`);
    worst = Math.max(worst, Math.abs(model - m));
  });
  console.log('start\n  ' + rows.join('\n  '));
  assert.ok(worst < 6, `worst ${worst.toFixed(2)} px`);
});
