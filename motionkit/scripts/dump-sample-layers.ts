/** Writes the synthetic good/bad layer sets as raw alpha + a manifest, for scripts/render_samples.py to color into PNGs. */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { synthLayers } from '../test/fixtures/synthLayers.ts';
import { defaultJoints, JOINTS, PARTS, CANVAS } from '../src/rigTemplate.ts';

const out = process.argv[2];
if (!out) throw new Error('usage: node scripts/dump-sample-layers.ts <out-dir>');
const manifest: Record<string, unknown> = { canvas: CANVAS, joints: JOINTS.map((j) => ({ ...j, ...defaultJoints()[j.id] })), parts: PARTS.map((p) => ({ name: p.name, label: p.label })), sets: {} };
for (const kind of ['good', 'bad'] as const) {
  const dir = join(out, kind);
  mkdirSync(dir, { recursive: true });
  const names: string[] = [];
  for (const [name, a] of synthLayers(kind)) {
    writeFileSync(join(dir, `${name}.alpha`), a.data);
    names.push(name);
  }
  (manifest.sets as Record<string, string[]>)[kind] = names;
}
writeFileSync(join(out, 'manifest.json'), JSON.stringify(manifest, null, 1));
console.log('dumped to', out);
