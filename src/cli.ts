/**
 * spine-motion: generate smooth Spine 4.3 animations from presets, check smoothness, preview.
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { PRESETS } from './presets.ts';
import { compileRecipe, type Recipe } from './recipe.ts';
import { checkSkeleton, reportMarkdown } from './check.ts';
import { previewHtml } from './preview.ts';
import { guessRecipe } from './roles.ts';
import { assertSpine43 } from './spineJson.ts';
import { loadJson, saveJson } from './io.ts';
import { cmdCharacter, CHARACTER_HELP } from './characterCli.ts';

const HELP = `spine-motion · Spine 4.3 流暢動作產生器

用法：
${CHARACTER_HELP}
  spine-motion presets                         列出所有動作預設與參數
  spine-motion init <骨架.json> [-o 配方.json] [--facing 1|-1]
                                               依骨骼名稱猜角色，產生配方草稿
  spine-motion generate <骨架.json> <配方.json> [-o 輸出.json] [--preview] [--check]
                                               依配方產生動畫，寫進骨架 JSON 的副本
  spine-motion check <骨架.json> [--loops a,b] [--only a,b] [-o 報告.md] [--json]
                                               流暢度檢查（手做或產生的動畫都可以）
  spine-motion preview <骨架.json> [-o 預覽.html]
                                               產生可離線開啟的動作預覽頁

骨架必須是 Spine 4.3 編輯器匯出的 JSON。`;

function arg(args: string[], name: string): string | undefined {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
}
function positional(args: string[]): string[] {
  const out: string[] = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('-')) {
      if (!['--preview', '--check', '--json'].includes(args[i])) i++;
      continue;
    }
    out.push(args[i]);
  }
  return out;
}
function sibling(file: string, suffix: string): string {
  return join(dirname(resolve(file)), basename(file).replace(/\.json$/i, '') + suffix);
}

function cmdPresets(args: string[]): void {
  if (args.includes('--json')) {
    console.log(JSON.stringify(PRESETS.map(({ build: _b, ...p }) => p), null, 1));
    return;
  }
  for (const p of PRESETS) {
    console.log(`\n■ ${p.id}  ${p.title}  [${p.kind === 'loop' ? '循環' : '一次'}]`);
    console.log(`  ${p.summary}`);
    console.log(`  角色：${p.roles.map((r) => `${r.name}${r.required ? '*' : ''}${r.list ? '[]' : ''}（${r.desc}）`).join('、')}`);
    if (p.params.length) console.log(`  參數：${p.params.map((x) => `${x.name}=${JSON.stringify(x.default)}（${x.desc}）`).join('、')}`);
  }
  console.log('\n* = 必填角色；[] = 骨骼清單');
}

function cmdInit(args: string[]): void {
  const [file] = positional(args);
  if (!file) throw new Error('請指定骨架 JSON');
  const json = loadJson(file);
  assertSpine43(json, file);
  const facing = (Number(arg(args, '--facing') ?? 1) < 0 ? -1 : 1) as 1 | -1;
  const { recipe, notes } = guessRecipe(json, facing);
  const out = arg(args, '-o') ?? sibling(file, '.recipe.json');
  if (existsSync(out) && !args.includes('--force')) throw new Error(`${out} 已存在；要覆寫請加 --force`);
  writeFileSync(out, JSON.stringify(recipe, null, 2), 'utf8');
  console.log(`已產生配方：${out}`);
  console.log(`角色：${Object.entries(recipe.roles).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('/') : v}`).join('  ')}`);
  console.log(`動畫：${recipe.animations.map((a) => `${a.name}(${a.preset ?? '自訂'})`).join('  ')}`);
  for (const n of notes) console.log(`注意：${n}`);
  console.log('請檢查 roles 對不對、facing 是否正確（角色面向右 = 1），再執行 generate。');
}

function cmdGenerate(args: string[]): void {
  const [file, recipeFile] = positional(args);
  if (!file || !recipeFile) throw new Error('用法：spine-motion generate <骨架.json> <配方.json>');
  const skeleton = loadJson(file);
  const recipe = JSON.parse(readFileSync(recipeFile, 'utf8')) as Recipe;
  const { json, summary } = compileRecipe(skeleton, recipe);
  const out = arg(args, '-o') ?? sibling(file, '.motion.json');
  saveJson(out, json);
  console.log(`已寫入：${out}`);
  for (const s of summary) {
    console.log(`  ${s.name.padEnd(14)} ${s.loop ? '循環' : '一次'} ${s.duration.toFixed(2)}s  ${s.timelines} 軌 / ${s.keys} 個關鍵影格${s.events.length ? '  事件 ' + s.events.join(' ') : ''}${s.bakedPhysics ? `  烘焙物理 ${s.bakedPhysics} 根骨` : ''}${s.physics ? `  runtime 物理 ${s.physics} 個` : ''}`);
  }
  const loops = summary.filter((s) => s.loop).map((s) => s.name);
  if (args.includes('--check')) runCheck(json, out, loops, undefined, sibling(out, '.check.md'));
  if (args.includes('--preview')) {
    const html = sibling(out, '.preview.html');
    writeFileSync(html, previewHtml(json, basename(out), loops), 'utf8');
    console.log(`預覽：${html}`);
  }
}

function runCheck(json: unknown, file: string, loops: string[] | undefined, only: string[] | undefined, out?: string, asJson = false): void {
  const reports = checkSkeleton(json, { loops, only });
  if (asJson) {
    console.log(JSON.stringify(reports, null, 1));
    return;
  }
  const md = reportMarkdown(basename(file), reports);
  if (out) {
    writeFileSync(out, md, 'utf8');
    console.log(`檢查報告：${out}`);
  }
  for (const r of reports) {
    const e = r.issues.filter((i) => i.severity === 'error').length;
    const w = r.issues.filter((i) => i.severity === 'warn').length;
    console.log(`  ${r.name.padEnd(14)} 分數 ${String(r.score).padStart(3)}  錯誤 ${e}  警告 ${w}  提示 ${r.issues.length - e - w}${r.loop ? '  (循環)' : ''}`);
  }
}

function cmdCheck(args: string[]): void {
  const [file] = positional(args);
  if (!file) throw new Error('請指定骨架 JSON');
  const json = loadJson(file);
  assertSpine43(json, file);
  const list = (n: string) => arg(args, n)?.split(',').map((s) => s.trim()).filter(Boolean);
  runCheck(json, file, list('--loops'), list('--only'), arg(args, '-o') ?? (args.includes('--json') ? undefined : sibling(file, '.check.md')), args.includes('--json'));
}

function cmdPreview(args: string[]): void {
  const [file] = positional(args);
  if (!file) throw new Error('請指定骨架 JSON');
  const json = loadJson(file);
  assertSpine43(json, file);
  const out = arg(args, '-o') ?? sibling(file, '.preview.html');
  writeFileSync(out, previewHtml(json, basename(file), arg(args, '--loops')?.split(',') ?? []), 'utf8');
  console.log(`預覽：${out}`);
}

export function main(argv: string[]): number {
  const [cmd, ...args] = argv;
  try {
    switch (cmd) {
      case 'presets': cmdPresets(args); break;
      case 'init': cmdInit(args); break;
      case 'generate': cmdGenerate(args); break;
      case 'check': cmdCheck(args); break;
      case 'preview': cmdPreview(args); break;
      case 'character': cmdCharacter(args); break;
      default: console.log(HELP); return cmd ? 1 : 0;
    }
    return 0;
  } catch (e) {
    console.error(`錯誤：${(e as Error).message}`);
    return 1;
  }
}

if (import.meta.url === `file:///${process.argv[1]?.replace(/\\/g, '/').replace(/^\//, '')}` || process.argv[1]?.endsWith('cli.ts')) {
  process.exitCode = main(process.argv.slice(2));
}
