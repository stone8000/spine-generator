/**
 * Trial, Node half: the motion kit builds the rig and the motions; the official 4.3 runtime plays them.
 *
 *   node scripts/trial/rig.ts <out-dir> [good|bad]
 *
 * Writes <out-dir>/rig.json (Spine 4.3: the autorig skeleton + half-angle helper bones at elbows and knees + the
 * preset animations) and <out-dir>/frames.json (every bone's world matrix at setup and at every 1/30 s of every
 * animation, IK, transform constraints and physics included). The Python half (research/trials/skin_trial.py)
 * skins the layers onto these matrices twice — as the autorig's full-canvas regions and as spinegen's
 * joint-spanning meshes — so the two renders differ in the attachments only.
 *
 * The helpers do not change any motion: no slot uses them in the region version, and the presets key only the
 * autorig's own bones.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { AnimationState, AnimationStateData, Physics, Skeleton } from '@esotericsoftware/spine-core';
import { buildRig } from '../../src/autorig.ts';
import { defaultJoints } from '../../src/rigTemplate.ts';
import { compileRecipe } from '../../src/recipe.ts';
import { estimateSize } from '../../src/roles.ts';
import { checkSkeleton } from '../../src/check.ts';
import { parseSkeleton } from '../../src/spineJson.ts';
import { synthLayers } from '../../test/fixtures/synthLayers.ts';

const out = process.argv[2];
const kind = (process.argv[3] ?? 'good') as 'good' | 'bad';
if (!out) throw new Error('usage: node scripts/trial/rig.ts <out-dir> [good|bad]');
mkdirSync(out, { recursive: true });

const FPS = 30;
const ANIMS = [
  { name: 'idle', preset: 'fighter-idle-bounce' },
  { name: 'punch', preset: 'punch' },
  { name: 'celebrate', preset: 'celebrate' },
  { name: 'jump', preset: 'jump' },
  { name: 'hit', preset: 'hit-react' },
];

const layers = synthLayers(kind);
const rig = buildRig(defaultJoints(), [...layers.keys()]);
if (rig.missing.length) throw new Error(`missing layers: ${rig.missing.join(', ')}`);

// Half-angle helpers: a bone at the joint, child of the upper segment, kept by a transform constraint at half the
// lower segment's local rotation (probe-half.ts: exact in 4.3.13, also when IK drives the lower bone). Constraints
// run in array order, so they go after the IK chains that move the lower bones.
const HELPERS: [string, string, string][] = [
  ['elbowF_mid', 'upperArmF', 'foreArmF'],
  ['elbowB_mid', 'upperArmB', 'foreArmB'],
  ['kneeF_mid', 'thighF', 'shinF'],
  ['kneeB_mid', 'thighB', 'shinB'],
];
const json = rig.json;
for (const [name, upper, lower] of HELPERS) {
  const u = json.bones.find((b: any) => b.name === upper);
  const l = json.bones.find((b: any) => b.name === lower);
  json.bones.push({ name, parent: upper, x: u.length, rotation: (l.rotation ?? 0) / 2, length: 20 });
  json.constraints.push({
    name: `${name}_half`, type: 'transform', bones: [name], source: lower, localSource: true, localTarget: true,
    properties: { rotate: { to: { rotate: { scale: 0.5 } } } }, mixRotate: 1,
  });
}

const { json: animated } = compileRecipe(json, { roles: rig.roles, size: estimateSize(json), animations: ANIMS });
writeFileSync(join(out, 'rig.json'), JSON.stringify(animated, null, 1));

const data = parseSkeleton(animated);
const sk = new Skeleton(data);
const pose = () => Object.fromEntries(sk.bones.map((b: any) => {
  const p = b.getAppliedPose();
  return [b.data.name, [p.a, p.b, p.c, p.d, p.worldX, p.worldY]];
}));
sk.setupPose();
sk.updateWorldTransform(Physics.reset);
const frames: any = { fps: FPS, setup: pose(), animations: {} };

for (const anim of data.animations) {
  sk.setupPose();
  const state = new AnimationState(new AnimationStateData(data));
  state.setAnimation(0, anim.name, false);
  const n = Math.round(anim.duration * FPS);
  const list = [];
  for (let i = 0; i <= n; i++) {
    const dt = i === 0 ? 0 : 1 / FPS;
    state.update(dt);
    state.apply(sk);
    sk.update(dt);
    sk.updateWorldTransform(i === 0 ? Physics.reset : Physics.update);
    list.push({ t: i / FPS, bones: pose() });
  }
  frames.animations[anim.name] = list;
}
writeFileSync(join(out, 'frames.json'), JSON.stringify(frames));

// the kit's own smoothness verdict on what it generated, for the record
const reports = checkSkeleton(animated, { loops: ['idle'] } as any);
for (const r of reports as any[]) {
  const n = (s: string) => r.issues.filter((i: any) => i.severity === s).length;
  console.log(`${r.animation}: ${n('error')} errors, ${n('warn')} warnings`);
}
console.log(`wrote ${out}/rig.json and frames.json: ${data.bones.length} bones, ${data.animations.length} animations`);
