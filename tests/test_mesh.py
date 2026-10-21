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
