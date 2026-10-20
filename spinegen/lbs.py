"""Spine 4.2 pose evaluation in numpy: bone world transforms, constraints,
region and mesh (linear blend) skinning, and the G1 fold count.

WHY
  The budget scanner (P2) sweeps thousands of poses and needs world vertices
  in-process. The official runtime stays the judge: gate C1 holds this module
  to <= 0.1 px of spine-core 4.2.120 (oracle_check.py). It is written from
  the documented transform maths, not copied from runtime source (SOTA §6).
  Where the runtime's results are not those of the clean maths, the
  difference is described here (FAITHFUL, NOT EXACT), reproduced, and
  pinned by C1 against the oracle on synthetic rigs that exercise it
  (spinegen.synth "features", tests/test_oracle.py).

WHAT IS MODELLED
  * world transform of a bone from its applied local values, for every
    inherit mode: normal, onlyTranslation, noRotationOrReflection (keeps the
    parent's scale, drops its rotation and reflection), noScale and
    noScaleOrReflection (keep the parent's rotation, drop its scale);
  * the runtime's update order: of the constraints that run (4.2 runs one
    per order value 0..N-1, see ir.running), each after the bones it reads
    and before the children of the bones it moves;
  * IK (one bone with compress/stretch/uniform; two bones with bend,
    softness and stretch, and the ellipse solution when the parent's scale
    is not uniform) and transform constraints (world or local, absolute or
    relative, plus the IR's half_angle shorthand);
  * path constraints are not evaluated (reported in `warnings`); physics is
    not simulated: world(physics="reset") is the first frame after a reset
    (what `dump` shows for a setup pose or an explicit pose), "none" is
    Physics.none (what `dump --physics none` shows for animation frames);
  * region quads (with atlas whitespace offsets) and vertex attachments
    (weighted or rigid, with deform offsets).

FAITHFUL, NOT EXACT
  Where the 4.2 runtime's arithmetic is not the clean inverse or the clean
  blend, this module follows the runtime, because the runtime is what every
  player draws. Three places matter:
  * after a constraint edits a world matrix, the applied values are
    re-derived the runtime's way (applied_from_world): a sheared local loses
    a cos(shear) factor of y scale, noRotationOrReflection forgets the
    parent's rotation, noScale measures rotation from the pose-rotation
    frame. A bone re-updated from those values (a later constraint moves
    its parent) or read by a later local constraint lands where the runtime
    puts it, not where the exact decomposition would;
  * a physics reset re-derives its bone's applied values the same way, even
    though it moves nothing;
  * a local absolute transform constraint does not wrap rotation or shear
    differences and divides its scale blend by the bone's own scale; world
    constraints wrap their angle once, not fully.

CONVENTIONS
  World matrices are (a, b, c, d, x, y): column 0 = (a, c) is the bone's x
  axis, as in the runtime. The skeleton sits at the origin, scale 1, no flip,
  y up. Degrees convert with the runtime's PI (3.1415927), so poses agree to
  ~1e-9 instead of ~1e-5.
"""
import math

import numpy as np

from . import ir as irmod

PI = 3.1415927  # the runtime's MathUtils.PI, not math.pi
PI2 = PI * 2
DEG_RAD = PI / 180
RAD_DEG = 180 / PI
PHYSICS_MODES = ("reset", "none")
LOCAL_FIELDS = ("x", "y", "rotation", "scale_x", "scale_y", "shear_x", "shear_y")
_POSE_ALIASES = {"scaleX": "scale_x", "scaleY": "scale_y", "shearX": "shear_x", "shearY": "shear_y"}


# ---------------------------------------------------------------- bone maths

def _local(rotation, shear_x, shear_y, scale_x, scale_y):
    rx = (rotation + shear_x) * DEG_RAD
    ry = (rotation + 90 + shear_y) * DEG_RAD
    return math.cos(rx) * scale_x, math.cos(ry) * scale_y, math.sin(rx) * scale_x, math.sin(ry) * scale_y


def _no_rotation_frame(pa, pb, pc, pd):
    """Parent frame with its rotation and reflection removed, for
    noRotationOrReflection: the x axis keeps its length, the y axis is
    rebuilt perpendicular to it with the parent's |det|. Returns the frame
    as (qa, qb, qc, qd) plus the parent's x-axis angle in degrees."""
    s = pa * pa + pc * pc
    if s > 0.0001:
        s = abs(pa * pd - pb * pc) / s
        return pa, -pc * s, pc, pa * s, math.atan2(pc, pa) * RAD_DEG
    # degenerate x axis: fall back to the y axis for the angle
    return 0.0, -pb, 0.0, pd, 90 - math.atan2(pd, pb) * RAD_DEG


def _no_scale_frame(pa, pb, pc, pd, rotation, keep_reflection):
    """Unit frame of noScale(OrReflection): x along where the parent maps the
    bone's rotation, y perpendicular; noScale keeps a reflected parent's
    handedness, noScaleOrReflection does not."""
    r = rotation * DEG_RAD
    cos, sin = math.cos(r), math.sin(r)
    za, zc = pa * cos + pb * sin, pc * cos + pd * sin
    s = math.sqrt(za * za + zc * zc)
    if s > 0.00001:
        s = 1 / s
    za, zc = za * s, zc * s
    s = math.sqrt(za * za + zc * zc)
    if keep_reflection and pa * pd - pb * pc < 0:
        s = -s
    r = PI / 2 + math.atan2(zc, za)
    return za, math.cos(r) * s, zc, math.sin(r) * s


def compose(parent, x, y, rotation, scale_x, scale_y, shear_x, shear_y, inherit="normal"):
    """World (a, b, c, d, wx, wy) of a bone from applied local values."""
    if parent is None:
        la, lb, lc, ld = _local(rotation, shear_x, shear_y, scale_x, scale_y)
        return la, lb, lc, ld, x, y
    pa, pb, pc, pd, px, py = parent
    wx, wy = pa * x + pb * y + px, pc * x + pd * y + py  # translation always follows the parent
    if inherit == "normal":
        la, lb, lc, ld = _local(rotation, shear_x, shear_y, scale_x, scale_y)
        return pa * la + pb * lc, pa * lb + pb * ld, pc * la + pd * lc, pc * lb + pd * ld, wx, wy
    if inherit == "onlyTranslation":
        la, lb, lc, ld = _local(rotation, shear_x, shear_y, scale_x, scale_y)
        return la, lb, lc, ld, wx, wy
    if inherit == "noRotationOrReflection":
        qa, qb, qc, qd, prx = _no_rotation_frame(pa, pb, pc, pd)
        la, lb, lc, ld = _local(rotation - prx, shear_x, shear_y, scale_x, scale_y)
        return qa * la + qb * lc, qa * lb + qb * ld, qc * la + qd * lc, qc * lb + qd * ld, wx, wy
    if inherit in ("noScale", "noScaleOrReflection"):
        za, zb, zc, zd = _no_scale_frame(pa, pb, pc, pd, rotation, inherit == "noScale")
        la, lb, lc, ld = _local(0.0, shear_x, shear_y, scale_x, scale_y)
        return za * la + zb * lc, za * lb + zb * ld, zc * la + zd * lc, zc * lb + zd * ld, wx, wy
    raise ValueError(f"inherit {inherit!r}")


def _div(n, d):
    """n / d with IEEE results for d == 0 (inf or nan), as JavaScript gives.

    The runtime divides by determinants and lengths that can be 0 and lets
    the inf/nan through; a degenerate rig must come out degenerate here too,
    not raise."""
    if d != 0:
        return n / d
    if n == 0 or n != n:
        return math.nan
    return math.copysign(math.inf, n) * math.copysign(1.0, d)


def applied_from_world(parent, world, inherit="normal", rotation=0.0):
    """The applied local values spine-core 4.2 derives from a world matrix.

    A constraint that edits a bone's world matrix (a world transform
    constraint, physics) leaves the runtime to recompute the bone's applied
    values from it (Bone.updateAppliedTransform). Later constraints read
    those values, and a bone re-updated later is composed from them, so they
    have to be the runtime's values, which are NOT the exact inverse of
    compose():
      * the local matrix R (world seen from the inherit frame) is read as
        scale_x = |R x axis|, scale_y = det(R) / scale_x and shear_y the
        angle between the axes, so a sheared local loses cos(shear) of its
        y scale;
      * noRotationOrReflection measures the rotation in the frame without
        the parent's rotation but does not add that rotation back (compose
        subtracts it again), and only the first row of the inverse frame is
        rebuilt;
      * noScale / noScaleOrReflection build the frame from `rotation`, the
        bone's POSE rotation (not the applied one), and return the angle
        relative to that frame;
      * a root bone reports shear_y with the opposite sign.
    """
    a, b, c, d, wx, wy = world
    if parent is None:
        return (wx, wy, math.atan2(c, a) * RAD_DEG, math.hypot(a, c), math.hypot(b, d), 0.0,
                math.atan2(a * b + c * d, a * d - b * c) * RAD_DEG)
    pa, pb, pc, pd, px, py = parent
    pid = _div(1.0, pa * pd - pb * pc)
    ia, ib, ic, id_ = pd * pid, pb * pid, pc * pid, pa * pid
    dx, dy = wx - px, wy - py
    ax, ay = dx * ia - dy * ib, dy * id_ - dx * ic
    if inherit == "onlyTranslation":
        ra, rb, rc, rd = a, b, c, d
    else:
        if inherit == "noRotationOrReflection":
            s = _div(abs(pa * pd - pb * pc), pa * pa + pc * pc)
            qb, qd = -pc * s, pa * s
            qid = _div(1.0, pa * qd - qb * pc)
            ia, ib = qd * qid, qb * qid  # ic, id stay those of the parent itself
        elif inherit in ("noScale", "noScaleOrReflection"):
            r = rotation * DEG_RAD
            qa, qc = pa * math.cos(r) + pb * math.sin(r), pc * math.cos(r) + pd * math.sin(r)
            s = math.sqrt(qa * qa + qc * qc)
            if s > 0.00001:
                s = 1 / s
            qa, qc = qa * s, qc * s
            s = math.sqrt(qa * qa + qc * qc)
            if inherit == "noScale" and pid < 0:
                s = -s
            r = PI / 2 + math.atan2(qc, qa)
            qb, qd = math.cos(r) * s, math.sin(r) * s
            qid = _div(1.0, qa * qd - qb * qc)
            ia, ib, ic, id_ = qd * qid, qb * qid, qc * qid, qa * qid
        ra, rb = ia * a - ib * c, ia * b - ib * d
        rc, rd = id_ * c - ic * a, id_ * d - ic * b
    sx = math.sqrt(ra * ra + rc * rc)
    if sx > 0.0001:
        det = ra * rd - rb * rc
        return ax, ay, math.atan2(rc, ra) * RAD_DEG, sx, det / sx, 0.0, -math.atan2(ra * rb + rc * rd, det) * RAD_DEG
    return ax, ay, 90 - math.atan2(rd, rb) * RAD_DEG, 0.0, math.sqrt(rb * rb + rd * rd), 0.0, 0.0


def _wrap180(deg):
    if deg > 180:
        return deg - 360
    if deg < -180:
        return deg + 360
    return deg


def _wrap_pi_once(r):
    """One turn of wrapping toward (-pi, pi], as the runtime's world
    transform constraint does it (an angle past 3 pi stays outside)."""
    if r > PI:
        return r - PI2
    if r < -PI:
        return r + PI2
    return r


# ---------------------------------------------------------------- bind pose

def bind_pose(ir):
    """World matrices of the bones' setup values, no constraints.

    This is the space the IR stores vertices in: the reader maps a file's
    bone-local vertices through it and the emitter maps them back, so the
    two are exact inverses whatever the constraints do at setup."""
    world = {}
    for b in ir["bones"]:
        parent = world[b["parent"]] if b["parent"] is not None else None
        world[b["name"]] = compose(parent, b["x"], b["y"], b["rotation"], b["scale_x"], b["scale_y"],
                                   b["shear_x"], b["shear_y"], b["inherit"])
    return world


def to_world(m, lx, ly):
    a, b, c, d, x, y = m
    return a * lx + b * ly + x, c * lx + d * ly + y


def to_local(m, wx, wy):
    a, b, c, d, x, y = m
    det = a * d - b * c
    if det == 0:
        raise ValueError("bone matrix is singular; vertices bound to it cannot be expressed locally")
    dx, dy = wx - x, wy - y
    return (d * dx - b * dy) / det, (a * dy - c * dx) / det


def bind_attachment(ir, slot_bone, att, bind=None):
    """Bone-local binding of a vertex attachment from its IR world vertices.

    Returns {"weighted": bool, "vertex": int[k], "bone": int[k], "local":
    float[k, 2], "weight": float[k]} with one row per influence (rigid
    meshes: one per vertex, bone = slot bone), in the runtime's order —
    which is also the layout of deform offsets.
    """
    bind = bind or bind_pose(ir)
    bidx = irmod.bone_index(ir)
    verts = att["vertices"]
    weights = att.get("weights")
    rows_v, rows_b, rows_l, rows_w = [], [], [], []
    if weights is None:
        m = bind[slot_bone]
        for i, (x, y) in enumerate(verts):
            rows_v.append(i)
            rows_b.append(bidx[slot_bone])
            rows_l.append(to_local(m, x, y))
            rows_w.append(1.0)
    else:
        for i, ((x, y), row) in enumerate(zip(verts, weights)):
            for inf in row:
                px, py = inf[2] if len(inf) > 2 else (x, y)
                rows_v.append(i)
                rows_b.append(bidx[inf[0]])
                rows_l.append(to_local(bind[inf[0]], px, py))
                rows_w.append(float(inf[1]))
    return {"weighted": weights is not None, "count": len(verts),
            "vertex": np.array(rows_v, dtype=np.int64), "bone": np.array(rows_b, dtype=np.int64),
            "local": np.array(rows_l, dtype=float).reshape(-1, 2), "weight": np.array(rows_w, dtype=float)}


# ---------------------------------------------------------------- region quads

def region_offsets(att, region=None):
    """Attachment-space corners of a region quad, in the runtime's order.

    The attachment image is width x height, centred on (x, y), rotated and
    scaled. An atlas region stripped of whitespace covers only part of its
    original image: (offset_x, offset_y) from the original's bottom-left,
    packed size width x height. Without an atlas region the image is taken
    as unstripped, which gives the same quad.
    """
    w, h = att["width"], att["height"]
    sx, sy = att["scale_x"], att["scale_y"]
    if region is not None and region.get("original_width"):
        ow, oh = region["original_width"], region["original_height"]
        rsx, rsy = w / ow * sx, h / oh * sy
        x1 = -w / 2 * sx + region["offset_x"] * rsx
        y1 = -h / 2 * sy + region["offset_y"] * rsy
        x2, y2 = x1 + region["width"] * rsx, y1 + region["height"] * rsy
    else:
        x1, y1, x2, y2 = -w / 2 * sx, -h / 2 * sy, w / 2 * sx, h / 2 * sy
    r = att["rotation"] * DEG_RAD
    cos, sin = math.cos(r), math.sin(r)
    out = []
    for lx, ly in ((x1, y1), (x1, y2), (x2, y2), (x2, y1)):
        out.append((lx * cos - ly * sin + att["x"], lx * sin + ly * cos + att["y"]))
    return np.array(out)


# ---------------------------------------------------------------- folds

def signed_areas(xy, tris):
    """Twice the signed area of each triangle; xy (n, 2), tris (m, 3)."""
    xy, tris = np.asarray(xy, float), np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    a, b, c = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    return (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])


def _windings(world_xy, uvs, tris, eps):
    """Per triangle: sign(world area) * sign(UV area), and whether it counts
    (degenerate triangles, |area| <= eps or no UV area, never do)."""
    w = signed_areas(world_xy, tris)
    u = signed_areas(uvs, tris)
    return np.sign(w) * np.sign(u), (np.abs(w) > eps) & (u != 0)


def fold_reference(world_xy, uvs, tris, eps=1e-6):
    """The winding an unfolded triangle of this mesh has, from its SETUP
    world vertices: -1 normally (UVs are y-down, the skeleton y-up, so the
    windings are opposite), +1 for a mesh drawn mirrored at setup on
    purpose (a limb reused left/right under a bone with scaleX -1).
    The majority of the counted triangles decides; a tie is -1."""
    prod, ok = _windings(world_xy, uvs, tris, eps)
    return 1 if int((prod[ok] > 0).sum()) > int((prod[ok] < 0).sum()) else -1


def fold_mask(world_xy, uvs, tris, eps=1e-6, reference=-1):
    """Triangles drawn folded over (G1 FOLDS): their winding is not the
    mesh's setup winding `reference` (fold_reference). Degenerate triangles
    are not counted."""
    prod, ok = _windings(world_xy, uvs, tris, eps)
    return ok & (prod != reference)


# ---------------------------------------------------------------- evaluator

class Evaluator:
    """Pose evaluation bound to one IR (bindings are computed once).

    pose        {bone: {"rotation", "x", "y", "scaleX"|"scale_x", ...,
                 "inherit"}} absolute local values; missing fields keep setup
    state       {constraint name: {field: value}} overrides of constraint
                 values (ik mix/softness/bend_positive/..., transform
                 {"mix": {...}}), as animations key them
    attachments {slot: attachment key | None} overrides of what slots show
    deform      {slot: dense offsets} in the bound attachment's layout
    """

    def __init__(self, ir, atlas=None, skin=None):
        self.ir = ir
        self.atlas = atlas
        self.skin = skin
        self.bones = ir["bones"]
        self.index = irmod.bone_index(ir)
        self.parent = [self.index[b["parent"]] if b["parent"] is not None else -1 for b in self.bones]
        self.children = [[] for _ in self.bones]
        for i, p in enumerate(self.parent):
            if p >= 0:
                self.children[p].append(i)
        self.slot_index = {s["name"]: i for i, s in enumerate(ir["slots"])}
        self.bind = bind_pose(ir)
        self._bindings = {}
        self._fold_refs = None
        self.warnings = []
        self._order = self._update_order()

    # ---- update order (the runtime's update cache, rebuilt from its rules)

    def _active_skin(self):
        return irmod.find_skin(self.ir, self.skin) if self.skin else None

    def _update_order(self):
        skin = self._active_skin()
        skin_bones = set(skin["bones"]) if skin else set()
        active = [not b["skin_required"] or b["name"] in skin_bones for b in self.bones]
        for i in range(len(self.bones)):  # a skin's bone activates its parents
            if active[i] and self.bones[i]["skin_required"]:
                p = self.parent[i]
                while p >= 0:
                    active[p] = True
                    p = self.parent[p]
        done = [not a for a in active]  # inactive bones never update
        order = []

        def sort_bone(i):
            if done[i]:
                return
            if self.parent[i] >= 0:
                sort_bone(self.parent[i])
            order.append(("bone", i))
            done[i] = True

        def sort_reset(kids):
            for k in kids:
                if not active[k]:
                    continue
                if done[k]:
                    sort_reset(self.children[k])
                done[k] = False

        def skin_ok(kind, c):
            return not c.get("skin_required") or (skin is not None and c["name"] in skin.get(kind, []))

        for kind, c in irmod.running(self.ir):
            if not skin_ok(kind, c):
                continue
            if kind == "ik":
                bones = [self.index[n] for n in c["bones"]]
                tgt = self.index[c["target"]]
                if not active[tgt]:
                    continue
                sort_bone(tgt)
                sort_bone(bones[0])
                if len(bones) == 1:
                    order.append(("ik", c))
                    sort_reset(self.children[bones[0]])
                else:
                    sort_bone(bones[-1])
                    order.append(("ik", c))
                    sort_reset(self.children[bones[0]])
                    done[bones[-1]] = True
            elif kind == "transform":
                half = c.get("kind") == "half_angle"
                bones = [self.index[n] for n in ([c["bone"]] if half else c["bones"])]
                tgt = self.index[c["target"]]
                if not active[tgt]:
                    continue
                sort_bone(tgt)
                for b in bones:
                    if (half or c["local"]) and self.parent[b] >= 0:
                        sort_bone(self.parent[b])  # a local constraint reads the parent's world
                    sort_bone(b)
                order.append(("transform", c))
                for b in bones:
                    sort_reset(self.children[b])
                for b in bones:
                    done[b] = True
            elif kind == "path":
                slot = self.ir["slots"][self.slot_index[c["target"]]]
                sort_bone(self.index[slot["bone"]])
                bones = [self.index[n] for n in c["bones"]]
                for b in bones:
                    sort_bone(b)
                order.append(("path", c))
                for b in bones:
                    sort_reset(self.children[b])
                for b in bones:
                    done[b] = True
            elif kind == "physics":
                b = self.index[c["bone"]]
                if not active[b]:
                    continue
                sort_bone(b)
                order.append(("physics", c))
                sort_reset(self.children[b])
                done[b] = True
        for i in range(len(self.bones)):
            sort_bone(i)
        return order

    # ---- world transforms

    def _pose_values(self, pose):
        applied, inherit = [], []
        for b in self.bones:
            p = {_POSE_ALIASES.get(k, k): v for k, v in (pose or {}).get(b["name"], {}).items()}
            applied.append([float(p.get(f, b[f])) for f in LOCAL_FIELDS])
            inherit.append(p.get("inherit", b["inherit"]))
        return applied, inherit

    def world(self, pose=None, state=None, constraints=True, physics="reset"):
        """World matrices, one (a, b, c, d, x, y) per bone, in IR bone order.

        physics "reset": the frame right after a physics reset, which is what
        the runtime shows for a setup pose or an explicit pose (`dump`
        without --anim): no bone moves, but each physics bone with mix != 0
        has its applied values re-derived from its world matrix.
        "none": Physics.none, the physics constraints do nothing at all.
        """
        if physics not in PHYSICS_MODES:
            raise ValueError(f"physics must be one of {PHYSICS_MODES} (lbs does not simulate physics)")
        applied, inherit = self._pose_values(pose)
        world = [(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)] * len(self.bones)
        ctx = _Ctx(self, applied, inherit, world)
        for kind, item in self._order:
            if kind == "bone":
                ctx.update(item)
            elif constraints:
                c = dict(item)
                if state and item["name"] in state:
                    c = _merged(c, state[item["name"]])
                if kind == "ik":
                    ctx.ik(c)
                elif kind == "transform":
                    ctx.transform(c)
                elif kind == "path":
                    if _path_active(c):
                        self._warn(f"path constraint {c['name']} not evaluated")
                elif kind == "physics" and physics == "reset" and c["mix"] != 0:
                    ctx.reapply(self.index[c["bone"]])
        return world

    def world_dict(self, world):
        return {b["name"]: world[i] for i, b in enumerate(self.bones)}

    def _warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # ---- attachments and vertices

    def attachment(self, slot_name, key):
        if key is None:
            return None
        return irmod.resolve_attachment(self.ir, self.skin, slot_name, key)

    def binding(self, slot_name, att):
        geo = irmod.geometry_of(self.ir, slot_name, att)
        k = id(geo)
        if k not in self._bindings:
            slot = self.ir["slots"][self.slot_index[slot_name]]
            self._bindings[k] = (geo, bind_attachment(self.ir, slot["bone"], geo, self.bind))
        return self._bindings[k]

    def attachment_vertices(self, world, slot_name, att, deform=None):
        """World vertices (n, 2) of `att` shown on `slot_name`."""
        slot = self.ir["slots"][self.slot_index[slot_name]]
        if att["type"] == "region":
            region = None
            if self.atlas is not None:
                region = self.atlas["regions"].get(att.get("path") or att["name"])
            return np.array([to_world(world[self.index[slot["bone"]]], x, y)
                             for x, y in region_offsets(att, region)])
        if att["type"] == "point":
            return np.array([to_world(world[self.index[slot["bone"]]], att["x"], att["y"])])
        geo, bnd = self.binding(slot_name, att)
        local = bnd["local"]
        if deform is not None and len(deform):
            local = local + np.asarray(deform, float).reshape(-1, 2)
        W = np.array(world)
        if not bnd["weighted"]:
            a, b, c, d, x, y = W[self.index[slot["bone"]]]
            return np.stack([a * local[:, 0] + b * local[:, 1] + x, c * local[:, 0] + d * local[:, 1] + y], 1)
        M = W[bnd["bone"]]
        px = (M[:, 0] * local[:, 0] + M[:, 1] * local[:, 1] + M[:, 4]) * bnd["weight"]
        py = (M[:, 2] * local[:, 0] + M[:, 3] * local[:, 1] + M[:, 5]) * bnd["weight"]
        n = bnd["count"]
        return np.stack([np.bincount(bnd["vertex"], px, n), np.bincount(bnd["vertex"], py, n)], 1)

    def shown(self, attachments=None):
        """(slot name, attachment or None) for every slot, setup order."""
        out = []
        for s in self.ir["slots"]:
            key = s["attachment"]
            if attachments and s["name"] in attachments:
                key = attachments[s["name"]]
            out.append((s["name"], self.attachment(s["name"], key)))
        return out

    def frame(self, pose=None, state=None, attachments=None, deform=None, world=None):
        """The same shape as one `qa/run.mjs dump` frame (minus anim/t)."""
        world = world if world is not None else self.world(pose, state)
        bones = {b["name"]: dict(zip("abcdxy", world[i])) for i, b in enumerate(self.bones)}
        slots = {}
        for name, att in self.shown(attachments):
            if att is None:
                slots[name] = {"attachment": None, "vertices": []}
                continue
            v = self.attachment_vertices(world, name, att, (deform or {}).get(name))
            slots[name] = {"attachment": att["name"], "vertices": v.reshape(-1).tolist()}
        return {"bones": bones, "slots": slots}

    def fold_references(self):
        """{(slot, id(attachment)): +1 | -1}: each mesh's winding at the setup
        pose (fold_reference), for every mesh the active and default skins
        can show on each slot. Computed once."""
        if self._fold_refs is None:
            world = self.world()
            refs = {}
            for skin_name in dict.fromkeys([self.skin, "default"]):
                skin = irmod.find_skin(self.ir, skin_name) if skin_name else None
                for slot, atts in (skin["attachments"] if skin else {}).items():
                    for att in atts.values():
                        if att["type"] in ("mesh", "linkedmesh") and (slot, id(att)) not in refs:
                            geo = irmod.geometry_of(self.ir, slot, att)
                            v = self.attachment_vertices(world, slot, att)
                            refs[(slot, id(att))] = fold_reference(v, geo["uvs"], geo["triangles"])
            self._fold_refs = refs
        return self._fold_refs

    def folds(self, pose=None, state=None, attachments=None, deform=None, world=None):
        """G1: folded triangles over every shown mesh, each against its own
        setup winding (a mesh mirrored at setup is not folded)."""
        world = world if world is not None else self.world(pose, state)
        refs = self.fold_references()
        total, tris, per = 0, 0, {}
        for name, att in self.shown(attachments):
            if att is None or att["type"] not in ("mesh", "linkedmesh"):
                continue
            geo = irmod.geometry_of(self.ir, name, att)
            v = self.attachment_vertices(world, name, att, (deform or {}).get(name))
            m = fold_mask(v, geo["uvs"], geo["triangles"], reference=refs.get((name, id(att)), -1))
            per[name] = int(m.sum())
            total += per[name]
            tris += len(geo["triangles"])
        return {"folds": total, "triangles": tris, "slots": per}


def _merged(c, over):
    c = dict(c)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k] = {**c[k], **v}
        else:
            c[k] = v
    return c


def _path_active(c):
    m = c["mix"]
    return m["rotate"] != 0 or m["x"] != 0 or m["y"] != 0


def _ik2_ellipse(l1, ex, ey, psx, psy, tx, ty, dd, bend):
    """Two-bone IK when the parent's scale is not uniform: (a1, a2) radians.

    In the parent's unscaled space the joint sits at (l1, 0) and the parent's
    scale stretches the child, so a child turned by theta ends on an ellipse:
    (l1 + ex cos theta, ey sin theta), ex = psx * l2, ey = psy * l2. Reaching
    the target at squared distance dd puts the end at x = X with
      (ey^2 - ex^2) X^2 - 2 ey^2 l1 X + (ey^2 l1^2 + ex^2 dd - ex^2 ey^2) = 0
    (substitute cos theta = (X - l1) / ex into x^2 + y^2 = dd). The root of
    smaller magnitude is taken, solved in the cancellation-free form; then
    Y = +-sqrt(dd - X^2) by the bend, the parent turns the end point onto the
    target, and theta is read back in the unstretched frame.
    Out of reach (no real root, or X beyond the target distance), the chain
    goes to the ellipse point nearest to or farthest from the parent origin,
    whichever extreme the target distance is closer to.
    """
    ta = math.atan2(ty, tx)
    aa, bb = ex * ex, ey * ey
    qa, qb, qc = bb - aa, -2 * bb * l1, bb * l1 * l1 + aa * dd - aa * bb
    disc = qb * qb - 4 * qa * qc
    if disc >= 0:
        q = math.sqrt(disc)
        if qb < 0:
            q = -q
        q = -(qb + q) * 0.5
        r0, r1 = _div(q, qa), _div(qc, q)
        x = r0 if abs(r0) < abs(r1) else r1
        rest = dd - x * x
        if rest >= 0:
            y = math.sqrt(rest) * bend
            return ta - math.atan2(y, x), math.atan2(y / psy, (x - l1) / psx)
    # the two ends of the ellipse's major sweep, then its interior extremum
    near = [PI, l1 - ex, 0.0, (l1 - ex) ** 2]
    far = [0.0, l1 + ex, 0.0, (l1 + ex) ** 2]
    c = _div(-ex * l1, aa - bb)
    if -1 <= c <= 1:
        c = math.acos(c)
        x, y = ex * math.cos(c) + l1, ey * math.sin(c)
        d = x * x + y * y
        if d < near[3]:
            near = [c, x, y, d]
        if d > far[3]:
            far = [c, x, y, d]
    angle, x, y, _ = near if dd <= (near[3] + far[3]) * 0.5 else far
    return ta - math.atan2(y * bend, x), angle * bend


class _Ctx:
    """Mutable state of one world() call: applied values and world matrices."""

    def __init__(self, ev, applied, inherit, world):
        self.ev, self.applied, self.inherit, self.world = ev, applied, inherit, world
        # the pose rotation (bone.rotation), which constraints never change:
        # applied_from_world builds the noScale frame from it
        self.pose_rotation = [v[2] for v in applied]

    def parent_world(self, i):
        p = self.ev.parent[i]
        return self.world[p] if p >= 0 else None

    def update(self, i):
        self.world[i] = compose(self.parent_world(i), *self.applied[i], self.inherit[i])

    def set_applied(self, i, vals):
        self.applied[i] = list(vals)
        self.update(i)

    def reapply(self, i):
        """Re-derive bone i's applied values from its world matrix, the way
        the runtime does after any world edit (applied_from_world)."""
        self.applied[i] = list(applied_from_world(self.parent_world(i), self.world[i], self.inherit[i],
                                                  self.pose_rotation[i]))

    def set_world(self, i, m):
        self.world[i] = m
        self.reapply(i)

    # ---- IK

    def ik(self, c):
        if c["mix"] == 0:
            return
        idx = [self.ev.index[n] for n in c["bones"]]
        t = self.world[self.ev.index[c["target"]]]
        if len(idx) == 1:
            self.ik1(idx[0], t[4], t[5], c["compress"], c["stretch"], c["uniform"], c["mix"])
        else:
            bend = 1 if c["bend_positive"] else -1
            self.ik2(idx[0], idx[-1], t[4], t[5], bend, c["stretch"], c["uniform"], c["softness"], c["mix"])

    def ik1(self, i, tx, ty, compress, stretch, uniform, alpha):
        """Point bone i's x axis at (tx, ty); optionally scale it to reach."""
        p = self.parent_world(i)
        if p is None:
            raise ValueError("IK bone must have a parent")
        ax, ay, ar, asx, asy, ashx, ashy = self.applied[i]
        bone_w = self.world[i]
        mode = self.inherit[i]
        pa, pb, pc, pd, px, py = p
        extra = 0.0
        if mode == "onlyTranslation":
            vx, vy = tx - bone_w[4], ty - bone_w[5]
        else:
            if mode == "noRotationOrReflection":
                # the parent without its rotation; unlike compose() there is
                # no separate degenerate branch, the length is floored instead
                s = abs(pa * pd - pb * pc) / max(0.0001, pa * pa + pc * pc)
                pb, pd = -pc * s, pa * s
                extra = math.atan2(pc, pa) * RAD_DEG
            det = pa * pd - pb * pc
            if abs(det) <= 0.0001:
                vx = vy = 0.0
            else:
                dx, dy = tx - px, ty - py
                vx, vy = (dx * pd - dy * pb) / det - ax, (dy * pa - dx * pc) / det - ay
        delta = math.atan2(vy, vx) * RAD_DEG - ashx - ar + extra
        if asx < 0:
            delta += 180
        delta = _wrap180(delta)
        sx, sy = asx, asy
        if compress or stretch:
            if mode in ("noScale", "noScaleOrReflection"):
                vx, vy = tx - bone_w[4], ty - bone_w[5]
            length = self.ev.bones[i]["length"] * sx
            if length > 0.0001:
                dd = vx * vx + vy * vy
                if (compress and dd < length * length) or (stretch and dd > length * length):
                    s = (math.sqrt(dd) / length - 1) * alpha + 1
                    sx *= s
                    if uniform:
                        sy *= s
        self.set_applied(i, (ax, ay, ar + delta * alpha, sx, sy, ashx, ashy))

    def ik2(self, pi, ci, tx, ty, bend, stretch, uniform, softness, alpha):
        """Two-bone IK by the law of cosines in the grandparent's space."""
        if self.inherit[pi] != "normal" or self.inherit[ci] != "normal":
            return  # the runtime leaves such chains alone (gate L5 rejects them)
        px, py, prot, psx, psy, _, _ = self.applied[pi]
        cx, cy, crot, csx0, csy0, cshx, cshy = self.applied[ci]
        sx, sy = psx, psy
        # solve with positive scales, put reflections back at the end
        off1, s2 = (180.0, -1) if psx < 0 else (0.0, 1)
        psx = abs(psx)
        if psy < 0:
            psy, s2 = -psy, -s2
        off2 = 180.0 if csx0 < 0 else 0.0
        csx = abs(csx0)
        same_scale = abs(psx - psy) <= 0.0001
        if not same_scale or stretch:
            cy = 0.0  # the child is treated as sitting on the parent's x axis
        a, b, c, d, wx, wy = self.world[pi]
        jx, jy = a * cx + b * cy + wx, c * cx + d * cy + wy
        g = self.parent_world(pi)
        if g is None:
            raise ValueError("IK parent must itself have a parent")
        ga, gb, gc, gd, gx, gy = g
        det = ga * gd - gb * gc
        inv = 0.0 if abs(det) <= 0.0001 else 1 / det

        def in_parent_space(x, y):
            x, y = x - gx, y - gy
            return (x * gd - y * gb) * inv - px, (y * ga - x * gc) * inv - py

        dx, dy = in_parent_space(jx, jy)
        l1 = math.sqrt(dx * dx + dy * dy)
        l2 = self.ev.bones[ci]["length"] * csx
        if l1 < 0.0001:
            self.ik1(pi, tx, ty, False, stretch, False, alpha)
            self.set_applied(ci, (cx, cy, 0.0, csx0, csy0, cshx, cshy))
            return
        vx, vy = in_parent_space(tx, ty)
        dd = vx * vx + vy * vy
        if softness != 0:
            # past (reach - softness) the target is eased in quadratically, so
            # the chain straightens smoothly instead of snapping at full reach
            softness *= psx * (csx + 1) * 0.5
            td = math.sqrt(dd)
            sd = td - l1 - l2 * psx + softness
            if sd > 0:
                p = min(1.0, sd / (softness * 2)) - 1
                p = (sd - softness * (1 - p * p)) / td
                vx, vy = vx - p * vx, vy - p * vy
                dd = vx * vx + vy * vy
        if same_scale:
            l2 *= psx
            cos = (dd - l1 * l1 - l2 * l2) / (2 * l1 * l2)
            if cos < -1:
                cos, a2 = -1.0, PI * bend
            elif cos > 1:
                cos, a2 = 1.0, 0.0
                if stretch:
                    k = (math.sqrt(dd) / (l1 + l2) - 1) * alpha + 1
                    sx *= k
                    if uniform:
                        sy *= k
            else:
                a2 = math.acos(cos) * bend
            ea, eb = l1 + l2 * cos, l2 * math.sin(a2)
            a1 = math.atan2(vy * ea - vx * eb, vx * ea + vy * eb)
        else:
            a1, a2 = _ik2_ellipse(l1, l2 * psx, l2 * psy, psx, psy, vx, vy, dd, bend)
        os = math.atan2(cy, cx) * s2
        r1 = _wrap180((a1 - os) * RAD_DEG + off1 - prot)
        self.set_applied(pi, (px, py, prot + r1 * alpha, sx, sy, 0.0, 0.0))
        r2 = _wrap180(((a2 + os) * RAD_DEG - cshx) * s2 + off2 - crot)
        self.set_applied(ci, (cx, cy, crot + r2 * alpha, csx0, csy0, cshx, cshy))

    # ---- transform constraints

    def transform(self, c):
        if c.get("kind") == "half_angle":
            c = {"bones": [c["bone"]], "target": c["target"], "local": True, "relative": False,
                 "offset": {"rotation": 0.0, "x": 0.0, "y": 0.0, "scale_x": 0.0, "scale_y": 0.0, "shear_y": 0.0},
                 "mix": {"rotate": c["mix"], "x": 0.0, "y": 0.0, "scale_x": 0.0, "scale_y": 0.0, "shear_y": 0.0}}
        mix = c["mix"]
        if all(v == 0 for v in mix.values()):
            return
        ti = self.ev.index[c["target"]]
        for name in c["bones"]:
            i = self.ev.index[name]
            if c["local"]:
                self._transform_local(i, ti, c["offset"], mix, c["relative"])
            else:
                self._transform_world(i, ti, c["offset"], mix, c["relative"])

    def _transform_world(self, i, ti, off, mix, relative):
        ta, tb, tc, td, tx, ty = self.world[ti]
        a, b, c, d, x, y = self.world[i]
        reflect = 1.0 if ta * td - tb * tc > 0 else -1.0
        off_r = off["rotation"] * DEG_RAD * reflect
        off_shy = off["shear_y"] * DEG_RAD * reflect
        if mix["rotate"] != 0:
            r = math.atan2(tc, ta) + off_r if relative else math.atan2(tc, ta) - math.atan2(c, a) + off_r
            r = _wrap_pi_once(r) * mix["rotate"]
            cos, sin = math.cos(r), math.sin(r)
            a, b, c, d = cos * a - sin * c, cos * b - sin * d, sin * a + cos * c, sin * b + cos * d
        if mix["x"] != 0 or mix["y"] != 0:
            px, py = to_world(self.world[ti], off["x"], off["y"])
            if relative:
                x, y = x + px * mix["x"], y + py * mix["y"]
            else:
                x, y = x + (px - x) * mix["x"], y + (py - y) * mix["y"]
        if mix["scale_x"] != 0:
            s = math.sqrt(a * a + c * c)
            if relative:
                k = (math.sqrt(ta * ta + tc * tc) - 1 + off["scale_x"]) * mix["scale_x"] + 1
            else:
                k = (s + (math.sqrt(ta * ta + tc * tc) - s + off["scale_x"]) * mix["scale_x"]) / s if s else 1.0
            a, c = a * k, c * k
        if mix["scale_y"] != 0:
            s = math.sqrt(b * b + d * d)
            if relative:
                k = (math.sqrt(tb * tb + td * td) - 1 + off["scale_y"]) * mix["scale_y"] + 1
            else:
                k = (s + (math.sqrt(tb * tb + td * td) - s + off["scale_y"]) * mix["scale_y"]) / s if s else 1.0
            b, d = b * k, d * k
        if mix["shear_y"] > 0:
            by = math.atan2(d, b)
            if relative:
                r = _wrap_pi_once(math.atan2(td, tb) - math.atan2(tc, ta))
                r = by + (r - PI / 2 + off_shy) * mix["shear_y"]
            else:
                r = _wrap_pi_once(math.atan2(td, tb) - math.atan2(tc, ta) - (by - math.atan2(c, a)))
                r = by + (r + off_shy) * mix["shear_y"]
            s = math.sqrt(b * b + d * d)
            b, d = math.cos(r) * s, math.sin(r) * s
        self.set_world(i, (a, b, c, d, x, y))

    def _transform_local(self, i, ti, off, mix, relative):
        tx, ty, tr, tsx, tsy, _, tshy = self.applied[ti]
        x, y, r, sx, sy, shx, shy = self.applied[i]
        if relative:
            r += (tr + off["rotation"]) * mix["rotate"]
            x += (tx + off["x"]) * mix["x"]
            y += (ty + off["y"]) * mix["y"]
            sx *= (tsx - 1 + off["scale_x"]) * mix["scale_x"] + 1
            sy *= (tsy - 1 + off["scale_y"]) * mix["scale_y"] + 1
            shy += (tshy + off["shear_y"]) * mix["shear_y"]
        else:
            # The runtime's absolute local blend, kept as it is: the rotation
            # and shear differences are NOT wrapped (a target 340 deg away
            # turns the bone the long way), and the scale blend is divided by
            # the bone's own scale (a bone at scale 2 pulled half way to a
            # target at 1 ends at 0.75, not 1.5). The half_angle helper is
            # this constraint, so its >180 deg behaviour is the runtime's too.
            if mix["rotate"] != 0:
                r += (tr - r + off["rotation"]) * mix["rotate"]
            x += (tx - x + off["x"]) * mix["x"]
            y += (ty - y + off["y"]) * mix["y"]
            if mix["scale_x"] != 0 and sx != 0:
                sx = (sx + (tsx - sx + off["scale_x"]) * mix["scale_x"]) / sx
            if mix["scale_y"] != 0 and sy != 0:
                sy = (sy + (tsy - sy + off["scale_y"]) * mix["scale_y"]) / sy
            if mix["shear_y"] != 0:
                shy += (tshy - shy + off["shear_y"]) * mix["shear_y"]
        self.set_applied(i, (x, y, r, sx, sy, shx, shy))


# ---------------------------------------------------------------- shortcuts

def setup_frame(ir, atlas=None, skin=None):
    return Evaluator(ir, atlas, skin).frame()


def frame(ir, pose=None, atlas=None, skin=None, **kw):
    return Evaluator(ir, atlas, skin).frame(pose, **kw)


def fold_count(ir, pose=None, skin=None, **kw):
    return Evaluator(ir, None, skin).folds(pose, **kw)["folds"]
