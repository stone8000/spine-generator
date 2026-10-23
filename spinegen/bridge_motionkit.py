"""spinegen rig -> Spine 4.3 -> the motion kit's presets -> QA on spine-core 4.3.13.

    python -m spinegen.bridge_motionkit <rig.json> <atlas> -o <outdir>
        [--presets fighter-idle-bounce,punch,celebrate,hit-react] [--facing 1|-1]
        [--name NAME] [--arm-softness 0.1] [--leg-softness 0.15] [--no-ik-guard | --kit-guard]
        [--budget auto|none|budget.json] [--joints joints.json] [--no-intro] [--no-qa]

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
       crouch <- the hip, when the legs' IK targets are not under it (feet
       planted under the root): the kit's jump then dips and lands on the
       hip, so the legs bend, and the root carries only the flight.
     size is the kit's own estimate (`spine-motion init`), so preset
     distances follow the rig exactly as the kit would scale them.
     INTRO (--no-intro turns it off): an idle loop (fighter-idle-*,
     breathe) gets the kit's recipe `intro`, a one-shot `<name>_in` one
     cycle long that starts on the drawn pose and ends on the loop's frame
     0 (smootherstep envelope: speed and acceleration join). An idle preset
     starts mid-cycle (the monkey's: hips at their lowest, 39.6 px off the
     drawn pose, no frame of the cycle closer than 15.5 px), and a loop
     cannot both close on itself and start at rest. Play `<name>_in`, then
     queue `<name>` looping. bridge.json `intro` has both joins in px.
  3b. IK GUARD PER CHAIN (recipe `ikGuardChains`; guard_chains): the kit's
     guard keeps a fist target within 85-95 % of the arm's bone lengths;
     an arm drawn straight sits past that at rest (the monkey: 99.8 %, and
     its target is pushed further for the softness), so the kit's guard
     bent the drawn elbows 32-53 deg before any motion (56.7 px of
     vertices at amplitude 0). Per chain the knee is the setup distance
     when that is past 85 %, so the drawn pose maps to itself, and the
     ceiling is 3/4 of the way to where the softened solve goes straight.
     `rest_probe` checks it: frame 0 of the first preset at amplitude 0
     against the setup pose. --kit-guard keeps the kit's.
  3c. BUDGET (budget.json and joints.json beside rig.json, from
     spinegen.budget; fit_budget): the presets do not know the art's
     limits. Each animation is played in spine-core 4.3.13 and every
     joint's angle read (spinegen.budget_motion): held past theta_soft for
     HOLD_SECONDS, or past theta_hard on any frame, or any G1 fold, is out
     of budget. The joints' groups (each arm through its fist target, the
     body through hip / chest: planted legs bend only as the body moves)
     get an amplitude factor (recipe `boneAmplitude`), bisected until the
     animation is in budget; bridge.json lists per animation which joints
     were clamped, why, the factor and the angles before / after. An arm
     carried at under ART_REQUEST_BELOW of its motion gets an art request
     (artreq.json: a pose to draw, or, when the limit is a cut-out seam,
     the joint's art).
  4. node motionkit/bin/spine-motion.js generate <rig43> <recipe> --check
     (npm ci in motionkit/ first when its runtime is missing), plus
     `check --json` for the per-animation scores.
  5. qa/run.mjs check --runtime 4.3 with an IR summary (counts, constraint
     order, planted feet for M1: the kit never lifts a foot target, so every
     preset animation plants both feet for its whole length, except the
     jump's flight between its `takeoff` and `land` events, when the root
     carries the feet).
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
    return {"softness": round(new, 4), "softness_before": round(old, 4), "reach": round(reach, 3), "soft_eff": new * eff,
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
                           arm_length=srep["reach"], wrist_distance=srep["target_distance"],
                           soft_eff=srep["soft_eff"], target_distance=srep["pushed_to"])
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
    hip = roles.get("hip")
    if hip and planted_feet(ir, hip):
        # the kit's jump moves the root; with the feet's IK targets outside the hip (planted), the dip and the
        # landing go on the hip so the legs bend, and the root carries only the flight
        roles["crouch"] = hip
    return roles, notes


def planted_feet(ir, hip):
    """Leg IK targets that the hip does not carry (under the root, say): feet that stay where they are when the
    body moves."""
    parent = {b["name"]: b["parent"] for b in ir["bones"]}
    role = {b["name"]: b.get("role") or "" for b in ir["bones"]}

    def under(b, a):
        while b is not None:
            if b == a:
                return True
            b = parent[b]
        return False
    return [c["target"] for c in ir["ik"] if len(c["bones"]) == 2 and all(role[b].startswith("leg_") for b in c["bones"])
            and not under(c["target"], hip)]


INTRO_PRESETS = ("fighter-idle-bounce", "fighter-idle-weave", "breathe")  # idles: the first thing a player shows
LOOP_PRESETS = ("fighter-idle-bounce", "fighter-idle-weave", "celebrate", "breathe")


def make_recipe(roles, presets, size, facing=1, ik_guard=True, intro=True):
    """The kit recipe. `intro`: an idle loop also gets the kit's one-shot opening `<name>_in` (recipe `intro`,
    one cycle long), which starts on the drawn pose and ends on the loop's frame 0: an idle preset starts mid-cycle
    (the monkey's: hips at their lowest, 39.6 px of vertices off the drawn pose), and a loop cannot both close on
    itself and start at rest unless its cycle passes through the rest pose."""
    anims = []
    for p in presets:
        a = {"name": ANIM_NAMES.get(p, p.replace("-", "_")), "preset": p}
        if p.startswith("fighter-idle"):
            a["params"] = {"hair": "keys" if roles.get("hair") else "none"}
        if intro and p in INTRO_PRESETS:
            a["intro"] = True
        anims.append(a)
    return {"fps": 30, "facing": facing, "size": size, "ikGuard": bool(ik_guard), "roles": roles, "animations": anims}


# ---------------------------------------------------------------- IK guard per chain

GUARD_KNEE = 0.85    # the kit's own guard: identity up to 85 % of the chain's reach ...
GUARD_SHARE = 0.75   # ... ours eases out to this share of the way from the knee to where the solve goes straight
GUARD_MIN_ROOM = 0.02  # fraction of reach: the least room the ease gets (a target drawn at the very end)


def guard_chains(ir):
    """The kit's IK reach guard, per screen-driven two-bone IK, from the SETUP reach (recipe
    `ikGuardChains`, motionkit/src/solve.ts).

    WHY: the kit's guard is identity up to 85 % of the chain's bone lengths and never lets the
    target past 95 %. An arm drawn straight (the monkey's: wrist at 99.8 % of the arm) with a
    target pushed out for its softness sits far past that at rest, so the default guard pulls
    every fist in from frame 0 and bends the drawn elbows 35-53 deg before the preset moves
    anything. Here the knee is the setup distance itself when that is further out than 85 %, so
    the drawn pose maps to itself, and the ceiling is GUARD_SHARE of the way from the knee to
    where this chain's solve goes straight (reach + softness, the end of spine-core's ease):
    the target eases in before the elbow locks, so the guard still stops the snap.
    Returns {target: {knee, max, setup, reach, straight_at}} in world units from the chain root."""
    bind = lbs.bind_pose(ir)
    bones = {b["name"]: b for b in ir["bones"]}
    out = {}
    for c in ir["ik"]:
        if len(c["bones"]) != 2 or not c.get("runs", True):
            continue
        up, fo = bones[c["bones"][0]], bones[c["bones"][1]]
        root = bind[up["name"]][4:6]
        elbow = bind[fo["name"]][4:6]
        tip = lbs.to_world(bind[fo["name"]], fo["length"], 0.0)
        reach = math.dist(root, elbow) + math.dist(elbow, tip)
        eff = abs(up["scale_x"]) * (abs(fo["scale_x"]) + 1) * 0.5
        straight = reach + float(c.get("softness", 0.0)) * eff
        d0 = math.dist(root, bind[c["target"]][4:6])
        knee = max(GUARD_KNEE * reach, d0)
        room = max(GUARD_SHARE * (straight - knee), GUARD_MIN_ROOM * reach)
        out[c["target"]] = {"knee": round(knee, 4), "max": round(knee + room, 4), "setup": round(d0, 4),
                            "reach": round(reach, 4), "straight_at": round(straight, 4)}
    return out


# ---------------------------------------------------------------- budget-aware amplitudes

FIT_TOL = 0.04      # amplitude factors are bisected to this
FIT_ROUNDS = 14
ART_REQUEST_BELOW = 0.75  # an arm the art carries at under 3/4 of the preset's motion gets an art request


def joint_groups(ir, roles, joints):
    """{group: {"bones": [driver bones], "joints": [joint names]}}: what the bridge scales when a
    joint leaves the budget. A joint of an arm IK chain (the shoulder and elbow bones, by the
    joints file) belongs to that arm, scaled through its fist target; every other joint (hips,
    knees, ankles: the feet stay planted, so the legs bend only as the body moves) belongs to
    the body, scaled through the hip / body / chest roles."""
    groups = {}
    in_arm = {}
    for c in ir["ik"]:
        if c["name"].startswith("arm_ik_"):
            g = "arm_" + c["name"][len("arm_ik_"):]
            groups[g] = {"bones": [c["target"]], "joints": []}
            for b in c["bones"]:
                in_arm[b] = g
    body = []
    for r in ("hip", "body", "chest"):
        v = roles.get(r)
        if isinstance(v, str) and v not in body:
            body.append(v)
    groups["body"] = {"bones": body, "joints": []}
    for j in joints:
        groups[in_arm.get(j["bone"], "body")]["joints"].append(j["name"])
    return {g: v for g, v in groups.items() if v["bones"]}


def _blame(res, groups):
    """Groups whose joints are out of budget in this measurement; a fold with every joint in
    budget blames the group whose joint came closest to its hard limit."""
    bad = {g for g, v in groups.items() if any(not res["joints"][j]["ok"] for j in v["joints"])}
    if not bad and res["max_folds"]:
        best, worst = None, -1.0
        for g, v in groups.items():
            for j in v["joints"]:
                r = res["joints"][j]
                for d in ("+", "-"):
                    if r[d]["hard"] and math.isfinite(r[d]["hard"]):
                        f = r[d]["peak"] / r[d]["hard"]
                        if f > worst:
                            best, worst = g, f
        if best:
            bad = {best}
    return bad


def _anim_recipe(recipe, anim, factors, groups):
    a = dict(anim)
    amp = {b: round(f, 4) for g, f in factors.items() for b in groups[g]["bones"] if f != 1}
    if amp:
        a["boneAmplitude"] = amp
    else:
        a.pop("boneAmplitude", None)
    return dict(recipe, animations=[a])


def fit_budget(rig43, atlas, recipe, joints, groups, work, loops=()):
    """Per animation, the largest per-group amplitude factors at which every joint stays in
    the art budget (holds within theta_soft, transits within theta_hard; spinegen.budget_motion)
    and spine-core 4.3.13 counts no fold.

    Bisection per group (a group out of budget shrinks, one in budget grows back toward its
    last failing value); the groups couple (the body moving under a still fist bends the arm),
    so if the bisection ends out of budget, one common multiplier on top is bisected the same
    way (at 0 the animation is the setup pose, which is in budget). Returns {anim name: report}."""
    from . import budget_motion as bmo
    os.makedirs(work, exist_ok=True)
    out = {}
    for anim in recipe["animations"]:
        name = anim["name"]
        path = os.path.join(work, f"{name}.json")
        rpath = os.path.join(work, f"{name}.recipe.json")

        def evaluate(factors):
            with open(rpath, "w") as f:
                json.dump(_anim_recipe(recipe, anim, factors, groups), f, indent=1)
            kit("generate", rig43, rpath, "-o", path)
            return bmo.measure(path, atlas, joints, [name], loops)[name]

        f = {g: 1.0 for g in groups}
        first = evaluate(f)
        trace = [{"factors": dict(f), "ok": first["ok"]}]
        rec = {"before": _summ(first), "clamped": {}, "factors": dict(f), "fallback": None, "rounds": 1}
        if first["ok"]:
            rec["after"] = rec["before"]
            out[name] = rec
            continue
        lo = {g: 0.0 for g in groups}
        hi = {g: None for g in groups}
        res, best = first, None
        for _ in range(FIT_ROUNDS):
            bad = _blame(res, groups)
            if res["ok"]:
                best = dict(f) if best is None or sum(f.values()) > sum(best.values()) else best
            nxt = dict(f)
            for g in groups:
                if g in bad:
                    hi[g] = f[g]
                    nxt[g] = (lo[g] + hi[g]) / 2
                elif hi[g] is not None:
                    lo[g] = f[g]
                    nxt[g] = (lo[g] + hi[g]) / 2 if hi[g] - lo[g] > FIT_TOL else lo[g]
            if res["ok"] and all(hi[g] is None or hi[g] - lo[g] <= FIT_TOL for g in groups):
                break
            if nxt == f:
                break
            f = nxt
            res = evaluate(f)
            rec["rounds"] += 1
            trace.append({"factors": dict(f), "ok": res["ok"]})
        if best is None or not res["ok"]:
            if best is not None:
                f = best
                res = evaluate(f)
                rec["rounds"] += 1
            else:  # coupled: one multiplier on the bisected factors
                base, glo, ghi = dict(f), 0.0, 1.0
                for _ in range(FIT_ROUNDS):
                    m = (glo + ghi) / 2
                    r = evaluate({g: v * m for g, v in base.items()})
                    rec["rounds"] += 1
                    if r["ok"]:
                        glo = m
                    else:
                        ghi = m
                    if ghi - glo <= FIT_TOL / 2:
                        break
                f = {g: v * glo for g, v in base.items()}
                res = evaluate(f)
                rec["fallback"] = {"multiplier": round(glo, 4), "base": {g: round(v, 4) for g, v in base.items()}}
        rec["factors"] = {g: round(v, 4) for g, v in f.items()}
        rec["after"] = _summ(res)
        rec["ok"] = res["ok"]
        rec["trace"] = trace
        for g, v in groups.items():
            for j in v["joints"]:
                b = first["joints"][j]
                if not b["ok"]:
                    a = res["joints"][j]
                    d = b["worst_dir"]
                    rec["clamped"][j] = {"group": g, "factor": rec["factors"][g], "why": b["why"],
                                         "hard_by": b[d]["hard_by"] if b[d]["transit"] else b[d]["soft_by"],
                                         "before": [b["max_minus"], b["max_plus"]], "after": [a["max_minus"], a["max_plus"]],
                                         "soft": [b["-"]["soft"], b["+"]["soft"]], "hard": [b["-"]["hard"], b["+"]["hard"]]}
        out[name] = rec
    return out


def _summ(res):
    return {"ok": res["ok"], "max_folds": res["max_folds"], "fold_frames": res["fold_frames"],
            "joints": {n: {k: r[k] for k in ("ok", "max_plus", "max_minus", "t0")} | ({"why": r["why"]} if not r["ok"] else {})
                       for n, r in res["joints"].items()}}


def art_requests(fit, recipe, groups, roles):
    """spinegen-artreq/1 items for the arms the budget could not carry: a preset that needs an
    arm far past what the drawn arm bends (the monkey's celebrate: fists over the head from arms
    drawn hanging) is not a motion to scale away but a pose to draw (a raised-arm attachment)."""
    items = []
    presets = {a["name"]: a["preset"] for a in recipe["animations"]}
    for name, rec in fit.items():
        for g, fac in rec["factors"].items():
            if not g.startswith("arm_") or fac >= ART_REQUEST_BELOW:
                continue
            clamped = {j: c for j, c in rec["clamped"].items() if c["group"] == g}
            if not clamped:
                continue
            side = g[len("arm_"):]
            asked = "; ".join(f"{j} {c['before'][0]:.0f}..{c['before'][1]:.0f} deg against soft "
                              f"{-c['soft'][0]:.0f}..{c['soft'][1]:.0f}, hard {-c['hard'][0]:.0f}..{c['hard'][1]:.0f}"
                              for j, c in clamped.items())
            seams = all(str(c.get("hard_by") or "").startswith("seam") for c in clamped.values())
            if seams:  # a cut-out joint opens before it bends: the joint's art, not a pose
                what = (f"overlapping art at the {side} arm's joints ({', '.join(clamped)}): the cut-out opens a "
                        f"seam within a few degrees (or rebuild the arm in joint mode)")
            else:
                what = (f"a second {side} arm drawn in the pose '{name}' reaches ({presets[name]}"
                        + (": raised, elbow bent, fist above the shoulder" if presets[name] == "celebrate" else "")
                        + ") to swap in, since the drawn arm folds before it gets there")
            items.append({"id": f"pose_{name}_{g}", "kind": "joint_art" if seams else "attachment", "slot": None,
                          "layer": None, "what": what,
                          "why": f"the preset asks {asked}; the drawn arm carries {fac:.0%} of its motion "
                                 f"(target {groups[g]['bones'][0]}), so '{name}' plays scaled down",
                          "anim": name, "factor": fac, "status": "open"})
    return items


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
    plants = []
    for a, body in anims.items():
        ev = {e["name"]: e.get("time", 0.0) for e in body.get("events", [])}
        if "takeoff" in ev and "land" in ev:  # the kit's jump: the root flies between them, the feet with it
            dur = _anim_duration(body)
            spans = [s for s in ([0.0, ev["takeoff"]], [ev["land"], dur]) if s[1] > s[0]]
            plants += [{"bone": b, "anim": a, "spans": spans} for b in feet]
        else:
            plants += [{"bone": b, "anim": a} for b in feet]
    return {"counts": counts, "foot_plant": plants,
            **{k: [{"name": c["name"], "order": c["order"]} for c in ir[k]] for k in ("ik", "transform", "path", "physics")}}


def _anim_duration(body):
    """Last key or event time of a Spine 4.3 animation body."""
    t = [e.get("time", 0.0) for e in body.get("events", [])]

    def walk(x):
        if isinstance(x, dict):
            if "time" in x and isinstance(x["time"], (int, float)):
                t.append(x["time"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk({k: v for k, v in body.items() if k != "events"})
    return max(t, default=0.0)


def intro_probe(motion, atlas, recipe):
    """Per animation with an intro: the largest vertex distance of `<name>_in` frame 0 from the setup pose, and of its
    last frame from the loop's frame 0 (spine-core 4.3.13, physics none: the poses the keys make)."""
    out = {}
    setup = oracle.dump(motion, atlas, runtime="4.3", physics="none")["frames"][0]["slots"]
    with open(motion) as f:
        anims = json.load(f)["animations"]
    for a in recipe["animations"]:
        if not a.get("intro"):
            continue
        name_in = a.get("introName", a["name"] + "_in")
        d_in = _anim_duration(anims[name_in])
        f0 = oracle.dump(motion, atlas, anim=name_in, times=[0.0, d_in], runtime="4.3", physics="none")["frames"]
        g0 = oracle.dump(motion, atlas, anim=a["name"], times=[0.0], runtime="4.3", physics="none")["frames"][0]["slots"]
        out[a["name"]] = {"intro": name_in, "seconds": round(d_in, 4), "frame0_vs_setup_px": _slots_dist(f0[0]["slots"], setup),
                          "end_vs_loop_frame0_px": _slots_dist(f0[-1]["slots"], g0)}
    return out


def _slots_dist(fa, fb):
    worst = 0.0
    for slot, sa in fa.items():
        va, vb = sa["vertices"] or [], fb[slot]["vertices"] or []
        for i in range(0, min(len(va), len(vb)), 2):
            worst = max(worst, math.hypot(va[i] - vb[i], va[i + 1] - vb[i + 1]))
    return worst


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
        leg_softness=LEG_SOFTNESS, ik_guard=True, kit_guard=False, budget="auto", joints=None, intro=True):
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
    recipe = make_recipe(roles, presets, size, facing, ik_guard, intro)
    if ik_guard and not kit_guard:
        recipe["ikGuardChains"] = {t: {"knee": g["knee"], "max": g["max"]} for t, g in guard_chains(bridged).items()}
        rep["ik_guard"] = {"mode": "per chain", "chains": guard_chains(bridged)}
    else:
        rep["ik_guard"] = {"mode": "kit default" if ik_guard else "off"}
    loops = [a["name"] for a in recipe["animations"] if a["preset"] in LOOP_PRESETS]
    if ik_guard:
        rep["ik_guard"]["rest_px"] = rest_probe(rig43, atlas_out, recipe, os.path.join(out_dir, "work"))

    budget_path, joints_path = _budget_files(rig_path, budget, joints)
    if budget_path:
        from . import budget_motion as bmo
        bj = bmo.load(budget_path, joints_path)
        groups = joint_groups(bridged, roles, bj)
        fit = fit_budget(rig43, atlas_out, recipe, bj, groups, os.path.join(out_dir, "work"), set(loops))
        for a in recipe["animations"]:
            amp = {b: fit[a["name"]]["factors"][g] for g in groups for b in groups[g]["bones"]
                   if fit[a["name"]]["factors"][g] != 1}
            if amp:
                a["boneAmplitude"] = amp
        reqs = art_requests(fit, recipe, groups, roles)
        rep["budget"] = {"budget": os.path.abspath(budget_path), "joints": os.path.abspath(joints_path),
                         "hold_seconds": bmo.HOLD_SECONDS, "fps": bmo.FPS, "groups": groups, "fit": fit,
                         "artreq": reqs}
        if reqs:
            with open(os.path.join(out_dir, "artreq.json"), "w") as f:
                json.dump({"schema": "spinegen-artreq/1", "items": reqs}, f, indent=1, ensure_ascii=False)
    else:
        rep["budget"] = None
    recipe_path = os.path.join(out_dir, f"{name}.recipe.json")
    with open(recipe_path, "w") as f:
        json.dump(recipe, f, indent=1, ensure_ascii=False)
    rep["recipe"] = {"path": recipe_path, "size": size, "roles": roles, "notes": notes, "kit_guessed_roles": guessed}

    motion = os.path.join(out_dir, f"{name}.motion.json")
    rep["generate_stdout"] = kit("generate", rig43, recipe_path, "-o", motion, "--check")
    if intro and oracle.available("4.3")[0]:
        rep["intro"] = intro_probe(motion, atlas_out, recipe)
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


def _budget_files(rig_path, budget, joints):
    """(budget.json, joints.json) to fit against, or (None, None). "auto": the ones the rig
    builder and spinegen.budget leave beside rig.json, when both are there."""
    if not budget:
        return None, None
    d = os.path.dirname(os.path.abspath(rig_path))
    b = os.path.join(d, "budget.json") if budget == "auto" else budget
    j = joints or os.path.join(d, "joints.json")
    if not (os.path.exists(b) and os.path.exists(j)):
        if budget == "auto":
            return None, None
        raise FileNotFoundError(f"budget {b} or joints {j} missing")
    return b, j


def rest_probe(rig43, atlas, recipe, work):
    """Largest vertex distance between the setup pose and frame 0 of the first animation played
    at amplitude 0 (every preset track at rest): what the IK guard alone does to the drawn
    pose. 0 means a preset that starts at rest starts in the drawn pose."""
    os.makedirs(work, exist_ok=True)
    a = dict(recipe["animations"][0], name="rest_probe", amplitude=0)
    rp, mp = os.path.join(work, "rest_probe.recipe.json"), os.path.join(work, "rest_probe.json")
    with open(rp, "w") as f:
        json.dump(dict(recipe, animations=[a]), f, indent=1)
    kit("generate", rig43, rp, "-o", mp)
    fa = oracle.dump(mp, atlas, runtime="4.3")["frames"][0]["slots"]
    fb = oracle.dump(mp, atlas, anim="rest_probe", times=[0.0], runtime="4.3")["frames"][0]["slots"]
    worst = 0.0
    for slot, sa in fa.items():
        va, vb = sa["vertices"] or [], fb[slot]["vertices"] or []
        for i in range(0, min(len(va), len(vb)), 2):
            worst = max(worst, math.hypot(va[i] - vb[i], va[i + 1] - vb[i + 1]))
    return worst


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
    ap.add_argument("--kit-guard", action="store_true",
                    help="the kit's own 85-95%% guard instead of the bridge's per-chain one (from the setup reach)")
    ap.add_argument("--budget", default="auto",
                    help="budget.json to fit the presets' amplitudes to ('auto': beside rig.json; 'none': no fit)")
    ap.add_argument("--joints", default=None, help="joints.json for --budget (default: beside rig.json)")
    ap.add_argument("--no-intro", action="store_true",
                    help="no one-shot fade-in opening <idle>_in before the idle loops")
    ap.add_argument("--no-qa", action="store_true")
    args = ap.parse_args(argv)
    rep = run(args.rig, args.atlas, args.out, [p.strip() for p in args.presets.split(",") if p.strip()],
              args.facing, args.name, args.arm_softness, not args.no_qa, args.leg_softness, not args.no_ik_guard,
              args.kit_guard, None if args.budget == "none" else args.budget, args.joints, not args.no_intro)
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
    g = rep["ik_guard"]
    print(f"IK guard: {g['mode']}" + (f"; amplitude-0 frame 0 vs setup {g['rest_px']:.3f} px" if "rest_px" in g else ""))
    if rep["budget"]:
        for an, fr in rep["budget"]["fit"].items():
            cl = ", ".join(f"{j} ({c['why']}; {c['group']} x{c['factor']:.2f})" for j, c in fr["clamped"].items())
            print(f"budget {an}: " + ("in budget as the preset is" if not fr["clamped"] else f"clamped {cl}")
                  + ("" if fr["after"]["ok"] else "  STILL OVER"))
        for it in rep["budget"]["artreq"]:
            print("art request:", it["what"])
    for an, it in rep.get("intro", {}).items():
        print(f"intro {it['intro']} ({it['seconds']} s): frame 0 vs setup {it['frame0_vs_setup_px']:.3f} px; "
              f"end vs {an} frame 0 {it['end_vs_loop_frame0_px']:.3f} px")
    print(f"recipe: size {rep['recipe']['size']}, roles {json.dumps(rep['recipe']['roles'], ensure_ascii=False)}")
    for s in rep["smoothness"]:
        print(f"  {s['name']:10s} score {s['score']:3d}  errors {s['errors']}  warnings {s['warnings']}  keys {s['keys']}")
    if "qa" in rep:
        print(rep["qa"]["text"].rstrip())
    print(f"outputs in {args.out}")
    return 0 if rep.get("qa", {}).get("pass", True) else 1


if __name__ == "__main__":
    sys.exit(main())
