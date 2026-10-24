/**
 * The kit's autorig joint guess (guessJoints) for a folder of layers, for tools outside the studio.
 *
 *   node scripts/guess-joints.ts <manifest.json>
 *
 * manifest.json: {"width": W, "height": H, "layers": {"<layer name>": "<raw alpha file>"}}, each raw file W x H bytes
 * (row-major, y down: the PNG's alpha channel). Prints {"joints": {<JointId>: {"x", "y"}}, "defaults": {...}} (canvas
 * px, y down). spinegen.ingest_layers decodes the PNGs and calls this, so the guess is the studio's own.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { guessJoints, type AlphaMap } from '../src/autorig.ts';
import { defaultJoints } from '../src/rigTemplate.ts';

const path = process.argv[2];
if (!path) throw new Error('usage: node scripts/guess-joints.ts <manifest.json>');
const man = JSON.parse(readFileSync(path, 'utf8'));
const layers = new Map<string, AlphaMap>();
for (const [name, file] of Object.entries(man.layers as Record<string, string>)) {
  const data = new Uint8Array(readFileSync(resolve(dirname(path), file)));
  if (data.length !== man.width * man.height) throw new Error(`${name}: ${data.length} bytes, expected ${man.width * man.height}`);
  layers.set(name, { width: man.width, height: man.height, data });
}
const canvas = { width: man.width, height: man.height };
process.stdout.write(JSON.stringify({ joints: guessJoints(layers), defaults: defaultJoints(canvas) }) + '\n');
