/**
 * Gates decided without playing anything: version, counts, constraint order,
 * curve shapes, IK/physics/transform validity, atlas and mesh structure
 * (SOTA §5, L1-L9).
 *
 * WHY each gate looks at BOTH the file and the loaded runtime data: every
 * failure these gates exist for is silent in the runtime. A 4.1 file with
 * 4.2 physics loads fine; an `order` gap just never runs the constraint; a
 * short curve array becomes NaN only when that key is reached; `size:` that
 * disagrees with the PNG only shifts UVs. So the file is read for what the
 * author wrote, and the runtime data for what a player will actually do.
 */
import path from "node:path";
import { spine } from "./runtime.mjs";
import { makeGate, skippedGate } from "./gate.mjs";
import { pngSize } from "./png.mjs";
import {
  parseVersion, versionFeatures, rawCounts, rawConstraints, timelines, curveChannels,
  attachmentEntries, decodeMeshVertices, boundaryEdges, insidePolygon,
} from "./raw.mjs";

const vnum = (v) => v.major * 1000 + v.minor;

// ---------------------------------------------------------------- L1 VERSION

/**
 * L1: declared major.minor equals the target, the runtime loads the file,
 * and no feature is newer than the declared version (or than the target),
 * nor an older key name the target ignores (4.1 bone `transform`, linked
 * mesh `deform`, animation `deform` / `draworder`, object-form skins).
 */
export function gateVersion(raw, target, loadError) {
  const items = [];
  const declared = parseVersion(raw.skeleton?.spine);
  const tgt = parseVersion(target);
  if (!declared) items.push("skeleton.spine is missing or not a version");
  else if (declared.major !== tgt.major || declared.minor !== tgt.minor) {
    items.push(`declares ${declared.text}, target is ${target}`);
  }
  for (const f of versionFeatures(raw)) {
    if (f.since === "<4.2") {
      if (vnum(tgt) >= 4002) items.push(`${f.feature} on ${f.where}: 4.2+ runtimes ignore it (write '${f.instead}')`);
      continue;
    }
    const since = parseVersion(f.since);
    if (declared && vnum(declared) < vnum(since)) {
      items.push(`${f.feature} (${f.where}) is a ${f.since} feature in a file declaring ${declared.text}`);
    } else if (vnum(tgt) < vnum(since)) {
      items.push(`${f.feature} (${f.where}) is a ${f.since} feature; a ${target} runtime ignores it`);
    }
  }
  if (loadError) items.push(`spine-core 4.2.120 throws on load: ${loadError}`);
  return makeGate("L1_VERSION", {
    value: items.length, max: 0, items,
    worst: items[0],
    note: `declared ${declared ? declared.text : "?"}, target ${target}`,
  });
}

// ----------------------------------------------------------------- L2 COUNTS

/** The L2 counts as the 4.2 runtime loaded them. */
export function runtimeCounts(data) {
  let attachments = 0, meshes = 0, regions = 0;
  for (const skin of data.skins) {
    for (const entry of skin.getAttachments()) {
      attachments++;
      if (entry.attachment instanceof spine.MeshAttachment) meshes++;
      else if (entry.attachment instanceof spine.RegionAttachment) regions++;
    }
  }
  return {
    bones: data.bones.length, slots: data.slots.length, skins: data.skins.length,
    animations: data.animations.length, events: data.events.length,
    ik: data.ikConstraints.length, transform: data.transformConstraints.length,
    path: data.pathConstraints.length, physics: data.physicsConstraints.length,
    slider: 0, // 4.2 has no slider constraint
    attachments, meshes, regions,
  };
}

/**
 * Expected counts from the IR (spinegen-rig/1, spinegen/ir.py). An explicit
 * `counts` object wins; otherwise each count the IR determines is derived,
 * and a key the IR lacks (rig.json saved without its motion half has no
 * `animations`) is simply not compared. A `half_angle` transform entry is
 * emitted as one transform constraint. The SOTA §3.2 sketch keys
 * (`helpers`, `springs`, `meshes`) are honoured too.
 */
export function irCounts(ir) {
  if (ir.counts && typeof ir.counts === "object") return { ...ir.counts };
  const out = {};
  const len = (x) => (Array.isArray(x) ? x.length : x && typeof x === "object" ? Object.keys(x).length : undefined);
  const helpers = Array.isArray(ir.helpers) ? ir.helpers : [];
  if (Array.isArray(ir.bones)) out.bones = ir.bones.length + helpers.length;
  for (const k of ["slots", "skins", "events", "animations", "ik", "path"]) if (len(ir[k]) !== undefined) out[k] = len(ir[k]);
  if (Array.isArray(ir.transform) || helpers.length) {
    out.transform = (Array.isArray(ir.transform) ? ir.transform.length : 0) + helpers.filter((h) => h.kind === "half_angle").length;
  }
  if (Array.isArray(ir.physics) || Array.isArray(ir.springs)) {
    out.physics = (Array.isArray(ir.physics) ? ir.physics.length : 0) + (Array.isArray(ir.springs) ? ir.springs.length : 0);
  }
  if (Array.isArray(ir.skins)) {
    const atts = ir.skins.flatMap((s) => Object.values(s.attachments ?? {}).flatMap((m) => Object.values(m ?? {})));
    out.meshes = atts.filter((a) => a.type === "mesh" || a.type === "linkedmesh").length;
    out.regions = atts.filter((a) => a.type === "region").length;
  } else if (Array.isArray(ir.meshes)) out.meshes = ir.meshes.length;
  return out;
}

/** L2: runtime counts equal the file's own arrays, and the IR's when given. */
export function gateCounts(raw, data, ir) {
  const json = rawCounts(raw);
  if (!data) {
    return makeGate("L2_COUNTS", { value: 1, max: 0, note: "runtime did not load the file", data: { json } });
  }
  const runtime = runtimeCounts(data);
  const expected = ir ? irCounts(ir) : null;
  const items = [];
  for (const k of Object.keys(json)) {
    if (runtime[k] !== json[k]) items.push(`${k}: runtime ${runtime[k]} vs file ${json[k]}`);
  }
  if (expected) {
    for (const [k, v] of Object.entries(expected)) {
      if (!(k in runtime)) items.push(`${k}: unknown count in IR`);
      else if (runtime[k] !== v) items.push(`${k}: runtime ${runtime[k]} vs IR ${v}`);
    }
  }
  const summary = ["bones", "slots", "meshes", "ik", "transform", "path", "physics", "animations"]
    .map((k) => `${runtime[k]} ${k}`).join(", ");
  return makeGate("L2_COUNTS", {
    value: items.length, max: 0, items, worst: items[0],
    note: expected ? summary : `${summary} (no --ir: compared with the file only)`,
    data: { runtime, json, ...(expected ? { ir: expected } : {}) },
  });
}

// ------------------------------------------------------------------ L3 ORDER

/**
 * L3: the `order` values over ik+transform+path+physics are exactly 0..N-1.
 * A gap or a duplicate is silent in 4.2: the constraint pushed past N-1, or
 * the later of two sharing an order, simply never runs
 * (research/experiments/spine42/order.mjs, [E4]); items name each one.
 */
export function gateOrder(raw, data) {
  const list = [];
  if (data) {
    for (const [kind, arr] of [["ik", data.ikConstraints], ["transform", data.transformConstraints],
      ["path", data.pathConstraints], ["physics", data.physicsConstraints]]) {
      for (const c of arr) list.push({ kind, name: c.name, order: c.order });
    }
  } else {
    const cons = rawConstraints(raw);
    for (const kind of ["ik", "transform", "path", "physics"]) {
      for (const c of cons[kind]) list.push({ kind, name: c.name, order: c.order ?? 0 });
    }
  }
  const n = list.length;
  const byOrder = new Map();
  for (const c of list) byOrder.set(c.order, [...(byOrder.get(c.order) ?? []), c]);
  const items = [];
  for (const [order, cs] of byOrder) {
    if (!Number.isInteger(order) || order < 0 || order >= n) items.push(`order ${order} (${cs.map((c) => c.name).join(", ")}) outside 0..${n - 1}`);
    if (cs.length > 1) items.push(`order ${order} shared by ${cs.map((c) => `${c.kind} ${c.name}`).join(", ")}`);
  }
  for (let k = 0; k < n; k++) if (!byOrder.has(k)) items.push(`order ${k} unused`);
  // Skeleton.updateCache: for each order 0..N-1 the first match in ik, transform,
  // path, physics list order runs; everything else never does
  const runs = new Set();
  for (let k = 0; k < n; k++) { const c = list.find((x) => x.order === k); if (c) runs.add(c); }
  for (const c of list) if (!runs.has(c)) items.push(`never runs in 4.2: ${c.kind} ${c.name} (order ${c.order})`);
  const unwritten = [];
  for (const kind of ["ik", "transform", "path", "physics"]) {
    for (const c of Array.isArray(raw[kind]) ? raw[kind] : []) if (c.order === undefined) unwritten.push(c.name);
  }
  return makeGate("L3_ORDER", {
    value: items.length, max: 0, items, worst: items[0],
    note: `${n} constraints` + (unwritten.length ? `; no 'order' key (runtime default 0): ${unwritten.join(", ")}` : ""),
  });
}

// ------------------------------------------------------------ L4 CURVES/NAN

const NUMERIC_KEY_FIELDS = new Set([
  "time", "value", "x", "y", "mix", "softness", "mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY",
  "mixShearY", "offset", "delay", "index", "int", "float", "volume", "balance", "angle",
]);

/**
 * L4, file half: every `curve` array holds exactly 4 numbers per channel of
 * its timeline, and every numeric key field is a finite number. The runtime
 * half (loaded frames and every sampled world value) comes from the sweep.
 */
export function curveFindings(raw) {
  const items = [];
  for (const tl of timelines(raw)) {
    const where = `${tl.anim}/${tl.group}/${tl.owner}/${tl.name}`;
    const channels = curveChannels(tl.group, tl.name);
    tl.keys.forEach((key, i) => {
      if (!key || typeof key !== "object") { items.push(`${where}[${i}]: key is not an object`); return; }
      for (const [f, v] of Object.entries(key)) {
        if (NUMERIC_KEY_FIELDS.has(f) && (typeof v !== "number" || !Number.isFinite(v))) items.push(`${where}[${i}].${f} = ${JSON.stringify(v)}`);
      }
      if (Array.isArray(key.vertices) && key.vertices.some((v) => typeof v !== "number" || !Number.isFinite(v))) {
        items.push(`${where}[${i}].vertices has a non-number`);
      }
      const c = key.curve;
      if (c === undefined || c === "stepped") return;
      if (!Array.isArray(c)) { items.push(`${where}[${i}].curve is ${JSON.stringify(c)}`); return; }
      if (channels === undefined) { items.push(`${where}: unknown timeline, curve not checked`); return; }
      if (c.length !== 4 * channels) items.push(`${where}[${i}].curve has ${c.length} numbers, needs ${4 * channels}`);
      if (c.some((v) => typeof v !== "number" || !Number.isFinite(v))) items.push(`${where}[${i}].curve has a non-number`);
    });
  }
  return items;
}

/** L4, runtime half: every loaded timeline's frames and curves are finite. */
export function loadedTimelineFindings(data) {
  const items = [];
  const finite = (arr) => !arr || Array.prototype.every.call(arr, Number.isFinite);
  for (const anim of data.animations) {
    for (const tl of anim.timelines) {
      const what = `${anim.name}/${tl.constructor.name}`;
      if (!finite(tl.frames)) items.push(`${what}: non-finite frame value`);
      if (tl.curves && !finite(tl.curves)) items.push(`${what}: non-finite curve (curve array too short?)`);
      if (Array.isArray(tl.vertices) && tl.vertices.some((v) => !finite(v))) items.push(`${what}: non-finite deform vertex`);
    }
  }
  return items;
}

// --------------------------------------------------------------- L5 IK_VALID

/**
 * L5: IK bones are `inherit: normal` (4.2 two-bone IK silently does nothing
 * otherwise); a one-bone IK bone has a parent; a two-bone IK parent has a
 * parent itself (the runtime throws otherwise), the child is its direct
 * child and has length > 0.
 */
export function gateIk(data) {
  if (!data) return makeGate("L5_IK_VALID", { value: 1, max: 0, note: "runtime did not load the file" });
  const items = [];
  for (const ik of data.ikConstraints) {
    const bones = ik.bones;
    if (bones.length < 1 || bones.length > 2) { items.push(`${ik.name}: ${bones.length} bones (1 or 2 allowed)`); continue; }
    for (const b of bones) if (b.inherit !== spine.Inherit.Normal) items.push(`${ik.name}: bone ${b.name} inherit ${spine.Inherit[b.inherit]}, needs Normal`);
    if (bones.length === 1 && !bones[0].parent) items.push(`${ik.name}: bone ${bones[0].name} has no parent`);
    if (bones.length === 2) {
      const [parent, child] = bones;
      if (!parent.parent) items.push(`${ik.name}: parent ${parent.name} has no parent (runtime throws)`);
      if (child.parent !== parent) items.push(`${ik.name}: ${child.name} is not a child of ${parent.name}`);
      if (!(child.length > 0)) items.push(`${ik.name}: child ${child.name} length ${child.length}`);
    }
  }
  return makeGate("L5_IK_VALID", { value: items.length, max: 0, items, worst: items[0], note: `${data.ikConstraints.length} IK constraints` });
}

// ---------------------------------------------------------- L6 PHYSICS_VALID

const PHYSICS_EXPLICIT = ["fps", "inertia", "strength", "damping", "mass", "limit"];
const PHYSICS_WEIGHTS = ["x", "y", "rotate", "scaleX", "shearX"];

/**
 * L6: per physics constraint, at least one non-zero weight; rotate/shearX/
 * scaleX physics on a bone >= 0.02 H long (a 0-length bone whips 44-82°,
 * [E6]); fps/inertia/strength/damping/mass/limit written out (defaults
 * differ between 4.2 and 4.3); limit >= 1000 (Hard Time's 60-80 throttles
 * follow-through).
 */
export function gatePhysics(raw, H) {
  const cons = rawConstraints(raw).physics;
  const boneLen = new Map((Array.isArray(raw.bones) ? raw.bones : []).map((b) => [b.name, b.length ?? 0]));
  const items = [];
  for (const c of cons) {
    if (!PHYSICS_WEIGHTS.some((w) => (c[w] ?? 0) !== 0)) items.push(`${c.name}: every weight (${PHYSICS_WEIGHTS.join(", ")}) is 0`);
    const len = boneLen.get(c.bone) ?? 0;
    const angular = ["rotate", "shearX", "scaleX"].filter((w) => (c[w] ?? 0) !== 0);
    if (angular.length && len < 0.02 * H) items.push(`${c.name}: ${angular.join("/")} physics on ${c.bone} length ${len} < 0.02H = ${(0.02 * H).toFixed(1)}`);
    const missing = PHYSICS_EXPLICIT.filter((k) => c[k] === undefined);
    if (missing.length) items.push(`${c.name}: not written: ${missing.join(", ")}`);
    const limit = c.limit ?? 5000;
    if (limit < 1000) items.push(`${c.name}: limit ${limit} < 1000`);
  }
  return makeGate("L6_PHYSICS_VALID", { value: items.length, max: 0, items, worst: items[0], note: `${cons.length} physics constraints, H=${H}` });
}

// -------------------------------------------------------------------- L7 ATLAS

/**
 * L7: each page's `size:` equals its PNG's real size, pma matches the
 * expected setting when one is given, every region lies on its page, and
 * every region/mesh attachment found its region.
 */
export function gateAtlas(atlas, atlasPath, missingRegions, { pma } = {}) {
  const items = [];
  const pages = [];
  for (const page of atlas.pages) {
    const file = path.join(path.dirname(atlasPath), page.name);
    let real = null;
    try { real = pngSize(file); } catch (e) { items.push(`page ${page.name}: ${e.code === "ENOENT" ? "PNG not found" : e.message}`); }
    if (!(page.width > 0 && page.height > 0)) items.push(`page ${page.name}: no size: line`);
    else if (real && (real.width !== page.width || real.height !== page.height)) {
      items.push(`page ${page.name}: size: ${page.width}x${page.height} but PNG is ${real.width}x${real.height}`);
    }
    if (pma !== undefined && page.pma !== pma) items.push(`page ${page.name}: pma ${page.pma}, runtime expects ${pma}`);
    pages.push({ name: page.name, declared: [page.width, page.height], png: real ? [real.width, real.height] : null, pma: page.pma });
  }
  for (const r of atlas.regions) {
    const turned = r.degrees === 90 || r.degrees === 270;
    const w = turned ? r.height : r.width, h = turned ? r.width : r.height;
    if (r.x < 0 || r.y < 0 || r.x + w > r.page.width || r.y + h > r.page.height) {
      items.push(`region ${r.name}: ${r.x},${r.y} ${w}x${h} outside page ${r.page.name} ${r.page.width}x${r.page.height}`);
    }
  }
  for (const m of missingRegions) items.push(`${m.kind} attachment ${m.attachment}: no region '${m.path}'`);
  return makeGate("L7_ATLAS", {
    value: items.length, max: 0, items, worst: items[0],
    note: `${atlas.pages.length} page(s), ${atlas.regions.length} regions` + (pma === undefined ? "; pma not checked (no --pma)" : ""),
    data: { pages },
  });
}

/** Region lookups the runtime would make, from the file alone (used when loading threw). */
export function missingRegionsFromFile(raw, atlas) {
  const out = [];
  for (const a of attachmentEntries(raw)) {
    if (!["region", "mesh", "linkedmesh"].includes(a.type)) continue;
    const p = a.map.path ?? a.name;
    if (a.map.sequence) continue; // per-frame names need the runtime's padding rules
    if (!atlas.findRegion(p)) out.push({ path: p, attachment: a.name, kind: a.type });
  }
  return out;
}

// --------------------------------------------------------------------- L8 MESH

/**
 * Structural findings for one mesh map: {hard: [...], influences: [...],
 * enclosed}. `enclosed` counts boundary vertices after the hull that lie
 * inside the hull polygon: the rim of a hole. Spine's hull is the outline
 * polygon and every other vertex is interior to it, so a hole is legal
 * (the editor shows its rim as interior vertices); a boundary vertex
 * OUTSIDE the hull polygon (a separate island, or a hull that is not the
 * outline) is not.
 */
function meshFindings(where, map, boneCount) {
  const hard = [], influences = [];
  let enclosed = 0;
  const uvs = Array.isArray(map.uvs) ? map.uvs : [];
  const tris = Array.isArray(map.triangles) ? map.triangles : [];
  if (uvs.length % 2) hard.push(`${where}: odd uvs length ${uvs.length}`);
  const dec = decodeMeshVertices(map);
  const n = dec.count;
  const hull = map.hull ?? 0;
  if (!(hull > 0)) hard.push(`${where}: hull=${hull}`);
  else if (hull > n) hard.push(`${where}: hull ${hull} > ${n} vertices`);
  if (tris.length % 3) hard.push(`${where}: ${tris.length} triangle indices, not a multiple of 3`);
  const bad = tris.filter((i) => !Number.isInteger(i) || i < 0 || i >= n).length;
  if (bad) hard.push(`${where}: ${bad} triangle indices outside 0..${n - 1}`);
  if (hull > 0 && hull <= n && !bad && tris.length % 3 === 0) {
    const edges = boundaryEdges(tris);
    const after = new Set();
    for (const e of edges) for (const v of e.split(",").map(Number)) if (v >= hull) after.add(v);
    const hullLoop = Array.from({ length: hull }, (_, i) => i);
    const outside = [...after].filter((v) => !insidePolygon(uvs, hullLoop, uvs[2 * v], uvs[2 * v + 1]));
    enclosed = after.size - outside.length;
    if (outside.length) hard.push(`${where}: ${outside.length} boundary vertices after the first ${hull} lie outside the hull polygon (hull not first, or an island no one-polygon hull can hold)`);
    let open = 0;
    for (let i = 0; i < hull; i++) {
      const a = i, b = (i + 1) % hull;
      if (!edges.has(a < b ? `${a},${b}` : `${b},${a}`)) open++;
    }
    if (open) hard.push(`${where}: ${open} of ${hull} hull edges are not on the mesh boundary`);
  }
  if (dec.error) hard.push(`${where}: ${dec.error}`);
  if (dec.weighted && dec.influences) {
    let badBone = 0, badSum = 0, over3 = 0, worstSum = 1;
    for (const vertex of dec.influences) {
      if (vertex.some((inf) => !Number.isInteger(inf.bone) || inf.bone < 0 || inf.bone >= boneCount)) badBone++;
      const sum = vertex.reduce((s, inf) => s + inf.w, 0);
      // 1 ± 1e-4 inclusive: 4-decimal weights summing to 1.0001 are legal, and
      // the float sum of them lands a hair above, hence the 1e-9 slack.
      if (Math.abs(sum - 1) > 1e-4 + 1e-9) { badSum++; if (Math.abs(sum - 1) > Math.abs(worstSum - 1)) worstSum = sum; }
      if (vertex.length > 3) over3++;
    }
    if (badBone) hard.push(`${where}: ${badBone} vertices bound to a bone index outside 0..${boneCount - 1}`);
    if (badSum) hard.push(`${where}: ${badSum} vertices with weight sum off 1 by > 1e-4 (worst ${worstSum})`);
    if (over3) influences.push(`${where}: ${over3} vertices with > 3 influences`);
  }
  return { hard, influences, enclosed };
}

/**
 * L8: hull > 0 with the hull vertices first (every boundary vertex is a
 * hull vertex or encloses a hole inside the hull polygon), triangle indices
 * in range, weights summing to 1 ± 1e-4; plus the "<= 3 influences per vertex" rule,
 * returned as its own gate: hard for our own output (--ir given), a
 * warning for imported files whose rigs we did not choose.
 */
export function gateMesh(raw, { generated }) {
  const boneCount = Array.isArray(raw.bones) ? raw.bones.length : 0;
  const hard = [], influences = [], holes = [];
  let meshes = 0;
  for (const a of attachmentEntries(raw)) {
    if (a.type !== "mesh") continue; // linked meshes share their parent's geometry
    meshes++;
    const f = meshFindings(`${a.slot}/${a.name}`, a.map, boneCount);
    hard.push(...f.hard);
    influences.push(...f.influences);
    if (f.enclosed) holes.push(`${a.slot}/${a.name} (${f.enclosed})`);
  }
  const hull0 = hard.filter((s) => /: hull=/.test(s)).length;
  const holeNote = holes.length ? `; hole rims inside the hull (legal, the editor fills them on re-triangulation): ${holes.join(", ")}` : "";
  return [
    makeGate("L8_MESH", { value: hard.length, max: 0, items: hard, worst: hard[0], note: `${meshes} meshes, ${hull0} with hull=0${holeNote}` }),
    makeGate("L8_MESH_INFLUENCES", {
      value: influences.length, max: 0, hard: generated, items: influences, worst: influences[0],
      note: generated ? "hard: generated rig (--ir)" : "warning only: imported file",
    }),
  ];
}

// ------------------------------------------------------------------ L9 TC_MIX

const TC_MIXES = ["mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY"];

/** L9: every transform constraint writes all six mixes (each defaults to 1). */
export function gateTcMix(raw) {
  const cons = rawConstraints(raw).transform;
  const items = [];
  for (const c of cons) {
    const missing = TC_MIXES.filter((k) => c[k] === undefined);
    if (missing.length) items.push(`${c.name}: not written: ${missing.join(", ")} (default 1)`);
  }
  return makeGate("L9_TC_MIX", { value: items.length, max: 0, items, worst: items[0], note: `${cons.length} transform constraints` });
}

export { skippedGate };
