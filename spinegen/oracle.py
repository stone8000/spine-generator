"""Call the official-runtime oracle (`node qa/run.mjs`) from Python.

WHY A WRAPPER
  The judge is spine-core 4.2.120 running in Node (SOTA §3.3); Python never
  re-implements it, only asks it. Tests and oracle_check need the same
  answer to "is the oracle installed?" so they can skip cleanly on a machine
  without Node or without `npm ci` in qa/.

  SPINEGEN_QA_RUN points at another run.mjs with the same interface (for
  example a 4.3 build in qa43/).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run_script():
    return Path(os.environ.get("SPINEGEN_QA_RUN") or REPO / "qa" / "run.mjs")


def available():
    """(ok, reason) — whether `dump` can run here."""
    node = shutil.which("node")
    if not node:
        return False, "node is not on PATH"
    run = run_script()
    if not run.exists():
        return False, f"{run} does not exist (the JS oracle is not built)"
    for d in [run.parent, *run.parent.parents]:
        if (d / "node_modules" / "@esotericsoftware" / "spine-core").is_dir():
            return True, ""
    return False, f"@esotericsoftware/spine-core is not installed for {run} (run npm ci in {run.parent})"


def _run(args, timeout):
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    p = subprocess.run(["node", str(run_script()), *args], capture_output=True, text=True, timeout=timeout)
    return p


def dump(skeleton, atlas, anim=None, times=None, fps=None, physics=None, pose=None, pose_mode=None,
         constraints=True, skin=None, folds=False, timeout=600):
    """World bones and slot vertices from the runtime (see qa/README.md, dump).

    pose        {bone: {rotation, x, y, scaleX, scaleY, shearX, shearY}}
    pose_mode   "add" (runtime default: added to the frame's local values) or "set"
    constraints False zeroes every constraint mix (pure forward kinematics)
    folds       adds each frame's G1 fold count
    """
    args = ["dump", "--skeleton", str(skeleton), "--atlas", str(atlas)]
    if anim is not None:
        args += ["--anim", anim]
    if times is not None:
        args += ["--times", ",".join(repr(float(t)) for t in times)]
    if fps is not None:
        args += ["--fps", str(fps)]
    if physics is not None:
        args += ["--physics", physics]
    if pose:
        args += ["--pose", json.dumps(pose)]
    if pose_mode:
        args += ["--pose-mode", pose_mode]
    if not constraints:
        args += ["--constraints", "off"]
    if skin:
        args += ["--skin", skin]
    if folds:
        args += ["--folds"]
    p = _run(args, timeout)
    if p.returncode != 0:
        raise RuntimeError(f"qa dump failed ({p.returncode}): {p.stderr.strip()[-2000:]}")
    return json.loads(p.stdout)


def check(skeleton, atlas, target=None, ir=None, fps=None, c1=None, timeout=1800):
    """(exit code, report) of `qa/run.mjs check` (0 = every hard gate passed).

    c1 is the file oracle_check --out wrote; qa merges it as gate C1_ORACLE.
    """
    args = ["check", "--skeleton", str(skeleton), "--atlas", str(atlas)]
    if target:
        args += ["--target", target]
    if ir:
        args += ["--ir", str(ir)]
    if fps:
        args += ["--fps", ",".join(str(f) for f in fps)]
    if c1:
        args += ["--c1", str(c1)]
    p = _run(args, timeout)
    try:
        report = json.loads(p.stdout)
    except ValueError:
        report = {"raw": p.stdout, "stderr": p.stderr}
    return p.returncode, report
