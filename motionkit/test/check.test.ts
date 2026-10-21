import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { checkSkeleton } from '../src/check.ts';
import { compileRecipe, type Recipe } from '../src/recipe.ts';

const rig = JSON.parse(readFileSync(new URL('../examples/rigs/fighter.json', import.meta.url), 'utf8'));
const roles = { hip: 'hip', head: 'head', fistFront: 'ik-fist-front', fistBack: 'ik-fist-back', hair: ['hair1', 'hair2', 'hair3'], chest: 'chest' };

test('negative control: a sloppy hand-made idle is caught', () => {
  const bad = JSON.parse(JSON.stringify(rig));
  bad.animations = {
    idle_bad: {
      bones: {
        hip: {
          // linear ramp, then a long hold, and the loop does not return to its start value
          translatey: [{ value: 0 }, { time: 0.6, value: 12 }, { time: 1.4, value: 12, curve: [1.6, 12, 1.8, 3] }, { time: 2, value: 3 }],
        },
        head: {
          // returns to the start value but arrives fast while it left slowly (speed seam), plus a direction kink at 1 s
          rotate: [
            { value: 0, curve: [0.33, 0, 0.66, 4] }, // leaves flat, arrives at 1 s still rising (+11.8°/s)
            { time: 1, value: 8, curve: [1.33, 0, 1.66, 3] }, // ...then leaves falling at -24°/s: direction kink; arrives at 2 s at -8.8°/s vs flat start: speed seam
            { time: 2, value: 0 },
          ],
        },
      },
    },
  };
  const [r] = checkSkeleton(bad, { loops: ['idle_bad'] });
  const codes = new Set(r.issues.map((i) => i.code));
  for (const c of ['linear', 'hold', 'seam-value', 'kink', 'seam-slope', 'world-seam']) assert.ok(codes.has(c), `expected ${c}; got ${[...codes].join(', ')}`);
  assert.ok(r.score < 50, `score ${r.score}`);
});

test('generated loops pass with no errors and no warnings', () => {
  const recipe: Recipe = {
    roles,
    animations: [
      { name: 'idle', preset: 'fighter-idle-bounce' },
      { name: 'idle_weave', preset: 'fighter-idle-weave', facing: -1 },
      { name: 'idle_breathe', preset: 'breathe' },
      { name: 'loop_hair_sway', preset: 'chain-sway', params: { mode: 'keys' } },
    ],
  };
  recipe.roles.chain = ['hair1', 'hair2', 'hair3'];
  const { json } = compileRecipe(rig, recipe);
  for (const r of checkSkeleton(json)) {
    assert.ok(r.loop, `${r.name} detected as loop`);
    const bad = r.issues.filter((i) => i.severity !== 'info');
    assert.equal(bad.length, 0, `${r.name}: ${bad.map((i) => `${i.code} ${i.bone ?? ''} ${i.timeline ?? ''}`).join('; ')}`);
  }
});
