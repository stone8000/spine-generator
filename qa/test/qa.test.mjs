/**
 * Self-test of the QA oracle: `node --test` from qa/ (npm test).
 *
 * WHY end-to-end through the CLI: the exit code and the report JSON are the
 * contract the Python build relies on, so they are what is tested. Each gate
 * gets the clean synthetic rig (must pass) and one fault built for it (must
 * fail BY THAT GATE). The third-party characters (Hard Time, Go Bananas) are
 * read from their absolute paths and the tests skip when they are absent;
 * nothing third-party is ever copied into the repo.
 */
import { test, describe, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { baseSkeleton, legalVariants, variants, writeFixture } from "./synth.mjs";

const QA = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const RUN = path.join(QA, "run.mjs");
const LOAD43 = path.join(QA, "..", "qa43", "load.mjs");

function node(script, args, env = {}) {
  const r = spawnSync(process.execPath, [script, ...args], { encoding: "utf8", maxBuffer: 1 << 28, env: { ...process.env, ...env } });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr };
}

function checkWith(env, f, ...extra) {
  const r = node(RUN, ["check", "--skeleton", f.json, "--atlas", f.atlas, ...extra], env);
  assert.ok(r.code === 0 || r.code === 1, `check exited ${r.code}: ${r.stderr}`);
  const report = JSON.parse(r.stdout);
  const gate = (id) => report.gates.find((g) => g.id === id);
  return { code: r.code, report, gate };
}

const check = (f, ...extra) => checkWith({}, f, ...extra);

function dump(f, ...extra) {
  const r = node(RUN, ["dump", "--skeleton", f.json, "--atlas", f.atlas, ...extra]);
  assert.equal(r.code, 0, r.stderr);
  return JSON.parse(r.stdout);
}

/** Hard gates that failed, by id. */
const failed = (report) => report.gates.filter((g) => g.hard && !g.pass).map((g) => g.id);

let dir;
const fixtures = {};
before(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "spinegen-qa-"));
  fixtures.base = writeFixture(path.join(dir, "base"), "arm", baseSkeleton());
  for (const [name, v] of Object.entries({ ...variants(), ...legalVariants() })) {
    fixtures[name] = writeFixture(path.join(dir, name), "arm", v.skeleton, v.atlas);
  }
});
after(() => fs.rmSync(dir, { recursive: true, force: true }));

describe("synthetic arm: clean rig", () => {
  test("every hard gate passes, exit 0", () => {
    const { code, report } = check(fixtures.base);
    assert.deepEqual(failed(report), []);
    assert.equal(code, 0);
    assert.equal(report.schema, "spinegen-qa/1");
    assert.equal(report.runtime, "@esotericsoftware/spine-core@4.2.120");
    for (const g of report.gates) for (const k of ["id", "pass", "value", "max"]) assert.ok(k in g, `${g.id} lacks ${k}`);
  });

  test("M1 is skipped without --ir; C1 is measured without --c1 (never skipped)", () => {
    const { gate } = check(fixtures.base);
    assert.equal(gate("M1_FOOT_PLANT").skipped, true);
    const c1 = gate("C1_ORACLE");
    assert.equal(c1.skipped, undefined);
    assert.equal(c1.pass, true);
    assert.ok(c1.value <= 0.1);
    assert.match(c1.note, /measured by python -m spinegen\.oracle_check --all/);
    assert.match(c1.note, /bone origins .* bone matrices/);
  });

  test("C1 that cannot be measured fails the run", () => {
    const { code, gate } = checkWith({ SPINEGEN_PYTHON: path.join(dir, "no-such-python") }, fixtures.base);
    assert.equal(gate("C1_ORACLE").pass, false);
    assert.match(gate("C1_ORACLE").note, /^not measured/);
    assert.equal(code, 1);
  });

  test("G1: elbow +170 folds the arm mesh; setup has none", () => {
    assert.equal(dump(fixtures.base, "--folds").frames[0].folds, 0);
    assert.ok(dump(fixtures.base, "--folds", "--pose", '{"fore":{"rotation":170}}').frames[0].folds >= 1);
    const { code, gate } = check(fixtures.base, "--pose", '{"fore":{"rotation":170}}');
    assert.ok(gate("G1_FOLDS").value >= 1);
    assert.equal(code, 1);
  });

  test("M1: a static foot passes, a moving bone fails", () => {
    const ir = (bone) => {
      const p = path.join(dir, `ir_${bone}.json`);
      fs.writeFileSync(p, JSON.stringify({ foot_plant: [{ bone, anim: "wave" }] }));
      return p;
    };
    assert.equal(check(fixtures.base, "--ir", ir("target")).gate("M1_FOOT_PLANT").pass, true);
    const moving = check(fixtures.base, "--ir", ir("fore")).gate("M1_FOOT_PLANT");
    assert.equal(moving.pass, false);
    assert.ok(moving.value > 1);
  });

  test("L2 compares with --ir counts", () => {
    const p = path.join(dir, "ir_counts.json");
    fs.writeFileSync(p, JSON.stringify({ counts: { bones: 7, meshes: 1, ik: 1, transform: 1, physics: 2 } }));
    const g = check(fixtures.base, "--ir", p).gate("L2_COUNTS");
    assert.equal(g.pass, false);
    assert.deepEqual(g.items, ["physics: runtime 1 vs IR 2"]);
  });

  test("--c1 merges the Python oracle result as a hard gate", () => {
    const p = path.join(dir, "c1.json");
    fs.writeFileSync(p, JSON.stringify({ value: 0.25, max: 0.1, pass: false, worst: { slot: "arm" } }));
    const { code, gate } = check(fixtures.base, "--c1", p);
    assert.equal(gate("C1_ORACLE").pass, false);
    assert.equal(code, 1);
  });
});

describe("synthetic arm: one fault per gate", () => {
  const expectFail = {
    declares41: "L1_VERSION", // a 4.2 physics[] in a file declaring 4.1.20
    transformKey: "L1_VERSION", // 4.1 bone key that 4.2 ignores
    constraints43: "L1_VERSION",
    orderGap: "L3_ORDER",
    orderDup: "L3_ORDER",
    linkedDeform: "L1_VERSION",
    animDeform: "L1_VERSION",
    drawOrderOld: "L1_VERSION",
    skinsObject: "L1_VERSION",
    setupFold: "G1_FOLDS",
    shortCurve: "L4_CURVES_NAN",
    ikRootParent: "L5_IK_VALID",
    ikInherit: "L5_IK_VALID",
    physicsImplicit: "L6_PHYSICS_VALID",
    physicsFpsFragile: "P1_FPS_ROBUST",
    atlasSize: "L7_ATLAS",
    missingRegion: "L7_ATLAS",
    hull0: "L8_MESH",
    hullNotFirst: "L8_MESH",
    meshIsland: "L8_MESH",
    weightSum: "L8_MESH",
    tcMixImplicit: "L9_TC_MIX",
    flip: "G3_FLIP",
  };
  for (const [name, id] of Object.entries(expectFail)) {
    test(`${name} fails ${id}`, () => {
      const { code, report } = check(fixtures[name]);
      assert.ok(failed(report).includes(id), `${name}: failed ${JSON.stringify(failed(report))}`);
      assert.equal(code, 1);
    });
  }

  test("meshHole: a hole enclosed by the hull is legal; L8 notes it, nothing else fails", () => {
    const { code, report, gate } = check(fixtures.meshHole);
    assert.equal(gate("L8_MESH").pass, true);
    assert.match(gate("L8_MESH").note, /hole rims inside the hull.*arm\/arm \(4\)/);
    assert.deepEqual(failed(report), []);
    assert.equal(code, 0);
  });

  test("orderDup: L3 names the constraint 4.2 never runs", () => {
    const g = check(fixtures.orderDup).gate("L3_ORDER");
    assert.ok(g.items.includes("never runs in 4.2: physics tail_phys (order 1)"), JSON.stringify(g.items));
  });

  test("older key names: L1 says what 4.2 reads instead", () => {
    const item = (name) => check(fixtures[name]).gate("L1_VERSION").items.find((s) => s.includes("ignore it"));
    assert.match(item("linkedDeform"), /linked mesh 'deform' key on default\/arm\/arm2: .*write 'timelines'/);
    assert.match(item("drawOrderOld"), /'draworder' key on wave: .*write 'drawOrder'/);
    assert.match(item("skinsObject"), /skins as a \{name: attachments\} object/);
  });

  test("mirrored: a mesh mirrored at setup is not folded; bending it past the fold still is", () => {
    const { code, report, gate } = check(fixtures.mirrored);
    assert.deepEqual(failed(report), []);
    assert.equal(code, 0);
    assert.equal(gate("G1_FOLDS").value, 0);
    assert.match(gate("G1_FOLDS").note, /mirrored at setup .*arm\/arm/);
    assert.equal(gate("G3_FLIP").pass, true);
    assert.equal(dump(fixtures.mirrored, "--folds").frames[0].folds, 0);
    assert.ok(dump(fixtures.mirrored, "--folds", "--pose", '{"fore":{"rotation":170}}').frames[0].folds >= 1);
  });

  test("setupFold: a triangle folded at rest is listed as a setup fold", () => {
    const g = check(fixtures.setupFold).gate("G1_FOLDS");
    assert.equal(g.value, 2);
    assert.deepEqual(g.items, ["arm/arm: 2 of 16 triangles already fold at setup (against the mesh's majority winding)"]);
  });

  test("meshIsland: the island's vertices are named as outside the hull polygon", () => {
    const g = check(fixtures.meshIsland).gate("L8_MESH");
    assert.deepEqual(g.items, ["arm/arm: 3 boundary vertices after the first 14 lie outside the hull polygon (hull not first, or an island no one-polygon hull can hold)"]);
  });

  test("constraints43: the 4.2 runtime drops 4.3 constraints[], L2 sees it", () => {
    const g = check(fixtures.constraints43).gate("L2_COUNTS");
    assert.equal(g.pass, false);
    assert.ok(g.items.some((s) => s.startsWith("ik: runtime 1 vs file 2")));
  });

  test("ikRootParent: the runtime throws mid-playback, gates fail by name", () => {
    const { gate } = check(fixtures.ikRootParent);
    assert.match(gate("G1_FOLDS").note, /spine-core threw/);
  });

  test("fourInfluences: a warning for imported files, hard with --ir", () => {
    const plain = check(fixtures.fourInfluences);
    const g = plain.gate("L8_MESH_INFLUENCES");
    assert.equal(g.pass, false);
    assert.equal(g.hard, false);
    assert.ok(!failed(plain.report).includes("L8_MESH_INFLUENCES"));
    const p = path.join(dir, "ir_empty.json");
    fs.writeFileSync(p, "{}");
    assert.ok(failed(check(fixtures.fourInfluences, "--ir", p).report).includes("L8_MESH_INFLUENCES"));
  });
});

describe("dump: the shared interface", () => {
  test("setup frame shape; region quad is BL, UL, UR, BR", () => {
    const out = dump(fixtures.base);
    assert.equal(out.runtime, "@esotericsoftware/spine-core@4.2.120");
    assert.equal(out.frames.length, 1);
    const f = out.frames[0];
    assert.equal(f.anim, null);
    assert.equal(f.t, 0);
    assert.deepEqual(Object.keys(f.bones.fore).sort(), ["a", "b", "c", "d", "x", "y"]);
    assert.equal(f.slots.arm.attachment, "arm");
    assert.equal(f.slots.arm.vertices.length, 30);
    const hand = f.slots.hand.vertices.map((v) => Math.round(v * 1e3) / 1e3);
    assert.deepEqual(hand, [200, -10, 200, 10, 240, 10, 240, -10]);
  });

  test("animation frames: 1/fps grid from 0 to the duration inclusive", () => {
    const out = dump(fixtures.base, "--anim", "wave", "--fps", "30");
    assert.equal(out.frames.length, 31);
    assert.equal(out.frames[30].t, 1);
    assert.ok(out.frames.every((f) => f.anim === "wave"));
  });

  test("--times on the grid equal the full run; off-grid times do not disturb them", () => {
    const full = dump(fixtures.base, "--anim", "wave").frames;
    const some = dump(fixtures.base, "--anim", "wave", "--times", "0.5,0.123,0").frames;
    assert.deepEqual(some.map((f) => f.t), [0, 0.123, 0.5]);
    assert.deepEqual(some[2].bones, full[30].bones);
    assert.deepEqual(some[2].slots, full[30].slots);
  });

  test("--pose add vs set", () => {
    const add = dump(fixtures.base, "--pose", '{"fore":{"rotation":90}}').frames[0].bones.fore;
    const set = dump(fixtures.base, "--pose-mode", "set", "--pose", '{"fore":{"rotation":90}}').frames[0].bones.fore;
    assert.ok(Math.abs(add.c - 1) < 1e-6); // setup rotation 0 + 90
    assert.deepEqual(add, set);
  });

  test("--dt const steps exactly 1/fps; the default grid dt lands on k/fps", () => {
    const grid = dump(fixtures.base, "--anim", "wave", "--fps", "30").frames;
    const cnst = dump(fixtures.base, "--anim", "wave", "--fps", "30", "--dt", "const").frames;
    assert.deepEqual(cnst.map((f) => f.t), grid.map((f) => f.t));
    let acc = 0;
    cnst.forEach((f, k) => { if (k) acc += 1 / 30; assert.equal(f.trackTime, acc); });
  });

  test("bad usage exits 2", () => {
    const f = fixtures.base;
    assert.equal(node(RUN, ["dump", "--skeleton", f.json, "--atlas", f.atlas, "--anim", "nope"]).code, 2);
    assert.equal(node(RUN, ["dump", "--skeleton", f.json, "--atlas", f.atlas, "--pose", '{"nope":{"rotation":1}}']).code, 2);
    assert.equal(node(RUN, ["dump", "--skeleton", f.json, "--atlas", f.atlas, "--dt", "wobbly"]).code, 2);
    assert.equal(node(RUN, ["frobnicate"]).code, 2);
  });
});

describe("pinned runtimes (SOTA §3.3)", () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(QA, "package.json"), "utf8"));
  const installed = (name) => JSON.parse(fs.readFileSync(path.join(QA, "node_modules", ...name.split("/"), "package.json"), "utf8"));

  test("spine-core and spine-canvaskit are exact 4.2.120 devDependencies", () => {
    for (const name of ["@esotericsoftware/spine-core", "@esotericsoftware/spine-canvaskit"]) {
      assert.equal(pkg.devDependencies[name], "4.2.120", `${name} must be pinned exactly`);
      assert.equal(installed(name).version, "4.2.120", `${name} installed version`);
    }
  });

  test("spine-canvaskit uses the same spine-core 4.2.120 (one deduped copy)", async () => {
    assert.equal(installed("@esotericsoftware/spine-canvaskit").dependencies["@esotericsoftware/spine-core"], "4.2.120");
    assert.ok(!fs.existsSync(path.join(QA, "node_modules", "@esotericsoftware", "spine-canvaskit", "node_modules", "@esotericsoftware", "spine-core")));
    const [ck, core] = await Promise.all([import("@esotericsoftware/spine-canvaskit"), import("@esotericsoftware/spine-core")]);
    assert.equal(ck.Skeleton, core.Skeleton);
    assert.equal(typeof ck.SkeletonRenderer, "function");
  });
});

describe("qa43: the same file on spine-core 4.3.13", { skip: !fs.existsSync(path.join(QA, "..", "qa43", "node_modules")) && "npm install in qa43/ first" }, () => {
  test("unreferenced 4.2 constraints are dropped silently", () => {
    const f = fixtures.base;
    const r = node(LOAD43, ["--skeleton", f.json, "--atlas", f.atlas]);
    assert.equal(r.code, 1);
    const out = JSON.parse(r.stdout);
    assert.equal(out.counts.constraints, 0);
    assert.equal(out.json.constraints, 3);
  });

  test("a 4.2 file whose animation keys an IK by name throws", () => {
    const f = fixtures.ikKeyed;
    const r = node(LOAD43, ["--skeleton", f.json, "--atlas", f.atlas]);
    assert.equal(r.code, 1);
    assert.match(JSON.parse(r.stdout).threw, /not found/i);
  });
});

// ------------------------------------------------- third-party acceptance (P1 row of SOTA §7)

const HT = "/home/user/chenyuping1998/wp/apps/HardTime/static/assets/spines/htCast";
const MONKEY = "/home/user/hacksaw-character-motion/gobananas_monkey/spines/bananditBandit";
const pair = (dir, name) => ({ json: path.join(dir, `${name}.json`), atlas: path.join(dir, `${name}.atlas`) });
const absent = (p) => !fs.existsSync(p.json) && `not on this machine: ${p.json}`;

describe("Hard Time warden", { skip: absent(pair(HT, "warden")) }, () => {
  const warden = pair(HT, "warden");

  test("L2: 24 bones, 20 meshes, 4 IK, 4 physics; L8: 20 meshes with hull=0", () => {
    const { gate } = check(warden);
    const rt = gate("L2_COUNTS").data.runtime;
    assert.deepEqual([rt.bones, rt.meshes, rt.ik, rt.physics], [24, 20, 4, 4]);
    assert.equal(gate("L2_COUNTS").pass, true);
    assert.equal(gate("L8_MESH").value, 20);
    assert.equal(gate("L8_MESH").items.filter((s) => s.endsWith("hull=0")).length, 20);
  });

  test("G1: 0 folds over react (and every animation); chest +150 folds", () => {
    assert.equal(check(warden).gate("G1_FOLDS").value, 0);
    const react = dump(warden, "--anim", "react", "--folds").frames;
    assert.ok(react.every((f) => f.folds === 0));
    assert.ok(dump(warden, "--folds", "--pose", '{"chest":{"rotation":150}}').frames[0].folds >= 1);
    assert.ok(check(warden, "--pose", '{"chest":{"rotation":150}}').gate("G1_FOLDS").value >= 1);
  });

  test("P1 per animation: react 1.92/1.20 px (grid dt); --dt const reproduces [E3]'s 1.39/0.90", () => {
    const react = (...extra) => check(warden, ...extra).gate("P1_FPS_ROBUST").data.per_anim.react;
    const grid = react(), cnst = react("--dt", "const");
    assert.deepEqual([grid[30], grid[60]].map((x) => x.toFixed(2)), ["1.92", "1.20"]);
    assert.deepEqual([cnst[30], cnst[60]].map((x) => x.toFixed(2)), ["1.39", "0.90"]);
  });

  test("on 4.3.13 every constraint is lost", { skip: !fs.existsSync(path.join(QA, "..", "qa43", "node_modules")) }, () => {
    const r = node(LOAD43, ["--skeleton", warden.json, "--atlas", warden.atlas]);
    const out = JSON.parse(r.stdout);
    assert.ok(out.threw || out.counts.constraints === 0);
    assert.equal(r.code, 1);
  });
});

describe("Hard Time prisoner", { skip: absent(pair(HT, "prisoner")) }, () => {
  test("loads; counts agree with the file", () => {
    assert.equal(check(pair(HT, "prisoner")).gate("L2_COUNTS").pass, true);
  });
});

describe("Go Bananas monkey", { skip: absent(pair(MONKEY, "monkey")) }, () => {
  test("L1 fails: declares 4.1.20 but carries 4.2 physics", () => {
    const g = check(pair(MONKEY, "monkey")).gate("L1_VERSION");
    assert.equal(g.pass, false);
    assert.ok(g.items.some((s) => /physics.*4\.2 feature.*4\.1\.20/.test(s)));
  });
});
