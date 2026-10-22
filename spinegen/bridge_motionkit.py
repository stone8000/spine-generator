"""spinegen rig -> Spine 4.3 -> the motion kit's presets -> QA on spine-core 4.3.13.

    python -m spinegen.bridge_motionkit <rig.json> <atlas> -o <outdir>
        [--presets fighter-idle-bounce,punch,celebrate,hit-react] [--facing 1|-1]
        [--name NAME] [--arm-softness 0.1] [--leg-softness 0.15] [--no-ik-guard] [--no-qa]

WHY
  spinegen builds rigs that hold big angles (meshes across the joints,
  half-angle helpers, planted leg IK); motionkit/ makes motion that reads
  well on screen (Iron Jaw-measured presets, screen-space retargeting,
  physics bake, IK guard, smoothness check) but only for Spine 4.3. This is
  the seam between them, so a spinegen character gets the kit's motion and
  both judges see the result: the kit's smoothness check and qa's gates on
  the official 4.3.13 runtime.

STEPS (each writes into <outdir>; nothing goes next to the input)
  1. ARM IK, added at emit time and only here: the kit's punch, celebrate
     and idle move the FISTS as IK targets on screen, and a spinegen rig's
     arms are FK. Per side with a template shoulder and elbow (bone roles
     `arm_<s>.shoulder`, `arm_<s>.elbow`): a target `ik_hand_<s>` under the
     root at the forearm's tip (the wrist), a two-bone IK upper arm ->
     forearm onto it, ordered right after the rig's other IK so the
     half-angle helpers and physics read the solved arm. bend direction
     from the SETUP elbow (the sign of the forearm's local angle, which is
     what keeps the setup pose; an arm drawn straight takes the template's
     outward rule); softness = `arm_softness` x arm length. The rig's own
     animations are dropped: they were keyed FK and an arm IK at mix 1 would
     pin those arms (the kit's presets replace them).
  1b. LEG softness: every two-bone leg IK of the rig gets `leg_softness` x
     leg length (default 0.15, the kit's own LEG_SOFTNESS) when the rig has
     less. rigbuild caps leg softness under the setup slack, which on legs
     drawn almost straight leaves almost none (the monkey: 0 and 3.6 px of
     the 36-38 px the kit uses), and then the kit's hip bounce snaps the
     knee straight.
     SOFTNESS WITHOUT MOVING THE SETUP (arms and legs alike): a softened
     solve stops short of its target, so the target is pushed out along the
     chain root -> target until the softened solve lands exactly where the
     drawn chain ended (soft_target inverts spine-core's softness ease; the
     kit's autorig does the same, motionkit/src/autorig.ts softTarget). The
     solver then gets the very same softened point at setup, so the setup
     pose is unchanged whatever the solver branch; bones hanging from the
     target (a planted foot) get the opposite offset and stay where drawn.
     A target that carries art or that another constraint reads is not
     moved (and that chain keeps the rig's softness), and every softening
     is checked: one that moves any other bone at setup by more than
     SETUP_TOL is undone.
     COST (measured on the monkey, BRIDGE_REPORT): a chain drawn straight
     sits at the flat end of the ease, so near the setup the knee barely
     follows a hip drop (none at first for a fully straight leg) and the
     shin runs on past the planted foot, 10-15 px in the kit's dips, where
     a hard IK would bend the knee and then snap it straight on the way
     up. --leg-softness 0 keeps the rig's own (hard) legs; only knees drawn
     bent give both.
  2. emit.spine43 -> <name>.rig43.json, with the atlas and its page copied
     beside it.
  3. A recipe from the TEMPLATE ROLES (not the kit's name guessing):
       hip/body <- `hips`, chest <- `torso`, head <- `head`,
       fists <- the arm IK targets (front first: further along `facing`),
       shoulders <- the shoulder bones, shoulderBack <- the one behind,
       hair <- accessory chains under the head, chain <- other accessory
       chains; an accessory the IR already drives with runtime physics is
       left to it (baking the kit's hair physics on top would move it twice).
     size is the kit's own estimate (`spine-motion init`), so preset
     distances follow the rig exactly as the kit would scale them.
  4. node motionkit/bin/spine-motion.js generate <rig43> <recipe> --check
     (npm ci in motionkit/ first when its runtime is missing), plus
     `check --json` for the per-animation scores.
  5. qa/run.mjs check --runtime 4.3 with an IR summary (counts, constraint
     order, planted feet for M1: the kit never lifts a foot target, so every
     preset animation plants both feet for its whole length).
  bridge.json in <outdir> holds every number of every step, including the
  setup pose of the bridged 4.3 file against the plain 4.3 emit, both played
  by spine-core 4.3.13 (`setup_vertices_43_px`).
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys

from . import ir as irmod
from . import lbs, oracle
from .emit import spine43

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(REPO, "motionkit")
QA_RUN = os.path.join(REPO, "qa", "run.mjs")
DEFAULT_PRESETS = ("fighter-idle-bounce", "punch", "celebrate", "hit-react")
ANIM_NAMES = {"fighter-idle-bounce": "idle", "fighter-idle-weave": "idle_weave", "punch": "punch",
              "celebrate": "celebrate", "hit-react": "hit", "breathe": "breathe", "jump": "jump"}
SETUP_TOL = 0.01    # px: the bridge's IK changes may not move the setup pose more than this
LEG_SOFTNESS = 0.15  # fraction of leg length; the kit's own value (motionkit/src/autorig.ts)
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# ---------------------------------------------------------------- softness without moving the setup

def soft_reached(td, reach, s):
    """Distance a two-bone IK aims at when its target is `td` from the chain root.

    spine-core's softness ease (IkConstraint 2-bone apply, the same in
    4.2.120 and 4.3.13): within `s` of full reach the target is pulled in
    quadratically, so the chain never snaps straight. `reach` and `s` are
    the runtime's own (l1 + l2*psx, softness*psx*(csx+1)/2)."""
    if s <= 0:
        return td
    sd = td - reach + s
    if sd <= 0:
        return td
    p = min(1.0, sd / (s * 2)) - 1
    return td - (sd - s * (1 - p * p))


def soft_target(d, reach, s):
    """Target distance at which the softened solve aims at `d` (the inverse of soft_reached).

    WHY: pushing the target out to this distance lets an IK keep its
    softness and still land on the drawn joint at setup (motionkit's autorig
    softTarget does the same for its legs). soft_reached rises monotonically
    from d to `reach` at reach + s, so bisection finds it; a `d` past what
    the softened chain can aim at gets reach + s, the straight chain."""
    if s <= 0 or soft_reached(d, reach, s) >= d - 1e-12:
        return d
    lo, hi = d, reach + s
    for _ in range(200):
        m = (lo + hi) / 2
        if soft_reached(m, reach, s) < d:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def _readers(ir, bone):
    """Names of the constraints that read or write `bone` (as a constrained bone,
    source, target or physics bone)."""
    out = []
    for _, c in irmod.constraints(ir):
        names = set(c.get("bones") or [])
        for k in ("bone", "source", "target"):
            if isinstance(c.get(k), str):
                names.add(c[k])
        if bone in names:
            out.append(c["name"])
    return out


def soften_ik(out, c, frac):
    """Give two-bone IK `c` (a constraint of `out`, changed in place) softness
    `frac` x chain reach without moving the setup pose (module doc, 1b).

    Returns a report dict, or None when `c` already has at least that much.
    Raises ValueError when the target cannot be moved safely (the caller
    keeps the rig's softness and notes why)."""
    if len(c["bones"]) != 2:
        raise ValueError("not a two-bone IK")
    bones = {b["name"]: b for b in out["bones"]}
    upper, fore, target = bones[c["bones"][0]], bones[c["bones"][1]], bones[c["target"]]
    if fore["parent"] != upper["name"]:
        raise ValueError(f"{fore['name']} is not a child of {upper['name']}")
    on_target = [s["name"] for s in out["slots"] if s["bone"] == target["name"]]
    if on_target:
        raise ValueError(f"target {target['name']} carries slots ({', '.join(on_target)}); moving it moves art")
    readers = [n for n in _readers(out, target["name"]) if n != c["name"]]
    if readers:
        raise ValueError(f"target {target['name']} is read by {', '.join(readers)}")
    bind = lbs.bind_pose(out)
    # the solver's own frame: the upper bone's parent, positions relative to the upper's local x/y
    pp = bind[upper["parent"]] if upper["parent"] is not None else _IDENTITY
    sx, sy = upper["x"], upper["y"]
    ex, ey = lbs.to_local(pp, *bind[fore["name"]][4:6])
    tx, ty = lbs.to_local(pp, *bind[target["name"]][4:6])
    psx, csx = abs(upper["scale_x"]), abs(fore["scale_x"])
    l1 = math.hypot(ex - sx, ey - sy)
    reach = l1 + fore["length"] * csx * psx
    d = math.hypot(tx - sx, ty - sy)
    if d < 1e-9 or reach < 1e-9:
        raise ValueError("degenerate chain or target on the chain root")
    eff = psx * (csx + 1) * 0.5  # the runtime scales softness by this
    old, new = float(c.get("softness", 0.0)), frac * reach
    if new <= old:
        return None
    aim = soft_reached(d, reach, old * eff)  # where the rig's own solve aims now
    td = soft_target(aim, reach, new * eff)
    nx, ny = sx + (tx - sx) * td / d, sy + (ty - sy) * td / d
    wx, wy = lbs.to_world(pp, nx, ny)
    tpar = bind[target["parent"]] if target["parent"] is not None else _IDENTITY
    m_old = bind[target["name"]]
    target["x"], target["y"] = lbs.to_local(tpar, wx, wy)
    # the target's linear part does not depend on its own x/y: children keep their world place
    m_new = (*m_old[:4], wx, wy)
    kids = [b for b in out["bones"] if b["parent"] == target["name"]]
    for k in kids:
        k["x"], k["y"] = lbs.to_local(m_new, *bind[k["name"]][4:6])
    c["softness"] = new
    return {"softness": round(new, 4), "softness_before": round(old, 4), "reach": round(reach, 3),
            "target_distance": round(d, 3), "pushed_to": round(td, 3), "push_px": round(math.hypot(wx - m_old[4], wy - m_old[5]), 3),
            "target_xy": [wx, wy], "children_kept": [k["name"] for k in kids]}


def _soften_checked(out, c, frac, base, keep, notes):
    """soften_ik on a copy, kept only if the setup pose stays within SETUP_TOL of `base`."""
    trial = irmod.clone(out)
    tc = next(x for x in trial["ik"] if x["name"] == c["name"])
    try:
        rep = soften_ik(trial, tc, frac)
    except ValueError as e:
        notes.append(f"{c['name']}: softness left at {c.get('softness', 0):.2f} ({e})")
        return out, None
    if rep is None:
        return out, None
    err = setup_error(base, trial, skip=keep | {c["target"]})
    rep["setup_error_px"] = err
    if err > SETUP_TOL:
        notes.append(f"{c['name']}: softening moved the setup by {err:.4f} px; left at {c.get('softness', 0):.2f}")
        return out, None
    return trial, rep


# ---------------------------------------------------------------- arm IK

def _by_role(ir):
    return {b.get("role"): b["name"] for b in ir["bones"] if b.get("role")}


def _wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def add_arm_ik(ir, softness=0.1):
    """A copy of `ir` with a two-bone IK per arm (module doc, step 1).

    Returns (ir, report): report["arms"][side] = {upper, fore, target,
    wrist_xy (the drawn fist), target_xy (pushed out for the softness),
    bend_positive, softness, softness_frac, push_px, ...}."""
    out = irmod.clone(ir)
    roles = _by_role(out)
    root = next(b["name"] for b in out["bones"] if b["parent"] is None)
    bind = lbs.bind_pose(out)
    names = {b["name"] for b in out["bones"]}
    report = {"arms": {}, "notes": []}
    new_ik = []
    for side in ("l", "r"):
        upper, fore = roles.get(f"arm_{side}.shoulder"), roles.get(f"arm_{side}.elbow")
        if not (upper and fore):
            report["notes"].append(f"side {side}: no shoulder/elbow roles; no arm IK")
            continue
        bu = next(b for b in out["bones"] if b["name"] == upper)
        bf = next(b for b in out["bones"] if b["name"] == fore)
        if bf["parent"] != upper:
            report["notes"].append(f"side {side}: {fore} is not a child of {upper}; no arm IK")
            continue
        target = f"ik_hand_{side}"
        if target in names:
            report["notes"].append(f"side {side}: {target} already exists; no arm IK")
            continue
        tip = lbs.to_world(bind[fore], bf["length"], 0.0)
        # the forearm's local angle IS the IK's a2 (child offset on the parent's x axis); a mirrored
        # parent turns it the other way
        flip = (1 if bu["scale_x"] >= 0 else -1) * (1 if bu["scale_y"] >= 0 else -1)
        a2 = _wrap(bf["rotation"]) * flip
        bend = a2 > 0 if abs(a2) > 1e-6 else True
        out["bones"].append(irmod.new_bone(target, root, *lbs.to_local(bind[root], *tip), role="ik_target"))
        names.add(target)
        new_ik.append({"name": f"arm_ik_{side}", "order": 0, "runs": True, "skin_required": False,
                       "bones": [upper, fore], "target": target, "mix": 1.0, "softness": 0.0,
                       "bend_positive": bool(bend), "compress": False, "stretch": False, "uniform": False})
        report["arms"][side] = {"upper": upper, "fore": fore, "target": target, "wrist_xy": [tip[0], tip[1]],
                                "target_xy": [tip[0], tip[1]], "bend_positive": bool(bend),
                                "elbow_local_deg": round(a2, 3), "softness": 0.0, "softness_frac": softness}
    if not new_ik:
        return out, report
    # right after the last IK that runs: the helpers and physics then read the solved arm
    order = irmod.constraints(out)
    last_ik = max((i for i, (k, c) in enumerate(order) if k == "ik"), default=-1)
    seq = [c for _, c in order[:last_ik + 1]] + new_ik + [c for _, c in order[last_ik + 1:]]
    out["ik"] += new_ik
    for i, c in enumerate(seq):
        c["order"] = i
    if softness > 0:
        for side, arm in report["arms"].items():
            c = next(x for x in out["ik"] if x["name"] == f"arm_ik_{side}")
            out, srep = _soften_checked(out, c, softness, ir, set(), report["notes"])
            if srep:
                arm.update(softness=srep["softness"], target_xy=srep["target_xy"], push_px=srep["push_px"],
                           arm_length=srep["reach"], wrist_distance=srep["target_distance"])
    report["setup_error_px"] = setup_error(ir, out)
    if report["setup_error_px"] > SETUP_TOL:
        report["notes"].append(f"arm IK moves the setup pose by {report['setup_error_px']:.4f} px")
    return out, report


def soften_legs(ir, frac=LEG_SOFTNESS):
    """A copy of `ir` whose two-bone leg IK (chains of `leg_<s>.*` bones) has
    `frac` x leg length softness, targets pushed to keep the setup (module doc, 1b).

    Returns (ir, report): report["legs"][ik name] = soften_ik's numbers."""
    out = irmod.clone(ir)
    report = {"legs": {}, "notes": [], "frac": frac}
    role = {b["name"]: b.get("role") or "" for b in out["bones"]}
    moved = set()
    if frac > 0:
        for c in list(out["ik"]):
            if len(c["bones"]) == 2 and all(role[b].startswith("leg_") for b in c["bones"]):
                out, srep = _soften_checked(out, c, frac, ir, moved, report["notes"])
                if srep:
                    moved.add(c["target"])
                    report["legs"][c["name"]] = srep
    report["moved_targets"] = sorted(moved)
    report["setup_error_px"] = setup_error(ir, out, skip=moved)
    return out, report


def setup_error(a, b, skip=()):
    """Largest setup-pose world move of any bone `a` has (bar `skip`: IK targets the
    bridge pushed out on purpose), constraints on (lbs, 4.2 semantics; run() also
    checks the 4.3.13 vertices)."""
    wa = lbs.Evaluator(a).world()
    eb = lbs.Evaluator(b)
    wb = eb.world_dict(eb.world())
    worst = 0.0
    for i, bone in enumerate(a["bones"]):
        if bone["name"] in skip:
            continue
        m, n = wa[i], wb[bone["name"]]
        worst = max(worst, math.hypot(m[4] - n[4], m[5] - n[5]),
                    100 * max(abs(m[k] - n[k]) for k in range(4)))  # matrix entries as px at 100 px
    return worst


# ---------------------------------------------------------------- roles and recipe

def _chains(ir, bones):
    """Parent -> child chains among `bones` (a set of names), roots first."""
    parent = {b["name"]: b["parent"] for b in ir["bones"]}
    kids = {}
    for b in ir["bones"]:
        if b["name"] in bones and parent[b["name"]] in bones:
            kids.setdefault(parent[b["name"]], []).append(b["name"])
    out = []
    for b in ir["bones"]:
        n = b["name"]
        if n not in bones or parent[n] in bones:
            continue
        chain = [n]
        while kids.get(chain[-1]):
            chain.append(kids[chain[-1]][0])
        out.append(chain)
    return out


def roles_from_template(ir, arm_report, facing=1):
    """The kit's role map from the IR's template roles (module doc, step 3)."""
    roles, notes = {}, []
    by = _by_role(ir)
    root = next(b["name"] for b in ir["bones"] if b["parent"] is None)
    roles["root"] = root
    for kit, tmpl in (("hip", "hips"), ("body", "hips"), ("chest", "torso"), ("head", "head")):
        if by.get(tmpl):
            roles[kit] = by[tmpl]
        elif kit != "body":
            notes.append(f"no template bone '{tmpl}' for the kit's role '{kit}'")
    bind = lbs.bind_pose(ir)
    fists = [a["target"] for a in arm_report["arms"].values()]
    if fists:
        xs = {a["target"]: a.get("wrist_xy", a["target_xy"])[0] for a in arm_report["arms"].values()}
        fists.sort(key=lambda f: -facing * xs[f])
        roles["fists"] = fists
        roles["fist"] = roles["fistFront"] = fists[0]
        if len(fists) > 1:
            roles["fistBack"] = fists[1]
    shoulders = [by[f"arm_{s}.shoulder"] for s in ("l", "r") if by.get(f"arm_{s}.shoulder")]
    if shoulders:
        roles["shoulders"] = shoulders
        roles["shoulderBack"] = min(shoulders, key=lambda s: facing * bind[s][4])
    physics = {c["bone"] for c in ir["physics"]}
    acc = {b["name"] for b in ir["bones"] if b.get("role") == "accessory"}
    left = sorted(acc & physics)
    if left:
        notes.append(f"accessories with runtime physics in the IR, left to it: {', '.join(left)}")
    head = by.get("head")
    hair, other = [], []
    for ch in _chains(ir, acc - physics):
        root_parent = next(b["parent"] for b in ir["bones"] if b["name"] == ch[0])
        (hair if root_parent == head else other).append(ch)
    if hair:
        roles["hair"] = max(hair, key=len)
    if other:
        roles["chain"] = max(other, key=len)
    return roles, notes


def make_recipe(roles, presets, size, facing=1, ik_guard=True):
    anims = []
    for p in presets:
        a = {"name": ANIM_NAMES.get(p, p.replace("-", "_")), "preset": p}
        if p.startswith("fighter-idle"):
            a["params"] = {"hair": "keys" if roles.get("hair") else "none"}
        anims.append(a)
    return {"fps": 30, "facing": facing, "size": size, "ikGuard": bool(ik_guard), "roles": roles, "animations": anims}


# ---------------------------------------------------------------- node steps

def ensure_kit():
    """npm ci in motionkit/ when its pinned runtime is missing."""
    if not os.path.isdir(os.path.join(KIT, "node_modules", "@esotericsoftware", "spine-core")):
        subprocess.run(["npm", "ci"], cwd=KIT, check=True, capture_output=True, text=True)


def kit(*args, cwd=None):
    p = subprocess.run(["node", os.path.join(KIT, "bin", "spine-motion.js"), *args], capture_output=True,
                       text=True, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"spine-motion {args[0]} failed ({p.returncode}): {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def kit_size(rig43):
    """The kit's own size estimate for this rig (`spine-motion init`)."""
    tmp = rig43[:-5] + ".init.recipe.json"
    kit("init", rig43, "-o", tmp, "--force")
    with open(tmp) as f:
        guess = json.load(f)
    os.remove(tmp)
    return guess["size"], guess["roles"]


def qa_ir(ir, motion_json):
    """What qa's --ir reads, for the generated file: counts (the kit's
    animations replace the rig's), constraint order, planted feet."""
    with open(motion_json) as f:
        skel = json.load(f)
    anims = skel.get("animations", {})
    counts = irmod.counts(dict(ir, animations=[]))
    counts["animations"] = len(anims)
    counts["events"] = len(skel.get("events", {}))  # the kit's swing / impact / hit
    feet = sorted({fp["bone"] for fp in ir.get("foot_plant", [])})
    plants = [{"bone": b, "anim": a} for a in anims for b in feet]
    return {"counts": counts, "foot_plant": plants,
            **{k: [{"name": c["name"], "order": c["order"]} for c in ir[k]] for k in ("ik", "transform", "path", "physics")}}


def setup_vertices_43(plain43, rig43, atlas):
    """Largest setup-pose world-vertex distance between the plain 4.3 emit and the
    bridged 4.3 file, both posed by spine-core 4.3.13: the proof that the arm IK
    and the pushed soft targets leave the drawn pose where it was."""
    fa = oracle.dump(plain43, atlas, runtime="4.3")["frames"][0]["slots"]
    fb = oracle.dump(rig43, atlas, runtime="4.3")["frames"][0]["slots"]
    worst = 0.0
    for slot, sa in fa.items():
        va, vb = sa["vertices"] or [], fb[slot]["vertices"] or []
        if len(va) != len(vb):
            return float("inf")
        for i in range(0, len(va), 2):
            worst = max(worst, math.hypot(va[i] - vb[i], va[i + 1] - vb[i + 1]))
    return worst


def qa_check(motion_json, atlas, ir_summary_path, out_path):
    p = subprocess.run(["node", QA_RUN, "check", "--runtime", "4.3", "--skeleton", motion_json, "--atlas", atlas,
                        "--ir", ir_summary_path, "--out", out_path], capture_output=True, text=True)
    if p.returncode not in (0, 1):
        raise RuntimeError(f"qa check failed ({p.returncode}): {p.stderr.strip()}")
    with open(out_path) as f:
        return json.load(f), p.stderr


# ---------------------------------------------------------------- the bridge

def run(rig_path, atlas, out_dir, presets=DEFAULT_PRESETS, facing=1, name=None, arm_softness=0.1, qa=True,
        leg_softness=LEG_SOFTNESS, ik_guard=True):
    ir = irmod.load(rig_path)
    name = name or ir.get("name") or "rig"
    os.makedirs(out_dir, exist_ok=True)
    rep = {"input": {"rig": os.path.abspath(rig_path), "atlas": os.path.abspath(atlas)}, "name": name,
           "presets": list(presets), "facing": facing}

    legged, legs = soften_legs(ir, leg_softness)
    rep["leg_ik"] = legs
    bridged, arms = add_arm_ik(legged, arm_softness)
    rep["arm_ik"] = arms
    rep["setup_error_px"] = setup_error(ir, bridged, skip=set(legs["moved_targets"]))
    dropped = [a["name"] for a in bridged.get("animations", [])]
    bridged["animations"] = []
    rep["dropped_animations"] = dropped
    irmod.save(bridged, os.path.join(out_dir, f"{name}.bridge_rig.json"), os.path.join(out_dir, f"{name}.bridge_motion.json"))

    rig43 = os.path.join(out_dir, f"{name}.rig43.json")
    emit_rep = {}
    spine43.write(bridged, rig43, report=emit_rep)
    rep["emit43"] = {k: emit_rep[k] for k in ("warnings", "defaults", "half_angle", "differs")}
    atlas_out = os.path.join(out_dir, f"{name}.atlas")
    shutil.copyfile(atlas, atlas_out)
    for page in _atlas_pages(atlas):
        shutil.copyfile(os.path.join(os.path.dirname(atlas), page), os.path.join(out_dir, page))
    if oracle.available("4.3")[0]:
        plain43 = os.path.join(out_dir, f"{name}.plain43.json")
        spine43.write(dict(irmod.clone(ir), animations=[]), plain43)
        rep["setup_vertices_43_px"] = setup_vertices_43(plain43, rig43, atlas_out)

    ensure_kit()
    size, guessed = kit_size(rig43)
    roles, notes = roles_from_template(bridged, arms, facing)
    recipe = make_recipe(roles, presets, size, facing, ik_guard)
    recipe_path = os.path.join(out_dir, f"{name}.recipe.json")
    with open(recipe_path, "w") as f:
        json.dump(recipe, f, indent=1, ensure_ascii=False)
    rep["recipe"] = {"path": recipe_path, "size": size, "roles": roles, "notes": notes, "kit_guessed_roles": guessed}

    motion = os.path.join(out_dir, f"{name}.motion.json")
    rep["generate_stdout"] = kit("generate", rig43, recipe_path, "-o", motion, "--check")
    loops = [a["name"] for a in recipe["animations"] if a["preset"] in ("fighter-idle-bounce", "fighter-idle-weave", "celebrate", "breathe")]
    checks = json.loads(kit("check", motion, "--loops", ",".join(loops), "--json"))
    rep["smoothness"] = [{"name": r["name"], "score": r["score"], "loop": r["loop"], "keys": r["keyCount"],
                          "errors": sum(i["severity"] == "error" for i in r["issues"]),
                          "warnings": sum(i["severity"] == "warn" for i in r["issues"]),
                          "issues": r["issues"]} for r in checks]
    if qa:
        summ = os.path.join(out_dir, f"{name}.qa_ir.json")
        with open(summ, "w") as f:
            json.dump(qa_ir(bridged, motion), f, indent=1)
        report, text = qa_check(motion, atlas_out, summ, os.path.join(out_dir, f"{name}.qa43.json"))
        rep["qa"] = {"pass": report["pass"], "runtime": report["runtime"],
                     "gates": {g["id"]: {k: g.get(k) for k in ("pass", "value", "max", "hard", "skipped", "note", "worst")}
                               for g in report["gates"]}, "text": text}
    rep["files"] = {"rig43": rig43, "recipe": recipe_path, "motion": motion, "atlas": atlas_out,
                    "check_md": motion[:-5] + ".check.md"}
    with open(os.path.join(out_dir, "bridge.json"), "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    return rep


def _atlas_pages(atlas):
    """Page file names of a Spine atlas (a line ending in an image extension, before its `size:`)."""
    pages = []
    with open(atlas) as f:
        for line in f:
            s = line.strip()
            if s and ":" not in s and s.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                pages.append(s)
    return pages


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rig", help="IR rig.json (motion.json beside it)")
    ap.add_argument("atlas")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--presets", default=",".join(DEFAULT_PRESETS))
    ap.add_argument("--facing", type=int, default=1, choices=(1, -1))
    ap.add_argument("--name", default=None)
    ap.add_argument("--arm-softness", type=float, default=0.1, help="arm IK softness, fraction of arm length")
    ap.add_argument("--leg-softness", type=float, default=LEG_SOFTNESS,
                    help="leg IK softness, fraction of leg length (0 keeps the rig's)")
    ap.add_argument("--no-ik-guard", action="store_true", help="recipe ikGuard false (the kit's 85-95%% reach guard off)")
    ap.add_argument("--no-qa", action="store_true")
    args = ap.parse_args(argv)
    rep = run(args.rig, args.atlas, args.out, [p.strip() for p in args.presets.split(",") if p.strip()],
              args.facing, args.name, args.arm_softness, not args.no_qa, args.leg_softness, not args.no_ik_guard)
    for side, a in rep["arm_ik"]["arms"].items():
        print(f"arm IK {side}: {a['upper']} -> {a['fore']} onto {a['target']}, bendPositive {a['bend_positive']}, "
              f"softness {a['softness']} (target pushed {a.get('push_px', 0)} px past the wrist)")
    for n, g in rep["leg_ik"]["legs"].items():
        print(f"leg IK {n}: softness {g['softness_before']} -> {g['softness']} (target pushed {g['push_px']} px)")
    print(f"setup moved by the bridge (lbs): {rep['setup_error_px']:.2e} px"
          + (f"; 4.3.13 setup vertices vs the plain 4.3 emit: {rep['setup_vertices_43_px']:.2e} px"
             if "setup_vertices_43_px" in rep else ""))
    for n in rep["leg_ik"]["notes"] + rep["arm_ik"]["notes"] + rep["recipe"]["notes"]:
        print("note:", n)
    print(f"recipe: size {rep['recipe']['size']}, roles {json.dumps(rep['recipe']['roles'], ensure_ascii=False)}")
    for s in rep["smoothness"]:
        print(f"  {s['name']:10s} score {s['score']:3d}  errors {s['errors']}  warnings {s['warnings']}  keys {s['keys']}")
    if "qa" in rep:
        print(rep["qa"]["text"].rstrip())
    print(f"outputs in {args.out}")
    return 0 if rep.get("qa", {}).get("pass", True) else 1


if __name__ == "__main__":
    sys.exit(main())
