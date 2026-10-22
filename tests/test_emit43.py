"""IR -> Spine 4.3 JSON (spinegen.emit.spine43) and its equivalence with the 4.2 emit.

WHY: Spine 4.3 is the primary target and 4.2 stays for old games, so one IR
has to mean the same character in both files. The layout tests pin the 4.3
spellings (a 4.2 key in a 4.3 file is dropped without a word); the runtime
tests play the 4.2 emit in spine-core 4.2.120 and the 4.3 emit in 4.3.13 and
compare every bone and vertex, on a synthetic rig only (no third-party art).
"""
import json
import os
import subprocess

import pytest

from spinegen import equiv43, oracle, synth
from spinegen import ir as irmod
from spinegen.emit import spine42, spine43
from spinegen.spine42_reader import read

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def features_ir():
    return read(synth.features()[0], "features")


def helper_ir(keyed=False):
    """The features rig with its local half-angle transform turned into the
    IR's `half_angle` shorthand, the helper given a non-zero setup rotation
    (the to-offset term) and a box to show it; `keyed` also keys the helper's
    rotation, which forces the direct form."""
    a = features_ir()
    i = next(i for i, c in enumerate(a["transform"]) if c["name"] == "tc_half")
    a["transform"][i] = {"name": "tc_half", "order": a["transform"][i]["order"], "runs": True,
                         "skin_required": False, "kind": "half_angle", "bone": "elbow_mid", "target": "fore",
                         "mix": 0.5}
    next(b for b in a["bones"] if b["name"] == "elbow_mid")["rotation"] = 17.0
    a["slots"].append(irmod.new_slot("box_elbow_mid", "elbow_mid", "box"))
    skin = irmod.default_skin(a)
    skin["attachments"]["box_elbow_mid"] = {"box": dict(skin["attachments"]["box_tla_b"]["box"])}
    if keyed:
        anim = next(an for an in a["animations"] if an["name"] == "pose_reach")
        anim["timelines"].append({"type": "bone", "target": "elbow_mid", "property": "rotate",
                                  "keys": [{"time": 0.0, "value": -30.0, "curve": None}]})
    assert irmod.validate(a) == []
    return a


def need_runtimes():
    for rt in ("4.2", "4.3"):
        ok, why = oracle.available(rt)
        if not ok:
            pytest.skip(f"spine-core {rt} unavailable: {why}")


# ---------------------------------------------------------------- layout

def test_one_constraint_list_in_ir_order():
    a = features_ir()
    out = spine43.emit(a)
    for k in ("ik", "transform", "path", "physics"):
        assert k not in out, f"4.3 drops a top-level {k}[]"
    want = [(k, c["name"]) for k, c in irmod.constraints(spine42.prepare(a))]
    assert [(c["type"], c["name"]) for c in out["constraints"]] == want
    assert all("order" not in c for c in out["constraints"])
    assert out["skeleton"]["spine"] == "4.3.13"
    assert spine43.emit(a, version="4.3.20")["skeleton"]["spine"] == "4.3.20"


def test_43_spellings():
    out = spine43.emit(features_ir())
    by = {c["name"]: c for c in out["constraints"]}
    assert by["ik_aim"]["scaleY"] == "uniform" and by["ik_leg"]["scaleY"] == "none"
    assert all("uniform" not in c for c in out["constraints"])
    tc = by["tc_local_rel"]
    assert (tc["source"], tc["localSource"], tc["localTarget"], tc["additive"]) == ("tip", True, True, True)
    assert not {"target", "local", "relative"} & set(tc)
    assert set(tc["properties"]) == {"rotate", "x", "y", "scaleX", "scaleY", "shearY"}
    assert all(list(p["to"]) == [name] for name, p in tc["properties"].items())  # identity map
    for k in ("mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY"):
        assert k in tc
    assert by["tc_world"]["localSource"] is False and by["tc_world_rel"]["additive"] is True
    assert by["pc"]["slot"] == "path" and "target" not in by["pc"]
    for p in (c for c in out["constraints"] if c["type"] == "physics"):
        for k in ("x", "y", "rotate", "scaleX", "shearX", "limit", "fps", "inertia", "strength", "damping", "mass",
                  "wind", "gravity", "mix"):
            assert k in p, k
    linked = out["skins"][0]["attachments"]["arm"]["arm_alt"]
    assert linked["source"] == "arm" and "parent" not in linked


def test_half_angle_written_in_the_verified_form():
    rep = {}
    out = spine43.emit(helper_ir(), report=rep)
    tc = next(c for c in out["constraints"] if c["name"] == "tc_half")
    assert (tc["source"], tc["bones"], tc["localSource"], tc["localTarget"], tc["additive"]) == \
        ("fore", ["elbow_mid"], True, True, False)
    to = tc["properties"]["rotate"]["to"]["rotate"]
    assert list(tc["properties"]) == ["rotate"] and tc["mixRotate"] == 1
    assert to == {"scale": 0.5, "offset": 8.5}  # (1 - mix) * the helper's setup rotation 17
    assert rep["half_angle"]["tc_half"] == {"setup_rotation": 17.0, "form": "scale"}
    assert spine43.half_angle_check(out) == [("tc_half", 0.5, 8.5)]
    # something keys the helper: the blend itself (scale 1, mixRotate = mix)
    rep = {}
    out = spine43.emit(helper_ir(keyed=True), report=rep)
    tc = next(c for c in out["constraints"] if c["name"] == "tc_half")
    assert rep["half_angle"]["tc_half"]["form"] == "direct"
    assert tc["mixRotate"] == 0.5 and tc["properties"]["rotate"]["to"]["rotate"] == {"offset": 0, "scale": 1}


def test_physics_defaults_are_43s_where_the_ir_is_silent():
    a = features_ir()
    p = next(c for c in a["physics"] if c["name"] == "phys_ns")
    for k in ("inertia", "damping", "strength"):
        del p[k]
    rep = {}
    out = spine43.emit(a, report=rep)
    q = next(c for c in out["constraints"] if c["name"] == "phys_ns")
    assert (q["inertia"], q["damping"], q["strength"]) == (0.5, 0.85, 100)
    assert rep["defaults"] == [{"physics": "phys_ns", "filled": ["inertia", "strength", "damping"]}]


def test_constraint_42_never_ran_stays_inert():
    a = read(synth.order_rig()[0], "gap")
    out = spine43.emit(a)
    assert [c["name"] for c in out["constraints"]] == ["ik", "tc"]
    tc = out["constraints"][1]
    assert all(tc[k] == 0 for k in ("mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY"))
    assert "transform" not in out["animations"]["move"]


def test_flags_what_43_plays_differently():
    rep = {}
    spine43.emit(features_ir(), report=rep)
    text = "\n".join(rep["warnings"])
    assert "tc_local_abs: absolute local scale on tla_b" in text
    assert "tc_world_wrap: world rotate offset 250.0" in text
    assert "tc_world: world shearY offset 4.0" in text
    assert "tc_world_rel: world relative shearY" in text
    assert "tc_chain writes chp_normal's world transform before ik ik_chp" in text
    assert "phys_ns writes phy_b's world transform before ik ik_phy" in text
    assert {"tla_b", "chp_normal", "chq_noScale", "ch_reader", "phy_b", "follow_wr"} <= set(rep["differs"])
    assert not {"upper", "fore", "elbow_mid", "tip", "thigh", "shin", "aim"} & set(rep["differs"])


def test_generated_rig_has_nothing_to_flag():
    # spinegen's own order: IK, then local half-angle helpers, then physics
    from spinegen import synth_art
    a, _, _ = synth_art.build(mode="joint", arms=2, legs=2)
    rep = {}
    out = spine43.emit(a, report=rep)
    assert rep["differs"] == [] and not [w for w in rep["warnings"] if "transform" in w or "writes" in w]
    assert len(spine43.half_angle_check(out)) == 4


# ---------------------------------------------------------------- runtimes

def test_43_runtime_loads_every_constraint(tmp_path):
    need_runtimes()
    jp, ap = synth.write(str(tmp_path), "features")
    p = tmp_path / "features43.json"
    spine43.write(helper_ir(), str(p))
    r = subprocess.run(["node", os.path.join(REPO, "qa43", "load.mjs"), "--skeleton", str(p), "--atlas", ap],
                       capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 0, r.stderr
    assert out["counts"]["constraints"] == out["json"]["constraints"] == 20


@pytest.mark.parametrize("keyed", [False, True])
def test_42_and_43_emits_play_the_same(tmp_path, keyed):
    """Every frame of every animation, physics off: the 4.2 emit in 4.2.120 and the
    4.3 emit in 4.3.13 agree to 0.01 px on every slot whose bone the emitter
    did not flag, the half-angle helper included; the flags are not idle."""
    need_runtimes()
    _, ap = synth.write(str(tmp_path), "features")
    a = helper_ir(keyed)
    res = equiv43.run(a, ap, str(tmp_path), "f", fps=30, modes=("none",), skip_differs=True)
    s = equiv43.summary(res)["none"]
    assert s["mismatch"] == 0 and s["frames"] > 30
    assert s["max_px"] <= 0.01, s
    assert s["bone_m"] <= 1e-6, s
    assert "elbow_mid" not in res["differs"]
    flagged = equiv43.run(a, ap, str(tmp_path), "f", fps=30, modes=("none",))
    assert equiv43.summary(flagged)["none"]["max_px"] > 1.0  # the flagged bones really differ


def test_physics_differs_and_is_reported(tmp_path):
    need_runtimes()
    _, ap = synth.write(str(tmp_path), "features")
    res = equiv43.run(features_ir(), ap, str(tmp_path), "f", fps=30, modes=("none", "update"), skip_differs=True)
    s = equiv43.summary(res)
    assert s["none"]["max_px"] <= 0.01
    assert s["update"]["max_px"] > 0.01  # 4.3 interpolates physics between steps; 4.2 does not
