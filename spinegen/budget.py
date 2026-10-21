"""Per-joint art budget: how far each joint turns before the art breaks
(SOTA §4.3; budget.json format §3.2).

    python -m spinegen.budget <rig.json | skeleton.json> <images dir | .atlas> --joints joints.json
        -o budget.json [--sheet sheet.png] [--step 2] [--max-angle 180] [--scale 0.5]
        [--no-verify] [--only name,name] [--skin name]

WHY
  "This arm holds 26 degrees" was measured on the monkey by eye, 10 degrees
  at a time (its README: 25 fine, 30 stiff, 40 leaves the vest, 50 a plank).
  Motion synthesis needs that number per joint and per direction, for every
  character, with the reason, so a motion that wants more knows what to
  ask for (redistribute, transit only, draw order, swap, deform, art
  request; §4.3). This module turns the eye into gates:
    theta_soft  the last angle with 0 folds, joint-band area >= 0.9, 0 seam
                gap px, 0 holes, 0 ghost (G1 G2 G4 G5 G6): holds may go here;
    theta_hard  the last angle with 0 folds and 0 holes (G1 G5): strikes and
                transits may pass through, never stop.
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
  Speed: slots the bone does not move are rasterised once and reused; the
  sweep runs at `scale` (default 0.5, pixel thresholds scaled with it) and
  stops at the first hard failure; then both limits are VERIFIED at full
  scale (the angle must pass and the next step must fail at scale 1; if not,
  the limit is walked to where that holds), because a coarse pixel can hide
  a 1 px tear.

OUTPUT (budget.json)
  {"schema": "spinegen-budget/1",
   "joints": {name: {"+": {"soft", "hard", "limit_by", "hard_by", "limits"},
                     "-": {...}}},
   "meta": {...}}
  "+" is a positive local rotation of the bone (counter-clockwise, y up).
  limit_by is "<gate>@<where>" for the soft limit (fold@slot, hole@slot,
  seam@upper|lower, ghost@slot, area@<joint>_band); hard_by the same for the
  hard limit; limits lists every soft gate that failed at the first failing
  angle. A limit that never fails within max_angle is max_angle with
  limit_by null.

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
from . import raster

SCHEMA = "spinegen-budget/1"
SHEET_EVERY = 10.0
SOFT_ORDER = ("fold", "hole", "seam", "ghost", "area")  # limit_by priority when several fail at once


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
        for k in ("center", "upper", "helper", "lower", "halfwidth", "axis", "extent", "slots"):
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
             "ghost_paint": ag.ghost_paint(self.rest, fr),
             "area": ag.band_area_ratio(self.rest, fr, j)}
        fails = []
        for name in SOFT_ORDER:
            for key in ([name] if name != "ghost" else ["ghost_band", "ghost_paint"]):
                if not g[key]["pass"]:
                    fails.append(_label(name, g[key], self.jr))
        hard_fails = [_label(n, g[n], self.jr) for n in ("fold", "hole") if not g[n]["pass"]]
        return {"angle": angle, "soft": not fails, "hard": not hard_fails,
                "soft_by": fails[0] if fails else None, "hard_by": hard_fails[0] if hard_fails else None,
                "limits": fails, "gates": g}


def _label(gate, res, jr):
    if gate == "area":
        return f"area@{jr.name}_band"
    if gate == "seam":
        return f"seam@{res['worst'] or jr.name}"
    return f"{gate}@{res['worst']}"


# ---------------------------------------------------------------- sweeping

def _angles(step, max_angle):
    n = int(math.floor(max_angle / step + 1e-9))
    out = [round(k * step, 6) for k in range(1, n + 1)]
    if not out or out[-1] < max_angle - 1e-9:
        out.append(float(max_angle))
    return out


def sweep(probe, sign, step, max_angle, trace=None):
    """Coarse sweep in one direction: (soft, hard, soft_res, hard_res); a
    *_res is the judgement at the first failing angle (None if none)."""
    prev = 0.0
    soft = hard = None
    soft_res = hard_res = None
    for a in _angles(step, max_angle):
        res = probe.judge(sign * a)
        if trace is not None:
            trace.append({"angle": sign * a, "soft": res["soft"], "hard": res["hard"], "by": res["soft_by"]})
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
    return soft, hard, soft_res, hard_res


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


def scan(ir, images, joints, step=2.0, max_angle=180.0, scale=0.5, verify_full=True, skin=None, only=None,
         progress=None, keep_trace=False):
    """budget.json (dict) for `joints` (see load_joints for the entry form).

    scale        the sweep's render scale; 1.0 sweeps at full scale and
                 skips verification
    verify_full  re-check every limit at scale 1 (default)
    """
    t0 = time.time()
    joints = load_joints(joints)
    coarse_cache, full_cache = {}, {}
    out = {"schema": SCHEMA, "joints": {}, "meta": {"step": step, "max_angle": max_angle, "scale": scale,
                                                    "verified": bool(verify_full and scale != 1.0),
                                                    "frames": {"coarse": 0, "full": 0}, "joints": {}}}
    for j in joints:
        if only and j["name"] not in only:
            continue
        coarse = Probe(ir, images, j, scale, skin, coarse_cache)
        full = None
        if verify_full and scale != 1.0:
            full = Probe(ir, images, j, 1.0, skin, full_cache)
        res_j = {}
        trace = [] if keep_trace else None
        for d in j["dirs"]:
            sign = 1 if d > 0 else -1
            soft, hard, soft_res, hard_res = sweep(coarse, sign, step, max_angle, trace)
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
                     "limits": soft_res["limits"] if soft_res else []}
            if full is not None:
                entry["coarse"] = {"soft": coarse_soft, "hard": coarse_hard}
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
    out["meta"]["seconds"] = round(time.time() - t0, 2)
    return out


# ---------------------------------------------------------------- contact sheet

def contact_sheet(ir, images, joints, budget=None, every=SHEET_EVERY, max_angle=180.0, thumb=160, skin=None,
                  only=None):
    """(H, W, 4) uint8: one row per joint and direction, one thumbnail per
    `every` degrees from 0 to max_angle, framed green (soft holds), amber
    (hard only) or red (past hard), using `budget` when given."""
    from PIL import Image, ImageDraw
    joints = load_joints(joints)
    cache = {}
    angles = [0.0] + _angles(every, max_angle)
    label_w, pad = 230, 4
    rows = []
    for j in joints:
        if only and j["name"] not in only:
            continue
        p = Probe(ir, images, j, 1.0, skin, cache)
        reach = _reach(p, j)
        c = p.jr.j["center"]
        cv = raster.Canvas((c[0] - reach, c[1] + reach), (2 * reach, 2 * reach), thumb / (2 * reach))
        for d in j["dirs"]:
            sign = 1 if d > 0 else -1
            key = "+" if sign > 0 else "-"
            lim = (budget or {}).get("joints", {}).get(j["name"], {}).get(key)
            tiles = []
            for a in angles:
                img = _on_white(p.frame(sign * a, canvas=cv).rgba())
                col = (150, 150, 150)
                if lim:
                    col = (40, 160, 60) if a <= lim["soft"] else (230, 150, 20) if a <= lim["hard"] else (210, 40, 40)
                t = Image.new("RGB", (thumb + 2 * pad, thumb + 2 * pad + 14), col)
                t.paste(Image.fromarray(img), (pad, pad))
                ImageDraw.Draw(t).text((pad + 2, thumb + pad + 1), f"{key}{a:g}", fill=(255, 255, 255))
                tiles.append(t)
            text = f"{j['name']} {key}\nbone {j['bone']}"
            if lim:
                text += f"\nsoft {lim['soft']:g}  hard {lim['hard']:g}\n{lim['limit_by']}\nhard: {lim['hard_by']}"
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


def _reach(probe, j):
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


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig", help="rig.json (spinegen-rig/1) or a Spine 4.x skeleton JSON")
    ap.add_argument("images", help="directory of attachment PNGs or a .atlas")
    ap.add_argument("--joints", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--sheet", default=None, help="write a contact sheet PNG (every 10 deg)")
    ap.add_argument("--step", type=float, default=2.0)
    ap.add_argument("--max-angle", type=float, default=180.0)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated joint names")
    ap.add_argument("--skin", default=None)
    ap.add_argument("--trace", action="store_true", help="keep every judged angle in meta")
    a = ap.parse_args(argv)
    ir = raster.load_rig(a.rig)
    images = raster.load_images(a.images)
    joints = load_joints(a.joints)
    only = set(a.only.split(",")) if a.only else None
    budget = scan(ir, images, joints, a.step, a.max_angle, a.scale, not a.no_verify, a.skin, only,
                  progress=lambda m: print(m, file=sys.stderr), keep_trace=a.trace)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(budget, f, indent=1)
    print(f"{a.out}: {len(budget['joints'])} joint(s) in {budget['meta']['seconds']} s", file=sys.stderr)
    if a.sheet:
        raster.save_png(a.sheet, contact_sheet(ir, images, joints, budget, max_angle=a.max_angle, skin=a.skin,
                                               only=only))
        print(f"{a.sheet}: contact sheet", file=sys.stderr)


if __name__ == "__main__":
    main()
