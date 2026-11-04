/**
 * The stand34 template end to end on the synthetic test character (scripts/trial/make_stand34_test.py): the one-line
 * character pipeline picks the template from the layer names, every motion is smooth with no seams or flips, swaps
 * end on the idle drawings, and the throw lands while the far arm is out.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { decodePngAlpha } from '../src/png.ts';
import { buildCharacter, characterRows, layerName } from '../src/character.ts';
import { isStand34, STAND34_ANIMS } from '../src/stand34.ts';
import { parseSkeleton, sampleAnimation } from '../src/spineJson.ts';

const dir = new URL('../examples/characters/stand34-test/layers/', import.meta.url);
const alphas = new Map(readdirSync(dir).filter((f) => f.endsWith('.png')).map((f) => [layerName(f), decodePngAlpha(new Uint8Array(readFileSync(new URL(f, dir))))]));
const r = buildCharacter(alphas, { name: 'stand34-test' });

test('stand34: detected from the layer names, every motion built', () => {
  assert.ok(isStand34(alphas.keys()));
  assert.equal(r.template, 'stand34');
  assert.deepEqual(r.summary.map((s) => s.name), STAND34_ANIMS.map((a) => a.name));
  assert.deepEqual(r.warnings, []);
});

test('stand34: every motion smooth (≥ 95), no seams, no flips', () => {
  for (const x of characterRows(r)) {
    assert.ok(x.score >= 95, `${x.name} score ${x.score}: ${x.problems.join(' / ')}`);
    assert.deepEqual(x.seams, [], `${x.name} seams`);
    assert.deepEqual(x.flips, [], `${x.name} flips`);
  }
});

test('stand34: once-motions end on the idle drawings (no stale face, no hat left in the hand)', () => {
  const rest: Record<string, string> = { head: 'head_idle', hand_r: 'hand_r_pinch', hand_l: 'hand_l_hip', hat: 'hat' };
  for (const name of ['idle_fidget', 'gaze', 'react_small', 'gaze_dud', 'win_big_end']) {
    const slots = r.json.animations[name].slots ?? {};
    for (const [slot, want] of Object.entries(rest)) {
      const keys = slots[slot]?.attachment;
      if (keys?.length) assert.equal(keys[keys.length - 1].name, want, `${name}/${slot}`);
    }
  }
});

test('stand34: gaze throws (event) while the far hand is out toward the board', () => {
  const ev = r.json.animations.gaze.events.find((e: { name: string }) => e.name === 'throw');
  assert.ok(ev, 'throw event');
  const data = parseSkeleton(r.json);
  const s = sampleAnimation(data, 'gaze', { rate: 120, seconds: data.findAnimation('gaze')!.duration });
  const x = s.bones.get('ik-fist-back')!.x;
  const at = Math.round(ev.time * 120);
  assert.ok(x[at] < x[0] - 100, `far hand ${x[0].toFixed(0)} → ${x[at].toFixed(0)} at the throw`);
});
