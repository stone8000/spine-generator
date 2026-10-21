"""Colors the dumped synthetic layers into PNGs and draws the reference sheets for the image-agent brief.

usage: python scripts/render_samples.py <dump-dir> <examples/layers dir> <docs dir>
"""
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

dump, layers_out, docs_out = sys.argv[1], sys.argv[2], sys.argv[3]
man = json.load(open(os.path.join(dump, "manifest.json"), encoding="utf-8"))
W, H = man["canvas"]["width"], man["canvas"]["height"]
order = [p["name"] for p in man["parts"]]

SKIN = (232, 182, 146)
COLORS = {
    "hair_back": (58, 42, 31), "hair_front": (78, 56, 40),
    "head": SKIN, "neck": (214, 164, 130),
    "torso": (70, 104, 176), "pelvis": (52, 56, 68),
    "arm_front_upper": SKIN, "arm_front_lower": SKIN, "fist_front": (240, 234, 222),
    "arm_back_upper": (196, 150, 118), "arm_back_lower": (196, 150, 118), "fist_back": (206, 200, 190),
    "leg_front_upper": (62, 66, 80), "leg_front_lower": (62, 66, 80),
    "leg_back_upper": (44, 47, 58), "leg_back_lower": (44, 47, 58),
}

try:
    FONT = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc", 26)
    FONT_S = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc", 20)
except OSError:
    FONT = FONT_S = ImageFont.load_default()


def layer_png(alpha_bytes, color):
    a = Image.frombytes("L", (W, H), alpha_bytes)
    inner = a.filter(ImageFilter.MinFilter(7))
    rgb = Image.new("RGB", (W, H), color)
    outline = Image.new("RGB", (W, H), tuple(int(c * 0.45) for c in color))
    rgb.paste(outline, (0, 0), Image.eval(a, lambda v: 255 if v else 0).point(lambda v: v).convert("L"))
    rgb.paste(Image.new("RGB", (W, H), color), (0, 0), inner)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


composites = {}
for kind, names in man["sets"].items():
    d = os.path.join(layers_out, kind)
    os.makedirs(d, exist_ok=True)
    imgs = {}
    for n in names:
        data = open(os.path.join(dump, kind, n + ".alpha"), "rb").read()
        img = layer_png(data, COLORS.get(n, (150, 150, 150)))
        img.save(os.path.join(d, n + ".png"), optimize=True)
        imgs[n] = img
    comp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for n in order:
        if n in imgs:
            comp.alpha_composite(imgs[n])
    composites[kind] = (comp, imgs)
    print(kind, len(imgs), "layers ->", d)

os.makedirs(docs_out, exist_ok=True)

# pose reference: composite + numbered joints + legend
comp, imgs = composites["good"]
legend_w = 560
sheet = Image.new("RGB", (W + legend_w, H), (245, 246, 248))
checker = Image.new("RGB", (W, H), (255, 255, 255))
cd = ImageDraw.Draw(checker)
for y in range(0, H, 32):
    for x in range(0, W, 32):
        if (x // 32 + y // 32) % 2:
            cd.rectangle([x, y, x + 31, y + 31], fill=(236, 238, 242))
sheet.paste(checker, (0, 0))
sheet.paste(comp, (0, 0), comp)
dr = ImageDraw.Draw(sheet)
dr.rectangle([0, 0, W - 1, H - 1], outline=(190, 194, 202), width=2)
ground = [j for j in man["joints"] if j["id"] in ("ankleF", "ankleB")]
gy = 1456
dr.line([(60, gy), (W - 60, gy)], fill=(160, 166, 176), width=2)
dr.text((70, gy + 8), "地面（root）", font=FONT_S, fill=(110, 116, 126))
links = [("hip", "waist"), ("waist", "neckBase"), ("neckBase", "headBase"), ("headBase", "headTop"),
         ("shoulderF", "elbowF"), ("elbowF", "wristF"), ("shoulderB", "elbowB"), ("elbowB", "wristB"),
         ("hipF", "kneeF"), ("kneeF", "ankleF"), ("hipB", "kneeB"), ("kneeB", "ankleB"), ("hairRoot", "hairTip")]
J = {j["id"]: j for j in man["joints"]}
for a, b in links:
    dr.line([(J[a]["x"], J[a]["y"]), (J[b]["x"], J[b]["y"])], fill=(224, 72, 80), width=3)
for i, j in enumerate(man["joints"], 1):
    x, y = j["x"], j["y"]
    dr.ellipse([x - 13, y - 13, x + 13, y + 13], fill=(255, 255, 255), outline=(224, 72, 80), width=3)
    tw = dr.textlength(str(i), font=FONT_S)
    dr.text((x - tw / 2, y - 13), str(i), font=FONT_S, fill=(40, 40, 40))
lx = W + 30
dr.text((lx, 40), "姿勢參考（面向右・拳擊架式）", font=FONT, fill=(20, 22, 26))
dr.text((lx, 80), f"畫布 {W} × {H} px，透明背景", font=FONT_S, fill=(90, 96, 106))
dr.text((lx, 108), "腳底踩在灰線上，角色置中", font=FONT_S, fill=(90, 96, 106))
for i, j in enumerate(man["joints"], 1):
    dr.text((lx, 160 + (i - 1) * 34), f"{i:>2}  {j['label']}", font=FONT_S, fill=(30, 32, 36))
dr.text((lx, 160 + len(man["joints"]) * 34 + 20), "紅點是關節：部件要在這些點交接，\n交接處兩邊都要多畫、互相重疊。", font=FONT_S, fill=(150, 40, 46))
sheet.save(os.path.join(docs_out, "pose-reference.png"), optimize=True)

# layer sheet: every layer on its own, with its file name
cols, tw_, th_ = 4, 256, 384
rows = (len(order) + cols - 1) // cols
pad = 54
ls = Image.new("RGB", (cols * (tw_ + 20) + 20, rows * (th_ + pad) + 20), (245, 246, 248))
ld = ImageDraw.Draw(ls)
for k, n in enumerate(order):
    cx, cy = 20 + (k % cols) * (tw_ + 20), 20 + (k // cols) * (th_ + pad)
    cell = checker.resize((tw_, th_))
    if n in imgs:
        cell.paste(imgs[n].resize((tw_, th_), Image.LANCZOS), (0, 0), imgs[n].resize((tw_, th_), Image.LANCZOS))
    ls.paste(cell, (cx, cy))
    ld.rectangle([cx, cy, cx + tw_ - 1, cy + th_ - 1], outline=(190, 194, 202))
    label = next(p["label"] for p in man["parts"] if p["name"] == n)
    ld.text((cx, cy + th_ + 4), f"{n}.png", font=FONT_S, fill=(20, 22, 26))
    ld.text((cx, cy + th_ + 26), label + ("" if n in imgs else "（選用）"), font=FONT_S, fill=(110, 116, 126))
ls.save(os.path.join(docs_out, "layer-sheet.png"), optimize=True)

# what the bad set looks like at rest, for the spec's "common mistakes" section
bad_comp, _ = composites["bad"]
side = Image.new("RGB", (W * 2 + 30, H), (245, 246, 248))
for i, (c, title) in enumerate([(comp, "正確：部件互相重疊、被遮處畫完"), (bad_comp, "錯誤：肩下沒畫、頭在下巴處切平又沒有脖子")]):
    bg = checker.copy()
    bg.paste(c, (0, 0), c)
    side.paste(bg, (i * (W + 30), 0))
    ImageDraw.Draw(side).text((i * (W + 30) + 30, 30), title, font=FONT, fill=(150, 40, 46) if i else (30, 120, 70))
side.resize((side.width // 2, side.height // 2), Image.LANCZOS).save(os.path.join(docs_out, "good-vs-bad.png"), optimize=True)
print("docs images ->", docs_out)
