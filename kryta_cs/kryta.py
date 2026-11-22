"""The server half: the map folder Kryta hosts the map from, and the navigation cache.

A map is a file, not a row (Kryta's docs/decisions/0014-the-world-content-is-a-pack.md).
The world reads its pack at start-up - `WorldPack` in worldserver.json, by default
`config/World/Vanilla` - and every map is one folder under `<pack>/maps/`:

    <pack>/maps/<campaign>/<area>/<kind>/<map id>/basic.json     the map's record
    <pack>/maps/<campaign>/<area>/<kind>/<map id>/spawns.json    where a character may stand
    <pack>/maps/<campaign>/<area>/<kind>/<map id>/portals.json   arches and arrival points

The folder's name must be the map id its basic.json states (game/src/Maps/Pack.rs), and
the world reads every folder it finds, so one id must live in exactly one folder.

Publishing a custom map therefore:
  - writes `<pack>/maps/Custom/<map id>/` (basic.json, spawns.json, and kryta-cs.json, the
    editor's own note of what it did), and
  - moves the pack's own folder for that id, if there is one, out of `maps/` to
    `<pack>/kryta-cs-displaced/<its path under maps>/`, so the id is not stated twice.
Taking the map off the world reverses both. Nothing is deleted from the pack: the
displaced folder is moved back exactly as it was.

The world no longer reads Gw.dat (docs/decisions/0014-headless-navigation-assets.md): it
walks from `data/path-graphs.bin`, and a map that cache does not hold answers NoGraph.
The control panel (kryta-management-service) adds a map's graph to that cache from the
archive its `MapArchiveFile` names, which is what `point_manager_at` and
`dump_path_graph` drive.
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

from .model import MapProject

WORLD_CONFIG = os.path.join("worldserver", "worldserver.json")
MANAGE_CONFIG = os.path.join("tools", "manage", "manage.json")
TRAVEL_TARGETS = os.path.join("game", "src", "Maps", "TravelTargets.rs")
DEFAULT_PACK = "config/World/Vanilla"
DEFAULT_PATH_GRAPHS = "data/path-graphs.bin"
CUSTOM_DIR = "Custom"                       # <pack>/maps/Custom/<map id>/
DISPLACED_DIR = "kryta-cs-displaced"        # <pack>/kryta-cs-displaced/<path under maps>/
MARKER = "kryta-cs.json"

# The old publish wrote SQL rows; the world ignores them now and the files are cleaned up.
WORLD_SQL_DIR = os.path.join("data", "sql", "base", "db_world")
CUSTOM_PREFIX = "custom-map-"


# ------------------------------------------------------------------ settings files

def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _setting(doc, key):
    if not isinstance(doc, dict):
        return None
    v = doc.get(key)
    return v.get("value") if isinstance(v, dict) else v


def world_config_path(repo: str) -> str:
    return os.path.join(repo, WORLD_CONFIG)


def manage_config_path(repo: str) -> str:
    return os.path.join(repo, MANAGE_CONFIG)


def read_world_setting(repo: str, key: str):
    return _setting(_read_json(world_config_path(repo)), key)


def read_manage_setting(repo: str, key: str):
    return _setting(_read_json(manage_config_path(repo)), key)


def _abs(repo: str, value: str) -> str:
    if os.path.isabs(value) or re.match(r"^[A-Za-z]:[/\\]", value):
        return value
    return os.path.join(repo, value)


def pack_dir(repo: str) -> str:
    """The world pack the world serves: worldserver.json `WorldPack`, else config/World/Vanilla."""
    return _abs(repo, read_world_setting(repo, "WorldPack") or DEFAULT_PACK)


def maps_dir(repo: str) -> str:
    return os.path.join(pack_dir(repo), "maps")


def custom_folder(repo: str, map_id: int) -> str:
    return os.path.join(maps_dir(repo), CUSTOM_DIR, str(int(map_id)))


def displaced_root(repo: str) -> str:
    return os.path.join(pack_dir(repo), DISPLACED_DIR)


# ------------------------------------------------------------------ the catalogue

class CatalogueRow:
    def __init__(self, map_id, map_type, file_id, name, party_cap=None, instance_value=None, folder=None):
        self.map_id, self.map_type, self.file_id = map_id, map_type, file_id
        self.name, self.party_cap, self.instance_value = name, party_cap, instance_value
        self.folder = folder

    @property
    def hostable(self):
        return self.map_type is not None

    def label(self):
        kind = {0: "outpost", 1: "explorable", 11: "creation"}.get(self.map_type, "not hosted")
        return f"{self.map_id}  {self.name or '(no name)'}  [{kind}]"


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _map_folders(root: str):
    """Every folder under `root` that holds a basic.json, as the world's own walk finds them."""
    for here, _dirs, files in os.walk(root):
        if "basic.json" in files:
            yield here


def _record(folder: str):
    return _read_json(os.path.join(folder, "basic.json"))


def read_catalogue(repo: str, include_custom: bool = False) -> dict:
    """Every map the pack states, by id. -> {id: CatalogueRow}

    With include_custom False (the default) a map this editor published is reported as the
    pack's own record it displaced, so the catalogue is the game's, not the editor's.
    """
    rows = {}
    root = maps_dir(repo)
    custom_root = os.path.join(root, CUSTOM_DIR)
    sources = [root]
    if not include_custom:
        sources.append(os.path.join(displaced_root(repo), "maps"))
    for source in sources:
        if not os.path.isdir(source):
            continue
        for folder in _map_folders(source):
            is_custom = os.path.normcase(os.path.abspath(folder)).startswith(
                os.path.normcase(os.path.abspath(custom_root)) + os.sep)
            if is_custom and not include_custom:
                continue
            rec = _record(folder)
            if not isinstance(rec, dict):
                continue
            mid = _int(rec.get("map_id"))
            if mid is None:
                continue
            rows[mid] = CatalogueRow(mid, _int(rec.get("map_type")), _int(rec.get("map_file_id")) or 0,
                                     rec.get("name") or "", _int(rec.get("max_player_size")),
                                     _int(rec.get("instance_value")), folder)
    return rows


def tp_slots(repo: str) -> dict:
    """Map ids `/tp` can reach, from game/src/Maps/TravelTargets.rs. -> {map_id: [alias, ...]}

    `/tp` resolves through a fixed table ported from the reference (TravelTargets.rs), so a
    map is reachable in game today only under one of these ids. Publishing onto one of them
    makes `/tp <alias>` load the custom map until it is taken off the world again, which
    puts the pack's own folder for that id back.
    """
    out = {}
    try:
        with open(os.path.join(repo, TRAVEL_TARGETS), "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return out
    for m in re.finditer(r'alias:\s*"([^"]+)",\s*label:\s*"([^"]*)",\s*map_id:\s*(\d+),\s*map_type:\s*(\d+),'
                         r'\s*hosted:\s*(true|false)', text):
        if m.group(5) == "true":
            out.setdefault(int(m.group(3)), []).append(m.group(1))
    return out


def tp_slot_types(repo: str) -> dict:
    """{map_id: map_type} of the hosted `/tp` slots, so a mismatched map type can be flagged."""
    out = {}
    try:
        with open(os.path.join(repo, TRAVEL_TARGETS), "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return out
    for m in re.finditer(r'map_id:\s*(\d+),\s*map_type:\s*(\d+),\s*hosted:\s*true', text):
        out.setdefault(int(m.group(1)), int(m.group(2)))
    return out


def _marker(folder: str):
    return _read_json(os.path.join(folder, MARKER))


def maps_claimed(repo: str) -> dict:
    """Map ids this editor has published. -> {map_id: (slug, folder name)}"""
    out = {}
    root = os.path.join(maps_dir(repo), CUSTOM_DIR)
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        mk = _marker(folder)
        if isinstance(mk, dict) and mk.get("state") == "published" and _int(mk.get("map_id")) is not None:
            out[int(mk["map_id"])] = (mk.get("slug") or "?", name)
    return out


def free_map_ids(repo: str) -> list:
    """Catalogue ids the world does not host: a custom map can take one over."""
    cat = read_catalogue(repo)
    return [r for r in sorted(cat.values(), key=lambda r: r.map_id) if not r.hostable]


# ------------------------------------------------------------------ publish / unpublish

def _pack_folders_for(repo: str, map_id: int) -> list:
    """The pack's own folders (not Custom/) that state `map_id`."""
    out = []
    root = maps_dir(repo)
    custom_root = os.path.normcase(os.path.abspath(os.path.join(root, CUSTOM_DIR)))
    if not os.path.isdir(root):
        return out
    for folder in _map_folders(root):
        if os.path.normcase(os.path.abspath(folder)).startswith(custom_root + os.sep):
            continue
        rec = _record(folder)
        if isinstance(rec, dict) and _int(rec.get("map_id")) == int(map_id):
            out.append(folder)
    return out


def _displaced_folder(repo: str, rel: str) -> str:
    return os.path.join(displaced_root(repo), "maps", rel)


def _write_json(path: str, doc) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _f(v: float) -> float:
    return float(round(v, 2))


def render_basic(project: MapProject, original: dict | None) -> dict:
    """The custom map's basic.json: the pack's record for the id it stands in for, with this
    map's kind, archive file id and name. The instance value is kept, because the client
    resolves a destination by it (a map without one cannot be named)."""
    if project.file_id is None:
        raise ValueError("the map has no file id yet; install it into Gw.dat first")
    doc = {k: v for k, v in (original or {}).items() if k not in ("doc", "note", "information")}
    doc.update({
        "note": f"{project.name}: a map authored in the Kryta Construction Set (kryta-cs). Written by the "
                f"editor and replaced by its next publish; edit the map project, not this file.",
        "map_id": int(project.map_id),
        "name": project.name,
        "map_type": int(project.map_type),
        "map_file_id": int(project.file_id),
    })
    doc.setdefault("instance_value", None)
    return doc


def render_spawns(project: MapProject) -> dict:
    spawns = []
    for i, s in enumerate(project.spawns):
        row = {"idx": i, "x": _f(s.x), "y": _f(s.y), "plane": 0}
        if s.angle is not None:
            row["angle"] = _f(s.angle)
        spawns.append(row)
    return {"map_id": int(project.map_id), "spawns": spawns}


def legacy_sql_files(repo: str, slug: str | None = None) -> list:
    """The custom-map-*.sql files the old publish wrote (the world no longer reads map rows)."""
    pattern = CUSTOM_PREFIX + (slug + "-" if slug else "") + "*.sql"
    return sorted(glob.glob(os.path.join(repo, WORLD_SQL_DIR, pattern)))


custom_files = legacy_sql_files       # the old name, for any caller still using it


def _remove_legacy_sql(repo: str, slug: str) -> list:
    removed = []
    for path in legacy_sql_files(repo, slug):
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


def _move_back(repo: str, rel: str) -> str | None:
    """Return a displaced folder to its place under maps/. -> where it went, or None."""
    src = _displaced_folder(repo, rel)
    if not os.path.isdir(src):
        return None
    dst = os.path.join(maps_dir(repo), rel)
    if os.path.exists(dst):
        # The pack already has that folder again (an update put it back): keep the pack's copy.
        shutil.rmtree(src)
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return dst


def _prune_empty(path: str, stop: str) -> None:
    path, stop = os.path.abspath(path), os.path.abspath(stop)
    while path.startswith(stop) and path != stop:
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


def publish(repo: str, project: MapProject) -> str:
    """Write the map's folder into the world pack. Read by the world at its next start.

    -> the folder written. If the pack has its own folder for this id it is moved aside
    (see the module docstring), and the record it held is the base of the new basic.json.
    """
    if project.file_id is None:
        raise ValueError("the map has no file id yet; install it into Gw.dat first")
    if not os.path.isdir(maps_dir(repo)):
        raise ValueError(f"no world pack at {maps_dir(repo)}; is WorldPack in worldserver.json right?")
    mid = int(project.map_id)
    folder = custom_folder(repo, mid)
    marker = _marker(folder) or {}
    displaced = marker.get("displaced")          # path under maps/ of the folder we moved aside

    # Another map of this editor was published onto the same id: give its displaced folder
    # record over to this one rather than losing track of it.
    original = None
    if displaced and os.path.isdir(_displaced_folder(repo, displaced)):
        original = _record(_displaced_folder(repo, displaced))

    for found in _pack_folders_for(repo, mid):
        rel = os.path.relpath(found, maps_dir(repo))
        target = _displaced_folder(repo, rel)
        if os.path.exists(target):
            shutil.rmtree(target)             # an update re-added the folder; the newer copy wins
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(found, target)
        _prune_empty(os.path.dirname(found), maps_dir(repo))
        displaced = rel
        original = _record(target)

    os.makedirs(folder, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    _write_json(os.path.join(folder, "basic.json"), render_basic(project, original))
    _write_json(os.path.join(folder, "spawns.json"), render_spawns(project))
    portals = os.path.join(folder, "portals.json")
    if os.path.exists(portals):
        os.remove(portals)                    # a custom map has no doorways yet
    _write_json(os.path.join(folder, MARKER), {
        "note": "Written by the Kryta Construction Set. 'displaced' is the pack folder this map stands in "
                "for, kept under kryta-cs-displaced/ until the map is taken off the world.",
        "map_id": mid,
        "slug": project.slug,
        "state": "published",
        "published": stamp,
        "project": project.path or None,
        "displaced": displaced,
    })
    _remove_legacy_sql(repo, project.slug)
    return folder


def unpublish(repo: str, project: MapProject) -> str:
    """Take the map off the world: remove its folder and put the pack's own back.

    -> a short description of what was done.
    """
    mid = int(project.map_id)
    folder = custom_folder(repo, mid)
    marker = _marker(folder) or {}
    displaced = marker.get("displaced")
    done = []
    if os.path.isdir(folder):
        shutil.rmtree(folder)
        _prune_empty(os.path.dirname(folder), maps_dir(repo))
        done.append(f"removed {folder}")
    if displaced:
        back = _move_back(repo, displaced)
        if back:
            done.append(f"put back {back}")
        _prune_empty(os.path.dirname(_displaced_folder(repo, displaced)), pack_dir(repo))
    for path in _remove_legacy_sql(repo, project.slug):
        done.append(f"removed {os.path.basename(path)}")
    return "; ".join(done) if done else f"map {mid} was not published"


def published_folder(repo: str, project: MapProject) -> str | None:
    folder = custom_folder(repo, project.map_id)
    mk = _marker(folder)
    if isinstance(mk, dict) and mk.get("slug") == project.slug and mk.get("state") == "published":
        return folder
    return None


# ------------------------------------------------------------------ the archive and the graph cache

def server_archive(repo: str) -> str | None:
    """The Gw.dat the Kryta tools use: the manager's MapArchiveFile, else the world's old GwDat."""
    v = read_manage_setting(repo, "MapArchiveFile") or read_world_setting(repo, "GwDat")
    return _abs(repo, v) if v else None


def _set_settings(path: str, values: dict) -> bool:
    """Set top-level settings in a Kryta JSON config, keeping a .kryta-cs.bak once. -> changed?"""
    doc = _read_json(path)
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a readable JSON settings file")
    if all(_setting(doc, k) == v for k, v in values.items()):
        return False
    bak = path + ".kryta-cs.bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    for key, new in values.items():
        cur = doc.get(key)
        if isinstance(cur, dict) and "value" in cur:
            cur["value"] = new
        else:
            doc[key] = {"value": new}
    _write_json(path, doc)
    return True


def point_manager_at(repo: str, dat: str) -> list:
    """Make the control panel extract graphs from `dat`, and keep the world on the cache.

    Sets manage.json MapArchiveFile to the archive. If an older version of this editor
    cleared worldserver.json PathGraphs (the world used to import from Gw.dat), it is put
    back to data/path-graphs.bin: the world refuses to start without it now.
    -> the files changed.
    """
    changed = []
    value = dat.replace("\\", "/")
    mpath = manage_config_path(repo)
    if os.path.exists(mpath) and _set_settings(mpath, {"MapArchiveFile": value}):
        changed.append(mpath)
    wpath = world_config_path(repo)
    if os.path.exists(wpath) and not read_world_setting(repo, "PathGraphs"):
        if _set_settings(wpath, {"PathGraphs": DEFAULT_PATH_GRAPHS}):
            changed.append(wpath)
    return changed


point_server_at = point_manager_at    # the old name


def manager_url(repo: str) -> str:
    bind = read_manage_setting(repo, "Bind") or "127.0.0.1"
    if bind in ("0.0.0.0", "::", ""):
        bind = "127.0.0.1"
    port = _int(read_manage_setting(repo, "Port")) or 6602
    return f"http://{bind}:{port}"


def dump_path_graph(repo: str, map_id: int, timeout: float = 120.0) -> dict:
    """Ask the running control panel to add this map's walk graph to data/path-graphs.bin.

    The graph is the mesh the client compiled into Gw.dat on its first load of the map, so
    this is done after that load. The panel reads the archive its MapArchiveFile names (as
    it was when the panel started). -> the panel's answer, e.g. {"map_id", "file_id", ...}
    Raises RuntimeError with the panel's own words when it refuses or is not running.
    """
    url = manager_url(repo) + "/api/navigation/dump"
    token = read_manage_setting(repo, "Token") or ""
    if token:
        url += "?token=" + urllib.parse.quote(token)
    body = urllib.parse.urlencode({"map_id": str(int(map_id))}).encode("ascii")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode("utf-8")).get("error") or str(exc)
        except Exception:                                            # noqa: BLE001
            msg = str(exc)
        raise RuntimeError(msg) from None
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"the control panel does not answer at {manager_url(repo)} ({exc}); "
                           f"start it (run_server.py, 'manage') and try again") from None


# ------------------------------------------------------------------ choices for the UI

def map_id_choices(repo: str | None, slug: str | None = None) -> list:
    """(map_id, label) pairs for a custom map: the /tp test slots first, then free catalogue ids.

    A /tp slot is the only kind a player can reach in game today (see tp_slots); it takes the
    retail map's place while the custom map is published. A free id is one the world does not
    host, so nothing is displaced, but nothing travels to it yet either.
    """
    if not repo:
        return [(167, "167")]
    cat = read_catalogue(repo)
    claimed = maps_claimed(repo)
    out = []

    def taken(mid):
        other = claimed.get(mid)
        return f"  - published by {other[0]}" if other and other[0] != slug else ""

    slots = tp_slots(repo)
    order = sorted(slots, key=lambda m: (m != 449, m))      # Kamadan first: outside pre-Searing
    for mid in order:
        r = cat.get(mid)
        name = r.name if r else str(mid)
        out.append((mid, f"{mid}  /tp {slots[mid][-1]}  (stands in for {name}){taken(mid)}"))
    for r in sorted(cat.values(), key=lambda r: r.map_id):
        if not r.hostable:
            out.append((r.map_id, f"{r.map_id}  free id, not reachable by /tp yet{taken(r.map_id)}"))
    return out
