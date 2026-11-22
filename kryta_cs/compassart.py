"""Compass art: paint a custom map's own terrain into the world atlas the compass crops.

The compass does not draw a map's geometry. It crops a per-world atlas of 512 x 512
ATEX tiles (the "chunk" tier) at the area row's footprint origin, one texel per terrain
cell, and clamps the crop to the LOADED map file's cell dims. A custom map that borrows
a slot therefore shows that slot's painted art. This module is Rurik's Tier 3 (A3),
done for an editor project:

  Rurik studies/minimap/FINDINGS.md
    3.3   the compass reads FOOTPRINT_A (+0x48) when MissionCliGetMap() == 0 (the
          0x0199 map-type byte, MISSION_MAP_OUTPOST) and FOOTPRINT_B (+0x58) otherwise,
          with NO fallback: a degenerate rect makes it bail (0x008C274F). The U/M map
          (GmMapHelpers) falls back to the other rect when the chosen one is all zero.
    6b.2  atlas = local + footprint origin, then shr 9 picks the tile: 1 texel = 1 cell.
    6g.1  an authored ATEX is larger than ArenaNet's stored tile, so it goes in with
          datmove (a relocation), not datwrite --replace.
    6g.4  tiles are shared: other area rows whose footprints overlap the painted cells
          show the same art. collateral() lists them.
    6g.5  A3: tilerender.py shaded the authored heightfield, pasted over ArenaNet's own
          decoded tile, and the compass drew it.

The shading is tilerender.hillshade's formula (same light, same hypsometric ramp,
same central differences), generalised to a rectangle, with the ramp blended toward
the biome donor's look: the mean colour of the donor map's own compass crop. With
`tint=None` it reproduces tilerender.hillshade byte for byte (tests/test_compassart.py).

What this writes, and how it comes back:
  * the touched tile rows in Gw.dat, through datwrite.Writer.replace (fits in place)
    or datmove.move (needs more room), each journalled under
    <archive dir>/kryta-maps/compassart/;
  * the ORIGINAL stored bytes of every tile it touches, saved there before the first
    write, so restore() can put ArenaNet's tile back exactly (compression and all);
  * compassart.json there: which projects painted which tile, and where. Several
    projects may share a tile; each restore removes only its own paint.
"""

from __future__ import annotations

import binascii
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass, field

from . import paths, worldmap
from .model import MapProject

paths.bootstrap_rurik()

import archive as archive_mod                                  # noqa: E402
import atex                                                    # noqa: E402
import datmove                                                 # noqa: E402
import datwrite                                                # noqa: E402
import png                                                     # noqa: E402
import tilerender                                              # noqa: E402

TILE = tilerender.TILE                 # 512, CONST_WORLD_CHUNK_SIZE
STATE_VERSION = 1
STATE_DIR = "compassart"
STATE_FILE = "compassart.json"
TINT_WEIGHT = 0.5                      # how far the ramp leans toward the donor's colour

RECT_A = "A"
RECT_B = "B"
RECT_LABEL = {RECT_A: "A (+0x48, OUTPOST rect, map-type byte 0)",
              RECT_B: "B (+0x58, GAME rect, map-type byte != 0)"}


class CompassArtError(Exception):
    """A refusal, worded for a person. Nothing was written when it is raised before a write."""


# ------------------------------------------------------------------ which rect, which tiles

def compass_rect(row, map_type: int):
    """(key, rect) the compass crops for this row and map-type byte. FINDINGS 3.3.

    The compass has no fallback (it bails on a degenerate rect), so a degenerate
    choice is returned as-is and plan() refuses it.
    """
    if int(map_type) == 0:
        return RECT_A, tuple(row.rect_a)
    return RECT_B, tuple(row.rect_b)


def map_side_rect(row, map_type: int):
    """(key, rect) the U/M map uses: the compass's choice, else the other one if it is zero."""
    key, rect = compass_rect(row, map_type)
    if any(rect):
        return key, rect
    other = RECT_B if key == RECT_A else RECT_A
    return other, tuple(row.rect_b if other == RECT_B else row.rect_a)


@dataclass
class Piece:
    """One tile's share of the render."""
    tx: int
    ty: int
    file_id: int | None
    tile_at: tuple            # (ox, oy) texel in the tile
    src_at: tuple             # (sx, sy) cell in the render
    w: int
    h: int

    def cells(self):
        x0, y0 = self.tx * TILE + self.tile_at[0], self.ty * TILE + self.tile_at[1]
        return (x0, y0, x0 + self.w, y0 + self.h)


@dataclass
class Plan:
    map_id: int
    map_type: int
    world: int
    rect_key: str
    rect: tuple
    other_rect: tuple
    origin: tuple
    dims: tuple                # (dim_x, dim_y) cells, the latch's clamp
    pieces: list
    notes: list = field(default_factory=list)

    @property
    def rect_label(self):
        return RECT_LABEL[self.rect_key]

    def painted_cells(self):
        x0, y0 = self.origin
        return (x0, y0, x0 + self.dims[0], y0 + self.dims[1])


def plan(project: MapProject, rows, atlas) -> Plan:
    """Where the project's render lands in its slot's atlas. Reads nothing but its arguments."""
    by_id = {r.id: r for r in rows}
    row = by_id.get(int(project.map_id))
    if row is None:
        raise CompassArtError(f"map {project.map_id} has no row in this client's area table "
                              f"({len(rows)} rows).")
    key, rect = compass_rect(row, project.map_type)
    other = tuple(row.rect_b if key == RECT_A else row.rect_a)
    if not worldmap.rect_ok(rect):
        raise CompassArtError(
            f"map {project.map_id}'s rect {RECT_LABEL[key]} is {rect}, which has no area. The compass "
            f"bails on that rect (FINDINGS 3.3, 0x008C274F) and draws no atlas art at all, so there is "
            f"nothing to paint. (The other rect is {other}; a map type of "
            f"{'1' if key == RECT_A else '0'} would select it.)")
    world = row.world
    grid = atlas.grids.get(("chunk", world))
    if not grid:
        raise CompassArtError(f"world {world} has no chunk-tier grid in this client.")
    tiles = {(x, y): f for (x, y, f) in atlas.tiles.get(("chunk", world), [])}
    x0, y0 = rect[0], rect[1]
    dx, dy = int(project.dim_x), int(project.dim_y)
    notes = []
    if x0 < 0 or y0 < 0:
        raise CompassArtError(f"the footprint origin ({x0}, {y0}) is negative; no tile holds it.")
    rw, rh = rect[2] - rect[0], rect[3] - rect[1]
    if dx > rw or dy > rh:
        notes.append(f"the map ({dx}x{dy} cells) is larger than the rect ({rw}x{rh}); the compass "
                     f"clamps to the map's own dims, so the whole map is painted (FINDINGS 6g.2)")
    pieces = []
    for ty in range(y0 // TILE, (y0 + dy - 1) // TILE + 1):
        for tx in range(x0 // TILE, (x0 + dx - 1) // TILE + 1):
            cx0, cy0 = max(x0, tx * TILE), max(y0, ty * TILE)
            cx1, cy1 = min(x0 + dx, (tx + 1) * TILE), min(y0 + dy, (ty + 1) * TILE)
            if tx >= grid[0] or ty >= grid[1]:
                notes.append(f"cells ({cx0},{cy0})..({cx1},{cy1}) fall outside world {world}'s "
                             f"{grid[0]}x{grid[1]} grid and are not painted")
                continue
            fid = tiles.get((tx, ty))
            if not fid:
                notes.append(f"tile ({tx},{ty}) is a NULL slot (no art); cells ({cx0},{cy0})..({cx1},{cy1}) "
                             f"are not painted")
                continue
            pieces.append(Piece(tx, ty, fid, (cx0 - tx * TILE, cy0 - ty * TILE),
                                (cx0 - x0, cy0 - y0), cx1 - cx0, cy1 - cy0))
    if not pieces:
        raise CompassArtError("no chunk-tier tile holds any of this map's cells.")
    return Plan(int(project.map_id), int(project.map_type), world, key, rect, other, (x0, y0),
                (dx, dy), pieces, notes)


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def collateral(p: Plan, rows):
    """(overlapping, sharing): other rows of the world that show the painted cells,
    and rows that only share a touched tile. Each item is (row, [rect keys])."""
    painted = p.painted_cells()
    tiles = [(pc.tx * TILE, pc.ty * TILE, (pc.tx + 1) * TILE, (pc.ty + 1) * TILE) for pc in p.pieces]
    over, share = [], []
    for r in rows:
        if r.world != p.world or r.id == p.map_id:
            continue
        hit = [k for k, rc in ((RECT_A, r.rect_a), (RECT_B, r.rect_b))
               if worldmap.rect_ok(rc) and _overlap(rc, painted)]
        if hit:
            over.append((r, hit))
            continue
        near = [k for k, rc in ((RECT_A, r.rect_a), (RECT_B, r.rect_b))
                if worldmap.rect_ok(rc) and any(_overlap(rc, t) for t in tiles)]
        if near:
            share.append((r, near))
    return over, share


# ------------------------------------------------------------------ the picture

def shade(heights, dim_x, dim_y, tint=None, weight=TINT_WEIGHT,
          azimuth=tilerender.AZIMUTH_DEG, altitude=tilerender.ALTITUDE_DEG, pitch=tilerender.CELL):
    """RGB bytes, dim_x by dim_y, row 0 first: tilerender.hillshade for a rectangle.

    `heights` is the project's row-major elevation grid (larger is higher, row 0 is the
    world's maximum y, as model.py states), which is tilerender's input after its own
    sign flip, in the same row order. With `tint` (an (r, g, b) mean colour) the
    hypsometric ramp is blended toward it by `weight`; without it the result is
    tilerender.hillshade's to the byte.
    """
    z = [float(v) for row in heights for v in row]
    if len(z) != dim_x * dim_y:
        raise ValueError("heights do not match dim_x x dim_y")
    az, alt = math.radians(azimuth), math.radians(altitude)
    lo, hi = min(z), max(z)
    span = (hi - lo) or 1.0
    out = bytearray(dim_x * dim_y * 3)
    for gy in range(dim_y):
        for gx in range(dim_x):
            xm, xp = max(gx - 1, 0), min(gx + 1, dim_x - 1)
            ym, yp = max(gy - 1, 0), min(gy + 1, dim_y - 1)
            dzdx = (z[gy * dim_x + xp] - z[gy * dim_x + xm]) / (((xp - xm) or 1) * pitch)
            dzdy = (z[yp * dim_x + gx] - z[ym * dim_x + gx]) / (((yp - ym) or 1) * pitch)
            slope = math.atan(math.hypot(dzdx, dzdy))
            aspect = math.atan2(dzdy, -dzdx)
            s = (math.cos(alt) * math.cos(slope)
                 + math.sin(alt) * math.sin(slope) * math.cos(az - aspect))
            s = max(0.0, min(1.0, s))
            t = (z[gy * dim_x + gx] - lo) / span
            base = (60 + 120 * t, 110 + 110 * t, 55 + 90 * t)
            if tint is not None:
                lift = 0.75 + 0.5 * t
                base = tuple(base[i] * (1 - weight) + tint[i] * lift * weight for i in range(3))
            o = (gy * dim_x + gx) * 3
            for i in range(3):
                out[o + i] = max(0, min(255, int(base[i] * (0.35 + 0.65 * s))))
    return bytes(out)


def crop(rgb, width, x, y, w, h):
    """The w x h sub-image at (x, y) of an RGB buffer `width` wide."""
    out = bytearray(w * h * 3)
    for r in range(h):
        s = ((y + r) * width + x) * 3
        out[r * w * 3:(r + 1) * w * 3] = rgb[s:s + w * 3]
    return bytes(out)


def blit(dst, dst_w, src, src_w, sx, sy, w, h, dx, dy):
    """Copy the w x h block at (sx, sy) of `src` to (dx, dy) of `dst` (a bytearray)."""
    for r in range(h):
        s = ((sy + r) * src_w + sx) * 3
        d = ((dy + r) * dst_w + dx) * 3
        dst[d:d + w * 3] = src[s:s + w * 3]


def mean_rgb(rgb):
    n = len(rgb) // 3 or 1
    return tuple(sum(rgb[i::3]) / n for i in range(3))


def decode_tile_payload(payload):
    """A decoded tile as RGB bytes. Refuses anything but 512 x 512 (FINDINGS 3.1: four
    world-3 tiles are 256 x 256 and CompassMap:472 bounds at 512)."""
    rgba, w, h = atex.decode_rgba(payload)
    if (w, h) != (TILE, TILE):
        raise CompassArtError(f"the tile decodes to {w}x{h}, not {TILE}x{TILE}; this editor paints "
                              f"only full-size chunk tiles")
    rgb = bytearray(TILE * TILE * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = rgba[0::4], rgba[1::4], rgba[2::4]
    return bytes(rgb)


# ------------------------------------------------------------------ state and originals

def state_dir(dat: str) -> str:
    from . import pipeline
    path = os.path.join(pipeline.work_dir(dat), STATE_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def load_state(dat: str) -> dict:
    path = os.path.join(state_dir(dat), STATE_FILE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        if st.get("version") == STATE_VERSION:
            return st
    except (OSError, ValueError):
        pass
    return {"version": STATE_VERSION, "tiles": {}}


def save_state(dat: str, st: dict) -> None:
    path = os.path.join(state_dir(dat), STATE_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii") as fh:
        json.dump(st, fh, indent=1, ensure_ascii=True)
    os.replace(tmp, path)


def painted_by(dat: str, slug: str) -> list:
    """File ids of the tiles a project has paint on, per the state file."""
    if not dat or not os.path.isfile(dat):
        return []
    st = load_state(dat)
    return sorted(int(f) for f, t in st["tiles"].items() if slug in t.get("paints", {}))


def _read_file(path):
    with open(path, "rb") as fh:
        return fh.read()


def _write_file(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _save_original(dat, fid, row, log):
    """Keep ArenaNet's tile exactly as stored, once, before the first write to it."""
    here = state_dir(dat)
    with archive_mod.Archive(dat) as ar:
        e = ar.row(row)
        stored = ar.raw(e)
        payload = ar.read(e)
        info = {"row": row, "size": e.size, "compression": e.compression, "crc": e.crc,
                "offset": e.offset}
    if binascii.crc32(stored) != e.crc:
        raise CompassArtError(f"tile {fid} (row {row}): its stored bytes do not match the entry crc; "
                              f"refusing to treat them as the original")
    info["stored"] = f"{fid}.orig.stored.bin"
    info["payload"] = f"{fid}.orig.payload.bin"
    _write_file(os.path.join(here, info["stored"]), stored)
    _write_file(os.path.join(here, info["payload"]), payload)
    log(f"  kept the original tile {fid}: row {row}, {e.size:,} B stored (compression {e.compression}), "
        f"{len(payload):,} B payload")
    return info


# ------------------------------------------------------------------ writing

def guard(dat: str) -> None:
    """Never a live install under Program Files; Rurik's datwrite.guard adds C:\\gw."""
    p = os.path.normcase(os.path.abspath(dat))
    if os.sep + "program files" in p:
        raise CompassArtError(f"{dat} is inside Program Files; this editor never writes there. "
                              f"Point it at the Kryta copy of Gw.dat.")


def running_clients() -> list:
    """Names of running gw*.exe processes (Windows only; [] elsewhere or when unknown)."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:                                               # noqa: BLE001
        return []
    names = {line.split(",")[0].strip('"') for line in out.splitlines() if line.strip()}
    return sorted(n for n in names if n.lower().startswith("gw") and n.lower().endswith(".exe"))


def check_client_closed(dat: str) -> None:
    from . import pipeline
    running = running_clients()
    if running:
        raise CompassArtError(f"{', '.join(running)} is running. Close Guild Wars before the compass "
                              f"art is written into {dat}.")
    pipeline.check_writable(dat)


def write_row(dat, row, data, journal, compression=0, expect=None, log=None):
    """Put `data` in MFT row `row`: in place when it fits, else relocated. -> verb.

    datwrite.Writer.replace for a payload within the row's block reservation (Rurik's
    tested in-place path, which also shrinks), datmove.move for a larger one (FINDINGS
    6g.1: the authored tile outgrows ArenaNet's compressed one). Both journal first.
    """
    from . import pipeline
    with archive_mod.Archive(dat) as ar:
        e = ar.row(row)
        res = datmove.reservation_for(e.size, ar.block_size)
    try:
        with pipeline._captured(log):
            if len(data) <= res:
                w = datwrite.Writer(dat, journal)
                try:
                    w.replace(row, data, compression=compression, expect=expect)
                finally:
                    w.close()
                verb = "replace"
            else:
                p = datmove.move(dat, row, data, journal, confirm=True, compression=compression,
                                 expect=expect)
                print(f"moved row {row}: 0x{p.old_offset:X} ({p.old_reservation} B) -> "
                      f"0x{p.new_offset:X} ({p.new_reservation} B)")
                verb = "move"
    except datmove.Refused as exc:
        raise CompassArtError(str(exc)) from exc
    except SystemExit as exc:
        raise CompassArtError(str(exc)) from exc
    with archive_mod.Archive(dat) as ar:
        e = ar.row(row)
        got = ar.raw(e)
        bad = datmove.overlaps(ar)
    if got != bytes(data) or e.compression != compression or binascii.crc32(got) != e.crc:
        raise CompassArtError(f"row {row} did not read back as written. Revert with the journal "
                              f"{journal} (python datwrite.py --revert).")
    if bad:
        raise CompassArtError(f"after writing row {row}, rows share blocks: {bad[:3]}. Revert with "
                              f"{journal}.")
    with pipeline._captured(None):
        if datwrite.verify(dat):
            raise CompassArtError(f"the archive's checksum rules fail after writing row {row}. "
                                  f"Revert with {journal}.")
    return verb


def _journal_path(dat, fid, what):
    return os.path.join(state_dir(dat), f"{fid}-{what}-{time.strftime('%Y%m%dT%H%M%S')}.journal.json")


def _compose(dat, tile_state):
    """The original tile with every recorded paint on it, in the order they were made."""
    here = state_dir(dat)
    rgb = bytearray(decode_tile_payload(_read_file(os.path.join(here, tile_state["payload"]))))
    for _slug, pt in sorted(tile_state["paints"].items(), key=lambda kv: kv[1].get("made", 0)):
        src = _read_file(os.path.join(here, pt["rgb"]))
        blit(rgb, TILE, src, pt["w"], 0, 0, pt["w"], pt["h"], pt["at"][0], pt["at"][1])
    return bytes(rgb)


# ------------------------------------------------------------------ apply and restore

@dataclass
class TileWrite:
    file_id: int
    row: int
    verb: str
    before: int
    after: int
    journal: str


@dataclass
class ApplyResult:
    plan: Plan
    exe: str
    tint_source: str
    tint: tuple | None
    overlapping: list
    sharing: list
    writes: list
    originals: dict          # fid -> original decoded RGB (for previews)
    composed: dict           # fid -> composed RGB
    render: bytes

    @property
    def bytes_written(self):
        return sum(w.after for w in self.writes)


def _find_exe(dat, exe):
    exe = exe or worldmap.default_exe_for(dat)
    if not exe or not os.path.isfile(exe):
        raise CompassArtError(f"no Gw.exe beside {dat}; the area table and the atlas tables are read "
                              f"from the client.")
    return exe


def donor_look(project, rows, atlas, ar, table, log):
    """(mean rgb, label): the donor map's own compass crop, else (None, None).

    The client's area rows carry no map file id on build 39167 (areatable's file_id reads
    0 on rows 143, 148 and 449), so the
    donor's file id is matched through the Kryta map catalogue (0011-map-catalogue.sql)
    as well as through the row's own field.
    """
    fid = int(project.donor_file_id)
    ids = set()
    if paths.REPO_ROOT:
        from . import kryta
        ids = {mid for mid, c in kryta.read_catalogue(paths.REPO_ROOT).items() if c.file_id == fid}
    for r in sorted(rows, key=lambda r: (r.id not in ids, r.id)):
        if r.file_id != fid and r.id not in ids:
            continue
        rect = r.rect_a if worldmap.rect_ok(r.rect_a) else r.rect_b
        if not worldmap.rect_ok(rect):
            continue
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        tiles = {(x, y): f for (x, y, f) in atlas.tiles.get(("chunk", r.world), [])}
        tfid = tiles.get((cx // TILE, cy // TILE))
        if not tfid or table.get(tfid) is None:
            continue
        try:
            rgb = decode_tile_payload(ar.read(ar.row(table[tfid])))
        except Exception as exc:                                    # noqa: BLE001
            log(f"  donor tile {tfid}: {exc}")
            continue
        tx0, ty0 = (cx // TILE) * TILE, (cy // TILE) * TILE
        x0, y0 = max(rect[0], tx0) - tx0, max(rect[1], ty0) - ty0
        x1, y1 = min(rect[2], tx0 + TILE) - tx0, min(rect[3], ty0 + TILE) - ty0
        m = mean_rgb(crop(rgb, TILE, x0, y0, x1 - x0, y1 - y0))
        name = f" {r.name}" if r.name else ""
        return m, f"donor map 0x{fid:X} = area {r.id}{name} (world {r.world}), its compass crop"
    return None, None


def apply(project: MapProject, dat: str, exe: str | None = None, log=None,
          rows=None, atlas=None, write=True) -> ApplyResult:
    """Render the project into its slot's atlas tile(s) and (when `write`) install them."""
    log = log or (lambda s: None)
    from . import pipeline
    pipeline.check_archive(dat)
    guard(dat)
    exe = _find_exe(dat, exe)
    if write:
        check_client_closed(dat)
    if rows is None:
        rows, _info = worldmap.load_areas_cached(exe, dat)
    if atlas is None:
        atlas = worldmap.load_atlas_cached(exe, dat)
    p = plan(project, rows, atlas)
    log(f"compass art for map {p.map_id} (map type {p.map_type}): world {p.world}, rect {p.rect_label} "
        f"= {p.rect}, origin {p.origin}, {p.dims[0]}x{p.dims[1]} cells")
    if p.other_rect != p.rect:
        log(f"  the other rect is {p.other_rect}; the compass ignores it for this map type")
    for n in p.notes:
        log("  note: " + n)
    over, share = collateral(p, rows)

    st = load_state(dat)
    slug = project.slug
    here = state_dir(dat)
    writes, originals, composed = [], {}, {}
    with archive_mod.Archive(dat) as ar:
        table = archive_mod.file_id_table(ar, raw=True)
        tint, source = donor_look(project, rows, atlas, ar, table, log)
    if tint is None:
        source = "none found; Rurik's own ramp"
    log(f"  palette: hillshade, ramp tinted toward {tuple(int(v) for v in tint) if tint else '-'} "
        f"({source})")
    render = shade(project.heights, p.dims[0], p.dims[1], tint)

    for pc in p.pieces:
        row = table.get(pc.file_id)
        if row is None:
            raise CompassArtError(f"atlas file {pc.file_id} (tile {pc.tx},{pc.ty}) does not bind RAW in "
                                  f"{dat}; the client could not address it either (FINDINGS 6d.3).")
        key = str(pc.file_id)
        ts = st["tiles"].get(key)
        if ts is None:
            if write:
                ts = _save_original(dat, pc.file_id, row, log)
            else:
                with archive_mod.Archive(dat) as ar:
                    ts = {"row": row, "payload": None, "_payload": ar.read(ar.row(row))}
            ts.update({"world": p.world, "tx": pc.tx, "ty": pc.ty, "paints": {}})
        elif ts["row"] != row:
            raise CompassArtError(f"tile {pc.file_id} was row {ts['row']} when its original was kept and "
                                  f"is row {row} now; the archive changed under the state file.")
        if ts.get("payload"):
            originals[pc.file_id] = decode_tile_payload(_read_file(os.path.join(here, ts["payload"])))
        else:
            originals[pc.file_id] = decode_tile_payload(ts["_payload"])
        piece_rgb = crop(render, p.dims[0], pc.src_at[0], pc.src_at[1], pc.w, pc.h)
        rgb_name = f"{pc.file_id}.{slug}.rgb"
        ts["paints"][slug] = {"map_id": p.map_id, "at": list(pc.tile_at), "w": pc.w, "h": pc.h,
                              "rgb": rgb_name, "made": time.time()}
        if not write:
            img = bytearray(originals[pc.file_id])
            blit(img, TILE, piece_rgb, pc.w, 0, 0, pc.w, pc.h, pc.tile_at[0], pc.tile_at[1])
            composed[pc.file_id] = bytes(img)
            log(f"  tile ({pc.tx},{pc.ty}) file {pc.file_id} row {row}: {pc.w}x{pc.h} at texel "
                f"{pc.tile_at} (dry run, nothing written)")
            continue
        _write_file(os.path.join(here, rgb_name), piece_rgb)
        img = _compose(dat, ts)
        composed[pc.file_id] = img
        log(f"  tile ({pc.tx},{pc.ty}) file {pc.file_id} row {row}: painting {pc.w}x{pc.h} at texel "
            f"{pc.tile_at} ({100 * pc.w * pc.h / (TILE * TILE):.2f}% of the tile); encoding DXT1...")
        blob = atex.build_image(img, TILE, TILE)
        with archive_mod.Archive(dat) as ar:
            before = ar.row(row).size
        journal = _journal_path(dat, pc.file_id, "paint")
        verb = write_row(dat, row, blob, journal, log=log)
        ts.setdefault("journals", []).append(os.path.basename(journal))
        st["tiles"][key] = ts
        save_state(dat, st)
        writes.append(TileWrite(pc.file_id, row, verb, before, len(blob), journal))
        log(f"  wrote {len(blob):,} B ({verb}; was {before:,} B); journal {journal}")
    return ApplyResult(p, exe, source, tint, over, share, writes, originals, composed, render)


def restore(dat: str, slug: str | None = None, log=None) -> list:
    """Take a project's paint off every tile it touched (all paint when slug is None).

    A tile with no paint left gets ArenaNet's original stored bytes back, compression
    and crc included; a tile still painted by another project is re-composed without
    this one. -> [TileWrite]
    """
    log = log or (lambda s: None)
    guard(dat)
    st = load_state(dat)
    todo = [(k, t) for k, t in st["tiles"].items() if slug is None or slug in t.get("paints", {})]
    if not todo:
        log("no compass art from this editor" + (f" for {slug}" if slug else "") + " in this archive")
        return []
    check_client_closed(dat)
    here = state_dir(dat)
    out = []
    for key, ts in todo:
        fid, row = int(key), ts["row"]
        with archive_mod.Archive(dat) as ar:
            before = ar.row(row).size
        if slug is not None:
            ts["paints"].pop(slug, None)
        else:
            ts["paints"] = {}
        journal = _journal_path(dat, fid, "restore")
        if ts["paints"]:
            blob = atex.build_image(_compose(dat, ts), TILE, TILE)
            verb = write_row(dat, row, blob, journal, log=log)
            log(f"  tile {fid}: re-composed without {slug} ({len(ts['paints'])} paint(s) left)")
            out.append(TileWrite(fid, row, verb, before, len(blob), journal))
            st["tiles"][key] = ts
        else:
            stored = _read_file(os.path.join(here, ts["stored"]))
            payload = _read_file(os.path.join(here, ts["payload"]))
            comp = int(ts["compression"])
            verb = write_row(dat, row, stored, journal, compression=comp,
                             expect=payload if comp else None, log=log)
            with archive_mod.Archive(dat) as ar:
                e = ar.row(row)
                ok = ar.raw(e) == stored and e.crc == ts["crc"]
            if not ok:
                raise CompassArtError(f"tile {fid} did not read back as the original. Journal {journal}.")
            log(f"  tile {fid}: ArenaNet's original is back (row {row}, {len(stored):,} B, "
                f"compression {comp}, crc 0x{ts['crc']:08X})")
            out.append(TileWrite(fid, row, verb, before, len(stored), journal))
            del st["tiles"][key]
        save_state(dat, st)
    return out


# ------------------------------------------------------------------ preview

def preview_png(result: ApplyResult, path: str, margin=32, scale=4) -> str:
    """Before and after, side by side: the painted cells plus `margin` cells of the tile
    around them, scaled up `scale` times, with the painted rect outlined. Only the first
    tile's piece is shown when the render spans several."""
    pc = result.plan.pieces[0]
    x0 = max(0, pc.tile_at[0] - margin)
    y0 = max(0, pc.tile_at[1] - margin)
    x1 = min(TILE, pc.tile_at[0] + pc.w + margin)
    y1 = min(TILE, pc.tile_at[1] + pc.h + margin)
    w, h = x1 - x0, y1 - y0
    gap = 8
    W, H = (w * scale) * 2 + gap, h * scale
    img = bytearray(b"\x20\x20\x20" * (W * H))
    for panel, src in enumerate((result.originals[pc.file_id], result.composed[pc.file_id])):
        part = crop(src, TILE, x0, y0, w, h)
        ox = panel * (w * scale + gap)
        for yy in range(H):
            sr = (yy // scale) * w * 3
            d = (yy * W + ox) * 3
            row = bytearray(w * scale * 3)
            for xx in range(w):
                px = part[sr + xx * 3:sr + xx * 3 + 3]
                row[xx * scale * 3:(xx + 1) * scale * 3] = px * scale
            img[d:d + w * scale * 3] = row
        # outline the painted rect in yellow, 1 px, dashed every 4 px
        rx0, ry0 = (pc.tile_at[0] - x0) * scale, (pc.tile_at[1] - y0) * scale
        rx1, ry1 = rx0 + pc.w * scale - 1, ry0 + pc.h * scale - 1
        for xx in range(rx0, rx1 + 1):
            if (xx // 4) % 2 == 0:
                for yy in (ry0, ry1):
                    d = (yy * W + ox + xx) * 3
                    img[d:d + 3] = b"\xff\xe0\x30"
        for yy in range(ry0, ry1 + 1):
            if (yy // 4) % 2 == 0:
                for xx in (rx0, rx1):
                    d = (yy * W + ox + xx) * 3
                    img[d:d + 3] = b"\xff\xe0\x30"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    _write_file(path, png.encode(bytes(img), W, H, colour=png.COLOUR_RGB))
    return path


def describe(result: ApplyResult) -> list:
    """The report, as lines."""
    p = result.plan
    out = [f"map {p.map_id}, map type {p.map_type}: rect {p.rect_label} = {p.rect}, world {p.world}",
           f"origin {p.origin}, painted {p.dims[0]}x{p.dims[1]} cells = atlas cells {p.painted_cells()}",
           f"client tables read from {result.exe}",
           f"palette: {result.tint_source}"]
    for pc in p.pieces:
        out.append(f"tile ({pc.tx},{pc.ty}) file {pc.file_id}: {pc.w}x{pc.h} at texel {pc.tile_at}")
    for w in result.writes:
        out.append(f"row {w.row}: {w.before:,} -> {w.after:,} B ({w.verb}), journal {w.journal}")
    out.append(f"rows overlapping the painted cells (they show this art too): "
               + (", ".join(f"{r.id} {r.name or ''} [{'/'.join(k)}]".strip() for r, k in result.overlapping)
                  or "none"))
    out.append(f"rows sharing a touched tile but not the painted cells: "
               + (", ".join(f"{r.id}" for r, _k in result.sharing) or "none"))
    return out
