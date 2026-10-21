"""Synthetic TEXTURED characters: fixtures for the renderer, the art gates
and the budget scanner that are ours to publish.

    python -m spinegen.synth_art OUTDIR [--mode rigid|joint] [--arms 1|2] [--legs 0|2]
        [--overlap PX] [--ghost] [--hole] [--shard] [--skin] [--no-helper] [--scale S]

WHY
  The art gates (SOTA §5 G4 seam gap, G5 holes, G6 ghost) judge PIXELS, so
  their fixtures need painted art, and third-party art may not enter the
  repo (SOTA §6). Everything here is generated from a few numbers: a striped
  torso and two-piece limbs (upper piece + lower piece meeting at a joint),
  painted so that each failure is measurable:
    * limbs carry horizontal stripes and the torso vertical ones, in other
      colours, so a copy of an arm left in the torso paint (a "ghost") is
      detectable by correlation and a clean torso is not;
    * `overlap` sets how far each piece runs past the joint under its
      partner: 0 with square ends makes a cut-out that tears open at once
      (G4), a long underlay hides the tear but swings out as a ghost band
      (G6a);
    * `ghost` paints the arm's own pattern into the torso under the arm
      (G6b), `hole` leaves a transparent disc inside the torso under the
      upper arm that only a moved arm reveals (G5);
    * `shard` is the opposite cut: the torso paint inside the forearm image
      where it lies over the torso, which correlates exactly as a ghost does
      but is carried away by the forearm (G6b "carried", an art request);
    * `skin` paints every piece in ONE palette (two skin tones; the stripe
      directions still differ) with no outline: a same-coloured body, where
      a ghost's or a shard's edge carries no colour step at all (the P2
      review's counter-example to reading G6b's owner off colour alone).
  `mode` decides the weights: "rigid" binds every piece 100% to its own bone
  (the old cut-out, the RIGID comparison mode); "joint" gives the pieces the
  same piecewise ramp across each joint band (upper -> half-angle helper ->
  lower, SOTA §2 weights), so both pieces of a joint deform alike and the
  seam cannot open.

  The meshes are plain grids with extra rows in the joint bands. That is on
  purpose: these fixtures test the gates, not the mesher (spinegen.mesh) or
  the weighting (spinegen.weights), and must not move when those change.

COORDINATES
  Built directly in IR world space (y up; ground at y = 0, the body centred
  on x = 0). Every limb hangs straight down at setup, so an image's columns
  run along world x and its rows along world -y; a piece's image is placed
  by the world position of its top-left corner. Side "l" is world x < 0.

build() returns (ir, images, joints):
  images {attachment name: (h, w, 4) uint8 RGBA};
  joints [{"name", "bone", "dirs", "band": {"center", "upper", "helper",
           "lower", "halfwidth", "axis"}}] in world coordinates — the
  spinegen.budget.scan input; each band is also the weights.compute joint.
"""
import argparse
import json
import math
import os

import numpy as np

from . import ir as irmod
from . import lbs

# palette (RGB): limbs and torso use different hues and stripe directions
TORSO_A, TORSO_B = (205, 70, 45), (245, 225, 185)
ARM_UP_A, ARM_UP_B = (40, 90, 200), (225, 230, 245)
ARM_LO_A, ARM_LO_B = (30, 150, 80), (235, 240, 200)
LEG_UP_A, LEG_UP_B = (90, 60, 140), (220, 210, 240)
LEG_LO_A, LEG_LO_B = (150, 100, 40), (240, 220, 170)
HEAD_A, HEAD_B = (120, 80, 50), (230, 190, 150)
SKIN_A, SKIN_B = (226, 172, 136), (212, 158, 122)  # skin=True: every piece, no outline (CIELAB 5.2 apart)
OUTLINE = (25, 25, 30)
STRIPE = 18          # stripe period, px
ROW_STEP = 16.0      # mesh row spacing along a limb, px
BAND_ROWS = 2        # extra rows on each side of a joint inside the band
RAMP_K = 1.25        # joint ramp half-length / limb half-width (SOTA: band 1.0-1.5 hw)


# ---------------------------------------------------------------- painting

def _supersampled(h, w, inside, ss=4):
    """Coverage in [0, 1] of the shape `inside(x, y)` (pixel coords, y down)
    per pixel, from ss x ss samples: anti-aliased edges like real art."""
    o = (np.arange(ss) + 0.5) / ss
    ys = (np.arange(h)[:, None] + o[None, :]).reshape(-1)
    xs = (np.arange(w)[:, None] + o[None, :]).reshape(-1)
    X, Y = np.meshgrid(xs, ys)
    return inside(X, Y).reshape(h, ss, w, ss).mean((1, 3))


def _stadium(cx, top, bot, hw, round_top, round_bot):
    """Inside-test of a vertical bar [top, bot] x [cx - hw, cx + hw] with
    optionally semicircular ends (radius hw), pixel coordinates."""
    def inside(X, Y):
        ok = (np.abs(X - cx) <= hw) & (Y >= top) & (Y <= bot)
        if round_top:
            cap = Y < top + hw
            ok &= ~cap | ((X - cx) ** 2 + (Y - top - hw) ** 2 <= hw * hw)
        if round_bot:
            cap = Y > bot - hw
            ok &= ~cap | ((X - cx) ** 2 + (Y - bot + hw) ** 2 <= hw * hw)
        return ok
    return inside


def _paint(cover, col_a, col_b, stripes, outline=2.5):
    """RGBA uint8: stripes of two colours (index map `stripes`, 0/1) inside
    `cover`, with a dark outline band along the shape's edge."""
    import cv2
    h, w = cover.shape
    rgb = np.where(stripes[..., None] == 0, np.array(col_a, float), np.array(col_b, float))
    solid = (cover >= 0.5).astype(np.uint8)
    dist = cv2.distanceTransform(np.pad(solid, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    edge = np.clip((outline + 0.5 - dist) / 1.0, 0, 1)[..., None] * (solid[..., None] > 0)
    rgb = rgb * (1 - edge) + np.array(OUTLINE, float) * edge
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = np.clip(np.round(rgb), 0, 255)
    out[..., 3] = np.clip(np.round(cover * 255), 0, 255)
    return out


def _stripe_index(h, w, horizontal, period=STRIPE, phase=0.0):
    yy, xx = np.mgrid[0:h, 0:w]
    t = (yy if horizontal else xx) + phase
    return (np.floor(t / (period / 2.0)).astype(np.int64) % 2)


# ---------------------------------------------------------------- pieces

class Piece:
    """One layer: an image placed in world space plus the bone it hangs on.
    `x0, y0` is the world position of the image's top-left corner."""

    def __init__(self, name, bone, image, x0, y0):
        self.name, self.bone, self.image = name, bone, image
        self.x0, self.y0 = float(x0), float(y0)
        self.h, self.w = image.shape[:2]

    def to_world(self, px):
        px = np.asarray(px, float).reshape(-1, 2)
        return np.stack([self.x0 + px[:, 0], self.y0 - px[:, 1]], 1)

    def to_px(self, xy):
        xy = np.asarray(xy, float).reshape(-1, 2)
        return np.stack([xy[:, 0] - self.x0, self.y0 - xy[:, 1]], 1)


def limb_piece(name, bone, ax, y_top, y_bot, hw, colors, round_top=True, round_bot=True, pad=2, outline=2.5):
    """A vertical limb piece centred on world x = ax from y_top down to y_bot."""
    w = int(math.ceil(2 * hw)) + 2 * pad
    h = int(math.ceil(y_top - y_bot)) + 2 * pad
    cx = w / 2.0
    cover = _supersampled(h, w, _stadium(cx, pad, h - pad, hw, round_top, round_bot))
    img = _paint(cover, colors[0], colors[1], _stripe_index(h, w, horizontal=True), outline)
    return Piece(name, bone, img, ax - cx, y_top + pad)


# ---------------------------------------------------------------- meshes

def grid_mesh(piece, rows_y, cols):
    """A grid mesh over the piece's image rectangle: world rows `rows_y`
    (descending) by `cols` columns; outline first (hull), then interior.
    Returns (world verts (n, 2), uvs (n, 2), tris (m, 3), hull)."""
    ny, nx = len(rows_y), cols
    xs = np.linspace(piece.x0, piece.x0 + piece.w, nx)
    loop = [(i, 0) for i in range(nx)] + [(nx - 1, j) for j in range(1, ny)] + \
           [(i, ny - 1) for i in range(nx - 2, -1, -1)] + [(0, j) for j in range(ny - 2, 0, -1)]
    order = list(loop) + [(i, j) for j in range(1, ny - 1) for i in range(1, nx - 1)]
    index = {ij: k for k, ij in enumerate(order)}
    verts = np.array([(xs[i], rows_y[j]) for i, j in order], float)
    px = piece.to_px(verts)
    uvs = np.stack([px[:, 0] / piece.w, px[:, 1] / piece.h], 1)
    tris = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b, c, d = index[(i, j)], index[(i + 1, j)], index[(i + 1, j + 1)], index[(i, j + 1)]
            tris += [(a, b, c), (a, c, d)]
    return verts, np.clip(uvs, 0.0, 1.0), np.array(tris, np.int64), len(loop)


def limb_rows(y_top, y_bot, joints_y, hw, band_k=RAMP_K, step=ROW_STEP, band_rows=BAND_ROWS):
    """Row positions (descending world y): a uniform step, plus rows at each
    joint and evenly inside its band, so the mesh has edges to bend on."""
    n = max(2, int(math.ceil((y_top - y_bot) / step)) + 1)
    ys = set(np.round(np.linspace(y_top, y_bot, n), 6).tolist())
    for yj in joints_y:
        b = band_k * hw
        for k in range(-band_rows, band_rows + 1):
            y = yj + b * k / band_rows
            if y_bot < y < y_top:
                ys.add(round(y, 6))
    ys = sorted(ys, reverse=True)
    # drop rows closer than 3 px to a neighbour (keeps triangles well shaped)
    out = [ys[0]]
    for y in ys[1:]:
        if out[-1] - y >= 3.0 or y == ys[-1]:
            if out[-1] - y < 3.0 and len(out) > 1:
                out[-1] = y
            else:
                out.append(y)
    return out


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ramp_weights(y, joint, band_k=RAMP_K):
    """Piecewise ramp across one joint band of a vertical limb, as
    [(bone, w)] per vertex: upper -> helper -> lower (helper None: upper ->
    lower). y: world y of the vertices; the lower bone hangs below."""
    hw, yj = joint["halfwidth"], joint["center"][1]
    s = (yj - np.asarray(y, float)) / (band_k * hw)       # -1 .. 1 across the band
    t = _smooth((s + 1) / 2)                               # 0 upper .. 1 lower
    rows = []
    for ti in t:
        if joint.get("helper"):
            a, b = min(1.0, 2 * ti), max(0.0, 2 * ti - 1)   # piecewise, never a 3-way mix
            row = [(joint["upper"], 1 - a), (joint["helper"], a - b), (joint["lower"], b)]
        else:
            row = [(joint["upper"], 1 - ti), (joint["lower"], ti)]
        rows.append([(n, float(w)) for n, w in row if w > 1e-9])
    return rows


def _normalised(row):
    s = sum(w for _, w in row)
    return [(n, w / s) for n, w in row]


# ---------------------------------------------------------------- rig building

class _Builder:
    """Bones from WORLD setup positions (converted to parent-local once)."""

    def __init__(self, name):
        self.ir = irmod.new_rig(name)
        self.world = {}
        self.rot = {}

    def bone(self, name, parent, wx, wy, wrot, length=0.0):
        if parent is None:
            b = irmod.new_bone(name, None, wx, wy, wrot, length)
        else:
            lx, ly = lbs.to_local(self.world[parent], wx, wy)
            b = irmod.new_bone(name, parent, lx, ly, wrot - self.rot[parent], length)
        self.ir["bones"].append(b)
        p = self.world.get(parent)
        self.world[name] = lbs.compose(p, b["x"], b["y"], b["rotation"], 1, 1, 0, 0, "normal")
        self.rot[name] = wrot
        return b


def _mesh_attachment(piece, rows_y, cols, weights):
    verts, uvs, tris, hull = grid_mesh(piece, rows_y, cols)
    att = {"type": "mesh", "name": piece.name, "path": piece.name, "color": "ffffffff",
           "width": float(piece.w), "height": float(piece.h), "uvs": uvs.tolist(), "triangles": tris.tolist(),
           "hull": int(hull), "edges": None, "vertices": verts.tolist(), "weights": None, "sequence": None}
    if weights is not None:
        att["weights"] = [[[n, w] for n, w in row] for row in weights(verts)]
    return att


def _region_attachment(builder, piece):
    """A region showing the whole piece image unrotated in world space."""
    cx, cy = piece.x0 + piece.w / 2.0, piece.y0 - piece.h / 2.0
    lx, ly = lbs.to_local(builder.world[piece.bone], cx, cy)
    return {"type": "region", "name": piece.name, "path": piece.name, "x": lx, "y": ly,
            "rotation": -builder.rot[piece.bone], "scale_x": 1.0, "scale_y": 1.0,
            "width": float(piece.w), "height": float(piece.h), "color": "ffffffff", "sequence": None}


def build(mode="joint", arms=1, legs=0, overlap=24.0, ghost=False, hole=False, helper=True, head=None,
          scale=1.0, arm_hw=40.0, leg_hw=45.0, round_ends=None, shard=False, skin=False):
    """(ir, images, joints) of a synthetic character; see the module doc.

    mode       "rigid" (each piece 100% on its bone) or "joint" (ramps)
    arms       1 (left only) or 2; legs 0 or 2; head defaults to legs > 0
    overlap    px each limb piece runs past a joint under its partner
    ghost      paint the arm pattern into the torso under the arm
    hole       a transparent disc in the torso under the upper arm
    shard      paint the torso into the forearm wherever the forearm lies over
               it (the opposite flat-image cut: the occluder carries a piece
               of the underlay, like a hand cut with the trousers behind it)
    helper     half-angle helper bones at elbows and knees (joint mode)
    scale      multiplies every length (1.0: ~600 px tall, ~920 with legs)
    round_ends rounded piece ends at the joints (default: overlap > 0)
    skin       one skin palette for every piece, no outlines (module doc)
    """
    if mode not in ("rigid", "joint"):
        raise ValueError("mode must be rigid or joint")
    s = float(scale)
    head = legs > 0 if head is None else head
    use_helper = helper and mode == "joint"
    round_ends = overlap > 0 if round_ends is None else round_ends
    ov = overlap * s
    ahw, lhw = arm_hw * s, leg_hw * s
    B = _Builder(f"synth_art_{mode}")
    pal = {k: ((SKIN_A, SKIN_B) if skin else v) for k, v in (
        ("torso", (TORSO_A, TORSO_B)), ("arm_up", (ARM_UP_A, ARM_UP_B)), ("arm_lo", (ARM_LO_A, ARM_LO_B)),
        ("leg_up", (LEG_UP_A, LEG_UP_B)), ("leg_lo", (LEG_LO_A, LEG_LO_B)), ("head", (HEAD_A, HEAD_B)))}
    line = 0.0 if skin else 2.5
    images, joints, slots_back, slots_front, atts = {}, [], [], [], {}

    # torso
    hip_y = 360 * s if legs else 40 * s
    tw, th = 150 * s, 400 * s
    B.bone("root", None, 0, 0, 0)
    B.bone("hip", "root", 0, hip_y, 0)
    B.bone("torso", "hip", 0, hip_y, 90, th)
    torso = _torso_piece(tw, th, hip_y, pal["torso"], line)
    sides = ["l", "r"][:arms]

    # arms: axis just inside the torso edge so the upper arm covers torso paint
    for side in sides:
        sg = -1 if side == "l" else 1
        ax = sg * (tw - 20 * s)
        y_sh, y_el, y_wr = hip_y + th - 40 * s, hip_y + th - 260 * s, hip_y + th - 460 * s
        up, lo = f"upperarm_{side}", f"forearm_{side}"
        B.bone(up, "torso", ax, y_sh, -90, y_sh - y_el)
        B.bone(lo, up, ax, y_el, -90, y_el - y_wr)
        hp = None
        if use_helper:
            hp = f"elbow_mid_{side}"
            B.bone(hp, up, ax, y_el, -90, 0.0)
            B.ir["transform"].append({"name": f"elbow_half_{side}", "order": len(B.ir["transform"]), "runs": True,
                                      "skin_required": False, "kind": "half_angle", "bone": hp, "target": lo,
                                      "mix": 0.5})
        sh = _joint(f"shoulder_{side}", up, "torso", None, up, (ax, y_sh), ahw)
        el = _joint(f"elbow_{side}", lo, up, hp, lo, (ax, y_el), ahw)
        joints += [sh, el]
        p_up = limb_piece(f"upper_arm_{side}", up, ax, y_sh + ahw, y_el - ov, ahw, pal["arm_up"],
                          round_bot=round_ends, outline=line)
        p_lo = limb_piece(f"forearm_{side}", lo, ax, y_el + ov, y_wr, ahw * 0.9, pal["arm_lo"],
                          round_top=round_ends, outline=line)
        bands = [sh["band"], el["band"]]
        for p in (p_lo, p_up):  # forearm under the sleeve
            rows = limb_rows(p.y0, p.y0 - p.h, [b["center"][1] for b in bands], ahw)
            wfn = None if mode == "rigid" else _limb_weight_fn(bands, p.bone)
            atts[p.name] = _mesh_attachment(p, rows, 4, wfn)
            images[p.name] = p.image
            slots_front.append((p.name, p.bone))
        if ghost:
            _bake(torso, p_up)
            _bake(torso, p_lo)
        if shard:
            _bake(p_lo, torso)
        if hole:
            _cut_hole(torso, ax + sg * -4 * s, y_sh - 110 * s, 14 * s)

    # legs: behind the torso, thigh over calf
    for side in (["l", "r"] if legs else []):
        sg = -1 if side == "l" else 1
        ax = sg * 70 * s
        y_hp, y_kn, y_an = hip_y, hip_y - 170 * s, hip_y - 340 * s
        up, lo = f"thigh_{side}", f"calf_{side}"
        B.bone(up, "hip", ax, y_hp, -90, y_hp - y_kn)
        B.bone(lo, up, ax, y_kn, -90, y_kn - y_an)
        hp = None
        if use_helper:
            hp = f"knee_mid_{side}"
            B.bone(hp, up, ax, y_kn, -90, 0.0)
            B.ir["transform"].append({"name": f"knee_half_{side}", "order": len(B.ir["transform"]), "runs": True,
                                      "skin_required": False, "kind": "half_angle", "bone": hp, "target": lo,
                                      "mix": 0.5})
        hj = _joint(f"hip_{side}", up, "hip", None, up, (ax, y_hp), lhw)
        kn = _joint(f"knee_{side}", lo, up, hp, lo, (ax, y_kn), lhw)
        joints += [hj, kn]
        p_up = limb_piece(f"thigh_{side}", up, ax, y_hp + 60 * s, y_kn - ov, lhw, pal["leg_up"],
                          round_bot=round_ends, outline=line)
        p_lo = limb_piece(f"calf_{side}", lo, ax, y_kn + ov, y_an - 20 * s, lhw * 0.9, pal["leg_lo"],
                          round_top=round_ends, outline=line)
        bands = [hj["band"], kn["band"]]
        for p in (p_lo, p_up):
            rows = limb_rows(p.y0, p.y0 - p.h, [b["center"][1] for b in bands], lhw)
            wfn = None if mode == "rigid" else _limb_weight_fn(bands, p.bone, root_upper="hip")
            atts[p.name] = _mesh_attachment(p, rows, 4, wfn)
            images[p.name] = p.image
            slots_back.append((p.name, p.bone))

    # torso region (after baking), head on top
    images[torso.name] = torso.image
    atts[torso.name] = _region_attachment(B, torso)
    order = slots_back + [(torso.name, "torso")]
    if head:
        B.bone("head", "torso", 0, hip_y + th, 90, 150 * s)
        hd = _head_piece(80 * s, hip_y + th - 10 * s, pal["head"], line)
        images[hd.name] = hd.image
        atts[hd.name] = _region_attachment(B, hd)
        order.append((hd.name, "head"))
    order += slots_front
    B.ir["slots"] = [irmod.new_slot(n, b, n) for n, b in order]
    B.ir["skins"] = [{"name": "default", "bones": [], "ik": [], "transform": [], "path": [], "physics": [],
                      "color": None, "attachments": {n: {n: atts[n]} for n, _ in order}}]
    _fit_skeleton_box(B.ir)
    errs = irmod.validate(B.ir)
    if errs:
        raise AssertionError("synth_art built an invalid IR:\n  " + "\n  ".join(errs))
    return B.ir, images, joints


def _joint(name, bone, upper, helper, lower, center, hw):
    return {"name": name, "bone": bone, "dirs": [1, -1],
            "band": {"center": [float(center[0]), float(center[1])], "upper": upper, "helper": helper,
                     "lower": lower, "halfwidth": float(hw), "axis": [0.0, -1.0]}}


def _limb_weight_fn(bands, own, root_upper=None):
    """Weights for a limb piece: ramp inside each band; outside, the bone
    of the segment the vertex lies on (above the first joint: that joint's
    upper bone, between joints: the middle bone, below: the last lower)."""
    bands = sorted(bands, key=lambda b: -b["center"][1])

    def fn(verts):
        out = []
        for _, y in verts:
            row = None
            for b in bands:
                if abs(y - b["center"][1]) <= RAMP_K * b["halfwidth"]:
                    row = ramp_weights([y], b)[0]
                    break
            if row is None:
                seg = bands[0]["upper"]
                for b in bands:
                    if y < b["center"][1]:
                        seg = b["lower"]
                row = [(seg, 1.0)]
            out.append(_normalised(row))
        return out
    return fn


def _torso_piece(tw, th, hip_y, colors=(TORSO_A, TORSO_B), outline=2.5, pad=2):
    w, h = int(math.ceil(2 * tw)) + 2 * pad, int(math.ceil(th)) + 2 * pad
    r = min(tw, th) * 0.25

    def inside(X, Y):
        x, y = np.abs(X - w / 2.0), Y - pad
        ok = (x <= tw) & (y >= 0) & (y <= th)
        corner = (x > tw - r) & (y < r)
        return ok & (~corner | ((x - (tw - r)) ** 2 + (y - r) ** 2 <= r * r))
    cover = _supersampled(h, w, inside)
    img = _paint(cover, colors[0], colors[1], _stripe_index(h, w, horizontal=False), outline)
    return Piece("torso", "torso", img, -w / 2.0, hip_y + th + pad)


def _head_piece(r, y_base, colors=(HEAD_A, HEAD_B), outline=2.5, pad=2):
    d = int(math.ceil(2 * r)) + 2 * pad
    c = d / 2.0
    cover = _supersampled(d, d, lambda X, Y: (X - c) ** 2 + (Y - c) ** 2 <= r * r)
    img = _paint(cover, colors[0], colors[1], _stripe_index(d, d, horizontal=True, period=STRIPE * 2), outline)
    return Piece("head", "head", img, -c, y_base + d)


def _bake(base, over):
    """Paint `over` into `base` where they overlap in world space (the
    "arm left in the torso paint" a flat-image cut produces)."""
    ys, xs = np.mgrid[0:base.h, 0:base.w]
    wpt = base.to_world(np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1))
    opx = np.floor(over.to_px(wpt)).astype(np.int64)
    ok = (opx[:, 0] >= 0) & (opx[:, 0] < over.w) & (opx[:, 1] >= 0) & (opx[:, 1] < over.h)
    src = np.zeros((len(wpt), 4), float)
    src[ok] = over.image[opx[ok, 1], opx[ok, 0]]
    a = (src[:, 3:4] / 255.0) * (base.image.reshape(-1, 4)[:, 3:4] > 0)
    flat = base.image.reshape(-1, 4).astype(float)
    flat[:, :3] = flat[:, :3] * (1 - a) + src[:, :3] * a
    base.image[...] = np.clip(np.round(flat), 0, 255).astype(np.uint8).reshape(base.image.shape)


def _cut_hole(base, wx, wy, r):
    """A transparent disc in `base` centred on world (wx, wy)."""
    c = base.to_px([(wx, wy)])[0]
    cover = _supersampled(base.h, base.w, lambda X, Y: (X - c[0]) ** 2 + (Y - c[1]) ** 2 <= r * r)
    a = base.image[..., 3].astype(float) * (1 - cover)
    base.image[..., 3] = np.clip(np.round(a), 0, 255).astype(np.uint8)


def _fit_skeleton_box(ir):
    ev = lbs.Evaluator(ir)
    world = ev.world()
    pts = [ev.attachment_vertices(world, n, a) for n, a in ev.shown() if a is not None]
    allv = np.concatenate(pts)
    lo, hi = allv.min(0), allv.max(0)
    ir["skeleton"].update(x=float(lo[0]), y=float(lo[1]), width=float(hi[0] - lo[0]),
                          height=float(hi[1] - lo[1]), images="./", hash=ir["name"])


# ---------------------------------------------------------------- files

def write(outdir, **kw):
    """rig.json (IR), images/<name>.png, joints.json; returns their paths."""
    from PIL import Image
    ir, images, joints = build(**kw)
    os.makedirs(os.path.join(outdir, "images"), exist_ok=True)
    rig_path = os.path.join(outdir, "rig.json")
    irmod.save(ir, rig_path)
    for name, img in images.items():
        Image.fromarray(img, "RGBA").save(os.path.join(outdir, "images", name + ".png"))
    jp = os.path.join(outdir, "joints.json")
    with open(jp, "w") as f:
        json.dump({"schema": "spinegen-budget-joints/1", "joints": joints}, f, indent=1)
    return rig_path, os.path.join(outdir, "images"), jp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir")
    ap.add_argument("--mode", choices=["rigid", "joint"], default="joint")
    ap.add_argument("--arms", type=int, choices=[1, 2], default=1)
    ap.add_argument("--legs", type=int, choices=[0, 2], default=0)
    ap.add_argument("--overlap", type=float, default=24.0)
    ap.add_argument("--ghost", action="store_true")
    ap.add_argument("--hole", action="store_true")
    ap.add_argument("--shard", action="store_true")
    ap.add_argument("--skin", action="store_true")
    ap.add_argument("--no-helper", action="store_true")
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args(argv)
    for p in write(a.outdir, mode=a.mode, arms=a.arms, legs=a.legs, overlap=a.overlap, ghost=a.ghost,
                   hole=a.hole, helper=not a.no_helper, scale=a.scale, shard=a.shard, skin=a.skin):
        print(p)


if __name__ == "__main__":
    main()
