"""Offline checks: no Gw.dat and no Qt display needed.  python -m pytest tests"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import paths, terrain                      # noqa: E402
from kryta_cs.model import MapProject, PlacedProp, _tiled_index       # noqa: E402

paths.bootstrap_rurik()


def test_tiled_index_matches_rurik():
    from terrain import Terrain as RurikTerrain           # rurik toolkit/mapdata/terrain.py
    assert all(RurikTerrain.index(x, y, 96) == _tiled_index(x, y, 96)
               for x in range(96) for y in range(64))


def test_rurik_heights_round_trip():
    p = MapProject.new("rt", 64, 32)
    p.heights = terrain.gen_hills(p)
    terrain.quantize(p, 1.0)
    q = MapProject.new("rt", 64, 32)
    q.from_rurik_heights(p.to_rurik_heights())
    assert q.heights == p.heights


def test_json_round_trip(tmp_path):
    p = MapProject.new("Round Trip", 32, 32)
    p.heights = terrain.gen_crater(p)
    p.tiles = [0] * (32 * 32)
    p.tiles[5] = 7
    p.props = [PlacedProp(file_id=0x1B6B3, model=3, x=100.0, y=200.0, z=30.0,
                          rot=(0, 64, 0), scale=0x7F, grid_locked=False)]
    p.water_level = -42.5
    p.file_id = 0x5F123
    p.path = str(tmp_path / "rt.kmap.json")
    p.save()
    q = MapProject.load(p.path)
    assert q.file_id == 0x5F123 and q.dim_x == 32 and len(q.spawns) == 1
    assert q.tiles[5] == 7 and len(q.tiles) == 32 * 32
    assert len(q.props) == 1 and q.props[0].file_id == 0x1B6B3
    assert q.props[0].rot == (0, 64, 0) and q.props[0].model == 3
    assert q.props[0].grid_locked is False
    assert q.water_level == -42.5
    assert max(abs(a - b) for r1, r2 in zip(p.heights, q.heights) for a, b in zip(r1, r2)) < 0.01


def test_missing_texture_layer_defaults_to_zero(tmp_path):
    p = MapProject.new("Old Format", 32, 32)
    d = p.to_json()
    d.pop("tiles", None)
    q = MapProject.from_json(d)
    assert q.tiles == [0] * (32 * 32)


def test_model_uv_affine_maps_a_triangle():
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QTransform

    from kryta_cs.ui.uvmap import affine
    t = affine([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], [(10.0, 20.0), (40.0, 20.0), (10.0, 90.0)])
    assert isinstance(t, QTransform)
    assert t.map(QPointF(0.0, 0.0)) == QPointF(10.0, 20.0)
    assert t.map(QPointF(1.0, 0.0)) == QPointF(40.0, 20.0)
    assert t.map(QPointF(0.0, 1.0)) == QPointF(10.0, 90.0)
    assert affine([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)], [(0, 0), (1, 1), (2, 2)]) is None


def test_prop_rotation_is_the_client_convention():
    from kryta_cs.model import prop_apply, prop_matrix

    m = prop_matrix((0, 0, 0))
    assert prop_apply(m, 1.0, 0.0, 0.0) == (1.0, 0.0, 0.0)
    assert prop_apply(m, 0.0, 1.0, 0.0) == (0.0, 1.0, 0.0)
    assert prop_apply(m, 0.0, 0.0, 1.0) == (0.0, 0.0, 1.0)
    # rot[2] is the up axis: a quarter turn about z takes +x to -y.
    quarter = prop_matrix((0, 0, 64))
    x, y, z = prop_apply(quarter, 1.0, 0.0, 0.0)
    assert abs(x) < 1e-9 and abs(y + 1.0) < 1e-9 and abs(z) < 1e-9
    # rot[1] tilts about y, the depth axis, so the up vector moves.
    tilt = prop_matrix((0, 64, 0))
    x, y, z = prop_apply(tilt, 0.0, 0.0, 1.0)
    assert abs(x - 1.0) < 1e-9 and abs(y) < 1e-9 and abs(z) < 1e-9


def test_prop_selection_is_exclusive():
    from PySide6.QtWidgets import QApplication

    from kryta_cs.ui.document import Document

    app = QApplication.instance() or QApplication([])
    doc = Document()
    doc.set_project(MapProject.new("Select", 32, 32))
    assert doc.selected == 0 and doc.selected_prop == -1
    doc.select_prop(2)
    assert doc.selected_prop == 2 and doc.selected == -1
    doc.select(0)
    assert doc.selected == 0 and doc.selected_prop == -1
    assert app is not None


def test_every_generator_is_buildable():
    for name, gen in terrain.GENERATORS.items():
        p = MapProject.new(name, 64, 64)
        p.heights = gen(p)
        errors = [f for f in terrain.validate(p) if f.level == "error"]
        assert not errors, (name, [str(f) for f in errors])


# ------------------------------------------------------------------ publishing into the world pack

def _fake_repo(tmp_path):
    """The parts of a Kryta checkout the publish touches: a pack with map 449, the configs."""
    import json
    repo = tmp_path / "kryta"
    folder = repo / "config" / "World" / "Vanilla" / "maps" / "unplaced" / "449"
    folder.mkdir(parents=True)
    (folder / "basic.json").write_text(json.dumps(
        {"map_id": 449, "name": "Kamadan, Jewel of Istan", "map_type": 0, "map_file_id": 214476,
         "instance_value": 1234}))
    (folder / "spawns.json").write_text(json.dumps(
        {"map_id": 449, "spawns": [{"idx": 0, "x": -9067.0, "y": 13218.0, "plane": 0}]}))
    (repo / "worldserver").mkdir()
    (repo / "worldserver" / "worldserver.json").write_text(json.dumps(
        {"WorldPack": {"value": "config/World/Vanilla"}, "PathGraphs": {"value": ""}}))
    (repo / "tools" / "manage").mkdir(parents=True)
    (repo / "tools" / "manage" / "manage.json").write_text(json.dumps({"MapArchiveFile": {"value": ""}}))
    sql = repo / "data" / "sql" / "base" / "db_world"
    sql.mkdir(parents=True)
    (sql / "custom-map-test-vale-20261122000000.sql").write_text("-- old publish\n")
    return str(repo)


def _pack_ids(repo):
    import json
    ids = []
    for here, _d, files in os.walk(os.path.join(repo, "config", "World", "Vanilla", "maps")):
        if "basic.json" in files:
            rec = json.load(open(os.path.join(here, "basic.json")))
            assert os.path.basename(here) == str(rec["map_id"]), "a map's folder is its id (Pack.rs)"
            ids.append(rec["map_id"])
    return ids


def test_publish_takes_the_id_over_and_unpublish_puts_it_back(tmp_path):
    import json

    from kryta_cs import kryta
    from kryta_cs.model import Spawn
    repo = _fake_repo(tmp_path)
    p = MapProject.new("Test Vale", 32, 32, map_id=449, map_type=0)
    p.file_id = 0x5F123
    p.spawns = [Spawn(100.0, 200.0, 0.5), Spawn(-50.123, 70.0, None)]

    folder = kryta.publish(repo, p)
    assert _pack_ids(repo) == [449], "the id is stated once"
    basic = json.load(open(os.path.join(folder, "basic.json")))
    assert basic["map_file_id"] == 0x5F123 and basic["name"] == "Test Vale"
    assert basic["instance_value"] == 1234, "the displaced record's instance value is kept"
    spawns = json.load(open(os.path.join(folder, "spawns.json")))["spawns"]
    assert spawns[0] == {"idx": 0, "x": 100.0, "y": 200.0, "plane": 0, "angle": 0.5}
    assert "angle" not in spawns[1] and spawns[1]["x"] == -50.12
    assert kryta.legacy_sql_files(repo) == [], "the old SQL publish is cleaned up"
    assert kryta.maps_claimed(repo) == {449: ("test-vale", "449")}
    assert kryta.read_catalogue(repo)[449].name.startswith("Kamadan"), "the catalogue stays the game's"

    kryta.publish(repo, p)                                  # a second publish replaces the first
    assert _pack_ids(repo) == [449]

    kryta.unpublish(repo, p)
    assert _pack_ids(repo) == [449]
    pack = os.path.join(repo, "config", "World", "Vanilla")
    assert json.load(open(os.path.join(pack, "maps", "unplaced", "449", "basic.json")))["map_file_id"] == 214476
    assert sorted(os.listdir(pack)) == ["maps"], "nothing of the editor's is left in the pack"


def test_point_manager_sets_the_archive_and_repairs_path_graphs(tmp_path):
    from kryta_cs import kryta
    repo = _fake_repo(tmp_path)
    changed = kryta.point_manager_at(repo, "D:\\GWClone\\Gw.dat")
    assert len(changed) == 2
    assert kryta.read_manage_setting(repo, "MapArchiveFile") == "D:/GWClone/Gw.dat"
    assert kryta.read_world_setting(repo, "PathGraphs") == "data/path-graphs.bin"
    assert kryta.server_archive(repo) == "D:/GWClone/Gw.dat"
    assert kryta.point_manager_at(repo, "D:/GWClone/Gw.dat") == [], "a second call changes nothing"
