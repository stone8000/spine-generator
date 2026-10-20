"""Shared test helpers.

Third-party skeletons are read from where they live on this machine and
never copied into the repo; tests that need them skip when they are absent.
Synthetic fixtures are generated per test by spinegen.synth.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HARD_TIME = "/home/user/chenyuping1998/wp/apps/HardTime/static/assets/spines/htCast"
THIRD_PARTY = {
    "warden": f"{HARD_TIME}/warden",
    "prisoner": f"{HARD_TIME}/prisoner",
    "monkey": "/home/user/hacksaw-character-motion/gobananas_monkey/spines/bananditBandit/monkey",
}


def third_party(name):
    """(json, atlas) paths of a third-party rig, or skip the test."""
    base = THIRD_PARTY[name]
    if not (os.path.exists(base + ".json") and os.path.exists(base + ".atlas")):
        pytest.skip(f"{name} not on this machine ({base}.json)")
    return base + ".json", base + ".atlas"


def need_oracle():
    from spinegen import oracle
    ok, why = oracle.available()
    if not ok:
        pytest.skip(f"JS oracle unavailable: {why}")


def strip_source(ir):
    """An IR without its provenance block (which records the file version)."""
    return {k: v for k, v in ir.items() if k != "source"}


def unpermute(vertices, perm):
    """Re-emitted flat vertex list -> original vertex order (perm[new] = old)."""
    out = [None] * len(perm)
    for new, old in enumerate(perm):
        out[old] = (vertices[2 * new], vertices[2 * new + 1])
    return [c for xy in out for c in xy]


@pytest.fixture
def synth_features(tmp_path):
    from spinegen import synth
    return synth.write(str(tmp_path), "features")


@pytest.fixture
def synth_arm(tmp_path):
    from spinegen import synth
    return synth.write(str(tmp_path), "arm", "rigid")
