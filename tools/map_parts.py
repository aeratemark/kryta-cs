"""List the parts (chunks) inside a few map files, with their sizes, side by side.

Pre-Searing's map file grows wheat and flowers; Kryta Test Vale, built from the same textures,
grows none. So the switch is some other part of the map file, and comparing which parts each
file has, and how big they are, shows where to look. Writes tools\\map_parts.txt.

    py -3.12 map_parts.py [file id ...]      default: 113021 (pre-Searing), 0x1B97D (the test
                                              map's donor), 0x5F100 (Kryta Test Vale)
Reads only.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import paths                                     # noqa: E402

paths.bootstrap_rurik()

import mapchunks                                               # noqa: E402
from archive import Archive, ffna_chunks, file_id_table        # noqa: E402

DEFAULT = [("pre-Searing (Lakeside, Ascalon City)", 113021),
           ("test map's donor", 0x1B97D),
           ("Kryta Test Vale / Flora Test", 0x5F100)]


def parts(ar, entry):
    out = {}
    for cid, _off, size in ffna_chunks(ar.read(entry)):
        out[cid] = out.get(cid, 0) + size
    return out


def main():
    maps = [(f"0x{int(a, 0):X}", int(a, 0)) for a in sys.argv[1:]] or DEFAULT
    dat = paths.load_prefs().get("archive") or None
    lines = [f"Gw.dat: {dat or 'default'}", ""]
    columns = []
    with (Archive(dat) if dat else Archive()) as ar:
        table = file_id_table(ar)
        index = mapchunks.MapIndex(ar)
        for label, fid in maps:
            row = table.get(fid)
            if row is None:
                lines.append(f"{label}: file id 0x{fid:X} is not in this Gw.dat")
                columns.append((label, fid, {}))
                continue
            head = index.by_row[row]
            found = parts(ar, head)
            partner = index.partner(head)
            if partner is not None:
                for cid, size in parts(ar, partner).items():
                    found[cid] = found.get(cid, 0) + size
            columns.append((label, fid, found))
    every = sorted({cid for _l, _f, found in columns for cid in found})
    lines.append(f"{'part':38}" + "".join(f"{('0x%X' % fid):>14}" for _l, fid, _p in columns))
    for cid in every:
        try:
            name = mapchunks.chunk_label(cid)
        except Exception:
            name = ""
        row = f"0x{cid:08X} {name:27}"
        for _l, _f, found in columns:
            row += f"{(str(found[cid]) + ' B') if cid in found else '-':>14}"
        lines.append(row)
    lines.append("")
    for label, fid, _p in columns:
        lines.append(f"0x{fid:X} = {label}")
    out = os.path.join(HERE, "map_parts.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
