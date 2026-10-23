"""Two leg styles (rigbuild LEG STYLES): "outward" (the mascot's two-bone
IK, knees out) and "forward" (a realistic front view: the knee bends toward
the camera, so a crouch foreshortens the leg along its own line). Synthetic
character only (the P5 warden is the user's game art).
"""
import json
import os

import numpy as np
import pytest

from spinegen import budget, lbs, oracle, rigbuild
from spinegen import ir as irmod
from spinegen import template as tmpl

from test_rigbuild import layers_dir  # noqa: F401  (module fixture: synthetic layers dir)

DEPTHS = (0.03, 0.05, 0.10)
SHIFTS = (-40.0, 0.0, 40.0)


@pytest.fixture(scope="module")
def fwd(layers_dir):  # noqa: F811
    return rigbuild.build(layers_dir, mode="joint", leg_style="forward")


def _hips(ir):
    return irmod.by_name(ir["bones"])[irmod.leg_chains(ir)[0]["thigh"]]["parent"]


def _pose(ir, depth, dx):
    b = irmod.by_name(ir["bones"])
    h = _hips(ir)
    return {h: {"x": b[h]["x"] + dx, "y": b[h]["y"] - depth * ir["H"]}}


def test_auto_reads_the_legs_off_the_art(layers_dir):  # noqa: F811
    """The synthetic biped (legs 0.354 H, 3.1x as long as wide) is a mascot:
    "auto" keeps the outward legs. Lower the template's bars below it and
    the same art is read as realistic; an explicit style always wins."""
    res = rigbuild.build(layers_dir, mode="joint")
    ls = res["report"]["leg_style"]
    assert ls["style"] == "outward" and ls["asked"] == "auto"
    assert 0.3 < ls["leg_H"] < ls["min_leg_H"] and 2.5 < ls["slender"] < ls["min_slender"]
    assert all(len(c["bones"]) == 2 for c in res["ir"]["ik"])
    t = tmpl.load("biped_mascot")
    t["leg_style"] = dict(t["leg_style"], auto_forward_min_leg_H=0.3, auto_forward_min_slender=3.0)
    assert rigbuild.build(layers_dir, template=t, mode="joint")["report"]["leg_style"]["style"] == "forward"
    t["leg_style"]["auto_forward_min_slender"] = 3.2   # long enough, too stout: still a mascot
    assert rigbuild.build(layers_dir, template=t, mode="joint")["report"]["leg_style"]["style"] == "outward"
    assert rigbuild.build(layers_dir, template=t, mode="joint",
                          leg_style="forward")["report"]["leg_style"]["style"] == "forward"
    with pytest.raises(ValueError):
        rigbuild.build(layers_dir, mode="joint", leg_style="sideways")


def test_forward_legs_are_one_bone_chains_onto_a_following_knee(fwd):
    ir = fwd["ir"]
    b = irmod.by_name(ir["bones"])
    legs = irmod.leg_chains(ir)
    assert [l["style"] for l in legs] == ["forward", "forward"]
    order = {c["name"]: c["order"] for k in irmod.CONSTRAINT_KINDS for c in ir[k]}
    for leg in legs:
        s = leg["side"]
        assert b[leg["knee_target"]]["parent"] == leg["target"] == f"ik_foot_{s}"
        thigh_ik, shin_ik = (next(c for c in ir["ik"] if c["name"] == n) for n in leg["constraints"])
        assert thigh_ik["bones"] == [leg["thigh"]] and thigh_ik["compress"] and not thigh_ik["stretch"]
        assert shin_ik["bones"] == [leg["shin"]] and shin_ik["compress"] and shin_ik["stretch"]
        tc = next(c for c in ir["transform"] if c["name"] == f"knee_follow_{s}")
        assert tc["bones"] == [leg["knee_target"]] and tc["target"] == _hips(ir) and not tc["local"]
        assert order[tc["name"]] < order[thigh_ik["name"]] < order[shin_ik["name"]] < order[f"knee_half_{s}"]
        # the knee stays on the hip-ankle line sideways, drops the template's share of the hips
        assert 0.3 < tc["mix"]["x"] < 0.7 and tc["mix"]["y"] == pytest.approx(0.3)
        rep = fwd["report"]["ik"][leg["name"]]
        assert rep["style"] == "forward" and rep["setup_error_px"] <= 1e-3
    # budget's legs: the foot under the target, not the knee target
    bl = budget.ik_legs(ir)
    assert [x["foot"] for x in bl] == ["foot_l", "foot_r"] and all(x["style"] == "forward" for x in bl)
    # the setup pose is the drawn one
    ev = lbs.Evaluator(ir)
    w, w0 = ev.world(), ev.world(constraints=False)
    assert float(np.abs(np.asarray(w) - np.asarray(w0)).max()) < 1e-3


@pytest.mark.parametrize("depth", DEPTHS)
def test_forward_crouch_foreshortens_without_folds_or_slide(fwd, depth):
    """Down 0.03-0.10 H with the hips shifted -40 / 0 / +40 px: no fold, the
    whole planted foot does not move, the knees move sideways only with the
    hips' shift (the same amount for both legs, 0 straight down), the leg
    stays nearly straight in the picture and the thigh foreshortens more
    than the shin."""
    ir = fwd["ir"]
    c = rigbuild.crouch(ir, depth * ir["H"])
    assert c["knees_ok"] and c["foot_slide"] <= 1e-6 and c["max_slide"] <= 1e-6
    for k in c["knees"].values():
        assert k["style"] == "forward" and k["side_px"] <= 1e-4
        assert k["thigh_scale"] < k["shin_scale"] < 1.0 and k["bend_deg"] < 5.0
    ev = lbs.Evaluator(ir)
    feet = rigbuild.planted_vertices(ir)
    w0 = ev.world()
    shown = dict(ev.shown())
    legs = irmod.leg_chains(ir)
    for dx in SHIFTS:
        pose = _pose(ir, depth, dx)
        assert ev.folds(pose=pose)["folds"] == 0
        w1 = ev.world(pose=pose)
        for n, i in feet.items():
            moved = ev.attachment_vertices(w1, n, shown[n])[i] - ev.attachment_vertices(w0, n, shown[n])[i]
            assert np.abs(moved).max() <= 1e-6
        side = [w1[ev.index[l["shin"]]][4] - w0[ev.index[l["shin"]]][4] for l in legs]
        assert side[0] == pytest.approx(side[1], abs=1e-6)          # symmetric
        assert abs(side[0]) <= 0.7 * abs(dx) + 1e-4                 # less than the hips' shift


def test_outward_rig_has_no_forward_parts(layers_dir):  # noqa: F811
    """The mascot style is what it was: two-bone IK, no knee target, no
    knee-follow constraint."""
    ir = rigbuild.build(layers_dir, mode="joint", leg_style="outward")["ir"]
    assert not any(b["name"].startswith("ik_knee") for b in ir["bones"])
    assert not any(c["name"].startswith("knee_follow") for c in ir["transform"])
    assert [l["style"] for l in irmod.leg_chains(ir)] == ["outward", "outward"]


def test_leg_budget_scans_the_forward_legs(fwd):
    ir = fwd["ir"]
    ik = budget.ik_scan(ir, fwd["images"], fwd["joints"], shifts=(0.0, 40.0), depth_step=0.05, depth_max=0.10,
                        move_step=0.08, move_max=0.08)
    assert ik["meta"]["legs"] == ["leg_ik_l", "leg_ik_r"]
    crouch = [p for p in ik["moves"]["crouch dx+0"]["poses"] if p["valid"]]
    assert len(crouch) == 2 and all(p["dislocated_px"] <= budget.IK_DISLOCATE for p in crouch)
    H = ik["meta"]["H"]
    # foreshortening is what the forward leg means: G2 holds the joints to
    # the bones' axial scale, so the straight crouch holds all the way ...
    assert ik["moves"]["crouch dx+0"]["soft"] == pytest.approx(0.10 * H) and ik["moves"]["crouch dx+0"]["hard"] == \
        pytest.approx(0.10 * H)
    # ... where reading it against the setup area calls the first pose a squash
    probe = budget.LegProbe(ir, fwd["images"], budget.load_joints(fwd["joints"]), budget.ik_legs(ir))
    res = probe.judge(probe.pose_of("crouch", amount=0.05 * H))
    assert res["soft"]
    probe._foreshortening = lambda *a: 1.0
    assert not probe.judge(probe.pose_of("crouch", amount=0.05 * H))["soft"]


def test_forward_crouch_in_both_runtimes(fwd, tmp_path):
    """The world translate constraint and one-bone compress IK mean the same
    in spine-core 4.2.120 (also = spinegen.lbs, C1) and 4.3.13."""
    for rt in ("4.2", "4.3"):
        ok, why = oracle.available(rt)
        if not ok:
            pytest.skip(f"spine-core {rt} unavailable: {why}")
    from spinegen.emit import spine43
    paths = rigbuild.write(str(tmp_path), fwd)
    p43 = str(tmp_path / "rig43.json")
    spine43.write(fwd["ir"], p43)
    ir = fwd["ir"]
    ev = lbs.Evaluator(ir)
    shown = dict(ev.shown())
    h = _hips(ir)
    for depth, dx in ((0.10, -40.0), (0.05, 40.0)):
        pose = {h: {"x": dx, "y": -depth * ir["H"]}}
        f42 = oracle.dump(paths["skeleton"], paths["atlas"], pose=pose, pose_mode="add", folds=True)["frames"][0]
        f43 = oracle.dump(p43, paths["atlas"], pose=pose, pose_mode="add", folds=True, runtime="4.3")["frames"][0]
        assert f42["folds"] == 0 and f43["folds"] == 0
        w = ev.world(pose=_pose(ir, depth, dx))
        for n in shown:
            v42 = np.asarray(f42["slots"][n]["vertices"], float).reshape(-1, 2)
            v43 = np.asarray(f43["slots"][n]["vertices"], float).reshape(-1, 2)
            assert np.abs(v42 - v43).max() <= 0.01, n
            assert np.abs(v42 - ev.attachment_vertices(w, n, shown[n])).max() <= 0.01, n


def test_template_documents_both_styles():
    raw = json.load(open(os.path.join(tmpl.TEMPLATE_DIR, "biped_mascot.json")))
    assert raw["leg_style"]["default"] == "auto"
    assert any("'forward'" in line for line in raw["doc"])
