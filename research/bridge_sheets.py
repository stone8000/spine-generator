"""Rigid vs joint contact sheets for the motion-kit bridge (BRIDGE_REPORT 看圖).

    python research/bridge_sheets.py <bridge dir rigid> <bridge dir joint> -o <out dir>
        [--scale 0.6] [--all-frames]

Inputs are two `python -m spinegen.bridge_motionkit` output dirs of the same
character (rigid and joint builds). For every kit animation this plays the
generated 4.3 file in spine-core 4.3.13 (`qa/run.mjs dump --runtime 4.3`,
30 fps, physics on), picks the PEAK frames from the motion itself (where
the preset's driven bone is furthest out: the punch's full reach and its
wind-up, the celebrate's highest and lowest fists, the idle's deepest dip
and furthest jab, the hit's knock-back) and draws those frames for both
rigs with spinegen.raster, using the WORLD VERTICES the 4.3 runtime dumped
(not a re-skinning), so the picture is what a 4.3 player shows.

Each drawn frame is also judged by spinegen.art_gates (G1 folds, G4 seam
gap per joint, G5 holes, G6 ghosts) against the rig's setup frame, and the
ankle gap (shin tip to foot origin: a planted foot hangs from its IK
target, so a leg that cannot reach leaves the boot behind) is measured on
every frame. --all-frames runs the art gates on every 30 fps frame instead
of the peaks only.

Writes <anim>.png (rows rigid / joint, one column per peak frame),
<anim>_arms.png (the same frames cut to the arms at 2x), <anim>_legs.png
(the frame with the largest ankle gap, legs at 2x) and sheets.json with
every number. Third-party art stays outside the repo:
the images are read from the bridge dirs and written to <out dir> only.
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spinegen import art_gates as ag  # noqa: E402
from spinegen import budget, raster  # noqa: E402
from spinegen import ir as irmod  # noqa: E402
from spinegen.emit import spine43  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QA_RUN = os.path.join(REPO, "qa", "run.mjs")
BG = (236, 236, 232)


def dump43(skeleton, atlas, anim, fps=30):
    p = subprocess.run(["node", QA_RUN, "dump", "--runtime", "4.3", "--skeleton", skeleton, "--atlas", atlas,
                        "--anim", anim, "--fps", str(fps)], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return json.loads(p.stdout)["frames"]


class Rig:
    """One bridge output: its prepared IR (the 4.3 file's vertex order), images, joints, dumps."""

    def __init__(self, bridge_dir):
        with open(os.path.join(bridge_dir, "bridge.json")) as f:
            self.rep = json.load(f)
        name = self.rep["name"]
        self.name = name
        ir = irmod.load(os.path.join(bridge_dir, f"{name}.bridge_rig.json"))
        self.ir = spine43.prepare(ir)  # hull-first meshes: the order the runtime dumps vertices in
        self.files = self.rep["files"]
        self.images = raster.load_atlas_images(self.files["atlas"])
        joints_path = os.path.join(os.path.dirname(self.rep["input"]["rig"]), "joints.json")
        self.joints = budget.load_joints(joints_path) if os.path.exists(joints_path) else []
        with open(self.files["motion"]) as f:
            self.anims = list(json.load(f).get("animations", {}))
        self.presets = {a["name"]: a["preset"] for a in json.load(open(self.files["recipe"]))["animations"]}
        self.roles = self.rep["recipe"]["roles"]
        self.facing = self.rep["facing"]
        self.dumps = {a: dump43(self.files["motion"], self.files["atlas"], a) for a in self.anims}
        self.r = None

    def setup_canvas(self, canvas):
        self.r = raster.Renderer(self.ir, self.images, canvas=canvas, missing="skip")
        self.rest = self.r.frame()

    def frame(self, f):
        """A raster.Frame of one dumped frame: the dump's bone matrices and world vertices."""
        world = [tuple(f["bones"][b["name"]][k] for k in "abcdxy") for b in self.ir["bones"]]
        geoms = self.r.geometry(world)
        skin_err = 0.0
        for s, g in list(geoms.items()):
            v = np.asarray(f["slots"][s]["vertices"], float).reshape(-1, 2)
            if v.shape != g.xy.shape:
                raise ValueError(f"{s}: runtime has {v.shape[0]} vertices, IR {g.xy.shape[0]}")
            skin_err = max(skin_err, float(np.abs(v - g.xy).max()))
            geoms[s] = g._replace(xy=v)
        frags = {s: self.r.fragments(g) for s, g in geoms.items()}
        fr = self.r.composite(self.r.canvas, world, geoms, frags)
        fr.skin_err = skin_err
        return fr

    def ankle_gap(self, f):
        """Largest shin-tip to foot-origin distance in one frame (planted feet hang from their IK target)."""
        worst = 0.0
        for ik in self.ir["ik"]:
            shin = ik["bones"][-1]
            feet = [b["name"] for b in self.ir["bones"] if b["parent"] == ik["target"]]
            if not feet or not ik["name"].startswith("leg"):
                continue
            b = f["bones"][shin]
            L = next(x["length"] for x in self.ir["bones"] if x["name"] == shin)
            tip = (b["x"] + b["a"] * L, b["y"] + b["c"] * L)
            for foot in feet:
                q = f["bones"][foot]
                worst = max(worst, math.hypot(tip[0] - q["x"], tip[1] - q["y"]))
        return worst


def peaks(rig, anim):
    """[(frame index, label)] of a preset's peak frames, from the motion itself."""
    frames = rig.dumps[anim]
    preset = rig.presets.get(anim, "")
    F = rig.facing
    R = rig.roles
    pos = lambda b: np.array([[f["bones"][b]["x"], f["bones"][b]["y"]] for f in frames])  # noqa: E731
    if preset == "punch":
        x = pos(R["fist"])[:, 0] * F
        return [(int(x.argmin()), "wind-up"), (int(x.argmax()), "full reach")]
    if preset == "celebrate":
        y = np.mean([pos(f)[:, 1] for f in R["fists"]], axis=0)
        return [(int(y.argmax()), "fists highest"), (int(y.argmin()), "fists lowest")]
    if preset.startswith("fighter-idle"):
        hy = pos(R["hip"])[:, 1]
        out = [(int(hy.argmin()), "deepest dip"), (int(hy.argmax()), "highest")]
        if R.get("fistFront"):
            fx = pos(R["fistFront"])[:, 0] * F
            out.append((int(fx.argmax()), "front fist out"))
        return out
    if preset == "hit-react":
        hx = pos(R["hip"])[:, 0]
        d = np.abs(hx - hx[0])
        return [(int(d.argmax()), "knocked back")]
    d = np.zeros(len(frames))
    for b in frames[0]["bones"]:
        p = pos(b)
        d = np.maximum(d, np.hypot(*(p - p[0]).T))
    return [(int(d.argmax()), "largest move")]


def gates_of(rig, fr):
    res = ag.evaluate(rig.rest, fr, rig.joints)
    out = {}
    for g in res["gates"]:
        key = g["gate"] if "gate" in g else g.get("id")
        jn = g.get("joint")
        out[f"{key}:{jn}" if jn else key] = {"pass": g["pass"], "value": g.get("value")}
    return res["pass"], out


def summarize(gates):
    """Failing gates of one frame, compact: {"G4:elbow_l": value, ...}."""
    return {k: v["value"] for k, v in gates.items() if not v["pass"]}


def tile(fr, bg=BG):
    rgba = fr.rgba()
    img = Image.new("RGBA", (rgba.shape[1], rgba.shape[0]), bg + (255,))
    img.alpha_composite(Image.fromarray(rgba, "RGBA"))
    return img.convert("RGB")


LIMBS = {"arms": ("upperarm", "forearm", "hand"), "legs": ("shin", "foot", "thigh")}


def limb_box(rig, fr, limbs="arms", pad=30):
    """Pixel box around both arms' (or legs') slots in a frame."""
    slots = [s["name"] for s in rig.ir["slots"] if s["bone"].startswith(LIMBS[limbs])]
    pts = np.concatenate([fr.geoms[s].xy for s in slots if s in fr.geoms])
    px = rig.r.canvas.to_px(pts)
    x0, y0 = px.min(0) - pad
    x1, y1 = px.max(0) + pad
    return int(max(0, x0)), int(max(0, y0)), int(x1), int(y1)


def sheet(rows, labels, captions, cell, title):
    """rows: [[PIL image per column] per row]; one label per row, one caption per column."""
    left, top = 70, 46
    W = left + len(captions) * cell[0]
    H = top + len(rows) * (cell[1] + 18)
    out = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(out)
    d.text((6, 6), title, fill=(0, 0, 0))
    for c, cap in enumerate(captions):
        d.text((left + c * cell[0] + 4, 24), cap, fill=(0, 0, 0))
    for r, (label, imgs) in enumerate(zip(labels, rows)):
        y = top + r * (cell[1] + 18)
        d.text((6, y + cell[1] // 2), label, fill=(0, 0, 0))
        for c, (img, note) in enumerate(imgs):
            im = img.copy()
            im.thumbnail(cell)
            out.paste(im, (left + c * cell[0], y))
            d.text((left + c * cell[0] + 4, y + cell[1] + 2), note, fill=(160, 0, 0) if note else (0, 0, 0))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rigid")
    ap.add_argument("joint")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--scale", type=float, default=0.6)
    ap.add_argument("--all-frames", action="store_true")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    rigs = {"rigid": Rig(args.rigid), "joint": Rig(args.joint)}

    # one canvas for every frame of both rigs: the union of every dumped vertex
    pts = [np.asarray(s["vertices"], float).reshape(-1, 2)
           for rig in rigs.values() for fs in rig.dumps.values() for f in fs for s in f["slots"].values() if s["vertices"]]
    canvas = raster.bbox_canvas(np.concatenate(pts), margin=0.04, scale=args.scale)
    for rig in rigs.values():
        rig.setup_canvas(canvas)

    report = {"scale": args.scale, "canvas": {"origin": list(canvas.origin), "size": [canvas.W, canvas.H]}, "anims": {}}
    joint = rigs["joint"]
    for anim in joint.anims:
        picks = peaks(joint, anim)
        rows, arm_rows = [], []
        rec = {"preset": joint.presets.get(anim), "peaks": [], "rigs": {}}
        for name, rig in rigs.items():
            frames = rig.dumps[anim]
            gaps = [rig.ankle_gap(f) for f in frames]
            r_rec = {"ankle_gap_max_px": round(max(gaps), 2), "ankle_gap_t": frames[int(np.argmax(gaps))]["t"],
                     "peaks": []}
            judged = range(len(frames)) if args.all_frames else [i for i, _ in picks]
            fail_frames, worst = 0, {}
            drawn = {}
            for i in judged:
                fr = rig.frame(frames[i])
                ok, gates = gates_of(rig, fr)
                bad = summarize(gates)
                if bad:
                    fail_frames += 1
                    for k, v in bad.items():  # G2 is an area ratio: lower is worse
                        worst[k] = min(worst.get(k, 1.0), v) if k.startswith("G2") else max(worst.get(k, 0), v or 0)
                if i in dict(picks):
                    drawn[i] = (fr, bad)
            r_rec.update(judged_frames=len(judged), failing_frames=fail_frames, worst=worst)
            row, arm_row = [], []
            for i, label in picks:
                fr, bad = drawn[i]
                note = ", ".join(f"{k} {v:g}" if isinstance(v, (int, float)) else k for k, v in bad.items())[:60]
                img = tile(fr)
                row.append((img, note))
                x0, y0, x1, y1 = limb_box(rig, fr)
                crop = img.crop((x0, y0, min(x1, img.width), min(y1, img.height)))
                arm_row.append((crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS), note))
                r_rec["peaks"].append({"t": frames[i]["t"], "label": label, "failing": bad,
                                       "skin_err_px": round(fr.skin_err, 6), "ankle_gap_px": round(gaps[i], 2)})
            rec["rigs"][name] = r_rec
            rows.append(row)
            arm_rows.append(arm_row)
        rec["peaks"] = [{"t": joint.dumps[anim][i]["t"], "label": lab} for i, lab in picks]
        caps = [f"t={joint.dumps[anim][i]['t']:.3f}s {lab}" for i, lab in picks]
        cell = (rows[0][0][0].width, rows[0][0][0].height)
        sheet(rows, list(rigs), caps, cell, f"{anim} ({rec['preset']}): 4.3.13 runtime vertices, rigid vs joint").save(
            os.path.join(args.out, f"{anim}.png"))
        acell = (max(im.width for r in arm_rows for im, _ in r), max(im.height for r in arm_rows for im, _ in r))
        sheet(arm_rows, list(rigs), caps, acell, f"{anim}: arms at 2x").save(os.path.join(args.out, f"{anim}_arms.png"))
        # the legs at the joint rig's largest ankle gap, both rigs
        gi = int(np.argmax([joint.ankle_gap(f) for f in joint.dumps[anim]]))
        leg_row = []
        for name, rig in rigs.items():
            fr = rig.frame(rig.dumps[anim][gi])
            img = tile(fr)
            x0, y0, x1, y1 = limb_box(rig, fr, "legs")
            crop = img.crop((x0, y0, min(x1, img.width), min(y1, img.height)))
            leg_row.append([(crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS),
                             f"ankle gap {rig.ankle_gap(rig.dumps[anim][gi]):.1f} px")])
        lcell = (max(r[0][0].width for r in leg_row), max(r[0][0].height for r in leg_row))
        sheet(leg_row, list(rigs), [f"t={joint.dumps[anim][gi]['t']:.3f}s largest ankle gap"], lcell,
              f"{anim}: legs at 2x").save(os.path.join(args.out, f"{anim}_legs.png"))
        rec["legs_t"] = joint.dumps[anim][gi]["t"]
        report["anims"][anim] = rec
        print(f"{anim}: " + "; ".join(f"{n} ankle gap {r['ankle_gap_max_px']} px, {r['failing_frames']}/"
                                       f"{r['judged_frames']} frames fail art gates {r['worst']}"
                                       for n, r in rec["rigs"].items()))
    with open(os.path.join(args.out, "sheets.json"), "w") as f:
        json.dump(report, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
