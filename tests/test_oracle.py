"""Against the official runtime (`node qa/run.mjs dump`, spine-core 4.2.120).

Skips unless qa/ is built (npm ci in qa/) or SPINEGEN_QA_RUN points at a
run.mjs with the same interface.

  C1 ORACLE    numpy LBS within 0.1 px of spine-core (SOTA §5)
  P1 (6)       a third-party rig read into the IR and re-emitted draws the
               same triangles at the same world vertices in spine-core,
               <= 0.01 px (spinegen.reemit_check), and the emitted file keeps
               only the faults that are content, not format
"""
import json
import re

import pytest

from conftest import need_oracle, third_party

from spinegen import oracle, oracle_check, reemit_check, synth

C1_TOL = 0.1
REEMIT_TOL = 0.01
RIGS = ["warden", "prisoner", "monkey"]


@pytest.fixture(autouse=True)
def _oracle():
    need_oracle()


@pytest.mark.parametrize("name", RIGS)
def test_c1_setup_pose(name):
    js, at = third_party(name)
    rep, n, _ = oracle_check.run(js, at)
    assert n == 1 and not rep["mismatch"]
    assert rep["max_px"] <= C1_TOL, rep["worst"]


@pytest.mark.parametrize("anim,times", [("idle", [0, 0.3, 0.7, 1.0, 1.4, 2.0]),
                                        ("react", [0, 0.1, 0.333, 0.433, 0.667, 1.0])])
def test_c1_warden_animation_without_physics(anim, times):
    # SOTA P1 (5): >= 5 times of idle and react (react on its beat boundaries), physics none
    js, at = third_party("warden")
    rep, n, _ = oracle_check.run(js, at, anim=anim, times=times)
    assert n == len(times) and not rep["mismatch"]
    assert rep["max_px"] <= C1_TOL, rep["worst"]


@pytest.mark.parametrize("anim,times", [(None, None), ("pose_reach", [0]), ("pose_fold", [0]),
                                        ("pose_wrap", [0]), ("swing", None)])
def test_c1_synthetic_features(synth_features, anim, times):
    """Every inherit mode, IK flavour and transform-constraint variant, deform,
    linked mesh and stripped region, against the runtime; and the places
    where the runtime is faithful rather than exact (local absolute blend
    past 180 deg, applied values re-derived after world edits and physics
    resets, non-uniform two-bone IK in and out of reach), plus G1 parity on a
    mesh mirrored at setup."""
    js, at = synth_features
    rep, n, warnings = oracle_check.run(js, at, anim=anim, times=times, fps=30 if anim == "swing" else None,
                                        folds=True)
    assert not rep["mismatch"], rep["mismatch"]
    assert rep["skipped"] == []  # the point attachment (muzzle) is compared too
    assert rep["max_px"] <= C1_TOL, rep["worst"]
    assert rep["bone_px"] <= C1_TOL and rep["bone_m"] <= 1e-4
    assert rep["folds"] == []
    assert not [w for w in warnings if "path" not in w]  # only the (mix 0) path constraint is not modelled
    assert n == (31 if anim == "swing" else 1)


@pytest.mark.parametrize("rig", ["order_gap", "order_dup"])
def test_c1_constraints_42_never_runs(tmp_path, rig):
    """A transform constraint in an order gap or sharing an order never runs
    in 4.2; lbs must skip it too (bones included: the constrained bone b)."""
    js, at = synth.write(str(tmp_path), rig)
    for anim in (None, "move"):
        rep, n, _ = oracle_check.run(js, at, anim=anim, fps=30)
        assert oracle_check.c1_value(rep) <= C1_TOL and not rep["mismatch"], (anim, rep)


@pytest.mark.parametrize("rig", ["order_gap", "order_dup"])
def test_reemit_keeps_a_dead_constraint_dead(tmp_path, rig):
    js, at = synth.write(str(tmp_path / "src"), rig)
    out, _, report = reemit_check.emit_copy(js, str(tmp_path / "re"))
    assert [i["name"] for i in report["inert"]] == ["tc"] and report["inert"][0]["timelines_dropped"] == 1
    assert any("never runs in 4.2" in w for w in report["reader_warnings"])
    tot = reemit_check.summary(reemit_check.world(js, out, at, {}, 30, "none"))
    assert tot["mismatch"] == [] and tot["max_px"] <= REEMIT_TOL and tot["bone_px"] <= REEMIT_TOL
    _, emitted = oracle.check(out, at)
    gates = {g["id"]: g for g in emitted["gates"]}
    assert gates["L3_ORDER"]["pass"] and gates["C1_ORACLE"]["pass"]


def test_reemit_of_an_ignored_41_key_plays_the_same(tmp_path):
    """A bone with 4.1's `transform` key plays as normal in 4.2; the re-emitted
    file must not turn it into a live inherit."""
    skel, atlas = synth.arm()
    skel["bones"][2]["rotation"] = 30
    skel["bones"][1].update(rotation=60, scaleX=2, scaleY=0.5)
    skel["bones"][3]["transform"] = "noScale"
    js, at = tmp_path / "old.json", tmp_path / "old.atlas"
    js.write_text(json.dumps(skel))
    at.write_text(atlas)
    (tmp_path / "synth.png").write_bytes(synth.blank_png(512, 256))
    assert reemit_check.main([str(js), str(at), "--fps", "30"]) == 0
    assert reemit_check.main([str(js), str(at), "--fps", "30", "--upgrade-legacy"]) == 1  # opted-in change


def test_dump_point_attachment_is_its_world_position(synth_features):
    # spine-core 4.2's PointAttachment extends VertexAttachment; the dump must
    # still give [x, y], not the empty vertex list of a vertex attachment
    js, at = synth_features
    v = oracle.dump(js, at)["frames"][0]["slots"]["muzzle"]
    assert v["attachment"] == "muzzle" and len(v["vertices"]) == 2


@pytest.mark.parametrize("fps", [30, 60, 144])
def test_dump_track_time_is_what_python_reconstructs(synth_arm, fps):
    js, at = synth_arm
    frames = oracle.dump(js, at, anim="bend", fps=fps, physics="none")["frames"]
    assert len(frames) > 1 and all("trackTime" in f for f in frames)
    assert [f["trackTime"] for f in frames] == [oracle_check.track_time(f["t"], fps) for f in frames]
    off = oracle.dump(js, at, anim="bend", times=[0.123], fps=fps, physics="none")["frames"][0]
    assert off["trackTime"] == oracle_check.track_time(0.123, fps)


def test_c1_explicit_pose_and_fold_parity():
    """Any local pose, not just animations: forced chest +150 folds 83 triangles
    in spine-core (SOTA P1 (4)); numpy must see the same vertices and folds."""
    js, at = third_party("warden")
    pose = {"chest": {"rotation": 150}, "arm_l": {"rotation": -60, "scaleX": 1.2}, "neck": {"shearY": 15}}
    rep, n, _ = oracle_check.run(js, at, pose=pose, folds=True)
    assert rep["max_px"] <= C1_TOL and not rep["mismatch"]
    assert rep["folds"] == []
    runtime = oracle.dump(js, at, pose={"chest": {"rotation": 150}}, folds=True)["frames"][0]["folds"]
    assert runtime == 83


def test_c1_pose_set_on_animation_and_constraints_off(synth_features):
    js, at = synth_features
    rep, n, _ = oracle_check.run(js, at, anim="swing", times=[0.2, 0.31, 0.7], pose_mode="set",
                                 pose={"fore": {"rotation": 150}, "p": {"shearX": 20}}, folds=True)
    assert n == 3 and rep["max_px"] <= C1_TOL and not rep["mismatch"] and rep["folds"] == []
    rep, n, _ = oracle_check.run(js, at, anim="pose_reach", times=[0], constraints=False)
    assert rep["max_px"] <= C1_TOL and rep["bone_m"] <= 1e-4


def test_oracle_check_cli_and_check_merge(synth_arm, tmp_path):
    js, at = synth_arm
    assert oracle_check.main([js, at, "--anim", "bend", "--fps", "30"]) == 0
    c1 = tmp_path / "c1.json"
    assert oracle_check.main([js, at, "--folds", "--out", str(c1)]) == 0
    code, report = oracle.check(js, at, c1=c1)
    gate = next(g for g in report["gates"] if g["id"] == "C1_ORACLE")
    assert gate["pass"] is True and gate["value"] <= C1_TOL


def test_check_measures_c1_itself(synth_features):
    """Without --c1, `qa/run.mjs check` runs oracle_check --all (setup and every
    animation): C1 is a must-pass gate and is never skipped."""
    js, at = synth_features
    code, report = oracle.check(js, at)
    gate = next(g for g in report["gates"] if g["id"] == "C1_ORACLE")
    assert gate["pass"] is True and not gate.get("skipped") and gate["value"] <= C1_TOL
    assert "over 65 frame(s)" in gate["note"]  # setup + swing (61 at 60 fps) + 3 single-key poses


@pytest.mark.parametrize("name", RIGS)
def test_reemitted_rig_draws_the_same(tmp_path, name):
    """SOTA P1 (6): setup + every animation on the 30 fps grid (physics none,
    as the acceptance row asks) and on the 60 fps grid with physics on, per
    vertex through the hull permutation; triangles and UVs correspond one to
    one in the two files."""
    js, at = third_party(name)
    out, _, report = reemit_check.emit_copy(js, str(tmp_path))
    perms = reemit_check.perm_table(report)
    topo = reemit_check.topology(js, out, perms)
    assert topo["problems"] == [] and topo["uv_max"] <= 5e-7
    slot_perms = reemit_check._slot_perms(out, perms, None)
    for fps, physics in ((30, "none"), (60, "update")):
        tot = reemit_check.summary(reemit_check.world(js, out, at, slot_perms, fps, physics))
        assert tot["mismatch"] == []
        assert tot["max_px"] <= REEMIT_TOL, (fps, physics, tot["worst"])
        assert tot["bone_px"] <= REEMIT_TOL


def test_reemitted_warden_fixes_every_format_fault(tmp_path):
    """The emitted warden passes L1-L9 except two content faults the emitter
    must keep to draw the same: physics limit 60/80 (Hard Time's bug, fixed
    by the P5 rebuild) and the torso's island outside its outline."""
    js, at = third_party("warden")
    out, rig, _ = reemit_check.emit_copy(js, str(tmp_path))
    orig, emitted = reemit_check.gates(js, out, at, rig)
    assert orig["L8_MESH"]["value"] == 20 and orig["L6_PHYSICS_VALID"]["value"] == 8
    for gid in ("L1_VERSION", "L2_COUNTS", "L3_ORDER", "L4_CURVES_NAN", "L5_IK_VALID", "L7_ATLAS",
                "L8_MESH_INFLUENCES", "L9_TC_MIX", "G1_FOLDS", "G3_FLIP", "P3_DETERMINISM", "C1_ORACLE"):
        assert emitted[gid]["pass"], (gid, emitted[gid])
    assert emitted["L2_COUNTS"]["data"]["ir"]["meshes"] == 20
    l6 = emitted["L6_PHYSICS_VALID"]["items"]  # fps, strength, mass are written now
    assert len(l6) == 4 and all(re.fullmatch(r"phys_\w+: limit (60|80) < 1000", i) for i in l6), l6
    assert emitted["L8_MESH"]["items"] == [
        "torso/torso: 6 boundary vertices after the first 90 lie outside the hull polygon "
        "(hull not first, or an island no one-polygon hull can hold)"]


def test_reemitted_prisoner_hole_is_legal(tmp_path):
    js, at = third_party("prisoner")
    out, rig, _ = reemit_check.emit_copy(js, str(tmp_path))
    _, emitted = reemit_check.gates(js, out, at, rig)
    assert emitted["L8_MESH"]["pass"] and "towel/towel (3)" in emitted["L8_MESH"]["note"]


def test_reemit_check_cli(synth_features, tmp_path):
    js, at = synth_features
    assert reemit_check.main([js, at, "--out", str(tmp_path / "re"), "--fps", "30"]) == 0
