/**
 * `spine-motion character <圖層資料夾>`: one command from an art delivery (layer PNGs, optional layers.json) to a
 * Spine 4.3 skeleton with six animations, a check report, an offline preview page and a preview GIF.
 */
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync, copyFileSync, rmSync } from 'node:fs';
import { basename, dirname, join, resolve, relative } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { decodePngAlpha } from './png.ts';
import { buildCharacter, characterRows, layerName, parseOrder, previewFrames, FIRST_FRAME_TOLERANCE } from './character.ts';
import { parseBudget, POSE_JOINTS } from './poseChecks.ts';
import { previewHtml } from './preview.ts';
import type { AlphaMap } from './autorig.ts';
import { PARTS, type Joints } from './rigTemplate.ts';

export const CHARACTER_HELP = `  spine-motion character <圖層資料夾> [-o 輸出資料夾] [--name 名稱] [--joints 關節.json]
                         [--celebrate 參數.json] [--params 動作參數.json] [--budget 角度預算.json]
                         [--no-gif] [--bones]
                                               一行做完角色：圖層 PNG → Spine 4.3 骨架＋6 個動作、
                                               檢查報告、預覽頁與預覽 GIF`;

const SCRIPTS = fileURLToPath(new URL('../scripts/trial/', import.meta.url));
// folder names that say "these are the layers", so the character is named after the folder above
const GENERIC = /^(layers?|交件|圖層|parts|png)$/i;

function arg(args: string[], name: string): string | undefined {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
}

/** First existing file among names, looked up in the layers folder, then the folder above it. */
function findBeside(dir: string, names: string[]): string | undefined {
  for (const d of [dir, dirname(dir)]) for (const n of names) if (existsSync(join(d, n))) return join(d, n);
  return undefined;
}

function readJson(file: string, what: string): unknown {
  try {
    return JSON.parse(readFileSync(file, 'utf8'));
  } catch (e) {
    throw new Error(`${what} ${file} 讀不到或不是 JSON：${(e as Error).message}`);
  }
}

/** Runs render_gif.py with python3 / python / py; returns why it couldn't, or undefined when the GIF was written. */
function renderGif(imagesDir: string, framesFile: string, out: string, bones: boolean): string | undefined {
  const script = join(SCRIPTS, 'render_gif.py');
  for (const py of process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python']) {
    const r = spawnSync(py, [script, 'gif', imagesDir, framesFile, out, ...(bones ? ['--bones'] : [])], { encoding: 'utf8' });
    if (r.error) continue; // not installed under this name
    if (r.status === 0) return undefined;
    if (/No module named ['"]?PIL/.test(r.stderr)) return `${py} 沒有 Pillow，執行 \`${py} -m pip install pillow\` 後重跑即可產生 GIF`;
    return `${py} 產生 GIF 失敗：${r.stderr.trim().split('\n').pop()}`;
  }
  return '找不到 Python 3，裝好 Python 與 Pillow（pip install pillow）後重跑即可產生 GIF；其他檔案不受影響';
}

export function cmdCharacter(args: string[]): void {
  const dirArg = args.find((a, i) => !a.startsWith('-') && !['-o', '--name', '--joints', '--celebrate', '--params', '--budget'].includes(args[i - 1]));
  if (!dirArg) throw new Error('用法：spine-motion character <圖層資料夾>（放部件 PNG，可附 layers.json）');
  const dir = resolve(dirArg);
  if (!existsSync(dir)) throw new Error(`找不到資料夾 ${dirArg}`);
  const folder = basename(dir);
  const name = arg(args, '--name') ?? (GENERIC.test(folder) ? basename(dirname(dir)) : folder);
  const outDir = resolve(arg(args, '-o') ?? join(dirname(dir), `${name}_spine`));
  const show = (f: string) => {
    const r = relative(process.cwd(), f);
    return r.startsWith('..') ? f : r || '.';
  };

  // ---- read the delivery
  const files = readdirSync(dir).filter((f) => /\.png$/i.test(f));
  const known = new Set(PARTS.map((p) => p.name));
  const alphas = new Map<string, AlphaMap>();
  const source = new Map<string, string>();
  const skipped: string[] = [];
  for (const f of files) {
    const n = layerName(f);
    if (!known.has(n)) {
      skipped.push(f); // full.png, previews, sheets
      continue;
    }
    try {
      alphas.set(n, decodePngAlpha(new Uint8Array(readFileSync(join(dir, f)))));
      source.set(n, join(dir, f));
    } catch (e) {
      throw new Error(`${f}：${(e as Error).message}`);
    }
  }
  if (!alphas.size) throw new Error(`${show(dir)} 裡沒有圖層 PNG（檔名要照 docs/生圖交付規格.md，例如 head.png、torso.png）`);
  const sizes = new Set([...alphas.values()].map((a) => `${a.width}×${a.height}`));
  if (sizes.size > 1) throw new Error(`圖層尺寸不一致（${[...sizes].join('、')}）：每張都要是同一張畫布，部件留在原位`);
  const first = alphas.values().next().value!;
  const canvas = { width: first.width, height: first.height };

  const used: string[] = [];
  const orderFile = findBeside(dir, ['layers.json']);
  const order = orderFile ? parseOrder(readJson(orderFile, '疊圖順序')) : undefined;
  if (orderFile) used.push(`疊圖順序：${show(orderFile)}`);
  const jointsFile = arg(args, '--joints') ?? findBeside(dir, ['joints.json', 'joints_manual.json']);
  const joints = jointsFile ? (readJson(jointsFile, '關節') as Joints) : undefined;
  if (jointsFile) used.push(`關節：${show(jointsFile)}`);
  const celebrateFile = arg(args, '--celebrate') ?? findBeside(dir, ['celebrate.params.json']);
  let params: Record<string, Record<string, number>> | undefined;
  if (celebrateFile) {
    const c = readJson(celebrateFile, '慶祝參數') as { params?: Record<string, number> } & Record<string, number>;
    params = { celebrate: c.params ?? c };
    used.push(`慶祝參數：${show(celebrateFile)}`);
  }
  // per-animation params, e.g. { "punch": { "reach": 170 }, "celebrate": { "swing": 30 } }; celebrate.params.json wins for celebrate
  const paramsFile = arg(args, '--params') ?? findBeside(dir, ['motion.params.json']);
  if (paramsFile) {
    const all = readJson(paramsFile, '動作參數') as Record<string, Record<string, number>>;
    params = { ...all, ...(params && { celebrate: { ...all.celebrate, ...params.celebrate } }) };
    used.push(`動作參數：${show(paramsFile)}`);
  }
  const budgetFile = arg(args, '--budget') ?? findBeside(dir, ['budget.json']);
  const budget = budgetFile ? parseBudget(readJson(budgetFile, '角度預算')) : undefined;
  if (budgetFile) used.push(`角度預算：${show(budgetFile)}`);

  console.log(`角色「${name}」：${alphas.size} 個圖層，畫布 ${canvas.width}×${canvas.height}`);
  for (const u of used) console.log(`  使用 ${u}`);
  if (!jointsFile) console.log('  關節：自動猜（要手動調，見報告的「關節」一節）');
  if (skipped.length) console.log(`  略過非圖層檔：${skipped.join('、')}`);

  // ---- build, animate, check
  console.log('建骨架、產生 6 個動作、檢查流暢度、接縫、翻面與關節角度…');
  const r = buildCharacter(alphas, { name, canvas, joints, order, params, budget });
  const rows = characterRows(r);
  mkdirSync(join(outDir, 'images'), { recursive: true });
  for (const [n, f] of source) copyFileSync(f, join(outDir, 'images', `${n}.png`));
  writeFileSync(join(outDir, `${name}.json`), JSON.stringify(r.json, null, 1));
  writeFileSync(join(outDir, 'joints.json'), JSON.stringify(r.joints, null, 1));
  const loops = r.summary.filter((s) => s.loop).map((s) => s.name);
  writeFileSync(join(outDir, '預覽.html'), previewHtml(r.json, name, loops), 'utf8');

  // ---- preview GIF (Python + Pillow composites the real art)
  let gifNote: string | undefined = 'skipped';
  const gif = join(outDir, '預覽.gif');
  if (!args.includes('--no-gif')) {
    const framesFile = join(outDir, 'frames.json');
    console.log('合成預覽 GIF…');
    writeFileSync(framesFile, JSON.stringify(previewFrames(r, alphas)));
    gifNote = renderGif(join(outDir, 'images'), framesFile, gif, args.includes('--bones'));
    rmSync(framesFile, { force: true });
  }

  // ---- report
  const missingOptional = PARTS.filter((p) => !p.required && !alphas.has(p.name)).map((p) => p.name);
  const seamFail = rows.filter((x) => x.seams.length);
  const low = rows.filter((x) => x.score < 80);
  const jointIssues = r.warnings; // checkJoints + rig warnings (layers.json names, IK fit)
  const flipFail = rows.filter((x) => x.flips.length);
  const overFail = rows.filter((x) => x.over.length);
  const firstOff = rows.filter((x) => x.firstFrameOff);
  const ok = !seamFail.length && !low.length && !jointIssues.length && !flipFail.length && !overFail.length;
  const md: string[] = [`# ${name} · 角色動作報告`, ''];
  md.push(ok ? `**結論：可以用。** 6 個動作流暢度都在 80 以上、沒有接縫露底、沒有手肘膝蓋反折${budget ? '、關節角度都在預算內' : ''}、關節檢查沒有問題。` : '**結論：需要看一下。** 下面標 ⚠ 的項目照建議處理後重跑同一行指令即可。', '');
  md.push('## 動作', '', '| 動畫 | 長度 | 流暢度 | 接縫 | 翻面 | 第一格離原圖 | 說明 |', '|---|---|---|---|---|---|---|');
  for (const x of rows) {
    const first = x.firstFrame.degrees < 0.5 ? '✓' : `${x.firstFrameOff ? '⚠ ' : ''}${x.firstFrame.label} ${Math.round(x.firstFrame.degrees)}°`;
    const notes = [...x.over.map((o) => `⚠ 超出角度預算：${o}`), ...x.problems];
    md.push(`| ${x.name} | ${x.duration.toFixed(2)} s | ${x.score}${x.score < 80 ? ' ⚠' : ''} | ${x.seams.length ? '⚠ ' + x.seams.join('、') : '✓'} | ${x.flips.length ? '⚠ ' + x.flips.join('、') : '✓'} | ${first} | ${notes.join('<br>')} |`);
  }
  md.push('', '流暢度 100 是滿分，80 以上看起來就順；接縫 ⚠ 表示那個關節在那一秒露出背景（破圖）；翻面 ⚠ 表示手肘或膝蓋折到畫的反方向（反折），或某個部件被鏡像。');
  md.push(`第一格離原圖：動畫第 0 格離畫好的姿勢最遠的關節和角度。待機超過 ${FIRST_FRAME_TOLERANCE}° 標 ⚠：遊戲從靜態圖切進待機時會跳一下。只提醒，不影響結論。`, '');
  if (firstOff.length) md.push(`- ⚠ ${firstOff.map((x) => x.name).join('、')} 不是從原圖開始：預設動作的各部位相位不同，第 0 格時拳頭和肩膀已經在擺動途中。讓第一格貼原圖又不降流暢度的版本還在動作研究中，先只標出來。`, '');
  // joint turns per animation, so a budget can be written from what the motions ask for
  md.push('## 關節角度', '', budget ? `預算來自 \`${show(budgetFile!)}\`，超出的格子標 ⚠。` : '每個動作把每個關節轉離原圖多少度（+ 逆時針／− 順時針）。交件附 `budget.json`（每個關節的圖撐得住幾度）就會檢查超出預算，寫法見 `docs/一行做角色.md`。', '');
  const cols = POSE_JOINTS.filter((j) => r.pose.some((p) => p.range[j.bone] && (p.range[j.bone].plus >= 0.5 || p.range[j.bone].minus >= 0.5)));
  md.push(`| 動畫 | ${cols.map((j) => j.label).join(' | ')} |`, `|---|${cols.map(() => '---|').join('')}`);
  for (const p of r.pose) {
    const cell = (bone: string) => {
      const v = p.range[bone], o = p.over.filter((x) => x.bone === bone);
      return `${o.length ? '⚠ ' : ''}+${Math.round(v.plus)}／−${Math.round(v.minus)}`;
    };
    md.push(`| ${p.animation} | ${cols.map((j) => cell(j.bone)).join(' | ')} |`);
  }
  if (budget) md.push('', `| 預算 | ${cols.map((j) => (budget[j.bone] ? `+${budget[j.bone].plus ?? '∞'}／−${budget[j.bone].minus ?? '∞'}` : '—')).join(' | ')} |`);
  md.push('');
  md.push('## 關節與骨架', '', jointsFile ? `用 \`${show(jointsFile)}\` 的關節。` : '自動猜的關節，存在 `joints.json`。', '');
  if (jointIssues.length) md.push(...jointIssues.map((s) => `- ⚠ ${s}`), '', '修法：用瀏覽器開 `studio/spine-motion-studio.html` →「從圖層建立角色」選這些 PNG，把紅點拖到對的位置；或直接改 `joints.json` 的座標（圖片像素，y 向下），再用 `--joints joints.json` 重跑。', '');
  else md.push('- ✓ 關節與骨架檢查沒有問題', '');
  md.push('## 圖層', '', `- ${alphas.size} 層，畫布 ${canvas.width}×${canvas.height}`, `- 疊圖順序：${orderFile ? '照 `layers.json`' : '模板預設（交件附 `layers.json` 就會照它疊）'}`);
  if (missingOptional.length) md.push(`- 沒有的選用圖層：${missingOptional.join('、')}`);
  if (skipped.length) md.push(`- 略過的檔案：${skipped.join('、')}`);
  if (params) md.push(`- 動作參數：\`${JSON.stringify(params)}\``);
  md.push('', '## 檔案', '', '| 檔案 | 用途 |', '|---|---|');
  md.push(`| \`${name}.json\` + \`images/\` | Spine 4.3 骨架＋6 個動畫。Spine 編輯器 Import Data 匯入，或直接給遊戲載入 |`);
  md.push('| `預覽.gif` | 6 個動作連著播（真實圖層） |', '| `預覽.html` | 離線預覽頁（骨架線、洋蔥皮、軌跡、曲線） |', '| `joints.json` | 這次用的 19 個關節點，可以改了再用 `--joints` 重跑 |');
  writeFileSync(join(outDir, '報告.md'), md.join('\n') + '\n', 'utf8');

  // ---- console summary
  console.log('');
  for (const x of rows) {
    const extra = [...x.flips.map((f) => `翻面 ⚠ ${f}`), ...x.over.map((o) => `超預算 ⚠ ${o}`), ...(x.firstFrameOff ? [`第一格離原圖 ${x.firstFrame.label} ${Math.round(x.firstFrame.degrees)}°`] : [])];
    console.log(`  ${x.name.padEnd(11)} 流暢度 ${String(x.score).padStart(3)}${x.score < 80 ? ' ⚠' : '  '}  接縫 ${x.seams.length ? '⚠ ' + x.seams.join('、') : '✓'}  翻面 ${x.flips.length ? '⚠' : '✓'}${extra.length ? '  ' + extra.join('；') : ''}`);
  }
  for (const s of jointIssues) console.log(`  ⚠ ${s}`);
  console.log('');
  console.log(ok ? '結論：可以用。' : '結論：需要看一下，詳見報告。');
  console.log(`輸出：${show(outDir)}/`);
  console.log(`  ${name}.json + images/   Spine 4.3（Import Data）`);
  console.log('  報告.md                 檢查報告');
  console.log('  預覽.html               離線預覽頁');
  if (!gifNote) console.log('  預覽.gif                動作預覽 GIF');
  else if (gifNote !== 'skipped') console.log(`  （沒有產生 GIF：${gifNote}）`);
}
