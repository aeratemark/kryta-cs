"""The Lua reader, edits and Check: offline, no Qt. The pack tests read the checkout's own
config/World/Vanilla and are skipped outside a Kryta checkout; the world-load tests need lupa."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs.lua import check, pack                                    # noqa: E402
from kryta_cs.lua.source import (Call, Edit, Global, LuaSyntaxError, LuaTable, Script,  # noqa: E402
                                 check_syntax, literal, parse, returned_table, to_lua, tokenize)

PACK = os.path.abspath(os.path.join(HERE, "..", "..", "config", "World", "Vanilla"))
HAS_PACK = os.path.isfile(os.path.join(PACK, "scripts", "runtime.lua"))
needs_pack = pytest.mark.skipif(not HAS_PACK, reason="not inside a Kryta checkout")
needs_lupa = pytest.mark.skipif(check.lua_module() is None, reason="lupa is not installed")


def pack_lua_files():
    out = []
    for root, _dirs, files in os.walk(PACK):
        for name in files:
            if name.endswith(".lua"):
                out.append(os.path.join(root, name))
    return sorted(out)


# ----------------------------------------------------------------------------------- reading

VALID = [
    "return {}",
    "local a <const>, b <close> = 1, nil",
    "local function f(...) return select('#', ...) end",
    "function a.b.c:d(x) return self, x end",
    "for i = 1, 10, 2 do end for k, v in pairs(t) do goto done end ::done::",
    "repeat local x = 1 until x == 1",
    "while true do break end",
    "if a then elseif b then else end",
    "x = 1 // 2 | 3 & 4 ~ 5 << 6 >> 7 .. 'a' .. \"b\" ^ -2",
    "local s = [==[ long ]] still ]==] .. [[x]]",
    "--[[ a\nlong comment ]] return 1",
    "--[==[ ]] ]==] return 2",
    "f{ a = 1, [2] = 3; 'x' } f'str' f\"str\" a.b:c{}",
    "local t = { 0x1F, 0xA.8p1, 1e10, .5, 3., 0x10 }",
    "local s = '\\x41\\u{48}\\65\\z\n   tail\\n'",
    "return function(...) local t = { ... } return #t end",
    "a, b.c, d[1] = 1, 2, 3",
    "#!/usr/bin/lua\nreturn 1",
    "local x = not not - - ~ ~ 1",
]

INVALID = [
    ("return {", 1, "unexpected symbol near '<eof>'"),
    ("local x = = 1", 1, "unexpected symbol"),
    ("if x then\n  y = 1\n", 3, "'end' expected"),
    ("x = 'unfinished\n", 1, "unfinished string"),
    ("return 1\nreturn 2", 2, "'<eof>' expected"),
    ("f() = 1", 1, "syntax error"),
    ("local x <bad> = 1", 1, "unknown attribute"),
    ("x = 3x", 1, "malformed number"),
    ("--[[ never closed", 1, "unfinished long comment"),
    ("return {\n  a = 1\n  b = 2\n}", 3, "'}' expected"),
    ("x = @", 1, "unexpected symbol"),
]


@pytest.mark.parametrize("src", VALID)
def test_valid_lua_parses(src):
    assert check_syntax(src) is None, check_syntax(src)


@pytest.mark.parametrize("src,line,words", INVALID)
def test_invalid_lua_names_its_line(src, line, words):
    err = check_syntax(src)
    assert err is not None
    assert err.line == line, err
    assert words in err.message, err


def test_unclosed_block_names_its_opener():
    err = check_syntax("function f()\n  if x then\n    return 1\n  end\n")
    assert "to close 'function' at line 1" in err.message


def test_strings_and_numbers_read_as_lua_reads_them():
    tokens, comments = tokenize("x = '\\65\\x42\\u{43}\\n' .. [[\nD]] -- c\n--[[ e ]] y = 0x10 + 1.5e1")
    values = [t.value for t in tokens if t.kind in ("string", "number")]
    assert values == ["ABC\n", "D", 16, 15.0]
    assert len(comments) == 2


@needs_pack
def test_every_pack_lua_file_parses():
    files = pack_lua_files()
    assert len(files) >= 40
    for path in files:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            parse(src)
        except LuaSyntaxError as err:
            pytest.fail(f"{path}: {err}")


@needs_pack
def test_a_dialog_reads_as_data():
    with open(os.path.join(PACK, "dialogs", "sir_tydus.lua"), encoding="utf-8") as fh:
        src = fh.read()
    t = returned_table(src)
    assert t["npc"] == "Sir Tydus"
    by_map = t[148]
    assert by_map["talk_range"] == 200.0
    pages = by_map["pages"].sequence()
    assert len(pages) >= 3
    body = pages[0]["body"]
    assert isinstance(body, Call) and body.name == "gw"
    assert Global("PLAYER") in body.args
    offer = pages[2]
    assert offer["when"]["kind"] == "quest_offer_open"
    assert offer["buttons"][1]["action"]["kind"] == "quest"


@needs_pack
def test_a_function_reads_as_a_script_with_its_text():
    with open(os.path.join(PACK, "npcs", "behaviors.lua"), encoding="utf-8") as fh:
        src = fh.read()
    t = returned_table(src)
    assert "patrol" in t
    assert t["patrol"]["kind"] == "patrol"
    scripted = [v for v in t.items if isinstance(v[1], LuaTable) and isinstance(v[1].get("script"), Script)]
    assert scripted, "behaviors.lua has scripted behaviours"


# ------------------------------------------------------------------------------------ editing

DIALOG = '''-- A greeter.
return {
    npc = "Greeter",   -- the placement's name

    [148] = {
        pages = {
            {
                body = "Hello.",
                buttons = {
                    { icon = 15, label = "Farewell.", id = 128, action = { kind = "close" } },
                },
            },
        },
        -- kept as written
        on_talk = function(ctx) if ctx.char.level >= 10 then return 1 end end,
    },
}
'''


def test_no_edit_is_the_same_text():
    assert Edit(DIALOG).apply() == DIALOG


def test_set_value_changes_only_that_value():
    t = returned_table(DIALOG)
    button = t[148]["pages"][1]["buttons"][1]
    new = Edit(DIALOG).set_value(button.field("id"), 129).apply()
    assert new == DIALOG.replace("id = 128", "id = 129")
    assert returned_table(new)[148]["pages"][1]["buttons"][1]["id"] == 129


def test_set_value_writes_a_table_in_the_pack_style():
    t = returned_table(DIALOG)
    page = t[148]["pages"][1]
    new = Edit(DIALOG).set_value(page.field("body"), Call("gw", [5347, Global("PLAYER")], None)).apply()
    assert 'body = gw(5347, PLAYER),' in new
    action = returned_table(new)[148]["pages"][1]["buttons"][1]
    new2 = Edit(new).set_value(action.field("action"), {"kind": "page", "page": 1}).apply()
    assert "action = {\n" in new2
    assert returned_table(new2)[148]["pages"][1]["buttons"][1]["action"]["page"] == 1
    assert "-- kept as written" in new2 and "on_talk = function(ctx)" in new2


def test_add_and_remove_fields_keep_the_rest():
    t = returned_table(DIALOG)
    pages_node = t[148].field("pages").value
    new = Edit(DIALOG).add_field(pages_node, None, {"body": "Second.", "buttons": []}).apply()
    pages = returned_table(new)[148]["pages"].sequence()
    assert [p["body"] for p in pages] == ["Hello.", "Second."]
    t2 = returned_table(new)
    second = t2[148]["pages"].field(2)
    back = Edit(new).remove_field(t2[148]["pages"].node, second).apply()
    assert back == DIALOG
    added = Edit(DIALOG).add_field(t.node, "talk_range", 300.0).apply()
    assert returned_table(added)["talk_range"] == 300.0
    assert added.startswith("-- A greeter.\n")


def test_overlapping_edits_are_refused():
    t = returned_table(DIALOG)
    f = t.field("npc")
    e = Edit(DIALOG).set_value(f, "A")
    with pytest.raises(ValueError):
        e.set_value(f, "B")


def test_to_lua_round_trips_through_the_reader():
    value = {"npc": "Q\"uote\\d", "list": [1, 2.5, True, None], 7: {"x": -3}, "and": "keyword key"}
    src = "return " + to_lua(value)
    t = returned_table(src)
    assert t["npc"] == 'Q"uote\\d'
    assert t["list"].sequence() == [1, 2.5, True]
    assert t[7]["x"] == -3
    assert t["and"] == "keyword key"


def test_literal_of_an_expression_is_a_script():
    chunk = parse("return { a = 1 + 2, b = f(1 + 1), c = -4, d = gw(1, PLAYER), e = a.b(1) }")
    t = literal(chunk.returned(), chunk.source)
    assert isinstance(t["a"], Script) and t["a"].source == "1 + 2"
    assert isinstance(t["b"], Script) and isinstance(t["e"], Script)
    assert isinstance(t["d"], Call) and t["d"].args == [1, Global("PLAYER")]
    assert t["c"] == -4


# ------------------------------------------------------------------------------- files, Check

def test_files_keep_their_line_ends_and_bom(tmp_path):
    path = str(tmp_path / "x.lua")
    with open(path, "wb") as fh:
        fh.write("\ufeffreturn {\r\n    a = 1,\r\n}\r\n".encode("utf-8"))
    text, newline, bom, _ = pack.read_script(path)
    assert text == "return {\n    a = 1,\n}\n" and newline == "\r\n" and bom
    pack.write_script(path, text.replace("1", "2"), newline, bom)
    with open(path, "rb") as fh:
        assert fh.read() == "\ufeffreturn {\r\n    a = 2,\r\n}\r\n".encode("utf-8")


def test_file_stem_and_new_dialog():
    assert pack.file_stem("Sir Tydus") == "sir_tydus"
    assert pack.file_stem("Gilderoy le Fleur [Merchant]") == "gilderoy_le_fleur_merchant"
    t = returned_table(pack.new_dialog_text("New Person", 163))
    assert t["npc"] == "New Person" and t[163]["pages"][1]["buttons"][1]["id"] == 128


def test_a_syntax_problem_names_file_line_and_column():
    found = check.syntax("dialogs/x.lua", "return {\n  a = ,\n}")
    assert len(found) == 1
    assert (found[0].rel, found[0].line) == ("dialogs/x.lua", 2)


def test_lua_errors_and_dialog_problems_find_their_line():
    p = check.from_lua_error("dialogs/x.lua:12: attempt to index a nil value", "dialogs/x.lua")
    assert (p.rel, p.line, p.message) == ("dialogs/x.lua", 12, "attempt to index a nil value")
    src = {"dialogs/greeter.lua": DIALOG}
    p = check.locate_dialog("dialogs/greeter.lua (Greeter, map 148): page 1 choice 128: icon must be", src)
    assert p.rel == "dialogs/greeter.lua"
    assert DIALOG.splitlines()[p.line - 1].strip().startswith("{ icon = 15")
    p = check.locate_dialog("dialogs/greeter.lua (Greeter, map 148) page 1: an empty text", src)
    assert DIALOG.splitlines()[p.line - 1].strip() == "{"


def test_literal_units_are_the_worlds():
    assert check.literal_units("Hi") == [0x0108, 0x0107, ord("H"), ord("i"), 0x0001]
    assert check.literal_units("a\tb") == [0x0108, 0x0107, ord("a"), ord("b"), 0x0001]


@needs_pack
def test_the_catalogue_lists_every_kind():
    files = pack.catalogue(PACK)
    kinds = {f.kind for f in files}
    assert kinds == set(pack.KINDS)
    rels = {f.rel for f in files}
    assert "scripts/runtime.lua" in rels and "dialogs/sir_tydus.lua" in rels
    assert all(f.exists for f in files if f.kind == "skill" and f.note.startswith("skill "))


@needs_pack
def test_the_committed_pack_has_no_syntax_problems():
    found = [p for p in check.check_pack(PACK) if p.severity == "error"]
    assert found == [], "\n".join(map(str, found))


# ------------------------------------------------------------------- the world's load (lupa)

@needs_pack
@needs_lupa
def test_the_world_load_accepts_the_committed_pack():
    found = [p for p in check.check_pack(PACK) if p.severity == "error"]
    assert found == [], "\n".join(map(str, found))


@needs_pack
@needs_lupa
def test_a_bad_choice_is_found_on_its_line():
    rel = "dialogs/sir_tydus.lua"
    with open(os.path.join(PACK, *rel.split("/")), encoding="utf-8") as fh:
        src = fh.read()
    t = returned_table(src)
    button = t[148]["pages"][3]["buttons"][1]
    bad = Edit(src).set_value(button.field("icon"), 300).apply()
    found = check.check_file(PACK, rel, bad)
    assert any("icon must be a number from 0 to 255" in p.message for p in found), found
    hit = next(p for p in found if "icon must be" in p.message)
    assert hit.line == bad.count("\n", 0, button.field("icon").start) + 1 or hit.line is not None


@needs_pack
@needs_lupa
def test_a_runaway_script_is_stopped_by_the_budget():
    rel = "dialogs/sir_tydus.lua"
    found = check.check_file(PACK, rel, "while true do end\nreturn {}")
    assert any("budget" in p.message for p in found), found


@needs_pack
@needs_lupa
def test_the_sandbox_has_no_io_os_or_load():
    box = check.Sandbox()
    assert box.run("return io == nil and os == nil and load == nil and require == nil "
                   "and dofile == nil and debug == nil and python == nil", "probe") is True
    assert box.run("return type(string.format) == 'function' and type(math.floor) == 'function'",
                   "probe") is True


@needs_pack
@needs_lupa
def test_a_bad_behaviour_node_is_named():
    rel = "npcs/behaviors.lua"
    found = check.check_file(PACK, rel, "return { guard = { kind = 'patrol', speed = 3 } }")
    assert any("unknown field 'speed'" in p.message for p in found), found
