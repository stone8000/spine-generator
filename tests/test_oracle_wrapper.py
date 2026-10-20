"""The oracle plumbing that must work without Node: skipping and timing."""
import struct

from spinegen import oracle, oracle_check, playback, synth
from spinegen.spine42_reader import read


def test_missing_oracle_is_reported_not_raised(monkeypatch, tmp_path):
    monkeypatch.setenv("SPINEGEN_QA_RUN", str(tmp_path / "nowhere" / "run.mjs"))
    ok, why = oracle.available()
    assert not ok and why
    assert oracle_check.main(["x.json", "x.atlas"]) == 2


def test_track_time_is_the_runtime_float_sum():
    # the dump steps dt = k/fps - (k-1)/fps; AnimationState adds them up
    acc = 0.0
    for k in range(1, 7):
        acc += k / 30 - (k - 1) / 30
    assert oracle_check.track_time(0.2, 30) == acc
    assert oracle_check.track_time(0.0, 60) == 0.0
    off = oracle_check.track_time(0.255, 60)  # off grid: 15 steps, then one short one
    assert abs(off - 0.255) < 1e-12


def test_key_times_are_float32_like_the_runtime():
    # spine-core keeps frames in a Float32Array: a key at 0.2 sits at 0.20000000298,
    # so at t = 0.2 a timeline whose first key is 0.2 still shows setup
    assert struct.unpack("f", struct.pack("f", 0.2))[0] > 0.2
    ir = read(synth.features()[0], "f")
    assert "scale_y" not in playback.sample(ir, "swing", 0.2)["bones"].get("tip", {})
    assert playback.sample(ir, "swing", 0.2001)["bones"]["tip"]["scale_y"] == 1.1


def test_c1_counts_bone_origins_and_matrices_not_only_vertices():
    # a bone 143 px off with no attachment on it must still fail C1
    rep = {"max_px": 0.0, "bone_px": 143.2, "bone_m": 0.0, "mismatch": [], "worst": None, "frames": 1}
    assert oracle_check.c1_result(rep, 0.1)["pass"] is False
    rep.update(bone_px=0.0, bone_m=0.01)  # a 0.01 matrix error is 1 px at 100 px from the bone
    r = oracle_check.c1_result(rep, 0.1)
    assert r["pass"] is False and abs(r["value"] - 1.0) < 1e-12
    rep.update(bone_m=1e-8)
    assert oracle_check.c1_result(rep, 0.1)["pass"] is True
