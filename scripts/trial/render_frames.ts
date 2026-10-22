/**
 * The character trial, end to end with the kit's own code (the same functions the studio runs):
 * layers → guessJoints → buildRig → compileRecipe → smoothness + seam checks → per-frame layer transforms for the GIF.
 *
 * usage: node scripts/trial/render_frames.ts <alpha dir> <out dir> [truth joints json | -] [layers.json]
 *   <alpha dir>: <layer>.alpha files (raw 8-bit alpha, 1024×1536), dumped from the PNGs by render_gif.py
 *   [layers.json]: optional delivery-specific draw order (back → front)
 * env: NAME (skeleton/file name, default "character"), JOINTS (joints json instead of guessJoints),
 *      CELEBRATE (json file { "params": {...} } for the celebrate preset)
 * writes <out>/<NAME>.json (Spine 4.3 skeleton + animations, images in ./images/), <out>/joints.json,
 * <out>/frames.json, <out>/report.md
 */
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

const kit = new URL('../../', import.meta.url);
const imp = (p: string) => import(new URL(p, kit).href);
const { Skeleton, AnimationState, AnimationStateData, Physics } = await imp('node_modules/@esotericsoftware/spine-core/dist/index.js');
const { guessJoints, buildRig } = await imp('src/autorig.ts');
const { compileRecipe } = await imp('src/recipe.ts');
const { checkSkeleton } = await imp('src/check.ts');
const { checkSeams } = await imp('src/seams.ts');
const { parseSkeleton } = await imp('src/spineJson.ts');
const { estimateSize } = await imp('src/roles.ts');
const { JOINTS } = await imp('src/rigTemplate.ts');

const [alphaDir, outDir, truthFile, orderFile] = process.argv.slice(2);
const name = process.env.NAME ?? 'character';
mkdirSync(outDir, { recursive: true });
const W = 1024, H = 1536;
const alphas = new Map<string, { width: number; height: number; data: Uint8Array }>();
for (const f of readdirSync(alphaDir).filter((f) => f.endsWith('.alpha'))) {
  alphas.set(f.replace(/\.alpha$/, ''), { width: W, height: H, data: new Uint8Array(readFileSync(join(alphaDir, f))) });
}

// ---- rig exactly as the studio does: auto-guessed joints, no manual dragging
const joints = process.env.JOINTS ? JSON.parse(readFileSync(process.env.JOINTS, 'utf8')) : guessJoints(alphas);
const lines: string[] = ['# 角色試驗報告', ''];
if (truthFile && truthFile !== '-') {
  const truth = JSON.parse(readFileSync(truthFile, 'utf8'));
  const errs = JOINTS.map((j: { id: string; label: string }) => [j.label, Math.hypot(joints[j.id].x - truth[j.id].x, joints[j.id].y - truth[j.id].y)] as const);
  lines.push('## 自動猜關節（和畫的真實位置比較）', '', '| 關節 | 誤差 px |', '|---|---|', ...errs.map(([l, e]: readonly [string, number]) => `| ${l} | ${e.toFixed(0)} |`), '');
}
const rig = buildRig(joints, [...alphas.keys()], name);
writeFileSync(join(outDir, 'joints.json'), JSON.stringify(joints, null, 1));
// optional delivery-specific draw order (back → front), e.g. a rear glove guarding in front of the chest
if (orderFile) {
  const order: string[] = JSON.parse(readFileSync(orderFile, 'utf8')).order.map((f: string) => f.replace(/\.png$/, ''));
  const rank = (n: string) => (order.indexOf(n) < 0 ? 1e9 : order.indexOf(n));
  rig.json.slots.sort((a: { name: string }, b: { name: string }) => rank(a.name) - rank(b.name));
}
const size = estimateSize(rig.json);
const ANIMS = [
  { name: 'idle', preset: 'fighter-idle-bounce' },
  { name: 'idle_weave', preset: 'fighter-idle-weave' },
  { name: 'punch', preset: 'punch' },
  { name: 'hit', preset: 'hit-react' },
  { name: 'jump', preset: 'jump' },
  { name: 'celebrate', preset: 'celebrate', params: process.env.CELEBRATE ? JSON.parse(readFileSync(process.env.CELEBRATE, 'utf8')).params : undefined },
];
const { json, summary } = compileRecipe(rig.json, { fps: 30, facing: 1, size, roles: rig.roles, animations: ANIMS });
writeFileSync(join(outDir, `${name}.json`), JSON.stringify(json, null, 1));

lines.push(`## 骨架`, '', `- ${json.bones.length} 根骨、${json.slots.length} 個圖層、size ${size}`, `- 警告：${rig.warnings.length ? rig.warnings.join('；') : '無'}`, '');
lines.push('## 動作檢查', '', '| 動畫 | 長度 | 流暢度 | 錯誤/警告 | 接縫 |', '|---|---|---|---|---|');
const loops = summary.filter((s: { loop: boolean }) => s.loop).map((s: { name: string }) => s.name);
const checks = checkSkeleton(json, { loops });
const seams = checkSeams(json, alphas);
for (const s of summary) {
  const r = checks.find((c: { name: string }) => c.name === s.name);
  const bad = r.issues.filter((i: { severity: string }) => i.severity !== 'info');
  const sm = seams.find((x: { animation: string }) => x.animation === s.name);
  lines.push(`| ${s.name} | ${s.duration.toFixed(2)} s | ${r.score} | ${bad.length ? bad.map((i: { message: string }) => i.message).join('<br>') : '0'} | ${sm.issues.length ? sm.issues.map((i: { joint: string; time: number; area: number }) => `${i.joint}@${i.time}s ${Math.round(i.area)}px²`).join('、') : '✓'} |`);
}
writeFileSync(join(outDir, 'report.md'), lines.join('\n') + '\n');
console.log(lines.slice(-summary.length - 2).join('\n'));

// ---- the showcase: how the animations play back to back in a game (mixed, 25 fps)
const FPS = 25;
const dur = (n: string) => summary.find((s: { name: string }) => s.name === n).duration;
const SEQ: { anim: string; loop: boolean; seconds: number; label: string }[] = [
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
const skin = json.skins.find((s: { name: string }) => s.name === 'default').attachments;

// opaque box of each layer, to frame the whole sequence
const box = new Map<string, number[]>();
for (const [n, a] of alphas) {
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
      const p = sk.findBone(slot.bone).getAppliedPose();
      const r = ((att.rotation ?? 0) * Math.PI) / 180, cr = Math.cos(r), sr = Math.sin(r);
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
    const root = sk.findBone('root').getAppliedPose();
    const bones = sk.bones.filter((b: { data: { length: number } }) => b.data.length > 0).map((b: { getAppliedPose(): { a: number; c: number; worldX: number; worldY: number }; data: { length: number } }) => {
      const q = b.getAppliedPose();
      return [q.worldX, q.worldY, q.worldX + q.a * b.data.length, q.worldY + q.c * b.data.length];
    });
    raw.push({ label: seg.label, slots, root: [root.worldX, root.worldY], bones });
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
    return [n, [S * m00, -S * m01, -S * m10, S * m11, E, F]]; // out = [A B; C D]·img + [E F]
  }),
  ground: toOut(f.root[0], 0),
  bones: f.bones.map(([x0, y0, x1, y1]) => [...toOut(x0, y0), ...toOut(x1, y1)]),
}));
writeFileSync(join(outDir, 'frames.json'), JSON.stringify({ width: OW, height: OH, fps: FPS, frames }));
console.log(`frames: ${frames.length} (${(frames.length / FPS).toFixed(1)} s) at ${OW}×${OH}`);
