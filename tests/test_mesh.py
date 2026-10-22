"""spinegen.mesh: outline-hugging CDT meshes with joint-band rows.

All layers here are synthetic, drawn by the helpers below (a tapered arm
44 -> 36 px wide, a torso blob with a hole), so nothing third-party is needed.
"""
import math

import numpy as np
import pytest

from spinegen import mesh, meshtopo


# ---------------------------------------------------------------- synthetic layers

def capsule(size, p0, r0, p1, r1, ss=4):
    """(H, W) uint8 alpha of a tapered capsule (circle p0 r0 -> circle p1 r1), anti-aliased."""
    w, h = size
    ys, xs = np.mgrid[0:h * ss, 0:w * ss]
    P = np.stack([(xs + 0.5) / ss, (ys + 0.5) / ss], -1)
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    t = np.clip(((P - p0) @ d) / (d @ d), 0, 1)
    dist = np.linalg.norm(P - (p0 + t[..., None] * d), axis=-1)
    cover = (dist <= r0 + (r1 - r0) * t).astype(float)
    return np.round(cover.reshape(h, ss, w, ss).mean((1, 3)) * 255).astype(np.uint8)


def arm(angle_deg=0.0):
    """A 200 px arm 44 -> 36 px wide, centred in a 300 x 300 layer at `angle_deg`
    (image space), or flat in a 250 x 60 layer. Returns (alpha, p0, p1)."""
    if angle_deg == 0.0:
        p0, p1 = np.array([26.0, 30.0]), np.array([224.0, 30.0])
        return capsule((250, 60), p0, 22, p1, 18), p0, p1
    a = math.radians(angle_deg)
    d = np.array([math.cos(a), math.sin(a)])
    c = np.array([150.0, 150.0])
    p0, p1 = c - 100 * d, c + 100 * d
    return capsule((300, 300), p0, 22, p1, 18), p0, p1


def torso(ss=4):
    """A 320 x 376 px elliptical blob (the monkey torso's size) with an
    elliptical hole, anti-aliased. Returns (alpha, hole centre, hole radii)."""
    ys, xs = np.mgrid[0:384 * ss, 0:328 * ss]
    x, y = (xs + 0.5) / ss, (ys + 0.5) / ss
    blob = ((x - 164) / 160) ** 2 + ((y - 192) / 188) ** 2 <= 1
    hc, hr = (164.0, 230.0), (30.0, 40.0)
    hole = ((x - hc[0]) / hr[0]) ** 2 + ((y - hc[1]) / hr[1]) ** 2 <= 1
    cover = (blob & ~hole).astype(float).reshape(384, ss, 328, ss).mean((1, 3))
    return np.round(cover * 255).astype(np.uint8), np.array(hc), np.array(hr)


def elbow_band(p0, p1, rows=None, extent=None, at=0.5):
    d = (p1 - p0) / np.linalg.norm(p1 - p0)
    b = {"center": tuple(p0 + at * (p1 - p0)), "axis": tuple(d), "halfwidth": 20.0}
    if rows is not None:
        b["rows"] = rows
    if extent is not None:
        b["extent"] = extent
    return b


# ---------------------------------------------------------------- structure

@pytest.mark.parametrize("which", ["arm", "arm_band", "torso"])
def test_hull_first_in_outline_order_and_uvs(which):
    if which == "torso":
        alpha, origin, bands = torso()[0], (10.0, -5.0), []
    else:
        alpha, p0, p1 = arm()
        origin = (100.0, 50.0)
        bands = [elbow_band(p0 + origin, p1 + origin)] if which == "arm_band" else []
    m = mesh.build(alpha, origin, bands=bands)
    V, T, hull = m["verts"], m["tris"], m["hull"]
    assert T.min() >= 0 and T.max() < len(V)
    assert meshtopo.hull_is_first(T.tolist(), hull)  # L8: verts[:hull] walk the outer outline in order
    loops = meshtopo.boundary_loops(T.tolist())
    outer = max(loops, key=lambda lp: abs(meshtopo.polygon_area(V.tolist(), lp)))
    assert sorted(outer) == list(range(hull))
    h, w = alpha.shape
    assert np.allclose(m["uvs"], (V - origin) / [w, h])  # UVs: the same points over the layer image
    assert (m["uvs"] >= 0).all() and (m["uvs"] <= 1).all()
    assert len(np.unique(T, axis=0)) == len(T)
    assert (np.abs(mesh_signed(V, T)) > 1e-9).all()      # no degenerate triangles


def mesh_signed(V, T):
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])


def test_mesh_covers_the_art_and_little_else():
    for alpha in (arm()[0], torso()[0]):
        st = mesh.build(alpha, (0, 0))["stats"]
        assert st["missed_solid"] == 0.0           # every pixel with alpha >= 128 is inside the mesh
        assert st["missed_art"] < 0.002
        assert st["overdraw"] < 0.06               # transparent share of the mesh area ([E2]: 0.6-9.7%)


def test_refinement_reaches_min_angle():
    for alpha in (arm()[0], arm(35)[0], torso()[0]):
        for min_angle in (20.0, 28.0):
            m = mesh.build(alpha, (0, 0), min_angle=min_angle)
            assert not m["stats"]["unrefined"]["angle"]
            assert mesh.min_angles(m["verts"], m["tris"]).min() >= min_angle - 1e-6


def test_max_area_bounds_triangles():
    m = mesh.build(torso()[0], (0, 0), max_area=400.0)
    assert (np.abs(mesh_signed(m["verts"], m["tris"])) / 2 <= 400.0 + 1e-6).all()


def test_torso_hole_is_kept_open():
    alpha, hc, hr = torso()
    m = mesh.build(alpha, (0, 0))
    assert m["holes"] == 1
    assert len(meshtopo.boundary_loops(m["tris"].tolist())) == 2
    cen = m["verts"][m["tris"]].mean(1)
    in_hole = (((cen - hc) / (hr - 2)) ** 2).sum(1) < 1   # 2 px inside the hole's rim
    assert not in_hole.any()


def test_islands_are_bridged_and_specks_dropped():
    alpha = capsule((200, 80), (20, 40), 15, (90, 40), 15)
    alpha[30:50, 150:180] = 255       # a separate island of 600 px: bridged into the one outline
    alpha[5:7, 120:122] = 255         # a 4 px speck (16 px after the 1 px dilation): dropped
    m = mesh.build(alpha, (0, 0))
    st = m["stats"]
    assert st["islands_bridged"] == 1 and st["islands_dropped_px"] > 0
    assert len(meshtopo.boundary_loops(m["tris"].tolist())) == 1   # Spine's hull is one polygon
    solid = int((alpha >= 128).sum())
    assert round(st["missed_solid"] * solid) <= 4                  # only the dropped speck is missed


def test_empty_layer_raises():
    with pytest.raises(ValueError):
        mesh.build(np.zeros((10, 10), np.uint8), (0, 0))


# ---------------------------------------------------------------- joint bands

def test_row_offsets_layout():
    assert np.allclose(mesh.row_offsets(3), [-1, -2 / 3, -1 / 3, -0.1, 0.1, 1 / 3, 2 / 3, 1])
    assert np.allclose(mesh.row_offsets(2), [-1, -0.5, -0.1, 0.1, 0.5, 1])
    for r in (1, 2, 3, 4):
        o = mesh.row_offsets(r)
        assert np.all(np.diff(o) > 0) and o[0] == -1 and o[-1] == 1
        assert not np.isclose(o, 0).any()   # no row on the centre, where a helper rig is single-influence


@pytest.mark.parametrize("angle", [0.0, 35.0, 90.0])
def test_band_rows_cross_the_limb_at_their_offsets(angle):
    alpha, p0, p1 = arm(angle)
    origin = np.array([7.0, -3.0])
    band = elbow_band(p0 + origin, p1 + origin)
    m = mesh.build(alpha, origin, bands=[band])
    rows = m["band_rows"][0]
    offs = mesh.row_offsets(mesh.BAND_ROWS) * mesh.BAND_EXTENT * band["halfwidth"]
    assert len(rows) == len(offs)
    c, a = np.asarray(band["center"]), np.asarray(band["axis"])
    nrm = np.array([-a[1], a[0]])
    for row, s in zip(rows, offs):
        assert len(row) >= 3                                   # two outline ends + interior points
        P = m["verts"][row]
        assert np.abs((P - c) @ a - s).max() < 0.05            # on the row line (bulge <= 0.02 px)
        assert row[0] < m["hull"] and row[1] < m["hull"]       # both ends are on the outline
        assert all(i >= m["hull"] for i in row[2:])            # the rest is interior
        across = (P - c) @ nrm
        width = across.max() - across.min()
        assert 36.0 < width < 48.0                             # spans the whole limb (44 -> 36 px)
    assert not m["stats"]["unrefined"]["angle"]


def test_band_rows_never_break_refinement():
    """Regression: a row end computed on a straight outline edge rounds to
    either side of it; ~1e-13 px on the art side used to leave an ulp-thin
    sliver outside the outline that CDT's refinement cannot split
    (split_vertex_invalid), which stopped refinement (min angle 8 deg)."""
    alpha, p0, p1 = arm()
    for at in np.linspace(0.15, 0.85, 15):
        for rows in (1, 2, 3):
            for extent in (1.0, 1.5, 2.0):
                m = mesh.build(alpha, (0, 0), bands=[elbow_band(p0, p1, rows, extent, at)])
                assert not m["stats"]["unrefined"]["angle"], (at, rows, extent, m["stats"]["unrefined"])
                assert m["stats"]["min_angle"] >= 28.0 - 1e-6


def test_band_through_a_hole_and_explicit_offsets():
    alpha = torso()[0]
    band = {"center": (164.0, 230.0), "axis": (0.0, 1.0), "halfwidth": 60.0, "offsets": [-0.5, 0.0, 0.5]}
    m = mesh.build(alpha, (0, 0), bands=[band])
    assert m["holes"] == 1 and not m["stats"]["unrefined"]["angle"]
    rows = m["band_rows"][0]
    assert len(rows) == 3
    # the centre row crosses the hole: it keeps the span around the band centre's nearest inside part
    assert all(len(r) >= 2 for r in rows)


# ---------------------------------------------------------------- the lean profile (rigbuild's default)

LEAN = {"min_angle": 0.0, "outline": "adaptive", "row_points": "ends", "edges": "constrain"}


def furry(alpha, period=6, depth=4):
    """`alpha` with a sawtooth of notches cut into its top and bottom edges
    (a jagged, furry outline at the scale of a few px)."""
    a = alpha.copy()
    h, w = a.shape
    for j in range(w):
        cut = int(round(depth * abs((j % period) - period / 2) / (period / 2)))
        col = np.nonzero(a[:, j] > 8)[0]
        if len(col) > 2 * depth + 4:
            a[col[0]:col[0] + cut, j] = 0
            a[col[-1] - cut + 1:col[-1] + 1, j] = 0
    return a


def test_lean_rows_are_edges_with_only_their_ends():
    alpha, p0, p1 = arm()
    m = mesh.build(alpha, (0, 0), bands=[elbow_band(p0, p1)], **LEAN)
    edges = {tuple(sorted(e)) for t in m["tris"].tolist() for e in ((t[0], t[1]), (t[1], t[2]), (t[0], t[2]))}
    for row in m["band_rows"][0]:
        assert len(row) == 2 and row[0] < m["hull"] and row[1] < m["hull"]
        assert tuple(sorted(row)) in edges                     # the row is one edge of the mesh
    # no Steiner point anywhere: every vertex is an outline vertex
    assert m["stats"]["vertices"] == m["hull"]
    st = m["stats"]
    assert st["missed_solid"] == 0.0 and st["missed_art"] < 0.002
    ref = mesh.build(alpha, (0, 0), bands=[elbow_band(p0, p1)])["stats"]
    assert st["vertices"] < 0.6 * ref["vertices"]


def test_adaptive_outline_covers_a_furry_edge_with_fewer_vertices():
    """The one-sided simplification may stand off the art (covering the
    notches of a furry edge) but never cut into it more than eps."""
    alpha = furry(arm()[0], depth=3)
    ex = mesh.build(alpha, (0, 0), min_angle=0.0, edges="constrain")["stats"]
    ad = mesh.build(alpha, (0, 0), min_angle=0.0, edges="constrain", outline="adaptive")["stats"]
    assert ad["hull"] < 0.7 * ex["hull"]
    assert ad["missed_solid"] <= ex["missed_solid"] + 1e-9 and ad["missed_art"] <= ex["missed_art"] + 1e-9
    assert ad["overdraw"] > ex["overdraw"]                    # the price: transparent notches drawn
    # a clean edge keeps eps: the slack only buys anything where there are notches
    clean = mesh.build(arm()[0], (0, 0), min_angle=0.0, edges="constrain", outline="adaptive")["stats"]
    assert clean["missed_solid"] == 0.0 and clean["overdraw"] < 0.06


def test_lean_rows_cut_every_lobe_they_cross():
    """A band row also cuts the outline's other spans along its line (a lobe
    beside the limb): no triangle near the band reaches across a row."""
    alpha, p0, p1 = arm()
    lobe = capsule((250, 60), (125, 4), 3, (140, 4), 3)       # a tuft above the arm, at the elbow
    alpha = np.maximum(alpha, lobe)
    band = elbow_band(p0, p1)
    m = mesh.build(alpha, (0, 0), bands=[band], **LEAN)
    c, a = np.asarray(band["center"]), np.asarray(band["axis"])
    offs = mesh.row_offsets(mesh.BAND_ROWS) * mesh.BAND_EXTENT * band["halfwidth"]
    s = (m["verts"] - c) @ a
    for t in m["tris"]:
        lo, hi = s[t].min(), s[t].max()
        assert not any(lo < o - 0.05 and hi > o + 0.05 for o in offs)   # never across a row line


def test_band_slivers_adds_medial_points_only_near_the_band():
    alpha, p0, p1 = arm()
    alpha = furry(alpha, depth=3)
    band = elbow_band(p0, p1)
    plain = mesh.build(alpha, (0, 0), bands=[band], **LEAN)
    m = mesh.build(alpha, (0, 0), bands=[band], band_slivers=15.0, **LEAN)
    inner = m["verts"][m["hull"]:]
    assert len(inner) > 0 and m["stats"]["vertices"] > plain["stats"]["vertices"]
    c, a = np.asarray(band["center"]), np.asarray(band["axis"])
    reach = (mesh.BAND_EXTENT + mesh.BAND_SLIVER_MARGIN) * band["halfwidth"] + 1e-6
    assert (np.abs((inner - c) @ a) <= reach).all()
    assert m["stats"]["missed_solid"] <= plain["stats"]["missed_solid"] + 1e-9


def test_slack_follows_the_limb_width():
    alpha = arm()[0]
    m, _ = mesh._art_mask(alpha)
    sl = mesh.outline_slack(m, 1.75)
    assert sl.min() >= 1.75 and sl.max() <= mesh.SLACK_MAX
    thin = mesh._art_mask(capsule((60, 20), (6, 10), 3, (54, 10), 3))[0]
    assert mesh.outline_slack(thin, 1.75).max() == pytest.approx(1.75)   # a 6 px finger keeps eps


def test_band_outside_the_layer_adds_nothing():
    alpha, p0, p1 = arm()
    plain = mesh.build(alpha, (0, 0))
    far = mesh.build(alpha, (0, 0), bands=[{"center": (1000.0, 1000.0), "axis": (1, 0), "halfwidth": 20}])
    assert len(far["verts"]) == len(plain["verts"])
    assert all(r == [] for r in far["band_rows"][0])


# ---------------------------------------------------------------- vertex budget

def grid_count_reference(alpha, cell=22):
    """Hard Time build_cast_spine.py grid_mesh(), loop for loop: vertices used by kept cells."""
    a = alpha > 8
    ys, xs = np.nonzero(a)
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    nx = max(2, int(math.ceil((x1 - x0) / cell)) + 1)
    ny = max(2, int(math.ceil((y1 - y0) / cell)) + 1)
    gx, gy = np.linspace(x0, x1, nx), np.linspace(y0, y1, ny)
    used = set()
    for j in range(ny - 1):
        for i in range(nx - 1):
            xa, xb = int(gx[i]) - 3, int(math.ceil(gx[i + 1])) + 3
            ya, yb = int(gy[j]) - 3, int(math.ceil(gy[j + 1])) + 3
            if a[max(0, ya):yb, max(0, xa):xb].any():
                used |= {(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)}
    return len(used)


def test_grid_equivalent_vertex_count_matches_hard_time():
    for alpha in (arm()[0], arm(35)[0], torso()[0], capsule((40, 30), (10, 15), 4, (30, 15), 4)):
        assert mesh.grid_equivalent_vertex_count(alpha) == grid_count_reference(alpha)
    assert mesh.grid_equivalent_vertex_count(np.zeros((5, 5), np.uint8)) == 0


def test_vertex_counts_against_the_22px_grid():
    """The P2 budget compares a rig's vertices with the grid it replaces. A
    big layer is where the CDT wins (torso: ~35% of the grid); a thin limb
    costs about what the grid does, and a joint band adds its rows."""
    t = mesh.build(torso()[0], (0, 0))["stats"]
    assert t["vertices"] <= 0.45 * t["grid_vertices"]
    alpha, p0, p1 = arm()
    a = mesh.build(alpha, (0, 0))["stats"]
    assert a["vertices"] <= 1.2 * a["grid_vertices"]
    b = mesh.build(alpha, (0, 0), bands=[elbow_band(p0, p1)])["stats"]
    assert a["vertices"] < b["vertices"] <= a["vertices"] + 50     # 8 rows across the limb
    b2 = mesh.build(alpha, (0, 0), bands=[elbow_band(p0, p1, rows=2)])["stats"]
    assert a["vertices"] < b2["vertices"] < b["vertices"]          # 6 rows: cheaper


def test_asymmetric_band_rows_follow_each_half():
    """"reach" [upper, lower] scales each half's rows by its own length,
    "rows" [upper, lower] sets each half's count; symmetric reach is the
    old extent exactly (P2 A1: a long sleeve half, a short forearm half)."""
    band = dict(elbow_band(*arm()[1:]), reach=(3.0, 1.5), rows=(3, 4))
    hw = band["halfwidth"]
    pos = mesh.band_row_positions(band)
    want = np.array([-1, -2 / 3, -1 / 3, -mesh.CENTER_PAIR]) * 3.0 * hw
    want = np.concatenate([want, np.array([mesh.CENTER_PAIR, 0.25, 0.5, 0.75, 1.0]) * 1.5 * hw])
    assert np.allclose(pos, want)
    assert mesh.band_reach(band) == (3.0 * hw, 1.5 * hw)
    sym = elbow_band(*arm()[1:], extent=1.5)
    assert np.allclose(mesh.band_row_positions(dict(sym, reach=(1.5, 1.5))), mesh.band_row_positions(sym))


def test_row_points_make_each_lean_row_a_chain_of_edges():
    """With row_points k a lean ("ends") row keeps k interior points and is
    a chain of constrained edges through them: no triangle crosses it, and
    a band without the option meshes exactly as before."""
    alpha, p0, p1 = arm()
    band = dict(elbow_band(p0, p1), reach=(2.5, 1.5), rows=(3, 4), row_points=2)
    m = mesh.build(alpha, (0, 0), bands=[band], **LEAN)
    edges = {tuple(sorted(e)) for t in m["tris"].tolist() for e in ((t[0], t[1]), (t[1], t[2]), (t[0], t[2]))}
    c, a = np.asarray(band["center"]), np.asarray(band["axis"])
    nrm = np.array([-a[1], a[0]])
    for row in m["band_rows"][0]:
        assert len(row) == 4 and row[0] < m["hull"] and row[1] < m["hull"]
        chain = [row[0]] + sorted(row[2:], key=lambda i: (m["verts"][i] - m["verts"][row[0]]) @ nrm
                                  * np.sign((m["verts"][row[1]] - m["verts"][row[0]]) @ nrm)) + [row[1]]
        for u, v in zip(chain[:-1], chain[1:]):
            assert tuple(sorted((u, v))) in edges
    s = (m["verts"] - c) @ a
    for t in m["tris"]:
        lo, hi = s[t].min(), s[t].max()
        assert not any(lo < o - 0.05 and hi > o + 0.05 for o in mesh.band_row_positions(band))
    assert m["stats"]["vertices"] == m["hull"] + 2 * len(m["band_rows"][0])
    plain = dict(band, row_points=0)
    ref = mesh.build(alpha, (0, 0), bands=[plain], **LEAN)
    assert ref["stats"]["vertices"] == ref["hull"]
