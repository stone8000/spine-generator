// Loads <work>/skinned.json with real mesh attachments in the official 4.3 runtime and prints world vertices of
// every mesh at a few animation times, for comparison with the Python skinning (skin_trial.py).
import { readFileSync, writeFileSync } from 'node:fs';
import { AnimationState, AnimationStateData, AtlasAttachmentLoader, MeshAttachment, Physics, Skeleton, SkeletonJson,
  Texture, TextureAtlas } from '@esotericsoftware/spine-core';

const work = process.argv[2];
const json = JSON.parse(readFileSync(`${work}/skinned.json`, 'utf8'));
const names = json.slots.map((s: any) => s.name);
const atlasText = ['page.png', 'size:1024,1536', 'filter:Linear,Linear', ...names.flatMap((n: string) => [n, 'bounds:0,0,1024,1536'])].join('\n');
const atlas = new TextureAtlas(atlasText);
class FakeTex extends Texture { setFilters() {} setWraps() {} dispose() {} }
for (const p of atlas.pages) p.setTexture(new FakeTex({ width: 1024, height: 1536 } as any));
const data = new SkeletonJson(new AtlasAttachmentLoader(atlas)).readSkeletonData(json);
const sk = new Skeleton(data);
const out: any = {};
for (const [anim, t] of [['celebrate', 0.4], ['punch', 0.433], ['jump', 0.6]] as [string, number][]) {
  sk.setupPose();
  const state = new AnimationState(new AnimationStateData(data));
  state.setAnimation(0, anim, false);
  const fps = 30, n = Math.round(t * fps);
  for (let i = 0; i <= n; i++) {
    const dt = i === 0 ? 0 : 1 / fps;
    state.update(dt); state.apply(sk); sk.update(dt);
    sk.updateWorldTransform(i === 0 ? Physics.reset : Physics.update);
  }
  out[`${anim}@${n}`] = {};
  for (const slot of sk.slots) {
    const att = slot.appliedPose.getAttachment();
    if (!(att instanceof MeshAttachment)) continue;
    const w = new Float32Array(att.worldVerticesLength);
    att.computeWorldVertices(sk, slot, 0, att.worldVerticesLength, w, 0, 2);
    out[`${anim}@${n}`][slot.data.name] = Array.from(w);
  }
}
writeFileSync(`${work}/runtime_vertices.json`, JSON.stringify(out));
console.log('meshes per frame:', Object.values(out).map((f: any) => Object.keys(f).length).join(','));
