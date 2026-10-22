"""Band-shape variants for P2_REPORT A1 (elbows) and A2 (deep crouch): build a
rig with template edits, then measure what the report tabulates.

    python research/p2_band_variants.py elbow <layers dir> -o <out dir> [--joints J] [--name N]
        [--set 'elbow_{s}={"reach": {"upper": 3.5, "lower": 2.0}, "row_points": 2}'] [--mix 0.5]
    python research/p2_band_variants.py crouch <layers dir> -o <out dir> [--set ...]
        [--depths 0.05,0.08,0.10,0.12] [--straighten knee|ankle|hip]

`--set joint=JSON` updates a template joint entry (raw template names, `{s}`
expanded for both sides; repeatable); `--mix` sets the elbow helpers' share
of the turn. The rig is written to <out dir> (spinegen.rigbuild.write) and a
<out dir>/variant.json holds the numbers:

  elbow   both elbows scanned by spinegen.budget (step 2, max 150, the whole
          rig's canvas, so the numbers match a full budget run): soft, hard
          and limit_by per direction; vertices and the 22 px grid ratio; and
          "hand": the largest distance (px) of a hand-layer vertex at or past
          the wrist joint from where rigid forearm motion would put it, at
          +-90 and +-120 deg (0 = the hand does not bend with the elbow).
  crouch  per depth (fraction of H): folds in the official runtime
          (qa dump --folds, pose added to setup), rigbuild.crouch's knees
          (moves the expected way, end bends, symmetry), sole / whole-foot /
          calf-hem slides, and G2 of the leg bands (spinegen.art_gates).

WHY a script: the report's variant tables are many builds of the same
character with one template knob changed; this keeps them reproducible.
Nothing here reads third-party art itself; the layers dir is the caller's.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo root
from spinegen import art_gates as ag  # noqa: E402
from spinegen import budget, lbs, raster, rigbuild  # noqa: E402
from spinegen import ir as irmod  # noqa: E402
from spinegen import template as tmpl  # noqa: E402

QA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qa", "run.mjs")


def template(sets, mix=None, straighten=None):
    with open(os.path.join(tmpl.TEMPLATE_DIR, "biped_mascot.json")) as f:
        raw = json.load(f)
    for name, spec in sets:
        raw["joints"][name].update(spec)
    for h in raw["helpers"]:
        if mix is not None and h["name"].startswith("elbow"):
            h["mix"] = mix
    for ik in raw["ik"]:
        if straighten:
            ik["straighten"] = straighten
    return tmpl.expand(raw)


def hand_distortion(ir, joints, angles=(90.0, 120.0)):
    """{elbow: {"+90": px, ...}}: hand-layer vertices at or past the wrist
    (along the band axis) against rigid motion of the forearm bone."""
    ev = lbs.Evaluator(ir)
    state = {c["name"]: {"mix": 0.0} for c in ir["ik"]}
    w0 = ev.world(None, state)
    shown = dict(ev.shown())
    bones = irmod.by_name(ir["bones"])
    out = {}
    for j in joints["joints"]:
        if not j["name"].startswith("elbow"):
            continue
        fore = j["band"]["lower"]
        hand = next((b["name"] for b in ir["bones"] if b["parent"] == fore and b["name"].startswith("hand")), None)
        slot = next((s["name"] for s in ir["slots"] if s["bone"] == fore), None)
        if hand is None or slot is None:
            continue
        c, ax = np.array(j["band"]["center"]), np.array(j["band"]["axis"])
        v0 = ev.attachment_vertices(w0, slot, shown[slot])
        past = (v0 - c) @ ax >= float((np.array(w0[ev.index[hand]][4:6]) - c) @ ax) - 1e-6
        m0 = np.array(w0[ev.index[fore]])
        local = np.linalg.solve(np.array([[m0[0], m0[1]], [m0[2], m0[3]]]), (v0 - m0[4:6]).T).T
        res = {}
        for a in angles:
            for sg in (1, -1):
                w1 = ev.world({fore: {"rotation": bones[fore]["rotation"] + sg * a}}, state)
                v1 = ev.attachment_vertices(w1, slot, shown[slot])
                m1 = np.array(w1[ev.index[fore]])
                rigid = local @ np.array([[m1[0], m1[1]], [m1[2], m1[3]]]).T + m1[4:6]
                d = np.linalg.norm(v1 - rigid, axis=1)
                res[f"{'+' if sg > 0 else '-'}{a:g}"] = round(float(d[past].max()), 3) if past.any() else 0.0
        out[j["name"]] = res
    return out


def elbow(res, paths):
    ir, images = raster.load_rig(paths["rig"]), raster.load_images(paths["atlas"])
    b = budget.scan(ir, images, res["joints"], 2.0, 150.0, only={"elbow_l", "elbow_r"})
    tot = res["report"]["totals"]
    return {"elbows": {j: {s: {k: e[k] for k in ("soft", "hard", "limit_by")} for s, e in d.items()}
                       for j, d in b["joints"].items()},
            "vertices": tot["vertices"], "vertex_ratio": tot["vertex_ratio"], "b2": tot["b2"],
            "hand": hand_distortion(ir, res["joints"]),
            "bands": {k: v["reach"] for k, v in res["report"]["bands"].items() if k.startswith("elbow")}}


def core_folds(skeleton, atlas, pose):
    out = subprocess.run(["node", QA, "dump", "--skeleton", skeleton, "--atlas", atlas, "--pose", json.dumps(pose),
                          "--pose-mode", "add", "--folds"], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["frames"][0]["folds"]


def crouch(res, paths, depths):
    ir = res["ir"]
    b = irmod.by_name(ir["bones"])
    hips = b[ir["ik"][0]["bones"][0]]["parent"]
    dirs = {n: r["knee_dir"] for n, r in res["report"]["ik"].items() if r.get("knee_dir") is not None}
    r = raster.Renderer(raster.load_rig(paths["rig"]), raster.load_images(paths["atlas"]), missing="skip")
    rest = r.frame(None)
    legs = [j for j in budget.load_joints(res["joints"]) if j["name"].split("_")[0] in ("hip", "knee", "ankle")]
    out = {}
    for f in depths:
        depth = f * ir["H"]
        c = rigbuild.crouch(ir, depth, paths["skeleton"], paths["atlas"], knee_dirs=dirs)
        fr = r.frame({hips: {"y": b[hips]["y"] - depth}})
        g2 = {j["name"]: ag.band_area_ratio(rest, fr, j) for j in legs}
        out[f"{f:g}"] = {
            "depth_px": depth, "folds_spine_core": core_folds(paths["skeleton"], paths["atlas"], {hips: {"y": -depth}}),
            "folds_lbs": lbs.Evaluator(ir).folds(pose={hips: {"y": b[hips]["y"] - depth}})["slots"],
            "knees_ok": c["knees_ok"], "knee_bend_diff_deg": c["knee_bend_diff_deg"],
            "knees": {k: {"along": v["along"], "bend_deg": v["bend_deg"]} for k, v in c["knees"].items()},
            "sole_slide": c["max_slide"], "foot_slide": c["foot_slide"], "calf_hem_slide": c["shin_bottom_slide"],
            "g2": {k: {"sum": g["value"], "tri_min": g["tri_min"], "tri_out": g["tri_out_of_range"]}
                   for k, g in g2.items()}}
    out["bands"] = {k: v["reach"] for k, v in res["report"]["bands"].items() if k.split("_")[0] in ("hip", "knee", "ankle")}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("elbow", "crouch"))
    ap.add_argument("layers_dir")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--joints", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--set", action="append", default=[], help="joint=JSON template update (repeatable)")
    ap.add_argument("--mix", type=float, default=None, help="elbow helpers' share of the turn")
    ap.add_argument("--straighten", choices=("knee", "ankle", "hip"), default=None)
    ap.add_argument("--depths", default="0.05,0.08,0.10,0.12")
    a = ap.parse_args(argv)
    sets = []
    for s in a.set:
        k, v = s.split("=", 1)
        sets.append((k, json.loads(v)))
    t = template(sets, a.mix, a.straighten)
    res = rigbuild.build(a.layers_dir, a.joints, t, "joint", name=a.name)
    paths = rigbuild.write(a.out, res)
    if a.what == "elbow":
        row = elbow(res, paths)
    else:
        row = crouch(res, paths, [float(x) for x in a.depths.split(",")])
    row["set"] = dict(sets)
    row["mix"], row["straighten"] = a.mix, a.straighten
    with open(os.path.join(a.out, "variant.json"), "w") as f:
        json.dump(row, f, indent=1, default=float)
    print(json.dumps(row, default=float)[:2000])


if __name__ == "__main__":
    main()
