"""The Lua files of a world pack, by what the world does with them.

The world reads four kinds, each through its own Lua state (`game/src/Scripting/`):

| kind      | where                     | read by                                      |
|-----------|---------------------------|----------------------------------------------|
| runtime   | `scripts/runtime.lua`     | `NpcScripts.rs`: runs every dialog           |
| dialog    | `dialogs/*.lua`           | `NpcScripts.rs`, through the runtime         |
| behaviors | `npcs/behaviors.lua`      | `NpcBehaviors.rs`: the NPC behaviour trees   |
| skill     | the `script` of a `skills.json` row | `SkillScripts.rs`: what a skill does |

The paths are the world's own constants; if the world moves a folder, change it here too.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

RUNTIME_FILE = "scripts/runtime.lua"      # NpcScripts.rs RUNTIME_FILE
DIALOG_DIR = "dialogs"                     # NpcScripts.rs DIALOG_DIR
BEHAVIORS_FILE = "npcs/behaviors.lua"      # NpcBehaviors.rs, under the pack's npcs/ folder
SKILLS_FILE = "skills.json"                # the skill rows; a row's `script` names its Lua file
SKILL_DIR = "skills"

_BOM = "\ufeff"

KINDS = ("runtime", "dialog", "behaviors", "skill")
KIND_TITLES = {"runtime": "Runtime", "dialog": "Dialogs", "behaviors": "Behaviors", "skill": "Skills"}


@dataclass
class ScriptFile:
    kind: str             # one of KINDS
    rel: str              # the pack-relative path with forward slashes, as the world names it
    path: str             # the absolute path on this machine
    label: str            # what the editor lists it as
    exists: bool = True
    note: str = ""        # e.g. the skill row that names it, or why it is not read

    @property
    def name(self) -> str:
        return os.path.basename(self.rel)


def _rel(pack: str, path: str) -> str:
    return os.path.relpath(path, pack).replace(os.sep, "/")


def skill_rows(pack: str) -> list[dict]:
    """The `skills.json` rows that name a script: {id, name, script}. Empty when the file is missing."""
    path = os.path.join(pack, SKILLS_FILE)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc.get("skills", []) if isinstance(doc, dict) else doc
    return [{"id": r.get("id"), "name": r.get("name", ""), "script": r["script"]}
            for r in rows if isinstance(r, dict) and r.get("script")]


def catalogue(pack: str, with_skill_rows: bool = True) -> list[ScriptFile]:
    """Every Lua file the world reads from `pack`, and every `.lua` in its script folders it does not."""
    out: list[ScriptFile] = []
    runtime = os.path.join(pack, *RUNTIME_FILE.split("/"))
    out.append(ScriptFile("runtime", RUNTIME_FILE, runtime, "runtime.lua", os.path.isfile(runtime)))

    ddir = os.path.join(pack, DIALOG_DIR)
    if os.path.isdir(ddir):
        for name in sorted(os.listdir(ddir)):
            if name.endswith(".lua"):
                path = os.path.join(ddir, name)
                out.append(ScriptFile("dialog", f"{DIALOG_DIR}/{name}", path, name[:-4]))

    beh = os.path.join(pack, *BEHAVIORS_FILE.split("/"))
    out.append(ScriptFile("behaviors", BEHAVIORS_FILE, beh, "behaviors.lua", os.path.isfile(beh)))

    named: dict[str, dict] = {}
    if with_skill_rows:
        try:
            for row in skill_rows(pack):
                named[row["script"].replace("\\", "/")] = row
        except (OSError, ValueError):
            named = {}
    seen = set()
    for rel, row in sorted(named.items(), key=lambda kv: kv[0]):
        path = os.path.join(pack, *rel.split("/"))
        seen.add(os.path.normcase(os.path.abspath(path)))
        out.append(ScriptFile("skill", rel, path, os.path.basename(rel)[:-4], os.path.isfile(path),
                              f"skill {row['id']} ({row['name']})"))
    sdir = os.path.join(pack, SKILL_DIR)
    if os.path.isdir(sdir):
        for name in sorted(os.listdir(sdir)):
            path = os.path.join(sdir, name)
            if name.endswith(".lua") and os.path.normcase(os.path.abspath(path)) not in seen:
                out.append(ScriptFile("skill", _rel(pack, path), path, name[:-4], True,
                                      "no skills.json row names it, so the world does not read it"
                                      if with_skill_rows else ""))
    return out


def new_dialog_text(npc: str, map_id: int) -> str:
    """A new dialog file in the shape `dialogs/README.md` gives: one map, one page, a Farewell."""
    from .source import lua_string
    return (
        f"-- {npc}: what a talk opens and what each choice does, per map.\n"
        "--\n"
        "-- The format is dialogs/README.md. Made in Kryta-CS.\n"
        "\n"
        "return {\n"
        f"    npc = {lua_string(npc)},\n"
        "\n"
        f"    [{int(map_id)}] = {{\n"
        "        pages = {\n"
        "            {\n"
        "                body = \"Greetings.\",\n"
        "                buttons = {\n"
        "                    { icon = 15, label = \"Farewell.\", id = 128, action = { kind = \"close\" } },\n"
        "                },\n"
        "            },\n"
        "        },\n"
        "    },\n"
        "}\n"
    )


def file_stem(npc: str) -> str:
    """The file name the pack uses for an NPC: lower case, words joined by `_` (`sir_tydus`)."""
    out = []
    for c in npc.lower():
        out.append(c if c.isalnum() else "_")
    stem = "_".join(part for part in "".join(out).split("_") if part)
    return stem or "npc"


def read_script(path: str) -> tuple[str, str, bool, float]:
    """A file's text with `\\n` line ends, its own line end, whether it had a BOM, and its mtime."""
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8")
    bom = text.startswith(_BOM)
    if bom:
        text = text[1:]
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, bom, os.path.getmtime(path)


def write_script(path: str, text: str, newline: str = "\n", bom: bool = False) -> float:
    """Write through a temporary file, so a failed write leaves the old file whole. Returns the mtime."""
    data = text.replace("\n", newline) if newline != "\n" else text
    if bom:
        data = _BOM + data
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".kryta-cs.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return os.path.getmtime(path)
