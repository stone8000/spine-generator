"""Layer alpha -> a Spine mesh that hugs the art and can bend at its joints.

    build(alpha, origin, bands=(), eps=1.75, min_angle=28.0, max_area=None)
        -> {"verts", "uvs", "tris", "hull", ...}
    grid_equivalent_vertex_count(alpha, cell=22) -> int

WHY THIS AND NOT A GRID (SOTA §2 網格, [E2])
  Hard Time's grid_mesh() lays a 22 px grid over the layer's bounding box.
  Its border vertices sit outside the art, so weights get extrapolated onto
  empty space; it has no edge where a limb should fold; and it spends
  vertices on area rather than on outline. Tracing the outline and filling it
  with a quality constrained Delaunay triangulation puts vertices where the
  silhouette needs them (E2: 2634 -> 966 vertices on the warden, transparent
  overdraw 5.8-51.9% -> 0.6-9.7%).

  The price is that a straight limb gets almost no vertices along its length,
  so it cannot bend smoothly: a joint needs rows of vertices across the limb
  inside the joint band. `bands` inserts those rows as constrained points
  BEFORE refinement, so the quality pass triangulates around them instead of
  ignoring them. Where the rows go is row_offsets(); how long the band is,
  BAND_EXTENT.

THE BAND IS LONGER THAN SOTA §2 SAYS (measured, E12)
  SOTA §2 gives a band of 1.0-1.5x the local half-width h. [E1], the numpy
  experiment that SOTA cites, actually ramped over +-40 px on a limb with
  h = 20, i.e. +-2h. In the official runtime (elbow42.py, spine-core
  4.2.120, two-layer synthetic arm, half-angle helper, rows=3) the elbow's
  inner side folds first at 120 deg with e = 1.25h, at 140 deg with 1.5h,
  and not before 150 deg with 2h; G2's single-triangle ratio >= 0.3 holds to
  80 / 90 / 110 deg. That matches the continuum limit of a linear ramp, whose
  inner side folds at 4 atan(e / 2h) with the helper (128 / 147 / 180 deg).
  The band's area at 120 deg barely depends on e (0.89-0.90 of the
  silhouette; continuum 1 - (1 - cos 60)/6 = 0.917 for any e). So
  BAND_EXTENT is 2.0: same area, much more fold margin. A caller that needs
  a shorter band (a short forearm, a band that would reach the next joint)
  passes "extent"; the joint given to weights.compute and to the G2 gate
  must carry the same "extent".

PIPELINE
  1. art mask: alpha > ALPHA_MIN (Hard Time's threshold), grown by DILATE px
     so that the simplified outline still covers anti-aliased edges;
  2. one outline: islands smaller than MIN_ISLAND px are dropped, larger
     ones are bridged to the main body with a thin strip (Spine's hull is
     one polygon; the strip is transparent in the texture, so nothing shows);
  3. OpenCV findContours (outer + holes, RETR_CCOMP) -> approxPolyDP(eps);
     a simplification that self-intersects is retried at a smaller eps,
     a hole that still collides with the outline is filled instead (filling
     a hole draws transparent texels: it looks the same);
  4. joint-band rows: points where each row crosses the outline are spliced
     into the outline (pushed SPLICE_BULGE off it, see _splice), interior
     points are added along the row;
  5. PythonCDT: conforming CDT of the outline, refinement to `min_angle`
     (and `max_area`), outer triangles and holes erased;
  6. vertices reordered hull first, in outline order (the editor's hull rule,
     L8 MESH), then hole rims, then interior vertices.

COORDINATES
  Layer pixels are image space: x right, y down, pixel (i, j) covers
  [j, j+1) x [i, i+1). `origin` is the canvas position of alpha[0, 0]'s
  corner. Returned `verts` are canvas coordinates (still y down; the rig
  builder is the one place that converts to the y-up world), `uvs` are
  verts relative to the layer image in [0, 1].
"""
import math

import cv2
import numpy as np
import pythoncdt as cdt

from . import meshtopo

ALPHA_MIN = 8          # alpha > 8 is art (Hard Time grid_mesh, E2)
DILATE = 1             # px the mask grows before tracing (E2: 1 px, <= 0.7% opaque missed)
MIN_ISLAND = 30        # px^2 after the DILATE growth: smaller separate specks are dropped (E2: same cut)
MIN_HOLE = 30          # px^2: smaller holes are filled
BAND_EXTENT = 2.0      # a joint band reaches +-BAND_EXTENT * halfwidth along the limb (module docstring)
BAND_ROWS = 3          # rows per half band (row_offsets): 8 rows across the band
CENTER_PAIR = 0.1      # the centre row is a pair at +-0.1 band half-lengths (row_offsets)
MIN_EDGE = 1.0         # px: refinement never makes edges shorter than this
MAX_STEINER = 20000    # refinement budget per layer (a safety cap, never reached in practice)
PAD = 2                # px of empty border added before tracing, so art touching the edge still closes
SPLICE_BULGE = 0.02    # px a band row's end point is pushed off its outline edge, at most (see _splice)
CLEAR = 2.0 * MIN_EDGE # px: a row point this close to an outline vertex snaps to it; interior row
                       # points keep this far from the outline and from each other (closer ones make
                       # edges the refiner may not split, i.e. slivers it cannot repair)


# ---------------------------------------------------------------- public

def build(alpha, origin, bands=(), eps=1.75, min_angle=28.0, max_area=None):
    """Triangulate one layer's alpha (see the module docstring).

    alpha     (H, W) uint8 layer alpha
    origin    canvas (x, y) of alpha[0, 0]
    bands     [{"center": (x, y), "axis": (dx, dy), "halfwidth": px,
                "rows": per half band (default BAND_ROWS),
                "extent": half-length along the axis in halfwidths (default BAND_EXTENT),
                "offsets": optional explicit row positions in [-1, 1] (fractions of
                the half-length; overrides rows)}] in canvas coordinates; rows of
              vertices perpendicular to `axis` go at row_offsets(rows) * extent *
              halfwidth from the center, each from outline to outline
    eps       approxPolyDP tolerance in px
    min_angle refinement target in degrees (CDT smallest-angle criterion)
    max_area  optional largest triangle area in px^2

    Returns {"verts": (N, 2) canvas, "uvs": (N, 2), "tris": (M, 3) int,
    "hull": int (verts[:hull] is the outer outline, in order), "holes": int,
    "band_rows": per band, per row (in axis order), the vertex indices of that
    row: its two outline ends first, then its interior points,
    "stats": {vertices, triangles, hull, holes, min_angle, grid_vertices,
    unrefined (CDT's counts of what refinement could not fix), missed_art,
    missed_solid, overdraw, islands_dropped_px, islands_bridged}}.
    """
    alpha = np.asarray(alpha)
    if alpha.ndim != 2:
        raise ValueError(f"alpha must be (H, W), got {alpha.shape}")
    h, w = alpha.shape
    origin = np.asarray(origin, float).reshape(2)
    mask, art_stats = _art_mask(alpha)
    outer, holes = _outlines(mask, eps, (w, h))
    loops = [outer] + holes
    bands = list(bands)
    loops, interior, row_points = _band_points(loops, bands, origin)
    tv, tt, refine = _triangulate(loops, interior, min_angle, max_area)
    tv, tt, hull, nholes = _hull_first(tv, tt)
    rows = _locate(tv, row_points)
    out = {
        "verts": tv + origin,
        "uvs": tv / np.array([w, h], float),
        "tris": tt,
        "hull": hull,
        "holes": nholes,
        "band_rows": rows,
    }
    out["stats"] = dict(art_stats, vertices=len(tv), triangles=len(tt), hull=hull, holes=nholes,
                        min_angle=float(min_angles(tv, tt).min()) if len(tt) else 0.0,
                        grid_vertices=grid_equivalent_vertex_count(alpha),
                        unrefined=refine, **coverage(alpha, out, origin))
    return out


def grid_equivalent_vertex_count(alpha, cell=22):
    """Vertices Hard Time's grid_mesh() would give this layer (the P2
    "<= 60% of the grid" yardstick): a grid of `cell` px over the bbox of
    alpha > 8, keeping cells with art within 3 px of them."""
    a = np.asarray(alpha) > ALPHA_MIN
    ys, xs = np.nonzero(a)
    if not len(xs):
        return 0
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    nx = max(2, int(math.ceil((x1 - x0) / cell)) + 1)
    ny = max(2, int(math.ceil((y1 - y0) / cell)) + 1)
    gx, gy = np.linspace(x0, x1, nx), np.linspace(y0, y1, ny)
    # summed-area table: "any art in the padded cell" in O(1) per cell
    sat = np.pad(a.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    H, W = a.shape
    xa = np.clip(gx[:-1].astype(int) - 3, 0, W)
    xb = np.clip(np.ceil(gx[1:]).astype(int) + 3, 0, W)
    ya = np.clip(gy[:-1].astype(int) - 3, 0, H)
    yb = np.clip(np.ceil(gy[1:]).astype(int) + 3, 0, H)
    YA, XA = np.meshgrid(ya, xa, indexing="ij")
    YB, XB = np.meshgrid(yb, xb, indexing="ij")
    keep = (sat[YB, XB] - sat[YA, XB] - sat[YB, XA] + sat[YA, XA]) > 0
    used = np.zeros((ny, nx), bool)
    used[:-1, :-1] |= keep
    used[:-1, 1:] |= keep
    used[1:, 1:] |= keep
    used[1:, :-1] |= keep
    return int(used.sum())


def outlines(m):
    """[outer outline, hole rims...] of a built mesh as (k, 2) canvas
    polygons: what weights.compute takes as `outline` for its visibility
    test (the outer outline alone is verts[:hull])."""
    V = np.asarray(m["verts"], float)
    loops = meshtopo.boundary_loops(np.asarray(m["tris"]).tolist()) or []
    hull = int(m["hull"])
    out = [V[:hull]]
    for lp in loops:
        if not set(lp) <= set(range(hull)):
            out.append(V[lp])
    return out


def row_offsets(rows):
    """Row positions across a band, as fractions of its half-length e.

    Each half of the band (joint centre to band end) is cut into `rows`
    equal slices and gets a row on every slice boundary, the band's ends
    included, so the mesh carries the weight ramp's breakpoints exactly
    (weights.py ramps upper -> helper over one half and helper -> lower
    over the other). The one boundary that is not a row is the centre:
    there a half-angle helper owns 100% of the weight, a single influence
    that B2 would count against the band, so it becomes a pair of rows at
    +-CENTER_PAIR.  rows=3: -1, -2/3, -1/3, -0.1, 0.1, 1/3, 2/3, 1.

    WHY (E12, research/experiments/py/elbow42.py): without rows on the band
    ends the mesh's ramp runs on to the next vertex outside the band; without
    rows near the half-band middles (+-e/2, where a blend is most compressed)
    and near the centre, the triangles span the bend and lose area. At 120
    deg this layout keeps 0.90 of the silhouette area where 2 rows at +-e/2
    kept 0.86-0.88 (continuum limit 0.917).
    """
    rows = int(rows)
    if rows < 1:
        raise ValueError("a band needs at least one row per half")
    half = np.arange(1, rows + 1) / rows          # (0, 1]: slice boundaries of one half, end included
    return np.concatenate([-half[::-1], [-CENTER_PAIR, CENTER_PAIR], half])


def min_angles(verts, tris):
    """Smallest interior angle (degrees) of each triangle."""
    p = np.asarray(verts, float)[np.asarray(tris, np.int64)]
    out = np.full(len(p), 180.0)
    for i in range(3):
        u = p[:, (i + 1) % 3] - p[:, i]
        v = p[:, (i + 2) % 3] - p[:, i]
        c = (u * v).sum(1) / np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1), 1e-300)
        out = np.minimum(out, np.degrees(np.arccos(np.clip(c, -1, 1))))
    return out


def coverage(alpha, mesh, origin=None):
    """How well a mesh covers its layer: fraction of art pixels (alpha > 8,
    and alpha >= 128) it misses, and fraction of its own area that is
    transparent (overdraw). Rasterised at 1/16 px."""
    alpha = np.asarray(alpha)
    origin = np.zeros(2) if origin is None else np.asarray(origin, float)
    covered = rasterize(np.asarray(mesh["verts"], float) - origin, mesh["tris"], alpha.shape)
    art = alpha > ALPHA_MIN
    solid = alpha >= 128
    return {
        "missed_art": float((art & ~covered).sum() / max(1, art.sum())),
        "missed_solid": float((solid & ~covered).sum() / max(1, solid.sum())),
        "overdraw": float((covered & ~art).sum() / max(1, covered.sum())),
    }


def rasterize(verts, tris, shape):
    """Boolean coverage of triangles (layer px coordinates) on an (H, W) grid."""
    m = np.zeros(shape, np.uint8)
    pts = np.round(np.asarray(verts, float) * 16 - 8).astype(np.int32)  # pixel centres at +0.5
    for t in np.asarray(tris, np.int64):
        cv2.fillConvexPoly(m, pts[t], 1, lineType=cv2.LINE_8, shift=4)
    return m.astype(bool)


# ---------------------------------------------------------------- 1-2: mask

def _art_mask(alpha):
    """Art mask grown by DILATE px and merged into one piece.

    Returns (mask padded by PAD px on every side, stats)."""
    art = (alpha > ALPHA_MIN).astype(np.uint8)
    if not art.any():
        raise ValueError("layer has no art (alpha > 8 nowhere)")
    m = cv2.copyMakeBorder(art, PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=0)
    if DILATE:
        k = 2 * DILATE + 1
        m = cv2.dilate(m, np.ones((k, k), np.uint8))
    m, dropped, bridged = _one_piece(m)
    return m, {"islands_dropped_px": int(dropped), "islands_bridged": int(bridged)}


def _one_piece(m):
    """Drop specks, bridge the remaining islands to the largest one."""
    from scipy import ndimage  # local: only layers with islands pay for the import

    dropped = bridged = 0
    while True:
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        if n <= 2:
            return m, dropped, bridged
        areas = stats[1:, cv2.CC_STAT_AREA]
        small = [i + 1 for i, a in enumerate(areas) if a < MIN_ISLAND]
        if small:
            dropped += int(sum(areas[i - 1] for i in small))
            m[np.isin(lab, small)] = 0
            continue
        main = 1 + int(np.argmax(areas))
        dist, (iy, ix) = ndimage.distance_transform_edt(lab != main, return_indices=True)
        others = (lab > 0) & (lab != main)
        k = np.flatnonzero(others.ravel())[np.argmin(dist[others])]
        y, x = divmod(int(k), m.shape[1])
        cv2.line(m, (x, y), (int(ix[y, x]), int(iy[y, x])), 1, thickness=3)
        bridged += 1


# ---------------------------------------------------------------- 3: outlines

def _outlines(mask, eps, size):
    """(outer polygon, [hole polygons]) in layer coordinates, simplified."""
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    hier = hier[0]
    outer_ids = [i for i in range(len(contours)) if hier[i][3] < 0]
    oi = max(outer_ids, key=lambda i: cv2.contourArea(contours[i]))
    outer = _simplify(contours[oi], eps, size, [])
    holes = []
    for i in range(len(contours)):
        if hier[i][3] != oi or cv2.contourArea(contours[i]) < MIN_HOLE:
            continue
        hole = _simplify(contours[i], eps, size, [outer] + holes)
        if hole is not None and len(hole) >= 3:
            holes.append(hole)
    return outer, holes


def _simplify(contour, eps, size, others):
    """approxPolyDP until the polygon is simple and clear of `others`.

    Returns (k, 2) float layer coordinates (pixel centres, clamped to the
    image), or None for a hole that cannot be kept apart from the outline."""
    e = float(eps)
    while True:
        p = cv2.approxPolyDP(contour, e, True)[:, 0, :].astype(float) - PAD + 0.5
        p[:, 0] = np.clip(p[:, 0], 0, size[0])
        p[:, 1] = np.clip(p[:, 1], 0, size[1])
        p = _dedupe(p)
        clear = not any(_polygons_cross(p, o) for o in others) and (not others or inside(others[0], p).all())
        if len(p) >= 3 and _is_simple(p) and clear:
            return p
        if e <= 0.25:
            if others:
                return None  # a hole squeezed against the outline: fill it
            raise ValueError("cannot trace a simple outline for this layer")
        e /= 2


def _dedupe(p):
    keep = np.ones(len(p), bool)
    d = np.linalg.norm(p - np.roll(p, 1, axis=0), axis=1)
    keep &= d > 1e-9
    return p[keep] if keep.any() else p[:1]


def _edges(p):
    return np.stack([p, np.roll(p, -1, axis=0)], 1)


def _cross2(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def _crossings(S, T):
    """(n, m) bool: segment S[i] properly crosses segment T[j] (interiors meet)."""
    p, r = S[:, None, 0], (S[:, 1] - S[:, 0])[:, None]
    q, s = T[None, :, 0], (T[:, 1] - T[:, 0])[None]
    d1 = _cross2(r, q - p)
    d2 = _cross2(r, q + s - p)
    d3 = _cross2(s, p - q)
    d4 = _cross2(s, p + r - q)
    tol = 1e-9
    return ((d1 > tol) & (d2 < -tol) | (d1 < -tol) & (d2 > tol)) & \
           ((d3 > tol) & (d4 < -tol) | (d3 < -tol) & (d4 > tol))


def _is_simple(p):
    e = _edges(p)
    x = _crossings(e, e)
    n = len(p)
    i, j = np.nonzero(x)
    adjacent = ((j - i) % n == 1) | ((i - j) % n == 1)
    return not (~adjacent).any()


def _polygons_cross(a, b):
    return bool(_crossings(_edges(a), _edges(b)).any())


def inside(poly, pts):
    """Even-odd point-in-polygon for many points: (k,) bool."""
    poly = np.asarray(poly, float)
    pts = np.asarray(pts, float).reshape(-1, 2)
    x, y = pts[:, 0:1], pts[:, 1:2]
    xi, yi = poly[:, 0][None], poly[:, 1][None]
    xj, yj = np.roll(poly[:, 0], 1)[None], np.roll(poly[:, 1], 1)[None]
    with np.errstate(divide="ignore", invalid="ignore"):
        hit = ((yi > y) != (yj > y)) & (x < xi + (y - yi) * (xj - xi) / (yj - yi))
    return (hit.sum(1) % 2) == 1


# ---------------------------------------------------------------- 4: joint bands

def _band_points(loops, bands, origin):
    """Splice row/outline crossings into the loops; interior row points.

    Returns (loops, interior points (k, 2), rows) where rows lists, per band
    and row, the layer-space points of that row (to find their vertex
    indices after triangulation): each row's two outline points where they
    actually landed (pushed out by the splice bulge, or the outline vertex
    they snapped to), then its interior points."""
    inserts = [[] for _ in loops]  # per loop: (edge index, u, point, insert id)
    interior, rows = [], []
    for band in bands:
        c = np.asarray(band["center"], float) - origin
        a = np.asarray(band["axis"], float)
        a = a / np.linalg.norm(a)
        nrm = np.array([-a[1], a[0]])
        hw = float(band["halfwidth"])
        ext = float(band.get("extent", BAND_EXTENT)) * hw
        if band.get("offsets") is not None:
            offs = np.sort(np.asarray(band["offsets"], float)) * ext
        else:
            offs = row_offsets(band.get("rows", BAND_ROWS)) * ext
        gap = max(2.0 * ext / max(len(offs) - 1, 1), 0.5 * hw)  # spacing along a row ~ spacing between rows
        band_rows = []
        for s in offs:
            band_rows.append(_row(loops, c + s * a, nrm, gap, inserts, interior))
        rows.append(band_rows)
    landed = {}
    out = [_splice(lp, ins, li > 0, landed) for li, (lp, ins) in enumerate(zip(loops, inserts))]
    rows = [[[landed[p] if isinstance(p, int) else p for p in row] for row in band] for band in rows]
    interior = np.array(interior, float).reshape(-1, 2)
    interior = _clear_points(interior, out)
    return out, interior, rows


def _row(loops, p0, nrm, gap, inserts, interior):
    """One row across the limb through p0 along nrm. Its two outline
    crossings are queued for splicing (returned as insert ids), its interior
    points appended to `interior` (returned as points)."""
    hits = []  # (t, loop, edge, u)
    for li, lp in enumerate(loops):
        e = _edges(lp)
        d = e[:, 1] - e[:, 0]
        den = _cross2(nrm[None], d)
        ok = np.abs(den) > 1e-12
        w = e[:, 0] - p0
        with np.errstate(divide="ignore", invalid="ignore"):
            t = _cross2(w, d) / den
            u = _cross2(w, nrm[None]) / den
        for k in np.flatnonzero(ok & (u >= 0) & (u < 1)):
            hits.append((float(t[k]), li, int(k), float(u[k])))
    hits.sort()
    if len(hits) < 2:
        return []
    # inside intervals alternate (even-odd over outer + holes); take the one nearest t = 0
    spans = [(hits[i], hits[i + 1]) for i in range(0, len(hits) - 1, 2)]
    lo, hi = min(spans, key=lambda sp: 0 if sp[0][0] <= 0 <= sp[1][0] else min(abs(sp[0][0]), abs(sp[1][0])))
    t0, t1 = lo[0], hi[0]
    if t1 - t0 < 2.0:
        return []
    pts = []
    for t, li, k, u in (lo, hi):
        ident = sum(len(x) for x in inserts)
        inserts[li].append((k, u, p0 + t * nrm, ident))
        pts.append(ident)
    m = max(1, int(round((t1 - t0) / gap)))
    for j in range(1, m):
        p = p0 + (t0 + (t1 - t0) * j / m) * nrm
        interior.append(p)
        pts.append(p)
    return pts


def _splice(loop, ins, hole, landed):
    """Insert points on loop edges (sorted along each edge).

    A point within CLEAR px of a vertex already there snaps to that vertex.
    The others are pushed off their edge, away from the art, by
    SPLICE_BULGE * 4u(1-u) px (u = position along the edge). WHY: a point
    computed on an edge rounds to either side of it; one that lands ~1e-13 px
    on the art side leaves an ulp-thin sliver triangle outside the outline,
    and CDT's refinement then cannot split that edge (Unrefined
    split_vertex_invalid) and gives up on the whole neighbourhood (min angle
    8 deg instead of 28). The bulge is strictly concave along the edge, so
    several points spliced into one edge also stay strictly convex.
    landed[insert id] receives where each point ended up."""
    if not ins:
        return loop
    by_edge = {}
    for k, u, p, ident in ins:
        by_edge.setdefault(k, []).append((u, p, ident))
    out = []
    n = len(loop)
    away = _away_sign(loop, hole)
    for k in range(n):
        out.append(loop[k])
        a, b = loop[k], loop[(k + 1) % n]
        d = b - a
        normal = away * np.array([d[1], -d[0]]) / max(np.linalg.norm(d), 1e-300)
        for u, p, ident in sorted(by_edge.get(k, []), key=lambda e: e[0]):
            if np.linalg.norm(p - out[-1]) <= CLEAR:
                landed[ident] = out[-1]
            elif np.linalg.norm(p - b) <= CLEAR:
                landed[ident] = b
            else:
                q = p + normal * (SPLICE_BULGE * 4.0 * u * (1.0 - u))
                out.append(q)
                landed[ident] = q
    return np.array(out, float)


def _away_sign(loop, hole):
    """+1 when (dy, -dx) of the loop's edges points away from the art, else -1.

    For a counter-clockwise loop (positive shoelace area in these
    coordinates) (dy, -dx) is the outward normal; the art is inside the
    outer loop and outside a hole."""
    x, y = loop[:, 0], loop[:, 1]
    ccw = float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum()) > 0
    return (1.0 if ccw else -1.0) * (-1.0 if hole else 1.0)


def _clear_points(pts, loops):
    """Interior points strictly inside the outline, out of the holes, and at
    least CLEAR px from every outline edge and from each other."""
    if not len(pts):
        return pts
    keep = inside(loops[0], pts)
    for hole in loops[1:]:
        keep &= ~inside(hole, pts)
    for lp in loops:
        keep &= _dist_to_edges(pts, _edges(lp)) > CLEAR
    pts = pts[keep]
    out = []
    for p in pts:
        if all(np.linalg.norm(p - q) > CLEAR for q in out):
            out.append(p)
    return np.array(out, float).reshape(-1, 2)


def _dist_to_edges(pts, e):
    a, b = e[None, :, 0], e[None, :, 1]
    p = pts[:, None]
    ab = b - a
    t = np.clip(((p - a) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-300), 0, 1)
    return np.linalg.norm(p - (a + t[..., None] * ab), axis=-1).min(1)


# ---------------------------------------------------------------- 5: CDT

def _triangulate(loops, interior, min_angle, max_area):
    pts = np.vstack(loops + [interior])
    edges, base = [], 0
    for lp in loops:
        n = len(lp)
        edges += [(base + i, base + (i + 1) % n) for i in range(n)]
        base += n
    t = cdt.Triangulation(cdt.VertexInsertionOrder.AUTO, cdt.IntersectingConstraintEdges.TRY_RESOLVE, 0.0)
    t.insert_vertices([cdt.V2d(float(x), float(y)) for x, y in pts])
    t.conform_to_edges([cdt.Edge(a, b) for a, b in edges])
    erase = t.collect_outer_triangles_and_holes()
    unrefined = {}
    if max_area:
        r = t.refine_triangles(MAX_STEINER, cdt.RefinementCriterion.LARGEST_AREA, float(max_area), erase, MIN_EDGE)
        unrefined["area"] = _unrefined(r)
    if min_angle:
        r = t.refine_triangles(MAX_STEINER, cdt.RefinementCriterion.SMALLEST_ANGLE,
                               cdt.deg_to_rad(float(min_angle)), erase, MIN_EDGE)
        unrefined["angle"] = _unrefined(r)
    t.finalize_triangulation(erase)
    va = t.vertices_array()
    verts = np.stack([va["x"], va["y"]], 1).astype(float)
    tris = t.triangles_array()["vertices"].astype(np.int64).reshape(-1, 3)
    used = np.unique(tris)
    remap = -np.ones(len(verts), np.int64)
    remap[used] = np.arange(len(used))
    return verts[used], remap[tris], unrefined


def _unrefined(r):
    return {k: int(getattr(r, k)) for k in ("short_edge_triangles", "circumcenter_outside", "circumcenter_on_vertex",
                                             "sharp_fixed_corner", "short_edges", "split_vertex_invalid")
            if int(getattr(r, k))}


# ---------------------------------------------------------------- 6: hull first

def _hull_first(verts, tris):
    """Reorder: outer outline (in walk order) first, hole rims, interior."""
    loops = meshtopo.boundary_loops(tris.tolist())
    if not loops:
        raise ValueError("triangulation boundary does not close")
    pts = verts.tolist()
    outer = max(loops, key=lambda lp: abs(meshtopo.polygon_area(pts, lp)))
    first = min(range(len(outer)), key=lambda i: outer[i])
    order = outer[first:] + outer[:first]
    seen = set(order)
    nholes = 0
    for lp in loops:
        if lp is outer:
            continue
        nholes += 1
        for v in lp:
            if v not in seen:
                order.append(v)
                seen.add(v)
    order += [v for v in range(len(verts)) if v not in seen]
    inv = np.empty(len(order), np.int64)
    inv[np.array(order)] = np.arange(len(order))
    return verts[np.array(order)], inv[tris], len(outer), nholes


def _locate(verts, rows):
    """Vertex indices of the band-row points (nearest vertex within 0.5 px)."""
    out = []
    for band in rows:
        b = []
        for pts in band:
            idx = []
            for p in pts:
                d = np.linalg.norm(verts - p, axis=1)
                k = int(np.argmin(d))
                if d[k] < 0.5:
                    idx.append(k)
            b.append(idx)
        out.append(b)
    return out
