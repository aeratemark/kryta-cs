"""Which of the file-check reply's file ids does this Gw.dat still have?

    python tools/check_filecheck_ids.py "C:/Program Files (x86)/Guild Wars/Gw.dat"

The login server answers the client's file check with six Gw.dat file ids
(crates/asura-protocol/src/client/filecheck.rs). If the client lacks one, it tries to download it.
This prints whether each id (the reference's, and the ones captured from ArenaNet) is in Gw.dat,
and the newest ids in the same ranges, so the reply can be matched to an updated client.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asura_cs.formats.gwdat import GwDat  # noqa: E402

REFERENCE = [391083, 391092, 344988, 391094, 391095, 391093]
CAPTURED = [391339, 391348, 344988, 391350, 391351, 391349]


def main() -> int:
    if len(sys.argv) < 2 or not Path(sys.argv[1]).is_file():
        print('usage: python tools/check_filecheck_ids.py "C:/path/to/Gw.dat"')
        return 1
    dat = GwDat(sys.argv[1])
    for label, ids in (("reference (what the server sends now)", REFERENCE), ("captured from ArenaNet", CAPTURED)):
        print(label)
        for fid in ids:
            e = dat.entry(fid)
            print(f"  {fid:7}  {'present' if e else 'MISSING'}" + (f"  size {e.size}" if e else ""))
    ids = sorted(dat.by_id)
    print(f"\n{len(ids)} file ids, highest {ids[-1]}")
    for lo, hi in ((344900, 345100), (390900, 400000)):
        near = [i for i in ids if lo <= i < hi]
        print(f"ids in [{lo}, {hi}): {len(near)}; last 30: {near[-30:]}")
    print("highest 40 ids:", ids[-40:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
