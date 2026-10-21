"""spinegen.art_gates on synthetic textured characters (spinegen.synth_art):
one case that must fail and one that must pass per gate (SOTA §5).

    G1 folds          joint-mode elbow bent 100 deg folds; 30 deg does not
    G2 band area      joint-mode shoulder at 80 deg loses band area; 10 keeps it
    G4 seam gap       a square-cut, no-overlap elbow tears at 4 deg; a ramp-
                      weighted one never does; motion alone is never a tear
    G5 holes          a torso with an unpainted patch under the arm shows it
                      when the arm moves; arm + forearm + torso closing a loop
                      around open space is not a hole (SOTA §8.3)
    G6a ghost band    a long square underlay swings out past 0.25 limb width
    G6b ghost paint   an arm baked into the torso paint correlates ~1; a clean
                      torso ~0
"""
import functools

import numpy as np
import pytest

from spinegen import art_gates as ag
from spinegen import budget, raster, synth_art

SCALE = 0.5   # ~300 px tall fixtures: every gate still has tens of pixels to read


@functools.lru_cache(maxsize=None)
def _rig(**kw):
    ir, images, joints = synth_art.build(scale=SCALE, **kw)
    canvas = budget.scan_canvas(ir, images, joints, 180)
    return ir, images, {j["name"]: j for j in budget.load_joints(joints)}, canvas


def _probe(joint, **kw):
    ir, images, joints, canvas = _rig(**kw)
    return budget.Probe(ir, images, joints[joint], 1.0, canvas=canvas)


def _frame(pose_deg, **kw):
    """(rest, frame, joints) with several bones turned from setup."""
    ir, images, joints, canvas = _rig(**kw)
    r = raster.Renderer(ir, images, canvas=canvas)
    rot = {b["name"]: b["rotation"] for b in ir["bones"]}
    return r.frame(), r.frame({b: {"rotation": rot[b] + a} for b, a in pose_deg.items()}), joints


# ---------------------------------------------------------------- G1 / G2

def test_g1_folds_only_past_the_bend_the_weights_hold():
    p = _probe("elbow_l", mode="joint")
    assert ag.folds(p.frame(30), rest=p.rest)["value"] == 0
    bad = ag.folds(p.frame(100), rest=p.rest)
    assert bad["value"] > 0 and not bad["pass"] and bad["worst"] in ("upper_arm_l", "forearm_l")


def test_g2_band_area():
    p = _probe("shoulder_l", mode="joint")
    ok = ag.band_area_ratio(p.rest, p.frame(10), p.joint)
    assert ok["pass"] and ok["value"] >= 0.99 and ok["slots"]
    bad = ag.band_area_ratio(p.rest, p.frame(80), p.joint)
    assert not bad["pass"] and bad["value"] < 0.9 and bad["worst"] == "upper_arm_l"
    # a rest frame is exactly 1
    assert ag.band_area_ratio(p.rest, p.rest, p.joint)["value"] == pytest.approx(1.0)


# ---------------------------------------------------------------- G4

def test_g4_square_cut_elbow_tears_at_once():
    p = _probe("elbow_l", mode="rigid", overlap=0, round_ends=False)
    assert ag.seam_gap(p.rest, p.frame(0), p.joint)["value"] == 0
    for a in (4, -10, 30):
        r = ag.seam_gap(p.rest, p.frame(a), p.joint, masks=True)
        assert not r["pass"] and r["value"] > 0, a
        assert r["worst"] == "upper_arm_l|forearm_l"
        # every counted pixel is background now and inside the hull
        gap, hull = r["masks"]["gap"], r["masks"]["hull"]
        assert (gap <= hull).all() and gap.sum() == r["value"]
    # it grows with the angle
    v = [ag.seam_gap(p.rest, p.frame(a), p.joint)["value"] for a in (4, 10, 30)]
    assert v[0] < v[1] < v[2]


def test_g4_ramp_weighted_elbow_never_opens():
    p = _probe("elbow_l", mode="joint")
    for a in (10, 30, -30, 60, 100, -100):
        assert ag.seam_gap(p.rest, p.frame(a), p.joint)["value"] == 0, a


def test_g4_motion_alone_is_not_a_tear():
    """Swinging the whole arm at the shoulder carries both elbow pieces
    alike: the elbow reads 0 even for a square-cut, no-overlap cut-out."""
    rest, fr, joints = _frame({"upperarm_l": 35}, mode="rigid", overlap=0, round_ends=False)
    assert ag.seam_gap(rest, fr, joints["elbow_l"])["value"] == 0
    assert ag.ghost_band(rest, fr, joints["elbow_l"])["value"] == 0


# ---------------------------------------------------------------- G5

def test_g5_unpainted_patch_under_the_arm():
    p = _probe("shoulder_l", mode="rigid", hole=True)
    r = ag.holes(p.rest, p.frame(-20), masks=True)
    assert not r["pass"] and r["value"] >= 1 and r["worst"] == "torso"
    assert r["holes"][0]["enclosed_by"] == "static" and r["holes"][0]["area"] >= 16
    assert r["masks"]["holes"].sum() == sum(h["px"] for h in r["holes"])
    # the same patch is covered at rest and by small motions
    assert ag.holes(p.rest, p.rest)["value"] == 0
    clean = _probe("shoulder_l", mode="rigid")
    assert ag.holes(clean.rest, clean.frame(-20))["value"] == 0


def test_g5_loop_of_parts_around_open_space_is_not_a_hole():
    rest, fr, _ = _frame({"upperarm_l": -45, "forearm_l": 90}, mode="rigid")
    lab, st, ids = ag._closed_background(fr.alpha >= 0.5)
    assert max(int(st[k, 4]) for k in ids) > 500          # the loop is really closed
    assert ag.holes(rest, fr)["value"] == 0


# ---------------------------------------------------------------- G6

def test_g6a_long_square_underlay_swings_out():
    p = _probe("shoulder_l", mode="rigid", overlap=30, round_ends=False)
    ok = ag.ghost_band(p.rest, p.frame(-10), p.joint)
    assert ok["pass"] and ok["value"] <= ok["max"]
    bad = ag.ghost_band(p.rest, p.frame(-40), p.joint, masks=True)
    assert not bad["pass"] and bad["value"] > bad["max"] == pytest.approx(0.25 * 2 * 40 * SCALE)
    assert bad["worst"] == "upper_arm_l" and bad["masks"]["ghost"].any()


def test_g6b_arm_baked_into_torso_paint():
    p = _probe("elbow_l", mode="rigid", ghost=True)
    for a in (20, -20):
        r = ag.ghost_paint(p.rest, p.frame(a), masks=True)
        assert not r["pass"] and r["value"] > 0.9, a
        assert r["worst"] == "torso|forearm_l"
        assert r["masks"]["revealed"].sum() >= ag.NCC_MIN_PX * SCALE ** 2
    clean = _probe("elbow_l", mode="rigid")
    for a in (20, -20, 40):
        r = ag.ghost_paint(clean.rest, clean.frame(a))
        assert r["pass"] and r["value"] < 0.2, a


def test_ncc():
    rng = np.random.default_rng(1)
    x = rng.uniform(0, 1, (200, 3))
    assert ag.ncc(x, x) == pytest.approx(1.0)
    assert ag.ncc(x, 1 - x) == pytest.approx(-1.0)
    assert ag.ncc(x, 0.5 * x + 0.2) == pytest.approx(1.0)       # gain and offset do not matter
    assert ag.ncc(x, np.full_like(x, 0.3)) == 0.0                 # a flat patch matches nothing


# ---------------------------------------------------------------- joints and the bundle

def test_joint_parts_and_derived_band():
    p = _probe("elbow_l", mode="rigid")
    jr = p.jr
    assert jr.upper_slots == ["upper_arm_l"] and jr.lower_slots == ["forearm_l"]
    assert jr.readable and jr.band.sum() > 0 and jr.hw == pytest.approx(40 * SCALE)
    # derive what the joints file leaves out: upper = parent, centre = bone
    # origin, halfwidth from the art (the forearm is 0.9 x the upper arm)
    ir, images, joints, canvas = _rig(mode="rigid")
    q = budget.Probe(ir, images, {"name": "e", "bone": "forearm_l", "dirs": [1], "band": {"lower": "forearm_l"}},
                     1.0, canvas=canvas)
    assert q.jr.j["upper"] == "upperarm_l"
    assert np.allclose(q.jr.j["center"], joints["elbow_l"]["band"]["center"])
    assert q.jr.hw == pytest.approx(0.9 * 40 * SCALE, abs=2.0)


def test_evaluate_bundles_every_gate():
    p = _probe("elbow_l", mode="rigid", overlap=0, round_ends=False)
    out = ag.evaluate(p.rest, p.frame(10), [p.joint])
    ids = [g["id"] for g in out["gates"]]
    assert ids == ["G1_FOLDS", "G5_HOLES", "G6B_GHOST_PAINT", "G2_AREA", "G4_SEAM_GAP", "G6A_GHOST_BAND"]
    assert not out["pass"]
    assert ag.evaluate(p.rest, p.rest, [p.joint])["pass"]
