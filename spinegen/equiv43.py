"""One IR, two files: does the 4.3 emit play like the 4.2 emit?

    python -m spinegen.equiv43 <rig.json | skeleton-4.2.json> <atlas> [--out DIR] [--fps 30]
        [--tol 0.01] [--json]

WHY
  Spine 4.3 is the primary target and 4.2 stays for old games, so one IR
  must give the same character on both runtimes. The 4.2 emitter is proven
  against spine-core 4.2.120 (C1, reemit_check); this tool carries that
  proof over: emit 4.2 (emit.spine42) and 4.3 (emit.spine43) from the same
  IR, play every animation in its own runtime (spine-core 4.2.120 and
  4.3.13, `qa/run.mjs dump --runtime`), and compare world bone matrices and
  every slot's world vertices frame by frame. Both emitters share
  spine42.prepare(), so the vertex order is the same and vertex i is
  compared with vertex i.

  physics none     constraints and skinning only: expect <= --tol (0.01 px).
  physics update   the same plus physics. 4.3 changed physics: it
                   interpolates between fixed physics steps (xLag,
                   rotateLag, scaleLag; 4.2 shows the last step) and the
                   defaults differ (the emitters write every field, so only
                   the interpolation remains). This is reported, not judged:
                   the per-animation numbers and the physics bones they come
                   from say how much follow-through moves.

Input: an IR rig.json (spinegen-rig/1, motion.json next to it) or a Spine
4.2 skeleton JSON, read with spine42_reader. Output files go to --out (a
temp dir by default); nothing is written next to the input.
Exit 0 when physics-none frames agree within --tol, 1 when not, 2 when a
runtime is not installed.
"""
import argparse
import json
import os
import sys
import tempfile

import numpy as np

from . import ir as irmod
from . import oracle
from .emit import spine42, spine43
from .spine42_reader import read

MODES = ("none", "update")


def load_ir(path):
    """IR from a rig.json, or from a Spine 4.2 skeleton JSON."""
    with open(path) as f:
        head = json.load(f)
    if head.get("schema") == irmod.SCHEMA_RIG:
        return irmod.load(path)
    return read(path)


def emit_both(ir, out_dir, name):
    """(4.2 path, 4.3 path, {"42": report, "43": report})."""
    os.makedirs(out_dir, exist_ok=True)
    p42, p43 = os.path.join(out_dir, f"{name}.42.json"), os.path.join(out_dir, f"{name}.43.json")
    r42, r43 = {}, {}
    spine42.write(ir, p42, report=r42)
    spine43.write(ir, p43, report=r43)
    return p42, p43, {"42": r42, "43": r43}


def compare(fa, fb, skip_bones=(), slot_bone=None):
    """Largest differences between two frames lists of the same playback.

    {"frames", "max_px" (slot vertices), "bone_px" (origins), "bone_m"
    (matrix entries), "worst", "worst_bone", "mismatch"}. Bones in
    `skip_bones`, and slots on them (`slot_bone` maps slot -> bone), are left
    out: the bones the 4.3 emitter reports as playing differently
    (report["differs"]), so a test can hold everything else to the tolerance.
    """
    skip = set(skip_bones)
    slot_bone = slot_bone or {}
    out = {"frames": 0, "max_px": 0.0, "bone_px": 0.0, "bone_m": 0.0, "worst": None, "worst_bone": None,
           "mismatch": []}
    if len(fa) != len(fb):
        out["mismatch"].append(f"{len(fa)} vs {len(fb)} frames")
        return out
    for a, b in zip(fa, fb):
        out["frames"] += 1
        for name, ba in a["bones"].items():
            if name in skip:
                continue
            bb = b["bones"].get(name)
            if bb is None:
                out["mismatch"].append(f"bone {name} missing at t={a['t']}")
                continue
            d = float(np.hypot(ba["x"] - bb["x"], ba["y"] - bb["y"]))
            if d > out["bone_px"]:
                out["bone_px"] = d
                out["worst_bone"] = {"anim": a["anim"], "t": a["t"], "bone": name}
            out["bone_m"] = max(out["bone_m"], max(abs(ba[k] - bb[k]) for k in "abcd"))
        for slot, sa in a["slots"].items():
            if slot_bone.get(slot) in skip:
                continue
            sb = b["slots"].get(slot)
            if sb is None or sa["attachment"] != sb["attachment"] or len(sa["vertices"]) != len(sb["vertices"]):
                out["mismatch"].append(f"{slot} at t={a['t']}: {sa['attachment']!r} vs {sb and sb['attachment']!r}")
                continue
            if not sa["vertices"]:
                continue
            va = np.asarray(sa["vertices"], float).reshape(-1, 2)
            vb = np.asarray(sb["vertices"], float).reshape(-1, 2)
            dist = np.hypot(*(va - vb).T)
            i = int(dist.argmax())
            if dist[i] > out["max_px"]:
                out["max_px"] = float(dist[i])
                out["worst"] = {"anim": a["anim"], "t": a["t"], "slot": slot, "vertex": i}
    return out


def run(ir, atlas, out_dir, name="rig", fps=30, modes=MODES, skip_differs=False):
    """Emit both files and compare them in their runtimes, per mode and animation.

    skip_differs leaves out the bones (and their slots) the 4.3 emitter
    flags as playing differently; the flagged list is in result["differs"]
    either way."""
    p42, p43, reports = emit_both(ir, out_dir, name)
    skip = reports["43"]["differs"] if skip_differs else ()
    slot_bone = {s["name"]: s["bone"] for s in ir["slots"]}
    anims = [a["name"] for a in ir.get("animations", [])]
    result = {"files": {"4.2": p42, "4.3": p43}, "atlas": atlas, "fps": fps,
              "warnings": {"4.2": reports["42"]["warnings"], "4.3": reports["43"]["warnings"]},
              "defaults_43": reports["43"]["defaults"], "half_angle": reports["43"]["half_angle"],
              "differs": reports["43"]["differs"], "skipped": list(skip), "modes": {}}
    for mode in modes:
        per = {}
        for anim in [None, *anims]:
            if anim is None and mode != modes[0]:
                continue  # the setup pose does not depend on physics stepping
            kw = dict(anim=anim, fps=fps, physics=mode) if anim else {}
            da = oracle.dump(p42, atlas, runtime="4.2", **kw)["frames"]
            db = oracle.dump(p43, atlas, runtime="4.3", **kw)["frames"]
            per[anim or "setup"] = compare(da, db, skip, slot_bone)
        result["modes"][mode] = per
    return result


def summary(result):
    out = {}
    for mode, per in result["modes"].items():
        worst = max(per.items(), key=lambda kv: kv[1]["max_px"])
        out[mode] = {"max_px": worst[1]["max_px"], "anim": worst[0], "worst": worst[1]["worst"],
                     "bone_px": max(r["bone_px"] for r in per.values()),
                     "bone_m": max(r["bone_m"] for r in per.values()),
                     "frames": sum(r["frames"] for r in per.values()),
                     "mismatch": sum(len(r["mismatch"]) for r in per.values())}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig", help="IR rig.json or a Spine 4.2 skeleton JSON")
    ap.add_argument("atlas")
    ap.add_argument("--out", default=None, help="where the two emitted files go (default: a temp dir)")
    ap.add_argument("--name", default=None)
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--json", action="store_true", help="print the whole result as JSON")
    args = ap.parse_args(argv)
    for rt in ("4.2", "4.3"):
        ok, why = oracle.available(rt)
        if not ok:
            print(f"equiv43: {why}", file=sys.stderr)
            return 2
    ir = load_ir(args.rig)
    name = args.name or ir.get("name") or os.path.splitext(os.path.basename(args.rig))[0]
    out_dir = args.out or tempfile.mkdtemp(prefix="spinegen-equiv43-")
    result = run(ir, args.atlas, out_dir, name, args.fps)
    result["summary"] = summary(result)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        for w in result["warnings"]["4.3"]:
            print("4.3 emit warning:", w)
        for mode, s in result["summary"].items():
            print(f"physics {mode:6s}: {s['frames']} frames, vertices max {s['max_px']:.6f} px ({s['anim']}: "
                  f"{s['worst']}), bone origins {s['bone_px']:.6f} px, matrices {s['bone_m']:.2e}, "
                  f"{s['mismatch']} mismatches")
        for mode, per in result["modes"].items():
            print(f"  {mode}: " + ", ".join(f"{k} {v['max_px']:.4f}" for k, v in per.items()))
        print(f"files in {out_dir}")
    with open(os.path.join(out_dir, f"{name}.equiv43.json"), "w") as f:
        json.dump(result, f, indent=1)
    s = result["summary"]["none"]
    return 0 if s["max_px"] <= args.tol and not s["mismatch"] else 1


if __name__ == "__main__":
    sys.exit(main())
