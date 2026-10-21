"""spinegen.budget: theta_soft / theta_hard per joint direction (SOTA §4.3)
in the spinegen-budget/1 form (§3.2), on synthetic characters whose limits
are known by construction; the monkey (third-party, read in place) only
when it is on this machine.
"""
import json

import numpy as np
import pytest

from conftest import third_party

from spinegen import budget, raster, synth_art

SCALE = 0.5


def _scan(only, step=4.0, max_angle=60.0, **kw):
    scan_kw = {k: kw.pop(k) for k in ("scale", "verify_full", "keep_trace") if k in kw}
    ir, images, joints = synth_art.build(scale=SCALE, **kw)
    return budget.scan(ir, images, joints, step=step, max_angle=max_angle, only=set(only), **scan_kw)


def test_load_joints_forms():
    a = budget.load_joints([{"name": "e", "bone": "fore", "center": [1, 2], "halfwidth": 5}])
    b = budget.load_joints({"joints": {"e": {"bone": "fore", "band": {"center": [1, 2], "halfwidth": 5}}}})
    assert a == b == [{"name": "e", "bone": "fore", "dirs": [1, -1],
                       "band": {"center": [1, 2], "halfwidth": 5, "lower": "fore"}}]
    c = budget.load_joints({"joints": [{"band": {"lower": "fore"}, "world": [3, 4], "dirs": [1]}]})
    assert c[0]["name"] == "fore" and c[0]["band"]["center"] == [3, 4] and c[0]["dirs"] == [1]
    with pytest.raises(ValueError):
        budget.load_joints([{"name": "x"}])


def test_square_cut_elbow_soft_zero_hard_unlimited():
    """A rigid cut-out never folds or holes at the elbow (hard = max) but
    tears at the first step (soft = 0, limited by the seam)."""
    out = _scan(["elbow_l"], mode="rigid", overlap=0, round_ends=False)
    assert out["schema"] == "spinegen-budget/1"
    for d in ("+", "-"):
        e = out["joints"]["elbow_l"][d]
        assert e["soft"] == 0 and e["limit_by"] == "seam@upper_arm_l|forearm_l"
        assert e["hard"] == 60 and e["hard_by"] is None
        assert e["first_fail"]["G4_SEAM_GAP"]["angle"] == (4 if d == "+" else -4)
        assert "G1_FOLDS" not in e["first_fail"] and "G5_HOLES" not in e["first_fail"]
    m = out["meta"]["joints"]["elbow_l"]
    assert m["upper_slots"] == ["upper_arm_l"] and m["lower_slots"] == ["forearm_l"]
    assert m["moving_slots"] == ["forearm_l"]


def test_ramp_weighted_elbow_is_limited_by_folds():
    out = _scan(["elbow_l"], max_angle=120.0, mode="joint")
    for d, sign in (("+", 1), ("-", -1)):
        e = out["joints"]["elbow_l"][d]
        assert 60 <= e["soft"] <= e["hard"] < 120
        assert e["hard_by"].startswith("fold@")
        assert e["first_fail"]["G1_FOLDS"]["angle"] == sign * (e["hard"] + 4)
        assert all(abs(f["angle"]) > e["soft"] for f in e["first_fail"].values())


def test_unpainted_torso_limits_the_shoulder_hard():
    out = _scan(["shoulder_l"], mode="rigid", hole=True)
    clean = _scan(["shoulder_l"], mode="rigid")
    for d, sign in (("+", 1), ("-", -1)):
        e = out["joints"]["shoulder_l"][d]
        assert e["hard_by"] == "hole@torso" and e["hard"] < 30
        assert e["first_fail"]["G5_HOLES"]["angle"] == sign * (e["hard"] + 4)
        assert e["soft"] <= e["hard"]
        c = clean["joints"]["shoulder_l"][d]
        assert c["hard"] == 60 and c["hard_by"] is None and "G5_HOLES" not in c["first_fail"]


def test_coarse_sweep_limits_are_verified_at_full_scale():
    ir, images, joints = synth_art.build(scale=SCALE, mode="rigid", overlap=24)
    out = budget.scan(ir, images, joints, step=4.0, max_angle=60.0, scale=0.5, only={"elbow_l"})
    assert out["meta"]["verified"] and out["meta"]["frames"]["full"] > 0
    canvas = raster.Canvas(out["meta"]["canvas"]["origin"], out["meta"]["canvas"]["size"], 1.0)
    p = budget.Probe(ir, images, budget.load_joints(joints)[1], 1.0, canvas=canvas)
    for d, sign in (("+", 1), ("-", -1)):
        e = out["joints"]["elbow_l"][d]
        assert "coarse" in e
        if e["soft"] > 0:
            assert p.judge(sign * e["soft"])["soft"]
        if e["soft"] < 60:
            assert not p.judge(sign * (e["soft"] + 4))["soft"]


def test_trace_keeps_every_gate_value():
    out = _scan(["elbow_l"], step=10.0, max_angle=30.0, mode="rigid", overlap=0, round_ends=False,
                keep_trace=True)
    tr = out["meta"]["joints"]["elbow_l"]["trace"]
    assert [t["angle"] for t in tr] == [10, 20, 30, -10, -20, -30]
    assert set(tr[0]["values"]) == set(budget.GATE_IDS.values())
    assert tr[0]["values"]["G4_SEAM_GAP"] > 0


def test_contact_sheet_layout():
    ir, images, joints = synth_art.build(scale=0.3, mode="rigid", overlap=0, round_ends=False)
    b = budget.scan(ir, images, joints, step=10.0, max_angle=30.0, only={"elbow_l"})
    sheet = budget.contact_sheet(ir, images, joints, b, every=10.0, max_angle=30.0, thumb=60, only={"elbow_l"})
    tile_w, tile_h = 60 + 6, 3 * 60 + 12 + 26
    assert sheet.shape == (2 * tile_h, 230 + 4 * tile_w, 4)
    # the 0 deg tile is framed green (soft holds), the 10 deg one not
    assert tuple(sheet[1, 231, :3]) == (40, 160, 60)
    assert tuple(sheet[1, 231 + tile_w, :3]) != (40, 160, 60)


def test_cli_writes_budget_and_per_joint_sheets(tmp_path):
    rig, imgdir, jp = synth_art.write(str(tmp_path / "c"), mode="rigid", overlap=0, round_ends=False, scale=0.3)
    out = tmp_path / "b" / "budget.json"
    budget.main([rig, imgdir, "--joints", jp, "-o", str(out), "--step", "10", "--max-angle", "20",
                 "--only", "elbow_l,shoulder_l", "--sheet", str(tmp_path / "sheets"), "--sheet-max", "20"])
    b = json.loads(out.read_text())
    assert set(b["joints"]) == {"elbow_l", "shoulder_l"}
    assert b["joints"]["elbow_l"]["+"]["limit_by"].startswith("seam@")
    assert sorted(p.name for p in (tmp_path / "sheets").iterdir()) == ["elbow_l.png", "shoulder_l.png"]


def test_scan_canvas_holds_the_swung_limb():
    ir, images, joints = synth_art.build(scale=SCALE, mode="rigid")
    small = raster.Renderer(ir, images).canvas
    big = budget.scan_canvas(ir, images, joints, 180)
    assert big.w > small.w and big.h > small.h
    r = raster.Renderer(ir, images, canvas=big)
    rot = {b["name"]: b["rotation"] for b in ir["bones"]}
    for a in (90, -90, 180):
        xy = np.concatenate([g.xy for g in r.geometry(r.ev.world({"upperarm_l": {"rotation": rot["upperarm_l"] + a}}))
                             .values()])
        px = big.to_px(xy)
        assert (px >= 0).all() and (px[:, 0] <= big.W).all() and (px[:, 1] <= big.H).all()


# ---------------------------------------------------------------- the monkey (third-party, read in place)

def test_monkey_hand_carries_a_trouser_shard():
    """The shipped monkey's hand layers carry a shard of the trousers (a
    flat-image cut): moving the shoulder at all reveals thigh paint that
    correlates with what the hand showed there (G6b), and the pair is
    named. The rest pose passes."""
    js, atlas = third_party("monkey")
    ir, images = raster.load_rig(js), raster.load_images(atlas)
    p = budget.Probe(ir, images, {"name": "shoulder_r", "bone": "armR", "dirs": [1], "band": {"lower": "armR"}})
    assert p.judge(0)["soft"]
    r = p.judge(10)
    g = r["gates"]["ghost_paint"]
    assert not g["pass"] and g["worst"] == "right_leg_0_thigh|right_arm_2_hand"
    assert r["hard"]  # no fold, no hole
