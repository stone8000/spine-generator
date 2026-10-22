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


def folds(ir, skel, atlas, pose):
    """(runtime total, {slot: folds}) for one pose."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "d.json")
        subprocess.run(["node", QA, "dump", "--skeleton", skel, "--atlas", atlas, "--pose", json.dumps(pose),
                        "--pose-mode", "add", "--folds", "--out", out], check=True)
        fr = json.load(open(out))["frames"][0]
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


def grid(d, depths, shifts):
    """{"rows": [{"depth", "dx", "folds", "slots"}]} for rig dir d."""
    ir, skel, atlas = _rig(d)
    hips = irmod.by_name(ir["bones"])[ir["ik"][0]["bones"][0]]["parent"]
    rows = []
    for depth in depths:
        for dx in shifts:
            total, per = folds(ir, skel, atlas, {hips: {"x": dx, "y": -depth * ir["H"]}})
            if sum(per.values()) != total:
                raise AssertionError(f"per-slot folds {per} do not add up to the runtime's {total}")
            rows.append({"depth": depth, "dx": dx, "folds": total, "slots": per})
    return {"H": ir["H"], "hips": hips, "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig")
    ap.add_argument("--depths", default="0.05,0.07,0.08,0.10,0.12,0.13")
    ap.add_argument("--shifts", default="-40,-30,-25,-20,-15,-10,-5,0,5,10,15,20,25,30,40")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    depths = [float(x) for x in a.depths.split(",")]
    shifts = [float(x) for x in a.shifts.split(",")]
    res = grid(a.rig, depths, shifts)
    print(f"spine-core folds; rows: hips lowered by depth x H (H = {res['H']:.1f}), columns: hips moved dx px")
    print(f"{'depth':>6s}" + "".join(f"{x:>6g}" for x in shifts))
    for depth in depths:
        r = [x for x in res["rows"] if x["depth"] == depth]
        print(f"{depth:6.2f}" + "".join(f"{x['folds']:6d}" for x in r))
    bad = {}
    for x in res["rows"]:
        for s, n in x["slots"].items():
            bad[s] = bad.get(s, 0) + n
    print("folds by slot over the grid:", bad or "none")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
