"""Summarise a Guild Wars Map Browser map export (map_<id>.json) into a small text file.

The exports run to hundreds of megabytes, too big to hand around; the summary is a few kilobytes:

- the JSON's shape (keys, types, list lengths, a sample of each list's first item), so the format
  can be read without the file;
- every model or file reference found anywhere in it, counted, most used first;
- how often the named flora models (tall grass clumps, ferns, shrubs) are referenced, and where
  the grass clumps stand, when the placement carries a position.

    py -3.12 gwmb_props.py [path\\to\\map_113021.json]

Writes `<the json's name>_summary.txt` beside this script. Reads only; changes nothing.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

DEFAULT = r"D:\ASURA-main\GWMB\FullMapExport\lakeside-preascalon-ashford\map_113021.json"

# Kryta-CS's model_names.json names for plants (kryta_cs/model_names.json).
FLORA = {
    0x117E9: "Tall grass clump", 0x117EA: "Tall grass clump", 0x117EB: "Tall grass clump",
    0x1B7B7: "Fern", 0x1B87B: "Yellow autumn shrub", 0x1B87C: "Yellow autumn shrub (variant)",
    0x1B87D: "Yellow autumn shrub (variant)", 0x1B72A: "Yellow autumn shrub (variant)",
    0x1B7BF: "Flowering bush, red and orange blooms", 0x1B7B2: "Flower planter (stone trough)",
    0x1B6F5: "Stone ramp / bridge, grassy top",
}
GRASS = {0x117E9, 0x117EA, 0x117EB}
REF_KEY = re.compile(r"(model|file|filename|file_id|fileid|hash|mesh)", re.I)
POS_KEYS = (("x", "y", "z"), ("pos_x", "pos_y", "pos_z"))


def as_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.fullmatch(r"(?:0x)?([0-9A-Fa-f]+)", value.strip())
        if m and value.strip().lower().startswith("0x"):
            return int(m.group(1), 16)
        if value.strip().isdigit():
            return int(value.strip())
    return None


def shape(value, depth=0, out=None, path="$"):
    """The JSON's shape, a line per key, lists described by their first item."""
    if out is None:
        out = []
    pad = "  " * depth
    if depth > 6:
        return out
    if isinstance(value, dict):
        out.append(f"{pad}{path}: object with {len(value)} key(s)")
        for i, (k, v) in enumerate(value.items()):
            if i >= 40:
                out.append(f"{pad}  ... {len(value) - 40} more key(s)")
                break
            shape(v, depth + 1, out, f".{k}")
    elif isinstance(value, list):
        out.append(f"{pad}{path}: list of {len(value)}")
        if value:
            shape(value[0], depth + 1, out, "[0]")
    else:
        text = json.dumps(value)
        out.append(f"{pad}{path}: {type(value).__name__} = {text[:80]}")
    return out


def position(d):
    for keys in POS_KEYS:
        if all(isinstance(d.get(k), (int, float)) for k in keys):
            return tuple(d[k] for k in keys)
    for key in ("pos", "position", "translation", "location"):
        p = d.get(key)
        if isinstance(p, dict):
            got = position(p)
            if got:
                return got
        if isinstance(p, list) and len(p) >= 3 and all(isinstance(c, (int, float)) for c in p[:3]):
            return tuple(p[:3])
    return None


def walk(value, refs, by_key, flora_hits, path="$"):
    """Count every model/file reference; note where flora references sit and stand."""
    if isinstance(value, dict):
        here = position(value)
        for k, v in value.items():
            if REF_KEY.search(k):
                fid = as_id(v)
                if fid is not None:
                    refs[fid] += 1
                    by_key[k] += 1
                    if fid in FLORA:
                        flora_hits[fid].append((path + "." + k, here))
            walk(v, refs, by_key, flora_hits, path + "." + k if len(path) < 120 else path)
    elif isinstance(value, list):
        for item in value:
            walk(item, refs, by_key, flora_hits, path + "[]")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if not os.path.isfile(src):
        print(f"Not found: {src}")
        return 1
    print(f"Reading {src} ({os.path.getsize(src) / 1e6:.0f} MB); this takes a minute ...")
    with open(src, encoding="utf-8") as fh:
        doc = json.load(fh)
    lines = [f"Summary of {src}", ""]
    lines.append("== Shape ==")
    lines.extend(shape(doc))
    refs, by_key, flora_hits = Counter(), Counter(), defaultdict(list)
    walk(doc, refs, by_key, flora_hits)
    lines.append("")
    lines.append("== Keys that hold model/file references ==")
    for k, n in by_key.most_common(20):
        lines.append(f"{n:8d}  {k}")
    lines.append("")
    lines.append(f"== Most referenced ids ({len(refs)} distinct, {sum(refs.values())} references) ==")
    for fid, n in refs.most_common(60):
        lines.append(f"{n:8d}  0x{fid:X}  {FLORA.get(fid, '')}")
    lines.append("")
    lines.append("== Flora ==")
    for fid, name in FLORA.items():
        hits = flora_hits.get(fid, [])
        where = Counter(p for p, _ in hits).most_common(3)
        lines.append(f"0x{fid:X}  {name}: {len(hits)} reference(s); found at {where}")
    grass = [pos for fid in GRASS for _, pos in flora_hits.get(fid, []) if pos]
    lines.append("")
    lines.append(f"== Grass clump positions: {len(grass)} with a position ==")
    if grass:
        xs = [p[0] for p in grass]
        ys = [p[1] for p in grass]
        zs = [p[2] for p in grass]
        lines.append(f"x {min(xs):.0f}..{max(xs):.0f}  y {min(ys):.0f}..{max(ys):.0f}  z {min(zs):.0f}..{max(zs):.0f}")
        for p in grass[:40]:
            lines.append(f"  {p[0]:.1f} {p[1]:.1f} {p[2]:.1f}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.path.splitext(os.path.basename(src))[0] + "_summary.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
