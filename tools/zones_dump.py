"""Copy a map's Zones part, and the list of files it depends on, out of Gw.dat for study.

Pre-Searing's map file (113021) carries an 18 KB Zones part and a ~190-file Zones dependency list;
Kryta-CS's builds carry 42 bytes and no list, and grow no wheat or flowers. This writes, into
tools\\zones_<file id>\\:

- zones_bloated.bin, zones_stripped.bin   the Zones part as stored (both streams of the map)
- zones_deps.txt                           the files the Zones part depends on: file id, the kind
                                           of file the archive holds there, its size, and whether
                                           the Map Browser export has a picture or model of it

These are ArenaNet's bytes from your own Gw.dat: study material only. Do not commit the folder.

    py -3.12 zones_dump.py [map file id, default 113021]
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import paths                                     # noqa: E402

paths.bootstrap_rurik()

import mapchunks                                               # noqa: E402
from archive import Archive, ffna_chunks, file_id_table        # noqa: E402

GWMB = r"D:\ASURA-main\GWMB\FullMapExport"


def exported(fid):
    if not os.path.isdir(GWMB):
        return ""
    names = (f"{fid}.png", f"model_0x{fid:X}_gwmb.json")
    for folder in os.listdir(GWMB):
        for n in names:
            if os.path.isfile(os.path.join(GWMB, folder, n)):
                return f"{folder}/{n}"
    return ""


def main():
    fid = int(sys.argv[1], 0) if len(sys.argv) > 1 else 113021
    dat = paths.load_prefs().get("archive") or None
    out = os.path.join(HERE, f"zones_{fid}")
    os.makedirs(out, exist_ok=True)
    lines = []
    with (Archive(dat) if dat else Archive()) as ar:
        table = file_id_table(ar)
        index = mapchunks.MapIndex(ar)
        head = index.by_row[table[fid]]
        partner = index.partner(head)
        deps = []
        for entry, stream in ((head, "bloated"), (partner, "stripped")):
            if entry is None:
                continue
            data = ar.read(entry)
            for cid, off, size in ffna_chunks(data):
                try:
                    c = mapchunks.decompose(cid)
                except ValueError:
                    continue
                if c.base_id != mapchunks.BASE_ZONES:
                    continue
                blob = bytes(data[off:off + size])
                if c.chunk_type == mapchunks.TYPE_DATA:
                    with open(os.path.join(out, f"zones_{stream}.bin"), "wb") as fh:
                        fh.write(blob)
                    lines.append(f"zones_{stream}.bin: chunk 0x{cid:08X}, {size} bytes")
                elif not deps:
                    deps = mapchunks.decode_dependencies(blob).file_ids
        lines.append(f"{len(deps)} dependency file(s):")
        lines.append("  #   file id          row     kind      bytes   Map Browser export")
        by_row = index.by_row
        for i, d in enumerate(deps):
            row = table.get(d)
            kind, size = "-", 0
            if row is not None:
                entry = by_row.get(row)
                if entry is not None:
                    try:
                        raw = ar.read(entry)
                        size = len(raw)
                        kind = bytes(raw[:4]).decode("latin-1").replace("\x00", ".")
                    except Exception as err:            # one unreadable row should not stop the list
                        kind = f"? {type(err).__name__}"
            lines.append(f"{i:3d}  {d:7d} 0x{d:05X}  {row if row is not None else '-':>7}  {kind:6}  "
                         f"{size:8d}  {exported(d)}")
    with open(os.path.join(out, "zones_deps.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:6]))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
