"""List every Spine skeleton file (.json / .skel) in a repo with the editor version it was exported from. args: repo_root out_csv"""
import sys, os, json, csv, re

root, out = sys.argv[1], sys.argv[2]
rows = []
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if d not in ("node_modules", ".git")]
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, root).replace("\\", "/")
        low = f.lower()
        ver = None
        kind = None
        if low.endswith(".skel") or low.endswith(".skel.bytes"):
            kind = "binary"
            with open(p, "rb") as fh:
                head = fh.read(64)
            m = re.search(rb"(\d+\.\d+\.\d+[\w.-]*)", head)
            ver = m.group(1).decode() if m else "?"
        elif low.endswith(".json"):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    head = fh.read(400)
                if '"skeleton"' not in head or '"spine"' not in head:
                    continue
                m = re.search(r'"spine"\s*:\s*"([^"]+)"', head)
                kind = "json"
                ver = m.group(1) if m else "?"
            except (UnicodeDecodeError, OSError):
                continue
        if kind:
            atlas = os.path.splitext(p)[0] + ".atlas"
            rows.append({"path": rel, "format": kind, "exported_with": ver,
                         "major_minor": ".".join(ver.split(".")[:2]) if ver and ver != "?" else "?",
                         "has_atlas_next_to_it": os.path.exists(atlas),
                         "size_kb": round(os.path.getsize(p) / 1024, 1)})
rows.sort(key=lambda r: r["path"])
with open(out, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
from collections import Counter
print(len(rows), "skeleton files")
print(Counter(r["major_minor"] for r in rows))
print(Counter(r["path"].split("/")[0] + "/" + r["path"].split("/")[1] for r in rows))
