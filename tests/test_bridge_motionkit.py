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
    # the idle opens on the drawn pose (the kit's one-shot <name>_in); one-shots and celebrate get none
    assert [a.get("intro") for a in recipe["animations"]] == [True, None, None, None]
    assert not any("intro" in a for a in bm.make_recipe(roles, bm.DEFAULT_PRESETS, 1.2, intro=False)["animations"])
    assert "crouch" not in roles  # no leg IK


def test_crouch_role_only_over_planted_feet():
    ir, _, _ = biped()
    legged = with_legs(ir)
    out, arms = bm.add_arm_ik(legged)
    roles, _ = bm.roles_from_template(out, arms)
    assert roles["crouch"] == roles["hip"] and bm.planted_feet(out, roles["hip"]) == ["ik_foot_l", "ik_foot_r"]
    carried = irmod.clone(out)
    for b in carried["bones"]:
        if b["name"].startswith("ik_foot_"):
            b["parent"] = roles["hip"]  # targets the hip carries: the feet move with the body, nothing to split
    assert "crouch" not in bm.roles_from_template(carried, arms)[0]


def test_qa_plants_the_feet_except_in_flight(tmp_path):
    ir, _, _ = biped()
    legged = with_legs(ir)
    legged["foot_plant"] = [{"bone": "foot_l", "anim": "x", "spans": [[0, 1]]}]
    motion = {"animations": {
        "idle": {"bones": {"hip": {"translatey": [{"time": 0, "value": 0}, {"time": 2.0, "value": 0}]}}},
        "jump": {"bones": {"root": {"translatey": [{"time": 0, "value": 0}, {"time": 1.2, "value": 0}]}},
                 "events": [{"name": "takeoff", "time": 0.18}, {"name": "land", "time": 0.8}]}}}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(motion))
    plants = bm.qa_ir(legged, str(path))["foot_plant"]
    assert {"bone": "foot_l", "anim": "idle"} in plants
    assert {"bone": "foot_l", "anim": "jump", "spans": [[0.0, 0.18], [0.8, 1.2]]} in plants


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


# ---------------------------------------------------------------- IK guard per chain, budget fit

def test_guard_chains_leave_the_drawn_arm_alone():
    """The per-chain guard's knee is the setup distance when the arm is drawn past 85 %
    (straight arms, targets pushed out for softness), so the drawn pose maps to itself;
    its ceiling stays short of where the softened solve goes straight."""
    for bend in (0.0, 12.0, 90.0):
        ir, _, _ = biped(bend_l=bend, bend_r=-bend)
        out, rep = bm.add_arm_ik(ir)
        g = bm.guard_chains(out)
        assert set(g) == {"ik_hand_l", "ik_hand_r"}
        for t, c in g.items():
            assert c["knee"] >= c["setup"] - 1e-9 and c["knee"] >= bm.GUARD_KNEE * c["reach"] - 1e-9
            assert c["knee"] < c["max"] <= max(c["straight_at"], c["knee"] + bm.GUARD_MIN_ROOM * c["reach"]) + 1e-9
            side = t[-1]
            assert c["straight_at"] == pytest.approx(rep["arms"][side]["arm_length"] + rep["arms"][side]["soft_eff"], abs=1e-3)
            if bend == 90.0:  # a bent arm sits well inside 85 %: the kit's own knee
                assert c["knee"] == pytest.approx(bm.GUARD_KNEE * c["reach"], abs=1e-3)
            else:
                assert c["knee"] == pytest.approx(c["setup"], abs=1e-3)


def _fake_joint(name, bone, upper, soft, hard):
    return {"name": name, "bone": bone, "upper": upper, "limits": {"+": (soft, hard), "-": (soft, hard)},
            "by": {"+": ("fold@x", "fold@x"), "-": ("seam@a|b", "seam@a|b")}}


def test_budget_judge_holds_transits_and_loops():
    from spinegen import budget_motion as bmo
    j = _fake_joint("elbow_l", "forearm_l", "upperarm_l", 30, 60)
    fps = 60
    hold = int(bmo.HOLD_SECONDS * fps)
    # passes through 50 deg for a frame: a transit, inside hard
    r = bmo.judge([0, 20, 50, 20, 0], j, fps)
    assert r["ok"] and r["+"]["peak"] == 50 and not r["+"]["hold"]
    # past soft for HOLD_SECONDS: a hold
    r = bmo.judge([0] + [40] * hold + [0], j, fps)
    assert not r["ok"] and r["+"]["hold"] and r["worst_dir"] == "+" and "held past theta_soft" in r["why"]
    # one frame past hard: a transit violation, whichever direction
    r = bmo.judge([0, -61, 0], j, fps)
    assert not r["ok"] and r["-"]["transit"] and r["worst_dir"] == "-"
    # a loop's last and first frames join up into one hold
    half = [40] * (hold // 2 + 1)
    seq = half + [0] * 10 + half
    assert bmo.judge(seq, j, fps)["ok"]
    assert not bmo.judge(seq, j, fps, loop=True)["ok"]


def test_joint_groups_follow_the_arm_ik():
    ir = with_legs(biped()[0])
    out, _ = bm.add_arm_ik(ir)
    roles = {"hip": "hip", "body": "hip", "chest": "torso"}
    joints = [{"name": n, "bone": b} for n, b in (("shoulder_l", "upperarm_l"), ("elbow_l", "forearm_l"),
                                                   ("elbow_r", "forearm_r"), ("knee_l", "calf_l"), ("ankle_l", "foot_l"))]
    g = bm.joint_groups(out, roles, joints)
    assert g["arm_l"] == {"bones": ["ik_hand_l"], "joints": ["shoulder_l", "elbow_l"]}
    assert g["arm_r"] == {"bones": ["ik_hand_r"], "joints": ["elbow_r"]}
    assert g["body"] == {"bones": ["hip", "torso"], "joints": ["knee_l", "ankle_l"]}


def test_art_requests_name_the_cause():
    groups = {"arm_l": {"bones": ["ik_hand_l"], "joints": ["elbow_l"]}, "body": {"bones": ["hip"], "joints": []}}
    recipe = {"animations": [{"name": "celebrate", "preset": "celebrate"}, {"name": "idle", "preset": "fighter-idle-bounce"}]}
    clamp = {"group": "arm_l", "factor": 0.4, "why": "transit", "before": [0, 171], "after": [0, 85],
             "soft": [90, 96], "hard": [98, 114]}
    fit = {"celebrate": {"factors": {"arm_l": 0.4, "body": 1.0}, "clamped": {"elbow_l": dict(clamp, hard_by="fold@arm")}},
           "idle": {"factors": {"arm_l": 0.1, "body": 1.0}, "clamped": {"elbow_l": dict(clamp, hard_by="seam@a|b")}}}
    items = {i["anim"]: i for i in bm.art_requests(fit, recipe, groups, {})}
    assert items["celebrate"]["kind"] == "attachment" and "raised" in items["celebrate"]["what"]
    assert items["idle"]["kind"] == "joint_art" and "seam" in items["idle"]["what"]
    fit["celebrate"]["factors"]["arm_l"] = 0.9  # carried mostly: no request
    assert [i["anim"] for i in bm.art_requests(fit, recipe, groups, {})] == ["idle"]


def test_bridge_fits_the_budget_and_keeps_frame_zero(tmp_path):
    """End to end with a budget that cannot carry the kit's celebrate: the fit scales the
    arms down until no joint leaves it and 4.3.13 counts no fold, writes the art request,
    and the per-chain guard leaves the drawn pose alone at rest (the kit's own does not)."""
    _ready()
    ir, images, joints = biped(bend_l=3.0, bend_r=-3.0)  # arms drawn almost straight
    rig = tmp_path / "in" / "rig.json"
    irmod.save(ir, str(rig))
    atlas, _ = atlasmod.write(str(tmp_path / "in" / "biped.atlas"), images)
    lim = {"soft": 40.0, "hard": 60.0, "limit_by": "fold@x", "hard_by": "fold@x"}
    budget = {"schema": "spinegen-budget/1", "joints": {j["name"]: {"+": lim, "-": lim} for j in joints}}
    with open(tmp_path / "in" / "budget.json", "w") as f:
        json.dump(budget, f)
    with open(tmp_path / "in" / "joints.json", "w") as f:
        json.dump({"joints": joints}, f)
    rep = bm.run(str(rig), atlas, str(tmp_path / "out"), name="biped", presets=("celebrate",), qa=False)
    fit = rep["budget"]["fit"]["celebrate"]
    assert fit["before"]["ok"] is False and fit["after"]["ok"] is True
    assert {"elbow_l", "elbow_r"} <= set(fit["clamped"])
    assert all(fit["factors"][g] < 1 for g in ("arm_l", "arm_r"))
    for j in ("elbow_l", "elbow_r"):
        lo, hi = fit["after"]["joints"][j]["max_minus"], fit["after"]["joints"][j]["max_plus"]
        assert -60 <= lo and hi <= 60
    with open(rep["files"]["recipe"]) as f:
        recipe = json.load(f)
    assert set(recipe["animations"][0]["boneAmplitude"]) == {"ik_hand_l", "ik_hand_r"}
    assert rep["ik_guard"]["mode"] == "per chain" and rep["ik_guard"]["rest_px"] < 0.5
    assert os.path.exists(tmp_path / "out" / "artreq.json")
    # an idle with the kit's opening: idle_in starts on the drawn pose and ends on idle's frame 0
    idle = bm.run(str(rig), atlas, str(tmp_path / "idle"), name="biped", presets=("fighter-idle-bounce",), qa=False)
    it = idle["intro"]["idle"]
    assert it["intro"] == "idle_in" and it["frame0_vs_setup_px"] < 0.5 and it["end_vs_loop_frame0_px"] < 0.5
    assert {s["name"] for s in idle["smoothness"]} == {"idle_in", "idle"}
    kitg = bm.run(str(rig), atlas, str(tmp_path / "kit"), name="biped", presets=("celebrate",), qa=False,
                  kit_guard=True, budget=None)
    assert kitg["ik_guard"]["rest_px"] > 1  # the kit's 85-95 % guard bends a straight arm at rest
