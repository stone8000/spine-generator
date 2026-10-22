"""Skinning weights for a layer mesh: 2D bone heat plus joint-band ramps.

    compute(verts, tris, outline, bones, joints=(), mode="auto", max_influences=3, prune=0.02)
        -> [[(bone name, weight), ...] per vertex]

WHY TWO METHODS (SOTA §2 權重)
  Spine 4.2 skins with plain linear blending (LBS): a vertex shared 50/50 by
  two bones that differ by an angle d is pulled toward the joint by cos(d/2),
  whatever produced the weights. What a weighting method decides is WHICH
  vertices get blended and by how much, and those are two different jobs:

  * At a joint the blend must be a controlled ramp across the limb, wide
    enough that the inner side does not fold: a piecewise-linear ramp over
    the joint band |s| <= e (s along the limb from the joint centre, e =
    extent x halfwidth, default mesh.BAND_EXTENT = 2), upper -> helper ->
    lower when the rig has a half-angle helper bone (each half then only
    spans half the angle), upper -> lower without one. In the official
    runtime (E12, research/experiments/py/elbow42.py) the helper version of
    a two-layer synthetic arm has 0 folds to 150 deg and keeps 0.90 of the
    band's silhouette area at 120 deg; without the helper it folds from 130
    deg and keeps 0.74. Continuum limits for a linear ramp: area 1 - (1 -
    cos D)/6 (D the angle between the two blended bones), first fold at
    2 atan(e/h), or 4 atan(e/2h) with the helper (h the limb half-width).
  * Everywhere else (a torso, a tail, a cape, the far end of a limb) the
    weights should follow the SHAPE, not straight-line distance: Pinocchio's
    bone heat (Baran & Popovic 2007), as Blender implements it. Each bone
    heats the vertices it is nearest to and can "see"; heat diffuses over the
    mesh (cotangent Laplacian), so a vertex on the left leg is reached from
    the right leg only through the crotch, never across the gap.

  The two meet without a seam: band vertices are fixed to the ramp (Dirichlet
  values) and the heat equation is solved for the rest, so weights are
  continuous across the band edge. mesh.build puts a vertex row on each band
  end; those vertices are pinned to the ramp's end values (100% upper or
  lower), so the ramp stops where the band does instead of diffusing on.
  Overlapping layers at a joint (upper arm over forearm) get the same ramp
  at the same canvas point, so they deform together and the seam stays shut.

BONE HEAT IN 2D (Pinocchio; Blender meshlaplacian.cc heat_bone_weighting)
  For each bone j solve  (L + D H) w_j = D H p_j  where
    L   cotangent Laplacian of the triangle mesh (stiffness form),
    D   lumped vertex areas (a third of each incident triangle),
    H   per vertex: k * C / d^2, d = distance to the nearest bone, k = number
        of bones tied at that distance (Blender's tie rule: within a relative
        1e-4) that are VISIBLE from the vertex; 0 when none is visible,
    p_j 1/k at vertices whose tied, visible nearest bones include j.
  In 2D both L and D*H are scale free, so the result does not depend on
  pixel size or mesh density beyond discretisation.
  Visibility (2D): the segment from the vertex to the closest point on the
  bone stays inside the layer's outline polygon(s).
  Bones that are a joint's `helper` take no part in the heat: a half-angle
  helper only ever gets weight from its joint's ramp.

PRUNING
  Each vertex keeps at most `max_influences` bones (SOTA: 3), drops any under
  `prune` (0.02), and is renormalised to sum 1.

COORDINATES
  Canvas pixels (y down), the same space as mesh.build's verts; nothing here
  depends on the y direction.
"""
import numpy as np
from scipy import sparse
from scipy.sparse import linalg as spla

from .mesh import BAND_EXTENT, inside

MODES = ("auto", "heat", "rigid")
C_WEIGHT = 1.0       # Pinocchio's c (Blender C_WEIGHT)
DIST_EPS = 1e-4      # Blender DISTANCE_EPSILON: bones within (1 + eps) x the nearest distance tie
MIN_DIST = 0.5       # px: a vertex on a bone still gets a finite H
LATERAL = 1.6        # a joint band reaches this many halfwidths sideways from the joint's bones
END_TOL = 0.5        # px: mesh.build puts a vertex row on each band end; a vertex this close to an
                     # end belongs to the band (pinned to the ramp's end value) but is not "inside"
                     # it for B2 (it carries one influence by design)
B2_MIN = 0.9         # SOTA §5 B2: >= 90% of joint-band vertices have >= 2 influences


# ---------------------------------------------------------------- public

def compute(verts, tris, outline, bones, joints=(), mode="auto", max_influences=3, prune=0.02,
            visibility=True):
    """Per-vertex weights (see the module docstring).

    verts     (N, 2) canvas coordinates; tris (M, 3)
    outline   (K, 2) polygon, or a list of polygons (outer outline first, then
              holes) for the visibility test
    bones     [{"name", "head": (x, y), "tail": (x, y)}]; head == tail is a point bone
    joints    [{"center", "upper", "helper" (name or None), "lower", "halfwidth",
               optional "axis" (default: mean direction of the upper and lower
               bones), "extent" (band half-length in halfwidths, default
               mesh.BAND_EXTENT; give the mesh band the same), "reach"
              ([upper, lower] halfwidths from the centre, overriding
              extent: an asymmetric band, joint_reach), "lateral" (band
              reach sideways in halfwidths, default LATERAL; see band_mask),
              "exclusive" (True: band vertices carry only the joint's bones; by
              default other bones keep the heat weight they had there)}]; every bone a
              joint names must be in `bones`
    mode      "auto": heat + joint ramps; "heat": heat only; "rigid": every vertex
              100% on bones[0] (the old cut-out, for comparison)
    visibility  False switches the 2D visibility test off (Euclidean nearest
              bone), for comparison only
    """
    names = [b["name"] for b in bones]
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not bones:
        raise ValueError("no bones")
    for j in joints if mode == "auto" else ():
        missing = [j[k] for k in ("upper", "lower", "helper") if j.get(k) and j[k] not in names]
        if missing:
            raise ValueError(f"joint at {tuple(j['center'])} names bones not in `bones`: {missing}")
    n = len(verts)
    if mode == "rigid":
        return [[(names[0], 1.0)] for _ in range(n)]
    W = weight_matrix(verts, tris, outline, bones, joints if mode == "auto" else (), visibility)
    return prune_weights(W, names, max_influences, prune)


def weight_matrix(verts, tris, outline, bones, joints=(), visibility=True):
    """Unpruned (N, B) weights, rows summing to 1, columns in `bones` order."""
    V = np.asarray(verts, float).reshape(-1, 2)
    T = np.asarray(tris, np.int64).reshape(-1, 3)
    polys = _polygons(outline)
    helpers = {j["helper"] for j in joints if j.get("helper")}
    heat_cols = np.array([b["name"] not in helpers for b in bones])
    L, D = cotan_laplacian(V, T)
    A, B = _heat_system(V, L, D, polys, bones, heat_cols, visibility)
    W = _solve(A, B)
    if joints:
        fixed = np.zeros(len(V), bool)
        target = W.copy()
        index = {b["name"]: i for i, b in enumerate(bones)}
        for j in joints:
            m, ramp = joint_ramp(V, j, bones)
            cols = [index[j["upper"]], index[j["lower"]]] + ([index[j["helper"]]] if j.get("helper") else [])
            if j.get("exclusive"):
                target[m] = 0.0  # the band belongs to its joint's bones alone
                mass = np.ones((int(m.sum()), 1))
            else:
                mass = target[m][:, cols].sum(1, keepdims=True)
            target[np.ix_(m, cols)] = 0.0
            target[np.ix_(m, cols[:2])] += mass * ramp[:, :2]
            if j.get("helper"):
                target[np.ix_(m, cols[2:])] += mass * ramp[:, 2:]
            fixed |= m
        W = _solve(A, B, fixed, target[fixed]) if fixed.any() else W
    return _normalise(W, V, bones)


def prune_weights(W, names, max_influences=3, prune=0.02):
    """Keep each vertex's top `max_influences` weights >= `prune` (the
    largest always survives), renormalised; sorted heaviest first."""
    out = []
    order = np.argsort(-W, axis=1, kind="stable")[:, :max_influences]
    for row, idx in zip(W, order):
        keep = [i for i in idx if row[i] >= prune] or [idx[0]]
        s = float(sum(row[i] for i in keep))
        out.append([(names[i], float(row[i] / s)) for i in keep])
    return out


def as_matrix(weights, names):
    """[[(name, w), ...]] -> (N, B) array in `names` order."""
    col = {n: i for i, n in enumerate(names)}
    M = np.zeros((len(weights), len(names)))
    for v, row in enumerate(weights):
        for name, w in row:
            M[v, col[name]] += w
    return M


# ---------------------------------------------------------------- joint bands

def joint_reach(joint):
    """(upper, lower) px the band reaches from its centre along the axis,
    toward the upper and the lower bone.

    `reach` [upper, lower] in halfwidths, else `extent` both ways. WHY two
    lengths: a band only has to be long where the art is wide for its
    length. The elbow's sleeve side has a whole upper arm to spread over,
    while the forearm side must stop before a rigid wrist or it bends the
    hand (P2 A1); one symmetric extent has to pick between the two."""
    hw = float(joint["halfwidth"])
    if joint.get("reach") is not None:
        up, lo = (float(v) for v in joint["reach"])
    else:
        up = lo = float(joint.get("extent", BAND_EXTENT))
    if up <= 0 or lo <= 0:
        raise ValueError(f"joint at {tuple(joint['center'])}: band reach must be > 0 both ways, not {(up, lo)}")
    return up * hw, lo * hw


def joint_frame(joint, bones):
    """(center, unit axis from upper toward lower, halfwidth, band half-length)."""
    by = {b["name"]: b for b in bones}
    c = np.asarray(joint["center"], float)
    hw = float(joint["halfwidth"])
    ext = float(joint.get("extent", BAND_EXTENT)) * hw
    if joint.get("axis") is not None:
        axis = np.asarray(joint["axis"], float)
    else:
        axis = np.zeros(2)
        for name in (joint["upper"], joint["lower"]):
            d = np.asarray(by[name]["tail"], float) - np.asarray(by[name]["head"], float)
            if np.linalg.norm(d) > 1e-9:
                axis += d / np.linalg.norm(d)
        if np.linalg.norm(axis) < 1e-9:
            axis = np.asarray(by[joint["lower"]]["head"], float) - np.asarray(by[joint["upper"]]["head"], float)
    if np.linalg.norm(axis) < 1e-9:
        raise ValueError(f"joint at {joint['center']}: cannot tell the limb axis; give 'axis'")
    return c, axis / np.linalg.norm(axis), hw, ext


def band_coords(verts, joint, bones):
    """(s, lateral): signed distance along the limb axis from the joint
    centre, and distance to the nearer of the joint's upper/lower bones."""
    V = np.asarray(verts, float).reshape(-1, 2)
    c, axis, _, _ = joint_frame(joint, bones)
    by = {b["name"]: b for b in bones}
    s = (V - c) @ axis
    lat = np.minimum(_seg_dist(V, by[joint["upper"]]), _seg_dist(V, by[joint["lower"]]))
    return s, lat


def band_mask(verts, joint, bones, strict=False):
    """Vertices in the joint band: |s| <= half-length + END_TOL (with
    `strict`: |s| < half-length - END_TOL, which leaves out the rows on the
    band's ends) and within the joint's "lateral" (default LATERAL)
    halfwidths of its bones. The lateral limit keeps a band from claiming
    unrelated art beside the limb; a caller whose mesh holds only the limb
    (the rig builder's per-layer meshes) passes a larger one, because a limb
    wider than LATERAL x halfwidth (a flared hem) would otherwise leave its
    outer vertices on heat weights next to ramped ones, and fold there."""
    s, lat = band_coords(verts, joint, bones)
    _, _, hw, _ = joint_frame(joint, bones)
    up, lo = joint_reach(joint)
    if strict:
        along = (s > -up + END_TOL) & (s < lo - END_TOL)
    else:
        along = (s >= -up - END_TOL) & (s <= lo + END_TOL)
    return along & (lat <= float(joint.get("lateral", LATERAL)) * hw)


def joint_ramp(verts, joint, bones):
    """(band mask, ramp weights for the band vertices).

    ramp columns: [upper, lower] or, with a helper, [upper, lower, helper].
    t runs 0 -> 1 across the band; with a helper the ramp is piecewise:
    upper -> helper over the upper half (centre - reach up .. centre),
    helper -> lower over the lower half, so no vertex mixes more than two
    of the three ([E1] pw_mid) and the helper owns the centre whatever the
    two halves' lengths (joint_reach)."""
    m = band_mask(verts, joint, bones)
    s, _ = band_coords(np.asarray(verts, float).reshape(-1, 2)[m], joint, bones)
    up, lo = joint_reach(joint)
    if joint.get("helper"):
        a = np.clip((s + up) / up, 0, 1)
        b = np.clip(s / lo, 0, 1)
        return m, np.stack([1 - a, b, a - b], 1)
    t = np.clip((s + up) / (up + lo), 0.0, 1.0)
    return m, np.stack([1 - t, t], 1)


def b2(verts, weights, joints, bones):
    """SOTA §5 B2 JOINT_BAND: share of joint-band vertices (strictly inside
    the band) with >= 2 influences. Returns {"value", "pass", "count", "worst"}."""
    inside_band = np.zeros(len(verts), bool)
    for j in joints:
        inside_band |= band_mask(verts, j, bones, strict=True)
    idx = np.flatnonzero(inside_band)
    if not len(idx):
        return {"value": None, "pass": True, "count": 0, "worst": None}
    single = [int(i) for i in idx if len(weights[i]) < 2]
    value = 1.0 - len(single) / len(idx)
    return {"value": value, "pass": value >= B2_MIN, "count": int(len(idx)), "worst": single[:10]}


# ---------------------------------------------------------------- heat

def cotan_laplacian(V, T):
    """(L, D): cotangent stiffness matrix (positive semi-definite, rows sum
    to 0) and lumped vertex areas."""
    n = len(V)
    i, j, k = T[:, 0], T[:, 1], T[:, 2]
    e1, e2 = V[j] - V[i], V[k] - V[i]
    cross = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    cross = np.maximum(cross, 1e-12)

    def cot(a, b, c):  # cotangent of the angle at a in triangle (a, b, c)
        u, w = V[b] - V[a], V[c] - V[a]
        return (u * w).sum(1) / cross

    ci, cj, ck = cot(i, j, k), cot(j, k, i), cot(k, i, j)
    rows = np.concatenate([j, k, k, i, i, j])
    cols = np.concatenate([k, j, i, k, j, i])
    vals = -0.5 * np.concatenate([ci, ci, cj, cj, ck, ck])
    off = sparse.coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsr()
    L = off - sparse.diags(np.asarray(off.sum(1)).ravel())
    D = np.bincount(T.ravel(), np.repeat(cross / 6.0, 3), minlength=n)
    return L.tocsr(), D


def _heat_system(V, L, D, polys, bones, heat_cols, visibility):
    """(A, B) of (L + D H) W = D H P over every bone column."""
    n, nb = len(V), len(bones)
    dist = np.stack([_seg_dist(V, b) for b in bones], 1)
    dist[:, ~heat_cols] = np.inf
    vis = visible(V, bones, polys) if visibility else np.ones((n, nb), bool)
    if visibility and not (vis & heat_cols[None]).any():
        vis[:] = True  # no bone can be seen from anywhere: fall back to straight-line nearest
    mind = dist.min(1)
    tied = (dist <= mind[:, None] * (1 + DIST_EPS) + 1e-9) & vis & heat_cols[None]
    k = tied.sum(1)
    d2 = np.maximum(mind, MIN_DIST) ** 2
    h = np.where(k > 0, k * C_WEIGHT / d2, 0.0)  # Blender: numclosest * C / d^2
    P = tied / np.maximum(k, 1)[:, None]
    A = (L + sparse.diags(D * h)).tocsc()
    B = (D * h)[:, None] * P
    return A, B


def _solve(A, B, fixed=None, values=None):
    """Solve A W = B, optionally with W[fixed] = values (Dirichlet rows)."""
    n = A.shape[0]
    if fixed is None:
        return _factor_solve(A, B)
    W = np.zeros_like(B)
    W[fixed] = values
    free = ~fixed
    if free.any():
        Aff = A[free][:, free]
        rhs = B[free] - A[free][:, fixed] @ values
        W[free] = _factor_solve(Aff.tocsc(), rhs)
    return W


def _factor_solve(A, B):
    # a component with no heat source makes A singular there; a tiny diagonal
    # keeps the factorisation alive and _normalise repairs those rows
    eps = 1e-12 * max(1.0, abs(A.diagonal()).max())
    lu = spla.splu((A + sparse.identity(A.shape[0], format="csc") * eps).tocsc())
    X = lu.solve(np.ascontiguousarray(B, float))
    return X.reshape(B.shape)


def _normalise(W, V, bones):
    """Clip the small negative values cotangent weights can produce, rows to
    sum 1; a row with no weight at all goes 100% to its nearest bone."""
    W = np.clip(np.nan_to_num(W, nan=0.0), 0.0, None)
    s = W.sum(1)
    dead = s < 1e-9
    if dead.any():
        dist = np.stack([_seg_dist(V[dead], b) for b in bones], 1)
        W[dead] = 0.0
        W[np.flatnonzero(dead), dist.argmin(1)] = 1.0
        s = W.sum(1)
    return W / s[:, None]


# ---------------------------------------------------------------- geometry

def _polygons(outline):
    if isinstance(outline, np.ndarray) and outline.ndim == 2:
        return [outline.astype(float)]
    if len(outline) and np.ndim(outline[0]) == 1:
        return [np.asarray(outline, float)]
    return [np.asarray(p, float) for p in outline]


def _closest_on_bone(V, bone):
    a = np.asarray(bone["head"], float)
    b = np.asarray(bone["tail"], float)
    ab = b - a
    ll = float(ab @ ab)
    if ll < 1e-18:
        return np.broadcast_to(a, V.shape)
    t = np.clip(((V - a) @ ab) / ll, 0.0, 1.0)
    return a + t[:, None] * ab


def _seg_dist(V, bone):
    return np.linalg.norm(V - _closest_on_bone(V, bone), axis=1)


def visible(V, bones, polys):
    """(N, B) bool: the segment from each vertex to the closest point on each
    bone stays inside the outline (no proper crossing with any outline edge,
    and its midpoint is inside the outer polygon and outside every hole)."""
    edges = np.concatenate([np.stack([p, np.roll(p, -1, axis=0)], 1) for p in polys])
    a, b = edges[:, 0], edges[:, 1]
    out = np.zeros((len(V), len(bones)), bool)
    for k, bone in enumerate(bones):
        q = _closest_on_bone(V, bone)
        crosses = _any_crossing(V, q, a, b)
        mid = (V + q) / 2
        ok = inside(polys[0], mid)
        for hole in polys[1:]:
            ok &= ~inside(hole, mid)
        out[:, k] = ~crosses & ok
    return out


def _any_crossing(p0, p1, a, b, tol=1e-9):
    """(N,) whether segment p0[i]-p1[i] properly crosses any segment a[k]-b[k]."""
    r = (p1 - p0)[:, None]
    s = (b - a)[None]
    qp = a[None] - p0[:, None]

    def cross(u, v):
        return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]

    d1 = cross(r, qp)
    d2 = cross(r, qp + s)
    d3 = cross(s, -qp)
    d4 = cross(s, -qp + r)
    hit = ((d1 > tol) & (d2 < -tol) | (d1 < -tol) & (d2 > tol)) & \
          ((d3 > tol) & (d4 < -tol) | (d3 < -tol) & (d4 > tol))
    return hit.any(1)
