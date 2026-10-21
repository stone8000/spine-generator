import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileRecipe } from '../src/recipe.ts';
import { parseSkeleton, sampleAnimation } from '../src/spineJson.ts';

const rig = JSON.parse(readFileSync(new URL('../examples/rigs/fighter.json', import.meta.url), 'utf8'));
const roles = { hip: 'hip', head: 'head', hair: ['hair1', 'hair2', 'hair3'] };

/** step (0..59) of the maximum of the fundamental, on a 2 s loop */
function peakStep(times: number[], v: number[], T = 2) {
  let sc = 0, ss = 0;
  const m = v.reduce((a, b) => a + b, 0) / v.length;
  for (let i = 0; i < v.length - 1; i++) {
    const w = (2 * Math.PI * times[i]) / T;
    sc += (v[i] - m) * Math.cos(w);
    ss += (v[i] - m) * Math.sin(w);
  }
  return Math.round(((Math.atan2(ss, sc) / (2 * Math.PI)) * 60 + 60) % 60);
}
function tipLag(json: unknown, warmUp: number) {
  const s = sampleAnimation(parseSkeleton(json), 'idle', { rate: 120, seconds: 2, warmUp });
  const head = s.bones.get('head')!;
  const h3 = s.bones.get('hair3')!;
  const tipY = h3.y.map((y, i) => y + Math.sin((h3.rot[i] * Math.PI) / 180) * 26);
  const tipX = h3.x.map((x, i) => x + Math.cos((h3.rot[i] * Math.PI) / 180) * 26);
  return {
    y: (peakStep(s.times, tipY) - peakStep(s.times, head.y) + 60) % 60,
    x: (peakStep(s.times, tipX) - peakStep(s.times, head.x) + 60) % 60,
  };
}

test("hair 'none' is rigid: the tip moves in step with the head", () => {
  const { json } = compileRecipe(rig, { roles, animations: [{ name: 'idle', preset: 'fighter-idle-bounce', params: { hair: 'none' } }] });
  const lag = tipLag(json, 0);
  assert.ok(lag.y <= 1 || lag.y >= 59, `y lag ${lag.y}`);
});

test("hair 'physics' keeps runtime constraints and the tip trails the head", () => {
  const { json } = compileRecipe(rig, { roles, animations: [{ name: 'idle', preset: 'fighter-idle-bounce', params: { hair: 'physics' } }] });
  assert.equal(json.constraints.filter((c: { type: string }) => c.type === 'physics').length, 3);
  const lag = tipLag(json, 6);
  assert.ok(lag.y >= 3 && lag.y <= 15, `y lag ${lag.y}`);
  assert.ok(lag.x >= 3 && lag.x <= 15, `x lag ${lag.x}`);
});

test("hair 'keys' bakes the physics: no constraints left, same follow-through from plain keys", () => {
  const { json, summary } = compileRecipe(rig, { roles, animations: [{ name: 'idle', preset: 'fighter-idle-bounce', params: { hair: 'keys' } }] });
  assert.equal((json.constraints ?? []).filter((c: { type: string }) => c.type === 'physics').length, 0);
  assert.equal(summary[0].bakedPhysics, 3);
  for (const b of ['hair1', 'hair2', 'hair3']) assert.ok(json.animations.idle.bones[b]?.rotate?.length >= 3, `${b} has rotate keys`);
  const physJson = compileRecipe(rig, { roles, animations: [{ name: 'idle', preset: 'fighter-idle-bounce', params: { hair: 'physics' } }] }).json;
  const baked = tipLag(json, 0);
  const live = tipLag(physJson, 6);
  assert.ok(Math.abs(baked.y - live.y) <= 1 && Math.abs(baked.x - live.x) <= 1, `baked ${JSON.stringify(baked)} vs live ${JSON.stringify(live)}`);
});
