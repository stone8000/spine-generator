"""Art gates on rendered frames (SOTA §5): G1 folds, G2 joint-band area,
G4 seam gap, G5 holes, G6 ghost.

WHY THESE ARE PIXEL GATES
  A rig can pass every structural check and still look broken: two pieces
  of a limb pull apart and the background shows at the elbow (G4), a moved
  arm uncovers an unpainted hole in the torso (G5), the underlay a piece
  hid under its partner swings into view, or the torso turns out to still
  carry a painted copy of the arm (G6). Only pixels show these, so the
  gates read frames from spinegen.raster, whose geometry is spinegen.lbs
  (pinned to spine-core by C1) and whose coverage is thresholded at alpha
  0.5, where the texture filter cannot move a decision.

WHERE THE DEFINITIONS COME FROM
  G4 and G6(a) follow the reading rigc's `render --seam` makes (concepts
  only; no code or material taken), restricted to the SOTA joint disk:
  * a joint has two PARTS, the slots on the upper and on the lower side;
  * its BAND is what both parts draw at the setup pose near the joint
    (where the art was painted to overlap);
  * a point of a frame is CARRIED BACK into a part's setup pose by the
    affine map of that part's triangle holding it, or, outside the part,
    of the triangle it is least outside of (largest smallest barycentric
    weight). That is exact for a rigid piece and the nearest affine piece
    of a skinned one; a limb that swings whole carries every point back
    to where it was, so motion alone never reads as a tear.
  G4 SEAM GAP counts pixels inside the convex hull of the band as both parts
  carry it into the frame (each part carrying the band pixels it drew
  itself), within the disk of radius 1.5 x halfwidth around the (posed)
  joint, eroded by 1 px, that the two parts together leave below alpha 0.5
  and that at least one part, carried back, finds drawn at setup and not on
  the rest drawing's 1 px rim. The hull is the joint's own extent (the far
  end of a swinging limb, or the crook of a bent one, never counts); the
  rest test keeps a notch the art already had from counting, and the rim
  erosion keeps an anti-aliased outline that recedes by a pixel from
  reading as a tear. A jagged outline (fur) that turns still opens real
  1 px wide notches inside the hull.
  CALIBRATION (SOTA §5 gives "0 px" as a starting value, §8.3 asks for it
  to be calibrated): the count keeps only gap components (8-connected) of
  at least 4 px, the area of a 2 x 2 block (G4_MIN_PX, scaled by the
  frame's scale squared); the threshold stays 0 such pixels. A 1-3 px speck
  where a jagged (fur) or anti-aliased outline turns is not visible at 1:1;
  a crack that is visible, even a 1 px hairline across a limb, is longer
  than that. "raw_px" keeps the count before the rule; P2_REPORT has the
  monkey's first failures before and after.
  G6 GHOST (a) takes the pixels only one part draws whose own carry-back
  lands in the band but whose partner's carry-back finds nothing drawn at
  setup: the overlap swung clear of its partner. Its LENGTH is how far it
  sticks out, the largest distance of such a pixel from the partner's
  drawn pixels; it must stay <= 0.25 x limb width (2 x halfwidth).
  G6 GHOST (b) is texel identity: a pixel now shows texel t of slot U, and
  at rest texel t sat under another slot O drawn above U, and O has moved
  (SOTA: "after the occluder moves away"; a limb sliding out from under a
  sleeve that stayed put is G6(a)'s business, not a paint ghost). Those NEWLY
  REVEALED pixels, eroded 2 px (a visible ghost is a patch; a 1-4 px sliver
  where the occluder's outline sat next to the underlying slot's own
  outline correlates edge with edge, not paint with paint), are compared
  with what O showed there at rest by normalised cross-correlation of
  mean-free RGB; NCC > 0.3 means U's paint repeats the occluder (an arm left
  in the torso paint by a flat-image cut).
  The correlation cannot say WHICH layer holds the copy, and only one case
  shows: an arm copy in the torso is revealed as a second arm, but a shard
  of trousers cut into the hand image reveals trousers where trousers were
  (the hand carries the copy away, and it shows only where it lands on
  something else). When a pair correlates, duplicates() finds the copy at
  rest (the occluder's paint matches the paint under it, CIELAB distance
  < 10) and gives it an owner: a copy that continues the underlay's
  visible paint seamlessly across the occluder's edge is CARRIED by the
  occluder (that is why it is invisible at rest).
  A carried pair leaves the G6(b) judgement and is reported as "carried"
  (budget.json turns it into an art request: clean the shard out of that
  layer), so G6(b) limits a joint only by a duplicate that is visible
  where it is revealed. Where the shard itself shows (over background or
  over paint it does not match) is carried_visible()'s count.
  G5 HOLES are background pixels (>= 16 px per component) that one group
  of slots encloses on its own art, the slots that did not move or the
  ones that did, and that something covered at rest: a moved arm baring an
  unpainted patch of the torso. A loop of arm, torso and leg around open
  space is not a hole: no single group encloses it (SOTA §8.3's false
  positive), and neither is a hole the art already showed at rest.
  G2 AREA sums the signed area of the mesh triangles whose setup centroid
  lies in the joint band (+-1.25 halfwidths along the limb, +-1.6 across,
  as spinegen.weights draws it), oriented by each mesh's setup winding, so
  a fold subtracts: posed / rest >= 0.9 (the budget's soft limit; SOTA
  allows 0.85 outside holds), AND every one of those triangles keeps its
  own posed / rest area within [0.3, 3] (SOTA §5 G2 names both). The sum
  alone let a band hide a crushed or stretched triangle behind the others
  (P2 review F3: the monkey's elbows read 98-110 deg on the sum while
  single band triangles left the range from 60-80 deg); strict_tris=False
  keeps the old sum-only reading for comparison.
  G1 FOLDS is lbs' count (the same definition as qa/run.mjs G1).

UNITS
  Pixel counts and pixel thresholds are at the frame's scale; thresholds
  given in px are for scale 1 (SOTA: 1:1 renders) and scale with it (areas
  by scale^2). Lengths in results are in world units unless named *_px.

Every gate returns {"id", "value", "pass", "worst", ...}; `worst` names the
slot (or slot pair) the failure is in, which budget.py turns into
"<gate>@<slot>".
"""
import math

import numpy as np

from . import lbs
from .raster import COVER

DISK = 1.5             # G4 disk radius, in limb halfwidths
G4_MIN_PX = 4.0        # G4 counts a gap component only from this many px (a 2 x 2 block; scale 1; module doc)
BAND_EXTENT = 1.25     # G2 band half-length along the limb, halfwidths (= spinegen.mesh/weights)
BAND_LATERAL = 1.6     # G2 band half-width across the limb, halfwidths (= spinegen.weights)
MIN_BAND_RATIO = 0.9   # G2 soft (hold) limit
TRI_RANGE = (0.3, 3.0)  # G2 single-triangle ratio range
HOLE_MIN_AREA = 16.0   # G5, px at scale 1
GHOST_MAX_LEN = 0.25   # G6(a), x limb width
GHOST_MAX_NCC = 0.3    # G6(b)
NCC_MIN_PX = 64.0      # G6(b) evaluates a (slot, occluder) pair from this many px (scale 1; see ghost_paint)
NCC_ERODE = 2.0        # G6(b) correlates only revealed pixels this far (px, scale 1) inside the revealed patch
DUP_DE = 10.0          # G6(b) duplicates(): CIELAB distance under which two layers' paint is "the same"
SEAM_REACH = 3.0       # G6(b) duplicates(): px (scale 1) across the occluder's edge where a copy's owner is read
SEAM_MIN_PX = 8.0      # G6(b) duplicates(): px of contact with visible underlay paint needed to call a copy carried
DUP_MIN_PX = 16.0      # G6(b) duplicates(): smallest duplicate component, px (scale 1)


# ---------------------------------------------------------------- joints

def as_joint(joint):
    """Normalise a joint: the weights.compute form {center, upper, helper,
    lower, halfwidth[, axis, extent, name, slots]} or the budget form
    {name, bone, dirs, band: {...}}. Coordinates are WORLD (y up)."""
    band = dict(joint.get("band") or {})
    for k in ("center", "upper", "helper", "lower", "halfwidth", "axis", "extent", "slots", "other_limbs",
              "band_center"):
        if k not in band and k in joint:
            band[k] = joint[k]
    band["name"] = joint.get("name") or band.get("name") or f"{band.get('upper')}-{band.get('lower')}"
    band["bone"] = joint.get("bone") or band.get("lower")
    band.setdefault("helper", None)
    if band.get("lower") is None:
        band["lower"] = band["bone"]
    return band


def resolve_joint(rest, joint):
    """as_joint plus what can be derived from the rig at rest: the upper
    bone (the lower bone's parent), the center (the lower bone's setup
    origin), the axis (_limb_axis) and the halfwidth
    (estimate_halfwidth). Raises if a named bone does not exist or the
    halfwidth is not positive."""
    j = as_joint(joint)
    ev = rest.renderer.ev
    for k in ("bone", "lower", "upper", "helper"):
        if j.get(k) is not None and j[k] not in ev.index:
            raise KeyError(f"joint {j['name']}: bone {j[k]!r} not in the rig")
    li = ev.index[j["lower"]]
    if j.get("upper") is None:
        p = ev.parent[li]
        j["upper"] = ev.bones[p]["name"] if p >= 0 else j["lower"]
    m = rest.world[li]
    if j.get("center") is None:
        j["center"] = [m[4], m[5]]
    if j.get("axis") is None:
        j["axis"] = _limb_axis(ev, rest.world, li)
    j["center"] = [float(j["center"][0]), float(j["center"][1])]
    if j.get("halfwidth") is None:
        j["halfwidth"] = estimate_halfwidth(rest, j)
    j["halfwidth"] = float(j["halfwidth"])
    if not j["halfwidth"] > 0:
        raise ValueError(f"joint {j['name']}: halfwidth must be > 0 (got {j['halfwidth']})")
    return j


def _limb_axis(ev, world, li):
    """Unit world direction the limb leaves the joint in: the lower bone's
    x axis when it has a length, else toward its first child's origin (a
    cut-out rig like the monkey's leaves every bone at length 0 and
    rotation 0, so its x axis says nothing about the limb), else world x."""
    m = world[li]
    if (ev.bones[li].get("length") or 0.0) <= 1e-6:
        for ci in ev.children[li]:
            d = np.array([world[ci][4] - m[4], world[ci][5] - m[5]])
            n = float(np.hypot(*d))
            if n > 1e-6:
                return [float(d[0] / n), float(d[1] / n)]
    n = math.hypot(m[0], m[2])
    return [m[0] / n, m[2] / n] if n > 1e-9 else [1.0, 0.0]


def estimate_halfwidth(rest, j, reach=40.0):
    """Limb halfwidth at a joint from the art: the largest inscribed radius
    of the LOWER part's drawn pixels (the limb leaving the joint) within
    `reach` world units beyond its pixel nearest the center. A joint point
    often sits on the art's edge or outside it (a shoulder bone placed at
    the top of the arm), so the nearest art, not the point, decides."""
    import cv2
    c = rest.canvas
    lower = _lower_slots(rest, j)
    solid = (union_alpha(rest, lower) >= COVER).astype(np.uint8)
    if not solid.any():
        raise ValueError(f"joint {j['name']}: the lower bone {j['lower']!r} moves no drawn slot")
    dist = cv2.distanceTransform(np.pad(solid, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    px = c.to_px([j["center"]])[0]
    ii, jj = np.nonzero(solid)
    d = np.hypot(jj + 0.5 - px[0], ii + 0.5 - px[1])
    near = d <= d.min() + reach * c.scale
    return float(dist[ii[near], jj[near]].max()) / c.scale


def _subtree(ev, name):
    root = ev.index[name]
    out, todo = set(), [root]
    while todo:
        i = todo.pop()
        out.add(i)
        todo += ev.children[i]
    return out


def slot_bone_mass(rest, slot):
    """{bone index: total weight} of what the slot shows at rest (a region
    or an unweighted mesh: all on the slot bone)."""
    ev = rest.renderer.ev
    g = rest.geoms.get(slot)
    if g is None:
        return {}
    if g.att["type"] == "region":
        return {ev.index[ev.ir["slots"][ev.slot_index[slot]]["bone"]]: 1.0}
    _, bnd = ev.binding(slot, g.att)
    out = {}
    for b, w in zip(bnd["bone"].tolist(), bnd["weight"].tolist()):
        out[b] = out.get(b, 0.0) + w
    return out


def joint_parts(rest, j):
    """(upper slots, lower slots) of a resolved joint, in draw order.

    A slot belongs to the side its heaviest bone is on: the lower side is
    the lower bone's subtree; the upper side the upper bone's subtree minus
    that (the upper bone, its helper and cuff bones, ...). Upper-side slots
    count only if their art reaches into the joint disk at rest, so the
    head is not part of a shoulder because it hangs off the torso, and
    slots on OTHER limbs (`j["other_limbs"]`: the root bones of the other
    joints' lower sides, which budget.scan fills in) never count: a hand
    hanging next to the thigh is not part of the hip.
    `j["slots"] = {"upper": [...], "lower": [...]}` overrides."""
    if j.get("slots"):
        names = set(rest.geoms)
        return ([s for s in j["slots"]["upper"] if s in names], [s for s in j["slots"]["lower"] if s in names])
    ev = rest.renderer.ev
    low = _subtree(ev, j["lower"])
    up = _subtree(ev, j["upper"]) - low
    for b in j.get("other_limbs") or ():
        if b in ev.index and ev.index[j["lower"]] not in _subtree(ev, b):
            up -= _subtree(ev, b)
    upper = []
    disk = _disk_mask(rest.canvas, j["center"], DISK * j["halfwidth"])
    for s in rest.slots:
        b = _heaviest_bone(rest, s)
        if b is not None and b in up and (rest.slot_alpha(s)[disk] >= COVER).any():
            upper.append(s)
    return upper, _lower_slots(rest, j)


def _heaviest_bone(rest, slot):
    mass = slot_bone_mass(rest, slot)
    return max(mass, key=mass.get) if mass else None


def _lower_slots(rest, j):
    """Slots whose heaviest bone is in the lower bone's subtree."""
    if j.get("slots"):
        return [s for s in j["slots"]["lower"] if s in rest.geoms]
    low = _subtree(rest.renderer.ev, j["lower"])
    return [s for s in rest.slots if _heaviest_bone(rest, s) in low]


def _disk_mask(canvas, center, radius):
    px = canvas.to_px([center])[0]
    ii, jj = np.mgrid[0:canvas.H, 0:canvas.W]
    r = radius * canvas.scale
    return (jj + 0.5 - px[0]) ** 2 + (ii + 0.5 - px[1]) ** 2 <= r * r


def center_now(rest, frame, j):
    """The joint center carried by its upper bone from rest to the frame."""
    ev = rest.renderer.ev
    i = ev.index[j["upper"]]
    return _apply(_delta(rest.world[i], frame.world[i]), np.array([j["center"]]))[0]


def _delta(m_rest, m_now):
    """2x3 world affine taking a point attached to a bone at rest to where
    the bone puts it now: M_now * M_rest^-1."""
    a, b, c, d, x, y = m_rest
    det = a * d - b * c
    inv = np.array([[d, -b], [-c, a]]) / det
    A = np.array([[m_now[0], m_now[1]], [m_now[2], m_now[3]]]) @ inv
    t = np.array([m_now[4], m_now[5]]) - A @ np.array([x, y])
    return np.hstack([A, t[:, None]])


def _apply(M, pts):
    pts = np.asarray(pts, float).reshape(-1, 2)
    return pts @ M[:, :2].T + M[:, 2]


# ---------------------------------------------------------------- parts as triangle soups

class Part:
    """The triangles of a set of slots, at rest and in a frame, and the
    piecewise-affine carry between the two."""

    def __init__(self, rest, slots):
        self.slots = list(slots)
        tris, rest_xy, owner = [], [], []
        for k, s in enumerate(self.slots):
            g = rest.geoms[s]
            t = np.asarray(g.tris, np.int64).reshape(-1, 3)
            tris.append(t)
            rest_xy.append(g.xy[t])
            owner.append(np.full(len(t), k))
        self.rest_tris = np.concatenate(rest_xy) if rest_xy else np.zeros((0, 3, 2))
        self.owner = np.concatenate(owner) if owner else np.zeros(0, np.int64)
        self._idx = tris

    def posed(self, frame):
        out = []
        for s, t in zip(self.slots, self._idx):
            g = frame.geoms.get(s)
            if g is None or len(g.xy) <= (t.max() if len(t) else -1):
                raise ValueError(f"slot {s} shows different geometry in the frame than at rest")
            out.append(g.xy[t])
        return np.concatenate(out) if out else np.zeros((0, 3, 2))

    def locate(self, src, pts):
        """(triangle index, barycentrics (n, 3)) of each point in the soup
        `src` (m, 3, 2): the triangle holding it, else the one it is least
        outside of (largest smallest barycentric weight)."""
        pts = np.asarray(pts, float).reshape(-1, 2)
        n, m = len(pts), len(src)
        if n == 0 or m == 0:
            return np.zeros(n, np.int64), np.zeros((n, 3))
        a, v0, v1 = src[:, 0], src[:, 1] - src[:, 0], src[:, 2] - src[:, 0]
        d = v0[:, 0] * v1[:, 1] - v0[:, 1] * v1[:, 0]
        good = np.abs(d) > 1e-12
        dd = np.where(good, d, 1.0)
        best_t = np.zeros(n, np.int64)
        best_l = np.zeros((n, 3))
        chunk = max(1, int(2e6 // m))
        for s0 in range(0, n, chunk):
            p = pts[s0:s0 + chunk]
            px = p[:, None, 0] - a[None, :, 0]
            py = p[:, None, 1] - a[None, :, 1]
            l1 = (px * v1[None, :, 1] - py * v1[None, :, 0]) / dd
            l2 = (v0[None, :, 0] * py - v0[None, :, 1] * px) / dd
            l0 = 1.0 - l1 - l2
            mn = np.minimum(np.minimum(l0, l1), l2)
            mn[:, ~good] = -np.inf
            t = np.argmax(mn, 1)  # first (lowest index) on a tie
            r = np.arange(len(p))
            best_t[s0:s0 + chunk] = t
            best_l[s0:s0 + chunk] = np.stack([l0[r, t], l1[r, t], l2[r, t]], 1)
        return best_t, best_l

    @staticmethod
    def through(dst, t, lam):
        return np.einsum("nk,nkd->nd", lam, dst[t])

    def carry_back(self, frame, pts, posed=None):
        """World points of a frame -> this part's setup pose."""
        posed = self.posed(frame) if posed is None else posed
        t, lam = self.locate(posed, pts)
        return self.through(self.rest_tris, t, lam)

    def carry_forward(self, frame, pts, located=None, posed=None):
        """Setup-pose world points -> where this part puts them in the frame."""
        posed = self.posed(frame) if posed is None else posed
        t, lam = located if located is not None else self.locate(self.rest_tris, pts)
        return self.through(posed, t, lam)


# ---------------------------------------------------------------- per-joint rest data

class JointRest:
    """What every frame's G2/G4/G6(a) reading needs from the rest frame,
    computed once per (rest frame, joint)."""

    def __init__(self, rest, joint, disk=DISK):
        import cv2
        self.rest = rest
        self.j = resolve_joint(rest, joint)
        self.name = self.j["name"]
        self.hw = self.j["halfwidth"]
        self.radius = disk * self.hw
        self.upper_slots, self.lower_slots = joint_parts(rest, self.j)
        self.upper = Part(rest, self.upper_slots)
        self.lower = Part(rest, self.lower_slots)
        c = rest.canvas
        self.U0 = union_alpha(rest, self.upper_slots) >= COVER
        self.L0 = union_alpha(rest, self.lower_slots) >= COVER
        self.drawn0 = self.U0 | self.L0
        # G4's "1 px erosion" applied to what the parts drew at rest: a pixel
        # on the rest silhouette's rim is not a place a tear can open, only a
        # place where the outline recedes by a pixel as a piece turns
        self.drawn0_in = cv2.erode(self.drawn0.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        # where the two drawings overlap or MEET (1 px): a cut-out whose
        # pieces only abut has a seam line and no overlap, and tears too
        k = np.ones((3, 3), np.uint8)
        # (kept inside the drawn pixels, so the hull never starts outside the
        # silhouette and the 1 px erosion still takes the anti-aliased rim off)
        meet = (cv2.dilate(self.U0.astype(np.uint8), k) > 0) & (cv2.dilate(self.L0.astype(np.uint8), k) > 0)
        band = meet & self.drawn0 & _disk_mask(c, self.j["center"], self.radius)
        self.band = band
        ii, jj = np.nonzero(band)
        self.band_world = c.to_world(np.stack([jj + 0.5, ii + 0.5], 1))
        # each part carries only the band pixels it drew itself: a rim pixel
        # only the forearm drew is not the sleeve's to hold in place, and
        # carrying it with the sleeve would read the forearm's outline moving
        # by a pixel as a tear
        self.band_mine_up = self.U0[ii, jj]
        self.band_mine_lo = self.L0[ii, jj]
        self.band_up = self.upper.locate(self.upper.rest_tris, self.band_world[self.band_mine_up])
        self.band_lo = self.lower.locate(self.lower.rest_tris, self.band_world[self.band_mine_lo])

    @property
    def readable(self):
        """A joint with no band (the parts do not meet at rest) is a SKIP."""
        return bool(self.band.any()) and bool(self.upper_slots) and bool(self.lower_slots)

    def rest_drawn(self, world_pts, interior=False):
        """Did either part draw these setup-pose world points at rest?
        interior=True: and not on the drawing's 1 px rim."""
        c = self.rest.canvas
        px = np.floor(c.to_px(world_pts)).astype(np.int64)
        ok = (px[:, 0] >= 0) & (px[:, 0] < c.W) & (px[:, 1] >= 0) & (px[:, 1] < c.H)
        out = np.zeros(len(px), bool)
        out[ok] = (self.drawn0_in if interior else self.drawn0)[px[ok, 1], px[ok, 0]]
        return out

    def in_band(self, world_pts):
        c = self.rest.canvas
        px = np.floor(c.to_px(world_pts)).astype(np.int64)
        ok = (px[:, 0] >= 0) & (px[:, 0] < c.W) & (px[:, 1] >= 0) & (px[:, 1] < c.H)
        out = np.zeros(len(px), bool)
        out[ok] = self.band[px[ok, 1], px[ok, 0]]
        return out


def joint_rest(rest, joint, disk=DISK):
    """Cached JointRest on the rest frame."""
    cache = rest.cache.setdefault("joint_rest", {})
    key = (repr(sorted(as_joint(joint).items(), key=lambda kv: kv[0])), disk)
    if key not in cache:
        cache[key] = JointRest(rest, joint, disk)
    return cache[key]


# ---------------------------------------------------------------- pixel helpers

def union_alpha(frame, slots, box=None):
    """Composite alpha of `slots` (order does not matter for alpha), full
    canvas or the (i0, i1, j0, j1) window `box`."""
    H, W = frame.canvas.shape
    if box is None:
        out = np.zeros(H * W, np.float32)
        for s in slots:
            f = frame.frags.get(s)
            if f is not None and len(f.idx):
                out[f.idx] = out[f.idx] + f.alpha * (1 - out[f.idx])
        return out.reshape(H, W)
    i0, i1, j0, j1 = box
    out = np.zeros((i1 - i0) * (j1 - j0), np.float32)
    for s in slots:
        f = frame.frags.get(s)
        if f is None or not len(f.idx):
            continue
        r, c = np.divmod(f.idx, W)
        k = (r >= i0) & (r < i1) & (c >= j0) & (c < j1)
        idx = (r[k] - i0) * (j1 - j0) + (c[k] - j0)
        out[idx] = out[idx] + f.alpha[k] * (1 - out[idx])
    return out.reshape(i1 - i0, j1 - j0)


def _box(canvas, center_px, r_px):
    i0 = int(max(0, math.floor(center_px[1] - r_px)))
    i1 = int(min(canvas.H, math.ceil(center_px[1] + r_px) + 1))
    j0 = int(max(0, math.floor(center_px[0] - r_px)))
    j1 = int(min(canvas.W, math.ceil(center_px[0] + r_px) + 1))
    return i0, max(i1, i0 + 1), j0, max(j1, j0 + 1)


def _box_world(canvas, box, ii, jj):
    """World points of window pixel centres."""
    i0, _, j0, _ = box
    return canvas.to_world(np.stack([jj + j0 + 0.5, ii + i0 + 0.5], 1))


def _bbox_world(canvas, box, mask):
    ii, jj = np.nonzero(mask)
    if not len(ii):
        return None
    w = _box_world(canvas, box, ii, jj)
    lo, hi = w.min(0), w.max(0)
    return [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])]


def _result(gid, value, ok, worst=None, **extra):
    out = {"id": gid, "value": value, "pass": bool(ok), "worst": worst}
    out.update(extra)
    return out


# ---------------------------------------------------------------- G1

def folds(frame, slots=None, rest=None):
    """G1: folded triangles over the shown meshes, each against its mesh's
    setup winding (lbs.fold_mask; the same definition as qa/run.mjs G1).
    `slots` restricts the count; with `rest`, a triangle already folded in
    the rest frame does not count again (the budget asks what the MOTION
    folds, not what the art had)."""
    ev = frame.renderer.ev
    refs = ev.fold_references()
    per = {}
    for s, g in frame.geoms.items():
        if (slots is not None and s not in slots) or g.att["type"] not in ("mesh", "linkedmesh"):
            continue
        ref = refs.get((s, id(g.att)), -1)
        m = lbs.fold_mask(g.xy, g.uv, g.tris, reference=ref)
        g0 = rest.geoms.get(s) if rest is not None else None
        if g0 is not None and g0.att is g.att:
            m &= ~lbs.fold_mask(g0.xy, g0.uv, g0.tris, reference=ref)
        per[s] = int(m.sum())
    total = sum(per.values())
    worst = max(per, key=per.get) if total else None
    return _result("G1_FOLDS", total, total == 0, worst, slots={s: n for s, n in per.items() if n})


# ---------------------------------------------------------------- G2

def band_triangles(rest, jr, extent=BAND_EXTENT, lateral=BAND_LATERAL):
    """[(slot, triangle indices)] of the part meshes whose setup centroid is
    inside the joint band (axis-aligned with the joint's axis).

    The band is centred on j["band_center"] when the joint gives one, else
    on the joint: a one-sided band (a planted ankle, whose band runs up
    the shin from the pivot so the boot stays 100% foot) bends about the
    pivot but deforms only on one side, and a window centred on the pivot
    would count half rigid boot, whose area never changes, and read the
    band too lenient."""
    j = jr.j
    ext = float(j.get("extent") or extent) * jr.hw
    lat = lateral * jr.hw
    ax = np.asarray(j["axis"], float)
    ax = ax / (np.linalg.norm(ax) or 1.0)
    nrm = np.array([-ax[1], ax[0]])
    out = []
    for s in jr.upper_slots + jr.lower_slots:
        g = rest.geoms[s]
        if g.att["type"] not in ("mesh", "linkedmesh"):
            continue
        cen = g.xy[g.tris].mean(1) - np.asarray(j.get("band_center") or j["center"], float)
        a, b = cen @ ax, cen @ nrm
        k = np.nonzero((np.abs(a) <= ext) & (np.abs(b) <= lat))[0]
        if len(k):
            out.append((s, k))
    return out


def band_area_ratio(rest, frame, joint, min_ratio=MIN_BAND_RATIO, tri_range=TRI_RANGE, strict_tris=True):
    """G2: posed / rest signed area of the joint band's triangles (each mesh
    oriented by its setup winding, so folded triangles subtract) >=
    min_ratio, and, with strict_tris (default, SOTA §5), every band
    triangle's own ratio inside tri_range. "by" says which part failed
    ("band", "tris" or both); `worst` is the slot with the lowest ratio when
    the band fails, else the slot with the most triangles out of range;
    "tri_worst" names that slot's most extreme triangle (index into its
    mesh, ratio)."""
    jr = joint_rest(rest, joint)
    ev = rest.renderer.ev
    refs = ev.fold_references()
    num = den = 0.0
    tri_min, tri_max, bad, per = math.inf, -math.inf, 0, {}
    extreme = {}
    for s, k in band_triangles(rest, jr):
        g0, g1 = rest.geoms[s], frame.geoms.get(s)
        if g1 is None:
            continue
        ref = refs.get((s, id(g0.att)), -1)
        su = np.sign(lbs.signed_areas(g0.uv, g0.tris[k]))
        a0 = lbs.signed_areas(g0.xy, g0.tris[k]) * su * ref
        a1 = lbs.signed_areas(g1.xy, g1.tris[k]) * su * ref
        ok = a0 > 1e-9
        a0, a1, kk = a0[ok], a1[ok], k[ok]
        if not len(a0):
            continue
        num += a1.sum()
        den += a0.sum()
        r = a1 / a0
        tri_min, tri_max = min(tri_min, r.min()), max(tri_max, r.max())
        out = (r < tri_range[0]) | (r > tri_range[1])
        nb = int(out.sum())
        bad += nb
        per[s] = {"ratio": float(a1.sum() / a0.sum()), "tris": int(len(a0)), "tri_out_of_range": nb}
        if nb:
            # most extreme = furthest outside the range on a log scale
            dist = np.maximum(np.log(tri_range[0] / np.maximum(r, 1e-9)), np.log(np.maximum(r, 1e-9) / tri_range[1]))
            i = int(np.argmax(np.where(out, dist, -np.inf)))
            extreme[s] = (int(kk[i]), float(r[i]))
    if den <= 0:
        return _result("G2_AREA", 1.0, True, None, joint=jr.name, note="no mesh triangles in the band")
    ratio = num / den
    band_ok = ratio >= min_ratio
    tris_ok = bad == 0 or not strict_tris
    by = [n for n, good in (("band", band_ok), ("tris", tris_ok)) if not good]
    worst = tri_worst = None
    if not band_ok:
        worst = min(per, key=lambda s: per[s]["ratio"])
    elif not tris_ok:
        worst = max(per, key=lambda s: per[s]["tri_out_of_range"])
    if bad:
        w = worst if worst in extreme else max(extreme, key=lambda s: per[s]["tri_out_of_range"])
        tri_worst = {"slot": w, "tri": extreme[w][0], "ratio": extreme[w][1]}
    return _result("G2_AREA", float(ratio), band_ok and tris_ok, worst, joint=jr.name, min=min_ratio,
                   tri_min=float(tri_min), tri_max=float(tri_max), tri_out_of_range=bad, tri_range=list(tri_range),
                   strict_tris=bool(strict_tris), by=by, tri_worst=tri_worst, slots=per)


# ---------------------------------------------------------------- G4

def seam_gap(rest, frame, joint, disk=DISK, erode=1, masks=False, min_px=None):
    """G4: background opened inside the joint (see the module doc).
    The value counts only gap components of at least min_px pixels
    (default G4_MIN_PX, scale 1; "raw_px" is the count before that rule).
    masks=True adds "masks": {"box": (i0, i1, j0, j1), "gap", "hull"}."""
    import cv2
    min_px = G4_MIN_PX if min_px is None else min_px
    jr = joint_rest(rest, joint, disk)
    pair = "|".join(jr.upper_slots[-1:] + jr.lower_slots[-1:]) or jr.name
    if not jr.readable:
        return _result("G4_SEAM_GAP", 0, True, None, joint=jr.name, skipped="the parts share no pixel at rest")
    c = frame.canvas
    cn = center_now(rest, frame, jr.j)
    cpx = c.to_px([cn])[0]
    rpx = jr.radius * c.scale
    box = _box(c, cpx, rpx + 2)
    i0, i1, j0, j1 = box
    h, w = i1 - i0, j1 - j0
    posed_u, posed_l = jr.upper.posed(frame), jr.lower.posed(frame)
    carried = np.concatenate([jr.upper.carry_forward(frame, None, jr.band_up, posed_u),
                              jr.lower.carry_forward(frame, None, jr.band_lo, posed_l)])
    hp = c.to_px(carried) - [j0 + 0.5, i0 + 0.5]   # cv2 puts pixel centres on integers
    hull = cv2.convexHull(np.round(hp * 16).astype(np.int32))
    region = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(region, hull, 1, lineType=cv2.LINE_8, shift=4)
    ii, jj = np.mgrid[i0:i1, j0:j1]
    region &= ((jj + 0.5 - cpx[0]) ** 2 + (ii + 0.5 - cpx[1]) ** 2 <= rpx * rpx).astype(np.uint8)
    if erode:
        region = cv2.erode(region, np.ones((3, 3), np.uint8), iterations=int(erode))
    alpha = union_alpha(frame, jr.upper_slots + jr.lower_slots, box)
    cand = (region > 0) & (alpha < COVER)
    gap = np.zeros_like(cand)
    if cand.any():
        ci, cj = np.nonzero(cand)
        pts = _box_world(c, box, ci, cj)
        hit = jr.rest_drawn(jr.upper.carry_back(frame, pts, posed_u), interior=erode > 0) | \
            jr.rest_drawn(jr.lower.carry_back(frame, pts, posed_l), interior=erode > 0)
        gap[ci[hit], cj[hit]] = True
    raw = int(gap.sum())
    gap = drop_specks(gap, min_px * c.scale ** 2)
    n = int(gap.sum())
    out = _result("G4_SEAM_GAP", n, n == 0, pair if n else None, joint=jr.name,
                  area=n / c.scale ** 2, box=_bbox_world(c, box, gap), hull_px=int((region > 0).sum()),
                  depth=_depth(gap) / c.scale, raw_px=raw, min_px=min_px)
    if masks:
        out["masks"] = {"box": box, "gap": gap, "hull": region > 0}
    return out


def drop_specks(mask, min_px):
    """The mask without its 8-connected components of fewer than min_px
    pixels. G4's calibration rule, see G4_MIN_PX."""
    import cv2
    if min_px <= 1 or not mask.any():
        return mask
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = st[:, cv2.CC_STAT_AREA] >= min_px
    keep[0] = False
    return keep[lab]


def _depth(mask):
    """Largest inscribed radius of a pixel mask, px (1 for a 1 px sliver)."""
    import cv2
    if not mask.any():
        return 0.0
    return float(cv2.distanceTransform(np.pad(mask.astype(np.uint8), 1), cv2.DIST_L2, 5).max())


# ---------------------------------------------------------------- G5

def _closed_background(solid):
    """(labels, stats, ids): 4-connected background components that do not
    touch the canvas edge."""
    import cv2
    bg = (~solid).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bg, connectivity=4)
    if n <= 1:
        return np.zeros_like(lab), stats, []
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    keep = np.ones(n, bool)
    keep[0] = False
    keep[edge] = False
    lab = np.where(keep[lab], lab, 0)
    return lab, stats, np.nonzero(keep)[0].tolist()


def holes(rest, frame, min_area=HOLE_MIN_AREA, masks=False):
    """G5: background that the art itself encloses and that was covered at
    rest: >= min_area px (scale 1) per 8-connected component.

    The slots split into those that moved since rest and those that did
    not. A pixel is a hole pixel when, in the frame, nothing is drawn there
    (alpha < 0.5), it lies in a closed background component of ONE group's
    own art (the static slots alone, or the moved slots alone), and at rest
    something covered it (for the moved group: at the point the moved art
    carries it back to). So a moved arm uncovering an unpainted patch inside
    the torso counts; an arm, the torso and a leg closing a loop around
    open space does not (no single group encloses it: the false positive
    SOTA §8.3 warns about), nor does a hole the art showed at rest."""
    import cv2
    c = frame.canvas
    H, W = c.shape
    moved = moved_slots(rest, frame)
    static = [s for s in frame.slots if s not in moved]
    mov = [s for s in frame.slots if s in moved]
    bare = frame.alpha < COVER
    covered0 = rest.alpha >= COVER
    hole = np.zeros((H, W), bool)
    owner = np.full((H, W), -1, np.int8)
    for gi, group in enumerate((static, mov)):
        if not group:
            continue
        lab, _, ids = _closed_background(union_alpha(frame, group) >= COVER)
        cand = (lab > 0) & bare
        if not cand.any():
            continue
        ii, jj = np.nonzero(cand)
        if gi == 0:
            hit = covered0[ii, jj]
        else:
            back = Part(rest, mov).carry_back(frame, c.to_world(np.stack([jj + 0.5, ii + 0.5], 1)))
            px = np.floor(c.to_px(back)).astype(np.int64)
            ok = (px[:, 0] >= 0) & (px[:, 0] < W) & (px[:, 1] >= 0) & (px[:, 1] < H)
            hit = np.zeros(len(ii), bool)
            hit[ok] = covered0[px[ok, 1], px[ok, 0]]
        hole[ii[hit], jj[hit]] = True
        owner[ii[hit], jj[hit]] = gi
    found = []
    if hole.any():
        n, lab, stats, _ = cv2.connectedComponentsWithStats(hole.astype(np.uint8), connectivity=8)
        min_px = min_area * c.scale ** 2
        for k in range(1, n):
            if stats[k, 4] < min_px:
                continue
            x, y, w, h = stats[k, :4]
            ii, jj = np.nonzero(lab[y:y + h, x:x + w] == k)
            ii, jj = ii + y, jj + x
            group = static if np.bincount(owner[ii, jj] + 1, minlength=3).argmax() == 1 else mov
            found.append({"area": int(stats[k, 4]) / c.scale ** 2, "px": int(stats[k, 4]),
                          "slot": _hole_owner(frame, ii, jj, group), "box": _pix_box_world(c, ii, jj),
                          "enclosed_by": "static" if group is static else "moved"})
    found.sort(key=lambda f: -f["area"])
    worst = found[0]["slot"] if found else None
    out = _result("G5_HOLES", len(found), not found, worst, holes=found[:10],
                  area=float(sum(f["area"] for f in found)))
    if masks:
        keep = np.zeros((H, W), bool)
        if hole.any():
            big = np.nonzero(stats[:, 4] >= min_area * c.scale ** 2)[0]
            keep = np.isin(lab, big[big > 0])
        out["masks"] = {"holes": keep}
    return out


def _pix_box_world(c, ii, jj):
    w = c.to_world(np.stack([jj + 0.5, ii + 0.5], 1))
    lo, hi = w.min(0), w.max(0)
    return [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])]


def _hole_owner(frame, ii, jj, slots=None):
    """The slot a hole is in: the one whose geometry (mesh or quad, ignoring
    texture alpha) covers most of it, else the one drawn most on its rim."""
    from .raster import raster_triangles
    c = frame.canvas
    flat = ii * c.W + jj
    lo, hi = c.to_world(np.array([[jj.min(), ii.max() + 1]], float))[0], \
        c.to_world(np.array([[jj.max() + 1, ii.min()]], float))[0]
    best, best_n = None, 0
    for s, g in frame.geoms.items():
        if slots is not None and s not in slots:
            continue
        mn, mx = g.xy.min(0), g.xy.max(0)
        if mx[0] < lo[0] or mn[0] > hi[0] or mx[1] < lo[1] or mn[1] > hi[1]:
            continue
        pix, _, _ = raster_triangles(c.to_px(g.xy), g.tris, c.shape)
        n = int(np.isin(flat, pix).sum())
        if n > best_n:
            best, best_n = s, n
    if best is not None:
        return best
    import cv2
    m = np.zeros(c.shape, np.uint8)
    m[ii, jj] = 1
    rim = (cv2.dilate(m, np.ones((3, 3), np.uint8)) > 0) & (m == 0)
    names = np.array(frame.slots + [None], dtype=object)
    owners = names[frame.top[rim]]
    owners = [o for o in owners if o is not None and (slots is None or o in slots)]
    return max(set(owners), key=owners.count) if owners else None


# ---------------------------------------------------------------- G6

def ghost_band(rest, frame, joint, max_len=GHOST_MAX_LEN, disk=DISK, masks=False):
    """G6(a): the overlap band of one part exposed where its partner, from
    its own point of view, drew nothing at rest; length = how far it sticks
    out past the partner, limit max_len x limb width."""
    import cv2
    jr = joint_rest(rest, joint, disk)
    limit = max_len * 2 * jr.hw
    if not jr.readable:
        return _result("G6A_GHOST_BAND", 0.0, True, None, joint=jr.name, max=limit,
                       skipped="the parts share no pixel at rest")
    c = frame.canvas
    cpx = c.to_px([center_now(rest, frame, jr.j)])[0]
    # the band lies within the rest disk; a carried band pixel stays within
    # about one radius of the joint, plus what it may stick out (limit)
    box = _box(c, cpx, (jr.radius + limit) * c.scale + 3)
    U = union_alpha(frame, jr.upper_slots, box) >= COVER
    L = union_alpha(frame, jr.lower_slots, box) >= COVER
    posed = {"u": jr.upper.posed(frame), "l": jr.lower.posed(frame)}
    best = (0.0, None, None)
    total = 0
    ghost_mask = np.zeros(U.shape, bool)
    for own, other, mask_own, mask_other, name in (
            (jr.upper, jr.lower, U, L, "u"), (jr.lower, jr.upper, L, U, "l")):
        only = mask_own & ~mask_other
        if not only.any():
            continue
        ii, jj = np.nonzero(only)
        pts = _box_world(c, box, ii, jj)
        keep = jr.in_band(own.carry_back(frame, pts, posed[name]))
        if not keep.any():
            continue
        oth = "l" if name == "u" else "u"
        keep[keep] = ~jr.rest_drawn(other.carry_back(frame, pts[keep], posed[oth]))
        if not keep.any():
            continue
        total += int(keep.sum())
        ghost_mask[ii[keep], jj[keep]] = True
        dist = cv2.distanceTransform((~mask_other).astype(np.uint8), cv2.DIST_L2, 5)
        d = float(dist[ii[keep], jj[keep]].max()) / c.scale
        if d > best[0]:
            slot = own.slots[-1] if len(own.slots) == 1 else _top_slot(frame, box, ii[keep], jj[keep], own.slots)
            best = (d, slot, (ii[keep], jj[keep]))
    worst = best[1] if best[0] > limit else None
    out = _result("G6A_GHOST_BAND", best[0], best[0] <= limit, worst, joint=jr.name, max=limit, px=total)
    if masks:
        out["masks"] = {"box": box, "ghost": ghost_mask}
    return out


def _top_slot(frame, box, ii, jj, slots):
    i0, _, j0, _ = box
    names = np.array(frame.slots + [None], dtype=object)
    owners = [o for o in names[frame.top[ii + i0, jj + j0]] if o in slots]
    return max(set(owners), key=owners.count) if owners else slots[-1]


def ghost_paint(rest, frame, max_ncc=GHOST_MAX_NCC, min_px=NCC_MIN_PX, slots=None, erode=None, masks=False,
                visibility=False):
    """G6(b): newly revealed pixels of an underlying slot vs what its
    occluder showed there at rest (texel identity; see the module doc).
    The value is the highest NCC over (revealed slot, occluder) pairs with
    at least min_px pixels (scale 1); `worst` is "revealed|occluder".

    The correlation is symmetric: it is as high when the OCCLUDER's image
    carries a copy of the underlay (a flat-image cut that left a shard of
    trousers in the hand layer) as when the underlay carries a copy of the
    occluder (an arm left in the torso paint). Only the second shows where
    the pixels are revealed: the first reveals trousers where trousers were.
    So a pair that correlates is checked for who holds the copy: if most
    of its revealed pixels that lie in a duplicate (duplicates()) lie in
    one the OCCLUDER carries, the pair is listed under "carried" instead of
    judged: the shard is the occluder layer's fault, goes to the art
    request list (budget.json "artreq"), and is not a limit of the joint. visibility=True adds, per carried pair, "visible_px": the
    shard pixels now drawn over background or over paint that does not
    match them (carried_visible()).
    masks=True adds "masks": {"revealed": (H, W) bool of the judged pixels
    of the worst pair}."""
    import cv2
    c = frame.canvas
    H, W = c.shape
    erode = NCC_ERODE if erode is None else erode
    rest_names = list(rest.slots)
    rest_rank = {s: k for k, s in enumerate(rest_names)}
    top_now = frame.top.reshape(-1)
    tex_now = frame.top_texel.reshape(-1)
    names_now = frame.slots
    rest_top = rest.top.reshape(-1)
    rest_tex = rest.top_texel.reshape(-1)
    moved = moved_slots(rest, frame)
    # only pixels whose (slot, texel) changed can show something new
    now_rank = np.array([rest_rank.get(s, -1) for s in names_now] + [-1])[top_now]
    changed = (top_now >= 0) & ((now_rank != rest_top) | (tex_now != rest_tex))
    idx = np.nonzero(changed)[0]
    moved_rank = np.array([s in moved for s in rest_names], bool)
    pairs = {}
    img_now = frame.image.reshape(-1, 4)
    img_rest = rest.image.reshape(-1, 4)
    for u in np.unique(top_now[idx]):
        uname = names_now[u]
        if uname not in rest_rank or (slots is not None and uname not in slots):
            continue
        p = idx[top_now[idx] == u]
        q = texel_home(rest, uname)[tex_now[p]]
        ok = q >= 0
        p, q = p[ok], q[ok]
        occ = rest_top[q]
        hidden = (occ >= 0) & (occ > rest_rank[uname])
        hidden[hidden] = moved_rank[occ[hidden]]
        p, q, occ = p[hidden], q[hidden], occ[hidden]
        for o in np.unique(occ):
            k = occ == o
            pairs[(uname, rest_names[o])] = (p[k], q[k])
    best, items, carried = (0.0, None, None), [], []
    need = max(min_px * c.scale ** 2, 4)
    for (u, o), (p, q) in pairs.items():
        m = np.zeros(H * W, np.uint8)
        m[p] = 1
        inner = cv2.erode(m.reshape(H, W), _disk_kernel(erode * c.scale)).reshape(-1)
        k = inner[p] > 0
        if k.sum() < need:
            continue
        v = ncc(img_now[p[k], :3], img_rest[q[k], :3])
        if v > max_ncc:
            # a duplicate: which layer holds it? (only asked once the
            # pattern correlates: two layers sharing a palette agree pixel
            # by pixel by chance, which duplicates() alone would take for a
            # copy)
            dup = duplicates(rest, u, o)
            lab = dup["labels"][q[k]]
            mine = dup["carried"][q[k]]
            if (lab > 0).any() and mine.sum() >= 0.5 * (lab > 0).sum():
                it = {"slot": u, "occluder": o, "px": int(k.sum()), "ncc": v,
                      "components": sorted(int(x) for x in np.unique(lab[mine]))}
                if visibility:
                    it["visible_px"] = carried_visible(rest, frame, u, o, it["components"])
                carried.append(it)
                continue
        items.append({"slot": u, "occluder": o, "px": int(k.sum()), "ncc": v})
        if v > best[0]:
            best = (v, f"{u}|{o}", p[k])
    items.sort(key=lambda it: -it["ncc"])
    ok = best[0] <= max_ncc
    out = _result("G6B_GHOST_PAINT", float(best[0]), ok, None if ok else best[1], max=max_ncc, pairs=items[:10],
                  carried=carried)
    if masks:
        m = np.zeros(H * W, bool)
        if best[2] is not None:
            m[best[2]] = True
        out["masks"] = {"revealed": m.reshape(H, W)}
    return out


def _lab(rgb):
    """CIELAB (L 0-100) of straight RGB in [0, 1], (n, 3) float32."""
    import cv2
    x = np.clip(np.asarray(rgb, np.float32), 0, 1).reshape(-1, 1, 3)
    return cv2.cvtColor(x, cv2.COLOR_RGB2LAB).reshape(-1, 3)


def _straight(premult):
    a = premult[:, 3:4]
    return premult[:, :3] / np.maximum(a, 1e-6)


def _seam_de(comp, under_vis, lab, reach):
    """How the copy meets the underlay's visible paint across the
    occluder's edge: for every visible-underlay pixel within `reach` px of
    the component, the colour distance to the nearest component pixel
    (both (h, w) masks of one box; lab (h, w, 3) CIELAB of the rest frame).
    Returns (median distance, number of such pixels); (inf, 0) if the copy
    never meets visible underlay paint."""
    from scipy import ndimage
    dist, (iy, ix) = ndimage.distance_transform_edt(~comp, return_indices=True)
    near = under_vis & (dist <= reach)
    n = int(near.sum())
    if not n:
        return math.inf, 0
    a = lab[near]
    b = lab[iy[near], ix[near]]
    return float(np.median(np.linalg.norm(a - b, axis=1))), n


def duplicates(rest, under, occluder):
    """Where the occluder's paint at rest repeats the paint of the slot
    under it, and which of the two layers carries the copy. Cached.

    A DUPLICATE pixel is one the occluder covers at rest while `under`
    draws beneath it (alpha >= 0.5) with nearly the same colour (CIELAB
    distance < DUP_DE); duplicate pixels are grouped into 8-connected
    components of >= DUP_MIN_PX px. A component's OWNER is read off the
    occluder's edge, where the copy meets the underlay's own visible paint:
    a shard of the underlay cut into the occluder continues that paint
    SEAMLESSLY across the edge (that is why it cannot be seen at rest), so
    the median colour distance between the visible underlay pixels within
    SEAM_REACH px of the copy and their nearest copy pixel is < DUP_DE, over
    at least SEAM_MIN_PX px of contact: owner "occluder" (a shard of
    trousers cut into the hand image). A copy of the occluder left in the
    underlay instead meets the underlay's paint at the occluder's outline,
    which is an edge in the art (an outline stroke, another colour): owner
    "under" (the arm left in the torso). Two layers sharing one palette (a
    striped sleeve over a striped shirt) agree pixel by pixel by chance but
    do not continue each other across the edge, so they also read "under",
    which G6b keeps judging: a doubt never hides a ghost.

    Returns {"labels": (H*W,) int32 component id (0 none), "carried": (H*W,)
    bool pixels of occluder-owned components, "components": {id: {"px",
    "owner", "seam_de", "seam_px"}}}."""
    import cv2
    cache = rest.cache.setdefault("duplicates", {})
    key = (under, occluder)
    if key in cache:
        return cache[key]
    c = rest.canvas
    H, W = c.shape
    n = H * W
    rank = {s: k for k, s in enumerate(rest.slots)}
    labels = np.zeros(n, np.int32)
    carried = np.zeros(n, bool)
    comps = {}
    fu = rest.frags.get(under)
    if fu is None or occluder not in rank or under not in rank:
        cache[key] = {"labels": labels, "carried": carried, "components": comps}
        return cache[key]
    top = rest.top.reshape(-1)
    ua = np.zeros(n, np.float32)
    ua[fu.idx] = fu.alpha
    ov = np.nonzero((top == rank[occluder]) & (ua >= COVER))[0]
    img = rest.image.reshape(-1, 4)
    ucol = np.zeros((n, 3), np.float32)
    ucol[fu.idx] = _straight(fu.rgba)
    dup = np.zeros(n, np.uint8)
    if len(ov):
        de = np.linalg.norm(_lab(_straight(img[ov])) - _lab(ucol[ov]), axis=1)
        dup[ov[de < DUP_DE]] = 1
    nl, lab, st, _ = cv2.connectedComponentsWithStats(dup.reshape(H, W), connectivity=8)
    lab = lab.reshape(-1)
    under_vis = (top == rank[under]).reshape(H, W)
    reach = max(2.0, SEAM_REACH * c.scale)
    pad = int(math.ceil(reach)) + 2
    for i in range(1, nl):
        if st[i, cv2.CC_STAT_AREA] < DUP_MIN_PX * c.scale ** 2:
            continue
        x0, y0, bw, bh = st[i, cv2.CC_STAT_LEFT], st[i, cv2.CC_STAT_TOP], st[i, cv2.CC_STAT_WIDTH], st[i, cv2.CC_STAT_HEIGHT]
        i0, i1, j0, j1 = max(0, y0 - pad), min(H, y0 + bh + pad), max(0, x0 - pad), min(W, x0 + bw + pad)
        comp = lab.reshape(H, W)[i0:i1, j0:j1] == i
        box_lab = _lab(_straight(img.reshape(H, W, 4)[i0:i1, j0:j1].reshape(-1, 4))).reshape(i1 - i0, j1 - j0, 3)
        de, n_seam = _seam_de(comp, under_vis[i0:i1, j0:j1], box_lab, reach)
        owner = "occluder" if n_seam >= SEAM_MIN_PX * c.scale and de < DUP_DE else "under"
        full = lab == i
        labels[full] = i
        if owner == "occluder":
            carried[full] = True
        comps[i] = {"px": int(comp.sum()), "owner": owner, "seam_de": de, "seam_px": n_seam}
    cache[key] = {"labels": labels, "carried": carried, "components": comps}
    return cache[key]


def carried_texels(rest, under, occluder, components):
    """The occluder's texels (flat texture indices) that make up the given
    duplicate components, and their bbox in the occluder's image
    (x0, y0, x1, y1)."""
    dup = duplicates(rest, under, occluder)
    pix = np.nonzero(np.isin(dup["labels"], list(components)))[0]
    tex = np.unique(rest.top_texel.reshape(-1)[pix])
    tex = tex[tex >= 0]
    g = rest.geoms[occluder]
    t = rest.renderer.texture(g.tex_key)
    if not len(tex):
        return tex, None
    ty, tx = np.divmod(tex, t.w)
    return tex, (int(tx.min()), int(ty.min()), int(tx.max()) + 1, int(ty.max()) + 1)


def carried_visible(rest, frame, under, occluder, components, min_de=DUP_DE):
    """How many pixels of a shard the occluder carries show in `frame`: the
    shard's texels drawn on top there, over background (what is under them
    has alpha < 0.5) or over paint whose colour differs by >= min_de. At
    rest (and wherever the shard still lies over matching paint) this is 0:
    the shard is camouflaged until it moves over something else."""
    tex, _ = carried_texels(rest, under, occluder, components)
    f = frame.frags.get(occluder)
    if f is None or not len(tex) or occluder not in frame.slots:
        return 0
    io = frame.slots.index(occluder)
    top = frame.top.reshape(-1)
    on = np.isin(f.texel, tex) & (f.alpha >= COVER)
    pix = f.idx[on]
    sel = top[pix] == io
    pix, col = pix[sel], f.rgba[on][sel]
    if not len(pix):
        return 0
    n = frame.canvas.H * frame.canvas.W
    where = np.full(n, -1, np.int64)
    where[pix] = np.arange(len(pix))
    under_img = np.zeros((len(pix), 4), np.float32)
    for s in frame.slots[:io]:   # what is drawn below the occluder there, composited
        g = frame.frags.get(s)
        if g is None or frame.geoms[s].blend == "additive":
            continue
        k = where[g.idx]
        hit = k >= 0
        if hit.any():
            dst = under_img[k[hit]]
            under_img[k[hit]] = g.rgba[hit] + dst * (1 - g.rgba[hit][:, 3:4])
    bare = under_img[:, 3] < COVER
    de = np.linalg.norm(_lab(_straight(col)) - _lab(_straight(np.maximum(under_img, 1e-6))), axis=1)
    return int((bare | (de >= min_de)).sum())


def _disk_kernel(r):
    """A disc structuring element of radius r px (at least the 3x3 cross)."""
    k = max(1, int(round(r)))
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
    return (xx * xx + yy * yy <= r * r + 0.5).astype(np.uint8) if r >= 1 else \
        np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], np.uint8)


def moved_slots(rest, frame, tol=1e-3):
    """Slots whose drawn geometry differs from rest by more than `tol`
    world units (or that changed attachment)."""
    out = set()
    for s, g in frame.geoms.items():
        g0 = rest.geoms.get(s)
        if g0 is None or g0.att is not g.att or g0.xy.shape != g.xy.shape or np.abs(g0.xy - g.xy).max() > tol:
            out.add(s)
    return out


def ncc(x, y):
    """Normalised cross-correlation of two (n, 3) colour samples, each
    channel mean-free (a pattern match, not a mean-colour match). Flat
    samples (no variance) correlate 0."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    x = x - x.mean(0)
    y = y - y.mean(0)
    den = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    if den < 1e-9 * max(1, x.size):
        return 0.0
    return float((x * y).sum() / den)


def texel_home(rest, slot):
    """For each texel of the slot's texture, the rest-frame flat pixel it
    lands on (-1 where the slot does not draw it). Cached."""
    cache = rest.cache.setdefault("texel_home", {})
    if slot not in cache:
        r = rest.renderer
        g = rest.geoms[slot]
        idx = np.arange(rest.canvas.H * rest.canvas.W, dtype=np.int64).reshape(rest.canvas.shape)
        cache[slot] = r.texture_lookup(g, idx, rest.canvas, fill=-1).reshape(-1)
    return cache[slot]


def ghost(rest, frame, joint=None, max_len=GHOST_MAX_LEN, max_ncc=GHOST_MAX_NCC):
    """G6: (a) at the joint (when given) and (b) over the whole frame."""
    a = ghost_band(rest, frame, joint, max_len) if joint is not None else None
    b = ghost_paint(rest, frame, max_ncc)
    ok = b["pass"] and (a is None or a["pass"])
    worst = (a["worst"] if a is not None and not a["pass"] else None) or b["worst"]
    return _result("G6_GHOST", {"band_len": None if a is None else a["value"], "ncc": b["value"]}, ok, worst,
                   band=a, paint=b)


# ---------------------------------------------------------------- all of them

def evaluate(rest, frame, joints=()):
    """Every art gate on one frame: G1, G5, G6(b) over the body; G2, G4,
    G6(a) per joint. Returns {"pass", "gates": [...]}."""
    gates = [folds(frame, rest=rest), holes(rest, frame), ghost_paint(rest, frame)]
    for j in joints:
        gates += [band_area_ratio(rest, frame, j), seam_gap(rest, frame, j), ghost_band(rest, frame, j)]
    return {"pass": all(g["pass"] for g in gates), "gates": gates}
