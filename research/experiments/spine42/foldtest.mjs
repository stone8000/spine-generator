import fs from "node:fs";
import * as spine from "@esotericsoftware/spine-core";
const [jsonPath, atlasPath] = process.argv.slice(2);
const atlas = new spine.TextureAtlas(fs.readFileSync(atlasPath, "utf8"));
class FakeTex extends spine.Texture { setFilters() {} setWraps() {} dispose() {} }
for (const p of atlas.pages) p.setTexture(new FakeTex({ width: p.width, height: p.height }));
const data = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(fs.readFileSync(jsonPath, "utf8"));
function folds(sk){ let f=0, tot=0; for (const slot of sk.slots){const att=slot.getAttachment(); if(!(att instanceof spine.MeshAttachment)) continue;
 const w=new Float32Array(att.worldVerticesLength); att.computeWorldVertices(slot,0,att.worldVerticesLength,w,0,2); const t=att.triangles, uv=att.regionUVs;
 for(let k=0;k<t.length;k+=3){const a=t[k]*2,b=t[k+1]*2,c=t[k+2]*2; const ar=(w[b]-w[a])*(w[c+1]-w[a+1])-(w[c]-w[a])*(w[b+1]-w[a+1]); const ua=(uv[b]-uv[a])*(uv[c+1]-uv[a+1])-(uv[c]-uv[a])*(uv[b+1]-uv[a+1]); tot++; if(Math.sign(ar)===Math.sign(ua)&&Math.abs(ar)>1e-6) f++;}} return [f,tot];}
const sk=new spine.Skeleton(data); sk.setToSetupPose(); sk.updateWorldTransform(spine.Physics.reset); console.log("setup folds", folds(sk));
sk.findBone("chest").rotation += 150; sk.updateWorldTransform(spine.Physics.reset); console.log("chest+150 folds", folds(sk));
sk.setToSetupPose(); sk.findBone("neck").rotation += 170; sk.updateWorldTransform(spine.Physics.reset); console.log("neck+170 folds", folds(sk));
