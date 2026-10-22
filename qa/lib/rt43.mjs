/**
 * Runtime adapter: the official spine-core 4.3.13 (qa43/'s pinned copy).
 *
 * WHY: Spine 4.3 is the primary target, so its runtime must be the judge of
 * a 4.3 file, the same gates as 4.2 (rt42.mjs explains the adapter). What
 * 4.3 changes underneath, all behind the same functions:
 *  - a bone's world matrix lives on `bone.appliedPose` (the pose after
 *    constraints), its animated local values on `bone.pose`;
 *  - a slot's attachment on `slot.appliedPose`; vertex attachments take the
 *    skeleton; a region's corners take its current sequence offsets;
 *  - constraints are one list (`data.constraints`, `sk.constraints`) run in
 *    list order, each with a `pose` holding its mixes;
 *  - `setupPose()`, `setSkin(name)`, `setAnimation(track, Animation)`.
 * spine-core 4.3.13 is imported from qa43/ (qa43/spine.mjs): qa/'s own
 * node_modules pins 4.2.120 and must stay that way.
 */
import fs from "node:fs";
import * as spine from "../../qa43/spine.mjs";

class FakeTexture extends spine.Texture {
  setFilters() {}
  setWraps() {}
  dispose() {}
}

function placeholderRegion() {
  const r = new spine.TextureRegion();
  Object.assign(r, { u: 0, v: 0, u2: 1, v2: 1, width: 1, height: 1, originalWidth: 1, originalHeight: 1, degrees: 0, offsetX: 0, offsetY: 0 });
  r.texture = new FakeTexture({ width: 1, height: 1 });
  return r;
}

/**
 * AtlasAttachmentLoader that records a missing region instead of throwing.
 * 4.3 funnels every lookup through findRegion(name, path), sequences
 * included, so one override covers regions, meshes and sequence frames.
 */
class RecordingAtlasLoader extends spine.AtlasAttachmentLoader {
  constructor(atlas) {
    super(atlas, false);
    this.missing = [];
    this.placeholder = placeholderRegion();
    this.kind = "region";
  }

  findRegion(name, path) {
    const region = this.atlas.findRegion(path);
    if (region) return region;
    this.missing.push({ path, attachment: name, kind: this.kind });
    return this.placeholder;
  }

  newRegionAttachment(skin, placeholder, name, path, sequence) {
    this.kind = sequence && sequence.regions.length > 1 ? "sequence" : "region";
    return super.newRegionAttachment(skin, placeholder, name, path, sequence);
  }

  newMeshAttachment(skin, placeholder, name, path, sequence) {
    this.kind = sequence && sequence.regions.length > 1 ? "sequence" : "mesh";
    return super.newMeshAttachment(skin, placeholder, name, path, sequence);
  }
}

const PHYSICS = { update: spine.Physics.update, reset: spine.Physics.reset, none: spine.Physics.none };
const ofType = (data, T) => data.constraints.filter((c) => c instanceof T);
const KIND = [["ik", spine.IkConstraintData], ["transform", spine.TransformConstraintData],
  ["path", spine.PathConstraintData], ["physics", spine.PhysicsConstraintData], ["slider", spine.SliderData]];

export const adapter = {
  version: "4.3",
  RUNTIME: "@esotericsoftware/spine-core@4.3.13",
  spine,

  load(skeletonPath, atlasPath) {
    const text = fs.readFileSync(skeletonPath, "utf8");
    const raw = JSON.parse(text);
    const atlas = new spine.TextureAtlas(fs.readFileSync(atlasPath, "utf8"));
    for (const page of atlas.pages) page.setTexture(new FakeTexture({ width: page.width, height: page.height }));
    const loader = new RecordingAtlasLoader(atlas);
    try {
      const data = new spine.SkeletonJson(loader).readSkeletonData(JSON.parse(text));
      return { raw, atlas, data, missingRegions: loader.missing, error: null };
    } catch (e) {
      return { raw, atlas, data: null, missingRegions: loader.missing, error: String(e && e.message ? e.message : e) };
    }
  },

  newSkeleton(data) { return new spine.Skeleton(data); },
  setSkin(sk, name) { sk.setSkin(name); },
  setupPose(sk) { sk.setupPose(); },
  physics(name) { return PHYSICS[name]; },
  newState(data, animation) {
    const state = new spine.AnimationState(new spine.AnimationStateData(data));
    state.setAnimation(0, animation, false);
    return state;
  },

  world(bone) { return bone.appliedPose; },
  local(bone) { return bone.pose; },
  attachment(slot) { return slot.appliedPose.getAttachment(); },

  slotVertices(sk, slot) {
    const att = slot.appliedPose.getAttachment();
    if (!att) return new Float64Array(0);
    if (att instanceof spine.RegionAttachment) {
      const out = new Float64Array(8);
      att.computeWorldVertices(slot, att.getOffsets(slot.appliedPose), out, 0, 2);
      return out;
    }
    if (att instanceof spine.PointAttachment) { // also a VertexAttachment in 4.3
      const p = att.computeWorldPosition(slot.bone.appliedPose, new spine.Vector2());
      return Float64Array.of(p.x, p.y);
    }
    if (att instanceof spine.VertexAttachment) {
      const out = new Float64Array(att.worldVerticesLength);
      att.computeWorldVertices(sk, slot, 0, att.worldVerticesLength, out, 0, 2);
      return out;
    }
    return new Float64Array(0);
  },

  meshSetupVertices(sk, slot, att) {
    const w = new Float64Array(att.worldVerticesLength);
    att.computeWorldVertices(sk, slot, 0, att.worldVerticesLength, w, 0, 2);
    return w;
  },

  skinEntries(skin) {
    return skin.getAttachments().map((e) => ({ slotIndex: e.slotIndex, name: e.placeholder, attachment: e.attachment }));
  },

  disableConstraints(sk) {
    for (const c of sk.constraints) {
      const p = c.pose;
      for (const k of ["mix", "mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY"]) if (k in p) p[k] = 0;
    }
  },

  ikConstraints(data) { return ofType(data, spine.IkConstraintData); },
  transformConstraints(data) { return ofType(data, spine.TransformConstraintData); },
  pathConstraints(data) { return ofType(data, spine.PathConstraintData); },
  physicsConstraints(data) { return ofType(data, spine.PhysicsConstraintData); },
  sliders(data) { return ofType(data, spine.SliderData); },
  /** [{kind, name, order}] where order is the list position: 4.3's evaluation order. */
  constraintList(data) {
    return data.constraints.map((c, order) => ({ kind: (KIND.find(([, T]) => c instanceof T) ?? ["?"])[0], name: c.name, order }));
  },
  boneInherit(boneData) { return boneData.setupPose.inherit; },
};
