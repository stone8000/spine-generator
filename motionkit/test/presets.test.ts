/**
 * Every preset, end to end: compile on a test rig, play it in the real Spine 4.3 runtime, check smoothness,
 * and verify the motion does what the preset promises.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileRecipe, type Recipe, type RecipeAnimation } from '../src/recipe.ts';
import { PRESETS } from '../src/presets.ts';
import { checkSkeleton } from '../src/check.ts';
import { parseSkeleton, sampleAnimation } from '../src/spineJson.ts';

const fighter = JSON.parse(readFileSync(new URL('../examples/rigs/fighter.json', import.meta.url), 'utf8'));
const symbol = JSON.parse(readFileSync(new URL('../examples/rigs/symbol.json', import.meta.url), 'utf8'));

const fighterRoles = {
  hip: 'hip', chest: 'chest', head: 'head', root: 'root', body: 'hip',
  fistFront: 'ik-fist-front', fistBack: 'ik-fist-back', fist: 'ik-fist-front', fists: ['ik-fist-front', 'ik-fist-back'],
  shoulderBack: 'upperArmB', shoulders: ['upperArmF', 'upperArmB'],
  hair: ['hair1', 'hair2', 'hair3'], chain: ['hair1', 'hair2', 'hair3'],
};
const symbolRoles = { symbol: 'symbol', title: 'title' };
const SYMBOL_PRESETS = new Set(['symbol-land', 'symbol-win', 'symbol-win-loop', 'symbol-idle', 'tease-shake', 'text-slam']);

function build(id: string, params: Record<string, unknown> = {}) {
  const isSymbol = SYMBOL_PRESETS.has(id);
  const anim: RecipeAnimation = { name: id.replace(/-/g, '_'), preset: id, params };
  const recipe: Recipe = { roles: isSymbol ? symbolRoles : fighterRoles, animations: [anim] };
  const { json, summary } = compileRecipe(isSymbol ? symbol : fighter, recipe);
  const data = parseSkeleton(json);
  const s = sampleAnimation(data, anim.name, { rate: 240, seconds: summary[0].duration, loop: summary[0].loop });
  return { json, summary: summary[0], s, name: anim.name };
}

for (const p of PRESETS) {
  test(`${p.id}: compiles, plays in the runtime, passes the smoothness check`, () => {
    const { json, summary, name } = build(p.id);
    assert.ok(summary.timelines > 0, 'has timelines');
    assert.equal(summary.loop, p.kind === 'loop');
    const [r] = checkSkeleton(json, { loops: p.kind === 'loop' ? [name] : [] });
    const errors = r.issues.filter((i) => i.severity === 'error');
    assert.equal(errors.length, 0, errors.map((e) => `${e.code} ${e.bone ?? ''}: ${e.message}`).join('\n'));
    if (p.kind === 'loop') {
      const warns = r.issues.filter((i) => i.severity === 'warn');
      assert.equal(warns.length, 0, warns.map((e) => `${e.code} ${e.bone ?? ''}: ${e.message}`).join('\n'));
    }
  });
}

const near = (a: number, b: number, tol: number, what: string) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a.toFixed(3)} vs ${b} (±${tol})`);
const idxAt = (t: number) => Math.round(t * 240);

test('punch that fits the arm: winds up, hits at 0.337 s, overshoots 6 %, returns home', () => {
  const { s, summary } = build('punch', { reach: 25, windup: 20 });
  assert.deepEqual(summary.events, ['swing@0.27s', 'impact@0.337s']);
  const x = s.bones.get('ik-fist-front')!.x.map((v, i, a) => v - a[0]);
  near(Math.min(...x.slice(0, idxAt(0.3))), -20, 0.5, 'wind-up');
  near(x[idxAt(0.337)], 25, 0.5, 'reach at impact');
  near(Math.max(...x), 25 * 1.06, 0.5, 'overshoot peak');
  near(x[x.length - 1], 0, 0.2, 'back home');
});

/** Elbow angle (forearm vs upper arm, world) for a preset, with the IK reach guard on or off. */
function elbow(id: string, params: Record<string, unknown>, ikGuard: boolean, side = 'F') {
  const recipe: Recipe = { ikGuard, roles: fighterRoles, animations: [{ name: 'a', preset: id, params }] };
  const { json, summary } = compileRecipe(fighter, recipe);
  const s = sampleAnimation(parseSkeleton(json), 'a', { rate: 240, seconds: summary[0].duration, loop: summary[0].loop });
  const up = s.bones.get(`upperArm${side}`)!.rot;
  const fo = s.bones.get(`foreArm${side}`)!.rot;
  const a = fo.map((f, i) => ((f - up[i] + 540) % 360) - 180);
  return { minBend: Math.min(...a.map(Math.abs)), maxStep: Math.max(...a.slice(1).map((v, i) => Math.abs(v - a[i]))) };
}

test('punch longer than the arm: without the guard the elbow locks straight; with it, it stays bent and moves 3× smoother', () => {
  const off = elbow('punch', { reach: 130 }, false);
  const on = elbow('punch', { reach: 130 }, true);
  assert.ok(off.minBend < 1, `no guard locks straight (min bend ${off.minBend.toFixed(1)}°)`);
  assert.ok(on.minBend > 20, `guard keeps the elbow bent (min ${on.minBend.toFixed(1)}°)`);
  assert.ok(on.maxStep * 3 < off.maxStep, `guard step ${on.maxStep.toFixed(1)}° vs ${off.maxStep.toFixed(1)}°`);
});

test('celebrate lifts the fists past reach: without the guard both arms lock straight; with it they stay bent', () => {
  for (const side of ['F', 'B']) {
    const off = elbow('celebrate', { lift: 200 }, false, side);
    const on = elbow('celebrate', { lift: 200 }, true, side);
    assert.ok(off.minBend < 1, `${side}: no guard locks straight`);
    assert.ok(on.minBend > 20, `${side}: guard keeps it bent (${on.minBend.toFixed(1)}°)`);
    assert.ok(on.maxStep < 1, `${side}: smooth (${on.maxStep.toFixed(2)}°/sample)`);
  }
});

/** Shoulder -> fist-target distance per sample of a punch, with the default guard, none, or per-chain thresholds. */
function reachTrace(recipeExtra: Partial<Recipe>) {
  const recipe: Recipe = { roles: fighterRoles, animations: [{ name: 'a', preset: 'punch', params: { reach: 130 } }], ...recipeExtra };
  const { json, summary } = compileRecipe(fighter, recipe);
  const s = sampleAnimation(parseSkeleton(json), 'a', { rate: 240, seconds: summary[0].duration, loop: false });
  const sh = s.bones.get('upperArmF')!, fi = s.bones.get('ik-fist-front')!;
  return sh.x.map((x, i) => Math.hypot(fi.x[i] - x, fi.y[i] - sh.y[i]));
}

test('ikGuardChains: per-target knee / max replace 85 % / 95 %; identity up to the knee; false turns one chain off', () => {
  const off = reachTrace({ ikGuard: false });
  const d0 = off[0];
  const own = reachTrace({ ikGuardChains: { 'ik-fist-front': { knee: d0, max: d0 + 10 } } });
  assert.ok(Math.abs(own[0] - d0) < 1e-6, `frame 0 untouched (${own[0]} vs ${d0})`);
  assert.ok(Math.max(...own) <= d0 + 10 + 1e-6, `never past max (${Math.max(...own).toFixed(2)} vs ${(d0 + 10).toFixed(2)})`);
  assert.ok(Math.max(...off) > d0 + 30, 'the punch itself goes well past it');
  const none = reachTrace({ ikGuardChains: { 'ik-fist-front': false } });
  assert.ok(none.every((v, i) => Math.abs(v - off[i]) < 1e-6), 'false = no guard on that chain');
});

test('boneAmplitude scales only that bone\'s tracks', () => {
  const run = (boneAmplitude?: Record<string, number>) => {
    const recipe: Recipe = { ikGuard: false, roles: fighterRoles, animations: [{ name: 'a', preset: 'celebrate', boneAmplitude }] }; // guard off: it is not linear
    const { json, summary } = compileRecipe(fighter, recipe);
    const s = sampleAnimation(parseSkeleton(json), 'a', { rate: 240, seconds: summary[0].duration, loop: true });
    return { front: s.bones.get('ik-fist-front')!.y, back: s.bones.get('ik-fist-back')!.y, hip: s.bones.get('hip')!.y };
  };
  const full = run(), half = run({ 'ik-fist-front': 0.5 }), rest = run({ 'ik-fist-front': 0 });
  const err = Math.max(...full.front.map((v, i) => Math.abs((half.front[i] - rest.front[i]) * 2 - (v - rest.front[i]))));
  assert.ok(err < 0.2, `front fist motion halves (worst ${err.toFixed(3)})`);
  assert.ok(Math.max(...full.front) - Math.min(...full.front) > 40, 'the fist does move');
  assert.deepEqual(half.back, full.back, 'other fist unchanged');
  assert.deepEqual(half.hip, full.hip, 'hip unchanged');
});

test('jump: crouches, peaks at the height on frame 13–14, lands at 0.8 s, settles', () => {
  const { s, summary } = build('jump', { height: 120, crouch: 25 });
  assert.deepEqual(summary.events, ['takeoff@0.18s', 'land@0.8s']);
  const y = s.bones.get('root')!.y.map((v, i, a) => v - a[0]);
  near(Math.min(...y.slice(0, idxAt(0.3))), -25, 0.5, 'crouch');
  const top = Math.max(...y);
  near(top, 120, 0.5, 'height');
  near(y.indexOf(top) / 240, 0.45, 0.034, 'apex time');
  near(y[idxAt(0.8)], 0, 0.5, 'touch-down');
  near(y[y.length - 1], 0, 0.5, 'settled');
  const sy = s.bones.get('hip')!;
  assert.ok(Math.min(...sy.sx, ...sy.sy) < 0.95, 'squash happens');
});

test('symbol-land: dips 0.11 symbol below the rest row after landing, then settles', () => {
  const { s } = build('symbol-land', { symbolSize: 98 });
  const y = s.bones.get('symbol')!.y.map((v, i, a) => v - a[a.length - 1]);
  near(Math.min(...y.slice(idxAt(0.12))), -0.11 * 98, 0.4, 'dip');
  near(y[y.length - 1], 0, 0.1, 'rest');
});

test('symbol-win: pops to 122 % at 0.1 s and springs back to 100 %', () => {
  const { s } = build('symbol-win');
  const sx = s.bones.get('symbol')!.sx;
  near(Math.max(...sx), 1.22, 0.005, 'pop');
  near(sx.indexOf(Math.max(...sx)) / 240, 0.1, 0.02, 'pop time');
  near(sx[sx.length - 1], 1, 0.01, 'settle');
});

test('text-slam: starts at 240 %, slams to 92 % at 0.1 s, settles at 100 %', () => {
  const { s } = build('text-slam');
  const sx = s.bones.get('title')!.sx;
  near(sx[0], 2.4, 0.01, 'start');
  near(Math.min(...sx), 0.92, 0.005, 'slam');
  near(sx[sx.length - 1], 1, 0.01, 'settle');
});
