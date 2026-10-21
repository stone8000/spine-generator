"""[E12] Does the half-angle helper hold the elbow in the OFFICIAL runtime?

    python research/experiments/py/elbow42.py [--out /tmp/spinegen-elbow42] [--extent 2.0] [--rows 2]
        [--angles 0:150:10] [--both-ways] [--extents 1.0,1.25,1.5,2.0] [--json results.json]
        [--gates [--gate-step 2] [--sheet DIR]]

WHY
  SOTA §8 open question 1: the half-angle helper bone + piecewise ramp was
  only measured in a numpy model on one straight strip mesh [E1]. This
  rebuilds the experiment the way the P2 rig builder builds a limb, and lets
  spine-core 4.2.120 judge it:
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

MEASURES (per angle; folds and vertices come from the runtime dump)
  folds       G1, counted by the runtime-side detector (`--folds`)
  band_sum    sum of posed / setup signed triangle areas over the triangles of
              BOTH layers whose setup centroid is in the band |s| <= extent*hw.
              Strict: where the layers overlap, the band's compressed middle
              is counted twice and each layer lacks the band end that stretches
  band_union  area of the union of those triangles (rasterised at 1/4 px),
              posed / setup: the joint's silhouette, what the eye sees
  layers      band_sum per layer
  window      band_sum over the wider window |s| <= 2.5 hw that [E1] used
  min_tri     smallest single-triangle area ratio in the band (G2 wants >= 0.3)
  c1          numpy LBS (spinegen.lbs) vs runtime world vertices, px (C1)

  Continuum reference (linear ramp, dense sampling; derived in the P2 notes):
  a two-bone blend whose bones differ by D keeps 1 - (1 - cos D) * mean(t(1-t))
  of the band's area, i.e. 1 - (1 - cos D)/6 for a linear ramp, independent of
  the band's length; with the helper D is half the elbow angle. The inner side
  first folds at  theta = 2 atan(e/h) (no helper) or 4 atan(e/2h) (helper),
  e = band half-length, h = limb half-width.

  A rigid cut-out never folds and keeps its area by construction; its failure
  is the seam (gap / ghost at the joint), which the raster gates G4/G6 measure.
  --gates runs exactly those: spinegen.budget scans each variant (numpy LBS,
  which C1 above pins to the runtime, rendered by spinegen.raster and read by
  spinegen.art_gates G1 G2 G4 G5 G6), so the three variants get the same
  theta_soft / theta_hard and limiting reason a character's budget.json has;
  --sheet also writes one contact sheet per variant.

Outputs (skeleton, atlas, PNG, results JSON) go to --out, never into the repo.
"""
import argparse
import json
import math
import os
import subprocess
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from spinegen import budget as budgetmod, ir as irmod, lbs, mesh, weights  # noqa: E402
from spinegen.emit import spine42  # noqa: E402

# canvas layout (y down): shoulder, elbow, wrist on one horizontal line
SHOULDER = np.array([40.0, 60.0])
ELBOW = SHOULDER + (100.0, 0.0)
WRIST = ELBOW + (90.0, 0.0)
R_SHOULDER, R_ELBOW, R_WRIST = 22.0, 20.0, 18.0   # 44 -> 40 -> 36 px wide
OVERLAP = 30.0                                     # the two layers share 30 px at the elbow
HW = R_ELBOW                                       # local half-width at the joint
AXIS = np.array([1.0, 0.0])
VARIANTS = ("rigid", "ramp", "helper")
WINDOW = 2.5                                       # [E1]'s area window, in halfwidths
G2_TRI = 0.3                                       # SOTA §5 G2: single-triangle area ratio >= 0.3
SOFT_AREA = 0.9                                    # SOTA §4.3 theta_soft: band area >= 0.9
SS = 4                                             # union raster: samples per px


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
    upper_end = ELBOW + AXIS * (overlap / 2 - R_ELBOW)
    fore_start = ELBOW - AXIS * (overlap / 2 - R_ELBOW)
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


def band(extent, rows):
    return {"center": tuple(ELBOW), "axis": tuple(AXIS), "halfwidth": HW, "rows": rows, "extent": extent}


def to_world(xy):
    """Canvas (y down) -> world (y up), shoulder at the origin: the one
    place this experiment converts (it is its own rig builder)."""
    xy = np.asarray(xy, float)
    return np.stack([xy[..., 0] - SHOULDER[0], SHOULDER[1] - xy[..., 1]], -1)


def build_meshes(layers, extent, rows):
    """{layer: mesh dict}, one joint band at the elbow per layer."""
    return {name: mesh.build(rgba[..., 3], origin, bands=[band(extent, rows)])
            for name, (rgba, origin) in layers.items()}


def build(variant, extent=mesh.BAND_EXTENT, rows=mesh.BAND_ROWS, layers=None, meshes=None):
    """(IR rig, {layer: mesh dict}, {layer: weights}) for one variant."""
    layers = layers or arm_layers()
    meshes = meshes or build_meshes(layers, extent, rows)
    helper = variant == "helper"
    wts = {}
    for name, m in meshes.items():
        own = "upper" if name == "upper_arm" else "fore"
        bones = canvas_bones(helper)
        bones.sort(key=lambda b: b["name"] != own)  # bones[0] = the layer's own bone (rigid mode)
        if variant == "rigid":
            wts[name] = weights.compute(m["verts"], m["tris"], mesh.outlines(m), bones, mode="rigid")
        else:
            wts[name] = weights.compute(m["verts"], m["tris"], mesh.outlines(m), bones, [joint(helper, extent)])
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

def band_triangles(m, reach_hw):
    """Triangles whose setup centroid is within |s| <= reach_hw * hw of the elbow."""
    c = m["verts"][m["tris"]].mean(1)
    return np.abs((c - ELBOW) @ AXIS) <= reach_hw * HW


def union_area(tri_sets):
    """Area (px^2) of the union of triangles [(k, 3, 2) world arrays], at 1/SS px."""
    allp = np.concatenate([t.reshape(-1, 2) for t in tri_sets if len(t)])
    lo = np.floor(allp.min(0)) - 2
    size = (np.ceil(allp.max(0)) - lo + 4).astype(int) * SS
    img = np.zeros((size[1], size[0]), np.uint8)
    for t in tri_sets:
        pts = np.round((t - lo) * SS * 16).astype(np.int32)  # 4 bits of sub-sample precision
        for tri in pts:
            cv2.fillConvexPoly(img, tri, 1, lineType=cv2.LINE_8, shift=4)
    return float(img.sum()) / SS ** 2


def area_metrics(meshes, frame0, frame, extent):
    """The MEASURES of the module docstring at one pose."""
    out = {"layers": {}}
    sums = {"band": [0.0, 0.0], "window": [0.0, 0.0]}
    tri_min = math.inf
    union0, union1 = [], []
    for name, m in meshes.items():
        v0 = np.asarray(frame0["slots"][name]["vertices"], float).reshape(-1, 2)
        v1 = np.asarray(frame["slots"][name]["vertices"], float).reshape(-1, 2)
        a0 = lbs.signed_areas(v0, m["tris"])
        a1 = lbs.signed_areas(v1, m["tris"])
        sel = band_triangles(m, extent)
        for key, s in (("band", sel), ("window", band_triangles(m, WINDOW))):
            sums[key][0] += float(a0[s].sum())
            sums[key][1] += float(a1[s].sum())
        out["layers"][name] = float(a1[sel].sum() / a0[sel].sum()) if sel.any() else None
        if sel.any():
            tri_min = min(tri_min, float((a1[sel] / a0[sel]).min()))
            union0.append(v0[m["tris"][sel]])
            union1.append(v1[m["tris"][sel]])
    out["band_sum"] = sums["band"][1] / sums["band"][0]
    out["window"] = sums["window"][1] / sums["window"][0]
    out["band_union"] = union_area(union1) / union_area(union0)
    out["min_tri"] = tri_min
    return out


def node_dump(skel, atlas, pose):
    run = os.path.join(ROOT, "qa", "run.mjs")
    p = subprocess.run(["node", run, "dump", "--skeleton", skel, "--atlas", atlas, "--folds",
                        "--pose", json.dumps(pose)], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:])
    return json.loads(p.stdout)["frames"][0]


def sweep(variant, outdir, angles, extent=mesh.BAND_EXTENT, rows=mesh.BAND_ROWS, sign=1, layers=None, meshes=None):
    """Emit one variant and judge it at each elbow angle in spine-core."""
    layers = layers or arm_layers()
    rig, meshes, wts = build(variant, extent, rows, layers, meshes)
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, f"{rig['name']}_e{extent:g}_r{rows}")
    write_atlas(layers, base + ".png", base + ".atlas")
    spine42.write(rig, base + ".json")
    ev = lbs.Evaluator(rig)
    frame0 = node_dump(base + ".json", base + ".atlas", {})
    out = []
    for deg in angles:
        pose = {"fore": {"rotation": sign * deg}}
        fr = node_dump(base + ".json", base + ".atlas", pose)
        mine = ev.frame(pose)
        c1 = max(float(np.abs(np.asarray(fr["slots"][s]["vertices"]) - np.asarray(mine["slots"][s]["vertices"])).max())
                 for s in meshes)
        row = {"angle": sign * deg, "folds": fr["folds"], "folds_numpy": ev.folds(pose)["folds"], "c1": c1}
        row.update(area_metrics(meshes, frame0, fr, extent))
        out.append(row)
    j = joint(variant == "helper", extent)
    info = {"variant": variant, "extent": extent, "rows": rows, "sign": sign, "skeleton": base + ".json",
            "vertices": {k: len(m["verts"]) for k, m in meshes.items()},
            "grid_vertices": {k: m["stats"]["grid_vertices"] for k, m in meshes.items()},
            "b2": ({k: weights.b2(m["verts"], wts[k], [j], canvas_bones(variant == "helper"))["value"]
                    for k, m in meshes.items()} if variant != "rigid" else None)}
    info.update(limits(out))
    return info, out


def limits(rows):
    """Largest |angle|, scanning outward and stopping at the first failure:
    hard = 0 folds; g2 = 0 folds and every band triangle >= G2_TRI;
    soft_sum / soft_union = 0 folds and band_sum / band_union >= SOFT_AREA."""
    tests = {
        "hard": lambda r: r["folds"] == 0,
        "g2": lambda r: r["folds"] == 0 and r["min_tri"] >= G2_TRI,
        "soft_sum": lambda r: r["folds"] == 0 and r["band_sum"] >= SOFT_AREA,
        "soft_union": lambda r: r["folds"] == 0 and r["band_union"] >= SOFT_AREA,
    }
    out = {}
    for key, ok in tests.items():
        best = 0.0
        for r in rows:
            if not ok(r):
                break
            best = r["angle"]
        out[key] = best
    return out


def continuum(theta_deg, extent, helper):
    """Continuum predictions for a straight limb of half-width HW: band area
    ratio (linear ramp) and the first folding angle."""
    d = math.radians(theta_deg / 2 if helper else theta_deg)
    fold = 4 * math.degrees(math.atan(extent / 2)) if helper else 2 * math.degrees(math.atan(extent))
    return {"area": 1 - (1 - math.cos(d)) / 6, "fold_at": fold}


def parse_angles(spec):
    a, b, s = (float(x) for x in spec.split(":"))
    return [a + i * s for i in range(int(round((b - a) / s)) + 1)]


def gate_scan(variant, extent, rows, layers, meshes, step=2.0, max_angle=150.0, sheet_dir=None):
    """budget.scan of one variant: {"+": entry, "-": entry} (budget.json
    form) for the elbow, plus the contact sheet path when sheet_dir is set."""
    rig, _, _ = build(variant, extent, rows, layers, meshes)
    images = {name: rgba for name, (rgba, _) in layers.items()}
    jn = [{"name": "elbow", "bone": "fore", "dirs": [1, -1], "band": dict(joint(variant == "helper", extent),
                                                                          center=tuple(to_world(ELBOW)),
                                                                          axis=(1.0, 0.0))}]
    b = budgetmod.scan(rig, images, jn, step=step, max_angle=max_angle)
    out = {"budget": b["joints"]["elbow"], "seconds": b["meta"]["seconds"]}
    if sheet_dir:
        os.makedirs(sheet_dir, exist_ok=True)
        path = os.path.join(sheet_dir, f"elbow42_{variant}.png")
        budgetmod.write_sheets(path, rig, images, jn, budget=b, max_angle=max_angle, every=30.0)
        out["sheet"] = path
    return out


def print_gates(variant, g):
    for d in ("+", "-"):
        e = g["budget"][d]
        ff = ", ".join(f"{k} {v['angle']:g}" for k, v in sorted(e["first_fail"].items(), key=lambda kv: abs(kv[1]["angle"])))
        print(f"  {variant:7s} {d}  soft {e['soft']:5.0f} ({e['limit_by']})  hard {e['hard']:5.0f} ({e['hard_by']})"
              f"  first fails: {ff or '-'}")


def print_sweep(info, rows):
    print(f"\n{info['variant']} (extent {info['extent']} hw, {info['rows']} rows per half, "
          f"bend {'+' if info['sign'] > 0 else '-'}): verts {info['vertices']} grid {info['grid_vertices']} "
          f"B2 {info['b2']}")
    print("  angle folds(rt) folds(np) band_sum band_union upper_arm forearm window min_tri  c1_px")
    for r in rows:
        la = r["layers"]
        print(f"  {r['angle']:5.0f} {r['folds']:8d} {r['folds_numpy']:9d} {r['band_sum']:8.3f} {r['band_union']:10.3f} "
              f"{la['upper_arm']:9.3f} {la['forearm']:7.3f} {r['window']:6.3f} {r['min_tri']:7.3f} {r['c1']:.1e}")
    print(f"  hard (0 folds) {info['hard']:.0f}  G2 tri>=0.3 {info['g2']:.0f}  "
          f"soft band_sum>=0.9 {info['soft_sum']:.0f}  soft band_union>=0.9 {info['soft_union']:.0f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="/tmp/spinegen-elbow42")
    ap.add_argument("--extent", type=float, default=mesh.BAND_EXTENT)
    ap.add_argument("--rows", type=int, default=mesh.BAND_ROWS)
    ap.add_argument("--angles", default="0:150:10")
    ap.add_argument("--both-ways", action="store_true", help="also bend the elbow the other way")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--extents", help="also tabulate these band extents (comma list) at --at degrees")
    ap.add_argument("--at", type=float, default=120.0)
    ap.add_argument("--json")
    ap.add_argument("--gates", action="store_true", help="also scan every variant with the raster art gates")
    ap.add_argument("--gate-step", type=float, default=2.0)
    ap.add_argument("--sheet", help="with --gates: write one contact sheet per variant into this directory")
    args = ap.parse_args(argv)
    angles = parse_angles(args.angles)
    layers = arm_layers()
    results = {"sweeps": [], "extents": []}
    meshes = build_meshes(layers, args.extent, args.rows)
    for sign in ((1, -1) if args.both_ways else (1,)):
        for v in args.variants.split(","):
            info, rows = sweep(v, args.out, angles, args.extent, args.rows, sign, layers, meshes)
            info["rows_out"] = rows
            results["sweeps"].append(info)
            print_sweep(info, rows)
    if args.extents:
        print(f"\nband extent table (rows {args.rows} per half), values at {args.at:g} deg; "
              "limits from the 0..150 sweep")
        print("  extent variant  verts  B2_min  folds band_sum band_union min_tri | hard  g2 soft_sum soft_union"
              " | continuum area fold_at")
        for ext in (float(x) for x in args.extents.split(",")):
            m = build_meshes(layers, ext, args.rows)
            for v in ("ramp", "helper"):
                info, rows = sweep(v, args.out, angles, ext, args.rows, 1, layers, m)
                r = min(rows, key=lambda r: abs(r["angle"] - args.at))
                ref = continuum(args.at, ext, v == "helper")
                print(f"  {ext:6.2f} {v:7s} {sum(info['vertices'].values()):5d} {min(info['b2'].values()):7.3f} "
                      f"{r['folds']:6d} {r['band_sum']:8.3f} {r['band_union']:10.3f} {r['min_tri']:7.3f} | "
                      f"{info['hard']:4.0f} {info['g2']:3.0f} {info['soft_sum']:8.0f} {info['soft_union']:10.0f} | "
                      f"{ref['area']:14.3f} {ref['fold_at']:7.1f}")
                results["extents"].append(dict(info, at=r))
    if args.gates:
        print(f"\nraster art gates (spinegen.budget, step {args.gate_step:g}, 0..{max(angles):g} deg; "
              f"extent {args.extent:g} hw)")
        results["gates"] = {}
        for v in args.variants.split(","):
            g = gate_scan(v, args.extent, args.rows, layers, meshes, args.gate_step, max(angles), args.sheet)
            results["gates"][v] = g
            print_gates(v, g)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=1)
    return results


if __name__ == "__main__":
    main()
