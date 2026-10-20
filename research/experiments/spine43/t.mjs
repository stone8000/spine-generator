import * as spine from "@esotericsoftware/spine-core";
const L={newRegionAttachment(){},newMeshAttachment(){},newBoundingBoxAttachment(){},newPathAttachment(){},newPointAttachment(){},newClippingAttachment(){}};
const base=()=>({skeleton:{spine:"4.2.43"},bones:[{name:"root"},{name:"a",parent:"root",length:50},{name:"t",parent:"root",y:50}],
  ik:[{name:"ik",order:0,bones:["a"],target:"t"}],physics:[{name:"p",order:1,bone:"a",rotate:1}],slots:[],skins:[{name:"default",attachments:{}}],animations:{}});
const cases={ "no anim keys": base(), "anim keys ik": Object.assign(base(),{animations:{x:{ik:{ik:[{time:0,mix:1}]}}}}),
 "anim physics reset ''": Object.assign(base(),{animations:{x:{physics:{"":{reset:[{time:0}]}}}}}),
 "anim physics named": Object.assign(base(),{animations:{x:{physics:{p:{mix:[{time:0,value:1}]}}}}}) };
for (const [k,j] of Object.entries(cases)) { try { const d=new spine.SkeletonJson(L).readSkeletonData(j); console.log(k,"-> loaded, constraints",d.constraints.length); } catch(e){ console.log(k,"-> THROWS:",e.message); } }
