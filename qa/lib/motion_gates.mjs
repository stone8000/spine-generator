/**
 * Gates that need the skeleton in motion (SOTA §5: G1, G3, M1, P1, P3, and
 * the world-value half of L4).
 *
 * WHY every frame of every animation, through the same `play` loop as
 * `dump`: a fold or a flip lives for a few frames of one strike, and physics
 * depends on the exact dt sequence, so sampling "some" frames or stepping
 * differently from a player would let exactly the bad frames through.
 * Comparisons across frame rates use common times t = k/30, which fixes the
 * old probe's misaligned 144 fps samples.
 */
import { play, slotVertices, countFolds, foldReferences, boneTip, localToWorld, world, R } from "./runtime.mjs";
import { makeGate, skippedGate, pixelScale, round } from "./gate.mjs";

const det = (bone) => { const p = world(bone); return p.a * p.d - p.b * p.c; };

/** Setup-pose determinant sign of every bone (the G3 reference). */
function setupSigns(data, skin) {
  let signs = null;
  play(data, { physics: "update", skin }, (sk) => { signs = sk.bones.map((b) => Math.sign(det(b))); });
  return signs;
}

/**
 * One pass over the setup pose and every frame of every animation at `fps`:
 * fold counts (G1, each mesh against its own setup winding), determinant
 * sign flips against setup (G3) and non-finite world values (L4). `pose`
 * (dump/check --pose) is added to every frame, which is how a "what if
 * chest turns +150°" question is asked.
 */
export function sweep(data, { fps = 60, pose = null, poseMode = "add", skin = null, flipAllowed = new Set(), dt = "grid" } = {}) {
  const ref = setupSigns(data, skin);
  const refs = foldReferences(data, skin);
  const out = { frames: 0, folds: { max: 0, triangleFrames: 0, worst: null, mirrored: refs.mirrored, setupFolds: refs.setupFolds }, flips: new Map(), nonFinite: [] };
  const visit = (anim) => (sk, t) => {
    out.frames++;
    let frameFolds = 0, worstSlot = null, worstSlotFolds = 0;
    sk.bones.forEach((b, i) => {
      const p = world(b);
      const vals = [p.a, p.b, p.c, p.d, p.worldX, p.worldY];
      if (!vals.every(Number.isFinite)) { if (out.nonFinite.length < 50) out.nonFinite.push(`${anim ?? "setup"}@${round(t)} bone ${b.data.name}`); return; }
      const s = Math.sign(det(b));
      if (s !== 0 && ref[i] !== 0 && s !== ref[i] && !flipAllowed.has(b.data.name) && !out.flips.has(b.data.name)) {
        out.flips.set(b.data.name, { anim, t: round(t), bone: b.data.name, det: round(det(b), 6), setupSign: ref[i] });
      }
    });
    for (const slot of sk.slots) {
      const w = slotVertices(sk, slot);
      if (!w.every(Number.isFinite)) { if (out.nonFinite.length < 50) out.nonFinite.push(`${anim ?? "setup"}@${round(t)} slot ${slot.data.name}`); continue; }
      const f = countFolds(slot, w, refs);
      frameFolds += f;
      if (f > worstSlotFolds) { worstSlotFolds = f; worstSlot = slot.data.name; }
    }
    out.folds.triangleFrames += frameFolds;
    if (frameFolds > out.folds.max) {
      out.folds.max = frameFolds;
      out.folds.worst = { anim, t: round(t), folds: frameFolds, slot: worstSlot, slotFolds: worstSlotFolds };
    }
  };
  play(data, { physics: "update", pose, poseMode, skin }, visit(null));
  for (const a of data.animations) play(data, { anim: a.name, fps, physics: "update", pose, poseMode, skin, dt }, visit(a.name));
  return out;
}

/**
 * G1: no folded triangle in any frame (value = most folds in one frame).
 * A triangle is folded when its winding differs from its mesh's setup
 * winding, so a mesh mirrored at setup on purpose is not folded (its
 * reflection is G3's business); triangles that already disagree with their
 * mesh at setup are folds at rest and are listed as items.
 */
export function gateFolds(sw, fps, pose) {
  const { mirrored, setupFolds } = sw.folds;
  return makeGate("G1_FOLDS", {
    value: sw.folds.max, max: 0, worst: sw.folds.worst ?? undefined, items: setupFolds,
    note: `${sw.frames} frames (setup + every animation at ${fps} fps)` + (pose ? `, with --pose ${JSON.stringify(pose)}` : "") +
      `; ${sw.folds.triangleFrames} folded triangle-frames in total` +
      (mirrored.length ? `; mirrored at setup (reference winding flipped): ${mirrored.join(", ")}` : ""),
  });
}

/** G3: no bone's world determinant changes sign from setup (unless the IR flags it `flip`). */
export function gateFlip(sw, allowed) {
  const flips = [...sw.flips.values()];
  return makeGate("G3_FLIP", {
    value: flips.length, max: 0, worst: flips[0],
    items: flips.map((f) => `${f.bone} first flips at ${f.anim ?? "setup"}@${f.t}`),
    note: `bones whose world determinant sign left setup's` + (allowed.size ? `; allowed by IR: ${[...allowed].join(", ")}` : ""),
  });
}

// ------------------------------------------------------------- M1 FOOT_PLANT

/**
 * M1: during each planted span from the IR (`foot_plant`, see README), the
 * foot point stays within 1 px of where the span starts and moves < 5 px/s
 * (both scaled by H). Without planted feet in the IR the gate is skipped.
 */
export function gateFootPlant(data, ir, H, dt = "grid") {
  const plants = Array.isArray(ir?.foot_plant) ? ir.foot_plant : [];
  if (!plants.length) return skippedGate("M1_FOOT_PLANT", ir ? "skipped: --ir has no foot_plant entries" : "skipped: needs --ir with foot_plant", 1);
  const s = pixelScale(H), maxDisp = 1 * s, maxSpeed = 5 * s, fps = 60;
  let worst = null, worstDisp = 0, worstSpeed = 0;
  const items = [];
  for (const p of plants) {
    const anims = p.anim ? [p.anim] : data.animations.map((a) => a.name);
    for (const anim of anims) {
      const A = data.findAnimation(anim);
      if (!A) { items.push(`foot_plant ${p.bone}: no animation ${anim}`); continue; }
      if (!data.findBone(p.bone)) { items.push(`foot_plant: no bone ${p.bone}`); continue; }
      const spans = Array.isArray(p.spans) && p.spans.length ? p.spans : [[0, A.duration]];
      const [lx, ly] = Array.isArray(p.point) ? p.point : [0, 0];
      const track = [];
      play(data, { anim, fps, physics: "update", dt }, (sk, t) => {
        const v = localToWorld(sk.findBone(p.bone), lx, ly);
        track.push([t, v.x, v.y]);
      });
      for (const [t0, t1] of spans) {
        const inSpan = track.filter(([t]) => t >= t0 - 1e-9 && t <= t1 + 1e-9);
        if (!inSpan.length) continue;
        const [, x0, y0] = inSpan[0];
        inSpan.forEach(([t, x, y], i) => {
          const disp = Math.hypot(x - x0, y - y0);
          const speed = i ? Math.hypot(x - inSpan[i - 1][1], y - inSpan[i - 1][2]) * fps : 0;
          if (disp > worstDisp) { worstDisp = disp; worst = { anim, t: round(t), bone: p.bone, disp: round(disp), span: [t0, t1] }; }
          worstSpeed = Math.max(worstSpeed, speed);
        });
      }
    }
  }
  const pass = !items.length && worstDisp <= maxDisp && worstSpeed <= maxSpeed;
  return makeGate("M1_FOOT_PLANT", {
    pass, value: round(worstDisp), max: round(maxDisp), worst: worst ?? undefined, items,
    note: `max speed ${round(worstSpeed, 2)} px/s (max ${round(maxSpeed, 2)}), H=${H}`,
  });
}

// ------------------------------------------------------------ P1 FPS_ROBUST

/** Physics bone tips at every step of one run, with their times. */
function physicsTrack(data, anim, fps, dt) {
  const bones = R.physicsConstraints(data).map((pc) => pc.bone.name);
  const track = [];
  play(data, { anim, fps, physics: "update", dt }, (sk, t) => {
    track.push({ t, tips: bones.map((n) => boneTip(sk.findBone(n))) });
  });
  return { bones, track };
}

/** Tip positions at time t, linearly interpolated between steps (exact on the grid). */
function tipsAt(track, t) {
  let i = track.findIndex((f) => f.t >= t - 1e-9);
  if (i < 0) i = track.length - 1;
  const f1 = track[i];
  if (i === 0 || Math.abs(f1.t - t) < 1e-9) return f1.tips;
  const f0 = track[i - 1], u = (t - f0.t) / (f1.t - f0.t);
  return f1.tips.map((p, j) => [f0.tips[j][0] + u * (p[0] - f0.tips[j][0]), f0.tips[j][1] + u * (p[1] - f0.tips[j][1])]);
}

/**
 * P1: physics bone tips at each frame rate against the highest one, at
 * t = k/30. Threshold (120/fps) px — 4/2/1 px at 30/60/120 fps — scaled by
 * H. `data.per_anim` has every animation's own deviation per frame rate.
 * Baseline: warden react 1.39/0.90 px at 30/60 fps ([E3]), which stepped
 * a constant dt = 1/fps; `check --dt const` runs the gate that way (the
 * default grid dt gives 1.92/1.20 px, see play()).
 */
export function gateFpsRobust(data, fpsList, H, dt = "grid") {
  if (!R.physicsConstraints(data).length) return skippedGate("P1_FPS_ROBUST", "skipped: no physics constraints", 0);
  if (new Set(fpsList).size < 2) return skippedGate("P1_FPS_ROBUST", `skipped: needs >= 2 frame rates, got --fps ${fpsList.join(",")}`, 0);
  const ref = Math.max(...fpsList);
  const s = pixelScale(H);
  const per = {};
  let pass = true, worstRatio = -1, headline = null;
  for (const fps of fpsList.filter((f) => f !== ref).sort((a, b) => a - b)) {
    per[fps] = { dev: 0, max: round((120 / fps) * s), worst: null };
  }
  const perAnim = {};
  for (const a of data.animations) {
    const base = physicsTrack(data, a.name, ref, dt);
    perAnim[a.name] = {};
    for (const fps of Object.keys(per).map(Number)) {
      const run = physicsTrack(data, a.name, fps, dt);
      let dev = 0;
      for (let k = 0; k <= Math.floor(a.duration * 30 + 1e-9); k++) {
        const t = k / 30, p = tipsAt(run.track, t), q = tipsAt(base.track, t);
        p.forEach((tip, j) => {
          const d = Math.hypot(tip[0] - q[j][0], tip[1] - q[j][1]);
          dev = Math.max(dev, d);
          if (d > per[fps].dev) { per[fps].dev = d; per[fps].worst = { anim: a.name, t: round(t), bone: run.bones[j] }; }
        });
      }
      perAnim[a.name][fps] = round(dev);
    }
  }
  for (const [fps, r] of Object.entries(per)) {
    r.dev = round(r.dev);
    if (r.dev > r.max) pass = false;
    const ratio = r.dev / r.max;
    if (ratio > worstRatio) { worstRatio = ratio; headline = { fps: +fps, ...r }; }
  }
  return makeGate("P1_FPS_ROBUST", {
    pass, value: headline.dev, max: headline.max, worst: { fps: headline.fps, ...headline.worst },
    note: `physics tips vs ${ref} fps at t=k/30; ` + Object.entries(per).map(([f, r]) => `${f} fps ${r.dev}/${r.max} px`).join(", ") +
      `; H=${H}; dt ${dt === "const" ? "1/fps" : "k/fps - (k-1)/fps"}`,
    data: { reference_fps: ref, per_fps: per, per_anim: perAnim, dt },
  });
}

// ------------------------------------------------------------ P3 DETERMINISM

/** A fixed, seeded dt sequence covering `duration`: dt in [1/240, 1/24]. */
function jitteredDts(duration, seed = 12345) {
  let x = seed >>> 0;
  const rnd = () => ((x = (Math.imul(x, 1664525) + 1013904223) >>> 0) / 2 ** 32);
  const dts = [0];
  let t = 0;
  while (t < duration) { const dt = 1 / 240 + rnd() * (1 / 24 - 1 / 240); dts.push(dt); t += dt; }
  return dts;
}

/** Every bone's world values and every slot's world vertices, one flat array per frame. */
function snapshotRun(data, anim, dts) {
  const frames = [];
  play(data, { anim, dts, physics: "update" }, (sk) => {
    const v = [];
    for (const b of sk.bones) { const p = world(b); v.push(p.a, p.b, p.c, p.d, p.worldX, p.worldY); }
    for (const slot of sk.slots) v.push(...slotVertices(sk, slot));
    frames.push(v);
  });
  return frames;
}

/**
 * P3: the same jittered dt sequence played twice on fresh skeletons gives
 * bit-identical world values ([E3]: 0 difference). Jittered rather than
 * uniform dt so physics' fixed-step accumulator is exercised.
 */
export function gateDeterminism(data) {
  let maxDiff = 0, worst = null, frames = 0;
  for (const a of data.animations) {
    const dts = jitteredDts(Math.max(a.duration, 0.5));
    const r1 = snapshotRun(data, a.name, dts), r2 = snapshotRun(data, a.name, dts);
    frames += r1.length;
    r1.forEach((v, i) => v.forEach((x, j) => {
      const d = Object.is(x, r2[i][j]) ? 0 : Math.abs(x - r2[i][j]);
      if (!(d <= maxDiff)) { maxDiff = Number.isNaN(d) ? Infinity : d; worst = { anim: a.name, frame: i }; }
    }));
  }
  return makeGate("P3_DETERMINISM", {
    value: maxDiff, max: 0, worst: worst ?? undefined,
    note: `${data.animations.length} animations x 2 runs, ${frames} frames each run, seeded jittered dt in [1/240, 1/24]`,
  });
}
