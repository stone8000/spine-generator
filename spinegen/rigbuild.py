"""Layers + joints + a template -> an IR rig that bends at its joints.

    python -m spinegen.rigbuild <layers dir> --template biped_mascot --mode joint|rigid -o <outdir>
        [--joints joints.json] [--name monkey] [--no-source] [--mesh lean|refined]
        [--joint-modes modes.json | --joint-modes rigid_budget.json --need 90]
        [--bands bands.json] [--fit-reach 'elbow_l,elbow_r=2,2.25,2.5,3,3.5' [--fit-need 90]]
        [--fit-crouch 'knee_{s}.lower=1.5,3;ankle_{s}.power=0.8,0.85,0.9,0.95,1,1.1,1.2']
        [--no-accessory-copies] [--no-physics-match]

writes rig.json + motion.json (IR), <name>.json (Spine 4.2, spinegen.emit.spine42),
<name>.atlas + <name>.png (spinegen.atlas.pack), joints.json (the budget scanner's
joints, world coordinates) and build_report.json (band_fit.json with --fit-reach /
--fit-crouch); physics mixes matched to the source's swing (match_physics);
then a crouch check in the official runtime (crouch(): knees by the template's
rule, planted feet <= 1 px), after the accessory check (accessory_check).

WHY (SOTA §7 P2: make the art hold big angles)
  The monkey bends like a paper doll: each part is a rigid card on one bone,
  so a joint opens a seam or folds the card over its neighbour long before
  the motion wants to stop (26 deg at the shoulder by eye). The fix is in
  the rig, not the art: every layer becomes a mesh with vertex rows across
  each joint it crosses (spinegen.mesh), the vertices in a joint band are
  shared by the bones on both sides with a ramp through a half-angle
  helper (spinegen.weights; [E1], [E12]: 0 folds to 150 deg in spine-core),
  and the legs get two-bone IK onto planted foot targets so a crouch bends
  the knees instead of skating the feet (M1).

LEG STYLES (template "leg_style", --leg-style auto|outward|forward)
  "outward"  the two-bone leg IK below: a crouch bends each knee away from
             the midline. Right for a front-view mascot's short, flared
             legs; on a realistic character's long, straight trousers it
             is a bow-legged frog squat already at 0.03 H (the Hard Time
             warden, P5_REPORT).
  "forward"  the knee bends toward the camera, which a front view shows as
             the leg getting SHORTER along its own line: a knee target
             ik_knee_<s> under the foot target, pulled toward the hips by
             a world translate constraint (knee_follow_<s>: vertically the
             template's knee_drop share of the hips' move, sideways the
             knee's place on the hip-ankle line, so the knee stays on that
             line), a one-bone IK with compress on the thigh onto it and
             one with compress + stretch on the shin onto the foot target.
             Same joint bands, half-angle helpers and planted feet
             (_forward_legs).
  "auto"     "forward" when the legs are long for the body AND slender
             (_resolve_leg_style), else "outward". spinegen.ir.leg_chains
             reads either kind of leg back from an IR.

WHAT IT BUILDS (template = templates/<name>.json, spinegen.template)
  bones    one per template bone whose joint is known (joints.json), placed
           at the joint and aimed at the joint its tail names; a bone with
           no tail joint is aimed at its own art (centroid of the art
           beyond the joint) or keeps its parent's direction. Accessories
           (source bones no role matched: cuffs, props, a banana) keep their
           setup transform under their mapped parent.
  helpers  a half-angle helper at each elbow and knee: a bone under the
           upper limb at the joint with the lower limb's local setup
           transform, driven by the IR's `half_angle` constraint (mix 0.5).
           The 4.2 emitter expands it to a local rotate-only transform
           constraint with every other mix written 0 and orders it after
           IK here. It is the absolute local form with the helper's setup
           rotation equal to the child's, which turns the helper by exactly
           half of the child's change: the same motion as SOTA's relative
           form with offset -child setup angle.
  ik       a two-bone IK per leg (thigh, shin) onto ik_foot_<s> under root;
           the foot bone hangs from that target, so a planted foot cannot
           slide whatever the hips do. bend direction: from ANATOMY, the
           template's "knee" rule ("outward": a crouch moves the knee away
           from the body's midline, a front view; "facing": along the
           character's facing, a side view). Both directions are solved in
           a small test crouch and the one that moves the knee the way the
           rule says wins (_anatomical_bend). NOT from the setup leg's
           kink: a source leg kinked a few degrees inward (the monkey's
           left, -9 deg) would make that knee cave toward the midline in
           every crouch while the other goes out (P2 review: 56 px). A
           setup leg kinked against the rule is straightened instead (its
           knee moved onto the hip-ankle line, _align_knees), since an IK
           solving the other way would flip it at setup. softness: the
           template's fraction of leg length, capped so the setup pose is
           unchanged (a nearly straight setup leg leaves almost no room;
           the report gives the cap). That is the "outward" leg style;
           see LEG STYLES for "forward".
  meshes   every layer through mesh.build. In "joint" mode a layer gets a
           band at each joint it crosses (its art comes within one
           halfwidth of the joint) where the template says it bends:
           "both" sides at elbows, knees, wrists, ankles; only the "lower"
           side at shoulders and hips. halfwidth = the largest inscribed
           radius of the lower limb's art near the joint (the same rule as
           art_gates.estimate_halfwidth); band half-length = mesh.BAND_EXTENT
           halfwidths each way, or the template joint's "reach" {upper,
           lower} (an asymmetric band: the elbow runs 2.25 halfwidths up the
           sleeve and 2 down the forearm; build(bands=...) sets one joint's,
           and fit_reach() fits it per joint by the budget), shortened so
           neighbouring bands
           on one bone never overlap (half the gap each, unless one needs
           less) and so no band runs past a rigid joint down its lower bone
           (_stop_at_joints: the elbow stops at the wrist, the hand
           stays rigid). A joint's "rows" and "row_points" shape its rows
           (mesh.build). The ankle of a PLANTED foot is one-sided: its band runs
           from the ankle up the shin (the upper half of the band it would
           have had), so everything below the ankle is 100% foot and stays
           on the ground in a crouch; a centred band put up to half of the
           shin's turn into the boot below the ankle (the monkey's boots
           moved 10-20 px there). In "rigid" mode the same meshes have no
           bands.
           The mesh profile (MESH_PROFILES) is "lean" by default.
  modes    per joint, "joint" mode can still keep a joint rigid (no band),
           ramp it without a helper, or ramp it through the helper
           (`joint_modes`; default: helper where the template has one).
           choose_joint_modes() picks them from a measured rigid budget:
           rigid where the cut-out already holds what the motion needs.
  weights  "joint": weights.compute (bone heat + the joint ramps); then the
           source's own accessory weights (a sleeve hem on a cuff bone) are
           carried over by interpolating the source mesh, each as a share
           of its parent bone's weight here (weights.compose_accessory:
           exact at rest, so the bands bend the same and nothing has to be
           faded near a joint). The build fails by name when a physics
           bone that moved source vertices moves none here
           (accessory_check). "rigid": every vertex 100% on the layer's
           bone, the cut-out for comparison.
  physics  the source's constraints, renamed, every field explicit (the IR
           already is), ordered after the transforms. The CLI then measures
           each one's swing in the official runtime against the source's
           and scales down the mix of one that swings harder here
           (match_physics: the monkey's pant hems, whose thighs now turn
           under the leg IK).

ACCESSORY COPIES (P2 open issue 8)
  An accessory sharing only its parent's weight barely moves near a joint,
  where the band's helper and the bone across the joint hold most of the
  row: the monkey's left sleeve swung 17 px of physics against the
  source's 58 (its lower sleeve is 100% cuff there). So each accessory
  gets a copy under each band bone next to its parent (the helper, the
  bone across the joint: cuffL_elbow_mid_l, cuffL_forearm_l): the same
  setup world transform as the accessory, the band bone as parent, and a
  local relative rotate-only transform constraint from the accessory with
  offset -its setup rotation, ordered after every physics constraint, so a
  copy turns by exactly the accessory's own change (physics or keys) about
  the same point. At rest a copy moves like the bone it hangs from, so
  weight moved to it changes nothing without physics or keys (spine-core,
  physics none: <= 6e-4 px in 8 of the monkey's 9 animations; flutter keys
  the cuffs and differs by its own motion). The accessory then takes its
  source share of the whole chain, filled from the parent outward
  (weights.compose_accessory_chain), which never puts a 4th bone on a
  vertex of two band bones (L8). Measured (spine-core, 60 fps, physics
  update vs none, worst of throwit / cheer / chestbeat / flinch; source /
  parent-only / copies): left sleeve 58.4 / 17.0 / 54.0 px, right 32.2 /
  9.6 / 28.3; thighs 23.3 / 14.5 / 17.1 and 23.9 / 7.6 / 19.7 with
  match_physics (without it the copies let the IK-driven pant swing fold
  the thigh in cheer: G1 2, thigh 57.6 px).
  motion   the source's animations where bones map: rotate deltas as they
           are, translate deltas turned into the new parent's frame, scale
           axes swapped where a bone now points 90 deg away. Timelines that
           cannot carry over are dropped and listed (leg FK under IK, deform
           keys of the old meshes, old constraints). The planted targets get
           keys only where the source foot leaves the ground (a jump); IR
           `foot_plant` names the planted spans for QA M1.

COORDINATES
  layers.json canvas is image space (y down); the IR is world space (y up).
  Canvas below is the ONE place that converts (points and directions).
  Everything geometric before the IR (bands, heat bones, art masks) is done
  in canvas pixels, the space mesh.build and weights.compute work in.
"""
import argparse
import json
import math
import os

import numpy as np

from . import atlas as atlasmod
from . import ingest_spine
from . import ir as irmod
from . import lbs, mesh, playback, weights
from . import template as tmpl

MODES = ("joint", "rigid")
JOINT_MODES = ("rigid", "ramp", "helper")
# mesh.build options per profile (spinegen.mesh, "THE LEAN OPTIONS"). "lean":
# constrained CDT, no min-angle refinement, band rows as constrained edges
# with only their outline points (cutting every span of the outline they
# cross), the one-sided covering outline. On the monkey: 582 vertices
# (0.48x the 22 px grid) against 2264 (1.87x) "refined", B2 0.98, 0 folds
# over every animation in spine-core. With the template's band shapes and
# the elbows' fitted reach (P2 A1, A2, review R1, R2): 637 vertices (0.53x),
# elbows 90-98 deg under G2's per-triangle range and fixed window, 0 folds
# in a crouch to 0.10 H with the hips 40 px to either side (P2_REPORT,
# mesh.py THE LEAN OPTIONS).
MESH_PROFILES = {"lean": {"min_angle": 0.0, "outline": "adaptive", "row_points": "ends", "edges": "constrain"},
                 "refined": {}}
SCHEMA_BUDGET_JOINTS = "spinegen-budget-joints/1"
HW_REACH = 40.0       # px: halfwidth = largest inscribed radius within this of the art nearest the joint
CROSS = 1.0           # a layer crosses a joint when its art comes within CROSS x halfwidth of it
SOFT_GUARD = 0.999    # softness stays this fraction under the setup slack (no effect at setup)
BAKE_FPS = 30         # source feet are sampled at this rate to key the IK targets in a jump
RECENTER_ROWS = 0.25  # _recenter reads the cross-section at +-this many halfwidths along the limb
RECENTER_MIN = 2.0    # px: a smaller offset from the centre line is left alone
RECENTER_MAX = 0.6    # halfwidths: the largest move (a joint is not moved off its limb)
LIFT_MIN = 0.5        # px: a source foot that never rises more than this keeps its target planted
LIFT_FULL = 8.0       # px of lift over which a lifted target starts following the source foot's x
BAKE_TOL = 0.25       # px: a baked key is dropped when its neighbours interpolate it this closely
AXIS_SNAP = 10.0      # deg: scale keys swap x/y when a bone now points 90 +- this from its source
FOLDED_MIN = 135.0    # deg: a "both" joint bent more than this at setup is kept rigid (joint_bands)
AXIS_MEAN_MAX = 45.0  # deg: a "both" band runs along the mean of its bones only when they are this close
KNEE_RULES = ("outward", "facing")  # template ik "knee" (see _knee_dir)
LEG_STYLES = ("auto", "outward", "forward")  # template "leg_style" / --leg-style (LEG STYLES)
FORWARD_SIDE_MAX = 0.1  # crouch(): a forward knee moves sideways at most this fraction of the hips' drop ...
FORWARD_SIDE_PX = 2.0   # ... or this many px
FORWARD_BEND_MAX = 20.0  # deg: crouch(): a forward leg ends bent at most this much in the picture
KNEE_TEST = 0.1       # the bend test lowers the hips by this fraction of the leg's length
KNEE_TOL = 0.5        # px: a setup knee this close to the hip-ankle line counts as straight
SETUP_TOL = 1e-3      # px: the chosen bend must reproduce the setup leg this closely
KNEE_SYM_DEG = 10.0   # crouch(): two legs' end bends agree this closely (mirror symmetry)
SHIN_ROWS = 10.0      # px: crouch() reports the slide of the bottom this many px of the shin's art
M1_PX = 1.0           # px: SOTA §5 M1, the most a planted foot vertex may move (the CLI's crouch check)
ACC_TAPER = 1.0       # halfwidths over which an accessory's share tapers to 0 toward a vertex with no room for it
FIT_DEPTHS = (0.05, 0.07, 0.08, 0.10, 0.12, 0.13)  # fit_crouch's crouch grid: hips down, fractions of H
FIT_SHIFTS = (-40.0, -30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0)  # ... and sideways, px
PHYS_MATCH_FPS = 60   # match_physics measures the physics swing at this rate (the source's physics fps)
PHYS_LIMIT_MIN = 1000.0  # L6: a physics "limit" below this throttles follow-through (SOTA §2; Hard Time's 60/80)
PHYS_LIMIT_FIX = 5000.0  # ... and is raised to the 4.2 runtime's default
PHYS_MATCH_TOL = 0.1  # match_physics leaves a constraint alone when it swings at most this much more than the source
LATERAL_PAD = 0.25    # halfwidths added to the widest art a band must take in (rigbuild._lateral)


# ---------------------------------------------------------------- canvas <-> world

class Canvas:
    """layers.json canvas (x right, y DOWN) <-> IR world (y up). The only
    conversion between the two in the builder."""

    def __init__(self, doc):
        w = doc.get("world") or {"origin": [0.0, float(doc["canvas"][1])], "y": "down"}
        if w.get("y", "down") != "down":
            raise ValueError("layers.json world.y must be 'down' (canvas = image space)")
        self.ox, self.oy = (float(v) for v in w["origin"])

    def to_world(self, xy):
        xy = np.asarray(xy, float)
        return np.stack([self.ox + xy[..., 0], self.oy - xy[..., 1]], -1)

    def dir_to_world(self, d):
        d = np.asarray(d, float)
        return np.stack([d[..., 0], -d[..., 1]], -1)


# ---------------------------------------------------------------- public

BAND_KEYS = ("reach", "rows", "row_points", "overlap", "compose", "power")  # what `bands` may set per joint
FIT_STEP = 2.0        # deg: fit_reach's budget scans (the budget's own default step)


def build(layers_dir, joints=None, template="biped_mascot", mode="joint", source="auto", name=None,
          joint_modes=None, mesh_profile="lean", recenter=True, bands=None, accessory_copies=True, leg_style=None):
    """Build the rig. Returns {"ir", "images", "joints" (budget form),
    "report"}.

    layers_dir  an ingest_spine / PSD-export layers dir (layers.json + PNGs)
    joints      joints.json path or dict (default: <layers_dir>/joints.json)
    source      "auto": the source IR named by layers.json (physics,
                animations, accessory weights), None: none, or an IR dict
    joint_modes {joint: "rigid" | "ramp" | "helper"} per-joint override of
                how a joint bends in "joint" mode (choose_joint_modes() picks
                them from a measured budget); default: the template's
    mesh_profile "lean" (default) or "refined", see MESH_PROFILES
    recenter    move elbows and knees onto the art's centre line (default;
                _Builder._recenter says why)
    bands       {joint: {reach, rows, row_points, overlap, compose}} per-joint
                band shape over the template's (one side of a pair:
                "elbow_l"), e.g. fit_reach()'s choice for this character
    accessory_copies  give each accessory a copy under the joint-band bones
                next to its parent (default; ACCESSORY COPIES); False: the
                accessory shares only its parent's weight (P2 R3)
    leg_style   "auto" | "outward" | "forward" (LEG STYLES); default: the
                template's leg_style.default ("auto")
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mesh_profile not in MESH_PROFILES:
        raise ValueError(f"mesh_profile must be one of {sorted(MESH_PROFILES)}")
    doc, imgs = ingest_spine.load_layers(layers_dir)
    if joints is None:
        joints = os.path.join(layers_dir, "joints.json")
    if isinstance(joints, (str, os.PathLike)):
        with open(joints) as f:
            joints = json.load(f)
    t = tmpl.load(template) if isinstance(template, str) else template
    if bands:
        t = json.loads(json.dumps(t))
        for jname, spec in bands.items():
            if jname not in t["joints"]:
                raise ValueError(f"bands: no joint {jname!r} in template {t['name']}")
            bad = set(spec) - set(BAND_KEYS)
            if bad:
                raise ValueError(f"bands {jname}: only {BAND_KEYS} can be set, not {sorted(bad)}")
            for k, v in spec.items():
                t["joints"][jname][k] = dict(t["joints"][jname].get(k) or {}, **v) if k == "reach" else v
    if source == "auto":
        source = _load_source(layers_dir, doc)
    b = _Builder(doc, imgs, joints, t, mode, source, name)
    b.joint_modes = dict(joint_modes or {})
    b.mesh_kw = dict(MESH_PROFILES[mesh_profile])
    b.mesh_profile = mesh_profile
    b.recenter = recenter
    b.accessory_copies = accessory_copies
    if leg_style is not None and leg_style not in LEG_STYLES:
        raise ValueError(f"leg_style must be one of {LEG_STYLES}, not {leg_style!r}")
    b.leg_style_asked = leg_style
    return b.run()


def choose_joint_modes(budget, need, template="biped_mascot"):
    """Per-joint bending mode from a RIGID budget (budget.json of the
    cut-out, e.g. a rigbuild --mode rigid scan): a joint whose cut-out
    already holds `need` degrees both ways (theta_soft) stays rigid; any
    other bends with a ramp, through a half-angle helper where the template
    has one. `need` is degrees, or {joint: degrees} (missing joints: 90).

    WHY: one mode everywhere is wrong both ways. On cut-out art with round,
    overlapping ends a rigid joint already holds and a band only adds
    vertices and risk; a limb root ramped into the torso WITHOUT a helper
    collapsed the upper arm past 100 deg in a separate trial. The budget
    measures which case a joint is in. Returns ({joint: mode}, {joint:
    reason})."""
    t = tmpl.load(template) if isinstance(template, str) else template
    modes, why = {}, {}
    for jname, dirs in budget.get("joints", {}).items():
        want = need.get(jname, 90.0) if isinstance(need, dict) else float(need)
        soft = min(e["soft"] for e in dirs.values())
        spec = t["joints"].get(jname, {})
        if soft >= want:
            modes[jname], why[jname] = "rigid", f"cut-out holds {soft:g} >= {want:g} deg"
        else:
            modes[jname] = "helper" if spec.get("helper") else "ramp"
            by = min(dirs.values(), key=lambda e: e["soft"])["limit_by"]
            why[jname] = f"cut-out holds {soft:g} < {want:g} deg ({by})"
    return modes, why


def fit_reach(layers_dir, joints=None, template="biped_mascot", candidates=None, need=90.0, max_angle=150.0,
              step=FIT_STEP, progress=None, **build_kw):
    """Per joint, the band reach toward the upper bone that holds the most
    (theta_soft, both ways) in the budget scan, from `candidates` ({joint:
    [halfwidths]}). Returns ({joint: {"reach": {"upper": v}}} for build's
    `bands`, {"need", "table": {joint: {str(v): {"+", "-", "min",
    "limit_by"}}}, "chosen"}).

    Each candidate value is built once for every joint that lists it, and
    only those joints are scanned (spinegen.budget, the whole rig's
    canvas): joints on different limbs do not interact. A joint takes the
    value with the largest min(soft+, soft-); among values that reach
    `need` both ways it takes the shortest (fewer vertices bent, and the
    window G2 sums stays closest to the bend); ties go to the shorter.

    WHY (P2 review R1): one reach for both elbows has to trade between them.
    The monkey's left sleeve fans its art 1.7 halfwidths off the axis at the
    crook, so a short band squeezes single triangles there below 0.3 of
    their area (L+ 88 deg at 2.25 halfwidths, 96 at 2.5); its right sleeve
    leans 1.2-2.3 halfwidths toward the torso, so a long band loses area in
    G2's fixed window when the arm bends toward the body (R- 80 deg at
    3.5, 90 at 2.25). The rig is fitted to the art, as a rigger would,
    by the gate that judges it."""
    from . import budget as budgetmod
    t = tmpl.load(template) if isinstance(template, str) else template
    candidates = candidates or {}
    values = sorted({float(v) for vs in candidates.values() for v in vs})
    table = {j: {} for j in candidates}
    for v in values:
        js = [j for j, vs in candidates.items() if v in [float(x) for x in vs]]
        res = build(layers_dir, joints, t, bands={j: {"reach": {"upper": v}} for j in js}, **build_kw)
        ir = res["ir"]
        images = res["images"]
        scan = budgetmod.scan(ir, images, res["joints"], step, max_angle, only=set(js))
        for j in js:
            d = scan["joints"][j]
            table[j][f"{v:g}"] = {"+": d["+"]["soft"], "-": d["-"]["soft"],
                                  "min": min(d["+"]["soft"], d["-"]["soft"]),
                                  "limit_by": [d["+"]["limit_by"], d["-"]["limit_by"]],
                                  "reach": res["report"]["bands"][j]["reach"]}
            if progress:
                progress(f"fit {j} upper reach {v:g}: soft +{d['+']['soft']:g} / -{d['-']['soft']:g}")
    chosen = {}
    for j, rows in table.items():
        vals = sorted(rows, key=float)
        enough = [v for v in vals if rows[v]["min"] >= need]
        chosen[j] = float(enough[0] if enough else max(vals, key=lambda v: (rows[v]["min"], -float(v))))
    return ({j: {"reach": {"upper": v}} for j, v in chosen.items()},
            {"need": need, "step": step, "table": table, "chosen": chosen})


def fit_crouch(layers_dir, joints=None, template="biped_mascot", candidates=None, depths=FIT_DEPTHS,
               shifts=FIT_SHIFTS, progress=None, bands=None, **build_kw):
    """Per leg, the leg joints' band shape with the fewest folds over a
    crouch grid. `candidates` {template joint ("knee_{s}"): {band key:
    [values]}} (keys: "power", or "lower"/"upper" for the reach in
    halfwidths), e.g. {"knee_{s}": {"lower": [1.5, 3]}, "ankle_{s}":
    {"power": [0.9, 1, 1.1]}}: every combination is built once (both sides
    alike) and each side keeps its own best. Returns ({joint: band spec}
    for build's `bands`, {"table": {side: {combo: folds}}, "chosen": {side:
    combo}, "depths", "shifts"}).

    The grid: the hips (the leg IK thighs' parent) lowered by depth x H and
    moved dx px sideways, legs on IK (the R2 crouch); folds are lbs's G1
    (pinned to spine-core by C1) on the layers that carry the side's leg
    bands. The two legs do not interact. Ties go to the combination closest
    to the template (its own reach, power 1).

    WHY (P2 open issue: deep-crouch folds): where a calf folds depends on
    where its art sits off the ramps' axes, and two calves of one character
    differ. The monkey's right hem flares 80-93 px off the ankle's axis just
    above the ankle and wants the foot's ramp gentle there (power < 1) and
    the knee's ramp run down the whole calf; its left calf reaches 70-77 px
    off the knee's axis on the inner side and wants the opposite (in the
    continuum, det J over the art's pixels at 0.13 H with the hips 40 px
    over, power 0.9 lifts the right calf from -0.06 to +0.02 and takes the
    left from +0.02 to -0.22). The synthetic flared calf of the tests folds
    more with the knee run down the whole calf (139 against 84 over 0.10-
    0.16 H). One template value cannot serve them all, so each leg is
    fitted, as fit_reach fits each elbow."""
    import itertools
    t = tmpl.load(template) if isinstance(template, str) else template
    raw = {k: v for k, v in (candidates or {}).items()}
    axes = [(jn, key, [float(v) for v in vals]) for jn, spec in raw.items() for key, vals in spec.items()]
    sides = sorted({j.rsplit("_", 1)[-1] for j in t["joints"] for jn in raw
                    if "{s}" in jn and jn.replace("{s}", j.rsplit("_", 1)[-1]) == j})

    def default(jn, key, side):
        spec = t["joints"].get(jn.replace("{s}", side), {})
        if key == "power":
            return float(spec.get("power", 1.0))
        return float((spec.get("reach") or {}).get(key, mesh.BAND_EXTENT))

    table = {s: {} for s in sides}
    for combo in itertools.product(*[vals for _, _, vals in axes]):
        bb = json.loads(json.dumps(bands or {}))
        for (jn, key, _), v in zip(axes, combo):
            for side in sides:
                j = jn.replace("{s}", side)
                e = bb.setdefault(j, {})
                if key == "power":
                    e["power"] = v
                else:
                    e["reach"] = dict(e.get("reach") or {}, **{key: v})
        res = build(layers_dir, joints, t, bands=bb, **build_kw)
        ir = res["ir"]
        ev = lbs.Evaluator(ir)
        b = irmod.by_name(ir["bones"])
        hips = b[ir["ik"][0]["bones"][0]]["parent"]
        layers = {side: {L for jn, _, _ in axes for L in res["report"]["bands"].get(jn.replace("{s}", side),
                                                                                   {}).get("layers", [])}
                  for side in sides}
        folds = {side: 0 for side in sides}
        for d in depths:
            for dx in shifts:
                per = ev.folds(pose={hips: {"x": b[hips]["x"] + dx, "y": b[hips]["y"] - d * ir["H"]}})["slots"]
                for side in sides:
                    folds[side] += sum(per.get(L, 0) for L in layers[side])
        key = ", ".join(f"{jn} {k} {v:g}" for (jn, k, _), v in zip(axes, combo))
        for side in sides:
            table[side][key] = {"folds": folds[side], "combo": list(combo)}
        if progress:
            progress(f"fit crouch {key}: folds " + ", ".join(f"{s} {folds[s]}" for s in sides))
    chosen, out = {}, {}
    for side in sides:
        def cost(item, side=side):
            _, r = item
            dev = sum(abs(math.log(v / default(jn, k, side))) for (jn, k, _), v in zip(axes, r["combo"]))
            return (r["folds"], dev)
        key, r = min(table[side].items(), key=cost)
        chosen[side] = key
        for (jn, k, _), v in zip(axes, r["combo"]):
            if abs(v - default(jn, k, side)) < 1e-12:
                continue
            j = jn.replace("{s}", side)
            e = out.setdefault(j, {})
            if k == "power":
                e["power"] = v
            else:
                e["reach"] = dict(e.get("reach") or {}, **{k: v})
    return out, {"table": table, "chosen": chosen, "depths": list(depths), "shifts": list(shifts)}


def write(outdir, res):
    """Write a build() result; returns {kind: path}."""
    from .emit import spine42
    os.makedirs(outdir, exist_ok=True)
    ir, name = res["ir"], res["ir"]["name"]
    paths = {"rig": os.path.join(outdir, "rig.json"), "motion": os.path.join(outdir, "motion.json"),
             "skeleton": os.path.join(outdir, f"{name}.json"), "atlas": os.path.join(outdir, f"{name}.atlas"),
             "joints": os.path.join(outdir, "joints.json"), "report": os.path.join(outdir, "build_report.json")}
    irmod.save(ir, paths["rig"], paths["motion"])
    emit_report = {}
    spine42.write(ir, paths["skeleton"], report=emit_report)
    _, paths["png"] = atlasmod.write(paths["atlas"], res["images"])
    _dump(paths["joints"], res["joints"])
    rep = dict(res["report"], emit={k: v for k, v in emit_report.items() if v})
    _dump(paths["report"], rep)
    return paths


# ---------------------------------------------------------------- the builder

class _Builder:
    def __init__(self, doc, imgs, jdoc, t, mode, src, name):
        self.doc, self.imgs, self.jdoc, self.t, self.mode, self.src = doc, imgs, jdoc, t, mode, src
        self.cv = Canvas(doc)
        self.jxy = {k: np.asarray(v["xy"], float) for k, v in jdoc.get("joints", {}).items()}
        self.jsrc = {k: v.get("src", "") for k, v in jdoc.get("joints", {}).items()}
        self.layers = sorted(doc["layers"], key=lambda L: L["z"])
        # never "rig": write() puts the IR in rig.json next to <name>.json
        self.name = name or (src["name"] if src else os.path.basename(os.path.normpath(doc.get("name", "skeleton"))))
        self.notes = []
        self.spec = {b["name"]: b for b in t["bones"]}
        self.joint_modes = {}  # joint -> "rigid" | "ramp" | "helper" (build() sets it)
        self.recenter = True     # elbows and knees onto the art's centre line (_recenter; build() sets it)
        self.mesh_kw, self.mesh_profile = dict(MESH_PROFILES["lean"]), "lean"
        self.bones = {}      # name -> {"parent", "head", "tail" (canvas), "len", "role", "limb_parent"}
        self.order = []      # bone names, parents first
        self._art = {}
        self.straightened = {}  # knee joint -> px moved onto the hip-ankle line (_align_knees)
        self.acc_report = {}    # accessory -> what it carried from the source (_carry_accessories)
        self.acc_copies = {}    # accessory copy bone -> {"of": accessory, "parent": band bone} (ACCESSORY COPIES)
        self.accessory_copies = True  # False: accessories share their parent's weight only (P2 R3; build() sets it)
        self.leg_style_asked = None   # build()'s leg_style (None: the template's default)
        self.leg_style, self.leg_style_why = "outward", {}  # resolved in run() (_resolve_leg_style)
        self.knee_targets = {}        # forward legs: thigh IK name -> its knee target bone

    # ---- orchestration

    def run(self):
        self.leg_style, self.leg_style_why = self._resolve_leg_style()
        self.skeleton()
        if self.recenter and self._recenter():
            self.bones, self.order = {}, []
            self.skeleton()
        self.straightened = self._align_knees()
        if self.straightened:
            self.bones, self.order = {}, []
            self.skeleton()
        self.ir = irmod.new_rig(self.name)
        self.ir["bones"] = self.ir_bones()
        self.bands = self.joint_bands()
        self.ir_constraints()
        meshes, stats = self.meshes()
        self.add_accessory_copies(1 + max([c["order"] for k in irmod.CONSTRAINT_KINDS for c in self.ir[k]],
                                          default=-1))
        self.ir_slots(meshes)
        self.carry_motion()
        self.fit_skeleton_box()
        errs = irmod.validate(self.ir)
        if errs:
            raise AssertionError("rigbuild produced an invalid IR:\n  " + "\n  ".join(errs[:20]))
        images = {L["name"]: self.imgs[L["name"]] for L in self.layers}
        report = {"schema": "spinegen-rigbuild/1", "name": self.name, "mode": self.mode,
                  "mesh_profile": self.mesh_profile, "joint_modes": {j: b["kind"] for j, b in self.bands.items()},
                  "template": self.t["name"], "layers": stats, "ik": self.ik_report,
                  "leg_style": dict(self.leg_style_why, style=self.leg_style),
                  "bands": {j: _round_band(b) for j, b in self.bands.items()},
                  "totals": _totals(stats), "accessories": self.accessory_check(), "notes": self.notes}
        return {"ir": self.ir, "images": images, "joints": self.budget_joints(), "report": report}

    # ---- skeleton (canvas)

    def skeleton(self):
        t = self.t
        ik_by_target = {ik["target"]: ik for ik in t["ik"]}
        for spec in t["bones"]:
            name, j = spec["name"], spec["joint"]
            if spec["kind"] == "ik_target":
                ik = ik_by_target.get(name)
                if ik is None or j not in self.jxy or not all(b in self.bones for b in ik["bones"]):
                    continue
            elif j not in self.jxy:
                if not spec["optional"] and spec["parents"]:
                    self.notes.append(f"bone {name}: joint {j} not given; bone left out")
                continue
            elif spec["optional"] and self.jsrc.get(j, "").startswith("bone_tip:"):
                continue  # the joint is only where another bone ends: no bone of its own
            parent = next((p for p in spec["parents"] if p in self.bones), None)
            if spec["parents"] and parent is None:
                self.notes.append(f"bone {name}: none of its parents {spec['parents']} exists; left out")
                continue
            self.bones[name] = {"parent": parent, "head": self.jxy[j], "joint": j, "kind": spec["kind"],
                                "role": self._role(spec)}
            self.order.append(name)
        # limb parent: the bone that ends at this bone's joint (the shin for a
        # foot hanging from its IK target), else the real parent
        for name, b in self.bones.items():
            b["limb_parent"] = b["parent"]
            for other in self.order:
                if other != name and b["joint"] in self.spec[other]["tails"] and self.bones[other]["kind"] == "bone":
                    b["limb_parent"] = other
                    break
        for name in self.order:
            self._aim(name)
        self.helpers = []
        for h in t["helpers"]:
            if h["of"] in self.bones and h["parent"] in self.bones:
                of = self.bones[h["of"]]
                self.bones[h["name"]] = {"parent": h["parent"], "head": of["head"], "tail": of["tail"],
                                         "len": 0.0, "dir": of["dir"], "joint": h["joint"], "kind": "helper",
                                         "role": "helper", "limb_parent": h["parent"]}
                self.order.insert(self.order.index(h["of"]) + 1, h["name"])
                self.helpers.append(h)
        self.knee_targets = {}
        if self.leg_style == "forward":
            for ik in t["ik"]:
                thigh, shin = ik["bones"]
                if not all(n in self.bones for n in (thigh, shin, ik["target"])):
                    continue
                name = f"ik_knee_{ik['side']}" if ik.get("side") else f"ik_knee_{ik['name']}"
                head = self.bones[shin]["head"]
                self.bones[name] = {"parent": ik["target"], "head": head.copy(), "tail": head.copy(), "len": 0.0,
                                    "dir": np.array([1.0, 0.0]), "joint": None, "kind": "ik_target",
                                    "role": "ik_target", "limb_parent": ik["target"]}
                self.order.append(name)
                self.knee_targets[ik["name"]] = name
        for acc in self.jdoc.get("accessories", []):
            parent = acc.get("parent")
            if parent not in self.bones:
                self.notes.append(f"accessory {acc['name']}: parent {parent!r} not in the rig; put under root")
                parent = self.order[0]
            d = np.asarray(acc["dir"], float)
            head = np.asarray(acc["xy"], float)
            self.bones[acc["name"]] = {"parent": parent, "head": head, "len": float(acc["len"]), "dir": d,
                                       "tail": head + d * float(acc["len"]), "joint": None, "kind": "accessory",
                                       "role": "accessory", "limb_parent": parent}
            self.order.append(acc["name"])

    def _recenter(self):
        """Move each elbow and knee (a "both" joint with a helper) across
        the limb onto the middle of the art's cross-section there. Returns
        the shifts made.

        WHY: a joint band bends evenly only about a pivot on the limb's
        centre line. A pivot off to one side compresses that side more in
        one direction and stretches it in the other: the monkey's source
        elbow_r sits 0.48 halfwidths (20 px) off centre, and its band kept
        less than 0.9 of its area from 52 deg one way while it GREW the
        other way; on the centre line the same rig held to 108 deg. A
        cut-out does not care where its pivot is (the seam hides it), so
        source rigs often have it off centre."""
        shifts = {}
        n0 = len(self.notes)
        bands = self.joint_bands()  # provisional (its notes come again with the final bands)
        del self.notes[n0:]
        for jname, band in bands.items():
            spec = self.t["joints"].get(jname, {})
            if spec.get("band") != "both" or not spec.get("helper") or not band["layers"]:
                continue
            c, a, hw = band["center"], band["axis"], band["halfwidth"]
            n = np.array([-a[1], a[0]])
            mids = []
            for s0 in np.linspace(-RECENTER_ROWS, RECENTER_ROWS, 5) * hw:
                lo, hi = math.inf, -math.inf
                for L in self.layers:
                    if L["name"] not in band["layers"]:
                        continue
                    ii, jj = np.nonzero(self._layer_mask(L))
                    P = np.stack([L["x"] + jj + 0.5, L["y"] + ii + 0.5], 1) - c
                    k = np.abs(P @ a - s0) < 1.0
                    if k.any():
                        lat = P[k] @ n
                        lo, hi = min(lo, lat.min()), max(hi, lat.max())
                if math.isfinite(lo):
                    mids.append(0.5 * (lo + hi))
            if not mids:
                continue
            d = float(np.clip(np.median(mids), -RECENTER_MAX * hw, RECENTER_MAX * hw))
            if abs(d) >= RECENTER_MIN:
                self.jxy[jname] = self.jxy[jname] + d * n
                shifts[jname] = d
                self.notes.append(f"joint {jname}: moved {d:+.1f} px across the limb onto the art's centre line "
                                  f"({d / hw:+.2f} halfwidths)")
        return shifts

    def _resolve_leg_style(self):
        """(style, why): the leg style this character gets (LEG STYLES).
        Asked explicitly (build's leg_style, --leg-style) it is taken as is.
        "auto" reads two things off the art, and both must say "realistic"
        for "forward":
          leg_H     hip to ankle (joints.json, mean of the legs) over the
                    character's height H (joints.json H, else the art's):
                    a mascot's legs are a short part of a big-headed body;
          slender   that length over the legs' mean width (the thigh and
                    shin layers' opaque area / length): a long, straight
                    trouser leg against a mascot's short, flared stump.
        Measured: the Hard Time warden 0.43 H / 4.3, the monkey 0.28 H /
        1.8, the tests' synthetic biped 0.354 H / 3.1. A template without
        leg_style is always "outward"."""
        spec = self.t.get("leg_style") or {}
        asked = self.leg_style_asked or spec.get("default", "outward")
        why = {"asked": asked}
        if asked not in LEG_STYLES:
            raise ValueError(f"leg_style must be one of {LEG_STYLES}, not {asked!r}")
        if asked != "auto":
            return asked, why
        if not spec:
            return "outward", dict(why, reason="the template has no leg_style")
        lengths, slender = [], []
        for ik in self.t["ik"]:
            hj = self.spec.get(ik["bones"][0], {}).get("joint")
            aj = self.spec.get(ik["target"], {}).get("joint")
            if hj not in self.jxy or aj not in self.jxy:
                continue
            n = float(np.linalg.norm(self.jxy[hj] - self.jxy[aj]))
            lengths.append(n)
            area = sum(int(self._layer_mask(L).sum()) for L in self.layers if L.get("role") in ik["bones"])
            if area and n:
                slender.append(n * n / area)
        if not lengths:
            return "outward", dict(why, reason="no leg joints")
        H = self.jdoc.get("H")
        if not H:
            lo, hi = math.inf, -math.inf
            for L in self.layers:
                rows = np.flatnonzero(self._layer_mask(L).any(1))
                if len(rows):
                    lo, hi = min(lo, L["y"] + rows[0]), max(hi, L["y"] + rows[-1] + 1)
            H = hi - lo
        leg_H = float(np.mean(lengths)) / float(H)
        sl = float(np.mean(slender)) if slender else 0.0
        lim_h = float(spec.get("auto_forward_min_leg_H", 0.38))
        lim_s = float(spec.get("auto_forward_min_slender", 3.5))
        style = "forward" if leg_H >= lim_h and sl >= lim_s else "outward"
        return style, dict(why, leg_H=round(leg_H, 4), min_leg_H=lim_h, slender=round(sl, 3), min_slender=lim_s,
                           H=round(float(H), 2),
                           reason=f"legs {leg_H:.3f} H ({'>=' if leg_H >= lim_h else '<'} {lim_h:g}), "
                                  f"length/width {sl:.2f} ({'>=' if sl >= lim_s else '<'} {lim_s:g})")

    def _knee_dir(self, ik):
        """World unit vector the template's "knee" rule says a crouch moves
        this leg's knee along, or None when the template fixes
        bend_positive itself.

        "outward": away from the body's midline, the hips bone's origin (or
        the legs' mean hip when that sits on this leg's line). Geometric,
        not by name: a front-view character's left leg is on the image's
        right, and the template's side names say nothing about that.
        "facing": +x for joints.json "facing": 1, -x for -1.
        No rule and no bend_positive: "outward" (the setup kink is never
        the rule, see the module doc)."""
        rule = ik.get("knee", None if "bend_positive" in ik else "outward")
        if rule is None:
            return None
        if rule == "outward":
            thigh = ik["bones"][0]
            hip = self.cv.to_world(self.bones[thigh]["head"])
            dx = hip[0] - self.cv.to_world(self.bones[self.bones[thigh]["parent"]]["head"])[0]
            if abs(dx) < 1e-6:
                heads = [self.cv.to_world(self.bones[i["bones"][0]]["head"])[0] for i in self.t["ik"]
                         if i["bones"][0] in self.bones]
                dx = hip[0] - float(np.mean(heads))
            if abs(dx) < 1e-6:
                raise ValueError(f"ik {ik['name']}: knee 'outward' but the leg sits on the body's midline")
            return np.array([math.copysign(1.0, dx), 0.0])
        if rule == "facing":
            f = self.jdoc.get("facing")
            if f not in (1, -1):
                raise ValueError(f"ik {ik['name']}: knee 'facing' needs joints.json 'facing': 1 (right) or -1 (left)")
            return np.array([float(f), 0.0])
        raise ValueError(f"ik {ik['name']}: knee must be one of {KNEE_RULES}, not {rule!r}")

    def _align_knees(self):
        """Straighten every setup leg whose knee sits on the wrong side of
        its hip-ankle line for the template's knee rule: the knee joint is
        moved onto that line (world px moved are returned per knee joint).

        WHY: a two-bone IK puts the knee on one side of the hip-target line
        and nowhere else. A setup leg kinked the other way (the monkey's
        left leg, 9 deg inward, 9 px off the line) cannot be reproduced by
        the anatomical bend; the IK would flip it to the mirror position at
        setup. A straight leg is reproduced by either bend, so it is the
        smallest change that lets the knee follow anatomy. The art does not
        move, only the pivot."""
        out = {}
        if self.leg_style == "forward":
            return out  # the knee bends toward the camera: no side of the hip-ankle line is wrong
        for ik in self.t["ik"]:
            if not all(b in self.bones for b in ik["bones"] + [ik["target"]]):
                continue
            want = self._knee_dir(ik)
            if want is None:
                continue
            thigh, shin = ik["bones"]
            hip, knee, ankle = (self.cv.to_world(self.bones[n]["head"]) for n in (thigh, shin, ik["target"]))
            n = _knee_normal(hip, ankle, want)
            off = float((knee - hip) @ n)
            if off < -KNEE_TOL:
                how = ik.get("straighten", "knee")
                d = ankle - hip
                t = float((knee - hip) @ d / (d @ d))   # where the knee sits along hip -> ankle
                # move one joint across the line so that the line passes the knee
                which, by = {"knee": (shin, -off), "ankle": (ik["target"], off / t),
                             "hip": (thigh, off / (1.0 - t))}[how]
                j = self.bones[which]["joint"]
                self.jxy[j] = self.jxy[j] + self.cv.dir_to_world(by * n)  # the y flip is its own inverse
                out[j] = abs(by)
                self.notes.append(f"joint {j}: moved {abs(by):.1f} px so the knee is on the hip-ankle line (the "
                                  f"setup leg was kinked {-off:.1f} px against the template's knee rule "
                                  f"{ik['knee']!r})")
        return out

    def _role(self, spec):
        j = self.t["joints"].get(spec["joint"] or "", {})
        return j.get("budget") or spec["name"] if spec["kind"] == "bone" else spec["kind"]

    def _aim(self, name):
        """Tail of a bone: its template tail joint, else its own art (or
        its parent's art beyond the joint), else the parent's direction."""
        b, spec = self.bones[name], self.spec[name]
        head = b["head"]
        if b["kind"] == "ik_target":
            b.update(dir=np.array([1.0, 0.0]), len=0.0, tail=head.copy())
            return
        for j in spec["tails"]:
            if j in self.jxy and np.linalg.norm(self.jxy[j] - head) > 0.5:
                d = self.jxy[j] - head
                b.update(dir=d / np.linalg.norm(d), len=float(np.linalg.norm(d)), tail=self.jxy[j].copy())
                return
        lp = b["limb_parent"]
        pdir = self.bones[lp]["dir"] if lp in self.bones and "dir" in self.bones[lp] else None
        pts = self._art_points([name]) if self._art_points([name]).size else (
            self._art_points([lp]) if lp else np.zeros((0, 2)))
        if pts.size and pdir is not None:
            pts = pts[(pts - head) @ pdir > 0]
        if pts.size:
            d = pts.mean(0) - head
            if np.linalg.norm(d) > 1e-6:
                d = d / np.linalg.norm(d)
                b.update(dir=d, len=float(max(((pts - head) @ d).max(), 1.0)))
                b["tail"] = head + d * b["len"]
                return
        d = pdir if pdir is not None else np.array([1.0, 0.0])
        b.update(dir=d, len=0.0, tail=head.copy())

    # ---- art masks (canvas)

    def _layer_mask(self, L):
        return self.imgs[L["name"]][..., 3] > mesh.ALPHA_MIN

    def _art_points(self, roles):
        """Canvas pixel centres of the art of the shown layers bound to `roles`."""
        key = tuple(roles)
        if key not in self._art:
            pts = []
            for L in self.layers:
                if L["role"] in roles and "hidden" not in L["tags"]:
                    ii, jj = np.nonzero(self._layer_mask(L))
                    pts.append(np.stack([L["x"] + jj + 0.5, L["y"] + ii + 0.5], 1))
            self._art[key] = np.concatenate(pts) if pts else np.zeros((0, 2))
        return self._art[key]

    def _halfwidth(self, roles, center):
        """Largest inscribed radius of the roles' art within HW_REACH px of
        the art pixel nearest `center` (art_gates.estimate_halfwidth)."""
        import cv2
        Ls = [L for L in self.layers if L["role"] in roles and "hidden" not in L["tags"]]
        if not Ls:
            return None
        W, H = self.doc["canvas"]
        solid = np.zeros((H, W), np.uint8)
        for L in Ls:
            solid[L["y"]:L["y"] + L["h"], L["x"]:L["x"] + L["w"]] |= self._layer_mask(L).astype(np.uint8)
        dist = cv2.distanceTransform(np.pad(solid, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
        ii, jj = np.nonzero(solid)
        d = np.hypot(jj + 0.5 - center[0], ii + 0.5 - center[1])
        near = d <= d.min() + HW_REACH
        return float(dist[ii[near], jj[near]].max())

    # ---- IR bones (world)

    def ir_bones(self):
        out, world, wrot = [], {}, {}
        for name in self.order:
            b = self.bones[name]
            head = self.cv.to_world(b["head"])
            d = self.cv.dir_to_world(b["dir"])
            rot = math.degrees(math.atan2(d[1], d[0]))
            if b["kind"] == "helper":  # the lower limb's local setup transform, see the module doc
                of = next(h["of"] for h in self.t["helpers"] if h["name"] == name)
                rot = wrot[of]
            p = b["parent"]
            if p is None:
                bone = irmod.new_bone(name, None, head[0], head[1], rot, b["len"], role=b["role"])
            else:
                lx, ly = lbs.to_local(world[p], head[0], head[1])
                bone = irmod.new_bone(name, p, lx, ly, rot - wrot[p], b["len"], role=b["role"])
            world[name] = lbs.compose(world.get(p), bone["x"], bone["y"], bone["rotation"], 1, 1, 0, 0, "normal")
            wrot[name] = rot
            out.append(bone)
        self.world, self.wrot = world, wrot
        return out

    # ---- joint bands (canvas)

    def joint_bands(self):
        """{joint: band} for every template joint that bends and has both
        bones: center, axis (upper -> lower), halfwidth, extent, the bones
        and which layers carry it."""
        bands = {}
        for jname, spec in self.t["joints"].items():
            if spec["band"] == "none":
                continue
            lower = next((n for n in self.order if self.bones[n]["joint"] == jname and self.bones[n]["kind"] == "bone"),
                         None)
            if lower is None or jname not in self.jxy:
                continue
            upper = self.bones[lower]["limb_parent"] if spec["band"] == "both" else self.bones[lower]["parent"]
            if upper is None:
                continue
            c = self.jxy[jname]
            lo, up = self.bones[lower], self.bones[upper]
            if spec["band"] == "lower" or up["len"] <= 0:
                axis = lo["dir"]
            elif lo["len"] <= 0 or lo["dir"] @ up["dir"] < math.cos(math.radians(AXIS_MEAN_MAX)):
                axis = up["dir"]  # a foot leaves the shin sideways: the band runs along the shin
            else:
                axis = lo["dir"] + up["dir"]
            axis = axis / (np.linalg.norm(axis) or 1.0)
            hw = self._halfwidth([lower], c)
            if hw is None and spec["band"] == "both":
                hw = self._halfwidth([upper], c)
            if not hw:
                self.notes.append(f"joint {jname}: no art to measure a halfwidth; no band")
                continue
            helper = spec.get("helper") if spec.get("helper") in self.bones else None
            kind = "rigid" if self.mode == "rigid" else self.joint_modes.get(jname, "helper" if helper else "ramp")
            if (kind != "rigid" and spec["band"] == "both" and up["len"] > 0 and lo["len"] > 0
                    and jname not in self.joint_modes and lo["dir"] @ up["dir"] < math.cos(math.radians(FOLDED_MIN))):
                # a limb folded back on itself at setup (the Hard Time
                # warden's baton arm, 164 deg): one axis cannot order the
                # two pieces (the forearm's art lies on the upper arm's side
                # of the joint and would ramp onto the upper arm, which kept
                # it at the shoulder when the elbow opened, P5); each piece
                # stays on its own bone, as the cut-out it was
                bend = math.degrees(math.acos(max(-1.0, min(1.0, float(lo["dir"] @ up["dir"])))))
                self.notes.append(f"joint {jname}: folded {bend:.0f} deg at setup (> {FOLDED_MIN:g}); kept rigid")
                kind = "rigid"
            if kind not in JOINT_MODES:
                raise ValueError(f"joint {jname}: mode must be one of {JOINT_MODES}, not {kind!r}")
            if kind == "helper" and helper is None:
                self.notes.append(f"joint {jname}: no helper in the template; ramp without one")
                kind = "ramp"
            if kind != "helper":
                helper = None
            if spec.get("compose", "upper") not in weights.COMPOSE:
                raise ValueError(f"joint {jname}: compose must be one of {weights.COMPOSE}, not {spec['compose']!r}")
            want = spec.get("reach") or {}
            reach = [float(want.get("upper", mesh.BAND_EXTENT)), float(want.get("lower", mesh.BAND_EXTENT))]
            bands[jname] = {"center": c, "axis": axis, "halfwidth": hw, "reach": reach,
                            "upper": upper, "lower": lower, "helper": helper, "mode": spec["band"],
                            "kind": kind, "budget": spec.get("budget"),
                            "rows": spec.get("rows", mesh.BAND_ROWS), "row_points": int(spec.get("row_points", 0)),
                            "overlap": bool(spec.get("overlap", False)), "compose": spec.get("compose", "upper"),
                            "power": float(spec.get("power", 1.0))}
        targets = {n for n in self.order if self.bones[n]["kind"] == "ik_target"}
        for jname, band in bands.items():
            band["layers"] = [L["name"] for L in self.layers if self._carries(L, band)]
            band["pivot"] = band["center"]
            band["planted"] = self.bones[band["lower"]]["parent"] in targets
        self._g2_windows(bands)
        self._stop_at_joints(bands)
        # two bands on one layer must not overlap: they share the distance
        # between them, half each, except that a band reaching less than its
        # half leaves the rest to the other (a short shoulder band lets a long
        # elbow band run up the sleeve; two default bands still meet halfway).
        # A band marked "overlap" (the knee, the planted ankle) does not share
        # with the band above it on its chain: it runs into it and composes
        # (weights.py CHAIN COMPOSITION). The band above keeps the length its
        # template asks for (the knee's 1.5 halfwidths down the calf, which
        # the planted ankle overlaps); with no reach in the template it still
        # stops at its half of the gap: grown to the full default (P2 A2: the
        # hip band 1.14 -> 2.0 halfwidths, 118 of the thigh's 134 px) it put a
        # third bone on most of the thigh and left the pant hem no room under
        # L8's three influences (P2 review; the knee band still reaches its
        # 100 px up the thigh, and the crouch folds did not change)
        new = {j: list(b["reach"]) for j, b in bands.items()}
        for a, ba in bands.items():
            for b, bb in bands.items():
                if ba.get("overlap") and ba["upper"] == bb["lower"]:
                    continue  # a runs into the band above it
                if bb.get("overlap") and bb["upper"] == ba["lower"] and self.t["joints"][a].get("reach"):
                    continue  # a asked for its length (the knee's 1.5 down the calf); b overlaps it
                if a != b and set(ba["layers"]) & set(bb["layers"]):
                    d = float(np.linalg.norm(ba["center"] - bb["center"]))
                    ia = 1 if (bb["center"] - ba["center"]) @ ba["axis"] > 0 else 0  # a's half toward b
                    ib = 1 if (ba["center"] - bb["center"]) @ bb["axis"] > 0 else 0
                    ra, rb = ba["reach"][ia] * ba["halfwidth"], bb["reach"][ib] * bb["halfwidth"]
                    new[a][ia] = min(new[a][ia], max(0.5 * d, d - rb) / ba["halfwidth"])
        for j, b in bands.items():
            b["reach"] = new[j]
            if not self.t["joints"][j].get("reach"):
                b["reach"] = [min(new[j])] * 2  # no reach in the template: symmetric, the measured default
        # a planted foot is 100% foot below the ankle (module doc): its band
        # becomes the shin-side half of the band it would have had, so it
        # cannot reach the knee's band either
        for jname, band in bands.items():
            if band["planted"]:
                half = 0.5 * band["reach"][0]
                band["reach"] = [half, half]
                band["center"] = band["pivot"] - half * band["halfwidth"] * band["axis"]
                self.notes.append(f"joint {jname}: planted foot; band from the ankle {2 * half:.2f} "
                                  f"halfwidths up the shin, nothing below the ankle")
        for jname, band in bands.items():
            band["extent"] = 0.5 * (band["reach"][0] + band["reach"][1])
            if abs(band["reach"][0] - band["reach"][1]) > 1e-9:
                self.notes.append(f"joint {jname}: band reaches {band['reach'][0]:.2f} halfwidths toward "
                                  f"{band['upper']}, {band['reach'][1]:.2f} toward {band['lower']}")
        for band in bands.values():
            band["lateral"] = self._lateral(band)
        return bands

    def _g2_windows(self, bands):
        """band["window"] = [up, lo]: the halfwidths from the pivot that
        G2's area sum reads (art_gates WHICH TRIANGLES), from the joints'
        geometry alone: mesh.BAND_EXTENT, or half the distance to the
        nearest other banded joint on a shared layer, the same both ways;
        lo never past the next joint down the lower bone; lo = 0 for a
        planted foot (only the shin side bends). It is the band every joint
        had before the template gave reach and overlap (P2 A1/A2), so G2
        reads the same art before and after a band changes length.

        WHY (P2 review R1): read over the band itself, a longer band
        averages in more of the lightly bent limb, and the gate eases as
        the rig changes: the monkey's right elbow toward the body read 94
        deg over A1's 3.5-halfwidth band and 80 over this window."""
        for jname, band in bands.items():
            hw = band["halfwidth"]
            ext = mesh.BAND_EXTENT
            for other, bo in bands.items():
                if other != jname and set(band["layers"]) & set(bo["layers"]):
                    ext = min(ext, 0.5 * float(np.linalg.norm(band["pivot"] - bo["pivot"])) / hw)
            lo = ext
            for tj in self.spec[band["lower"]]["tails"]:
                if tj in self.jxy:
                    d = float((self.jxy[tj] - band["pivot"]) @ band["axis"]) / hw
                    if d > 0:
                        lo = min(lo, d)
                    break
            band["window"] = [ext, 0.0 if band["planted"] else lo]

    def _stop_at_joints(self, bands):
        """A band's lower half ends at the next joint down its lower bone
        (the elbow's at the wrist, the knee's at the ankle); a band that
        overlaps the band above it ("overlap") ends at that band's joint.

        WHY: a rigid joint means the part beyond it moves as one piece with
        its bone (the template's wrists: a mascot's hand image carries the
        forearm, so a wrist band would steal the elbow's length). A band
        running past it bends that part with the elbow: the monkey's fist
        curled with a 3-halfwidth elbow band (P2 F3). On the monkey the
        default 2-halfwidth band already ends 2-6 px short of the wrists.
        Past a banded joint the next band takes over (sharing, or chain
        composition), and below a planted ankle everything is foot."""
        for jname, band in bands.items():
            for tj in self.spec[band["lower"]]["tails"]:
                if tj not in self.jxy:
                    continue
                d = float((self.jxy[tj] - band["center"]) @ band["axis"]) / band["halfwidth"]
                if 0 < d < band["reach"][1]:
                    band["reach"][1] = d
                    self.notes.append(f"joint {jname}: band stops at the joint {tj}, "
                                      f"{d:.2f} halfwidths down {band['lower']}")
                break
            if band.get("overlap"):
                up = self.bones[band["upper"]]
                d = float((band["center"] - up["head"]) @ band["axis"]) / band["halfwidth"]
                if 0 < d < band["reach"][0]:
                    band["reach"][0] = d
                    self.notes.append(f"joint {jname}: band stops at the joint {up['joint']}, "
                                      f"{d:.2f} halfwidths up {band['upper']}")

    def _lateral(self, band):
        """Band reach sideways, in halfwidths: enough to take in all the art
        of the layers that carry it within its length (weights.band_mask)."""
        up, lo = (r * band["halfwidth"] for r in band["reach"])
        bones = [{"name": n, "head": tuple(self.bones[n]["head"]), "tail": tuple(self.bones[n]["tail"])}
                 for n in (band["upper"], band["lower"])]
        j = _weight_joint(dict(band, lateral=weights.LATERAL))
        reach = weights.LATERAL
        for L in self.layers:
            if L["name"] not in band["layers"]:
                continue
            ii, jj = np.nonzero(self._layer_mask(L))
            pts = np.stack([L["x"] + jj + 0.5, L["y"] + ii + 0.5], 1)
            s, lat = weights.band_coords(pts, j, bones)
            near = (s >= -up - 2.0) & (s <= lo + 2.0)
            if near.any():
                reach = max(reach, float(lat[near].max()) / band["halfwidth"] + LATERAL_PAD)
        return reach

    def _carries(self, L, band):
        """Does layer L bend at this band (template side rule + its art
        reaches the joint)?"""
        side_ok = L["role"] == band["lower"] or (band["mode"] == "both" and L["role"] == band["upper"])
        if not side_ok:
            return False
        ii, jj = np.nonzero(self._layer_mask(L))
        if not len(ii):
            return False
        d = np.hypot(L["x"] + jj + 0.5 - band["center"][0], L["y"] + ii + 0.5 - band["center"][1]).min()
        return bool(d <= CROSS * band["halfwidth"])

    # ---- constraints

    def ir_constraints(self):
        order = 0
        self.ik_report = {}
        bind = lbs.bind_pose(self.ir)
        if self.leg_style == "forward":
            order = self._forward_legs(bind)
        for ik in ([] if self.leg_style == "forward" else self.t["ik"]):
            if not all(b in self.bones for b in ik["bones"] + [ik["target"]]):
                continue
            thigh, shin = ik["bones"]
            hip, knee = self.cv.to_world(self.bones[thigh]["head"]), self.cv.to_world(self.bones[shin]["head"])
            ankle = self.cv.to_world(self.bones[ik["target"]]["head"])
            l1, l2 = float(np.linalg.norm(knee - hip)), self.bones[shin]["len"]
            slack = max(0.0, l1 + l2 - float(np.linalg.norm(ankle - hip)))
            want = float(ik.get("softness", 0.0)) * (l1 + l2)
            soft = min(want, slack * SOFT_GUARD)
            c = {"name": ik["name"], "order": order, "runs": True, "skin_required": False,
                 "bones": [thigh, shin], "target": ik["target"], "mix": 1.0, "softness": soft,
                 "bend_positive": bool(ik.get("bend_positive", True)), "compress": False,
                 "stretch": bool(ik.get("stretch", False)), "uniform": False}
            bend_deg = self._bend(thigh, shin)
            knee_dir = self._knee_dir(ik)
            rep = {}
            if knee_dir is not None:
                c["bend_positive"], rep = self._anatomical_bend(c, knee_dir)
            err = self._setup_error(c, bind)
            if err > SETUP_TOL:
                self.notes.append(f"ik {ik['name']}: bend_positive {c['bend_positive']} moves the setup leg by "
                                  f"{err:.2f} px (the setup kink opposes it)")
            self.ir["ik"].append(c)
            order += 1
            self.ik_report[ik["name"]] = {"softness": round(soft, 4), "softness_wanted": round(want, 3),
                                          "setup_slack": round(slack, 4), "bend_deg": round(bend_deg, 3),
                                          "bend_positive": c["bend_positive"], "knee": ik.get("knee"),
                                          "knee_dir": None if knee_dir is None else knee_dir.tolist(),
                                          "straightened_px": round(sum(self.straightened.get(self.bones[n]["joint"], 0.0)
                                                                       for n in (thigh, shin, ik["target"])), 3),
                                          "straightened_joint": next((self.bones[n]["joint"] for n in
                                                                      (thigh, shin, ik["target"]) if
                                                                      self.bones[n]["joint"] in self.straightened),
                                                                     None),
                                          "setup_error_px": round(err, 6), "planted": bool(ik.get("planted")),
                                          **rep}
            if soft < want:
                self.notes.append(f"ik {ik['name']}: softness {want:.1f} px capped to {soft:.2f} px, the setup "
                                  f"leg's slack (more would move the setup foot)")
        for h in self.helpers:
            self.ir["transform"].append({"name": h["name"].replace("_mid", "_half"), "order": order, "runs": True,
                                         "skin_required": False, "kind": "half_angle", "bone": h["name"],
                                         "target": h["of"], "mix": float(h.get("mix", 0.5))})
            order += 1
        rename = self._rename()
        for p in (self.src or {}).get("physics", []):
            bone = rename.get(p["bone"])
            if bone not in self.bones:
                self.notes.append(f"physics {p['name']}: bone {p['bone']!r} has no counterpart; dropped")
                continue
            c = json.loads(json.dumps(p))
            c.update(bone=bone, order=order, runs=True)
            if float(c.get("limit", PHYS_LIMIT_FIX)) < PHYS_LIMIT_MIN:
                # a velocity cap this low saturates at a few hundred units/s
                # (inertia 0.5: ~120): every big move's follow-through is cut
                # off (Hard Time's warden: 60 / 80; P1 L6, SOTA §2)
                self.notes.append(f"physics {p['name']}: limit {c['limit']:g} < {PHYS_LIMIT_MIN:g} raised to "
                                  f"{PHYS_LIMIT_FIX:g} (the runtime default; a low limit throttles follow-through)")
                c["limit"] = PHYS_LIMIT_FIX
            self.ir["physics"].append(c)
            order += 1

    def _forward_legs(self, bind):
        """The "forward" leg style's constraints (LEG STYLES); returns the
        next free order. Per leg, in this order: a world translate
        constraint knee_follow_<s> on the knee target toward the hips, a
        one-bone IK on the thigh onto the knee target (compress), a one-bone
        IK on the shin onto the foot target (compress + stretch: the shin
        inherits the thigh's shortening along its axis, so it must be
        allowed to stretch back to the foot in the thigh's frame)."""
        order = 0
        spec = dict((self.t.get("leg_style") or {}).get("forward") or {})
        drop = float(spec.get("knee_drop", 0.3))
        side_rule = spec.get("knee_side", "on_line")
        legs = []
        for ik in self.t["ik"]:
            knee_t = self.knee_targets.get(ik["name"])
            if knee_t is None:
                continue
            thigh, shin = ik["bones"]
            hips = self.bones[thigh]["parent"]
            hip, knee, ankle = (np.array(bind[n][4:6]) for n in (thigh, shin, ik["target"]))
            d = hip - ankle
            u = float((knee - ankle) @ d / (d @ d))  # the knee's place along ankle -> hip
            mix_x = {"on_line": u, "foot": 0.0, "hips": 1.0}.get(side_rule)
            if mix_x is None:
                mix_x = float(side_rule)
            off = lbs.to_local(bind[hips], *knee)
            legs.append((ik, knee_t, thigh, shin, hips, u, mix_x, off))
        for ik, knee_t, thigh, shin, hips, u, mix_x, off in legs:
            self.ir["transform"].append({
                "name": f"knee_follow_{ik['side']}", "order": order, "runs": True, "skin_required": False,
                "kind": "transform", "bones": [knee_t], "target": hips, "local": False, "relative": False,
                "offset": {"rotation": 0.0, "x": float(off[0]), "y": float(off[1]), "scale_x": 0.0, "scale_y": 0.0,
                           "shear_y": 0.0},
                "mix": {"rotate": 0.0, "x": float(mix_x), "y": drop, "scale_x": 0.0, "scale_y": 0.0, "shear_y": 0.0}})
            order += 1
        made = []
        for ik, knee_t, thigh, shin, hips, u, mix_x, off in legs:
            c = {"name": ik["name"], "order": order, "runs": True, "skin_required": False, "bones": [thigh],
                 "target": knee_t, "mix": 1.0, "softness": 0.0, "bend_positive": True,
                 "compress": bool(spec.get("compress", True)), "stretch": bool(spec.get("stretch", False)),
                 "uniform": False}
            self.ir["ik"].append(c)
            made.append(c)
            order += 1
        for ik, knee_t, thigh, shin, hips, u, mix_x, off in legs:
            c = {"name": f"shin_ik_{ik['side']}", "order": order, "runs": True, "skin_required": False,
                 "bones": [shin], "target": ik["target"], "mix": 1.0, "softness": 0.0, "bend_positive": True,
                 "compress": True, "stretch": True, "uniform": False}
            self.ir["ik"].append(c)
            made.append(c)
            order += 1
        for ik, knee_t, thigh, shin, hips, u, mix_x, off in legs:
            names = [ik["name"], f"shin_ik_{ik['side']}"]
            cs = [c for c in made if c["name"] in names]
            tf = [c for c in self.ir["transform"] if c["name"] == f"knee_follow_{ik['side']}"]
            rig = dict(self.ir, ik=cs, transform=tf, physics=[])
            w = lbs.Evaluator(rig).world()
            idx = irmod.bone_index(rig)
            err = float(max(abs(np.asarray(w[idx[n]]) - np.asarray(bind[n])).max() for n in (thigh, shin)))
            if err > SETUP_TOL:
                self.notes.append(f"ik {ik['name']}: the forward leg moves the setup leg by {err:.2f} px")
            base = {"style": "forward", "knee": "forward", "knee_dir": None, "knee_target": knee_t,
                    "knee_drop": drop, "knee_side": side_rule, "knee_mix_x": round(mix_x, 4),
                    "knee_along": round(u, 4), "bend_deg": round(self._bend(thigh, shin), 3),
                    "setup_error_px": round(err, 6), "softness": 0.0, "straightened_px": 0.0}
            self.ik_report[ik["name"]] = dict(base, planted=False, bones=[thigh], target=knee_t)
            self.ik_report[names[1]] = dict(base, planted=bool(ik.get("planted")), bones=[shin],
                                            target=ik["target"])
        return order

    def _bend(self, thigh, shin):
        a, b = self.bones[thigh]["dir"], self.bones[shin]["dir"]
        a, b = self.cv.dir_to_world(a), self.cv.dir_to_world(b)
        return math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a @ b))

    def _setup_error(self, c, bind):
        """How far (px, largest world-matrix entry) the IK constraint `c`
        moves its bones from their setup pose."""
        rig = dict(self.ir, ik=[c], transform=[], physics=[])
        w = lbs.Evaluator(rig).world()
        idx = irmod.bone_index(rig)
        return float(max(abs(np.asarray(w[idx[n]]) - np.asarray(bind[n])).max() for n in c["bones"]))

    def _anatomical_bend(self, c, knee_dir):
        """(bend_positive, report): the IK bend whose knee moves along
        `knee_dir` (world) when the hips drop KNEE_TEST x the leg's length.
        Each bend is solved in that test crouch (this IK alone, softness 0)
        and the one whose knee lies on the knee_dir side of the hip-ankle
        line wins: a direct test of what the rule asks, whatever Spine's
        sign convention and the leg's orientation."""
        thigh, shin = c["bones"]
        b = irmod.by_name(self.ir["bones"])
        hips = b[thigh]["parent"]
        depth = KNEE_TEST * (b[thigh]["length"] + b[shin]["length"])
        side = {}
        for bend in (True, False):
            rig = dict(self.ir, ik=[dict(c, bend_positive=bend, softness=0.0)], transform=[], physics=[])
            ev = lbs.Evaluator(rig)
            w = ev.world(pose={hips: {"y": b[hips]["y"] - depth}})
            hip, knee, ankle = (np.array(w[ev.index[n]][4:6]) for n in (thigh, shin, c["target"]))
            side[bend] = float((knee - hip) @ _knee_normal(hip, ankle, knee_dir))
        bend = side[True] > side[False]
        return bend, {"knee_test_px": round(max(side.values()), 3), "knee_test_depth": round(depth, 3)}

    # ---- meshes and weights

    def meshes(self):
        out, stats = {}, []
        for L in self.layers:
            alpha = self.imgs[L["name"]][..., 3]
            bands = [b for b in self.bands.values() if L["name"] in b["layers"] and b["kind"] != "rigid"]
            m = mesh.build(alpha, (L["x"], L["y"]), bands=[_mesh_band(b) for b in bands], **self.mesh_kw)
            if self.mode == "rigid" or not bands:
                w = [[(L["role"], 1.0)] for _ in range(len(m["verts"]))]
            else:
                names = [L["role"]] + [n for b in bands for n in (b["upper"], b["lower"], b["helper"]) if n]
                names = list(dict.fromkeys(names))
                bones = [{"name": n, "head": tuple(self.bones[n]["head"]), "tail": tuple(self.bones[n]["tail"])}
                         for n in names]
                joints = [_weight_joint(b) for b in bands]
                w = weights.compute(m["verts"], m["tris"], mesh.outlines(m), bones, joints)
                w = self._hard_band_ends(m, bands, w)
                w = self._planted_sole(L, m, w)
            if self.mode == "joint":
                w = self._carry_accessories(L, m, bands, w)
            out[L["name"]] = (m, w)
            st = {"layer": L["name"], "bone": L["role"], "bands": [b["budget"] or "" for b in bands],
                  **{k: m["stats"][k] for k in ("vertices", "triangles", "hull", "holes", "grid_vertices",
                                                 "min_angle", "missed_art", "overdraw")},
                  "unrefined": m["stats"]["unrefined"]}
            if bands:
                bj = [_weight_joint(b) for b in bands]
                bones = [{"name": n, "head": tuple(self.bones[n]["head"]), "tail": tuple(self.bones[n]["tail"])}
                         for n in dict.fromkeys([n for b in bands for n in (b["upper"], b["lower"], b["helper"]) if n])]
                st["b2"] = weights.b2(m["verts"], w, bj, bones)
            stats.append(st)
        return out, stats

    def _planted_sole(self, L, m, w):
        """A planted foot's own layer is 100% foot at and below its ankle
        (canvas y >= the ankle's), the line crouch() and planted_vertices()
        check M1 on. _hard_band_ends measures along the ankle's axis and
        only within the band's lateral reach: a wide boot's toe 60 px off a
        tilted shin axis kept 4% shin from bone heat and slid 1.2 px in a
        0.05 H crouch (the Hard Time warden, P5). No-op when the layer is
        not a planted foot or no vertex qualifies (the monkey's boots)."""
        b = self.bones.get(L["role"])
        if b is None or b.get("kind") != "bone" or self.bones.get(b["parent"], {}).get("kind") != "ik_target":
            return w
        ay = float(b["head"][1])
        out = []
        for v, row in zip(m["verts"], w):
            if v[1] >= ay - 1e-6 and any(n != L["role"] for n, _ in row):
                row = [(L["role"], 1.0)]
            out.append(row)
        return out

    def _hard_band_ends(self, m, bands, w):
        """Past a band's end the joint is over: a vertex beyond the lower
        end keeps none of the upper bone (or helper), one beyond the upper
        end none of the lower. Bone heat leaks a few percent across (a sole
        would ride on the shin and slide when the knee bends); this moves
        that weight to the bone on the vertex's own side."""
        names = list(dict.fromkeys([n for row in w for n, _ in row]
                                   + [n for b in bands for n in (b["upper"], b["lower"], b["helper"]) if n]))
        M = weights.as_matrix(w, names)
        col = {n: i for i, n in enumerate(names)}
        for b in bands:
            bones = [{"name": n, "head": tuple(self.bones[n]["head"]), "tail": tuple(self.bones[n]["tail"])}
                     for n in (b["upper"], b["lower"], b["helper"]) if n]
            s, lat = weights.band_coords(m["verts"], _weight_joint(b), bones)
            up, lo = (r * b["halfwidth"] + weights.END_TOL for r in b["reach"])
            near = lat <= b["lateral"] * b["halfwidth"]
            # past the lower end the bones of the band above on the chain go
            # too (an "overlap" band composes with them): below a planted
            # ankle the knee's ramp, which runs down the whole calf along the
            # knee's own axis, can reach a hem vertex the ankle's axis puts
            # past the ankle (P2: a 0.023 knee-helper weight slid the
            # monkey's left hem 1.4 px in a 0.05 H crouch)
            above = tuple(n for o in bands if o["lower"] == b["upper"] for n in (o["upper"], o["helper"]) if n)
            for side, keep, drop in ((s > lo, b["lower"], (b["upper"], b["helper"]) + above),
                                     (s < -up, b["upper"], (b["lower"], b["helper"]))):
                rows = side & near
                for n in drop:
                    if n:
                        M[rows, col[keep]] += M[rows, col[n]]
                        M[rows, col[n]] = 0.0
        return weights.prune_weights(M, names, prune=weights.KEEP)  # compute pruned them (PRUNING)

    def _carry_accessories(self, L, m, bands, w):
        """Source weights of accessory bones (a cuff on a sleeve hem),
        carried onto the new mesh as a share of the accessory's PARENT
        bone (weights.compose_accessory): the source's accessory weight
        over accessory + parent, interpolated from the source mesh. A
        vertex 100% cuff in the source and 40% upper arm here gets 40%
        cuff. Records what each accessory carried in self.acc_report.

        WHY not a fade near the joints (the old rule, ACC_FADE, scaled by
        each band's reach): once the elbow band ran 3.5 halfwidths up the
        sleeve and the knee band 2 up the thigh (P2 A1/A2), the fade
        covered every vertex the cuffs and pant hems held and their physics
        moved nothing (review: cuffR 12 -> 0, pantL/pantR 8 -> 0 vertices;
        in spine-core the left sleeve's physics-driven motion 8.3 -> 1.1 px
        at 30 fps, the thighs' 7.6 / 4.4 -> 0). Composing with the parent
        is exact at rest, so it needs no fade (weights.compose_accessory)."""
        accs = [b for b in L.get("src", {}).get("influences", [])
                if b in self.bones and self.bones[b]["kind"] == "accessory" and b != L["role"]]
        if not accs or self.src is None:
            return w
        att = irmod.resolve_attachment(self.src, None, L["slot"], L["attachment"])
        if att is None or att["type"] != "mesh" or not att.get("weights"):
            return w
        from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
        sv = np.asarray(att["vertices"], float).reshape(-1, 2)
        nv = self.cv.to_world(m["verts"])
        rename = self._rename()

        def carried(vals):
            lin = LinearNDInterpolator(sv, vals)(nv)
            return np.where(np.isnan(lin), NearestNDInterpolator(sv, vals)(nv), lin)

        chains = {a: self._accessory_chain(a, bands) for a in accs}
        names = list(dict.fromkeys([n for row in w for n, _ in row] + accs
                                   + [self.bones[a]["parent"] for a in accs]
                                   + [n for c in chains.values() for pair in c for n in pair]))
        M = weights.as_matrix(w, names)
        for parent in dict.fromkeys(self.bones[a]["parent"] for a in accs):
            mine = [a for a in accs if self.bones[a]["parent"] == parent]
            src = {a: np.array([sum(inf[1] for inf in row if inf[0] == a) for row in att["weights"]])
                   for a in mine}
            par = np.array([sum(inf[1] for inf in row if rename.get(inf[0]) == parent) for row in att["weights"]])
            got = {a: np.clip(carried(v), 0.0, 1.0) for a, v in src.items()}
            tot = sum(got.values()) + np.clip(carried(par), 0.0, 1.0)
            for k, a in enumerate(mine):
                # shares of the parent's ORIGINAL weight: each later accessory
                # takes its part of what the earlier ones left
                left = 1.0 - sum(got[b] for b in mine[:k]) / np.maximum(tot, 1e-9)
                share = np.where(tot > 1e-6, got[a] / np.maximum(tot, 1e-9), 0.0)
                share = np.where(left > 1e-9, share / np.maximum(left, 1e-9), 0.0)
                # a vertex with three bones already keeps its share on the
                # parent (L8); the share tapers to 0 toward those vertices
                # over ACC_TAPER halfwidths, so the physics motion of two
                # neighbours never differs by a whole share (on the monkey's
                # thigh a 0.47 jump between two outline vertices 11 px apart
                # flipped the sliver triangle across the thigh in cheer)
                chain = chains[a]
                _, stuck = weights.accessory_chain_split(M, names, chain, share)
                if stuck.any() and bands:
                    hw = float(np.mean([b["halfwidth"] for b in bands]))
                    d = np.linalg.norm(m["verts"][:, None, :] - m["verts"][stuck][None, :, :], axis=-1).min(1)
                    share = share * np.clip(d / (ACC_TAPER * hw), 0.0, 1.0)
                n = weights.compose_accessory_chain(M, names, chain, share)
                for bone, c in chain[1:]:
                    if n.get(c):
                        self.acc_copies.setdefault(c, {"of": a, "parent": bone})
                rep = self.acc_report.setdefault(a, {"parent": parent, "layers": {}})
                rep["layers"][L["name"]] = {
                    "source_vertices": int((src[a] > 0).sum()), "source_mass": round(float(src[a].sum()), 3),
                    "vertices": int(((M[:, [names.index(c) for _, c in chain]]) > 0).any(1).sum()),
                    "mass": round(float(M[:, [names.index(c) for _, c in chain]].sum()), 3),
                    "copies": {c: k for c, k in n.items() if c != a and k}}
        return weights.prune_weights(M, names, prune=weights.KEEP)  # compute pruned them (PRUNING)

    def _accessory_chain(self, acc, bands):
        """[(bone, accessory bone)]: the accessory's parent and the accessory,
        then for each of this layer's bands at the parent its helper and the
        bone across the joint, each with a copy of the accessory hanging from
        it (ACCESSORY COPIES); only the parent without copies."""
        parent = self.bones[acc]["parent"]
        chain = [(parent, acc)]
        if not self.accessory_copies:
            return chain
        for b in bands:
            if parent not in (b["upper"], b["lower"]):
                continue
            other = b["lower"] if parent == b["upper"] else b["upper"]
            for bone in (b["helper"], other):
                if bone and bone != parent and bone not in [x for x, _ in chain]:
                    chain.append((bone, f"{acc}_{bone}"))
        return chain

    def add_accessory_copies(self, order):
        """Put the accessory copies that got weight (self.acc_copies) in the
        IR: a bone under its band bone with the accessory's setup world
        transform, and a local relative rotate transform constraint from the
        accessory, ordered after every physics constraint. Returns the next
        free constraint order. ACCESSORY COPIES in the module doc."""
        if not self.acc_copies:
            return order
        ir_b = irmod.by_name(self.ir["bones"])
        for c, spec in sorted(self.acc_copies.items()):
            a, p = spec["of"], spec["parent"]
            ab = self.bones[a]
            head = self.cv.to_world(ab["head"])
            lx, ly = lbs.to_local(self.world[p], head[0], head[1])
            rot = self.wrot[a] - self.wrot[p]
            bone = irmod.new_bone(c, p, lx, ly, rot, ab["len"], role="accessory")
            self.ir["bones"].append(bone)
            self.world[c] = lbs.compose(self.world[p], bone["x"], bone["y"], bone["rotation"], 1, 1, 0, 0, "normal")
            self.wrot[c] = self.wrot[a]
            self.bones[c] = {"parent": p, "head": ab["head"], "tail": ab["tail"], "len": ab["len"], "dir": ab["dir"],
                             "joint": None, "kind": "accessory_copy", "role": "accessory", "limb_parent": p,
                             "of": a}
            self.order.append(c)
            zero = {"rotation": 0.0, "x": 0.0, "y": 0.0, "scale_x": 0.0, "scale_y": 0.0, "shear_y": 0.0}
            self.ir["transform"].append({
                "name": f"{c}_follow", "order": order, "runs": True, "skin_required": False, "kind": "transform",
                "bones": [c], "target": a, "local": True, "relative": True,
                "offset": dict(zero, rotation=-float(ir_b[a]["rotation"])),
                "mix": dict(zero, rotate=1.0)})
            order += 1
        return order

    def accessory_check(self):
        """What each physics-driven accessory bone moves, source vs this rig:
        {"ok", "lost", "bones": {bone: {"physics", "source_vertices",
        "source_mass", "vertices", "mass", "layers"}}}. "lost" names the
        physics bones that moved source vertices and move none here; the
        CLI fails on it (in "joint" mode; "rigid" carries no accessory
        weights by definition).

        WHY (P2 review): G1/M1/C1 cannot see it and P1 measures bone tips,
        so a carry-over that strips a cuff's vertices leaves its physics
        constraint running on a bone that moves nothing, silently."""
        phys = {c["bone"] for c in self.ir.get("physics", [])}
        counts = {}
        for ir, key in ((self.src, "source"), (self.ir, "new")):
            for sk in (ir or {}).get("skins", []):
                for atts in sk["attachments"].values():
                    for att in atts.values():
                        for row in att.get("weights") or []:
                            seen = set()
                            for inf in row:
                                kind = self.bones.get(inf[0], {}).get("kind")
                                if inf[1] > 0 and kind in ("accessory", "accessory_copy"):
                                    acc = self.bones[inf[0]].get("of", inf[0])  # a copy counts for its accessory
                                    c = counts.setdefault(acc, {"source": [0, 0.0], "new": [0, 0.0]})[key]
                                    c[0] += acc not in seen
                                    c[1] += float(inf[1])
                                    seen.add(acc)
        bones = {}
        for b, c in sorted(counts.items()):
            bones[b] = {"physics": b in phys, "source_vertices": c["source"][0],
                        "source_mass": round(c["source"][1], 3), "vertices": c["new"][0],
                        "mass": round(c["new"][1], 3), "layers": self.acc_report.get(b, {}).get("layers", {})}
        lost = [b for b, r in bones.items() if r["physics"] and r["source_vertices"] and not r["vertices"]]
        if self.mode != "joint":
            lost = []
        for b in lost:
            self.notes.append(f"accessory {b}: its physics moved {bones[b]['source_vertices']} source vertices "
                              f"and moves none here")
        return {"ok": not lost, "lost": lost, "bones": bones}

    # ---- slots and skin

    def ir_slots(self, meshes):
        slots, atts = {}, {}
        for L in self.layers:
            s = L.get("slot", L["name"])
            if s not in slots:
                slots[s] = irmod.new_slot(s, L["role"], None, blend=L.get("blend") or "normal")
            if "hidden" not in L["tags"]:
                slots[s]["attachment"] = L.get("attachment", L["name"])
                slots[s]["bone"] = L["role"]
            m, w = meshes[L["name"]]
            img = self.imgs[L["name"]]
            atts.setdefault(s, {})[L.get("attachment", L["name"])] = {
                "type": "mesh", "name": L.get("attachment", L["name"]), "path": L["name"],
                "color": L.get("color") or "ffffffff", "width": float(img.shape[1]), "height": float(img.shape[0]),
                "uvs": np.asarray(m["uvs"]).tolist(), "triangles": np.asarray(m["tris"]).tolist(),
                "hull": int(m["hull"]), "edges": None, "vertices": self.cv.to_world(m["verts"]).tolist(),
                "weights": [[[n, float(x)] for n, x in row] for row in w], "sequence": None}
        self.ir["slots"] = list(slots.values())
        self.ir["skins"] = [{"name": "default", "bones": [], "ik": [], "transform": [], "path": [], "physics": [],
                             "color": None, "attachments": atts}]

    # ---- motion carry-over

    def _rename(self):
        """Source bone -> bone of this rig."""
        out = {src: role for role, src in self.jdoc.get("bones", {}).items() if role in self.bones}
        for acc in self.jdoc.get("accessories", []):
            out[acc["name"]] = acc["name"]
        return out

    def carry_motion(self):
        self.ir["foot_plant"] = []
        if self.src is None:
            return
        self.ir["events"] = json.loads(json.dumps(self.src.get("events", [])))
        rename = self._rename()
        sbind = lbs.bind_pose(self.src)
        sbones = irmod.by_name(self.src["bones"])
        ik_bones = {b for c in self.ir["ik"] for b in c["bones"]}
        slots = {s["name"] for s in self.ir["slots"]}
        physics = {c["name"] for c in self.ir["physics"]}
        dropped = {}

        def srot(n):
            return math.degrees(math.atan2(sbind[n][2], sbind[n][0])) if n is not None else 0.0

        def drop(why):
            dropped[why] = dropped.get(why, 0) + 1

        for anim in self.src.get("animations", []):
            tls = []
            for tl in anim["timelines"]:
                typ, prop = tl["type"], tl["property"]
                if typ == "bone":
                    new = rename.get(tl["target"])
                    if new is None:
                        drop(f"bone {tl['target']}: no counterpart")
                        continue
                    if new in ik_bones and prop != "inherit":
                        drop(f"bone {tl['target']} {prop}: under leg IK")
                        continue
                    old_parent = sbones[tl["target"]]["parent"]
                    d_parent = srot(old_parent) - (self.wrot[self.bones[new]["parent"]] if self.bones[new]["parent"]
                                                   else 0.0)
                    d_own = self.wrot[new] - srot(tl["target"])
                    tl2 = _carry_bone_timeline(tl, new, d_parent, d_own, self.notes)
                    if tl2 is not None:
                        tls.append(tl2)
                elif typ == "slot":
                    if tl["target"] in slots:
                        tls.append(json.loads(json.dumps(tl)))
                    else:
                        drop(f"slot {tl['target']}: no counterpart")
                elif typ == "physics":
                    if tl["target"] in ("", None) or tl["target"] in physics:
                        tls.append(json.loads(json.dumps(tl)))
                    else:
                        drop(f"physics {tl['target']}: dropped constraint")
                elif typ in ("event", "draw_order"):
                    tl2 = json.loads(json.dumps(tl))
                    if typ == "draw_order":
                        for k in tl2["keys"]:
                            if k.get("order") is not None:
                                k["order"] = [s for s in k["order"] if s in slots]
                    tls.append(tl2)
                else:
                    drop(f"{typ} timelines (the source's own meshes / constraints)")
            plant = self._bake_targets(anim, tls)
            self.ir["animations"].append({"name": anim["name"], "timelines": tls})
            self.ir["foot_plant"] += plant
        for why, n in sorted(dropped.items()):
            self.notes.append(f"motion: dropped {n} timeline(s): {why}")

    def _bake_targets(self, anim, tls):
        """Key each planted IK target where the source foot leaves the
        ground; return the planted spans (IR foot_plant)."""
        out = []
        dur = irmod.duration(anim)
        times = np.unique(np.concatenate([np.arange(0, dur, 1.0 / BAKE_FPS), [dur]]))
        sev = lbs.Evaluator(self.src)
        sidx = sev.index
        sroot = self.src["bones"][0]["name"]
        poses = None
        for c in self.ir["ik"]:
            tgt = c["target"]
            if not self.ik_report.get(c["name"], {}).get("planted"):
                continue
            foot = self._source_foot(c)
            if foot is None:
                continue
            if poses is None:
                poses = [sev.world(pose=playback.sample(self.src, anim, float(t))["bones"]) for t in times]
                w0 = sev.world()
            def rel(w):
                return np.array(lbs.to_local(w[sidx[sroot]], *foot(w)))
            p0 = rel(w0)
            off = np.array([rel(w) - p0 for w in poses])
            lift = off[:, 1]
            dx = np.where(lift > 0, off[:, 0] * np.clip(lift / LIFT_FULL, 0.0, 1.0), 0.0)
            dy = np.maximum(lift, 0.0)
            foot_bone = next((n for n in self.order if self.bones[n]["parent"] == tgt and
                              self.bones[n]["kind"] == "bone"), tgt)
            if lift.max() <= LIFT_MIN:
                out.append({"bone": foot_bone, "anim": anim["name"], "spans": [[0.0, float(dur)]]})
                continue
            planted = (dx == 0) & (dy == 0)
            # a planted span must interpolate to exactly 0: keep the keys where it starts and ends
            edges = np.flatnonzero(np.diff(planted.astype(int))) if len(planted) > 1 else np.zeros(0, int)
            must = sorted(set(edges.tolist()) | set((edges + 1).tolist()))
            keep = _simplify(times, np.stack([dx, dy], 1), BAKE_TOL, must)
            keys = [{"time": float(times[i]), "x": float(dx[i]), "y": float(dy[i]), "curve": None} for i in keep]
            tls.append({"type": "bone", "target": tgt, "property": "translate", "keys": keys})
            spans, start = [], None
            for i, ok in enumerate(planted):
                if ok and start is None:
                    start = i
                if (not ok or i == len(planted) - 1) and start is not None:
                    end = i if ok else i - 1
                    if end > start:
                        spans.append([float(times[start]), float(times[end])])
                    start = None
            if spans:
                out.append({"bone": foot_bone, "anim": anim["name"], "spans": spans})
        return out

    def _source_foot(self, c):
        """A function world -> source foot position (the source bone mapped
        to this leg's foot, else the source shin's tip)."""
        sbones = irmod.by_name(self.src["bones"])
        idx = irmod.bone_index(self.src)
        rev = self.jdoc.get("bones", {})
        foot = next((n for n in self.order if self.bones[n]["parent"] == c["target"]), None)
        if foot is not None and foot in rev and rev[foot] in sbones:
            i = idx[rev[foot]]
            return lambda w: (w[i][4], w[i][5])
        shin = rev.get(c["bones"][-1])
        if shin in sbones and sbones[shin]["length"] > 0:
            i, L = idx[shin], sbones[shin]["length"]
            return lambda w: (w[i][4] + w[i][0] * L, w[i][5] + w[i][2] * L)
        return None

    # ---- skeleton box, budget joints

    def fit_skeleton_box(self):
        ev = lbs.Evaluator(self.ir)
        world = ev.world()
        pts = [ev.attachment_vertices(world, n, a) for n, a in ev.shown() if a is not None]
        allv = np.concatenate(pts) if pts else np.zeros((1, 2))
        lo, hi = allv.min(0), allv.max(0)
        sk = self.ir["skeleton"]
        sk.update(x=float(lo[0]), y=float(lo[1]), width=float(hi[0] - lo[0]), height=float(hi[1] - lo[1]),
                  images="./", hash=f"spinegen-{self.name}-{self.mode}",
                  fps=(self.src or {}).get("skeleton", {}).get("fps"))
        self.ir["template"] = self.t["name"]
        self.ir["H"] = self.jdoc.get("H") or float(hi[1] - lo[1])
        if self.src is not None:
            self.ir["source"] = {"format": "spinegen-layers/1", "spine": None,
                                 "warnings": [], "from": self.doc.get("source")}

    def budget_joints(self):
        out = []
        for jname, b in self.bands.items():
            # center = the joint the bone turns about; a one-sided (planted)
            # or asymmetric band also names where it deforms: band_center
            # and extent (half its length) are where G2 holds every
            # triangle to its range; "window" is where G2 sums the area
            # (art_gates WHICH TRIANGLES, _g2_windows)
            up, lo = b["reach"]
            band = {"center": self.cv.to_world(b.get("pivot", b["center"])).tolist(), "upper": b["upper"],
                    "helper": b["helper"], "lower": b["lower"], "halfwidth": b["halfwidth"],
                    "axis": self.cv.dir_to_world(b["axis"]).tolist(), "extent": 0.5 * (up + lo),
                    "window": [float(v) for v in b["window"]]}
            if b.get("planted"):
                band["band_center"] = self.cv.to_world(b["center"]).tolist()
            elif abs(up - lo) > 1e-9:
                mid = b["center"] + 0.5 * (lo - up) * b["halfwidth"] * b["axis"]
                band["band_center"] = self.cv.to_world(mid).tolist()
                band["reach"] = [up, lo]
            out.append({"name": jname, "id": b["budget"], "bone": b["lower"], "dirs": [1, -1], "band": band})
        return {"schema": SCHEMA_BUDGET_JOINTS, "template": self.t["name"], "mode": self.mode,
                "note": "world coordinates (y up); '+' = counter-clockwise local rotation", "joints": out}


# ---------------------------------------------------------------- physics drive (P2 open issue 8)

def physics_tip_motion(skeleton, atlas, anims=None, fps=PHYS_MATCH_FPS):
    """{physics constraint: {anim: px}}: in the official runtime, the
    largest distance between the constrained bone's world tip with physics
    "update" and "none", per animation (all of the skeleton's by default)."""
    from . import oracle
    with open(skeleton) as f:
        doc = json.load(f)
    length = {b["name"]: float(b.get("length", 0.0)) for b in doc.get("bones", [])}
    cons = {c["name"]: c["bone"] for c in doc.get("physics", [])}
    anims = list(doc.get("animations", {})) if anims is None else list(anims)
    out = {n: {} for n in cons}
    for anim in anims:
        runs = [oracle.dump(skeleton, atlas, anim=anim, fps=fps, physics=ph)["frames"] for ph in ("update", "none")]
        for n, b in cons.items():
            def tip(fr):
                m = fr["bones"][b]
                return np.array([m["x"] + m["a"] * length[b], m["y"] + m["c"] * length[b]])
            out[n][anim] = float(max(np.linalg.norm(tip(u) - tip(o)) for u, o in zip(*runs)))
    return out


def match_physics(ir, skeleton, atlas, src_skeleton, src_atlas, fps=PHYS_MATCH_FPS, tol=PHYS_MATCH_TOL):
    """Scale down the mix of each physics constraint that swings its bone
    harder here than in the source, in place on `ir`; returns {constraint:
    {"source_px", "px", "mix", "mix_source", "anims"}} (px: the largest
    physics_tip_motion over the animations both rigs have).

    WHY (P2 open issue 8): once a pant hem's copies carry its weight across
    the knee (ACCESSORY COPIES), its swing shows on the art, and the drive
    is not the source's: the monkey's legs squash with scale keys and the
    thighs never turn (pant tip 2 px of physics swing in cheer), while here
    the leg IK turns the thigh at every bob of the hips (34 px in cheer, 24
    in chestbeat), which folded the thigh in cheer (G1 2). A physics mix
    scales the constraint's output linearly (spine-core: the offset times
    mix x rotate), so mix x source/here brings the largest swing back to
    the source's; the constraint's own spring is left as authored. Never
    raised: a rig that swings less keeps its mix."""
    with open(skeleton) as f:
        here_anims = set(json.load(f).get("animations", {}))
    with open(src_skeleton) as f:
        anims = sorted(here_anims & set(json.load(f).get("animations", {})))
    if not anims:
        return {}
    return scale_physics_mix(ir, physics_tip_motion(skeleton, atlas, anims, fps),
                             physics_tip_motion(src_skeleton, src_atlas, anims, fps), tol)


def write_matched(outdir, res, layers_dir, paths=None):
    """(paths, physics_match report or None): match_physics against the
    source Spine files layers.json names, then write the rig again (write()
    first when `paths` is None). Nothing is matched without the source
    files, physics constraints or the runtime oracle."""
    from . import oracle
    paths = paths or write(outdir, res)
    with open(os.path.join(layers_dir, "layers.json")) as f:
        src = json.load(f).get("source") or {}
    if not (res["ir"].get("physics") and src.get("file") and src.get("atlas") and os.path.exists(src["file"])
            and oracle.available()[0]):
        return paths, None
    pm = match_physics(res["ir"], paths["skeleton"], paths["atlas"], _limit_fixed(src["file"], outdir), src["atlas"])
    res["report"]["physics_match"] = pm
    return write(outdir, res), pm


def _limit_fixed(src_file, outdir):
    """The source skeleton, or a copy of it in outdir with every physics
    limit below PHYS_LIMIT_MIN raised as the rebuild raises it: match_physics
    must compare against the source's swing WITHOUT the throttle, or it
    would scale the repaired follow-through back down to the bug."""
    with open(src_file) as f:
        doc = json.load(f)
    bad = [c for c in doc.get("physics", []) if float(c.get("limit", PHYS_LIMIT_FIX)) < PHYS_LIMIT_MIN]
    if not bad:
        return src_file
    for c in bad:
        c["limit"] = PHYS_LIMIT_FIX
    path = os.path.join(outdir, "source_limit_fixed.json")
    with open(path, "w") as f:
        json.dump(doc, f)
    return path


def scale_physics_mix(ir, here, src, tol=PHYS_MATCH_TOL):
    """match_physics's rule on measured swings ({constraint: {anim: px}},
    physics_tip_motion of this rig and of the source), in place on `ir`."""
    out = {}
    for c in ir.get("physics", []):
        n = c["name"]
        anims = sorted(set(here.get(n, {})) & set(src.get(n, {})))
        if not anims:
            continue
        s_px, n_px = max(src[n][a] for a in anims), max(here[n][a] for a in anims)
        mix0 = float(c["mix"])
        if n_px > (1.0 + tol) * s_px and n_px > 1e-6:
            c["mix"] = round(mix0 * s_px / n_px, 4)
        out[n] = {"source_px": round(s_px, 3), "px": round(n_px, 3), "mix_source": mix0, "mix": c["mix"],
                  "anims": {a: [round(src[n][a], 3), round(here[n][a], 3)] for a in anims}}
    return out


# ---------------------------------------------------------------- crouch (M1)

def foot_contacts(ir, within=2.0):
    """{slot: [vertex index]}: the soles of the planted feet, i.e. the
    vertices of each slot that a leg IK target carries (its bone hangs
    from the target) lying within `within` px of that slot's lowest setup
    vertex."""
    targets = {leg["target"] for leg in irmod.leg_chains(ir)}
    feet = {b["name"] for b in ir["bones"] if b["parent"] in targets} | targets
    ev = lbs.Evaluator(ir)
    world = ev.world()
    out = {}
    for name, att in ev.shown():
        if att is None or att["type"] not in ("mesh", "region"):
            continue
        rows = att.get("weights")
        on_foot = rows and all(any(n in feet for n, *_ in row) for row in rows)
        slot_bone = next(sl["bone"] for sl in ir["slots"] if sl["name"] == name)
        if not (on_foot or (not rows and slot_bone in feet)):
            continue
        v = ev.attachment_vertices(world, name, att)
        out[name] = [int(i) for i in np.flatnonzero(v[:, 1] <= v[:, 1].min() + within)]
    return out


def planted_vertices(ir):
    """{slot: [vertex index]}: the WHOLE planted foot, every vertex of a
    slot a leg IK target carries (as foot_contacts) that lies at or below
    its ankle (the target's setup origin). M1 is defined on the sole, but a
    foot is planted as a whole: a boot whose upper half rides on the shin
    deforms while its two sole vertices stay put (P2 review)."""
    ev = lbs.Evaluator(ir)
    world = ev.world()
    ankle = {}
    targets = {leg["target"] for leg in irmod.leg_chains(ir)}
    for b in ir["bones"]:
        if b["parent"] in targets:
            ankle[b["name"]] = world[ev.index[b["parent"]]][5]
    for t in targets:
        ankle[t] = world[ev.index[t]][5]
    out = {}
    for name in foot_contacts(ir):
        att = dict(ev.shown())[name]
        rows = att.get("weights")
        bones = {n for row in rows for n, *_ in row} if rows else \
            {next(sl["bone"] for sl in ir["slots"] if sl["name"] == name)}
        y = max(ankle[b] for b in bones if b in ankle)
        v = ev.attachment_vertices(world, name, att)
        out[name] = [int(i) for i in np.flatnonzero(v[:, 1] <= y + 1e-6)]
    return out


def shin_bottoms(ir, rows=SHIN_ROWS):
    """{slot: [vertex index]}: the bottom `rows` px of every slot on a leg
    IK's lower bone (the calf's hem over the boot): what a crouch should
    not drag across the foot.

    By the SLOT's bone, not the heaviest influence: once the hem below the
    ankle is 100% foot (the planted ankle band), a short calf can carry
    more foot weight than shin weight in total and would drop out of the
    measurement exactly when it matters (the monkey's right calf: 23.5
    foot, 20.5 shin)."""
    shins = {leg["shin"] for leg in irmod.leg_chains(ir)}
    slot_bone = {sl["name"]: sl["bone"] for sl in ir["slots"]}
    ev = lbs.Evaluator(ir)
    world = ev.world()
    out = {}
    for name, att in ev.shown():
        if att is None or att["type"] not in ("mesh", "region") or slot_bone.get(name) not in shins:
            continue
        v = ev.attachment_vertices(world, name, att)
        out[name] = [int(i) for i in np.flatnonzero(v[:, 1] <= v[:, 1].min() + rows)]
    return out


def crouch(ir, depth, skeleton=None, atlas=None, within=2.0, knee_dirs=None):
    """Lower the hips (the parent of the IK thighs) by `depth` world units
    and measure what the feet and knees do. With `skeleton` and `atlas` the
    official runtime measures it (spinegen.oracle.dump, pose added to
    setup); otherwise spinegen.lbs.

    knee_dirs  {ik name: world (x, y)}: the way each knee must move (the
               build report's ik.<name>.knee_dir, from the template's knee
               rule); default: away from the body's midline (the hips
               bone's x), the front-view "outward" rule.

    Returns {"depth", "by", "hips_drop",
      "max_slide", "slots", "contact_vertices"  the soles (foot_contacts;
                   SOTA §5 M1: <= 1 px),
      "foot_slide", "foot_slots", "foot_vertices"  the whole planted foot
                   below the ankle (planted_vertices), same limit,
      "shin_bottom_slide", "shin_bottom_slots"  the bottom SHIN_ROWS px of
                   the shins' art (shin_bottoms; reported: the hem the
                   reviewer saw drag 24 / 13 px over the monkey's boots),
      "knees": {ik: {"dx", "dy", "dir", "along", "end_px", "bend_deg",
                   "setup_bend_deg"}}  the knee's world move, its component
                   along the expected direction (+ = the right way), how
                   far the knee ends off the hip-ankle line on that side,
                   and the leg's bend (angle between thigh and shin) after
                   the crouch and at setup,
      "knee_bend_diff_deg"  |bend_deg| difference of two legs: mirror
                   symmetry is read on the END pose, because a leg already
                   bent at setup moves its knee less for the same drop,
      "knees_ok"   every knee moved along its direction and ends on that
                   side, and two legs' bends agree within KNEE_SYM_DEG}."""
    bones = irmod.by_name(ir["bones"])
    legs = irmod.leg_chains(ir)
    hips = {bones[leg["thigh"]]["parent"] for leg in legs}
    if len(hips) != 1:
        raise ValueError(f"cannot tell the hips bone from the leg IK chains ({sorted(hips)})")
    hips = hips.pop()
    pose = {hips: {"y": -float(depth)}}
    groups = {"sole": foot_contacts(ir, within), "foot": planted_vertices(ir), "shin": shin_bottoms(ir)}
    slots = sorted({n for g in groups.values() for n in g})
    knees = [n for leg in legs for n in (leg["thigh"], leg["shin"], leg["target"])]
    if skeleton is not None:
        from . import oracle
        d0 = oracle.dump(skeleton, atlas)["frames"][0]
        d1 = oracle.dump(skeleton, atlas, pose=pose, pose_mode="add")["frames"][0]
        verts = [{n: np.asarray(d["slots"][n]["vertices"], float).reshape(-1, 2) for n in slots} for d in (d0, d1)]
        drop = d0["bones"][hips]["y"] - d1["bones"][hips]["y"]
        pos = [{n: np.array([d["bones"][n]["x"], d["bones"][n]["y"]]) for n in knees + [hips]} for d in (d0, d1)]
        by = "spine-core (qa dump)"
    else:
        ev = lbs.Evaluator(ir)
        idx = ev.index
        w0 = ev.world()
        w1 = ev.world(pose={hips: {"y": bones[hips]["y"] - float(depth)}})
        shown = dict(ev.shown())
        verts = [{n: ev.attachment_vertices(w, n, shown[n]) for n in slots} for w in (w0, w1)]
        drop = w0[idx[hips]][5] - w1[idx[hips]][5]
        pos = [{n: np.array(w[idx[n]][4:6]) for n in knees + [hips]} for w in (w0, w1)]
        by = "spinegen.lbs"

    def slide(group):
        return {n: float(np.linalg.norm(verts[1][n][i] - verts[0][n][i], axis=1).max()) if i else 0.0
                for n, i in groups[group].items()}

    out = {"depth": float(depth), "by": by, "hips_drop": float(drop)}
    for key, group in (("", "sole"), ("foot_", "foot"), ("shin_bottom_", "shin")):
        sl = slide(group)
        out[f"{key}slots" if key else "slots"] = sl
        out[f"{key}slide" if key else "max_slide"] = max(sl.values()) if sl else None
    out["contact_vertices"] = {n: len(i) for n, i in groups["sole"].items()}
    out["foot_vertices"] = {n: len(i) for n, i in groups["foot"].items()}
    out["knees"] = {}
    for leg in legs:
        t, k, a = leg["thigh"], leg["shin"], leg["target"]
        move = pos[1][k] - pos[0][k]
        if leg["style"] == "forward":
            # the knee bends toward the camera: the leg shortens along its
            # own line, the knee moves little sideways (LEG STYLES)
            l0 = [float(np.linalg.norm(pos[0][k] - pos[0][t])), float(np.linalg.norm(pos[0][a] - pos[0][k]))]
            l1 = [float(np.linalg.norm(pos[1][k] - pos[1][t])), float(np.linalg.norm(pos[1][a] - pos[1][k]))]
            out["knees"][leg["name"]] = {
                "style": "forward", "dx": float(move[0]), "dy": float(move[1]), "dir": None,
                "side_px": float(abs(move[0])), "thigh_scale": l1[0] / l0[0] if l0[0] else None,
                "shin_scale": l1[1] / l0[1] if l0[1] else None,
                "bend_deg": _leg_bend(pos[1][t], pos[1][k], pos[1][a]),
                "setup_bend_deg": _leg_bend(pos[0][t], pos[0][k], pos[0][a])}
            continue
        want = (knee_dirs or {}).get(leg["name"])
        want = np.asarray(want, float) if want is not None else \
            np.array([math.copysign(1.0, pos[0][t][0] - pos[0][hips][0]), 0.0])
        want = want / (np.linalg.norm(want) or 1.0)
        n = _knee_normal(pos[1][t], pos[1][a], want)
        out["knees"][leg["name"]] = {"dx": float(move[0]), "dy": float(move[1]), "dir": want.tolist(),
                                   "along": float(move @ want), "end_px": float((pos[1][k] - pos[1][t]) @ n),
                                   "bend_deg": _leg_bend(pos[1][t], pos[1][k], pos[1][a]),
                                   "setup_bend_deg": _leg_bend(pos[0][t], pos[0][k], pos[0][a])}
    kn = list(out["knees"].values())
    out["knee_bend_diff_deg"] = abs(kn[0]["bend_deg"] - kn[1]["bend_deg"]) if len(kn) == 2 else None
    side_max = max(FORWARD_SIDE_PX, FORWARD_SIDE_MAX * abs(out["hips_drop"]))
    out["knees_ok"] = bool(kn) and all(
        (x["side_px"] <= side_max and x["bend_deg"] <= FORWARD_BEND_MAX and x["thigh_scale"] < 1.0)
        if x.get("style") == "forward" else (x["along"] > 0 and x["end_px"] > 0) for x in kn) and \
        (out["knee_bend_diff_deg"] is None or out["knee_bend_diff_deg"] <= KNEE_SYM_DEG)
    return out


def _leg_bend(hip, knee, ankle):
    """Unsigned angle (deg) between the thigh (hip -> knee) and the shin
    (knee -> ankle): 0 for a straight leg."""
    a, b = np.asarray(knee) - np.asarray(hip), np.asarray(ankle) - np.asarray(knee)
    return float(abs(math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a @ b))))


# ---------------------------------------------------------------- helpers

def _mesh_band(b):
    return {"center": tuple(b["center"]), "axis": tuple(b["axis"]), "halfwidth": b["halfwidth"],
            "reach": tuple(b["reach"]), "rows": b.get("rows", mesh.BAND_ROWS), "row_points": b.get("row_points", 0)}


def _weight_joint(b):
    return {"center": tuple(b["center"]), "upper": b["upper"], "helper": b["helper"], "lower": b["lower"],
            "halfwidth": b["halfwidth"], "axis": tuple(b["axis"]), "reach": tuple(b["reach"]),
            "lateral": b.get("lateral", weights.LATERAL), "exclusive": True, "overlap": bool(b.get("overlap")),
            "compose": b.get("compose", "upper"), "power": b.get("power", 1.0)}


def _chained(a, b):
    """Is one of two bands an "overlap" band hanging below the other on the
    same chain (its upper bone is the other's lower bone)?"""
    return (b.get("overlap") and b["upper"] == a["lower"]) or (a.get("overlap") and a["upper"] == b["lower"])


def _knee_normal(hip, ankle, want):
    """Unit normal of the hip -> ankle line on the `want` side (world)."""
    d = np.asarray(ankle, float) - np.asarray(hip, float)
    d = d / (np.linalg.norm(d) or 1.0)
    n = np.array([-d[1], d[0]])
    return n if n @ want >= 0 else -n


def _round_band(b):
    return {"center": [round(float(v), 3) for v in b["center"]], "axis": [round(float(v), 5) for v in b["axis"]],
            "pivot": [round(float(v), 3) for v in b.get("pivot", b["center"])], "planted": bool(b.get("planted")),
            "halfwidth": round(b["halfwidth"], 3), "extent": round(b["extent"], 4),
            "reach": [round(float(v), 4) for v in b["reach"]],
            "window": [round(float(v), 4) for v in b.get("window", b["reach"])],
            "lateral": round(b.get("lateral", weights.LATERAL), 3), "upper": b["upper"],
            "lower": b["lower"], "helper": b["helper"], "mode": b["mode"], "layers": b["layers"],
            "power": float(b.get("power", 1.0))}


def _totals(stats):
    v = sum(s["vertices"] for s in stats)
    g = sum(s["grid_vertices"] for s in stats)
    b2 = [s["b2"] for s in stats if s.get("b2") and s["b2"]["count"]]
    inside = sum(x["count"] for x in b2)
    multi = sum(x["count"] * x["value"] for x in b2)
    return {"vertices": v, "grid_vertices": g, "vertex_ratio": round(v / g, 4) if g else None,
            "b2": round(multi / inside, 4) if inside else None, "b2_band_vertices": inside}


def _snap(deg):
    """deg mod 180 -> 0 (same axes), 90 (swapped) or None (neither)."""
    r = deg % 180.0
    if min(r, 180.0 - r) <= AXIS_SNAP:
        return 0
    if abs(r - 90.0) <= AXIS_SNAP:
        return 90
    return None


def _carry_bone_timeline(tl, new, d_parent, d_own, notes):
    """One source bone timeline onto bone `new`: rotate as is; translate
    turned by d_parent (source parent world rotation - new parent's);
    scale/shear axes swapped when the bone itself turned by ~90 deg."""
    tl2 = json.loads(json.dumps(tl))
    tl2["target"] = new
    prop = tl["property"]
    if prop in ("translate", "translatex", "translatey"):
        c, s = _cos_sin(d_parent)
        if (c, s) == (1.0, 0.0):
            return tl2
        keys = []
        for k in tl2["keys"]:
            # both channels as (value, [cx1, cy1, cx2, cy2]); a missing channel is a constant 0
            if prop == "translate":
                vx, vy = k["x"], k["y"]
            else:
                vx, vy = (k["value"], 0.0) if prop == "translatex" else (0.0, k["value"])
            cv = k.get("curve")
            nk = {"time": k["time"], "x": c * vx - s * vy, "y": s * vx + c * vy, "curve": cv}
            if isinstance(cv, list):
                if prop == "translate":
                    qx, qy = cv[0:4], cv[4:8]
                    if abs(qx[0] - qy[0]) > 1e-9 or abs(qx[2] - qy[2]) > 1e-9:
                        notes.append(f"bone {new} translate: the channels' curves differ in timing; "
                                     f"turned approximately")
                else:
                    q = cv[0:4]
                    zero = [q[0], 0.0, q[2], 0.0]
                    qx, qy = (q, zero) if prop == "translatex" else (zero, q)
                nk["curve"] = [qx[0], c * qx[1] - s * qy[1], qx[2], c * qx[3] - s * qy[3],
                               qy[0], s * qx[1] + c * qy[1], qy[2], s * qx[3] + c * qy[3]]
            keys.append(nk)
        if prop != "translate":
            notes.append(f"bone {new} {prop}: written as translate (turned into the new parent's frame)")
        tl2.update(property="translate", keys=keys)
    elif prop in ("scale", "scalex", "scaley", "shear", "shearx", "sheary"):
        snap = _snap(d_own)
        if snap is None:
            notes.append(f"bone {new} {prop}: the bone turned {d_own:.1f} deg from its source; axes kept "
                         f"(approximate)")
        elif snap == 90:
            if prop in ("scale", "shear"):
                for k in tl2["keys"]:
                    k["x"], k["y"] = k["y"], k["x"]
                    if isinstance(k.get("curve"), list):
                        k["curve"] = k["curve"][4:8] + k["curve"][0:4]
            else:
                tl2["property"] = {"scalex": "scaley", "scaley": "scalex", "shearx": "sheary",
                                   "sheary": "shearx"}[prop]
    return tl2


def _cos_sin(deg):
    """cos, sin of an angle, exact at multiples of 90 deg."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return tuple(float(round(v)) if abs(v - round(v)) < 1e-9 else v for v in (c, s))


def _simplify(times, vals, tol, must=()):
    """Indices of keys to keep so that linear interpolation between kept
    keys stays within `tol` of every sample (greedy; the endpoints and the
    indices in `must` are always kept, and no segment skips over one)."""
    n = len(times)
    must = set(int(i) for i in must)
    keep = [0]
    i = 0
    while i < n - 1:
        j = i + 1
        while j + 1 < n and j not in must:
            t = (times[i + 1:j + 2] - times[i]) / (times[j + 1] - times[i])
            interp = vals[i] + (vals[j + 1] - vals[i]) * t[:, None]
            if np.abs(interp - vals[i + 1:j + 2]).max() > tol:
                break
            j += 1
        keep.append(j)
        i = j
    return keep


def _load_source(layers_dir, doc):
    rel = (doc.get("source") or {}).get("rig")
    if not rel:
        return None
    p = os.path.join(layers_dir, rel)
    if not os.path.exists(p):
        return None
    motion = os.path.join(os.path.dirname(p), "motion.json")
    return irmod.load(p, motion if os.path.exists(motion) else None)


def _dump(path, obj):
    def default(o):
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, np.bool_):
            return bool(o)
        raise TypeError(type(o))
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, default=default)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("layers_dir")
    ap.add_argument("--template", default="biped_mascot")
    ap.add_argument("--mode", choices=MODES, default="joint")
    ap.add_argument("--joints", default=None, help="joints.json (default: <layers dir>/joints.json)")
    ap.add_argument("--name", default=None, help="output basename (default: the source rig's name)")
    ap.add_argument("--no-source", action="store_true", help="ignore the source rig (no physics / animations)")
    ap.add_argument("--crouch", type=float, default=0.05,
                    help="crouch check with the hips lowered by this fraction of H (0: skip): both knees move "
                         "by the template's knee rule and the planted feet stay within 1 px (M1), else exit "
                         "non-zero; uses the runtime oracle when it is installed")
    ap.add_argument("--mesh", choices=sorted(MESH_PROFILES), default="lean", help="mesh profile (MESH_PROFILES)")
    ap.add_argument("--joint-modes", default=None,
                    help="JSON {joint: rigid|ramp|helper}, or a RIGID budget.json to choose them from (with --need)")
    ap.add_argument("--need", type=float, default=90.0,
                    help="degrees a joint must hold (both ways) to stay rigid when choosing from a budget")
    ap.add_argument("--keep-joints", action="store_true",
                    help="keep elbows and knees where the joints file puts them (no recentering)")
    ap.add_argument("--bands", default=None,
                    help="JSON {joint: {reach, rows, row_points, overlap, compose}}: per-joint band shape over the "
                         "template's (e.g. a band_fit.json written by --fit-reach)")
    ap.add_argument("--fit-reach", default=None, metavar="JOINTS=VALUES",
                    help="fit each joint's upper reach by the budget (fit_reach), e.g. "
                         "'elbow_l,elbow_r=2,2.25,2.5,3,3.5'; the chosen reaches go into the build and "
                         "<out>/band_fit.json")
    ap.add_argument("--fit-need", type=float, default=90.0, help="degrees --fit-reach tries to hold both ways")
    ap.add_argument("--fit-crouch", default=None, metavar="SPEC",
                    help="fit each leg's band shapes by the crouch grid's folds (fit_crouch), e.g. "
                         "'knee_{s}.lower=1.5,3;ankle_{s}.power=0.8,0.9,1,1.1,1.2'; the choice goes into the "
                         "build and <out>/band_fit.json")
    ap.add_argument("--no-physics-match", action="store_true",
                    help="keep every physics mix as the source has it (default: match_physics scales down "
                         "a constraint that swings harder than in the source)")
    ap.add_argument("--no-accessory-copies", action="store_true",
                    help="accessories share their parent's weight only (P2 R3; default: ACCESSORY COPIES)")
    ap.add_argument("--leg-style", choices=LEG_STYLES, default=None,
                    help="how the knees bend (LEG STYLES): outward = the mascot's two-bone IK, knees out; "
                         "forward = toward the camera, the leg foreshortens; auto (the template default) picks "
                         "by the legs' share of the height")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    modes = None
    if a.joint_modes:
        with open(a.joint_modes) as f:
            jm = json.load(f)
        if jm.get("schema") == "spinegen-budget/1":
            modes, why = choose_joint_modes(jm, a.need, a.template)
            for j in sorted(modes):
                print(f"  joint {j}: {modes[j]} ({why[j]})")
        else:
            modes = jm
    bands = None
    if a.bands:
        with open(a.bands) as f:
            bands = json.load(f)
        bands = bands.get("bands", bands)
    fit = None
    if a.fit_reach:
        names, vals = a.fit_reach.split("=", 1)
        cands = {j.strip(): [float(v) for v in vals.split(",")] for j in names.split(",")}
        fitted, fit = fit_reach(a.layers_dir, a.joints, a.template, cands, a.fit_need,
                                progress=lambda m: print("  " + m), mode=a.mode,
                                source=None if a.no_source else "auto", name=a.name, joint_modes=modes,
                                mesh_profile=a.mesh, recenter=not a.keep_joints, leg_style=a.leg_style)
        bands = dict(bands or {})
        for j, spec in fitted.items():
            bands[j] = dict(bands.get(j, {}), **spec)
        for j, v in fit["chosen"].items():
            print(f"  fit {j}: upper reach {v:g} halfwidths")
    pfit = None
    if a.fit_crouch:
        cands = {}
        for part in a.fit_crouch.split(";"):
            name, vals = part.split("=", 1)
            jn, key = name.strip().rsplit(".", 1)
            cands.setdefault(jn, {})[key] = [float(v) for v in vals.split(",")]
        fitted, pfit = fit_crouch(a.layers_dir, a.joints, a.template, cands, progress=lambda m: print("  " + m),
                                  bands=bands, mode=a.mode, source=None if a.no_source else "auto", name=a.name,
                                  joint_modes=modes, mesh_profile=a.mesh, recenter=not a.keep_joints, leg_style=a.leg_style)
        bands = json.loads(json.dumps(bands or {}))
        for j, spec in fitted.items():
            e = bands.setdefault(j, {})
            for k, v in spec.items():
                e[k] = dict(e.get(k) or {}, **v) if k == "reach" else v
        for side, key in pfit["chosen"].items():
            print(f"  fit crouch, leg {side}: {key}")
    res = build(a.layers_dir, a.joints, a.template, a.mode, None if a.no_source else "auto", a.name, modes, a.mesh,
                not a.keep_joints, bands, accessory_copies=not a.no_accessory_copies, leg_style=a.leg_style)
    ls = res["report"]["leg_style"]
    print(f"  leg style: {ls['style']} (asked {ls['asked']}" + (f"; {ls['reason']}" if ls.get("reason") else "") + ")")
    if pfit is not None:
        res["report"]["fit_crouch"] = pfit
    if fit is not None:
        res["report"]["fit_reach"] = fit
    paths = write(a.out, res)
    if not a.no_physics_match and not a.no_source:
        paths, pm = write_matched(a.out, res, a.layers_dir, paths)
        for n, r in (pm or {}).items():
            if r["mix"] != r["mix_source"]:
                print(f"  physics {n}: swings {r['px']:.1f} px here, {r['source_px']:.1f} px in the source: "
                      f"mix {r['mix_source']:g} -> {r['mix']:g}")
    if fit is not None or pfit is not None:
        _dump(os.path.join(a.out, "band_fit.json"), {"bands": bands, "fit": fit, "fit_crouch": pfit})
    tot = res["report"]["totals"]
    ir = res["ir"]
    print(f"{paths['skeleton']}: {len(ir['bones'])} bones, {len(ir['slots'])} slots, {len(ir['ik'])} ik, "
          f"{len(ir['transform'])} transform, {len(ir['physics'])} physics, {len(ir['animations'])} animations; "
          f"{tot['vertices']} vertices ({tot['vertex_ratio']} of the 22 px grid), B2 {tot['b2']}")
    for n in res["report"]["notes"]:
        print("  note:", n)
    acc = res["report"]["accessories"]
    for b, r in acc["bones"].items():
        if r["physics"]:
            print(f"  accessory {b} (physics): {r['source_vertices']} -> {r['vertices']} vertices, weight "
                  f"{r['source_mass']:g} -> {r['mass']:g}")
    if not acc["ok"]:
        raise SystemExit(f"accessory check failed: physics bones {', '.join(acc['lost'])} move no vertex "
                         f"(rig written to {a.out} for inspection)")
    if a.crouch > 0 and ir["ik"]:
        from . import oracle
        ok, _ = oracle.available()
        dirs = {n: r["knee_dir"] for n, r in res["report"]["ik"].items() if r.get("knee_dir") is not None}
        c = crouch(ir, a.crouch * ir["H"], *((paths["skeleton"], paths["atlas"]) if ok else (None, None)),
                   knee_dirs=dirs)
        res["report"]["crouch"] = c
        _dump(paths["report"], dict(json.load(open(paths["report"])), crouch=c))

        def px(v, f=".4f"):
            return "n/a" if v is None else format(v, f)
        print(f"  crouch {a.crouch:g} H = {c['depth']:.1f} px ({c['by']}): soles slide {px(c['max_slide'])} px "
              f"(M1 <= 1 px, {sum(c['contact_vertices'].values())} sole vertices); whole foot below the ankle "
              f"{px(c['foot_slide'])} px ({sum(c['foot_vertices'].values())} vertices); shin bottoms "
              f"{px(c['shin_bottom_slide'], '.2f')} px")
        for n, k in c["knees"].items():
            if k.get("style") == "forward":
                print(f"  {n}: knee moves {k['dx']:+.1f}, {k['dy']:+.1f} px (forward: {k['side_px']:.1f} px sideways); "
                      f"thigh x{k['thigh_scale']:.3f}, shin x{k['shin_scale']:.3f}, bent {k['setup_bend_deg']:.1f} -> "
                      f"{k['bend_deg']:.1f} deg")
                continue
            print(f"  {n}: knee moves {k['dx']:+.1f}, {k['dy']:+.1f} px ({k['along']:+.1f} px the expected way); "
                  f"ends {k['end_px']:+.1f} px off the hip-ankle line, bent {k['setup_bend_deg']:.1f} -> "
                  f"{k['bend_deg']:.1f} deg")
        print(f"  knees {'ok' if c['knees_ok'] else 'WRONG'} (end bends differ by "
              f"{px(c['knee_bend_diff_deg'], '.1f')} deg, <= {KNEE_SYM_DEG:g})")
        # fail by name (SOTA §3.1): a rig whose knees cave in or whose feet
        # skate is not a rig to animate, whatever else it passes
        bad = [k for k, v in (("knees", not c["knees_ok"]),
                              ("M1 soles", (c["max_slide"] or 0.0) > M1_PX),
                              ("M1 whole foot", (c["foot_slide"] or 0.0) > M1_PX)) if v]
        if bad:
            raise SystemExit(f"crouch check failed: {', '.join(bad)} (rig written to {a.out} for inspection)")


if __name__ == "__main__":
    main()
