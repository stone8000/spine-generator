"""Played animations against the art budget: which joint leaves what the art can carry, and for how long.

    python -m spinegen.budget_motion <skeleton43.json> <atlas> --budget budget.json --joints joints.json
        [--anims idle,punch] [--fps 60]

WHY
  spinegen.budget measures, per joint and direction, how far the art turns
  before it breaks: theta_soft (a pose may HOLD there) and theta_hard (a
  strike or transit may pass through, never stop). A motion generator
  that does not read those numbers (the motion kit's presets are tuned on
  Iron Jaw, not on this art) asks for whatever its preset says; on the
  monkey the kit's celebrate folds both forearms to 168-171 deg against
  elbow limits of 90-138. This module plays a file in spine-core 4.3.13
  (`qa/run.mjs dump --runtime 4.3`, physics on, like qa's G1) and reads
  every joint's angle on every frame, so the bridge can scale a preset
  down until the art carries it (spinegen.bridge_motionkit, budget step).

ANGLE
  A joint's angle is the world rotation of its bone relative to the band's
  `upper` bone (joints.json; the bone's parent by default), minus the same
  at the setup pose, wrapped to (-180, 180]. That is the local rotation
  the budget sweep turned, also for bones whose Spine parent is not the
  upper limb (a planted foot hangs from its IK target, its joint is still
  the ankle against the shin). A mirrored upper (negative world
  determinant) turns local + clockwise on screen, so the sign follows it.

VERDICT per joint and animation
  hold    beyond theta_soft (in that direction) for HOLD_SECONDS or longer
          in a row (a loop's last and first frames join up);
  transit beyond theta_hard on any frame.
  ok = neither. Folds (qa G1's count, per frame) are reported beside: the
  budget is one joint at a time, two joints bent together can fold first.
"""
import argparse
import json
import math
import os
import subprocess
import sys

from . import budget as budgetmod

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QA_RUN = os.path.join(REPO, "qa", "run.mjs")
HOLD_SECONDS = 0.1  # past theta_soft this long in a row is a held pose, not a pass-through
FPS = 60            # qa G1 judges every 60 fps frame


def load(budget_path, joints_path):
    """[{name, bone, upper, limits: {"+": (soft, hard), "-": (soft, hard)}, by: {...}}] for the
    joints present in both files. Limits are magnitudes in degrees; a direction the sweep did
    not measure is unlimited (inf)."""
    with open(budget_path) as f:
        bud = json.load(f)
    out = []
    for j in budgetmod.load_joints(joints_path):
        b = bud["joints"].get(j["name"])
        if not b:
            continue
        lim, by = {}, {}
        for d in ("+", "-"):
            e = b.get(d)
            lim[d] = (abs(e["soft"]), abs(e["hard"])) if e else (math.inf, math.inf)
            by[d] = (e.get("limit_by"), e.get("hard_by")) if e else (None, None)
        out.append({"name": j["name"], "bone": j["bone"], "upper": j["band"].get("upper"), "limits": lim, "by": by})
    return out


def dump(skeleton, atlas, anim=None, fps=FPS, folds=True):
    args = ["node", QA_RUN, "dump", "--runtime", "4.3", "--skeleton", skeleton, "--atlas", atlas]
    if anim is not None:
        args += ["--anim", anim, "--fps", str(fps)]
    if folds:
        args += ["--folds"]
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"qa dump failed: {p.stderr.strip()[-1500:]}")
    return json.loads(p.stdout)["frames"]


def _rot(m):
    return math.degrees(math.atan2(m["c"], m["a"]))


def _wrap(d):
    return (d + 180.0) % 360.0 - 180.0


def joint_angle(bones, joint, parents):
    """Angle of `joint` in one dumped frame (module doc, ANGLE), not yet relative to the setup."""
    bone = bones[joint["bone"]]
    up = joint["upper"] or parents.get(joint["bone"])
    if up is None:
        return _rot(bone)
    u = bones[up]
    sign = -1.0 if u["a"] * u["d"] - u["b"] * u["c"] < 0 else 1.0
    return sign * _wrap(_rot(bone) - _rot(u))


def parents_of(skeleton):
    with open(skeleton) as f:
        sk = json.load(f)
    return {b["name"]: b.get("parent") for b in sk["bones"]}


def series(frames, setup_bones, joints, parents):
    """{joint: [angle from setup per frame]}."""
    out = {}
    for j in joints:
        a0 = joint_angle(setup_bones, j, parents)
        out[j["name"]] = [_wrap(joint_angle(f["bones"], j, parents) - a0) for f in frames]
    return out


def _longest_run(flags, loop):
    best = run = 0
    for v in flags:
        run = run + 1 if v else 0
        best = max(best, run)
    if loop and flags and flags[0] and flags[-1] and not all(flags):
        head = next(i for i, v in enumerate(flags) if not v)
        tail = next(i for i, v in enumerate(reversed(flags)) if not v)
        best = max(best, head + tail)
    return best


def judge(angles, joint, fps=FPS, loop=False):
    """Verdict for one joint over one animation (module doc)."""
    hold_frames = max(1, int(math.ceil(HOLD_SECONDS * fps - 1e-9)))
    rec = {"max_plus": round(max(angles), 2), "max_minus": round(min(angles), 2), "t0": round(angles[0], 2)}
    worst = None
    for d, sgn in (("+", 1), ("-", -1)):
        soft, hard = joint["limits"][d]
        mag = [sgn * a for a in angles]
        over_soft = [m > soft + 1e-6 for m in mag]
        run = _longest_run(over_soft, loop)
        hard_frames = sum(m > hard + 1e-6 for m in mag)
        r = {"soft": soft, "hard": hard, "peak": round(max(mag), 2), "soft_run_s": round(run / fps, 3),
             "hard_frames": hard_frames, "hold": run >= hold_frames, "transit": hard_frames > 0,
             "soft_by": joint["by"][d][0], "hard_by": joint["by"][d][1]}
        # how far past its limit, as a fraction of it: what the retarget shrinks first
        excess = max(r["peak"] / hard if r["transit"] else 0, r["peak"] / soft if r["hold"] and soft > 0 else 0)
        r["excess"] = round(excess, 3)
        rec[d] = r
        if r["hold"] or r["transit"]:
            worst = d if worst is None or excess > rec[worst]["excess"] else worst
    rec["ok"] = worst is None
    rec["worst_dir"] = worst
    if worst:
        w = rec[worst]
        rec["why"] = ("transit past theta_hard" if w["transit"] else "held past theta_soft") + \
            f" {worst}{w['peak']:.0f} (soft {w['soft']:.0f}, hard {w['hard']:.0f})"
    return rec


def measure(skeleton, atlas, joints, anims, loops=(), fps=FPS):
    """{anim: {"joints": {name: judge()}, "folds": [per frame], "max_folds", "ok"}} for each animation."""
    parents = parents_of(skeleton)
    setup = dump(skeleton, atlas, folds=False)[0]["bones"]
    out = {}
    for a in anims:
        frames = dump(skeleton, atlas, a, fps)
        ang = series(frames, setup, joints, parents)
        js = {j["name"]: judge(ang[j["name"]], j, fps, a in loops) for j in joints}
        folds = [f.get("folds", 0) for f in frames]
        out[a] = {"joints": js, "folds": folds, "max_folds": max(folds) if folds else 0,
                  "fold_frames": sum(1 for v in folds if v), "angles": ang,
                  "ok": all(r["ok"] for r in js.values()) and not any(folds)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("skeleton")
    ap.add_argument("atlas")
    ap.add_argument("--budget", required=True)
    ap.add_argument("--joints", required=True)
    ap.add_argument("--anims", default=None, help="comma list (default: every animation in the file)")
    ap.add_argument("--loops", default="", help="comma list of looping animations (hold runs wrap around)")
    ap.add_argument("--fps", type=int, default=FPS)
    args = ap.parse_args(argv)
    joints = load(args.budget, args.joints)
    if args.anims:
        anims = [a for a in args.anims.split(",") if a]
    else:
        with open(args.skeleton) as f:
            anims = list(json.load(f).get("animations", {}))
    res = measure(args.skeleton, args.atlas, joints, anims, set(args.loops.split(",")), args.fps)
    for a, r in res.items():
        bad = [f"{n}: {j['why']}" for n, j in r["joints"].items() if not j["ok"]]
        print(f"{a}: {'ok' if r['ok'] else 'OVER'}  folds max {r['max_folds']} on {r['fold_frames']} frames")
        for b in bad:
            print("   ", b)
    return 0 if all(r["ok"] for r in res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
