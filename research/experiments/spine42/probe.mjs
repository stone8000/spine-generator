// Headless geometric QA probe with official spine-core 4.2 (evaluation use).
import fs from "node:fs";
import * as spine from "@esotericsoftware/spine-core";

const [jsonPath, atlasPath] = process.argv.slice(2);
const atlas = new spine.TextureAtlas(fs.readFileSync(atlasPath, "utf8"));
class FakeTex extends spine.Texture { setFilters() {} setWraps() {} dispose() {} }
for (const p of atlas.pages) p.setTexture(new FakeTex({ width: p.width, height: p.height }));
const json = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas));
const data = json.readSkeletonData(fs.readFileSync(jsonPath, "utf8"));
console.log("version", data.version, "bones", data.bones.length, "phys", data.physicsConstraints.length, "ik", data.ikConstraints.length,
  "anims", data.animations.map(a => a.name + ":" + a.duration.toFixed(3)).join(","));

function run(anim, fps, physicsMode) {
  const sk = new spine.Skeleton(data);
  const st = new spine.AnimationState(new spine.AnimationStateData(data));
  st.setAnimation(0, anim, false);
  sk.setToSetupPose();
  sk.updateWorldTransform(spine.Physics.reset);
  const dt = 1 / fps, samples = {};
  const T = data.findAnimation(anim).duration;
  let flips = 0, minArea = Infinity;
  for (let i = 0; i * dt <= T + 1e-9; i++) {
    const d = i === 0 ? 0 : dt;
    st.update(d); st.apply(sk); sk.update(d); sk.updateWorldTransform(physicsMode);
    const t = +(i * dt).toFixed(4);
    // sample physics bone tips at common times (multiples of 1/30)
    if (Math.abs(t * 30 - Math.round(t * 30)) < 1e-3) {
      samples[Math.round(t * 30)] = data.physicsConstraints.map(pc => {
        const b = sk.findBone(pc.bone.name); return [b.worldX + b.a * b.data.length, b.worldY + b.c * b.data.length];
      });
    }
    // triangle fold check: signed area sign vs setup
    for (const slot of sk.slots) {
      const att = slot.getAttachment();
      if (!(att instanceof spine.MeshAttachment)) continue;
      const w = new Float32Array(att.worldVerticesLength);
      att.computeWorldVertices(slot, 0, att.worldVerticesLength, w, 0, 2);
      const tri = att.triangles;
      for (let k = 0; k < tri.length; k += 3) {
        const [a, b, c] = [tri[k] * 2, tri[k + 1] * 2, tri[k + 2] * 2];
        const area = (w[b] - w[a]) * (w[c + 1] - w[a + 1]) - (w[c] - w[a]) * (w[b + 1] - w[a + 1]);
        const uv = att.regionUVs; // setup orientation from uvs (y down in uv space)
        const uarea = (uv[b] - uv[a]) * (uv[c + 1] - uv[a + 1]) - (uv[c] - uv[a]) * (uv[b + 1] - uv[a + 1]);
        if (Math.sign(area) === Math.sign(uarea) && Math.abs(area) > 1e-6) flips++; // uv y is flipped vs world y
        minArea = Math.min(minArea, Math.abs(area));
      }
    }
  }
  return { samples, flips };
}
for (const anim of ["react", "reactBig"]) {
  const ref = run(anim, 240, spine.Physics.update);
  for (const fps of [30, 60, 144]) {
    const r = run(anim, fps, spine.Physics.update);
    let maxd = 0;
    for (const k of Object.keys(ref.samples)) if (r.samples[k]) r.samples[k].forEach((p, j) => {
      const q = ref.samples[k][j]; maxd = Math.max(maxd, Math.hypot(p[0] - q[0], p[1] - q[1]));
    });
    const r2 = run(anim, fps, spine.Physics.update);
    let rep = 0; for (const k of Object.keys(r.samples)) r.samples[k].forEach((p, j) => { rep = Math.max(rep, Math.hypot(p[0] - r2.samples[k][j][0], p[1] - r2.samples[k][j][1])); });
    console.log(anim, "fps", fps, "max physics-tip deviation vs 240fps(px)", maxd.toFixed(2), "repeat-run diff", rep, "folded-tri-frames", r.flips);
  }
}
