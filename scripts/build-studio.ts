/** Bundles the studio (engine + Spine 4.3 runtime + example rigs + example layer art) into one self-contained HTML file. */
import { build } from 'esbuild';
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

const result = await build({
  entryPoints: [here('../src/studio/app.ts')],
  bundle: true,
  format: 'iife',
  platform: 'browser',
  target: 'es2020',
  minify: true,
  write: false,
  charset: 'utf8',
  legalComments: 'none',
});
const js = result.outputFiles[0].text.replace(/<\/script/gi, '<\/script');
const examples = {
  fighter: JSON.parse(readFileSync(here('../examples/rigs/fighter.json'), 'utf8')),
  symbol: JSON.parse(readFileSync(here('../examples/rigs/symbol.json'), 'utf8')),
};
const layerSet = (kind: string) => {
  const dir = here(`../examples/layers/${kind}/`);
  const files = readdirSync(dir).filter((f) => f.endsWith('.png')).sort();
  return Object.fromEntries(files.map((f) => [f.replace(/\.png$/, ''), `data:image/png;base64,${readFileSync(dir + f).toString('base64')}`]));
};
const layers = { good: layerSet('good'), bad: layerSet('bad') };
const html = readFileSync(here('../src/studio/template.html'), 'utf8')
  .replace('/*__EXAMPLES__*/null', () => JSON.stringify(examples))
  .replace('/*__LAYERS__*/null', () => JSON.stringify(layers))
  .replace('/*__APP__*/', () => js);
mkdirSync(here('../studio/'), { recursive: true });
const out = here('../studio/spine-motion-studio.html');
writeFileSync(out, html, 'utf8');
console.log(`寫入 ${out}（${(html.length / 1024).toFixed(0)} KB）`);
