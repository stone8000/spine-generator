/**
 * End-to-end: recipe → keys → real Spine 4.3 runtime playback → measure on-screen motion → compare with the targets.
 * The fighter rig is deliberately awkward (hip rotated 90°, head three levels deep, fist IK targets under root).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileRecipe, type Recipe } from '../src/recipe.ts';
import { parseSkeleton, sampleAnimation } from '../src/spineJson.ts';

const rig = JSON.parse(readFileSync(new URL('../examples/rigs/fighter.json', import.meta.url), 'utf8'));
const roles = { hip: 'hip', head: 'head', fistFront: 'ik-fist-front', fistBack: 'ik-fist-back', hair: ['hair1', 'hair2', 'hair3'] };

/** Fit offset + A cos(2π(t - peak)/T): returns amplitude and time of the maximum. */
function fitCos(times: number[], v: number[], T: number) {
  let sc = 0, ss = 0, mean = 0;
  for (const x of v) mean += x / v.length;
  for (let i = 0; i < v.length - 1; i++) {
    const w = (2 * Math.PI * times[i]) / T;
    sc += (v[i] - mean) * Math.cos(w);
    ss += (v[i] - mean) * Math.sin(w);
  }
  const n = v.length - 1;
  const a = (2 * sc) / n, b = (2 * ss) / n;
  const amp = Math.hypot(a, b);
  const peak = ((Math.atan2(b, a) / (2 * Math.PI)) * T + T) % T;
  return { amp, peak };
}
const stepOf = (t: number, T = 2) => Math.round((t / T) * 60) % 60;
const stepDiff = (a: number, b: number) => Math.min((a - b + 60) % 60, (b - a + 60) % 60);

function measure(json: unknown, anim: string) {
  const data = parseSkeleton(json);
  const s = sampleAnimation(data, anim, { rate: 120, seconds: 2 });
  const rel = (bone: string) => {
    const b = s.bones.get(bone)!;
    return { x: b.x.map((x) => x - b.x[0]), y: b.y.map((y) => y - b.y[0]), rot: b.rot, raw: b };
  };
  return { s, rel };
}

test('fighter-idle-bounce reproduces the Iron Jaw screen motion on an awkward rig (facing right)', () => {
  const recipe: Recipe = { roles, animations: [{ name: 'idle', preset: 'fighter-idle-bounce' }] };
  const { json } = compileRecipe(rig, recipe);
  const { s, rel } = measure(json, 'idle');
  const checks: [string, 'x' | 'y' | 'rot', number, number][] = [
    // bone, axis, target amplitude, step of the MAXIMUM of that axis (Spine axes: y up, rotation CCW)
    ['hip', 'y', 9.0, 30], // lowest at step 0
    ['hip', 'x', 4.2, 3], // most forward (+x) at step 3
    ['hip', 'rot', 1.1, 33], // lean forward = clockwise (negative) at step 3 → max at 33
    ['head', 'y', 10.5, 30],
    ['head', 'x', 8.8, 2],
    ['head', 'rot', 4.3, 29],
    ['ik-fist-front', 'x', 15.0, 40],
    ['ik-fist-front', 'y', 15.8, 34],
    ['ik-fist-back', 'x', 12.7, 4],
    ['ik-fist-back', 'y', 15.4, 9],
  ];
  for (const [bone, axis, amp, step] of checks) {
    const r = rel(bone);
    const series = axis === 'rot' ? r.rot : r[axis];
    const f = fitCos(s.times, series, 2);
    assert.ok(Math.abs(f.amp - amp) / amp < 0.03, `${bone}.${axis}: amplitude ${f.amp.toFixed(2)} vs ${amp}`);
    assert.ok(stepDiff(stepOf(f.peak), step) <= 1, `${bone}.${axis}: peak step ${stepOf(f.peak)} vs ${step}`);
  }
});

test('the same preset on a mirrored rig facing left moves forward = screen left', () => {
  const mirrored = JSON.parse(JSON.stringify(rig));
  mirrored.bones[0].scaleX = -1; // root mirrored: every local rotation now turns the other way on screen
  const recipe: Recipe = { facing: -1, roles, animations: [{ name: 'idle', preset: 'fighter-idle-bounce' }] };
  const { json } = compileRecipe(mirrored, recipe);
  const { s, rel } = measure(json, 'idle');
  const hipX = fitCos(s.times, rel('hip').x, 2);
  assert.ok(Math.abs(hipX.amp - 4.2) < 0.15, `amp ${hipX.amp}`);
  // forward (screen left, -x) at step 3 → +x maximum at step 33
  assert.ok(stepDiff(stepOf(hipX.peak), 33) <= 1, `peak ${stepOf(hipX.peak)}`);
  const headRot = fitCos(s.times, rel('head').rot, 2);
  // lean forward for a left-facing character = counter-clockwise (positive) at step 59
  assert.ok(stepDiff(stepOf(headRot.peak), 59) <= 1, `head rot peak ${stepOf(headRot.peak)}`);
  assert.ok(Math.abs(headRot.amp - 4.3) < 0.15, `head rot amp ${headRot.amp}`);
});

test('loops close: world pose at the end equals the start for every bone', () => {
  const recipe: Recipe = { roles, animations: [{ name: 'idle', preset: 'fighter-idle-bounce' }, { name: 'weave', preset: 'fighter-idle-weave', facing: -1 }] };
  const { json } = compileRecipe(rig, recipe);
  for (const anim of ['idle', 'weave']) {
    const { s } = measure(json, anim);
    for (const [name, b] of s.bones) {
      const n = b.x.length - 1;
      const d = Math.hypot(b.x[n] - b.x[0], b.y[n] - b.y[0]);
      assert.ok(d < 0.05, `${anim}/${name}: seam ${d}`);
    }
  }
});
