/**
 * Layers → finished character, the whole pipeline with the kit's own code (the same functions the studio runs):
 * guessJoints (or given joints) → checkJoints → buildRig → compileRecipe → smoothness + seam checks → per-frame layer
 * transforms for a preview GIF. Pure computation: the `character` command and scripts/trial/render_frames.ts share it.
 */
import { Skeleton, AnimationState, AnimationStateData, Physics } from '@esotericsoftware/spine-core';
import { guessJoints, buildRig, checkDrawOrder, type AlphaMap } from './autorig.ts';
import { compileRecipe, type CompileResult, type RecipeAnimation } from './recipe.ts';
import { checkSkeleton, type AnimationReport } from './check.ts';
import { checkSeams, type SeamReport } from './seams.ts';
import { poseUsage, type AngleBudget, type PoseUsage } from './poseChecks.ts';
import { parseSkeleton, type SpineJson } from './spineJson.ts';
import { estimateSize } from './roles.ts';
import { JOINTS, CANVAS, type Joints, type CanvasSize } from './rigTemplate.ts';

/** The six character motions every delivery is tried with. */
export const CHARACTER_ANIMS: RecipeAnimation[] = [
  { name: 'idle', preset: 'fighter-idle-bounce' },
  { name: 'idle_weave', preset: 'fighter-idle-weave' },
  { name: 'punch', preset: 'punch' },
  { name: 'hit', preset: 'hit-react' },
  { name: 'jump', preset: 'jump' },
  { name: 'celebrate', preset: 'celebrate' },
];

/** Layer file name → layer name, the way the studio reads it ("Arm-Front Upper.png" → arm_front_upper). */
export const layerName = (file: string) => file.replace(/\.png$/i, '').trim().toLowerCase().replace(/[\s-]+/g, '_');

/** Draw order from a delivery's layers.json: { "order": [...] } back → front, or a plain array. */
export function parseOrder(j: unknown): string[] {
  const list = Array.isArray(j) ? j : (j as { order?: unknown })?.order;
  if (!Array.isArray(list) || !list.every((x) => typeof x === 'string')) throw new Error('layers.json 要有 order 陣列（由後往前的檔名）');
  return list.map(layerName);
}

export interface CharacterOptions {
  name?: string;
  canvas?: CanvasSize;
  /** Hand-set joints; otherwise guessed from the art. */
  joints?: Joints;
  /** Draw order back → front (layer names), from layers.json. */
  order?: string[];
  /** Extra params per animation name, e.g. { celebrate: { swing: 30 } }. */
  params?: Record<string, Record<string, number>>;
  /** Run checkJoints against the art (head top). Off reproduces the older trial script exactly. */
  checkArt?: boolean;
  /** Sort slots after building instead of passing the order to buildRig (the older trial script's way). */
  sortSlots?: boolean;
  /** Angle each joint's art holds (budget.json); joints turned past it are listed per animation. */
  budget?: AngleBudget;
}

export interface CharacterResult {
  json: SpineJson;
  joints: Joints;
  guessed: boolean;
  missing: string[];
  warnings: string[];
  /** Draw-order rule breaks: gloves / hand props behind the head. */
  orderIssues: string[];
  size: number;
  summary: CompileResult['summary'];
  checks: AnimationReport[];
  seams: SeamReport[];
  /** Joint turns, flips and frame 0 per animation. */
  pose: PoseUsage[];
}

export function buildCharacter(alphas: Map<string, AlphaMap>, opts: CharacterOptions = {}): CharacterResult {
  const name = opts.name ?? 'character';
  const canvas = opts.canvas ?? CANVAS;
  const joints = opts.joints ?? guessJoints(alphas);
  const rig = buildRig(joints, [...alphas.keys()], name, canvas, {
    order: opts.sortSlots ? undefined : opts.order,
    alphas: opts.checkArt === false ? undefined : alphas,
  });
  if (rig.missing.length) throw new Error(`缺少必要圖層：${rig.missing.map((m) => m + '.png').join('、')}`);
  if (opts.sortSlots && opts.order) {
    const order = opts.order;
    const rank = (n: string) => (order.indexOf(n) < 0 ? 1e9 : order.indexOf(n));
    rig.json.slots.sort((a: { name: string }, b: { name: string }) => rank(a.name) - rank(b.name));
  }
  const orderIssues = checkDrawOrder(rig.json.slots.map((x: { name: string }) => x.name));
  const size = estimateSize(rig.json);
  const animations = CHARACTER_ANIMS.map((a) => (opts.params?.[a.name] ? { ...a, params: opts.params[a.name] } : a));
  const { json, summary } = compileRecipe(rig.json, { fps: 30, facing: 1, size, roles: rig.roles, animations });
  const loops = summary.filter((s) => s.loop).map((s) => s.name);
  const checks = checkSkeleton(json, { loops });
  const seams = checkSeams(json, alphas);
  const pose = poseUsage(json, opts.budget);
  return { json, joints, guessed: !opts.joints, missing: rig.missing, warnings: rig.warnings, orderIssues, size, summary, checks, seams, pose };
}

/** An idle whose frame 0 is further than this from the drawn pose visibly jumps when the game swaps the still art for it. */
export const FIRST_FRAME_TOLERANCE = 5;

/** Per-animation result rows: smoothness score, non-info issues, seam issues, flips, budget overruns, frame 0. */
export function characterRows(r: CharacterResult) {
  return r.summary.map((s) => {
    const c = r.checks.find((x) => x.name === s.name)!;
    const sm = r.seams.find((x) => x.animation === s.name)!;
    const p = r.pose.find((x) => x.animation === s.name)!;
    return {
      name: s.name,
      duration: s.duration,
      loop: !!s.loop,
      score: c.score,
      problems: c.issues.filter((i) => i.severity !== 'info').map((i) => i.message),
      seams: sm.issues.map((i) => `${i.joint}@${i.time}s ${Math.round(i.area)}px²`),
      flips: p.flips.map((f) => `${f.label}${f.kind === 'mirror' ? '鏡像' : '反折'}@${f.time}s（${f.frames} 格）`),
      over: p.over.map((o) => `${o.label} ${o.dir === '-' ? '−' : '+'}${Math.round(o.used)}°（預算 ${o.allowed}°）`),
      firstFrame: p.firstFrame,
      firstFrameOff: s.name.startsWith('idle') && p.firstFrame.degrees > FIRST_FRAME_TOLERANCE,
    };
  });
}

/** Joint-guess error against known joints, per joint label. */
export function jointErrors(joints: Joints, truth: Joints): [string, number][] {
  return JOINTS.map((j) => [j.label, Math.hypot(joints[j.id].x - truth[j.id].x, joints[j.id].y - truth[j.id].y)]);
}

// ---------------------------------------------------------------- preview frames
export interface PreviewFrames {
  width: number;
  height: number;
  fps: number;
  frames: { label: string; slots: [string, number[]][]; ground: number[]; bones: number[][] }[];
}

const LABELS: Record<string, string> = { idle: '待機・彈跳型', idle_weave: '待機・閃身型', punch: '出拳', hit: '受擊反應', jump: '跳躍', celebrate: '慶祝' };

/**
 * How the animations play back to back in a game (mixed, 25 fps), as an affine transform per layer per frame:
 * out = [A B; C D]·img + [E F] in GIF pixels, with a fixed camera over the whole sequence.
 */
export function previewFrames(r: CharacterResult, alphas: Map<string, AlphaMap>): PreviewFrames {
  const { json, summary } = r;
  const FPS = 25;
  const dur = (n: string) => summary.find((s) => s.name === n)!.duration;
  const SEQ: { anim: string; loop: boolean; seconds: number }[] = [
    { anim: 'idle', loop: true, seconds: dur('idle') },
    { anim: 'punch', loop: false, seconds: dur('punch') },
    { anim: 'idle', loop: true, seconds: 0.8 },
    { anim: 'hit', loop: false, seconds: dur('hit') },
    { anim: 'idle_weave', loop: true, seconds: dur('idle_weave') },
    { anim: 'jump', loop: false, seconds: dur('jump') },
    { anim: 'celebrate', loop: false, seconds: dur('celebrate') },
    { anim: 'idle', loop: true, seconds: 0.6 },
  ];
  const data = parseSkeleton(json);
  const sk = new Skeleton(data);
  const stateData = new AnimationStateData(data);
  stateData.defaultMix = 0.12;
  const state = new AnimationState(stateData);
  const skin = json.skins.find((s: { name: string }) => s.name === 'default').attachments;

  // opaque box of each layer, to frame the whole sequence
  const box = new Map<string, number[]>();
  for (const [n, a] of alphas) {
    const W = a.width, H = a.height;
    let x0 = W, y0 = H, x1 = 0, y1 = 0;
    for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) if (a.data[y * W + x] > 16) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    box.set(n, [x0, y0, x1, y1]);
  }
  const raw: { label: string; slots: [string, number[]][]; root: number[]; bones: number[][] }[] = [];
  let X0 = Infinity, Y0 = Infinity, X1 = -Infinity, Y1 = -Infinity;
  sk.setupPose();
  for (const seg of SEQ) {
    state.setAnimation(0, seg.anim, seg.loop);
    const n = Math.round(seg.seconds * FPS);
    for (let i = 0; i < n; i++) {
      if (raw.length) {
        state.update(1 / FPS);
        sk.update(1 / FPS);
      }
      sk.setupPose();
      state.apply(sk);
      sk.updateWorldTransform(Physics.update);
      const slots: [string, number[]][] = [];
      for (const slot of json.slots) {
        const att = skin[slot.name]?.[slot.attachment];
        if (!att) continue;
        const p = sk.findBone(slot.bone)!.getAppliedPose();
        const rr = ((att.rotation ?? 0) * Math.PI) / 180, cr = Math.cos(rr), sr = Math.sin(rr);
        const sx = att.scaleX ?? 1, sy = att.scaleY ?? 1;
        const m00 = (p.a * cr + p.b * sr) * sx, m01 = (-p.a * sr + p.b * cr) * sy;
        const m10 = (p.c * cr + p.d * sr) * sx, m11 = (-p.c * sr + p.d * cr) * sy;
        const qx = p.worldX + p.a * (att.x ?? 0) + p.b * (att.y ?? 0);
        const qy = p.worldY + p.c * (att.x ?? 0) + p.d * (att.y ?? 0);
        // image px (y down) → world: E + M·(ix, −iy)
        const ex = qx - m00 * (att.width / 2) + m01 * (att.height / 2);
        const ey = qy - m10 * (att.width / 2) + m11 * (att.height / 2);
        slots.push([slot.attachment, [m00, m01, m10, m11, ex, ey]]);
        const [bx0, by0, bx1, by1] = box.get(slot.attachment)!;
        for (const [ix, iy] of [[bx0, by0], [bx1, by0], [bx0, by1], [bx1, by1]]) {
          const wx = ex + m00 * ix - m01 * iy, wy = ey + m10 * ix - m11 * iy;
          X0 = Math.min(X0, wx); X1 = Math.max(X1, wx); Y0 = Math.min(Y0, wy); Y1 = Math.max(Y1, wy);
        }
      }
      const root = sk.findBone('root')!.getAppliedPose();
      const bones = sk.bones.filter((b) => b.data.length > 0).map((b) => {
        const q = b.getAppliedPose();
        return [q.worldX, q.worldY, q.worldX + q.a * b.data.length, q.worldY + q.c * b.data.length];
      });
      raw.push({ label: LABELS[seg.anim] ?? seg.anim, slots, root: [root.worldX, root.worldY], bones });
    }
  }

  // world → GIF px: fixed camera over the whole sequence, ground 6 % above the bottom
  const OH = 640;
  const pad = 40;
  Y0 = Math.min(Y0, -20);
  const S = (OH - 2 * pad) / (Y1 - Y0);
  const OW = Math.round(((X1 - X0) * S + 2 * pad) / 2) * 2;
  const toOut = (x: number, y: number) => [pad + (x - X0) * S, pad + (Y1 - y) * S];
  const frames = raw.map((f) => ({
    label: f.label,
    slots: f.slots.map(([n, [m00, m01, m10, m11, ex, ey]]) => {
      const [E, F] = toOut(ex, ey);
      return [n, [S * m00, -S * m01, -S * m10, S * m11, E, F]] as [string, number[]];
    }),
    ground: toOut(f.root[0], 0),
    bones: f.bones.map(([x0, y0, x1, y1]) => [...toOut(x0, y0), ...toOut(x1, y1)]),
  }));
  return { width: OW, height: OH, fps: FPS, frames };
}
