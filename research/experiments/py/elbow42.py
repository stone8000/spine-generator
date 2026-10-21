"""[E12] Does the half-angle helper hold the elbow in the OFFICIAL runtime?

    python research/experiments/py/elbow42.py [--out /tmp/spinegen-elbow42] [--extent 1.25] [--rows 2]
                                              [--angles 0:150:10] [--both-ways] [--json results.json]

WHY
  SOTA §8 open question 1: the half-angle helper bone + piecewise ramp was
  only measured in a numpy model on one straight strip mesh [E1]. This
  rebuilds the experiment the way the P2 rig builder will build a limb, and
  lets spine-core 4.2.120 judge it:
    * a SYNTHETIC two-layer arm drawn here (a tapered upper arm 44 -> 40 px and
      a forearm 40 -> 36 px whose layers overlap 30 px at the elbow), so the
      result is ours to publish;
    * each layer meshed by spinegen.mesh.build with a joint band at the elbow,
      weighted by spinegen.weights.compute;
    * an IR rig (spinegen.ir) emitted by spinegen.emit.spine42, the helper as
      the IR's `half_angle` constraint (expanded by the emitter);
    * `node qa/run.mjs dump --folds --pose {"fore":{"rotation":θ}}` per angle.

  Variants, same meshes for all three:
    rigid   each layer 100% on its own bone (the monkey's cut-out)
    ramp    upper -> fore ramp across the band, no helper
    helper  upper -> elbow_mid -> fore piecewise ramp, elbow_mid turned by
            half the elbow angle by the transform constraint

  Per angle: G1 folds (runtime count), joint-band area ratio (posed / setup,
  signed, over triangles whose setup centroid is in the band |s| <= extent*hw,
  both layers together), the same over the wider E1 window |s| <= 2.5 hw,
  the smallest single-triangle ratio in the band (G2 wants >= 0.3), and the
  numpy LBS vs runtime vertex difference (C1, so the numpy budget scanner can
  be trusted on this rig).

  A rigid cut-out never folds and keeps its area by construction; its failure
  is the seam (gap / ghost at the joint), which the raster gates G4/G6 measure.

Outputs (skeleton, atlas, PNG, results JSON) go to --out, never into the repo.
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from spinegen import ir as irmod, lbs, mesh, weights  # noqa: E402
from spinegen.emit import spine42  # noqa: E402

# canvas layout (y down): shoulder, elbow, wrist on one horizontal line
SHOULDER = np.array([40.0, 60.0])
ELBOW = SHOULDER + (100.0, 0.0)
WRIST = ELBOW + (90.0, 0.0)
R_SHOULDER, R_ELBOW, R_WRIST = 22.0, 20.0, 18.0   # 44 -> 40 -> 36 px wide
OVERLAP = 30.0                                     # the two layers share 30 px at the elbow
HW = R_ELBOW                                       # local half-width at the joint
VARIANTS = ("rigid", "ramp", "helper")


# ---------------------------------------------------------------- synthetic art

def tapered_capsule(p0, r0, p1, r1, ss=4, pad=4):
    """RGBA layer of a capsule from circle (p0, r0) to (p1, r1), anti-aliased,
    with stripes across the limb so a texture warp would be visible.
    Returns (rgba (h, w, 4) uint8, origin canvas (x, y))."""
    lo = np.floor(np.minimum(p0 - r0, p1 - r1)) - pad
    hi = np.ceil(np.maximum(p0 + r0, p1 + r1)) + pad
    w, h = (hi - lo).astype(int)
    ys, xs = np.mgrid[0:h * ss, 0:w * ss]
    P = np.stack([(xs + 0.5) / ss + lo[0], (ys + 0.5) / ss + lo[1]], -1)
    d = p1 - p0
    t = np.clip(((P - p0) @ d) / (d @ d), 0, 1)
    dist = np.linalg.norm(P - (p0 + t[..., None] * d), axis=-1)
    cover = (dist <= r0 + (r1 - r0) * t).astype(float)
    alpha = cover.reshape(h, ss, w, ss).mean((1, 3))
    s = ((P - p0) @ (d / np.linalg.norm(d))).reshape(h, ss, w, ss).mean((1, 3))
    stripe = (np.floor(s / 8) % 2).astype(float)
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 0] = 120 + 80 * stripe
    rgba[..., 1] = 80 + 40 * stripe
    rgba[..., 2] = 40
    rgba[..., 3] = np.round(alpha * 255).astype(np.uint8)
    return rgba, lo


def arm_layers(overlap=OVERLAP):
    """{name: (rgba, origin)}: upper arm reaching overlap/2 past the elbow,
    forearm starting overlap/2 before it."""
    ax = np.array([1.0, 0.0])
    upper_end = ELBOW + ax * (overlap / 2 - R_ELBOW)
    fore_start = ELBOW - ax * (overlap / 2 - R_ELBOW)
    return {"upper_arm": tapered_capsule(SHOULDER, R_SHOULDER, upper_end, R_ELBOW),
            "forearm": tapered_capsule(fore_start, R_ELBOW, WRIST, R_WRIST)}


# ---------------------------------------------------------------- rig

def canvas_bones(helper):
    out = [{"name": "upper", "head": tuple(SHOULDER), "tail": tuple(ELBOW)},
           {"name": "fore", "head": tuple(ELBOW), "tail": tuple(WRIST)}]
    if helper:
        out.append({"name": "elbow_mid", "head": tuple(ELBOW), "tail": tuple(ELBOW)})
    return out


def joint(helper, extent):
    return {"center": tuple(ELBOW), "upper": "upper", "helper": "elbow_mid" if helper else None,
            "lower": "fore", "halfwidth": HW, "extent": extent}


def to_world(xy):
    """Canvas (y down) -> world (y up), shoulder at the origin."""
    xy = np.asarray(xy, float)
    return np.stack([xy[..., 0] - SHOULDER[0], SHOULDER[1] - xy[..., 1]], -1)


def build(variant, extent=mesh.BAND_EXTENT, rows=2, layers=None):
    """(IR rig, {layer: mesh dict}, {layer: weights}) for one variant."""
    layers = layers or arm_layers()
    helper = variant == "helper"
    band = {"center": tuple(ELBOW), "axis": (1.0, 0.0), "halfwidth": HW, "rows": rows, "extent": extent}
    meshes, wts = {}, {}
    for name, (rgba, origin) in layers.items():
        m = mesh.build(rgba[..., 3], origin, bands=[band])
        own = "upper" if name == "upper_arm" else "fore"
        bones = canvas_bones(helper)
        bones.sort(key=lambda b: b["name"] != own)  # bones[0] = the layer's own bone (rigid mode)
        if variant == "rigid":
            w = weights.compute(m["verts"], m["tris"], mesh.outlines(m), bones, mode="rigid")
        else:
            w = weights.compute(m["verts"], m["tris"], mesh.outlines(m), bones, [joint(helper, extent)])
        meshes[name], wts[name] = m, w
    rig = irmod.new_rig(f"elbow42_{variant}")
    rig["bones"] = [irmod.new_bone("root"),
                    irmod.new_bone("upper", "root", length=100.0),
                    irmod.new_bone("fore", "upper", x=100.0, length=90.0)]
    if helper:
        rig["bones"].append(irmod.new_bone("elbow_mid", "upper", x=100.0, length=0.0))
        rig["transform"].append({"name": "elbow_half", "order": 0, "runs": True, "skin_required": False,
                                 "kind": "half_angle", "bone": "elbow_mid", "target": "fore", "mix": 0.5})
    rig["slots"] = [irmod.new_slot("upper_arm", "upper", "upper_arm"), irmod.new_slot("forearm", "fore", "forearm")]
    atts = {}
    for name, m in meshes.items():
        rgba = layers[name][0]
        atts[name] = {name: {
            "type": "mesh", "name": name, "path": name, "color": "ffffffff",
            "width": float(rgba.shape[1]), "height": float(rgba.shape[0]),
            "uvs": m["uvs"].tolist(), "triangles": m["tris"].tolist(), "hull": int(m["hull"]), "edges": None,
            "vertices": to_world(m["verts"]).tolist(),
            "weights": [[[b, w] for b, w in row] for row in wts[name]], "sequence": None}}
    rig["skins"] = [{"name": "default", "bones": [], "ik": [], "transform": [], "path": [], "physics": [],
                     "color": None, "attachments": atts}]
    allv = to_world(np.vstack([m["verts"] for m in meshes.values()]))
    lo, hi = allv.min(0), allv.max(0)
    rig["skeleton"].update(x=float(lo[0]), y=float(lo[1]), width=float(hi[0] - lo[0]), height=float(hi[1] - lo[1]),
                           images="./", hash=f"elbow42-{variant}")
    errs = irmod.validate(rig)
    if errs:
        raise ValueError(errs)
    return rig, meshes, wts


def write_atlas(layers, path_png, path_atlas):
    """Both layer images side by side on one page (4.x `bounds:` format)."""
    from PIL import Image
    pad = 2
    x, h = pad, 0
    placed = []
    for name, (rgba, _) in layers.items():
        placed.append((name, x, rgba))
        x += rgba.shape[1] + pad
        h = max(h, rgba.shape[0])
    W, H = x, h + 2 * pad
    page = np.zeros((H, W, 4), np.uint8)
    lines = [os.path.basename(path_png), f"size:{W},{H}", "filter:Linear,Linear", "pma:false"]
    for name, px, rgba in placed:
        page[pad:pad + rgba.shape[0], px:px + rgba.shape[1]] = rgba
        lines += [name, f"bounds:{px},{pad},{rgba.shape[1]},{rgba.shape[0]}"]
    Image.fromarray(page, "RGBA").save(path_png)
    with open(path_atlas, "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- measurement

def band_triangles(m, extent, window=None):
    """Triangles whose setup centroid is within the band (|s| <= extent*hw,
    or <= window*hw) and within the limb."""
    c = m["verts"][m["tris"]].mean(1)
    s = (c - ELBOW) @ np.array([1.0, 0.0])
    reach = (window if window is not None else extent) * HW
    return np.abs(s) <= reach


def area_metrics(rig, meshes, frame0, frame, extent):
    """Band area ratio (signed sums), E1-window ratio, min triangle ratio."""
    tot = {"band": [0.0, 0.0], "window": [0.0, 0.0]}
    tri_min = math.inf
    for name, m in meshes.items():
        v0 = np.asarray(frame0["slots"][name]["vertices"], float).reshape(-1, 2)
        v1 = np.asarray(frame["slots"][name]["vertices"], float).reshape(-1, 2)
        a0 = lbs.signed_areas(v0, m["tris"])
        a1 = lbs.signed_areas(v1, m["tris"])
        for key, sel in (("band", band_triangles(m, extent)), ("window", band_triangles(m, extent, 2.5))):
            tot[key][0] += float(a0[sel].sum())
            tot[key][1] += float(a1[sel].sum())
        sel = band_triangles(m, extent)
        if sel.any():
            tri_min = min(tri_min, float((a1[sel] / a0[sel]).min()))
    return {"band_area": tot["band"][1] / tot["band"][0], "window_area": tot["window"][1] / tot["window"][0],
            "min_tri_ratio": tri_min}


def node_dump(skel, atlas, pose):
    run = os.path.join(ROOT, "qa", "run.mjs")
    p = subprocess.run(["node", run, "dump", "--skeleton", skel, "--atlas", atlas, "--folds",
                        "--pose", json.dumps(pose)], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:])
    return json.loads(p.stdout)["frames"][0]


def sweep(variant, outdir, angles, extent=mesh.BAND_EXTENT, rows=2, sign=1):
    """Emit one variant and judge it at each elbow angle in spine-core."""
    layers = arm_layers()
    rig, meshes, wts = build(variant, extent, rows, layers)
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, rig["name"])
    write_atlas(layers, base + ".png", base + ".atlas")
    spine42.write(rig, base + ".json")
    ev = lbs.Evaluator(rig)
    frame0 = node_dump(base + ".json", base + ".atlas", {})
    rows_out = []
    for deg in angles:
        pose = {"fore": {"rotation": sign * deg}}
        fr = node_dump(base + ".json", base + ".atlas", pose)
        mine = ev.frame({"fore": {"rotation": sign * deg}})
        c1 = max(float(np.abs(np.asarray(fr["slots"][s]["vertices"]) - np.asarray(mine["slots"][s]["vertices"])).max())
                 for s in meshes)
        row = {"angle": sign * deg, "folds": fr["folds"], "folds_numpy": ev.folds({"fore": {"rotation": sign * deg}})["folds"],
               "c1": c1}
        row.update(area_metrics(rig, meshes, frame0, fr, extent))
        rows_out.append(row)
    info = {"variant": variant, "extent": extent, "rows": rows, "skeleton": base + ".json",
            "vertices": {k: len(m["verts"]) for k, m in meshes.items()},
            "grid_vertices": {k: m["stats"]["grid_vertices"] for k, m in meshes.items()},
            "b2": {k: weights.b2(m["verts"], wts[k], [joint(variant == "helper", extent)],
                                 canvas_bones(variant == "helper"))["value"] for k, m in meshes.items()}
            if variant != "rigid" else None}
    return info, rows_out


def soft_hard(rows, area_min=0.9):
    """Largest angle (in sweep order, stopping at the first failure) with
    0 folds and band area >= area_min (soft), and with 0 folds (hard)."""
    soft = hard = 0
    soft_ok = hard_ok = True
    for r in rows:
        hard_ok = hard_ok and r["folds"] == 0
        soft_ok = soft_ok and hard_ok and r["band_area"] >= area_min
        if hard_ok:
            hard = r["angle"]
        if soft_ok:
            soft = r["angle"]
    return soft, hard


def parse_angles(spec):
    a, b, s = (float(x) for x in spec.split(":"))
    return [a + i * s for i in range(int(round((b - a) / s)) + 1)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="/tmp/spinegen-elbow42")
    ap.add_argument("--extent", type=float, default=mesh.BAND_EXTENT)
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--angles", default="0:150:10")
    ap.add_argument("--both-ways", action="store_true", help="also bend the elbow the other way")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    angles = parse_angles(args.angles)
    results = []
    for sign in ((1, -1) if args.both_ways else (1,)):
        for v in args.variants.split(","):
            info, rows = sweep(v, args.out, angles, args.extent, args.rows, sign)
            soft, hard = soft_hard(rows)
            info.update(sign=sign, rows_out=rows, soft=soft, hard=hard)
            results.append(info)
            print(f"\n{v} (extent {args.extent} hw, {args.rows} rows, bend {'+' if sign > 0 else '-'}): "
                  f"verts {info['vertices']} grid {info['grid_vertices']} B2 {info['b2']}")
            print("  angle folds(rt) folds(np) band_area window_area min_tri  c1_px")
            for r in rows:
                print(f"  {r['angle']:5.0f} {r['folds']:8d} {r['folds_numpy']:9d} {r['band_area']:9.3f} "
                      f"{r['window_area']:11.3f} {r['min_tri_ratio']:7.3f} {r['c1']:.1e}")
            print(f"  soft (0 folds, band area >= 0.9): {soft}   hard (0 folds): {hard}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=1)
    return results


if __name__ == "__main__":
    main()
