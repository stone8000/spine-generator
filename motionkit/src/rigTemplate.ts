/**
 * The character template that image agents draw to. One canvas, one pose (boxing guard, facing right), fixed layer
 * names. Joint positions are defaults; the studio lets the user drag them onto the actual art before rigging.
 *
 * Coordinates: canvas pixels, y down, origin top-left (what image tools use). The rig converts to Spine units
 * (1 unit = 1 canvas px, y up, origin = ROOT_PX, the ground point between the feet).
 */

export const CANVAS = { width: 1024, height: 1536 } as const;
export const ROOT_PX = { x: 512, y: 1456 } as const;

export type JointId =
  | 'hip' | 'waist' | 'neckBase' | 'headBase' | 'headTop'
  | 'shoulderF' | 'elbowF' | 'wristF'
  | 'shoulderB' | 'elbowB' | 'wristB'
  | 'hipF' | 'kneeF' | 'ankleF'
  | 'hipB' | 'kneeB' | 'ankleB'
  | 'hairRoot' | 'hairTip';

export interface JointDef {
  id: JointId;
  label: string;
  /** Default position on the canvas (px, y down). */
  x: number;
  y: number;
}

/** Spine units → canvas px: x + 512, 1456 − y. Defaults are the measured fighter test rig scaled ×2.2. */
const P = (x: number, y: number) => ({ x: Math.round(ROOT_PX.x + x), y: Math.round(ROOT_PX.y - y) });

export const JOINTS: JointDef[] = [
  { id: 'hip', label: '骨盆中心', ...P(0, 660) },
  { id: 'waist', label: '腰（骨盆上緣）', ...P(0, 792) },
  { id: 'neckBase', label: '脖子底（兩肩連線中點）', ...P(0, 1056) },
  { id: 'headBase', label: '下巴後方（頭頸交界）', ...P(0, 1122) },
  { id: 'headTop', label: '頭頂', ...P(34, 1317) },
  { id: 'shoulderF', label: '前肩', ...P(55, 1034) },
  { id: 'elbowF', label: '前手肘', ...P(132, 901) },
  { id: 'wristF', label: '前手腕', ...P(242, 993) },
  { id: 'shoulderB', label: '後肩', ...P(-44, 1023) },
  { id: 'elbowB', label: '後手肘', ...P(33, 890) },
  { id: 'wristB', label: '後手腕', ...P(125, 999) },
  { id: 'hipF', label: '前腿根', ...P(33, 660) },
  { id: 'kneeF', label: '前膝', ...P(79, 333) },
  { id: 'ankleF', label: '前腳踝', ...P(56, 40) },
  { id: 'hipB', label: '後腿根', ...P(-33, 660) },
  { id: 'kneeB', label: '後膝', ...P(-79, 333) },
  { id: 'ankleB', label: '後腳踝', ...P(-79, 40) },
  { id: 'hairRoot', label: '前髮根部', ...P(-10, 1291) },
  { id: 'hairTip', label: '前髮尾端', ...P(-135, 1143) },
];

export interface PartDef {
  /** File name without .png. */
  name: string;
  label: string;
  /** Bone the image follows. */
  bone: string;
  required: boolean;
  /** For the agent brief: what must be painted beyond the visible area. */
  hidden: string;
}

/** Draw order: first = furthest back. Facing right: "F" = the side nearer the viewer / the leading side. */
export const PARTS: PartDef[] = [
  { name: 'hair_back', label: '後髮', bone: 'head', required: false, hidden: '被頭擋住的部分整片畫完' },
  { name: 'arm_back_upper', label: '後手上臂', bone: 'upperArmB', required: true, hidden: '肩膀端多畫進軀幹裡、手肘端畫成圓頭' },
  { name: 'arm_back_lower', label: '後手前臂', bone: 'foreArmB', required: true, hidden: '手肘端畫成圓頭，與上臂重疊' },
  { name: 'fist_back', label: '後手拳頭', bone: 'handB', required: true, hidden: '手腕端多畫一段，與前臂重疊' },
  { name: 'leg_back_upper', label: '後腿大腿', bone: 'thighB', required: true, hidden: '腿根多畫進骨盆裡、膝蓋畫成圓頭' },
  { name: 'leg_back_lower', label: '後腿小腿（含腳）', bone: 'shinB', required: true, hidden: '膝蓋端畫成圓頭，與大腿重疊' },
  { name: 'pelvis', label: '骨盆／褲頭', bone: 'hip', required: true, hidden: '上緣多畫進軀幹裡，兩側腿根處畫滿' },
  { name: 'leg_front_upper', label: '前腿大腿', bone: 'thighF', required: true, hidden: '腿根多畫進骨盆裡、膝蓋畫成圓頭' },
  { name: 'leg_front_lower', label: '前腿小腿（含腳）', bone: 'shinF', required: true, hidden: '膝蓋端畫成圓頭，與大腿重疊' },
  { name: 'torso', label: '軀幹（胸、腹）', bone: 'chest', required: true, hidden: '兩肩下方、被手臂擋住的胸口與腋下都要畫完' },
  { name: 'neck', label: '脖子', bone: 'neck', required: false, hidden: '上下兩端都多畫，伸進頭和軀幹裡' },
  { name: 'head', label: '頭（臉）', bone: 'head', required: true, hidden: '下巴下方多畫一段脖子，伸進軀幹' },
  { name: 'hair_front', label: '前髮（一撮）', bone: 'hairF', required: false, hidden: '髮根多畫進頭裡' },
  { name: 'arm_front_upper', label: '前手上臂', bone: 'upperArmF', required: true, hidden: '肩膀端多畫進軀幹裡、手肘端畫成圓頭' },
  { name: 'arm_front_lower', label: '前手前臂', bone: 'foreArmF', required: true, hidden: '手肘端畫成圓頭，與上臂重疊' },
  { name: 'fist_front', label: '前手拳頭', bone: 'handF', required: true, hidden: '手腕端多畫一段，與前臂重疊' },
];

/** Roles the motion presets expect, as named by this template. */
export const TEMPLATE_ROLES: Record<string, string | string[]> = {
  root: 'root', hip: 'hip', body: 'hip', chest: 'chest', head: 'head',
  fist: 'ik-fist-front', fistFront: 'ik-fist-front', fistBack: 'ik-fist-back', fists: ['ik-fist-front', 'ik-fist-back'],
  shoulderBack: 'upperArmB', shoulders: ['upperArmF', 'upperArmB'],
};

export type Joints = Record<JointId, { x: number; y: number }>;

export interface CanvasSize {
  width: number;
  height: number;
}

/** Where the ground point sits on a canvas of this size (the template's proportions, scaled). */
export function rootOf(c: CanvasSize = CANVAS): { x: number; y: number } {
  return { x: (ROOT_PX.x / CANVAS.width) * c.width, y: (ROOT_PX.y / CANVAS.height) * c.height };
}

/** Template joints, scaled to a canvas of this size. */
export function defaultJoints(c: CanvasSize = CANVAS): Joints {
  const sx = c.width / CANVAS.width, sy = c.height / CANVAS.height;
  return Object.fromEntries(JOINTS.map((j) => [j.id, { x: j.x * sx, y: j.y * sy }])) as Joints;
}

/** Canvas px (y down) → Spine units (y up, origin at the canvas's ground point). */
export function toSpine(p: { x: number; y: number }, c: CanvasSize = CANVAS): { x: number; y: number } {
  const r = rootOf(c);
  return { x: p.x - r.x, y: r.y - p.y };
}
