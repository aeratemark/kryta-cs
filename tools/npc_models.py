"""Read every NPC type's model files out of Gw.dat, to see what the editor can draw for an NPC.

An NPC type (the world pack's npcs/definitions.json) names a model file (`file_id`, the 0x0056
packet's) and up to eight body files (`model_files`, the 0x0057 packet's). A composited character's
model file is its skeleton and animations with no geometry (skelfile.py); its bodies carry the
meshes. This writes, into tools\\npc_models\\:

- summary.txt       per type: each file, whether it has geometry, sub-models, vertices, triangles,
                    the bounding box, and whether its vertices are skinned
- <file id>.json    each mesh's vertex positions and triangles, for a preview render

These are shapes from your own Gw.dat: study material only. Do not commit the folder.

    py -3.12 npc_models.py
"""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import kryta, paths                              # noqa: E402

paths.bootstrap_rurik()

import modelfile                                               # noqa: E402
from archive import Archive, file_id_table                     # noqa: E402

OUT = os.path.join(HERE, "npc_models")


def describe(ar, table, fid, written):
    row = table.get(int(fid))
    if row is None:
        return f"0x{fid:X}: no row in this archive"
    try:
        data = ar.read(ar.row(row))
    except Exception as exc:                                     # noqa: BLE001
        return f"0x{fid:X}: unreadable ({type(exc).__name__}: {exc})"
    try:
        mf = modelfile.ModelFile.decode(data)
    except Exception as exc:                                     # noqa: BLE001
        return f"0x{fid:X}: {len(data):,} B, not a model ({type(exc).__name__}: {exc})"
    has_skel = mf.find(modelfile.SKELETON_CHUNK) is not None
    try:
        geo = mf.geometry()
    except Exception as exc:                                     # noqa: BLE001
        return (f"0x{fid:X}: {len(data):,} B, skeleton={'yes' if has_skel else 'no'}, "
                f"geometry unreadable ({type(exc).__name__}: {exc})")
    if geo is None:
        return f"0x{fid:X}: {len(data):,} B, skeleton={'yes' if has_skel else 'no'}, no geometry"
    subs, nv, nt, skinned = [], 0, 0, 0
    lo = [1e30] * 3
    hi = [-1e30] * 3
    for sm in geo.submodels:
        pos = sm.positions()
        tris = [t for t in sm.triangles if max(t) < len(pos)]
        nv += len(pos)
        nt += len(tris)
        if sm.groups() is not None:
            skinned += 1
        for p in pos:
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
        subs.append({"positions": [[round(c, 2) for c in p] for p in pos],
                     "triangles": [list(t) for t in tris]})
    if fid not in written:
        with open(os.path.join(OUT, f"{fid}.json"), "w", encoding="ascii") as fh:
            json.dump({"file_id": fid, "submodels": subs}, fh, separators=(",", ":"))
        written.add(fid)
    box = " ".join(f"{a:.0f}..{b:.0f}" for a, b in zip(lo, hi)) if nv else "-"
    return (f"0x{fid:X}: {len(data):,} B, skeleton={'yes' if has_skel else 'no'}, "
            f"{len(geo.submodels)} sub-model(s), {nv:,} vertices, {nt:,} triangles, "
            f"{skinned} skinned, box x y z {box}")


def main():
    dat = paths.load_prefs().get("archive") or None
    pack = kryta.pack_dir(paths.REPO_ROOT)
    with open(os.path.join(pack, "npcs", "definitions.json"), encoding="utf-8-sig") as fh:
        defs = json.load(fh)["definitions"]
    os.makedirs(OUT, exist_ok=True)
    lines, written = [], set()
    with (Archive(dat) if dat else Archive()) as ar:
        table = file_id_table(ar)
        for d in defs:
            lines.append(f"{d['id']}  (level {d.get('level')}, team {d.get('team')})")
            files = [("model", d.get("file_id"))] + [("body", f) for f in d.get("model_files", [])]
            for kind, fid in files:
                if not fid:
                    continue
                try:
                    lines.append(f"    {kind} {describe(ar, table, int(fid), written)}")
                except Exception:                                # noqa: BLE001
                    lines.append(f"    {kind} 0x{int(fid):X}: probe failed\n"
                                 + traceback.format_exc())
    with open(os.path.join(OUT, "summary.txt"), "w", encoding="ascii", errors="replace") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:60]))
    print(f"... {len(defs)} NPC types, {len(written)} meshes written to {OUT}")


if __name__ == "__main__":
    main()
