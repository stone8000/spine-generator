#!/usr/bin/env node
/**
 * spinegen QA oracle: the official spine-core 4.2.120 runtime as the judge.
 *
 *   node qa/run.mjs dump  --skeleton x.json --atlas x.atlas [--anim name] [--times 0,0.1,..]
 *                         [--fps 60] [--physics reset|update|none] [--pose JSON|@file]
 *                         [--pose-mode add|set] [--constraints on|off] [--skin name]
 *                         [--folds] [--dt grid|const] [--out file]
 *   node qa/run.mjs check --skeleton x.json --atlas x.atlas [--target 4.2] [--ir build/rig.json]
 *                         [--fps 30,60,120,240] [--pose JSON|@file] [--pma true|false]
 *                         [--c1 c1.json] [--skin name] [--dt grid|const]
 *                         [--out report.json]
 *
 * WHY two commands: `dump` is the raw oracle (world bone matrices and world
 * vertices exactly as a 4.2 player computes them) that the Python side's
 * numpy LBS is compared against (gate C1 lives there); `check` turns the
 * same runtime into named pass/fail gates (SOTA §5) so a build can "fail by
 * name and write nothing". Exit codes: 0 pass, 1 a hard gate failed,
 * 2 bad usage or an input that cannot be read.
 */
import fs from "node:fs";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { RUNTIME, load, play, slotVertices, countFolds, foldReferences, validatePose, skeletonHeight } from "./lib/runtime.mjs";
import { makeGate } from "./lib/gate.mjs";
import {
  gateVersion, gateCounts, gateOrder, curveFindings, loadedTimelineFindings, gateIk, gatePhysics,
  gateAtlas, missingRegionsFromFile, gateMesh, gateTcMix,
} from "./lib/static_gates.mjs";
import { sweep, gateFolds, gateFlip, gateFootPlant, gateFpsRobust, gateDeterminism } from "./lib/motion_gates.mjs";

class UsageError extends Error {}

const FLAGS = new Set(["folds", "help"]);

/** `--key value`, `--key=value` and bare boolean flags; first positional is the command. */
function parseArgs(argv) {
  const opts = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) { opts._.push(a); continue; }
    const eq = a.indexOf("=");
    const key = a.slice(2, eq < 0 ? undefined : eq);
    if (eq >= 0) opts[key] = a.slice(eq + 1);
    else if (FLAGS.has(key)) opts[key] = true;
    else {
      if (i + 1 >= argv.length) throw new UsageError(`--${key} needs a value`);
      opts[key] = argv[++i];
    }
  }
  return opts;
}

/** Re-throw a validation error as a usage error (exit 2, no stack trace). */
function asUsage(fn) {
  try { return fn(); } catch (e) { throw new UsageError(e.message); }
}

function need(opts, key) {
  if (!opts[key]) throw new UsageError(`--${key} is required`);
  return opts[key];
}

/** JSON given inline or as @path. */
function readJsonArg(text, what) {
  try {
    return JSON.parse(text.startsWith("@") ? fs.readFileSync(text.slice(1), "utf8") : text);
  } catch (e) {
    throw new UsageError(`${what}: ${e.message}`);
  }
}

/** --dt grid (k/fps - (k-1)/fps, the default) | const (1/fps every step, as [E3] stepped). */
function dtOpt(opts) {
  const v = opts.dt ?? "grid";
  if (!["grid", "const"].includes(v)) throw new UsageError("--dt must be grid or const");
  return v;
}

function numberList(text, what) {
  const xs = String(text).split(",").map((s) => Number(s.trim()));
  if (!xs.length || xs.some((x) => !Number.isFinite(x) || x < 0)) throw new UsageError(`${what} must be a comma list of numbers >= 0`);
  return xs;
}

function loadInputs(opts) {
  const skeleton = need(opts, "skeleton"), atlas = need(opts, "atlas");
  for (const f of [skeleton, atlas]) if (!fs.existsSync(f)) throw new UsageError(`no such file: ${f}`);
  return { skeleton, atlas, L: load(skeleton, atlas) };
}

function writeJson(obj, out) {
  const text = JSON.stringify(obj);
  if (out) fs.writeFileSync(out, text + "\n");
  else process.stdout.write(text + "\n");
}

// ---------------------------------------------------------------------- dump

/**
 * One frame in the shared interface: bones (world matrix + origin) and slots
 * (attachment + world vertices). Animated frames add `trackTime`, the time
 * spine-core actually applied, so the Python side samples its IR at exactly
 * that float instead of reconstructing the runtime's dt sum.
 */
function frameJson(sk, anim, t, foldRefs, trackTime) {
  const bones = {}, slots = {};
  let folds = 0;
  for (const b of sk.bones) bones[b.data.name] = { a: b.a, b: b.b, c: b.c, d: b.d, x: b.worldX, y: b.worldY };
  for (const slot of sk.slots) {
    const att = slot.getAttachment();
    const v = slotVertices(slot);
    if (foldRefs) folds += countFolds(slot, v, foldRefs);
    slots[slot.data.name] = { attachment: att ? att.name : null, vertices: Array.from(v) };
  }
  const frame = { anim, t, bones, slots };
  if (trackTime !== null && trackTime !== undefined) frame.trackTime = trackTime;
  if (foldRefs) frame.folds = folds;
  return frame;
}

function dump(opts) {
  const { L } = loadInputs(opts);
  if (!L.data) throw new UsageError(`spine-core 4.2.120 cannot load the skeleton: ${L.error}`);
  const pose = opts.pose ? readJsonArg(opts.pose, "--pose") : null;
  if (pose) asUsage(() => validatePose(L.data, pose));
  const anim = opts.anim ?? null;
  if (anim && !L.data.findAnimation(anim)) throw new UsageError(`no animation named '${anim}' (have: ${L.data.animations.map((a) => a.name).join(", ")})`);
  if (opts.skin && !L.data.findSkin(opts.skin)) throw new UsageError(`no skin named '${opts.skin}'`);
  const fps = opts.fps ? Number(opts.fps) : 60;
  if (!(fps > 0)) throw new UsageError("--fps must be > 0");
  const playOpts = {
    anim, fps,
    physics: opts.physics ?? "update",
    times: opts.times ? numberList(opts.times, "--times") : null,
    pose, poseMode: opts["pose-mode"] ?? "add",
    constraints: (opts.constraints ?? "on") !== "off",
    skin: opts.skin ?? null,
    dt: dtOpt(opts),
  };
  if (!["add", "set"].includes(playOpts.poseMode)) throw new UsageError("--pose-mode must be add or set");
  const frames = [];
  const refs = opts.folds ? foldReferences(L.data, playOpts.skin) : null;
  play(L.data, playOpts, (sk, t, i, trackTime) => frames.push(frameJson(sk, anim, t, refs, trackTime)));
  frames.sort((x, y) => x.t - y.t); // off-grid --times come from their own runs
  writeJson({ runtime: RUNTIME, frames }, opts.out);
  return 0;
}

// --------------------------------------------------------------------- check

const notRun = (id, max = 0) => makeGate(id, { value: null, max, pass: false, note: "not run: the runtime did not load the file" });

/**
 * Run a runtime-driven gate; if spine-core throws mid-playback (a two-bone
 * IK under the root does, at the first world transform) the gate fails by
 * name with the runtime's message instead of the whole report crashing.
 */
function guarded(id, max, fn) {
  try {
    return fn();
  } catch (e) {
    return makeGate(id, { value: null, max, pass: false, note: `spine-core threw: ${e.message}` });
  }
}

const QA_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.dirname(QA_DIR);

/**
 * C1 is the Python side's numpy LBS against this runtime's `dump`. SOTA §5
 * makes it a must-pass gate, so it is never skipped: `--c1 file` merges a
 * result spinegen.oracle_check already wrote; without it, check runs
 * `python -m spinegen.oracle_check --all` itself (setup pose plus every
 * animation at 60 fps, the same --pose and --skin), with SPINEGEN_PYTHON
 * (default python3) from the repository root. When that cannot run, C1
 * FAILS as "not measured": an unmeasured must-pass gate is not a pass.
 */
function gateOracle(opts, skeleton, atlas, pose, skin) {
  if (opts.c1) {
    const r = readJsonArg(`@${opts.c1}`, "--c1");
    return makeGate("C1_ORACLE", { value: r.value, max: r.max ?? 0.1, pass: r.pass, worst: r.worst, note: r.note ?? `from ${opts.c1}` });
  }
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "spinegen-c1-"));
  const out = path.join(tmp, "c1.json");
  const args = ["-m", "spinegen.oracle_check", path.resolve(skeleton), path.resolve(atlas), "--all", "--fps", "60", "--out", out];
  if (pose) args.push("--pose", JSON.stringify(pose));
  if (skin) args.push("--skin", skin);
  const python = process.env.SPINEGEN_PYTHON || "python3";
  try {
    const p = spawnSync(python, args, {
      cwd: REPO, encoding: "utf8", maxBuffer: 1 << 26,
      env: { ...process.env, SPINEGEN_QA_RUN: fileURLToPath(import.meta.url) },
    });
    if ((p.status === 0 || p.status === 1) && fs.existsSync(out)) {
      const r = JSON.parse(fs.readFileSync(out, "utf8"));
      return makeGate("C1_ORACLE", { value: r.value, max: r.max ?? 0.1, pass: r.pass, worst: r.worst, note: `${r.note}; measured by python -m spinegen.oracle_check --all` });
    }
    const why = (p.error ? p.error.message : (p.stderr || p.stdout || `exit ${p.status}`)).trim().split("\n").slice(-1)[0];
    return makeGate("C1_ORACLE", { value: null, max: 0.1, pass: false, note: `not measured (${python} -m spinegen.oracle_check: ${why}); pass --c1 <json> from spinegen.oracle_check --out` });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

/** H for pixel thresholds; the file's own value when the runtime cannot pose the skeleton. */
function heightOf(data, fileHeight) {
  if (!data) return fileHeight;
  try { return skeletonHeight(data); } catch { return fileHeight; }
}

function irFlipSet(ir) {
  const names = new Set(Array.isArray(ir?.flip) ? ir.flip : []);
  for (const b of Array.isArray(ir?.bones) ? ir.bones : []) if (b.flip) names.add(b.name);
  return names;
}

function check(opts) {
  const { skeleton, atlas: atlasPath, L } = loadInputs(opts);
  const { raw, data, atlas } = L;
  const target = opts.target ?? "4.2";
  const ir = opts.ir ? readJsonArg(`@${opts.ir}`, "--ir") : null;
  const fpsList = numberList(opts.fps ?? "30,60,120,240", "--fps").filter((f) => f > 0);
  const pose = opts.pose ? readJsonArg(opts.pose, "--pose") : null;
  if (pose && data) asUsage(() => validatePose(data, pose));
  const pma = opts.pma === undefined ? undefined : opts.pma === "true";
  const skin = opts.skin ?? null;
  if (skin && data && !data.findSkin(skin)) throw new UsageError(`no skin named '${skin}'`);
  const H = heightOf(data, raw.skeleton?.height ?? 0);
  const flipAllowed = irFlipSet(ir);
  const dtMode = dtOpt(opts);

  const gates = [gateVersion(raw, target, L.error), gateCounts(raw, data, ir), gateOrder(raw, data)];

  // L4 needs the sweep's world values, so the sweep runs first.
  let sw = null, sweepError = null;
  if (data) {
    try { sw = sweep(data, { fps: 60, pose, skin, flipAllowed, dt: dtMode }); } catch (e) { sweepError = e.message; }
  }
  const l4 = [
    ...curveFindings(raw),
    ...(data ? loadedTimelineFindings(data) : []),
    ...(sw ? sw.nonFinite.map((s) => `non-finite world value: ${s}`) : []),
    ...(sweepError ? [`world values not checked: spine-core threw: ${sweepError}`] : []),
  ];
  gates.push(makeGate("L4_CURVES_NAN", {
    value: l4.length, max: 0, items: l4, worst: l4[0],
    note: sw ? `curve shapes, loaded timelines, and world values over ${sw.frames} frames` : "file and loaded timelines only",
  }));
  gates.push(data ? gateIk(data) : notRun("L5_IK_VALID"));
  gates.push(gatePhysics(raw, H));
  gates.push(gateAtlas(atlas, atlasPath, data ? L.missingRegions : missingRegionsFromFile(raw, atlas), { pma }));
  gates.push(...gateMesh(raw, { generated: !!ir }));
  gates.push(gateTcMix(raw));
  if (data) {
    const fromSweep = (id, f) => (sw ? f() : makeGate(id, { value: null, max: 0, pass: false, note: `spine-core threw: ${sweepError}` }));
    gates.push(fromSweep("G1_FOLDS", () => gateFolds(sw, 60, pose)), fromSweep("G3_FLIP", () => gateFlip(sw, flipAllowed)));
    gates.push(guarded("M1_FOOT_PLANT", 1, () => gateFootPlant(data, ir, H, dtMode)));
    gates.push(guarded("P1_FPS_ROBUST", 0, () => gateFpsRobust(data, fpsList, H, dtMode)));
    gates.push(guarded("P3_DETERMINISM", 0, () => gateDeterminism(data)));
  } else {
    gates.push(notRun("G1_FOLDS"), notRun("G3_FLIP"), notRun("M1_FOOT_PLANT", 1), notRun("P1_FPS_ROBUST"), notRun("P3_DETERMINISM"));
  }
  gates.push(gateOracle(opts, skeleton, atlasPath, pose, skin));

  const pass = gates.every((g) => g.pass || !g.hard);
  const report = {
    schema: "spinegen-qa/1", runtime: RUNTIME, pass,
    input: { skeleton, atlas: atlasPath, target, ir: opts.ir ?? null, pose, H, dt: dtMode },
    gates,
  };
  writeJson(report, opts.out);
  for (const g of gates) {
    const tag = g.skipped ? "SKIP" : g.pass ? "pass" : g.hard ? "FAIL" : "warn";
    process.stderr.write(`${tag.padEnd(4)} ${g.id.padEnd(19)} ${String(g.value).padStart(8)} / ${g.max}  ${g.note ?? ""}\n`);
  }
  process.stderr.write(`${pass ? "PASS" : "FAIL"} ${skeleton}\n`);
  return pass ? 0 : 1;
}

// ---------------------------------------------------------------------- main

function main(argv) {
  const opts = parseArgs(argv);
  const cmd = opts._[0];
  if (opts.help || !cmd) {
    process.stderr.write(fs.readFileSync(new URL(import.meta.url), "utf8").split("*/")[0].replace(/^#!.*\n\/\*\*?/, "").replace(/^ \* ?/gm, "") + "\n");
    return cmd || opts.help ? 0 : 2;
  }
  if (cmd === "dump") return dump(opts);
  if (cmd === "check") return check(opts);
  throw new UsageError(`unknown command '${cmd}' (dump | check)`);
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (e) {
  process.stderr.write(`qa/run.mjs: ${e instanceof UsageError ? "" : "error: "}${e.message}\n`);
  if (!(e instanceof UsageError)) process.stderr.write(`${e.stack}\n`);
  process.exitCode = 2;
}
