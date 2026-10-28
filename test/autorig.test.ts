import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { buildRig, checkDrawOrder, checkJoints, guessJoints } from '../src/autorig.ts';
import { defaultJoints, JOINTS, toSpine } from '../src/rigTemplate.ts';
import { compileRecipe } from '../src/recipe.ts';
import { checkSeams } from '../src/seams.ts';
import { checkSkeleton } from '../src/check.ts';
import { parseSkeleton, sampleAnimation } from '../src/spineJson.ts';
import { estimateSize } from '../src/roles.ts';
import { CATALOG } from '../src/catalog.ts';
import { Skeleton, Physics } from '@esotericsoftware/spine-core';
import { synthLayers } from './fixtures/synthLayers.ts';

const near = (a: number, b: number, tol: number, what: string) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a.toFixed(3)} vs ${b} (±${tol})`);

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

/** World positions of some bones over a whole animation, played in the runtime. */
function play(json: Record<string, unknown>, anim: string, bones: string[]) {
  const data = parseSkeleton(json);
  const s = sampleAnimation(data, anim, { rate: 120, seconds: data.findAnimation(anim)!.duration });
  return Object.fromEntries(bones.map((b) => [b, s.bones.get(b)!.x.map((x, i) => ({ x, y: s.bones.get(b)!.y[i] }))]));
}

test('jump on the auto-rig: the feet stay on the ground through the crouch and the landing, so the knees bend', () => {
  const rig = buildRig(defaultJoints(), [...synthLayers('good').keys()]);
  const size = estimateSize(rig.json);
  const { json } = compileRecipe(rig.json, { roles: rig.roles, size, animations: [{ name: 'jump', preset: 'jump' }] });
  const p = play(json, 'jump', ['root', 'ik-foot-front', 'ik-foot-back', 'hip', 'shinF']);
  for (const [i, r] of p.root.entries()) {
    if (r.y >= 0) continue; // airborne: the feet travel with the body
    for (const f of ['ik-foot-front', 'ik-foot-back']) near(p[f][i].y, p[f][0].y, 0.5, `${f} planted at step ${i}`);
  }
  assert.ok(Math.min(...p.root.map((r) => r.y)) < -20 * size, 'the body crouches');
  // the knee is the shin's origin: it moves forward (bends) while the hip drops
  const lowest = p.hip.reduce((m, h, i) => (h.y < p.hip[m].y ? i : m), 0);
  assert.ok(p.shinF[lowest].x - p.shinF[0].x > 5, `knee comes forward ${(p.shinF[lowest].x - p.shinF[0].x).toFixed(1)}`);
});

test('celebrate on the auto-rig: the front glove pumps up to the top of the head, the elbow stays bent', () => {
  const j = defaultJoints();
  const rig = buildRig(j, [...synthLayers('good').keys()]);
  const { json } = compileRecipe(rig.json, { roles: rig.roles, size: estimateSize(rig.json), animations: [{ name: 'cel', preset: 'celebrate' }] });
  const p = play(json, 'cel', ['upperArmF', 'foreArmF', 'ik-fist-front']);
  const top = toSpine(j.headTop).y, chin = toSpine(j.headBase).y;
  const fy = p['ik-fist-front'].map((f) => f.y);
  assert.ok(Math.min(...fy) > chin, `the glove never drops below the chin (${Math.min(...fy).toFixed(0)} vs ${chin})`);
  assert.ok(Math.max(...fy) > top - 40, `the glove reaches the top of the head (${Math.max(...fy).toFixed(0)} vs ${top})`);
  for (let i = 0; i < fy.length; i++) {
    const s = p.upperArmF[i], e = p.foreArmF[i], f = p['ik-fist-front'][i];
    const bend = Math.abs(((Math.atan2(f.y - e.y, f.x - e.x) - Math.atan2(e.y - s.y, e.x - s.x)) * 180) / Math.PI);
    assert.ok(bend > 20, `step ${i}: elbow bent ${bend.toFixed(1)}°`);
  }
});

test('a delivery draw order (layers.json) reorders the slots; unlisted layers keep their template neighbour', () => {
  const layers = synthLayers('good');
  const names = [...layers.keys()];
  const plain = buildRig(defaultJoints(), names).json.slots.map((x: { name: string }) => x.name);
  assert.ok(plain.indexOf('fist_back') > plain.indexOf('head'), 'template: the rear glove is drawn in front of the head');
  // the bandit monkey's order: rear forearm and glove in front of the chest, pelvis in front of the legs
  const order = ['arm_back_upper', 'leg_back_upper', 'leg_back_lower', 'leg_front_upper', 'leg_front_lower', 'pelvis', 'neck', 'torso', 'arm_back_lower', 'fist_back', 'head', 'arm_front_upper', 'arm_front_lower', 'fist_front'];
  const { json, warnings } = buildRig(defaultJoints(), names, 'c', undefined, { order });
  const got = json.slots.map((x: { name: string }) => x.name);
  assert.deepEqual(got.filter((n: string) => order.includes(n)), order);
  assert.ok(got.indexOf('fist_back') > got.indexOf('torso'), 'rear glove now in front of the chest');
  if (names.includes('hair_back')) assert.equal(got[0], 'hair_back', 'unlisted hair_back stays at the back');
  if (names.includes('hair_front')) assert.equal(got[got.indexOf('head') + 1], 'hair_front', 'unlisted hair_front stays right after the head');
  assert.deepEqual(warnings, []);
  assert.equal(got.length, plain.length);
});

test('draw-order rule: gloves and hand props in front of the head (Stone, 2026-10-28)', () => {
  const names = [...synthLayers('good').keys()];
  const plain = buildRig(defaultJoints(), names);
  assert.deepEqual(plain.orderIssues, [], 'the template order follows the rule');
  // the bandit monkey's order tucks the rear glove behind the head
  const order = ['arm_back_upper', 'leg_back_upper', 'leg_back_lower', 'leg_front_upper', 'leg_front_lower', 'pelvis', 'neck', 'torso', 'arm_back_lower', 'fist_back', 'head', 'arm_front_upper', 'arm_front_lower', 'fist_front'];
  const behind = buildRig(defaultJoints(), names, 'c', undefined, { order });
  assert.equal(behind.orderIssues.length, 1);
  assert.match(behind.orderIssues[0], /fist_back 疊在頭後面/);
  assert.deepEqual(behind.warnings, [], 'kept apart from the joint warnings');
  const prop = checkDrawOrder(['weapon_sword', 'torso', 'head', 'fist_back']);
  assert.equal(prop.length, 1, 'only the prop behind the head');
  assert.match(prop[0], /^weapon_sword /);
  assert.deepEqual(checkDrawOrder(['torso', 'fist_back']), [], 'no head layer, nothing to compare');
});

test('joint sanity check: the template passes; a folded neck, a sideways head and a dropped shoulder are reported', () => {
  const layers = synthLayers('good');
  assert.deepEqual(checkJoints(defaultJoints(), layers), []);
  assert.deepEqual(checkJoints(guessJoints(layers), layers), []);
  // what the auto-guess produced on the bandit monkey (near-frontal art, wide stance)
  const j = defaultJoints();
  j.neckBase = { x: 513, y: 351 }; j.headBase = { x: 554, y: 398 }; j.headTop = { x: 742, y: 301 }; j.shoulderF = { x: 669, y: 494 }; j.waist = { x: 513, y: 708 };
  const issues = checkJoints(j, layers);
  for (const want of ['脖子會反折', '頭軸歪了', '頭頂點比頭的最上緣低', '前肩點比脖子底低']) assert.ok(issues.some((i) => i.includes(want)), `missing: ${want}\n${issues.join('\n')}`);
  assert.ok(buildRig(j, [...layers.keys()]).warnings.some((w) => w.includes('脖子會反折')), 'buildRig passes the checks on as warnings');
});

test('celebrate keeps each upper arm within `swing` of how it was drawn (a rear arm drawn hanging out to the side)', () => {
  const j = defaultJoints();
  j.elbowB = { x: j.shoulderB.x - 60, y: j.shoulderB.y + 125 };
  j.wristB = { x: j.elbowB.x + 120, y: j.elbowB.y - 60 };
  const rig = buildRig(j, [...synthLayers('good').keys()]);
  const turn = (params: Record<string, number>) => {
    const { json } = compileRecipe(rig.json, { roles: rig.roles, size: estimateSize(rig.json), animations: [{ name: 'cel', preset: 'celebrate', params }] });
    const p = play(json, 'cel', ['upperArmB', 'foreArmB']);
    const ang = (i: number) => (Math.atan2(p.foreArmB[i].y - p.upperArmB[i].y, p.foreArmB[i].x - p.upperArmB[i].x) * 180) / Math.PI;
    const drawn = (Math.atan2(-(j.elbowB.y - j.shoulderB.y), j.elbowB.x - j.shoulderB.x) * 180) / Math.PI;
    return Math.max(...p.upperArmB.map((_: unknown, i: number) => Math.abs(((ang(i) - drawn + 540) % 360) - 180)));
  };
  // the IK guard and the forearm pose add some turn of their own, so compare against the uncapped pose
  const capped = turn({ swing: 30 }), free = turn({}), small = turn({ swing: 10 });
  assert.ok(free > 75, `negative control: with the default (no cap) the upper arm turns ${free.toFixed(1)}°`);
  assert.ok(capped < free - 30, `30° cap: ${capped.toFixed(1)}° vs ${free.toFixed(1)}° uncapped`);
  assert.ok(small <= capped + 0.1, `a tighter cap never turns more: ${small.toFixed(1)}° vs ${capped.toFixed(1)}°`);
});

test('celebrate with only a tighter swing cap stays smooth: the capped arm keeps its bend and never flips mid-pump', () => {
  // joints of a near-frontal, wide-stance delivery (the bandit monkey, hand-set): capping its arms used to fold the
  // elbow shut or flip it during the pump (smoothness 20–30)
  const j = { ...defaultJoints(), ...JSON.parse(readFileSync(new URL('./fixtures/wideStanceJoints.json', import.meta.url), 'utf8')) };
  const rig = buildRig(j, [...synthLayers('good').keys()]);
  for (const swing of [10, 20, 30]) {
    const { json } = compileRecipe(rig.json, { roles: rig.roles, size: estimateSize(rig.json), animations: [{ name: 'cel', preset: 'celebrate', params: { swing } }] });
    const [r] = checkSkeleton(json, { loops: ['cel'] });
    const arms = r.issues.filter((i) => i.severity !== 'info' && /arm|fist/i.test(`${i.bone ?? ''} ${i.message}`));
    assert.deepEqual(arms.map((i) => i.message), [], `swing ${swing}`);
  }
});
