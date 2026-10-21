/**
 * Standalone HTML preview: plays every animation of a skeleton in the real Spine 4.3 runtime (embedded, works
 * offline), drawn as bones. Onion skin and motion trails make arcs and spacing visible; the graph shows the selected
 * bone's height and angle over time with the playhead.
 */
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import type { SpineJson } from './spineJson.ts';

const require = createRequire(import.meta.url);

export function previewHtml(json: SpineJson, title: string, loops: string[] = []): string {
  const runtime = readFileSync(require.resolve('@esotericsoftware/spine-core/dist/iife/spine-core.min.js'), 'utf8');
  const data = JSON.stringify(json).replace(/</g, '\\u003c');
  return `<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)} · 動作預覽</title>
<style>
:root{--bg:#14161b;--panel:#1c1f26;--ink:#e7e9ee;--muted:#8b93a1;--rule:#2c313b;--accent:#f0565d;--bone:#cfd5df;--trail:#4ea1ff;--ghost:#ffffff}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font:14px/1.5 "Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif}
main{display:grid;grid-template-rows:auto 1fr 170px;height:100%;padding:0 max(16px,env(safe-area-inset-left)) 12px max(16px,env(safe-area-inset-right));gap:10px}
header{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;padding-top:12px}
h1{font-size:15px;margin:0 8px 0 0;font-weight:700}
select,button,input{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--rule);border-radius:4px;padding:4px 8px}
button{cursor:pointer}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
label{display:inline-flex;gap:6px;align-items:center;color:var(--muted)}
.time{font-family:Consolas,monospace;font-variant-numeric:tabular-nums;color:var(--muted);min-width:150px}
canvas{width:100%;height:100%;display:block;background:var(--panel);border:1px solid var(--rule);border-radius:4px}
#scrub{flex:1 1 220px;padding:0}
#event{position:fixed;top:64px;left:50%;transform:translateX(-50%);background:var(--accent);color:#fff;padding:4px 12px;border-radius:4px;font-weight:700;opacity:0;transition:opacity .25s}
</style></head><body><main>
<header>
 <h1>${escapeHtml(title)}</h1>
 <select id="anim" aria-label="動畫"></select>
 <button id="play">暫停</button>
 <label>速度 <select id="speed"><option>0.25</option><option>0.5</option><option selected>1</option></select></label>
 <label><input type="checkbox" id="loop" checked> 循環</label>
 <label><input type="checkbox" id="onion"> 洋蔥皮</label>
 <label><input type="checkbox" id="trail" checked> 軌跡</label>
 <label>觀察骨骼 <select id="bone"></select></label>
 <input type="range" id="scrub" min="0" max="1000" value="0" aria-label="時間軸">
 <span class="time" id="time"></span>
</header>
<canvas id="view"></canvas>
<canvas id="graph"></canvas>
</main><div id="event"></div>
<script>${runtime}</script>
<script>
const SKELETON = ${data};
const LOOPS = ${JSON.stringify(loops)};
const nullLoader = { newRegionAttachment(){return null}, newMeshAttachment(){return null}, newBoundingBoxAttachment(){return null}, newPathAttachment(){return null}, newPointAttachment(){return null}, newClippingAttachment(){return null} };
const copy = JSON.parse(JSON.stringify(SKELETON));
for (const a of Object.values(copy.animations || {})) delete a.attachments;
const data = new spine.SkeletonJson(nullLoader).readSkeletonData(copy);
const sk = new spine.Skeleton(data);
const state = new spine.AnimationState(new spine.AnimationStateData(data));
const $ = (id) => document.getElementById(id);
const view = $('view'), graph = $('graph'), vctx = view.getContext('2d'), gctx = graph.getContext('2d');
const names = data.animations.map((a) => a.name);
for (const n of names) $('anim').add(new Option(n, n));
for (const b of data.bones) $('bone').add(new Option(b.name, b.name));
const LOOPY = /idle|loop|breath|float|pulse|sway|walk|run|stand|wait/i;
let playing = true, time = 0, speed = 1, current = names[0];
let bounds = { x0: -100, y0: -100, x1: 100, y1: 100 };
let trails = new Map(), curve = { y: [], rot: [] };
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const isLoop = (n) => LOOPS.length ? LOOPS.includes(n) : LOOPY.test(n);

function pose(t, physics) {
  sk.setupPose();
  const anim = data.findAnimation(current);
  state.setAnimation(0, current, $('loop').checked);
  state.update(t);
  state.apply(sk);
  sk.updateWorldTransform(physics ? spine.Physics.update : spine.Physics.pose);
  return anim;
}
function tip(b) { const p = b.getAppliedPose(); return [p.worldX + p.a * b.data.length, p.worldY + p.c * b.data.length]; }
function measure() {
  const anim = data.findAnimation(current);
  const D = anim.duration || 1;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  trails = new Map(); curve = { y: [], rot: [] };
  const sel = $('bone').value;
  for (let i = 0; i <= 240; i++) {
    const t = D * i / 240;
    pose(t, false);
    for (const b of sk.bones) {
      const p = b.getAppliedPose(); const [tx, ty] = tip(b);
      x0 = Math.min(x0, p.worldX, tx); y0 = Math.min(y0, p.worldY, ty); x1 = Math.max(x1, p.worldX, tx); y1 = Math.max(y1, p.worldY, ty);
      if (!trails.has(b.data.name)) trails.set(b.data.name, []);
      trails.get(b.data.name).push([tx, ty]);
      if (b.data.name === sel) { curve.y.push(p.worldY); curve.rot.push(p.getWorldRotationX()); }
    }
  }
  const pad = Math.max(x1 - x0, y1 - y0) * 0.12 + 10;
  bounds = { x0: x0 - pad, y0: y0 - pad, x1: x1 + pad, y1: y1 + pad };
}
function fit(c) {
  const r = c.getBoundingClientRect(); const dpr = devicePixelRatio || 1;
  if (c.width !== Math.round(r.width * dpr)) { c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); }
  return [c.width, c.height, dpr];
}
function drawBones(ctx, toX, toY, alpha, color) {
  ctx.globalAlpha = alpha;
  for (const b of sk.bones) {
    const p = b.getAppliedPose(); const [tx, ty] = tip(b);
    const bc = b.data.color;
    const isDefault = !bc || bc.a === 0 || (bc.r > 0.6 && bc.g > 0.6 && bc.b > 0.6); // no color in the JSON = (0,0,0,0)
    const c = color || (isDefault ? null : 'rgb(' + Math.round(bc.r * 255) + ',' + Math.round(bc.g * 255) + ',' + Math.round(bc.b * 255) + ')');
    ctx.strokeStyle = c || cssVar('--bone');
    ctx.fillStyle = ctx.strokeStyle;
    if (b.data.length > 0) {
      ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(toX(p.worldX), toY(p.worldY)); ctx.lineTo(toX(tx), toY(ty)); ctx.stroke();
      ctx.beginPath(); ctx.arc(toX(p.worldX), toY(p.worldY), 3.5, 0, 6.283); ctx.fill();
    } else {
      const x = toX(p.worldX), y = toY(p.worldY); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x - 7, y); ctx.lineTo(x + 7, y); ctx.moveTo(x, y - 7); ctx.lineTo(x, y + 7); ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
}
function render() {
  const [w, h, dpr] = fit(view);
  vctx.clearRect(0, 0, w, h);
  const s = Math.min(w / (bounds.x1 - bounds.x0), h / (bounds.y1 - bounds.y0));
  const ox = (w - (bounds.x1 - bounds.x0) * s) / 2, oy = (h - (bounds.y1 - bounds.y0) * s) / 2;
  const toX = (x) => ox + (x - bounds.x0) * s, toY = (y) => h - oy - (y - bounds.y0) * s;
  const anim = data.findAnimation(current); const D = anim.duration || 1;
  if ($('trail').checked) {
    const sel = $('bone').value;
    for (const [name, pts] of trails) {
      vctx.strokeStyle = name === sel ? cssVar('--accent') : cssVar('--trail');
      vctx.globalAlpha = name === sel ? 0.9 : 0.25; vctx.lineWidth = name === sel ? 2 : 1;
      vctx.beginPath(); pts.forEach(([x, y], i) => i ? vctx.lineTo(toX(x), toY(y)) : vctx.moveTo(toX(x), toY(y))); vctx.stroke();
    }
    vctx.globalAlpha = 1;
  }
  if ($('onion').checked) {
    for (let k = 6; k >= 1; k--) { pose(Math.max(0, time - k / 30), false); drawBones(vctx, toX, toY, 0.08 + 0.03 * (6 - k), '#ffffff'); }
  }
  pose(time, true);
  drawBones(vctx, toX, toY, 1);
  // graph
  const [gw, gh] = fit(graph);
  gctx.clearRect(0, 0, gw, gh);
  const plot = (arr, color, label, row) => {
    if (!arr.length) return;
    const lo = Math.min(...arr), hi = Math.max(...arr), span = Math.max(hi - lo, 1e-6);
    gctx.strokeStyle = color; gctx.lineWidth = 2 * dpr; gctx.beginPath();
    arr.forEach((v, i) => { const x = (i / (arr.length - 1)) * gw, y = gh * 0.1 + (1 - (v - lo) / span) * gh * 0.8; i ? gctx.lineTo(x, y) : gctx.moveTo(x, y); });
    gctx.stroke();
    gctx.fillStyle = color; gctx.font = (12 * dpr) + 'px system-ui';
    gctx.fillText(label + '  ' + lo.toFixed(1) + ' … ' + hi.toFixed(1), 8 * dpr, (16 + row * 16) * dpr);
  };
  plot(curve.y, '#4ea1ff', '高度 y', 0);
  plot(curve.rot, '#f2c94c', '角度 °', 1);
  const px = ((time % D) / D) * gw;
  gctx.strokeStyle = '#f0565d'; gctx.lineWidth = 1 * dpr; gctx.beginPath(); gctx.moveTo(px, 0); gctx.lineTo(px, gh); gctx.stroke();
  const frame = Math.round((time % (D + 1e-9)) * 30);
  $('time').textContent = (time % (D + 1e-9)).toFixed(3) + ' s · 第 ' + frame + ' 幀 / ' + Math.round(D * 30);
  $('scrub').value = String(Math.round(((time % D) / D) * 1000));
}
let last = performance.now();
function tick(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (playing) {
    const D = data.findAnimation(current).duration || 1;
    const before = time; time += dt * speed;
    if (!$('loop').checked && time > D) { time = D; playing = false; $('play').textContent = '播放'; }
    const anim = data.findAnimation(current);
    for (const tl of anim.timelines) if (tl instanceof spine.EventTimeline) {
      for (const ev of tl.events) { const a = before % D, b = time % D; const hit = a <= b ? ev.time > a && ev.time <= b : ev.time > a || ev.time <= b; if (hit) flash(ev.data.name); }
    }
    sk.update(dt * speed);
  }
  render(); requestAnimationFrame(tick);
}
let flashTimer = 0;
function flash(name) { const e = $('event'); e.textContent = '事件：' + name; e.style.opacity = 1; clearTimeout(flashTimer); flashTimer = setTimeout(() => (e.style.opacity = 0), 600); }
$('anim').onchange = () => { current = $('anim').value; time = 0; $('loop').checked = isLoop(current); playing = true; $('play').textContent = '暫停'; measure(); };
$('bone').onchange = measure;
$('play').onclick = () => { playing = !playing; $('play').textContent = playing ? '暫停' : '播放'; };
$('speed').onchange = () => (speed = +$('speed').value);
$('scrub').oninput = () => { playing = false; $('play').textContent = '播放'; time = (+$('scrub').value / 1000) * (data.findAnimation(current).duration || 1); };
$('loop').checked = isLoop(current);
const headBone = data.bones.find((b) => /head/i.test(b.name)) || data.bones[Math.min(1, data.bones.length - 1)];
$('bone').value = headBone.name;
measure(); requestAnimationFrame(tick);
</script></body></html>`;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}
