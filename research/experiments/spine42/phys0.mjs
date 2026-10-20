import * as spine from "@esotericsoftware/spine-core";
function mk(len){
 const json={skeleton:{spine:"4.2.43"},bones:[{name:"root"},{name:"b",parent:"root",length:len,rotation:-90}],
  physics:[{name:"p",order:0,bone:"b",rotate:1,inertia:0.5,strength:100,damping:0.85}],slots:[],skins:[{name:"default",attachments:{}}],animations:{}};
 const data=new spine.SkeletonJson({newRegionAttachment(){},newMeshAttachment(){},newBoundingBoxAttachment(){},newPathAttachment(){},newPointAttachment(){},newClippingAttachment(){}}).readSkeletonData(json);
 const sk=new spine.Skeleton(data); sk.updateWorldTransform(spine.Physics.reset);
 const out=[]; for(let i=1;i<=6;i++){ sk.findBone("root").x = i*1; sk.update(1/60); sk.updateWorldTransform(spine.Physics.update); out.push(sk.findBone("b").getWorldRotationX().toFixed(1)); }
 return out.join(" ");
}
console.log("len 0  :", mk(0)); console.log("len 50 :", mk(50));
