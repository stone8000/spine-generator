"""P5 leg-style sheet: the shipped Hard Time warden's crouch next to two
rebuilds (e.g. the "outward" mascot legs and the "forward" realistic legs),
one row per rig, one column per pose (setup, then crouches).

    python research/p5_leg_view.py <shipped.json> <shipped.atlas> <rebuild dir> [<rebuild dir> ...] -o out.png
        [--poses 0.03:0,0.05:0,0.10:0,0.10:-40,0.10:40] [--scale 0.35] [--labels shipped,outward,forward]

A pose is depth:dx (hips down depth x H, sideways dx px). The shipped rig
has no leg solver of its own: its animations key the knee targets
(knee_l / knee_r, under root) at half the hips' move, so that is what its
column shows (thigh and shin squashed by one-bone IK with compress). The
rebuilds use their own leg IK. The legs' joints are marked (red "l", blue
"r": hip, knee, ankle). Rendering is spinegen.raster (geometry
spinegen.lbs, pinned to spine-core by C1). Outputs go outside the repo
(the warden is the user's game art)."""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spinegen import ir as irmod  # noqa: E402
from spinegen import raster  # noqa: E402

MARK = {"l": (255, 0, 0), "r": (0, 0, 255)}
SHIPPED_KNEE_SHARE = 0.5  # the shipped animations key knee_l/r at half the hips' translate


def _on_white(img):
    a = img[..., 3:4] / 255.0
    return Image.fromarray((img[..., :3] * a + 255 * (1 - a)).astype(np.uint8))


def tile(ir, images, canvas, depth, dx, shipped=False):
    by = irmod.by_name(ir["bones"])
    H = float(ir.get("H") or ir["skeleton"]["height"])
    pose = None
    marks = []
    if shipped:
        hips = "hips"
        if depth or dx:
            pose = {hips: {"x": by[hips]["x"] + dx, "y": by[hips]["y"] - depth * H}}
            for k in ("knee_l", "knee_r"):
                pose[k] = {"x": by[k]["x"] + SHIPPED_KNEE_SHARE * dx,
                           "y": by[k]["y"] - SHIPPED_KNEE_SHARE * depth * H}
        for s in "lr":
            marks.append((s, [f"leg_{s}", f"shin_{s}", f"foot_{s}"]))
    else:
        legs = irmod.leg_chains(ir)
        hips = by[legs[0]["thigh"]]["parent"]
        if depth or dx:
            pose = {hips: {"x": by[hips]["x"] + dx, "y": by[hips]["y"] - depth * H}}
        for leg in legs:
            marks.append((leg["side"], [leg["thigh"], leg["shin"], leg["target"]]))
    r = raster.Renderer(ir, images, canvas=canvas, missing="skip")
    fr = r.frame(pose)
    w = r.ev.world_dict(r.ev.world(pose))
    img = _on_white(fr.rgba())
    dr = ImageDraw.Draw(img)
    for side, names in marks:
        pts = [tuple(fr.canvas.to_px([w[n][4:6]])[0]) for n in names]
        dr.line(pts, fill=MARK.get(side, (0, 0, 0)), width=2)
        for x, y in pts:
            dr.ellipse([x - 4, y - 4, x + 4, y + 4], outline=MARK.get(side, (0, 0, 0)), width=2)
    return img


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("skeleton")
    ap.add_argument("atlas")
    ap.add_argument("rebuilds", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--poses", default="0.03:0,0.05:0,0.10:0,0.10:-40,0.10:40")
    ap.add_argument("--scale", type=float, default=0.35)
    ap.add_argument("--labels", default=None)
    ap.add_argument("--box", default="-450,-20,900,1180", help="world x0,y0,w,h of each tile")
    a = ap.parse_args(argv)
    x0, y0, bw, bh = (float(v) for v in a.box.split(","))
    canvas = raster.Canvas((x0, y0 + bh), (bw, bh), a.scale)
    poses = [(0.0, 0.0)] + [tuple(float(v) for v in p.split(":")) for p in a.poses.split(",")]
    rigs = [("shipped", raster.load_rig(a.skeleton), raster.load_images(a.atlas), True)]
    labels = a.labels.split(",") if a.labels else ["shipped"] + [os.path.basename(os.path.dirname(d.rstrip("/")))
                                                                  for d in a.rebuilds]
    for d in a.rebuilds:
        atlas = next(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".atlas"))
        rigs.append((None, irmod.load(os.path.join(d, "rig.json"), os.path.join(d, "motion.json")),
                     raster.load_images(atlas), False))
    rows = []
    for (_, ir, images, shipped), label in zip(rigs, labels):
        tiles = [tile(ir, images, canvas, dep, dx, shipped) for dep, dx in poses]
        row = Image.new("RGB", (sum(t.width for t in tiles) + 110, max(t.height for t in tiles)), (255, 255, 255))
        ImageDraw.Draw(row).text((4, 4), label, fill=(0, 0, 0))
        x = 110
        for t in tiles:
            row.paste(t, (x, 0))
            x += t.width
        rows.append(row)
    head = 18
    sheet = Image.new("RGB", (rows[0].width, sum(r.height for r in rows) + head), (255, 255, 255))
    x = 110
    tw = rows[0].width - 110
    for dep, dx in poses:
        txt = "setup" if not (dep or dx) else f"{dep:g}H" + (f" dx{dx:+g}" if dx else "")
        ImageDraw.Draw(sheet).text((x + 4, 3), txt, fill=(0, 0, 0))
        x += tw // len(poses)
    y = head
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    sheet.save(a.out)
    print(a.out)


if __name__ == "__main__":
    main()
