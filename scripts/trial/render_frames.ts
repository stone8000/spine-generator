/**
 * The character trial, end to end with the kit's own code (the same functions the studio runs):
 * layers → guessJoints → buildRig → compileRecipe → smoothness + seam checks → per-frame layer transforms for the GIF.
 * The pipeline lives in src/character.ts; `spine-motion character <圖層資料夾>` does all of this in one step.
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
const { buildCharacter, characterRows, jointErrors, parseOrder, previewFrames } = await imp('src/character.ts');

const [alphaDir, outDir, truthFile, orderFile] = process.argv.slice(2);
const name = process.env.NAME ?? 'character';
mkdirSync(outDir, { recursive: true });
const W = 1024, H = 1536;
const alphas = new Map<string, { width: number; height: number; data: Uint8Array }>();
for (const f of readdirSync(alphaDir).filter((f) => f.endsWith('.alpha'))) {
  alphas.set(f.replace(/\.alpha$/, ''), { width: W, height: H, data: new Uint8Array(readFileSync(join(alphaDir, f))) });
}

const r = buildCharacter(alphas, {
  name,
  joints: process.env.JOINTS ? JSON.parse(readFileSync(process.env.JOINTS, 'utf8')) : undefined,
  order: orderFile ? parseOrder(JSON.parse(readFileSync(orderFile, 'utf8'))) : undefined,
  params: process.env.CELEBRATE ? { celebrate: JSON.parse(readFileSync(process.env.CELEBRATE, 'utf8')).params } : undefined,
  checkArt: false,
  sortSlots: true,
});
const lines: string[] = ['# 角色試驗報告', ''];
if (truthFile && truthFile !== '-') {
  const errs = jointErrors(r.joints, JSON.parse(readFileSync(truthFile, 'utf8')));
  lines.push('## 自動猜關節（和畫的真實位置比較）', '', '| 關節 | 誤差 px |', '|---|---|', ...errs.map(([l, e]: [string, number]) => `| ${l} | ${e.toFixed(0)} |`), '');
}
writeFileSync(join(outDir, 'joints.json'), JSON.stringify(r.joints, null, 1));
writeFileSync(join(outDir, `${name}.json`), JSON.stringify(r.json, null, 1));

lines.push(`## 骨架`, '', `- ${r.json.bones.length} 根骨、${r.json.slots.length} 個圖層、size ${r.size}`, `- 警告：${r.warnings.length ? r.warnings.join('；') : '無'}`, '');
lines.push('## 動作檢查', '', '| 動畫 | 長度 | 流暢度 | 錯誤/警告 | 接縫 |', '|---|---|---|---|---|');
for (const row of characterRows(r)) {
  lines.push(`| ${row.name} | ${row.duration.toFixed(2)} s | ${row.score} | ${row.problems.length ? row.problems.join('<br>') : '0'} | ${row.seams.length ? row.seams.join('、') : '✓'} |`);
}
writeFileSync(join(outDir, 'report.md'), lines.join('\n') + '\n');
console.log(lines.slice(-r.summary.length - 2).join('\n'));

const frames = previewFrames(r, alphas);
writeFileSync(join(outDir, 'frames.json'), JSON.stringify(frames));
console.log(`frames: ${frames.frames.length} (${(frames.frames.length / frames.fps).toFixed(1)} s) at ${frames.width}×${frames.height}`);
