"""spinegen.rigbuild: layers + joints + template -> an IR rig that bends.

On a SYNTHETIC two-arm, two-leg character (spinegen.synth_art) that goes
the whole way a real one does: IR -> Spine 4.2 JSON + atlas -> ingest ->
layers dir -> rigbuild. The source carries two animations of its own (a
wave and a jump) so the motion carry-over is checked against the source's
world poses. The official runtime judges where it is installed; the monkey
(third-party, read in place) only when it is on this machine.
"""
import json
import math
import os

import numpy as np
import pytest

from conftest import third_party

from spinegen import atlas, ingest_spine, lbs, playback, raster, rigbuild, synth_art
from spinegen import ir as irmod
from spinegen.emit import spine42


# ---------------------------------------------------------------- fixtures

JUMP = 40.0


FOOT_W, FOOT_H, ANKLE_UP = 110, 100, 70  # foot image size; ankle height above the sole


def _add_feet(ir, images):
    """synth_art legs end in a calf; give each a foot: a bone at the calf's
    tip (the ankle) and an opaque painted box reaching ANKLE_UP px below the
    ankle and pointing outward, drawn behind the calf."""
    bones = irmod.by_name(ir["bones"])
    ev = lbs.Evaluator(ir)
    w = ev.world_dict(ev.world())
    slots = ir["slots"]
    for side, sg in (("l", -1), ("r", 1)):
        calf = bones[f"calf_{side}"]
        a, _, c, _, x, y = w[f"calf_{side}"]
        ax, ay = x + a * calf["length"], y + c * calf["length"]
        ir["bones"].append(irmod.new_bone(f"foot_{side}", f"calf_{side}", calf["length"], 0.0, 90.0, 40.0))
        img = np.zeros((FOOT_H, FOOT_W, 4), np.uint8)
        img[2:-2, 2:-2] = (110, 70, 40, 255)
        img[2:-2:12, 2:-2] = (60, 35, 20, 255)
        name = f"foot_{side}"
        images[name] = img
        cx = ax + sg * (FOOT_W / 2 - 35)       # toes outward
        cy = ay - ANKLE_UP + FOOT_H / 2        # sole ANKLE_UP below the ankle
        att = {"type": "region", "name": name, "path": name, "x": cx - ax, "y": cy - ay, "rotation": 0.0,
               "scale_x": 1.0, "scale_y": 1.0, "width": float(FOOT_W), "height": float(FOOT_H),
               "color": "ffffffff", "sequence": None}
        ir["skins"][0]["attachments"][name] = {name: att}
        i = next(k for k, s in enumerate(slots) if s["name"] == f"calf_{side}")
        slots.insert(i, irmod.new_slot(name, name, name))
    synth_art._fit_skeleton_box(ir)


def _source():
    """The synthetic source rig with feet, a wave (upper arm + hip sway)
    and a jump (hips up JUMP px: the source's feet leave the ground)."""
    ir, images, _ = synth_art.build(mode="rigid", arms=2, legs=2)
    _add_feet(ir, images)
    lin = None
    ir["animations"] = [
        {"name": "wave", "timelines": [
            {"type": "bone", "target": "upperarm_l", "property": "rotate",
             "keys": [{"time": 0.0, "value": 0.0, "curve": lin}, {"time": 0.5, "value": -40.0, "curve": lin},
                      {"time": 1.0, "value": 0.0, "curve": lin}]},
            {"type": "bone", "target": "hip", "property": "translate",
             "keys": [{"time": 0.0, "x": 0.0, "y": 0.0, "curve": lin}, {"time": 0.5, "x": 6.0, "y": -10.0, "curve": lin},
                      {"time": 1.0, "x": 0.0, "y": 0.0, "curve": lin}]}]},
        {"name": "jump", "timelines": [
            {"type": "bone", "target": "hip", "property": "translate",
             "keys": [{"time": 0.0, "x": 0.0, "y": 0.0, "curve": lin}, {"time": 0.2, "x": 0.0, "y": 0.0, "curve": lin},
                      {"time": 0.5, "x": 0.0, "y": JUMP, "curve": lin}, {"time": 0.8, "x": 0.0, "y": 0.0, "curve": lin},
                      {"time": 1.0, "x": 0.0, "y": 0.0, "curve": lin}]}]},
    ]
    assert not irmod.validate(ir)
    return ir, images


@pytest.fixture(scope="module")
def layers_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("rigbuild")
    ir, images = _source()
    spine42.write(ir, str(d / "synth.json"))
    at, _ = atlas.write(str(d / "synth.atlas"), images)
    ingest_spine.unpack(str(d / "synth.json"), at, str(d / "layers"))
    return str(d / "layers")


@pytest.fixture(scope="module")
def built(layers_dir):
    return {m: rigbuild.build(layers_dir, mode=m) for m in rigbuild.MODES}


def _world(ir, pose=None, state=None):
    ev = lbs.Evaluator(ir)
    return ev.world_dict(ev.world(pose, state))


def _rot(m):
    return math.degrees(math.atan2(m[2], m[0]))


# ---------------------------------------------------------------- skeleton and constraints

def test_skeleton_constraints_and_order(built):
    ir = built["joint"]["ir"]
    assert not irmod.validate(ir)
    bones = irmod.by_name(ir["bones"])
    for n in ("root", "hips", "torso", "head", "upperarm_l", "forearm_r", "elbow_mid_l", "thigh_r", "shin_l",
              "knee_mid_r", "ik_foot_l", "ik_foot_r"):
        assert n in bones, n
    assert "hand_l" not in bones  # the source has no hand: the wrist is only the forearm's tip
    assert bones["ik_foot_l"]["parent"] == "root" and bones["foot_l"]["parent"] == "ik_foot_l"
    # helpers: under the upper limb with the lower limb's local setup transform
    for h, lower in (("elbow_mid_l", "forearm_l"), ("knee_mid_r", "shin_r")):
        assert bones[h]["parent"] == bones[lower]["parent"]
        for k in ("x", "y", "rotation"):
            assert bones[h][k] == pytest.approx(bones[lower][k], abs=1e-9)
    # half_angle helpers ordered after every IK
    ik_orders = [c["order"] for c in ir["ik"]]
    assert {c["kind"] for c in ir["transform"]} == {"half_angle"}
    assert all(c["mix"] == 0.5 for c in ir["transform"])
    assert min(c["order"] for c in ir["transform"]) > max(ik_orders)
    assert sorted(ik_orders + [c["order"] for c in ir["transform"]]) == list(range(len(ik_orders) + 4))
    for c in ir["ik"]:
        assert c["target"].startswith("ik_foot_") and c["mix"] == 1.0 and not c["stretch"]
    # the template's bend directions apply to a straight setup leg
    rep = built["joint"]["report"]["ik"]
    assert rep["leg_ik_l"]["bend_positive"] is True and rep["leg_ik_r"]["bend_positive"] is False
    assert rep["leg_ik_l"]["softness"] <= rep["leg_ik_l"]["setup_slack"] + 1e-9


def test_setup_pose_is_unchanged_by_constraints(built):
    """IK softness capped by the slack, helpers at the child's setup
    rotation: the constrained setup pose IS the bind pose."""
    ir = built["joint"]["ir"]
    bind = lbs.bind_pose(ir)
    w = _world(ir)
    for n, m in bind.items():
        assert np.allclose(w[n], m, atol=1e-6), n


def test_setup_render_matches_the_layers(built, layers_dir):
    doc, imgs = ingest_spine.load_layers(layers_dir)
    ref = ingest_spine.recomposite(doc, imgs).astype(int)
    for mode in rigbuild.MODES:
        res = built[mode]
        img = raster.render(res["ir"], res["images"], size=tuple(doc["canvas"]), origin=doc["world"]["origin"])
        diff = np.abs(img.astype(int) - ref).max(-1)
        # the CDT outline may leave out an anti-aliased edge pixel here and there (mesh.py)
        assert (diff > 24).mean() < 0.002, mode
        assert diff.mean() < 0.5, mode


# ---------------------------------------------------------------- meshes and weights

def test_weights_joint_vs_rigid(built):
    for mode in rigbuild.MODES:
        ir = built[mode]["ir"]
        for _, slot, _, att in irmod.iter_attachments(ir):
            assert att["type"] == "mesh" and att["hull"] > 2
            for row in att["weights"]:
                assert 1 <= len(row) <= 3 and sum(w for _, w in row) == pytest.approx(1.0, abs=1e-6)
                if mode == "rigid":
                    assert len(row) == 1
    tot = built["joint"]["report"]["totals"]
    assert tot["b2"] >= 0.9 and tot["b2_band_vertices"] > 100
    assert built["rigid"]["report"]["totals"]["b2"] is None
    bands = built["joint"]["report"]["bands"]
    assert set(bands) >= {"shoulder_l", "elbow_l", "hip_r", "knee_r"}
    assert set(bands["elbow_l"]["layers"]) == {"upper_arm_l", "forearm_l"}
    assert bands["shoulder_l"]["layers"] == ["upper_arm_l"]  # the torso does not bend with the arm
    assert bands["elbow_l"]["helper"] == "elbow_mid_l"


@pytest.mark.parametrize("angle", [90.0, -90.0])
def test_joint_elbow_holds_where_rigid_tears(built, angle):
    """At 90 deg the helper ramp has no fold (lbs; C1 ties it to the runtime)."""
    ir = built["joint"]["ir"]
    b = irmod.by_name(ir["bones"])["forearm_l"]
    ev = lbs.Evaluator(ir)
    f = ev.folds(pose={"forearm_l": {"rotation": b["rotation"] + angle}})
    assert f["folds"] == 0


# ---------------------------------------------------------------- feet

def test_crouch_keeps_the_soles_planted(built):
    ir = built["joint"]["ir"]
    c = rigbuild.crouch(ir, 0.05 * ir["H"])
    assert c["hips_drop"] == pytest.approx(0.05 * ir["H"])
    assert c["contact_vertices"] and c["max_slide"] <= 1e-6
    # the knees really bend, and nothing folds
    b = irmod.by_name(ir["bones"])
    w0, w1 = _world(ir), _world(ir, {"hips": {"y": b["hips"]["y"] - c["depth"]}})
    knee0 = _rot(w0["shin_l"]) - _rot(w0["thigh_l"])
    knee1 = _rot(w1["shin_l"]) - _rot(w1["thigh_l"])
    assert abs(knee1 - knee0) > 20
    assert lbs.Evaluator(ir).folds(pose={"hips": {"y": b["hips"]["y"] - c["depth"]}})["folds"] == 0
    # the rigid comparison rig has the same skeleton, so the same planted soles
    assert rigbuild.crouch(built["rigid"]["ir"], 0.05 * ir["H"])["max_slide"] <= 1e-6


# ---------------------------------------------------------------- motion

def test_motion_carry_over(built):
    src, _ = _source()
    ir = built["joint"]["ir"]
    assert [a["name"] for a in ir["animations"]] == ["wave", "jump"]
    for t in (0.25, 0.5):
        ps = playback.sample(src, "wave", t)
        pn = playback.sample(ir, "wave", t)
        ws, wn = _world(src, ps["bones"]), _world(ir, pn["bones"], pn["state"])
        assert _rot(wn["upperarm_l"]) == pytest.approx(_rot(ws["upperarm_l"]), abs=1e-6)
        assert wn["forearm_l"][4:] == pytest.approx(ws["forearm_l"][4:], abs=1e-6)
        assert wn["hips"][4:] == pytest.approx(ws["hip"][4:], abs=1e-6)
    # wave never lifts a foot: planted all the way, no target keys
    wave = next(a for a in ir["animations"] if a["name"] == "wave")
    assert not [tl for tl in wave["timelines"] if tl["target"].startswith("ik_foot")]
    plants = [p for p in ir["foot_plant"] if p["anim"] == "wave"]
    assert len(plants) == 2 and all(p["spans"] == [[0.0, 1.0]] for p in plants)


def test_jump_lifts_the_targets(built):
    """The source's feet leave the ground in the jump: the IK targets follow
    them up (keys), and foot_plant leaves the airborne part out."""
    src, _ = _source()
    ir = built["joint"]["ir"]
    jump = next(a for a in ir["animations"] if a["name"] == "jump")
    tl = [tl for tl in jump["timelines"] if tl["target"] == "ik_foot_l"]
    assert len(tl) == 1 and tl[0]["property"] == "translate"
    assert max(k["y"] for k in tl[0]["keys"]) == pytest.approx(JUMP, abs=0.3)
    p = playback.sample(ir, "jump", 0.5)
    w = _world(ir, p["bones"], p["state"])
    ws = _world(src, playback.sample(src, "jump", 0.5)["bones"])
    assert w["foot_l"][4:] == pytest.approx(ws["foot_l"][4:], abs=0.3)
    spans = next(p for p in ir["foot_plant"] if p["anim"] == "jump" and p["bone"] == "foot_l")["spans"]
    assert spans[0][0] == 0.0 and spans[0][1] <= 0.21 and spans[-1][1] == pytest.approx(1.0)
    assert all(not (s0 < 0.5 < s1) for s0, s1 in spans)


def test_translate_turned_into_the_new_parent_frame():
    """A child whose parent now points 90 deg away keeps its world motion."""
    tl = {"type": "bone", "target": "head", "property": "translate",
          "keys": [{"time": 0.0, "x": 10.0, "y": 2.0, "curve": [0.1, 10.0, 0.2, 5.0, 0.1, 2.0, 0.2, 1.0]}]}
    notes = []
    out = rigbuild._carry_bone_timeline(tl, "head", d_parent=-90.0, d_own=0.0, notes=notes)
    k = out["keys"][0]
    # new parent frame = old one turned +90: local (x, y) = R(-90) (10, 2) = (2, -10)
    assert (k["x"], k["y"]) == (2.0, -10.0)
    assert k["curve"] == [0.1, 2.0, 0.2, 1.0, 0.1, -10.0, 0.2, -5.0]
    one = rigbuild._carry_bone_timeline(dict(tl, property="translatey", keys=[{"time": 0, "value": 3.0, "curve": None}]),
                                        "head", 90.0, 0.0, notes)
    assert one["property"] == "translate" and (one["keys"][0]["x"], one["keys"][0]["y"]) == (-3.0, 0.0)
    sc = rigbuild._carry_bone_timeline({"type": "bone", "target": "t", "property": "scale",
                                        "keys": [{"time": 0, "x": 1.0, "y": 1.2, "curve": None}]}, "torso", 0.0, 94.0, notes)
    assert (sc["keys"][0]["x"], sc["keys"][0]["y"]) == (1.2, 1.0)
    sx = rigbuild._carry_bone_timeline({"type": "bone", "target": "t", "property": "scalex", "keys": []}, "t", 0, -88, notes)
    assert sx["property"] == "scaley"


# ---------------------------------------------------------------- files and the runtime

def test_cli_writes_a_rig_the_runtime_accepts(layers_dir, tmp_path):
    from spinegen import oracle
    out = tmp_path / "out"
    rigbuild.main([layers_dir, "--template", "biped_mascot", "--mode", "joint", "-o", str(out), "--name", "synth"])
    for f in ("rig.json", "motion.json", "synth.json", "synth.atlas", "synth.png", "joints.json", "build_report.json"):
        assert (out / f).exists(), f
    rep = json.load(open(out / "build_report.json"))
    assert rep["crouch"]["max_slide"] <= 1.0
    joints = json.load(open(out / "joints.json"))
    assert joints["schema"] == "spinegen-budget-joints/1"
    names = {j["name"] for j in joints["joints"]}
    assert {"elbow_l", "knee_r", "shoulder_r"} <= names
    # the atlas holds every layer image exactly
    imgs = raster.load_atlas_images(str(out / "synth.atlas"))
    doc, layer_imgs = ingest_spine.load_layers(layers_dir)
    assert all(np.array_equal(imgs[n], layer_imgs[n]) for n in layer_imgs)
    ok, why = oracle.available()
    if not ok:
        pytest.skip(f"runtime oracle unavailable: {why}")
    code, report = oracle.check(str(out / "synth.json"), str(out / "synth.atlas"), ir=str(out / "rig.json"))
    gates = {g["id"]: g for g in report["gates"]}
    for gid in ("L1_VERSION", "L2_COUNTS", "L3_ORDER", "L4_CURVES_NAN", "L5_IK_VALID", "L6_PHYSICS_VALID",
                "L7_ATLAS", "L8_MESH", "L8_MESH_INFLUENCES", "L9_TC_MIX", "G1_FOLDS", "G3_FLIP", "M1_FOOT_PLANT",
                "C1_ORACLE"):
        assert gates[gid]["pass"], (gid, gates[gid])
    rig = irmod.load(str(out / "rig.json"))
    c = rigbuild.crouch(rig, 0.05 * rig["H"], str(out / "synth.json"), str(out / "synth.atlas"))
    assert c["by"].startswith("spine-core") and c["max_slide"] <= 1.0


@pytest.mark.parametrize("mode", rigbuild.MODES)
def test_monkey_rebuild(mode, tmp_path):
    from spinegen import oracle
    skel, at = third_party("monkey")
    layers = tmp_path / "layers"
    ingest_spine.unpack(skel, at, str(layers))
    res = rigbuild.build(str(layers), mode=mode)
    paths = rigbuild.write(str(tmp_path / mode), res)
    ir = res["ir"]
    assert len(ir["animations"]) == 9 and len(ir["physics"]) == 7 and len(ir["ik"]) == 2
    assert {c["bone"] for c in ir["physics"]} >= {"cuffL", "pantR", "banana"}
    assert rigbuild.crouch(ir, 0.05 * ir["H"])["max_slide"] <= 1.0
    ok, why = oracle.available()
    if not ok:
        pytest.skip(f"runtime oracle unavailable: {why}")
    _, report = oracle.check(paths["skeleton"], paths["atlas"], ir=paths["rig"])
    failed = [g["id"] for g in report["gates"] if g.get("hard", True) and not g["pass"]]
    assert set(failed) <= {"P1_FPS_ROBUST"}, failed  # the monkey's own physics, carried over as is
