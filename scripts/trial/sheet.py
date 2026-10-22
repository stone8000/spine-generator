"""Contact sheet of GIF frames: python sheet.py <gif> <out.png> <first> <last> <step> [cols]"""
import sys
from PIL import Image, ImageDraw, ImageFont
gif, out, a, b, step = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
cols = int(sys.argv[6]) if len(sys.argv) > 6 else 6
im = Image.open(gif)
idx = list(range(a, b + 1, step))
frames = []
for i in idx:
    im.seek(i)
    f = im.convert("RGB")
    frames.append(f.crop((0, 40, f.width, f.height - 30)))
w, h = frames[0].size
rows = (len(frames) + cols - 1) // cols
sheet = Image.new("RGB", (w * cols, h * rows), (20, 20, 24))
try:
    font = ImageFont.truetype("C:/Windows/Fonts/msjhbd.ttc", 18)
except OSError:
    font = ImageFont.load_default()
for k, (i, f) in enumerate(zip(idx, frames)):
    sheet.paste(f, ((k % cols) * w, (k // cols) * h))
    ImageDraw.Draw(sheet).text(((k % cols) * w + 8, (k // cols) * h + 4), f"#{i}", font=font, fill=(255, 220, 120))
sheet.save(out)
