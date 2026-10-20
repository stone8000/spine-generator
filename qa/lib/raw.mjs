/**
 * Facts read straight from a skeleton JSON file, without any Spine runtime.
 *
 * WHY a second reading next to the runtime's: a runtime silently ignores what
 * it does not know (4.2 drops a 4.3 `constraints[]`, 4.3 drops 4.2's top-level
 * `ik`/`physics`, 4.2 ignores the 4.1 bone key `transform`). Comparing what
 * the FILE says with what the RUNTIME loaded is the only way to see that loss.
 * This module imports nothing from spine-core on purpose, so the 4.3 probe
 * (qa43/) can reuse it against a different runtime.
 */

const CONSTRAINT_KINDS = ["ik", "transform", "path", "physics"];

/** Number of entries in an array or object map; 0 when absent. */
function size(x) {
  if (Array.isArray(x)) return x.length;
  if (x && typeof x === "object") return Object.keys(x).length;
  return 0;
}

/** "4.2.43" -> {major:4, minor:2, patch:43, text}; null when absent/unparsable. */
export function parseVersion(text) {
  const m = /^(\d+)\.(\d+)(?:\.(\d+))?/.exec(String(text ?? ""));
  if (!m) return null;
  return { major: +m[1], minor: +m[2], patch: m[3] === undefined ? 0 : +m[3], text: String(text) };
}

/**
 * Skins as [{name, attachments: {slot: {attName: map}}, ...}] for both the
 * 4.x array form and the 3.x object form.
 */
export function skinList(raw) {
  const s = raw.skins;
  if (Array.isArray(s)) return s.map((k) => ({ ...k, attachments: k.attachments ?? {} }));
  if (s && typeof s === "object") return Object.entries(s).map(([name, attachments]) => ({ name, attachments }));
  return [];
}

/** Every attachment as {skin, slot, name, map, type} (type defaults to region). */
export function attachmentEntries(raw) {
  const out = [];
  for (const skin of skinList(raw)) {
    for (const [slot, atts] of Object.entries(skin.attachments)) {
      for (const [name, map] of Object.entries(atts ?? {})) {
        out.push({ skin: skin.name, slot, name, map, type: String(map.type ?? "region").toLowerCase() });
      }
    }
  }
  return out;
}

/**
 * Constraints as the file declares them, per kind: the 4.2 top-level arrays
 * plus any 4.3 `constraints[]` entries of that type.
 */
export function rawConstraints(raw) {
  const v43 = Array.isArray(raw.constraints) ? raw.constraints : [];
  const out = {};
  for (const kind of CONSTRAINT_KINDS) {
    const top = Array.isArray(raw[kind]) ? raw[kind] : [];
    out[kind] = [...top, ...v43.filter((c) => c.type === kind)];
  }
  out.slider = v43.filter((c) => c.type === "slider");
  return out;
}

/** The counts L2 compares, as written in the file. */
export function rawCounts(raw) {
  const cons = rawConstraints(raw);
  const atts = attachmentEntries(raw);
  const ofType = (...types) => atts.filter((a) => types.includes(a.type)).length;
  return {
    bones: size(raw.bones),
    slots: size(raw.slots),
    skins: skinList(raw).length,
    animations: size(raw.animations),
    events: size(raw.events),
    ik: cons.ik.length,
    transform: cons.transform.length,
    path: cons.path.length,
    physics: cons.physics.length,
    slider: cons.slider.length,
    attachments: atts.length,
    meshes: ofType("mesh", "linkedmesh"),
    regions: ofType("region"),
  };
}

/**
 * Features whose meaning depends on the format version, as
 * [{feature, where, since, instead?}] where `since` is the first version
 * that reads it, or "<4.2" for an older key name a 4.2 runtime IGNORES
 * (SkeletonJson 4.2.120 reads only the new name, given as `instead`).
 * L1 compares these with the declared `skeleton.spine`; spinegen's reader
 * ignores the same "<4.2" keys, with a warning each.
 */
export function versionFeatures(raw) {
  const found = [];
  const add = (feature, where, since, instead) => found.push({ feature, where, since, ...(instead ? { instead } : {}) });
  if (size(raw.physics) > 0) add("top-level physics[]", `${size(raw.physics)} constraints`, "4.2");
  if (raw.skeleton && raw.skeleton.referenceScale !== undefined) add("skeleton.referenceScale", "skeleton", "4.2");
  for (const b of Array.isArray(raw.bones) ? raw.bones : []) {
    if (b.inherit !== undefined) add("bone 'inherit' key", b.name, "4.2");
    if (b.transform !== undefined) add("bone 'transform' key (4.1 name of inherit)", b.name, "<4.2", "inherit");
  }
  if (raw.skins && !Array.isArray(raw.skins) && typeof raw.skins === "object") {
    add("skins as a {name: attachments} object (pre-4.0)", `${Object.keys(raw.skins).length} skins`, "<4.2", "skins: [{name, attachments}]");
  }
  for (const skin of skinList(raw)) {
    if (size(skin.physics) > 0) add("skin physics[]", skin.name, "4.2");
    for (const [slot, atts] of Object.entries(skin.attachments ?? {})) {
      for (const [name, a] of Object.entries(atts ?? {})) {
        if (a && a.type === "linkedmesh" && a.deform !== undefined) add("linked mesh 'deform' key", `${skin.name}/${slot}/${name}`, "<4.2", "timelines");
      }
    }
  }
  for (const [anim, map] of Object.entries(raw.animations ?? {})) {
    if (size(map.physics) > 0) add("physics timelines", anim, "4.2");
    for (const [bone, tls] of Object.entries(map.bones ?? {})) if (tls && tls.inherit) add("inherit timeline", `${anim}/${bone}`, "4.2");
    if (map.deform !== undefined) add("animation 'deform' map", anim, "<4.2", "attachments/<skin>/<slot>/<attachment>/deform");
    if (map.draworder !== undefined) add("animation 'draworder' key", anim, "<4.2", "drawOrder");
  }
  if (Array.isArray(raw.constraints)) add("top-level constraints[]", `${raw.constraints.length} constraints`, "4.3");
  return found;
}

/**
 * Number of curve channels a timeline interpolates, i.e. a key's `curve`
 * array must hold 4 numbers per channel (cx1, cy1, cx2, cy2 each).
 * 0 means the timeline is stepped by nature and takes no curve.
 * Returns undefined for a timeline name this table does not know.
 */
export function curveChannels(group, name) {
  const table = {
    bones: { rotate: 1, translate: 2, translatex: 1, translatey: 1, scale: 2, scalex: 1, scaley: 1, shear: 2, shearx: 1, sheary: 1, inherit: 0 },
    slots: { rgba: 4, rgb: 3, rgba2: 7, rgb2: 6, alpha: 1, attachment: 0, color: 4, twocolor: 7 },
    path: { position: 1, spacing: 1, mix: 3 },
    physics: { inertia: 1, strength: 1, damping: 1, mass: 1, wind: 1, gravity: 1, mix: 1, reset: 0 },
    attachments: { deform: 1, sequence: 0 },
  };
  if (group === "ik") return 2; // mix, softness
  if (group === "transform") return 6; // mixRotate, mixX, mixY, mixScaleX, mixScaleY, mixShearY
  if (group === "deform") return 1;
  return table[group]?.[String(name).toLowerCase()];
}

/**
 * Every keyed timeline of every animation as {anim, group, owner, name, keys}.
 * `owner` is the bone/slot/constraint (or skin/slot/attachment path) keyed.
 */
export function timelines(raw) {
  const out = [];
  for (const [anim, map] of Object.entries(raw.animations ?? {})) {
    const push = (group, owner, name, keys) => { if (Array.isArray(keys)) out.push({ anim, group, owner, name, keys }); };
    for (const group of ["bones", "slots", "path", "physics"]) {
      for (const [owner, tls] of Object.entries(map[group] ?? {})) {
        for (const [name, keys] of Object.entries(tls ?? {})) push(group, owner, name, keys);
      }
    }
    for (const group of ["ik", "transform"]) {
      for (const [owner, keys] of Object.entries(map[group] ?? {})) push(group, owner, group, keys);
    }
    // 4.1+: attachments/<skin>/<slot>/<attachment>/{deform, sequence}
    for (const [skin, slots] of Object.entries(map.attachments ?? {})) {
      for (const [slot, atts] of Object.entries(slots ?? {})) {
        for (const [att, tls] of Object.entries(atts ?? {})) {
          for (const [name, keys] of Object.entries(tls ?? {})) push("attachments", `${skin}/${slot}/${att}`, name, keys);
        }
      }
    }
    // 4.0 and older: deform/<skin>/<slot>/<attachment> = keys
    for (const [skin, slots] of Object.entries(map.deform ?? {})) {
      for (const [slot, atts] of Object.entries(slots ?? {})) {
        for (const [att, keys] of Object.entries(atts ?? {})) push("deform", `${skin}/${slot}/${att}`, "deform", keys);
      }
    }
  }
  return out;
}

/**
 * A mesh attachment's vertex list decoded from the file.
 * Returns {count, weighted, influences: [[{bone, x, y, w}]] | null, error?}.
 * Weighted layout per vertex: boneCount, then boneCount x (bone, x, y, weight).
 */
export function decodeMeshVertices(map) {
  const uvs = Array.isArray(map.uvs) ? map.uvs : [];
  const verts = Array.isArray(map.vertices) ? map.vertices : [];
  const count = uvs.length >> 1;
  if (verts.length === uvs.length) return { count, weighted: false, influences: null };
  const influences = [];
  let i = 0;
  while (i < verts.length && influences.length < count) {
    const n = verts[i++];
    if (!Number.isInteger(n) || n < 1 || i + 4 * n > verts.length) {
      return { count, weighted: true, influences, error: `bad bone count ${n} at vertex ${influences.length}` };
    }
    const vertex = [];
    for (let k = 0; k < n; k++, i += 4) vertex.push({ bone: verts[i], x: verts[i + 1], y: verts[i + 2], w: verts[i + 3] });
    influences.push(vertex);
  }
  if (influences.length !== count || i !== verts.length) {
    return { count, weighted: true, influences, error: `weighted data holds ${influences.length} vertices / ${verts.length - i} spare numbers, uvs say ${count}` };
  }
  return { count, weighted: true, influences };
}

/**
 * Boundary edges of a triangle list (edges used by exactly one triangle),
 * as a Set of "lo,hi" strings. The hull of a Spine mesh is its outer
 * boundary, so these are what the first `hull` vertices must trace.
 */
export function boundaryEdges(triangles) {
  const uses = new Map();
  for (let k = 0; k + 2 < triangles.length; k += 3) {
    const t = [triangles[k], triangles[k + 1], triangles[k + 2]];
    for (let e = 0; e < 3; e++) {
      const a = t[e], b = t[(e + 1) % 3];
      const key = a < b ? `${a},${b}` : `${b},${a}`;
      uses.set(key, (uses.get(key) ?? 0) + 1);
    }
  }
  return new Set([...uses].filter(([, n]) => n === 1).map(([k]) => k));
}

/**
 * Whether UV point (x, y) lies strictly inside the polygon through the UVs of
 * `loop` (vertex indices), even-odd rule. L8 uses it to tell a hole enclosed
 * by the hull, which the editor sees as interior vertices, from an island
 * outside it, which no one-polygon hull can hold.
 */
export function insidePolygon(uvs, loop, x, y) {
  let inside = false;
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const xi = uvs[2 * loop[i]], yi = uvs[2 * loop[i] + 1], xj = uvs[2 * loop[j]], yj = uvs[2 * loop[j] + 1];
    if ((yi > y) !== (yj > y) && x < xi + ((y - yi) * (xj - xi)) / (yj - yi)) inside = !inside;
  }
  return inside;
}
