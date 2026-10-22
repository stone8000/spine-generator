/**
 * Self-test of the QA oracle on spine-core 4.3.13 (`--runtime 4.3`).
 *
 * WHY: Spine 4.3 is the primary target, so its runtime must judge 4.3 files
 * with the same gates as 4.2, and the 4.3-only ways a file goes wrong
 * silently (a 4.2 spelling in a 4.3 file, a leftover `order`, a transform
 * mix keyed for a property the constraint does not drive) must fail by name.
 * Same method as qa.test.mjs: the clean synthetic arm passes every hard
 * gate; each variant breaks one rule and must fail by that gate. All
 * fixtures are synthetic (test/synth.mjs). Skipped when qa43/ is not
 * installed (npm ci in qa43/).
 */
import { test, describe, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { baseSkeleton, baseSkeleton43, variants43, writeFixture } from "./synth.mjs";

const QA = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const RUN = path.join(QA, "run.mjs");
const HAVE43 = fs.existsSync(path.join(QA, "..", "qa43", "node_modules", "@esotericsoftware", "spine-core"));

function node(args) {
  const r = spawnSync(process.execPath, [RUN, ...args], { encoding: "utf8", maxBuffer: 1 << 28 });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr };
}

function check(f, ...extra) {
  const r = node(["check", "--runtime", "4.3", "--skeleton", f.json, "--atlas", f.atlas, ...extra]);
  assert.ok(r.code === 0 || r.code === 1, `check exited ${r.code}: ${r.stderr}`);
  const report = JSON.parse(r.stdout);
  return { code: r.code, report, gate: (id) => report.gates.find((g) => g.id === id) };
}

function dump(f, runtime, ...extra) {
  const r = node(["dump", "--runtime", runtime, "--skeleton", f.json, "--atlas", f.atlas, ...extra]);
  assert.equal(r.code, 0, r.stderr);
  return JSON.parse(r.stdout);
}

const failed = (report) => report.gates.filter((g) => g.hard && !g.pass).map((g) => g.id);

let dir;
const fx = {};
before(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "spinegen-qa43-"));
  fx.base = writeFixture(path.join(dir, "base"), "arm", baseSkeleton43());
  fx.base42 = writeFixture(path.join(dir, "base42"), "arm", baseSkeleton());
  for (const [name, v] of Object.entries(variants43())) fx[name] = writeFixture(path.join(dir, name), "arm", v.skeleton, v.atlas);
});
after(() => fs.rmSync(dir, { recursive: true, force: true }));

describe("4.3 judge: clean synthetic arm", { skip: !HAVE43 && "npm ci in qa43/ first" }, () => {
  test("every hard gate passes on spine-core 4.3.13, exit 0; C1 is skipped as 4.2-only", () => {
    const { code, report, gate } = check(fx.base);
    assert.deepEqual(failed(report), []);
    assert.equal(code, 0);
    assert.equal(report.runtime, "@esotericsoftware/spine-core@4.3.13");
    assert.equal(report.input.target, "4.3");
    const ids = report.gates.map((g) => g.id);
    for (const id of ["L1_VERSION", "L2_COUNTS", "L3_ORDER", "L4_CURVES_NAN", "L5_IK_VALID", "L6_PHYSICS_VALID", "L7_ATLAS",
      "L8_MESH", "L9_TC_MIX", "G1_FOLDS", "G3_FLIP", "M1_FOOT_PLANT", "P1_FPS_ROBUST", "P3_DETERMINISM"]) {
      assert.ok(ids.includes(id), id);
    }
    assert.deepEqual([gate("L2_COUNTS").data.runtime.ik, gate("L2_COUNTS").data.runtime.transform, gate("L2_COUNTS").data.runtime.physics], [1, 1, 1]);
    assert.equal(gate("C1_ORACLE").skipped, true);
    assert.match(gate("C1_ORACLE").note, /^4\.2 only/);
    assert.equal(gate("P1_FPS_ROBUST").skipped, undefined);
  });

  test("a 4.2 file on the 4.3 judge: L1 and L2 say the constraints are gone", () => {
    const { code, gate } = check(fx.base42);
    assert.equal(code, 1);
    assert.ok(gate("L1_VERSION").items.some((s) => /top-level ik\[\] .*4\.3 reads only constraints\[\]/.test(s)));
    assert.ok(gate("L2_COUNTS").items.some((s) => s.startsWith("ik: runtime 0 vs file 1")));
  });

  test("G1: elbow +170 folds the arm on 4.3 too", () => {
    assert.equal(dump(fx.base, "4.3", "--folds").frames[0].folds, 0);
    assert.ok(dump(fx.base, "4.3", "--folds", "--pose", '{"fore":{"rotation":170}}').frames[0].folds >= 1);
    assert.ok(check(fx.base, "--pose", '{"fore":{"rotation":170}}').gate("G1_FOLDS").value >= 1);
  });

  test("M1 with --ir: a static foot passes, a moving bone fails", () => {
    const ir = (bone) => {
      const p = path.join(dir, `ir43_${bone}.json`);
      fs.writeFileSync(p, JSON.stringify({ foot_plant: [{ bone, anim: "wave" }] }));
      return p;
    };
    assert.equal(check(fx.base, "--ir", ir("target")).gate("M1_FOOT_PLANT").pass, true);
    assert.equal(check(fx.base, "--ir", ir("fore")).gate("M1_FOOT_PLANT").pass, false);
  });

  test("L2 and L3 against --ir: counts, and the IR's evaluation order", () => {
    const good = path.join(dir, "ir43_order.json");
    fs.writeFileSync(good, JSON.stringify({
      counts: { bones: 7, meshes: 1, ik: 1, transform: 1, physics: 1 },
      ik: [{ name: "ik_arm", order: 0 }], transform: [{ name: "elbow_half", order: 1 }], physics: [{ name: "tail_phys", order: 2 }],
    }));
    const ok = check(fx.base, "--ir", good);
    assert.equal(ok.gate("L2_COUNTS").pass, true);
    assert.equal(ok.gate("L3_ORDER").pass, true);
    assert.match(ok.gate("L3_ORDER").note, /list order = IR evaluation order/);
    const bad = path.join(dir, "ir43_swapped.json");
    fs.writeFileSync(bad, JSON.stringify({
      counts: { physics: 2 },
      ik: [{ name: "ik_arm", order: 1 }], transform: [{ name: "elbow_half", order: 0 }], physics: [{ name: "tail_phys", order: 2 }],
    }));
    const no = check(fx.base, "--ir", bad);
    assert.deepEqual(no.gate("L2_COUNTS").items, ["physics: runtime 1 vs IR 2"]);
    assert.equal(no.gate("L3_ORDER").pass, false);
    assert.ok(no.gate("L3_ORDER").items.includes("position 0: runtime runs ik ik_arm, IR order has transform elbow_half"));
  });
});

describe("4.3 judge: one fault per gate", { skip: !HAVE43 && "npm ci in qa43/ first" }, () => {
  const expectFail = {
    declares42: "L1_VERSION",
    topLevelIk: "L1_VERSION",
    unknownType: "L1_VERSION",
    target42: "L1_VERSION",
    linkedParent: "L1_VERSION",
    uniform42: "L1_VERSION",
    orderKey: "L3_ORDER",
    dupName: "L3_ORDER",
    shortCurve: "L4_CURVES_NAN",
    ikRootParent: "L5_IK_VALID",
    ikInherit: "L5_IK_VALID",
    physicsImplicit: "L6_PHYSICS_VALID",
    physicsFpsFragile: "P1_FPS_ROBUST",
    atlasSize: "L7_ATLAS",
    missingRegion: "L7_ATLAS",
    hull0: "L8_MESH",
    weightSum: "L8_MESH",
    tcMixImplicit: "L9_TC_MIX",
    tcNoProperties: "L9_TC_MIX",
    tcKeyedDeadMix: "L9_TC_MIX",
    setupFold: "G1_FOLDS",
    flip: "G3_FLIP",
  };
  for (const [name, id] of Object.entries(expectFail)) {
    test(`${name} fails ${id}`, () => {
      const { code, report } = check(fx[name]);
      assert.ok(failed(report).includes(id), `${name}: failed ${JSON.stringify(failed(report))}`);
      assert.equal(code, 1);
    });
  }

  test("items name the 4.3 spelling", () => {
    const item = (name, id) => check(fx[name]).gate(id).items.join("\n");
    assert.match(item("target42", "L1_VERSION"), /4\.2 key 'target' in a 4\.3 transform \(4\.3 reads 'source'\)/);
    assert.match(item("linkedParent", "L1_VERSION"), /linked mesh default\/arm\/arm2: no 'source' \(4\.2 'parent' is not read\)/);
    assert.match(item("unknownType", "L1_VERSION"), /type "IK" is not read by 4\.3/);
    assert.match(item("orderKey", "L3_ORDER"), /physics tail_phys: 'order' 0 but runs at list position 2/);
    assert.match(item("tcKeyedDeadMix", "L9_TC_MIX"), /wave\/transform\/elbow_half: keys mixX, which drive nothing in 4\.3/);
  });

  test("ikRootParent: 4.3 throws mid-playback too, gates fail by name", () => {
    assert.match(check(fx.ikRootParent).gate("G1_FOLDS").note, /spine-core threw/);
  });
});

describe("dump --runtime 4.3", { skip: !HAVE43 && "npm ci in qa43/ first" }, () => {
  test("same frame shape as 4.2; region quad BL, UL, UR, BR", () => {
    const out = dump(fx.base, "4.3");
    assert.equal(out.runtime, "@esotericsoftware/spine-core@4.3.13");
    const f = out.frames[0];
    assert.deepEqual(Object.keys(f.bones.fore).sort(), ["a", "b", "c", "d", "x", "y"]);
    assert.equal(f.slots.arm.vertices.length, 30);
    assert.deepEqual(f.slots.hand.vertices.map((v) => Math.round(v * 1e3) / 1e3), [200, -10, 200, 10, 240, 10, 240, -10]);
  });

  test("the same rig in each version's layout plays the same, physics off", () => {
    const a = dump(fx.base42, "4.2", "--anim", "wave", "--fps", "30", "--physics", "none").frames;
    const b = dump(fx.base, "4.3", "--anim", "wave", "--fps", "30", "--physics", "none").frames;
    assert.equal(a.length, b.length);
    let worst = 0;
    a.forEach((fa, i) => {
      for (const [slot, sa] of Object.entries(fa.slots)) {
        sa.vertices.forEach((v, k) => { worst = Math.max(worst, Math.abs(v - b[i].slots[slot].vertices[k])); });
      }
    });
    assert.ok(worst < 1e-6, `max vertex difference ${worst}`);
  });

  test("--runtime must be 4.2 or 4.3", () => {
    assert.equal(node(["dump", "--runtime", "5.0", "--skeleton", fx.base.json, "--atlas", fx.base.atlas]).code, 2);
  });
});
