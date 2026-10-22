"""spinegen rig -> Spine 4.3 -> motion kit -> QA on 4.3.13 (spinegen.bridge_motionkit).

WHY: the bridge adds arm IK the rig did not have and hands the kit roles
read from the template, so the tests pin what must hold whatever the
character: the arm IK leaves the setup pose where it was drawn, runs after
the rig's other IK and before the half-angle helpers, bends the way the
drawn elbow bends, and the roles the kit gets are the template's bones.
Synthetic rigs only (spinegen.synth_art); the end-to-end test needs Node,
motionkit/ and qa43/ installed and skips otherwise.
"""
import json
import os
import shutil

import pytest

from spinegen import atlas as atlasmod
from spinegen import bridge_motionkit as bm
from spinegen import ir as irmod
from spinegen import lbs, oracle, synth_art

ROLES = {"hip": "hips", "torso": "torso", "head": "head", "upperarm_l": "arm_l.shoulder",
         "forearm_l": "arm_l.elbow", "upperarm_r": "arm_r.shoulder", "forearm_r": "arm_r.elbow"}


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


def test_arm_ik_keeps_the_setup_and_reaches_the_wrists():
    ir, _, _ = biped()
    out, rep = bm.add_arm_ik(ir)
    assert set(rep["arms"]) == {"l", "r"}
    assert rep["setup_error_px"] <= bm.SETUP_TOL
    bind = lbs.bind_pose(out)
    for side, arm in rep["arms"].items():
        fore = next(b for b in out["bones"] if b["name"] == f"forearm_{side}")
        tip = lbs.to_world(bind[fore["name"]], fore["length"], 0)
        t = bind[arm["target"]]
        assert abs(t[4] - tip[0]) < 1e-6 and abs(t[5] - tip[1]) < 1e-6
        assert next(b for b in out["bones"] if b["name"] == arm["target"])["parent"] == "root"
        assert arm["softness"] <= arm["slack"] + 1e-9  # capped: the setup pose does not move
    assert irmod.validate(out) == []


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
    assert [c["name"] for c in motion["constraints"] if c["type"] == "ik"] == ["arm_ik_l", "arm_ik_r"]
    assert {s["name"] for s in rep["smoothness"]} == {"punch", "hit"}
    assert all(s["errors"] == 0 for s in rep["smoothness"])
    gates = rep["qa"]["gates"]
    assert rep["qa"]["runtime"] == "@esotericsoftware/spine-core@4.3.13"
    for gid in ("L1_VERSION", "L2_COUNTS", "L3_ORDER", "L4_CURVES_NAN", "L5_IK_VALID", "L7_ATLAS", "L8_MESH",
                "L9_TC_MIX", "G3_FLIP", "P3_DETERMINISM"):
        assert gates[gid]["pass"], (gid, gates[gid])
    assert gates["C1_ORACLE"]["skipped"]
    assert os.path.exists(tmp_path / "out" / "bridge.json")
