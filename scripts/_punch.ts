import { readFileSync } from 'node:fs';
import { Skeleton, AnimationState, AnimationStateData, Physics } from '@esotericsoftware/spine-core';
import { parseSkeleton } from '../src/spineJson.ts';
const json = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const anim = process.argv[3] ?? 'punch';
const data = parseSkeleton(json); const sk = new Skeleton(data); const st = new AnimationState(new AnimationStateData(data));
const D = data.findAnimation(anim)!.duration;
const at = (t: number) => { sk.setupPose(); st.setAnimation(0, anim, false); st.update(t); st.apply(sk); sk.updateWorldTransform(Physics.none); };
const P = (n: string) => { const p = sk.findBone(n)!.getAppliedPose(); return { x: p.worldX, y: p.worldY }; };
at(0); const s0 = P('upperArmF'), f0 = P('ik-fist-front'), h0 = P('hip');
const L = sk.findBone('upperArmF')!.data.length + sk.findBone('foreArmF')!.data.length;
console.log('arm L', L.toFixed(0), 'rest ext', (Math.hypot(f0.x - s0.x, f0.y - s0.y) / L).toFixed(2));
for (const t of [0.2, 0.27, 0.3, 0.337, 0.4, 0.5, 0.6]) {
  at(t); const s = P('upperArmF'), f = P('ik-fist-front'), h = P('hip'), w = P('handF');
  console.log(t.toFixed(3), 'fistTarget dx', (f.x - f0.x).toFixed(0), 'hand dx', (w.x - f0.x).toFixed(0), 'shoulder dx', (s.x - s0.x).toFixed(0), 'hip dx', (h.x - h0.x).toFixed(0), 'ext', (Math.hypot(w.x - s.x, w.y - s.y) / L).toFixed(2));
}
