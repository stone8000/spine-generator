"""spinegen.ingest_spine: a Spine character -> layers dir (layers.json,
joints.json, one PNG per attachment at its setup canvas position).

The contract the rig builder depends on: the layers drawn back in z order
ARE the original setup render (<= 1/255 per channel), the canvas <-> world
mapping is the one written in layers.json, and joints land on the source
bones' setup origins under template names. Synthetic characters only; the
monkey test skips when the third-party files are absent.
"""
import json
import os

import numpy as np
import pytest

from spinegen import atlas, ingest_spine, lbs, raster, synth_art
from spinegen import ir as irmod
from spinegen.emit import spine42

from conftest import third_party


def _character(**kw):
    kw.setdefault("arms", 2)
    kw.setdefault("legs", 2)
    return synth_art.build(**kw)


def _max_diff(res, ir, images):
    doc, imgs = res["layers"], res["images"]
    a = ingest_spine.recomposite(doc, imgs).astype(int)
    b = ingest_spine.setup_render(ir, images, doc).astype(int)
    return int(np.abs(a - b).max())


@pytest.mark.parametrize("mode", ["joint", "rigid"])
def test_recomposite_equals_setup_render(mode):
    ir, images, _ = _character(mode=mode)
    res = ingest_spine.unpack(ir, images)
    assert _max_diff(res, ir, images) <= 1
    assert len(res["layers"]["layers"]) == len(ir["slots"])
    # z follows the draw order
    assert [L["slot"] for L in sorted(res["layers"]["layers"], key=lambda L: L["z"])] == \
        [s["name"] for s in ir["slots"]]


def test_through_spine_files(tmp_path):
    """Spine JSON + atlas in, layers dir out, read back from disk."""
    ir, images, _ = _character()
    skel = tmp_path / "synth.json"
    spine42.write(ir, str(skel))
    at, _ = atlas.write(str(tmp_path / "synth.atlas"), images)
    out = tmp_path / "layers"
    res = ingest_spine.unpack(str(skel), at, str(out))
    doc, imgs = ingest_spine.load_layers(str(out))
    assert doc["schema"] == "spinegen-layers/1"
    assert doc["source"]["kind"] == "spine" and doc["source"]["sha256"]
    src_ir = raster.load_rig(str(skel))
    diff = np.abs(ingest_spine.recomposite(doc, imgs).astype(int)
                  - ingest_spine.setup_render(src_ir, raster.load_atlas_images(at), doc).astype(int))
    assert diff.max() <= 1
    for name in ("layers.json", "joints.json", "source/rig.json", "source/motion.json"):
        assert os.path.exists(out / name), name
    assert res["paths"]["layers"] == str(out / "layers.json")


def test_joints_on_setup_bones_under_template_names():
    ir, images, _ = _character()
    res = ingest_spine.unpack(ir, images)
    j = res["joints"]
    assert j["schema"] == "spinegen-joints/1" and j["template"] == "biped_mascot"
    assert j["bones"]["upperarm_l"] == "upperarm_l" and j["bones"]["shin_r"] == "calf_r"
    assert j["bones"]["hips"] == "hip"
    # helpers driven by the source's own constraints are not mapped and not accessories
    assert not any(a["name"].startswith(("elbow_mid", "knee_mid")) for a in j["accessories"])
    ox, oy = res["layers"]["world"]["origin"]
    ev = lbs.Evaluator(ir)
    w = ev.world_dict(ev.world())
    for joint, bone in (("shoulder_l", "upperarm_l"), ("elbow_r", "forearm_r"), ("knee_l", "calf_l"),
                        ("hip_r", "thigh_r"), ("head", "head")):
        x, y = j["joints"][joint]["xy"]
        assert (x, y) == pytest.approx((w[bone][4] - ox, oy - w[bone][5]), abs=1e-3), joint
    # synth_art has no foot bone: the ankle is the calf's tip
    a, _, c, _, x, y = w["calf_l"]
    L = irmod.by_name(ir["bones"])["calf_l"]["length"]
    assert j["joints"]["ankle_l"]["src"] == "bone_tip:calf_l"
    assert j["joints"]["ankle_l"]["xy"] == pytest.approx([x + a * L - ox, oy - (y + c * L)], abs=1e-3)
    assert j["H"] > 800


def test_tint_hidden_attachment_and_layer_crop():
    ir, images, _ = _character(arms=1, legs=0)
    slot = next(s for s in ir["slots"] if s["name"] == "torso")
    slot["color"] = "ff8040ff"
    # a second, hidden attachment on the forearm slot (a prop shown by an animation)
    skin = ir["skins"][0]["attachments"]
    prop = dict(skin["forearm_l"]["forearm_l"], name="prop", path="prop")
    skin["forearm_l"]["prop"] = prop
    images["prop"] = images["forearm_l"].copy()
    res = ingest_spine.unpack(ir, images)
    layers = {L["name"]: L for L in res["layers"]["layers"]}
    assert layers["torso"]["color"] == "ff8040ff"
    assert "hidden" in layers["prop"]["tags"] and "hidden" not in layers["forearm_l"]["tags"]
    assert layers["prop"]["slot"] == "forearm_l"
    # the layer image is untinted art; the tint comes back in recomposite
    assert _max_diff(res, ir, images) <= 1
    # crops are tight: every border row/column of a layer has a drawn pixel
    for name, img in res["images"].items():
        a = img[..., 3] > 0
        assert a[0].any() and a[-1].any() and a[:, 0].any() and a[:, -1].any(), name


def test_monkey_unpacks_exactly(tmp_path):
    skel, at = third_party("monkey")
    res = ingest_spine.unpack(skel, at, str(tmp_path / "monkey_layers"))
    ir = raster.load_rig(skel)
    assert _max_diff(res, ir, raster.load_atlas_images(at)) <= 1
    j = res["joints"]
    assert {"shoulder_l", "elbow_l", "wrist_l", "hip_r", "knee_r", "ankle_r", "head", "pelvis"} <= set(j["joints"])
    assert j["bones"]["upperarm_l"] == "armL" and j["bones"]["foot_r"] == "legR_foot"
    assert {a["name"] for a in j["accessories"]} >= {"cuffL", "cuffR", "pantL", "pantR", "prop"}
    hidden = [L["name"] for L in res["layers"]["layers"] if "hidden" in L["tags"]]
    assert hidden == ["sack"]
    with open(tmp_path / "monkey_layers" / "layers.json") as f:
        assert len(json.load(f)["layers"]) == 13
