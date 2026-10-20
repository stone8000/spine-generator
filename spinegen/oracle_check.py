"""Gate C1 ORACLE: numpy LBS against spine-core, in world pixels.

    python -m spinegen.oracle_check x.json x.atlas [--anim A | --all] [--times 0,0.1,...]
        [--fps 60] [--pose JSON|@file] [--pose-mode add|set] [--constraints on|off]
        [--skin S] [--folds] [--tol 0.1] [--json] [--out c1.json]

Without --anim the setup pose is compared (plus --pose, if given); the
runtime shows it right after a physics reset, and lbs models that frame
(physics="reset"). With --anim the runtime plays the animation with physics
off (`dump --physics none`) and Python samples the same animation
(playback.py) at the runtime's own track time, also with physics none.
--all compares the setup pose and every animation on the --fps grid (what
`qa/run.mjs check` runs when it is not handed a --c1 file). --folds also
compares the G1 fold count of every frame.

WHAT C1 MEASURES (the shared interface asks for the same bones AND slots)
  value = the largest of: world vertex distance, bone origin distance, and
  bone matrix difference expressed in px at MATRIX_REACH px from the bone
  (max |da|, |db|, |dc|, |dd| * 100). A bone that carries no attachment is
  still held to 0.1 px.

Exits 0 when the value is within --tol (SOTA §5: 0.1 px), every slot shows
the same attachment with the same vertex count and (with --folds) every
fold count agrees; 1 otherwise; 2 when the oracle is not installed or
refuses the input. --out writes the result in the form `qa/run.mjs check
--c1` merges into the QA report.
"""
import argparse
import json
import math
import sys

import numpy as np

from . import atlas as atlas_mod
from . import oracle, playback
from .lbs import LOCAL_FIELDS, Evaluator
from .spine42_reader import read

_SPINE_FIELDS = {"scaleX": "scale_x", "scaleY": "scale_y", "shearX": "shear_x", "shearY": "shear_y"}
MATRIX_REACH = 100.0  # px: a matrix difference counts as the vertex error it makes this far from the bone


def compare_frames(ref, test, label=None):
    """Distances between two dump-shaped frames.

    Returns {"max_px", "worst", "bone_px", "bone_m", "mismatch", "skipped"}:
    vertex distance is Euclidean per vertex; bone_px is the largest world
    origin distance and bone_m the largest a/b/c/d difference. A slot whose
    attachment or vertex count differs is a mismatch; a slot only one side
    gives vertices for is skipped.
    """
    out = {"max_px": 0.0, "worst": None, "bone_px": 0.0, "bone_m": 0.0, "mismatch": [], "skipped": []}
    for name, rb in ref["bones"].items():
        tb = test["bones"].get(name)
        if tb is None:
            out["mismatch"].append(f"bone {name} missing")
            continue
        out["bone_px"] = max(out["bone_px"], float(np.hypot(rb["x"] - tb["x"], rb["y"] - tb["y"])))
        out["bone_m"] = max(out["bone_m"], max(abs(rb[k] - tb[k]) for k in "abcd"))
    for name, rs in ref["slots"].items():
        ts = test["slots"].get(name)
        if ts is None:
            out["mismatch"].append(f"slot {name} missing")
            continue
        if rs["attachment"] != ts["attachment"]:
            out["mismatch"].append(f"slot {name}: {rs['attachment']!r} != {ts['attachment']!r} at {label}")
            continue
        a, b = np.asarray(rs["vertices"], float), np.asarray(ts["vertices"], float)
        if len(a) == 0 or len(b) == 0:
            if len(a) != len(b):
                out["skipped"].append(name)
            continue
        if a.shape != b.shape:
            out["mismatch"].append(f"slot {name}: {len(a) // 2} vs {len(b) // 2} vertices")
            continue
        d = np.hypot(*(a - b).reshape(-1, 2).T)
        i = int(d.argmax())
        if d[i] > out["max_px"]:
            out["max_px"] = float(d[i])
            out["worst"] = {"slot": name, "vertex": i, **({"t": label} if label is not None else {})}
    return out


def merge(reports):
    out = {"max_px": 0.0, "worst": None, "bone_px": 0.0, "bone_m": 0.0, "mismatch": [], "skipped": []}
    for r in reports:
        if r["max_px"] >= out["max_px"]:
            out["max_px"], out["worst"] = r["max_px"], r["worst"] or out["worst"]
        out["bone_px"] = max(out["bone_px"], r["bone_px"])
        out["bone_m"] = max(out["bone_m"], r["bone_m"])
        out["mismatch"] += r["mismatch"]
        out["skipped"] = sorted(set(out["skipped"]) | set(r["skipped"]))
    return out


def track_time(t, fps):
    """The animation time spine-core has when qa/run.mjs reports time t.

    The dump steps a 1/fps grid with dt = k/fps - (k-1)/fps and AnimationState
    adds each dt to its track time, so the time is that float sum, not k/fps:
    it can sit a hair before a key the nominal time is on, and the runtime
    keeps key times in float32 besides (playback.py), so stepped and
    single-key timelines hinge on it. An off-grid time is the grid up to the
    step before it plus one partial step.
    """
    k = round(t * fps)
    on_grid = abs(k / fps - t) < 1e-9
    n = k if on_grid else math.floor(t * fps)
    acc = 0.0
    for j in range(1, n + 1):
        acc += j / fps - (j - 1) / fps
    return acc if on_grid else acc + (t - n / fps)


def _posed(ir, base, pose, mode):
    """Python's local pose for a frame: `base` (setup or the animation's
    values) with `pose` added to it or replacing it, as `dump --pose` does."""
    setup = {b["name"]: b for b in ir["bones"]}
    out = {k: dict(v) for k, v in base.items()}
    for bone, fields in (pose or {}).items():
        cur = out.setdefault(bone, {})
        for f, v in fields.items():
            f = _SPINE_FIELDS.get(f, f)
            if f not in LOCAL_FIELDS:
                raise ValueError(f"pose: {bone}.{f} is not a local bone field")
            cur[f] = v if mode == "set" else cur.get(f, setup[bone][f]) + v
    return out


def run(skeleton, atlas_path, anim=None, times=None, fps=None, skin=None, pose=None, pose_mode="add",
        constraints=True, folds=False):
    """Compare every frame; returns (merged report, frame count, lbs warnings).

    The report also carries "folds": [(t, runtime, numpy)] for frames whose
    fold counts differ, when `folds` is set.
    """
    ref = oracle.dump(skeleton, atlas_path, anim=anim, times=times, fps=fps,
                      physics="none" if anim else None, pose=pose, pose_mode=pose_mode,
                      constraints=constraints, skin=skin, folds=folds)
    ir = read(skeleton)
    ev = Evaluator(ir, atlas_mod.load(atlas_path), skin)
    rate = float(fps or 60)
    reports, fold_diff = [], []
    for fr in ref["frames"]:
        p = {"bones": {}, "state": {}, "attachments": {}, "deform": {}}
        if fr.get("anim") is not None:
            tt = fr.get("trackTime")  # a dump that reports the runtime's own time wins
            p = playback.sample(ir, fr["anim"], float(tt) if tt is not None else track_time(float(fr["t"]), rate))
        bones = _posed(ir, p["bones"], pose, pose_mode)
        world = ev.world(bones, p["state"], constraints=constraints, physics="none" if anim else "reset")
        test = ev.frame(world=world, attachments=p["attachments"], deform=p["deform"])
        reports.append(compare_frames(fr, test, label=fr.get("t")))
        if folds and "folds" in fr:
            mine = ev.folds(world=world, attachments=p["attachments"], deform=p["deform"])["folds"]
            if mine != fr["folds"]:
                fold_diff.append((fr.get("t"), fr["folds"], mine))
    rep = merge(reports)
    if folds:
        rep["folds"] = fold_diff
    return rep, len(reports), ev.warnings


def c1_value(rep):
    """The one C1 number: vertices, bone origins and bone matrices, in px."""
    return max(rep["max_px"], rep["bone_px"], rep["bone_m"] * MATRIX_REACH)


def run_all(skeleton, atlas_path, fps=60, skin=None, pose=None, pose_mode="add", constraints=True, folds=False):
    """Setup pose plus every animation on the fps grid: (merged report, frames, warnings)."""
    rep, n, warnings = run(skeleton, atlas_path, skin=skin, pose=pose, pose_mode=pose_mode,
                           constraints=constraints, folds=folds)
    reports = [rep]
    for anim in [a["name"] for a in read(skeleton).get("animations", [])]:
        r, k, w = run(skeleton, atlas_path, anim=anim, fps=fps, skin=skin, pose=pose, pose_mode=pose_mode,
                      constraints=constraints, folds=folds)
        if r["worst"] is not None:
            r["worst"] = {"anim": anim, **r["worst"]}
        reports.append(r)
        n += k
        warnings += [x for x in w if x not in warnings]
    out = merge(reports)
    if folds:
        out["folds"] = [f for r in reports for f in r.get("folds", [])]
    return out, n, warnings


def c1_result(rep, tol):
    """The --c1 file `qa/run.mjs check` merges: {value, max, pass, worst, note}."""
    value = c1_value(rep)
    ok = value <= tol and not rep["mismatch"] and not rep.get("folds")
    note = (f"numpy lbs vs dump over {rep.get('frames', '?')} frame(s): vertices {rep['max_px']:.2e} px, "
            f"bone origins {rep['bone_px']:.2e} px, bone matrices {rep['bone_m']:.2e} (x{MATRIX_REACH:g} px)")
    if rep["mismatch"]:
        note += f"; {len(rep['mismatch'])} mismatch(es): {rep['mismatch'][0]}"
    if rep.get("folds"):
        note += f"; fold counts differ at {len(rep['folds'])} frame(s)"
    if rep.get("warnings"):
        note += "; lbs: " + "; ".join(rep["warnings"][:3])
    return {"value": value, "max": tol, "pass": ok, "worst": rep["worst"], "note": note}


def main(argv=None):
    ap = argparse.ArgumentParser(description="C1: numpy LBS vs spine-core world vertices")
    ap.add_argument("skeleton")
    ap.add_argument("atlas")
    ap.add_argument("--anim")
    ap.add_argument("--all", action="store_true", help="setup pose and every animation on the --fps grid")
    ap.add_argument("--times", help="comma-separated seconds (default: every frame at --fps)")
    ap.add_argument("--fps", type=float)
    ap.add_argument("--pose", help='local bone pose, JSON or @file: {"chest": {"rotation": 150}}')
    ap.add_argument("--pose-mode", choices=["add", "set"], default="add")
    ap.add_argument("--constraints", choices=["on", "off"], default="on")
    ap.add_argument("--skin")
    ap.add_argument("--folds", action="store_true", help="also compare G1 fold counts")
    ap.add_argument("--tol", type=float, default=0.1)
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--out", help="write the C1 result for `qa/run.mjs check --c1`")
    args = ap.parse_args(argv)
    ok, why = oracle.available()
    if not ok:
        print(f"oracle unavailable: {why}", file=sys.stderr)
        return 2
    times = [float(t) for t in args.times.split(",")] if args.times else None
    pose = None
    if args.pose:
        pose = json.load(open(args.pose[1:])) if args.pose.startswith("@") else json.loads(args.pose)
    if args.all and (args.anim or times):
        ap.error("--all compares every animation; it takes no --anim or --times")
    try:
        if args.all:
            rep, n, warnings = run_all(args.skeleton, args.atlas, args.fps or 60, args.skin, pose, args.pose_mode,
                                       args.constraints == "on", args.folds)
        else:
            rep, n, warnings = run(args.skeleton, args.atlas, args.anim, times, args.fps, args.skin, pose,
                                   args.pose_mode, args.constraints == "on", args.folds)
    except RuntimeError as e:  # the oracle refused the input (exit 2: bad path, anim, pose)
        print(str(e), file=sys.stderr)
        return 2
    rep.update(frames=n, tol=args.tol, warnings=warnings)
    result = c1_result(rep, args.tol)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f)
            f.write("\n")
    if args.json:
        print(json.dumps({**rep, "pass": result["pass"]}, indent=1))
    else:
        w = rep["worst"] or {}
        print(f"C1 {'PASS' if result['pass'] else 'FAIL'}: {result['value']:.6f} px over {n} frame(s)"
              f" (tol {args.tol}); max vertex diff {rep['max_px']:.2e} px, worst "
              f"{w['anim'] + '/' if w.get('anim') else ''}{w.get('slot')}#{w.get('vertex')} t={w.get('t')};"
              f" bone origin {rep['bone_px']:.2e} px,"
              f" matrix {rep['bone_m']:.2e}")
        for m in rep["mismatch"][:20]:
            print("  mismatch:", m)
        for t, theirs, mine in rep.get("folds", [])[:20]:
            print(f"  folds at t={t}: runtime {theirs}, numpy {mine}")
        if rep["skipped"]:
            print("  skipped (no vertices on one side):", ", ".join(rep["skipped"]))
        for wmsg in warnings:
            print("  lbs:", wmsg)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
