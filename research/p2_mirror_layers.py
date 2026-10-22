"""Mirror a layers dir left-right: every layer image flipped, x -> W - x.

    python research/p2_mirror_layers.py <layers dir> -o <mirrored layers dir>

Layer images are flipped horizontally and placed at canvas_width - x - w;
joints.json's joint and accessory positions (and accessory directions) are
mirrored the same way. Names stay: the layer named "left" is now drawn on
the image's right. The source rig link is dropped (its animations and
physics are not mirrored), so build the result with no source.

WHY (P2 review R2): a fold that shows up only when the hips move one way
could be the art (one calf flares, the other does not) or a sign error in
the rig (IK bend, band side). Built from mirrored art, the first moves to
the mirrored shift with the art; the second stays where it was.
"""
import argparse
import json
import os
import shutil
import sys

from PIL import Image


def mirror(src, dst):
    if os.path.exists(dst):
        raise SystemExit(f"{dst} exists")
    shutil.copytree(src, dst)
    with open(os.path.join(src, "layers.json")) as f:
        doc = json.load(f)
    w = int(doc["canvas"][0])
    for lay in doc["layers"]:
        p = os.path.join(dst, "layers", lay["name"] + ".png")
        Image.open(p).transpose(Image.FLIP_LEFT_RIGHT).save(p)
        lay["x"] = w - lay["x"] - lay["w"]
    doc["source"] = None
    with open(os.path.join(dst, "layers.json"), "w") as f:
        json.dump(doc, f)
    with open(os.path.join(src, "joints.json")) as f:
        jd = json.load(f)
    for j in jd["joints"].values():
        j["xy"][0] = w - j["xy"][0]
    for a in jd.get("accessories", []):
        a["xy"][0] = w - a["xy"][0]
        a["dir"][0] = -a["dir"][0]
    with open(os.path.join(dst, "joints.json"), "w") as f:
        json.dump(jd, f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("layers")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    mirror(a.layers, a.out)


if __name__ == "__main__":
    sys.exit(main())
