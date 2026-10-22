"""Per-joint art budget: how far each joint turns before the art breaks
(SOTA §4.3; budget.json format §3.2).

    python -m spinegen.budget <rig.json | skeleton.json> <images dir | .atlas> --joints joints.json
        -o budget.json [--sheet sheet.png | --sheet DIR] [--sheet-max 90] [--step 2]
        [--max-angle 180] [--scale 1] [--no-verify] [--only name,name] [--skin name] [--trace]
        [--artreq artreq.json]

WHY
  "This arm holds 26 degrees" was measured on the monkey by eye, 10 degrees
  at a time (its README: 25 fine, 30 stiff, 40 leaves the vest, 50 a plank).
  Motion synthesis needs that number per joint and per direction, for every
  character, with the reason, so a motion that wants more knows what to
  ask for (redistribute, transit only, draw order, swap, deform, art
  request; §4.3). This module turns the eye into gates:
    theta_soft  the last angle with 0 folds, joint-band area >= 0.9, 0 seam
                gap px, 0 holes, 0 ghost (G1 G2 G4 G5 G6): holds may go here;
    theta_hard  the last angle with 0 folds and 0 holes (G1; G5 holes in the
                body and G4 seam gaps, the joint's own holes, with G4's
                >= 4 px component rule): strikes and transits may pass
                through, never stop. SOTA §4.3 asks 0 folds AND 0 holes, and
                §5 names G4 "joint holes": a cut-out elbow whose wedge is
                open is broken in a strike as much as in a hold (the rigid
                monkey's elbows read hard 150 while 168-1015 px wedges stood
                open from 60 deg, before G4 counted here).
  "Last" means contiguous from the setup pose: the first failing angle ends
  the range, whatever happens beyond it.

HOW
  For each joint and direction, ONLY the joint's bone turns, from its setup
  rotation, in steps; its helper follows through the rig's own transform
  constraints. IK is switched off (mix 0) for the sweep, because an IK
  chain would otherwise pull the turned bone back to its target; physics is
  the reset frame (no simulation). Each pose is rendered by spinegen.raster
  (geometry = spinegen.lbs, which C1 pins to spine-core) and read by
  spinegen.art_gates against the rest frame.
  The sweep goes on past the soft limit to the hard one, recording where
  EACH gate first fails (first_fail), because a disagreement with a hand
  measurement has to be argued gate by gate.
  Speed: slots the bone does not move are rasterised once and reused, and
  the sweep stops at the first hard failure. It runs at full scale by
  default (SOTA §5 pixel thresholds are for 1:1 renders; the monkey's four
  arm joints take under a minute). `scale` < 1 sweeps coarser (pixel
  thresholds scaled with it) and then VERIFIES both limits at scale 1 (the
  angle must pass and the next step fail; if not, the limit is walked to
  where that holds), because a coarse pixel can hide a 1 px tear. That
  check is local: contiguity below the limit and first_fail come from the
  coarse sweep.

OUTPUT (budget.json)
  {"schema": "spinegen-budget/1",
   "joints": {name: {"+": {"soft", "hard", "limit_by", "hard_by", "limits",
                           "first_fail": {gate id: {"angle", "by", "value"}}},
                     "-": {...}}},
   "meta": {...}}
  "+" is a positive local rotation of the bone (counter-clockwise, y up).
  limit_by is "<gate>@<where>" for the soft limit (fold@slot, hole@slot,
  seam@upper|lower, ghost@slot (G6a band) or ghost@revealed|occluder (G6b
  paint), area@<joint>_band (G2 summed band area) or tri@slot (G2: a band
  triangle of that slot left [0.3, 3])); hard_by the same for the
  hard limit; limits lists every soft gate that failed at the first failing
  angle. A limit that never fails within max_angle is max_angle with
  limit_by null.
  "ik" (rigs with leg IK, ik_scan): the legs in the poses legs make, IK
  on: a crouch (depth x sideways shift), a step and a lifted foot per leg,
  each walked in steps of H; per move the depth/distance each leg joint
  holds to, and per leg joint and direction the turn it holds to in those
  poses ("joints"). These replace the per-joint hip / knee / ankle sweeps
  above for planning, which turn one bone with IK off and so pull the
  ankle out of its boot.
  "artreq" lists art requests (spinegen-artreq/1 items) for duplicates a
  layer carries away from the paint it copies (G6(b) "carried", e.g. a
  shard of trousers cut into a hand image): not a joint limit, an art fix,
  with the angles where the scan first met it ("seen") and first saw it
  show ("visible_from", null if it never left matching paint).

JOINTS FILE (--joints)
  A list, or {"joints": [...]}, or {"joints": {name: entry}}; entries are
  {"name", "bone", "dirs": [1, -1], "band": {"center": [x, y], "upper",
  "helper", "lower", "halfwidth"}} in WORLD coordinates (y up) — the rig
  builder converts from canvas space, nothing here does. Missing pieces are
  derived from the rig: lower = bone, upper = its parent, center = the
  bone's setup origin, halfwidth = the art's inscribed radius there.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

from . import art_gates as ag
from . import lbs, raster

SCHEMA = "spinegen-budget/1"
SHEET_EVERY = 10.0
SOFT_ORDER = ("fold", "hole", "seam", "ghost", "area")  # limit_by priority when several fail at once
HARD_ORDER = ("fold", "hole", "seam")  # theta_hard: 0 folds, 0 holes (G5 body, G4 joint); hard_by priority


# ---------------------------------------------------------------- joints

def load_joints(path_or_obj):
    """Joint entries from a joints file (see the module doc)."""
    obj = path_or_obj
    if isinstance(path_or_obj, (str, os.PathLike)):
        with open(path_or_obj) as f:
            obj = json.load(f)
    if isinstance(obj, dict):
        obj = obj.get("joints", obj)
    if isinstance(obj, dict):
        obj = [dict(v, name=v.get("name", k)) for k, v in obj.items()]
    out = []
    for e in obj:
        e = dict(e)
        band = dict(e.get("band") or {})
        for k in ("center", "upper", "helper", "lower", "halfwidth", "axis", "extent", "slots", "band_center",
                  "window"):
            if k in e and k not in band:
                band[k] = e[k]
        if "center" not in band and "world" in e:
            band["center"] = e["world"]
        bone = e.get("bone") or band.get("lower")
        if bone is None:
            raise ValueError(f"joint {e.get('name')!r}: no bone to turn (give 'bone' or band.lower)")
        band.setdefault("lower", bone)
        out.append({"name": e.get("name") or bone, "bone": bone, "dirs": list(e.get("dirs", [1, -1])),
                    "band": band})
    # every joint knows where the other limbs start (art_gates.joint_parts:
    # a hand hanging by the thigh is no part of the hip)
    lowers = [j["band"]["lower"] for j in out]
    for j in out:
        others = [b for b in lowers if b != j["band"]["lower"]]
        if others:
            j["band"].setdefault("other_limbs", others)
    return out


# ---------------------------------------------------------------- one joint at one scale

class Probe:
    """Renders and judges one joint's bone at any angle, at one scale."""

    def __init__(self, ir, images, joint, scale=1.0, skin=None, rest_cache=None, canvas=None):
        self.joint = joint
        self.scale = scale
        key = ("rest", scale)
        if rest_cache is not None and key in rest_cache:
            self.r, self.rest, self.state = rest_cache[key]
        else:
            self.r = raster.Renderer(ir, images, skin=skin, scale=scale, canvas=canvas)
            self.state = {c["name"]: {"mix": 0.0} for c in ir["ik"]}
            self.rest = self.r.frame(state=self.state)
            if rest_cache is not None:
                rest_cache[key] = (self.r, self.rest, self.state)
        ev = self.r.ev
        self.bone = joint["bone"]
        if self.bone not in ev.index:
            raise KeyError(f"joint {joint['name']}: bone {self.bone!r} not in the rig")
        self.bi = ev.index[self.bone]
        self.setup_rot = ir["bones"][self.bi]["rotation"]
        self.jr = ag.joint_rest(self.rest, joint)
        self.moving = self._moving_slots()
        self.reuse = {s: f for s, f in self.rest.frags.items() if s not in self.moving}

    def _moving_slots(self):
        ev = self.r.ev
        rest_xy = {s: g.xy for s, g in self.rest.geoms.items()}
        moving = set()
        for a in (37.0, -53.0, 91.0):
            world = ev.world(self.pose(a), self.state)
            for s, g in self.r.geometry(world).items():
                x0 = rest_xy.get(s)
                if x0 is None or x0.shape != g.xy.shape or np.abs(x0 - g.xy).max() > 1e-6:
                    moving.add(s)
        return moving

    def pose(self, angle):
        return {self.bone: {"rotation": self.setup_rot + angle}}

    def frame(self, angle, canvas=None):
        if canvas is not None:
            return self.r.frame(self.pose(angle), self.state, canvas=canvas)
        return self.r.frame(self.pose(angle), self.state, reuse=self.reuse)

    def judge(self, angle):
        """Every gate at one angle: {"soft", "hard", "soft_by", "hard_by",
        "limits", "gates"}."""
        fr = self.frame(angle)
        j = self.joint
        g = {"fold": ag.folds(fr, slots=self.moving, rest=self.rest),
             "hole": ag.holes(self.rest, fr),
             "seam": ag.seam_gap(self.rest, fr, j),
             "ghost_band": ag.ghost_band(self.rest, fr, j),
             "ghost_paint": ag.ghost_paint(self.rest, fr, visibility=True),
             "area": ag.band_area_ratio(self.rest, fr, j)}
        fails = []
        for name in SOFT_ORDER:
            for key in ([name] if name != "ghost" else ["ghost_band", "ghost_paint"]):
                if not g[key]["pass"]:
                    fails.append(_label(key, g[key], self.jr))
        hard_fails = [_label(n, g[n], self.jr) for n in HARD_ORDER if not g[n]["pass"]]
        return {"angle": angle, "soft": not fails, "hard": not hard_fails,
                "soft_by": fails[0] if fails else None, "hard_by": hard_fails[0] if hard_fails else None,
                "limits": fails, "gates": g, "frame": fr}


def _label(gate, res, jr):
    if gate == "area":
        # the band's summed area, or (G2's per-triangle range) one triangle
        return f"area@{jr.name}_band" if "band" in res.get("by", ["band"]) else f"tri@{res['worst']}"
    if gate == "seam":
        return f"seam@{res['worst'] or jr.name}"
    return f"{gate.split('_')[0]}@{res['worst']}"


# ---------------------------------------------------------------- sweeping

def _angles(step, max_angle):
    n = int(math.floor(max_angle / step + 1e-9))
    out = [round(k * step, 6) for k in range(1, n + 1)]
    if not out or out[-1] < max_angle - 1e-9:
        out.append(float(max_angle))
    return out


GATE_IDS = {"fold": "G1_FOLDS", "area": "G2_AREA", "seam": "G4_SEAM_GAP", "hole": "G5_HOLES",
            "ghost_band": "G6A_GHOST_BAND", "ghost_paint": "G6B_GHOST_PAINT"}


def sweep(probe, sign, step, max_angle, trace=None, carried=None):
    """Sweep in one direction until the hard limit fails: (soft, hard,
    soft_res, hard_res, first_fail); a *_res is the judgement at the first
    failing angle (None if none); first_fail {gate id: {"angle", "by",
    "value"}} is where each gate first failed (angles signed, absent if it
    never failed before the sweep stopped). Per-gate first failures are what
    a disagreement with a hand measurement is argued from.
    `carried` (a dict) collects the duplicates G6(b) found carried by an
    occluder (art_gates.ghost_paint), keyed (under, occluder): {"seen"
    (first angle G6(b) met it), "visible_from" (first angle from then on
    where the shard shows on >= NCC_MIN_PX px, or None), "max_visible_px",
    "components"}."""
    prev = 0.0
    soft = hard = None
    soft_res = hard_res = None
    first = {}
    need = ag.NCC_MIN_PX * probe.scale ** 2
    for a in _angles(step, max_angle):
        res = probe.judge(sign * a)
        if carried is not None:
            now = {(it["slot"], it["occluder"]): it for it in res["gates"]["ghost_paint"].get("carried", [])}
            for key, it in now.items():
                c = carried.setdefault(key, {"seen": sign * a, "visible_from": None, "max_visible_px": 0,
                                             "components": set()})
                c["components"].update(it["components"])
            for key, c in carried.items():
                # once a copy is known to travel with its layer, watch it at
                # every later angle, revealed with a high NCC or not
                vis = now[key]["visible_px"] if key in now else \
                    ag.carried_visible(probe.rest, res["frame"], key[0], key[1], sorted(c["components"]))
                c["max_visible_px"] = max(c["max_visible_px"], vis)
                if vis >= need and c["visible_from"] is None:
                    c["visible_from"] = sign * a
        for key, gid in GATE_IDS.items():
            g = res["gates"][key]
            if not g["pass"] and gid not in first:
                first[gid] = {"angle": sign * a, "by": _label(key, g, probe.jr), "value": g["value"]}
        if trace is not None:
            trace.append({"angle": sign * a, "soft": res["soft"], "hard": res["hard"], "by": res["soft_by"],
                          "values": {GATE_IDS[k]: _num(g["value"]) for k, g in res["gates"].items()}})
        if soft is None and not res["soft"]:
            soft, soft_res = prev, res
        if not res["hard"]:
            hard, hard_res = prev, res
            break
        prev = a
    if soft is None:
        soft = max_angle if hard is None else hard
    if hard is None:
        hard = max_angle
    return soft, hard, soft_res, hard_res, first


def _num(v):
    return round(float(v), 4) if isinstance(v, (int, float, np.floating, np.integer)) else v


def verify(probe, sign, angle, step, max_angle, which):
    """Move a coarse limit to where it holds at this probe's (full) scale:
    `which` passes at the angle and fails one step further. Returns (angle,
    judgement at the failing step or None, number of frames judged)."""
    def ok(a):
        if a <= 0:
            return True, None
        r = probe.judge(sign * a)
        return r[which], r
    n = 0
    a = angle
    good, _ = ok(a)
    n += 1
    while not good and a > 0:  # coarse was optimistic
        a = max(0.0, round(a - step, 6))
        good, _ = ok(a)
        n += 1
    while a < max_angle:       # coarse was pessimistic (or confirm the next step fails)
        nxt = min(max_angle, round(a + step, 6))
        good, res = ok(nxt)
        n += 1
        if not good:
            return a, res, n
        a = nxt
    return a, None, n


def scan_canvas(ir, images, max_angle=180.0, skin=None, margin=0.05):
    """A world canvas holding the setup pose AND every bone of the rig
    turned on its own to any angle up to max_angle (sampled every 45 deg,
    IK off as in the sweep), grown by `margin` and snapped outward to whole
    world units, so its origin is integer-aligned.

    WHY the whole rig, not the joints being scanned: the gates read pixels,
    and a pixel grid that moves moves the numbers. The canvas used to be
    the box of the scanned joints' sweeps, so `--only arms` rendered on a
    different (fractionally offset) grid than the full scan, and the same
    joint read differently (the joint monkey's shoulder_l -: 20 vs 18 deg,
    hip_l: 8/6 vs 10/26). Every bone, whatever is scanned, gives one
    canvas per rig and max_angle; the integer origin keeps any two such
    canvases on the same lattice.
    WHY the canvas is that big at all: the default renderer canvas is the
    setup box + 10%; an arm swung out of it would leave the frame, and G5
    counts a background component touching the canvas edge as open, so a
    hole the arm closes off-canvas would be missed."""
    r = raster.Renderer(ir, images, skin=skin, missing="skip")
    ev = r.ev
    state = {c["name"]: {"mix": 0.0} for c in ir["ik"]}
    pts = [r.setup_points(ev.world(None, state))]
    for b in ir["bones"]:
        if b["parent"] is None:
            continue
        for k in range(-4, 5):
            a = 45.0 * k
            if k and abs(a) <= max_angle + 1e-9:
                pts.append(r.setup_points(ev.world({b["name"]: {"rotation": b["rotation"] + a}}, state)))
    c = raster.bbox_canvas(np.concatenate(pts), margin)
    x0, y1 = math.floor(c.x0), math.ceil(c.y0)  # y up: the canvas origin is its top-left corner
    x1, y0 = math.ceil(c.x0 + c.w), math.floor(c.y0 - c.h)
    return raster.Canvas((float(x0), float(y1)), (float(x1 - x0), float(y1 - y0)), 1.0)


def scan(ir, images, joints, step=2.0, max_angle=180.0, scale=1.0, verify_full=True, skin=None, only=None,
         progress=None, keep_trace=False, canvas=None):
    """budget.json (dict) for `joints` (see load_joints for the entry form).

    scale        the sweep's render scale; 1.0 (default) sweeps at full
                 scale and needs no verification
    verify_full  with scale < 1, re-check every limit at scale 1 (default)
    canvas       world canvas (scale 1); default scan_canvas()
    """
    t0 = time.time()
    joints = load_joints(joints)
    if only:
        joints = [j for j in joints if j["name"] in only]
    canvas = canvas or scan_canvas(ir, images, max_angle, skin)  # the whole rig's, whatever `only` says
    coarse_cache, full_cache = {}, {}
    artreq = {}
    out = {"schema": SCHEMA, "joints": {}, "meta": {"step": step, "max_angle": max_angle, "scale": scale,
                                                    "verified": bool(verify_full and scale != 1.0),
                                                    "frames": {"coarse": 0, "full": 0}, "joints": {}}}
    for j in joints:
        if only and j["name"] not in only:
            continue
        coarse = Probe(ir, images, j, scale, skin, coarse_cache, canvas=canvas.scaled(scale))
        full = None
        if verify_full and scale != 1.0:
            full = Probe(ir, images, j, 1.0, skin, full_cache, canvas=canvas)
        res_j = {}
        trace = [] if keep_trace else None
        for d in j["dirs"]:
            sign = 1 if d > 0 else -1
            found = {}
            soft, hard, soft_res, hard_res, first = sweep(coarse, sign, step, max_angle, trace, found)
            for (u, o), c in found.items():
                _artreq(artreq, coarse.rest, u, o, c, f"{j['name']} {'+' if sign > 0 else '-'}")
            out["meta"]["frames"]["coarse"] += len(_angles(step, min(max_angle, hard + step)))
            coarse_soft, coarse_hard = soft, hard
            if full is not None:
                hard, r, n = verify(full, sign, hard, step, max_angle, "hard")
                hard_res = r or (hard_res if hard < max_angle else None)
                out["meta"]["frames"]["full"] += n
                soft, r, n = verify(full, sign, min(soft, hard), step, hard, "soft")
                out["meta"]["frames"]["full"] += n
                if r is not None:
                    soft_res = r
                elif soft >= hard:
                    soft_res = hard_res
            entry = {"soft": soft, "hard": hard,
                     "limit_by": soft_res["soft_by"] if soft_res else None,
                     "hard_by": hard_res["hard_by"] if hard_res else None,
                     "limits": soft_res["limits"] if soft_res else [],
                     "first_fail": first}
            if full is not None:
                entry["coarse"] = {"soft": coarse_soft, "hard": coarse_hard}
                entry["first_fail_scale"] = scale
            res_j["+" if sign > 0 else "-"] = entry
            if progress:
                progress(f"{j['name']} {'+' if sign > 0 else '-'}: soft {soft:g} hard {hard:g} "
                         f"({entry['limit_by']}; hard {entry['hard_by']})")
        out["joints"][j["name"]] = res_j
        jr = coarse.jr
        out["meta"]["joints"][j["name"]] = {
            "bone": j["bone"], "upper": jr.j["upper"], "helper": jr.j.get("helper"), "lower": jr.j["lower"],
            "center": jr.j["center"], "halfwidth": jr.hw, "upper_slots": jr.upper_slots,
            "lower_slots": jr.lower_slots, "moving_slots": sorted(coarse.moving), "band_px": int(jr.band.sum())}
        if keep_trace:
            out["meta"]["joints"][j["name"]]["trace"] = trace
    out["artreq"] = list(artreq.values())
    out["meta"]["canvas"] = canvas.to_dict()
    out["meta"]["seconds"] = round(time.time() - t0, 2)
    return out


def _artreq(items, rest, under, occluder, c, where):
    """Merge one sweep's carried duplicate into the art requests: one
    item per (occluder, under) pair, in the spinegen-artreq/1 item form
    (SOTA §3.2) plus where the scan met it."""
    key = (occluder, under)
    _, box = ag.carried_texels(rest, under, occluder, sorted(c["components"]))
    it = items.get(key)
    if it is None:
        g = rest.geoms.get(occluder)
        it = items[key] = {
            "id": f"clean_{occluder}__{under}", "kind": "clean", "layer": g.tex_key if g is not None else occluder,
            "slot": occluder, "region": list(box) if box else None,
            "what": f"paint of {under} cut into {occluder}",
            "why": f"G6(b): {occluder} carries a copy of {under}'s paint; it moves with {occluder} and shows "
                   f"wherever it leaves matching paint (not a joint limit: the copy is in the layer)",
            "seen": {}, "visible_from": {}, "max_visible_px": 0, "status": "open"}
    it["seen"][where] = c["seen"]
    it["visible_from"][where] = c["visible_from"]
    it["max_visible_px"] = max(it["max_visible_px"], int(c["max_visible_px"]))


# ---------------------------------------------------------------- legs in IK poses

IK_MOVES = ("crouch", "step", "lift")
IK_DEPTH_STEP = 0.01   # crouch: hips lowered in steps of this fraction of H
IK_DEPTH_MAX = 0.20
IK_SHIFTS = (-40.0, -20.0, 0.0, 20.0, 40.0)  # crouch: hips moved sideways, px (P2 review R2's grid)
IK_MOVE_STEP = 0.02    # step / lift: the foot target moves in steps of this fraction of H
IK_MOVE_MAX = 0.30
IK_REACH = 0.98        # a step lowers the hips until each leg spans at most this much of its length
IK_DISLOCATE = 1.0     # px: an ankle (shin tip) this far off its foot is a broken pose (hard)


def ik_legs(ir):
    """[{"name", "side", "thigh", "shin", "target", "foot", "hips"}] for each
    two-bone leg IK whose target carries a foot bone (rigbuild's legs)."""
    by = {b["name"]: b for b in ir["bones"]}
    out = []
    for c in ir.get("ik", []):
        if len(c["bones"]) != 2:
            continue
        thigh, shin = c["bones"]
        feet = [b["name"] for b in ir["bones"] if b["parent"] == c["target"]]
        out.append({"name": c["name"], "side": c["name"].rsplit("_", 1)[-1], "thigh": thigh, "shin": shin,
                    "target": c["target"], "foot": feet[0] if feet else None, "hips": by[thigh]["parent"]})
    return out


class LegProbe:
    """Renders and judges whole-leg poses with the leg IK ON (the poses a
    crouch, a step or a lifted foot actually make), on the legs' own slots
    plus the trunk the hips carry. ik_scan says why."""

    def __init__(self, ir, images, joints, legs, skin=None, canvas=None, H=None):
        self.ir, self.legs = ir, legs
        by = {b["name"]: b for b in ir["bones"]}
        self.by = by
        r0 = raster.Renderer(ir, images, skin=skin, canvas=canvas)
        ev = r0.ev
        self.ev = ev
        leg_bones = set()
        for leg in legs:
            for root in (leg["thigh"], leg["target"]):
                leg_bones |= _subtree_names(ir, root)
        hips = {leg["hips"] for leg in legs}
        trunk = set(hips) | {b["name"] for b in ir["bones"] if b["parent"] in hips and b["name"] not in leg_bones}
        # the legs' slots and the trunk on the hips (the hip seam's upper
        # part); arms, head and props are left out: a hand hanging by a
        # crouching thigh encloses background that is no leg's hole (P2 R2)
        self.slots = [s["name"] for s in ir["slots"] if s["bone"] in leg_bones | trunk]
        self.r = r0
        self.rest = r0.frame(slots=self.slots)
        self.H = float(H or ir.get("H") or 1.0)
        names = {leg[k] for leg in legs for k in ("thigh", "shin", "foot") if leg[k]}
        self.joints = [j for j in joints if j["bone"] in names]
        self.jrs = {j["name"]: ag.joint_rest(self.rest, j) for j in self.joints}
        self.world0 = ev.world_dict(ev.world())
        self.length = {leg["name"]: _dist(self.world0[leg["thigh"]], self.world0[leg["shin"]])
                       + float(by[leg["shin"]].get("length") or 0.0) for leg in legs}

    def pose_of(self, move, leg=None, amount=0.0, dx=0.0):
        """Absolute local values: crouch (hips down `amount` px, sideways
        dx), step (leg's target sideways `amount` px, the hips half way
        and lowered until both legs reach), lift (leg's target up `amount`)."""
        b = self.by
        hips = self.legs[0]["hips"]
        hx, hy = b[hips]["x"], b[hips]["y"]
        pose = {}
        if move == "crouch":
            pose[hips] = {"x": hx + dx, "y": hy - amount}
        elif move == "step":
            t = b[leg["target"]]
            pose[leg["target"]] = {"x": t["x"] + amount, "y": t["y"]}
            sx = 0.5 * amount
            pose[hips] = {"x": hx + sx, "y": hy - self._lower_for_reach(leg, amount, sx)}
        elif move == "lift":
            t = b[leg["target"]]
            pose[leg["target"]] = {"x": t["x"], "y": t["y"] + amount}
        else:
            raise ValueError(f"move must be one of {IK_MOVES}")
        return pose

    def _lower_for_reach(self, moved, amount, sx):
        """How far the hips must drop (moved sideways by sx) for every leg
        to span at most IK_REACH of its length (`moved` leg's target moved
        `amount` px in world x; root-parented targets assumed)."""
        need = 0.0
        for leg in self.legs:
            hip = np.asarray(self.world0[leg["thigh"]][4:6], float) + [sx, 0.0]
            ank = np.asarray(self.world0[leg["target"]][4:6], float)
            if leg is moved:
                ank = ank + [amount, 0.0]
            r = IK_REACH * self.length[leg["name"]]
            dxl = abs(ank[0] - hip[0])
            if dxl >= r:
                return float("inf")
            need = max(need, (hip[1] - ank[1]) - math.sqrt(r * r - dxl * dxl))
        return max(0.0, need)

    def judge(self, pose):
        """Gates on one pose: {"soft", "hard" (the whole pose), "soft_by",
        "hard_by", "limits", "joints": {joint: {"soft": [labels], "hard":
        [labels]}} (the gates that name a leg joint: its G2/G4/G6a, and G1
        folds nearest it), "global": {"soft", "hard"} (G5 holes and G6b paint
        ghosts, which name no joint), "valid" (every ankle within
        IK_DISLOCATE px of its foot: a pose the IK can make), "turns" {joint:
        deg}, "dislocated_px", "values"}. soft = G1 G2 G4 G5 G6; hard = G1 G4
        G5 (budget's theta_hard)."""
        if any(not np.all(np.isfinite([v for v in p.values()])) for p in pose.values()):
            return None
        world = self.ev.world(pose)
        fr = self.r.frame(world=world, slots=self.slots)
        g = {"fold": ag.folds(fr, slots=self.slots, rest=self.rest), "hole": ag.holes(self.rest, fr),
             "ghost_paint": ag.ghost_paint(self.rest, fr, visibility=True)}
        per = {j["name"]: {"soft": [], "hard": []} for j in self.joints}
        glob = {"soft": [], "hard": []}
        if not g["fold"]["pass"]:
            for n, slots in self._fold_joints(fr).items():
                lab = f"fold@{max(slots, key=slots.get)}"
                per[n]["soft"].append(lab)
                per[n]["hard"].append(lab)
        if not g["hole"]["pass"]:
            glob["soft"].append(_label("hole", g["hole"], None))
            glob["hard"].append(glob["soft"][-1])
        if not g["ghost_paint"]["pass"]:
            glob["soft"].append(_label("ghost_paint", g["ghost_paint"], None))
        for j in self.joints:
            jr = self.jrs[j["name"]]
            for key, fn in (("seam", ag.seam_gap), ("ghost_band", ag.ghost_band), ("area", ag.band_area_ratio)):
                res = fn(self.rest, fr, j)
                g[f"{key}:{j['name']}"] = res
                if not res["pass"]:
                    lab = _label(key, res, jr)
                    per[j["name"]]["soft"].append(lab)
                    if key == "seam":
                        per[j["name"]]["hard"].append(lab)
        wd = self.ev.world_dict(world)
        disl = 0.0
        for leg in self.legs:
            if leg["foot"]:
                tip = _tip(wd[leg["shin"]], float(self.by[leg["shin"]].get("length") or 0.0))
                disl = max(disl, _dist((0, 0, 0, 0, *tip), wd[leg["foot"]]))
        fails = [f"{x}#{n}" for n, e in per.items() for x in e["soft"]] + glob["soft"]
        hard = [f"{x}#{n}" for n, e in per.items() for x in e["hard"]] + glob["hard"]
        return {"soft": not fails, "hard": not hard, "soft_by": fails[0] if fails else None,
                "hard_by": hard[0] if hard else None, "limits": fails, "joints": per, "global": glob,
                "valid": disl <= IK_DISLOCATE, "turns": self.turns(wd), "dislocated_px": round(disl, 3),
                "values": {k: _num(v["value"]) for k, v in g.items()}}

    def _fold_joints(self, fr):
        """{joint: {slot: folded triangles}}: each folded triangle goes to the
        nearest joint (centre now) among the joints whose parts hold its slot."""
        refs = self.ev.fold_references()
        cen = {n: np.asarray(ag.center_now(self.rest, fr, jr.j), float) for n, jr in self.jrs.items()}
        out = {}
        for sname, g in fr.geoms.items():
            if g.att["type"] not in ("mesh", "linkedmesh"):
                continue
            m = lbs.fold_mask(g.xy, g.uv, g.tris, reference=refs.get((sname, id(g.att)), -1))
            g0 = self.rest.geoms.get(sname)
            if g0 is not None:
                m &= ~lbs.fold_mask(g0.xy, g0.uv, g0.tris, reference=refs.get((sname, id(g.att)), -1))
            # the joints this slot is a part of (upper or lower), nearest first
            mine = [n for n, jr in self.jrs.items() if sname in jr.upper_slots or sname in jr.lower_slots] or list(cen)
            for t in np.asarray(g.tris)[m]:
                c = g.xy[t].mean(0)
                n = min(mine, key=lambda n: float(np.linalg.norm(cen[n] - c)))
                out.setdefault(n, {}).setdefault(sname, 0)
                out[n][sname] += 1
        return out

    def turns(self, wd):
        """{joint name: deg} each leg joint's turn from setup (its bone's
        world angle relative to its upper bone's, minus the same at setup)."""
        out = {}
        for j in self.joints:
            jr = self.jrs[j["name"]]
            lo, up = jr.j["lower"], jr.j["upper"]
            a = _ang(wd[lo]) - _ang(wd[up]) - (_ang(self.world0[lo]) - _ang(self.world0[up]))
            out[j["name"]] = round((a + 180.0) % 360.0 - 180.0, 2)
        return out


def _subtree_names(ir, root):
    kids = {}
    for b in ir["bones"]:
        kids.setdefault(b["parent"], []).append(b["name"])
    out, todo = set(), [root]
    while todo:
        n = todo.pop()
        out.add(n)
        todo += kids.get(n, [])
    return out


def _ang(m):
    return math.degrees(math.atan2(m[2], m[0]))


def _tip(m, length):
    return m[4] + m[0] * length, m[5] + m[2] * length


def _dist(m1, m2):
    return float(math.hypot(m1[4] - m2[4], m1[5] - m2[5]))


def ik_sweep(probe, make, amounts):
    """Judge every amount (increasing): {"poses": [{"amount", "valid",
    "soft", "hard", "by", "joints", "global", "turns", "values"}],
    "soft", "hard" (the last amount up to which every valid pose held, the
    whole pose), "soft_by", "hard_by", "per_joint": {joint: {"soft",
    "hard", "soft_by", "hard_by"}} (the same, that joint's own gates),
    "first_valid"}. Poses the IK cannot make (an ankle off its foot) are
    listed and skipped: a crouch shifted 40 px is only reachable once the
    hips are low enough."""
    poses = []
    for a in amounts:
        res = probe.judge(make(a))
        if res is None:
            poses.append({"amount": round(a, 3), "valid": False})
            continue
        poses.append({"amount": round(a, 3), "valid": res["valid"], "soft": res["soft"], "hard": res["hard"],
                      "by": res["soft_by"], "hard_by": res["hard_by"], "joints": res["joints"],
                      "global": res["global"], "turns": res["turns"], "dislocated_px": res["dislocated_px"],
                      "values": res["values"]})
    valid = [p for p in poses if p["valid"]]

    def limit(ok, by):
        last, first = 0.0, None
        for p in valid:
            if not ok(p):
                first = p
                break
            last = p["amount"]
        return last, (by(first) if first else None)
    out = {"poses": poses, "first_valid": valid[0]["amount"] if valid else None}
    out["soft"], out["soft_by"] = limit(lambda p: p["soft"], lambda p: p["by"])
    out["hard"], out["hard_by"] = limit(lambda p: p["hard"], lambda p: p["hard_by"])
    out["per_joint"] = {}
    for n in probe.jrs:
        e = {}
        for kind in ("soft", "hard"):
            e[kind], e[f"{kind}_by"] = limit(lambda p: not p["joints"][n][kind],
                                             lambda p: p["joints"][n][kind][0])
        out["per_joint"][n] = e
    return out


def ik_scan(ir, images, joints, skin=None, canvas=None, shifts=IK_SHIFTS, depth_step=IK_DEPTH_STEP,
            depth_max=IK_DEPTH_MAX, move_step=IK_MOVE_STEP, move_max=IK_MOVE_MAX, progress=None, keep_trace=False):
    """The legs' budget in the poses legs make: {"moves": {...}, "joints":
    {...}, "meta": {...}} (budget.json "ik"), or None without leg IK.

    WHY (P2 open issue 6): the per-joint sweep turns ONE bone with IK off,
    so turning a thigh or shin while the foot stays on its IK target pulls
    the ankle out of the boot: the hip / knee / ankle numbers it gives are
    for poses no leg ever makes. Legs move through their IK: a crouch (the
    hips down, optionally shifted sideways: one leg takes the weight), a
    step (one foot target sideways, the hips half way and down so both legs
    reach) and a lifted foot (one target up, the knee folds). Each is
    walked in steps of H to its maximum, judged on the leg slots and the
    trunk (LegProbe) with every leg joint's G2/G4/G6a, G1 (blamed on the
    nearest joint), G5 and G6b; a pose whose ankle leaves its foot by >
    IK_DISLOCATE px is one the IK cannot make and is skipped. Amounts are
    px (and *_H: fractions of H): crouch depth, step and lift distance.
    The walk does not stop at the first failure: one joint failing (the
    monkey's crotch seam at 0.02 H) must not hide where the others fail.

    "moves" {name: {"soft", "hard" (the whole pose holds up to), "per_joint"
    {joint: {"soft", "hard", "soft_by", "hard_by"}}, ...}}.
    "joints" {joint: {"+"|"-": {"soft", "soft_move", "soft_by",
    "soft_fails_at", "soft_fails_move", and the same for hard}}}: per
    direction of the joint's turn from setup (+ counter-clockwise, as
    budget's sweeps), the turn the joint reached in poses up to its own
    soft (hard) limit in some move (soft_move), capped by the smallest turn
    at which a move's first pose past that limit failed it (soft_fails_at,
    in soft_fails_move, by gate soft_by). This is the joint's usable limit
    in IK: every scanned pose that turned it less held."""
    legs = ik_legs(ir)
    if not legs:
        return None
    t0 = time.time()
    joints = load_joints(joints)
    probe = LegProbe(ir, images, joints, legs, skin, canvas)
    H = probe.H
    moves = {}

    def steps(step, mx):
        n = int(round(mx / step))
        return [round(k * step * H, 6) for k in range(1, n + 1)]

    def run(key, make, amounts, **extra):
        m = ik_sweep(probe, make, amounts)
        m.update(extra)
        m["soft_H"], m["hard_H"] = round(m["soft"] / H, 4), round(m["hard"] / H, 4)
        for e in m["per_joint"].values():
            e["soft_H"], e["hard_H"] = round(e["soft"] / H, 4), round(e["hard"] / H, 4)
        moves[key] = m
        if progress:
            pj = ", ".join(f"{n} {e['soft_H']:g}/{e['hard_H']:g}" for n, e in m["per_joint"].items())
            progress(f"{key}: soft {m['soft_H']:g}H hard {m['hard_H']:g}H ({m['soft_by']}; hard {m['hard_by']}); "
                     f"per joint soft/hard H: {pj}")

    for dx in shifts:
        run(f"crouch dx{dx:+g}", lambda a, dx=dx: probe.pose_of("crouch", amount=a, dx=dx),
            steps(depth_step, depth_max), move="crouch", dx=dx)
    for leg in legs:
        for sign in (1, -1):
            run(f"step {leg['side']}{'+' if sign > 0 else '-'}",
                lambda a, leg=leg, sign=sign: probe.pose_of("step", leg, sign * a), steps(move_step, move_max),
                move="step", leg=leg["name"], dir=sign)
        run(f"lift {leg['side']}", lambda a, leg=leg: probe.pose_of("lift", leg, a), steps(move_step, move_max),
            move="lift", leg=leg["name"])
    out_j = {}
    for n in probe.jrs:
        e = {}
        for sign, key in ((1, "+"), (-1, "-")):
            d = {}
            for kind in ("soft", "hard"):
                reach, rmove, fail = 0.0, None, None
                for mk, m in moves.items():
                    lim = m["per_joint"][n][kind]
                    for p in m["poses"]:
                        if not p["valid"]:
                            continue
                        t = p["turns"][n] * sign
                        if p["amount"] <= lim + 1e-9:
                            if t > reach:
                                reach, rmove = t, mk
                        elif p["joints"][n][kind]:
                            # the first pose of this move past the joint's limit: it failed here
                            if t > 0 and (fail is None or t < fail[0]):
                                fail = (t, mk, p["joints"][n][kind][0])
                            break
                d[kind] = round(min(reach, fail[0]) if fail else reach, 2)
                d[f"{kind}_move"] = (fail[1] if fail and fail[0] <= reach else rmove)
                d[f"{kind}_by"] = fail[2] if fail else None
                d[f"{kind}_fails_at"] = round(fail[0], 2) if fail else None
                d[f"{kind}_fails_move"] = fail[1] if fail else None
            e[key] = d
        out_j[n] = e
    if not keep_trace:
        for m in moves.values():
            for p in m["poses"]:
                p.pop("values", None)
    return {"moves": moves, "joints": out_j,
            "meta": {"H": H, "legs": [l["name"] for l in legs], "slots": probe.slots,
                     "depth_step_H": depth_step, "move_step_H": move_step, "shifts_px": list(shifts),
                     "reach": IK_REACH, "dislocate_px": IK_DISLOCATE, "seconds": round(time.time() - t0, 2)}}


# ---------------------------------------------------------------- contact sheet

OVERLAY = {"seam": (255, 0, 255), "ghost_band": (255, 200, 0), "ghost_paint": (0, 200, 255), "hole": (255, 0, 0)}


def contact_sheet(ir, images, joints, budget=None, every=SHEET_EVERY, max_angle=90.0, thumb=180, skin=None,
                  only=None, canvas=None):
    """(H, W, 4) uint8 contact sheet: one row per joint and direction, one
    column per `every` degrees from 0 to max_angle. Each tile stacks
      * the limb (a fixed window around the joint that holds the moving
        slots at any angle), box-filtered down: how the pose reads;
      * the joint close-up, pixel for pixel (nearest-neighbour up): what
        the gates read, at the renderer's own 1:1 pixels;
      * a 1:1 close-up centred on the first failing pixel gate's pixels
        (the joint when none fails; G6b's duplicate can sit far from the
        joint), with every failing gate painted over: magenta G4 seam gap,
        yellow G6a exposed band, cyan G6b revealed duplicate paint, red G5
        hole (+ G1/G2 values in the caption).
    Frames are green while the soft limit holds, amber to the hard limit,
    red past it (grey without a budget). The point of the sheet is to LOOK
    at the first failing angle and check it is visibly broken."""
    from PIL import Image, ImageDraw
    joints = load_joints(joints)
    cache = {}
    angles = [0.0] + _angles(every, max_angle)
    label_w, pad, cap = 230, 3, 26
    rows = []
    for j in joints:
        if only and j["name"] not in only:
            continue
        p = Probe(ir, images, j, 1.0, skin, cache, canvas=canvas)
        reach = _reach(p)
        c = np.asarray(p.jr.j["center"], float)
        limb_cv = raster.Canvas((c[0] - reach, c[1] + reach), (2 * reach, 2 * reach), 1.0)
        half = max(1.4 * p.jr.radius, 24.0)
        for d in j["dirs"]:
            sign = 1 if d > 0 else -1
            key = "+" if sign > 0 else "-"
            lim = (budget or {}).get("joints", {}).get(j["name"], {}).get(key)
            tiles = []
            for a in angles:
                fr = p.frame(sign * a)
                res = p.judge(sign * a) if a else None
                limb = p.r.frame(p.pose(sign * a), p.state, canvas=limb_cv).rgba()
                limb = Image.fromarray(_on_white(limb)).resize((thumb, thumb), Image.BOX)
                cn = ag.center_now(p.rest, fr, p.jr.j)
                raw = _window(fr, cn, half)
                masks, notes = _failures(p, fr, res) if res is not None else ({}, [])
                focus, where = cn, "joint"
                for k in ("seam", "hole", "ghost_band", "ghost_paint"):
                    if k in masks and masks[k].any():
                        ii, jj = np.nonzero(masks[k])
                        focus = fr.canvas.to_world([[jj.mean() + 0.5, ii.mean() + 0.5]])[0]
                        where = k
                        break
                over = _window(fr, focus, half)
                _paint(fr, masks, focus, half, over)
                big = [Image.fromarray(x).resize((thumb, thumb), Image.NEAREST) for x in (raw, over)]
                if where != "joint":
                    ImageDraw.Draw(big[1]).text((3, 2), f"at {where}", fill=(0, 0, 0))
                col = (150, 150, 150)
                if lim:
                    col = (40, 160, 60) if a <= lim["soft"] else (230, 150, 20) if a <= lim["hard"] else (210, 40, 40)
                t = Image.new("RGB", (thumb + 2 * pad, 3 * thumb + 4 * pad + cap), col)
                for k, im in enumerate((limb, big[0], big[1])):
                    t.paste(im, (pad, pad + k * (thumb + pad)))
                txt = f"{key}{a:g}" + (("  " + " ".join(notes)) if notes else "")
                ImageDraw.Draw(t).text((pad + 2, 3 * thumb + 3 * pad + 2), txt[:34], fill=(255, 255, 255))
                tiles.append(t)
            text = f"{j['name']} {key}\nbone {j['bone']}\nhalfwidth {p.jr.hw:.1f}"
            if lim:
                text += (f"\nsoft {lim['soft']:g}  hard {lim['hard']:g}\n{lim['limit_by']}\nhard: {lim['hard_by']}")
                for gid, ff in sorted((lim.get("first_fail") or {}).items(), key=lambda kv: abs(kv[1]["angle"])):
                    text += f"\n{gid} {ff['angle']:+g}"
            text += ("\n\nrows: limb / joint 1:1 /\nfirst failure 1:1, painted:\nmagenta G4 seam, yellow G6a,\n"
                     "cyan G6b, red G5")
            lab = Image.new("RGB", (label_w, tiles[0].height), (255, 255, 255))
            ImageDraw.Draw(lab).multiline_text((6, 6), text, fill=(0, 0, 0))
            row = Image.new("RGB", (label_w + sum(t.width for t in tiles), tiles[0].height), (255, 255, 255))
            row.paste(lab, (0, 0))
            x = label_w
            for t in tiles:
                row.paste(t, (x, 0))
                x += t.width
            rows.append(row)
    if not rows:
        return np.zeros((1, 1, 4), np.uint8)
    W = max(r.width for r in rows)
    sheet = Image.new("RGB", (W, sum(r.height for r in rows)), (255, 255, 255))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    return np.asarray(sheet.convert("RGBA"))


def _window(frame, center, half):
    """White-backed RGB (n, n, 3) of the frame's pixels in a square of
    half-size `half` world units around `center` (off-canvas is white)."""
    c = frame.canvas
    cx, cy = c.to_px([center])[0]
    r = int(math.ceil(half * c.scale))
    i0, j0 = int(math.floor(cy)) - r, int(math.floor(cx)) - r
    n = 2 * r + 1
    out = np.full((n, n, 3), 255, np.uint8)
    rgb = _on_white(frame.rgba())
    si0, sj0 = max(0, i0), max(0, j0)
    si1, sj1 = min(c.H, i0 + n), min(c.W, j0 + n)
    if si1 > si0 and sj1 > sj0:
        out[si0 - i0:si1 - i0, sj0 - j0:sj1 - j0] = rgb[si0:si1, sj0:sj1]
    return out


def _failures(probe, frame, res):
    """({gate: full-canvas bool mask}, caption notes) of the failing gates."""
    H, W = frame.canvas.shape
    g = res["gates"]
    masks, notes = {}, []
    if not g["seam"]["pass"]:
        m = ag.seam_gap(probe.rest, frame, probe.joint, masks=True)["masks"]
        masks["seam"] = _unbox(m["gap"], m["box"], (H, W))
        notes.append(f"G4:{g['seam']['value']}px")
    if not g["ghost_band"]["pass"]:
        m = ag.ghost_band(probe.rest, frame, probe.joint, masks=True)["masks"]
        masks["ghost_band"] = _unbox(m["ghost"], m["box"], (H, W))
        notes.append(f"G6a:{g['ghost_band']['value']:.0f}")
    if not g["ghost_paint"]["pass"]:
        masks["ghost_paint"] = ag.ghost_paint(probe.rest, frame, masks=True)["masks"]["revealed"]
        notes.append(f"G6b:{g['ghost_paint']['value']:.2f}")
    if not g["hole"]["pass"]:
        masks["hole"] = ag.holes(probe.rest, frame, masks=True)["masks"]["holes"]
        notes.append(f"G5:{g['hole']['value']}")
    if not g["fold"]["pass"]:
        notes.append(f"G1:{g['fold']['value']}")
    if not g["area"]["pass"]:
        bad = g["area"].get("tri_out_of_range") if "tris" in g["area"].get("by", []) else 0
        notes.append(f"G2:{g['area']['value']:.2f}" + (f"/{bad}t" if bad else ""))
    return masks, notes


def _paint(frame, masks, center, half, img):
    """Paint full-canvas gate masks into the window `img` (as cut by
    _window around `center`), in place."""
    c = frame.canvas
    H, W = c.shape
    cx, cy = c.to_px([center])[0]
    r = int(math.ceil(half * c.scale))
    i0, j0 = int(math.floor(cy)) - r, int(math.floor(cx)) - r
    n = img.shape[0]
    for key in ("ghost_paint", "ghost_band", "hole", "seam"):
        m = masks.get(key)
        if m is None:
            continue
        win = np.zeros((n, n), bool)
        si0, sj0 = max(0, i0), max(0, j0)
        si1, sj1 = min(H, i0 + n), min(W, j0 + n)
        if si1 > si0 and sj1 > sj0:
            win[si0 - i0:si1 - i0, sj0 - j0:sj1 - j0] = m[si0:si1, sj0:sj1]
        col = np.array(OVERLAY[key], float)
        if key == "seam":
            img[win] = col.astype(np.uint8)
        else:
            img[win] = np.clip(img[win] * 0.45 + col * 0.55, 0, 255).astype(np.uint8)


def _unbox(mask, box, shape):
    out = np.zeros(shape, bool)
    i0, i1, j0, j1 = box
    out[i0:i1, j0:j1] = mask
    return out


def _reach(probe):
    """Half-size of a square around the joint that holds the moving slots
    at any angle (their farthest setup vertex) and some context."""
    c = np.asarray(probe.jr.j["center"])
    far = probe.jr.hw * 2
    for s in probe.moving:
        g = probe.rest.geoms[s]
        far = max(far, float(np.sqrt(((g.xy - c) ** 2).sum(1)).max()))
    return far * 1.05 + 4


def _on_white(rgba):
    a = rgba[..., 3:4].astype(float) / 255
    rgb = rgba[..., :3] * a + 255 * (1 - a)
    return np.clip(np.round(rgb), 0, 255).astype(np.uint8)


def write_sheets(path, ir, images, joints, budget=None, max_angle=90.0, every=SHEET_EVERY, skin=None, only=None,
                 canvas=None):
    """`path` ending in .png: one sheet for all joints; otherwise a
    directory with <joint>.png per joint (small enough to look at)."""
    joints = load_joints(joints)
    if str(path).lower().endswith(".png"):
        raster.save_png(path, contact_sheet(ir, images, joints, budget, every, max_angle, skin=skin, only=only,
                                            canvas=canvas))
        return [path]
    out = []
    for j in joints:
        if only and j["name"] not in only:
            continue
        f = os.path.join(path, f"{j['name']}.png")
        raster.save_png(f, contact_sheet(ir, images, [j], budget, every, max_angle, skin=skin, canvas=canvas))
        out.append(f)
    return out


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig", help="rig.json (spinegen-rig/1) or a Spine 4.x skeleton JSON")
    ap.add_argument("images", help="directory of attachment PNGs or a .atlas")
    ap.add_argument("--joints", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--sheet", default=None,
                    help="contact sheet: a .png for all joints, else a directory of <joint>.png")
    ap.add_argument("--sheet-max", type=float, default=90.0, help="last angle on the sheet (deg)")
    ap.add_argument("--sheet-every", type=float, default=SHEET_EVERY)
    ap.add_argument("--step", type=float, default=2.0)
    ap.add_argument("--max-angle", type=float, default=180.0)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="sweep render scale; < 1 is faster and re-checks the limits at 1")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated joint names")
    ap.add_argument("--skin", default=None)
    ap.add_argument("--trace", action="store_true", help="keep every judged angle in meta")
    ap.add_argument("--artreq", default=None, help="also write the art requests as spinegen-artreq/1 here")
    ap.add_argument("--no-ik-poses", action="store_true",
                    help="skip the legs' IK-pose scan (ik_scan: crouch, step, lifted foot) budget.json \"ik\"")
    a = ap.parse_args(argv)
    ir = raster.load_rig(a.rig)
    images = raster.load_images(a.images)
    joints = load_joints(a.joints)
    only = set(a.only.split(",")) if a.only else None
    budget = scan(ir, images, joints, a.step, a.max_angle, a.scale, not a.no_verify, a.skin, only,
                  progress=lambda m: print(m, file=sys.stderr), keep_trace=a.trace)
    legs = {n for leg in ik_legs(ir) for n in (leg["thigh"], leg["shin"], leg["foot"])}
    if not a.no_ik_poses and any(j["bone"] in legs and (not only or j["name"] in only) for j in joints):
        canvas = raster.Canvas(budget["meta"]["canvas"]["origin"], budget["meta"]["canvas"]["size"], 1.0)
        budget["ik"] = ik_scan(ir, images, joints, a.skin, canvas, progress=lambda m: print(m, file=sys.stderr),
                               keep_trace=a.trace)
        for n, e in budget["ik"]["joints"].items():
            print(f"{n} in IK poses: " + "; ".join(
                f"{k} soft {v['soft']:g} ({v['soft_move']}; {v['soft_by']}) hard {v['hard']:g}"
                for k, v in e.items()), file=sys.stderr)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(budget, f, indent=1)
    if a.artreq and budget["artreq"]:
        with open(a.artreq, "w") as f:
            json.dump({"schema": "spinegen-artreq/1", "items": budget["artreq"]}, f, indent=1)
        print(f"{a.artreq}: {len(budget['artreq'])} art request(s)", file=sys.stderr)
    print(f"{a.out}: {len(budget['joints'])} joint(s) in {budget['meta']['seconds']} s", file=sys.stderr)
    if a.sheet:
        canvas = raster.Canvas(budget["meta"]["canvas"]["origin"], budget["meta"]["canvas"]["size"], 1.0)
        for f in write_sheets(a.sheet, ir, images, joints, budget, min(a.sheet_max, a.max_angle), a.sheet_every,
                              a.skin, only, canvas):
            print(f"{f}: contact sheet", file=sys.stderr)


if __name__ == "__main__":
    main()
