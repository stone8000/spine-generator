"""spinegen.weights: 2D bone heat with visibility, joint-band ramps, pruning.

Synthetic layers only (drawn here, meshed by spinegen.mesh). The last test
re-runs the E12 elbow experiment in the official runtime and skips without it.
"""
import os
import sys

import numpy as np
import pytest

from spinegen import mesh, weights

from conftest import need_oracle

HW = 20.0


# ---------------------------------------------------------------- fixtures

def capsule(size, p0, r0, p1, r1, ss=4):
    w, h = size
    ys, xs = np.mgrid[0:h * ss, 0:w * ss]
    P = np.stack([(xs + 0.5) / ss, (ys + 0.5) / ss], -1)
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    t = np.clip(((P - p0) @ d) / (d @ d), 0, 1)
    dist = np.linalg.norm(P - (p0 + t[..., None] * d), axis=-1)
    cover = (dist <= r0 + (r1 - r0) * t).astype(float)
    return np.round(cover.reshape(h, ss, w, ss).mean((1, 3)) * 255).astype(np.uint8)


SHOULDER, ELBOW, WRIST = np.array([26.0, 30.0]), np.array([126.0, 30.0]), np.array([224.0, 30.0])


def arm_bones(helper):
    b = [{"name": "upper", "head": tuple(SHOULDER), "tail": tuple(ELBOW)},
         {"name": "fore", "head": tuple(ELBOW), "tail": tuple(WRIST)}]
    if helper:
        b.append({"name": "elbow_mid", "head": tuple(ELBOW), "tail": tuple(ELBOW)})  # zero-length bone
    return b


def elbow_joint(helper, extent=None):
    j = {"center": tuple(ELBOW), "upper": "upper", "helper": "elbow_mid" if helper else None,
         "lower": "fore", "halfwidth": HW}
    if extent is not None:
        j["extent"] = extent
    return j


@pytest.fixture(scope="module")
def arm_mesh():
    """One layer holding a whole arm (44 -> 36 px), meshed with an elbow band
    (the monkey's arm layers are like this: the elbow is inside the layer)."""
    alpha = capsule((250, 60), SHOULDER, 22, WRIST, 18)
    return mesh.build(alpha, (0, 0), bands=[{"center": tuple(ELBOW), "axis": (1, 0), "halfwidth": HW}])


def arm_weights(m, helper, **kw):
    return weights.compute(m["verts"], m["tris"], mesh.outlines(m), arm_bones(helper), [elbow_joint(helper)], **kw)


def check_rows(w, names, max_influences=3, prune=0.02):
    for row in w:
        assert 1 <= len(row) <= max_influences
        assert abs(sum(v for _, v in row) - 1.0) < 1e-9
        assert all(n in names for n, _ in row)
        assert len({n for n, _ in row}) == len(row)
        if len(row) > 1:
            assert min(v for _, v in row) >= prune - 1e-12


# ---------------------------------------------------------------- basics

@pytest.mark.parametrize("helper", [False, True])
def test_weights_sum_to_one_with_at_most_three_influences(arm_mesh, helper):
    w = arm_weights(arm_mesh, helper)
    assert len(w) == len(arm_mesh["verts"])
    check_rows(w, [b["name"] for b in arm_bones(helper)])


def test_rigid_mode_is_one_bone(arm_mesh):
    bones = arm_bones(True)
    w = weights.compute(arm_mesh["verts"], arm_mesh["tris"], mesh.outlines(arm_mesh), bones, mode="rigid")
    assert w == [[("upper", 1.0)]] * len(arm_mesh["verts"])
    bones.reverse()
    w = weights.compute(arm_mesh["verts"], arm_mesh["tris"], mesh.outlines(arm_mesh), bones, mode="rigid")
    assert all(row == [("elbow_mid", 1.0)] for row in w)   # bones[0], whatever it is


@pytest.mark.parametrize("helper", [False, True])
def test_b2_joint_band_is_blended(arm_mesh, helper):
    w = arm_weights(arm_mesh, helper)
    r = weights.b2(arm_mesh["verts"], w, [elbow_joint(helper)], arm_bones(helper))
    assert r["count"] >= 20 and r["value"] >= 0.9 and r["pass"]


def test_b2_fails_for_a_rigid_cut(arm_mesh):
    bones = arm_bones(False)
    w = weights.compute(arm_mesh["verts"], arm_mesh["tris"], mesh.outlines(arm_mesh), bones, mode="rigid")
    r = weights.b2(arm_mesh["verts"], w, [elbow_joint(False)], bones)
    assert r["value"] == 0.0 and not r["pass"]   # Hard Time v2: 0%


def test_far_ends_follow_their_own_bone(arm_mesh):
    w = arm_weights(arm_mesh, True)
    x = arm_mesh["verts"][:, 0]
    assert all(w[i] == [("upper", 1.0)] for i in np.flatnonzero(x < 50))
    assert all(w[i] == [("fore", 1.0)] for i in np.flatnonzero(x > 200))


def test_missing_joint_bone_is_an_error(arm_mesh):
    with pytest.raises(ValueError, match="elbow_mid"):
        weights.compute(arm_mesh["verts"], arm_mesh["tris"], mesh.outlines(arm_mesh), arm_bones(False),
                        [elbow_joint(True)])


# ---------------------------------------------------------------- the joint ramp

def band_vertices(m, joint, bones):
    s, _ = weights.band_coords(m["verts"], joint, bones)
    inside = weights.band_mask(m["verts"], joint, bones, strict=True)
    idx = np.flatnonzero(inside)
    return idx[np.argsort(s[idx])], s


@pytest.mark.parametrize("helper", [False, True])
def test_ramp_is_monotone_along_the_limb(arm_mesh, helper):
    bones = arm_bones(helper)
    w = arm_weights(arm_mesh, helper)
    W = weights.as_matrix(w, [b["name"] for b in bones])
    idx, s = band_vertices(arm_mesh, elbow_joint(helper), bones)
    up, lo = W[idx, 0], W[idx, 1]
    assert np.all(np.diff(up) <= 1e-9) and np.all(np.diff(lo) >= -1e-9)
    assert up[0] >= 2 / 3 - 1e-9 and lo[-1] >= 2 / 3 - 1e-9   # first row: t = 1/6 (helper: a = 1/3)
    if helper:
        mid = W[idx, 2]
        k = int(np.argmax(mid))
        assert abs(s[idx][k]) <= 0.1 * mesh.BAND_EXTENT * HW + 1e-6   # peaks at the joint centre
        assert np.all(np.diff(mid[:k + 1]) >= -1e-9) and np.all(np.diff(mid[k:]) <= 1e-9)


@pytest.mark.parametrize("helper", [False, True])
def test_ramp_values_are_the_piecewise_linear_ramp(arm_mesh, helper):
    bones = arm_bones(helper)
    W = weights.weight_matrix(arm_mesh["verts"], arm_mesh["tris"], mesh.outlines(arm_mesh), bones,
                              [elbow_joint(helper)])
    idx, s = band_vertices(arm_mesh, elbow_joint(helper), bones)
    e = mesh.BAND_EXTENT * HW
    t = (s[idx] + e) / (2 * e)
    if helper:
        a, b = np.clip(2 * t, 0, 1), np.clip(2 * t - 1, 0, 1)
        want = np.stack([1 - a, b, a - b], 1)
    else:
        want = np.stack([1 - t, t], 1)
    assert np.allclose(W[idx], want, atol=1e-9)


def test_band_end_rows_pin_the_ramp_ends(arm_mesh):
    """mesh.build's band-end rows are 100% upper / 100% lower, so the ramp
    (and the helper) stop where the band does."""
    bones = arm_bones(True)
    w = arm_weights(arm_mesh, True)
    W = weights.as_matrix(w, [b["name"] for b in bones])
    s = (arm_mesh["verts"] - ELBOW) @ np.array([1.0, 0.0])
    e = mesh.BAND_EXTENT * HW
    ends = arm_mesh["band_rows"][0][0] + arm_mesh["band_rows"][0][-1]
    assert np.allclose(W[arm_mesh["band_rows"][0][0], 0], 1.0)
    assert np.allclose(W[arm_mesh["band_rows"][0][-1], 1], 1.0)
    assert len(ends) >= 6
    assert np.all(W[np.abs(s) > e + weights.END_TOL, 2] == 0.0)    # no helper weight outside the band


def test_heat_mode_ignores_joints(arm_mesh):
    bones = arm_bones(False)
    heat = weights.compute(arm_mesh["verts"], arm_mesh["tris"], mesh.outlines(arm_mesh), bones,
                           [elbow_joint(False)], mode="heat")
    auto = arm_weights(arm_mesh, False)
    assert heat != auto
    check_rows(heat, ["upper", "fore"])


def test_weights_do_not_depend_on_the_y_direction(arm_mesh):
    """The rig builder converts canvas (y down) to world (y up); weights must
    come out the same in either."""
    flip = np.array([1.0, -1.0])
    bones = arm_bones(True)
    a = weights.weight_matrix(arm_mesh["verts"], arm_mesh["tris"], mesh.outlines(arm_mesh), bones, [elbow_joint(True)])
    fb = [dict(b, head=tuple(np.asarray(b["head"]) * flip), tail=tuple(np.asarray(b["tail"]) * flip)) for b in bones]
    fj = dict(elbow_joint(True), center=tuple(ELBOW * flip))
    b = weights.weight_matrix(arm_mesh["verts"] * flip, arm_mesh["tris"], [o * flip for o in mesh.outlines(arm_mesh)],
                              fb, [fj])
    assert np.allclose(a, b, atol=1e-9)


# ---------------------------------------------------------------- visibility

def pants(gap):
    """A hip block and two legs: a wide left leg (half-width 30) and a thin
    right one (half-width 10), `gap` px apart. Returns (alpha, bones)."""
    h, w = 230, 170
    a = np.zeros((h, w), np.uint8)
    a[10:60, 20:150] = 255                       # hips
    a[40:220, 30:90] = 255                       # left leg: x 30..90, bone at x = 60
    a[40:220, 90 + gap:110 + gap] = 255          # right leg: 20 px wide, bone at x = 100 + gap
    bones = [{"name": "hip", "head": (85.0, 15.0), "tail": (85.0, 40.0)},
             {"name": "leg_l", "head": (60.0, 50.0), "tail": (60.0, 215.0)},
             {"name": "leg_r", "head": (100.0 + gap, 50.0), "tail": (100.0 + gap, 215.0)}]
    return a, bones


@pytest.mark.parametrize("gap", [6, 10])
def test_left_leg_never_gets_right_leg_weight_across_the_crotch_gap(gap):
    """Where the gap between the legs is narrower than the distance to the
    vertex's own bone, the nearest bone by straight-line distance is the
    OTHER leg's. Visibility (the segment to the bone stays inside the
    outline) keeps that bone from heating the vertex; diffusion has to go
    round through the crotch, which leaves nothing after pruning."""
    alpha, bones = pants(gap)
    m = mesh.build(alpha, (0, 0))
    out = mesh.outlines(m)
    V = m["verts"]
    left = (V[:, 0] < 91) & (V[:, 1] > 100)
    inner = left & (V[:, 0] > 85)                                 # the left leg's inner edge
    d_own = np.abs(V[:, 0] - 60)
    d_other = np.abs(V[:, 0] - (100 + gap))
    assert (inner & (d_other < d_own)).sum() >= 3                 # the test is meaningful: they exist
    w = weights.compute(V, m["tris"], out, bones)
    check_rows(w, ["hip", "leg_l", "leg_r"])
    assert all("leg_r" not in dict(w[i]) for i in np.flatnonzero(left))
    blind = weights.compute(V, m["tris"], out, bones, visibility=False)
    assert any(dict(blind[i]).get("leg_r", 0) > 0.5 for i in np.flatnonzero(inner))   # without it: wrong leg


def test_visible_matrix_on_a_simple_case():
    square = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    notch = np.array([[0, 0], [10, 0], [10, 10], [6, 10], [6, 3], [4, 3], [4, 10], [0, 10]], float)
    V = np.array([[2.0, 8.0], [8.0, 8.0]])
    bone = [{"name": "b", "head": (8.0, 8.0), "tail": (8.0, 9.0)}]
    assert weights.visible(V, bone, [square]).all()
    vis = weights.visible(V, bone, [notch])
    assert not vis[0, 0] and vis[1, 0]                           # the notch's wall hides the bone


# ---------------------------------------------------------------- E12 in the official runtime

def _elbow42():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research", "experiments", "py")
    if path not in sys.path:
        sys.path.insert(0, path)
    import elbow42
    return elbow42


def test_helper_elbow_holds_120_degrees_in_spine_core(tmp_path):
    """SOTA §7 P2 acceptance 2, on the synthetic two-layer arm, judged by
    spine-core 4.2.120 (qa/run.mjs dump --folds): with the half-angle helper
    the elbow has 0 folds at 120 and 150 deg and keeps >= 0.9 of the joint
    band's silhouette area at 120 deg (measured 0.902; the continuum limit
    of a linear ramp is 0.917). Without the helper the same meshes fold
    before 150 deg. numpy LBS agrees with the runtime (C1)."""
    need_oracle()
    e = _elbow42()
    angles = [0.0, 120.0, 150.0]
    layers = e.arm_layers()
    meshes = e.build_meshes(layers, mesh.BAND_EXTENT, mesh.BAND_ROWS)
    out = {}
    for v in ("rigid", "ramp", "helper"):
        info, rows = e.sweep(v, str(tmp_path), angles, layers=layers, meshes=meshes)
        out[v] = {r["angle"]: r for r in rows}
        assert max(r["c1"] for r in rows) < 0.1
        assert all(r["folds"] == r["folds_numpy"] for r in rows)
    assert all(r["folds"] == 0 and abs(r["band_sum"] - 1) < 1e-6 for r in out["rigid"].values())
    h = out["helper"]
    assert h[120.0]["folds"] == 0 and h[150.0]["folds"] == 0
    assert h[120.0]["band_union"] >= 0.9
    assert out["ramp"][150.0]["folds"] > 0
    assert out["ramp"][120.0]["band_union"] < h[120.0]["band_union"] - 0.1


@pytest.mark.parametrize("helper", [False, True])
def test_asymmetric_reach_ramp(arm_mesh, helper):
    """"reach" [upper, lower]: with a helper, upper -> helper over the upper
    reach and helper -> lower over the lower one (the helper owns the joint
    whatever the lengths); without one, one linear ramp over the whole band.
    A symmetric reach is the old extent exactly."""
    bones = arm_bones(helper)
    up, lo = 1.6, 0.8
    j = dict(elbow_joint(helper), reach=(up, lo))
    V = arm_mesh["verts"]
    m, ramp = weights.joint_ramp(V, j, bones)
    s = (V[m] - ELBOW) @ np.array([1.0, 0.0])
    assert s.min() >= -up * HW - weights.END_TOL - 1e-9 and s.max() <= lo * HW + weights.END_TOL + 1e-9
    if helper:
        a = np.clip((s + up * HW) / (up * HW), 0, 1)
        b = np.clip(s / (lo * HW), 0, 1)
        assert np.allclose(ramp, np.stack([1 - a, b, a - b], 1))
    else:
        t = np.clip((s + up * HW) / ((up + lo) * HW), 0, 1)
        assert np.allclose(ramp, np.stack([1 - t, t], 1))
    sym = dict(elbow_joint(helper), reach=(1.5, 1.5))
    m1, r1 = weights.joint_ramp(V, sym, bones)
    m2, r2 = weights.joint_ramp(V, elbow_joint(helper, extent=1.5), bones)
    assert np.array_equal(m1, m2) and np.allclose(r1, r2)
    with pytest.raises(ValueError):
        weights.joint_ramp(V, dict(sym, reach=(1.5, 0.0)), bones)


def test_chain_composition_of_overlapping_bands():
    """An "overlap" band running into the band above it on the same chain
    splits whatever its upper bone holds there (weights CHAIN COMPOSITION):
    a shin vertex in both the knee's lower half and the ankle's ramp gets
    helper, shin and foot, rows sum to 1, and the result does not depend
    on the order the joints are given in. Without "overlap" the later band
    simply takes the vertex (the old rule, which rigbuild avoids by sharing)."""
    alpha = capsule((330, 60), (26, 30), 22, (304, 30), 20)
    knee, ankle = np.array([126.0, 30.0]), np.array([216.0, 30.0])
    bones = [{"name": "thigh", "head": (26.0, 30.0), "tail": tuple(knee)},
             {"name": "shin", "head": tuple(knee), "tail": tuple(ankle)},
             {"name": "knee_mid", "head": tuple(knee), "tail": tuple(knee)},
             {"name": "foot", "head": tuple(ankle), "tail": (304.0, 30.0)}]
    jk = {"center": tuple(knee), "upper": "thigh", "helper": "knee_mid", "lower": "shin", "halfwidth": HW,
          "axis": (1, 0), "reach": (2.0, 3.0), "exclusive": True}
    ja = {"center": tuple(ankle), "upper": "shin", "helper": None, "lower": "foot", "halfwidth": HW,
          "axis": (1, 0), "reach": (3.5, 0.5), "exclusive": True, "overlap": True}
    m = mesh.build(alpha, (0, 0), bands=[dict(jk, rows=3), dict(ja, rows=3)])
    names = [b["name"] for b in bones]
    W = weights.weight_matrix(m["verts"], m["tris"], mesh.outlines(m), bones, [jk, ja])
    W2 = weights.weight_matrix(m["verts"], m["tris"], mesh.outlines(m), bones, [ja, jk])
    assert np.allclose(W, W2)
    assert np.allclose(W.sum(1), 1.0)
    x = m["verts"][:, 0]
    both = (x > ankle[0] - 3.5 * HW + 1) & (x < knee[0] + 3.0 * HW - 1)
    assert both.any()
    mk, rk = weights.joint_ramp(m["verts"], jk, bones)
    ma, ra = weights.joint_ramp(m["verts"], ja, bones)
    kr = np.zeros((len(x), 3))
    kr[mk] = rk                                   # columns: thigh, shin, helper
    t = np.zeros(len(x))
    t[ma] = ra[:, 1]                              # foot share of the ankle ramp
    idx = np.flatnonzero(both & mk & ma)
    col = {n: i for i, n in enumerate(names)}
    assert np.allclose(W[idx, col["knee_mid"]], kr[idx, 2])
    assert np.allclose(W[idx, col["shin"]], kr[idx, 1] * (1 - t[idx]))
    assert np.allclose(W[idx, col["foot"]], kr[idx, 1] * t[idx])
    assert (W[idx][:, [col["knee_mid"], col["shin"], col["foot"]]] > 0.02).all(1).any()
    # without "overlap" the ankle band takes its vertices whole
    W3 = weights.weight_matrix(m["verts"], m["tris"], mesh.outlines(m), bones, [jk, dict(ja, overlap=False)])
    assert np.allclose(W3[idx, col["knee_mid"]], 0.0)


def test_compose_accessory_takes_a_share_of_the_parent_only():
    """P2 review: an accessory (a cuff hanging from the upper arm) takes its
    share of the PARENT's weight, never of the helper's or another bone's,
    so merging it back gives the original weights exactly (it moves like
    its parent at rest); parts under the prune threshold go whole to one
    bone, and a vertex that already has three bones keeps its share on the
    parent (L8)."""
    names = ["upper", "helper", "hips", "cuff"]
    M = np.array([[0.6, 0.4, 0.0, 0.0],     # share 1: all of the upper arm's 0.6
                  [0.6, 0.4, 0.0, 0.0],     # share 0.5: 0.3 each
                  [0.03, 0.97, 0.0, 0.0],   # 0.015 is under the prune: stays on the parent
                  [0.3, 0.7, 0.0, 0.0],     # 0.285 to the cuff leaves 0.015: all 0.3 go
                  [0.5, 0.3, 0.2, 0.0],     # three bones already: no room
                  [0.0, 1.0, 0.0, 0.0]])    # no parent weight: nothing to take
    share = np.array([1.0, 0.5, 0.5, 0.95, 0.5, 1.0])
    x, stuck = weights.accessory_split(M, names, "cuff", "upper", share)
    assert stuck.tolist() == [False, False, False, False, True, False]
    W = M.copy()
    n = weights.compose_accessory(W, names, "cuff", "upper", share)
    assert n == 3
    assert np.allclose(W[:, 3], [0.6, 0.3, 0.0, 0.3, 0.0, 0.0])
    assert np.allclose(W[:, :3].sum(1) + W[:, 3], 1.0)
    assert np.allclose(W[:, 1:3], M[:, 1:3])               # helper and hips untouched
    merged = W.copy()
    merged[:, 0] += merged[:, 3]
    merged[:, 3] = 0.0
    assert np.allclose(merged, M)
    assert ((W > 0) & (W < 0.02)).sum() == 0
    assert ((W >= 0.02).sum(1) <= 3).all()


def test_row_composition_gives_the_lower_band_its_ramp_outright():
    """compose "row" (P2 review R2): an overlap band's own bone takes its
    ramp value of the whole row, so the foot's weight is the ankle ramp's
    t everywhere (it falls off evenly up the calf), and the knee's helper
    and the shin keep their split of the rest: helper h (1 - t), shin
    (1 - h)(1 - t). The default ("upper") gives the foot t x the shin's
    share instead."""
    alpha = capsule((330, 60), (26, 30), 22, (304, 30), 20)
    knee, ankle = np.array([126.0, 30.0]), np.array([216.0, 30.0])
    bones = [{"name": "thigh", "head": (26.0, 30.0), "tail": tuple(knee)},
             {"name": "shin", "head": tuple(knee), "tail": tuple(ankle)},
             {"name": "knee_mid", "head": tuple(knee), "tail": tuple(knee)},
             {"name": "foot", "head": tuple(ankle), "tail": (304.0, 30.0)}]
    jk = {"center": tuple(knee), "upper": "thigh", "helper": "knee_mid", "lower": "shin", "halfwidth": HW,
          "axis": (1, 0), "reach": (2.0, 3.0), "exclusive": True}
    ja = {"center": tuple(ankle), "upper": "shin", "helper": None, "lower": "foot", "halfwidth": HW,
          "axis": (1, 0), "reach": (3.5, 0.5), "exclusive": True, "overlap": True, "compose": "row"}
    m = mesh.build(alpha, (0, 0), bands=[dict(jk, rows=3), dict(ja, rows=3)])
    col = {b["name"]: i for i, b in enumerate(bones)}
    W = weights.weight_matrix(m["verts"], m["tris"], mesh.outlines(m), bones, [jk, ja])
    U = weights.weight_matrix(m["verts"], m["tris"], mesh.outlines(m), bones, [jk, dict(ja, compose="upper")])
    assert np.allclose(W.sum(1), 1.0)
    x = m["verts"][:, 0]
    mk, rk = weights.joint_ramp(m["verts"], jk, bones)
    ma, ra = weights.joint_ramp(m["verts"], ja, bones)
    kr = np.zeros((len(x), 3))
    kr[mk] = rk                                   # columns: thigh, shin, helper
    t = np.zeros(len(x))
    t[ma] = ra[:, 1]
    idx = np.flatnonzero(mk & ma & (x > ankle[0] - 3.5 * HW + 1) & (x < knee[0] + 3.0 * HW - 1))
    assert len(idx) and (kr[idx, 2] > 0.05).any()  # the overlap, where the knee's helper still holds weight
    assert np.allclose(W[idx, col["foot"]], t[idx])
    assert np.allclose(W[idx, col["knee_mid"]], kr[idx, 2] * (1 - t[idx]))
    assert np.allclose(W[idx, col["shin"]], kr[idx, 1] * (1 - t[idx]))
    assert np.allclose(U[idx, col["foot"]], kr[idx, 1] * t[idx])
    assert (W[idx, col["foot"]] > U[idx, col["foot"]] + 1e-6).any()
