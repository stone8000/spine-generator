"""The same frames of several bridge outputs side by side (BRIDGE_REPORT 看圖).

    python research/bridge_compare.py <out.png> <arms|legs> <anim> <frame,frame,...> <bridge dir> [<bridge dir> ...]

WHY: a bridge option (leg softness, the kit's IK guard) changes the pose of
the same preset frame; looking at it means drawing that frame of each
output next to each other on one canvas, cut to the limbs at 2x. Uses the
4.3.13 runtime's world vertices exactly as research/bridge_sheets.py does.
Each cell is captioned with that frame's ankle gap, signed: short of the
foot (-) or past it into the boot (+). Third-party art stays outside the
repo: write <out.png> under the bridge output folder.
"""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge_sheets as bs  # noqa: E402
from spinegen import raster  # noqa: E402


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 5:
        print(__doc__)
        return 2
    out, limbs, anim, frames, *dirs = argv
    frames = [int(x) for x in frames.split(",")]
    rigs = [bs.Rig(d) for d in dirs]
    pts = [np.asarray(s["vertices"], float).reshape(-1, 2)
           for r in rigs for f in r.dumps[anim] for s in f["slots"].values() if s["vertices"]]
    canvas = raster.bbox_canvas(np.concatenate(pts), margin=0.04, scale=0.6)
    rows = []
    for r in rigs:
        r.setup_canvas(canvas)
        row = []
        for i in frames:
            f = r.dumps[anim][i]
            fr = r.frame(f)
            img = bs.tile(fr)
            x0, y0, x1, y1 = bs.limb_box(r, fr, limbs)
            crop = img.crop((x0, y0, min(x1, img.width), min(y1, img.height)))
            short, past = r.ankle_along(f)
            row.append((crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS),
                        f"ankle {short:+.1f} / {past:+.1f} px"))
        rows.append(row)
    cell = (max(im.width for r in rows for im, _ in r), max(im.height for r in rows for im, _ in r))
    # parent/name: two outputs of one character differ by their folder, not their name
    labels = ["/".join(os.path.normpath(d).split(os.sep)[-2:])[-24:] for d in dirs]
    caps = [f"{anim} frame {i} t={rigs[0].dumps[anim][i]['t']:.3f}s" for i in frames]
    bs.sheet(rows, labels, caps, cell, f"{anim}: {limbs} at 2x, " + " / ".join(dirs)[:150]).save(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
