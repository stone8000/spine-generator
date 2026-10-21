"""spinegen.budget: theta_soft / theta_hard per joint direction (SOTA §4.3)
in the spinegen-budget/1 form (§3.2), on synthetic characters whose limits
are known by construction; the monkey (third-party, read in place) only
when it is on this machine.
"""
import json

import numpy as np
import pytest

from conftest import third_party

from spinegen import art_gates as ag
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


def test_square_cut_elbow_tears_soft_and_hard_zero():
    """A rigid cut-out never folds or opens a body hole at the elbow, but
    its seam tears at the first step: soft = hard = 0, both limited by G4.
    P2 review F2: the seam gap is a joint HOLE (SOTA §5 G4), so theta_hard
    (0 folds AND 0 holes, §4.3) stops there too; before, hard read the
    sweep's maximum (60 here, 150 on the rigid monkey) while the wedge
    stood open."""
    out = _scan(["elbow_l"], mode="rigid", overlap=0, round_ends=False)
    assert out["schema"] == "spinegen-budget/1"
    for d in ("+", "-"):
        e = out["joints"]["elbow_l"][d]
        assert e["soft"] == 0 and e["limit_by"] == "seam@upper_arm_l|forearm_l"
        assert e["hard"] == 0 and e["hard_by"] == "seam@upper_arm_l|forearm_l"
        assert e["first_fail"]["G4_SEAM_GAP"]["angle"] == (4 if d == "+" else -4)
        assert "G1_FOLDS" not in e["first_fail"] and "G5_HOLES" not in e["first_fail"]
    m = out["meta"]["joints"]["elbow_l"]
    assert m["upper_slots"] == ["upper_arm_l"] and m["lower_slots"] == ["forearm_l"]
    assert m["moving_slots"] == ["forearm_l"]


def test_hard_limit_keeps_g4s_speck_rule(monkeypatch):
    """theta_hard counts G4 with the same component rule as soft (G4_MIN_PX):
    what the rule drops ends neither a hold nor a strike."""
    ir, images, joints = synth_art.build(scale=SCALE, mode="rigid", overlap=0, round_ends=False)
    elbow = next(j for j in budget.load_joints(joints) if j["name"] == "elbow_l")
    p = budget.Probe(ir, images, elbow, 1.0, canvas=budget.scan_canvas(ir, images, joints, 60))
    assert p.judge(0)["hard"]
    r = p.judge(4)
    g = r["gates"]["seam"]
    assert not g["pass"] and not r["hard"] and r["hard_by"].startswith("seam@")
    assert g["raw_px"] >= g["value"] >= ag.G4_MIN_PX * SCALE ** 2
    # the same gap read as specks (every component under the rule): it holds
    monkeypatch.setattr(ag, "G4_MIN_PX", 10.0 * g["raw_px"] / SCALE ** 2)
    r = p.judge(4)
    assert r["gates"]["seam"]["raw_px"] == g["raw_px"] and r["gates"]["seam"]["value"] == 0
    assert r["hard"] and not any(x.startswith("seam@") for x in r["limits"])


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
    # the open seam is a hard failure: each direction stops at its first step
    assert [t["angle"] for t in tr] == [10, -10]
    assert set(tr[0]["values"]) == set(budget.GATE_IDS.values())
    assert tr[0]["values"]["G4_SEAM_GAP"] > 0 and not tr[0]["hard"]
    held = _scan(["elbow_l"], step=10.0, max_angle=30.0, mode="joint", keep_trace=True)
    assert [t["angle"] for t in held["meta"]["joints"]["elbow_l"]["trace"]] == [10, 20, 30, -10, -20, -30]


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


def test_shard_in_the_forearm_is_an_art_request_not_a_limit():
    """A forearm image that carries torso paint (synth_art shard): the
    elbow is not limited by G6b; budget.json lists one art request for the
    forearm layer, with the angle the shard first shows."""
    b = _scan(["elbow_l"], step=10.0, max_angle=40.0, mode="rigid", shard=True)
    for d in ("+", "-"):
        e = b["joints"]["elbow_l"][d]
        assert "G6B_GHOST_PAINT" not in e["first_fail"], d
    (it,) = b["artreq"]
    assert it["id"] == "clean_forearm_l__torso" and it["kind"] == "clean" and it["layer"] == "forearm_l"
    assert it["status"] == "open" and it["max_visible_px"] > 0
    assert set(it["seen"]) == {"elbow_l +", "elbow_l -"}
    assert it["visible_from"]["elbow_l +"] in (10.0, 20.0) and it["visible_from"]["elbow_l -"] in (-10.0, -20.0)
    x0, y0, x1, y1 = it["region"]
    assert 0 <= x0 < x1 and 0 <= y0 < y1
    # the same arm with the copy in the torso instead is a G6b limit, no request
    g = _scan(["elbow_l"], step=10.0, max_angle=40.0, mode="rigid", ghost=True)
    assert g["joints"]["elbow_l"]["+"]["limit_by"] == "ghost@torso|forearm_l" and g["artreq"] == []


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
    flat-image cut): moving the shoulder reveals thigh paint that correlates
    with what the hand showed there. G6b sees that the HAND holds the copy
    (it looks like the thigh around it, not like the hand), so the joint
    passes and the pair is reported as carried, visible where the hand
    takes it off the thigh. The rest pose passes."""
    js, atlas = third_party("monkey")
    ir, images = raster.load_rig(js), raster.load_images(atlas)
    p = budget.Probe(ir, images, {"name": "shoulder_r", "bone": "armR", "dirs": [1], "band": {"lower": "armR"}})
    assert p.judge(0)["soft"]
    r = p.judge(10)
    g = r["gates"]["ghost_paint"]
    assert g["pass"]
    c = {(it["slot"], it["occluder"]): it for it in g["carried"]}
    it = c[("right_leg_0_thigh", "right_arm_2_hand")]
    assert it["ncc"] > 0.3 and it["visible_px"] >= 64
    assert r["hard"]  # no fold, no hole
