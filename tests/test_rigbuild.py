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
    # the knee direction comes from the template's rule ("outward", a front
    # view), mirrored per side: opposite Spine bends for mirror legs
    rep = built["joint"]["report"]["ik"]
    assert rep["leg_ik_l"]["knee"] == "outward"
    assert rep["leg_ik_l"]["knee_dir"] == [-1.0, 0.0] and rep["leg_ik_r"]["knee_dir"] == [1.0, 0.0]
    assert rep["leg_ik_l"]["bend_positive"] != rep["leg_ik_r"]["bend_positive"]
    for r in rep.values():
        assert r["setup_error_px"] <= 1e-3 and r["straightened_px"] == 0.0 and r["knee_test_px"] > 0
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


def test_lean_mesh_profile_is_cheaper_and_bends_the_same(built, layers_dir):
    """The default "lean" profile against the "refined" one: fewer than 60%
    of the vertices, the same B2, and the elbow still holds 120 deg."""
    ref = rigbuild.build(layers_dir, mode="joint", mesh_profile="refined")
    lean = built["joint"]
    assert lean["report"]["mesh_profile"] == "lean" and ref["report"]["mesh_profile"] == "refined"
    v_lean, v_ref = lean["report"]["totals"]["vertices"], ref["report"]["totals"]["vertices"]
    assert v_lean < 0.6 * v_ref
    assert lean["report"]["totals"]["b2"] >= 0.9
    ir = lean["ir"]
    b = irmod.by_name(ir["bones"])["forearm_l"]
    for a in (120.0, -120.0):
        assert lbs.Evaluator(ir).folds(pose={"forearm_l": {"rotation": b["rotation"] + a}})["folds"] == 0


def test_off_centre_elbow_is_moved_onto_the_limb(layers_dir):
    """A joints file that puts the elbow 12 px off the arm's centre line:
    the builder moves it back (and says so); --keep-joints keeps it."""
    with open(os.path.join(layers_dir, "joints.json")) as f:
        jd = json.load(f)
    x, y = jd["joints"]["elbow_l"]["xy"]
    jd["joints"]["elbow_l"]["xy"] = [x + 12.0, y]          # the arm hangs straight down: x is across it
    res = rigbuild.build(layers_dir, jd, mode="joint")
    c = res["report"]["bands"]["elbow_l"]["center"]
    assert abs(c[0] - x) <= 2.0
    assert any(n.startswith("joint elbow_l: moved") for n in res["report"]["notes"])
    kept = rigbuild.build(layers_dir, jd, mode="joint", recenter=False)
    assert kept["report"]["bands"]["elbow_l"]["center"][0] == pytest.approx(x + 12.0)
    # on the untouched synthetic character every joint is on its centre line already
    assert not any(" moved " in n for n in rigbuild.build(layers_dir, mode="joint")["report"]["notes"])


def test_joint_modes_override_and_choice(layers_dir):
    res = rigbuild.build(layers_dir, mode="joint", joint_modes={"elbow_l": "rigid", "knee_l": "ramp"})
    modes = res["report"]["joint_modes"]
    assert modes["elbow_l"] == "rigid" and modes["knee_l"] == "ramp" and modes["elbow_r"] == "helper"
    bands = res["report"]["bands"]
    assert bands["knee_l"]["helper"] is None and bands["elbow_r"]["helper"] == "elbow_mid_r"
    # a rigid elbow: the forearm stays 100% forearm, and the budget still gets the joint
    att = next(a for _, s, _, a in irmod.iter_attachments(res["ir"]) if s == "forearm_l")
    assert all(len(row) == 1 for row in att["weights"])
    assert "elbow_l" in {j["name"] for j in res["joints"]["joints"]}
    with pytest.raises(ValueError):
        rigbuild.build(layers_dir, mode="joint", joint_modes={"elbow_l": "bendy"})
    # chosen from a rigid budget: rigid where the cut-out already holds what is needed
    budget = {"schema": "spinegen-budget/1", "joints": {
        "elbow_l": {"+": {"soft": 100.0, "limit_by": None}, "-": {"soft": 95.0, "limit_by": None}},
        "elbow_r": {"+": {"soft": 22.0, "limit_by": "seam@a|b"}, "-": {"soft": 120.0, "limit_by": None}},
        "shoulder_l": {"+": {"soft": 26.0, "limit_by": "area@shoulder_l_band"}, "-": {"soft": 40.0, "limit_by": None}}}}
    chosen, why = rigbuild.choose_joint_modes(budget, {"elbow_l": 90.0, "elbow_r": 90.0, "shoulder_l": 150.0})
    assert chosen == {"elbow_l": "rigid", "elbow_r": "helper", "shoulder_l": "ramp"}
    assert "seam@a|b" in why["elbow_r"]


def _template(**joints):
    """biped_mascot with some joints' entries replaced (raw form, then expanded)."""
    from spinegen import template as tmpl
    with open(os.path.join(tmpl.TEMPLATE_DIR, "biped_mascot.json")) as f:
        raw = json.load(f)
    for name, spec in joints.items():
        raw["joints"][name] = spec
    return tmpl.expand(raw)


SYMMETRIC_ELBOW = {"band": "both", "helper": "elbow_mid_{s}", "budget": "arm_{s}.elbow"}


def test_elbow_band_reaches_up_the_sleeve_and_stops_at_the_wrist(layers_dir):
    """The template's elbow band is asymmetric (reach 2.25 up the sleeve,
    2 down the forearm); asked for more than the forearm holds, it stops
    at the rigid wrist; a band with no reach in the template stays
    symmetric (the measured default). The budget joint carries both
    regions G2 reads: the band (band_center, extent = half its length),
    where every triangle is range-checked, and the joint's fixed window
    (2 halfwidths both ways here, whatever the reach), where the area is
    summed (P2 review R1)."""
    res = rigbuild.build(layers_dir, mode="joint")
    bands = res["report"]["bands"]
    assert bands["elbow_l"]["reach"] == [2.25, 2.0]
    assert bands["shoulder_l"]["reach"][0] == bands["shoulder_l"]["reach"][1]   # no reach in the template
    hw = bands["elbow_l"]["halfwidth"]
    j = next(x for x in res["joints"]["joints"] if x["name"] == "elbow_l")["band"]
    mid = np.asarray(j["center"]) + 0.5 * (2.0 - 2.25) * hw * np.asarray(j["axis"])
    assert np.allclose(j["band_center"], mid) and j["extent"] == pytest.approx(2.125)
    assert j["window"] == [2.0, 2.0]
    longer = rigbuild.build(layers_dir, mode="joint", bands={"elbow_l": {"reach": {"upper": 3.5}}})
    jl = next(x for x in longer["joints"]["joints"] if x["name"] == "elbow_l")["band"]
    assert jl["extent"] == pytest.approx(2.75) and jl["window"] == j["window"]
    # asking for a forearm half longer than the forearm: clipped at the wrist
    with open(os.path.join(layers_dir, "joints.json")) as f:
        jd = json.load(f)
    elbow, wrist = (np.asarray(jd["joints"][k]["xy"], float) for k in ("elbow_l", "wrist_l"))
    long = rigbuild.build(layers_dir, mode="joint",
                          template=_template(**{"elbow_{s}": dict(SYMMETRIC_ELBOW, reach={"upper": 2, "lower": 9})}))
    b = long["report"]["bands"]["elbow_l"]
    d = float((wrist - elbow) @ np.asarray(b["axis"])) / b["halfwidth"]
    assert b["reach"][1] == pytest.approx(d, abs=1e-3) and d < 9
    assert any("stops at the joint wrist_l" in n for n in long["report"]["notes"])
    # every vertex at or past the wrist (the synthetic forearm's art ends there) is 100% forearm
    att = next(a for _, s, _, a in irmod.iter_attachments(long["ir"]) if s == "forearm_l")
    V = np.asarray(att["vertices"], float).reshape(-1, 2)
    wr = rigbuild.Canvas(json.load(open(os.path.join(layers_dir, "layers.json")))).to_world(wrist)
    ax = np.array([b["axis"][0], -b["axis"][1]])
    past = (V - wr) @ ax > -0.5
    assert past.any() and all(att["weights"][i] == [["forearm_l", 1.0]] for i in np.flatnonzero(past))


def test_neighbouring_bands_share_by_need(layers_dir):
    """Two bands on one layer (shoulder and elbow on the upper arm): half
    the gap each, but a band reaching less than its half leaves the rest to
    the other (rigbuild.joint_bands)."""
    def reaches(shoulder_lower, elbow_upper):
        t = _template(**{"shoulder_{s}": {"band": "lower", "budget": "arm_{s}.shoulder",
                                          "reach": {"upper": 2.0, "lower": shoulder_lower}},
                         "elbow_{s}": dict(SYMMETRIC_ELBOW, reach={"upper": elbow_upper, "lower": 2.0})})
        bands = rigbuild.build(layers_dir, mode="joint", template=t)["report"]["bands"]
        sh, el = bands["shoulder_l"], bands["elbow_l"]
        d = float(np.linalg.norm(np.subtract(sh["center"], el["center"])))
        return sh["reach"][1] * sh["halfwidth"], el["reach"][0] * el["halfwidth"], d
    sh, el, d = reaches(0.5, 50.0)           # a short shoulder band: the elbow takes the rest
    assert sh + el == pytest.approx(d, abs=0.05) and el > 0.5 * d
    sh, el, d = reaches(50.0, 50.0)          # both want more than the gap: half each
    assert sh == pytest.approx(0.5 * d, abs=0.05) and el == pytest.approx(0.5 * d, abs=0.05)
    sh, el, d = reaches(0.5, 1.0)            # room for both: neither changes
    assert sh + el < d


def test_template_elbow_holds_past_90_where_the_symmetric_band_stops(layers_dir, tmp_path):
    """The template's elbow (2.25 halfwidths up the sleeve, 4 forearm rows,
    3 points per row) holds more than 110 deg on the synthetic arm under
    G2's per-triangle range and fixed window, where the old symmetric
    2-halfwidth band without row points stopped at 90 deg on one fan
    triangle of the upper arm (tri@upper_arm_l). P2 review R1: A1's 3.5
    halfwidths up the sleeve reads no better than that old band once G2
    sums the joint's window instead of the band (92 deg, by area)."""
    from spinegen import budget
    out = {}
    long_elbow = dict(_template()["joints"]["elbow_l"], reach={"upper": 3.5, "lower": 2.0})
    long_elbow = {k: v for k, v in long_elbow.items() if k != "side"}
    for name, t in (("new", "biped_mascot"), ("old", _template(**{"elbow_{s}": SYMMETRIC_ELBOW})),
                    ("long", _template(**{"elbow_{s}": dict(long_elbow, helper="elbow_mid_{s}",
                                                           budget="arm_{s}.elbow")}))):
        res = rigbuild.build(layers_dir, mode="joint", template=t)
        assert res["report"]["bands"]["elbow_l"]["window"] == [2.0, 2.0]  # the same window for all three
        paths = rigbuild.write(str(tmp_path / name), res)
        ir, images = raster.load_rig(paths["rig"]), raster.load_images(paths["atlas"])
        b = budget.scan(ir, images, res["joints"], 4.0, 150.0, only={"elbow_l"})
        out[name] = b["joints"]["elbow_l"]["+"]
    assert out["old"]["soft"] <= 92 and out["old"]["limit_by"].startswith("tri@upper_arm")
    assert out["new"]["soft"] >= 110
    assert out["long"]["soft"] <= 96 and out["long"]["limit_by"].startswith("area@")


def test_fit_reach_takes_the_shortest_reach_that_holds(layers_dir):
    """fit_reach (P2 review R1) scans each candidate upper reach with the
    budget, per joint, and takes the shortest that holds `need` both ways
    (else the one that holds most); build(bands=...) applies it to that
    joint alone, and the joint's G2 window does not move with it."""
    bands, fit = rigbuild.fit_reach(layers_dir, candidates={"elbow_l": [2.25, 3.5]}, need=100.0, step=4.0,
                                    mode="joint")
    rows = fit["table"]["elbow_l"]
    assert rows["2.25"]["min"] >= 100 > rows["3.5"]["min"]
    assert fit["chosen"] == {"elbow_l": 2.25} and bands == {"elbow_l": {"reach": {"upper": 2.25}}}
    res = rigbuild.build(layers_dir, mode="joint", bands={"elbow_l": {"reach": {"upper": 3.0}}})
    assert res["report"]["bands"]["elbow_l"]["reach"] == [3.0, 2.0]
    assert res["report"]["bands"]["elbow_r"]["reach"] == [2.25, 2.0]
    assert res["report"]["bands"]["elbow_l"]["window"] == res["report"]["bands"]["elbow_r"]["window"]
    with pytest.raises(ValueError, match="only"):
        rigbuild.build(layers_dir, mode="joint", bands={"elbow_l": {"helper": None}})


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
    # both knees go outward, mirror-symmetric; the WHOLE foot stays put
    assert c["knees_ok"] and c["knee_bend_diff_deg"] <= 1e-6
    assert c["foot_slide"] <= 1e-6 and min(c["foot_vertices"].values()) > 2
    # the rigid comparison rig has the same skeleton, so the same planted soles
    assert rigbuild.crouch(built["rigid"]["ir"], 0.05 * ir["H"])["max_slide"] <= 1e-6


LEG_OLD = {"knee_{s}": {"band": "both", "helper": "knee_mid_{s}", "budget": "leg_{s}.knee"},
           "ankle_{s}": {"band": "both", "budget": "leg_{s}.ankle"}}


@pytest.fixture(scope="module")
def flared(layers_dir, tmp_path_factory):
    """The synthetic character with a mascot's calf: the knees moved down
    to 2.7 calf halfwidths above the ankles, and each trouser hem flared
    28 px outward over its bottom 40 px (the monkey's calves are 2.1-2.8
    halfwidths long and flare 1.9 halfwidths at the hem)."""
    import shutil
    from PIL import Image
    d = str(tmp_path_factory.mktemp("flared") / "layers")
    shutil.copytree(layers_dir, d)
    with open(os.path.join(d, "layers.json")) as f:
        doc = json.load(f)
    pad, rows = 28, 40
    for lay in doc["layers"]:
        if lay["role"] not in ("shin_l", "shin_r"):
            continue
        path = os.path.join(d, "layers", lay["name"] + ".png")
        im = np.array(Image.open(path).convert("RGBA"))
        h, w = im.shape[:2]
        out = np.zeros((h, w + 2 * pad, 4), np.uint8)
        out[:, pad:pad + w] = im
        paint = im[h // 2, w // 2]
        for k in range(rows):                     # outward = away from the body's midline
            ext = int(pad * (1 - k / rows))
            if lay["role"] == "shin_l":
                out[h - 1 - k, pad - ext:pad + 2] = paint
            else:
                out[h - 1 - k, pad + w - 2:pad + w + ext] = paint
        Image.fromarray(out).save(path)
        lay["x"] -= pad
        lay["w"] += 2 * pad
    with open(os.path.join(d, "layers.json"), "w") as f:
        json.dump(doc, f)
    with open(os.path.join(d, "joints.json")) as f:
        jd = json.load(f)
    for s in "lr":
        jd["joints"][f"knee_{s}"]["xy"][1] = jd["joints"][f"ankle_{s}"]["xy"][1] - 110.0
    with open(os.path.join(d, "joints.json"), "w") as f:
        json.dump(jd, f)
    return d


def test_knee_and_planted_ankle_overlap_on_a_short_flared_calf(flared):
    """P2 A2: the template's knee (1.5 halfwidths down the calf) and planted
    ankle (2.4 up the shin) overlap and compose (weights CHAIN COMPOSITION)
    instead of sharing the calf. On a short, flared calf a deep crouch
    folds the shared bands and not the overlapping ones, and F1's
    guarantees hold: knees outward and symmetric, whole foot and calf hems
    0 px."""
    folds = {}
    for name, t in (("new", "biped_mascot"), ("old", _template(**LEG_OLD))):
        res = rigbuild.build(flared, mode="joint", template=t)
        ir = res["ir"]
        b = irmod.by_name(ir["bones"])
        bands = res["report"]["bands"]
        if name == "new":
            # not shared: each reaches its own length (the ankle stops at the knee)
            assert bands["knee_l"]["reach"] == [2.0, 1.5]
            calf = float(np.linalg.norm(np.subtract(bands["knee_l"]["pivot"], bands["ankle_l"]["pivot"])))
            ankle = 2 * bands["ankle_l"]["reach"][0] * bands["ankle_l"]["halfwidth"]
            assert ankle + 1.5 * bands["knee_l"]["halfwidth"] > calf
            # three influences at most where they overlap, and some vertex has all three
            att = next(a for _, s, _, a in irmod.iter_attachments(ir) if s == "calf_l")
            assert max(len(r) for r in att["weights"]) == 3
            assert any({n for n, _ in r} == {"knee_mid_l", "shin_l", "foot_l"} for r in att["weights"])
        for f in (0.08, 0.10):
            pose = {"hips": {"y": b["hips"]["y"] - f * ir["H"]}}
            folds[name, f] = lbs.Evaluator(ir).folds(pose=pose)["folds"]
            if name == "new":
                c = rigbuild.crouch(ir, f * ir["H"])
                assert c["knees_ok"] and c["foot_slide"] <= 1e-6 and c["shin_bottom_slide"] <= 1e-6
    assert folds["new", 0.08] == 0 and folds["old", 0.08] >= 4
    assert folds["new", 0.10] <= folds["old", 0.10] / 4


CUFF_FULL, CUFF_NONE = 100.0, 120.0  # px above the elbow: 100% cuff below FULL, none above NONE
PANT_FULL, PANT_NONE = 90.0, 130.0  # px above the knee: the same for the pant hem on the left thigh


def _physics(bone, order):
    """A monkey-like physics constraint on `bone` (its cuffs' values)."""
    return {"name": f"{bone}_phys", "order": order, "skin_required": False, "bone": bone, "x": 0.0, "y": 0.0,
            "rotate": 1.0, "scale_x": 0.0, "shear_x": 0.0, "limit": 5000.0, "fps": 60.0, "inertia": 0.28,
            "strength": 230.0, "damping": 0.82, "mass": 1.0, "wind": 0.0, "gravity": 0.0, "mix": 1.0,
            "global": {k: False for k in ("inertia", "strength", "damping", "mass", "wind", "gravity", "mix")},
            "spring": None, "runs": True}


@pytest.fixture(scope="module")
def cuffed(tmp_path_factory):
    """The synthetic source with monkey-style accessories: cuff_l under
    upperarm_l and pant_l under thigh_l, each with a physics constraint;
    the left sleeve is 100% cuff from the elbow up to CUFF_FULL px and 100%
    upper arm above CUFF_NONE px (the monkey's sleeves: 100% cuffL over
    their lower half), the left thigh likewise 100% pant up to PANT_FULL px
    above the knee. Returns the layers dir and the elbow's and knee's
    world y."""
    ir, images = _source()
    b = irmod.by_name(ir["bones"])
    ev = lbs.Evaluator(ir)
    w = ev.world_dict(ev.world())
    ends = {}
    for acc, parent, slot, child, full, none in (("cuff_l", "upperarm_l", "upper_arm_l", "forearm_l", CUFF_FULL,
                                                  CUFF_NONE),
                                                 ("pant_l", "thigh_l", "thigh_l", "calf_l", PANT_FULL, PANT_NONE)):
        ir["bones"].insert(irmod.bone_index(ir)[parent] + 1,
                           irmod.new_bone(acc, parent, 0.0, 0.0, 0.0, b[parent]["length"]))
        y_j = ends[acc] = float(w[child][5])
        att = ir["skins"][0]["attachments"][slot][slot]
        rows = []
        for x, y in att["vertices"]:
            t = float(np.clip((y - y_j - full) / (none - full), 0.0, 1.0))  # 0 accessory .. 1 parent
            rows.append([[n, v] for n, v in ((acc, 1.0 - t), (parent, t)) if v > 0])
        att["weights"] = rows
        ir["physics"].append(_physics(acc, len(ir["transform"]) + len(ir["ik"]) + len(ir["physics"])))
    assert not irmod.validate(ir)
    d = tmp_path_factory.mktemp("cuffed")
    spine42.write(ir, str(d / "synth.json"))
    at, _ = atlas.write(str(d / "synth.atlas"), images)
    ingest_spine.unpack(str(d / "synth.json"), at, str(d / "layers"))
    return str(d / "layers"), ends["cuff_l"], ends["pant_l"]


def test_accessory_weights_ride_on_their_parent(cuffed, tmp_path):
    """P2 review (A2_no_regression_secondary_motion): an elbow band of 3.5
    halfwidths (126 px, A1's template) runs up the sleeve over the whole cuff.
    The old carry-over faded accessory weights out over each band's reach
    and left the cuff almost nothing; now the cuff takes its source share
    of whatever the upper arm holds, so it keeps every sleeve vertex it had
    (100% of the upper arm's share inside CUFF_FULL), the joint bends
    exactly as without it (rest-exact: the cuff moves like its parent), and
    its physics moves the sleeve in the official runtime."""
    d, y_el, _ = cuffed
    res = rigbuild.build(d, mode="joint", bands={"elbow_l": {"reach": {"upper": 3.5}}})
    ir = res["ir"]
    acc = res["report"]["accessories"]
    assert acc["ok"] and acc["bones"]["cuff_l"]["physics"] and acc["bones"]["cuff_l"]["vertices"] > 0
    att = next(a for _, s, _, a in irmod.iter_attachments(ir) if s == "upper_arm_l")
    up = res["report"]["bands"]["elbow_l"]["reach"][0] * res["report"]["bands"]["elbow_l"]["halfwidth"]
    assert up >= CUFF_NONE  # the band covers the whole cuff: the case the fade emptied
    rows = [dict((n, x) for n, x in r) for r in att["weights"]]
    above = np.array([y - y_el for _, y in att["vertices"]])
    inside = [r for r, a in zip(rows, above) if 2.0 < a < CUFF_FULL - 10.0]
    assert inside and all("upperarm_l" not in r for r in inside)           # the whole upper-arm share
    assert sum(r.get("cuff_l", 0.0) for r in inside) > 0.2 * len(inside)
    assert all("cuff_l" not in r for r, a in zip(rows, above) if a > CUFF_NONE + 10.0)
    assert all(len(r) <= 3 for r in rows)
    # rest-exact: the same rig with the cuff merged back into its parent
    # poses every vertex identically (no physics in lbs)
    merged = json.loads(json.dumps(ir))
    for _, s, _, a in irmod.iter_attachments(merged):
        if a.get("weights"):
            a["weights"] = [[[{"cuff_l": "upperarm_l", "pant_l": "thigh_l"}.get(n, n), x] for n, x in r]
                            for r in a["weights"]]
    pose = {"forearm_l": {"rotation": 100.0}, "upperarm_l": {"rotation": -30.0},
            "hips": {"y": -0.1 * ir["H"]}}
    for slot in ("upper_arm_l", "thigh_l"):
        got = []
        for rig in (ir, merged):
            ev = lbs.Evaluator(rig)
            got.append(ev.attachment_vertices(ev.world(pose), slot, dict(ev.shown())[slot]))
        assert np.abs(got[0] - got[1]).max() < 1e-9
    from spinegen import oracle
    ok, why = oracle.available()
    if not ok:
        pytest.skip(f"runtime oracle unavailable: {why}")
    paths = rigbuild.write(str(tmp_path / "rig"), res)
    moved = []
    for phys in ("update", "none"):
        fr = oracle.dump(paths["skeleton"], paths["atlas"], anim="wave", fps=60, physics=phys)["frames"]
        moved.append(np.stack([np.asarray(f["slots"]["upper_arm_l"]["vertices"]).reshape(-1, 2) for f in fr]))
    assert np.linalg.norm(moved[0] - moved[1], axis=-1).max() > 1.0  # the cuff's physics moves the sleeve


def test_accessory_tapers_toward_vertices_with_no_room(cuffed):
    """P2 review: a thigh vertex in the knee band's overlap with the hip
    band already has three bones (hips, thigh, knee helper), so L8 leaves
    no room for the pant hem and its share stays on the thigh. A
    neighbour with room must not then take a whole share: two vertices 11
    px apart moving 0 and 0.47 x the pant's swing flipped the monkey's
    sliver triangle across the thigh in cheer. The share tapers to 0 over
    ACC_TAPER halfwidths toward such vertices. A knee band reaching 3
    halfwidths up the thigh makes the overlap wide here."""
    d, _, y_kn = cuffed
    knee = {"band": "both", "helper": "knee_mid_{s}", "budget": "leg_{s}.knee",
            "reach": {"upper": 3.0, "lower": 1.5}, "overlap": True}
    res = rigbuild.build(d, mode="joint", template=_template(**{"knee_{s}": knee}))
    ir = res["ir"]
    att = next(a for _, s, _, a in irmod.iter_attachments(ir) if s == "thigh_l")
    rows = [dict((n, x) for n, x in r) for r in att["weights"]]
    assert all(len(r) <= 3 for r in rows)
    v = np.asarray(att["vertices"], float)
    above = v[:, 1] - y_kn
    stuck = [i for i, r in enumerate(rows) if len(r) == 3 and "pant_l" not in r and "thigh_l" in r
             and 5.0 < above[i] < PANT_NONE - 5.0]
    assert stuck, "the fixture must have vertices with no room inside the pant hem"
    hw = min(res["report"]["bands"][j]["halfwidth"] for j in ("hip_l", "knee_l"))
    T = np.asarray(att["triangles"]).reshape(-1, 3)
    for i in stuck:
        for j in set(T[(T == i).any(1)].ravel()) - {i}:
            r = rows[j]
            share = r.get("pant_l", 0.0) / max(r.get("pant_l", 0.0) + r.get("thigh_l", 0.0), 1e-9)
            assert share <= np.linalg.norm(v[i] - v[j]) / (rigbuild.ACC_TAPER * hw) + 1e-9, (i, j, share)
    assert res["report"]["accessories"]["bones"]["pant_l"]["vertices"] > 0


def test_band_above_an_overlap_keeps_its_share(layers_dir):
    """The knee overlaps the hip band (template "overlap") and keeps the
    reach the template asks for (2 halfwidths up the thigh); the hip band,
    which asks for none, still stops at its half of the gap as before P2
    A2, instead of growing to the full 2-halfwidth default over most of
    the thigh (the monkey: 1.14 -> 2.0 halfwidths, 118 of 134 px)."""
    res = rigbuild.build(layers_dir, mode="joint")
    bands = res["report"]["bands"]
    hip, knee = bands["hip_l"], bands["knee_l"]
    d = float(np.linalg.norm(np.subtract(hip["pivot"], knee["pivot"])))
    assert knee["reach"] == [2.0, 1.5]
    want = max(0.5 * d, d - knee["reach"][0] * knee["halfwidth"]) / hip["halfwidth"]
    assert hip["reach"] == pytest.approx([want, want], abs=1e-3) and want < 2.0


ANKLE_UPPER_24 = {"ankle_{s}": {"band": "both", "budget": "leg_{s}.ankle", "reach": {"upper": 2.4, "lower": 2.4},
                                 "overlap": True}}


def test_crouch_with_the_weight_shifted_does_not_fold_the_flared_calf(flared):
    """P2 review (A2_robust_to_weight_shift): a crouch that also moves the
    hips sideways bends one ankle much more than the other (the monkey's
    right ankle: 44 deg straight down at 0.10 H, 60 deg with the hips 40 px
    over). The template's ankle runs the whole calf and composes by row
    (weights CHAIN COMPOSITION: foot = the ankle ramp's t outright), so the
    foot's weight falls off evenly up the flared calf. A2's ankle (2.4
    halfwidths, foot = t x the shin's share) folds there."""
    folds = {}
    for name, t in (("new", "biped_mascot"), ("old", _template(**ANKLE_UPPER_24))):
        ir = rigbuild.build(flared, mode="joint", template=t)["ir"]
        b = irmod.by_name(ir["bones"])
        for f in (0.08, 0.10):
            for dx in (-40.0, -20.0, 0.0, 20.0, 40.0):
                pose = {"hips": {"x": b["hips"]["x"] + dx, "y": b["hips"]["y"] - f * ir["H"]}}
                folds[name, f, dx] = lbs.Evaluator(ir).folds(pose=pose)["folds"]
    assert all(v == 0 for (n, _, _), v in folds.items() if n == "new"), folds
    assert sum(v for (n, _, _), v in folds.items() if n == "old") >= 5, folds
    with pytest.raises(ValueError, match="compose"):
        rigbuild.build(flared, mode="joint", template=_template(
            **{"ankle_{s}": dict(ANKLE_UPPER_24["ankle_{s}"], compose="take")}))


def test_straighten_moves_the_joint_the_template_names(layers_dir, kinked):
    """ik "straighten": which joint goes onto the hip-ankle line when the
    setup leg is kinked against the knee rule (default the knee; the A2
    study also tried the ankle and the hip). Either way the setup leg is
    reproduced and the knee bends outward."""
    for how in ("ankle", "hip"):
        t = _template()
        for ik in t["ik"]:
            ik["straighten"] = how
        res = rigbuild.build(layers_dir, joints=kinked["joints"], mode="joint", template=t, recenter=False)
        rep = res["report"]["ik"]
        moved = {r["straightened_joint"] for r in rep.values() if r["straightened_px"] > 0}
        assert moved and all(j.startswith("ankle_" if how == "ankle" else "hip_") for j in moved)
        # a fully straight leg is the IK's singular pose: 0.009 px with the ankle moved
        assert all(r["setup_error_px"] <= 0.05 for r in rep.values())
        assert rigbuild.crouch(res["ir"], 0.05 * res["ir"]["H"])["knees_ok"]


def _knees_and_foot(ir, depth):
    """World x move of each knee and the largest move of any foot vertex
    below its ankle when the hips drop `depth` (lbs; independent of
    rigbuild.crouch's own bookkeeping)."""
    b = irmod.by_name(ir["bones"])
    w0, w1 = _world(ir), _world(ir, {"hips": {"y": b["hips"]["y"] - depth}})
    knees = {s: w1[f"shin_{s}"][4] - w0[f"shin_{s}"][4] for s in "lr"}
    ev = lbs.Evaluator(ir)
    a0, a1 = ev.world(), ev.world(pose={"hips": {"y": b["hips"]["y"] - depth}})
    foot = 0.0
    for s in "lr":
        att = dict(ev.shown())[f"foot_{s}"]
        v0, v1 = ev.attachment_vertices(a0, f"foot_{s}", att), ev.attachment_vertices(a1, f"foot_{s}", att)
        below = v0[:, 1] <= w0[f"ik_foot_{s}"][5] + 1e-6
        foot = max(foot, float(np.linalg.norm(v1[below] - v0[below], axis=1).max()))
    return knees, foot


@pytest.fixture(scope="module")
def kinked(layers_dir):
    """The synthetic legs with both setup knees kinked 10 px toward +x:
    the left leg inward, the right one outward (the monkey's case: -9 and
    -19.5 deg kinks of the same sign). Kept where they are (no
    recentering) so only the kink differs."""
    with open(os.path.join(layers_dir, "joints.json")) as f:
        jd = json.load(f)
    for s in "lr":
        jd["joints"][f"knee_{s}"]["xy"][0] += 10.0
    return {"joints": jd, "res": rigbuild.build(layers_dir, joints=jd, mode="joint", recenter=False)}


def test_knees_bend_outward_whatever_the_setup_kink(kinked, tmp_path):
    """P2 review F1: the bend came from the setup kink, so a leg kinked a
    few degrees inward caved toward the midline in every crouch (the
    monkey's left knee: 56 px inward, the right 51 px outward). The knee
    rule decides now; the inward-kinked leg is straightened at setup."""
    res = kinked["res"]
    ir, rep = res["ir"], res["report"]["ik"]
    assert rep["leg_ik_l"]["straightened_px"] == pytest.approx(10.0, abs=1e-6)
    assert rep["leg_ik_r"]["straightened_px"] == 0.0  # kinked the right way: kept
    assert all(r["setup_error_px"] <= 1e-3 for r in rep.values())
    knees, foot = _knees_and_foot(ir, 0.05 * ir["H"])
    assert knees["l"] < -5.0 and knees["r"] > 5.0, knees  # both outward (l is at -x)
    assert foot <= 1e-6
    c = rigbuild.crouch(ir, 0.05 * ir["H"])
    assert c["knees_ok"] and c["knee_bend_diff_deg"] <= rigbuild.KNEE_SYM_DEG, c["knees"]
    # the setup pose is still the source's: the straightened knee moved the
    # pivot, not the art
    bones = irmod.by_name(ir["bones"])
    w = _world(ir)
    assert w["shin_l"][4] == pytest.approx(kinked["joints"]["joints"]["knee_l"]["xy"][0] - 181.0 - 10.0, abs=1e-6)
    assert bones["shin_l"]["length"] > 0
    # the official runtime agrees
    from spinegen import oracle
    ok, why = oracle.available()
    if not ok:
        pytest.skip(f"runtime oracle unavailable: {why}")
    paths = rigbuild.write(str(tmp_path / "kinked"), res)
    dirs = {n: r["knee_dir"] for n, r in rep.items()}
    rc = rigbuild.crouch(ir, 0.05 * ir["H"], paths["skeleton"], paths["atlas"], knee_dirs=dirs)
    assert rc["by"].startswith("spine-core") and rc["knees_ok"], rc["knees"]
    assert rc["foot_slide"] <= 1.0 and rc["max_slide"] <= 1.0


def test_knee_rule_facing(layers_dir):
    """A side-view template ("knee": "facing") bends both knees along the
    character's facing, here -x (joints.json "facing": -1)."""
    from spinegen import template as tmpl
    t = tmpl.load("biped_mascot")
    for ik in t["ik"]:
        ik["knee"] = "facing"
    with open(os.path.join(layers_dir, "joints.json")) as f:
        jd = json.load(f)
    with pytest.raises(ValueError, match="facing"):
        rigbuild.build(layers_dir, joints=jd, template=t, source=None)
    jd["facing"] = -1
    res = rigbuild.build(layers_dir, joints=jd, template=t, source=None)
    ir = res["ir"]
    assert all(r["knee_dir"] == [-1.0, 0.0] for r in res["report"]["ik"].values())
    knees, _ = _knees_and_foot(ir, 0.05 * ir["H"])
    assert knees["l"] < -5.0 and knees["r"] < -5.0, knees
    dirs = {n: r["knee_dir"] for n, r in res["report"]["ik"].items()}
    assert rigbuild.crouch(ir, 0.05 * ir["H"], knee_dirs=dirs)["knees_ok"]
    # judged by the front-view rule instead, the right knee goes the wrong way
    assert not rigbuild.crouch(ir, 0.05 * ir["H"])["knees_ok"]


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
    assert rep["crouch"]["max_slide"] <= 1.0 and rep["crouch"]["foot_slide"] <= 1.0 and rep["crouch"]["knees_ok"]
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
    assert c["by"].startswith("spine-core") and c["max_slide"] <= 1.0 and c["foot_slide"] <= 1.0
    assert c["knees_ok"], c["knees"]


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
    c = rigbuild.crouch(ir, 0.05 * ir["H"])
    assert c["max_slide"] <= 1.0 and c["foot_slide"] <= 1.0 and c["knees_ok"], c["knees"]
    ok, why = oracle.available()
    if not ok:
        pytest.skip(f"runtime oracle unavailable: {why}")
    _, report = oracle.check(paths["skeleton"], paths["atlas"], ir=paths["rig"])
    failed = [g["id"] for g in report["gates"] if g.get("hard", True) and not g["pass"]]
    assert set(failed) <= {"P1_FPS_ROBUST"}, failed  # the monkey's own physics, carried over as is
