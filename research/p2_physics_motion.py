"""How far physics moves each slot's vertices, in the official runtime.

    python research/p2_physics_motion.py <skeleton.json> <atlas> [--anims a,b] [--fps 30] [--json out.json]

For each animation, spine-core (qa/run.mjs dump) plays it twice: physics
"update" and physics "none"; the value per slot is the largest distance
between the two runs' world vertices over every frame. Prints a table
(slot x animation, px) and the worst animation per slot.

WHY (P2 review, A2_no_regression_secondary_motion): the source's physics
bones (cuffs, pant hems) drive part of the sleeves and thighs; a rig change
that strips their vertex weights leaves the constraints running on bones
that move nothing, and no other gate notices (G1/M1/C1 pass, P1 measures
bone tips, not vertices). This is the vertex-level number: physics-driven
motion of the art itself.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QA = os.path.join(REPO, "qa", "run.mjs")
DEFAULT_ANIMS = ("throwit", "cheer", "chestbeat", "flinch")


def dump(skeleton, atlas, anim, physics, fps):
    """{slot: (frames, n, 2) world vertices} of one runtime playback."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "d.json")
        subprocess.run(["node", QA, "dump", "--skeleton", skeleton, "--atlas", atlas, "--anim", anim,
                        "--fps", str(fps), "--physics", physics, "--out", out], check=True)
        frames = json.load(open(out))["frames"]
    slots = {}
    for s in frames[0]["slots"]:
        vs = [np.asarray(f["slots"][s]["vertices"], float).reshape(-1, 2) for f in frames]
        if vs and all(len(v) == len(vs[0]) for v in vs) and len(vs[0]):
            slots[s] = np.stack(vs)
    return slots


def physics_motion(skeleton, atlas, anims=DEFAULT_ANIMS, fps=30):
    """{anim: {slot: max px}}: physics update vs none (module doc)."""
    out = {}
    for a in anims:
        on, off = dump(skeleton, atlas, a, "update", fps), dump(skeleton, atlas, a, "none", fps)
        out[a] = {s: float(np.linalg.norm(on[s] - off[s], axis=-1).max()) for s in on if s in off}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("skeleton")
    ap.add_argument("atlas")
    ap.add_argument("--anims", default=",".join(DEFAULT_ANIMS))
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = physics_motion(a.skeleton, a.atlas, a.anims.split(","), a.fps)
    slots = sorted({s for r in res.values() for s in r})
    anims = list(res)
    print(f"{'slot':28s}" + "".join(f"{x:>11s}" for x in anims) + "      max")
    for s in slots:
        vals = [res[x].get(s, 0.0) for x in anims]
        if max(vals) < 1e-6:
            continue
        print(f"{s:28s}" + "".join(f"{v:11.2f}" for v in vals) + f"{max(vals):9.2f}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"fps": a.fps, "motion_px": res}, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
