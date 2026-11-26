"""The map's placed NPCs: names, the rows the world reads, and publish setting the pack's file aside."""

import json
import math
import os

import pytest

from kryta_cs import kryta, npcs
from kryta_cs.model import MapProject, PlacedNpc

# NpcPack.rs placement_row: the only keys a placement row may hold.
PLACEMENT_KEYS = {"id", "definition", "x", "y", "plane", "angle", "direction", "scale", "name", "level",
                  "effects", "h0023", "properties", "items", "weapons", "route", "walk_speed",
                  "behavior", "respawn"}
REQUIRED = {"id", "definition", "x", "y", "plane", "effects", "h0023"}


def _pack(tmp_path, own_file=False):
    pack = tmp_path / "pack"
    (pack / "maps").mkdir(parents=True)
    (pack / "npcs" / "placements").mkdir(parents=True)
    (pack / "npcs" / "definitions.json").write_text(json.dumps({"definitions": [
        {"id": "npc-163-1", "definition_index": 1, "file_id": 116225, "primary": 1, "level": 20,
         "team": "nonc", "speed": 120.0},
        {"id": "mob-1421-bandit-raider", "definition_index": 1421, "file_id": 5, "primary": 2,
         "level": 3, "team": "mon1", "speed": 220.0, "combat": {"hostile": True}},
    ]}))
    (pack / "npcs" / "behaviors.lua").write_text('return {\n  patrol = { kind = "stand" },\n}\n')
    (pack / "npcs" / "placements" / "163.json").write_text(json.dumps({"map_id": 163, "placements": [
        {"id": "163-1", "definition": "npc-163-1", "x": 0, "y": 0, "plane": 0, "effects": 0,
         "h0023": 1.0, "name": npcs.encode_name("Duke Barradin")}], "routes": []}))
    if own_file:
        (pack / "npcs" / "placements" / "449.json").write_text('{"map_id": 449, "placements": [], "routes": []}')
    return str(pack)


def _project(n=1):
    p = MapProject.new("NPC test", 32, 32, map_id=449)
    p.slug = "npc-test"
    p.file_id = 0x5F100
    for k in range(n):
        p.npcs.append(PlacedNpc(id=npcs.next_id(p), definition="npc-163-1", x=100.0 + k, y=200.0))
    return p


def test_a_name_round_trips_and_is_limited():
    units = npcs.encode_name("Duke Barradin")
    assert units[:2] == [264, 263] and units[-1] == 1
    assert npcs.decode_name(units) == "Duke Barradin"
    assert len(npcs.encode_name("x" * npcs.NAME_CHARS)) == npcs.NAME_UNITS
    with pytest.raises(npcs.NpcError):
        npcs.encode_name("x" * (npcs.NAME_CHARS + 1))
    assert npcs.decode_name([3933, 50198, 44838, 5755]) is None     # encoded string ids


def test_types_take_readable_names_from_the_pack(tmp_path):
    pack = _pack(tmp_path)
    types = {t.id: t for t in npcs.load_types(pack)}
    assert types["npc-163-1"].label == "Duke Barradin"
    assert types["mob-1421-bandit-raider"].label == "Bandit Raider"
    assert types["mob-1421-bandit-raider"].hostile and not types["npc-163-1"].hostile
    assert types["npc-163-1"].home_map == 163
    assert npcs.behaviors(pack) == ["patrol"]


def test_a_row_holds_only_what_the_world_reads():
    n = PlacedNpc(id="449-cs-1", definition="npc-163-1", x=1.234, y=5.0, angle=math.pi / 2,
                  name="Bob", level=7, behavior="patrol",
                  extra={"direction": [1.0, 0.0], "items": [], "unknown": 1})
    row = npcs.placement_row(n)
    assert set(row) <= PLACEMENT_KEYS and REQUIRED <= set(row)
    assert "direction" not in row                # an angle or a direction, not both
    assert row["name"] == npcs.encode_name("Bob") and row["level"] == 7
    bare = npcs.placement_row(PlacedNpc(id="a", definition="d", x=0, y=0, extra={"direction": [0.0, 1.0]}))
    assert bare["direction"] == [0.0, 1.0] and "angle" not in bare


def test_the_project_keeps_its_npcs():
    p = _project(2)
    p.npcs[0].name = "Bob"
    p.npcs[1].angle = 1.5
    q = MapProject.from_json(json.loads(json.dumps(p.to_json())))
    assert q.npcs == p.npcs
    assert [n.id for n in q.npcs] == ["449-cs-1", "449-cs-2"]
    assert npcs.next_id(q) == "449-cs-3"
    d = p.to_json()
    d["npcs"].append(dict(d["npcs"][0]))
    with pytest.raises(ValueError):
        MapProject.from_json(d)


def test_check_names_what_the_world_would_refuse(tmp_path):
    pack = _pack(tmp_path)
    p = _project(1)
    assert npcs.check(pack, p) == []
    p.npcs[0].definition = "npc-999-9"
    p.npcs[0].behavior = "dance"
    problems = npcs.check(pack, p)
    assert len(problems) == 2 and "npc-999-9" in problems[0] and "dance" in problems[1]


def test_publish_sets_the_packs_file_aside_and_unpublish_puts_it_back(tmp_path, monkeypatch):
    pack = _pack(tmp_path, own_file=True)
    repo = str(tmp_path)
    monkeypatch.setattr(kryta, "pack_dir", lambda _repo: pack)
    own = open(npcs.placement_path(pack, 449)).read()
    p = _project(2)
    kryta.publish(repo, p)
    written = json.load(open(npcs.placement_path(pack, 449)))
    assert written["map_id"] == 449 and len(written["placements"]) == 2
    assert open(npcs.displaced_path(pack, 449)).read() == own
    marker = json.load(open(os.path.join(kryta.custom_folder(repo, 449), kryta.MARKER)))
    assert marker["npcs_written"] and marker["npcs_displaced"]

    p.npcs.pop()                                  # publish again: ours is replaced, theirs stays aside
    kryta.publish(repo, p)
    assert len(json.load(open(npcs.placement_path(pack, 449)))["placements"]) == 1
    assert open(npcs.displaced_path(pack, 449)).read() == own

    kryta.unpublish(repo, p)
    assert open(npcs.placement_path(pack, 449)).read() == own
    assert not os.path.exists(npcs.displaced_path(pack, 449))


def test_publish_without_npcs_leaves_the_pack_alone_and_a_bad_npc_writes_nothing(tmp_path, monkeypatch):
    pack = _pack(tmp_path, own_file=True)
    repo = str(tmp_path)
    monkeypatch.setattr(kryta, "pack_dir", lambda _repo: pack)
    own = open(npcs.placement_path(pack, 449)).read()
    kryta.publish(repo, _project(0))
    assert open(npcs.placement_path(pack, 449)).read() == own

    bad = _project(1)
    bad.npcs[0].definition = "npc-999-9"
    kryta.unpublish(repo, bad)
    with pytest.raises(ValueError):
        kryta.publish(repo, bad)
    assert open(npcs.placement_path(pack, 449)).read() == own
    assert not os.path.isdir(kryta.custom_folder(repo, 449))


def test_npc_selection_is_exclusive_and_placing_undoes():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from kryta_cs.ui.document import Document, NpcsCommand, copy_npcs

    app = QApplication.instance() or QApplication([])
    doc = Document()
    doc.set_project(_project(0))
    before = copy_npcs(doc.project)
    after = before + [PlacedNpc(id="449-cs-1", definition="npc-163-1", x=1.0, y=2.0)]
    doc.undo.push(NpcsCommand(doc, before, after, "Place"))
    assert [n.id for n in doc.project.npcs] == ["449-cs-1"]
    doc.select_prop(1)
    doc.select_npc(0)
    assert doc.selected_npc == 0 and doc.selected_prop == -1 and doc.selected == -1
    doc.select(0)
    assert doc.selected_npc == -1
    doc.select_npc(0)
    doc.undo.undo()
    assert doc.project.npcs == [] and doc.selected_npc == -1
    assert app is not None


def test_a_figure_faces_the_npcs_angle_and_keeps_its_scale():
    from kryta_cs.model import prop_apply, prop_matrix
    from kryta_cs.ui.glmath import prop_scale
    for deg in (0, 45, 90, 180, 270, 333):
        a = math.radians(deg)
        fx, fy, _fz = prop_apply(prop_matrix(npcs.npc_rotation(a)), 0.0, 1.0, 0.0)  # the face, +y
        assert abs(fx - math.cos(a)) < 0.03 and abs(fy - math.sin(a)) < 0.03, deg
    assert npcs.npc_rotation(None) == npcs.npc_rotation(0.0)
    for scale in (0.6, 1.0, 1.5):
        assert abs(prop_scale(npcs.npc_scale_byte(scale)) - scale) < 0.005


def test_a_type_draws_from_its_bodies_else_its_model_file():
    assert npcs.body_files({"file_id": 5, "model_files": [7, 8]}) == (7, 8)
    assert npcs.body_files({"file_id": 5, "model_files": []}) == (5,)
    assert npcs.visual_scale({"visual": 0x64000000}) == 1.0
    assert npcs.visual_scale({"visual": 0x96000000}) == 1.5
