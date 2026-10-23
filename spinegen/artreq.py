"""Art request pack (SOTA §4.3 step 6 "request", §7 P3 "artreq"): what the
artist must paint, per problem, with a mask computed from the planned motion.

    python -m spinegen.artreq <rig dir> -o <out dir> [--bridge DIR] [--budget budget.json]
        [--joints joints.json] [--atlas x.atlas] [--quick]

WHY
  The budget (spinegen.budget) says how far each joint holds and names the
  gate that stops it; the bridge (motionkit) clamps a motion that wants more
  and writes an artreq.json item for a pose the art cannot reach. Neither
  tells the artist WHICH PIXELS to paint. SOTA §2 (補畫 row) asks for the
  mask to come from the motion: rasterise each occluder's SKINNED shape
  over every planned pose, take the union, subtract what was visible at
  rest, intersect with where the underlying layer can plausibly be. A
  rigid outline would miss what a weighted mesh does near a joint (the
  torso band stretching under the shoulder), so every pose here goes
  through spinegen.lbs (C1-pinned to spine-core) and spinegen.raster.

PLANNED MOTION (plan_poses)
  * targets (TARGETS): shoulders outward to SWAP_AT (105 deg, SOTA §7 P3:
    above it a raised-arm attachment swaps in) and inward to 30; elbows
    both ways to 120 (P2 open issue 2: 120 is where the crook is crushed);
    legs in the IK poses budget.ik_scan walks (crouch to 0.10 H with the
    hips shifted -40 / 0 / +40 px, a sideways step to 0.16 H, a lifted
    foot to 0.12 H). Each joint sweep turns that one bone with IK off, as
    the budget does; the leg poses keep the IK on (the ankle stays in its
    boot).
  * the bridge (bridge.json "budget.fit"): every animation's REQUESTED
    joint range per arm ("before", what the preset asked, not what the
    clamp delivered), as a grid over shoulder x elbow, clamped to the
    targets above (past them the answer is a swap attachment, not paint).

WHAT IS COLLECTED (Collector.add, per pose)
  reveal   texels of a layer U that a pose shows and that sat under an
           occluder O at rest (texel identity: frame.top_texel, carried to
           its rest pixel by art_gates.texel_home; the rest pixel must lie
           >= REVEAL_ERODE px inside O's rest coverage, so the 1-2 px rim
           where O's outline met U is not a reveal). This is "union of
           not-covered minus rest-visible" read in U's own texture, so U's
           skinning is exact; the "plausible area" is U's own art.
  extend   background where the layer should continue: G4 seam-gap pixels
           of each turned joint and G5 holes inside a mesh, carried back
           into the LOWER-drawn layer of the two (the one that can grow
           unseen: the torso under the sleeve, the forearm under the
           cuff) by the affine map of its nearest triangle (art_gates.Part
           .locate, least-outside), so the mask may lie outside the layer's
           current image (pad).
  squash   texels of mesh triangles whose posed / rest area falls below
           SQUASH (G2's single-triangle bound, 0.3; folded ones too): art
           there is crushed in the pose; the request is to move that art
           toward the limb's axis (a narrower cuff in the elbow crook).
  clean    budget.json "artreq" items (G6b: a shard of another layer's
           paint cut into this layer): mask = the carried duplicate's
           texels (art_gates.duplicates).
  attachment  the bridge's pose items (a second arm drawn in a pose the
           drawn arm cannot reach) and, for an arm with none, the raised
           arm above SWAP_AT: a guide mask of the limb's bones at the
           requested pose (capsules of the joints' halfwidths), on the
           character canvas.

PAINT CHECK (reveal)
  A revealed region is only a request when its paint is not finished. The
  monkey's layers were cut with an edge-colour smear under every occluder
  (cut_cast_layers.py UNDERLAY): each hidden texel repeats the colour of
  the nearest visible one. copied_frac is the share of revealed texels
  within SMEAR_DE of their nearest rest-visible texel of the same layer
  (beyond the first SMEAR_SKIP px, where painted continuations also
  match); >= SMEAR_FRAC reads as a smear. Flat fills read as copied too:
  the check is a filter for the artist's attention, not a judge, and every
  reveal above MIN_PX is listed (as "check" when it looks painted).

OUTPUT (<out>/)
  README.md     Traditional Chinese, for the artist: one section per item
  artreq.json   spinegen-artreq/1 items + mask/crop paths, poses, sign-off
  items/<nn>_<id>/mask.png     white = paint here, in the layer's own image
                               (grown by "pad" px where the mask leaves it)
                layer.png      the layer with the mask in magenta
                crop.png       rest | worst pose, the problem in magenta
                prompt.txt     image-edit prompt, English and Chinese
"""
import argparse
import json
import math
import os
import time

import numpy as np

from . import art_gates as ag
from . import budget as bud
from . import raster

SCHEMA = "spinegen-artreq/1"
SWAP_AT = 105.0         # deg: a raised arm swaps to an attachment above this (SOTA §7 P3 ②)
RAISE_TO = 165.0        # deg: the raised-arm attachment covers up to this
TARGETS = {"shoulder": {"out": SWAP_AT, "in": 30.0, "raise": RAISE_TO}, "elbow": {"out": 120.0, "in": 120.0}}
ARM_STEP = 5.0          # deg between swept poses
IK_POSES = {"crouch": {"H": (0.02, 0.04, 0.06, 0.08, 0.10), "dx": (-40.0, 0.0, 40.0)},
            "step": {"H": (0.04, 0.08, 0.12, 0.16)},
            "lift": {"H": (0.04, 0.08, 0.12)}}
BRIDGE_ELBOW_SAMPLES = 6
REVEAL_ERODE = 2        # px: a revealed texel's rest home lies this far inside the occluder's coverage
REVEAL_GROW = 2         # px: a paint mask grows this much (not onto rest-visible texels)
EXTEND_GROW = 4         # px: ... this much when a seam opens there (a gap of a few px is hard to hit)
MIN_PX = {"reveal": 64, "extend": 16, "squash": 32}   # texels (scale 1) for an item
SQUASH = ag.TRI_RANGE[0]
SMEAR_DE = 3.0          # CIELAB: a revealed texel this close to its nearest visible texel repeats it
SMEAR_SKIP = 3          # px: the first ring past the visible edge is not judged
SMEAR_FRAC = 0.5
PAD = 96                # px of room around a layer image for masks that leave it
CROP_MARGIN = 36
MAGENTA = np.array([255, 0, 255], float)
CYAN = np.array([0, 200, 255], float)


# ---------------------------------------------------------------- names

PART_NAMES = {  # template bone base name -> (zh, en)
    "torso": ("軀幹（上衣）", "torso (shirt)"), "hips": ("骨盆", "hips"), "head": ("頭", "head"),
    "neck": ("脖子", "neck"), "upperarm": ("上臂（袖子）", "upper arm (sleeve)"),
    "forearm": ("前臂與手", "forearm and hand"), "hand": ("手", "hand"),
    "thigh": ("大腿（褲管上段）", "thigh (upper trouser leg)"), "shin": ("小腿（褲管下段）", "shin (lower trouser leg)"),
    "foot": ("腳（靴子）", "foot (boot)"), "prop": ("道具", "prop"), "clav": ("鎖骨", "clavicle")}
JOINT_NAMES = {"shoulder": ("肩", "shoulder"), "elbow": ("肘", "elbow"), "hip": ("髖", "hip"),
               "knee": ("膝", "knee"), "ankle": ("腳踝", "ankle"), "wrist": ("手腕", "wrist"),
               "clav": ("鎖骨", "clavicle")}
CENTRAL = {"torso", "hips", "head", "neck", "prop", None}
MOVE_NAMES = {"crouch": ("蹲下", "crouch"), "step": ("往旁邊跨步", "side step"), "lift": ("抬腳", "lifted foot")}


def _base(name):
    for suf in ("_l", "_r"):
        if name.endswith(suf):
            return name[:-2]
    return name


class Names:
    """Human names for slots and joints: the template role of the slot's
    bone, and the side of the image it is on (screen left/right: a front
    view's own left is the image's right, so the image side is what an
    artist looks at)."""

    def __init__(self, ir, rest):
        self.ir = ir
        self.slot_bone = {s["name"]: s["bone"] for s in ir["slots"]}
        by = {b["name"]: b for b in ir["bones"]}
        self.by = by
        cx = []
        for s, g in rest.geoms.items():
            cx.append(g.xy[:, 0].mean())
        root = by.get("hips") or ir["bones"][0]
        world = rest.renderer.ev.world_dict(rest.world)
        self.mid = float(world[root["name"]][4]) if root["name"] in world else float(np.mean(cx))
        self.cx = {s: float(g.xy[:, 0].mean()) for s, g in rest.geoms.items()}
        self.bx = {n: float(m[4]) for n, m in world.items()}

    def _role(self, bone):
        b = bone
        while b is not None:
            base = _base(b)
            if base in PART_NAMES:
                return base
            b = self.by[b]["parent"] if b in self.by else None
        return None

    def side(self, slot):
        if self._role(self.slot_bone.get(slot)) in CENTRAL:
            return ("", "")
        x = self.cx.get(slot)
        if x is None or abs(x - self.mid) < 8:
            return ("", "")
        return ("畫面左側", "image-left") if x < self.mid else ("畫面右側", "image-right")

    def bone_side(self, bone):
        x = self.bx.get(bone)
        if x is None or abs(x - self.mid) < 8:
            return ("", "")
        return ("畫面左側", "image-left") if x < self.mid else ("畫面右側", "image-right")

    def slot(self, slot):
        role = self._role(self.slot_bone.get(slot))
        zh, en = PART_NAMES.get(role, (slot, slot))
        sz, se = self.side(slot)
        return (f"{sz}{zh}" if sz else zh), (f"{se} {en}" if se else en)

    def joint(self, j):
        kind = joint_kind(j)
        zh, en = JOINT_NAMES.get(kind, (j["name"], j["name"]))
        side = j["name"].rsplit("_", 1)[-1] if j["name"][-2:] in ("_l", "_r") else None
        return zh, en, side


def joint_kind(j):
    jid = j.get("id") or j["name"]
    return _base(jid.split(".")[-1]) if "." in jid else _base(j["name"])


# ---------------------------------------------------------------- poses

class Pose:
    __slots__ = ("label", "group", "source", "anim", "joints", "ik", "order", "primary")

    def __init__(self, label, group, source, joints=None, ik=None, anim=None, order=0, primary=None):
        self.label, self.group, self.source, self.anim = label, group, source, anim
        self.joints = dict(joints or {})
        self.ik = ik
        self.order = order
        self.primary = primary

    def to_dict(self):
        d = {"label": self.label, "group": self.group, "source": self.source}
        if self.anim:
            d["anim"] = self.anim
        if self.joints:
            d["joints"] = {k: round(v, 2) for k, v in self.joints.items()}
        if self.ik:
            d["ik"] = dict(self.ik)
        return d


def outward_sign(ir, j, template_sides=None):
    """+1 when a positive turn of the joint's bone swings its limb away from
    the body's midline (the template's sides say so for a template rig;
    otherwise measured: the limb's farthest descendant tip moves out)."""
    if template_sides:
        side = j["name"].rsplit("_", 1)[-1]
        s = template_sides.get(side, {}).get("outward")
        if s in (1, -1):
            return s
    from . import lbs
    ev = lbs.Evaluator(ir)
    by = {b["name"]: b for b in ir["bones"]}
    kids = {}
    for b in ir["bones"]:
        kids.setdefault(b["parent"], []).append(b["name"])
    tips, todo = [], [j["bone"]]
    while todo:
        n = todo.pop()
        tips.append(n)
        todo += kids.get(n, [])
    w0 = ev.world_dict(ev.world())
    mid = w0[ir["bones"][0]["name"]][4]
    off = {c["name"]: {"mix": 0.0} for c in ir.get("ik", [])}

    def spread(a):
        w = ev.world_dict(ev.world({j["bone"]: {"rotation": by[j["bone"]]["rotation"] + a}}, off))
        return max(abs(w[t][4] - mid) for t in tips)
    return 1 if spread(30.0) > spread(-30.0) else -1


def plan_poses(ir, joints, bridge=None, targets=TARGETS, step=ARM_STEP, ik_poses=IK_POSES, template_sides=None,
               quick=False):
    """[Pose]: the planned motion (see the module doc)."""
    poses = []
    jb = {j["name"]: j for j in joints}
    signs = {}
    for j in joints:
        kind = joint_kind(j)
        if kind not in targets:
            continue
        sgn = outward_sign(ir, j, template_sides)
        signs[j["name"]] = sgn
        for way in ("out", "in"):
            top = targets[kind].get(way)
            if not top:
                continue
            d = sgn if way == "out" else -sgn
            n = int(round(top / step))
            if quick:
                n = max(1, n // 3)
            for k in range(1, n + 1):
                a = d * top * k / n
                poses.append(Pose(f"{j['name']} {a:+g}", f"{j['name']} {'+' if d > 0 else '-'}", "target",
                                  {j["name"]: a}, order=k, primary=j["name"]))
    if bridge:
        fit = (bridge.get("budget") or {}).get("fit") or {}
        groups = (bridge.get("budget") or {}).get("groups") or {}
        for anim, f in fit.items():
            before = (f.get("before") or {}).get("joints") or {}
            for gname, g in groups.items():
                arm = [n for n in g.get("joints", []) if n in jb and joint_kind(jb[n]) in ("shoulder", "elbow")]
                if len(arm) != 2:
                    continue
                sh = next(n for n in arm if joint_kind(jb[n]) == "shoulder")
                el = next(n for n in arm if joint_kind(jb[n]) == "elbow")
                rs, re_ = before.get(sh), before.get(el)
                if not rs or not re_:
                    continue

                def clamp(n, v):
                    kind = joint_kind(jb[n])
                    t = targets.get(kind, {})
                    s = signs.get(n, 1)
                    hi_out, hi_in = t.get("out", 180.0), t.get("in", 180.0)
                    if v * s > 0:
                        return math.copysign(min(abs(v), hi_out), v)
                    return math.copysign(min(abs(v), hi_in), v)
                s_lo, s_hi = clamp(sh, rs["max_minus"]), clamp(sh, rs["max_plus"])
                e_lo, e_hi = clamp(el, re_["max_minus"]), clamp(el, re_["max_plus"])
                if max(abs(s_lo), abs(s_hi), abs(e_lo), abs(e_hi)) < 10.0:
                    continue  # a small motion: the joint sweeps above cover it
                ne = 3 if quick else BRIDGE_ELBOW_SAMPLES
                svals = sorted({round(s_lo, 2), round(s_hi, 2)})
                evals = sorted({round(e_lo + (e_hi - e_lo) * k / (ne - 1), 2) for k in range(ne)})
                k = 0
                for sv in svals:
                    for ev_ in evals:
                        k += 1
                        poses.append(Pose(f"{anim}: {sh} {sv:+g}, {el} {ev_:+g}", f"{anim} {gname}", "bridge",
                                          {sh: sv, el: ev_}, anim=anim, order=k, primary=el))
    legs = bud.ik_legs(ir)
    if legs and ik_poses:
        H = float(ir.get("H") or 1.0)
        c = ik_poses.get("crouch")
        if c:
            for dx in c["dx"]:
                for k, h in enumerate(c["H"] if not quick else c["H"][1::2], 1):
                    poses.append(Pose(f"crouch {h:g}H dx{dx:+g}", f"crouch dx{dx:+g}", "target",
                                      ik={"move": "crouch", "amount": h * H, "dx": dx, "H": h}, order=k))
        for leg in legs:
            st = ik_poses.get("step")
            if st:
                for sign in (1, -1):
                    for k, h in enumerate(st["H"] if not quick else st["H"][1::2], 1):
                        poses.append(Pose(f"step {leg['side']}{'+' if sign > 0 else '-'} {h:g}H",
                                          f"step {leg['side']}{'+' if sign > 0 else '-'}", "target",
                                          ik={"move": "step", "leg": leg["name"], "amount": sign * h * H, "H": h},
                                          order=k))
            li = ik_poses.get("lift")
            if li:
                for k, h in enumerate(li["H"] if not quick else li["H"][1::2], 1):
                    poses.append(Pose(f"lift {leg['side']} {h:g}H", f"lift {leg['side']}", "target",
                                      ik={"move": "lift", "leg": leg["name"], "amount": h * H, "H": h}, order=k))
    return poses, signs


# ---------------------------------------------------------------- rendering

class Scene:
    """The rig on one canvas: rest frame, posed frames, per-slot texture
    facts the collector needs."""

    def __init__(self, ir, images, joints, canvas=None, skin=None):
        self.ir = ir
        self.images = images
        self.joints = joints
        self.jb = {j["name"]: j for j in joints}
        self.canvas = canvas or bud.scan_canvas(ir, images, 180.0, skin)
        self.r = raster.Renderer(ir, images, skin=skin, canvas=self.canvas, missing="skip")
        self.ev = self.r.ev
        self.off = {c["name"]: {"mix": 0.0} for c in ir.get("ik", [])}
        self.rest = self.r.frame(state=self.off)
        self.by = {b["name"]: b for b in ir["bones"]}
        self.legs = bud.ik_legs(ir)
        self.leg_probe = bud.LegProbe(ir, images, joints, self.legs, skin, self.canvas) if self.legs else None
        self.rank = {s: k for k, s in enumerate(self.rest.slots)}
        self.names = Names(ir, self.rest)
        self._eroded = {}

    def frame(self, pose):
        if pose.ik:
            leg = next((l for l in self.legs if l["name"] == pose.ik.get("leg")), None)
            p = self.leg_probe.pose_of(pose.ik["move"], leg, pose.ik["amount"], pose.ik.get("dx", 0.0))
            if any(not np.all(np.isfinite(list(v.values()))) for v in p.values()):
                return None
            return self.r.frame(world=self.ev.world(p))
        p = {}
        for n, a in pose.joints.items():
            b = self.jb[n]["bone"]
            p[b] = {"rotation": self.by[b]["rotation"] + a}
        return self.r.frame(p, self.off)

    def turned_joints(self, pose):
        if pose.ik:
            names = {l[k] for l in self.legs for k in ("thigh", "shin", "foot") if l[k]}
            return [j for j in self.joints if j["bone"] in names]
        return [self.jb[n] for n in pose.joints]

    def occluder_core(self, o_rank):
        """Rest pixels the occluder (by rank) tops, eroded REVEAL_ERODE px."""
        import cv2
        if o_rank not in self._eroded:
            m = (self.rest.top == o_rank).astype(np.uint8)
            k = 2 * REVEAL_ERODE + 1
            self._eroded[o_rank] = (cv2.erode(m, np.ones((k, k), np.uint8)) > 0).reshape(-1)
        return self._eroded[o_rank]

    def tex_shape(self, slot):
        t = self.r.texture(self.rest.geoms[slot].tex_key)
        return t.h, t.w

    def visible_texels(self, slot):
        """(th, tw) bool: the slot's texels that show at rest (topmost)."""
        g = self.rest.geoms[slot]
        th, tw = self.tex_shape(slot)
        home = ag.texel_home(self.rest, slot)
        top = self.rest.top.reshape(-1)
        out = np.zeros(th * tw, bool)
        ok = home >= 0
        out[ok] = top[home[ok]] == self.rank[slot]
        return out.reshape(th, tw)


# ---------------------------------------------------------------- collecting

class Item:
    def __init__(self, kind, slot, other, shape):
        self.kind, self.slot, self.other = kind, slot, other
        th, tw = shape
        self.shape = shape
        self.mask = np.zeros((th + 2 * PAD, tw + 2 * PAD), bool)
        self.px = {}          # pose index -> px
        self.extra = {}

    @property
    def key(self):
        return (self.kind, self.slot, self.other)

    def add_tex(self, pose_i, tex_xy):
        """tex_xy (n, 2) texture px coords (col, row), may leave the image."""
        if not len(tex_xy):
            return
        j = np.floor(tex_xy[:, 0]).astype(np.int64) + PAD
        i = np.floor(tex_xy[:, 1]).astype(np.int64) + PAD
        ok = (i >= 0) & (i < self.mask.shape[0]) & (j >= 0) & (j < self.mask.shape[1])
        self.mask[i[ok], j[ok]] = True
        self.px[pose_i] = self.px.get(pose_i, 0) + int(ok.sum())

    def add_flat(self, pose_i, flat):
        th, tw = self.shape
        y, x = np.divmod(np.asarray(flat, np.int64), tw)
        self.add_tex(pose_i, np.stack([x + 0.5, y + 0.5], 1))


class Collector:
    def __init__(self, scene):
        self.s = scene
        self.items = {}

    def item(self, kind, slot, other):
        key = (kind, slot, other)
        if key not in self.items:
            self.items[key] = Item(kind, slot, other, self.s.tex_shape(slot))
        return self.items[key]

    def add(self, pi, pose, fr):
        self._reveal(pi, fr)
        self._extend(pi, pose, fr)
        if not pose.ik:
            self._squash(pi, pose, fr)

    def _reveal(self, pi, fr):
        s = self.s
        rest = s.rest
        top = fr.top.reshape(-1)
        tex = fr.top_texel.reshape(-1)
        rtop = rest.top.reshape(-1)
        for u in np.unique(top[top >= 0]):
            uname = fr.slots[u]
            if uname not in s.rank:
                continue
            p = np.nonzero(top == u)[0]
            home = ag.texel_home(rest, uname)
            q = home[tex[p]]
            ok = q >= 0
            p, q, t = p[ok], q[ok], tex[p][ok]
            occ = rtop[q]
            hid = occ > s.rank[uname]
            for o in np.unique(occ[hid]):
                k = hid & (occ == o)
                k[k] = s.occluder_core(int(o))[q[k]]
                if k.sum():
                    self.item("reveal", uname, rest.slots[o]).add_flat(pi, np.unique(t[k]))

    def _extend(self, pi, pose, fr):
        s = self.s
        for j in s.turned_joints(pose):
            res = ag.seam_gap(s.rest, fr, j, masks=True)
            if res.get("skipped") or not res["value"]:
                continue
            m = res["masks"]
            i0, i1, j0, j1 = m["box"]
            ii, jj = np.nonzero(m["gap"])
            pts = fr.canvas.to_world(np.stack([jj + j0 + 0.5, ii + i0 + 0.5], 1))
            jr = ag.joint_rest(s.rest, j)
            parts = [x for x in jr.upper_slots + jr.lower_slots if x in fr.geoms]
            if not parts:
                continue
            under = min(parts, key=lambda x: s.rank[x])
            self.item("extend", under, j["name"]).add_tex(pi, self.to_tex(fr, under, pts))
        h = ag.holes(s.rest, fr, masks=True)
        if not h["pass"]:
            import cv2
            n, lab = cv2.connectedComponents(h["masks"]["holes"].astype(np.uint8), connectivity=8)
            for k in range(1, n):
                ii, jj = np.nonzero(lab == k)
                owner = self.mesh_owner(fr, ii, jj)
                if owner is None:
                    continue
                pts = fr.canvas.to_world(np.stack([jj + 0.5, ii + 0.5], 1))
                self.item("extend", owner, "hole").add_tex(pi, self.to_tex(fr, owner, pts))

    def mesh_owner(self, fr, ii, jj, need=0.5):
        """The slot whose geometry (ignoring texture alpha) covers >= need of
        a hole: a transparent patch inside a layer's mesh, the layer's own
        unpainted area. A hole no mesh covers is open space the limbs
        enclosed (no layer to paint)."""
        c = fr.canvas
        flat = ii * c.W + jj
        best, best_n = None, 0
        for sname, g in fr.geoms.items():
            pix, _, _ = raster.raster_triangles(c.to_px(g.xy), g.tris, c.shape)
            n = int(np.isin(flat, pix).sum())
            if n > best_n:
                best, best_n = sname, n
        return best if best_n >= need * len(flat) else None

    def to_tex(self, fr, slot, world_pts):
        """Frame world points -> the slot's texture px, by the affine map of
        its triangle holding each point (least outside one off the mesh)."""
        g = fr.geoms[slot]
        tris = np.asarray(g.tris, np.int64).reshape(-1, 3)
        th, tw = self.s.tex_shape(slot)
        soup = g.xy[tris]
        part = ag.Part.__new__(ag.Part)
        t, lam = ag.Part.locate(part, soup, world_pts)
        uv = np.einsum("nk,nkd->nd", lam, np.asarray(g.uv, float)[tris][t])
        return uv * [tw, th]

    def _squash(self, pi, pose, fr):
        s = self.s
        moved = ag.moved_slots(s.rest, fr)
        for sname in moved:
            g, g0 = fr.geoms.get(sname), s.rest.geoms.get(sname)
            if g is None or g0 is None or g.att["type"] not in ("mesh", "linkedmesh"):
                continue
            tris = np.asarray(g.tris, np.int64).reshape(-1, 3)
            a0 = _areas(g0.xy, tris)
            a1 = _areas(g.xy, tris)
            big = np.abs(a0) > 1.0
            ratio = np.where(big, a1 / np.where(big, a0, 1.0), 1.0)
            bad = big & (ratio < SQUASH)
            if not bad.any():
                continue
            th, tw = s.tex_shape(sname)
            uvp = np.asarray(g.uv, float) * [tw, th]
            pix, _, _ = raster.raster_triangles(uvp, tris[bad], (th, tw))
            if len(pix):
                a = pose.joints.get(pose.primary, 0.0)
                self.item("squash", sname, f"{pose.primary} {'+' if a >= 0 else '-'}").add_flat(pi, pix)


def _areas(xy, tris):
    p = xy[tris]
    return 0.5 * ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                  - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))


def sweep(scene, poses, progress=None):
    col = Collector(scene)
    t0 = time.time()
    for i, pose in enumerate(poses):
        fr = scene.frame(pose)
        if fr is None:
            continue
        col.add(i, pose, fr)
        if progress and (i % 25 == 0 or i == len(poses) - 1):
            progress(f"{i + 1}/{len(poses)} poses ({time.time() - t0:.0f} s)")
    return col


# ---------------------------------------------------------------- paint check

def _lab_img(rgb):
    return ag._lab(np.asarray(rgb, np.float32).reshape(-1, 3) / 255.0)


def paint_check(scene, slot, mask_tex):
    """{"copied_frac", "judged_px", "mean_de"}: how much of the revealed paint
    repeats the nearest rest-visible texel of the same layer (a smear)."""
    import cv2
    th, tw = scene.tex_shape(slot)
    img = scene.images.get(scene.rest.geoms[slot].tex_key)
    if img is None:
        return {"copied_frac": None, "judged_px": 0}
    img = np.asarray(img)
    vis = scene.visible_texels(slot) & (img[..., 3] >= 128)
    m = mask_tex & (img[..., 3] >= 128) & ~vis
    if not vis.any() or not m.any():
        return {"copied_frac": None, "judged_px": 0}
    dist, lab = cv2.distanceTransformWithLabels((~vis).astype(np.uint8), cv2.DIST_L2, 5,
                                                labelType=cv2.DIST_LABEL_PIXEL)
    vi, vj = np.nonzero(vis)
    # labels index the zero pixels in scan order
    order = np.zeros(lab.max() + 1, np.int64)
    zi = np.nonzero((~vis).astype(np.uint8).reshape(-1) == 0)[0]
    lab_at = lab.reshape(-1)[zi]
    order[lab_at] = zi
    sel = m & (dist > SMEAR_SKIP)
    if not sel.any():
        return {"copied_frac": None, "judged_px": 0}
    flat = np.nonzero(sel.reshape(-1))[0]
    near = order[lab.reshape(-1)[flat]]
    rgb = img[..., :3].reshape(-1, 3)
    de = np.linalg.norm(_lab_img(rgb[flat]) - _lab_img(rgb[near]), axis=1)
    return {"copied_frac": round(float((de < SMEAR_DE).mean()), 3), "judged_px": int(len(flat)),
            "mean_de": round(float(de.mean()), 2)}


# ---------------------------------------------------------------- items

def _first_last(item, poses):
    """Per group of poses: the first pose (in sweep order) where the item
    reaches its MIN_PX, and the group's largest px."""
    need = MIN_PX.get(item.kind, 16)
    by = {}
    for pi, n in item.px.items():
        g = poses[pi].group
        e = by.setdefault(g, {"first": None, "max_px": 0, "worst": None})
        if n >= need and (e["first"] is None or poses[pi].order < poses[e["first"]].order):
            e["first"] = pi
        if n > e["max_px"]:
            e["max_px"], e["worst"] = n, pi
    return {g: e for g, e in by.items() if e["first"] is not None}


def _entry(it, poses):
    groups = _first_last(it, poses)
    worst = max(it.px, key=it.px.get)
    return {"kind": it.kind, "slot": it.slot, "other": it.other, "mask_px": int(it.mask.sum()),
            "max_px": int(it.px[worst]), "worst_pose": worst,
            "uses": [{"group": g, "first": poses[e["first"]].to_dict(), "max_px": e["max_px"],
                      "worst": poses[e["worst"]].to_dict()}
                     for g, e in sorted(groups.items(), key=lambda ge: -ge[1]["max_px"])],
            "_item": it}


def grow(scene, it, px):
    """Dilate an item's mask by px, never onto texels visible at rest (the
    finished art the edit must not touch): a sweep samples poses every few
    degrees, and an edit tool blends a few px into the mask's rim."""
    import cv2
    if px <= 0:
        return
    th, tw = it.shape
    k = 2 * px + 1
    g = cv2.dilate(it.mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    vis = np.zeros_like(it.mask)
    vis[PAD:PAD + th, PAD:PAD + tw] = scene.visible_texels(it.slot)
    it.mask = it.mask | (g & ~vis)


def build_items(scene, col, poses, budget=None, bridge_items=None, signs=None, targets=TARGETS):
    """The request list: [dict] (spinegen-artreq/1 items plus pack fields)."""
    out = []
    kept = [it for it in col.items.values()
            if it.px and max(it.px.values()) >= MIN_PX.get(it.kind, 16) and _first_last(it, poses)]
    # one problem per (layer, occluder): what a pose reveals of the layer
    # under the occluder, and the seam it opens there (the layer must
    # continue under the occluder), are one place for the artist to paint
    paint = {}
    for it in kept:
        if it.kind == "reveal":
            paint.setdefault((it.slot, it.other), []).append(it)
        elif it.kind == "extend" and it.other in scene.jb:
            jr = ag.joint_rest(scene.rest, scene.jb[it.other])
            side = jr.lower_slots if it.slot in jr.upper_slots else jr.upper_slots
            side = [x for x in side if x in scene.rank and scene.rank[x] > scene.rank[it.slot]] or side
            occ = max(side, key=lambda x: scene.rank.get(x, -1)) if side else it.other
            paint.setdefault((it.slot, occ), []).append(it)
        elif it.kind == "extend":   # a hole inside the layer's own mesh
            paint.setdefault((it.slot, "hole"), []).append(it)
    for (slot, occ), parts in paint.items():
        it = Item("paint", slot, occ, parts[0].shape)
        for p in parts:
            it.mask |= p.mask
            for pi, n in p.px.items():
                it.px[pi] = it.px.get(pi, 0) + n
        th, tw = it.shape
        rev = [p for p in parts if p.kind == "reveal"]
        ext = [p for p in parts if p.kind == "extend"]
        it.extra = {"reveal": rev, "extend": ext}
        grow(scene, it, EXTEND_GROW if ext else REVEAL_GROW)
        d = _entry(it, poses)
        d["joints"] = sorted({p.other for p in ext})
        d["reveal_px"] = int(sum(p.mask.sum() for p in rev))
        d["gap_px"] = int(sum(p.mask.sum() for p in ext))
        d["outside_px"] = int(it.mask.sum() - it.mask[PAD:PAD + th, PAD:PAD + tw].sum())
        if rev:
            m = np.zeros_like(it.mask)
            for p in rev:
                m |= p.mask
            d["paint"] = paint_check(scene, slot, m[PAD:PAD + th, PAD:PAD + tw])
        cf = (d.get("paint") or {}).get("copied_frac")
        # a seam that opens needs paint whatever the paint looks like
        d["unfinished"] = bool(ext) or bool(cf is not None and cf >= SMEAR_FRAC)
        out.append(d)
    for it in kept:
        if it.kind == "squash":
            out.append(_entry(it, poses))
    # clean (G6b carried shards, from the budget)
    for b in (budget or {}).get("artreq", []):
        if b.get("kind") != "clean":
            continue
        occ, under = b["slot"], b["what"].split("paint of ", 1)[-1].split(" cut into", 1)[0]
        if occ not in scene.rest.geoms or under not in scene.rest.geoms:
            continue
        dup = ag.duplicates(scene.rest, under, occ)
        pix = np.nonzero(dup["carried"])[0]
        tex = np.unique(scene.rest.top_texel.reshape(-1)[pix])
        tex = tex[tex >= 0]
        if not len(tex):
            continue
        it = Item("clean", occ, under, scene.tex_shape(occ))
        it.add_flat(-1, tex)
        out.append({"kind": "clean", "slot": occ, "other": under, "mask_px": int(it.mask.sum()),
                    "max_px": int(b.get("max_visible_px") or 0), "worst_pose": None, "budget_item": b,
                    "uses": [{"group": k, "seen": v, "visible_from": b.get("visible_from", {}).get(k)}
                             for k, v in b.get("seen", {}).items()], "_item": it})
    # attachments: the bridge's, and a raised arm above SWAP_AT for an arm without one
    arms = {}
    for j in scene.joints:
        if joint_kind(j) in ("shoulder", "elbow"):
            arms.setdefault(j["name"].rsplit("_", 1)[-1], {})[joint_kind(j)] = j
    have = set()
    for b in bridge_items or []:
        if b.get("kind") != "attachment":
            continue
        side = b["id"].rsplit("_", 1)[-1]
        have.add(side)
        out.append({"kind": "attachment", "slot": None, "other": side, "bridge_item": b,
                    "pose": _bridge_pose(b, arms.get(side, {})), "uses": [{"group": b.get("anim"), "factor": b.get("factor")}]})
    raise_to = (targets.get("shoulder") or {}).get("raise")
    for side, a in sorted(arms.items()):
        if "shoulder" not in a or not raise_to:
            continue
        sh = a["shoulder"]
        sgn = (signs or {}).get(sh["name"], 1)
        out.append({"kind": "attachment", "slot": None, "other": side, "bridge_item": None,
                    "pose": {sh["name"]: sgn * raise_to}, "swap_at": sgn * SWAP_AT,
                    "uses": [{"group": f"{sh['name']} {'+' if sgn > 0 else '-'}", "from": sgn * SWAP_AT,
                              "to": sgn * raise_to}],
                    "also_bridge": side in have})
    return out


def _bridge_pose(b, arm):
    """The requested pose of a bridge attachment item: each arm joint at the
    end of its requested range that is farthest from setup (the bridge's
    "why" carries the elbow range; the shoulder comes from bridge.json)."""
    return dict(b.get("_pose") or {})


# ---------------------------------------------------------------- writing

def _save(path, arr):
    from PIL import Image
    Image.fromarray(np.asarray(arr).astype(np.uint8)).save(path)


def _on_white(rgba):
    rgba = np.asarray(rgba, float)
    a = rgba[..., 3:4] / 255.0
    return rgba[..., :3] * a + 255.0 * (1 - a)


def _checker(h, w, n=8):
    ii, jj = np.mgrid[0:h, 0:w]
    v = np.where(((ii // n) + (jj // n)) % 2 == 0, 236.0, 214.0)
    return np.stack([v, v, v], -1)


def _layer_views(scene, it):
    """(mask uint8 (h, w), overlay rgb, pad [l, t, r, b]) in the layer's image
    grown only where the mask leaves it."""
    th, tw = it.shape
    m = it.mask
    ii, jj = np.nonzero(m)
    i0, i1 = min(PAD, ii.min()), max(PAD + th, ii.max() + 1)
    j0, j1 = min(PAD, jj.min()), max(PAD + tw, jj.max() + 1)
    mm = m[i0:i1, j0:j1]
    h, w = mm.shape
    img = np.zeros((h, w, 4), float)
    src = scene.images.get(scene.rest.geoms[it.slot].tex_key)
    if src is not None:
        img[PAD - i0:PAD - i0 + th, PAD - j0:PAD - j0 + tw] = np.asarray(src, float)
    a = img[..., 3:4] / 255.0
    rgb = img[..., :3] * a + _checker(h, w) * (1 - a)
    ov = rgb.copy()
    ov[mm] = ov[mm] * 0.35 + MAGENTA * 0.65
    # outline the original image box when the mask leaves it
    pad = [int(PAD - j0), int(PAD - i0), int(j1 - PAD - tw), int(i1 - PAD - th)]
    if any(pad):
        y0, x0 = PAD - i0, PAD - j0
        ov[y0, x0:x0 + tw] = ov[y0 + th - 1, x0:x0 + tw] = [0, 120, 255]
        ov[y0:y0 + th, x0] = ov[y0:y0 + th, x0 + tw - 1] = [0, 120, 255]
    return (mm * 255).astype(np.uint8), ov, pad


def _highlight(scene, it, fr):
    """(H, W) bool frame pixels that show the problem of item `it`: its
    texels where the layer is drawn (on top, for paint), plus the seams and
    holes of the item's joints in that frame."""
    c = fr.canvas
    H, W = c.shape
    out = np.zeros(H * W, bool)
    f = fr.frags.get(it.slot)
    if f is not None and len(f.idx):
        th, tw = it.shape
        inside = it.mask[PAD:PAD + th, PAD:PAD + tw].reshape(-1)
        if it.kind == "paint":   # only what the sweep found revealed, not the grown rim
            inside = np.zeros(th * tw, bool)
            for p in it.extra.get("reveal", []):
                inside |= p.mask[PAD:PAD + th, PAD:PAD + tw].reshape(-1)
        hit = (f.texel >= 0) & inside[np.clip(f.texel, 0, th * tw - 1)] & (f.alpha >= 0.5)
        pix = f.idx[hit]
        if it.kind == "paint":
            pix = pix[fr.top.reshape(-1)[pix] == fr.slots.index(it.slot)]
        out[pix] = True
    out = out.reshape(H, W)
    if it.kind == "paint":
        for p in it.extra.get("extend", []):
            out |= _gaps(scene, fr, p.other)
    return out


def _gaps(scene, fr, where):
    out = np.zeros(fr.canvas.shape, bool)
    if where == "hole":
        h = ag.holes(scene.rest, fr, masks=True)
        if not h["pass"]:
            out |= h["masks"]["holes"]
        return out
    j = scene.jb.get(where)
    if j is None:
        return out
    res = ag.seam_gap(scene.rest, fr, j, masks=True)
    if res.get("skipped") or not res["value"]:
        return out
    i0, i1, j0, j1 = res["masks"]["box"]
    out[i0:i1, j0:j1] |= res["masks"]["gap"]
    return out


def _crop_box(mask, c, margin=CROP_MARGIN, min_size=180, center=None):
    ii, jj = np.nonzero(mask)
    if not len(ii):
        return None
    i0, i1, j0, j1 = ii.min(), ii.max() + 1, jj.min(), jj.max() + 1
    cy, cx = (i0 + i1) / 2, (j0 + j1) / 2
    hh = max((i1 - i0) / 2 + margin, min_size / 2)
    hw = max((j1 - j0) / 2 + margin, min_size / 2)
    if center is not None:
        cx, cy = center
    H, W = c.shape
    return (int(max(0, cy - hh)), int(min(H, cy + hh)), int(max(0, cx - hw)), int(min(W, cx + hw)))


def _upscale(img, k):
    return np.repeat(np.repeat(img, k, 0), k, 1) if k > 1 else img


def _fit(img, h, w):
    """Pad (white) or cut an (h', w', 3) panel to (h, w)."""
    out = np.full((h, w, 3), 255.0)
    hh, ww = min(h, img.shape[0]), min(w, img.shape[1])
    out[:hh, :ww] = img[:hh, :ww]
    return out


def render_crop(scene, it, pose, path):
    """rest | pose | pose with the problem in magenta (cyan: a carried
    shard). The rest panel is centred where the layer had the problem's
    pixels at rest (carried back through the layer's own mesh), so a
    crouch or a raised arm shows the same piece of art in both."""
    import cv2
    fr = scene.frame(pose) if pose is not None else scene.rest
    hl = _highlight(scene, it, fr)
    if hl is None or not hl.any():
        return None
    box = _crop_box(hl, fr.canvas)
    i0, i1, j0, j1 = box
    ii, jj = np.nonzero(hl)
    ctr = fr.canvas.to_world([[jj.mean() + 0.5, ii.mean() + 0.5]])
    g = fr.geoms.get(it.slot)
    if g is not None:
        part = ag.Part(scene.rest, [it.slot])
        back = part.carry_back(fr, ctr)
        cpx = scene.rest.canvas.to_px(back)[0]
    else:
        cpx = ((j0 + j1) / 2, (i0 + i1) / 2)
    hh, hw = (i1 - i0) / 2, (j1 - j0) / 2
    H, W = scene.rest.canvas.shape
    r0, r1 = int(max(0, cpx[1] - hh)), int(min(H, cpx[1] + hh))
    c0, c1 = int(max(0, cpx[0] - hw)), int(min(W, cpx[0] + hw))
    a = _fit(_on_white(scene.rest.rgba())[r0:r1, c0:c1], i1 - i0, j1 - j0)
    rest_hl = _highlight(scene, it, scene.rest) if it.kind in ("clean", "squash") else None
    if rest_hl is not None and rest_hl.any():
        r = _fit(np.repeat(rest_hl[r0:r1, c0:c1, None], 3, 2).astype(float), i1 - i0, j1 - j0)[..., 0] > 0
        r = r.astype(np.uint8)
        edge = (cv2.dilate(r, np.ones((3, 3), np.uint8)) > 0) & (r == 0)
        a[edge] = MAGENTA
    b = _on_white(fr.rgba())[i0:i1, j0:j1]
    bh = b.copy()
    m = hl[i0:i1, j0:j1]
    bh[m] = bh[m] * 0.3 + (CYAN if it.kind == "clean" else MAGENTA) * 0.7
    sep = np.full((a.shape[0], 6, 3), 255.0)
    img = np.concatenate([a, sep, b, sep, bh], 1)
    k = 2 if img.shape[0] < 300 else 1
    _save(path, _upscale(img, k))
    return {"box_px": [int(j0), int(i0), int(j1), int(i1)], "rest_box_px": [c0, r0, c1, r1], "scale": k}


def guide_mask(scene, pose_joints, side):
    """(frame, mask): the arm of `side` at the requested pose, as capsules
    of its bones with the joints' halfwidths (the outline a new attachment
    must fill), and the frame of the drawn arm forced into that pose."""
    import cv2
    p = Pose("guide", "guide", "attachment", pose_joints)
    fr = scene.frame(p)
    wd = scene.ev.world_dict(fr.world)
    c = fr.canvas
    m = np.zeros(c.shape, np.uint8)
    chain = [j for j in scene.joints if j["name"].endswith("_" + side) and joint_kind(j) in ("shoulder", "elbow")]
    chain.sort(key=lambda j: 0 if joint_kind(j) == "shoulder" else 1)
    pts, hws = [], []
    for j in chain:
        b = j["bone"]
        pts.append((wd[b][4], wd[b][5]))
        hws.append(float(j["band"].get("halfwidth") or 30.0))
    if chain:
        last = chain[-1]["bone"]
        kids = [x["name"] for x in scene.ir["bones"] if x["parent"] == last and not x["name"].startswith(("elbow_mid", "cuff"))]
        L = float(scene.by[last].get("length") or 0.0)
        for k in kids:
            pass
        tip = (wd[last][4] + wd[last][0] * L, wd[last][5] + wd[last][2] * L)
        pts.append(tip)
        hws.append(hws[-1] * 1.3)  # the fist is wider than the wrist
    px = c.to_px(np.asarray(pts, float))
    for a, b_, h in zip(px[:-1], px[1:], hws[1:]):
        cv2.line(m, tuple(int(v) for v in a), tuple(int(v) for v in b_), 1, int(2 * h))
    for q, h in zip(px, hws):
        cv2.circle(m, tuple(int(v) for v in q), int(h), 1, -1)
    return fr, m > 0


# ---------------------------------------------------------------- text

def _deg(a):
    return f"{a:+.0f}°"


def _pose_zh(scene, pd):
    if pd is None:
        return ""
    if "ik" in pd:
        ik = pd["ik"]
        zh = MOVE_NAMES.get(ik["move"], (ik["move"],))[0]
        s = f"{zh} {ik.get('H', 0):g}H"
        if ik["move"] == "crouch" and ik.get("dx"):
            s += f"（重心往{'右' if ik['dx'] > 0 else '左'}移 {abs(ik['dx']):g} px）"
        if ik["move"] in ("step", "lift"):
            leg = next((l for l in scene.legs if l["name"] == ik.get("leg")), None)
            sz = scene.names.bone_side(leg["thigh"])[0] if leg else ""
            way = ""
            if ik["move"] == "step" and leg:
                out = (ik.get("amount", 0) > 0) == (sz == "畫面右側")
                way = "往外" if out else "往內"
            s = f"{sz}的腿{way}{zh} {ik.get('H', 0):g}H"
        return s
    parts = []
    for n, a in pd.get("joints", {}).items():
        j = scene.jb.get(n)
        zh = JOINT_NAMES.get(joint_kind(j), (n,))[0] if j else n
        parts.append(f"`{n}`（{zh}）{_deg(a)}")
    s = "、".join(parts)
    if pd.get("anim"):
        s = f"動畫 `{pd['anim']}`：" + s
    return s


def _pose_en(scene, pd):
    if pd is None:
        return ""
    if "ik" in pd:
        ik = pd["ik"]
        s = f"{MOVE_NAMES.get(ik['move'], (None, ik['move']))[1]} {ik.get('H', 0):g} H"
        if ik["move"] == "crouch" and ik.get("dx"):
            s += f" (hips shifted {ik['dx']:+g} px)"
        return s
    s = ", ".join(f"{n} {_deg(a)}" for n, a in pd.get("joints", {}).items())
    return (f"animation '{pd['anim']}': " + s) if pd.get("anim") else s


def describe(scene, d):
    """Fill the human fields of one item: id, title_zh, what_zh, why_zh,
    layer, prompts."""
    nm = scene.names
    kind = d["kind"]
    if kind == "attachment":
        side = d["other"]
        b = d.get("bridge_item")
        arm_zh = "畫面左側" if side == "l" and _arm_side(scene, side) < 0 else ("畫面右側" if _arm_side(scene, side) > 0 else "")
        arm_en = "image-left" if arm_zh == "畫面左側" else ("image-right" if arm_zh else "")
        if b:
            d["id"] = b["id"]
            anim = b.get("anim")
            d["title_zh"] = f"另畫一隻{arm_zh}手臂：`{anim}` 的姿勢（舉起、肘彎、拳頭在肩膀上方）"
            d["what_zh"] = (f"動作 `{anim}` 要的手臂姿勢超出現在這張圖能彎的範圍：肘要轉到 {b['why'].split('asks ')[-1].split(' against')[0]}，"
                            f"這張圖的肘只撐得住 {b['why'].split('against ')[-1].split(';')[0]}。橋接器把這支動畫的手臂幅度縮成 {b.get('factor', 0):.0%}。")
            d["why_zh"] = "肘彎超過 θ_hard 以後網格會摺疊、肘窩破洞；這不是補幾個像素能解決的，要另畫一張那個姿勢的手臂，在動作跨過門檻的那一格換圖。"
            pose_en = "raised, elbow bent, fist above the shoulder"
        else:
            sh = next(n for n in d["pose"])
            d["id"] = f"arm_up_{side}"
            d["title_zh"] = f"另畫一隻{arm_zh}手臂：舉過頭（肩 {abs(SWAP_AT):.0f}–{RAISE_TO:.0f}°）"
            d["what_zh"] = (f"雙手舉過頭（cheer）要肩膀轉到 {RAISE_TO:.0f}°。現在的手臂是往下垂的圖，肩膀轉超過約 {SWAP_AT:.0f}° 以後袖子在肩部被拉成細條、腋下開口；"
                            f"規劃是在 {SWAP_AT:.0f}° 換成另一張舉起來的手臂。")
            d["why_zh"] = "SOTA §4.3 第 4 步（換圖）；P2 量到肩膀往外只撐得住 18–60°。" + \
                ("（這隻手也有上面那個 celebrate 姿勢的換圖需求，可以一起畫成同一組：一張舉直、一張肘彎。）" if d.get("also_bridge") else "")
            pose_en = "raised straight above the head"
        d["layer"] = None
        d["layer_zh"] = f"新的圖層（{arm_zh}上臂＋前臂＋手，一張完整的手臂）"
        d["prompt_en"] = (
            f"This is the full character on its canvas, and a guide image with a magenta area. Draw ONLY a new {arm_en} arm "
            f"(sleeve, forearm and fist, as one piece) in this pose: {pose_en}, filling the magenta guide area.\n"
            "- Same canvas size and position as the guide; do not move or scale the character.\n"
            "- Transparent background; output the arm only, nothing else of the body.\n"
            "- Same style, colours, outline weight and stripe pattern as the existing arm; the sleeve stripes follow the bent arm.\n"
            "- Paint the shoulder end as a rounded cap that reaches about 40 px into the torso, so it overlaps the shoulder seam.")
        d["prompt_zh"] = (
            f"這是角色的整張圖，另附一張洋紅色範圍的姿勢參考。請只畫一隻新的{arm_zh}手臂（袖子、前臂、拳頭畫成一張），姿勢：{'舉起、肘彎、拳頭在肩膀上方' if b else '整隻舉直過頭'}，填滿洋紅色的範圍。\n"
            "- 畫布大小、位置和參考圖完全相同，不要移動、縮放角色。\n"
            "- 透明背景，只輸出這隻手臂，身體其他部位都不要。\n"
            "- 畫風、顏色、描邊粗細、袖子條紋和現在的手臂一致；條紋順著彎曲的手臂走。\n"
            "- 肩膀那一端畫成圓頭，往軀幹裡多畫約 40 像素，讓它和肩膀接縫重疊。")
        return d
    slot = d["slot"]
    zh, en = nm.slot(slot)
    d["layer"] = slot
    d["layer_zh"] = f"`{slot}`（{zh}）"
    uses = d.get("uses", [])
    first = uses[0].get("first") if uses and "first" in uses[0] else None
    top = [u for u in uses if "first" in u][:3]
    when_zh = "；".join(_pose_zh(scene, u["worst"]) for u in top)
    when_en = "; ".join(_pose_en(scene, u["worst"]) for u in top)
    if kind == "paint":
        it = d["_item"]
        hole = d["other"] == "hole"
        ozh, oen = ("", "") if hole else nm.slot(d["other"])
        d["id"] = f"paint_{slot}__under_{d['other']}" if not hole else f"paint_{slot}__holes"
        unf = d.get("unfinished")
        cf = (d.get("paint") or {}).get("copied_frac")
        gaps = [g for g in d.get("joints", []) if g in scene.jb]
        gzh = "、".join(f"`{g}`（{JOINT_NAMES.get(joint_kind(scene.jb[g]), (g,))[0]}）" for g in gaps)
        if hole:
            d["title_zh"] = f"{zh}有沒畫到的透明處，動起來會露出來"
        elif gaps and d.get("reveal_px"):
            d["title_zh"] = f"{zh}在{ozh}底下：畫完被擋住的部分，並往下多畫一截"
        elif gaps:
            d["title_zh"] = f"{zh}往{ozh}底下多畫一截（補接縫）"
        else:
            d["title_zh"] = (f"{zh}被{ozh}擋住的地方要畫完" if unf else f"確認：{zh}被{ozh}擋住的地方")
        what = []
        if d.get("reveal_px"):
            what.append(f"靜止時這塊被 `{d['other']}`（{ozh}）蓋住，看不到；動起來會露出來（{d['reveal_px']} px）。")
            if cf is not None and cf >= SMEAR_FRAC:
                what.append(f"現在這裡不是完成的圖，是把旁邊看得到的顏色往裡面拉的抹色（{cf:.0%} 的像素和最近的可見像素同色），"
                            "露出來就是一片條紋拉長、或一團深色的色塊。")
            elif cf is not None:
                what.append(f"這裡看起來有畫（只有 {cf:.0%} 和最近的可見像素同色），請確認露出來時圖是對的。")
        if gaps:
            what.append(f"{gzh}轉的時候，{zh}和{ozh}之間拉開一條縫，露出背景（G4 接縫破洞，{d.get('gap_px', 0)} px）："
                        f"{zh}畫在下面，往{ozh}底下多畫一截，靜止時看不到，動起來就把縫補住。")
        if hole:
            what.append("動起來時這一層網格裡的透明處露出背景（G5 破洞）。")
        if d.get("outside_px"):
            what.append(f"其中 {d['outside_px']} px 在現在圖片的範圍外，`mask.png` 已經把圖片往外擴（見 pad）。")
        d["what_zh"] = "".join(what)
        d["why_zh"] = ("遮罩是把每個規劃的姿勢用 Spine 的蒙皮算一次（1:1 像素）："
                       + (f"露出來的 `{slot}` 紋理像素（被 `{d['other']}` 擋住、靜止時看不到的那些）取聯集；" if d.get("reveal_px") else "")
                       + ("接縫裡的背景像素用這一層最近的三角形帶回它自己的圖上，取聯集；" if gaps or hole else "")
                       + f"最後往外擴 {EXTEND_GROW if gaps else REVEAL_GROW} px（不碰靜止時看得到的像素）。")
        d["prompt_en"] = (
            f"This is one layer of a 2D cartoon character: the {en}, and a mask. The white area of the mask is part of this layer that is "
            + ("hidden behind the " + oen + " in the default pose and " if not hole else "")
            + f"becomes visible when the character moves ({when_en}).\n"
            "- Paint ONLY inside the mask; every pixel outside it must stay exactly as it is.\n"
            f"- Paint finished art there: continue the {en}'s pattern, colours, shading and outlines from the visible part across the "
            "masked area (the current pixels are a smear of edge colours or empty; do not keep them).\n"
            + ("- The mask may reach past the current image edge: grow the canvas by the given padding (left, top, right, bottom) and keep "
               "the original pixels where they are. The extension sits under the neighbouring part: no new outline along the old edge.\n"
               if gaps or d.get("outside_px") else "")
            + "- Keep the same canvas size and position; transparent background stays transparent outside the mask.")
        d["prompt_zh"] = (
            f"這是 2D 卡通角色的一個圖層：{zh}，另附一張遮罩。遮罩白色範圍是這一層"
            + (f"在預設姿勢被{ozh}擋住、" if not hole else "")
            + f"角色動起來時會露出來的部分（{when_zh}）。\n"
            "- 只畫遮罩內；遮罩外的每個像素都保持原樣。\n"
            f"- 畫成完成的圖：把{zh}看得到那部分的圖案、顏色、陰影、描邊延續過來。現在這裡是邊緣顏色拉長的抹色或空白，不要保留。\n"
            + ("- 遮罩可能超出現在的圖片邊緣：照 pad（左、上、右、下）擴大畫布，原本的像素留在原位。多畫的這一截藏在相鄰部件底下，舊的邊緣不要再畫描邊。\n"
               if gaps or d.get("outside_px") else "")
            + "- 畫布大小、位置不變；遮罩以外的透明處維持透明。")
    elif kind == "squash":
        j = scene.jb.get(d["other"].split(" ")[0])
        jzh = JOINT_NAMES.get(joint_kind(j), (d["other"],))[0] if j else d["other"]
        jen = JOINT_NAMES.get(joint_kind(j), (None, d["other"]))[1] if j else d["other"]
        d["id"] = f"narrow_{slot}__{d['other'].replace(' ', '').replace('+', 'p').replace('-', 'm')}"
        d["title_zh"] = f"{zh}在{jzh}彎的內側收窄"
        d["what_zh"] = (f"`{d['other']}`（{jzh}）彎到 {when_zh}，遮罩範圍內的圖被壓到原本面積的 {SQUASH:.0%} 以下（或翻面），"
                        f"畫面上是一團擠在{jzh}窩裡的皺褶。這些圖離關節的軸太遠：網格只能把它擠扁。")
        d["why_zh"] = (f"遮罩是規劃姿勢裡每個面積比 < {SQUASH:g} 的三角形（G2 單一三角形的下限）畫回這一層的圖上，取聯集。"
                       "要嘛把這裡的圖收窄、往軸心靠，要嘛把這一截畫成可以被壓住的樣子（例如袖口的羅紋在內側變窄、變薄）。")
        d["prompt_en"] = (
            f"This is one layer of a 2D cartoon character: the {en}, and a mask. When the {jen} bends ({when_en}) the art inside the "
            f"white area is crushed into the crook of the {jen}.\n"
            f"- Redraw ONLY inside the mask: make the {en} narrower on this inner side, pulling its outline toward the limb's centre line "
            "(for a sleeve: a narrower, thinner cuff on the inner side of the elbow).\n"
            "- Pixels you remove become transparent; keep every pixel outside the mask exactly as it is.\n"
            "- Same style, colours and outline weight; the new outline joins the old one smoothly at the mask's ends.")
        d["prompt_zh"] = (
            f"這是 2D 卡通角色的一個圖層：{zh}，另附一張遮罩。{jzh}彎的時候（{when_zh}），遮罩白色範圍內的圖會被擠進{jzh}窩。\n"
            f"- 只改遮罩內：把{zh}在這一側收窄，輪廓往肢體的中線靠（袖子的話：肘彎內側的袖口畫窄、畫薄）。\n"
            "- 去掉的像素改成透明；遮罩外的每個像素保持原樣。\n"
            "- 畫風、顏色、描邊粗細一致；新的輪廓在遮罩兩端和舊輪廓平順接上。")
    elif kind == "clean":
        uzh, uen = nm.slot(d["other"])
        b = d["budget_item"]
        d["id"] = b["id"]
        vis = [f"`{k}` {_deg(v)}" for k, v in (b.get("visible_from") or {}).items() if v is not None]
        d["title_zh"] = f"{zh}的圖裡夾著一塊{uzh}，要擦掉"
        d["what_zh"] = (f"`{slot}` 的圖片裡切進了一塊 `{d['other']}`（{uzh}）的顏料。靜止時它剛好疊在{uzh}上面，看不出來；"
                        f"這一層一動，那塊{uzh}就跟著走，浮在別的東西上（最多 {b.get('max_visible_px', 0)} px）。"
                        + (f"露出來的角度：{'、'.join(vis)}。" if vis else ""))
        d["why_zh"] = "預算掃描的 G6b（殘影）找到的：這塊和底下那層的顏料無縫接上、和自己這層的顏料接不上，所以是這一層帶著的碎片（`budget.json` artreq）。"
        d["prompt_en"] = (
            f"This is one layer of a 2D cartoon character: the {en}, and a mask. Inside the white area the layer contains a piece of the "
            f"{uen} that was cut out together with it by mistake.\n"
            f"- Remove that piece: inside the mask, keep only what belongs to the {en}. Where the {en} continues under the piece, "
            f"paint its own art (colours, fur or fabric, outline) there; everything else inside the mask becomes transparent.\n"
            "- Keep every pixel outside the mask exactly as it is; same canvas size and position.")
        d["prompt_zh"] = (
            f"這是 2D 卡通角色的一個圖層：{zh}，另附一張遮罩。遮罩白色範圍內，這張圖誤把一塊{uzh}一起切了進來。\n"
            f"- 把那一塊去掉：遮罩內只留屬於{zh}的東西。{zh}本身延伸到那塊底下的地方，補上它自己的圖（顏色、毛或布料、描邊）；其餘改成透明。\n"
            "- 遮罩外的每個像素保持原樣；畫布大小、位置不變。")
    return d


def _arm_side(scene, side):
    j = scene.jb.get(f"shoulder_{side}")
    if j is None:
        return 0
    x = j["band"].get("center", [0, 0])[0]
    return -1 if x < scene.names.mid else 1


# ---------------------------------------------------------------- pack

PRIORITY = {"clean": 0, "paint": 1, "squash": 2, "attachment": 3}


def make_pack(rig_dir, out, bridge=None, budget_path=None, joints_path=None, atlas=None, quick=False,
              progress=None, template_sides=None):
    """Load a rigbuild output folder (and the bridge's) and write the pack."""
    rig_path = os.path.join(rig_dir, "rig.json")
    ir = raster.load_rig(rig_path)
    if atlas is None:
        atlas = next((os.path.join(rig_dir, f) for f in sorted(os.listdir(rig_dir)) if f.endswith(".atlas")), None)
    images = raster.load_images(atlas)
    jpath = joints_path or os.path.join(rig_dir, "joints.json")
    joints = load_joints(jpath)
    budget_path = budget_path or os.path.join(rig_dir, "budget.json")
    budget = json.load(open(budget_path)) if os.path.exists(budget_path) else None
    bridge_doc, bridge_items = None, []
    if bridge:
        bj = os.path.join(bridge, "bridge.json") if os.path.isdir(bridge) else None
        ba = os.path.join(bridge, "artreq.json") if os.path.isdir(bridge) else bridge
        if bj and os.path.exists(bj):
            bridge_doc = json.load(open(bj))
        if ba and os.path.exists(ba):
            bridge_items = json.load(open(ba)).get("items", [])
    if template_sides is None and ir.get("template"):
        try:
            from . import template as tmpl
            template_sides = tmpl.load(ir["template"]).get("sides")
        except Exception:
            template_sides = None
    return pack(ir, images, joints, out, budget=budget, bridge_doc=bridge_doc, bridge_items=bridge_items,
                quick=quick, progress=progress, template_sides=template_sides,
                sources={"rig": os.path.abspath(rig_path), "budget": os.path.abspath(budget_path) if budget else None,
                         "bridge": os.path.abspath(bridge) if bridge else None})


def load_joints(path_or_obj):
    """budget.load_joints, keeping each entry's template "id" (arm_l.elbow)."""
    obj = path_or_obj
    if isinstance(obj, (str, os.PathLike)):
        with open(obj) as f:
            obj = json.load(f)
    joints = bud.load_joints(obj)
    raw = obj.get("joints", obj) if isinstance(obj, dict) else obj
    raw = [dict(v, name=v.get("name", k)) for k, v in raw.items()] if isinstance(raw, dict) else raw
    for j, r in zip(joints, raw):
        if "id" in r:
            j["id"] = r["id"]
    return joints


def pack(ir, images, joints, out, budget=None, bridge_doc=None, bridge_items=(), targets=TARGETS, step=ARM_STEP,
         ik_poses=IK_POSES, quick=False, progress=None, template_sides=None, sources=None, name=None):
    """Sweep the planned poses and write the pack to `out`; returns the
    artreq.json document."""
    t0 = time.time()
    joints = load_joints(joints) if not (isinstance(joints, list) and joints and "band" in joints[0]) else joints
    bridge_items = [dict(b) for b in (bridge_items or [])]
    scene = Scene(ir, images, joints)
    poses, signs = plan_poses(ir, joints, bridge_doc, targets=targets, step=step, ik_poses=ik_poses,
                              template_sides=template_sides, quick=quick)
    if progress:
        progress(f"{len(poses)} planned poses on a {scene.canvas.shape[1]}x{scene.canvas.shape[0]} canvas")
    col = sweep(scene, poses, progress)
    # the bridge's attachment poses: each arm joint at the end of its requested range farthest from setup
    fit = (bridge_doc or {}).get("budget", {}).get("fit", {})
    for b in bridge_items:
        jt = (fit.get(b.get("anim"), {}).get("before") or {}).get("joints", {})
        side = b["id"].rsplit("_", 1)[-1]
        pose = {}
        for n, e in jt.items():
            if n.endswith("_" + side) and n in scene.jb and joint_kind(scene.jb[n]) in ("shoulder", "elbow"):
                pose[n] = e["max_plus"] if abs(e["max_plus"]) >= abs(e["max_minus"]) else e["max_minus"]
        b["_pose"] = pose
    items = build_items(scene, col, poses, budget, bridge_items, signs, targets)
    items = [describe(scene, d) for d in items]
    # paint items that look finished are kept as checks, after the requests
    items.sort(key=lambda d: (PRIORITY.get(d["kind"], 9) + (4 if d["kind"] == "paint" and not d.get("unfinished") else 0),
                              -(d.get("max_px") or 0)))
    os.makedirs(os.path.join(out, "items"), exist_ok=True)
    doc_items = []
    for n, d in enumerate(items, 1):
        folder = os.path.join("items", f"{n:02d}_{d['id']}")
        os.makedirs(os.path.join(out, folder), exist_ok=True)
        rec = {k: v for k, v in d.items() if not k.startswith("_") and k not in ("bridge_item", "budget_item")}
        rec["folder"] = folder
        rec["status"] = "open"
        rec["signoff"] = {"by": None, "at": None}
        if d["kind"] == "attachment":
            fr, m = guide_mask(scene, d["pose"], d["other"])
            c = fr.canvas
            _save(os.path.join(out, folder, "mask.png"), m.astype(np.uint8) * 255)
            g = _on_white(fr.rgba())
            g[m] = g[m] * 0.55 + MAGENTA * 0.45
            box = _crop_box(m, c, margin=60, min_size=200)
            i0, i1, j0, j1 = box
            _save(os.path.join(out, folder, "crop.png"),
                  np.concatenate([_on_white(scene.rest.rgba())[i0:i1, j0:j1], np.full((i1 - i0, 6, 3), 255.0),
                                  g[i0:i1, j0:j1]], 1))
            rec["mask"] = {"file": f"{folder}/mask.png", "space": "canvas", "canvas": c.to_dict()}
            rec["crop"] = f"{folder}/crop.png"
            rec["pose"] = {k: round(v, 2) for k, v in d["pose"].items()}
            if d.get("bridge_item"):
                rec["bridge"] = {k: v for k, v in d["bridge_item"].items() if not k.startswith("_")}
        else:
            it = d["_item"]
            mask, ov, pad = _layer_views(scene, it)
            _save(os.path.join(out, folder, "mask.png"), mask)
            _save(os.path.join(out, folder, "layer.png"), ov)
            th, tw = it.shape
            rec["mask"] = {"file": f"{folder}/mask.png", "space": "layer", "layer_size": [tw, th], "pad": pad,
                           "px": int(it.mask.sum())}
            rec["layer_overlay"] = f"{folder}/layer.png"
            if d["kind"] == "clean":
                # the shard where it shows the most: the budget's first visible angle of its first joint
                vf = [(k, v) for k, v in (d["budget_item"].get("visible_from") or {}).items() if v is not None]
                pose = None
                if vf:
                    k, v = max(vf, key=lambda kv: abs(kv[1]))
                    jn = k.split(" ")[0]
                    a = math.copysign(max(abs(v) * 1.5, abs(v) + 10.0), v)
                    pose = Pose(f"{jn} {a:+g}", jn, "budget", {jn: a})
                cr = render_crop(scene, it, pose, os.path.join(out, folder, "crop.png"))
                rec["crop_pose"] = pose.to_dict() if pose else None
            else:
                pi = d["worst_pose"]
                ext = it.extra.get("extend") if it.kind == "paint" else None
                if ext:   # show the seam: the pose where it opens most
                    gp = {}
                    for p in ext:
                        for k, n in p.px.items():
                            gp[k] = gp.get(k, 0) + n
                    pi = max(gp, key=gp.get)
                pose = poses[pi]
                cr = render_crop(scene, it, pose, os.path.join(out, folder, "crop.png"))
                rec["crop_pose"] = pose.to_dict()
            rec["crop"] = f"{folder}/crop.png" if cr else None
            if cr:
                rec["crop_box_px"] = cr["box_px"]
            if d["worst_pose"] is not None:
                rec["worst_pose"] = poses[d["worst_pose"]].to_dict()
        with open(os.path.join(out, folder, "prompt.txt"), "w") as f:
            f.write("# English\n\n" + d["prompt_en"] + "\n\n# 中文\n\n" + d["prompt_zh"] + "\n")
        rec["prompt"] = f"{folder}/prompt.txt"
        doc_items.append(rec)
    src = sources or {}
    doc = {"schema": SCHEMA, "name": name or (os.path.basename(os.path.dirname(src["rig"])) if src.get("rig") else ir.get("name")),
           "rig": src.get("rig"), "budget": src.get("budget"), "bridge": src.get("bridge"),
           "poses": {"count": len(poses), "groups": sorted({p.group for p in poses}),
                     "targets": targets, "swap_at": SWAP_AT, "raise_to": RAISE_TO, "ik": ik_poses},
           "thresholds": {"min_px": MIN_PX, "squash": SQUASH, "smear_de": SMEAR_DE, "smear_frac": SMEAR_FRAC,
                          "reveal_erode_px": REVEAL_ERODE},
           "seconds": round(time.time() - t0, 1), "items": doc_items}
    with open(os.path.join(out, "artreq.json"), "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "README.md"), "w") as f:
        f.write(readme(scene, doc, items))
    return doc


def readme(scene, doc, items):
    L = []
    L.append(f"# 補畫單：{doc.get('name') or ''}\n")
    L.append("這份清單是程式量出來的：把骨架擺到所有規劃的姿勢（關節逐一轉到目標角度、腿用 IK 蹲／跨步／抬腳、"
             "加上動畫要的手臂姿勢），用 Spine 的蒙皮結果逐格算出哪些地方會露出來、破開或被擠扁。"
             "每一項都附：哪裡不對、哪一層、哪個關節／角度／動畫用到它、遮罩、問題的截圖，以及給生圖工具的提示詞（英文、中文）。\n")
    L.append("**怎麼看每一項的檔案**（在 `items/<編號>_<名稱>/`）：\n")
    L.append("- `mask.png`：白色 = 要畫的地方。大小就是那一層圖片的大小；遮罩超出原圖時，圖片往外擴，擴多少寫在 pad（左、上、右、下 px）。換圖的項目是整張角色畫布大小。")
    L.append("- `layer.png`：那一層的圖，遮罩範圍塗成洋紅色（棋盤格是透明處；藍框是原本圖片的邊界）。")
    L.append("- `crop.png`：左邊是靜止的樣子，右邊是問題最明顯的那個姿勢，問題的像素塗成洋紅色（夾帶的碎片是青色）。")
    L.append("- `prompt.txt`：給 Qwen-Image-Edit、GPT、Gemini 這類「遮罩式影像編輯」工具的提示詞。附上 `layer.png` 的原圖（不是洋紅色那張）和 `mask.png`。\n")
    L.append("**規則**：只改遮罩內；遮罩外一個像素都不要動（程式會比對遮罩外的色差，ΔE > 3 就退回）。"
             "AI 補的圖一律由美術看過、修過再交；簽核後在 `artreq.json` 的 `signoff` 填上名字和日期。\n")
    reqs = [d for d in items if not (d["kind"] == "paint" and not d.get("unfinished"))]
    checks = [d for d in items if d["kind"] == "paint" and not d.get("unfinished")]
    L.append(f"## 清單（{len(reqs)} 項要畫{f'，{len(checks)} 項請確認' if checks else ''}）\n")
    L.append("| # | 項目 | 圖層 | 用到它的姿勢 | 遮罩 px |")
    L.append("|---|---|---|---|---|")
    for n, (d, rec) in enumerate(zip(items, doc["items"]), 1):
        uses = d.get("uses", [])
        if d["kind"] == "attachment":
            u = (f"動畫 `{d['bridge_item']['anim']}`" if d.get("bridge_item") else
                 f"肩 {abs(SWAP_AT):.0f}–{RAISE_TO:.0f}°（舉過頭）")
        elif d["kind"] == "clean":
            u = "、".join(f"`{x['group']}` {_deg(x['visible_from'])}" for x in uses if x.get("visible_from") is not None) or "—"
        else:
            u = "；".join(_pose_zh(scene, x["worst"]) for x in uses[:3]) + ("…" if len(uses) > 3 else "")
        px = rec.get("mask", {}).get("px", "—")
        L.append(f"| {n} | [{d['title_zh']}](#{n}) | {d['layer_zh']} | {u} | {px} |")
    L.append("")
    for n, (d, rec) in enumerate(zip(items, doc["items"]), 1):
        L.append(f"<a id=\"{n}\"></a>\n## {n}. {d['title_zh']}\n")
        L.append(f"`{d['id']}` · 種類 `{d['kind']}` · 資料夾 `{rec['folder']}/`\n")
        L.append(f"- **哪裡不對**：{d['what_zh']}")
        L.append(f"- **哪一層**：{d['layer_zh']}")
        if d["kind"] == "attachment":
            pz = "、".join(f"`{k}` {_deg(v)}" for k, v in rec["pose"].items())
            L.append(f"- **用在哪裡**：{'動畫 `' + d['bridge_item']['anim'] + '`，' if d.get('bridge_item') else ''}姿勢 {pz}"
                     + (f"（橋接器目前把幅度縮成 {d['bridge_item'].get('factor', 0):.0%} 播）" if d.get("bridge_item") else
                        f"（在 {abs(SWAP_AT):.0f}° 換圖，回程留 10° 遲滯）"))
        elif d["kind"] == "clean":
            vis = [f"`{x['group']}` 在 {_deg(x['visible_from'])} 露出來" for x in d["uses"] if x.get("visible_from") is not None]
            L.append(f"- **用在哪裡**：每一個會移動這一層的動作；預算掃描裡 {'、'.join(vis) or '—'}")
        else:
            L.append("- **用在哪裡**（每組姿勢：第一次超過門檻的那一格 → 最多的那一格）：")
            for u in d["uses"]:
                a, b = _pose_zh(scene, u["first"]), _pose_zh(scene, u["worst"])
                L.append(f"  - {a}" + (f" → {b}" if b != a else "") + f"（{u['max_px']} px）")
        L.append(f"- **怎麼算的**：{d['why_zh']}")
        if d.get("paint") and d["paint"].get("copied_frac") is not None:
            p = d["paint"]
            L.append(f"- **圖的檢查**：露出來的 {p['judged_px']} px 裡 {p['copied_frac']:.0%} 和最近的可見像素同色（ΔE < {SMEAR_DE:g}），平均 ΔE {p['mean_de']:g}。"
                     + ("判為抹色，要畫。" if d.get("unfinished") else "看起來有畫，請確認。"))
        m = rec.get("mask", {})
        if m.get("space") == "layer":
            pad = m.get("pad", [0, 0, 0, 0])
            L.append(f"- **遮罩**：`{m['file']}`，{m['px']} px，在圖層 `{d['layer']}` 的圖上（{m['layer_size'][0]}×{m['layer_size'][1]}）"
                     + (f"，往外擴 pad 左 {pad[0]}、上 {pad[1]}、右 {pad[2]}、下 {pad[3]} px" if any(pad) else ""))
        elif m:
            L.append(f"- **遮罩**：`{m['file']}`（整張角色畫布，洋紅色範圍是新手臂要涵蓋的地方）")
        L.append("")
        if rec.get("layer_overlay"):
            L.append(f"![遮罩在圖層上]({rec['layer_overlay']})")
        if rec.get("crop"):
            L.append(f"![問題]({rec['crop']})")
        L.append("")
        L.append("**提示詞（English）**\n\n```\n" + d["prompt_en"] + "\n```\n")
        L.append("**提示詞（中文）**\n\n```\n" + d["prompt_zh"] + "\n```\n")
    L.append("---\n")
    L.append(f"規劃的姿勢 {doc['poses']['count']} 個（{len(doc['poses']['groups'])} 組）；"
             f"門檻：露出 ≥ {MIN_PX['reveal']} px、縫 ≥ {MIN_PX['extend']} px、擠扁 ≥ {MIN_PX['squash']} px；"
             f"抹色判定 ΔE < {SMEAR_DE:g} 的比例 ≥ {SMEAR_FRAC:.0%}。產生器：`python -m spinegen.artreq`（{doc['seconds']:.0f} 秒）。\n")
    return "\n".join(L)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="Write an art request pack (SOTA §7 P3) for a built rig.")
    ap.add_argument("rig_dir", help="rigbuild output: rig.json, joints.json, an atlas, budget.json")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--bridge", help="the bridge's output folder (bridge.json + artreq.json) or an artreq.json")
    ap.add_argument("--budget")
    ap.add_argument("--joints")
    ap.add_argument("--atlas")
    ap.add_argument("--quick", action="store_true", help="a third of the poses (tests, previews)")
    a = ap.parse_args(argv)
    doc = make_pack(a.rig_dir, a.out, a.bridge, a.budget, a.joints, a.atlas, a.quick, progress=print)
    for it in doc["items"]:
        print(f"{it['folder']}: {it['kind']} {it.get('layer') or ''} mask {it.get('mask', {}).get('px', '')}")
    print(f"{len(doc['items'])} items -> {a.out} ({doc['seconds']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
