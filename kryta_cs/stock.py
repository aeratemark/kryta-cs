"""Game maps: open one of ArenaNet's maps from Gw.dat, edit its props, flora and NPCs, patch it back.

A map project normally BUILDS a map: Rurik assembles a whole file from the editor's height field
and a donor's look. A game map project does not. It is read out of the map's own file, and its
build is that same file with two parts replaced and every other part carried byte for byte:

    props (0x10000004 and its model list 0x11000004)    the editor's props
    flora (0x10000003, Zones)                          the map's own flora types, the editor's areas

The terrain, the walkable mesh's source, the environment, the sound and everything Rurik does
not decode are the map's own. The terrain is SHOWN, and the editor's sculpt tool is off for a
game map; reshaping it would make the map's own path data disagree with its ground.

THE BASE IS THE UNTOUCHED MAP. Every build starts from the map as ArenaNet shipped it, so a second
build does not patch the first (read_original): the copy the editor saved just before it first wrote
the map (`kryta-maps/game-0x<id>-original.bin` beside Gw.dat), else the whole-archive backup
(`Gw.dat.kryta-original`), else Gw.dat itself, which the editor has then never written for this
map. Gw.dat holds a map's file only once the official client has downloaded it, so a map the
backup predates is read from Gw.dat and saved before the first write. A prop the map shipped with remembers its index
there (PlacedProp.src); the props chunk's two reference tables (tags 4 and 6, `{value, prop}`)
point into the prop array, so they are carried over for the props that remain and renumbered to
where those props now stand, and dropped for a deleted one. A build that changed nothing writes
the map's own bytes back.

COORDINATES. The editor's grid starts at (0, 0); a game map's world is its Map Parameters rect
(0x1000000C, x0 y0 x1 y1), centred on the origin. The editor keeps everything in its own frame,
`origin` = (x0, y0) is added back when the map's props, areas and NPCs are written.

ONE FILE CAN BE SEVERAL MAPS. Pre-Searing's file 113021 is both Lakeside County (146) and
Ascalon City (148): patching its props changes both. The NPCs are per map id.
"""

from __future__ import annotations

import json
import os

from . import flora as flora_mod
from . import npcs as npcs_mod
from . import paths
from . import zones as zones_mod
from .model import FloraArea, MapProject, PlacedProp, slugify

paths.bootstrap_rurik()

import deploy                                                  # noqa: E402
import mapbuild                                                # noqa: E402
import mapchunks                                               # noqa: E402
import mapfile                                                 # noqa: E402
import props as props_mod                                      # noqa: E402
import stripbuild as sb                                        # noqa: E402
import strippedterrain as stx                                  # noqa: E402
from archive import Archive, file_id_table                     # noqa: E402

BACKUP_SUFFIX = ".kryta-original"      # pipeline.BACKUP_SUFFIX: the archive as it was first found
WORK_DIR_NAME = "kryta-maps"           # pipeline.WORK_DIR_NAME: beside the archive


class StockError(ValueError):
    pass


def original_path(dat: str, file_id: int) -> str:
    """Where the editor keeps a game map's file as it was before the editor first wrote it."""
    return os.path.join(os.path.dirname(os.path.abspath(dat)), WORK_DIR_NAME,
                        f"game-0x{int(file_id):X}-original.bin")


class _NotThere(StockError):
    pass


def read_original(dat: str, file_id: int):
    """The map's Stripped stream as it shipped. -> (MapFile, where it was read from)."""
    saved = original_path(dat, file_id)
    if os.path.isfile(saved):
        with open(saved, "rb") as fh:
            return mapfile.MapFile.decode(fh.read(), strict=False), os.path.basename(saved)
    for src in (dat + BACKUP_SUFFIX, dat):
        if not os.path.isfile(src):
            continue
        try:
            return read_map(src, file_id), os.path.basename(src)
        except _NotThere:
            continue
    raise StockError(
        f"Map file 0x{int(file_id):X} is not in {dat}. Guild Wars keeps a map's file only once the "
        f"official client has downloaded it. Your other Guild Wars install may have it; or start "
        f"the official Gw.exe with -image, which downloads every file, and copy that Gw.dat here.")


def save_original(dat: str, file_id: int) -> str:
    """Keep the map's untouched file before the editor first writes it. -> its path."""
    saved = original_path(dat, file_id)
    if not os.path.isfile(saved):
        mf, _src = read_original(dat, file_id)
        os.makedirs(os.path.dirname(saved), exist_ok=True)
        tmp = saved + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(mf.encode())
        os.replace(tmp, saved)
    return saved


def read_map(dat: str, file_id: int):
    """The map file's Stripped stream (`mapfile.MapFile`) out of the archive at `dat`."""
    if not dat or not os.path.isfile(dat):
        raise StockError(f"There is no archive at {dat!r}.")
    with Archive(dat) as ar:
        row = file_id_table(ar).get(int(file_id))
        if row is None:
            raise _NotThere(f"map file 0x{int(file_id):X} is not in {dat}")
        try:
            return deploy._stripped_of(ar, row, "map")
        except Exception as exc:                                 # noqa: BLE001 - Refused and kin
            raise StockError(f"map file 0x{int(file_id):X} could not be read: {exc}") from None


def map_rect(mf) -> tuple:
    c = mf.find(sb.MAP_PARAMS)
    if c is None:
        raise StockError("the map has no Map Parameters part, so where it lies is unknown")
    rect, flags, _uuid, _v, _sig = mapbuild.decode_map_parameters(c.payload())
    return tuple(float(v) for v in rect), int(flags)


def _deps(mf, chunk_id) -> list:
    c = mf.find(chunk_id)
    return list(mapchunks.decode_dependencies(c.payload()).file_ids) if c is not None else []


# ------------------------------------------------------------------ open

def open_map(dat: str, file_id: int, map_id: int, name: str = "", map_type: int = 1,
             pack: str | None = None, log=None) -> MapProject:
    """A game map as a project: its ground to look at, its props, flora areas and NPCs to edit."""
    log = log or (lambda _s: None)
    mf, src = read_original(dat, file_id)
    log(f"map file 0x{int(file_id):X} read from {src}")
    (x0, y0, x1, y1), flags = map_rect(mf)
    trn_c = mf.find(sb.TERRAIN)
    if trn_c is None:
        raise StockError("the map has no terrain part")
    trn = stx.StrippedTerrain.decode(trn_c.payload())
    dx, dy = int(trn.dim_x), int(trn.dim_y)
    if abs((x1 - x0) - dx * sb.CELL) > 0.5 or abs((y1 - y0) - dy * sb.CELL) > 0.5:
        raise StockError(f"the map's rect ({x0}, {y0}, {x1}, {y1}) is not its {dx}x{dy} cells")

    title = name or f"Map {int(map_id)}"
    p = MapProject(name=title, slug=f"game-{int(map_id)}-{slugify(title)}", map_id=int(map_id),
                   map_type=int(map_type), dim_x=dx, dim_y=dy)
    p.stock = True
    p.origin = [x0, y0]
    p.file_id = int(file_id)
    p.donor_file_id = int(file_id)          # its own textures and prop models fill the palette
    p.constants_file_id = int(file_id)
    p.map_flags = flags
    p.water_level = None
    p.heights = [[0.0] * dx for _ in range(dy)]
    p.from_rurik_heights(trn.heights)
    p.tiles = list(trn.tiles)
    p.spawns = []

    pc = mf.find(sb.PROPS)
    if pc is not None:
        sp = props_mod.StrippedProps.decode(pc.payload())
        models = _deps(mf, sb.PROPS_DEPS)
        for i, q in enumerate(sp.props):
            if not 0 <= q.model < len(models):
                raise StockError(f"prop {i} names model {q.model}; the map lists {len(models)}")
            p.props.append(PlacedProp(file_id=int(models[q.model]), model=int(q.model),
                                      x=float(q.x) - x0, y=float(q.y) - y0, z=-float(q.z),
                                      rot=tuple(q.rot), scale=int(q.scale), flags=int(q.flags),
                                      outline=[tuple(v) for v in q.outline], grid_locked=False,
                                      src=i))
    zc = mf.find(zones_mod.STRIPPED_ZONES)
    if zc is not None:
        z = zones_mod.Zones.decode(zc.payload())
        names = {t.id: t.short_name for t in z.types}
        for a in z.areas:
            p.flora.append(FloraArea(names.get(a.type_id, str(a.type_id)),
                                     [(float(x) - x0, float(y) - y0) for x, y in a.points],
                                     int(a.type_id),
                                     "" if a.flags == zones_mod.AREA_FLAGS else a.flags.hex()))
    if pack:
        path = npcs_mod.placement_path(pack, map_id)
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as fh:
                doc = json.load(fh)
            p.npcs = npcs_mod.npcs_from_rows(doc.get("placements", []), (x0, y0))
            p.npc_file = {k: v for k, v in doc.items() if k != "placements"}
        else:
            p.npc_file = {"map_id": int(map_id), "routes": []}
    log(f"{title}: {dx}x{dy} cells, world ({x0:.0f}, {y0:.0f}) to ({x1:.0f}, {y1:.0f}), "
        f"{len(p.props)} prop(s), {len(p.flora)} flora area(s), {len(p.npcs)} NPC(s)")
    return p


# ------------------------------------------------------------------ build

def _clockwise(points: list) -> list:
    area = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        area += ax * by - bx * ay
    return list(reversed(points)) if area > 0 else list(points)


def _props_part(project, base, ox, oy, log):
    """(props payload, model list payload or None when unchanged) for the project's props."""
    pc = base.find(sb.PROPS)
    orig = props_mod.StrippedProps.decode(pc.payload()) if pc is not None else \
        props_mod.StrippedProps()
    models = _deps(base, sb.PROPS_DEPS)
    first = len(models)
    out, moved = [], {}
    for q in project.props:
        fid = int(q.file_id)
        if 0 <= q.model < len(models) and models[q.model] == fid:
            m = q.model
        elif fid in models:
            m = models.index(fid)
        else:
            models.append(fid)
            m = len(models) - 1
        if q.src is not None and 0 <= q.src < len(orig.props) and q.src not in moved:
            moved[q.src] = len(out)
        out.append(props_mod.Prop(model=m, x=float(q.x) + ox, y=float(q.y) + oy, z=-float(q.z),
                                  rot=tuple(int(v) for v in q.rot), scale=int(q.scale),
                                  flags=int(q.flags),
                                  outline=[tuple(int(v) for v in pt) for pt in q.outline]))
    if len(out) > 0xFFFF:
        raise StockError(f"{len(out)} props; the map's props part holds at most 65535")

    def carry(table):
        if table is None:
            return None
        return [type(r)(r.value, moved[r.prop]) for r in table if r.prop in moved]

    sp = props_mod.StrippedProps(out, carry(orig.refs4), carry(orig.refs6), orig.tag6_word,
                                 orig.version, orig.signature)
    sp.check_references()
    dropped = (len(orig.refs4) - len(sp.refs4)) + (len(orig.refs6 or []) - len(sp.refs6 or []))
    log(f"props: {len(out)} ({len(moved)} of the map's {len(orig.props)} kept, "
        f"{len(out) - len(moved)} new), {len(models) - first} model(s) added to its list"
        + (f", {dropped} reference(s) of removed props dropped" if dropped else ""))
    deps = mapchunks.encode_dependencies(models) if len(models) != first else None
    return sp.encode(), deps


def _flora_part(project, base, ox, oy, log):
    zc = base.find(zones_mod.STRIPPED_ZONES)
    areas = flora_mod.all_areas(project)
    if zc is None:
        if areas:
            raise StockError("this map has no flora part, so it has no flora types to paint")
        return None
    z = zones_mod.Zones.decode(zc.payload())
    ids = {t.id for t in z.types}
    by_name = {}
    for t in z.types:
        by_name.setdefault(t.short_name.lower(), t.id)
    out = []
    for a in areas:
        tid = a.type_id if a.type_id in ids else by_name.get(str(a.type).lower())
        if tid is None:
            raise StockError(f"flora type {a.type!r} is not one of this map's: "
                             + ", ".join(t.short_name for t in z.types))
        if len(a.points) < 3:
            raise StockError(f"a {a.type} area has {len(a.points)} point(s); it needs 3 or more")
        pts = [(float(x) + ox, float(y) + oy) for x, y in a.points]
        if a.type_id is None:
            pts = _clockwise(pts)              # a painted rectangle: the map's own are clockwise
        flags = bytes.fromhex(a.flags) if a.flags else zones_mod.AREA_FLAGS
        out.append(zones_mod.Area(tid, pts, flags))
    z.areas = out
    log(f"flora: {len(out)} area(s) of the map's {len(z.types)} type(s)")
    return z.encode()


def build(project: MapProject, dat: str, log=None) -> tuple:
    """The patched map file. -> (plain bytes, notes). Reads the archive, writes nothing."""
    log = log or (lambda _s: None)
    if not project.stock or project.file_id is None:
        raise StockError("this is not a game map project")
    base, src = read_original(dat, project.file_id)
    original = base.encode()
    (x0, y0, _x1, _y1), _flags = map_rect(base)
    ox, oy = float(project.origin[0]), float(project.origin[1])
    if abs(ox - x0) > 0.01 or abs(oy - y0) > 0.01:
        raise StockError(f"the map file lies at ({x0}, {y0}) and the project at ({ox}, {oy}); "
                         f"it was opened from a different map")
    log(f"base: map file 0x{int(project.file_id):X} as shipped, from {src}")
    mf = mapfile.MapFile.decode(original, strict=False)
    props_payload, deps_payload = _props_part(project, base, ox, oy, log)
    pc = mf.find(sb.PROPS)
    if pc is None:
        raise StockError("the map has no props part to write")
    idx = mf.chunks.index(pc)
    mf.chunks[idx] = mapfile.Chunk(pc.chunk_id, props_payload, mapfile.FORM_OPAQUE)
    if deps_payload is not None:
        dc = mf.find(sb.PROPS_DEPS)
        if dc is not None:
            mf.chunks[mf.chunks.index(dc)] = mapfile.Chunk(dc.chunk_id, deps_payload,
                                                           mapfile.FORM_OPAQUE)
        else:
            mf.chunks.insert(idx + 1, mapfile.Chunk(sb.PROPS_DEPS, deps_payload,
                                                    mapfile.FORM_OPAQUE))
    zones_payload = _flora_part(project, base, ox, oy, log)
    if zones_payload is not None:
        zc = mf.find(zones_mod.STRIPPED_ZONES)
        mf.chunks[mf.chunks.index(zc)] = mapfile.Chunk(zc.chunk_id, zones_payload,
                                                       mapfile.FORM_OPAQUE)
    blob = mf.encode()
    notes = []
    if blob == original:
        notes.append("nothing changed: the map's own file, byte for byte")
    else:
        changed = [f"0x{a.chunk_id:08X}" for a, b in zip(mapfile.MapFile.decode(blob, strict=False).chunks,
                                                         base.chunks)
                   if a.payload() != b.payload()]
        notes.append(f"{len(blob):,} B map, {len(original):,} B as shipped; parts changed: "
                     + (", ".join(changed) or "the part list"))
    for n in notes:
        log(n)
    return blob, notes
