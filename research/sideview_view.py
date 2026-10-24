"""Side-view (profile) poses to look at: one row of whole-body tiles per rig.

    python research/sideview_view.py <rig dir> [<rig dir> ...] -o sheet.png
        [--poses setup,crouch:0.05,crouch:0.10,crouch:0.10:+40,stride:l:0.12,stride:r:-0.12,lift:r:0.10,rot:upperarm_r:90]
        [--labels a,b] [--scale 0.35] [--box x0,y0,w,h]

Pose specs (amounts in fractions of H, angles in degrees; leg IK on):
  setup
  crouch:<depth>[:<dx px>]      hips down depth x H, moved dx px in world x
  stride:<side>:<amount>        that foot's target moved amount x H along +x
                                (forward for a rig facing right), the hips half
                                way and lowered so both legs reach (budget.ik_scan)
  lift:<side>:<amount>          that foot's target up
  rot:<bone>:<deg>              a bone turned by deg from setup (local, IK on)
The legs' joints are marked (red = "l", blue = "r"). Each rig dir is a
spinegen.rigbuild output (rig.json, motion.json, <name>.atlas). Rendering is
spinegen.raster (geometry spinegen.lbs, pinned to spine-core by C1).
"""
import argparse
import glob
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo root
from spinegen import budget  # noqa: E402
from spinegen import ir as irmod  # noqa: E402
from spinegen import raster  # noqa: E402

MARK = {"l": (255, 0, 0), "r": (0, 0, 255)}


def load(d):
    ir = irmod.load(os.path.join(d, "rig.json"), os.path.join(d, "motion.json"))
    atlas = glob.glob(os.path.join(d, "*.atlas"))[0]
    return ir, raster.Renderer(ir, raster.load_images(atlas), missing="skip")


def pose_of(ir, r, spec):
    b = irmod.by_name(ir["bones"])
    legs = budget.ik_legs(ir)
    H = float(ir["H"])
    p = spec.split(":")
    if p[0] == "setup":
        return None
    probe = _Probe(ir, r, legs)
    if p[0] == "crouch":
        return probe.pose_of("crouch", amount=float(p[1]) * H, dx=float(p[2]) if len(p) > 2 else 0.0)
    if p[0] == "rot":
        return {p[1]: {"rotation": b[p[1]]["rotation"] + float(p[2])}}
    leg = next(l for l in legs if l["side"] == p[1])
    if p[0] == "stride":
        return probe.pose_of("step", leg, float(p[2]) * H)
    if p[0] == "lift":
        return probe.pose_of("lift", leg, float(p[2]) * H)
    if p[0] == "rot":
        return {p[1]: {"rotation": b[p[1]]["rotation"] + float(p[2])}}
    raise ValueError(spec)


class _Probe(budget.LegProbe):
    """LegProbe's pose arithmetic without its rendering setup."""

    def __init__(self, ir, r, legs):
        self.ir, self.legs, self.by = ir, legs, irmod.by_name(ir["bones"])
        ev = r.ev
        self.world0 = ev.world_dict(ev.world())
        self.length = {leg["name"]: budget._dist(self.world0[leg["thigh"]], self.world0[leg["shin"]])
                       + float(self.by[leg["shin"]].get("length") or 0.0) for leg in legs}


def tile(ir, r, spec, box, scale):
    pose = pose_of(ir, r, spec)
    x0, y0, w, h = box
    cv = raster.Canvas((x0, y0 + h), (w, h), scale)
    fr = r.frame(pose, canvas=cv)
    rgba = fr.rgba()
    a = rgba[..., 3:4].astype(float) / 255
    rgb = np.clip(np.round(rgba[..., :3] * a + 255 * (1 - a)), 0, 255).astype(np.uint8)
    img = Image.fromarray(rgb, "RGB")
    d = ImageDraw.Draw(img)
    ev = r.ev
    wd = ev.world_dict(ev.world(pose))
    for leg in budget.ik_legs(ir):
        for n in (leg["thigh"], leg["shin"], leg["target"]):
            x, y = cv.to_px(wd[n][4:6])[0]
            d.ellipse([x - 4, y - 4, x + 4, y + 4], outline=MARK.get(leg["side"], (0, 0, 0)), width=2)
    d.text((4, 4), spec, fill=(0, 0, 0))
    return img


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rigs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--poses", default="setup,crouch:0.05,crouch:0.10,crouch:0.15,crouch:0.10:-40,crouch:0.10:40,"
                                        "stride:r:0.12,stride:l:-0.12,lift:r:0.10")
    ap.add_argument("--labels", default=None)
    ap.add_argument("--scale", type=float, default=0.35)
    ap.add_argument("--box", default="-420,-30,840,1420", help="world x0,y0,w,h of each tile")
    a = ap.parse_args(argv)
    box = [float(v) for v in a.box.split(",")]
    labels = a.labels.split(",") if a.labels else [os.path.basename(os.path.normpath(d)) for d in a.rigs]
    rows = []
    for d, lab in zip(a.rigs, labels):
        ir, r = load(d)
        tiles = [tile(ir, r, s, box, a.scale) for s in a.poses.split(",")]
        w = sum(t.width for t in tiles)
        row = Image.new("RGBA", (w + 120, max(t.height for t in tiles)), (255, 255, 255, 255))
        ImageDraw.Draw(row).text((4, 4), lab, fill=(0, 0, 0, 255))
        x = 120
        for t in tiles:
            row.paste(t, (x, 0))
            ImageDraw.Draw(row).line([(x, 0), (x, row.height)], fill=(200, 200, 200, 255))
            x += t.width
        rows.append(row)
    out = Image.new("RGBA", (max(r.width for r in rows), sum(r.height for r in rows)), (255, 255, 255, 255))
    y = 0
    for r in rows:
        out.paste(r, (0, y))
        y += r.height
    out.convert("RGB").save(a.out)
    print(a.out)


if __name__ == "__main__":
    main()
