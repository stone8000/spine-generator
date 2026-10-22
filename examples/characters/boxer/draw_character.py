"""Plays the image agent: draws a new boxer to the delivery spec (docs/生圖交付規格.md) as 16 full-canvas layers.

Proportions and pose are deliberately NOT the template's (bigger head, wider stance, different arm angles), the way a
real GPT/Gemini character would differ, so the studio's joint guessing is actually tested.

usage: python draw_character.py <out dir>
writes <out>/layers/*.png, <out>/composite.png, <out>/true_joints.json
"""
import json
import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageEnhance

OUT = sys.argv[1]
W, H = 1024, 1536
SS = 2  # draw at 2x, downsample for clean edges
OW = 5  # outline width (canvas px)
INK = (38, 28, 30, 255)

# where this character's joints really are (canvas px, y down) — the answer key for the joint guesser
J = {
    "hip": (515, 835), "waist": (518, 705), "neckBase": (522, 440), "headBase": (520, 372), "headTop": (560, 112),
    "shoulderF": (595, 462), "elbowF": (668, 608), "wristF": (788, 505),
    "shoulderB": (452, 470), "elbowB": (515, 618), "wristB": (642, 478),
    "hipF": (552, 835), "kneeF": (622, 1118), "ankleF": (590, 1398),
    "hipB": (478, 835), "kneeB": (452, 1125), "ankleB": (425, 1402),
    "hairRoot": (425, 212), "hairTip": (300, 345),
}

SKIN = (234, 180, 142)
SKIN_B = (205, 152, 118)
TOP = (204, 46, 52)
SHORTS = (38, 78, 168)
SHORTS_B = (30, 60, 132)
GLOVE = (242, 188, 40)
GLOVE_B = (206, 156, 30)
HAIR = (40, 32, 38)
BAND = (214, 40, 46)
SHOE = (236, 236, 242)
SHOE_B = (206, 206, 214)
SOLE = (60, 56, 64)


def P(p):
    return (p[0] * SS, p[1] * SS)


def unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1
    return dx / n, dy / n


def along(a, b, t):
    """Point at distance t (px) from a toward b (negative = behind a)."""
    ux, uy = unit(a, b)
    return (a[0] + ux * t, a[1] + uy * t)


class Part:
    def __init__(self):
        self.ops = []      # silhouette primitives: (kind, args, color)
        self.details = []  # callables(draw) drawn after shading

    def capsule(self, a, b, r1, r2, color):
        self.ops.append(("capsule", (a, b, r1, r2), color))

    def ellipse(self, c, rx, ry, color, angle=0):
        self.ops.append(("ellipse", (c, rx, ry, angle), color))

    def poly(self, pts, color):
        self.ops.append(("poly", (pts,), color))

    def detail(self, fn):
        self.details.append(fn)


def draw_op(d, kind, args, color, grow):
    g = grow
    if kind == "capsule":
        a, b, r1, r2 = args
        ux, uy = unit(a, b)
        nx, ny = -uy, ux
        q = [(a[0] + nx * (r1 + g), a[1] + ny * (r1 + g)), (b[0] + nx * (r2 + g), b[1] + ny * (r2 + g)),
             (b[0] - nx * (r2 + g), b[1] - ny * (r2 + g)), (a[0] - nx * (r1 + g), a[1] - ny * (r1 + g))]
        d.polygon([P(p) for p in q], fill=color)
        for c, r in ((a, r1), (b, r2)):
            d.ellipse([P((c[0] - r - g, c[1] - r - g)), P((c[0] + r + g, c[1] + r + g))], fill=color)
    elif kind == "ellipse":
        c, rx, ry, ang = args
        t = math.radians(ang)
        pts = []
        for i in range(96):
            u = 2 * math.pi * i / 96
            x, y = (rx + g) * math.cos(u), (ry + g) * math.sin(u)
            pts.append(P((c[0] + x * math.cos(t) - y * math.sin(t), c[1] + x * math.sin(t) + y * math.cos(t))))
        d.polygon(pts, fill=color)
    elif kind == "poly":
        (pts,) = args
        d.polygon([P(p) for p in pts], fill=color)
        if g:
            d.line([P(p) for p in pts + [pts[0]]], fill=color, width=int(2 * g * SS), joint="curve")
            for p in pts:
                d.ellipse([P((p[0] - g, p[1] - g)), P((p[0] + g, p[1] + g))], fill=color)


def shifted(mask, dx, dy):
    out = Image.new("L", mask.size, 0)
    out.paste(mask, (int(dx * SS), int(dy * SS)))
    return out


def render(part):
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for kind, args, color in part.ops:
        draw_op(d, kind, args, INK, OW)
    fill_mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(fill_mask)
    for kind, args, color in part.ops:
        draw_op(d, kind, args, color + (255,), 0)
        draw_op(md, kind, args, 255, 0)
    # cel shading: light from the upper right (the side the character faces)
    shade = ImageChops.subtract(fill_mask, shifted(fill_mask, 16, -16))
    light = ImageChops.subtract(fill_mask, shifted(fill_mask, -7, 7))
    img = Image.composite(ImageEnhance.Brightness(img).enhance(0.8), img, shade)
    img = Image.composite(ImageEnhance.Brightness(img).enhance(1.12), img, light)
    d = ImageDraw.Draw(img)
    for fn in part.details:
        fn(d)
    return img.resize((W, H), Image.LANCZOS)


def line(d, pts, w, color=INK):
    d.line([P(p) for p in pts], fill=color, width=int(w * SS), joint="curve")


def dot(d, c, r, color):
    d.ellipse([P((c[0] - r, c[1] - r)), P((c[0] + r, c[1] + r))], fill=color)


parts = {}

# ---- hair_back: spiky mass, painted fully behind the head
p = Part()
p.ellipse((515, 238), 132, 122, HAIR)
for tip, base in [((430, 92), (470, 160)), ((520, 70), (530, 150)), ((610, 88), (580, 150)), ((365, 170), (430, 200)),
                  ((350, 265), (420, 260)), ((380, 345), (430, 310))]:
    ux, uy = unit(base, tip)
    nx, ny = -uy, ux
    p.poly([(base[0] + nx * 45, base[1] + ny * 45), tip, (base[0] - nx * 45, base[1] - ny * 45)], HAIR)
parts["hair_back"] = p

# ---- head: face toward the right, ear, headband, a short neck stub under the jaw
p = Part()
p.ellipse((548, 250), 116, 132, SKIN, angle=-8)
p.poly([(600, 300), (668, 300), (650, 360), (600, 392), (545, 395), (505, 360)], SKIN)  # jaw
p.poly([(660, 240), (690, 282), (660, 292)], SKIN)  # nose
p.capsule((530, 352), (527, 388), 38, 38, SKIN)  # a little below the chin (there is a neck layer)
p.ellipse((488, 268), 24, 32, SKIN)  # ear
p.poly([(430, 190), (667, 178), (670, 212), (428, 226)], BAND)  # headband
def head_details(d):
    line(d, [(488 - 8, 255), (492, 285)], 3)                        # ear fold
    dot(d, (618, 255), 15, (255, 255, 255, 255))                     # eye white
    dot(d, (624, 257), 8, INK)                                       # pupil
    line(d, [(592, 228), (640, 222)], 7)                             # brow
    line(d, [(606, 335), (640, 330)], 5)                             # mouth
    line(d, [(560, 380), (600, 360)], 3, (190, 130, 100, 255))       # jaw line
    line(d, [(440, 198), (660, 188)], 3, (160, 26, 32, 255))         # band stitch
p.detail(head_details)
parts["head"] = p

# ---- hair_front: the two headband tails, sway with physics
p = Part()
root, tip = J["hairRoot"], J["hairTip"]
p.ellipse(root, 26, 22, BAND)
p.capsule(along(root, tip, -10), tip, 18, 9, BAND)
p.capsule(along(root, (tip[0] + 25, tip[1] - 40), -10), (tip[0] + 25, tip[1] - 40), 15, 7, BAND)
parts["hair_front"] = p

# ---- neck
p = Part()
p.capsule((524, 330), (520, 478), 42, 44, SKIN_B)
parts["neck"] = p

# ---- torso: tank top over skin; shoulders and armpits painted although the arms cover them
p = Part()
p.capsule(J["shoulderB"], J["shoulderF"], 52, 54, SKIN)
p.poly([(420, 470), (470, 428), (575, 425), (632, 470), (630, 580), (595, 735), (455, 738), (425, 590)], SKIN)
p.poly([(442, 480), (488, 470), (520, 505), (562, 470), (612, 482), (615, 585), (590, 740), (458, 742), (438, 600)], TOP)
def torso_details(d):
    line(d, [(488, 470), (520, 505), (562, 470)], 4)    # neckline
    line(d, [(530, 600), (585, 590)], 3, (150, 30, 36, 255))  # fold
p.detail(torso_details)
parts["torso"] = p

# ---- pelvis: shorts body, top tucked up into the torso, both hip sockets filled
p = Part()
p.poly([(450, 680), (590, 680), (612, 860), (565, 905), (515, 885), (468, 905), (430, 860)], SHORTS)
def pelvis_details(d):
    d.polygon([P((452, 712)), P((594, 712)), P((597, 738)), P((449, 738))], fill=(240, 240, 245, 255))
    line(d, [(452, 712), (594, 712)], 3)
    line(d, [(449, 738), (597, 738)], 3)
p.detail(pelvis_details)
parts["pelvis"] = p


def leg(prefix, hip, knee, ankle, skin, shorts, shoe, shoe_pts, sole_y):
    up = Part()
    up.capsule(along(hip, knee, -40), knee, 56, 46, skin)
    up.capsule(along(hip, knee, -40), along(hip, knee, 150), 66, 62, shorts)  # shorts leg moves with the thigh
    def up_details(d, a=along(hip, knee, 150)):
        ux, uy = unit(hip, knee)
        nx, ny = -uy, ux
        line(d, [(a[0] + nx * 62, a[1] + ny * 62), (a[0] - nx * 62, a[1] - ny * 62)], 4)
    up.detail(up_details)
    lo = Part()
    lo.capsule(along(knee, ankle, -28), ankle, 46, 36, skin)
    lo.poly(shoe_pts, shoe)
    def lo_details(d):
        xs = [x for x, _ in shoe_pts]
        d.polygon([P((min(xs) + 2, sole_y - 16)), P((max(xs) - 2, sole_y - 16)), P((max(xs) - 2, sole_y)), P((min(xs) + 2, sole_y))], fill=SOLE + (255,))
        line(d, [(min(xs) + 25, sole_y - 48), (min(xs) + 70, sole_y - 54)], 3)  # laces
    lo.detail(lo_details)
    parts[f"leg_{prefix}_upper"] = up
    parts[f"leg_{prefix}_lower"] = lo


leg("front", J["hipF"], J["kneeF"], J["ankleF"], SKIN, SHORTS, SHOE,
    [(548, 1372), (630, 1378), (705, 1420), (710, 1456), (540, 1456)], 1456)
leg("back", J["hipB"], J["kneeB"], J["ankleB"], SKIN_B, SHORTS_B, SHOE_B,
    [(385, 1376), (462, 1380), (530, 1422), (534, 1456), (378, 1456)], 1456)


def arm(prefix, shoulder, elbow, wrist, skin, glove):
    up = Part()
    up.capsule(along(shoulder, elbow, -38), elbow, 42, 34, skin)  # shoulder end reaches into the torso
    lo = Part()
    lo.capsule(along(elbow, wrist, -26), along(wrist, elbow, -14), 34, 29, skin)
    def lo_details(d, w=wrist, e=elbow):
        for t in (8, 26):
            a = along(w, e, t)
            ux, uy = unit(e, w)
            nx, ny = -uy, ux
            line(d, [(a[0] + nx * 30, a[1] + ny * 30), (a[0] - nx * 30, a[1] - ny * 30)], 3, (200, 200, 205, 255))
    lo.detail(lo_details)
    fist = Part()
    c = along(wrist, elbow, -58)
    fist.capsule(along(wrist, elbow, 30), along(wrist, elbow, -8), 36, 40, (245, 245, 248))  # cuff over the forearm
    fist.ellipse(c, 64, 58, glove, angle=math.degrees(math.atan2(*reversed(unit(elbow, wrist)))))
    fist.ellipse(along(c, (c[0], c[1] + 100), 40), 26, 20, glove)  # thumb
    def fist_details(d, c=c, w=wrist, e=elbow):
        ux, uy = unit(e, w)
        nx, ny = -uy, ux
        a = along(w, e, 6)
        line(d, [(a[0] + nx * 40, a[1] + ny * 40), (a[0] - nx * 40, a[1] - ny * 40)], 4)
        line(d, [(c[0] - 10, c[1] - 30), (c[0] + 30, c[1] - 10)], 3, (255, 236, 150, 255))  # shine
    fist.detail(fist_details)
    parts[f"arm_{prefix}_upper"] = up
    parts[f"arm_{prefix}_lower"] = lo
    parts[f"fist_{prefix}"] = fist


arm("back", J["shoulderB"], J["elbowB"], J["wristB"], SKIN_B, GLOVE_B)
arm("front", J["shoulderF"], J["elbowF"], J["wristF"], SKIN, GLOVE)

ORDER = ["hair_back", "arm_back_upper", "arm_back_lower", "fist_back", "leg_back_upper", "leg_back_lower", "pelvis",
         "leg_front_upper", "leg_front_lower", "neck", "torso", "head", "hair_front", "arm_front_upper",
         "arm_front_lower", "fist_front"]

os.makedirs(os.path.join(OUT, "layers"), exist_ok=True)
comp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
for name in ORDER:
    img = render(parts[name])
    img.save(os.path.join(OUT, "layers", name + ".png"), optimize=True)
    comp.alpha_composite(img)
bg = Image.new("RGBA", (W, H), (232, 234, 238, 255))
bg.alpha_composite(comp)
bg.convert("RGB").save(os.path.join(OUT, "composite.png"), optimize=True)
json.dump({k: {"x": v[0], "y": v[1]} for k, v in J.items()}, open(os.path.join(OUT, "true_joints.json"), "w"), indent=1)
print("layers:", len(ORDER), "->", os.path.join(OUT, "layers"))
