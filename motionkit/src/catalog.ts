/**
 * The pick list shown to users: how confident we are in each preset and why, plus slider ranges for fine-tuning.
 * test/catalog.test.ts checks every preset and every preset parameter is covered here.
 */

export type ParamUi =
  | { kind: 'range'; min: number; max: number; step: number; unit?: string }
  | { kind: 'choice'; options: { value: string; label: string }[] };

export interface CatalogEntry {
  id: string;
  /** 3 = measured from Iron Jaw and verified; 2 = animation convention, verified smooth; 1 = depends on rig setup. */
  confidence: 1 | 2 | 3;
  basis: string;
  category: 'character' | 'symbol';
  /** Suggested animation name in the exported file. */
  name: string;
  params: Record<string, ParamUi>;
}

const r = (min: number, max: number, step: number, unit?: string): ParamUi => ({ kind: 'range', min, max, step, unit });
const HAIR: ParamUi = {
  kind: 'choice',
  options: [
    { value: 'keys', label: '物理烘成關鍵影格（推薦）' },
    { value: 'physics', label: '保留 runtime 物理' },
    { value: 'none', label: '不動' },
  ],
};

export const CATALOG: CatalogEntry[] = [
  {
    id: 'fighter-idle-bounce', confidence: 3, category: 'character', name: 'idle',
    basis: 'Iron Jaw 左角色逐幀量測（週期 1.99 s、R² 0.998）；在旋轉 90° 與鏡像骨架上重現誤差 < 3%、相位 ≤ 1 幀',
    params: { period: r(1.2, 3, 0.05, 's'), intensity: r(0.3, 2, 0.05, '×'), hair: HAIR },
  },
  {
    id: 'fighter-idle-weave', confidence: 3, category: 'character', name: 'idle_weave',
    basis: 'Iron Jaw 右角色逐幀量測；閃身時頭的上下與左右相差 170°',
    params: { period: r(1.2, 3, 0.05, 's'), intensity: r(0.3, 2, 0.05, '×'), hair: HAIR },
  },
  {
    id: 'punch', confidence: 3, category: 'character', name: 'punch',
    basis: '時序取自 Iron Jaw 出拳（蓄力 8 幀、出手 2 幀、停留 8 幀、收回 7 幀）；IK 防打直已驗證',
    params: { reach: r(10, 300, 5), windup: r(0, 80, 1), lean: r(0, 15, 0.5, '°') },
  },
  {
    id: 'symbol-land', confidence: 3, category: 'symbol', name: 'land',
    basis: 'Iron Jaw 轉輪停輪量測：過衝 0.11 格、3–4 幀彈回',
    params: { symbolSize: r(40, 300, 1), drop: r(0, 2, 0.05, '格') },
  },
  {
    id: 'text-slam', confidence: 3, category: 'symbol', name: 'title_slam',
    basis: 'Iron Jaw Big Win 換級的打擊幀：0.1 秒砸入、過衝回彈',
    params: { from: r(1.2, 4, 0.05, '×'), tilt: r(0, 20, 0.5, '°') },
  },
  {
    id: 'breathe', confidence: 2, category: 'character', name: 'idle_breathe',
    basis: '動畫常規：胸口擴張、肩膀晚 3 幀、頭晚 5 幀；自動判斷骨骼方向；檢查 0 錯誤 0 警告',
    params: { period: r(2, 5, 0.1, 's'), intensity: r(0.3, 2, 0.05, '×') },
  },
  {
    id: 'chain-sway', confidence: 2, category: 'character', name: 'loop_sway',
    basis: '沿鏈傳遞的波；物理烘焙模式與即時物理相差 ≤ 1 幀',
    params: {
      amplitude: r(1, 20, 0.5, '°'), period: r(0.5, 4, 0.1, 's'), lagFrames: r(0, 8, 1, '幀'), gain: r(1, 1.6, 0.05, '×'),
      mode: { kind: 'choice', options: [{ value: 'keys', label: '數學波' }, { value: 'bake', label: '物理烘成關鍵影格' }, { value: 'physics', label: '保留 runtime 物理' }] },
    },
  },
  {
    id: 'hit-react', confidence: 2, category: 'character', name: 'hit',
    basis: '動畫常規：瞬間後退、彈簧回位，頭晚 2 幀甩得更大',
    params: { force: r(5, 120, 1), tilt: r(0, 30, 0.5, '°'), duration: r(0.5, 1.5, 0.05, 's') },
  },
  {
    id: 'symbol-win', confidence: 2, category: 'symbol', name: 'win',
    basis: '動畫常規：彈出到 122% 再以彈簧回位，搖晃衰減',
    params: { pop: r(0.05, 0.5, 0.01, '×'), wobble: r(0, 15, 0.5, '°') },
  },
  {
    id: 'symbol-win-loop', confidence: 2, category: 'symbol', name: 'win_loop',
    basis: '動畫常規：0.8 秒一次脈動，搭配慢搖',
    params: { pulse: r(0.02, 0.15, 0.005, '×') },
  },
  {
    id: 'symbol-idle', confidence: 2, category: 'symbol', name: 'idle',
    basis: '動畫常規：2 秒一次上下漂浮、4 秒一次輕搖',
    params: { symbolSize: r(40, 300, 1) },
  },
  {
    id: 'tease-shake', confidence: 2, category: 'symbol', name: 'tease',
    basis: '越抖越強並脹大、最後收回；頭尾不會跳',
    params: { symbolSize: r(40, 300, 1), duration: r(0.5, 3, 0.05, 's') },
  },
  {
    id: 'jump', confidence: 1, category: 'character', name: 'jump',
    basis: '擠壓伸展正確，但工具不彎腿：沒有腳 IK 時下蹲會像整個人下沉，需要骨架有腿部 IK 另外處理',
    params: { height: r(20, 300, 5), crouch: r(5, 60, 1) },
  },
  {
    id: 'celebrate', confidence: 1, category: 'character', name: 'celebrate',
    basis: '雙手要是 IK 目標骨才好看；手臂短時會被 IK 防打直限制高度',
    params: { lift: r(20, 250, 5), bounce: r(0, 40, 1) },
  },
];

/** Applies to every preset: overall speed, overall amplitude, facing. */
export const COMMON_PARAMS = {
  timeScale: { label: '速度', ui: r(0.5, 2, 0.05, '×'), default: 1 },
  amplitude: { label: '幅度', ui: r(0.3, 2, 0.05, '×'), default: 1 },
} as const;
