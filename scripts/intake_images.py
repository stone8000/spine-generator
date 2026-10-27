"""Image side of the delivery intake (Node has no PNG decoder): watch the drop folder, normalize a delivery into a
work folder, and render the preview GIF / joint overlay from what scripts/intake.ts computed.

usage:
  python -I scripts/intake_images.py scan <drop dir>                      → JSON: status empty|new|unchanged|locked
  python -I scripts/intake_images.py lock|unlock <drop dir>
  python -I scripts/intake_images.py mark <drop dir> <fingerprint> <done|error> <work dir> <delivery dir>
  python -I scripts/intake_images.py prepare <delivery dir> <work dir>    → layers/, alpha/, intake.json
  python -I scripts/intake_images.py render <work dir>                    → output/preview.gif, joints_overlay.png, overview.png, images/

Delivered files are data: they are only read, never executed, and never modified in place.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # -I ignores PYTHONIOENCODING; the console may be cp950
STATE = "_狀態.json"
LOCK = "_處理中.lock"
LOCK_HOURS = 3
MAX_TRIES = 2
WORK_HEIGHT = 1536  # deliveries taller than 2048 px are scaled to this height for the pipeline


def now():
    return datetime.datetime.now().astimezone()


def load_state(drop):
    p = os.path.join(drop, STATE)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"processed": {}}


def save_state(drop, s):
    json.dump(s, open(os.path.join(drop, STATE), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def pngs_in(folder):
    return sorted(f for f in os.listdir(folder) if f.lower().endswith(".png") and not f.startswith("_"))


def scan(drop):
    os.makedirs(drop, exist_ok=True)
    # unpack zips once, each into its own folder next to it
    for f in os.listdir(drop):
        if f.lower().endswith(".zip"):
            target = os.path.join(drop, os.path.splitext(f)[0])
            if not os.path.exists(target):
                with zipfile.ZipFile(os.path.join(drop, f)) as z:
                    z.extractall(target)  # zipfile drops absolute paths and '..' components
    # a delivery = a folder holding PNGs; the newest one wins
    best = None
    for d, dirs, files in os.walk(drop):
        dirs[:] = [x for x in dirs if not x.startswith(("_", "."))]
        p = pngs_in(d)
        if not p:
            continue
        newest = max(os.stat(os.path.join(d, f)).st_mtime for f in p)
        if best is None or newest > best[1]:
            best = (d, newest, p)
    if best is None:
        return {"status": "empty"}
    d, newest, files = best
    h = hashlib.sha1()
    for f in files:
        st = os.stat(os.path.join(d, f))
        h.update(f"{f}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8"))
    fp = h.hexdigest()[:16]
    name = os.path.basename(d) if os.path.abspath(d) != os.path.abspath(drop) else "交件_" + datetime.datetime.fromtimestamp(newest).strftime("%Y%m%d-%H%M")
    out = {"delivery": d, "fingerprint": fp, "png": len(files), "name": name}
    lock = os.path.join(drop, LOCK)
    if os.path.exists(lock):
        since = datetime.datetime.fromisoformat(open(lock, encoding="utf-8").read().strip())
        if now() - since < datetime.timedelta(hours=LOCK_HOURS):
            return {"status": "locked", "since": since.isoformat(), **out}
    rec = load_state(drop)["processed"].get(fp)
    if rec and (rec["status"] == "done" or rec.get("tries", 0) >= MAX_TRIES):
        return {"status": "unchanged", "last": rec, **out}
    return {"status": "new", **out}


def mark(drop, fp, status, work, delivery):
    s = load_state(drop)
    rec = s["processed"].get(fp, {"tries": 0})
    rec.update({"status": status, "at": now().isoformat(timespec="seconds"), "work": work, "delivery": delivery, "tries": rec.get("tries", 0) + 1})
    s["processed"][fp] = rec
    save_state(drop, s)


def norm(name):
    return re.sub(r"[\s\-]+", "_", os.path.splitext(name)[0].strip().lower())


def prepare(delivery, work):
    os.makedirs(os.path.join(work, "layers"), exist_ok=True)
    os.makedirs(os.path.join(work, "alpha"), exist_ok=True)
    found, rejected = {}, []
    for d, dirs, files in os.walk(delivery):
        dirs[:] = [x for x in dirs if not x.startswith(("_", "."))]
        for f in sorted(files):
            if not f.lower().endswith(".png") or f.startswith("_"):
                continue
            n = norm(f)
            if n in found:
                rejected.append({"file": f, "why": f"和 {found[n][0]} 檔名重複"})
                continue
            try:
                im = Image.open(os.path.join(d, f))
                im.load()
                found[n] = (f, im.convert("RGBA"))
            except Exception as e:  # unreadable: report, never crash the run
                rejected.append({"file": f, "why": f"讀不到圖片（{type(e).__name__}）"})
    sizes = {}
    for f, im in found.values():
        sizes[im.size] = sizes.get(im.size, 0) + 1
    if not sizes:
        canvas = (1024, 1536)
    else:
        canvas = max(sizes.items(), key=lambda kv: kv[1])[0]
    scale = WORK_HEIGHT / canvas[1] if canvas[1] > 2048 else 1.0
    work_canvas = (round(canvas[0] * scale), round(canvas[1] * scale))
    layers = []
    for n, (f, im) in found.items():
        if im.size != canvas:
            rejected.append({"file": f, "why": f"尺寸 {im.size[0]}×{im.size[1]}，和其他圖層的 {canvas[0]}×{canvas[1]} 不同"})
            continue
        a = im.getchannel("A")
        lo, hi = a.getextrema()
        if hi <= 128:
            rejected.append({"file": f, "why": "整張是透明的"})
            continue
        if lo > 128:
            rejected.append({"file": f, "why": "沒有透明背景"})
            continue
        if scale != 1.0:
            im = im.resize(work_canvas, Image.LANCZOS)
        im.save(os.path.join(work, "layers", n + ".png"))
        open(os.path.join(work, "alpha", n + ".alpha"), "wb").write(im.getchannel("A").tobytes())
        layers.append(n)
    info = {"canvas": {"width": work_canvas[0], "height": work_canvas[1]}, "layers": sorted(layers), "rejected": rejected,
            "source": os.path.abspath(delivery), "deliveredCanvas": {"width": canvas[0], "height": canvas[1]}, "scale": scale}
    json.dump(info, open(os.path.join(work, "intake.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"layers": len(layers), "rejected": len(rejected), "canvas": info["canvas"], "scale": scale}, ensure_ascii=False))


def font(size):
    for p in ("C:/Windows/Fonts/msjhbd.ttc", "C:/Windows/Fonts/msjh.ttc"):
        try:
            return ImageFont.truetype(p, size)
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


def render(work):
    out = os.path.join(work, "output")
    layers_dir = os.path.join(work, "layers")
    rig = json.load(open(os.path.join(out, "rig.json"), encoding="utf-8"))
    order = [s["attachment"] for s in rig["slots"]]
    # images for Spine Import Data, next to rig.json
    img_dir = os.path.join(out, "images")
    os.makedirs(img_dir, exist_ok=True)
    for n in order:
        shutil.copyfile(os.path.join(layers_dir, n + ".png"), os.path.join(img_dir, n + ".png"))

    # joint overlay: the art as delivered, with the joints the rig was built on
    joints = json.load(open(os.path.join(out, "joints.json"), encoding="utf-8"))
    first = Image.open(os.path.join(layers_dir, order[0] + ".png"))
    W, H = first.size
    comp = Image.new("RGBA", (W, H), (226, 229, 234, 255))
    for n in order:
        comp.alpha_composite(Image.open(os.path.join(layers_dir, n + ".png")).convert("RGBA"))
    d = ImageDraw.Draw(comp)
    f = font(max(14, W // 64))
    for i, (jid, p) in enumerate(joints.items(), 1):
        x, y, r = p["x"], p["y"], max(8, W // 90)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 255), outline=(224, 60, 70, 255), width=3)
        label = f"{i} {jid}"
        box = d.textbbox((x + r + 4, y - r), label, font=f)
        d.rectangle([box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2], fill=(255, 255, 255, 230))
        d.text((x + r + 4, y - r), label, font=f, fill=(190, 25, 35, 255))
    comp.convert("RGB").save(os.path.join(out, "joints_overlay.png"))

    # the preview GIF
    Q = 0.5
    spec = json.load(open(os.path.join(out, "frames.json"), encoding="utf-8"))
    GW, GH = spec["width"], spec["height"]
    imgs = {}
    for n in order:
        im = Image.open(os.path.join(layers_dir, n + ".png")).convert("RGBA")
        imgs[n] = im.resize((int(im.width * Q), int(im.height * Q)), Image.LANCZOS).convert("RGBa")
    base = backdrop(GW, GH)
    title, small = font(22), font(15)
    frames = []
    for fr in spec["frames"]:
        frame = base.copy().convert("RGBA")
        gx, gy = fr["ground"]
        shadow = Image.new("L", (GW, GH), 0)
        ImageDraw.Draw(shadow).ellipse([gx - 120, gy - 14, gx + 120, gy + 14], fill=120)
        frame.paste((10, 12, 16, 255), (0, 0), shadow.filter(ImageFilter.GaussianBlur(9)))
        ImageDraw.Draw(frame).line([(0, gy), (GW, gy)], fill=(70, 76, 90, 255), width=1)
        for name, (a, b, c, dd, e, ff) in fr["slots"]:
            det = a * dd - b * c
            inv = (dd / det * Q, -b / det * Q, (-dd * e + b * ff) / det * Q, -c / det * Q, a / det * Q, (c * e - a * ff) / det * Q)
            frame.alpha_composite(imgs[name].transform((GW, GH), Image.AFFINE, inv, resample=Image.BICUBIC).convert("RGBA"))
        dr = ImageDraw.Draw(frame)
        dr.text((18, 14), fr["label"], font=title, fill=(255, 255, 255, 255))
        dr.text((18, GH - 26), "spine-motion-kit · Spine 4.3 runtime · 自動綁骨", font=small, fill=(150, 156, 170, 255))
        frames.append(frame.convert("RGB"))
    sample = Image.new("RGB", (GW * 4, GH * 2))
    for i in range(8):
        sample.paste(frames[i * len(frames) // 8], ((i % 4) * GW, (i // 4) * GH))
    pal = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    gif = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    gif[0].save(os.path.join(out, "preview.gif"), save_all=True, append_images=gif[1:], duration=int(1000 / spec["fps"]), loop=0, disposal=1)

    # every 10th frame on one sheet, for looking at the motion without playing it
    picks = list(range(0, len(frames), 10))
    cols = 8
    rows = (len(picks) + cols - 1) // cols
    sheet = Image.new("RGB", (GW * cols, GH * rows), (20, 20, 24))
    lab = font(18)
    for k, i in enumerate(picks):
        sheet.paste(frames[i], ((k % cols) * GW, (k // cols) * GH))
        ImageDraw.Draw(sheet).text(((k % cols) * GW + 8, (k // cols) * GH + 44), f"#{i}", font=lab, fill=(255, 220, 120))
    sheet.save(os.path.join(out, "overview.png"))
    print(json.dumps({"gif": os.path.join(out, "preview.gif"), "frames": len(frames), "mb": round(os.path.getsize(os.path.join(out, "preview.gif")) / 1e6, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "scan":
        print(json.dumps(scan(rest[0]), ensure_ascii=False))
    elif cmd == "lock":
        open(os.path.join(rest[0], LOCK), "w", encoding="utf-8").write(now().isoformat(timespec="seconds"))
    elif cmd == "unlock":
        p = os.path.join(rest[0], LOCK)
        if os.path.exists(p):
            os.remove(p)
    elif cmd == "mark":
        mark(*rest[:5])
    elif cmd == "prepare":
        prepare(rest[0], rest[1])
    elif cmd == "render":
        render(rest[0])
    else:
        raise SystemExit(__doc__)
