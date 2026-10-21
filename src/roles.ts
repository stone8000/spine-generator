/**
 * Guess roles from bone names and IK constraints, and suggest animations. Used by `spine-motion init`.
 * The result is a starting point: the user edits the recipe file afterwards.
 */
import { setupFrames, type SpineJson } from './spineJson.ts';
import type { Recipe, RecipeAnimation } from './recipe.ts';

const NOT_IK = (n: string) => !/(^|[-_ ])ik([-_ ]|$)|target|ctrl|control/i.test(n);

function pick(names: string[], ...patterns: RegExp[]): string | undefined {
  for (const p of patterns) {
    const hit = names.find((n) => p.test(n) && NOT_IK(n));
    if (hit) return hit;
  }
  return undefined;
}

/** Longest parent→child chain made only of bones whose names match the pattern. */
function chain(frames: ReturnType<typeof setupFrames>, pattern: RegExp): string[] {
  const set = [...frames.keys()].filter((n) => pattern.test(n));
  let best: string[] = [];
  for (const start of set) {
    const parent = frames.get(start)!.parent;
    if (parent && set.includes(parent)) continue; // not a chain root
    const path = [start];
    for (;;) {
      const last = path[path.length - 1];
      const child = set.find((n) => frames.get(n)!.parent === last);
      if (!child) break;
      path.push(child);
    }
    if (path.length > best.length) best = path;
  }
  return best;
}

/**
 * Recipe `size` for a character skeleton: its setup-pose height (bone extents, tips included) over the 580 units
 * the presets were measured at. Spine projects use any unit scale, so motion distances must follow the rig.
 */
export function estimateSize(json: SpineJson): number {
  let lo = Infinity, hi = -Infinity;
  for (const f of setupFrames(json).values()) {
    if (!NOT_IK(f.name)) continue; // IK targets / controls can sit anywhere
    const r = (f.worldRotation * Math.PI) / 180;
    for (const y of [f.worldY, f.worldY + Math.sin(r) * f.length]) {
      lo = Math.min(lo, y);
      hi = Math.max(hi, y);
    }
  }
  const h = hi - lo;
  return h > 1 ? Math.round((h / 580) * 100) / 100 : 1;
}

export function guessRecipe(json: SpineJson, facing: 1 | -1 = 1): { recipe: Recipe; notes: string[] } {
  const frames = setupFrames(json);
  const names = [...frames.keys()];
  const roles: Record<string, string | string[]> = {};
  const notes: string[] = [];
  const root = names.find((n) => frames.get(n)!.parent === null)!;
  roles.root = root;

  const hip = pick(names, /^(hip|hips|pelvis|waist)$/i, /hip|pelvis|waist/i, /^body$/i);
  const chest = pick(names, /^chest$/i, /chest|torso|upper.?body|spine.?2/i, /spine/i);
  const head = pick(names, /^head$/i, /head/i);
  if (hip) roles.hip = hip;
  if (chest) roles.chest = chest;
  if (head) roles.head = head;
  if (hip) roles.body = hip;

  const hair = chain(frames, /hair|bang|fringe|ponytail/i);
  if (hair.length) roles.hair = hair;
  const other = chain(frames, /tail|cape|cloak|ribbon|scarf|skirt|tassel/i);
  if (other.length) roles.chain = other;
  else if (hair.length) roles.chain = hair; // nothing else that sways: let chain-sway use the hair

  // IK targets → fists, front = further toward the facing direction
  const ikTargets: string[] = (json.constraints ?? []).filter((c: SpineJson) => c.type === 'ik').map((c: SpineJson) => c.target);
  const hands = ikTargets.filter((t) => /hand|fist|arm|wrist|punch/i.test(t));
  const fists = (hands.length ? hands : names.filter((n) => /fist|hand|wrist/i.test(n))).slice(0, 2);
  if (fists.length) {
    const sorted = [...fists].sort((a, b) => facing * (frames.get(b)!.worldX - frames.get(a)!.worldX));
    roles.fists = sorted;
    roles.fist = sorted[0];
    roles.fistFront = sorted[0];
    if (sorted[1]) roles.fistBack = sorted[1];
    if (!hands.length) notes.push(`拳頭用的是一般骨骼 ${fists.join('、')}，不是 IK 目標；若手臂是 FK，出拳只會移動手掌。建議改用 IK 目標骨。`);
  }
  const feet = ikTargets.filter((t) => /foot|feet|leg|ankle|heel|toe/i.test(t));
  if (feet.length) roles.feet = feet.slice(0, 2);
  const shoulders = names.filter((n) => /shoulder|clavicle|upper.?arm/i.test(n) && NOT_IK(n)).slice(0, 2);
  if (shoulders.length) {
    roles.shoulders = shoulders;
    const back = [...shoulders].sort((a, b) => facing * (frames.get(a)!.worldX - frames.get(b)!.worldX))[0];
    roles.shoulderBack = back;
  }

  const animations: RecipeAnimation[] = [];
  const isSymbol = names.length <= 6 && !head;
  // symbol presets are tuned in their own units; characters scale with the rig
  const size = isSymbol ? 1 : estimateSize(json);
  if (size !== 1) notes.push(`角色高約 ${Math.round(size * 580)} 單位，動作位移倍率 size = ${size}。`);
  if (isSymbol) {
    const sym = names.find((n) => frames.get(n)!.parent === root) ?? root;
    roles.symbol = sym;
    roles.title = sym;
    animations.push(
      { name: 'idle', preset: 'symbol-idle' },
      { name: 'land', preset: 'symbol-land' },
      { name: 'win', preset: 'symbol-win' },
      { name: 'win_loop', preset: 'symbol-win-loop' },
      { name: 'tease', preset: 'tease-shake' },
    );
    notes.push(`看起來是符號骨架（${names.length} 根骨）：已套符號類預設，symbol = ${sym}。`);
  } else if (hip) {
    animations.push({ name: 'idle', preset: fists.length ? 'fighter-idle-bounce' : 'breathe', params: hair.length ? { hair: 'keys' } : {} });
    if (roles.fist) animations.push({ name: 'punch', preset: 'punch' });
    animations.push({ name: 'hit', preset: 'hit-react' }, { name: 'jump', preset: 'jump' });
    if (fists.length) animations.push({ name: 'celebrate', preset: 'celebrate' });
  } else if (chest) {
    animations.push({ name: 'idle', preset: 'breathe' });
  }
  if (other.length) animations.push({ name: 'loop_sway', preset: 'chain-sway', params: { mode: 'bake' } });
  if (!hip && !isSymbol) notes.push('找不到 hip / pelvis 之類的骨骼，請在 roles 手動指定 hip。');
  if (!head && !isSymbol) notes.push('找不到 head 骨骼。');
  return { recipe: { fps: 30, facing, size, roles, animations }, notes };
}
