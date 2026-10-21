import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { guessRecipe } from '../src/roles.ts';

const root = fileURLToPath(new URL('..', import.meta.url));
const bin = join(root, 'bin', 'spine-motion.js');
const fighterPath = join(root, 'examples', 'rigs', 'fighter.json');
const run = (...args: string[]) => spawnSync(process.execPath, [bin, ...args], { encoding: 'utf8' });

test('init guesses the roles of the test fighter', () => {
  const { recipe } = guessRecipe(JSON.parse(readFileSync(fighterPath, 'utf8')), 1);
  assert.equal(recipe.roles.hip, 'hip');
  assert.equal(recipe.roles.head, 'head');
  assert.deepEqual(recipe.roles.hair, ['hair1', 'hair2', 'hair3']);
  assert.equal(recipe.roles.fistFront, 'ik-fist-front'); // further toward the facing direction
  assert.equal(recipe.roles.fistBack, 'ik-fist-back');
  assert.ok(recipe.animations.some((a) => a.preset === 'fighter-idle-bounce'));
});

test('init → generate → check → preview through the real command line', () => {
  const dir = mkdtempSync(join(tmpdir(), 'smk-'));
  try {
    const recipe = join(dir, 'r.json');
    const out = join(dir, 'out.json');
    assert.equal(run('init', fighterPath, '-o', recipe).status, 0);
    const g = run('generate', fighterPath, recipe, '-o', out, '--check', '--preview');
    assert.equal(g.status, 0, g.stderr);
    assert.ok(existsSync(out) && existsSync(join(dir, 'out.check.md')) && existsSync(join(dir, 'out.preview.html')));
    const json = JSON.parse(readFileSync(out, 'utf8'));
    assert.ok(Object.keys(json.animations).includes('idle'));
    const c = run('check', out, '--json');
    assert.equal(c.status, 0);
    const reports = JSON.parse(c.stdout);
    const idle = reports.find((r: { name: string }) => r.name === 'idle');
    assert.equal(idle.issues.filter((i: { severity: string }) => i.severity !== 'info').length, 0);
    const html = readFileSync(join(dir, 'out.preview.html'), 'utf8');
    assert.ok(html.includes('var spine=') && html.includes('"idle"'), 'preview embeds the runtime and the data');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a Spine 4.2 export is refused with an actionable message', () => {
  const dir = mkdtempSync(join(tmpdir(), 'smk-'));
  try {
    const old = join(dir, 'old.json');
    writeFileSync(old, JSON.stringify({ skeleton: { spine: '4.2.43' }, bones: [{ name: 'root' }], ik: [] }));
    const r = run('check', old);
    assert.equal(r.status, 1);
    assert.match(r.stderr, /4\.3/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('help prints usage', () => {
  const out = execFileSync(process.execPath, [bin], { encoding: 'utf8' });
  assert.match(out, /spine-motion/);
});
