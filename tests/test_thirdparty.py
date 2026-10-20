"""Hard Time and Go Bananas skeletons, read in place (never copied here).

Python-only checks of the reader and emitter on real files; the same files
against the official runtime are in test_oracle.py.
"""
import numpy as np
import pytest

from conftest import strip_source, third_party, unpermute

from spinegen import atlas as atlas_mod
from spinegen import ir as irmod
from spinegen import lbs, playback
from spinegen.emit import spine42
from spinegen.spine42_reader import read

RIGS = ["warden", "prisoner", "monkey"]


def test_warden_counts_and_hull():
    js, _ = third_party("warden")
    a = read(js)
    c = irmod.counts(a)
    assert (c["bones"], c["meshes"], c["ik"], c["physics"]) == (24, 20, 4, 4)
    assert a["source"] == {"format": "spine-json", "spine": "4.2.43", "warnings": []}
    # L8 territory: every Hard Time mesh says hull 0; the emitter puts the outline first
    meshes = [att for *_, att in irmod.iter_attachments(a) if att["type"] == "mesh"]
    assert all(m["hull"] == 0 for m in meshes)
    rep = {}
    out = spine42.emit(a, report=rep)
    assert len(rep["hull"]) == 20
    # the torso mesh carries a separate 6-vertex island: no hull can hold it
    assert len(rep["warnings"]) == 1 and rep["warnings"][0].startswith("default/torso/torso: 2 outlines")
    assert "6 vertices form an island outside it" in rep["warnings"][0]
    assert all(att["hull"] > 0 for s in out["skins"] for atts in s["attachments"].values() for att in atts.values())


def test_prisoner_towel_hole_is_told_from_an_island():
    js, _ = third_party("prisoner")
    rep = {}
    spine42.emit(read(js), report=rep)
    assert rep["warnings"] == ["default/towel/towel: 2 outlines; the hull is the outer one; 3 vertices rim a hole "
                               "inside the hull (legal; the editor fills the hole if the mesh is re-triangulated)"]


def test_monkey_declared_version_is_flagged():
    js, _ = third_party("monkey")
    a = read(js)
    assert a["source"]["spine"] == "4.1.20"
    assert any("physics" in w for w in a["source"]["warnings"])


@pytest.mark.parametrize("name", RIGS)
def test_reemit_reads_back_as_prepared(name):
    js, _ = third_party(name)
    a = read(js)
    out = spine42.emit(a)
    b = read(out, a["name"])
    assert irmod.diff(strip_source(spine42.prepare(a)), strip_source(b), tol=1e-5) == []
    # Byte-stable from the second pass on. The first can move a vertex whose
    # influences agreed to within reader.SAME_PX by ~1e-7, enough to flip a
    # 6th decimal.
    again = spine42.emit(b)
    assert spine42.dumps(spine42.emit(read(again, a["name"]))) == spine42.dumps(again)


@pytest.mark.parametrize("name", RIGS)
def test_reemit_keeps_world_vertices(name):
    """SOTA P1 (6) in numpy: original vs re-emitted, setup and animated, <= 0.01 px."""
    js, at = third_party(name)
    a = read(js)
    rep = {}
    b = read(spine42.emit(a, report=rep), a["name"])
    perms = {h["slot"]: h["perm"] for h in rep["hull"] if h["reordered"]}
    atl = atlas_mod.load(at)
    ev_a, ev_b = lbs.Evaluator(a, atl), lbs.Evaluator(b, atl)
    worst = 0.0
    for anim in [None] + [x["name"] for x in a["animations"]]:
        times = [0.0] if anim is None else np.linspace(0, irmod.duration(playback.find_animation(a, anim)), 7)
        for t in times:
            frames = []
            for ev, ir in ((ev_a, a), (ev_b, b)):
                p = playback.sample(ir, anim, t) if anim else {"bones": {}, "state": {}, "attachments": {},
                                                               "deform": {}}
                frames.append(ev.frame(p["bones"], p["state"], p["attachments"], p["deform"]))
            fa, fb = frames
            for slot, sa in fa["slots"].items():
                vb = fb["slots"][slot]["vertices"]
                if slot in perms and vb:
                    vb = unpermute(vb, perms[slot])
                assert sa["attachment"] == fb["slots"][slot]["attachment"]
                if sa["vertices"]:
                    worst = max(worst, float(np.abs(np.subtract(sa["vertices"], vb)).max()))
    assert worst <= 0.01


def test_warden_folds_match_runtime_probe():
    # research/experiments/spine42/foldtest.mjs on spine-core 4.2.120: 0, 83, 51
    js, _ = third_party("warden")
    a = read(js)
    ev = lbs.Evaluator(a)
    setup = {b["name"]: b for b in a["bones"]}
    assert ev.folds()["folds"] == 0
    assert ev.folds({"chest": {"rotation": setup["chest"]["rotation"] + 150}})["folds"] == 83
    assert ev.folds({"neck": {"rotation": setup["neck"]["rotation"] + 170}})["folds"] == 51
