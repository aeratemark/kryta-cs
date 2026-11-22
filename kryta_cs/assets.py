"""The donor's textures and prop models, read from the owner's own Gw.dat.

The Construction Set's Object Window listed the game's own object records, and its
Landscape Edit Settings listed the terrain textures, because the editor rendered
the real thing. This module is the equivalent for this editor: it reads the donor
map Rurik's build borrows from, decodes its terrain textures to raw RGBA and
measures its prop models.

Like the game, it avoids doing the same work twice: every decoded texture and
every model measurement is kept in a disk cache under ~/.kryta_cs/cache, keyed by
the file id AND the archive entry's CRC and size, so a later start reads the
pixels back instead of decoding the archive again, and a deploy (which rewrites
Gw.dat but not these files) does not invalidate it. The archive's file-id table
is built once per archive version and shared by every model load. Nothing here writes, and nothing ships with the editor:
every byte comes from the archive the owner chose.

The binding used for textures is Rurik's measured one (`mapexport.py`,
"terrain_textures"): a tile byte indexes the Terrain Dependencies list directly.
`build_terrain_textures` refuses rather than guesses, and this module lets that
refusal reach the window."""

from __future__ import annotations

import json
import os
import struct
import threading
import zlib
from dataclasses import dataclass, field

from . import paths

paths.bootstrap_rurik()

import deploy                                                    # noqa: E402
import atex                                                      # noqa: E402
import modelfile                                                 # noqa: E402
import propscan                                                  # noqa: E402
import stripbuild as sb                                          # noqa: E402
import strippedterrain as stx                                    # noqa: E402
from archive import Archive, file_id_table                       # noqa: E402


class AssetError(Exception):
    """A refusal worded for the Object Window; nothing was written."""


@dataclass
class TextureAsset:
    tile: int
    file_id: int
    used: int = 0
    rgba: bytes | None = None          # width*height*4, top row first
    width: int = 0
    height: int = 0
    skipped: str | None = None

    @property
    def png(self):
        """Truthy when decoded (the field used to hold PNG bytes)."""
        return self.rgba


@dataclass
class ModelAsset:
    index: int
    file_id: int
    row: int | None = None
    extent: tuple | None = None
    n_verts: int = 0
    n_tris: int = 0
    n_submodels: int = 0
    placed: int = 0
    error: str | None = None

    @property
    def cells(self):
        if self.extent is None:
            return None
        return max(self.extent[0], self.extent[1]) / propscan.PITCH

    @property
    def height(self):
        return None if self.extent is None else self.extent[2]


@dataclass
class DonorAssets:
    archive: str
    donor_file_id: int
    donor_row: int
    textures: list = field(default_factory=list)
    models: list = field(default_factory=list)
    tex_word: int = 0
    table_a: bytes = b""
    table_b: bytes = b""
    donor_tiles: bytes = b""
    notes: list = field(default_factory=list)

    def texture(self, tile: int = 0):
        for t in self.textures:
            if t.tile == tile and t.png:
                return t
        return None

    @property
    def props_measured(self) -> int:
        return sum(1 for m in self.models if m.extent is not None)


@dataclass
class ModelTexture:
    file_id: int
    rgba: bytes | None = None
    width: int = 0
    height: int = 0
    skipped: str | None = None

    @property
    def png(self):
        return self.rgba


@dataclass
class ModelAssets:
    """A prop model's geometry plus the textures its material layers name.

    `textures` is aligned with the model's 0xFA5 texture-slot list;
    `sub_textures[i]` is `(texture slot or None, UV set)` for submodel i, from
    the model's layered material table (`modelfile.material_table`). That is the
    chain Rurik's M4/M5 work recovered: submodel -> material -> layer ->
    texPathIndex -> FA5."""
    file_id: int
    geometry: object
    materials: object = None
    texture_refs: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    sub_textures: list = field(default_factory=list)


# ------------------------------------------------------------------ caches

_CACHE_VERSION = 1
_TEX_MAGIC = b"KTX1"
_table_lock = threading.Lock()
_tables = {}                         # (path, size, mtime) -> file-id table


def _cache_dir(kind: str) -> str:
    path = os.path.join(paths.user_dir(), "cache", f"{kind}-v{_CACHE_VERSION}")
    os.makedirs(path, exist_ok=True)
    return path


def _entry_key(ar, row: int, file_id: int) -> str:
    """File id plus the archive entry's CRC and size: the same bytes give the
    same key whatever else in Gw.dat changed."""
    mft = ar.row(row)
    return f"{int(file_id):08x}-{int(getattr(mft, 'crc', 0)) & 0xFFFFFFFF:08x}-{int(getattr(mft, 'size', 0))}"


def shared_file_id_table(ar, dat: str):
    """The archive's file-id table, built once per archive version. It is ~170k
    entries; every model load used to rebuild it."""
    try:
        st = os.stat(dat)
        key = (os.path.abspath(dat), st.st_size, st.st_mtime_ns)
    except OSError:
        return file_id_table(ar)
    with _table_lock:
        table = _tables.get(key)
    if table is None:
        table = file_id_table(ar)
        with _table_lock:
            _tables.clear()
            _tables[key] = table
    return table


def _decode_texture_bytes(data):
    """(rgba, width, height) or a reason string."""
    if bytes(data[:4]) == atex.DDS_MAGIC:
        got = atex.dds_rgba(data)
        if got is None:
            return "a DDS shape this decoder refuses"
    else:
        got = atex.decode_rgba(data)
    rgba, width, height = got
    return bytes(rgba), int(width), int(height)


def cached_texture(ar, table, file_id: int):
    """(rgba, width, height) for a texture file id, from the disk cache when it
    is there, else decoded from the archive and cached. A reason string when it
    cannot be decoded; None when the id is not in the archive."""
    row = table.get(int(file_id))
    if row is None:
        return None
    path = os.path.join(_cache_dir("tex"), _entry_key(ar, row, file_id) + ".bin")
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
        if blob[:4] == _TEX_MAGIC:
            w, h = struct.unpack_from("<II", blob, 4)
            rgba = zlib.decompress(blob[12:])
            if len(rgba) == w * h * 4:
                return rgba, w, h
    except (OSError, zlib.error, struct.error):
        pass
    got = _decode_texture_bytes(ar.read(ar.row(row)))
    if isinstance(got, str):
        return got
    rgba, w, h = got
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(_TEX_MAGIC + struct.pack("<II", w, h) + zlib.compress(rgba, 1))
        os.replace(tmp, path)
    except OSError:
        pass
    return got


def _model_cache_path(ar, row: int, file_id: int) -> str:
    return os.path.join(_cache_dir("models"), _entry_key(ar, row, file_id) + ".json")


class _CountingArchive:
    """An Archive that reports each `read`: Rurik's prop scan reads exactly one
    file per model, so counting reads is its progress, without touching the
    vendored scanner."""

    def __init__(self, ar, on_read):
        self._ar, self._on_read = ar, on_read

    def read(self, *a, **k):
        data = self._ar.read(*a, **k)
        self._on_read()
        return data

    def __getattr__(self, name):
        return getattr(self._ar, name)


def load_donor_assets(dat: str, donor_file_id: int, log=None, textures_cb=None,
                      progress=None) -> DonorAssets:
    """Open the archive once and read the donor's texture table and prop models.

    `textures_cb` is called with the texture half as soon as the table is decoded,
    before the prop-model scan (which is the slow part). The Object Window can
    then show real textures while the models are still being measured."""
    log = log or (lambda _s: None)
    progress = progress or (lambda _done, _total, _text: None)
    if not dat:
        raise AssetError("No Gw.dat is chosen. Pick the archive your Kryta client runs from.")
    if not os.path.isfile(dat):
        raise AssetError(f"There is no file at {dat}.")
    with Archive(dat) as ar:
        row = shared_file_id_table(ar, dat).get(int(donor_file_id))
        if row is None:
            raise AssetError(f"donor file id 0x{int(donor_file_id):X} binds to no row in this archive. "
                             f"The file id is the portable key, so this is a real absence.")
        log(f"donor 0x{int(donor_file_id):X} -> row {row}")
        donor = deploy.Donor(ar, row, row)
        trn = stx.StrippedTerrain.decode(donor.stripped.find(sb.TERRAIN).payload())
        dep_ids = list(donor.terrain_dep_ids)
        if not dep_ids:
            raise AssetError(f"the donor map at row {row} carries no Terrain Dependencies chunk, "
                             f"so its texture table cannot be resolved.")
        offset = 1 if trn.tag3b is not None else 0
        n = len(donor.table_a)
        if len(dep_ids) != n + offset:
            raise AssetError(f"the donor's Terrain Dependencies list has {len(dep_ids)} entries for "
                             f"a {n}-entry tile table with{'' if offset else 'out'} a second tag-3 "
                             f"record; Rurik refuses to guess which binding this map uses.")
        used = [0] * n
        for b in trn.tiles:
            if 0 <= b < n:
                used[b] += 1
        table = shared_file_id_table(ar, dat)

        def decode_one(tile):
            fid = int(dep_ids[tile + offset])
            tex = TextureAsset(tile=tile, file_id=fid, used=used[tile])
            try:
                got = cached_texture(ar, table, fid)
            except Exception as exc:                                 # noqa: BLE001
                tex.skipped = f"{type(exc).__name__}: {exc}"
                return tex
            if got is None:
                tex.skipped = "file id not in the archive's table"
            elif isinstance(got, str):
                tex.skipped = got
            else:
                tex.rgba, tex.width, tex.height = got
            return tex

        result = DonorAssets(archive=os.path.abspath(dat), donor_file_id=int(donor_file_id),
                             donor_row=int(row), textures=[], models=[],
                             tex_word=int(donor.tex_word), table_a=bytes(donor.table_a),
                             table_b=bytes(donor.table_b), donor_tiles=bytes(trn.tiles))
        # The base texture is what the untouched build uses for every cell; decode
        # that first and let the window show it while the rest of the table is read.
        if n:
            base = decode_one(0)
            result.textures = [base]
            if textures_cb is not None:
                textures_cb(result)
            log(f"base texture: tile 0, file id 0x{base.file_id:X}")
        n_models = len(donor.prop_model_ids)
        total = n + n_models
        textures = [base] if n else []
        progress(len(textures), total, f"Reading terrain textures ({len(textures)} of {n}) ...")
        for tile in range(1, n):
            textures.append(decode_one(tile))
            progress(len(textures), total, f"Reading terrain textures ({len(textures)} of {n}) ...")
        result.textures = textures
        log(f"terrain textures: {sum(1 for t in textures if t.png)} decoded "
            f"of {len(textures)} table entries")
        if textures_cb is not None:
            textures_cb(result)
        log(f"scanning {len(donor.prop_model_ids)} prop model(s) ...")
        seen = [0]

        def one_model():
            seen[0] += 1
            progress(n + min(seen[0], n_models), total,
                     f"Measuring prop models ({min(seen[0], n_models)} of {n_models}) ...")

        # the whole scan is cached against the donor map's own entry: same
        # donor bytes, same model list, same measurements
        scan_path = os.path.join(_cache_dir("scans"),
                                 _entry_key(ar, row, donor_file_id) + ".json")
        models = None
        try:
            with open(scan_path, "r", encoding="ascii") as fh:
                models = [ModelAsset(index=m["index"], file_id=m["file_id"], row=m["row"],
                                     extent=tuple(m["extent"]) if m["extent"] else None,
                                     n_verts=m["n_verts"], n_tris=m["n_tris"],
                                     n_submodels=m["n_submodels"], placed=m["placed"],
                                     error=m["error"]) for m in json.load(fh)]
            progress(total, total, "Prop models (cached)")
        except (OSError, ValueError, KeyError, TypeError):
            models = None
        if models is None:
            try:
                scanned = propscan.scan(_CountingArchive(ar, one_model), donor.stripped, table)
            except Exception as exc:                                 # noqa: BLE001
                log(f"prop model scan failed: {type(exc).__name__}: {exc}")
                scanned = []
            models = []
            for m in scanned:
                models.append(ModelAsset(index=int(m.index), file_id=int(m.file_id), row=m.row,
                                         extent=m.extent, n_verts=m.n_verts, n_tris=m.n_tris,
                                         n_submodels=m.n_submodels, placed=m.placed, error=m.error))
            if scanned:
                try:
                    tmp = scan_path + ".tmp"
                    with open(tmp, "w", encoding="ascii") as fh:
                        json.dump([{"index": m.index, "file_id": m.file_id, "row": m.row,
                                    "extent": list(m.extent) if m.extent else None,
                                    "n_verts": m.n_verts, "n_tris": m.n_tris,
                                    "n_submodels": m.n_submodels, "placed": m.placed,
                                    "error": m.error} for m in models], fh)
                    os.replace(tmp, scan_path)
                except (OSError, TypeError, ValueError):
                    pass
        log(f"prop models: {sum(1 for m in models if m.extent is not None)} measured "
            f"of {len(models)}")
        result.models = models
        return result


def load_model_geometry(dat: str, file_id: int):
    """Decode one prop model's geometry out of the archive.

    The returned object is Rurik's `modelfile.geometry()`: submodels with
    positions, indices and normals in the model's own Y-up space. The caller
    transforms it into the map (the Blender importer's axes: model x,z are the
    ground plane and model y is height)."""
    if not dat or not os.path.isfile(dat):
        raise AssetError("No Gw.dat is chosen.")
    with Archive(dat) as ar:
        row = file_id_table(ar).get(int(file_id))
        if row is None:
            raise AssetError(f"model file id 0x{int(file_id):X} binds to no row in this archive.")
        return modelfile.ModelFile.decode(ar.read(ar.row(row))).geometry()


def _decode_model_texture(ar, table, file_id: int) -> ModelTexture:
    tex = ModelTexture(file_id=int(file_id))
    row = table.get(int(file_id))
    if row is None:
        tex.skipped = "file id not in the archive's table"
        return tex
    try:
        got = cached_texture(ar, table, file_id)
    except Exception as exc:                                         # noqa: BLE001
        tex.skipped = f"{type(exc).__name__}: {exc}"
        return tex
    if isinstance(got, str):
        tex.skipped = got
    elif got is not None:
        tex.rgba, tex.width, tex.height = got
    return tex


def load_model_assets(dat: str, file_id: int) -> ModelAssets:
    """Decode a prop model's geometry, material layers and their textures."""
    if not dat or not os.path.isfile(dat):
        raise AssetError("No Gw.dat is chosen.")
    with Archive(dat) as ar:
        table = shared_file_id_table(ar, dat)
        row = table.get(int(file_id))
        if row is None:
            raise AssetError(f"model file id 0x{int(file_id):X} binds to no row in this archive.")
        mf = modelfile.ModelFile.decode(ar.read(ar.row(row)))
        geo = mf.geometry()
        if geo is None:
            raise AssetError(f"model 0x{int(file_id):X} carries no geometry chunk")
        refs = mf.texture_refs()
        payload = mf.find(modelfile.GEOMETRY_CHUNK)
        try:
            mats = modelfile.material_table(payload) if payload is not None else None
        except Exception:                                            # noqa: BLE001
            mats = None
        textures = []
        for fid in refs:
            textures.append(None if fid is None else _decode_model_texture(ar, table, fid))
        sub_textures = []
        for sm in geo.submodels:
            chosen = (None, 0)
            if mats is not None:
                try:
                    kind, layers = mats.for_submodel(sm)
                except Exception:                                    # noqa: BLE001
                    kind, layers = "none", None
                if kind == "layered" and layers:
                    for layer in layers:
                        slot = int(layer.texpath)
                        if 0 <= slot < len(textures) and textures[slot] is not None and textures[slot].png:
                            chosen = (slot, max(0, int(layer.texarray)))
                            break
                    else:
                        layer = layers[0]
                        chosen = (int(layer.texpath), max(0, int(layer.texarray)))
            if sm.texcoord_sets == 0:
                chosen = (chosen[0], 0)
            elif chosen[1] >= sm.texcoord_sets:
                chosen = (chosen[0], 0)
            sub_textures.append(chosen)
        return ModelAssets(file_id=int(file_id), geometry=geo, materials=mats,
                           texture_refs=refs, textures=textures, sub_textures=sub_textures)
