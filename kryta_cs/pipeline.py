"""The client half: build an authored map and write it into Gw.dat.

Every step here is Rurik's (third_party/rurik/toolkit/mapdata/deploy.py, the "rung G"
pipeline), called in the same order deploy.main calls it. What this module adds is
the editor's way in: a MapProject instead of a content/areas.toml row, a log
callback instead of print(), and Kryta's own guards (a one-time backup of the
archive, and a refusal while the client holds it open).

The steps, as deploy.py documents them:
  1. geometry - the height field, snapped onto the terrain codec's lattice;
  2. borrow   - textures, sun, environment and sound from one donor map, read out of
                the owner's own archive at run time (no ArenaNet bytes are stored);
  3. assemble - stripbuild.build, a complete `ffna` map file;
  4. verify   - the map round-trips, the seed is walkable, the size is known;
  5. install  - a NEW chain under a file id nothing binds (datalloc), or a replace /
                grow / relocate of the chain this editor made before. The head is
                left empty, so the client compiles the walkable mesh on first load;
  6. readback - after a client has loaded the map once: the mesh it compiled.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field

from . import paths
from . import texlayers
from .model import FILE_ID_SEARCH_START, MapProject, _tiled_index

paths.bootstrap_rurik()

import deploy                                                  # noqa: E402
import datalloc                                                # noqa: E402
import mapchunks                                               # noqa: E402
import mapfile                                                 # noqa: E402
import envchunk                                                # noqa: E402
import props as props_mod                                      # noqa: E402
import stripbuild                                              # noqa: E402
import strippedterrain as stx                                  # noqa: E402
from archive import Archive, file_id_table                     # noqa: E402
from deployrefuse import Refused                               # noqa: E402
from rowbytes import install_bytes                             # noqa: E402

BACKUP_SUFFIX = ".kryta-original"
WORK_DIR_NAME = "kryta-maps"          # beside the archive: journals and staged map files
ENV_CHUNK = 0x10000009                # the environment the build writes, where the water record lives
ENV_CHUNK_BLOATED = 0x20000009        # the same chunk in a donor read straight from the archive


class PipelineError(Exception):
    """A refusal, worded for a person. Nothing was written when this is raised before install."""


class _LogWriter(io.TextIOBase):
    def __init__(self, log, real_stdout):
        self.log, self.buf, self.real = log, "", real_stdout

    def _emit(self, line):
        # A log callback that itself prints (log=print) would write back into this
        # writer and recurse; give it the real stdout while it runs.
        captured, sys.stdout = sys.stdout, self.real
        try:
            self.log(line)
        finally:
            sys.stdout = captured

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._emit(line)
        return len(s)

    def flush(self):
        if self.buf:
            self._emit(self.buf)
            self.buf = ""


@contextlib.contextmanager
def _captured(log):
    """Rurik's modules print their reasoning; route it into the editor's log."""
    w = _LogWriter(log or (lambda s: None), sys.stdout)
    with contextlib.redirect_stdout(w):
        try:
            yield
        finally:
            w.flush()


def tag_of(project: MapProject) -> str:
    return "kryta-" + project.slug


def work_dir(dat: str) -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(dat)), WORK_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def staged_map_path(project: MapProject, dat: str) -> str:
    return os.path.join(work_dir(dat), tag_of(project) + ".bin")


# ------------------------------------------------------------------ the archive

def check_archive(dat: str) -> None:
    if not dat:
        raise PipelineError("No Gw.dat is chosen. Pick the archive your Kryta client runs from.")
    if not os.path.isfile(dat):
        raise PipelineError(f"There is no file at {dat}.")
    with open(dat, "rb") as fh:
        if fh.read(3) != b"3AN":
            raise PipelineError(f"{dat} does not start like a Gw.dat archive.")


def check_writable(dat: str) -> None:
    """The client opens its archive exclusively; writing under it would fail half way."""
    try:
        with open(dat, "r+b"):
            pass
    except PermissionError:
        raise PipelineError(f"{dat} is locked. Close Guild Wars (and any server reading it) first.")


def archive_info(dat: str) -> dict:
    check_archive(dat)
    with Archive(dat) as ar:
        raw = file_id_table(ar, raw=True)
        plain = [f for f in raw if not f & 0x80000000]
        return {
            "path": dat,
            "bytes": os.path.getsize(dat),
            "entries": len(ar.entries),
            "file_ids": len(raw),
            "highest_plain_id": max(plain) if plain else 0,
            "next_free_id": datalloc.next_free_file_id(ar, FILE_ID_SEARCH_START),
        }


def binds(dat: str, file_id: int) -> bool:
    with Archive(dat) as ar:
        return file_id in file_id_table(ar, raw=True)


def backup_path(dat: str) -> str:
    return dat + BACKUP_SUFFIX


def ensure_backup(dat: str, progress=None) -> str | None:
    """Copy the archive aside once, before this editor ever writes to it.

    Rurik's writers journal every change and can revert them, but a whole copy is
    the one undo that does not depend on any journal. It is made once per archive
    and never overwritten. Returns the backup path, or None when one already exists.
    """
    dst = backup_path(dat)
    if os.path.exists(dst):
        return None
    size = os.path.getsize(dat)
    free = shutil.disk_usage(os.path.dirname(os.path.abspath(dat))).free
    if free < size + 256 * 1024 * 1024:
        raise PipelineError(
            f"Not enough free space for a backup of {dat} ({size / 2**30:.1f} GiB needed, "
            f"{free / 2**30:.1f} GiB free).")
    tmp = dst + ".partial"
    done = 0
    with open(dat, "rb") as src, open(tmp, "wb") as out:
        while True:
            chunk = src.read(16 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    os.replace(tmp, dst)
    return dst


def restore_backup(dat: str, progress=None) -> None:
    src_path = backup_path(dat)
    if not os.path.exists(src_path):
        raise PipelineError(f"There is no backup at {src_path}.")
    check_writable(dat)
    size = os.path.getsize(src_path)
    tmp = dat + ".restoring"
    done = 0
    with open(src_path, "rb") as src, open(tmp, "wb") as out:
        while True:
            chunk = src.read(16 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    os.replace(tmp, dat)
    wd = os.path.join(os.path.dirname(os.path.abspath(dat)), WORK_DIR_NAME)
    if os.path.isdir(wd):
        # The journals describe chains the restored archive no longer has.
        shutil.move(wd, wd + ".before-restore-" + str(int(os.path.getmtime(src_path))))


# ------------------------------------------------------------------ constants donor

# Rurik's measured criterion (content/areas.toml, the zones_largest provenance): the
# HEADER chunk has one distinct payload across all map heads, and the only thing the
# constants donor changes in a built map is its ZONES chunk. The template 0x22E2C
# ships the minimal 34-byte Zones form; a 15,870-byte Zones crashed the client. So a
# substitute donor is any map whose Zones payload is that 34-byte form.
MINIMAL_ZONES_BYTES = 34


@dataclass
class DonorCandidate:
    head_row: int
    file_id: int
    dim_x: int
    dim_y: int
    partner_bytes: int
    zones: bytes = field(repr=False, default=b"")

    def __str__(self):
        return (f"file id 0x{self.file_id:X} (head row {self.head_row}): "
                f"{self.dim_x}x{self.dim_y}, {self.partner_bytes:,} B map file")


def find_constants_donors(dat: str, log=None) -> tuple:
    """Every map in `dat` with the minimal 34-byte Zones chunk, best first.

    -> (candidates, distinct 34-byte Zones payloads, maps scanned, maps undecodable).
    Sorted 32x32 first (the template's size), then by smallest map file. Reads only.
    """
    log = log or (lambda s: None)
    check_archive(dat)
    cands, zones_forms, headers = [], set(), set()
    scanned = failed = 0
    with Archive(dat) as ar:
        by_row = {}
        for fid, row in file_id_table(ar, raw=True).items():
            if not fid & 0x80000000:
                by_row.setdefault(row, []).append(fid)
        index = mapchunks.MapIndex(ar)
        for head, partner in index.pairs:
            scanned += 1
            try:
                mf = mapfile.MapFile.decode(ar.read(partner), strict=False)
                zc, hc, tc = (mf.find(stripbuild.ZONES), mf.find(stripbuild.HEADER),
                              mf.find(stripbuild.TERRAIN))
                if zc is None or tc is None:
                    continue
                zones = zc.payload()
                if hc is not None:
                    headers.add(hc.payload())
                if len(zones) != MINIMAL_ZONES_BYTES:
                    continue
                terr = stx.StrippedTerrain.decode(tc.payload())
            except Exception:                                   # noqa: BLE001
                failed += 1
                continue
            ids = by_row.get(head.index)
            if not ids:
                continue                   # no plain id: the client cannot address it
            zones_forms.add(zones)
            cands.append(DonorCandidate(head.index, min(ids), terr.dim_x, terr.dim_y,
                                        partner.size, zones))
    cands.sort(key=lambda c: ((c.dim_x, c.dim_y) != (32, 32), c.partner_bytes, c.file_id))
    log(f"constants donor search: {scanned} maps, {len(cands)} with the {MINIMAL_ZONES_BYTES}-byte "
        f"Zones form ({len(zones_forms)} distinct payload(s)), {len(headers)} distinct HEADER "
        f"payload(s), {failed} undecodable")
    return cands, len(zones_forms), scanned, failed


def _ensure_constants_donor(project: MapProject, dat: str, log) -> None:
    """If the project's constants donor is not in this archive, pick one by Rurik's criterion."""
    fid = int(project.constants_file_id)
    with Archive(dat) as ar:
        if file_id_table(ar).get(fid) is not None:
            return
    log(f"constants donor 0x{fid:X} is not in this archive; searching for a map with the "
        f"minimal {MINIMAL_ZONES_BYTES}-byte Zones chunk (the only part the donor contributes)")
    cands, _distinct, _n, _bad = find_constants_donors(dat, log)
    if not cands:
        raise PipelineError(f"No map in {dat} has the minimal {MINIMAL_ZONES_BYTES}-byte Zones chunk, "
                            f"so there is no safe constants donor.")
    best = cands[0]
    log(f"constants donor chosen: {best} - minimal Zones form, "
        f"{'32x32 like the template' if (best.dim_x, best.dim_y) == (32, 32) else 'smallest map'}")
    project.constants_file_id = best.file_id


# ------------------------------------------------------------------ build

@dataclass
class BuildResult:
    blob: bytes
    stream: bytes
    compression: int
    snapped: list                       # Rurik-order heights the map actually carries
    worst_snap: float
    notes: list = field(default_factory=list)
    generated: int = 0
    borrowed: int = 0

    @property
    def summary(self) -> str:
        return (f"{len(self.blob):,} B map, {len(self.stream):,} B stored "
                f"({self.generated:,} B generated, {self.borrowed:,} B borrowed)")


def _apply_texture_tiles(blob: bytes, tiles: list, log) -> bytes:
    """Put the project's per-cell texture layer into the assembled terrain.

    The donor lends the texture table (tag 4/5) and the dependency list; this
    layer chooses which of those textures each cell names. Rurik's builder takes
    a uniform tile byte unless it is handed an array, and `deploy.assemble` does
    not expose that parameter, so the array is written into the already-assembled
    Stripped terrain chunk here. The terrain codec re-encodes and the map's chunk
    table derives the new size, so nothing is patched in place.
    """
    if not tiles:
        return blob
    mf = mapfile.MapFile.decode(blob)
    chunk = mf.find(stripbuild.TERRAIN)
    if chunk is None:
        raise PipelineError("the assembled map has no terrain chunk to texture")
    trn = stx.StrippedTerrain.decode(chunk.payload())
    dim_x, dim_y = trn.dim_x, trn.dim_y
    if len(tiles) != len(trn.tiles):
        raise PipelineError(f"the texture layer has {len(tiles)} cells and the terrain "
                            f"has {len(trn.tiles)}")
    if trn.table_a and max(tiles) >= len(trn.table_a):
        raise PipelineError(f"texture tile {max(tiles)} is outside the donor's "
                            f"{len(trn.table_a)}-entry table")
    # The client draws at most three texture layers per cell and asserts on a
    # fourth (TrnTexBlendHi.cpp:369, varIndex < arrsize(tileVar)): refuse here
    # instead of letting Gw.exe crash on load.
    bad = texlayers.overflow_cells(tiles, dim_x, dim_y, list(trn.table_a))
    if bad:
        where = ", ".join(f"{gx},{gy}" for gx, gy, _n in bad[:8])
        raise PipelineError(
            f"{len(bad)} cell(s) need more than three texture layers (four different "
            f"textures meet there: {where}{' ...' if len(bad) > 8 else ''}); the client "
            f"crashes on them. The editor repairs them when the map is opened with its "
            f"donor assets loaded.")
    # Tag 2 is stored in the TILED order the heights use (32x32 blocks, Rurik's
    # Terrain.index), not row-major. Writing the editor's row-major layer as-is
    # scrambled the blocks of any map wider than 32 cells - on a 64x64 map that
    # put four textures together on 114 cells and crashed the client.
    stored = [0] * len(tiles)
    for gy in range(dim_y):
        for gx in range(dim_x):
            stored[_tiled_index(gx, gy, dim_x)] = int(tiles[gy * dim_x + gx])
    new = bytes(stored)
    if new == trn.tiles:
        return blob
    trn.tiles = new
    mf.chunks[mf.chunks.index(chunk)] = mapfile.Chunk(chunk.chunk_id, trn.encode(),
                                                      mapfile.FORM_OPAQUE)
    used = len(set(new))
    log(f"texture layer: {used} distinct texture(s) over {len(new):,} cells")
    return mf.encode()


def _apply_authored_props(blob: bytes, placed, donor_model_ids, log) -> bytes:
    """Replace the assembled props chunk with the models the editor placed.

    `deploy.assemble` builds an empty props chunk (or its automatic tree
    scatter). The Object Window's drag-and-drop places model file ids; the
    client's prop record indexes the model dependency list, so the file ids are
    collected into that list and each prop's `model` is its index there. A model
    the donor no longer lists is a refusal, not a silent drop."""
    if not placed:
        return blob
    ids = [int(v) for v in donor_model_ids]
    used = []
    props = []
    for q in placed:
        fid = int(q.file_id)
        if fid not in ids:
            raise PipelineError(f"model 0x{fid:X} is not in this donor's model list; "
                                f"change the donor or remove the placed prop")
        if fid not in used:
            used.append(fid)
        # The map's z points DOWN: the file stores heights negated (Rurik's
        # Terrain heights; deploy.py places its props at the STORED height).
        # The editor keeps z up, as it keeps heights, so the prop's z is
        # negated here exactly as to_rurik_heights negates the ground. Writing
        # it unchanged sank a prop on raised ground by twice its height and
        # floated one on low ground.
        props.append(props_mod.Prop(model=used.index(fid),
                                    x=float(q.x), y=float(q.y), z=-float(q.z),
                                    rot=tuple(int(v) for v in q.rot),
                                    scale=int(q.scale), flags=int(q.flags),
                                    outline=[tuple(int(v) for v in pair) for pair in q.outline]))
    sp = props_mod.StrippedProps(props=props, refs4=[], refs6=None)
    mf = mapfile.MapFile.decode(blob)
    pc = mf.find(stripbuild.PROPS)
    if pc is None:
        raise PipelineError("the assembled map has no props chunk to replace")
    idx = mf.chunks.index(pc)
    mf.chunks[idx] = mapfile.Chunk(pc.chunk_id, sp.encode(), mapfile.FORM_OPAQUE)
    deps = mapchunks.encode_dependencies(used)
    dc = mf.find(stripbuild.PROPS_DEPS)
    if dc is not None:
        mf.chunks[mf.chunks.index(dc)] = mapfile.Chunk(dc.chunk_id, deps,
                                                       mapfile.FORM_OPAQUE)
    else:
        mf.chunks.insert(idx + 1,
                         mapfile.Chunk(stripbuild.PROPS_DEPS, deps, mapfile.FORM_OPAQUE))
    log(f"authored props: {len(props)} model(s), {len(used)} dependency file(s)")
    return mf.encode()


def _apply_water_level(blob: bytes, level, log) -> bytes:
    """Set the water plane's height in the borrowed environment chunk.

    `deploy.assemble` carries the donor's environment whole, so the water plane
    sits at the DONOR's height (0 on pre-Searing's map) whatever this map's
    ground does - which is why water appears in patches and pops as you move.
    envchunk's tag 6 is the water record and its float at +0x05 is the plane's
    base height (toolkit/mapdata/envchunk.py, "TAG6 IS THE WATER RECORD"); the
    record is 57 bytes and the patch keeps its length."""
    if level is None:
        return blob
    mf = mapfile.MapFile.decode(blob)
    c = mf.find(ENV_CHUNK)
    if c is None:
        c = mf.find(ENV_CHUNK_BLOATED)
    if c is None:
        return blob
    payload = bytearray(c.payload())
    try:
        env = envchunk.EnvChunk.decode(bytes(payload))
    except Exception:                                             # noqa: BLE001
        return blob
    water = env.section(envchunk.TAG_WATER)
    if water is None or not water.records:
        return blob
    packed = struct.pack("<f", float(level))
    hits = 0
    for rec in water.records:
        i = payload.find(rec)
        if i >= 0:
            payload[i + 5:i + 9] = packed
            hits += 1
    if not hits:
        log("water: the environment chunk carries no water record to set")
        return blob
    mf.chunks[mf.chunks.index(c)] = mapfile.Chunk(c.chunk_id, bytes(payload),
                                                  mapfile.FORM_OPAQUE)
    log(f"water: level {float(level):,.0f} written to {hits} record(s)")
    return mf.encode()


def build(project: MapProject, dat: str, log=None) -> BuildResult:
    """Steps 1-4. Reads the archive (for the donor) and writes nothing."""
    log = log or (lambda s: None)
    check_archive(dat)
    _ensure_constants_donor(project, dat, log)
    area = project.rurik_area()
    if project.props:
        area["trees"] = 0
        log("authored props replace the automatic tree scatter")
    dx, dy = project.dim_x, project.dim_y
    try:
        with _captured(log):
            heights = project.to_rurik_heights()
            snapped, worst = stx.snap_field(heights, dx, dy)
            print(f"geometry: {dx}x{dy} cells, lattice snap moved the worst sample {worst} unit(s)")
            with Archive(dat) as ar:
                biome_row = deploy._donor_row(ar, area, "biome donor", "donor_file_id", "donor_row")
                const_row = deploy._donor_row(ar, area, "constants donor", "constants_file_id",
                                              "constants_row", 46196)
                donor = deploy.Donor(ar, biome_row, const_row)
                print(f"borrowed from the donor: {len(donor.terrain_dep_ids)} terrain texture(s), "
                      f"sun angle {donor.angle_index}, environment {'yes' if donor.env else 'no'}, "
                      f"sound {'yes' if donor.sound else 'no'}")
            report = deploy.assemble(area, snapped, donor, dx, dim_y=dy)
            if project.props:
                report.blob = _apply_authored_props(report.blob, project.props,
                                                   donor.prop_model_ids, print)
            if getattr(project, "tiles", None):
                report.blob = _apply_texture_tiles(report.blob, project.tiles, print)
            report.blob = _apply_water_level(report.blob, project.water_level, print)
            stream, compression, size_note = install_bytes(report.blob)
            print(size_note)
            notes = deploy.verify(report, area, snapped, dx, install_size=len(stream),
                                  compression=compression)
            for n in notes:
                print(n)
    except Refused as exc:
        raise PipelineError(str(exc)) from exc
    except SystemExit as exc:
        raise PipelineError(str(exc)) from exc
    return BuildResult(report.blob, stream, compression, snapped, worst, notes,
                       report.generated, report.borrowed)


def apply_snap(project: MapProject, result: BuildResult) -> None:
    """Put the lattice-snapped heights back in the project, so the editor shows what ships."""
    project.from_rurik_heights(result.snapped)


# ------------------------------------------------------------------ install

@dataclass
class InstallResult:
    file_id: int
    head_row: int
    partner_row: int
    verb: str                 # "created", "replace", "grow" or "relocate"
    armed: bool
    staged: str


def install(project: MapProject, dat: str, log=None) -> InstallResult:
    """Step 5. Writes the archive. The caller makes the backup first (ensure_backup)."""
    log = log or (lambda s: None)
    check_archive(dat)
    check_writable(dat)
    here = work_dir(dat)
    tag = tag_of(project)

    if project.file_id is None:
        with Archive(dat) as ar:
            project.file_id = datalloc.next_free_file_id(ar, FILE_ID_SEARCH_START)
        log(f"file id 0x{project.file_id:X} chosen: nothing in this archive binds it")
    file_id = int(project.file_id)

    result = build(project, dat, log)
    staged = staged_map_path(project, dat)
    with open(staged, "wb") as fh:
        fh.write(result.blob)
    area = project.rurik_area()
    reserve = int(area.get("reserve_bytes", 0) or 0)

    try:
        with _captured(log):
            with Archive(dat) as ar:
                rows, create = deploy.resolve_or_create(ar, file_id, True, here=here, tag=tag)
            if create:
                print(f"creating file id {file_id:#x}: nothing binds it, so this map gets a file of its own")
                head_row, partner_row = deploy.create_chain(
                    dat, file_id, result.blob, result.stream, result.compression, here, tag,
                    reserve=reserve)
                verb = "created"
            else:
                head_row, partner_row, reservation = rows
                print(f"re-installing into the chain this editor made: head {head_row}, partner {partner_row}")
                verb = deploy.install_partner(
                    dat, staged, result.blob, result.stream, result.compression, head_row,
                    partner_row, reservation, tag, reserve=reserve, created=True)
                print(f"the partner row was written by {verb}")
            armed = deploy.head_is_armed(dat, head_row)
            if not armed:
                rebloat = os.path.join(paths.RURIK_MAPDATA, "rebloat.py")
                cmd = [_python(), rebloat, "--dat", dat, "--file-id", hex(file_id), "--arm",
                       "--confirm", "--baseline", os.path.join(here, f"{tag}_baseline.json"),
                       "--journal", os.path.join(here, f"{tag}_rebloat.json")]
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      env=dict(os.environ, PYTHONIOENCODING="utf-8"))
                for line in (proc.stdout + proc.stderr).splitlines():
                    print(line)
                if proc.returncode != 0:
                    raise PipelineError(f"arming the head was refused (rc {proc.returncode})")
                armed = True
            print("the head is empty, so the client compiles the walkable mesh when it loads this map")
    except Refused as exc:
        raise PipelineError(str(exc)) from exc
    except SystemExit as exc:
        raise PipelineError(str(exc)) from exc
    return InstallResult(file_id, head_row, partner_row, verb, armed, staged)


def _python() -> str:
    exe = sys.executable or "python"
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        alt = os.path.join(os.path.dirname(exe), base.replace("pythonw", "python"))
        if os.path.exists(alt):
            return alt
    return exe


# ------------------------------------------------------------------ status and readback

@dataclass
class MapStatus:
    installed: bool
    head_size: int = 0
    compiled: bool = False
    trapezoids: int | None = None
    text: str = ""


def status(project: MapProject, dat: str) -> MapStatus:
    if project.file_id is None or not dat or not os.path.isfile(dat):
        return MapStatus(False, text="not installed")
    try:
        with Archive(dat) as ar:
            table = file_id_table(ar)
            row = table.get(int(project.file_id))
            if row is None:
                return MapStatus(False, text=f"file id 0x{project.file_id:X} is not in this archive")
            head = next(e for e in ar.entries if e.index == row)
            size = head.size
    except Exception as exc:                                    # noqa: BLE001
        return MapStatus(False, text=f"could not read the archive: {exc}")
    if size == 0:
        return MapStatus(True, 0, False, None,
                         "installed; waiting for the client to load it once and compile its mesh")
    traps = None
    try:
        with _captured(None):
            traps = deploy.trapezoid_count(dat, int(project.file_id))
    except Exception:                                           # noqa: BLE001
        pass
    return MapStatus(True, size, True, traps,
                     f"installed and compiled by the client ({size:,} B head"
                     + (f", {traps} walkable trapezoids)" if traps is not None else ")"))


def readback(project: MapProject, dat: str, log=None) -> tuple:
    """Step 6: Rurik's readback of what the client compiled. -> (lines, failures)"""
    staged = staged_map_path(project, dat)
    if not os.path.exists(staged):
        raise PipelineError("There is no staged copy of the installed map; install it again first.")
    with open(staged, "rb") as fh:
        blob = fh.read()
    try:
        with _captured(log):
            lines, bad = deploy.readback(dat, int(project.file_id), blob, project.rurik_area())
    except Refused as exc:
        raise PipelineError(str(exc)) from exc
    return lines, bad
