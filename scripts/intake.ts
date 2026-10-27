/**
 * Delivery intake: layer PNGs from an image agent or artist → auto-rig → animations → smoothness + seam checks →
 * per-frame layer transforms for the preview GIF. Runs exactly the functions the studio runs.
 *
 * usage: node scripts/intake.ts <work dir> [--joints <joints.json>] [--truth <joints.json>]
 *   <work dir>/intake.json and <work dir>/alpha/*.alpha come from `python scripts/intake_images.py prepare`.
 *   --joints: use these joint positions (canvas px) instead of guessing — the studio's "drag the dots" step.
 *   --truth:  known joint positions, only to report how far the guess was off.
 * writes <work dir>/output/: rig.json (Spine 4.3; images in ./images/), joints.json, frames.json, report.md, result.json
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { Skeleton, AnimationState, AnimationStateData, Physics } from '@esotericsoftware/spine-core';
import { guessJoints, buildRig, type AlphaMap } from '../src/autorig.ts';
import { compileRecipe } from '../src/recipe.ts';
import { checkSkeleton } from '../src/check.ts';
import { checkSeams } from '../src/seams.ts';
import { parseSkeleton, type SpineJson } from '../src/spineJson.ts';
import { estimateSize } from '../src/roles.ts';
import { JOINTS, PARTS, type Joints, type CanvasSize } from '../src/rigTemplate.ts';

const args = process.argv.slice(2);
const work = args[0];
if (!work) throw new Error('usage: node scripts/intake.ts <work dir> [--joints file] [--truth file]');
const opt = (name: string) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};
const out = join(work, 'output');
mkdirSync(out, { recursive: true });

interface Intake {
  canvas: CanvasSize;
  layers: string[];
  rejected: { file: string; why: string }[];
}
const intake: Intake = JSON.parse(readFileSync(join(work, 'intake.json'), 'utf8'));
const { width: W, height: H } = intake.canvas;

// ---- layers: template names only (preview images and extras are listed, not rigged)
const isPart = (n: string) => PARTS.some((p) => p.name === n) || n.startsWith('hair_front');
const alphas = new Map<string, AlphaMap>();
for (const n of intake.layers.filter(isPart)) {
  alphas.set(n, { width: W, height: H, data: new Uint8Array(readFileSync(join(work, 'alpha', `${n}.alpha`))) });
}
const unknown = intake.layers.filter((n) => !isPart(n));
const missingRequired = PARTS.filter((p) => p.required && !alphas.has(p.name)).map((p) => p.name);
const missingOptional = PARTS.filter((p) => !p.required && !alphas.has(p.name) && !(p.name === 'hair_front' && [...alphas.keys()].some((n) => n.startsWith('hair_front')))).map((p) => p.name);

// ---- joints: given (dragged) or guessed
const jointsFile = opt('--joints');
const joints: Joints = jointsFile ? JSON.parse(readFileSync(jointsFile, 'utf8')) : guessJoints(alphas);
writeFileSync(join(out, 'joints.json'), JSON.stringify(joints, null, 1));

const lines: string[] = ['# 實作報告（自動產生）', ''];
lines.push('## 圖層', '', `- 畫布 ${W}×${H}，使用 ${alphas.size} 張：${[...alphas.keys()].join('、') || '無'}`);
lines.push(`- 缺少必要圖層：${missingRequired.join('、') || '無'}`);
lines.push(`- 沒有的選用圖層：${missingOptional.join('、') || '無'}`);
lines.push(`- 不在模板裡、沒有使用：${unknown.join('、') || '無'}`);
lines.push(`- 讀取時排除：${intake.rejected.map((r) => `${r.file}（${r.why}）`).join('、') || '無'}`, '');

const truthFile = opt('--truth');
lines.push(`## 關節（${jointsFile ? '來自 ' + jointsFile : '自動猜'}）`, '');
if (truthFile) {
  const truth = JSON.parse(readFileSync(truthFile, 'utf8'));
  lines.push('| 關節 | 位置 | 和真實位置差 px |', '|---|---|---|');
  for (const j of JOINTS) lines.push(`| ${j.label} | ${joints[j.id].x}, ${joints[j.id].y} | ${Math.hypot(joints[j.id].x - truth[j.id].x, joints[j.id].y - truth[j.id].y).toFixed(0)} |`);
} else {
  lines.push('| 關節 | 位置 |', '|---|---|');
  for (const j of JOINTS) lines.push(`| ${j.label} | ${joints[j.id].x}, ${joints[j.id].y} |`);
}
lines.push('');

const result = {
  ok: false,
  canvas: intake.canvas,
  missingRequired,
  unknown,
  rejected: intake.rejected,
  warnings: [] as string[],
  size: 0,
  animations: [] as { name: string; score: number; problems: string[]; seams: { joint: string; bone: string; time: number; area: number; rest: boolean }[] }[],
};

if (!alphas.size) {
  lines.push('沒有可用的圖層，無法建立骨架。');
  writeFileSync(join(out, 'report.md'), lines.join('\n') + '\n');
  writeFileSync(join(out, 'result.json'), JSON.stringify(result, null, 1));
  console.log('no usable layers');
  process.exit(0);
}

// ---- rig + animations, as the studio builds them
const rig = buildRig(joints, [...alphas.keys()], 'character', intake.canvas);
const size = estimateSize(rig.json);
result.size = size;
result.warnings = rig.warnings;
const ANIMS = [
  { name: 'idle', preset: 'fighter-idle-bounce' },
  { name: 'idle_weave', preset: 'fighter-idle-weave' },
  { name: 'punch', preset: 'punch' },
  { name: 'hit', preset: 'hit-react' },
  { name: 'jump', preset: 'jump' },
  { name: 'celebrate', preset: 'celebrate' },
];
const { json, summary } = compileRecipe(rig.json, { fps: 30, facing: 1, size, roles: rig.roles, animations: ANIMS });
writeFileSync(join(out, 'rig.json'), JSON.stringify(json, null, 1));
lines.push('## 骨架', '', `- ${json.bones.length} 根骨、${json.slots.length} 個圖層、size ${size}`, `- 警告：${rig.warnings.join('；') || '無'}`, '');

const checks = checkSkeleton(json, { loops: summary.filter((s) => s.loop).map((s) => s.name) });
const seams = checkSeams(json, alphas);
lines.push('## 動作', '', '| 動畫 | 長度 | 流暢度 | 錯誤／警告 | 接縫 |', '|---|---|---|---|---|');
for (const s of summary) {
  const r = checks.find((c) => c.name === s.name)!;
  const problems = r.issues.filter((i) => i.severity !== 'info').map((i) => i.message);
  const sm = seams.find((x) => x.animation === s.name)!;
  result.animations.push({ name: s.name, score: r.score, problems, seams: sm.issues.map((i) => ({ joint: i.joint, bone: i.bone, time: i.time, area: Math.round(i.area), rest: !!i.rest })) });
  const seamText = sm.issues.length ? sm.issues.map((i) => (i.rest ? `${i.joint}（靜止就有缺口 ${Math.round(i.area)} px²）` : `${i.joint}@${i.time}s ${Math.round(i.area)} px²`)).join('、') : '✓';
  lines.push(`| ${s.name} | ${s.duration.toFixed(2)} s | ${r.score} | ${problems.length ? problems.join('<br>') : '0'} | ${seamText} |`);
}
result.ok = !missingRequired.length && !rig.warnings.length && result.animations.every((a) => a.score >= 90 && !a.problems.length && !a.seams.length);
lines.push('', `**結論：${result.ok ? '通過' : '有問題，見上表'}**`);
writeFileSync(join(out, 'report.md'), lines.join('\n') + '\n');
writeFileSync(join(out, 'result.json'), JSON.stringify(result, null, 1));

// ---- the preview: the animations back to back with 0.12 s mixes, 25 fps, as a game plays them
const FPS = 25;
const dur = (n: string) => summary.find((s) => s.name === n)!.duration;
const SEQ = [
  { anim: 'idle', loop: true, seconds: dur('idle'), label: '待機・彈跳型' },
  { anim: 'punch', loop: false, seconds: dur('punch'), label: '出拳' },
  { anim: 'idle', loop: true, seconds: 0.8, label: '待機・彈跳型' },
  { anim: 'hit', loop: false, seconds: dur('hit'), label: '受擊反應' },
  { anim: 'idle_weave', loop: true, seconds: dur('idle_weave'), label: '待機・閃身型' },
  { anim: 'jump', loop: false, seconds: dur('jump'), label: '跳躍' },
  { anim: 'celebrate', loop: false, seconds: dur('celebrate'), label: '慶祝' },
  { anim: 'idle', loop: true, seconds: 0.6, label: '待機・彈跳型' },
];
const data = parseSkeleton(json);
const sk = new Skeleton(data);
const stateData = new AnimationStateData(data);
stateData.defaultMix = 0.12;
const state = new AnimationState(stateData);
const skin = (json.skins as SpineJson[]).find((s) => s.name === 'default')!.attachments;

const box = new Map<string, number[]>();
for (const [n, a] of alphas) {
  let x0 = W, y0 = H, x1 = 0, y1 = 0;
  for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) if (a.data[y * W + x] > 16) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  box.set(n, [x0, y0, x1, y1]);
}
const raw: { label: string; slots: [string, number[]][]; root: number[]; bones: number[][] }[] = [];
let X0 = Infinity, Y0 = Infinity, X1 = -Infinity, Y1 = -Infinity;
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
    for (const slot of json.slots as SpineJson[]) {
      const att = skin[slot.name]?.[slot.attachment];
      if (!att) continue;
      const p = sk.findBone(slot.bone)!.getAppliedPose();
      const r = ((att.rotation ?? 0) * Math.PI) / 180, cr = Math.cos(r), sr = Math.sin(r);
      const sx = att.scaleX ?? 1, sy = att.scaleY ?? 1;
      const m00 = (p.a * cr + p.b * sr) * sx, m01 = (-p.a * sr + p.b * cr) * sy;
      const m10 = (p.c * cr + p.d * sr) * sx, m11 = (-p.c * sr + p.d * cr) * sy;
      const qx = p.worldX + p.a * (att.x ?? 0) + p.b * (att.y ?? 0);
      const qy = p.worldY + p.c * (att.x ?? 0) + p.d * (att.y ?? 0);
      // image px (y down) → world: E + M·(ix, −iy); attachments are canvas-sized, so 1 image px = 1 unit
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
    raw.push({ label: seg.label, slots, root: [root.worldX, root.worldY], bones });
  }
}
const OH = 640, pad = 40;
Y0 = Math.min(Y0, -20);
const S = (OH - 2 * pad) / (Y1 - Y0);
const OW = Math.round(((X1 - X0) * S + 2 * pad) / 2) * 2;
const toOut = (x: number, y: number) => [pad + (x - X0) * S, pad + (Y1 - y) * S];
const frames = raw.map((f) => ({
  label: f.label,
  slots: f.slots.map(([n, [m00, m01, m10, m11, ex, ey]]) => {
    const [E, F] = toOut(ex, ey);
    return [n, [S * m00, -S * m01, -S * m10, S * m11, E, F]]; // out = [A B; C D]·img + [E F]
  }),
  ground: toOut(f.root[0], 0),
  bones: f.bones.map(([x0, y0, x1, y1]) => [...toOut(x0, y0), ...toOut(x1, y1)]),
}));
writeFileSync(join(out, 'frames.json'), JSON.stringify({ width: OW, height: OH, fps: FPS, frames }));
console.log(`${result.ok ? 'OK' : 'PROBLEMS'} · ${alphas.size} layers · size ${size} · ${frames.length} frames → ${out}`);
