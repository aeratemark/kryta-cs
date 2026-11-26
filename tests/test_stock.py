"""Game maps (kryta_cs/stock.py): the patch build keeps a map's own bytes and remaps what it edits.

Synthetic maps only, made with Rurik's own encoders: no ArenaNet bytes live in this repo.
tools/game_map_check.bat runs the same round trip on your own Gw.dat's maps."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import npcs, stock, zones                                   # noqa: E402
from kryta_cs.model import FloraArea, MapProject, PlacedProp             # noqa: E402

import mapbuild                                                           # noqa: E402
import mapchunks                                                          # noqa: E402
import mapfile                                                            # noqa: E402
import props as props_mod                                                 # noqa: E402
import stripbuild as sb                                                   # noqa: E402

X0, Y0 = -3072.0, -6144.0          # a game map's world corner: not the editor's (0, 0)
MODELS = [0x1111, 0x2222, 0x3333]


def _flora_type(tid, name):
    return zones.FloraType(tid, name, [2], [8.0], [8.0], [0.5], [0.25], [0], [2], [0.5, 1.0], [4, 4])


def _map(n_props=4):
    props = [props_mod.Prop(i % 3, X0 + 100.0 * i + 0.25, Y0 + 50.0 * i, -10.0 * i, (i, 0, 0), 0x7F,
                            0, [(1, 2), (3, 4)] if i == 0 else [])
             for i in range(n_props)]
    refs4 = [props_mod.PropRef(7, 0), props_mod.PropRef(8, 2)]
    refs6 = [props_mod.PropRef(9, 1), props_mod.PropRef(10, 3)]
    sp = props_mod.StrippedProps(props, refs4, refs6)
    z = zones.Zones(types=[_flora_type(1, "Zones\\Wheat.ini"), _flora_type(2, "Zones\\Flowers.ini")],
                    areas=[zones.Area(1, [(X0, Y0), (X0, Y0 + 96.0), (X0 + 96.0, Y0 + 96.0)]),
                           zones.Area(2, [(X0 + 10, Y0 + 10), (X0 + 10, Y0 + 50), (X0 + 50, Y0 + 50)],
                                      b"\x01\x00\x80")],
                    record2=b"\x00")
    rect = (X0, Y0, X0 + 32 * 96.0, Y0 + 32 * 96.0)
    chunks = [mapfile.Chunk(sb.MAP_PARAMS, mapbuild.encode_map_parameters(rect), mapfile.FORM_OPAQUE),
              mapfile.Chunk(sb.PROPS, sp.encode(), mapfile.FORM_OPAQUE),
              mapfile.Chunk(sb.PROPS_DEPS, mapchunks.encode_dependencies(MODELS), mapfile.FORM_OPAQUE),
              mapfile.Chunk(zones.STRIPPED_ZONES, z.encode(), mapfile.FORM_OPAQUE)]
    return mapfile.MapFile(chunks=chunks).encode()


def _project(blob):
    """What open_map makes of the map, minus the terrain (which the build never reads)."""
    mf = mapfile.MapFile.decode(blob, strict=False)
    p = MapProject.new("Game", 32, 32, map_id=146)
    p.stock, p.origin, p.file_id, p.spawns = True, [X0, Y0], 0x1B97D, []
    sp = props_mod.StrippedProps.decode(mf.find(sb.PROPS).payload())
    for i, q in enumerate(sp.props):
        p.props.append(PlacedProp(file_id=MODELS[q.model], model=q.model, x=q.x - X0, y=q.y - Y0,
                                  z=-q.z, rot=q.rot, scale=q.scale, flags=q.flags,
                                  outline=list(q.outline), grid_locked=False, src=i))
    z = zones.Zones.decode(mf.find(zones.STRIPPED_ZONES).payload())
    names = {t.id: t.short_name for t in z.types}
    for a in z.areas:
        p.flora.append(FloraArea(names[a.type_id], [(x - X0, y - Y0) for x, y in a.points], a.type_id,
                                 "" if a.flags == zones.AREA_FLAGS else a.flags.hex()))
    return p


@pytest.fixture
def game_map(monkeypatch, tmp_path):
    blob = _map()
    dat = tmp_path / "Gw.dat"
    dat.write_bytes(b"")
    monkeypatch.setattr(stock, "read_map", lambda _dat, _fid: mapfile.MapFile.decode(blob, strict=False))
    return blob, str(dat)


def test_an_unedited_game_map_builds_to_its_own_bytes(game_map):
    blob, dat = game_map
    p = _project(blob)
    out, notes = stock.build(p, dat)
    assert out == blob and "nothing changed" in notes[0]
    q = MapProject.from_json(json.loads(json.dumps(p.to_json())))     # saved and opened again
    assert stock.build(q, dat)[0] == blob


def test_editing_props_keeps_and_renumbers_their_references(game_map):
    blob, dat = game_map
    p = _project(blob)
    del p.props[1]                                   # its tag-6 reference goes with it
    p.props.reverse()                                # 3, 2, 0
    p.props[0].x += 5.0
    p.props.append(PlacedProp(file_id=0x4444, x=10.0, y=20.0, z=1.0, grid_locked=False))
    out, _notes = stock.build(p, dat)
    mf = mapfile.MapFile.decode(out, strict=False)
    sp = props_mod.StrippedProps.decode(mf.find(sb.PROPS).payload())
    assert len(sp.props) == 4
    assert sp.props[0].x == X0 + 300.0 + 0.25 + 5.0 and sp.props[3].model == 3
    assert [(r.value, r.prop) for r in sp.refs4] == [(7, 2), (8, 1)]
    assert [(r.value, r.prop) for r in sp.refs6] == [(10, 0)]
    deps = list(mapchunks.decode_dependencies(mf.find(sb.PROPS_DEPS).payload()).file_ids)
    assert deps == MODELS + [0x4444]
    # everything else is the map's own
    assert mf.find(sb.MAP_PARAMS).payload() == mapfile.MapFile.decode(blob, strict=False).find(
        sb.MAP_PARAMS).payload()


def test_flora_areas_keep_their_type_and_flags_and_painted_ones_join(game_map):
    blob, dat = game_map
    p = _project(blob)
    p.flora.pop(0)
    p.flora.append(FloraArea("Wheat", [(0.0, 0.0), (96.0, 0.0), (96.0, 96.0), (0.0, 96.0)]))
    out, _notes = stock.build(p, dat)
    z = zones.Zones.decode(mapfile.MapFile.decode(out, strict=False).find(zones.STRIPPED_ZONES).payload())
    assert [a.type_id for a in z.areas] == [2, 1]
    assert z.areas[0].flags == b"\x01\x00\x80"
    painted = z.areas[1].points
    assert painted[0][0] == X0 and len(painted) == 4
    area = sum(ax * by - bx * ay for (ax, ay), (bx, by) in zip(painted, painted[1:] + painted[:1]))
    assert area < 0                                  # clockwise, as the map's own areas are


def test_an_unknown_flora_type_and_a_moved_map_are_refused(game_map):
    blob, dat = game_map
    p = _project(blob)
    p.flora.append(FloraArea("Cactus", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]))
    with pytest.raises(stock.StockError):
        stock.build(p, dat)
    q = _project(blob)
    q.origin = [0.0, 0.0]
    with pytest.raises(stock.StockError):
        stock.build(q, dat)


def test_a_game_maps_npcs_are_written_in_place_with_their_routes(tmp_path):
    pack = tmp_path / "pack"
    (pack / "npcs" / "placements").mkdir(parents=True)
    (pack / "npcs" / "definitions.json").write_text(json.dumps({"definitions": [
        {"id": "npc-146-1", "definition_index": 1, "file_id": 5, "primary": 1, "level": 5, "team": "nonc"}]}))
    (pack / "npcs" / "behaviors.lua").write_text("return {}\n")
    own = {"map_id": 146, "note": "the pack's own", "placements": [
        {"id": "146-1", "definition": "npc-146-1", "x": -6505.0, "y": 1624.5, "plane": 0,
         "direction": [0.0, 1.0], "effects": 0, "h0023": 1.0, "name": [3933, 50198, 1],
         "route": "road-1"}],
        "routes": [{"id": "road-1", "mode": "loop", "points": []}]}
    path = pack / "npcs" / "placements" / "146.json"
    path.write_text(json.dumps(own))
    p = MapProject.new("Game", 32, 32, map_id=146)
    p.stock, p.origin = True, [X0, Y0]
    p.npcs = npcs.npcs_from_rows(own["placements"], p.origin)
    p.npc_file = {k: v for k, v in own.items() if k != "placements"}
    assert p.npcs[0].x == -6505.0 - X0
    assert abs(p.npcs[0].angle - 1.5707963) < 1e-6
    npcs.publish_stock(str(pack), p)
    assert json.loads(path.read_text()) == own       # untouched: the same file, encoded name and all
    p.npcs[0].x += 10.0
    p.npcs[0].angle = 0.0                            # turned: an angle replaces the direction
    npcs.publish_stock(str(pack), p)
    row = json.loads(path.read_text())["placements"][0]
    assert row["x"] == -6495.0 and row["angle"] == 0.0 and "direction" not in row
    assert row["name"] == [3933, 50198, 1] and json.loads(path.read_text())["routes"] == own["routes"]


def test_the_base_is_the_saved_original_then_the_backup_then_gwdat(monkeypatch, tmp_path):
    blob = _map()
    dat = tmp_path / "Gw.dat"
    dat.write_bytes(b"")
    (tmp_path / "Gw.dat.kryta-original").write_bytes(b"")
    seen = []

    def read(path, _fid):
        seen.append(os.path.basename(path))
        if path.endswith(".kryta-original"):          # the backup predates this map's download
            raise stock._NotThere("not there")
        return mapfile.MapFile.decode(blob, strict=False)

    monkeypatch.setattr(stock, "read_map", read)
    mf, src = stock.read_original(str(dat), 0x3A58)
    assert src == "Gw.dat" and seen == ["Gw.dat.kryta-original", "Gw.dat"]
    saved = stock.save_original(str(dat), 0x3A58)
    assert os.path.basename(saved) == "game-0x3A58-original.bin"
    monkeypatch.setattr(stock, "read_map", lambda *_a: pytest.fail("the saved copy comes first"))
    mf, src = stock.read_original(str(dat), 0x3A58)
    assert src == "game-0x3A58-original.bin" and mf.encode() == blob

    monkeypatch.setattr(stock, "read_map", lambda *_a: (_ for _ in ()).throw(stock._NotThere("no")))
    with pytest.raises(stock.StockError, match="not in"):
        stock.read_original(str(dat), 0x1234)
