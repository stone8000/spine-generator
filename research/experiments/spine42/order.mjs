import * as spine from "@esotericsoftware/spine-core";
const L={newRegionAttachment(){},newMeshAttachment(){},newBoundingBoxAttachment(){},newPathAttachment(){},newPointAttachment(){},newClippingAttachment(){}};
for (const order of [0,5]) {
 const json={skeleton:{spine:"4.2.43"},bones:[{name:"root"},{name:"a",parent:"root",length:50},{name:"t",parent:"root",x:0,y:50}],
  ik:[{name:"ik",order,bones:["a"],target:"t"}],slots:[],skins:[{name:"default",attachments:{}}],animations:{}};
 const data=new spine.SkeletonJson(L).readSkeletonData(json);
 const sk=new spine.Skeleton(data); sk.updateWorldTransform(spine.Physics.update);
 console.log("order",order,"-> bone a world rotation",sk.findBone("a").getWorldRotationX().toFixed(1),"(IK applied would be 90)", "cache has IK:", sk._updateCache.some(u=>u instanceof spine.IkConstraint));
}
