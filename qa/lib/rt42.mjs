/**
 * Runtime adapter: the official spine-core 4.2.120 (qa/'s own pinned copy).
 *
 * WHY an adapter: qa judges one file with the runtime the game will ship
 * (`--runtime 4.2|4.3`). The two runtimes load the same kind of data but
 * name and nest it differently (4.3 keeps a bone's world matrix on
 * `bone.appliedPose`, constraints in one list, `setupPose()` instead of
 * `setToSetupPose()`), so everything version-specific lives in rt42.mjs /
 * rt43.mjs behind the same small set of functions, and runtime.mjs and the
 * gates are written once against it. Nothing here re-implements runtime
 * maths; each function is a direct call or field read.
 */
import fs from "node:fs";
import * as spine from "@esotericsoftware/spine-core";

class FakeTexture extends spine.Texture {
  setFilters() {}
  setWraps() {}
  dispose() {}
}

/** A 1x1 full-page region used where the atlas has none (geometry stays valid). */
function placeholderRegion() {
  const r = new spine.TextureRegion();
  Object.assign(r, { u: 0, v: 0, u2: 1, v2: 1, width: 1, height: 1, originalWidth: 1, originalHeight: 1, degrees: 0, offsetX: 0, offsetY: 0 });
  r.texture = new FakeTexture({ width: 1, height: 1 });
  return r;
}

/** AtlasAttachmentLoader that lists missing regions in `this.missing` instead of throwing. */
class RecordingAtlasLoader extends spine.AtlasAttachmentLoader {
  constructor(atlas) {
    super(atlas);
    this.missing = [];
    this.placeholder = placeholderRegion();
  }

  regionFor(path, name, kind) {
    const region = this.atlas.findRegion(path);
    if (region) return region;
    this.missing.push({ path, attachment: name, kind });
    return this.placeholder;
  }

  fillSequence(name, basePath, sequence) {
    for (let i = 0; i < sequence.regions.length; i++) {
      sequence.regions[i] = this.regionFor(sequence.getPath(basePath, i), name, "sequence");
    }
  }

  newRegionAttachment(skin, name, path, sequence) {
    const att = new spine.RegionAttachment(name, path);
    if (sequence) this.fillSequence(name, path, sequence);
    else att.region = this.regionFor(path, name, "region");
    return att;
  }

  newMeshAttachment(skin, name, path, sequence) {
    const att = new spine.MeshAttachment(name, path);
    if (sequence) this.fillSequence(name, path, sequence);
    else att.region = this.regionFor(path, name, "mesh");
    return att;
  }
}

const PHYSICS = { update: spine.Physics.update, reset: spine.Physics.reset, none: spine.Physics.none };

export const adapter = {
  version: "4.2",
  RUNTIME: "@esotericsoftware/spine-core@4.2.120",
  spine,

  /** {raw, atlas, data, missingRegions, error}; see runtime.load. */
  load(skeletonPath, atlasPath) {
    const text = fs.readFileSync(skeletonPath, "utf8");
    const raw = JSON.parse(text);
    const atlas = new spine.TextureAtlas(fs.readFileSync(atlasPath, "utf8"));
    for (const page of atlas.pages) page.setTexture(new FakeTexture({ width: page.width, height: page.height }));
    const loader = new RecordingAtlasLoader(atlas);
    try {
      const data = new spine.SkeletonJson(loader).readSkeletonData(JSON.parse(text)); // fresh parse: no shared objects
      return { raw, atlas, data, missingRegions: loader.missing, error: null };
    } catch (e) {
      return { raw, atlas, data: null, missingRegions: loader.missing, error: String(e && e.message ? e.message : e) };
    }
  },

  newSkeleton(data) { return new spine.Skeleton(data); },
  setSkin(sk, name) { sk.setSkinByName(name); },
  setupPose(sk) { sk.setToSetupPose(); },
  physics(name) { return PHYSICS[name]; },
  newState(data, animation) {
    const state = new spine.AnimationState(new spine.AnimationStateData(data));
    state.setAnimationWith(0, animation, false);
    return state;
  },

  /** The object holding a bone's world matrix a, b, c, d, worldX, worldY (and localToWorld). */
  world(bone) { return bone; },
  /** The bone's local values an animation writes (rotation, x, y, scaleX, scaleY, shearX, shearY). */
  local(bone) { return bone; },
  attachment(slot) { return slot.getAttachment(); },

  /** World vertices of a slot's current attachment (see runtime.slotVertices). */
  slotVertices(sk, slot) {
    const att = slot.getAttachment();
    if (!att) return new Float64Array(0);
    if (att instanceof spine.RegionAttachment) {
      const out = new Float64Array(8);
      att.computeWorldVertices(slot, out, 0, 2);
      return out;
    }
    // Before VertexAttachment: in 4.2 PointAttachment extends it (with no
    // vertices), so the generic branch would swallow points and return [].
    if (att instanceof spine.PointAttachment) {
      const p = att.computeWorldPosition(slot.bone, new spine.Vector2());
      return Float64Array.of(p.x, p.y);
    }
    if (att instanceof spine.VertexAttachment) {
      const out = new Float64Array(att.worldVerticesLength);
      att.computeWorldVertices(slot, 0, att.worldVerticesLength, out, 0, 2);
      return out;
    }
    return new Float64Array(0);
  },

  /** Setup-pose mesh vertices without deform (fold references). */
  meshSetupVertices(sk, slot, att) {
    const w = new Float64Array(att.worldVerticesLength);
    att.computeWorldVertices(slot, 0, att.worldVerticesLength, w, 0, 2);
    return w;
  },

  /** Skin entries as {slotIndex, name, attachment}. */
  skinEntries(skin) { return skin.getAttachments(); },

  /** Zero every constraint's mix so the pose is pure forward kinematics (C1 hook). */
  disableConstraints(sk) {
    for (const c of sk.ikConstraints) c.mix = 0;
    for (const c of sk.transformConstraints) Object.assign(c, { mixRotate: 0, mixX: 0, mixY: 0, mixScaleX: 0, mixScaleY: 0, mixShearY: 0 });
    for (const c of sk.pathConstraints) Object.assign(c, { mixRotate: 0, mixX: 0, mixY: 0 });
    for (const c of sk.physicsConstraints) c.mix = 0;
  },

  // ---- skeleton data (gates read these, never a runtime class directly)
  ikConstraints(data) { return data.ikConstraints; },
  transformConstraints(data) { return data.transformConstraints; },
  pathConstraints(data) { return data.pathConstraints; },
  physicsConstraints(data) { return data.physicsConstraints; },
  sliders() { return []; },
  /** Constraint data in evaluation-relevant form: [{kind, name, order}] (4.2: the `order` field). */
  constraintList(data) {
    const out = [];
    for (const [kind, arr] of [["ik", data.ikConstraints], ["transform", data.transformConstraints],
      ["path", data.pathConstraints], ["physics", data.physicsConstraints]]) {
      for (const c of arr) out.push({ kind, name: c.name, order: c.order });
    }
    return out;
  },
  boneInherit(boneData) { return boneData.inherit; },
};
