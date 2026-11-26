"""Survey every map's Zones part (flora) in Gw.dat: how many flora types and areas each has, and
its record 2 in full, which is not decoded yet. Writes tools\\zones_survey.txt (small).

Reads only. Study output from your own Gw.dat; do not commit it.

    py -3.12 zones_survey.py
"""

import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import paths                                     # noqa: E402

paths.bootstrap_rurik()

import mapchunks                                               # noqa: E402
from archive import Archive, ffna_chunks, file_id_table        # noqa: E402

STRIPPED_ZONES = 0x10000003


def records(blob):
    """sig u32, version u8, then {u8 tag, u32 size, body} until tag 0xFF."""
    out = []
    off = 5
    while off + 5 <= len(blob):
        tag = blob[off]
        size = struct.unpack_from("<I", blob, off + 1)[0]
        out.append((tag, blob[off + 5:off + 5 + size]))
        off += 5 + size
        if tag == 0xFF:
            break
    return out, off


def main():
    dat = paths.load_prefs().get("archive") or None
    lines = []
    t0 = time.time()
    with (Archive(dat) if dat else Archive()) as ar:
        table = file_id_table(ar)
        row_to_id = {}
        for fid, row in table.items():
            row_to_id.setdefault(row, fid)
        index = mapchunks.MapIndex(ar)
        print(f"{len(index.pairs)} maps ...")
        for n, (head, partner) in enumerate(index.pairs):
            try:
                data = ar.read(partner)
            except Exception as err:
                lines.append(f"row {head.index}: unreadable ({type(err).__name__})")
                continue
            blob = None
            for cid, off, size in ffna_chunks(data):
                if cid == STRIPPED_ZONES:
                    blob = bytes(data[off:off + size])
                    break
            fid = row_to_id.get(head.index)
            who = f"0x{fid:X}" if fid is not None else f"row{head.index}"
            if blob is None:
                lines.append(f"{who:>9}: no Zones")
                continue
            recs, end = records(blob)
            tags = {t: b for t, b in recs}
            t1 = tags.get(1, b"")
            t3 = tags.get(3, b"")
            types = struct.unpack_from("<I", t1, 0)[0] if len(t1) >= 4 else None
            areas = struct.unpack_from("<I", t3, 0)[0] if len(t3) >= 4 else None
            shape = ",".join(f"{t}:{len(b)}" for t, b in recs)
            lines.append(f"{who:>9}: {len(blob):6d} B  version {blob[4]}  records [{shape}]  "
                         f"types {types}  areas {areas}  end_ok {end == len(blob)}  "
                         f"rec2 {tags.get(2, b'').hex(' ')}")
            if n % 50 == 0:
                print(f"  {n} ...")
    lines.append(f"\n{len(index.pairs)} maps in {time.time() - t0:.0f} s")
    out = os.path.join(HERE, "zones_survey.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
