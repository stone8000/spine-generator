"""Layers + joints + a template -> an IR rig that bends at its joints.

    python -m spinegen.rigbuild <layers dir> --template biped_mascot --mode joint|rigid -o <outdir>
        [--joints joints.json] [--name monkey] [--no-source]

writes rig.json + motion.json (IR), <name>.json (Spine 4.2, spinegen.emit.spine42),
<name>.atlas + <name>.png (spinegen.atlas.pack), joints.json (the budget scanner's
joints, world coordinates) and build_report.json.

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
           slide whatever the hips do. bend direction: the setup leg's own
           (both directions are solved and the one that reproduces the
           setup pose wins), the template's per side when the leg is
           straight. softness: the template's fraction of leg length,
           capped so the setup pose is unchanged (a nearly straight setup
           leg leaves almost no room; the report gives the cap).
  meshes   every layer through mesh.build. In "joint" mode a layer gets a
           band at each joint it crosses (its art comes within one
           halfwidth of the joint) where the template says it bends:
           "both" sides at elbows, knees, wrists, ankles; only the "lower"
           side at shoulders and hips. halfwidth = the largest inscribed
           radius of the lower limb's art near the joint (the same rule as
           art_gates.estimate_halfwidth); band half-length = mesh.BAND_EXTENT
           halfwidths, shortened so neighbouring bands on one bone never
           overlap. In "rigid" mode the same meshes have no bands.
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
SCHEMA_BUDGET_JOINTS = "spinegen-budget-joints/1"
HW_REACH = 40.0       # px: halfwidth = largest inscribed radius within this of the art nearest the joint
CROSS = 1.0           # a layer crosses a joint when its art comes within CROSS x halfwidth of it
STRAIGHT_DEG = 1.0    # a setup leg bent less than this takes the template's bend direction
SOFT_GUARD = 0.999    # softness stays this fraction under the setup slack (no effect at setup)
BAKE_FPS = 30         # source feet are sampled at this rate to key the IK targets in a jump
LIFT_MIN = 0.5        # px: a source foot that never rises more than this keeps its target planted
LIFT_FULL = 8.0       # px of lift over which a lifted target starts following the source foot's x
BAKE_TOL = 0.25       # px: a baked key is dropped when its neighbours interpolate it this closely
AXIS_SNAP = 10.0      # deg: scale keys swap x/y when a bone now points 90 +- this from its source
AXIS_MEAN_MAX = 45.0  # deg: a "both" band runs along the mean of its bones only when they are this close
ACC_FADE = (0.5, 2.0) # accessory weight: 0 inside 0.5 band half-lengths of a joint, full beyond 2.0
SOLE_ROWS = 2.0      # px: the sole is the foot art this close to its lowest row (template foot_contact)
SOLE_CLEAR = 0.75    # an ankle band reaches at most this fraction of the way from the ankle to the sole
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

def build(layers_dir, joints=None, template="biped_mascot", mode="joint", source="auto", name=None):
    """Build the rig. Returns {"ir", "images", "joints" (budget form),
    "report"}.

    layers_dir  an ingest_spine / PSD-export layers dir (layers.json + PNGs)
    joints      joints.json path or dict (default: <layers_dir>/joints.json)
    source      "auto": the source IR named by layers.json (physics,
                animations, accessory weights), None: none, or an IR dict
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    doc, imgs = ingest_spine.load_layers(layers_dir)
    if joints is None:
        joints = os.path.join(layers_dir, "joints.json")
    if isinstance(joints, (str, os.PathLike)):
        with open(joints) as f:
            joints = json.load(f)
    t = tmpl.load(template) if isinstance(template, str) else template
    if source == "auto":
        source = _load_source(layers_dir, doc)
    return _Builder(doc, imgs, joints, t, mode, source, name).run()


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
        self.bones = {}      # name -> {"parent", "head", "tail" (canvas), "len", "role", "limb_parent"}
        self.order = []      # bone names, parents first
        self._art = {}

    # ---- orchestration

    def run(self):
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
            bands[jname] = {"center": c, "axis": axis, "halfwidth": hw, "extent": mesh.BAND_EXTENT,
                            "upper": upper, "lower": lower, "helper": helper, "mode": spec["band"],
                            "budget": spec.get("budget")}
        targets = {n for n in self.order if self.bones[n]["kind"] == "ik_target"}
        for jname, band in bands.items():
            band["layers"] = [L["name"] for L in self.layers if self._carries(L, band)]
            # a planted foot's sole must be 100% foot: the ankle band stops short of it
            if self.bones[band["lower"]]["parent"] in targets:
                pts = self._art_points([band["lower"]])
                if pts.size:
                    sole = pts[pts[:, 1] >= pts[:, 1].max() - SOLE_ROWS]  # the lowest art rows (canvas y down)
                    reach = float(((sole - band["center"]) @ band["axis"]).min())
                    if reach > 0 and SOLE_CLEAR * reach < band["extent"] * band["halfwidth"]:
                        band["extent"] = SOLE_CLEAR * reach / band["halfwidth"]
                        self.notes.append(f"joint {jname}: band shortened to {band['extent']:.2f} halfwidths "
                                          f"to stay clear of the sole")
        # two bands on one layer must not overlap: they share the distance between them
        for a, ba in bands.items():
            for b, bb in bands.items():
                if a != b and set(ba["layers"]) & set(bb["layers"]):
                    d = float(np.linalg.norm(ba["center"] - bb["center"]))
                    ba["extent"] = min(ba["extent"], 0.5 * d / ba["halfwidth"])
        for band in bands.values():
            band["lateral"] = self._lateral(band)
        return bands

    def _lateral(self, band):
        """Band reach sideways, in halfwidths: enough to take in all the art
        of the layers that carry it within its length (weights.band_mask)."""
        ext = band["extent"] * band["halfwidth"]
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
            near = np.abs(s) <= ext + 2.0
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
            if abs(bend_deg) >= STRAIGHT_DEG:
                c["bend_positive"] = self._setup_bend(c, bind)
            self.ir["ik"].append(c)
            order += 1
            self.ik_report[ik["name"]] = {"softness": round(soft, 4), "softness_wanted": round(want, 3),
                                          "setup_slack": round(slack, 4), "bend_deg": round(bend_deg, 3),
                                          "bend_positive": c["bend_positive"], "planted": bool(ik.get("planted"))}
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

    def _setup_bend(self, c, bind):
        """The bend direction that reproduces the setup pose."""
        best, err = c["bend_positive"], math.inf
        for bend in (True, False):
            trial = dict(c, bend_positive=bend)
            rig = dict(self.ir, ik=[trial], transform=[], physics=[])
            w = lbs.Evaluator(rig).world()
            idx = irmod.bone_index(rig)
            e = max(abs(np.asarray(w[idx[n]]) - np.asarray(bind[n])).max() for n in c["bones"])
            if e < err:
                best, err = bend, e
        return best

    # ---- meshes and weights

    def meshes(self):
        out, stats = {}, []
        for L in self.layers:
            alpha = self.imgs[L["name"]][..., 3]
            bands = [b for b in self.bands.values() if L["name"] in b["layers"]] if self.mode == "joint" else []
            m = mesh.build(alpha, (L["x"], L["y"]), bands=[_mesh_band(b) for b in bands])
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
            ext = b["extent"] * b["halfwidth"] + weights.END_TOL
            near = lat <= b["lateral"] * b["halfwidth"]
            for side, keep, drop in ((s > ext, b["lower"], (b["upper"], b["helper"])),
                                     (s < -ext, b["upper"], (b["lower"], b["helper"]))):
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
            ext = b["extent"] * b["halfwidth"]
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
            out.append({"name": jname, "id": b["budget"], "bone": b["lower"], "dirs": [1, -1],
                        "band": {"center": self.cv.to_world(b["center"]).tolist(), "upper": b["upper"],
                                 "helper": b["helper"], "lower": b["lower"], "halfwidth": b["halfwidth"],
                                 "axis": self.cv.dir_to_world(b["axis"]).tolist(), "extent": b["extent"]}})
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


def crouch(ir, depth, skeleton=None, atlas=None, within=2.0):
    """Lower the hips (the parent of the IK thighs) by `depth` world units
    and measure how far each sole vertex moves (SOTA §5 M1: <= 1 px).
    With `skeleton` and `atlas` the official runtime measures it
    (spinegen.oracle.dump, pose added to setup); otherwise spinegen.lbs.
    Returns {"depth", "by", "max_slide", "slots": {slot: max px},
    "hips_drop"}."""
    hips = {irmod.by_name(ir["bones"])[c["bones"][0]]["parent"] for c in ir["ik"]}
    if len(hips) != 1:
        raise ValueError(f"cannot tell the hips bone from the leg IK chains ({sorted(hips)})")
    hips = hips.pop()
    pose = {hips: {"y": -float(depth)}}
    contacts = foot_contacts(ir, within)
    if skeleton is not None:
        from . import oracle
        d0 = oracle.dump(skeleton, atlas)["frames"][0]
        d1 = oracle.dump(skeleton, atlas, pose=pose, pose_mode="add")["frames"][0]
        verts = [{n: np.asarray(d["slots"][n]["vertices"], float).reshape(-1, 2) for n in contacts} for d in (d0, d1)]
        drop = d0["bones"][hips]["y"] - d1["bones"][hips]["y"]
        by = "spine-core (qa dump)"
    else:
        ev = lbs.Evaluator(ir)
        idx = ev.index
        w0 = ev.world()
        b = irmod.by_name(ir["bones"])[hips]
        w1 = ev.world(pose={hips: {"y": b["y"] - float(depth)}})
        verts = [{n: ev.attachment_vertices(w, n, dict(ev.shown())[n]) for n in contacts} for w in (w0, w1)]
        drop = w0[idx[hips]][5] - w1[idx[hips]][5]
        by = "spinegen.lbs"
    slide = {n: float(np.linalg.norm(verts[1][n][i] - verts[0][n][i], axis=1).max()) for n, i in contacts.items()}
    return {"depth": float(depth), "by": by, "hips_drop": float(drop), "contact_vertices":
            {n: len(i) for n, i in contacts.items()}, "slots": slide, "max_slide": max(slide.values()) if slide else None}


# ---------------------------------------------------------------- helpers

def _mesh_band(b):
    return {"center": tuple(b["center"]), "axis": tuple(b["axis"]), "halfwidth": b["halfwidth"], "extent": b["extent"]}


def _weight_joint(b):
    return {"center": tuple(b["center"]), "upper": b["upper"], "helper": b["helper"], "lower": b["lower"],
            "halfwidth": b["halfwidth"], "axis": tuple(b["axis"]), "extent": b["extent"],
            "lateral": b.get("lateral", weights.LATERAL), "exclusive": True}


def _round_band(b):
    return {"center": [round(float(v), 3) for v in b["center"]], "axis": [round(float(v), 5) for v in b["axis"]],
            "halfwidth": round(b["halfwidth"], 3), "extent": round(b["extent"], 4),
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
                    help="measure the soles' slide with the hips lowered by this fraction of H (0: skip); "
                         "uses the runtime oracle when it is installed")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    res = build(a.layers_dir, a.joints, a.template, a.mode, None if a.no_source else "auto", a.name)
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
        c = crouch(ir, a.crouch * ir["H"], *((paths["skeleton"], paths["atlas"]) if ok else (None, None)))
        res["report"]["crouch"] = c
        _dump(paths["report"], dict(json.load(open(paths["report"])), crouch=c))
        print(f"  crouch {a.crouch:g} H = {c['depth']:.1f} px ({c['by']}): soles slide {c['max_slide']:.4f} px "
              f"(M1 <= 1 px), {sum(c['contact_vertices'].values())} sole vertices")


if __name__ == "__main__":
    main()
