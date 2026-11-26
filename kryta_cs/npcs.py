"""NPCs: the world pack's NPC types, and a map's placed NPCs as the world reads them.

The world (game/src/Maps/NpcPack.rs) reads two kinds of file under `<pack>/npcs/`:

    definitions.json            the NPC TYPES: model, profession, level, team, speed, combat
    placements/<map id>.json    one map's PLACED NPCs: which type stands where, facing which way

The editor keeps a map's NPCs in its project (MapProject.npcs, kryta_cs/model.py) as it keeps
spawns, and Publish writes them as `placements/<map id>.json`. If the pack has its own file for
that id, Publish moves it to `<pack>/kryta-cs-displaced/npcs/placements/<map id>.json` and
taking the map off the world moves it back, as kryta.py does for the map's folder.

A definition's own name is encoded string ids (the client's text table), which the editor cannot
read. A placement may carry a LITERAL name instead - `[264, 263, <characters>, 1]`, what the
client shows - and dialogs/*.lua finds its NPC by that name. The editor shows a type's readable
name where any placement in the pack gives one.
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
from dataclasses import dataclass

DIR = "npcs"
DEFINITIONS = "definitions.json"
PLACEMENTS = "placements"
BEHAVIORS = "behaviors.lua"
DISPLACED_DIR = "kryta-cs-displaced"

NAME_HEAD = (264, 263)          # a literal name: these two units, the characters, then 1
NAME_TAIL = 1
NAME_UNITS = 32                 # NpcContent.rs AGENT_NAME_UNITS: the whole name, head and tail
NAME_CHARS = NAME_UNITS - len(NAME_HEAD) - 1

PROFESSIONS = {0: "None", 1: "Warrior", 2: "Ranger", 3: "Monk", 4: "Necromancer", 5: "Mesmer",
               6: "Elementalist", 7: "Assassin", 8: "Ritualist", 9: "Paragon", 10: "Dervish"}
HOSTILE_TEAMS = {"mon1", "mons", "band"}

# The fields of a placement row (NpcPack.rs placement_row) the editor carries from PlacedNpc.extra
# rather than writing itself.
EXTRA_KEYS = ("direction", "scale", "properties", "items", "weapons", "route", "walk_speed",
              "respawn", "plane", "effects", "h0023", "name")


TAU = 2.0 * math.pi


KEY_ORDER = "_keys"     # PlacedNpc.extra: the key order of the row it was read from


class NpcError(ValueError):
    pass


# ------------------------------------------------------------------ names

def encode_name(text: str) -> list:
    """`Duke Barradin` -> the literal-name units a placement's `name` holds."""
    if not text:
        raise NpcError("a name needs at least one character")
    if len(text) > NAME_CHARS:
        raise NpcError(f"a name holds at most {NAME_CHARS} characters; {text!r} has {len(text)}")
    units = [ord(c) for c in text]
    if any(u < 32 or u > 0xFFFF for u in units):
        raise NpcError(f"{text!r} has a character the name cannot carry")
    return [*NAME_HEAD, *units, NAME_TAIL]


def decode_name(units) -> str | None:
    """The literal name a `name` row carries, or None when it is not one."""
    if not isinstance(units, list) or len(units) < 4:
        return None
    if tuple(units[:2]) != NAME_HEAD or units[-1] != NAME_TAIL:
        return None
    try:
        return "".join(chr(int(u)) for u in units[2:-1])
    except (TypeError, ValueError):
        return None


def npc_rotation(angle) -> tuple:
    """The prop rotation bytes that turn a character model to face `angle` (radians, +x = 0).

    A character model faces its own +y (measured: tools/npc_models.py renders, 2026-11-24), so
    it is turned by angle - pi/2; prop_matrix's yaw is -byte * tau / 256 (model.py ROT_SIGNS)."""
    a = (0.0 if angle is None else float(angle)) - math.pi / 2.0
    return (0, 0, int(round(-a * 256.0 / TAU)) % 256)


def npc_scale_byte(scale: float) -> int:
    """The prop scale byte for a scale factor (glmath.prop_scale inverted)."""
    return max(1, min(255, int(round((float(scale) - 1.0 / 128.0) * 32768.0 / 255.0))))


# ------------------------------------------------------------------ the pack's NPC types

@dataclass
class NpcType:
    id: str
    index: int                  # definition_index
    file_id: int
    primary: int
    level: int
    team: str
    hostile: bool
    speed: float
    name: str | None            # readable, where a placement in the pack gives one
    bodies: tuple = ()          # the model files that carry its mesh (see body_files)
    scale: float = 1.0          # the visual word's top byte, in percent / 100

    @property
    def profession(self) -> str:
        return PROFESSIONS.get(self.primary, str(self.primary))

    @property
    def home_map(self) -> int | None:
        m = re.match(r"npc-(\d+)-", self.id)
        return int(m.group(1)) if m else None

    @property
    def label(self) -> str:
        """The name to show: the pack's readable one, else one made from the id."""
        if self.name:
            return self.name
        m = re.match(r"mob-\d+-(.+)$", self.id)
        if m and m.group(1) != "type":
            return m.group(1).replace("-", " ").replace("_", " ").title()
        return f"{self.profession} L{self.level}"


def body_files(row: dict) -> tuple:
    """The model files whose meshes draw an NPC type.

    A composited character's model file (`file_id`) is its skeleton and animations with no
    geometry; its meshes are the body files the 0x0057 packet sends (`model_files`). A type
    with no body files carries its mesh in the model file itself (a moa, a worm). MEASURED on the
    Vanilla pack (tools/npc_models.py, 2026-11-24): 115 of 120 types draw this way; the other 5
    have a skeleton and no body files in the pack."""
    bodies = tuple(int(f) for f in (row.get("model_files") or []) if f)
    if bodies:
        return bodies
    fid = row.get("file_id")
    return (int(fid),) if fid else ()


def visual_scale(row: dict) -> float:
    """The visual word's top byte is the scale in percent (definitions.json `fields`)."""
    pct = (int(row.get("visual", 0) or 0) >> 24) & 0xFF
    return pct / 100.0 if pct else 1.0


def npc_dir(pack: str) -> str:
    return os.path.join(pack, DIR)


def placement_path(pack: str, map_id: int) -> str:
    return os.path.join(pack, DIR, PLACEMENTS, f"{int(map_id)}.json")


def displaced_path(pack: str, map_id: int) -> str:
    return os.path.join(pack, DISPLACED_DIR, DIR, PLACEMENTS, f"{int(map_id)}.json")


def _read(path: str):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def pack_names(pack: str) -> dict:
    """{definition id: readable name} from every placement in the pack that carries one."""
    out = {}
    folder = os.path.join(pack, DIR, PLACEMENTS)
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        try:
            rows = _read(path).get("placements", [])
        except (OSError, ValueError, AttributeError):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = decode_name(row.get("name"))
            if name and row.get("definition"):
                out.setdefault(str(row["definition"]), name)
    return out


def load_types(pack: str) -> list:
    """The pack's NPC types, in file order. Raises NpcError when definitions.json is unreadable."""
    path = os.path.join(pack, DIR, DEFINITIONS)
    try:
        doc = _read(path)
    except FileNotFoundError:
        raise NpcError(f"the pack has no {DIR}/{DEFINITIONS}") from None
    except (OSError, ValueError) as exc:
        raise NpcError(f"{DIR}/{DEFINITIONS} could not be read: {exc}") from None
    names = pack_names(pack)
    out = []
    for row in doc.get("definitions", []) if isinstance(doc, dict) else []:
        try:
            combat = row.get("combat") or {}
            team = str(row.get("team", ""))
            out.append(NpcType(
                id=str(row["id"]), index=int(row.get("definition_index", 0)),
                file_id=int(row.get("file_id", 0)), primary=int(row.get("primary", 0)),
                level=int(row.get("level", 0)), team=team,
                hostile=bool(combat.get("hostile")) or team in HOSTILE_TEAMS,
                speed=float(row.get("speed", 0.0)), name=names.get(str(row["id"])),
                bodies=body_files(row), scale=visual_scale(row)))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def behaviors(pack: str) -> list:
    """The behavior names npcs/behaviors.lua returns, for a placement's `behavior` field."""
    path = os.path.join(pack, DIR, BEHAVIORS)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    except OSError:
        return []
    try:
        from .lua.source import returned_table
        table = returned_table(text)
    except Exception:                                            # noqa: BLE001 - a broken file
        return []
    if table is None:
        return []
    return [str(k) for k in table.keys() if isinstance(k, str)]


# ------------------------------------------------------------------ a map's placements

def next_id(project, used=()) -> str:
    """A fresh placement id for the project's map: `<map id>-cs-<n>`."""
    taken = {n.id for n in project.npcs} | set(used)
    prefix = f"{int(project.map_id)}-cs-"
    n = 1
    for t in taken:
        if t.startswith(prefix) and t[len(prefix):].isdigit():
            n = max(n, int(t[len(prefix):]) + 1)
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _num(v: float):
    """A coordinate as the file keeps it: whole numbers stay whole."""
    v = round(float(v), 6)
    return float(int(v)) if v == int(v) else v


def direction_angle(direction):
    """The facing a row's `direction` vector gives, or None when it is not one."""
    try:
        dx, dy = float(direction[0]), float(direction[1])
    except (TypeError, ValueError, IndexError):
        return None
    if dx == 0.0 and dy == 0.0:
        return None
    return math.atan2(dy, dx)


def placement_row(npc, origin=(0.0, 0.0)) -> dict:
    """One PlacedNpc as the world's placement row, in game coordinates (editor + origin).

    The fields the editor does not edit come from `extra` as they were read. A row that faced by
    a `direction` vector keeps it while the NPC is not turned; turned, it is written as an angle."""
    ox, oy = float(origin[0]), float(origin[1])
    row = {"id": npc.id, "definition": npc.definition,
           "x": _num(npc.x + ox), "y": _num(npc.y + oy), "plane": 0}
    extra = {k: v for k, v in (npc.extra or {}).items() if k in EXTRA_KEYS}
    row.update(extra)
    row.setdefault("effects", 0)
    row.setdefault("h0023", 1.0)
    if npc.angle is not None:
        kept = direction_angle(extra.get("direction"))
        if kept is not None and abs(kept - npc.angle) < 1e-6:
            row.pop("angle", None)              # not turned: the row's own vector stands
        else:
            row.pop("direction", None)          # the world takes an angle or a direction, not both
            row["angle"] = round(float(npc.angle), 6)
    if npc.name:
        row["name"] = encode_name(npc.name)
    if npc.level is not None:
        row["level"] = int(npc.level)
    if npc.behavior:
        row["behavior"] = npc.behavior
    order = (npc.extra or {}).get(KEY_ORDER)
    if isinstance(order, list):                 # a row read from a file keeps its key order
        rank = {k: i for i, k in enumerate(order)}
        row = dict(sorted(row.items(), key=lambda kv: rank.get(kv[0], len(rank))))
    return row


def npcs_from_rows(rows, origin=(0.0, 0.0)) -> list:
    """A placements file's rows as PlacedNpc, in editor coordinates (game - origin)."""
    from .model import PlacedNpc
    ox, oy = float(origin[0]), float(origin[1])
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        extra = {k: v for k, v in row.items()
                 if k not in ("id", "definition", "x", "y", "angle", "name", "level", "behavior")}
        extra[KEY_ORDER] = list(row)
        name = decode_name(row.get("name"))
        if row.get("name") is not None and name is None:
            extra["name"] = row["name"]          # encoded string ids: carried, not shown
        angle = row.get("angle")
        if angle is None:
            angle = direction_angle(row.get("direction"))
        out.append(PlacedNpc(id=str(row["id"]), definition=str(row["definition"]),
                             x=float(row["x"]) - ox, y=float(row["y"]) - oy,
                             angle=None if angle is None else float(angle), name=name,
                             level=None if row.get("level") is None else int(row["level"]),
                             behavior=None if row.get("behavior") is None else str(row["behavior"]),
                             extra=extra))
    return out


def placements_file(project) -> dict:
    origin = getattr(project, "origin", None) or (0.0, 0.0)
    rows = [placement_row(n, origin) for n in project.npcs]
    if getattr(project, "stock", False):
        doc = dict(project.npc_file or {})       # the map's own note and routes, as read
        doc["map_id"] = int(project.map_id)
        doc["placements"] = rows
        doc.setdefault("routes", [])
        order = ["map_id", "note", "placements", "routes"]
        return {k: doc[k] for k in order if k in doc} | {k: v for k, v in doc.items()
                                                        if k not in order}
    return {
        "map_id": int(project.map_id),
        "note": f"Written by the Kryta Construction Set from the map project '{project.slug}'. "
                f"Edit the NPCs there; publishing the map writes this file again.",
        "placements": rows,
        "routes": [],
    }


def check(pack: str, project) -> list:
    """What the world would refuse in the project's NPCs, worded for a person ([] when none)."""
    problems = []
    try:
        known = {t.id for t in load_types(pack)}
    except NpcError as exc:
        return [str(exc)]
    behave = set(behaviors(pack))
    routes = {str(r.get("id")) for r in ((getattr(project, "npc_file", None) or {}).get("routes") or [])
              if isinstance(r, dict)}
    seen = set()
    for n in project.npcs:
        at = f"NPC {n.id}"
        if n.id in seen:
            problems.append(f"{at}: the id is used twice")
        seen.add(n.id)
        if n.definition not in known:
            problems.append(f"{at}: its type {n.definition} is not in {DIR}/{DEFINITIONS}")
        if n.behavior and n.behavior not in behave:
            problems.append(f"{at}: behavior {n.behavior!r} is not in {DIR}/{BEHAVIORS}")
        if n.name:
            try:
                encode_name(n.name)
            except NpcError as exc:
                problems.append(f"{at}: {exc}")
        route = (n.extra or {}).get("route")
        if route and route not in routes:
            problems.append(f"{at}: it names route {route!r}, which its map's file does not hold")
    return problems


def _write_json(path: str, doc) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".kryta-cs.tmp"
    with open(tmp, "w", encoding="ascii", newline="\n") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=True)
        fh.write("\n")
    os.replace(tmp, path)


def publish(pack: str, project, marker: dict) -> dict:
    """Write the map's placements file, setting the pack's own aside. -> the marker's NPC keys.

    `marker` is the map's kryta-cs.json as it stood before this publish."""
    mid = int(project.map_id)
    target = placement_path(pack, mid)
    aside = displaced_path(pack, mid)
    if getattr(project, "stock", False):
        return publish_stock(pack, project)
    if marker.get("npcs_written") and os.path.exists(target):
        os.remove(target)                   # ours, from the last publish
    if project.npcs:
        problems = check(pack, project)
        if problems:
            raise NpcError("the map's NPCs would stop the world from starting:\n  "
                           + "\n  ".join(problems))
        if os.path.exists(target):
            os.makedirs(os.path.dirname(aside), exist_ok=True)
            if os.path.exists(aside):
                os.remove(aside)            # the pack re-added its file; the newer copy wins
            shutil.move(target, aside)
        _write_json(target, placements_file(project))
        return {"npcs_written": True, "npcs_displaced": os.path.exists(aside)}
    if os.path.exists(aside) and not os.path.exists(target):
        shutil.move(aside, target)
    return {"npcs_written": False, "npcs_displaced": False}


def publish_stock(pack: str, project) -> dict:
    """A game map's NPCs ARE the pack's own file: write it in place, routes and notes kept.

    Nothing is set aside, because there is nothing to stand in for; git is how this is undone."""
    problems = check(pack, project)
    if problems:
        raise NpcError("the map's NPCs would stop the world from starting:\n  "
                       + "\n  ".join(problems))
    _write_json(placement_path(pack, project.map_id), placements_file(project))
    return {"npcs_written": True, "npcs_displaced": False}


def unpublish(pack: str, map_id: int, marker: dict) -> list:
    """Remove our placements file and put the pack's own back. -> what was done."""
    done = []
    target = placement_path(pack, map_id)
    aside = displaced_path(pack, map_id)
    if marker.get("npcs_written") and os.path.exists(target):
        os.remove(target)
        done.append(f"removed {DIR}/{PLACEMENTS}/{int(map_id)}.json")
    if os.path.exists(aside) and not os.path.exists(target):
        shutil.move(aside, target)
        done.append(f"put back {DIR}/{PLACEMENTS}/{int(map_id)}.json")
    return done
