/**
 * The official spine-core 4.2 runtime, driven headless.
 *
 * WHY this file exists: every number the QA reports must be what a 4.2 player
 * would show, so loading, posing and stepping all go through spine-core
 * itself; nothing here re-implements runtime maths. What IS ours:
 *
 *  - a fake Texture, so atlases load in Node without a GPU or image decoding
 *    (UVs come from the page's declared size; the PNG is never decoded);
 *  - an attachment loader that records a missing atlas region instead of
 *    throwing, so one bad region yields a named L7 finding, not a crash that
 *    hides every other gate (geometry still works on a 1x1 placeholder);
 *  - ONE playback loop (`play`) shared by `dump` and every gate, so the
 *    oracle that Python compares against and the gates can never disagree on
 *    how a frame is produced.
 */
import fs from "node:fs";
import * as spine from "@esotericsoftware/spine-core";

export { spine };
export const RUNTIME = "@esotericsoftware/spine-core@4.2.120";

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

/**
 * Load a skeleton + atlas pair.
 * Returns {raw, atlas, data, missingRegions, error}. `data` is null and
 * `error` holds the runtime's message when loading throws; `raw` is the
 * parsed file either way, so file-only gates still run.
 */
export function load(skeletonPath, atlasPath) {
  const text = fs.readFileSync(skeletonPath, "utf8");
  const raw = JSON.parse(text);
  const atlas = new spine.TextureAtlas(fs.readFileSync(atlasPath, "utf8"));
  for (const page of atlas.pages) page.setTexture(new FakeTexture({ width: page.width, height: page.height }));
  const loader = new RecordingAtlasLoader(atlas);
  try {
    // A fresh parse: the reader must not share objects with `raw`.
    const data = new spine.SkeletonJson(loader).readSkeletonData(JSON.parse(text));
    return { raw, atlas, data, missingRegions: loader.missing, error: null };
  } catch (e) {
    return { raw, atlas, data: null, missingRegions: loader.missing, error: String(e && e.message ? e.message : e) };
  }
}

const POSE_FIELDS = ["rotation", "x", "y", "scaleX", "scaleY", "shearX", "shearY"];

/** Throw on a pose naming an unknown bone or field, before any frame is made. */
export function validatePose(data, pose) {
  for (const [bone, fields] of Object.entries(pose ?? {})) {
    if (!data.findBone(bone)) throw new Error(`pose: no bone named '${bone}'`);
    for (const [f, v] of Object.entries(fields ?? {})) {
      if (!POSE_FIELDS.includes(f)) throw new Error(`pose: '${bone}.${f}' is not one of ${POSE_FIELDS.join(", ")}`);
      if (typeof v !== "number" || !Number.isFinite(v)) throw new Error(`pose: '${bone}.${f}' must be a finite number`);
    }
  }
}

/**
 * Put `pose` on the skeleton's local bone values: "add" adds each field to
 * the current local value (setup, or the animation's frame), "set" replaces
 * it. Returns an undo function: the pose must not accumulate across frames,
 * because AnimationState does not rewrite a property no timeline keys.
 */
function applyPose(sk, pose, mode) {
  const saved = [];
  for (const [name, fields] of Object.entries(pose ?? {})) {
    const bone = sk.findBone(name);
    for (const [f, v] of Object.entries(fields)) {
      saved.push([bone, f, bone[f]]);
      bone[f] = mode === "set" ? v : bone[f] + v;
    }
  }
  return () => { for (const [bone, f, v] of saved.reverse()) bone[f] = v; };
}

/** Zero every constraint's mix so the pose is pure forward kinematics (C1 hook). */
function disableConstraints(sk) {
  for (const c of sk.ikConstraints) c.mix = 0;
  for (const c of sk.transformConstraints) Object.assign(c, { mixRotate: 0, mixX: 0, mixY: 0, mixScaleX: 0, mixScaleY: 0, mixShearY: 0 });
  for (const c of sk.pathConstraints) Object.assign(c, { mixRotate: 0, mixX: 0, mixY: 0 });
  for (const c of sk.physicsConstraints) c.mix = 0;
}

/** A skeleton at the origin, scale 1, no flip, in setup pose. */
export function newSkeleton(data, skinName) {
  const sk = new spine.Skeleton(data);
  Object.assign(sk, { x: 0, y: 0, scaleX: 1, scaleY: 1 });
  if (skinName) {
    if (!data.findSkin(skinName)) throw new Error(`no skin named '${skinName}'`);
    sk.setSkinByName(skinName);
  }
  sk.setToSetupPose();
  return sk;
}

const PHYSICS = { update: spine.Physics.update, reset: spine.Physics.reset, none: spine.Physics.none };

/**
 * Requested times split into grid steps and off-grid times. A time t is on
 * the 1/fps grid when t*fps is (within 1e-9) an integer k; it is then served
 * by step k of the plain run, so `--times 0.5` at 60 fps equals frame 30 of
 * the full dump. An off-grid time gets its own run (grid steps, then one
 * partial step), so it cannot change the dt sequence physics sees for any
 * other requested time.
 */
function splitTimes(times, fps) {
  const grid = new Map(), off = [];
  for (const t of [...new Set(times)].sort((a, b) => a - b)) {
    const k = Math.round(t * fps);
    if (Math.abs(k / fps - t) < 1e-9) grid.set(k, t);
    else off.push(t);
  }
  return { grid, off };
}

/**
 * Drive the runtime frame by frame and call `visit(sk, t, i, trackTime)`
 * after each world transform. Without `anim` the single frame is the setup
 * pose at t=0 and trackTime is null. With one, trackTime is track 0's own
 * time: the float sum of every dt it was given, which is the time
 * AnimationState applied (clamped to the duration), not the nominal t.
 *
 * Options: anim, fps (60), physics ("update" | "reset" | "none"), times
 * (visit only these, each once, grid times first; null = every grid step
 * from 0 to the duration), dts (explicit dt sequence, overrides fps/times;
 * the determinism gate uses it), pose, poseMode ("add" | "set"),
 * constraints (false = all mixes 0), skin, dt ("grid" | "const").
 *
 * Per frame, exactly the documented player order:
 *   state.update(dt); state.apply(sk); [pose]; sk.update(dt);
 *   sk.updateWorldTransform(first frame ? Physics.reset : physics)
 * Grid step k sits at time k/fps (not a running sum of dt), so every frame
 * rate lands exactly on the common comparison times k/30.
 *
 * dt "grid" (the default) steps by k/fps - (k-1)/fps; "const" steps by
 * exactly 1/fps every frame, as a fixed-timestep player (and the older
 * probe [E3], research/experiments/spine42/qa_probe.mjs) does. The two
 * differ by ~1e-17 s per step, yet physics whose step equals the frame time
 * lands on the other side of `remaining >= step` now and then, so the P1
 * numbers differ (warden react 1.92/1.20 px grid vs 1.39/0.90 px const at
 * 30/60 fps).
 */
export function play(data, opts, visit) {
  const { anim = null, fps = 60, physics = "update", times = null, dts = null, pose = null, poseMode = "add", constraints = true, skin = null, dt = "grid" } = opts;
  if (!(physics in PHYSICS)) throw new Error(`physics must be one of ${Object.keys(PHYSICS).join("|")}`);
  if (!["grid", "const"].includes(dt)) throw new Error("dt must be grid|const");
  const mode = PHYSICS[physics];
  const first = physics === "none" ? spine.Physics.none : spine.Physics.reset;
  const animation = anim ? data.findAnimation(anim) : null;
  if (anim && !animation) throw new Error(`no animation named '${anim}'`);

  /**
   * One run on a fresh skeleton over `steps` = [[t, dt], ...]. `emit(i, t)` returns
   * the time to report for step i (the caller's own value for a requested
   * time), or null to skip it.
   */
  const run = (steps, emit) => {
    const sk = newSkeleton(data, skin);
    let state = null;
    if (animation) {
      state = new spine.AnimationState(new spine.AnimationStateData(data));
      state.setAnimationWith(0, animation, false);
    }
    steps.forEach(([t, dt], i) => {
      if (state) { state.update(dt); state.apply(sk); }
      if (!constraints) disableConstraints(sk);
      const undo = applyPose(sk, pose, poseMode);
      sk.update(dt);
      sk.updateWorldTransform(i === 0 ? first : mode);
      const at = emit(i, t);
      if (at !== null) visit(sk, at, i, state ? state.tracks[0].trackTime : null);
      undo();
    });
  };
  /** Grid steps 0..n at t = k/fps; dt is the difference of exact times (or 1/fps), 0 first. */
  const step = (k) => (!k ? 0 : dt === "const" ? 1 / fps : k / fps - (k - 1) / fps);
  const gridUpTo = (n) => Array.from({ length: n + 1 }, (_, k) => [k / fps, step(k)]);

  const every = (i, t) => t;
  if (!animation) return run([[0, 0]], every);
  if (dts) {
    let t = 0;
    return run(dts.map((dt, i) => [(t += i ? dt : 0), i ? dt : 0]), every);
  }
  if (!times) return run(gridUpTo(Math.floor(animation.duration * fps + 1e-9)), every);
  const { grid, off } = splitTimes(times, fps);
  if (grid.size) run(gridUpTo(Math.max(...grid.keys())), (i) => grid.get(i) ?? null);
  for (const t of off) {
    const steps = gridUpTo(Math.floor(t * fps));
    steps.push([t, t - steps[steps.length - 1][0]]);
    run(steps, (i) => (i === steps.length - 1 ? t : null));
  }
}

/**
 * World vertices of a slot's current attachment, skeleton space:
 * meshes/bounding boxes/clipping/paths via computeWorldVertices (deform
 * included), regions as the 4-corner quad, points as [x, y]; [] otherwise.
 */
export function slotVertices(slot) {
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
}

/**
 * Per-triangle winding of a mesh with world vertices `w`: for each counted
 * triangle, sign(world area) * sign(UV area). Near-zero world areas and
 * zero UV areas are not counted (a degenerate sliver is not a fold).
 */
function windings(att, w) {
  const t = att.triangles, uv = att.regionUVs, out = [];
  for (let k = 0; k + 2 < t.length; k += 3) {
    const a = t[k] * 2, b = t[k + 1] * 2, c = t[k + 2] * 2;
    const area = (w[b] - w[a]) * (w[c + 1] - w[a + 1]) - (w[c] - w[a]) * (w[b + 1] - w[a + 1]);
    const uarea = (uv[b] - uv[a]) * (uv[c + 1] - uv[a + 1]) - (uv[c] - uv[a]) * (uv[b + 1] - uv[a + 1]);
    if (Math.abs(area) > 1e-6 && uarea !== 0) out.push(Math.sign(area) * Math.sign(uarea));
  }
  return out;
}

/**
 * The winding an unfolded triangle of each mesh has, taken from the SETUP
 * pose (after a physics reset, as every run starts). UV space is y-down and
 * skeleton space y-up, so normally the windings are opposite (-1); a mesh
 * drawn mirrored at setup on purpose (left/right limb art reused under a
 * bone with scaleX -1) has +1, and only a change from THAT is a fold.
 * The majority of a mesh's counted triangles decides (a tie is -1); setup
 * triangles that disagree with it are folds already at rest and are listed
 * in `setupFolds`. Covers every mesh the active and default skins can put on
 * each slot. Returns {sign(slot, att), mirrored: [...], setupFolds: [...]}.
 */
export function foldReferences(data, skinName = null) {
  const sk = newSkeleton(data, skinName);
  sk.updateWorldTransform(spine.Physics.reset);
  const refs = new Map(), mirrored = [], setupFolds = [];
  for (const skin of new Set([sk.skin, data.defaultSkin].filter(Boolean))) {
    for (const { slotIndex, name, attachment: att } of skin.getAttachments()) {
      if (!(att instanceof spine.MeshAttachment) || refs.get(att)?.has(slotIndex)) continue;
      const slot = sk.slots[slotIndex];
      const w = new Float64Array(att.worldVerticesLength);
      att.computeWorldVertices(slot, 0, att.worldVerticesLength, w, 0, 2); // setup: no deform
      const ws = windings(att, w);
      const pos = ws.filter((x) => x > 0).length, neg = ws.length - pos;
      const sign = pos > neg ? 1 : -1;
      if (!refs.has(att)) refs.set(att, new Map());
      refs.get(att).set(slotIndex, sign);
      const where = `${slot.data.name}/${name}`;
      if (sign > 0) mirrored.push(where);
      const against = sign > 0 ? neg : pos;
      if (against) setupFolds.push(`${where}: ${against} of ${ws.length} triangles already fold at setup (against the mesh's majority winding)`);
    }
  }
  return { sign: (slot, att) => refs.get(att)?.get(slot.data.index) ?? -1, mirrored, setupFolds };
}

/**
 * Folded triangles of a mesh slot given its world vertices: a triangle is
 * folded when its winding differs from the mesh's setup winding
 * (foldReferences; without one, the unmirrored -1). Returns 0 for non-mesh
 * attachments.
 */
export function countFolds(slot, w, refs = null) {
  const att = slot.getAttachment();
  if (!(att instanceof spine.MeshAttachment)) return 0;
  const ref = refs ? refs.sign(slot, att) : -1;
  return windings(att, w).filter((x) => x !== ref).length;
}

/** World point of a bone's tip (local (length, 0)); the origin for a 0-length bone. */
export function boneTip(bone) {
  const len = bone.data.length;
  return [bone.worldX + bone.a * len, bone.worldY + bone.c * len];
}

/**
 * Skeleton height H for pixel thresholds: the file's declared height, or the
 * setup-pose vertical extent of every attachment when the file has none.
 */
export function skeletonHeight(data) {
  if (data.height > 0) return data.height;
  let lo = Infinity, hi = -Infinity;
  play(data, { physics: "none" }, (sk) => {
    for (const slot of sk.slots) {
      const v = slotVertices(slot);
      for (let i = 1; i < v.length; i += 2) { lo = Math.min(lo, v[i]); hi = Math.max(hi, v[i]); }
    }
  });
  return hi > lo ? hi - lo : 0;
}
