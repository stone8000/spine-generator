"""Layers + joints + a template -> an IR rig that bends at its joints.

    python -m spinegen.rigbuild <layers dir> --template biped_mascot --mode joint|rigid -o <outdir>
        [--joints joints.json] [--name monkey] [--no-source] [--mesh lean|refined]
        [--joint-modes modes.json | --joint-modes rigid_budget.json --need 90]

writes rig.json + motion.json (IR), <name>.json (Spine 4.2, spinegen.emit.spine42),
<name>.atlas + <name>.png (spinegen.atlas.pack), joints.json (the budget scanner's
joints, world coordinates) and build_report.json; then a crouch check in the
official runtime (crouch(): knees by the template's rule, planted feet <= 1 px).

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
           the report gives the cap).
  meshes   every layer through mesh.build. In "joint" mode a layer gets a
           band at each joint it crosses (its art comes within one
           halfwidth of the joint) where the template says it bends:
           "both" sides at elbows, knees, wrists, ankles; only the "lower"
           side at shoulders and hips. halfwidth = the largest inscribed
           radius of the lower limb's art near the joint (the same rule as
           art_gates.estimate_halfwidth); band half-length = mesh.BAND_EXTENT
           halfwidths each way, or the template joint's "reach" {upper,
           lower} (an asymmetric band: the elbow runs 3.5 halfwidths up the
           sleeve and 2 down the forearm), shortened so neighbouring bands
           on one bone never overlap (half the gap each, unless one needs
           less) and so no band runs past a rigid joint down its lower bone
           (_stop_at_rigid_joints: the elbow stops at the wrist, the hand
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
           carried over by interpolating the source mesh, faded out over the
           inner half of every band so an accessory cannot hold a joint
           open. "rigid": every vertex 100% on the layer's bone, the cut-out
           for comparison.
  physics  the source's constraints, renamed, every field explicit (the IR
           already is), ordered after the transforms.
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
# over every animation in spine-core, elbows 98-110 deg on G2's band sum,
# 58-78 deg with its per-triangle range (P2_REPORT, mesh.py THE LEAN OPTIONS).
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
AXIS_MEAN_MAX = 45.0  # deg: a "both" band runs along the mean of its bones only when they are this close
ACC_FADE = (0.5, 2.0) # accessory weight: 0 inside 0.5 band half-lengths of a joint, full beyond 2.0
KNEE_RULES = ("outward", "facing")  # template ik "knee" (see _knee_dir)
KNEE_TEST = 0.1       # the bend test lowers the hips by this fraction of the leg's length
KNEE_TOL = 0.5        # px: a setup knee this close to the hip-ankle line counts as straight
SETUP_TOL = 1e-3      # px: the chosen bend must reproduce the setup leg this closely
KNEE_SYM_DEG = 10.0   # crouch(): two legs' end bends agree this closely (mirror symmetry)
SHIN_ROWS = 10.0      # px: crouch() reports the slide of the bottom this many px of the shin's art
M1_PX = 1.0           # px: SOTA §5 M1, the most a planted foot vertex may move (the CLI's crouch check)
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

def build(layers_dir, joints=None, template="biped_mascot", mode="joint", source="auto", name=None,
          joint_modes=None, mesh_profile="lean", recenter=True):
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
    if source == "auto":
        source = _load_source(layers_dir, doc)
    b = _Builder(doc, imgs, joints, t, mode, source, name)
    b.joint_modes = dict(joint_modes or {})
    b.mesh_kw = dict(MESH_PROFILES[mesh_profile])
    b.mesh_profile = mesh_profile
    b.recenter = recenter
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
        self.name = name or (src["name"] if src else os.path.basename(os.path.normpath(doc.get("name", "rig"))))
        self.notes = []
        self.spec = {b["name"]: b for b in t["bones"]}
        self.joint_modes = {}  # joint -> "rigid" | "ramp" | "helper" (build() sets it)
        self.recenter = True     # elbows and knees onto the art's centre line (_recenter; build() sets it)
        self.mesh_kw, self.mesh_profile = dict(MESH_PROFILES["lean"]), "lean"
        self.bones = {}      # name -> {"parent", "head", "tail" (canvas), "len", "role", "limb_parent"}
        self.order = []      # bone names, parents first
        self._art = {}
        self.straightened = {}  # knee joint -> px moved onto the hip-ankle line (_align_knees)

    # ---- orchestration

    def run(self):
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
                  "bands": {j: _round_band(b) for j, b in self.bands.items()},
                  "totals": _totals(stats), "notes": self.notes}
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
                j = self.bones[shin]["joint"]
                self.jxy[j] = self.jxy[j] + self.cv.dir_to_world(-off * n)  # the y flip is its own inverse
                out[j] = -off
                self.notes.append(f"joint {j}: moved {-off:.1f} px onto the hip-ankle line (the setup leg was "
                                  f"kinked against the template's knee rule {ik['knee']!r})")
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
            if kind not in JOINT_MODES:
                raise ValueError(f"joint {jname}: mode must be one of {JOINT_MODES}, not {kind!r}")
            if kind == "helper" and helper is None:
                self.notes.append(f"joint {jname}: no helper in the template; ramp without one")
                kind = "ramp"
            if kind != "helper":
                helper = None
            want = spec.get("reach") or {}
            reach = [float(want.get("upper", mesh.BAND_EXTENT)), float(want.get("lower", mesh.BAND_EXTENT))]
            bands[jname] = {"center": c, "axis": axis, "halfwidth": hw, "reach": reach,
                            "upper": upper, "lower": lower, "helper": helper, "mode": spec["band"],
                            "kind": kind, "budget": spec.get("budget"),
                            "rows": spec.get("rows", mesh.BAND_ROWS), "row_points": int(spec.get("row_points", 0))}
        targets = {n for n in self.order if self.bones[n]["kind"] == "ik_target"}
        for jname, band in bands.items():
            band["layers"] = [L["name"] for L in self.layers if self._carries(L, band)]
            band["pivot"] = band["center"]
            band["planted"] = self.bones[band["lower"]]["parent"] in targets
        self._stop_at_rigid_joints(bands)
        # two bands on one layer must not overlap: they share the distance
        # between them, half each, except that a band reaching less than its
        # half leaves the rest to the other (a short shoulder band lets a long
        # elbow band run up the sleeve; two default bands still meet halfway)
        new = {j: list(b["reach"]) for j, b in bands.items()}
        for a, ba in bands.items():
            for b, bb in bands.items():
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

    def _stop_at_rigid_joints(self, bands):
        """A band's lower half ends at the next joint down its lower bone
        when the template keeps that joint rigid (band "none"): the elbow's
        band stops at the wrist.

        WHY: a rigid joint means the part beyond it moves as one piece with
        its bone (the template's wrists: a mascot's hand image carries the
        forearm, so a wrist band would steal the elbow's length). A band
        running past it bends that part with the elbow: the monkey's fist
        curled with a 3-halfwidth elbow band (P2 F3). On the monkey the
        default 2-halfwidth band already ends 2-6 px short of the wrists."""
        for jname, band in bands.items():
            for tj in self.spec[band["lower"]]["tails"]:
                if tj not in self.jxy or self.t["joints"].get(tj, {}).get("band", "none") != "none":
                    continue
                d = float((self.jxy[tj] - band["center"]) @ band["axis"]) / band["halfwidth"]
                if 0 < d < band["reach"][1]:
                    band["reach"][1] = d
                    self.notes.append(f"joint {jname}: band stops at the rigid joint {tj}, "
                                      f"{d:.2f} halfwidths down {band['lower']}")
                break

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
        for ik in self.t["ik"]:
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
                                          "straightened_px": round(self.straightened.get(self.bones[shin]["joint"],
                                                                                         0.0), 3),
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
            self.ir["physics"].append(c)
            order += 1

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
            for side, keep, drop in ((s > lo, b["lower"], (b["upper"], b["helper"])),
                                     (s < -up, b["upper"], (b["lower"], b["helper"]))):
                rows = side & near
                for n in drop:
                    if n:
                        M[rows, col[keep]] += M[rows, col[n]]
                        M[rows, col[n]] = 0.0
        return weights.prune_weights(M, names)

    def _carry_accessories(self, L, m, bands, w):
        """Source weights of accessory bones (a cuff on a sleeve hem),
        interpolated onto the new mesh and faded out across the inner half
        of every band of this layer (module doc)."""
        accs = [b for b in L.get("src", {}).get("influences", [])
                if b in self.bones and self.bones[b]["kind"] == "accessory"]
        if not accs or self.src is None:
            return w
        att = irmod.resolve_attachment(self.src, None, L["slot"], L["attachment"])
        if att is None or att["type"] != "mesh" or not att.get("weights"):
            return w
        from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
        sv = np.asarray(att["vertices"], float).reshape(-1, 2)
        nv = self.cv.to_world(m["verts"])
        fade = np.ones(len(nv))
        bones = {n: {"name": n, "head": tuple(self.bones[n]["head"]), "tail": tuple(self.bones[n]["tail"])}
                 for b in bands for n in (b["upper"], b["lower"], b["helper"]) if n}
        for b in bands:
            j = _weight_joint(b)
            s, lat = weights.band_coords(m["verts"], j, list(bones.values()))
            ext = np.where(s < 0, b["reach"][0], b["reach"][1]) * b["halfwidth"]  # this side's half
            f = np.clip((np.abs(s) - ACC_FADE[0] * ext) / ((ACC_FADE[1] - ACC_FADE[0]) * ext), 0.0, 1.0)
            f[lat > b["lateral"] * b["halfwidth"]] = 1.0
            fade = np.minimum(fade, f)
        add = np.zeros((len(nv), len(accs)))
        for k, a in enumerate(accs):
            vals = np.array([sum(inf[1] for inf in row if inf[0] == a) for row in att["weights"]])
            lin = LinearNDInterpolator(sv, vals)(nv)
            near = NearestNDInterpolator(sv, vals)(nv)
            add[:, k] = np.where(np.isnan(lin), near, lin) * fade
        out = []
        names = list(dict.fromkeys([n for row in w for n, _ in row] + accs))
        col = {n: i for i, n in enumerate(names)}
        M = np.zeros((len(w), len(names)))
        for v, row in enumerate(w):
            for n, x in row:
                M[v, col[n]] += x
        tot = np.clip(add.sum(1, keepdims=True), 0.0, 1.0)
        M *= 1.0 - tot
        for k, a in enumerate(accs):
            M[:, col[a]] += add[:, k]
        out = weights.prune_weights(M, names)
        return out

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
        shin = rev.get(c["bones"][1])
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
            # and extent (half its length) are the window G2 reads
            # (art_gates.band_triangles)
            up, lo = b["reach"]
            band = {"center": self.cv.to_world(b.get("pivot", b["center"])).tolist(), "upper": b["upper"],
                    "helper": b["helper"], "lower": b["lower"], "halfwidth": b["halfwidth"],
                    "axis": self.cv.dir_to_world(b["axis"]).tolist(), "extent": 0.5 * (up + lo)}
            if b.get("planted"):
                band["band_center"] = self.cv.to_world(b["center"]).tolist()
            elif abs(up - lo) > 1e-9:
                mid = b["center"] + 0.5 * (lo - up) * b["halfwidth"] * b["axis"]
                band["band_center"] = self.cv.to_world(mid).tolist()
                band["reach"] = [up, lo]
            out.append({"name": jname, "id": b["budget"], "bone": b["lower"], "dirs": [1, -1], "band": band})
        return {"schema": SCHEMA_BUDGET_JOINTS, "template": self.t["name"], "mode": self.mode,
                "note": "world coordinates (y up); '+' = counter-clockwise local rotation", "joints": out}


# ---------------------------------------------------------------- crouch (M1)

def foot_contacts(ir, within=2.0):
    """{slot: [vertex index]}: the soles of the planted feet, i.e. the
    vertices of each slot that a leg IK target carries (its bone hangs
    from the target) lying within `within` px of that slot's lowest setup
    vertex."""
    targets = {c["target"] for c in ir["ik"]}
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
    targets = {c["target"]: c for c in ir["ik"]}
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
    shins = {c["bones"][1] for c in ir["ik"]}
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
    hips = {bones[c["bones"][0]]["parent"] for c in ir["ik"]}
    if len(hips) != 1:
        raise ValueError(f"cannot tell the hips bone from the leg IK chains ({sorted(hips)})")
    hips = hips.pop()
    pose = {hips: {"y": -float(depth)}}
    groups = {"sole": foot_contacts(ir, within), "foot": planted_vertices(ir), "shin": shin_bottoms(ir)}
    slots = sorted({n for g in groups.values() for n in g})
    knees = [n for c in ir["ik"] for n in (c["bones"][0], c["bones"][1], c["target"])]
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
    for c in ir["ik"]:
        t, k, a = c["bones"][0], c["bones"][1], c["target"]
        move = pos[1][k] - pos[0][k]
        want = (knee_dirs or {}).get(c["name"])
        want = np.asarray(want, float) if want is not None else \
            np.array([math.copysign(1.0, pos[0][t][0] - pos[0][hips][0]), 0.0])
        want = want / (np.linalg.norm(want) or 1.0)
        n = _knee_normal(pos[1][t], pos[1][a], want)
        out["knees"][c["name"]] = {"dx": float(move[0]), "dy": float(move[1]), "dir": want.tolist(),
                                   "along": float(move @ want), "end_px": float((pos[1][k] - pos[1][t]) @ n),
                                   "bend_deg": _leg_bend(pos[1][t], pos[1][k], pos[1][a]),
                                   "setup_bend_deg": _leg_bend(pos[0][t], pos[0][k], pos[0][a])}
    kn = list(out["knees"].values())
    out["knee_bend_diff_deg"] = abs(kn[0]["bend_deg"] - kn[1]["bend_deg"]) if len(kn) == 2 else None
    out["knees_ok"] = bool(kn) and all(x["along"] > 0 and x["end_px"] > 0 for x in kn) and \
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
            "lateral": b.get("lateral", weights.LATERAL), "exclusive": True}


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
            "lateral": round(b.get("lateral", weights.LATERAL), 3), "upper": b["upper"],
            "lower": b["lower"], "helper": b["helper"], "mode": b["mode"], "layers": b["layers"]}


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
    res = build(a.layers_dir, a.joints, a.template, a.mode, None if a.no_source else "auto", a.name, modes, a.mesh,
                not a.keep_joints)
    paths = write(a.out, res)
    tot = res["report"]["totals"]
    ir = res["ir"]
    print(f"{paths['skeleton']}: {len(ir['bones'])} bones, {len(ir['slots'])} slots, {len(ir['ik'])} ik, "
          f"{len(ir['transform'])} transform, {len(ir['physics'])} physics, {len(ir['animations'])} animations; "
          f"{tot['vertices']} vertices ({tot['vertex_ratio']} of the 22 px grid), B2 {tot['b2']}")
    for n in res["report"]["notes"]:
        print("  note:", n)
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
