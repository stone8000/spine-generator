"""P5 look-at sheet: the shipped Hard Time warden next to the generator's
rebuild, setup and animation frames (physics none: spinegen.raster on
spinegen.playback's sampled pose), optionally a forced pose.

    python research/p5_view.py <shipped.json> <shipped.atlas> <rebuild dir> -o out.png
        [--anim react --times 0.2,0.4] [--pose '{"bone": {"rotation": 60}}'] [--scale 0.4]

Outputs go outside the repo (the warden is the user's game art)."""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spinegen import playback, raster  # noqa: E402


def frame(ir, images, t=None, anim=None, extra=None, canvas=None):
    smp = playback.sample(ir, anim, t) if anim else {"bones": None, "state": None, "attachments": None,
                                                      "deform": None}
    pose = smp["bones"]
    if extra:
        pose = dict(pose or {})
        by = {b["name"]: b for b in ir["bones"]}
        for b, v in extra.items():
            cur = dict((pose or {}).get(b) or {})
            for k, d in v.items():
                cur[k] = cur.get(k, by[b][k]) + d
            pose[b] = cur
    r = raster.Renderer(ir, images, canvas=canvas)
    return r.frame(pose, smp["state"], attachments=smp["attachments"], deform=smp["deform"])


def on_white(img):
    a = img[..., 3:4] / 255.0
    return (img[..., :3] * a + 255 * (1 - a)).astype(np.uint8)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("skeleton")
    ap.add_argument("atlas")
    ap.add_argument("rebuild")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--anim")
    ap.add_argument("--times", default="0")
    ap.add_argument("--pose", help="extra local deltas on the REBUILD: {bone: {field: delta}}")
    ap.add_argument("--pose-src", help="extra local deltas on the SHIPPED rig")
    ap.add_argument("--scale", type=float, default=0.4)
    a = ap.parse_args(argv)
    from PIL import Image
    src = raster.load_rig(a.skeleton)
    src_img = raster.load_images(a.atlas)
    reb = raster.load_rig(os.path.join(a.rebuild, "rig.json"))
    atlas = next(os.path.join(a.rebuild, f) for f in os.listdir(a.rebuild) if f.endswith(".atlas"))
    reb_img = raster.load_images(atlas)
    canvas = raster.Canvas((-900.0, 2300.0), (1800.0, 2350.0), a.scale)
    rows = []
    for t in [float(x) for x in a.times.split(",")]:
        pa = json.loads(a.pose_src) if a.pose_src else None
        pb = json.loads(a.pose) if a.pose else None
        A = on_white(frame(src, src_img, t, a.anim, pa, canvas).rgba())
        B = on_white(frame(reb, reb_img, t, a.anim, pb, canvas).rgba())
        rows.append(np.concatenate([A, np.full((A.shape[0], 8, 3), 255, np.uint8), B], 1))
    Image.fromarray(np.concatenate(rows, 0) if len(rows) == 1 else np.concatenate(rows, 1)).save(a.out)
    print(a.out)


if __name__ == "__main__":
    main()
