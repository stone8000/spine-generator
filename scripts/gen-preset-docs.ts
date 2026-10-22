/** Regenerates docs/動作預設手冊.md from the preset definitions, so the manual never drifts from the code. */
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { PRESETS } from '../src/presets.ts';

const out = fileURLToPath(new URL('../docs/動作預設手冊.md', import.meta.url));
mkdirSync(fileURLToPath(new URL('../docs/', import.meta.url)), { recursive: true });

const lines: string[] = [
  '# 動作預設手冊',
  '',
  '> 本文件由 `npm run docs` 從程式碼自動產生，請勿手動修改。',
  '',
  '所有角色類預設都以**畫面空間**描述（往前、往上、前傾），工具會依你的骨架換算成每根骨的局部關鍵影格，所以同一個預設可用在任何骨架、任何朝向。`facing`：角色面向右為 `1`、面向左為 `-1`。`size`：角色大小相對於 Iron Jaw 量測（1 ≈ 角色高 580 單位），也可以改填 `characterHeight`。',
  '',
  '| 預設 | 名稱 | 類型 |',
  '|---|---|---|',
  ...PRESETS.map((p) => `| [\`${p.id}\`](#${p.id}) | ${p.title} | ${p.kind === 'loop' ? '循環' : '一次'} |`),
  '',
];
for (const p of PRESETS) {
  lines.push(`## ${p.id}`, '', `**${p.title}**（${p.kind === 'loop' ? '循環' : '一次'}）`, '', p.summary, '');
  lines.push('| 角色 | 必填 | 說明 |', '|---|---|---|');
  for (const r of p.roles) lines.push(`| \`${r.name}\`${r.list ? '（骨骼清單）' : ''} | ${r.required ? '是' : ''} | ${r.desc} |`);
  lines.push('');
  if (p.params.length) {
    lines.push('| 參數 | 預設值 | 說明 |', '|---|---|---|');
    for (const x of p.params) lines.push(`| \`${x.name}\` | \`${JSON.stringify(x.default)}\` | ${x.desc} |`);
    lines.push('');
  }
  lines.push('```json', JSON.stringify({ name: p.id.replace(/-/g, '_'), preset: p.id, params: Object.fromEntries(p.params.map((x) => [x.name, x.default])) }, null, 2), '```', '');
}
writeFileSync(out, lines.join('\n'), 'utf8');
console.log(`寫入 ${out}`);
