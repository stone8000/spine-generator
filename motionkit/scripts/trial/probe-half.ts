// Probe: does a 4.3 transform constraint (local source -> local target, rotate x0.5) keep a helper bone at half the
// forearm's local rotation, including when the forearm is driven by IK?
import { Skeleton, Physics } from '@esotericsoftware/spine-core';
import { parseSkeleton } from '../../src/spineJson.ts';

const json = {
  skeleton: { spine: '4.3.26' },
  bones: [
    { name: 'root' },
    { name: 'upper', parent: 'root', length: 100 },
    { name: 'fore', parent: 'upper', x: 100, rotation: 60, length: 100 },
    { name: 'mid', parent: 'upper', x: 100, rotation: 30, length: 20 },
    { name: 'target', parent: 'root', x: 150, y: 80 },
  ],
  slots: [],
  constraints: [
    { name: 'ik', type: 'ik', bones: ['upper', 'fore'], target: 'target', mix: 0 },
    { name: 'half', type: 'transform', bones: ['mid'], source: 'fore', localSource: true, localTarget: true,
      properties: { rotate: { to: { rotate: { scale: 0.5 } } } }, mixRotate: 1 },
  ],
  skins: [{ name: 'default', attachments: {} }],
  animations: {},
};
const sk = new Skeleton(parseSkeleton(json));
const fore = sk.findBone('fore')!, mid = sk.findBone('mid')!, upper = sk.findBone('upper')!;
for (const r of [60, 0, 90, 150, -40]) {
  sk.setupPose();
  fore.pose.rotation = r;
  sk.updateWorldTransform(Physics.none);
  const wr = (b: any) => Math.atan2(b.getAppliedPose().c, b.getAppliedPose().a) * 180 / Math.PI;
  console.log(`fore local ${r}: mid world-upper = ${(wr(mid) - wr(upper)).toFixed(2)} (want ${(r / 2).toFixed(2)})`);
}
// now with IK on
const ik = sk.constraints.find((c: any) => c.data.name === 'ik') as any;
ik.data.setupPose.mix = 1;
for (const [tx, ty] of [[150, 80], [120, 120], [60, 60]]) {
  sk.setupPose();
  sk.findBone('target')!.pose.x = tx; sk.findBone('target')!.pose.y = ty;
  sk.updateWorldTransform(Physics.none);
  const wr = (b: any) => Math.atan2(b.getAppliedPose().c, b.getAppliedPose().a) * 180 / Math.PI;
  console.log(`IK target (${tx},${ty}): fore-upper=${(wr(fore) - wr(upper)).toFixed(2)} mid-upper=${(wr(mid) - wr(upper)).toFixed(2)}`);
}
