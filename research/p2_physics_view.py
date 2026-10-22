"""Physics frames to look at: the official runtime's bones, rendered.

    python research/p2_physics_view.py <rig dir> [<rig dir> ...] -o out.png --anim throwit
        [--slot left_arm_0_upper_arm] [--fps 60] [--crop x0,y0,x1,y1 (default: around the slot)] [--labels a,b]

For each rig dir (a spinegen.rigbuild output), spine-core (qa/run.mjs dump)
plays the animation with physics "update" and "none"; the frame where the
slot's vertices differ most (over the first rig) is rendered for both runs
with spinegen.raster from the runtime's own bone matrices, side by side,
with the "none" outline of the slot drawn over the "update" render (red).

WHY (P2 open issue 8): p2_physics_motion.py gives the physics-driven
vertex motion in px; whether a sleeve swings as cloth or tears near the
elbow is faster to see. The lbs renderer has no physics, so the bones come
from the runtime dump.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo root
from spinegen import ir as irmod  # noqa: E402
from spinegen import raster  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QA = os.path.join(REPO, "qa", "run.mjs")


def dump(skel, atlas, anim, physics, fps):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "d.json")
        subprocess.run(["node", QA, "dump", "--skeleton", skel, "--atlas", atlas, "--anim", anim, "--fps", str(fps),
                        "--physics", physics, "--out", out], check=True, capture_output=True)
        return json.load(open(out))["frames"]


def load(d):
    ir = irmod.load(os.path.join(d, "rig.json"), os.path.join(d, "motion.json"))
    atlas = glob.glob(os.path.join(d, "*.atlas"))[0]
    skel = os.path.splitext(atlas)[0] + ".json"
    return ir, atlas, skel


def world_of(r, frame):
    return [tuple(frame["bones"][b["name"]][k] for k in "abcdxy") for b in r.ev.bones]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--anim", default="throwit")
    ap.add_argument("--slot", default="left_arm_0_upper_arm")
    ap.add_argument("--fps", type=float, default=60)
    ap.add_argument("--crop", default=None, help="x0,y0,x1,y1 in canvas px of the first rig")
    ap.add_argument("--labels", default=None)
    ap.add_argument("--margin", type=float, default=0.6, help="canvas margin around the setup bbox (fraction)")
    a = ap.parse_args(argv)
    labels = a.labels.split(",") if a.labels else [os.path.basename(os.path.normpath(d)) for d in a.dirs]
    runs, k_best = [], None
    for d in a.dirs:
        ir, atlas, skel = load(d)
        on, off = dump(skel, atlas, a.anim, "update", a.fps), dump(skel, atlas, a.anim, "none", a.fps)
        if k_best is None:
            diff = [np.linalg.norm(np.asarray(u["slots"][a.slot]["vertices"]).reshape(-1, 2)
                                   - np.asarray(o["slots"][a.slot]["vertices"]).reshape(-1, 2), axis=1).max()
                    for u, o in zip(on, off)]
            k_best = int(np.argmax(diff))
        runs.append((ir, atlas, on[k_best], off[k_best]))
    tiles = []
    canvas = None
    for (ir, atlas, fu, fo), lab in zip(runs, labels):
        r = raster.Renderer(ir, raster.load_images(atlas), missing="skip", canvas=canvas, margin=a.margin)
        canvas = canvas or r.canvas
        for fr, what in ((fo, "none"), (fu, "update")):
            f = r.frame(world=world_of(r, fr))
            rgba = f.rgba()
            al = rgba[..., 3:4] / 255.0
            img = Image.fromarray((rgba[..., :3] * al + 255 * (1 - al)).astype(np.uint8))
            if what == "update":
                dr = ImageDraw.Draw(img)
                v = canvas.to_px(np.asarray(fo["slots"][a.slot]["vertices"]).reshape(-1, 2))
                att = dict(r.ev.shown())[a.slot]
                tris = np.asarray(irmod.geometry_of(ir, a.slot, att)["triangles"]).reshape(-1, 3)
                edges = {tuple(sorted(e)) for t in tris for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))}
                cnt = {}
                for t in tris:
                    for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                        cnt[tuple(sorted(e))] = cnt.get(tuple(sorted(e)), 0) + 1
                for e in edges:
                    if cnt[e] == 1:  # the outline of the "none" frame
                        dr.line([tuple(v[e[0]]), tuple(v[e[1]])], fill=(255, 0, 0), width=1)
            if a.crop:
                img = img.crop(tuple(int(x) for x in a.crop.split(",")))
            else:  # around the slot in both runs of the first rig, 80 px of context
                fu0, fo0 = runs[0][2], runs[0][3]
                pts = canvas.to_px(np.concatenate([np.asarray(x["slots"][a.slot]["vertices"]).reshape(-1, 2)
                                                   for x in (fu0, fo0)]))
                lo, hi = pts.min(0) - 80, pts.max(0) + 80
                img = img.crop((int(max(lo[0], 0)), int(max(lo[1], 0)), int(hi[0]), int(hi[1])))
            tiles.append((img, f"{lab} {what}"))
    W, H = sum(t.width for t, _ in tiles), max(t.height for t, _ in tiles)
    sheet = Image.new("RGB", (W, H + 20), (255, 255, 255))
    x = 0
    for t, label in tiles:
        sheet.paste(t, (x, 20))
        ImageDraw.Draw(sheet).text((x + 4, 4), label, fill=(0, 0, 0))
        x += t.width
    sheet.save(a.out)
    print(a.out, f"frame {k_best} (t = {k_best / a.fps:.3f} s)")


if __name__ == "__main__":
    main()
