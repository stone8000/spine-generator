"""Synthetic stand34 test character: capsules between the template's default joints, every joint overlapped and
rounded, so the rig, the motions and the seam checks can be exercised without real art.

  python3 scripts/trial/make_stand34_test.py examples/characters/stand34-test/layers
"""
import sys, os
from PIL import Image, ImageDraw

W, H = 1024, 1536
J = {
 'hip': (515, 870), 'waist': (515, 770), 'neckBase': (520, 480), 'headBase': (515, 440), 'headTop': (505, 210),
 'hatBase': (510, 245), 'hatTop': (500, 60), 'glasses': (455, 335),
 'shoulderR': (615, 500), 'elbowR': (690, 650), 'wristR': (590, 585),
 'shoulderL': (425, 505), 'elbowL': (345, 650), 'wristL': (420, 765),
 'hipR': (565, 890), 'kneeR': (622, 1150), 'ankleR': (600, 1400), 'toeR': (680, 1445),
 'hipL': (465, 890), 'kneeL': (402, 1150), 'ankleL': (425, 1405), 'toeL': (335, 1448),
 'tailRoot': (600, 905), 'tail2': (725, 985), 'tail3': (805, 865), 'tailTip': (765, 740),
 'coatRootL': (470, 800), 'coatTipL': (450, 1010), 'coatRootR': (570, 800), 'coatTipR': (605, 1010),
}
INK = (27, 23, 22, 255)

def canvas():
    return Image.new('RGBA', (W, H), (0, 0, 0, 0))

def capsule(d, a, b, r, fill, ext=(0.0, 0.0)):
    ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    a2 = (ax - dx * ext[0], ay - dy * ext[0]); b2 = (bx + dx * ext[1], by + dy * ext[1])
    d.line([a2, b2], fill=fill, width=int(2 * r))
    for p in (a2, b2):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=fill)

def save(out, name, img):
    img.save(os.path.join(out, name + '.png'))

def main(out):
    os.makedirs(out, exist_ok=True)
    def part(name, draw):
        img = canvas(); draw(ImageDraw.Draw(img)); save(out, name, img)
    fur, red, cream, black = (140, 90, 60, 255), (190, 40, 40, 255), (240, 228, 200, 255), (30, 28, 28, 255)
    part('tail_1', lambda d: capsule(d, J['tailRoot'], J['tail2'], 16, fur, (0.15, 0.1)))
    part('tail_2', lambda d: capsule(d, J['tail2'], J['tail3'], 14, fur, (0.1, 0.1)))
    part('tail_3', lambda d: capsule(d, J['tail3'], J['tailTip'], 12, fur, (0.1, 0)))
    part('coat_tail_l', lambda d: d.polygon([(445, 780), (500, 780), (470, 1020), (430, 1010)], fill=red))
    part('coat_tail_r', lambda d: d.polygon([(540, 780), (600, 780), (625, 1015), (585, 1020)], fill=red))
    part('arm_l_upper', lambda d: capsule(d, J['shoulderL'], J['elbowL'], 34, red, (0.15, 0)))
    part('arm_l_lower', lambda d: capsule(d, J['elbowL'], J['wristL'], 30, red, (0, 0.12)))
    part('arm_r_upper', lambda d: capsule(d, J['shoulderR'], J['elbowR'], 34, red, (0.15, 0)))
    part('arm_r_lower', lambda d: capsule(d, J['elbowR'], J['wristR'], 30, red, (0, 0.12)))
    def hand(name, at, mark):
        def f(d):
            x, y = at
            d.ellipse([x - 30, y - 30, x + 30, y + 30], fill=fur)
            if mark == 'point': d.rectangle([x - 75, y - 8, x, y + 8], fill=fur)
            if mark == 'open': d.ellipse([x - 45, y - 45, x + 45, y + 15], fill=fur)
            if mark == 'pinch': d.ellipse([x - 40, y - 50, x - 15, y - 25], fill=fur)
            if mark == 'grip': d.rectangle([x - 40, y - 45, x + 10, y - 20], fill=fur)
            if mark == 'hat':
                d.rectangle([x - 50, y - 160, x + 50, y - 40], fill=black); d.rectangle([x - 75, y - 45, x + 75, y - 25], fill=black)
        part(name, f)
    hand('hand_l_hip', J['wristL'], 'hip'); hand('hand_l_point', J['wristL'], 'point'); hand('hand_l_open', J['wristL'], 'open')
    hand('hand_r_pinch', J['wristR'], 'pinch'); hand('hand_r_open', J['wristR'], 'open'); hand('hand_r_grip', J['wristR'], 'grip'); hand('hand_r_hatheld', J['wristR'], 'hat')
    for s in ('l', 'r'):
        S = s.upper()
        part(f'leg_{s}_upper', lambda d, S=S: capsule(d, J['hip' + S], J['knee' + S], 40, fur, (0.12, 0)))
        part(f'leg_{s}_lower', lambda d, S=S: capsule(d, J['knee' + S], J['ankle' + S], 34, fur, (0, 0.08)))
        part(f'foot_{s}', lambda d, S=S: capsule(d, J['ankle' + S], J['toe' + S], 30, black, (0.25, 0)))
    part('pelvis', lambda d: d.rounded_rectangle([430, 740, 610, 930], 50, fill=fur))
    part('torso', lambda d: (d.rounded_rectangle([410, 450, 630, 800], 70, fill=red), d.polygon([(470, 470), (570, 470), (540, 790), (500, 790)], fill=cream)))
    part('neck', lambda d: capsule(d, (518, 500), (515, 410), 36, fur))
    def head(name, mark):
        def f(d):
            d.ellipse([400, 200, 620, 460], fill=fur); d.ellipse([430, 320, 560, 450], fill=cream)
            e = {'idle': (8, INK), 'blink': (2, INK), 'spiral': (16, (155, 61, 245, 255)), 'grin': (8, INK), 'annoyed': (6, (120, 0, 0, 255))}[mark]
            for ex in (455, 535):
                d.ellipse([ex - e[0], 335 - e[0], ex + e[0], 335 + e[0]], fill=e[1])
            if mark == 'grin': d.chord([450, 380, 540, 440], 0, 180, fill=(255, 255, 255, 255))
        part(name, f)
    for m in ('idle', 'blink', 'spiral', 'grin', 'annoyed'): head('head_' + m, m)
    part('hat', lambda d: (d.rectangle([440, 70, 580, 250], fill=black), d.rectangle([410, 230, 610, 260], fill=black), d.rectangle([440, 205, 580, 230], fill=red)))
    part('glasses', lambda d: (d.ellipse([430, 310, 480, 360], outline=(200, 170, 60, 255), width=6), d.line([(480, 330), (600, 320)], fill=(200, 170, 60, 255), width=3)))

main(sys.argv[1] if len(sys.argv) > 1 else 'examples/characters/stand34-test/layers')
