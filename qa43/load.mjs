#!/usr/bin/env node
/**
 * What the official spine-core 4.3.13 runtime makes of a skeleton file.
 *
 *   node qa43/load.mjs --skeleton x.json [--atlas x.atlas]
 *
 * WHY: 4.3 reads constraints only from `constraints[]`. A 4.2 file's
 * top-level `ik`/`transform`/`path`/`physics` are silently dropped when
 * nothing references them by name, and the load throws when an animation or
 * skin does ([E5]). This probe prints the runtime's counts next to the file's
 * own and an L2-style verdict, so "our 4.2 output on a 4.3 player" is a
 * measured fact, not an assumption. Exit 0 when every count survives,
 * 1 when constraints are lost or the load throws, 2 on bad usage.
 *
 * It shares only the runtime-free JSON reader with qa/ (../qa/lib/raw.mjs);
 * anything importing spine-core must stay inside this package so it binds
 * to 4.3.13, not to qa/'s 4.2.120.
 */
import fs from "node:fs";
import * as spine from "@esotericsoftware/spine-core";
import { rawCounts } from "../qa/lib/raw.mjs";

const RUNTIME = "@esotericsoftware/spine-core@4.3.13";

class FakeTexture extends spine.Texture {
  setFilters() {}
  setWraps() {}
  dispose() {}
}

/** Attachment loader without an atlas: geometry-free stand-ins, enough to count. */
const nullLoader = {
  newRegionAttachment: (skin, name, path) => new spine.RegionAttachment(name, path),
  newMeshAttachment: (skin, name, path) => new spine.MeshAttachment(name, path),
  newBoundingBoxAttachment: (skin, name) => new spine.BoundingBoxAttachment(name),
  newPathAttachment: (skin, name) => new spine.PathAttachment(name),
  newPointAttachment: (skin, name) => new spine.PointAttachment(name),
  newClippingAttachment: (skin, name) => new spine.ClippingAttachment(name),
};

function attachmentLoader(atlasPath) {
  if (!atlasPath) return nullLoader;
  const atlas = new spine.TextureAtlas(fs.readFileSync(atlasPath, "utf8"));
  for (const page of atlas.pages) page.setTexture(new FakeTexture({ width: page.width, height: page.height }));
  return new spine.AtlasAttachmentLoader(atlas);
}

/** The same count names as qa/ L2, read from 4.3's single constraints[] list. */
function runtimeCounts(data) {
  const kind = (T) => data.constraints.filter((c) => c instanceof T).length;
  let attachments = 0, meshes = 0, regions = 0;
  for (const skin of data.skins) {
    for (const e of skin.getAttachments()) {
      attachments++;
      if (e.attachment instanceof spine.MeshAttachment) meshes++;
      else if (e.attachment instanceof spine.RegionAttachment) regions++;
    }
  }
  return {
    bones: data.bones.length, slots: data.slots.length, skins: data.skins.length,
    animations: data.animations.length, events: data.events.length,
    ik: kind(spine.IkConstraintData), transform: kind(spine.TransformConstraintData),
    path: kind(spine.PathConstraintData), physics: kind(spine.PhysicsConstraintData),
    slider: kind(spine.SliderData),
    attachments, meshes, regions,
    constraints: data.constraints.length,
  };
}

function parseArgs(argv) {
  const opts = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith("--") || i + 1 >= argv.length) throw new Error(`bad argument ${argv[i]}`);
    opts[argv[i].slice(2)] = argv[++i];
  }
  if (!opts.skeleton) throw new Error("--skeleton is required");
  return opts;
}

function main(argv) {
  let opts;
  try { opts = parseArgs(argv); } catch (e) {
    process.stderr.write(`qa43/load.mjs: ${e.message}\nusage: node qa43/load.mjs --skeleton x.json [--atlas x.atlas]\n`);
    return 2;
  }
  const text = fs.readFileSync(opts.skeleton, "utf8");
  const json = rawCounts(JSON.parse(text));
  json.constraints = json.ik + json.transform + json.path + json.physics + json.slider;
  const out = { runtime: RUNTIME, skeleton: opts.skeleton, declared: JSON.parse(text).skeleton?.spine ?? null, json };
  let data = null;
  try {
    data = new spine.SkeletonJson(attachmentLoader(opts.atlas)).readSkeletonData(JSON.parse(text));
  } catch (e) {
    out.threw = String(e && e.message ? e.message : e);
  }
  const items = [];
  if (data) {
    out.counts = runtimeCounts(data);
    for (const k of Object.keys(json)) if (out.counts[k] !== json[k]) items.push(`${k}: runtime ${out.counts[k]} vs file ${json[k]}`);
  } else {
    items.push(`load throws: ${out.threw}`);
  }
  out.L2_COUNTS = { id: "L2_COUNTS", pass: items.length === 0, value: items.length, max: 0, worst: items[0], items };
  process.stdout.write(JSON.stringify(out, null, 1) + "\n");
  process.stderr.write(`${out.L2_COUNTS.pass ? "pass" : "FAIL"} L2_COUNTS on 4.3.13: ${items.length ? items.join("; ") : "all counts survive"}\n`);
  return out.L2_COUNTS.pass ? 0 : 1;
}

process.exitCode = main(process.argv.slice(2));
