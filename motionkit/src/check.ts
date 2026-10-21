/**
 * Smoothness checker for any Spine 4.3 animation, generated or hand-made.
 *
 * Curve level (reads the JSON keys): linear segments in loops, holds, direction kinks and speed jumps at keys,
 * loop seams (value and slope). Runtime level (plays the animation in the real 4.3 runtime, IK and physics included):
 * per-bone acceleration spikes, world-space loop seams, and "everything peaks on the same frame" stiffness.
 */
import { parseSkeleton, sampleAnimation, type SpineJson } from './spineJson.ts';

export type Severity = 'error' | 'warn' | 'info';

export interface Issue {
  severity: Severity;
  code: string;
  bone?: string;
  timeline?: string;
  time?: number;
  message: string;
}

export interface AnimationReport {
  name: string;
  duration: number;
  loop: boolean;
  keyCount: number;
  score: number;
  issues: Issue[];
  movers: { bone: string; travel: number; maxSpeed: number; rotRange: number }[];
}

export interface CheckOptions {
  /** Animations treated as loops. Default: names matching /idle|loop|breath|float|pulse|sway|walk|run/i. */
  loops?: string[];
  only?: string[];
  fps?: number;
}

const LOOP_NAME = /idle|loop|breath|float|pulse|sway|walk|run|stand|wait/i;
const TWO_VALUE = new Set(['translate', 'scale', 'shear']);

interface Seg {
  t0: number;
  t1: number;
  v0: number;
  v1: number;
  kind: 'curve' | 'linear' | 'stepped';
  mOut: number; // slope leaving t0
  mIn: number; // slope arriving at t1
}

/** Units per second that count as "moving" for a timeline type. */
function moveThreshold(timeline: string): number {
  if (timeline.startsWith('rotate')) return 8;
  if (timeline.startsWith('scale')) return 0.08;
  if (timeline.startsWith('shear')) return 8;
  return 8;
}

// biome-ignore lint: schemaless JSON
function channels(timeline: string, keys: any[]): { name: string; segs: Seg[]; firstT: number; lastT: number }[] {
  const values = TWO_VALUE.has(timeline) ? [['x', 0], ['y', 1]] as const : [['value', 0]] as const;
  const dflt = timeline.startsWith('scale') ? 1 : 0;
  return values.map(([field, vi]) => {
    const segs: Seg[] = [];
    for (let i = 0; i < keys.length - 1; i++) {
      const a = keys[i];
      const b = keys[i + 1];
      const t0 = a.time ?? 0;
      const t1 = b.time ?? 0;
      const v0 = a[field] ?? dflt;
      const v1 = b[field] ?? dflt;
      const lin = (v1 - v0) / Math.max(t1 - t0, 1e-9);
      if (a.curve === 'stepped') {
        segs.push({ t0, t1, v0, v1, kind: 'stepped', mOut: 0, mIn: 0 });
      } else if (Array.isArray(a.curve)) {
        const [cx1, cy1, cx2, cy2] = a.curve.slice(vi * 4, vi * 4 + 4);
        const mOut = cx1 - t0 > 1e-9 ? (cy1 - v0) / (cx1 - t0) : Math.sign(cy1 - v0) * 1e6;
        const mIn = t1 - cx2 > 1e-9 ? (v1 - cy2) / (t1 - cx2) : Math.sign(v1 - cy2) * 1e6;
        segs.push({ t0, t1, v0, v1, kind: 'curve', mOut, mIn });
      } else {
        segs.push({ t0, t1, v0, v1, kind: 'linear', mOut: lin, mIn: lin });
      }
    }
    return { name: TWO_VALUE.has(timeline) ? `${timeline}.${field}` : timeline, segs, firstT: keys[0]?.time ?? 0, lastT: keys[keys.length - 1]?.time ?? 0 };
  });
}

function curveIssues(anim: SpineJson, duration: number, loop: boolean): { issues: Issue[]; keyCount: number } {
  const issues: Issue[] = [];
  let keyCount = 0;
  for (const [bone, tls] of Object.entries<SpineJson>(anim.bones ?? {})) {
    for (const [timeline, keys] of Object.entries<SpineJson[]>(tls)) {
      keyCount += keys.length;
      const thr = moveThreshold(timeline);
      for (const ch of channels(timeline, keys)) {
        const where = { bone, timeline: ch.name };
        ch.segs.forEach((s, i) => {
          const changes = Math.abs(s.v1 - s.v0) > thr * 0.01;
          if (s.kind === 'stepped' && changes) {
            issues.push({ ...where, severity: loop ? 'warn' : 'info', code: 'stepped', time: s.t1, message: `階梯曲線：數值在 ${s.t1.toFixed(3)}s 瞬間跳動` });
          }
          if (s.kind === 'linear' && changes && s.t1 - s.t0 >= 0.1) {
            issues.push({ ...where, severity: loop ? 'warn' : 'info', code: 'linear', time: s.t0, message: `等速段 ${s.t0.toFixed(2)}–${s.t1.toFixed(2)}s：速度不變，看起來機械。改成 Bezier 緩入緩出` });
          }
          if (!changes && s.t1 - s.t0 >= 0.25 && loop) {
            issues.push({ ...where, severity: 'info', code: 'hold', time: s.t0, message: `停頓 ${s.t0.toFixed(2)}–${s.t1.toFixed(2)}s：循環中靜止會像卡住，可加微小持續動作` });
          }
          if (s.kind === 'curve' && changes && s.t1 - s.t0 > 0.34 && Math.abs(s.v1 - s.v0) > thr * 0.25) {
            issues.push({ ...where, severity: 'info', code: 'long-segment', time: s.t0, message: `長曲線段 ${s.t0.toFixed(2)}–${s.t1.toFixed(2)}s：Spine runtime 把每段曲線切成 10 條直線，這段每條約 ${(((s.t1 - s.t0) / 10) * 1000).toFixed(0)} ms，大動作會有細微頓挫。中間加關鍵影格讓每段 ≤ 0.2 s` });
          }
          const next = ch.segs[i + 1];
          if (next && s.kind !== 'stepped' && next.kind !== 'stepped') {
            const a = s.mIn;
            const b = next.mOut;
            const big = Math.max(Math.abs(a), Math.abs(b));
            if (big > thr) {
              if (Math.sign(a) !== Math.sign(b) && Math.min(Math.abs(a), Math.abs(b)) > thr * 0.25) {
                issues.push({ ...where, severity: loop ? 'warn' : 'info', code: 'kink', time: s.t1, message: `方向急轉：${s.t1.toFixed(3)}s 速度從 ${a.toFixed(1)} 直接翻成 ${b.toFixed(1)}/s（打擊點可忽略；否則把切線拉平）` });
              } else if (Math.abs(a - b) > 0.6 * big && Math.abs(a - b) > thr) {
                issues.push({ ...where, severity: loop ? 'warn' : 'info', code: 'speed-jump', time: s.t1, message: `速度突變：${s.t1.toFixed(3)}s 前後 ${a.toFixed(1)} → ${b.toFixed(1)}/s（把兩側切線對齊）` });
              }
            }
          }
        });
        if (loop && ch.segs.length) {
          const first = ch.segs[0];
          const last = ch.segs[ch.segs.length - 1];
          if (ch.firstT > 1e-6 || ch.lastT < duration - 1e-6) {
            issues.push({ ...where, severity: 'warn', code: 'loop-coverage', message: `此軌只覆蓋 ${ch.firstT.toFixed(2)}–${ch.lastT.toFixed(2)}s，循環長度 ${duration.toFixed(2)}s：頭尾會停住` });
          }
          if (Math.abs(first.v0 - last.v1) > thr * 0.005) {
            issues.push({ ...where, severity: 'error', code: 'seam-value', message: `循環接縫跳動：開頭 ${first.v0.toFixed(3)}、結尾 ${last.v1.toFixed(3)}` });
          } else if (Math.abs(first.mOut - last.mIn) > Math.max(thr * 0.5, 0.3 * Math.max(Math.abs(first.mOut), Math.abs(last.mIn)))) {
            issues.push({ ...where, severity: 'warn', code: 'seam-slope', message: `循環接縫速度不連續：結尾 ${last.mIn.toFixed(1)}/s → 開頭 ${first.mOut.toFixed(1)}/s，每圈會頓一下` });
          }
        }
      }
    }
  }
  return { issues, keyCount };
}

function median(a: number[]): number {
  const s = [...a].sort((x, y) => x - y);
  return s.length ? s[Math.floor(s.length / 2)] : 0;
}

export function checkSkeleton(json: SpineJson, opts: CheckOptions = {}): AnimationReport[] {
  const data = parseSkeleton(json);
  const fps = opts.fps ?? 30;
  const reports: AnimationReport[] = [];
  for (const anim of data.animations) {
    if (opts.only && !opts.only.includes(anim.name)) continue;
    const loop = opts.loops ? opts.loops.includes(anim.name) : LOOP_NAME.test(anim.name);
    const D = anim.duration;
    const { issues, keyCount } = curveIssues(json.animations?.[anim.name] ?? {}, D, loop);

    const rate = 240;
    const hasPhysics = (json.constraints ?? []).some((c: SpineJson) => c.type === 'physics');
    const s = sampleAnimation(data, anim.name, { rate, seconds: D || 1 / rate, loop, warmUp: loop && hasPhysics ? D * Math.ceil(4 / Math.max(D, 0.1)) : 0 });
    const ikBones = new Set<string>((json.constraints ?? []).filter((c: SpineJson) => c.type === 'ik').flatMap((c: SpineJson) => c.bones ?? []));
    const eventTimes: { name: string; time: number }[] = (json.animations?.[anim.name]?.events ?? []).map((e: SpineJson) => ({ name: e.name, time: e.time ?? 0 }));
    const joltHits: { bone: string; time: number; acc: number; typical: number }[] = [];
    const movers: AnimationReport['movers'] = [];
    const peaks: { bone: string; frame: number }[] = [];
    for (const [bone, b] of s.bones) {
      const n = b.x.length;
      let travel = 0;
      const speed: number[] = [];
      for (let i = 1; i < n; i++) {
        const d = Math.hypot(b.x[i] - b.x[i - 1], b.y[i] - b.y[i - 1]);
        travel = Math.max(travel, Math.hypot(b.x[i] - b.x[0], b.y[i] - b.y[0]));
        speed.push(d * rate);
      }
      const rotRange = Math.max(...b.rot) - Math.min(...b.rot);
      if (travel < 0.5 && rotRange < 0.5) continue;
      movers.push({ bone, travel: +travel.toFixed(2), maxSpeed: +Math.max(...speed).toFixed(1), rotRange: +rotRange.toFixed(2) });
      // acceleration spikes: frame-to-frame speed changes far above this bone's typical change
      const acc = speed.slice(1).map((v, i) => Math.abs(v - speed[i]) * rate);
      const typical = median(acc) + 1e-6;
      const floor = 2000; // units/s² that a viewer notices at 30–60 fps
      let lastFlag = -1;
      acc.forEach((a, i) => {
        if (a > 12 * typical && a > floor && i - lastFlag > rate * 0.05) {
          lastFlag = i;
          joltHits.push({ bone, time: (i + 1) / rate, acc: a, typical });
        }
      });
      if (loop) {
        const seam = Math.hypot(b.x[n - 1] - b.x[0], b.y[n - 1] - b.y[0]);
        if (seam > 0.5) issues.push({ bone, severity: 'error', code: 'world-seam', message: `畫面上的循環接縫：頭尾位置差 ${seam.toFixed(2)}（含 IK / 物理）` });
        if (travel > 2) {
          let best = 0;
          for (let i = 1; i < n; i++) if (b.y[i] > b.y[best]) best = i;
          peaks.push({ bone, frame: Math.round((best / rate) * fps) });
        }
      }
    }
    // one row per moment: bones that jolt within the same frame share a cause (usually the body or an impact)
    joltHits.sort((a, b) => a.time - b.time);
    for (let i = 0; i < joltHits.length;) {
      let j = i;
      while (j + 1 < joltHits.length && joltHits[j + 1].time - joltHits[i].time <= 1 / fps) j++;
      const group = joltHits.slice(i, j + 1);
      const worst = group.reduce((a, b) => (b.acc > a.acc ? b : a));
      const t = +worst.time.toFixed(3);
      const ev = eventTimes.find((e) => worst.time - e.time >= -1 / fps && worst.time - e.time <= 2 / fps);
      const onlyIk = group.every((g) => ikBones.has(g.bone));
      const who = group.length === 1 ? worst.bone : `${group.length} 根骨（最大：${worst.bone}）`;
      if (ev) {
        issues.push({ bone: worst.bone, severity: 'info', code: 'impact', time: t, message: `${who}在事件「${ev.name}」同時急停／急起：刻意的打擊或起跳點（加速度 ${worst.acc.toFixed(0)}/s²）` });
      } else {
        const hint = onlyIk ? '只發生在 IK 鏈上，多半是 IK 打直或翻轉：設定 IK softness，或讓目標不要超出手臂長度' : loop ? '循環裡不該有，檢查這個時間點前後的切線' : '若是打擊點可忽略';
        issues.push({ bone: worst.bone, severity: loop ? 'warn' : 'info', code: 'jolt', time: t, message: `${who}在 ${t}s 頓挫（加速度 ${worst.acc.toFixed(0)}/s²，典型 ${worst.typical.toFixed(0)}）。${hint}` });
      }
      i = j + 1;
    }
    if (loop && peaks.length >= 4) {
      const counts = new Map<number, number>();
      for (const p of peaks) for (const f of [p.frame - 1, p.frame, p.frame + 1]) counts.set(f, (counts.get(f) ?? 0) + 1);
      const [frame, count] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
      if (count / peaks.length > 0.8) {
        issues.push({ severity: 'info', code: 'in-sync', time: frame / fps, message: `全身同步：${count}/${peaks.length} 根移動中的骨骼在第 ${frame} 幀附近同時到最高點。錯開 2–6 幀（頭、手、髮依序）會更生動` });
      }
    }
    // curve-level notes that coincide with an event are the intended hit / launch: say so
    for (const iss of issues) {
      if ((iss.code === 'kink' || iss.code === 'speed-jump') && iss.time !== undefined) {
        const ev = eventTimes.find((e) => Math.abs(e.time - iss.time!) <= 1.5 / fps);
        if (ev) iss.message += `（對應事件「${ev.name}」，刻意的）`;
      }
    }
    const penalty = issues.reduce((p, i) => p + (i.severity === 'error' ? 20 : i.severity === 'warn' ? 5 : 0), 0);
    reports.push({ name: anim.name, duration: +D.toFixed(3), loop, keyCount, score: Math.max(0, 100 - penalty), issues, movers });
  }
  return reports;
}

export function reportMarkdown(file: string, reports: AnimationReport[]): string {
  const sev = { error: '錯誤', warn: '警告', info: '提示' } as const;
  const lines = [`# 流暢度檢查：${file}`, '', '| 動畫 | 長度 | 循環 | 關鍵影格 | 錯誤 | 警告 | 提示 | 分數 |', '|---|---|---|---|---|---|---|---|'];
  for (const r of reports) {
    const c = (s: Severity) => r.issues.filter((i) => i.severity === s).length;
    lines.push(`| ${r.name} | ${r.duration}s | ${r.loop ? '是' : '否'} | ${r.keyCount} | ${c('error')} | ${c('warn')} | ${c('info')} | ${r.score} |`);
  }
  lines.push('', '分數：100 起算，每個錯誤 −20、警告 −5；提示不扣分。一次性動畫（出拳、受擊、跳躍）的急停急起是刻意的，與事件同時發生時會標成「刻意的打擊或起跳點」。', '');
  for (const r of reports) {
    lines.push(`## ${r.name}`, '');
    if (!r.issues.length) lines.push('沒有發現問題。', '');
    else {
      lines.push('| 等級 | 骨骼 | 軌道 | 時間 | 說明 |', '|---|---|---|---|---|');
      for (const i of r.issues) lines.push(`| ${sev[i.severity]} | ${i.bone ?? '—'} | ${i.timeline ?? '—'} | ${i.time !== undefined ? i.time.toFixed(3) + 's' : '—'} | ${i.message} |`);
      lines.push('');
    }
    if (r.movers.length) {
      lines.push('<details><summary>移動中的骨骼</summary>', '', '| 骨骼 | 最大位移 | 最大速度/s | 旋轉範圍° |', '|---|---|---|---|');
      for (const m of r.movers) lines.push(`| ${m.bone} | ${m.travel} | ${m.maxSpeed} | ${m.rotRange} |`);
      lines.push('', '</details>', '');
    }
  }
  return lines.join('\n');
}
