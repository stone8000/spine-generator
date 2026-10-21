import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRig, guessJoints } from '../src/autorig.ts';
import { defaultJoints, JOINTS, toSpine } from '../src/rigTemplate.ts';
import { compileRecipe } from '../src/recipe.ts';
import { checkSeams } from '../src/seams.ts';
import { checkSkeleton } from '../src/check.ts';
import { parseSkeleton } from '../src/spineJson.ts';
import { estimateSize } from '../src/roles.ts';
import { CATALOG } from '../src/catalog.ts';
import { Skeleton, Physics } from '@esotericsoftware/spine-core';
import { synthLayers } from './fixtures/synthLayers.ts';

test('joint guessing recovers the template joints from the layers (within 25 px)', () => {
  const truth = defaultJoints();
  const guess = guessJoints(synthLayers('good'));
  for (const j of JOINTS) {
    if (j.id === 'ankleF' || j.id === 'ankleB') continue; // feet change the shin's axis; checked loosely below
    const d = Math.hypot(guess[j.id].x - truth[j.id].x, guess[j.id].y - truth[j.id].y);
    assert.ok(d <= 25, `${j.id}: off by ${d.toFixed(1)} px`);
  }
  for (const id of ['ankleF', 'ankleB'] as const) {
    const d = Math.hypot(guess[id].x - truth[id].x, guess[id].y - truth[id].y);
    assert.ok(d <= 45, `${id}: off by ${d.toFixed(1)} px`);
  }
});

test('the rig reproduces the drawing: every bone starts on its joint and IK solves to the drawn elbows and knees', () => {
  const j = defaultJoints();
  const layers = synthLayers('good');
  const { json, missing, warnings } = buildRig(j, [...layers.keys()]);
  assert.deepEqual(missing, []);
  assert.deepEqual(warnings, []);
  const sk = new Skeleton(parseSkeleton(json));
  sk.setupPose();
  sk.updateWorldTransform(Physics.none);
  const at = (bone: string) => {
    const p = sk.findBone(bone)!.getAppliedPose();
    return { x: p.worldX, y: p.worldY };
  };
  const expect: [string, keyof typeof j][] = [['hip', 'hip'], ['chest', 'waist'], ['head', 'headBase'], ['upperArmF', 'shoulderF'], ['foreArmF', 'elbowF'], ['handF', 'wristF'], ['foreArmB', 'elbowB'], ['shinF', 'kneeF'], ['shinB', 'kneeB'], ['hairF', 'hairRoot']];
  for (const [bone, joint] of expect) {
    const want = toSpine(j[joint]);
    const got = at(bone);
    assert.ok(Math.hypot(got.x - want.x, got.y - want.y) < 0.5, `${bone} at (${got.x.toFixed(1)},${got.y.toFixed(1)}) vs ${joint} (${want.x},${want.y})`);
  }
});

const ANIMS = [
  { name: 'idle', preset: 'fighter-idle-bounce' },
  { name: 'weave', preset: 'fighter-idle-weave' },
  { name: 'breathe', preset: 'breathe' },
  { name: 'punch', preset: 'punch', params: { reach: 60 } },
  { name: 'hit', preset: 'hit-react' },
];

function rigAndAnimate(kind: 'good' | 'bad') {
  const layers = synthLayers(kind);
  const rig = buildRig(defaultJoints(), [...layers.keys()]);
  const { json } = compileRecipe(rig.json, { roles: rig.roles, size: estimateSize(rig.json), animations: ANIMS });
  return { json, layers };
}

test('good art: presets play on the auto-rig with no seam gaps and pass the smoothness check', () => {
  const { json, layers } = rigAndAnimate('good');
  for (const r of checkSkeleton(json, { loops: ['idle', 'weave', 'breathe'] })) {
    assert.equal(r.issues.filter((i) => i.severity === 'error').length, 0, `${r.name} smoothness errors`);
  }
  const seams = checkSeams(json, layers);
  for (const r of seams) assert.deepEqual(r.issues.map((i) => i.joint), [], `${r.animation}: ${r.issues.map((i) => `${i.joint}@${i.time}s ${i.area}px²`).join(', ')}`);
});

test('bad art: the seam check finds the unpainted shoulder and the flat-cut neck', () => {
  const { json, layers } = rigAndAnimate('bad');
  const seams = checkSeams(json, layers);
  const found = new Set(seams.flatMap((r) => r.issues.map((i) => i.bone)));
  assert.ok(found.has('upperArmF'), `front shoulder flagged; found: ${[...found].join(', ')}`);
  assert.ok(found.has('head') || found.has('neck'), `neck flagged; found: ${[...found].join(', ')}`);
  for (const b of found) assert.ok(['upperArmF', 'head', 'neck', 'chest'].includes(b), `unexpected joint flagged: ${b}`);
});

test('legs drawn almost straight: no character preset snaps the knees (softened leg IK)', () => {
  const rig = buildRig(defaultJoints(), [...synthLayers('good').keys()]);
  assert.ok(rig.json.constraints.filter((c: { name: string; softness?: number }) => c.name.startsWith('leg') && c.softness! > 0).length === 2);
  const size = estimateSize(rig.json);
  assert.ok(Math.abs(size - 2.27) < 0.02, `size ${size}`);
  for (const c of CATALOG.filter((x) => x.category === 'character')) {
    const { json, summary } = compileRecipe(rig.json, { roles: rig.roles, size, animations: [{ name: c.name, preset: c.id }] });
    const [r] = checkSkeleton(json, { only: [c.name], loops: summary[0].loop ? [c.name] : [] });
    const legs = r.issues.filter((i) => i.severity !== 'info' && /shin|thigh/.test(i.message));
    assert.deepEqual(legs.map((i) => i.message), [], c.id);
  }
});
