"""A crouch to look at: the setup legs, then two rigs' crouch, side by side.

    python research/p2_crouch_view.py <rig dir before> <rig dir after> -o crouch.png [--depth 0.05] [--dx 0]

Each rig dir is a spinegen.rigbuild output (rig.json, motion.json, <name>.atlas).
The hips (the leg IK thighs' parent) drop by `depth` x H with the leg IK on,
and move `dx` px sideways (P2 review R2: a crouch with the weight shifted);
the legs' joints are marked (red = the "l" leg, blue = "r"), and the image is
cropped from just above the hips down.

WHY: rigbuild's crouch check (crouch()) gives numbers (knee moves, foot slide,
end bends); a knee that caves in or a boot that tilts is faster to see than to
read, and P2 review F1 asked to LOOK at the crouch after the fix. Rendering is
spinegen.raster (geometry spinegen.lbs, pinned to spine-core by C1).
"""
import argparse
import glob
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo root
from spinegen import ir as irmod  # noqa: E402
from spinegen import raster  # noqa: E402

MARK = {"l": (255, 0, 0), "r": (0, 0, 255)}


def frames(d, depth, dx=0.0):
    """[setup, crouch] PIL images of the rig in dir `d`, cropped to the legs."""
    ir = irmod.load(os.path.join(d, "rig.json"), os.path.join(d, "motion.json"))
    atlas = glob.glob(os.path.join(d, "*.atlas"))[0]
    r = raster.Renderer(ir, raster.load_images(atlas), missing="skip")
    b = irmod.by_name(ir["bones"])
    hips = b[irmod.leg_chains(ir)[0]["thigh"]]["parent"]
    out = []
    for pose in (None, {hips: {"x": b[hips]["x"] + dx, "y": b[hips]["y"] - depth * ir["H"]}}):
        fr = r.frame(pose)
        w = r.ev.world_dict(r.ev.world(pose))
        rgba = fr.rgba()
        a = rgba[..., 3:4] / 255.0
        img = Image.fromarray((rgba[..., :3] * a + 255 * (1 - a)).astype(np.uint8))
        dr = ImageDraw.Draw(img)
        for leg in irmod.leg_chains(ir):
            side = leg["side"]
            pts = [tuple(fr.canvas.to_px([w[n][4:6]])[0]) for n in (leg["thigh"], leg["shin"], leg["target"])]
            dr.line(pts, fill=MARK.get(side, (0, 0, 0)), width=2)
            for x, y in pts:
                dr.ellipse([x - 5, y - 5, x + 5, y + 5], outline=MARK.get(side, (0, 0, 0)), width=3)
        top = fr.canvas.to_px([w[hips][4:6]])[0][1]
        out.append(img.crop((0, max(0, int(top) - 120), img.width, img.height)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--depth", type=float, default=0.05, help="hips drop, fraction of H")
    ap.add_argument("--dx", type=float, default=0.0, help="hips moved sideways, px (world x)")
    a = ap.parse_args(argv)
    before, after = frames(a.before, a.depth, a.dx), frames(a.after, a.depth, a.dx)
    what = f"crouch {a.depth:g}H" + (f" dx {a.dx:+g}" if a.dx else "")
    tiles = [(before[0], "setup"), (before[1], f"{what}, before"), (after[1], f"{what}, after")]
    W, H = sum(t.width for t, _ in tiles), max(t.height for t, _ in tiles)
    sheet = Image.new("RGB", (W, H + 20), (255, 255, 255))
    x = 0
    for t, label in tiles:
        sheet.paste(t, (x, 20))
        ImageDraw.Draw(sheet).text((x + 4, 4), label, fill=(0, 0, 0))
        x += t.width
    sheet.save(a.out)
    print(a.out)


if __name__ == "__main__":
    main()
