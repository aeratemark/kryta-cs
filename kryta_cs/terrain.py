"""Sculpting and judging a height field, offline. Nothing here touches an archive.

The walkability rules are Rurik's measurements of the retail client
(third_party/rurik/../studies/worldmaps/AUTHORING.md, quoted where used). The editor
draws them over the terrain so a map that will not mesh is visible before it is
built, instead of after a client run.
"""

from __future__ import annotations

import math
import random
from collections import deque

from .model import CELL, MapProject

# ------------------------------------------------------------------ slope rules

# AUTHORING.md section 3: the classifier reads a three-float array and emits three
# classes. Under the cut-35 set the array is {30, 35, ...}; under cut-45 it is
# {40, 45, ...}. Class 0 meshes on its own, class 1 is excluded outright, class 2
# (between) only meshes where class-0 ground climbs up beside it.
CLASS_FLAT, CLASS_STEEP, CLASS_RISKY = 0, 1, 2


def slope_set(map_flags: int) -> tuple:
    """(class-0 below, class-1 above) in degrees, from the map_flags top byte.

    AUTHORING.md section 2: "The parser normalises a top byte of 0 to 1, so 0 and 1
    select the same set". 2 selects 10/45/40. 3 and above are untested; treated as 2.
    """
    top = (map_flags >> 24) & 0xFF
    return (40.0, 45.0) if top >= 2 else (30.0, 35.0)


def _tri_slope(ax, ay, az, bx, by, bz, cx, cy, cz) -> float:
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    n = math.sqrt(nx * nx + ny * ny + nz * nz)
    if n == 0:
        return 0.0
    return math.degrees(math.acos(min(1.0, abs(nz) / n)))


def slope_map(p: MapProject) -> list:
    """Worst triangle slope, in degrees, of each quad between four samples.

    (dim_y-1) rows of (dim_x-1). The client meshes triangles; a quad is as bad as
    its worse half.
    """
    h = p.heights
    out = []
    for gy in range(p.dim_y - 1):
        row = []
        r0, r1 = h[gy], h[gy + 1]
        for gx in range(p.dim_x - 1):
            a, b, c, d = r0[gx], r0[gx + 1], r1[gx], r1[gx + 1]
            s1 = _tri_slope(0, 0, a, CELL, 0, b, 0, CELL, c)
            s2 = _tri_slope(CELL, 0, b, CELL, CELL, d, 0, CELL, c)
            row.append(max(s1, s2))
        out.append(row)
    return out


def classify(p: MapProject, slopes: list | None = None) -> list:
    lo, hi = slope_set(p.map_flags)
    slopes = slopes if slopes is not None else slope_map(p)
    return [[CLASS_FLAT if s < lo else (CLASS_STEEP if s > hi else CLASS_RISKY) for s in row]
            for row in slopes]


def quad_of_world(p: MapProject, x: float, y: float) -> tuple:
    gx, gy = p.world_to_cell(x, y)
    return int(math.floor(gx)), int(math.floor(gy))


def reachable(p: MapProject, classes: list, start: tuple, allow_risky=False) -> set:
    """Quads the client's flood fill could reach from `start` (a quad), 4-connected."""
    qh, qw = len(classes), len(classes[0]) if classes else 0
    sx, sy = start
    if not (0 <= sx < qw and 0 <= sy < qh):
        return set()
    ok = (CLASS_FLAT, CLASS_RISKY) if allow_risky else (CLASS_FLAT,)
    if classes[sy][sx] not in ok:
        return set()
    seen = {(sx, sy)}
    todo = deque([(sx, sy)])
    while todo:
        x, y = todo.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < qw and 0 <= ny < qh and (nx, ny) not in seen and classes[ny][nx] in ok:
                seen.add((nx, ny))
                todo.append((nx, ny))
    return seen


class Finding:
    ERROR, WARNING, INFO = "error", "warning", "info"

    def __init__(self, level, text, where=None):
        self.level, self.text, self.where = level, text, where

    def __repr__(self):
        return f"{self.level}: {self.text}"


def validate(p: MapProject) -> list:
    """Everything that would make the build refuse, or the client mesh nothing."""
    out = []
    if getattr(p, "stock", False):
        # A game map's ground, seed and mesh are ArenaNet's and are not rebuilt (kryta_cs/stock.py).
        return [Finding(Finding.INFO, "This is a game map: its ground is shown as it ships and is "
                                      "not rebuilt. Its props, flora and NPCs are what you edit.")]
    if not p.spawns:
        out.append(Finding(Finding.ERROR, "The map has no spawn point. Use Character > Place Spawn Point."))
    slopes = slope_map(p)
    classes = classify(p, slopes)
    lo, hi = slope_set(p.map_flags)
    wx, wy = p.world_size()

    seed = p.flood_seed()
    sq = quad_of_world(p, *seed)
    if not (0 <= seed[0] < wx and 0 <= seed[1] < wy):
        out.append(Finding(Finding.ERROR, "The flood seed is outside the map.", seed))
        flood = set()
    else:
        qx = min(max(sq[0], 0), p.dim_x - 2)
        qy = min(max(sq[1], 0), p.dim_y - 2)
        if classes[qy][qx] != CLASS_FLAT:
            out.append(Finding(
                Finding.ERROR,
                f"The flood seed stands on a {slopes[qy][qx]:.1f} degree slope. The client's mesh "
                f"compiler starts its flood there and asserts in PathFlood.cpp when that ground does "
                f"not mesh (AUTHORING.md section 5). Move the first spawn onto flat ground.", seed))
            flood = set()
        else:
            flood = reachable(p, classes, (qx, qy))

    for i, s in enumerate(p.spawns):
        if not (0 <= s.x < wx and 0 <= s.y < wy):
            out.append(Finding(Finding.ERROR, f"Spawn {i} is outside the map.", (s.x, s.y)))
            continue
        q = quad_of_world(p, s.x, s.y)
        q = (min(max(q[0], 0), p.dim_x - 2), min(max(q[1], 0), p.dim_y - 2))
        if classes[q[1]][q[0]] == CLASS_STEEP:
            out.append(Finding(Finding.ERROR, f"Spawn {i} stands on ground too steep to walk.", (s.x, s.y)))
        elif flood and q not in flood:
            out.append(Finding(Finding.WARNING,
                               f"Spawn {i} is not connected to the flood seed over flat ground, so the "
                               f"client may build no mesh under it.", (s.x, s.y)))

    risky = sum(row.count(CLASS_RISKY) for row in classes)
    if risky:
        out.append(Finding(
            Finding.WARNING,
            f"{risky} cell(s) slope between {lo:.0f} and {hi:.0f} degrees (orange). That band only "
            f"meshes where flat ground climbs up beside it (AUTHORING.md section 3, W20/W23); on its "
            f"own it compiles to nothing."))
    top = (p.map_flags >> 24) & 0xFF
    if top == 1:
        out.append(Finding(Finding.WARNING,
                           "map_flags top byte 0x01 selects the same 35 degree set as 0x00. Use 0x02 for "
                           "the 45 degree cut (AUTHORING.md section 2)."))
    if top >= 3:
        out.append(Finding(Finding.WARNING, "map_flags top byte 3 and above is untested by Rurik."))
    total = (p.dim_x - 1) * (p.dim_y - 1)
    if flood:
        out.append(Finding(Finding.INFO,
                           f"{len(flood)} of {total} cells ({100.0 * len(flood) / total:.1f}%) are reachable "
                           f"from the seed over flat ground."))
    if not (p.map_flags & 1):
        out.append(Finding(Finding.INFO,
                           "map_flags bit 0 is clear, so the client drops ground 40 or more units under "
                           "water (AUTHORING.md section 2)."))
    out += _water_findings(p)
    return out


WATER_FLICKER_BAND = 8.0       # world units: ground this close to the plane z-fights in the client


def _water_findings(p) -> list:
    """Ground lying on the water plane flickers in the client.

    The client draws the water as ONE flat plane over the whole map at the
    environment's level, and the ground is drawn where it is above it. Where
    the two are within a few units of each other the depth test cannot decide
    between them and the surface flickers as the camera moves."""
    level = getattr(p, "water_level", None)
    if level is None:
        return []
    near = []
    under = 0
    for gy, row in enumerate(p.heights):
        for gx, h in enumerate(row):
            if h < level:
                under += 1
            if abs(h - level) <= WATER_FLICKER_BAND:
                near.append((gx, gy))
    out = []
    if near:
        gx, gy = near[len(near) // 2]
        out.append(Finding(
            Finding.WARNING,
            f"{len(near)} height sample(s) lie within {WATER_FLICKER_BAND:.0f} units of the water "
            f"level ({level:,.0f}); the client's water plane flickers against the ground there. "
            f"Lower the water in Map Properties, or raise or lower that ground.",
            p.cell_centre(gx, gy)))
    if under:
        total = p.dim_x * p.dim_y
        out.append(Finding(Finding.INFO,
                           f"{100.0 * under / total:.0f}% of the ground is under the water level "
                           f"({level:,.0f})."))
    return out


# ------------------------------------------------------------------ generators

def _noise_grid(w, h, scale, seed):
    """Smooth value noise in [-1, 1], bilinear between random lattice points."""
    rnd = random.Random(seed)
    gw, gh = int(w / scale) + 2, int(h / scale) + 2
    lattice = [[rnd.uniform(-1, 1) for _ in range(gw)] for _ in range(gh)]

    def smooth(t):
        return t * t * (3 - 2 * t)

    out = []
    for y in range(h):
        fy = y / scale
        y0 = int(fy)
        ty = smooth(fy - y0)
        row = []
        for x in range(w):
            fx = x / scale
            x0 = int(fx)
            tx = smooth(fx - x0)
            a = lattice[y0][x0] + (lattice[y0][x0 + 1] - lattice[y0][x0]) * tx
            b = lattice[y0 + 1][x0] + (lattice[y0 + 1][x0 + 1] - lattice[y0 + 1][x0]) * tx
            row.append(a + (b - a) * ty)
        out.append(row)
    return out


def gen_flat(p: MapProject, base=0.0, **_):
    return [[float(base)] * p.dim_x for _ in range(p.dim_y)]


def gen_hills(p: MapProject, base=0.0, amplitude=300.0, scale=12.0, seed=1, **_):
    """Rolling hills: three octaves of value noise, kept gentle enough to walk."""
    w, h = p.dim_x, p.dim_y
    layers = [(_noise_grid(w, h, scale, seed), 1.0),
              (_noise_grid(w, h, scale / 2, seed + 1), 0.45),
              (_noise_grid(w, h, scale / 4, seed + 2), 0.2)]
    return [[base + amplitude * sum(g[y][x] * wgt for g, wgt in layers) / 1.65 for x in range(w)]
            for y in range(h)]


def gen_valley(p: MapProject, base=0.0, depth=600.0, **_):
    """A long valley floor between two gentle ridges, flat along its centre."""
    w, h = p.dim_x, p.dim_y
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            t = abs((x - (w - 1) / 2) / ((w - 1) / 2))       # 0 centre, 1 edge
            t = max(0.0, (t - 0.25) / 0.75)
            row.append(base + depth * t * t)
        out.append(row)
    return out


def gen_plateau(p: MapProject, base=0.0, height=480.0, **_):
    """A raised plateau in the middle, reached by one wide ramp from the south."""
    w, h = p.dim_x, p.dim_y
    cx0, cx1 = w // 4, w - w // 4
    cy0, cy1 = h // 4, h // 2
    ramp_len = max(4, int(height / 40))                       # <= 40 units/cell, the master rule
    out = [[float(base)] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if cx0 <= x < cx1 and cy0 <= y < cy1:
                out[y][x] = base + height
            elif w // 2 - 4 <= x < w // 2 + 4 and cy1 <= y < cy1 + ramp_len:
                out[y][x] = base + height * (1 - (y - cy1 + 1) / (ramp_len + 1))
    return out


def gen_crater(p: MapProject, base=0.0, rim=420.0, **_):
    """A bowl with a walkable floor and a rim, open on one side."""
    w, h = p.dim_x, p.dim_y
    cx, cy = (w - 1) / 2, (h - 1) / 2
    r = min(w, h) * 0.42
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            d = math.hypot(x - cx, y - cy) / r
            if d < 0.55:
                z = base
            elif d < 1.0:
                z = base + rim * ((d - 0.55) / 0.45) ** 2
            else:
                z = base + rim * max(0.0, 1 - (d - 1.0) * 2.5)
            if y > cy and abs(x - cx) < 4:
                z = min(z, base + max(0.0, (y - cy - r * 0.55)) * 30)
            row.append(z)
        out.append(row)
    return out


def gen_plaza(p: MapProject, base=0.0, **_):
    """Rurik's `gen_plaza` (mapgen.py), in elevation: the shape its first client runs walked."""
    w, h = p.dim_x, p.dim_y
    mx, my = w // 2, h // 2
    out = []
    for gy in range(h):
        row = []
        for gx in range(w):
            d = max(abs(gx - mx), abs(gy - my))
            if d <= 5:
                z = base
            elif gx > mx:
                z = base + 8 * (d - 5)
            else:
                z = base - 4 * (d - 5)
            row.append(float(z))
        out.append(row)
    return out


GENERATORS = {
    "Flat": gen_flat,
    "Plaza (Rurik's first walked map)": gen_plaza,
    "Rolling hills": gen_hills,
    "Valley": gen_valley,
    "Plateau with ramp": gen_plateau,
    "Crater": gen_crater,
}


# ------------------------------------------------------------------ brushes

class Brush:
    """A circular brush with a smooth falloff. Radius in cells, strength in units per dab."""

    TOOLS = ("raise", "lower", "smooth", "flatten", "level", "noise")

    def __init__(self, tool="raise", radius=4.0, strength=24.0, falloff=0.6, level=0.0):
        self.tool, self.radius, self.strength = tool, radius, strength
        self.falloff, self.level = falloff, level
        self.flatten_to = None

    def weight(self, d):
        if d >= self.radius:
            return 0.0
        inner = self.radius * (1 - self.falloff)
        if d <= inner:
            return 1.0
        t = (d - inner) / max(1e-6, self.radius - inner)
        return 0.5 * (1 + math.cos(math.pi * t))

    def begin(self, p: MapProject, gx: float, gy: float):
        ix = min(max(int(round(gx)), 0), p.dim_x - 1)
        iy = min(max(int(round(gy)), 0), p.dim_y - 1)
        self.flatten_to = p.heights[iy][ix]

    def apply(self, p: MapProject, gx: float, gy: float) -> tuple:
        """One dab at grid position (gx, gy). Returns the touched rectangle (x0, y0, x1, y1)."""
        r = self.radius
        x0, x1 = max(0, int(gx - r)), min(p.dim_x - 1, int(gx + r) + 1)
        y0, y1 = max(0, int(gy - r)), min(p.dim_y - 1, int(gy + r) + 1)
        h = p.heights
        src = [row[:] for row in h] if self.tool == "smooth" else None
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                w = self.weight(math.hypot(x - gx, y - gy))
                if w <= 0:
                    continue
                if self.tool == "raise":
                    h[y][x] += self.strength * w
                elif self.tool == "lower":
                    h[y][x] -= self.strength * w
                elif self.tool == "smooth":
                    acc, n = 0.0, 0
                    for yy in range(max(0, y - 1), min(p.dim_y, y + 2)):
                        for xx in range(max(0, x - 1), min(p.dim_x, x + 2)):
                            acc += src[yy][xx]
                            n += 1
                    h[y][x] += (acc / n - h[y][x]) * min(1.0, w * self.strength / 24.0)
                elif self.tool in ("flatten", "level"):
                    target = self.flatten_to if self.tool == "flatten" else self.level
                    if target is None:
                        target = h[y][x]
                    h[y][x] += (target - h[y][x]) * min(1.0, w * self.strength / 24.0)
                elif self.tool == "noise":
                    h[y][x] += random.uniform(-1, 1) * self.strength * w
        return x0, y0, x1, y1


def quantize(p: MapProject, step=48.0) -> None:
    """Rurik's Ashcoil master rule (AUTHORING.md section 3): snap every height to 48.

    Then every axial step is 0 or 48 (30.2 degrees, class 0 under the 45 degree set)
    and nothing can land in the conditionally-meshed band. Needs map_flags top byte 2.
    """
    for row in p.heights:
        for i, v in enumerate(row):
            row[i] = round(v / step) * step


def height_range(p: MapProject) -> tuple:
    lo = min(min(r) for r in p.heights)
    hi = max(max(r) for r in p.heights)
    return lo, hi
