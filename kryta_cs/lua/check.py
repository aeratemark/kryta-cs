"""Check: what the world would refuse in a pack's Lua, found before the world starts.

Two levels, as the Construction Set had a quick syntax pass and a full compile:

1. **Syntax**, always: `source.parse`, plain Python, the line and column of the first error.
2. **The world's own load**, when `lupa` is installed: each kind of file is run in a Lua 5.4 state
   set up as the world sets up its own (`game/src/Scripting/`): only `table`, `string` and `math`
   with the base functions, no `load`, `loadfile` or `dofile`, and the same budget of a million VM
   instructions per call. Then the world's checks are run on what loaded:

   - dialogs: `scripts/runtime.lua`, then `add_dialog(file, chunk)` for every `dialogs/*.lua` in
     name order, then the runtime's `check()`, then every text of `texts()` converted as
     `NpcScripts::text_units` converts it (a label at most 128 units with a 19-character name),
     as `NpcScripts::load` does;
   - behaviors: every entry of `npcs/behaviors.lua` read as `NpcBehaviors::load` and `parse_spec`
     read it;
   - skills: the prelude with `op.<word>` and `cast:<word>`, then every script a `skills.json` row
     names must return a table with an `on_use` function, as `LuaSkills::load` requires.

   Unsaved text in the editor is checked in place of the file on disk (`overrides`).

A problem names the file and, where it can be found, the line: from Lua's own message for an error
while loading, and by finding the page or choice in the file's table for a dialog check.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import pack as packmod
from .source import LuaSyntaxError, LuaTable, parse, returned_table

BUDGET = 1000 * 1000                 # NpcScripts.rs / SkillScripts.rs BUDGET_THOUSANDS * 1000
LABEL_UNITS = 128                    # DIALOG_LABEL_UNITS: a choice label's most units
CHECK_NAME = "X" * 19                # NpcScripts.rs CHECK_NAME: the longest character name
LITERAL_OPEN = (0x0108, 0x0107)
LITERAL_CLOSE = 0x0001

# SkillScripts.rs OP_WORDS and PRELUDE, as the world has them.
OP_WORDS = ("attack", "damage", "heal", "life_steal", "condition", "knockdown", "interrupt", "effect",
            "field", "chain", "drain_energy", "remove_hex", "resurrect", "lose_adrenaline", "unsupported")
SKILL_PRELUDE = r"""
op = {}
Cast = {}
Cast.__index = Cast
for _, word in ipairs(OP_WORDS) do
    op[word] = function(fields)
        local queued = {}
        for key, value in pairs(fields or {}) do queued[key] = value end
        queued.op = word
        return queued
    end
    Cast[word] = function(self, fields)
        table.insert(self._queue, op[word](fields))
    end
end
function Cast:run(queued)
    assert(type(queued) == "table" and type(queued.op) == "string", "cast:run takes an op.<word>{...}")
    table.insert(self._queue, queued)
end
"""

# Builds the sandbox and returns the two ways in, which the scripts themselves cannot reach.
_SANDBOX = r"""
local load, sethook, pcall, error, pack, unpack = load, debug.sethook, pcall, error, table.pack, table.unpack
local BUDGET = %d
local function arm()
    sethook(function() error("the script ran past its instruction budget", 2) end, "", BUDGET)
end
local function disarm() sethook() end
for _, name in ipairs({ "io", "os", "package", "require", "debug", "coroutine", "utf8",
                        "dofile", "loadfile", "load", "python" }) do
    _G[name] = nil
end
local H = {}
function H.run(source, name)
    local chunk, err = load(source, "=" .. name, "t", _G)
    if not chunk then error(err, 0) end
    arm()
    local result = pack(pcall(chunk))
    disarm()
    if not result[1] then error(result[2], 0) end
    return unpack(result, 2, result.n)
end
function H.call(fn, ...)
    arm()
    local result = pack(pcall(fn, ...))
    disarm()
    if not result[1] then error(result[2], 0) end
    return unpack(result, 2, result.n)
end
function H.type(value) return type(value) end
return H
""" % BUDGET


@dataclass
class Problem:
    rel: str                  # the pack-relative file, or "" for the pack as a whole
    message: str
    line: int | None = None
    col: int | None = None
    severity: str = "error"   # "error": the world refuses to start; "warning": it starts

    def where(self) -> str:
        if self.line:
            return f"{self.rel}:{self.line}"
        return self.rel or "(pack)"

    def __str__(self) -> str:
        return f"{self.where()}: {self.message}"


# ------------------------------------------------------------------------------------- lupa

def lua_module():
    """lupa's Lua 5.4 module, or None. lupa 2 has one module per Lua; lupa 1 only the default."""
    try:
        import lupa.lua54 as mod          # noqa: F401
        return mod
    except ImportError:
        pass
    try:
        import lupa as mod
        return mod
    except ImportError:
        return None


def lupa_status() -> tuple[bool, str]:
    mod = lua_module()
    if mod is None:
        return False, ("lupa is not installed: Check reads syntax only. "
                       "`pip install lupa` runs the files as the world does.")
    try:
        rt = mod.LuaRuntime(unpack_returned_tuples=True)
        version = rt.eval("_VERSION")
    except Exception as err:            # a broken install
        return False, f"lupa did not start Lua: {err}"
    if version != "Lua 5.4":
        return False, f"lupa runs {version}; the world runs Lua 5.4. Install a lupa with Lua 5.4."
    return True, f"{version} through lupa {getattr(mod, '__version__', '')}".strip()


class Sandbox:
    """One Lua 5.4 state, opened as the world opens its own."""

    def __init__(self):
        mod = lua_module()
        if mod is None:
            raise RuntimeError("lupa is not installed")
        self.mod = mod
        try:
            self.rt = mod.LuaRuntime(unpack_returned_tuples=True, register_eval=False,
                                     register_builtins=False)
        except TypeError:                    # an older lupa without register_builtins
            try:
                self.rt = mod.LuaRuntime(unpack_returned_tuples=True, register_eval=False)
            except TypeError:
                self.rt = mod.LuaRuntime(unpack_returned_tuples=True)
        self.h = self.rt.execute(_SANDBOX)
        self._run, self._call, self._type = self.h["run"], self.h["call"], self.h["type"]

    @property
    def LuaError(self):
        return self.mod.LuaError

    def run(self, source: str, name: str):
        return self._run(source, name)

    def call(self, fn, *args):
        return self._call(fn, *args)

    def type(self, value) -> str:
        if value is None:
            return "nil"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        return self._type(value)

    def globals(self):
        return self.rt.globals()


def _seq(table) -> list:
    """A Lua table's 1..n values."""
    out = []
    i = 1
    while True:
        v = table[i]
        if v is None:
            return out
        out.append(v)
        i += 1


# --------------------------------------------------------------------- problems and positions

_LUA_POS = re.compile(r"^(?P<rel>[\w./\\\- ]+?\.lua):(?P<line>\d+):\s*(?P<msg>.*)$", re.S)
_DIALOG_AT = re.compile(
    r"^(?P<rel>\S+\.lua) \((?P<npc>[^)]*), map (?P<map>-?\d+)\):? ?"
    r"(?:page (?P<page>\d+))?(?: choice (?P<choice>[^:\s]+))?"
)


def _line_of(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def from_lua_error(message: str, default_rel: str) -> Problem:
    """A Lua error message as a problem with its file and line, when the message carries them."""
    m = _LUA_POS.match(message.strip())
    if m:
        return Problem(m.group("rel").replace("\\", "/"), m.group("msg"), int(m.group("line")))
    return Problem(default_rel, message.strip())


def locate_dialog(message: str, sources: dict) -> Problem:
    """A dialog problem (`file (npc, map M): page N choice ID: ...`) with the line of that page or
    choice in the file, found by reading the file's table."""
    m = _DIALOG_AT.match(message)
    if not m:
        return Problem("", message)
    rel = m.group("rel")
    problem = Problem(rel, message[len(rel):].lstrip(" "))
    src = sources.get(rel)
    if src is None:
        return problem
    try:
        table = returned_table(src)
    except LuaSyntaxError:
        return problem
    if table is None:
        return problem
    node = table.node
    map_id = int(m.group("map"))
    fld = table.field(map_id)
    by_map = table.get(map_id)
    if fld is not None:
        node = fld.value
    if isinstance(by_map, LuaTable) and m.group("page"):
        pages = by_map.get("pages")
        pfld = by_map.field("pages")
        if pfld is not None:
            node = pfld.value
        if isinstance(pages, LuaTable):
            n = int(m.group("page"))
            f = pages.field(n)
            page = pages.get(n)
            if f is not None:
                node = f.value
            if isinstance(page, LuaTable) and m.group("choice"):
                buttons = page.get("buttons")
                if isinstance(buttons, LuaTable):
                    for b, bf in zip(buttons.sequence(), buttons.fields):
                        if isinstance(b, LuaTable) and str(b.get("id")) == m.group("choice"):
                            node = bf.value
                            break
    problem.line = _line_of(src, node.start)
    return problem


# ------------------------------------------------------------------------------ text units

def literal_units(text: str) -> list[int]:
    """NpcScripts::literal: the markers, the UTF-16 units without control characters, the end."""
    data = text.encode("utf-16-le")
    units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
    return list(LITERAL_OPEN) + [u for u in units if u >= 0x20] + [LITERAL_CLOSE]


def text_units(box: Sandbox, value, player: str) -> list[int]:
    """NpcScripts::text_units, over values from the sandbox. Raises ValueError with the world's words."""
    kind_of = box.type(value)
    if kind_of == "string":
        return literal_units(value)
    if kind_of == "table":
        kind = value["kind"]
        if kind == "gw":
            out = []
            for unit in _seq(value["units"]):
                if _is_player(box, unit):
                    out.extend(u for u in literal_units(player)[2:-1])
                    continue
                if isinstance(unit, bool) or not isinstance(unit, (int, float)) or (
                        isinstance(unit, float) and not unit.is_integer()):
                    raise ValueError(f"gw() takes whole numbers, found {box.type(unit)}")
                n = int(unit)
                if not 0 <= n <= 0xFFFF:
                    raise ValueError(f"gw() unit {n} is not from 0 to 65535")
                out.append(n)
            return out
        if kind == "mix":
            out = []
            for part in _seq(value["parts"]):
                if _is_player(box, part):
                    out.extend(literal_units(player))
                else:
                    out.extend(text_units(box, part, player))
            return out
        raise ValueError("a table that is not gw(...) or mix(...)")
    return _raise_not_text(kind_of)


def _raise_not_text(kind_of: str):
    raise ValueError(f"a {kind_of} is not a text; write a string, gw(...) or mix(...)")


def _is_player(box: Sandbox, value) -> bool:
    return box.type(value) == "table" and value["kind"] == "player"


# ------------------------------------------------------------------------------- the checks

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _source(pack: str, rel: str, overrides: dict) -> str:
    if rel in overrides:
        return overrides[rel]
    return _read(os.path.join(pack, *rel.split("/")))


def syntax(rel: str, text: str) -> list[Problem]:
    try:
        parse(text)
    except LuaSyntaxError as err:
        return [Problem(rel, err.message, err.line, err.col)]
    return []


def check_dialogs(pack: str, overrides: dict | None = None) -> list[Problem]:
    """NpcScripts::load, over the runtime and every dialog file."""
    overrides = overrides or {}
    files = [f for f in packmod.catalogue(pack, with_skill_rows=False) if f.kind == "dialog"]
    runtime_rel = packmod.RUNTIME_FILE
    if not os.path.isfile(os.path.join(pack, *runtime_rel.split("/"))) and runtime_rel not in overrides:
        if files:
            return [Problem(packmod.DIALOG_DIR, f"{packmod.DIALOG_DIR} exists, and {runtime_rel} does not: "
                                                "the dialogs have nothing to run them")]
        return []
    box = Sandbox()
    sources = {}
    try:
        sources[runtime_rel] = _source(pack, runtime_rel, overrides)
        runtime = box.run(sources[runtime_rel], runtime_rel)
    except box.LuaError as err:
        return [from_lua_error(str(err), runtime_rel)]
    if box.type(runtime) != "table" or box.type(runtime["add_dialog"]) != "function":
        return [Problem(runtime_rel, "does not return a table with add_dialog")]
    problems: list[Problem] = []
    for f in files:
        try:
            sources[f.rel] = _source(pack, f.rel, overrides)
        except OSError as err:
            problems.append(Problem(f.rel, str(err)))
            continue
        try:
            chunk = box.run(sources[f.rel], f.rel)
            box.call(runtime["add_dialog"], f.rel, chunk)
        except box.LuaError as err:
            problems.append(from_lua_error(str(err), f.rel))
    try:
        for message in _seq(box.call(runtime["check"])):
            problems.append(locate_dialog(str(message), sources))
        for entry in _seq(box.call(runtime["texts"])):
            place, text, label = entry["where"], entry["text"], bool(entry["label"])
            try:
                units = text_units(box, text, CHECK_NAME)
            except ValueError as err:
                problems.append(locate_dialog(f"{place}: {err}", sources))
                continue
            if not units:
                problems.append(locate_dialog(f"{place}: an empty text", sources))
            elif label and len(units) > LABEL_UNITS:
                problems.append(locate_dialog(
                    f"{place}: a label of {len(units)} units; the client takes at most {LABEL_UNITS}",
                    sources))
    except box.LuaError as err:
        problems.append(from_lua_error(str(err), runtime_rel))
    return problems


_NODE_KEYS = {
    "sequence": ("kind", "children"), "fallback": ("kind", "children"), "wait": ("kind", "ms"),
    "stand": ("kind",), "patrol": ("kind", "route"), "burrow": ("kind", "visible_ms", "hidden_ms"),
}


def _spec(box: Sandbox, table, at: str) -> None:
    """NpcBehaviors::parse_spec: raises ValueError naming the path to the bad node."""
    kind = table["kind"]
    if box.type(kind) != "string":
        raise ValueError(f"{at}kind: expected a string, found {box.type(kind)}")
    if kind not in _NODE_KEYS:
        raise ValueError(f"{at}unknown node kind {kind!r}; try sequence, fallback, wait, stand, patrol, burrow")
    allowed = _NODE_KEYS[kind]
    for key in list(table.keys()):
        if not isinstance(key, str):
            raise ValueError(f"{at}every key is a string")
        if key not in allowed:
            raise ValueError(f"{at}unknown field {key!r}; this node accepts {', '.join(allowed)}")
    if kind in ("sequence", "fallback"):
        children = table["children"]
        if box.type(children) != "table":
            raise ValueError(f"{at}children: expected a table, found {box.type(children)}")
        items = _seq(children)
        if not items:
            raise ValueError(f"{at}children: a control node needs at least one child")
        for i, child in enumerate(items):
            if box.type(child) != "table":
                raise ValueError(f"{at}children[{i}]: expected a table, found {box.type(child)}")
            _spec(box, child, f"{at}children[{i}]: ")
    elif kind == "wait":
        if box.type(table["ms"]) != "number":
            raise ValueError(f"{at}ms: expected a number, found {box.type(table['ms'])}")
    elif kind == "patrol":
        if table["route"] is not None and box.type(table["route"]) != "string":
            raise ValueError(f"{at}route: expected a string, found {box.type(table['route'])}")
    elif kind == "burrow":
        for key in ("visible_ms", "hidden_ms"):
            if box.type(table[key]) != "number":
                raise ValueError(f"{at}{key}: expected a number, found {box.type(table[key])}")


def _line_of_key(source: str, key) -> int | None:
    try:
        table = returned_table(source)
    except LuaSyntaxError:
        return None
    if table is None:
        return None
    f = table.field(key)
    return _line_of(source, f.start) if f is not None else None


def check_behaviors(pack: str, overrides: dict | None = None) -> list[Problem]:
    """NpcBehaviors::load over `npcs/behaviors.lua`."""
    overrides = overrides or {}
    rel = packmod.BEHAVIORS_FILE
    if rel not in overrides and not os.path.isfile(os.path.join(pack, *rel.split("/"))):
        return []
    source = _source(pack, rel, overrides)
    box = Sandbox()
    try:
        table = box.run(source, rel)
    except box.LuaError as err:
        return [from_lua_error(str(err), rel)]
    if box.type(table) != "table":
        return [Problem(rel, f"expected a table, found {box.type(table)}")]
    problems = []
    names = 0
    for name, value in list(table.items()):
        names += 1
        line = _line_of_key(source, name)
        kind = box.type(value)
        if kind == "function":
            continue
        if kind != "table":
            problems.append(Problem(rel, f"{name}: expected a table or a function, found {kind}", line))
            continue
        if box.type(value["script"]) == "function":
            if value["burrow"] is not None and box.type(value["burrow"]) != "boolean":
                problems.append(Problem(rel, f"{name}.burrow: expected a boolean", line))
            continue
        try:
            _spec(box, value, "")
        except ValueError as err:
            problems.append(Problem(rel, f"{name}: {err}", line))
    if names == 0:
        problems.append(Problem(rel, "no behaviors"))
    return problems


def check_skills(pack: str, overrides: dict | None = None) -> list[Problem]:
    """LuaSkills::load: every script a skills.json row names returns a table with `on_use`."""
    overrides = overrides or {}
    try:
        rows = packmod.skill_rows(pack)
    except (OSError, ValueError) as err:
        return [Problem(packmod.SKILLS_FILE, f"is not readable: {err}")]
    if not rows:
        return []
    box = Sandbox()
    g = box.globals()
    g["OP_WORDS"] = box.rt.table_from(list(OP_WORDS))
    box.run(SKILL_PRELUDE, "skill prelude")
    problems = []
    for row in sorted(rows, key=lambda r: r["id"] or 0):
        rel = row["script"].replace("\\", "/")
        name = f"skill {row['id']} ({row['name']})"
        try:
            source = _source(pack, rel, overrides)
        except OSError as err:
            problems.append(Problem(rel, f"{name}: is not readable: {err}"))
            continue
        try:
            chunk = box.run(source, rel)
        except box.LuaError as err:
            p = from_lua_error(str(err), rel)
            p.message = f"{name}: {p.message}"
            problems.append(p)
            continue
        if box.type(chunk) != "table":
            problems.append(Problem(rel, f"{name}: does not return a table"))
        elif box.type(chunk["on_use"]) != "function":
            problems.append(Problem(rel, f"{name}: has no on_use function", _line_of_key(source, "on_use")))
    return problems


def check_pack(pack: str, overrides: dict | None = None, kinds=packmod.KINDS) -> list[Problem]:
    """Everything the world would refuse in the pack's Lua, with unsaved text in `overrides`
    (pack-relative path -> text). Syntax first; the world's own load when lupa is installed."""
    overrides = overrides or {}
    problems: list[Problem] = []
    files = packmod.catalogue(pack)
    broken = set()
    for f in files:
        if f.kind not in kinds:
            continue
        if not f.exists and f.rel not in overrides:
            if f.kind == "skill":
                problems.append(Problem(f.rel, f"{f.note}: the file does not exist"))
            continue
        try:
            text = _source(pack, f.rel, overrides)
        except OSError as err:
            problems.append(Problem(f.rel, str(err)))
            continue
        found = syntax(f.rel, text)
        if found:
            broken.add(f.kind)
        problems.extend(found)
        if f.kind == "skill" and f.note.startswith("no skills.json row"):
            problems.append(Problem(f.rel, f.note, severity="warning"))
    if lua_module() is None:
        return problems
    runs = {"dialog": check_dialogs, "runtime": check_dialogs, "behaviors": check_behaviors,
            "skill": check_skills}
    done = set()
    for kind in kinds:
        run = runs[kind]
        if run in done:
            continue
        done.add(run)
        # A syntax error is already reported with its line; the world's load would stop at it too.
        if kind in broken or (run is check_dialogs and broken & {"dialog", "runtime"}):
            continue
        try:
            problems.extend(run(pack, overrides))
        except Exception as err:            # the check itself failed: say so, do not hide it
            problems.append(Problem("", f"the {packmod.KIND_TITLES[kind].lower()} check could not run: {err}"))
    return problems


def check_file(pack: str, rel: str, text: str, overrides: dict | None = None) -> list[Problem]:
    """The problems for one file, with `text` as its contents: its syntax, then the world's load of
    its kind (which reads the other files of that kind too, unsaved ones from `overrides`)."""
    found = syntax(rel, text)
    if found:
        return found
    overrides = dict(overrides or {})
    overrides[rel] = text
    kind = kind_of(rel)
    if kind is None or lua_module() is None:
        return []
    return [p for p in check_pack(pack, overrides, kinds=(kind,)) if p.rel in (rel, "")]


def kind_of(rel: str) -> str | None:
    if rel == packmod.RUNTIME_FILE:
        return "runtime"
    if rel == packmod.BEHAVIORS_FILE:
        return "behaviors"
    if rel.startswith(packmod.DIALOG_DIR + "/"):
        return "dialog"
    if rel.startswith(packmod.SKILL_DIR + "/"):
        return "skill"
    return None


def main(argv=None) -> int:
    """`python -m kryta_cs.lua.check [pack]`: Check All from a terminal. Exit 1 on any error."""
    import sys
    args = sys.argv[1:] if argv is None else argv
    if args:
        pack_dir = args[0]
    else:
        from .. import kryta, paths
        if not paths.REPO_ROOT:
            print("Not inside a Kryta checkout: name the pack folder.", file=sys.stderr)
            return 2
        pack_dir = kryta.pack_dir(paths.REPO_ROOT)
    ok, how = lupa_status()
    print(f"Checking {pack_dir} ({how})")
    found = check_pack(pack_dir)
    for p in found:
        print(f"{p.severity}: {p}")
    errors = sum(1 for p in found if p.severity == "error")
    print(f"{errors} error(s), {len(found) - errors} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
