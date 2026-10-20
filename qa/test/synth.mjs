/**
 * A synthetic Spine 4.2 character built entirely by our own code, for the
 * QA self-test: a two-bone arm with a weighted 15-vertex mesh across the
 * elbow, a region hand, a half-angle transform constraint, a (mix 0) two-bone
 * IK, one rotate-physics tail, and a small atlas with a real PNG.
 *
 * WHY synthetic: the repo is public, so no third-party skeleton, atlas or
 * PNG may be committed; and a fixture we fully control can be broken one
 * rule at a time (see `variants`), so each gate is shown to catch exactly
 * the fault it exists for, and to pass a clean rig.
 */
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const COLS = [0, 50, 100, 150, 200];
const ROWS = [-20, 0, 20];

/**
 * Arm mesh in skeleton space: 3 rows x 5 columns. The 12 boundary vertices
 * come first, counter-clockwise from (0,-20); the 3 interior ones last.
 */
function armGeometry() {
  const at = (x, y) => [x, y];
  const hull = [
    ...COLS.map((x) => at(x, -20)), at(200, 0),
    ...COLS.slice().reverse().map((x) => at(x, 20)), at(0, 0),
  ];
  const interior = [at(50, 0), at(100, 0), at(150, 0)];
  const pts = [...hull, ...interior];
  const index = new Map(pts.map((p, i) => [p.join(","), i]));
  const id = (x, y) => index.get(`${x},${y}`);
  const triangles = [];
  for (let c = 0; c + 1 < COLS.length; c++) {
    for (let r = 0; r + 1 < ROWS.length; r++) {
      const [x0, x1, y0, y1] = [COLS[c], COLS[c + 1], ROWS[r], ROWS[r + 1]];
      triangles.push(id(x0, y0), id(x1, y0), id(x1, y1), id(x0, y0), id(x1, y1), id(x0, y1));
    }
  }
  return { pts, hull: hull.length, triangles };
}

/** Weighted vertices: upper below x=100, fore above, 50/50 on the elbow column. */
function armVertices(pts, boneIndex) {
  const out = [];
  for (const [x, y] of pts) {
    const wFore = x < 100 ? 0 : x > 100 ? 1 : 0.5;
    const inf = [];
    if (wFore < 1) inf.push([boneIndex.upper, x, y, 1 - wFore]); // upper sits at the origin, rotation 0
    if (wFore > 0) inf.push([boneIndex.fore, x - 100, y, wFore]); // fore sits at (100, 0)
    out.push(inf.length, ...inf.flat());
  }
  return out;
}

/** The clean skeleton: every hard gate passes. */
export function baseSkeleton() {
  const bones = [
    { name: "root" },
    { name: "shoulder", parent: "root" },
    { name: "upper", parent: "shoulder", length: 100 },
    { name: "fore", parent: "upper", x: 100, length: 100 },
    { name: "elbow_mid", parent: "upper", x: 100 },
    { name: "tail", parent: "fore", x: 100, length: 60, rotation: -90 },
    { name: "target", parent: "root", x: 200 },
  ];
  const boneIndex = Object.fromEntries(bones.map((b, i) => [b.name, i]));
  const g = armGeometry();
  const uvs = g.pts.flatMap(([x, y]) => [x / 200, (20 - y) / 40]);
  return {
    skeleton: { hash: "synthetic-arm", spine: "4.2.43", x: -10, y: -30, width: 280, height: 200, referenceScale: 100, images: "./" },
    bones,
    slots: [
      { name: "arm", bone: "upper", attachment: "arm" },
      { name: "hand", bone: "fore", attachment: "hand" },
    ],
    ik: [{ name: "ik_arm", order: 0, bones: ["upper", "fore"], target: "target", mix: 0, softness: 0 }],
    transform: [{
      name: "elbow_half", order: 1, bones: ["elbow_mid"], target: "fore", local: true, relative: true,
      mixRotate: 0.5, mixX: 0, mixY: 0, mixScaleX: 0, mixScaleY: 0, mixShearY: 0,
    }],
    physics: [{
      name: "tail_phys", order: 2, bone: "tail", rotate: 0.5, x: 0, y: 0, scaleX: 0, shearX: 0,
      fps: 60, inertia: 0.2, strength: 100, damping: 0.6, mass: 1, limit: 5000, wind: 0, gravity: 0, mix: 1,
    }],
    skins: [{
      name: "default",
      attachments: {
        arm: { arm: { type: "mesh", uvs, triangles: g.triangles, vertices: armVertices(g.pts, boneIndex), hull: g.hull, width: 200, height: 40 } },
        hand: { hand: { x: 120, y: 0, width: 40, height: 20 } },
      },
    }],
    animations: {
      wave: {
        bones: {
          fore: { rotate: [
            { time: 0, value: 0, curve: [0.133, 0, 0.267, 40] },
            { time: 0.4, value: 40, curve: [0.6, 40, 0.8, 0] },
            { time: 1, value: 0 },
          ] },
          shoulder: { translate: [
            { time: 0, x: 0, y: 0, curve: [0.333, 0, 0.667, 4, 0.333, 0, 0.667, 2] },
            { time: 1, x: 4, y: 2 },
          ] },
        },
      },
    },
  };
}

export const ATLAS = { page: "synth.png", width: 256, height: 64 };

export function atlasText({ width = ATLAS.width, height = ATLAS.height, handRegion = "hand" } = {}) {
  return [
    ATLAS.page, `size:${width},${height}`, "filter:Linear,Linear",
    "arm", "bounds:0,0,200,40",
    handRegion, "bounds:200,0,40,20",
    "",
  ].join("\n");
}

// ---------------------------------------------------------------- tiny PNG

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4), crc = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "latin1"), data]);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

/** A valid, fully transparent RGBA PNG of the given size. */
export function transparentPng(width, height) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr.set([8, 6, 0, 0, 0], 8); // 8-bit RGBA, no interlace
  const raw = Buffer.alloc((width * 4 + 1) * height); // filter byte 0 + zero pixels per row
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---------------------------------------------------------------- variants

const clone = (x) => structuredClone(x);
const armMesh = (sk) => sk.skins[0].attachments.arm.arm;

/** Swap vertices i and j of a mesh everywhere they appear (uvs, vertices, triangles). */
function swapMeshVertices(mesh, i, j) {
  const uv = (k) => mesh.uvs.slice(2 * k, 2 * k + 2);
  const [ui, uj] = [uv(i), uv(j)];
  mesh.uvs.splice(2 * i, 2, ...uj);
  mesh.uvs.splice(2 * j, 2, ...ui);
  const verts = [];
  for (let p = 0; p < mesh.vertices.length;) { const n = mesh.vertices[p]; verts.push(mesh.vertices.slice(p, p + 1 + 4 * n)); p += 1 + 4 * n; }
  [verts[i], verts[j]] = [verts[j], verts[i]];
  mesh.vertices = verts.flat();
  mesh.triangles = mesh.triangles.map((t) => (t === i ? j : t === j ? i : t));
}

/** Move weighted vertex i by dx in x (every influence: the arm's bones are unrotated at setup). */
function shiftVertexX(mesh, i, dx) {
  for (let p = 0, k = 0; p < mesh.vertices.length; k++) {
    const n = mesh.vertices[p];
    if (k === i) for (let j = 0; j < n; j++) mesh.vertices[p + 2 + 4 * j] += dx;
    p += 1 + 4 * n;
  }
}

/**
 * Rebuild the arm mesh as a finer grid (x 0..150, a row every 10) so a cell
 * can be cut out without touching the outline. `hole` drops the cell at
 * x 50..100, y -10..0: a hole enclosed by the hull, which Spine's one-polygon
 * hull allows (its rim is interior to the editor). `island` adds a detached
 * triangle at x 175..200, outside the hull polygon, which no hull can hold.
 */
function gridArm(sk, { hole = false, island = false }) {
  const cols = [0, 50, 100, 150], rows = [-20, -10, 0, 10, 20];
  const outline = [
    ...cols.map((x) => [x, -20]), ...rows.slice(1).map((y) => [150, y]),
    ...cols.slice(0, -1).reverse().map((x) => [x, 20]), ...rows.slice(1, -1).reverse().map((y) => [0, y]),
  ];
  const interior = cols.slice(1, -1).flatMap((x) => rows.slice(1, -1).map((y) => [x, y]));
  const pts = [...outline, ...interior];
  const index = new Map(pts.map((p, i) => [p.join(","), i]));
  const id = (x, y) => index.get(`${x},${y}`);
  const triangles = [];
  for (let c = 0; c + 1 < cols.length; c++) {
    for (let r = 0; r + 1 < rows.length; r++) {
      const [x0, x1, y0, y1] = [cols[c], cols[c + 1], rows[r], rows[r + 1]];
      if (hole && x0 === 50 && y0 === -10) continue;
      triangles.push(id(x0, y0), id(x1, y0), id(x1, y1), id(x0, y0), id(x1, y1), id(x0, y1));
    }
  }
  if (island) {
    const n = pts.length;
    pts.push([175, -20], [200, -20], [200, 0]);
    triangles.push(n, n + 1, n + 2);
  }
  const boneIndex = Object.fromEntries(sk.bones.map((b, i) => [b.name, i]));
  Object.assign(armMesh(sk), {
    uvs: pts.flatMap(([x, y]) => [x / 200, (20 - y) / 40]), triangles, vertices: armVertices(pts, boneIndex), hull: outline.length,
  });
}

/**
 * Rigs that must PASS a gate a naive rule would fail: name -> {skeleton}.
 * meshHole: a hole enclosed by the hull (L8). mirrored: the whole arm drawn
 * mirrored at setup through shoulder scaleX -1, the usual way to reuse
 * left/right limb art; every triangle's winding agrees with its UVs, which
 * is the mesh's setup orientation, not a fold (G1).
 */
export function legalVariants() {
  const hole = clone(baseSkeleton());
  gridArm(hole, { hole: true });
  const mirrored = clone(baseSkeleton());
  mirrored.bones[1].scaleX = -1;
  return { meshHole: { skeleton: hole }, mirrored: { skeleton: mirrored } };
}

/**
 * One fault per variant: name -> {skeleton, atlas?} where atlas overrides
 * atlasText options. Each is the clean rig with exactly one rule broken.
 */
export function variants() {
  const v = {};
  const make = (name, mutate, atlas) => { const sk = clone(baseSkeleton()); mutate(sk); v[name] = { skeleton: sk, atlas }; };
  make("declares41", (sk) => { sk.skeleton.spine = "4.1.20"; });
  make("transformKey", (sk) => { sk.bones[1].transform = "noScale"; });
  make("constraints43", (sk) => { sk.constraints = [{ type: "ik", name: "ik43", bones: ["upper", "fore"], target: "target" }]; });
  make("orderGap", (sk) => { sk.physics[0].order = 5; });
  make("orderDup", (sk) => { sk.physics[0].order = 1; }); // shares order 1 with elbow_half: never runs
  // older key names that SkeletonJson 4.2.120 ignores (L1); the reader ignores them too
  make("linkedDeform", (sk) => { sk.skins[0].attachments.arm.arm2 = { type: "linkedmesh", path: "arm", parent: "arm", deform: false, width: 200, height: 40 }; });
  make("animDeform", (sk) => { sk.animations.wave.deform = { default: { arm: { arm: [{ time: 0 }] } } }; });
  make("drawOrderOld", (sk) => { sk.animations.wave.draworder = [{ time: 0.5, offsets: [{ slot: "arm", offset: 1 }] }]; });
  make("skinsObject", (sk) => { sk.skins = { default: sk.skins[0].attachments }; });
  // vertex (0,0) dragged to (60,0) with its UV kept: two of its three
  // triangles turn over against the rest of the mesh, folded at rest (G1)
  make("setupFold", (sk) => { shiftVertexX(armMesh(sk), 11, 60); });
  make("shortCurve", (sk) => { sk.animations.wave.bones.shoulder.translate[0].curve = [0.333, 0, 0.667, 4]; });
  make("ikRootParent", (sk) => { sk.ik[0].bones = ["root", "shoulder"]; sk.ik[0].mix = 1; });
  make("ikInherit", (sk) => { sk.bones[2].inherit = "noScale"; });
  make("physicsImplicit", (sk) => { delete sk.physics[0].strength; sk.physics[0].limit = 60; });
  // inertia 0.5 / damping 0.85 on this tail swings 0.78 px apart at 60 vs 240 fps (limit 0.44 px at H=200)
  make("physicsFpsFragile", (sk) => { Object.assign(sk.physics[0], { inertia: 0.5, damping: 0.85 }); });
  make("atlasSize", () => {}, { width: 512 });
  make("missingRegion", () => {}, { handRegion: "hand_renamed" });
  make("hull0", (sk) => { armMesh(sk).hull = 0; });
  make("hullNotFirst", (sk) => { swapMeshVertices(armMesh(sk), 0, 13); });
  make("meshIsland", (sk) => { gridArm(sk, { island: true }); });
  make("weightSum", (sk) => { const m = armMesh(sk); m.vertices[4] = 0.9; }); // vertex 0's only weight
  make("fourInfluences", (sk) => {
    const m = armMesh(sk);
    // vertex 0 (at 0,-20, all on 'upper'): spread over four bones at the same world point
    m.vertices.splice(0, 5, 4, 2, 0, -20, 0.25, 1, 0, -20, 0.25, 0, 0, -20, 0.25, 6, -200, -20, 0.25);
  });
  make("tcMixImplicit", (sk) => { delete sk.transform[0].mixScaleX; });
  make("flip", (sk) => { sk.animations.mirror = { bones: { upper: { scale: [{ time: 0, x: 1, y: 1 }, { time: 0.5, x: -1, y: 1 }] } } }; });
  make("ikKeyed", (sk) => { sk.animations.wave.ik = { ik_arm: [{ time: 0, mix: 0 }] }; });
  return v;
}

/** Write skeleton + atlas + PNG for `name` into dir; returns {json, atlas}. */
export function writeFixture(dir, name, skeleton, atlas = {}) {
  fs.mkdirSync(dir, { recursive: true });
  const json = path.join(dir, `${name}.json`), atl = path.join(dir, `${name}.atlas`);
  fs.writeFileSync(json, JSON.stringify(skeleton));
  fs.writeFileSync(atl, atlasText(atlas));
  fs.writeFileSync(path.join(dir, ATLAS.page), transparentPng(ATLAS.width, ATLAS.height));
  return { json, atlas: atl };
}
