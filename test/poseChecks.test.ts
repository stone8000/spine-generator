import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync, cpSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { decodePngAlpha } from '../src/png.ts';
import { buildCharacter, layerName } from '../src/character.ts';
import { parseBudget, poseUsage } from '../src/poseChecks.ts';

const root = fileURLToPath(new URL('..', import.meta.url));
const monkey = join(root, 'examples', 'characters', 'bandit-monkey');
const bin = join(root, 'bin', 'spine-motion.js');

function monkeyRig() {
  const dir = join(monkey, 'layers');
  const alphas = new Map();
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.png'))) alphas.set(layerName(f), decodePngAlpha(new Uint8Array(readFileSync(join(dir, f)))));
  const joints = JSON.parse(readFileSync(join(monkey, 'joints_manual.json'), 'utf8'));
  return buildCharacter(alphas, { joints });
}

test('pose checks: joint turns per animation, frame 0, no flips in the stock motions', () => {
  const r = monkeyRig();
  assert.deepEqual(r.pose.map((p) => p.animation), ['idle', 'idle_weave', 'punch', 'hit', 'jump', 'celebrate']);
  for (const p of r.pose) assert.deepEqual(p.flips, [], p.animation);
  const punch = r.pose.find((p) => p.animation === 'punch')!;
  assert.ok(punch.range.upperArmF.plus > 30, 'the punching arm swings up');
  assert.equal(punch.firstFrame.degrees, 0); // one-shots start on the drawn pose
  const idle = r.pose.find((p) => p.animation === 'idle')!;
  assert.ok(idle.firstFrame.degrees > 5, 'the stock idle starts mid-swing (motion research: idle frame 0 ≠ the art)');
});

test('pose checks: an elbow bent the other way and a mirrored layer are flips', () => {
  const r = monkeyRig();
  const json = structuredClone(r.json);
  const ik = json.constraints.find((c: { name: string }) => c.name === 'arm-front');
  json.animations.flipped = {
    ik: { 'arm-front': [{ time: 0, bendPositive: ik.bendPositive }, { time: 0.5, bendPositive: !ik.bendPositive }, { time: 1, bendPositive: ik.bendPositive }] },
    bones: { head: { scale: [{ time: 0.8, x: -1, y: 1 }] } },
  };
  const u = poseUsage(json).find((p) => p.animation === 'flipped')!;
  const bend = u.flips.find((f) => f.kind === 'bend')!;
  assert.equal(bend.label, '前手肘');
  assert.ok(Math.abs(bend.time - 0.5) < 0.02 && bend.frames >= 14 && bend.frames <= 16, JSON.stringify(bend));
  assert.ok(u.flips.some((f) => f.kind === 'mirror' && f.label.includes('head')));
});

test('pose checks: budget.json by bone or label, hard limit of the research form, overruns listed', () => {
  const b = parseBudget({ joints: { foreArmF: { '+': 40, '-': { soft: 20, hard: 30 } }, 後肩: { '+': 5 } } });
  assert.deepEqual(b.foreArmF, { plus: 40, minus: 30 });
  assert.deepEqual(b.upperArmB, { plus: 5, minus: null });
  assert.throws(() => parseBudget({ elbow: { '+': 1 } }), /不認得/);
  const r = monkeyRig();
  const punch = poseUsage(r.json, b).find((p) => p.animation === 'punch')!;
  assert.ok(punch.over.some((o) => o.bone === 'foreArmF' && o.dir === '-' && o.allowed === 30 && o.used > 30));
  assert.ok(punch.over.some((o) => o.bone === 'upperArmB' && o.dir === '+'));
});

test('character: the report has flips, frame 0 and joint turns, and a budget.json beside the layers fails the overruns', () => {
  const dir = mkdtempSync(join(tmpdir(), 'smk-pose-'));
  try {
    const layers = join(dir, 'monkey', 'layers');
    cpSync(join(monkey, 'layers'), layers, { recursive: true });
    cpSync(join(monkey, 'joints_manual.json'), join(dir, 'monkey', 'joints_manual.json'));
    let r = spawnSync(process.execPath, [bin, 'character', layers, '--no-gif'], { encoding: 'utf8' });
    assert.equal(r.status, 0, r.stderr);
    let report = readFileSync(join(dir, 'monkey', 'monkey_spine', '報告.md'), 'utf8');
    assert.match(report, /\| 翻面 \| 第一格離原圖 \|/);
    assert.match(report, /\| idle \| [\d.]+ s \| \d+ \| ✓ \| ✓ \| ⚠ /);
    assert.match(report, /## 關節角度/);
    writeFileSync(join(dir, 'monkey', 'budget.json'), JSON.stringify({ joints: { 前手肘: { '+': 30, '-': 30 } } }));
    r = spawnSync(process.execPath, [bin, 'character', layers, '--no-gif'], { encoding: 'utf8' });
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stdout, /角度預算：.*budget\.json/);
    assert.match(r.stdout, /超預算 ⚠ 前手肘 [+−]\d+°（預算 30°）/);
    report = readFileSync(join(dir, 'monkey', 'monkey_spine', '報告.md'), 'utf8');
    assert.match(report, /結論：需要看一下/);
    assert.match(report, /\| 預算 \|/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
