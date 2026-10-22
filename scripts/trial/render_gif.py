"""Composites the real layer PNGs with the per-frame transforms from render_frames.ts.

usage:
  python render_gif.py dump <layers dir> <alpha dir>                 raw alpha for render_frames.ts
  python render_gif.py gif <layers dir> <frames.json> <out.gif> [--bones] [--stills <dir> <i,j,...>]
"""
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

Q = 0.5  # layers are pre-shrunk by this much before the per-frame transform (cleaner downsampling)


def dump(layers, out):
    os.makedirs(out, exist_ok=True)
    for f in sorted(os.listdir(layers)):
        if f.endswith(".png"):
            a = Image.open(os.path.join(layers, f)).convert("RGBA").getchannel("A")
            open(os.path.join(out, f[:-4] + ".alpha"), "wb").write(a.tobytes())


# a font with Chinese glyphs for the labels: Windows, macOS, Linux (Noto CJK)
FONTS = ["C:/Windows/Fonts/msjhbd.ttc", "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
         "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]


def font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def backdrop(w, h):
    bg = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(bg)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(36 + 10 * t), int(40 + 10 * t), int(50 + 8 * t)))
    return bg


def render(layers, frames_file, out, bones=False, stills=None):
    spec = json.load(open(frames_file, encoding="utf-8"))
    W, H = spec["width"], spec["height"]
    imgs = {}
    for f in os.listdir(layers):
        if f.endswith(".png"):
            im = Image.open(os.path.join(layers, f)).convert("RGBA")
            imgs[f[:-4]] = im.resize((int(im.width * Q), int(im.height * Q)), Image.LANCZOS).convert("RGBa")
    base = backdrop(W, H)
    title, small = font(22), font(15)
    out_frames = []
    for k, fr in enumerate(spec["frames"]):
        frame = base.copy().convert("RGBA")
        gx, gy = fr["ground"]
        shadow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(shadow).ellipse([gx - 120, gy - 14, gx + 120, gy + 14], fill=120)
        frame.paste((10, 12, 16, 255), (0, 0), shadow.filter(ImageFilter.GaussianBlur(9)))
        ImageDraw.Draw(frame).line([(0, gy), (W, gy)], fill=(70, 76, 90, 255), width=1)
        for name, (a, b, c, d, e, f) in fr["slots"]:
            det = a * d - b * c
            # PIL wants output → input: img = M⁻¹·(out − t), in the pre-shrunk image's pixels
            inv = (d / det * Q, -b / det * Q, (-d * e + b * f) / det * Q,
                   -c / det * Q, a / det * Q, (c * e - a * f) / det * Q)
            layer = imgs[name].transform((W, H), Image.AFFINE, inv, resample=Image.BICUBIC).convert("RGBA")
            frame.alpha_composite(layer)
        dr = ImageDraw.Draw(frame)
        if bones:
            for x0, y0, x1, y1 in fr["bones"]:
                dr.line([(x0, y0), (x1, y1)], fill=(80, 220, 255, 255), width=2)
                dr.ellipse([x0 - 3, y0 - 3, x0 + 3, y0 + 3], fill=(80, 220, 255, 255))
        dr.text((18, 14), fr["label"], font=title, fill=(255, 255, 255, 255))
        dr.text((18, H - 26), "spine-motion-kit · Spine 4.3 runtime · 自動綁骨", font=small, fill=(150, 156, 170, 255))
        rgb = frame.convert("RGB")
        out_frames.append(rgb)
        if stills and k in stills[1]:
            os.makedirs(stills[0], exist_ok=True)
            rgb.save(os.path.join(stills[0], f"frame_{k:03d}.png"))
    # one shared palette for every frame, so flat colours don't flicker
    sample = Image.new("RGB", (W * 4, H * 2))
    for i in range(8):
        sample.paste(out_frames[i * len(out_frames) // 8], ((i % 4) * W, (i // 4) * H))
    pal = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    gif = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in out_frames]
    gif[0].save(out, save_all=True, append_images=gif[1:], duration=int(1000 / spec["fps"]), loop=0, disposal=1)
    print(f"{out}: {len(gif)} frames, {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "dump":
        dump(sys.argv[2], sys.argv[3])
    else:
        args = sys.argv[2:]
        bones = "--bones" in args
        stills = None
        if "--stills" in args:
            i = args.index("--stills")
            stills = (args[i + 1], {int(x) for x in args[i + 2].split(",")})
            args = args[:i] + args[i + 3:]
        args = [a for a in args if a != "--bones"]
        render(args[0], args[1], args[2], bones, stills)
