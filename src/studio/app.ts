/**
 * Spine 動作工作室 — browser app.
 *  • Start from a Spine 4.3 skeleton JSON, or from an image agent's layered PNGs (auto-rig with draggable joints).
 *  • Pick a dependable preset, fine-tune it with sliders, watch it play in the real Spine 4.3 runtime with the real art.
 *  • Live smoothness score, and (with art) a seam check that finds joints where background shows through.
 *  • Queue animations, export animation JSON / recipe. Everything runs locally.
 */
import { Skeleton, AnimationState, AnimationStateData, Physics, EventTimeline, type SkeletonData, type Bone } from '@esotericsoftware/spine-core';
import { PRESETS, type PresetDef } from '../presets.ts';
import { CATALOG, COMMON_PARAMS, type CatalogEntry, type ParamUi } from '../catalog.ts';
import { compileRecipe, type Recipe, type RecipeAnimation } from '../recipe.ts';
import { checkSkeleton, type AnimationReport } from '../check.ts';
import { guessRecipe, estimateSize } from '../roles.ts';
import { assertSpine43, parseSkeleton, type SpineJson } from '../spineJson.ts';
import { buildRig, guessJoints, JOINT_LINKS, type AlphaMap } from '../autorig.ts';
import { JOINTS, PARTS, defaultJoints, CANVAS, type Joints, type JointId, type CanvasSize } from '../rigTemplate.ts';
import { checkSeams, type SeamIssue } from '../seams.ts';

declare global {
  interface Window {
    __EXAMPLES__: { fighter: SpineJson; symbol: SpineJson } | null;
    __LAYERS__: { good: Record<string, string>; bad: Record<string, string> } | null;
    __studio: unknown;
  }
}

// ---------------------------------------------------------------- state
interface Draft {
  presetId: string;
  name: string;
  params: Record<string, unknown>;
  timeScale: number;
  amplitude: number;
  facing: 1 | -1;
}

interface LayerArt {
  bitmap: ImageBitmap;
  alpha: AlphaMap;
}

interface RigEdit {
  base: string;
  canvas: CanvasSize;
  layers: Map<string, LayerArt>;
  rejected: { name: string; why: string }[];
  joints: Joints;
}

const state = {
  skeleton: null as SpineJson | null,
  fileName: '',
  bones: [] as string[],
  roles: {} as Record<string, string | string[]>,
  /** Recipe size: motion distances scale with the rig (1 = 580-unit character). */
  size: 1,
  facing: 1 as 1 | -1,
  category: 'all' as 'all' | 'character' | 'symbol',
  advanced: false,
  draft: null as Draft | null,
  drafts: new Map<string, Draft>(), // remembers tweaks per preset
  queue: [] as RecipeAnimation[],
  compiled: null as { json: SpineJson; data: SkeletonData; report: AnimationReport | null; loop: boolean; duration: number } | null,
  error: '',
  /** Art for the current skeleton (when it was built from layers). */
  art: null as Map<string, LayerArt> | null,
  /** Joint editing step before a rig exists. */
  rig: null as RigEdit | null,
  seams: null as SeamIssue[] | null,
  seamBusy: false,
};

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const presetOf = (id: string): PresetDef => PRESETS.find((p) => p.id === id)!;
const catalogOf = (id: string): CatalogEntry => CATALOG.find((c) => c.id === id)!;
const stars = (n: number) => '★'.repeat(n) + '☆'.repeat(3 - n);

function toast(msg: string) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, ...kids: (Node | string)[]): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}

function setStatus(text: string, error = false) {
  const s = $('status');
  s.textContent = text;
  s.classList.toggle('err', error);
}

// ---------------------------------------------------------------- skeleton loading
function loadSkeleton(json: SpineJson, fileName: string, opts: { roles?: Record<string, string | string[]>; size?: number; art?: Map<string, LayerArt>; notes?: string[] } = {}) {
  try {
    assertSpine43(json, fileName);
    const guessed = opts.roles ? null : guessRecipe(json, 1);
    state.skeleton = json;
    state.fileName = fileName;
    state.bones = (json.bones as { name: string }[]).map((b) => b.name);
    state.roles = opts.roles ?? guessed!.recipe.roles;
    state.size = opts.size ?? guessed!.recipe.size ?? 1;
    state.facing = 1;
    state.queue = [];
    state.drafts.clear();
    state.draft = null;
    state.compiled = null;
    state.art = opts.art ?? null;
    state.rig = null;
    state.seams = null;
    ($('bones-on') as HTMLInputElement).checked = !state.art;
    state.category = state.roles.symbol ? 'symbol' : 'character';
    for (const c of document.querySelectorAll<HTMLButtonElement>('.chip[data-cat]')) c.setAttribute('aria-pressed', String(c.dataset.cat === state.category));
    const notes = (opts.notes ?? guessed?.notes ?? []).filter((n) => !n.startsWith('角色高約')); // size is already in the status line
    setStatus(`${fileName} · ${state.bones.length} 根骨 · ${Object.keys(state.roles).length} 個角色 · size ${state.size}${state.art ? ` · ${state.art.size} 張圖層` : ''}${notes.length ? ' · ' + notes[0] : ''}`);
    viewer.setMode('anim');
    renderList();
    const first = CATALOG.filter((c) => visible(c) && missingRoles(c.id).length === 0).sort((a, b) => b.confidence - a.confidence || CATALOG.indexOf(a) - CATALOG.indexOf(b))[0];
    if (first) select(first.id);
    else renderTune();
    renderQueue();
  } catch (e) {
    setStatus((e as Error).message, true);
  }
}

$('file').addEventListener('change', async (ev) => {
  const input = ev.target as HTMLInputElement;
  const f = input.files?.[0];
  if (!f) return;
  try {
    loadSkeleton(JSON.parse(await f.text()), f.name);
  } catch (e) {
    setStatus(`讀不到 JSON：${(e as Error).message}`, true);
  }
  input.value = '';
});
$('ex-fighter').addEventListener('click', () => window.__EXAMPLES__ && loadSkeleton(structuredClone(window.__EXAMPLES__.fighter), '範例：格鬥角色.json'));
$('ex-symbol').addEventListener('click', () => window.__EXAMPLES__ && loadSkeleton(structuredClone(window.__EXAMPLES__.symbol), '範例：符號.json'));

// ---------------------------------------------------------------- layers → rig
const KNOWN = new Set(PARTS.map((p) => p.name));
const normalize = (file: string) => file.replace(/\.png$/i, '').trim().toLowerCase().replace(/[\s-]+/g, '_');

async function decode(blob: Blob): Promise<LayerArt> {
  const bitmap = await createImageBitmap(blob);
  const c = document.createElement('canvas');
  c.width = bitmap.width;
  c.height = bitmap.height;
  const ctx = c.getContext('2d', { willReadFrequently: true })!;
  ctx.drawImage(bitmap, 0, 0);
  const d = ctx.getImageData(0, 0, c.width, c.height).data;
  const a = new Uint8Array(c.width * c.height);
  for (let i = 0; i < a.length; i++) a[i] = d[i * 4 + 3];
  return { bitmap, alpha: { width: c.width, height: c.height, data: a } };
}

async function loadLayers(entries: { name: string; blob: Blob }[], base: string) {
  setStatus(`讀取 ${entries.length} 張圖層…`);
  const decoded: { name: string; art: LayerArt }[] = [];
  const rejected: { name: string; why: string }[] = [];
  for (const e of entries) {
    try {
      decoded.push({ name: normalize(e.name), art: await decode(e.blob) });
    } catch {
      rejected.push({ name: e.name, why: '讀不到圖片' });
    }
  }
  if (!decoded.length) {
    setStatus('沒有可用的 PNG', true);
    return;
  }
  // the canvas size most layers share
  const sizes = new Map<string, number>();
  for (const d of decoded) {
    const k = `${d.art.alpha.width}x${d.art.alpha.height}`;
    sizes.set(k, (sizes.get(k) ?? 0) + 1);
  }
  const [best] = [...sizes.entries()].sort((a, b) => b[1] - a[1])[0];
  const [w, h] = best.split('x').map(Number);
  const layers = new Map<string, LayerArt>();
  for (const d of decoded) {
    if (d.art.alpha.width !== w || d.art.alpha.height !== h) {
      rejected.push({ name: d.name, why: `尺寸 ${d.art.alpha.width}×${d.art.alpha.height}，和其他圖層的 ${w}×${h} 不同` });
      continue;
    }
    if (!KNOWN.has(d.name) && !d.name.startsWith('hair_front')) {
      rejected.push({ name: d.name, why: '檔名不在模板裡' });
      continue;
    }
    let opaque = 0, transparent = 0;
    for (let i = 0; i < d.art.alpha.data.length; i += 97) d.art.alpha.data[i] > 128 ? opaque++ : transparent++;
    if (!opaque) {
      rejected.push({ name: d.name, why: '整張是透明的' });
      continue;
    }
    if (!transparent) {
      rejected.push({ name: d.name, why: '沒有透明背景' });
      continue;
    }
    layers.set(d.name, d.art);
  }
  const canvas = { width: w, height: h };
  const alphas = new Map([...layers].map(([n, a]) => [n, a.alpha]));
  state.rig = { base, canvas, layers, rejected, joints: layers.size ? guessJoints(alphas) : defaultJoints(canvas) };
  state.skeleton = null;
  state.compiled = null;
  state.draft = null;
  state.art = null;
  setStatus(`${base} · ${layers.size} 張圖層 · 拖動紅點到關節位置，再按「建立骨架」`);
  viewer.setMode('rig');
  renderList();
  renderRigPanel();
}

$('layers').addEventListener('change', async (ev) => {
  const input = ev.target as HTMLInputElement;
  const files = [...(input.files ?? [])];
  input.value = '';
  if (files.length) await loadLayers(files.map((f) => ({ name: f.name, blob: f })), '我的角色');
});

async function loadLayerExample(kind: 'good' | 'bad') {
  const set = window.__LAYERS__?.[kind];
  if (!set) return;
  const entries = await Promise.all(Object.entries(set).map(async ([name, url]) => ({ name: `${name}.png`, blob: await (await fetch(url)).blob() })));
  await loadLayers(entries, kind === 'good' ? '範例：圖層角色' : '範例：有問題的圖層');
}
$('ex-layers').addEventListener('click', () => loadLayerExample('good'));
$('ex-layers-bad').addEventListener('click', () => loadLayerExample('bad'));

function renderRigPanel() {
  const rig = state.rig!;
  const tune = $('tune');
  tune.replaceChildren();
  const list = el('div', { class: 'layers' });
  for (const p of PARTS) {
    const names = p.name === 'hair_front' ? [...rig.layers.keys()].filter((n) => n.startsWith('hair_front')) : rig.layers.has(p.name) ? [p.name] : [];
    const ok = names.length > 0;
    const cls = ok ? 'ok' : p.required ? 'miss' : 'opt';
    list.append(el('div', { class: `row ${cls}` }, el('span', {}, `${ok ? '✓' : p.required ? '✗' : '–'} ${p.name}.png`), el('span', {}, ok ? p.label : p.required ? '缺少' : '選用')));
  }
  for (const r of rig.rejected) list.append(el('div', { class: 'row miss' }, el('span', {}, `✗ ${r.name}`), el('span', {}, r.why)));
  const missing = PARTS.filter((p) => p.required && !rig.layers.has(p.name));
  tune.append(el('div', { class: 'section' }, el('span', { class: 'lbl' }, el('b', {}, `圖層檢查（畫布 ${rig.canvas.width}×${rig.canvas.height}）`)), list,
    ...(missing.length ? [el('div', { class: 'error-box' }, `缺少必要圖層：${missing.map((m) => m.name).join('、')}。可以先建立骨架試看看，缺的部位不會顯示。`)] : [])));

  const guess = el('button', { type: 'button', id: 'rig-guess' }, '自動猜關節');
  guess.addEventListener('click', () => {
    rig.joints = guessJoints(new Map([...rig.layers].map(([n, a]) => [n, a.alpha])));
    toast('已依圖層重新猜關節');
  });
  const reset = el('button', { type: 'button', id: 'rig-reset' }, '還原模板位置');
  reset.addEventListener('click', () => {
    rig.joints = defaultJoints(rig.canvas);
    toast('已還原模板位置');
  });
  const build = el('button', { type: 'button', class: 'primary', id: 'rig-build' }, '建立骨架，開始套動作');
  build.addEventListener('click', buildFromRig);
  tune.append(el('div', { class: 'section' },
    el('span', { class: 'lbl' }, el('b', {}, '關節對齊')),
    el('span', { class: 'hint' }, '把紅點拖到圖上真正的關節位置：肩、肘、腕、腿根、膝、踝、脖子、下巴後方、頭頂、前髮根與髮尾。滑鼠移到點上會顯示名稱；點擠在一起時用滾輪放大。點的位置決定部件繞哪裡轉，偏了手會像脫臼。'),
    el('div', { class: 'actions' }, guess, reset),
    el('div', { class: 'actions' }, build)));
  const export_ = el('button', { type: 'button', id: 'rig-joints' }, '下載關節位置');
  export_.addEventListener('click', () => download(`${rig.base}.joints.json`, JSON.stringify(rig.joints, null, 2)));
  const importInput = el('input', { type: 'file', accept: '.json,application/json', id: 'rig-joints-in', 'aria-label': '載入關節位置' });
  importInput.addEventListener('change', async () => {
    const f = importInput.files?.[0];
    importInput.value = '';
    if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      const bad = JOINTS.filter((x) => !Number.isFinite(j?.[x.id]?.x) || !Number.isFinite(j?.[x.id]?.y)).map((x) => x.label);
      if (bad.length) throw new Error(bad.length > 3 ? `不是關節位置檔（缺少 ${bad.length} 個關節）` : `缺少 ${bad.join('、')}`);
      rig.joints = Object.fromEntries(JOINTS.map((x) => [x.id, { x: j[x.id].x, y: j[x.id].y }])) as Joints;
      toast('已載入關節位置');
    } catch (e) {
      toast(`關節檔不對：${(e as Error).message}`);
    }
  });
  const import_ = el('label', { class: 'file-btn' }, el('button', { type: 'button', tabindex: '-1' }, '載入關節位置'), importInput);
  tune.append(el('div', { class: 'section' }, el('span', { class: 'hint' }, '同一個姿勢、同一張畫布的下一個角色：先載入上次存的關節位置，再微調。'), el('div', { class: 'actions' }, export_, import_)));
}

function buildFromRig() {
  const rig = state.rig;
  if (!rig) return;
  const names = [...rig.layers.keys()];
  const result = buildRig(rig.joints, names, rig.base, rig.canvas);
  const notes = [...result.warnings];
  if (result.missing.length) notes.unshift(`缺少 ${result.missing.join('、')}`);
  loadSkeleton(result.json, `${rig.base}.json`, { roles: result.roles, size: estimateSize(result.json), art: rig.layers, notes });
  if (notes.length) toast(notes[0]);
}

// ---------------------------------------------------------------- pick list
function visible(c: CatalogEntry) {
  return (state.category === 'all' || c.category === state.category) && (state.advanced || c.confidence > 1);
}

function missingRoles(id: string): string[] {
  return presetOf(id).roles.filter((r) => r.required && !roleValue(r.name).length).map((r) => r.name);
}

function roleValue(name: string): string[] {
  const v = state.roles[name];
  if (v === undefined) return [];
  return (Array.isArray(v) ? v : [v]).filter((b) => state.bones.includes(b));
}

function renderList() {
  const list = $('list');
  list.replaceChildren();
  if (state.rig) {
    list.append(el('p', { class: 'empty' }, '先在中間把關節對好，按右邊「建立骨架」後就能挑動作'));
    return;
  }
  const items = CATALOG.filter(visible).sort((a, b) => b.confidence - a.confidence || CATALOG.indexOf(a) - CATALOG.indexOf(b));
  for (const c of items) {
    const p = presetOf(c.id);
    const miss = state.skeleton ? missingRoles(c.id) : [];
    const b = el('button', { class: 'item', role: 'option', 'aria-selected': String(state.draft?.presetId === c.id), 'data-id': c.id, type: 'button' },
      el('span', { class: 'top' }, el('span', { class: 'stars', 'aria-label': `把握度 ${c.confidence} 顆星` }, stars(c.confidence)), el('span', { class: 'name' }, p.title.replace(/（.*?）/g, '')), el('span', { class: 'kind' }, p.kind === 'loop' ? '循環' : '一次')),
      el('span', { class: 'basis' }, c.basis),
    );
    if (miss.length) b.append(el('span', { class: 'missing' }, `需要指定：${miss.join('、')}（可在右邊選骨骼）`));
    b.addEventListener('click', () => select(c.id));
    list.append(b);
  }
}

for (const chip of document.querySelectorAll<HTMLButtonElement>('.chip[data-cat]')) {
  chip.addEventListener('click', () => {
    state.category = chip.dataset.cat as typeof state.category;
    for (const c of document.querySelectorAll<HTMLButtonElement>('.chip[data-cat]')) c.setAttribute('aria-pressed', String(c === chip));
    renderList();
  });
}
$('adv').addEventListener('click', () => {
  state.advanced = !state.advanced;
  $('adv').setAttribute('aria-pressed', String(state.advanced));
  renderList();
});

function uniqueName(base: string): string {
  const taken = new Set(state.queue.map((a) => a.name));
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) if (!taken.has(`${base}_${i}`)) return `${base}_${i}`;
}

function select(id: string) {
  if (!state.skeleton) {
    toast(state.rig ? '先按右邊「建立骨架」' : '先開啟骨架、載入圖層，或試試範例');
    return;
  }
  const remembered = state.drafts.get(id);
  const p = presetOf(id);
  state.draft = remembered ?? {
    presetId: id,
    name: uniqueName(catalogOf(id).name),
    params: Object.fromEntries(p.params.map((x) => [x.name, x.default])),
    timeScale: COMMON_PARAMS.timeScale.default,
    amplitude: COMMON_PARAMS.amplitude.default,
    facing: state.facing,
  };
  state.drafts.set(id, state.draft);
  renderList();
  renderTune();
  scheduleCompile(0);
}

// ---------------------------------------------------------------- fine-tune panel
type RangeUi = Extract<ParamUi, { kind: 'range' }>;

function rangeField(label: string, ui: RangeUi, value: number, fallback: number, hint: string, onChange: (v: number) => void, id: string) {
  const out = el('span', { class: 'val' }, fmt(value, ui));
  const input = el('input', { type: 'range', min: String(ui.min), max: String(ui.max), step: String(ui.step), value: String(value), id, 'aria-label': label });
  const reset = el('button', { type: 'button', class: 'reset', title: `恢復預設 ${fallback}`, 'aria-label': `${label} 恢復預設` }, '↺');
  input.addEventListener('input', () => {
    const v = +input.value;
    out.textContent = fmt(v, ui);
    onChange(v);
  });
  reset.addEventListener('click', () => {
    input.value = String(fallback);
    out.textContent = fmt(fallback, ui);
    onChange(fallback);
  });
  return el('div', { class: 'field' }, el('span', { class: 'lbl' }, el('span', {}, label), out), el('span', { class: 'row' }, input, reset), ...(hint ? [el('span', { class: 'hint' }, hint)] : []));
}

function fmt(v: number, ui: RangeUi) {
  const digits = ui.step >= 1 ? 0 : ui.step >= 0.1 ? 1 : ui.step >= 0.01 ? 2 : 3;
  return `${v.toFixed(digits)}${ui.unit ?? ''}`;
}

function renderTune() {
  const tune = $('tune');
  tune.replaceChildren();
  const d = state.draft;
  if (!d) {
    tune.append(el('p', { class: 'empty' }, state.skeleton ? '左邊挑一個動作' : '先開啟骨架、載入圖層，或試試範例'));
    return;
  }
  const p = presetOf(d.presetId);
  const c = catalogOf(d.presetId);

  const name = el('input', { type: 'text', value: d.name, id: 'anim-name', 'aria-label': '動畫名稱' });
  name.addEventListener('change', () => {
    d.name = name.value.trim() || c.name;
    name.value = d.name;
  });
  tune.append(el('div', { class: 'section' }, el('div', { class: 'field' }, el('span', { class: 'lbl' }, el('span', {}, '動畫名稱')), name)));

  const roles = el('div', { class: 'section' }, el('span', { class: 'lbl' }, el('b', {}, '骨骼對應')));
  for (const r of p.roles) {
    const current = roleValue(r.name);
    if (r.list) {
      const input = el('input', { type: 'text', value: current.join(', '), id: `role-${r.name}`, placeholder: '骨骼名稱，用逗號分隔，由根到尾', 'aria-label': r.desc });
      input.addEventListener('change', () => {
        const names = input.value.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
        const bad = names.filter((n) => !state.bones.includes(n));
        input.classList.toggle('bad', bad.length > 0);
        if (bad.length) {
          toast(`找不到骨骼：${bad.join('、')}`);
          return;
        }
        if (names.length) state.roles[r.name] = names;
        else delete state.roles[r.name];
        renderList();
        scheduleCompile();
      });
      roles.append(el('div', { class: 'field' }, el('span', { class: 'lbl' }, el('span', {}, `${r.name}${r.required ? ' *' : ''}`)), input, el('span', { class: 'hint' }, r.desc)));
    } else {
      const sel = el('select', { id: `role-${r.name}`, 'aria-label': r.desc });
      sel.append(el('option', { value: '' }, r.required ? '— 請選擇 —' : '（不使用）'));
      for (const b of state.bones) sel.append(el('option', { value: b }, b));
      sel.value = current[0] ?? '';
      sel.addEventListener('change', () => {
        if (sel.value) state.roles[r.name] = sel.value;
        else delete state.roles[r.name];
        renderList();
        scheduleCompile();
      });
      roles.append(el('div', { class: 'field' }, el('span', { class: 'lbl' }, el('span', {}, `${r.name}${r.required ? ' *' : ''}`)), sel, el('span', { class: 'hint' }, r.desc)));
    }
  }
  tune.append(roles);

  const params = el('div', { class: 'section' }, el('span', { class: 'lbl' }, el('b', {}, '參數')));
  for (const x of p.params) {
    const ui = c.params[x.name];
    if (ui.kind === 'range') {
      params.append(rangeField(x.name, ui, d.params[x.name] as number, x.default as number, x.desc, (v) => { d.params[x.name] = v; scheduleCompile(); }, `param-${x.name}`));
    } else {
      const sel = el('select', { id: `param-${x.name}`, 'aria-label': x.desc });
      for (const o of ui.options) sel.append(el('option', { value: o.value }, o.label));
      sel.value = String(d.params[x.name]);
      sel.addEventListener('change', () => { d.params[x.name] = sel.value; scheduleCompile(); });
      params.append(el('div', { class: 'field' }, el('span', { class: 'lbl' }, el('span', {}, x.name)), sel, el('span', { class: 'hint' }, x.desc)));
    }
  }
  tune.append(params);

  const common = el('div', { class: 'section' }, el('span', { class: 'lbl' }, el('b', {}, '通用')));
  common.append(rangeField(COMMON_PARAMS.timeScale.label, COMMON_PARAMS.timeScale.ui as RangeUi, d.timeScale, 1, '整體速度；事件時間會跟著移動', (v) => { d.timeScale = v; scheduleCompile(); }, 'common-speed'));
  common.append(rangeField(COMMON_PARAMS.amplitude.label, COMMON_PARAMS.amplitude.ui as RangeUi, d.amplitude, 1, '整體動作幅度', (v) => { d.amplitude = v; scheduleCompile(); }, 'common-amp'));
  const seg = el('span', { class: 'seg', role: 'group', 'aria-label': '角色朝向' });
  for (const [v, label] of [[1, '面向右'], [-1, '面向左']] as const) {
    const b = el('button', { type: 'button', 'aria-pressed': String(d.facing === v) }, label);
    b.addEventListener('click', () => {
      d.facing = v;
      for (const s of seg.children) s.setAttribute('aria-pressed', String(s === b));
      scheduleCompile();
    });
    seg.append(b);
  }
  common.append(el('div', { class: 'field' }, el('span', { class: 'lbl' }, el('span', {}, '朝向')), seg, ...(state.art ? [el('span', { class: 'hint' }, '用圖層建立的角色是面向右畫的；選面向左會讓動作往左，但圖不會翻面')] : [])));
  tune.append(common);

  tune.append(el('div', { class: 'section', id: 'result' }, el('span', { class: 'counts' }, '計算中…')));
  const add = el('button', { type: 'button', class: 'primary', id: 'add' }, '加入匯出清單');
  add.addEventListener('click', addToQueue);
  tune.append(el('div', { class: 'actions' }, add));
}

function renderResult() {
  const box = document.getElementById('result');
  if (!box) return;
  box.replaceChildren();
  if (state.error) {
    box.append(el('div', { class: 'error-box' }, state.error));
    return;
  }
  const r = state.compiled?.report;
  if (!r) return;
  const e = r.issues.filter((i) => i.severity === 'error').length;
  const w = r.issues.filter((i) => i.severity === 'warn').length;
  const cls = r.score >= 90 ? 'good' : r.score >= 60 ? 'mid' : 'low';
  box.append(
    el('div', { class: 'score' }, el('span', { class: `num ${cls}`, id: 'score' }, String(r.score)), el('span', { class: 'counts' }, `流暢度分數 · 錯誤 ${e} · 警告 ${w} · 提示 ${r.issues.length - e - w}`, el('br'), `${r.keyCount} 個關鍵影格 · ${r.duration.toFixed(2)} s${r.loop ? ' · 循環' : ''}`)),
  );
  if (r.issues.length) {
    const ul = el('ul', { class: 'issues' });
    for (const i of r.issues.slice(0, 6)) ul.append(el('li', { class: i.severity }, i.message));
    box.append(ul);
  }
  if (state.art) {
    box.append(el('span', { class: 'lbl' }, el('b', {}, '接縫檢查（用你的圖）')));
    if (state.seamBusy || !state.seams) box.append(el('span', { class: 'counts', id: 'seam-status' }, '檢查中…'));
    else if (!state.seams.length) box.append(el('span', { class: 'seam-ok', id: 'seam-status' }, '✓ 每一幀的關節都沒有露底'));
    else {
      const ul = el('ul', { class: 'seams', id: 'seam-list' });
      for (const s of state.seams) {
        const go = el('button', { type: 'button' }, s.rest ? '看這裡' : '看這一幀');
        go.addEventListener('click', () => viewer.seek(s.time));
        ul.append(el('li', {}, el('span', {}, s.rest ? `${s.joint}：靜止時就有缺口（約 ${Math.round(s.area)} px²）` : `${s.joint}：第 ${s.time.toFixed(2)} s 露底 ${Math.round(s.area)} px²`), go));
      }
      box.append(ul, el('span', { class: 'hint' }, '在對應的圖層上，把被遮住的部分補畫、讓兩個部件在關節處多重疊一些，再重新載入。紅圈標出位置。'));
    }
  }
}

// ---------------------------------------------------------------- compile
let timer = 0;
let seamToken = 0;
function scheduleCompile(delay = 140) {
  clearTimeout(timer);
  timer = window.setTimeout(compileDraft, delay);
}

function draftAnimation(d: Draft): RecipeAnimation {
  return { name: d.name, preset: d.presetId, params: { ...d.params }, timeScale: d.timeScale, amplitude: d.amplitude, facing: d.facing };
}

function recipeFor(animations: RecipeAnimation[]): Recipe {
  return { fps: 30, facing: state.facing, size: state.size, roles: structuredClone(state.roles), animations };
}

function compileDraft() {
  const d = state.draft;
  if (!d || !state.skeleton) return;
  try {
    const anim = draftAnimation(d);
    const { json, summary } = compileRecipe(state.skeleton, recipeFor([anim]));
    const loop = summary[0].loop;
    const [report] = checkSkeleton(json, { only: [anim.name], loops: loop ? [anim.name] : [] });
    state.compiled = { json, data: parseSkeleton(json), report, loop, duration: summary[0].duration };
    state.error = '';
    viewer.load(state.compiled.data, json, anim.name, loop);
    $('stage-title').textContent = presetOf(d.presetId).title;
    $('stage-sub').textContent = `${anim.name} · ${summary[0].duration.toFixed(2)} s · ${loop ? '循環' : '一次'}${summary[0].events.length ? ' · 事件 ' + summary[0].events.join(' ') : ''}`;
    if (state.art) {
      // the seam check renders every frame with the real art: run it after the preview is up
      state.seamBusy = true;
      state.seams = null;
      const token = ++seamToken;
      const art = state.art;
      setTimeout(() => {
        if (token !== seamToken) return;
        try {
          const alphas = new Map([...art].map(([n, a]) => [n, a.alpha]));
          state.seams = checkSeams(json, alphas, { animations: [anim.name] })[0]?.issues ?? [];
        } catch (e) {
          state.seams = [];
          toast(`接縫檢查失敗：${(e as Error).message}`);
        }
        state.seamBusy = false;
        renderResult();
      }, 30);
    }
  } catch (e) {
    state.error = (e as Error).message;
    state.compiled = null;
  }
  renderResult();
}

// ---------------------------------------------------------------- export queue
function addToQueue() {
  const d = state.draft;
  if (!d || !state.compiled) {
    toast(state.error ? '先修正錯誤' : '還在計算');
    return;
  }
  const anim = draftAnimation(d);
  const i = state.queue.findIndex((a) => a.name === anim.name);
  if (i >= 0) state.queue[i] = anim;
  else state.queue.push(anim);
  toast(`已加入：${anim.name}`);
  state.drafts.delete(d.presetId);
  renderQueue();
}

function renderQueue() {
  const q = $('queue');
  q.replaceChildren();
  if (!state.queue.length) q.append(el('span', { class: 'counts' }, '還沒有加入任何動作'));
  for (const a of state.queue) {
    const rm = el('button', { type: 'button', 'aria-label': `移除 ${a.name}` }, '×');
    rm.addEventListener('click', () => {
      state.queue = state.queue.filter((x) => x !== a);
      renderQueue();
    });
    const t = presetOf(a.preset!).title.replace(/（.*?）/g, '');
    q.append(el('div', { class: 'q' }, el('span', { class: 'n' }, a.name), el('span', { class: 'p' }, `${t} · 速度 ${a.timeScale}× · 幅度 ${a.amplitude}× · ${a.facing === 1 ? '面右' : '面左'}`), rm));
  }
  if (state.art && state.queue.length) q.append(el('span', { class: 'hint' }, '這個角色是用圖層建立的：把你的圖層 PNG 放在動畫 JSON 旁邊的 images 資料夾，Spine 4.3 Import Data 才找得到圖。'));
  for (const id of ['dl-json', 'dl-recipe', 'copy-json']) ($(id) as HTMLButtonElement).disabled = !state.queue.length;
}

function exportAnimationJson(): string {
  const { json } = compileRecipe(state.skeleton!, recipeFor(state.queue));
  return JSON.stringify(json, null, 1);
}
function exportRecipe(): string {
  return JSON.stringify(recipeFor(state.queue), null, 2);
}
function download(name: string, text: string) {
  const a = el('a', { href: URL.createObjectURL(new Blob([text], { type: 'application/json' })), download: name });
  document.body.append(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 1000);
}
const base = () => state.fileName.replace(/\.json$/i, '');
$('dl-json').addEventListener('click', () => {
  try {
    download(`${base()}.motion.json`, exportAnimationJson());
    toast('已下載動畫 JSON');
  } catch (e) {
    toast((e as Error).message);
  }
});
$('dl-recipe').addEventListener('click', () => {
  download(`${base()}.recipe.json`, exportRecipe());
  toast('已下載配方');
});
$('copy-json').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(exportAnimationJson());
    toast('已複製動畫 JSON');
  } catch {
    toast('瀏覽器不允許複製，請改用下載');
  }
});

// ---------------------------------------------------------------- viewer
const viewer = (() => {
  const view = $<HTMLCanvasElement>('view');
  const graph = $<HTMLCanvasElement>('graph');
  const vctx = view.getContext('2d')!;
  const gctx = graph.getContext('2d')!;
  const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  let mode: 'anim' | 'rig' = 'anim';
  let data: SkeletonData | null = null;
  let json: SpineJson | null = null;
  let sk: Skeleton | null = null;
  let anim = '';
  let state2: AnimationState | null = null;
  let loop = true;
  let duration = 1;
  let time = 0;
  let playing = true;
  let bounds = { x0: -100, y0: -100, x1: 100, y1: 100 };
  let trails = new Map<string, [number, number][]>();
  let curve = { y: [] as number[], rot: [] as number[] };
  let flash = { text: '', until: 0 };
  // screen mapping of the last frame (world → screen), used by the joint editor too
  let map = { s: 1, ox: 0, oy: 0, h: 1, x0: 0, y0: 0 };
  let drag: JointId | null = null;
  let hover: JointId | null = null;

  const tip = (b: Bone): [number, number] => {
    const p = b.getAppliedPose();
    return [p.worldX + p.a * b.data.length, p.worldY + p.c * b.data.length];
  };
  function pose(t: number, physics: Physics) {
    if (!sk || !state2) return;
    sk.setupPose();
    state2.setAnimation(0, anim, loop);
    state2.update(t);
    state2.apply(sk);
    sk.updateWorldTransform(physics);
  }
  function measure() {
    if (!sk) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    trails = new Map();
    curve = { y: [], rot: [] };
    const watch = ($('watch') as HTMLSelectElement).value;
    for (let i = 0; i <= 240; i++) {
      pose((duration * i) / 240, Physics.pose);
      for (const b of sk.bones) {
        const p = b.getAppliedPose();
        const [tx, ty] = tip(b);
        x0 = Math.min(x0, p.worldX, tx);
        y0 = Math.min(y0, p.worldY, ty);
        x1 = Math.max(x1, p.worldX, tx);
        y1 = Math.max(y1, p.worldY, ty);
        if (!trails.has(b.data.name)) trails.set(b.data.name, []);
        trails.get(b.data.name)!.push([tx, ty]);
        if (b.data.name === watch) {
          curve.y.push(p.worldY);
          curve.rot.push(p.getWorldRotationX());
        }
      }
    }
    if (state.art && json?.skeleton) {
      // the art is larger than the bones: fit the drawing canvas too
      const k = json.skeleton;
      x0 = Math.min(x0, k.x); y0 = Math.min(y0, k.y);
      x1 = Math.max(x1, k.x + k.width); y1 = Math.max(y1, k.y + k.height);
    }
    const pad = Math.max(x1 - x0, y1 - y0) * (state.art ? 0.04 : 0.12) + 10;
    bounds = { x0: x0 - pad, y0: y0 - pad, x1: x1 + pad, y1: y1 + pad };
  }
  function fit(c: HTMLCanvasElement): [number, number, number] {
    const r = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(r.width * dpr));
    const h = Math.max(1, Math.round(r.height * dpr));
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
    return [w, h, dpr];
  }
  function drawArt() {
    if (!sk || !json || !state.art) return;
    const skin = (json.skins ?? []).find((s: SpineJson) => s.name === 'default');
    const { s, ox, oy, h, x0, y0 } = map;
    for (const slot of json.slots ?? []) {
      const att = skin?.attachments?.[slot.name]?.[slot.attachment];
      const art = state.art.get(slot.attachment);
      if (!att || !art) continue;
      const p = sk.findBone(slot.bone)!.getAppliedPose();
      const r = ((att.rotation ?? 0) * Math.PI) / 180;
      const cr = Math.cos(r), sr = Math.sin(r);
      const sx = att.scaleX ?? 1, sy = att.scaleY ?? 1;
      const m00 = (p.a * cr + p.b * sr) * sx, m01 = (-p.a * sr + p.b * cr) * sy;
      const m10 = (p.c * cr + p.d * sr) * sx, m11 = (-p.c * sr + p.d * cr) * sy;
      const qx = p.worldX + p.a * (att.x ?? 0) + p.b * (att.y ?? 0);
      const qy = p.worldY + p.c * (att.x ?? 0) + p.d * (att.y ?? 0);
      const W = att.width, H = att.height;
      const kx = W / art.bitmap.width, ky = H / art.bitmap.height;
      // image px → world: Q + M·(kx·ix − W/2, H/2 − ky·iy); world → screen: (ox + (x − x0)s, h − oy − (y − y0)s)
      const ex = qx - m00 * (W / 2) + m01 * (H / 2);
      const ey = qy - m10 * (W / 2) + m11 * (H / 2);
      vctx.setTransform(s * m00 * kx, -s * m10 * kx, -s * m01 * ky, s * m11 * ky, ox + (ex - x0) * s, h - oy - (ey - y0) * s);
      vctx.drawImage(art.bitmap, 0, 0);
    }
    vctx.setTransform(1, 0, 0, 1, 0, 0);
  }
  function drawBones(toX: (x: number) => number, toY: (y: number) => number, alpha: number, override?: string) {
    if (!sk) return;
    vctx.globalAlpha = alpha;
    for (const b of sk.bones) {
      const p = b.getAppliedPose();
      const [tx, ty] = tip(b);
      const c = b.data.color;
      const isDefault = c.a === 0 || (c.r > 0.6 && c.g > 0.6 && c.b > 0.6);
      const color = override ?? (isDefault ? css('--bone') : `rgb(${Math.round(c.r * 255)},${Math.round(c.g * 255)},${Math.round(c.b * 255)})`);
      vctx.strokeStyle = color;
      vctx.fillStyle = color;
      if (b.data.length > 0) {
        vctx.lineWidth = 3;
        vctx.beginPath();
        vctx.moveTo(toX(p.worldX), toY(p.worldY));
        vctx.lineTo(toX(tx), toY(ty));
        vctx.stroke();
        vctx.beginPath();
        vctx.arc(toX(p.worldX), toY(p.worldY), 3.5, 0, Math.PI * 2);
        vctx.fill();
      } else {
        const x = toX(p.worldX), y = toY(p.worldY);
        vctx.lineWidth = 2;
        vctx.beginPath();
        vctx.moveTo(x - 7, y);
        vctx.lineTo(x + 7, y);
        vctx.moveTo(x, y - 7);
        vctx.lineTo(x, y + 7);
        vctx.stroke();
      }
    }
    vctx.globalAlpha = 1;
  }

  // ------------------------------------------------ joint editor
  // zoom (1 = whole canvas fits) and the canvas point at the view's centre, as fractions of the canvas
  const rigView = { zoom: 1, cx: 0.5, cy: 0.5 };
  let pan: { x: number; y: number } | null = null;
  function rigMap(w: number, h: number) {
    const rig = state.rig!;
    const pad = 16;
    const s = Math.min((w - 2 * pad) / rig.canvas.width, (h - 2 * pad) / rig.canvas.height) * rigView.zoom;
    return { s, ox: w / 2 - rigView.cx * rig.canvas.width * s, oy: h / 2 - rigView.cy * rig.canvas.height * s };
  }
  const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
  function renderRig() {
    const rig = state.rig!;
    const [w, h, dpr] = fit(view);
    vctx.setTransform(1, 0, 0, 1, 0, 0);
    vctx.clearRect(0, 0, w, h);
    const { s, ox, oy } = rigMap(w, h);
    vctx.fillStyle = '#20242c';
    vctx.fillRect(ox, oy, rig.canvas.width * s, rig.canvas.height * s);
    const order = PARTS.flatMap((p) => (p.name === 'hair_front' ? [...rig.layers.keys()].filter((n) => n.startsWith('hair_front')).sort() : [p.name]));
    for (const n of order) {
      const a = rig.layers.get(n);
      if (a) vctx.drawImage(a.bitmap, ox, oy, rig.canvas.width * s, rig.canvas.height * s);
    }
    const P = (id: JointId) => [ox + rig.joints[id].x * s, oy + rig.joints[id].y * s] as const;
    vctx.strokeStyle = css('--accent');
    vctx.lineWidth = 2 * dpr;
    for (const [a, b] of JOINT_LINKS) {
      if ((a === 'hairRoot' || b === 'hairTip') && ![...rig.layers.keys()].some((n) => n.startsWith('hair_front'))) continue;
      const [x1, y1] = P(a), [x2, y2] = P(b);
      vctx.beginPath();
      vctx.moveTo(x1, y1);
      vctx.lineTo(x2, y2);
      vctx.stroke();
    }
    JOINTS.forEach((j, i) => {
      const [x, y] = P(j.id);
      vctx.beginPath();
      vctx.arc(x, y, (j.id === hover || j.id === drag ? 11 : 8) * dpr, 0, Math.PI * 2);
      vctx.fillStyle = '#ffffff';
      vctx.fill();
      vctx.lineWidth = 3 * dpr;
      vctx.strokeStyle = css('--accent');
      vctx.stroke();
      vctx.fillStyle = '#111';
      vctx.font = `${10 * dpr}px ${css('--f-num')}`;
      const label = String(i + 1);
      vctx.fillText(label, x - vctx.measureText(label).width / 2, y + 3.5 * dpr);
    });
    const show = drag ?? hover;
    if (show) {
      const j = JOINTS.find((x) => x.id === show)!;
      const [x, y] = P(show);
      vctx.font = `${14 * dpr}px ${css('--f-ui')}`;
      const text = `${JOINTS.indexOf(j) + 1} ${j.label}`;
      const tw = vctx.measureText(text).width;
      vctx.fillStyle = 'rgba(0,0,0,.75)';
      vctx.fillRect(x + 14 * dpr, y - 22 * dpr, tw + 12 * dpr, 24 * dpr);
      vctx.fillStyle = '#fff';
      vctx.fillText(text, x + 20 * dpr, y - 5 * dpr);
    }
    $('time').textContent = '關節對齊';
  }
  function jointAt(ev: PointerEvent): JointId | null {
    const rig = state.rig;
    if (!rig) return null;
    const r = view.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const px = (ev.clientX - r.left) * dpr, py = (ev.clientY - r.top) * dpr;
    const { s, ox, oy } = rigMap(view.width, view.height);
    let best: JointId | null = null;
    let bd = 16 * dpr;
    for (const j of JOINTS) {
      const d = Math.hypot(ox + rig.joints[j.id].x * s - px, oy + rig.joints[j.id].y * s - py);
      if (d < bd) {
        bd = d;
        best = j.id;
      }
    }
    return best;
  }
  view.addEventListener('pointerdown', (ev) => {
    if (mode !== 'rig') return;
    drag = jointAt(ev);
    // grabbing empty space pans (only worth it once zoomed in)
    if (!drag && rigView.zoom > 1) pan = { x: ev.clientX, y: ev.clientY };
    if (drag || pan) view.setPointerCapture(ev.pointerId);
  });
  view.addEventListener('pointermove', (ev) => {
    if (mode !== 'rig' || !state.rig) return;
    if (pan) {
      const dpr = window.devicePixelRatio || 1;
      const { s } = rigMap(view.width, view.height);
      rigView.cx = clamp01(rigView.cx - ((ev.clientX - pan.x) * dpr) / (state.rig.canvas.width * s));
      rigView.cy = clamp01(rigView.cy - ((ev.clientY - pan.y) * dpr) / (state.rig.canvas.height * s));
      pan = { x: ev.clientX, y: ev.clientY };
      return;
    }
    if (!drag) {
      hover = jointAt(ev);
      return;
    }
    const r = view.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const { s, ox, oy } = rigMap(view.width, view.height);
    const c = state.rig.canvas;
    state.rig.joints[drag] = {
      x: Math.round(Math.min(c.width, Math.max(0, ((ev.clientX - r.left) * dpr - ox) / s))),
      y: Math.round(Math.min(c.height, Math.max(0, ((ev.clientY - r.top) * dpr - oy) / s))),
    };
  });
  view.addEventListener('pointerup', () => (drag = pan = null));
  view.addEventListener('pointercancel', () => (drag = pan = null));
  // the wheel zooms about the cursor, so crowded joints (hips, shoulders) can be pulled apart precisely
  view.addEventListener('wheel', (ev) => {
    if (mode !== 'rig' || !state.rig) return;
    ev.preventDefault();
    const r = view.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const px = (ev.clientX - r.left) * dpr, py = (ev.clientY - r.top) * dpr;
    const before = rigMap(view.width, view.height);
    const cx = (px - before.ox) / before.s, cy = (py - before.oy) / before.s; // canvas point under the cursor
    rigView.zoom = Math.min(6, Math.max(1, rigView.zoom * Math.exp(-ev.deltaY * 0.0015)));
    const s = rigMap(view.width, view.height).s;
    const c = state.rig.canvas;
    rigView.cx = clamp01((view.width / 2 - px + cx * s) / (c.width * s));
    rigView.cy = clamp01((view.height / 2 - py + cy * s) / (c.height * s));
  }, { passive: false });
  view.addEventListener('dblclick', () => {
    if (mode !== 'rig') return;
    Object.assign(rigView, { zoom: 1, cx: 0.5, cy: 0.5 });
  });

  // ------------------------------------------------ animation view
  function render() {
    if (mode === 'rig' && state.rig) {
      renderRig();
      return;
    }
    const [w, h, dpr] = fit(view);
    vctx.setTransform(1, 0, 0, 1, 0, 0);
    vctx.clearRect(0, 0, w, h);
    if (!sk) return;
    const s = Math.min(w / (bounds.x1 - bounds.x0), h / (bounds.y1 - bounds.y0));
    const ox = (w - (bounds.x1 - bounds.x0) * s) / 2;
    const oy = (h - (bounds.y1 - bounds.y0) * s) / 2;
    map = { s, ox, oy, h, x0: bounds.x0, y0: bounds.y0 };
    const toX = (x: number) => ox + (x - bounds.x0) * s;
    const toY = (y: number) => h - oy - (y - bounds.y0) * s;
    const watch = ($('watch') as HTMLSelectElement).value;
    const showBones = ($('bones-on') as HTMLInputElement).checked;
    if (($('trail') as HTMLInputElement).checked) {
      for (const [name, pts] of trails) {
        if (state.art && name !== watch) continue;
        vctx.strokeStyle = name === watch ? css('--accent') : css('--trail');
        vctx.globalAlpha = name === watch ? 0.9 : 0.22;
        vctx.lineWidth = name === watch ? 2 : 1;
        vctx.beginPath();
        pts.forEach(([x, y], i) => (i ? vctx.lineTo(toX(x), toY(y)) : vctx.moveTo(toX(x), toY(y))));
        vctx.stroke();
      }
      vctx.globalAlpha = 1;
    }
    if (($('onion') as HTMLInputElement).checked) {
      for (let k = 6; k >= 1; k--) {
        pose(Math.max(0, time - k / 30), Physics.pose);
        drawBones(toX, toY, 0.07 + 0.03 * (6 - k), '#ffffff');
      }
    }
    pose(time, Physics.update);
    if (state.art) drawArt();
    if (showBones || !state.art) drawBones(toX, toY, state.art ? 0.85 : 1);
    // seam markers: red rings on joints with gaps
    if (state.seams?.length) {
      vctx.font = `${13 * dpr}px ${css('--f-ui')}`;
      state.seams.forEach((sIss, i) => {
        const b = sk!.findBone(sIss.bone);
        if (!b) return;
        const p = b.getAppliedPose();
        const x = toX(p.worldX), y = toY(p.worldY);
        vctx.strokeStyle = css('--err');
        vctx.lineWidth = 3 * dpr;
        vctx.beginPath();
        vctx.arc(x, y, 56 * s, 0, Math.PI * 2);
        vctx.stroke();
        // labels stack to the side so neighbouring joints stay readable
        const lx = x + 64 * s, ly = y - 56 * s + i * 20 * dpr;
        const tw = vctx.measureText(sIss.joint).width;
        vctx.fillStyle = 'rgba(0,0,0,.72)';
        vctx.fillRect(lx - 4 * dpr, ly - 13 * dpr, tw + 8 * dpr, 18 * dpr);
        vctx.fillStyle = css('--err');
        vctx.fillText(sIss.joint, lx, ly);
      });
    }
    if (flash.text && performance.now() < flash.until) {
      vctx.fillStyle = css('--accent');
      vctx.font = `${16 * dpr}px ${css('--f-ui')}`;
      vctx.fillText(`事件：${flash.text}`, 14 * dpr, 26 * dpr);
    }
    const [gw, gh] = fit(graph);
    gctx.clearRect(0, 0, gw, gh);
    const plot = (arr: number[], color: string, label: string, row: number) => {
      if (arr.length < 2) return;
      const lo = Math.min(...arr), hi = Math.max(...arr), span = Math.max(hi - lo, 1e-6);
      gctx.strokeStyle = color;
      gctx.lineWidth = 2 * dpr;
      gctx.beginPath();
      arr.forEach((v, i) => {
        const x = (i / (arr.length - 1)) * gw;
        const y = gh * 0.12 + (1 - (v - lo) / span) * gh * 0.78;
        if (i) gctx.lineTo(x, y);
        else gctx.moveTo(x, y);
      });
      gctx.stroke();
      gctx.fillStyle = color;
      gctx.font = `${12 * dpr}px ${css('--f-ui')}`;
      gctx.fillText(`${label}  ${lo.toFixed(1)} … ${hi.toFixed(1)}`, 8 * dpr, (15 + row * 15) * dpr);
    };
    plot(curve.y, css('--trail'), '高度 y', 0);
    plot(curve.rot, css('--gold'), '角度 °', 1);
    const t = duration ? time % (duration + 1e-9) : 0;
    const px = (t / (duration || 1)) * gw;
    gctx.strokeStyle = css('--accent');
    gctx.lineWidth = dpr;
    gctx.beginPath();
    gctx.moveTo(px, 0);
    gctx.lineTo(px, gh);
    gctx.stroke();
    $('time').textContent = `${t.toFixed(3)} s · 第 ${Math.round(t * 30)} 幀 / ${Math.round(duration * 30)}`;
    ($('scrub') as HTMLInputElement).value = String(Math.round((t / (duration || 1)) * 1000));
  }
  let last = performance.now();
  function tick(now: number) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (mode === 'anim' && playing && data && sk) {
      const before = time;
      time += dt;
      if (!loop && time > duration) time = 0; // one-shots replay so tweaks are always visible
      const a = data.findAnimation(anim);
      if (a && duration > 0) {
        for (const tl of a.timelines) {
          if (!(tl instanceof EventTimeline)) continue;
          const b0 = before % duration, b1 = time % duration;
          for (const ev of tl.events) {
            const hit = b0 <= b1 ? ev.time > b0 && ev.time <= b1 : ev.time > b0 || ev.time <= b1;
            if (hit) flash = { text: ev.data.name, until: performance.now() + 600 };
          }
        }
      }
      sk.update(dt);
    }
    render();
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  $('play').addEventListener('click', () => {
    playing = !playing;
    $('play').textContent = playing ? '暫停' : '播放';
  });
  $('scrub').addEventListener('input', () => {
    playing = false;
    $('play').textContent = '播放';
    time = (+($('scrub') as HTMLInputElement).value / 1000) * duration;
  });
  $('watch').addEventListener('change', measure);

  return {
    setMode(m: 'anim' | 'rig') {
      mode = m;
      Object.assign(rigView, { zoom: 1, cx: 0.5, cy: 0.5 });
      view.classList.toggle('editing', m === 'rig');
      if (m === 'rig') {
        sk = null;
        data = null;
        $('stage-title').textContent = '關節對齊';
        $('stage-sub').textContent = '拖動紅點；滾輪放大、拖空白處平移、雙擊還原；完成後按右邊「建立骨架」';
        const gw = graph.getContext('2d')!;
        gw.clearRect(0, 0, graph.width, graph.height);
      }
    },
    load(d: SkeletonData, j: SpineJson, name: string, isLoop: boolean) {
      const sameSkeleton = data && data.bones.length === d.bones.length;
      data = d;
      json = j;
      sk = new Skeleton(d);
      state2 = new AnimationState(new AnimationStateData(d));
      anim = name;
      loop = isLoop;
      duration = d.findAnimation(name)?.duration ?? 1;
      if (time > duration) time = 0;
      const watch = $('watch') as HTMLSelectElement;
      if (!sameSkeleton || !watch.options.length) {
        watch.replaceChildren(...d.bones.map((b) => el('option', { value: b.name }, b.name)));
        const head = d.bones.find((b) => /head|symbol|title/i.test(b.name)) ?? d.bones[Math.min(1, d.bones.length - 1)];
        watch.value = head.name;
      }
      measure();
    },
    seek(t: number) {
      time = t;
      playing = false;
      $('play').textContent = '播放';
    },
    get time() {
      return time;
    },
  };
})();

renderList();

// hooks for automated checks
window.__studio = {
  state,
  select,
  setParam(name: string, value: unknown) {
    if (!state.draft) return;
    state.draft.params[name] = value;
    compileDraft();
  },
  setCommon(name: 'timeScale' | 'amplitude', value: number) {
    if (!state.draft) return;
    state.draft[name] = value;
    compileDraft();
  },
  addToQueue,
  exportAnimationJson,
  exportRecipe,
  loadExample: (k: 'fighter' | 'symbol') => window.__EXAMPLES__ && loadSkeleton(structuredClone(window.__EXAMPLES__[k]), `範例：${k}.json`),
  loadLayerExample,
  buildFromRig,
  canvasDefault: CANVAS,
};
