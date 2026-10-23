"""One comparison sheet per kit animation: the source character / rigid rebuild / joint rebuild.

    python research/bridge_compare_sheets.py <source skeleton.json> <source atlas> <bridge dir rigid> <bridge dir joint>
        -o <out dir> [--map idle=idle,punch=throwit,...] [--radius 70]

Rows: the SOURCE rig playing its own animation where one is comparable (the
monkey: idle -> idle, punch -> throwit, celebrate -> cheer, hit -> flinch,
breathe -> idle; none for the jump and the fade-in openings, where the row
shows the source's setup pose), then the rigid and the joint rebuild playing
the kit's animation (`python -m spinegen.bridge_motionkit` outputs). Columns:
the kit animation's PEAK frames (research/bridge_sheets.py `peaks`, picked on
the joint rebuild; rigid shows the very same times). The source row uses the
same criterion on its own animation (hip lowest / highest, the front fist
furthest out, ...) with the source's bones, so a column compares like with
like, not the same clock time of two different motions.

Every image is drawn from the WORLD VERTICES of the official runtime (source:
spine-core 4.2.120, its own file; rebuilds: 4.3.13), physics on, 30 fps.
Rebuild cells carry the art gates' failures (spinegen.art_gates against the
rig's setup frame, as in bridge_sheets).

Writes <anim>.png (whole character) and <anim>_joints.png (per column a 2 x 2
mosaic at 2x: left elbow, right elbow / left knee, right knee, centred on
the joint bone's origin, `--radius` world px around it). Third-party art is
read from the inputs and written to <out dir> only.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge_sheets as bs  # noqa: E402
from spinegen import raster, spine42_reader  # noqa: E402

DEFAULT_MAP = "idle=idle,punch=throwit,celebrate=cheer,hit=flinch,breathe=idle"
# joint bone (its origin is the joint) per close-up, source vs rebuild
JOINTS = (("elbow L", "armL_fore", "forearm_l"), ("elbow R", "armR_fore", "forearm_r"),
          ("knee L", "legL_calf", "shin_l"), ("knee R", "legR_calf", "shin_r"))
SOURCE_ROLES = {"hip": "hip", "fist": "armR_hand", "fistFront": "armR_hand", "fists": ["armR_hand", "armL_hand"]}


class Source:
    """The source skeleton in its own runtime (4.2.120 reads the editor's file as the game does)."""

    def __init__(self, skeleton, atlas):
        self.skeleton, self.atlas = skeleton, atlas
        self.ir = spine42_reader.read(skeleton)
        self.images = raster.load_atlas_images(atlas)
        self.setup = self._dump(None)[0]
        self.dumps = {}
        self.presets = {}
        self.roles = SOURCE_ROLES
        self.facing = 1

    def _dump(self, anim):
        args = ["node", bs.QA_RUN, "dump", "--skeleton", self.skeleton, "--atlas", self.atlas]
        if anim:
            args += ["--anim", anim, "--fps", "30"]
        p = subprocess.run(args, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr)
        return json.loads(p.stdout)["frames"]

    def load(self, anim, preset):
        if anim not in self.dumps:
            self.dumps[anim] = self._dump(anim)
        self.presets[anim] = preset

    def setup_canvas(self, canvas):
        self.r = raster.Renderer(self.ir, self.images, canvas=canvas, missing="skip")

    frame = bs.Rig.frame


def joint_xy(f, bone):
    b = f["bones"][bone]
    return b["x"], b["y"]


def mosaic(img, canvas, f, bones, radius, zoom=2):
    """2 x 2 close-ups around the joints' origins, `radius` world px each side."""
    cells = []
    for b in bones:
        cx, cy = canvas.to_px(np.array([joint_xy(f, b)]))[0]
        r = radius * _px_per_unit(canvas)
        box = (int(cx - r), int(cy - r), int(cx + r), int(cy + r))
        c = img.crop(box).resize((int(2 * r * zoom), int(2 * r * zoom)), Image.LANCZOS)
        d = ImageDraw.Draw(c)
        d.ellipse((c.width / 2 - 3, c.height / 2 - 3, c.width / 2 + 3, c.height / 2 + 3), outline=(255, 0, 255))
        cells.append(c)
    w, h = cells[0].size
    out = Image.new("RGB", (2 * w + 4, 2 * h + 4), (255, 255, 255))
    for i, c in enumerate(cells):
        out.paste(c, ((i % 2) * (w + 4), (i // 2) * (h + 4)))
    return out


def _px_per_unit(canvas):
    a = canvas.to_px(np.array([[0.0, 0.0], [1.0, 0.0]]))
    return float(abs(a[1][0] - a[0][0]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source")
    ap.add_argument("source_atlas")
    ap.add_argument("rigid")
    ap.add_argument("joint")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--map", default=DEFAULT_MAP, help="kit animation=source animation, comma separated")
    ap.add_argument("--radius", type=float, default=70.0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--cell", type=int, default=420, help="whole-character cell width in px")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    amap = dict(kv.split("=") for kv in args.map.split(",") if kv)
    src = Source(args.source, args.source_atlas)
    rigs = {"rigid": bs.Rig(args.rigid), "joint": bs.Rig(args.joint)}
    joint = rigs["joint"]
    for a in joint.anims:
        if a in amap:
            src.load(amap[a], joint.presets.get(a))

    pts = [np.asarray(s["vertices"], float).reshape(-1, 2)
           for fs in [*[fs for r in rigs.values() for fs in r.dumps.values()], *src.dumps.values(), [src.setup]]
           for f in fs for s in f["slots"].values() if s["vertices"]]
    canvas = raster.bbox_canvas(np.concatenate(pts), margin=0.04, scale=args.scale)
    for r in (*rigs.values(), src):
        r.setup_canvas(canvas)
    for r in rigs.values():
        r.rest = r.r.frame()

    report = {}
    for anim in joint.anims:
        picks = bs.peaks(joint, anim)
        caps = [f"{lab}" for _, lab in picks]
        sa = amap.get(anim)
        if sa:
            src.presets[sa] = joint.presets.get(anim)  # one source animation can stand in for two presets
            sp = bs.peaks(src, sa)
            sp = sp + [sp[-1]] * (len(picks) - len(sp))
            # the kit and the source may name different peak sets (a fallback "largest move"): pair by position
            src_frames = [(src.dumps[sa][i], f"source '{sa}' t={src.dumps[sa][i]['t']:.2f}s {lab}") for i, lab in sp[:len(picks)]]
        else:
            src_frames = [(src.setup, "source: no comparable animation (setup pose)")] * len(picks)
        rows, jrows, notes = [], [], {"source": [n for _, n in src_frames]}
        row, jrow = [], []
        for f, note in src_frames:
            img = bs.tile(src.frame(f))
            row.append((img, note))
            jrow.append((mosaic(img, canvas, f, [s for _, s, _ in JOINTS], args.radius), note))
        rows.append(row)
        jrows.append(jrow)
        for name, rig in rigs.items():
            row, jrow, nn = [], [], []
            for i, lab in picks:
                f = rig.dumps[anim][i]
                fr = rig.frame(f)
                _, gates = bs.gates_of(rig, fr)
                bad = bs.summarize(gates)
                note = f"t={f['t']:.2f}s " + ", ".join(f"{k} {v:g}" if isinstance(v, (int, float)) else k for k, v in bad.items())[:70]
                img = bs.tile(fr)
                row.append((img, note))
                jrow.append((mosaic(img, canvas, f, [r for _, _, r in JOINTS], args.radius), note))
                nn.append({"t": f["t"], "label": lab, "failing": bad})
            notes[name] = nn
            rows.append(row)
            jrows.append(jrow)
        labels = ["source", "rigid", "joint"]
        full = rows[0][0][0]
        cell = (args.cell, int(args.cell * full.height / full.width))
        bs.sheet(rows, labels, caps, cell,
                 f"{anim} ({joint.presets.get(anim)}): source '{sa or '-'}' (4.2.120) / rigid / joint (4.3.13), runtime vertices"
                 ).save(os.path.join(args.out, f"{anim}.png"))
        m = jrows[0][0][0]
        bs.sheet(jrows, labels, caps, (m.width, m.height),
                 f"{anim}: elbows L R / knees L R at 2x, +-{args.radius:g} px around the joint (magenta ring)"
                 ).save(os.path.join(args.out, f"{anim}_joints.png"))
        report[anim] = {"source_anim": sa, "peaks": [{"t": joint.dumps[anim][i]["t"], "label": lab} for i, lab in picks],
                        "cells": notes}
        print(anim, "->", os.path.join(args.out, f"{anim}.png"))
    with open(os.path.join(args.out, "compare.json"), "w") as f:
        json.dump(report, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
