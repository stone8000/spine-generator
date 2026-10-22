/**
 * The official spine-core runtime, driven headless: 4.2.120 (default) or
 * 4.3.13 (`useRuntime("4.3")`, the primary target).
 *
 * WHY this file exists: every number the QA reports must be what a player
 * of that version would show, so loading, posing and stepping all go
 * through spine-core itself; nothing here re-implements runtime maths. What
 * IS ours:
 *
 *  - a fake Texture, so atlases load in Node without a GPU or image decoding
 *    (UVs come from the page's declared size; the PNG is never decoded);
 *  - an attachment loader that records a missing atlas region instead of
 *    throwing, so one bad region yields a named L7 finding, not a crash that
 *    hides every other gate (geometry still works on a 1x1 placeholder);
 *  - ONE playback loop (`play`) shared by `dump` and every gate, so the
 *    oracle that Python compares against and the gates can never disagree on
 *    how a frame is produced;
 *  - the runtime adapter (rt42.mjs, rt43.mjs): the only code that knows
 *    which version is loaded. `spine`, `RUNTIME` and `R` are live bindings
 *    set by useRuntime(), which run.mjs calls before anything else.
 */
export let spine = null;
export let RUNTIME = null;
/** The selected runtime adapter (see rt42.mjs for its functions). */
export let R = null;

export const RUNTIMES = ["4.2", "4.3"];

/**
 * Select the runtime every later call uses. 4.3 needs `npm ci` in qa43/
 * (its spine-core is pinned there); the error says so instead of a stack.
 */
export async function useRuntime(version = "4.2") {
  if (!RUNTIMES.includes(version)) throw new Error(`--runtime must be one of ${RUNTIMES.join(", ")}, got '${version}'`);
  let mod;
  try {
    mod = version === "4.3" ? await import("./rt43.mjs") : await import("./rt42.mjs");
  } catch (e) {
    const where = version === "4.3" ? "qa43/" : "qa/";
    throw new Error(`spine-core for ${version} is not installed (npm ci in ${where}): ${e.message}`);
  }
  R = mod.adapter;
  spine = R.spine;
  RUNTIME = R.RUNTIME;
  return R;
}

/**
 * Load a skeleton + atlas pair.
 * Returns {raw, atlas, data, missingRegions, error}. `data` is null and
 * `error` holds the runtime's message when loading throws; `raw` is the
 * parsed file either way, so file-only gates still run.
 */
export function load(skeletonPath, atlasPath) {
  return R.load(skeletonPath, atlasPath);
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
    const local = R.local(sk.findBone(name));
    for (const [f, v] of Object.entries(fields)) {
      saved.push([local, f, local[f]]);
      local[f] = mode === "set" ? v : local[f] + v;
    }
  }
  return () => { for (const [local, f, v] of saved.reverse()) local[f] = v; };
}

/** A skeleton at the origin, scale 1, no flip, in setup pose. */
export function newSkeleton(data, skinName) {
  const sk = R.newSkeleton(data);
  Object.assign(sk, { x: 0, y: 0, scaleX: 1, scaleY: 1 });
  if (skinName) {
    if (!data.findSkin(skinName)) throw new Error(`no skin named '${skinName}'`);
    R.setSkin(sk, skinName);
  }
  R.setupPose(sk);
  return sk;
}

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
 * Per frame, exactly the documented player order (both versions):
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
 * 30/60 fps on 4.2).
 */
export function play(data, opts, visit) {
  const { anim = null, fps = 60, physics = "update", times = null, dts = null, pose = null, poseMode = "add", constraints = true, skin = null, dt = "grid" } = opts;
  if (!["update", "reset", "none"].includes(physics)) throw new Error("physics must be one of update|reset|none");
  if (!["grid", "const"].includes(dt)) throw new Error("dt must be grid|const");
  const mode = R.physics(physics);
  const first = physics === "none" ? R.physics("none") : R.physics("reset");
  const animation = anim ? data.findAnimation(anim) : null;
  if (anim && !animation) throw new Error(`no animation named '${anim}'`);

  /**
   * One run on a fresh skeleton over `steps` = [[t, dt], ...]. `emit(i, t)` returns
   * the time to report for step i (the caller's own value for a requested
   * time), or null to skip it.
   */
  const run = (steps, emit) => {
    const sk = newSkeleton(data, skin);
    const state = animation ? R.newState(data, animation) : null;
    steps.forEach(([t, dt], i) => {
      if (state) { state.update(dt); state.apply(sk); }
      if (!constraints) R.disableConstraints(sk);
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

/** World matrix + origin of a bone: {a, b, c, d, worldX, worldY} (4.3: its applied pose). */
export function world(bone) {
  return R.world(bone);
}

/** The attachment a slot shows now. */
export function slotAttachment(slot) {
  return R.attachment(slot);
}

/**
 * World vertices of a slot's current attachment, skeleton space:
 * meshes/bounding boxes/clipping/paths via computeWorldVertices (deform
 * included), regions as the 4-corner quad, points as [x, y]; [] otherwise.
 */
export function slotVertices(sk, slot) {
  return R.slotVertices(sk, slot);
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
  sk.updateWorldTransform(R.physics("reset"));
  const refs = new Map(), mirrored = [], setupFolds = [];
  for (const skin of new Set([sk.skin, data.defaultSkin].filter(Boolean))) {
    for (const { slotIndex, name, attachment: att } of R.skinEntries(skin)) {
      if (!(att instanceof spine.MeshAttachment) || refs.get(att)?.has(slotIndex)) continue;
      const slot = sk.slots[slotIndex];
      const ws = windings(att, R.meshSetupVertices(sk, slot, att)); // setup: no deform
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
  const att = R.attachment(slot);
  if (!(att instanceof spine.MeshAttachment)) return 0;
  const ref = refs ? refs.sign(slot, att) : -1;
  return windings(att, w).filter((x) => x !== ref).length;
}

/** World point of a bone's tip (local (length, 0)); the origin for a 0-length bone. */
export function boneTip(bone) {
  const len = bone.data.length, p = R.world(bone);
  return [p.worldX + p.a * len, p.worldY + p.c * len];
}

/** World point of a bone-local point. */
export function localToWorld(bone, x, y) {
  return R.world(bone).localToWorld(new spine.Vector2(x, y));
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
      const v = slotVertices(sk, slot);
      for (let i = 1; i < v.length; i += 2) { lo = Math.min(lo, v[i]); hi = Math.max(hi, v[i]); }
    }
  });
  return hi > lo ? hi - lo : 0;
}
