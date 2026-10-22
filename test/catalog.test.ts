import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CATALOG } from '../src/catalog.ts';
import { PRESETS } from '../src/presets.ts';
import { compileRecipe } from '../src/recipe.ts';
import { parseSkeleton, sampleAnimation } from '../src/spineJson.ts';

test('every preset and every preset parameter has pick-list metadata', () => {
  for (const p of PRESETS) {
    const c = CATALOG.find((x) => x.id === p.id);
    assert.ok(c, `catalog entry for ${p.id}`);
    for (const param of p.params) {
      const ui = c!.params[param.name];
      assert.ok(ui, `${p.id}.${param.name} has a slider or choice`);
      if (ui.kind === 'range') assert.ok((param.default as number) >= ui.min && (param.default as number) <= ui.max, `${p.id}.${param.name} default inside range`);
      else assert.ok(ui.options.some((o) => o.value === param.default), `${p.id}.${param.name} default is an option`);
    }
    assert.deepEqual(Object.keys(c!.params).sort(), p.params.map((x) => x.name).sort(), `${p.id}: no stale parameters`);
  }
  assert.equal(CATALOG.length, PRESETS.length);
});

const symbol = JSON.parse(readFileSync(new URL('../examples/rigs/symbol.json', import.meta.url), 'utf8'));

test('speed 2× halves the length and moves events; amplitude 0.5× halves the motion', () => {
  const base = compileRecipe(symbol, { roles: { symbol: 'symbol' }, animations: [{ name: 'a', preset: 'symbol-win' }] });
  const fast = compileRecipe(symbol, { roles: { symbol: 'symbol' }, animations: [{ name: 'a', preset: 'symbol-win', timeScale: 2, amplitude: 0.5 }] });
  assert.equal(base.summary[0].duration, 1);
  assert.equal(fast.summary[0].duration, 0.5);
  assert.deepEqual(fast.summary[0].events, ['win@0.05s']);
  const peak = (json: unknown, D: number) => Math.max(...sampleAnimation(parseSkeleton(json), 'a', { rate: 480, seconds: D, loop: false }).bones.get('symbol')!.sx);
  const p0 = peak(base.json, 1) - 1;
  const p1 = peak(fast.json, 0.5) - 1;
  assert.ok(Math.abs(p1 - p0 / 2) < 0.005, `pop ${p0.toFixed(3)} → ${p1.toFixed(3)}`);
});
