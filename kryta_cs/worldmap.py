"""The client's world map, read-only: the area table and the per-world atlas.

Everything here READS Gw.exe and Gw.dat and writes only into the editor's own cache
(~/.kryta_cs/worldmap-cache/<stamp>/). The readers are Rurik's, vendored verbatim under
third_party/rurik/toolkit/clientscan (MIT):

  areatable.py   s_missionClientData: 124-byte records, located structurally in .rdata
                 and corroborated by the `imul reg, reg, 0x7C` that indexes it.
                 +0x04 is the WORLD index into s_worldData (areatable.py docstring).
  textrec.py     name_id -> the map's name, through Gw.dat's text records.
  worldmap.py    the three atlas tiers (chunk / parchment / satellite) per world, as
                 512x512 ATEX tiles; tile (x, y) of a grid is slot y*CX + x.
  maprows.py     (not vendored; its constants are) the two footprint rects at +0x48
                 (OUTPOST) and +0x58 (GAME), (x0, y0, x1, y1) in terrain cells, signed.

Facts this module leans on (Rurik studies/minimap/FINDINGS.md 3.1, 3.3, 6b.2):
  * chunk tier: 1 texel = 1 terrain cell, a tile covers 512 x 512 cells.
  * a few tiles are 256 x 256; they are drawn stretched over their 512-cell slot.
  * parchment and satellite tiers are assumed here to span the same world extent as
    the chunk tier (their grids are coarser), so a texel of them covers
    chunkCX / tierCX cells. That is this editor's assumption, not a measured fact.

Nothing in this file imports Qt at module level, so the offline tests can use it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time

from . import paths

# s_missionClientData field offsets (third_party/rurik/toolkit/clientscan/areatable.py,
# and maprows.py FOOTPRINT_A / FOOTPRINT_B).
RECORD_SIZE = 0x7C
FOOTPRINT_A = 0x48          # OUTPOST rect (MissionCliGetMap() == 0)
FOOTPRINT_B = 0x58          # GAME rect    (MissionCliGetMap() != 0)

TIERS = ("chunk", "parchment", "satellite")
CHUNK_SIZE = 512            # CONST_WORLD_CHUNK_SIZE, cells per chunk-tier tile side

# Display names for the world index. These are the editor's labels, read off the area
# rows each world carries (e.g. world 1 holds Ascalon Academy and Lakeside County);
# the client has no name for a world.
WORLD_LABELS = {
    0: "Tyria",
    1: "Pre-Searing Ascalon",
    2: "Cantha",
    3: "Battle Isles",
    4: "Elona",
    5: "Realm of Torment",
}

CACHE_VERSION = 1
MAX_DISPLAY_SIDE = 4096     # the stitched PNG's longest side; larger worlds are downscaled


def world_label(world: int) -> str:
    name = WORLD_LABELS.get(world)
    return f"{world} - {name}" if name else f"{world}"


# ---------------------------------------------------------------------------- rows

class AreaRow:
    """One s_missionClientData record, with the fields the window shows."""

    __slots__ = ("id", "name", "name_id", "campaign", "continent", "region", "type", "flags",
                 "file_id", "rect_a", "rect_b")

    def __init__(self, id, name, name_id, campaign, continent, region, type, flags, file_id,
                 rect_a, rect_b):
        self.id, self.name, self.name_id = id, name, name_id
        self.campaign, self.continent, self.region = campaign, continent, region
        self.type, self.flags, self.file_id = type, flags, file_id
        self.rect_a, self.rect_b = tuple(rect_a), tuple(rect_b)

    @property
    def world(self) -> int:
        return self.continent        # +0x04 is the s_worldData index (areatable.py)

    def has_footprint(self) -> bool:
        return rect_ok(self.rect_a) or rect_ok(self.rect_b)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "AreaRow":
        return cls(**{k: d[k] for k in cls.__slots__})

    def __repr__(self):
        return f"<AreaRow {self.id} {self.name!r} w{self.continent}>"


def rect_ok(r) -> bool:
    """A footprint is real when it has area; {0,0,0,0} means 'absent' (GmMapHelpers)."""
    return bool(r) and r[2] > r[0] and r[3] > r[1]


def parse_rect(blob: bytes, rec_off: int, field: int):
    return struct.unpack_from("<4i", blob, rec_off + field)


def parse_row(blob: bytes, rec_off: int, index: int, name: str | None = None) -> AreaRow:
    """One record at `rec_off` of `blob`, field offsets as areatable.py states them."""
    import areatable                                    # vendored, via bootstrap_rurik()
    r = areatable.parse_record(blob, rec_off)
    return AreaRow(index, name, r["name_id"], r["campaign"], r["continent"], r["region"],
                   r["type"], r["flags"], r["file_id"],
                   parse_rect(blob, rec_off, FOOTPRINT_A), parse_rect(blob, rec_off, FOOTPRINT_B))


def locate_area_table(pe):
    """(base_va, count, corroborated) -- areatable.main()'s steps 1 to 4, unprinted."""
    import areatable
    hits = areatable.locate_structural(pe)
    if not hits:
        raise RuntimeError("areatable: no 124-byte-stride table in .rdata on this client")
    hits.sort(key=lambda h: -h["named"])
    code = areatable.locate_from_code(pe)
    agreed = sorted({h["va"] for h in hits} & set(code))
    base_va = agreed[0] if agreed else hits[0]["va"]
    base_off = pe.rva_to_off(base_va - pe.image_base)
    return base_va, base_off, areatable.extent(pe.data, base_off), bool(agreed)


def read_area_rows(exe: str, dat: str | None, progress=None):
    """(rows, info). Names resolve through Gw.dat when `dat` is given."""
    paths.bootstrap_rurik()
    from gwpe import PE
    pe = PE(exe)
    base_va, base_off, n, agreed = locate_area_table(pe)
    names = {}
    if dat:
        import textrec
        with textrec.TextIndex(exe=exe, dat=dat) as ix:
            for i in range(n):
                nid = struct.unpack_from("<I", pe.data, base_off + i * RECORD_SIZE + 0x74)[0]
                try:
                    names[i] = ix.get(nid)
                except Exception:               # an unreadable record: leave it unnamed
                    names[i] = None
                if progress and i % 64 == 0:
                    progress(i, n)
    rows = [parse_row(pe.data, base_off + i * RECORD_SIZE, i, names.get(i)) for i in range(n)]
    info = {"base_va": base_va, "records": n, "corroborated_by_code": agreed}
    return rows, info


# ---------------------------------------------------------------------------- atlas

class AtlasIndex:
    """grids[(tier, world)] = (cx, cy); tiles[(tier, world)] = [(x, y, file_id), ...]."""

    def __init__(self, grids=None, tiles=None, build=None):
        self.grids = grids or {}
        self.tiles = tiles or {}
        self.build = build

    def worlds(self):
        return sorted({w for (_t, w) in self.grids})

    def chunk_dims(self, world):
        """(width, height) of a world in terrain cells, from its chunk grid."""
        cx, cy = self.grids.get(("chunk", world), (0, 0))
        return cx * CHUNK_SIZE, cy * CHUNK_SIZE

    def cells_per_texel(self, tier, world):
        """How many cells one tier texel covers (1 for chunk; see the module docstring)."""
        ccx, _ = self.grids.get(("chunk", world), (0, 0))
        tcx, _ = self.grids.get((tier, world), (0, 0))
        if not ccx or not tcx:
            return 1.0
        return ccx / tcx

    def as_dict(self):
        return {"build": self.build,
                "grids": [[t, w, cx, cy] for (t, w), (cx, cy) in sorted(self.grids.items())],
                "tiles": [[t, w, x, y, f] for (t, w), lst in sorted(self.tiles.items())
                          for (x, y, f) in lst]}

    @classmethod
    def from_dict(cls, d):
        ix = cls(build=d.get("build"))
        for t, w, cx, cy in d["grids"]:
            ix.grids[(t, w)] = (cx, cy)
        for t, w, x, y, f in d["tiles"]:
            ix.tiles.setdefault((t, w), []).append((x, y, f))
        return ix


def read_atlas_index(exe: str) -> AtlasIndex:
    """Every (tier, world) grid and its non-NULL tiles, through Rurik's worldmap.py."""
    paths.bootstrap_rurik()
    from gwpe import PE
    import worldmap as wm
    pe = PE(exe)
    gets = wm.getters(pe)
    wd = wm.world_data(pe)
    ix = AtlasIndex()
    for g in wm.grids(pe, gets, wd):
        ix.grids[(g.tier, g.world)] = (g.cx, g.cy)
    for t in wm.tiles(pe, gets, wd):
        ix.tiles.setdefault((t.tier, t.world), []).append((t.x, t.y, t.file_id))
    try:
        import buildid
        ix.build = buildid.of_image(exe)[0]
    except Exception:
        ix.build = None
    return ix


def tile_cell_rect(x, y, cells_per_texel, tile_side=CHUNK_SIZE):
    """(x0, y0, x1, y1) in cells covered by grid tile (x, y) of a tier."""
    side = tile_side * cells_per_texel
    return (x * side, y * side, (x + 1) * side, (y + 1) * side)


def display_scale(width_cells, height_cells, max_side=MAX_DISPLAY_SIDE) -> int:
    """Cells per stitched pixel: the smallest power of two that fits max_side."""
    s = 1
    while max(width_cells, height_cells) / s > max_side:
        s *= 2
    return s


# ---------------------------------------------------------------------------- cache

def default_exe_for(dat: str) -> str | None:
    """The client beside the archive: Gw.exe, else the first *.exe there."""
    if not dat:
        return None
    folder = os.path.dirname(os.path.abspath(dat))
    cand = os.path.join(folder, "Gw.exe")
    if os.path.isfile(cand):
        return cand
    try:
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".exe") and name.lower().startswith("gw"):
                return os.path.join(folder, name)
    except OSError:
        pass
    return None


def archive_stamp(exe: str, dat: str) -> str:
    """The cache key: both files' path, size and mtime, and the cache layout version."""
    h = hashlib.sha1()
    h.update(str(CACHE_VERSION).encode())
    for p in (exe, dat):
        st = os.stat(p)
        h.update(os.path.normcase(os.path.abspath(p)).encode("utf-8"))
        h.update(f"|{st.st_size}|{st.st_mtime_ns}|".encode())
    return h.hexdigest()[:16]


def cache_dir(exe: str, dat: str) -> str:
    path = os.path.join(paths.user_dir(), "worldmap-cache", archive_stamp(exe, dat))
    os.makedirs(path, exist_ok=True)
    return path


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii") as fh:
        json.dump(payload, fh, ensure_ascii=True)
    os.replace(tmp, path)


def load_areas_cached(exe, dat, progress=None):
    """(rows, info), from the cache when this exe/dat pair was read before."""
    path = os.path.join(cache_dir(exe, dat), "areas.json")
    got = _read_json(path)
    if got and got.get("version") == CACHE_VERSION:
        return [AreaRow.from_dict(d) for d in got["rows"]], got["info"]
    rows, info = read_area_rows(exe, dat, progress)
    _write_json(path, {"version": CACHE_VERSION, "info": info, "rows": [r.as_dict() for r in rows]})
    return rows, info


def load_atlas_cached(exe, dat):
    path = os.path.join(cache_dir(exe, dat), "atlas.json")
    got = _read_json(path)
    if got and got.get("version") == CACHE_VERSION:
        return AtlasIndex.from_dict(got["index"])
    ix = read_atlas_index(exe)
    _write_json(path, {"version": CACHE_VERSION, "index": ix.as_dict()})
    return ix


def world_image_path(exe, dat, tier, world):
    base = os.path.join(cache_dir(exe, dat), f"world{world}-{tier}")
    return base + ".png", base + ".json"


def stitch_world(exe, dat, index: AtlasIndex, tier, world, progress=None, log=None):
    """Decode every tile of (tier, world) and write one PNG plus its metadata.

    Returns the metadata dict: {"png", "scale" (cells per pixel), "width"/"height" (cells),
    "tiles", "decoded", "missing" (file ids not in this archive), "undecodable"}.
    Uses QImage for the scaling and the PNG, so it needs PySide6 (QImage is safe off the
    GUI thread).
    """
    png_path, meta_path = world_image_path(exe, dat, tier, world)
    meta = _read_json(meta_path)
    if meta and meta.get("version") == CACHE_VERSION and os.path.isfile(png_path):
        meta["png"] = png_path
        return meta

    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    paths.bootstrap_rurik()
    import archive as archive_mod
    import atex

    wcells, hcells = index.chunk_dims(world)
    scale = display_scale(wcells, hcells)
    cpt = index.cells_per_texel(tier, world)
    img = QImage(max(1, wcells // scale), max(1, hcells // scale), QImage.Format_RGB32)
    img.fill(QColor(0, 0, 0))
    tiles = index.tiles.get((tier, world), [])
    decoded, missing, bad = 0, [], []
    ar = archive_mod.Archive(dat)
    try:
        table = archive_mod.file_id_table(ar)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for i, (x, y, fid) in enumerate(tiles):
            if progress:
                progress(i, len(tiles))
            row = table.get(fid)
            if row is None:
                missing.append(fid)
                continue
            try:
                data = ar.read(ar.row(row))
                rgba, w, h = atex.decode_rgba(data)
            except Exception as exc:            # a row that is not a decodable ATEX
                bad.append(fid)
                if log:
                    log(f"tile {tier} w{world} ({x},{y}) file {fid}: {exc.__class__.__name__}: {exc}")
                continue
            tile = QImage(bytes(rgba), w, h, w * 4, QImage.Format_RGBA8888)
            x0, y0, x1, y1 = tile_cell_rect(x, y, cpt)
            painter.drawImage(QRectF(x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale), tile)
            decoded += 1
        painter.end()
    finally:
        ar.close()
    img.save(png_path, "PNG")
    meta = {"version": CACHE_VERSION, "tier": tier, "world": world, "scale": scale,
            "width": wcells, "height": hcells, "tiles": len(tiles), "decoded": decoded,
            "missing": missing, "undecodable": bad, "made": int(time.time())}
    _write_json(meta_path, meta)
    meta["png"] = png_path
    return meta


# ---------------------------------------------------------------------------- free slots

# HEURISTIC, and labelled as one in the window: a row "looks free" when nothing in
# this server uses it and its client record looks like a placeholder.
_DEVISH = re.compile(r"(?i)\b(test|dev|debug|pvp|arena|tournament|unused|placeholder|qa)\b")
_UNNAMED = {"", "...", "[null]"}


def free_reasons(row: AreaRow, catalogue_ids) -> list:
    """Why a row might be a free slot for a custom map. Empty when it does not look free."""
    if row.id in catalogue_ids:
        return []
    why = []
    name = (row.name or "").strip()
    if name in _UNNAMED:
        why.append("unnamed")
    elif _DEVISH.search(name):
        why.append("dev/test/pvp name")
    if not row.has_footprint():
        why.append("no footprint")
    if why:
        why.append("not in catalogue")
    return why


def free_looking(rows, catalogue_ids):
    """[(row, reasons)] for the rows that look free, most reasons first, then by id."""
    out = [(r, free_reasons(r, catalogue_ids)) for r in rows]
    out = [(r, w) for r, w in out if w]
    out.sort(key=lambda rw: (-len(rw[1]), rw[0].id))
    return out
