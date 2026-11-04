/**
 * Motions for the stand34 template (a performer standing in front three-quarter view, turned toward the board on
 * screen left). Written pose-to-pose the way a 2D animator keys them: every action has its anticipation, a fast
 * action, an overshoot that settles, a held pose that keeps breathing, and a recovery; the parts that hang off the
 * body (tail, coat tails, hat) arrive late. Positions are in screen space and solved onto the rig, so the same keys
 * work on any drawing made to the template.
 *
 * Swap keys (head expressions, hand drawings, hat on head / in hand) always finish on the drawing the idle shows, so
 * mixing back to idle never leaves a stale face or a hat floating in the hand.
 */
import type { Motion1D } from './bake.ts';
import { poses, wave, shake, type PosePoint } from './motions.ts';
import type { EaseName } from './ease.ts';
import { empty, fn, hairFollow, type PresetContext, type PresetDef, type PresetOutput } from './presets.ts';

type P = { x: number; y: number };

/** The drawings the idle shows; every once-motion ends on these. */
const REST: Record<string, string> = { head: 'head_idle', hand_r: 'hand_r_pinch', hand_l: 'hand_l_hip', hat: 'hat', glasses: 'glasses' };

/** Body height in skeleton units (size 1 = 580). */
const height = (c: PresetContext) => 580 * c.size;

/** Pose-to-pose track from [time, value, ease] triples. */
function keys(points: [number, number, EaseName?][], duration: number, loop = false): Motion1D {
  return poses(points.map(([time, value, ease]) => ({ time, value, ease }) as PosePoint), { duration, loop });
}

/** Where a point attached to the hip ends up when the hip moves by (dx, dy) and turns by rot degrees. */
function followHip(c: PresetContext, p: P, dx: number, dy: number, rot: number): P {
  const H = c.worldPosition(c.role('hip'));
  const r = (rot * Math.PI) / 180, rx = p.x - H.x, ry = p.y - H.y;
  return { x: H.x + dx + Math.cos(r) * rx - Math.sin(r) * ry, y: H.y + dy + Math.sin(r) * rx + Math.cos(r) * ry };
}

interface Body {
  x: Motion1D;
  y: Motion1D;
  rot: Motion1D;
}

/** Hip motion plus the IK hands riding with it (a hand target hangs off the root and would otherwise stay behind). */
function body(c: PresetContext, out: PresetOutput, b: Body) {
  const hip = c.role('hip');
  out.screen.push({ bone: hip, channel: 'x', motion: b.x }, { bone: hip, channel: 'y', motion: b.y }, { bone: hip, channel: 'rotate', motion: b.rot });
}

/**
 * An IK hand: `path(t)` is where the wrist should be on screen at time t, or null to ride with the body from where it
 * was drawn. Both tracks are offsets from the drawn position.
 */
function hand(c: PresetContext, out: PresetOutput, target: string, D: number, b: Body, path: (t: number, riding: P) => P | null, loop = false): (t: number) => P {
  const home = c.ikChain(target)?.target ?? c.worldPosition(target);
  const at = (t: number) => {
    const riding = followHip(c, home, b.x.value(t), b.y.value(t), b.rot.value(t));
    return path(t, riding) ?? riding;
  };
  const breaks = [...(b.x.breaks ?? []), ...(b.y.breaks ?? [])];
  out.screen.push(
    { bone: target, channel: 'x', motion: fn(D, (t) => at(t).x - home.x, breaks, loop) },
    { bone: target, channel: 'y', motion: fn(D, (t) => at(t).y - home.y, breaks, loop) },
  );
  return at;
}

/** World angle (degrees) of the forearm of a 2-bone IK chain whose wrist is at `w`, elbow bent to the drawn side. */
function forearmAngle(chain: NonNullable<ReturnType<PresetContext['ikChain']>>, w: P): number {
  const [l1, l2] = chain.lengths;
  const dx = w.x - chain.root.x, dy = w.y - chain.root.y;
  const d = Math.min(Math.max(Math.hypot(dx, dy), 1e-3), l1 + l2 - 1e-3);
  const a = Math.atan2(dy, dx);
  const cosA = Math.max(-1, Math.min(1, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d)));
  const side = Math.sign((chain.target.x - chain.root.x) * (chain.mid.y - chain.root.y) - (chain.target.y - chain.root.y) * (chain.mid.x - chain.root.x)) || 1;
  const e = { x: chain.root.x + l1 * Math.cos(a + side * Math.acos(cosA)), y: chain.root.y + l1 * Math.sin(a + side * Math.acos(cosA)) };
  return (Math.atan2(w.y - e.y, w.x - e.x) * 180) / Math.PI;
}

/**
 * Keep a hand's drawing upright while it holds something (a hat stays a hat, not a hat on its side): `upright(t)` = 1
 * holds the drawn world angle plus `tilt(t)`, 0 lets the hand ride the forearm as usual.
 */
function holdUpright(c: PresetContext, out: PresetOutput, target: string, handBone: string, D: number, at: (t: number) => P, upright: (t: number) => number, tilt: (t: number) => number = () => 0, loop = false) {
  const chain = c.ikChain(target);
  if (!chain) return;
  const fa0 = forearmAngle(chain, chain.target);
  out.screen.push({ bone: handBone, channel: 'rotate', motion: fn(D, (t) => {
    const natural = ((forearmAngle(chain, at(t)) - fa0 + 540) % 360) - 180;
    const u = upright(t);
    return (1 - u) * natural + u * tilt(t);
  }, [], loop) });
}

const mix = (a: P, b: P, u: number): P => ({ x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u });
const smooth = (u: number) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const window = (t: number, t0: number, t1: number) => smooth((t - t0) / (t1 - t0));

/** Tail and coat tails: a travelling S-wave down the chain on top of physics follow-through. */
function tail(c: PresetContext, out: PresetOutput, D: number, amp: number, period: number, loop: boolean) {
  const chain = c.roleList('tail');
  period = D / Math.max(1, Math.round(D / period)); // whole cycles, so the wave ends where it started
  chain.forEach((bone, i) => {
    out.local.push({ bone, channel: 'rotate', motion: wave({ amplitude: amp * (1 + 0.5 * i), period, peakAt: (0.18 * i * period) % period, duration: D }) });
  });
  hairFollow(c, out);
  void loop;
}

function swap(out: PresetOutput, slot: string, k: [number, string | null][]) {
  (out.swaps ??= []).push({ slot, keys: k.map(([time, name]) => ({ time, name })) });
}

/** Keys every swap slot back to its idle drawing at t (and at 0, so a loop starts clean). */
function restSwaps(c: PresetContext, out: PresetOutput, t = 0, skip: string[] = []) {
  for (const [slot, name] of Object.entries(REST)) {
    if (skip.includes(slot)) continue;
    if (c.hasAttachment && !c.hasAttachment(slot, name)) continue;
    swap(out, slot, [[t, name]]);
  }
}

const TO_BOARD = (c: PresetContext) => -c.facing; // the board stands on screen left of a facing:1 delivery

// ---------------------------------------------------------------- idle
function idleBody(c: PresetContext, D: number): Body {
  const H = height(c);
  return {
    // weight drifts between the feet once per loop; the hip settles lower on each breath out
    x: wave({ amplitude: 0.006 * H, period: D, peakAt: 0.25 * D, duration: D }),
    y: wave({ amplitude: 0.004 * H, period: D / 2, peakAt: 0.1 * D, duration: D }),
    rot: wave({ amplitude: 1.1, period: D, peakAt: 0.3 * D, duration: D }),
  };
}

const standIdle: PresetDef = {
  id: 'stand-idle',
  title: '站姿待機（stand34）',
  kind: 'loop',
  summary: '呼吸、重心在兩腳間移動、肩髖反向、頭與帽子延遲跟隨、尾巴 S 形慢甩、一次眨眼。4 秒循環。',
  roles: [{ name: 'hip', required: true, desc: '骨盆' }],
  params: [
    { name: 'duration', default: 4, desc: '循環長度（秒）' },
    { name: 'blinkAt', default: 2.6, desc: '眨眼時間（秒）' },
  ],
  build(c) {
    const D = c.param('duration', 4);
    const out = empty(D, true);
    const b = idleBody(c, D);
    body(c, out, b);
    // the chest counters the hip (contrapposto) and breathes; the head trails the chest
    out.screen.push({ bone: c.role('chest'), channel: 'rotate', motion: wave({ amplitude: 1.4, period: D, peakAt: 0.8 * D, duration: D }) });
    out.local.push({ bone: c.role('chest'), channel: 'scaleX', motion: wave({ amplitude: 0.012, period: D / 2, peakAt: 0.6 * (D / 2), offset: 1, duration: D }) });
    out.screen.push({ bone: c.role('head'), channel: 'rotate', motion: wave({ amplitude: 1.8, period: D, peakAt: 0.42 * D, duration: D }) });
    const hat = c.roleOpt('hat');
    if (hat) out.local.push({ bone: hat, channel: 'rotate', motion: wave({ amplitude: 1.2, period: D, peakAt: 0.55 * D, duration: D }) });
    hand(c, out, c.role('fistBack'), D, b, () => null, true);
    // the lapel hand drifts a little on its own, out of step with the breath
    const H = height(c);
    hand(c, out, c.role('fistFront'), D, b, (t, r) => ({ x: r.x + 0.003 * H * Math.sin((2 * Math.PI * t) / D + 1), y: r.y + 0.004 * H * Math.sin((4 * Math.PI * t) / D) }), true);
    tail(c, out, D, 4, D, true);
    restSwaps(c, out, 0);
    const blink = c.param('blinkAt', 2.6);
    if (!c.hasAttachment || c.hasAttachment('head', 'head_blink')) swap(out, 'head', [[0, 'head_idle'], [blink, 'head_blink'], [blink + 0.12, 'head_idle']]);
    return out;
  },
};

// ---------------------------------------------------------------- fidget: adjust the monocle
const standFidget: PresetDef = {
  id: 'stand-fidget',
  title: '待機小動作：轉眼鏡（stand34）',
  kind: 'once',
  summary: '近手抬到眼鏡、捏住轉兩下、頭跟著歪，放回翻領。2 秒，首尾是待機原姿。',
  roles: [{ name: 'hip', required: true, desc: '骨盆' }],
  params: [
    { name: 'pinchX', default: -0.026, desc: '捏的指尖相對手腕的位置（身高比例，x）' },
    { name: 'pinchY', default: 0.03, desc: '捏的指尖相對手腕的位置（身高比例，y 向上）' },
  ],
  build(c) {
    const D = 2;
    const out = empty(D, false);
    const H = height(c);
    const zero = keys([[0, 0]], D);
    const b: Body = { x: zero, y: keys([[0, 0], [0.4, -0.004 * H, 'sineInOut'], [1.5, -0.004 * H], [D, 0, 'sineInOut']], D), rot: keys([[0, 0], [0.45, -1.5, 'sineInOut'], [1.5, -1.5], [D, 0, 'sineInOut']], D) };
    body(c, out, b);
    const glasses = c.roleOpt('glasses');
    const head = c.role('head');
    out.screen.push({ bone: head, channel: 'rotate', motion: keys([[0, 0], [0.45, 6 * c.facing, 'sineInOut'], [0.9, 4 * c.facing, 'sineInOut'], [1.3, 6 * c.facing, 'sineInOut'], [1.75, 0, 'sineInOut']], D) });
    const pinch = { x: c.param('pinchX', -0.026) * H, y: c.param('pinchY', 0.03) * H };
    const G = glasses ? c.worldPosition(glasses) : c.worldPosition(head);
    const grip = { x: G.x - pinch.x, y: G.y - pinch.y };
    // two small twists of the lens: the hand circles while it holds it
    const twist = (t: number) => (t > 0.55 && t < 1.35 ? Math.sin(((t - 0.55) / 0.8) * 2 * Math.PI) : 0);
    hand(c, out, c.role('fistFront'), D, b, (t, r) => {
      const up = window(t, 0.1, 0.5) * (1 - window(t, 1.4, 1.85));
      const p = mix(r, grip, up);
      return { x: p.x + 0.006 * H * twist(t), y: p.y + 0.004 * H * Math.abs(twist(t)) };
    });
    hand(c, out, c.role('fistBack'), D, b, () => null);
    if (glasses) out.local.push({ bone: glasses, channel: 'rotate', motion: fn(D, (t) => 9 * twist(t)) });
    tail(c, out, D, 3, D, false);
    restSwaps(c, out, 0);
    return out;
  },
};

// ---------------------------------------------------------------- hypno gaze (the multiplier throw)
const hypnoGaze: PresetDef = {
  id: 'hypno-gaze',
  title: '催眠一瞪（stand34）',
  kind: 'once',
  summary: '下沉預備 → 近手捏住眼鏡往下拉、螺旋眼 → 身體衝向盤面、遠手伸直指出（throw 事件） → 停住微顫 → 收回。1.6 秒。',
  roles: [{ name: 'hip', required: true, desc: '骨盆' }],
  params: [
    { name: 'reach', default: 0.97, desc: '指出那隻手伸到手臂長度的幾成' },
    { name: 'pointUp', default: 12, desc: '指出的角度：水平往上幾度' },
    { name: 'lunge', default: 0.03, desc: '身體衝向盤面的距離（身高比例）' },
    { name: 'pull', default: 0.035, desc: '眼鏡拉下的距離（身高比例）' },
    { name: 'pinchX', default: -0.026, desc: '捏的指尖相對手腕（身高比例，x）' },
    { name: 'pinchY', default: 0.03, desc: '捏的指尖相對手腕（身高比例，y 向上）' },
  ],
  build(c) {
    const D = 1.6;
    const out = empty(D, false);
    const H = height(c);
    const tb = TO_BOARD(c);
    const lunge = c.param('lunge', 0.03) * H;
    // anticipation away from the board and down, then the lunge (back-out overshoot), hold, recover
    const b: Body = {
      x: keys([[0, 0], [0.25, -0.008 * H * tb, 'sineInOut'], [0.45, -0.006 * H * tb], [0.6, lunge * tb, 'backOut'], [1.15, lunge * 0.9 * tb, 'sineInOut'], [1.55, 0, 'sineInOut'], [D, 0]], D),
      y: keys([[0, 0], [0.25, -0.022 * H, 'sineInOut'], [0.45, -0.018 * H], [0.6, -0.006 * H, 'quadOut'], [1.15, -0.008 * H], [1.55, 0, 'sineInOut'], [D, 0]], D),
      rot: keys([[0, 0], [0.25, -3 * tb, 'sineInOut'], [0.45, -2 * tb], [0.6, 5 * tb, 'backOut'], [1.15, 4 * tb, 'sineInOut'], [1.55, 0, 'sineInOut'], [D, 0]], D),
    };
    body(c, out, b);
    out.screen.push({ bone: c.role('chest'), channel: 'rotate', motion: keys([[0, 0], [0.27, -3 * tb, 'sineInOut'], [0.47, -2 * tb], [0.64, 4 * tb, 'backOut'], [1.15, 3 * tb, 'sineInOut'], [1.58, 0, 'sineInOut'], [D, 0]], D) });
    // chin down to look over the lowered glasses, then the stare
    const head = c.role('head');
    out.screen.push({ bone: head, channel: 'rotate', motion: keys([[0, 0], [0.3, -5 * c.facing, 'sineInOut'], [0.45, -7 * c.facing, 'quadOut'], [0.66, -3 * c.facing, 'backOut'], [1.2, -4 * c.facing, 'sineInOut'], [1.58, 0, 'sineInOut'], [D, 0]], D) });
    const hat = c.roleOpt('hat');
    if (hat) out.local.push({ bone: hat, channel: 'rotate', motion: keys([[0, 0], [0.5, 0], [0.7, -4 * tb, 'quadOut'], [0.9, 1.5 * tb, 'sineInOut'], [1.1, 0, 'sineInOut'], [D, 0]], D) });

    // near hand: up to the glasses, pull them down, hold, push them back up, back to the lapel
    const glasses = c.roleOpt('glasses');
    const G = glasses ? c.worldPosition(glasses) : c.worldPosition(head);
    const pinch = { x: c.param('pinchX', -0.026) * H, y: c.param('pinchY', 0.03) * H };
    const pull = c.param('pull', 0.035) * H;
    const down = (t: number) => pull * (window(t, 0.3, 0.45) - window(t, 1.15, 1.32));
    const onFace = (t: number) => followHip(c, G, b.x.value(t), b.y.value(t), b.rot.value(t));
    hand(c, out, c.role('fistFront'), D, b, (t, r) => {
      const g = onFace(t);
      const grip = { x: g.x - pinch.x, y: g.y - pinch.y - down(t) };
      return mix(r, grip, window(t, 0.06, 0.3) * (1 - window(t, 1.3, 1.56)));
    });
    if (glasses) out.screen.push({ bone: glasses, channel: 'y', motion: fn(D, (t) => -down(t), [0.3, 0.45, 1.15, 1.32]) });

    // far hand: from the hip straight out at the board, a slight upward angle, buzzing while it holds
    const far = c.role('fistBack');
    const chain = c.ikChain(far);
    if (chain) {
      const ang = (c.param('pointUp', 12) * Math.PI) / 180;
      const reach = c.param('reach', 0.97) * chain.reach;
      const buzz = shake({ amplitude: 0.0025 * H, frequency: 11, duration: D, envelope: 'flat', seed: 7 });
      hand(c, out, far, D, b, (t, r) => {
        const sh = followHip(c, chain.root, b.x.value(t), b.y.value(t), b.rot.value(t));
        const point = { x: sh.x + Math.cos(ang) * reach * tb, y: sh.y + Math.sin(ang) * reach };
        // the arm whips out on an arc (through a point above the straight line) and comes home late
        const out1 = window(t, 0.45, 0.62), back = window(t, 1.18, 1.52);
        const u = out1 * (1 - back);
        const arc = Math.sin(Math.PI * u) * 0.05 * H;
        const p = mix(r, point, u);
        const hold = t > 0.62 && t < 1.18 ? buzz.value(t) : 0;
        return { x: p.x, y: p.y + arc + hold };
      });
    }
    tail(c, out, D, 3, 0.8, false);
    out.events.push({ name: 'throw', time: 0.62 });
    restSwaps(c, out, 0);
    swap(out, 'head', [[0, 'head_idle'], [0.4, 'head_spiral'], [1.3, 'head_idle']]);
    swap(out, 'hand_l', [[0, 'hand_l_hip'], [0.5, 'hand_l_point'], [1.42, 'hand_l_hip']]);
    return out;
  },
};

// ---------------------------------------------------------------- small win: tip the hat
const hatTip: PresetDef = {
  id: 'hat-tip',
  title: '小獎：點帽（stand34）',
  kind: 'once',
  summary: '近手抓帽緣、帽子抬起往盤面歪、點頭露齒笑，放回。1.1 秒。',
  roles: [{ name: 'hip', required: true, desc: '骨盆' }],
  params: [
    { name: 'gripX', default: -0.06, desc: '帽緣抓點相對帽底中心（身高比例，x，往盤面為負）' },
    { name: 'gripY', default: 0.0, desc: '帽緣抓點相對帽底中心（身高比例，y 向上）' },
  ],
  build(c) {
    const D = 1.1;
    const out = empty(D, false);
    const H = height(c);
    const tb = TO_BOARD(c);
    const b: Body = {
      x: keys([[0, 0], [0.35, 0.004 * H * tb, 'sineInOut'], [0.8, 0.004 * H * tb], [D, 0, 'sineInOut']], D),
      y: keys([[0, 0], [0.2, -0.006 * H, 'sineInOut'], [0.4, 0.002 * H, 'quadOut'], [0.8, 0], [D, 0, 'sineInOut']], D),
      rot: keys([[0, 0], [0.4, 2 * tb, 'sineInOut'], [0.8, 2 * tb], [D, 0, 'sineInOut']], D),
    };
    body(c, out, b);
    const head = c.role('head');
    out.screen.push({ bone: head, channel: 'rotate', motion: keys([[0, 0], [0.3, -2 * c.facing, 'sineInOut'], [0.45, 6 * c.facing, 'backOut'], [0.8, 5 * c.facing], [1.05, 0, 'sineInOut'], [D, 0]], D) });
    const hat = c.roleOpt('hat');
    const lift = (t: number) => window(t, 0.28, 0.45) * (1 - window(t, 0.75, 0.92));
    if (hat) {
      out.local.push({ bone: hat, channel: 'rotate', motion: fn(D, (t) => 9 * lift(t) * -tb, [0.28, 0.45, 0.75, 0.92]) });
      out.local.push({ bone: hat, channel: 'x', motion: fn(D, (t) => 0.012 * H * lift(t), [0.28, 0.45, 0.75, 0.92]) });
    }
    const base = hat ? c.worldPosition(hat) : c.worldPosition(head);
    const grip0 = { x: base.x + c.param('gripX', -0.06) * H * c.facing, y: base.y + c.param('gripY', 0) * H };
    hand(c, out, c.role('fistFront'), D, b, (t, r) => {
      const g = followHip(c, grip0, b.x.value(t), b.y.value(t), b.rot.value(t));
      return mix(r, { x: g.x, y: g.y + 0.012 * H * lift(t) }, window(t, 0.05, 0.28) * (1 - window(t, 0.88, 1.08)));
    });
    hand(c, out, c.role('fistBack'), D, b, () => null);
    tail(c, out, D, 3, 0.55, false);
    restSwaps(c, out, 0);
    swap(out, 'hand_r', [[0, 'hand_r_pinch'], [0.24, 'hand_r_grip'], [0.95, 'hand_r_pinch']]);
    swap(out, 'head', [[0, 'head_idle'], [0.3, 'head_grin'], [0.95, 'head_idle']]);
    return out;
  },
};

// ---------------------------------------------------------------- dud: shrug
const shrug: PresetDef = {
  id: 'shrug',
  title: '丟空：聳肩攤手（stand34）',
  kind: 'once',
  summary: '雙手攤開掌心朝上、肩膀聳起、頭歪皺眉，停一下後垂下回位。1.2 秒。',
  roles: [{ name: 'hip', required: true, desc: '骨盆' }],
  params: [],
  build(c) {
    const D = 1.2;
    const out = empty(D, false);
    const H = height(c);
    const tb = TO_BOARD(c);
    const b: Body = {
      x: keys([[0, 0], [D, 0]], D),
      y: keys([[0, 0], [0.12, -0.006 * H, 'sineIn'], [0.32, 0.01 * H, 'backOut'], [0.8, 0.009 * H], [1.05, -0.003 * H, 'quadIn'], [D, 0, 'sineOut']], D),
      rot: keys([[0, 0], [0.32, -2 * tb, 'backOut'], [0.8, -2 * tb], [1.1, 0, 'sineInOut'], [D, 0]], D),
    };
    body(c, out, b);
    const head = c.role('head');
    out.screen.push({ bone: head, channel: 'rotate', motion: keys([[0, 0], [0.35, 8 * c.facing, 'backOut'], [0.8, 7 * c.facing], [1.12, 0, 'sineInOut'], [D, 0]], D) });
    out.local.push({ bone: c.role('chest'), channel: 'scaleY', motion: keys([[0, 1], [0.32, 1.02, 'backOut'], [0.8, 1.02], [1.08, 1, 'sineInOut'], [D, 1]], D) });
    const palms = (target: string, side: number) => {
      const chain = c.ikChain(target);
      if (!chain) return hand(c, out, target, D, b, () => null);
      hand(c, out, target, D, b, (t, r) => {
        const sh = followHip(c, chain.root, b.x.value(t), b.y.value(t), b.rot.value(t));
        // elbows stay near the ribs, forearms swing out with the palms up at belt height
        const palm = { x: sh.x + side * 0.38 * chain.reach, y: sh.y - 0.72 * chain.reach + 0.03 * H * window(t, 0.15, 0.32) };
        return mix(r, palm, window(t, 0.08, 0.34) * (1 - window(t, 0.85, 1.12)));
      });
    };
    palms(c.role('fistBack'), tb);
    palms(c.role('fistFront'), -tb);
    tail(c, out, D, 5, 0.6, false);
    restSwaps(c, out, 0);
    swap(out, 'hand_l', [[0, 'hand_l_hip'], [0.14, 'hand_l_open'], [1.0, 'hand_l_hip']]);
    swap(out, 'hand_r', [[0, 'hand_r_pinch'], [0.14, 'hand_r_open'], [1.0, 'hand_r_pinch']]);
    swap(out, 'head', [[0, 'head_idle'], [0.2, 'head_annoyed'], [1.02, 'head_idle']]);
    return out;
  },
};

// ---------------------------------------------------------------- big win: off with the hat, then the cheer loop
/** The cheer loop's body motion; win_big ends on its frame 0 and win_big_end starts there. */
/**
 * How far the hip can rise with both feet planted before a leg locks straight (a locked IK leg snaps). Drawn knees
 * that are nearly straight leave almost nothing; the motions bounce downward from the drawn pose instead.
 */
function legSlack(c: PresetContext): number {
  let slack = Infinity;
  for (const f of c.roleList('feet')) {
    const k = c.ikChain(f);
    if (k) slack = Math.min(slack, Math.max(0, k.reach - Math.hypot(k.target.x - k.root.x, k.target.y - k.root.y)));
  }
  return slack === Infinity ? 0.02 * height(c) : 0.45 * slack;
}

function cheerBody(c: PresetContext): Body {
  const D = 1.2, H = height(c), tb = TO_BOARD(c);
  const top = Math.min(0.012 * H, legSlack(c));
  const low = -0.012 * H;
  return {
    x: wave({ amplitude: 0, period: D, duration: D }),
    y: wave({ amplitude: (top - low) / 2, period: D / 2, peakAt: 0, offset: (top + low) / 2, duration: D }),
    rot: wave({ amplitude: 1.5, period: D, peakAt: 0, offset: -2 * tb, duration: D }),
  };
}

/** The held cheer pose (setup frame; hands are carried by the body like everything else). */
function cheerPose(c: PresetContext) {
  const tb = TO_BOARD(c);
  const near = c.ikChain(c.role('fistFront'));
  const far = c.ikChain(c.role('fistBack'));
  const b0 = cheerBody(c);
  return {
    body0: { x: b0.x.value(0), y: b0.y.value(0), rot: b0.rot.value(0) },
    // hat hand high over the head, a little outward; the open hand out wide toward the board
    near: near && { x: near.root.x - tb * 0.33 * near.reach, y: near.root.y + 0.91 * near.reach },
    far: far && { x: far.root.x + tb * 0.72 * far.reach, y: far.root.y + 0.42 * far.reach },
    head: 8 * c.facing,
  };
}

const hatRaise: PresetDef = {
  id: 'hat-raise',
  title: '大獎：摘帽高舉（stand34）',
  kind: 'once',
  summary: '下蹲蓄力 → 近手抓帽 → 彈起摘帽高舉、遠手張開、仰頭大笑。1.4 秒，結尾接大獎循環。',
  roles: [{ name: 'hip', required: true, desc: '骨盆' }],
  params: [
    { name: 'gripX', default: -0.06, desc: '帽緣抓點（同點帽）' },
    { name: 'gripY', default: 0.0, desc: '帽緣抓點（同點帽）' },
  ],
  build(c) {
    const D = 1.4;
    const out = empty(D, false);
    const H = height(c);
    const pose = cheerPose(c);
    const b: Body = {
      x: keys([[0, 0], [D, 0]], D),
      y: keys([[0, 0], [0.22, -0.03 * H, 'sineInOut'], [0.42, -0.026 * H], [0.62, Math.min(pose.body0.y + 0.012 * H, legSlack(c)), 'quadOut'], [0.85, pose.body0.y, 'sineInOut'], [D, pose.body0.y]], D),
      rot: keys([[0, 0], [0.22, 1.5, 'sineInOut'], [0.42, 1], [0.7, pose.body0.rot, 'backOut'], [D, pose.body0.rot]], D),
    };
    body(c, out, b);
    out.screen.push({ bone: c.role('head'), channel: 'rotate', motion: keys([[0, 0], [0.22, -4 * c.facing, 'sineInOut'], [0.45, -3 * c.facing], [0.72, pose.head + 3 * c.facing, 'backOut'], [1.0, pose.head, 'sineInOut'], [D, pose.head]], D) });
    const hat = c.roleOpt('hat');
    const head = c.role('head');
    const base = hat ? c.worldPosition(hat) : c.worldPosition(head);
    const grip0 = { x: base.x + c.param('gripX', -0.06) * H * c.facing, y: base.y + c.param('gripY', 0) * H };
    if (pose.near) {
      const top = pose.near;
      const at = hand(c, out, c.role('fistFront'), D, b, (t, r) => {
        const g = followHip(c, grip0, b.x.value(t), b.y.value(t), b.rot.value(t));
        if (t < 0.42) return mix(r, g, window(t, 0.05, 0.38));
        const u = window(t, 0.42, 0.72);
        const p = mix(g, followHip(c, top, b.x.value(t), b.y.value(t), b.rot.value(t)), u);
        return { x: p.x, y: p.y + Math.sin(Math.PI * u) * 0.03 * H };
      });
      // once the hat is in the hand it stays upright, tipped a little toward the board at the top
      holdUpright(c, out, c.role('fistFront'), 'handF', D, at, (t) => window(t, 0.36, 0.56), (t) => -8 * TO_BOARD(c) * window(t, 0.6, 0.9));
    }
    if (pose.far) {
      const wide = pose.far;
      hand(c, out, c.role('fistBack'), D, b, (t, r) => {
        const inn = { x: r.x - TO_BOARD(c) * 0.02 * H, y: r.y + 0.01 * H };
        if (t < 0.45) return mix(r, inn, window(t, 0.05, 0.22));
        return mix(inn, followHip(c, wide, b.x.value(t), b.y.value(t), b.rot.value(t)), window(t, 0.45, 0.75));
      });
    }
    tail(c, out, D, 6, 0.7, false);
    out.events.push({ name: 'hat_off', time: 0.42 });
    restSwaps(c, out, 0);
    swap(out, 'hand_r', [[0, 'hand_r_pinch'], [0.3, 'hand_r_grip'], [0.42, 'hand_r_hatheld']]);
    if (hat) swap(out, 'hat', [[0, 'hat'], [0.42, null]]);
    swap(out, 'hand_l', [[0, 'hand_l_hip'], [0.5, 'hand_l_open']]);
    swap(out, 'head', [[0, 'head_idle'], [0.5, 'head_grin']]);
    return out;
  },
};

const standCheer: PresetDef = {
  id: 'stand-cheer',
  title: '大獎循環：揮帽（stand34）',
  kind: 'loop',
  summary: '接在摘帽高舉之後：身體上下彈、帽子在頭上畫小圈、遠手跟拍子打開收回。1.2 秒循環。',
  roles: [{ name: 'hip', required: true, desc: '骨盆' }],
  params: [],
  build(c) {
    const D = 1.2;
    const out = empty(D, true);
    const H = height(c);
    const pose = cheerPose(c);
    const b = cheerBody(c);
    body(c, out, b);
    out.screen.push({ bone: c.role('head'), channel: 'rotate', motion: wave({ amplitude: 3, period: D / 2, peakAt: 0, offset: pose.head, duration: D }) });
    // the hat circles over the head (starting at the circle's top so frame 0 sits on the held pose)
    if (pose.near) {
      const top = pose.near;
      const at = hand(c, out, c.role('fistFront'), D, b, (t) => {
        const a = (2 * Math.PI * t) / D;
        const p = followHip(c, top, b.x.value(t), b.y.value(t), b.rot.value(t));
        return { x: p.x + 0.02 * H * Math.sin(a), y: p.y + 0.015 * H * (Math.cos(a) - 1) };
      }, true);
      // the hat rocks with the circle but never lies down
      holdUpright(c, out, c.role('fistFront'), 'handF', D, at, () => 1, (t) => -8 * TO_BOARD(c) + 6 * Math.sin((2 * Math.PI * t) / D), true);
    }
    if (pose.far) {
      const wide = pose.far;
      hand(c, out, c.role('fistBack'), D, b, (t) => {
        const p = followHip(c, wide, b.x.value(t), b.y.value(t), b.rot.value(t));
        return { x: p.x, y: p.y + 0.02 * H * Math.sin((4 * Math.PI * t) / D) };
      }, true);
    }
    tail(c, out, D, 6, D / 2, true);
    swap(out, 'hand_r', [[0, 'hand_r_hatheld']]);
    if (c.roleOpt('hat')) swap(out, 'hat', [[0, null]]);
    swap(out, 'hand_l', [[0, 'hand_l_open']]);
    swap(out, 'head', [[0, 'head_grin']]);
    return out;
  },
};

const hatOn: PresetDef = {
  id: 'hat-on',
  title: '大獎結束：戴回帽子（stand34）',
  kind: 'once',
  summary: '從大獎循環姿勢把帽子戴回頭上、壓一下帽頂、雙手回到待機位。1 秒。',
  roles: [{ name: 'hip', required: true, desc: '骨盆' }],
  params: [
    { name: 'gripX', default: -0.06, desc: '帽緣抓點（同點帽）' },
    { name: 'gripY', default: 0.0, desc: '帽緣抓點（同點帽）' },
  ],
  build(c) {
    const D = 1;
    const out = empty(D, false);
    const H = height(c);
    const pose = cheerPose(c);
    const b: Body = {
      x: keys([[0, 0], [D, 0]], D),
      y: keys([[0, pose.body0.y], [0.4, 0.004 * H, 'sineInOut'], [0.5, -0.006 * H, 'quadIn'], [0.75, 0, 'sineOut'], [D, 0]], D),
      rot: keys([[0, pose.body0.rot], [0.6, 0, 'sineInOut'], [D, 0]], D),
    };
    body(c, out, b);
    out.screen.push({ bone: c.role('head'), channel: 'rotate', motion: keys([[0, pose.head], [0.45, 0, 'sineInOut'], [0.55, -2 * c.facing, 'quadOut'], [0.8, 0, 'sineInOut'], [D, 0]], D) });
    const hat = c.roleOpt('hat');
    const base = hat ? c.worldPosition(hat) : c.worldPosition(c.role('head'));
    const grip0 = { x: base.x + c.param('gripX', -0.06) * H * c.facing, y: base.y + c.param('gripY', 0) * H };
    if (pose.near) {
      const top = pose.near;
      const at = hand(c, out, c.role('fistFront'), D, b, (t, r) => {
        const g = followHip(c, grip0, b.x.value(t), b.y.value(t), b.rot.value(t));
        if (t < 0.45) {
          const u = window(t, 0, 0.45);
          const p = mix(followHip(c, top, b.x.value(t), b.y.value(t), b.rot.value(t)), g, u);
          return { x: p.x, y: p.y + Math.sin(Math.PI * u) * 0.025 * H };
        }
        return mix(g, r, window(t, 0.55, 0.95));
      });
      holdUpright(c, out, c.role('fistFront'), 'handF', D, at, (t) => 1 - window(t, 0.3, 0.45), () => -8 * TO_BOARD(c));
    }
    if (pose.far) {
      const wide = pose.far;
      hand(c, out, c.role('fistBack'), D, b, (t, r) => mix(followHip(c, wide, b.x.value(t), b.y.value(t), b.rot.value(t)), r, window(t, 0.1, 0.8)));
    }
    if (hat) out.local.push({ bone: hat, channel: 'y', motion: keys([[0, 0], [0.45, 0], [0.52, -0.008 * H, 'quadIn'], [0.7, 0, 'backOut'], [D, 0]], D) });
    tail(c, out, D, 4, 0.7, false);
    out.events.push({ name: 'hat_on', time: 0.45 });
    swap(out, 'hand_r', [[0, 'hand_r_hatheld'], [0.45, 'hand_r_grip'], [0.8, 'hand_r_pinch']]);
    if (hat) swap(out, 'hat', [[0, null], [0.45, 'hat']]);
    swap(out, 'hand_l', [[0, 'hand_l_open'], [0.7, 'hand_l_hip']]);
    swap(out, 'head', [[0, 'head_grin'], [0.6, 'head_idle']]);
    if (c.hasAttachment?.('glasses', 'glasses')) swap(out, 'glasses', [[0, 'glasses']]);
    return out;
  },
};

export const STAND34_PRESETS: PresetDef[] = [standIdle, standFidget, hypnoGaze, hatTip, shrug, hatRaise, standCheer, hatOn];
