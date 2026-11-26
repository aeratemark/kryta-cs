"""Where a map's ground textures are, and the unknown per-texture value of each: to find what grows
the client's grass and flowers.

Lakeside's wheat and poppies are not props (the whole pre-Searing map file has 12 grass-clump props),
and they grow in some places and not in others of the same map file (113021 is Ascalon City, Lakeside
County and the rest of pre-Searing). So something about the ground decides. The suspect is the
terrain's tag 5: one 7-bit value per texture slot, a property of the texture file, meaning not found
(third_party/rurik/toolkit/mapdata/terrain.py). This writes, into tools\\terrain_flora_<file id>\\:

- summary.txt    every texture slot: its texture file, its tag-5 value, how many cells use it;
- tag5.png       the map from above, each cell coloured by its texture's tag-5 value, with a legend;
- slots.png      the same, coloured by texture slot (the slot number is in summary.txt);
- textures\\     each slot's texture as GWMB exported it, named slotNN_tag5-VV_<file id>.png,
                 when D:\\ASURA-main\\GWMB\\FullMapExport has it.

Compare tag5.png with where wheat grows in game: if the wheat areas are one colour and bare grass
another, tag 5 is the switch.

    py -3.12 terrain_flora.py [map file id, default 113021] [path\\to\\Gw.dat]

Reads only. Gw.dat comes from the argument, else Kryta-CS's settings (Edit > Preferences), else
Rurik's default.
"""

import os
import shutil
import struct
import sys
import zlib
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import paths                                     # noqa: E402

paths.bootstrap_rurik()

import mapchunks                                               # noqa: E402
from archive import Archive, ffna_chunks, file_id_table        # noqa: E402
from terrain import Terrain                                    # noqa: E402

GWMB = r"D:\ASURA-main\GWMB\FullMapExport"
PALETTE = [
    (230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180),
    (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212), (0, 128, 128), (220, 190, 255),
    (170, 110, 40), (255, 250, 200), (128, 0, 0), (170, 255, 195), (128, 128, 0), (255, 215, 180),
    (0, 0, 128), (128, 128, 128), (255, 255, 255), (0, 0, 0),
]


def write_png(path, width, height, rows):
    """rows: list of bytes, each width*3 RGB."""
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def terrain_dependencies(ar, entry):
    """The Terrain Dependencies file ids, from the map's head row or its partner."""
    rows = {e.index: e for e in ar.entries}
    seen = set()
    while entry is not None and entry.index not in seen:
        seen.add(entry.index)
        data = ar.read(entry)
        for cid, off, size in ffna_chunks(data):
            c = mapchunks.decompose(cid)
            if c.chunk_type == mapchunks.TYPE_DATA or c.base_id != 0x02:
                continue
            return mapchunks.decode_dependencies(bytes(data[off:off + size])).file_ids, f"0x{cid:08X}"
        nxt = mapchunks.next_stream(entry)
        entry = rows.get(nxt) if nxt else None
    return [], None


def gwmb_texture(file_id):
    if not os.path.isdir(GWMB):
        return None
    for folder in os.listdir(GWMB):
        p = os.path.join(GWMB, folder, f"{file_id}.png")
        if os.path.isfile(p):
            return p
    return None


def main():
    file_id = int(sys.argv[1], 0) if len(sys.argv) > 1 else 113021
    dat = sys.argv[2] if len(sys.argv) > 2 else (paths.load_prefs().get("archive") or None)
    out = os.path.join(HERE, f"terrain_flora_{file_id}")
    os.makedirs(os.path.join(out, "textures"), exist_ok=True)
    print(f"Reading map file {file_id} from {dat or 'the default Gw.dat'} ...")
    with (Archive(dat) if dat else Archive()) as ar:
        table = file_id_table(ar)
        row = table.get(file_id)
        if row is None:
            print(f"No file id {file_id} in the archive.")
            return 1
        trn = Terrain.load(file_id, archive=ar, table=table)
        head = next(e for e in ar.entries if e.index == row)
        deps, deps_chunk = terrain_dependencies(ar, head)

    shift = 1 if (trn.tag3b is not None and len(deps) == len(trn.table_b) + 1) else 0
    n = len(trn.table_b)
    used = Counter(trn.tiles)
    lines = [f"Map file {file_id}: terrain {trn.dim_x} x {trn.dim_y} cells, {n} texture slots",
             f"Terrain dependencies: {len(deps)} file(s) from chunk {deps_chunk}"
             + (", the first is the extra one (tag 3' present), so slot i is dependency i+1" if shift else ""),
             "",
             "slot  group(tag4)  tag5 (hex, binary)       cells    texture file id",
             "----  -----------  ----------------------  -------  ---------------"]
    for slot in range(n):
        v = trn.table_b[slot]
        tex = deps[slot + shift] if slot + shift < len(deps) else None
        lines.append(f"{slot:4d}  {trn.table_a[slot]:11d}  0x{v:02X}  {v:08b}       {used.get(slot, 0):7d}  "
                     f"{tex if tex is not None else '-'}")
        if tex is not None:
            src = gwmb_texture(tex)
            if src:
                shutil.copyfile(src, os.path.join(out, "textures", f"slot{slot:02d}_tag5-{v:02X}_{tex}.png"))
    values = sorted(set(trn.table_b))
    colour = {v: PALETTE[i % len(PALETTE)] for i, v in enumerate(values)}
    lines += ["", "tag5.png colours:"]
    for v in values:
        cells = sum(c for s, c in used.items() if s < n and trn.table_b[s] == v)
        lines.append(f"  0x{v:02X}  {v:08b}  rgb{colour[v]}  {cells} cells")
    lines += ["", "Both pictures are the map from above, one pixel per cell (96 world units); row 0 at the top",
              "is the terrain's first row. If the picture looks mirrored against the in-game map, it is: flip",
              "it in any image viewer before comparing."]

    w, h = trn.dim_x, trn.dim_y
    rows5, rows_slot = [], []
    for gy in range(h):
        r5 = bytearray()
        rs = bytearray()
        for gx in range(w):
            s = trn.tile_at(gx, gy)
            v = trn.table_b[s] if s < n else 0
            r5 += bytes(colour.get(v, (0, 0, 0)))
            rs += bytes(PALETTE[s % len(PALETTE)])
        rows5.append(bytes(r5))
        rows_slot.append(bytes(rs))
    write_png(os.path.join(out, "tag5.png"), w, h, rows5)
    write_png(os.path.join(out, "slots.png"), w, h, rows_slot)
    with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
