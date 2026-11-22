"""Offline checks for the World Map reader: no Gw.exe, no Gw.dat, no display."""
import os
import struct
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
sys.path.insert(0, TOOL)

from kryta_cs import paths                                # noqa: E402
from kryta_cs import worldmap as wm                       # noqa: E402

paths.bootstrap_rurik()


def _record(campaign=1, world=1, region=7, type_=10, flags=0, name_id=1234,
            rect_a=(768, 512, 1184, 1024), rect_b=(0, 0, 0, 0)):
    rec = bytearray(wm.RECORD_SIZE)
    struct.pack_into("<5I", rec, 0, campaign, world, region, type_, flags)
    struct.pack_into("<2I", rec, 0x18, 1, 8)               # party min/max
    struct.pack_into("<2I", rec, 0x20, 1, 8)               # players min/max
    struct.pack_into("<2I", rec, 0x30, 1, 20)              # level min/max
    struct.pack_into("<4i", rec, wm.FOOTPRINT_A, *rect_a)
    struct.pack_into("<4i", rec, wm.FOOTPRINT_B, *rect_b)
    struct.pack_into("<I", rec, 0x74, name_id)
    return bytes(rec)


def test_parse_row_reads_both_footprints_and_world():
    blob = b"\0" * 8 + _record(rect_b=(448, 928, 960, 1440))
    r = wm.parse_row(blob, 8, 148, "Somewhere")
    assert (r.id, r.name, r.continent, r.world, r.region, r.type) == (148, "Somewhere", 1, 1, 7, 10)
    assert r.rect_a == (768, 512, 1184, 1024) and r.rect_b == (448, 928, 960, 1440)
    assert r.name_id == 1234 and r.has_footprint()


def test_record_layout_is_what_areatable_accepts():
    import areatable
    assert areatable.RECORD_SIZE == wm.RECORD_SIZE
    assert areatable.record_ok(_record(), 0)


def test_rect_ok():
    assert wm.rect_ok((0, 0, 1, 1))
    assert not wm.rect_ok((0, 0, 0, 0))
    assert not wm.rect_ok((5, 5, 5, 9))


def test_row_round_trips_through_the_cache_dict():
    r = wm.parse_row(_record(), 0, 7, "X")
    q = wm.AreaRow.from_dict(r.as_dict())
    assert q.as_dict() == r.as_dict()


def test_free_reasons():
    named = wm.parse_row(_record(), 0, 10, "Ascalon City")
    blank = wm.parse_row(_record(rect_a=(0, 0, 0, 0)), 0, 11, "...")
    dev = wm.parse_row(_record(), 0, 12, "DEV Test Arena (1v1)")
    assert wm.free_reasons(named, set()) == []
    assert wm.free_reasons(blank, set()) == ["unnamed", "no footprint", "not in catalogue"]
    assert wm.free_reasons(blank, {11}) == []                  # the server uses it
    assert "dev/test/pvp name" in wm.free_reasons(dev, set())
    ordered = [r.id for r, _ in wm.free_looking([named, dev, blank], set())]
    assert ordered == [11, 12]


def test_atlas_geometry():
    ix = wm.AtlasIndex(grids={("chunk", 1): (4, 8), ("parchment", 1): (1, 2)},
                       tiles={("chunk", 1): [(1, 1, 99)]}, build=1)
    assert ix.chunk_dims(1) == (2048, 4096)
    assert ix.cells_per_texel("chunk", 1) == 1
    assert ix.cells_per_texel("parchment", 1) == 4
    assert wm.tile_cell_rect(1, 1, 1) == (512, 512, 1024, 1024)
    assert wm.tile_cell_rect(0, 1, 4) == (0, 2048, 2048, 4096)
    back = wm.AtlasIndex.from_dict(ix.as_dict())
    assert back.grids == ix.grids and back.tiles == ix.tiles


def test_display_scale_keeps_worlds_under_the_limit():
    assert wm.display_scale(2048, 4096) == 1
    assert wm.display_scale(8192, 16384) == 4
    assert wm.display_scale(8192, 6144) == 2


def test_stamp_and_exe_lookup(tmp_path):
    dat = tmp_path / "Gw.dat"
    dat.write_bytes(b"x")
    assert wm.default_exe_for(str(dat)) is None
    exe = tmp_path / "Gw.exe"
    exe.write_bytes(b"MZ")
    assert os.path.samefile(wm.default_exe_for(str(dat)), str(exe))
    a = wm.archive_stamp(str(exe), str(dat))
    dat.write_bytes(b"xy")
    assert wm.archive_stamp(str(exe), str(dat)) != a


def test_world_labels():
    assert wm.world_label(0) == "0 - Tyria"
    assert wm.world_label(9) == "9"


def test_kryta_cs_is_ascii():
    root = os.path.join(TOOL, "kryta_cs")
    for folder, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                data = open(os.path.join(folder, f), "rb").read()
                assert all(b < 128 for b in data), f


UPSTREAM = os.path.join(os.path.dirname(TOOL), "Other_Repos_Research", "Rurik-main", "toolkit")


@pytest.mark.skipif(not os.path.isdir(UPSTREAM), reason="no Rurik checkout beside the repo")
def test_vendored_clientscan_is_verbatim():
    for rel in ("clientscan/worldmap.py", "clientscan/areatable.py", "clientscan/consttable.py",
                "clientscan/buildid.py", "clientscan/pinned.py", "clientscan/textrec.py",
                "gwpe.py", "authsrv/gwcrypto.py"):
        ours = open(os.path.join(paths.RURIK_TOOLKIT, rel), "rb").read()
        theirs = open(os.path.join(UPSTREAM, rel), "rb").read()
        assert ours == theirs, rel
