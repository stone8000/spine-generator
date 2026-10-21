"""Rigid vs joint comparison sheets for P2_REPORT (看圖): the same joint and
direction from two budget contact sheets, cut to a few angles and stacked.

    python research/p2_sheets.py <rigid sheets dir> <joint sheets dir> -o <compare dir>
        [--angles 30,60,90,120] [--every 10]

The inputs are the per-joint sheets `python -m spinegen.budget ... --sheet DIR`
writes (spinegen.budget.contact_sheet: one row per direction, "+" first, one
tile per `every` degrees from 0). Each output <joint>_<pos|neg>.png has a
"rigid" row over a "joint" row, the tiles cut out pixel for pixel (frame
colours, captions and gate overlays as the budget drew them).

WHY a separate step: the budget sheets run 0-150 deg in 16 columns per joint,
too wide to read two rigs side by side; the report argues from a few angles,
and cutting the budget's own tiles (not re-rendering) keeps the comparison
exactly what the gates judged. Nothing here reads third-party art itself.
"""
import argparse
import os

from PIL import Image, ImageDraw

LABEL_W, THUMB, PAD, CAP = 230, 180, 3, 26   # spinegen.budget.contact_sheet's layout
TILE_W = THUMB + 2 * PAD
TILE_H = 3 * THUMB + 4 * PAD + CAP
LEFT = 60                                    # label column of the comparison sheet


def compare(rigid_png, joint_png, angles, every):
    """{"pos": image, "neg": image} for one joint."""
    out = {}
    sheets = {"rigid": Image.open(rigid_png).convert("RGB"), "joint": Image.open(joint_png).convert("RGB")}
    for row, key in enumerate(("pos", "neg")):
        img = Image.new("RGB", (LEFT + len(angles) * TILE_W, 2 * TILE_H), (255, 255, 255))
        for k, (name, sh) in enumerate(sheets.items()):
            if sh.height < (row + 1) * TILE_H:
                raise ValueError(f"{name} sheet has no row {row} ({sh.height} px tall)")
            for c, a in enumerate(angles):
                col = int(round(a / every))
                x0 = LABEL_W + col * TILE_W
                if x0 + TILE_W > sh.width:
                    raise ValueError(f"{name} sheet stops before {a} deg")
                tile = sh.crop((x0, row * TILE_H, x0 + TILE_W, (row + 1) * TILE_H))
                img.paste(tile, (LEFT + c * TILE_W, k * TILE_H))
            ImageDraw.Draw(img).text((4, k * TILE_H + 8), name, fill=(0, 0, 0))
        out[key] = img
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rigid")
    ap.add_argument("joint")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--angles", default="30,60,90,120")
    ap.add_argument("--every", type=float, default=10.0, help="the budget sheets' --sheet-every")
    a = ap.parse_args(argv)
    angles = [float(x) for x in a.angles.split(",")]
    os.makedirs(a.out, exist_ok=True)
    for f in sorted(os.listdir(a.rigid)):
        if not f.endswith(".png") or not os.path.exists(os.path.join(a.joint, f)):
            continue
        for key, img in compare(os.path.join(a.rigid, f), os.path.join(a.joint, f), angles, a.every).items():
            p = os.path.join(a.out, f"{f[:-4]}_{key}.png")
            img.save(p)
            print(p)


if __name__ == "__main__":
    main()
