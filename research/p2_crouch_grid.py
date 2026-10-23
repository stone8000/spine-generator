"""Crouch folds in spine-core over a grid of depths and sideways weight shifts.

    python research/p2_crouch_grid.py <rig dir> [--depths 0.05,0.07,0.08,0.10,0.12,0.13]
        [--shifts -40,-30,-20,-15,-10,0,10,15,20,30,40] [--json out.json]

The rig dir is a spinegen.rigbuild output (rig.json, motion.json, <name>.json,
<name>.atlas). Each pose lowers the hips (the leg IK thighs' parent) by
depth x H and moves them dx px sideways (world x, added to the setup), legs
on IK, feet planted; spine-core (qa/run.mjs dump --folds) poses it. The
table gives the runtime's fold count; the per-slot split is the same G1
definition (lbs.fold_mask against each mesh's setup winding) on the
runtime's vertices, and the per-slot sum is checked against the runtime's
total.

WHY (P2 review, A2_robust_to_weight_shift): a crouch straight down is the
one pose the A2 fix was tuned on; a real crouch also shifts the weight onto
one leg, which bends one ankle and knee more than the other. A 0-fold
result that holds only on the symmetric line is not robust.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from spinegen import ir as irmod  # noqa: E402
from spinegen import lbs  # noqa: E402

QA = os.path.join(REPO, "qa", "run.mjs")


def _rig(d):
    """(IR, Spine skeleton path, atlas path) of a rigbuild output dir (the
    skeleton is <rig name>.json, rigbuild.write)."""
    ir = irmod.load(os.path.join(d, "rig.json"), os.path.join(d, "motion.json"))
    return ir, os.path.join(d, ir["name"] + ".json"), glob.glob(os.path.join(d, "*.atlas"))[0]


def folds(ir, skel, atlas, pose, runtime="4.2", frame=None):
    """(runtime total, {slot: folds}) for one pose; `frame` (a list) gets
    the runtime's dumped frame appended."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "d.json")
        subprocess.run(["node", QA, "dump", "--skeleton", skel, "--atlas", atlas, "--pose", json.dumps(pose),
                        "--pose-mode", "add", "--folds", "--runtime", runtime, "--out", out], check=True)
        fr = json.load(open(out))["frames"][0]
    if frame is not None:
        frame.append(fr)
    ev = lbs.Evaluator(ir)
    refs = ev.fold_references()
    per = {}
    for slot, att in ev.shown():
        if att is None or att.get("type") != "mesh":
            continue
        geo = irmod.geometry_of(ir, slot, att)
        v = np.asarray(fr["slots"][slot]["vertices"], float).reshape(-1, 2)
        n = int(lbs.fold_mask(v, geo["uvs"], geo["triangles"], reference=refs.get((slot, id(att)), -1)).sum())
        if n:
            per[slot] = n
    return int(fr["folds"]), per


def grid(d, depths, shifts, runtime="4.2"):
    """{"rows": [{"depth", "dx", "folds", "slots"}]} for rig dir d. Runtime
    4.3: the same IR emitted by spinegen.emit.spine43 (<name>.rig43.json in
    the rig dir, written when missing) in spine-core 4.3.13."""
    ir, skel, atlas = _rig(d)
    if runtime == "4.3":
        skel43 = os.path.join(d, ir["name"] + ".rig43.json")
        if not os.path.exists(skel43):
            subprocess.run([sys.executable, "-m", "spinegen.emit.spine43", os.path.join(d, "rig.json"), skel43],
                           check=True, cwd=REPO)
        skel = skel43
    legs = irmod.leg_chains(ir)
    hips = irmod.by_name(ir["bones"])[legs[0]["thigh"]]["parent"]
    from spinegen import rigbuild
    feet = rigbuild.planted_vertices(ir)
    f0 = []
    folds(ir, skel, atlas, {}, runtime, f0)
    f0 = f0[0]

    def verts(fr, n):
        return np.asarray(fr["slots"][n]["vertices"], float).reshape(-1, 2)

    def bxy(fr, n):
        return np.array([fr["bones"][n]["x"], fr["bones"][n]["y"]])
    rows = []
    for depth in depths:
        for dx in shifts:
            got = []
            total, per = folds(ir, skel, atlas, {hips: {"x": dx, "y": -depth * ir["H"]}}, runtime, got)
            if sum(per.values()) != total:
                raise AssertionError(f"per-slot folds {per} do not add up to the runtime's {total}")
            fr = got[0]
            slide = max([float(np.linalg.norm(verts(fr, n)[i] - verts(f0, n)[i], axis=1).max())
                         for n, i in feet.items() if i] or [0.0])
            knees = {leg["side"]: (bxy(fr, leg["shin"]) - bxy(f0, leg["shin"])).round(2).tolist() for leg in legs}
            rows.append({"depth": depth, "dx": dx, "folds": total, "slots": per, "foot_slide": slide,
                         "knee_move": knees})
    return {"H": ir["H"], "hips": hips, "runtime": runtime, "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig")
    ap.add_argument("--depths", default="0.05,0.07,0.08,0.10,0.12,0.13")
    ap.add_argument("--shifts", default="-40,-30,-25,-20,-15,-10,-5,0,5,10,15,20,25,30,40")
    ap.add_argument("--json")
    ap.add_argument("--runtime", choices=("4.2", "4.3"), default="4.2")
    a = ap.parse_args(argv)
    depths = [float(x) for x in a.depths.split(",")]
    shifts = [float(x) for x in a.shifts.split(",")]
    res = grid(a.rig, depths, shifts, a.runtime)
    print(f"spine-core {a.runtime} folds; rows: hips lowered by depth x H (H = {res['H']:.1f}), columns: hips moved dx px")
    print(f"{'depth':>6s}" + "".join(f"{x:>6g}" for x in shifts))
    for depth in depths:
        r = [x for x in res["rows"] if x["depth"] == depth]
        print(f"{depth:6.2f}" + "".join(f"{x['folds']:6d}" for x in r))
    bad = {}
    for x in res["rows"]:
        for s, n in x["slots"].items():
            bad[s] = bad.get(s, 0) + n
    print("folds by slot over the grid:", bad or "none")
    print("whole planted foot slide, max over the grid: %.4f px" % max(x["foot_slide"] for x in res["rows"]))
    for x in res["rows"]:
        print(f"  {x['depth']:.2f} H dx {x['dx']:+4g}: knees moved " +
              ", ".join(f"{s} ({v[0]:+.1f}, {v[1]:+.1f})" for s, v in x["knee_move"].items()) +
              f"; foot slide {x['foot_slide']:.4f} px")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
