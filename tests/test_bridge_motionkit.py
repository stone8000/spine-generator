"""spinegen rig -> Spine 4.3 -> motion kit -> QA on 4.3.13 (spinegen.bridge_motionkit).

WHY: the bridge adds arm IK the rig did not have, softens arm and leg IK
by pushing their targets out, and hands the kit roles read from the
template, so the tests pin what must hold whatever the character: the IK
keeps its full softness and still leaves the setup pose where it was drawn
(planted feet included), runs after the rig's other IK and before the
half-angle helpers, bends the way the drawn elbow bends, and the roles the
kit gets are the template's bones.
Synthetic rigs only (spinegen.synth_art); the end-to-end test needs Node,
motionkit/ and qa43/ installed and skips otherwise.
"""
import json
import math
import os
import shutil

import pytest

from spinegen import atlas as atlasmod
from spinegen import bridge_motionkit as bm
from spinegen import ir as irmod
from spinegen import lbs, oracle, synth_art

ROLES = {"hip": "hips", "torso": "torso", "head": "head", "upperarm_l": "arm_l.shoulder",
         "forearm_l": "arm_l.elbow", "upperarm_r": "arm_r.shoulder", "forearm_r": "arm_r.elbow",
         "thigh_l": "leg_l.hip", "calf_l": "leg_l.knee", "thigh_r": "leg_r.hip", "calf_r": "leg_r.knee"}


def biped(bend_l=0.0, bend_r=0.0, accessory=False):
    """synth_art's two-armed, two-legged biped with template roles; the
    elbows optionally drawn bent (the forearm's local setup rotation)."""
    ir, images, joints = synth_art.build(mode="joint", arms=2, legs=2)
    for b in ir["bones"]:
        b["role"] = ROLES.get(b["name"], b.get("role"))
        if b["name"] == "forearm_l":
            b["rotation"] = bend_l
        if b["name"] == "forearm_r":
            b["rotation"] = bend_r
    for b in ir["bones"]:  # the helpers sit at the lower limb's local setup rotation
        if b["name"].startswith("elbow_mid_"):
            b["rotation"] = bend_l if b["name"].endswith("_l") else bend_r
    if accessory:
        ir["bones"] += [irmod.new_bone("tassel1", "head", 0, 150, 90, 40, role="accessory"),
                        irmod.new_bone("tassel2", "tassel1", 40, 0, 0, 30, role="accessory"),
                        irmod.new_bone("badge", "torso", 100, 30, 0, 20, role="accessory")]
        ir["physics"].append({"name": "badge_phys", "order": 99, "runs": True, "skin_required": False,
                              "bone": "badge", "x": 0, "y": 0, "rotate": 1, "scale_x": 0, "shear_x": 0,
                              "limit": 5000, "fps": 60, "inertia": 0.5, "strength": 100, "damping": 0.85, "mass": 1,
                              "wind": 0, "gravity": 0, "mix": 1, "global": {}, "spring": None})
    return ir, images, joints


def with_legs(ir, foot_slot=False, reader=False, drop=0.0):
    """Leg IK onto targets under the root at the calf tips (`drop` px lower: a
    leg that cannot quite reach), each with a planted foot bone under it."""
    bind = lbs.bind_pose(ir)
    for side in ("l", "r"):
        calf = next(b for b in ir["bones"] if b["name"] == f"calf_{side}")
        x, y = lbs.to_world(bind[calf["name"]], calf["length"], 0)
        ir["bones"].append(irmod.new_bone(f"ik_foot_{side}", "root", x, y - drop, role="ik_target"))
        ir["bones"].append(irmod.new_bone(f"foot_{side}", f"ik_foot_{side}", 0, 0, 0, 60, role=f"leg_{side}.ankle"))
        ir["ik"].append({"name": f"leg_ik_{side}", "order": 0, "runs": True, "skin_required": False,
                         "bones": [f"thigh_{side}", f"calf_{side}"], "target": f"ik_foot_{side}", "mix": 1.0,
                         "softness": 1.0, "bend_positive": side == "l", "compress": False, "stretch": False,
                         "uniform": False})
    if foot_slot:  # art on the target itself: moving the target would move it
        ir["slots"].append(dict(next(s for s in ir["slots"] if s["name"] == "calf_l"), name="boot_l", bone="ik_foot_l"))
    if reader:
        ir["physics"].append({"name": "foot_phys", "order": 99, "runs": True, "skin_required": False,
                              "bone": "ik_foot_r", "x": 1, "y": 1, "rotate": 0, "scale_x": 0, "shear_x": 0,
                              "limit": 5000, "fps": 60, "inertia": 0.5, "strength": 100, "damping": 0.85, "mass": 1,
                              "wind": 0, "gravity": 0, "mix": 1, "global": {}, "spring": None})
    for i, (_, c) in enumerate(irmod.constraints(ir)):
        c["order"] = i
    return ir


def solved_world(ir):
    ev = lbs.Evaluator(ir)
    return ev.world_dict(ev.world())


@pytest.mark.parametrize("x, reach, s", [(0.0, 100.0, 10.0), (95.0, 100.0, 10.0), (99.9, 100.0, 15.0),
                                         (100.0, 100.0, 15.0), (91.0, 100.0, 15.0), (50.0, 100.0, 0.0)])
def test_soft_target_inverts_the_runtime_ease(x, reach, s):
    td = bm.soft_target(x, reach, s)
    assert td >= x and td <= reach + s + 1e-9
    assert abs(bm.soft_reached(td, reach, s) - x) < 1e-6
    if x <= reach - s:  # outside the ease the target is already exact
        assert td == x


def test_arm_ik_keeps_full_softness_and_the_setup():
    """softness = 0.1 x arm length, not capped by the drawn slack: the target is
    pushed past the wrist so the softened solve lands on the drawn wrist."""
    for bend in (0.0, 12.0):  # a straight arm sits at the very end of the ease
        ir, _, _ = biped(bend_l=bend, bend_r=-bend)
        out, rep = bm.add_arm_ik(ir)
        assert set(rep["arms"]) == {"l", "r"}
        assert rep["setup_error_px"] <= bm.SETUP_TOL
        bind = lbs.bind_pose(out)
        w = solved_world(out)
        hard, _ = bm.add_arm_ik(ir, 0.0)  # the same arm IK without softness, target on the wrist
        wh = solved_world(hard)
        for side, arm in rep["arms"].items():
            fore = next(b for b in out["bones"] if b["name"] == f"forearm_{side}")
            drawn = lbs.to_world(bind[fore["name"]], fore["length"], 0)
            solved = lbs.to_world(w[fore["name"]], fore["length"], 0)
            # the softened solve lands where the hard one does; both sit within the solver's own
            # rounding of the drawn wrist (acos near a straight arm: ~4e-5 px even without softness)
            assert math.dist(solved, lbs.to_world(wh[fore["name"]], fore["length"], 0)) < 1e-6
            assert math.dist(drawn, solved) < 1e-4
            assert math.dist(drawn, arm["wrist_xy"]) < 1e-9
            c = next(x for x in out["ik"] if x["name"] == f"arm_ik_{side}")
            assert c["softness"] == pytest.approx(0.1 * arm["arm_length"]) and c["softness"] > 0
            t = bind[arm["target"]][4:6]
            sh = bind[f"upperarm_{side}"][4:6]
            assert math.dist(t, drawn) == pytest.approx(arm["push_px"], abs=1e-3) and arm["push_px"] > 1
            assert math.dist(sh, t) > math.dist(sh, drawn)  # pushed out along the chain
            assert next(b for b in out["bones"] if b["name"] == arm["target"])["parent"] == "root"
        assert irmod.validate(out) == []


def test_arm_ik_softness_with_mirrored_upper_arms():
    """The runtime solves with |scaleX| and scales softness by it: a mirrored
    upper arm (scaleX -1, as a flipped build has) keeps the setup too."""
    ir, _, _ = biped(bend_l=15.0, bend_r=-15.0)
    for b in ir["bones"]:
        if b["name"].startswith("upperarm_"):
            b["scale_x"] = -1.0
            b["rotation"] = 0.0
    out, rep = bm.add_arm_ik(ir)
    assert rep["setup_error_px"] <= bm.SETUP_TOL and all(a["softness"] > 0 for a in rep["arms"].values())


def test_legs_soften_and_planted_feet_stay():
    ir = with_legs(biped(bend_l=10.0, bend_r=-10.0)[0], drop=0.5)
    out, rep = bm.soften_legs(ir, 0.15)
    assert sorted(rep["legs"]) == ["leg_ik_l", "leg_ik_r"] and rep["moved_targets"] == ["ik_foot_l", "ik_foot_r"]
    assert rep["setup_error_px"] <= bm.SETUP_TOL  # every bone but the two targets, feet included
    wa, wb = solved_world(ir), solved_world(out)
    for side in ("l", "r"):
        c = next(x for x in out["ik"] if x["name"] == f"leg_ik_{side}")
        g = rep["legs"][c["name"]]
        assert c["softness"] == pytest.approx(0.15 * g["reach"]) and g["softness_before"] == 1.0
        assert math.dist(wa[f"foot_{side}"][4:6], wb[f"foot_{side}"][4:6]) < 1e-6
        assert math.dist(wa[f"ik_foot_{side}"][4:6], wb[f"ik_foot_{side}"][4:6]) == pytest.approx(g["push_px"], abs=1e-3)
        # the leg could not quite reach at setup; it still stops where the rig's own solve stopped
        assert math.dist(wa[f"calf_{side}"][4:6], wb[f"calf_{side}"][4:6]) < 1e-6
    assert irmod.validate(out) == []
    again, rep2 = bm.soften_legs(out, 0.1)  # less than the IK has: left alone
    assert rep2["legs"] == {} and rep2["moved_targets"] == []


def test_legs_with_art_or_readers_on_the_target_are_left_alone():
    ir = with_legs(biped()[0], foot_slot=True, reader=True)
    out, rep = bm.soften_legs(ir, 0.15)
    assert rep["legs"] == {}
    assert any("boot_l" in n for n in rep["notes"]) and any("foot_phys" in n for n in rep["notes"])
    assert [c["softness"] for c in out["ik"]] == [1.0, 1.0]
    assert bm.setup_error(ir, out) == 0.0


def test_arm_ik_runs_after_ik_and_before_the_helpers():
    ir, _, _ = biped()
    ir["bones"].append(irmod.new_bone("ik_foot_l", "root", -70, 20))
    ir["ik"].append({"name": "leg_ik_l", "order": 0, "runs": True, "skin_required": False,
                     "bones": ["thigh_l", "calf_l"], "target": "ik_foot_l", "mix": 1.0, "softness": 0.0,
                     "bend_positive": True, "compress": False, "stretch": False, "uniform": False})
    for i, c in enumerate(ir["transform"]):
        c["order"] = i + 1
    out, _ = bm.add_arm_ik(ir)
    order = [c["name"] for _, c in irmod.constraints(out)]
    assert order[:3] == ["leg_ik_l", "arm_ik_l", "arm_ik_r"]
    assert order[3:] == ["elbow_half_l", "elbow_half_r", "knee_half_l", "knee_half_r"]
    assert sorted(c["order"] for _, c in irmod.constraints(out)) == list(range(len(order)))


@pytest.mark.parametrize("bend", [25.0, -25.0])
def test_arm_ik_bends_the_way_the_elbow_is_drawn(bend):
    ir, _, _ = biped(bend_l=bend, bend_r=-bend)
    out, rep = bm.add_arm_ik(ir)
    assert rep["arms"]["l"]["bend_positive"] is (bend > 0)
    assert rep["arms"]["r"]["bend_positive"] is (bend < 0)
    assert rep["setup_error_px"] <= bm.SETUP_TOL  # the other bend would flip the drawn elbow
    flipped = irmod.clone(out)
    for c in flipped["ik"]:
        c["bend_positive"] = not c["bend_positive"]
    assert bm.setup_error(ir, flipped) > 10


def test_roles_come_from_the_template():
    ir, _, _ = biped(accessory=True)
    out, arms = bm.add_arm_ik(ir)
    roles, notes = bm.roles_from_template(out, arms, facing=1)
    assert (roles["hip"], roles["body"], roles["chest"], roles["head"]) == ("hip", "hip", "torso", "head")
    xs = {a["target"]: a["target_xy"][0] for a in arms["arms"].values()}
    assert roles["fists"] == sorted(xs, key=lambda f: -xs[f]) and roles["fist"] == roles["fistFront"] == roles["fists"][0]
    assert roles["fistBack"] == roles["fists"][1]
    assert set(roles["shoulders"]) == {"upperarm_l", "upperarm_r"} and roles["shoulderBack"] == "upperarm_l"
    assert roles["hair"] == ["tassel1", "tassel2"]  # an accessory chain under the head
    assert "chain" not in roles and any("badge" in n for n in notes)  # runtime physics is left alone
    left, _ = bm.roles_from_template(out, arms, facing=-1)
    assert left["fist"] == roles["fistBack"] and left["shoulderBack"] == "upperarm_r"
    recipe = bm.make_recipe(roles, bm.DEFAULT_PRESETS, 1.2)
    assert [a["name"] for a in recipe["animations"]] == ["idle", "punch", "celebrate", "hit"]
    assert recipe["animations"][0]["params"] == {"hair": "keys"} and recipe["size"] == 1.2


def _ready():
    for rt in ("4.2", "4.3"):
        ok, why = oracle.available(rt)
        if not ok:
            pytest.skip(why)
    if not shutil.which("npm") and not os.path.isdir(os.path.join(bm.KIT, "node_modules")):
        pytest.skip("motionkit/ not installed")


def test_bridge_end_to_end(tmp_path):
    """Synthetic biped through the whole bridge: a 4.3 file the kit animates,
    its smoothness check, and qa's gates on spine-core 4.3.13."""
    _ready()
    ir, images, _ = biped(bend_l=20.0, bend_r=-20.0)
    rig = tmp_path / "in" / "rig.json"
    irmod.save(ir, str(rig))
    atlas, _ = atlasmod.write(str(tmp_path / "in" / "biped.atlas"), images)
    rep = bm.run(str(rig), atlas, str(tmp_path / "out"), name="biped", presets=("punch", "hit-react"))
    assert os.path.exists(rep["files"]["motion"])
    with open(rep["files"]["motion"]) as f:
        motion = json.load(f)
    assert motion["skeleton"]["spine"].startswith("4.3.")
    assert set(motion["animations"]) == {"punch", "hit"}
    iks = [c for c in motion["constraints"] if c["type"] == "ik"]
    assert [c["name"] for c in iks] == ["arm_ik_l", "arm_ik_r"]
    assert all(c["softness"] > 10 for c in iks)  # 0.1 x 420 px arms, not a sliver under the slack
    # the pushed targets leave the drawn pose where it was in the 4.3.13 runtime itself
    assert rep["setup_vertices_43_px"] <= bm.SETUP_TOL
    assert {s["name"] for s in rep["smoothness"]} == {"punch", "hit"}
    assert all(s["errors"] == 0 for s in rep["smoothness"])
    gates = rep["qa"]["gates"]
    assert rep["qa"]["runtime"] == "@esotericsoftware/spine-core@4.3.13"
    for gid in ("L1_VERSION", "L2_COUNTS", "L3_ORDER", "L4_CURVES_NAN", "L5_IK_VALID", "L7_ATLAS", "L8_MESH",
                "L9_TC_MIX", "G3_FLIP", "P3_DETERMINISM"):
        assert gates[gid]["pass"], (gid, gates[gid])
    assert gates["C1_ORACLE"]["skipped"]
    assert os.path.exists(tmp_path / "out" / "bridge.json")
