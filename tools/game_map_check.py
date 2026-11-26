"""Open real game maps from your Gw.dat the way File > Open Game Map does, and build each back
unedited: every one must come out as the map's own file, byte for byte. Reads Gw.dat (or the
editor's untouched copies), writes nothing to it.

    py -3.12 game_map_check.py                 the pre-Searing maps
    py -3.12 game_map_check.py 146 148 449     these map ids
    py -3.12 game_map_check.py all             every map the world pack names a file for

Writes tools\\game_map_check.txt.
"""

import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import kryta, npcs, paths, stock                  # noqa: E402
from kryta_cs.model import MapProject                           # noqa: E402

PRE_SEARING = [145, 146, 147, 148, 160, 161, 162, 163, 164, 165, 166]
OUT = os.path.join(HERE, "game_map_check.txt")


def check(dat, pack, row, lines):
    t = time.time()
    p = stock.open_map(dat, row.file_id, row.map_id, row.name, row.map_type or 1, pack)
    original = stock.read_original(dat, row.file_id)[0].encode()
    blob, _notes = stock.build(p, dat)
    again = MapProject.from_json(json.loads(json.dumps(p.to_json())))
    blob2, _notes = stock.build(again, dat)
    npc_ok = True
    if p.npc_file is not None and os.path.isfile(npcs.placement_path(pack, row.map_id)):
        with open(npcs.placement_path(pack, row.map_id), encoding="utf-8-sig") as fh:
            own = json.load(fh)
        npc_ok = _same(own, npcs.placements_file(again))
    ok = blob == original and blob2 == original and npc_ok
    lines.append(f"{'OK  ' if ok else 'DIFF'} map {row.map_id:>4} {row.name[:28]:<28} file 0x{row.file_id:X}"
                 f"  {p.dim_x}x{p.dim_y}, origin ({p.origin[0]:.0f}, {p.origin[1]:.0f}), "
                 f"{len(p.props)} props, {len(p.flora)} flora areas, {len(p.npcs)} NPCs, "
                 f"{len(original):,} B, {time.time() - t:.1f} s"
                 + ("" if blob == original else "  [first build differs]")
                 + ("" if blob2 == original else "  [build after save differs]")
                 + ("" if npc_ok else "  [NPC file differs]"))
    return ok


def _same(a, b):
    def norm(o):
        if isinstance(o, dict):
            return {k: norm(v) for k, v in o.items()}
        if isinstance(o, list):
            return [norm(v) for v in o]
        if isinstance(o, (int, float)) and not isinstance(o, bool):
            return float(o)
        return o
    return norm(a) == norm(b)


def main(argv):
    repo = paths.REPO_ROOT
    dat = paths.load_prefs().get("archive") or kryta.server_archive(repo) or ""
    pack = kryta.pack_dir(repo)
    cat = kryta.read_catalogue(repo)
    if argv and argv[0] == "all":
        ids = sorted(m for m, r in cat.items() if r.file_id)
    elif argv:
        ids = [int(a) for a in argv]
    else:
        ids = PRE_SEARING
    lines = [f"archive: {dat}", f"pack: {pack}", ""]
    good = bad = 0
    for mid in ids:
        row = cat.get(mid)
        if row is None or not row.file_id:
            lines.append(f"SKIP map {mid}: the pack names no map file for it")
            continue
        try:
            if check(dat, pack, row, lines):
                good += 1
            else:
                bad += 1
        except Exception as exc:                                 # noqa: BLE001
            bad += 1
            lines.append(f"FAIL map {mid} {row.name}: {type(exc).__name__}: {exc}")
            lines.append(traceback.format_exc())
        print(lines[-1].splitlines()[0])
    lines.append("")
    lines.append(f"{good} map(s) round-trip byte for byte, {bad} do not")
    with open(OUT, "w", encoding="ascii", errors="replace") as fh:
        fh.write("\n".join(lines) + "\n")
    print(lines[-1])
    print(f"written to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
