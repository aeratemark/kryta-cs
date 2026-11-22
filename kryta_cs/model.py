"""A map project: everything one authored map is made of, saved as one JSON file.

The editor's own data. It is not a server file and not a client file. Building it
produces the client's half (a map file written into Gw.dat) and the server's half
(an SQL file under data/sql/base/db_world), and both are derived from this.

Coordinates, stated once because everything else leans on them:

  * The height field is `dim_x` by `dim_y` samples, one per 96-unit cell, kept here
    row-major with row 0 at the TOP of the editor view. Heights are elevations in
    world units, larger is higher.
  * `tiles` is one index into the donor's terrain-texture table per cell, in the
    same row-major order. It is the layer the Textures tab paints and the build
    writes into the terrain chunk.
  * `props` are placed donor prop models. The file id is the portable key; the
    build resolves it against the donor's own model list and writes the props
    chunk and its dependency list.
  * Grid row 0 is the world's maximum y. Rurik measured this against the client
    (studies/worldmaps/AUTHORING.md, "Terrain grid row 0 renders at world maxY"),
    so a cell's world centre is x = gx*96 + 48, y = (dim_y - 1 - gy)*96 + 48.
  * Rurik's codec stores heights NEGATED and in a tiled order (32x32 tiles,
    `Terrain.index`). `to_rurik_heights` and `from_rurik_heights` are the only
    two places that know.
"""

from __future__ import annotations

import json
import math
import math
import os
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

CELL = 96.0                     # world units per height sample (Rurik: "cell pitch is 96")
MAX_DIM = 256                   # AUTHORING.md section 1: "any size to 256x256"
TILE = 32                       # the codec stores 32x32 tiles, so a side is a multiple of 32

# Rurik's donors, by file id (content/areas.toml): the biome every authored area
# there borrows (pre-Searing Ascalon's map file, 0x1B97D = 113021, Kryta map 148's
# own file id) and the 32x32 reference map whose structural constants fit a small
# authored map (row 46196 on build 38797).
DEFAULT_DONOR_FILE_ID = 0x1B97D
DEFAULT_CONSTANTS_FILE_ID = 0x22E2C

# AUTHORING.md section 2: bit 0 keeps ground under the water line, the top byte
# 0x02 moves the walk cut from 35 to 45 degrees. W24 shipped exactly 0x02000001.
DEFAULT_MAP_FLAGS = 0x02000001

# Where this editor starts looking for a free file id. Rurik's own created chains
# sit at 0x5F0AD..0x5F0B3; starting above them keeps a Kryta map from ever sharing
# a spelling with one of Rurik's in an archive that holds both.
FILE_ID_SEARCH_START = 0x5F100

MAP_TYPES = {0: "Outpost", 1: "Explorable"}

TAU = 2.0 * math.pi

# Rurik's measured prop convention (toolkit/mapdata/mapexport.py, "rotation"):
# rot_bytes[i] is b*2*pi/256 about (x, y, z)[i] with signs (-1, +1, -1),
# applied z first, then x, then y (Blender "ZXY"); 3545/3545 multi-axis
# records agree with the client's own compiled basis.
ROT_SIGNS = (-1.0, 1.0, -1.0)


def _mat_mul(a, b):
    return tuple(sum(a[3 * r + k] * b[3 * k + c] for k in range(3))
                 for r in range(3) for c in range(3))


@lru_cache(maxsize=512)
def prop_matrix(rot) -> tuple:
    """The 3x3 matrix the client applies to a prop model's vertices.

    A model's own axes are the map's: x and y are the ground plane and z is
    height. The client converts model vertices and world positions with the
    same (x, -z, y) rule (FFNA_ModelFile.h:484-491 against FFNA_MapFile.h:93-96),
    so no axis swap belongs here - only the three rotation bytes."""
    ax = ROT_SIGNS[0] * int(rot[0]) * TAU / 256.0
    ay = ROT_SIGNS[1] * int(rot[1]) * TAU / 256.0
    az = ROT_SIGNS[2] * int(rot[2]) * TAU / 256.0
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    rx = (1.0, 0.0, 0.0, 0.0, cx, -sx, 0.0, sx, cx)
    ry = (cy, 0.0, sy, 0.0, 1.0, 0.0, -sy, 0.0, cy)
    rz = (cz, -sz, 0.0, sz, cz, 0.0, 0.0, 0.0, 1.0)
    return _mat_mul(_mat_mul(ry, rx), rz)          # z first, then x, then y


def prop_apply(m, x: float, y: float, z: float) -> tuple:
    return (m[0] * x + m[1] * y + m[2] * z,
            m[3] * x + m[4] * y + m[5] * z,
            m[6] * x + m[7] * y + m[8] * z)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "map"


@dataclass
class Spawn:
    """Where a character may stand on entry (Kryta's map_spawn row)."""
    x: float
    y: float
    angle: float | None = None      # radians; None leaves the server's neutral +y

    def to_json(self):
        return {"x": round(self.x, 2), "y": round(self.y, 2), "angle": self.angle}


@dataclass
class PlacedProp:
    """One donor prop model placed on the map, as the CS's object window placed them.

    `file_id` is the model's portable key; the build resolves it against the
    donor's own model list. `rot` is the three byte rotation the prop chunk
    stores (byte angle * 2*pi/256) and `scale` is its byte scale. `model` is the
    donor index, kept for display and for the Models tab.

    `grid_locked` is the Placement mode the CS called "snap to grid": the prop
    stands on a terrain cell, at that cell's centre and height (which is how
    Guild Wars places props - Rurik's deploy does the same). Unlocked, it may
    stand anywhere and follows the ground under the cursor."""
    file_id: int
    x: float
    y: float
    z: float
    rot: tuple = (0, 0, 0)
    scale: int = 0x7F
    flags: int = 0
    outline: list = field(default_factory=list)
    model: int = 0
    grid_locked: bool = True

    def to_json(self):
        return {"file_id": "0x%X" % int(self.file_id), "model": int(self.model),
                "x": round(self.x, 2), "y": round(self.y, 2), "z": round(self.z, 2),
                "rot": [int(v) for v in self.rot], "scale": int(self.scale),
                "flags": int(self.flags), "grid_locked": bool(self.grid_locked),
                "outline": [[int(a), int(b)] for a, b in self.outline]}


@dataclass
class MapProject:
    name: str = "New Map"
    slug: str = "new-map"
    map_id: int = 167
    map_type: int = 0
    dim_x: int = 64
    dim_y: int = 64
    heights: list = field(default_factory=list)     # row-major, dim_y rows of dim_x floats
    spawns: list = field(default_factory=list)      # [Spawn]
    props: list = field(default_factory=list)       # [PlacedProp] placed donor models
    tiles: list = field(default_factory=list)       # per cell: index into the donor's texture table
    water_level: float | None = 0.0                 # world height of the water plane; None = none
    seed: tuple | None = None                       # the client's flood seed; None = first spawn
    file_id: int | None = None                      # chosen when first installed
    donor_file_id: int = DEFAULT_DONOR_FILE_ID
    constants_file_id: int = DEFAULT_CONSTANTS_FILE_ID
    map_flags: int = DEFAULT_MAP_FLAGS
    textures: bool = True
    sun: bool = True
    environment: bool = True
    sound: bool = True
    trees: int = 0
    prop_models: list = field(default_factory=lambda: [0])
    prop_outline: int = 0
    reserve_bytes: int = 16384
    notes: str = ""
    path: str | None = field(default=None, repr=False, compare=False)

    # --------------------------------------------------------------- creation

    @classmethod
    def new(cls, name: str, dim_x: int = 64, dim_y: int = 64, base: float = 0.0,
            map_id: int = 167, map_type: int = 0) -> "MapProject":
        check_dims(dim_x, dim_y)
        p = cls(name=name, slug=slugify(name), map_id=map_id, map_type=map_type,
                dim_x=dim_x, dim_y=dim_y)
        p.heights = [[float(base)] * dim_x for _ in range(dim_y)]
        p.tiles = [0] * (dim_x * dim_y)
        cx, cy = p.cell_center(dim_x // 2, dim_y // 2)
        p.spawns = [Spawn(cx, cy, None)]
        return p

    # --------------------------------------------------------------- geometry

    def cell_center(self, gx: int, gy: int) -> tuple:
        return gx * CELL + CELL / 2, (self.dim_y - 1 - gy) * CELL + CELL / 2

    def world_to_cell(self, x: float, y: float) -> tuple:
        """(gx, gy) as floats, in the editor's row-0-at-top grid."""
        return x / CELL - 0.5, (self.dim_y - 1) - (y / CELL - 0.5)

    def world_size(self) -> tuple:
        return self.dim_x * CELL, self.dim_y * CELL

    def cell_centre(self, gx: int, gy: int) -> tuple:
        """The world (x, y) at a cell's centre - the point a grid-locked prop
        stands on, and the point Rurik's deploy places props at."""
        gx = min(max(int(gx), 0), self.dim_x - 1)
        gy = min(max(int(gy), 0), self.dim_y - 1)
        return (gx * CELL + CELL / 2.0, (self.dim_y - 1 - gy) * CELL + CELL / 2.0)

    def snap_world(self, x: float, y: float) -> tuple:
        """(x, y, z) of the cell nearest a world point, on its own height."""
        gx, gy = self.world_to_cell(x, y)
        cx = min(max(int(round(gx)), 0), self.dim_x - 1)
        cy = min(max(int(round(gy)), 0), self.dim_y - 1)
        wx, wy = self.cell_centre(cx, cy)
        return wx, wy, self.heights[cy][cx]

    def height_at_world(self, x: float, y: float) -> float:
        gx, gy = self.world_to_cell(x, y)
        gx = min(max(int(round(gx)), 0), self.dim_x - 1)
        gy = min(max(int(round(gy)), 0), self.dim_y - 1)
        return self.heights[gy][gx]

    def flood_seed(self) -> tuple:
        if self.seed is not None:
            return tuple(self.seed)
        if self.spawns:
            return (self.spawns[0].x, self.spawns[0].y)
        return self.cell_center(self.dim_x // 2, self.dim_y // 2)

    def tile_at(self, gx: int, gy: int) -> int:
        if not self.tiles:
            return 0
        gx = min(max(int(gx), 0), self.dim_x - 1)
        gy = min(max(int(gy), 0), self.dim_y - 1)
        return self.tiles[gy * self.dim_x + gx]

    # --------------------------------------------------------- Rurik's codec

    def to_rurik_heights(self) -> list:
        """Stored order and sign: tiled, negated, integer."""
        from_index = _tiled_index
        out = [0] * (self.dim_x * self.dim_y)
        for gy in range(self.dim_y):
            row = self.heights[gy]
            for gx in range(self.dim_x):
                out[from_index(gx, gy, self.dim_x)] = -int(round(row[gx]))
        return out

    def from_rurik_heights(self, stored: list) -> None:
        for gy in range(self.dim_y):
            row = self.heights[gy]
            for gx in range(self.dim_x):
                row[gx] = float(-stored[_tiled_index(gx, gy, self.dim_x)])

    def rurik_area(self) -> dict:
        """The `content/areas.toml` row Rurik's assemble() reads, built from this project."""
        sx, sy = self.flood_seed()
        return {
            "name": self.name,
            "map_id": self.map_id,
            "dims": [self.dim_x, self.dim_y] if self.dim_x != self.dim_y else self.dim_x,
            "seed_x": float(sx),
            "seed_y": float(sy),
            "donor_file_id": int(self.donor_file_id),
            "constants_file_id": int(self.constants_file_id),
            "textures": self.textures,
            "sun": self.sun,
            "environment": self.environment,
            "sound": self.sound,
            "trees": int(self.trees),
            "prop_models": list(self.prop_models or [0]),
            "prop_outline": int(self.prop_outline),
            "prop_outline_shape": "square",
            "map_flags": int(self.map_flags),
            "reserve_bytes": int(self.reserve_bytes),
        }

    # -------------------------------------------------------------- file I/O

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        d["spawns"] = [s.to_json() for s in self.spawns]
        d["props"] = [p.to_json() for p in self.props]
        d["heights"] = [[_compact(v) for v in row] for row in self.heights]
        d["format"] = "kryta-cs-map/1"
        for key in ("file_id", "donor_file_id", "constants_file_id", "map_flags"):
            if d.get(key) is not None:
                d[key] = "0x%X" % d[key]
        return d

    @classmethod
    def from_json(cls, d: dict, path: str | None = None) -> "MapProject":
        d = dict(d)
        d.pop("format", None)
        for key in ("file_id", "donor_file_id", "constants_file_id", "map_flags"):
            v = d.get(key)
            if isinstance(v, str):
                d[key] = int(v, 0)
        spawns = [Spawn(float(s["x"]), float(s["y"]), s.get("angle")) for s in d.pop("spawns", [])]
        props_raw = d.pop("props", [])
        seed = d.pop("seed", None)
        tiles = d.pop("tiles", None)
        known = {f for f in cls.__dataclass_fields__}
        p = cls(**{k: v for k, v in d.items() if k in known and k != "path"})
        p.spawns = spawns
        props = []
        for q in props_raw:
            fid = q.get("file_id")
            if isinstance(fid, str):
                fid = int(fid, 0)
            rot = tuple(int(v) for v in q.get("rot", (0, 0, 0)))
            if len(rot) != 3:
                raise ValueError("a placed prop's rotation is three bytes")
            outline = [tuple(int(v) for v in pair) for pair in q.get("outline", [])]
            props.append(PlacedProp(file_id=int(fid), model=int(q.get("model", 0)),
                                    x=float(q["x"]), y=float(q["y"]), z=float(q["z"]),
                                    rot=rot, scale=int(q.get("scale", 0x7F)),
                                    flags=int(q.get("flags", 0)), outline=outline,
                                    grid_locked=bool(q.get("grid_locked", True))))
        p.props = props
        p.seed = tuple(seed) if seed else None
        p.heights = [[float(v) for v in row] for row in p.heights]
        check_dims(p.dim_x, p.dim_y)
        if len(p.heights) != p.dim_y or any(len(r) != p.dim_x for r in p.heights):
            raise ValueError("the height field does not match dim_x x dim_y")
        cells = p.dim_x * p.dim_y
        if tiles is None:
            p.tiles = [0] * cells
        else:
            p.tiles = [int(v) for v in tiles]
            if len(p.tiles) != cells:
                raise ValueError(f"the texture layer has {len(p.tiles)} cells, expected {cells}")
            if any(v < 0 or v > 255 for v in p.tiles):
                raise ValueError("a texture tile index is outside 0..255")
        p.path = path
        return p

    def save(self, path: str | None = None) -> str:
        path = path or self.path
        if not path:
            raise ValueError("no path to save to")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="ascii") as fh:
            json.dump(self.to_json(), fh, indent=1, separators=(",", ":"))
        os.replace(tmp, path)
        self.path = path
        return path

    @classmethod
    def load(cls, path: str) -> "MapProject":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_json(json.load(fh), path)

    def copy(self) -> "MapProject":
        return MapProject.from_json(self.to_json(), self.path)


def _compact(v: float):
    r = round(v, 2)
    return int(r) if r == int(r) else r


def _tiled_index(gx: int, gy: int, dim_x: int) -> int:
    """Rurik's `Terrain.index`: 32x32 tiles, tile-row-major, cell-row-major."""
    return (((gy >> 5) * (dim_x >> 5) + (gx >> 5)) * 1024 + (gy & 31) * 32 + (gx & 31))


def check_dims(dim_x: int, dim_y: int) -> None:
    for d in (dim_x, dim_y):
        if d < TILE or d > MAX_DIM or d % TILE:
            raise ValueError(f"a side must be a multiple of {TILE} from {TILE} to {MAX_DIM}, got {d}")


def angle_to_degrees(angle):
    return None if angle is None else math.degrees(angle)
