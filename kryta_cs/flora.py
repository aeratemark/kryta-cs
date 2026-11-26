"""The flora brush's data: which cells grow which of the donor's flora types, and the outlines a build
writes for them. No Qt here, so it is tested offline.

A project keeps flora two ways, both written into the map's Zones part (kryta_cs/zones.py):

- `flora_layer`: one int per cell, the editor's brush layer. 0 is no flora; k is the flora type
  named `flora_names[k - 1]` (`LakesideWheat`). The brush paints it like the texture layer.
- `flora`: outlines typed by hand (`FloraArea`), for shapes the cell grid cannot draw.

`all_areas()` turns the painted cells into as few rectangles as it finds (each run of cells widened
down while the rows below match), so a painted patch becomes a handful of outlines rather than one
per cell. A cell is 96 world units; cell (gx, gy) spans x [gx*96, gx*96+96] and, because the editor
keeps row 0 at the top, y [(dim_y-1-gy)*96, (dim_y-gy)*96] (MapProject.cell_center).
"""

from __future__ import annotations

from .model import CELL, FloraArea, MapProject

# Overlay colours, one per flora type in the order the project first painted them.
PALETTE = [
    (230, 200, 40), (220, 70, 140), (60, 170, 70), (60, 140, 230), (240, 130, 40),
    (150, 90, 220), (40, 190, 190), (200, 60, 50), (140, 180, 40), (120, 120, 120),
]


def color(index: int) -> tuple:
    """The overlay colour of the flora type `flora_names[index]`."""
    return PALETTE[index % len(PALETTE)]


def ensure_layer(p: MapProject) -> list:
    if len(p.flora_layer) != p.dim_x * p.dim_y:
        p.flora_layer = [0] * (p.dim_x * p.dim_y)
    return p.flora_layer


def type_number(p: MapProject, name: str) -> int:
    """The layer value for a flora type, adding the name to the project if it is new."""
    for i, n in enumerate(p.flora_names):
        if n.lower() == name.lower():
            return i + 1
    p.flora_names.append(name)
    return len(p.flora_names)


def paint(p: MapProject, points, name: str | None) -> list:
    """Set the cells `points` [(gx, gy)] to flora type `name`, or clear them when `name` is None.
    Returns the cells that changed."""
    layer = ensure_layer(p)
    value = 0 if name is None else type_number(p, name)
    changed = []
    for gx, gy in points:
        if 0 <= gx < p.dim_x and 0 <= gy < p.dim_y:
            i = gy * p.dim_x + gx
            if layer[i] != value:
                layer[i] = value
                changed.append((gx, gy))
    return changed


def counts(p: MapProject) -> dict:
    """{flora type name: painted cells}."""
    out = {}
    for v in p.flora_layer:
        if v and 0 < v <= len(p.flora_names):
            name = p.flora_names[v - 1]
            out[name] = out.get(name, 0) + 1
    return out


def rectangles(layer: list, dim_x: int, dim_y: int) -> dict:
    """{layer value: [(gx0, gy0, width, height), ...]} covering every painted cell exactly once."""
    taken = [False] * (dim_x * dim_y)
    out = {}
    for gy in range(dim_y):
        for gx in range(dim_x):
            i = gy * dim_x + gx
            v = layer[i]
            if not v or taken[i]:
                continue
            w = 1
            while gx + w < dim_x and layer[i + w] == v and not taken[i + w]:
                w += 1
            h = 1
            while gy + h < dim_y:
                row = (gy + h) * dim_x + gx
                if all(layer[row + k] == v and not taken[row + k] for k in range(w)):
                    h += 1
                else:
                    break
            for yy in range(gy, gy + h):
                for xx in range(gx, gx + w):
                    taken[yy * dim_x + xx] = True
            out.setdefault(v, []).append((gx, gy, w, h))
    return out


def rect_points(p: MapProject, gx: int, gy: int, w: int, h: int) -> list:
    """A cell rectangle as a world-unit outline (the build winds it clockwise)."""
    x0, x1 = gx * CELL, (gx + w) * CELL
    y1 = (p.dim_y - gy) * CELL
    y0 = (p.dim_y - gy - h) * CELL
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def all_areas(p: MapProject) -> list:
    """Every outline a build writes: the hand-typed ones, then the painted layer's rectangles."""
    out = list(p.flora)
    if len(p.flora_layer) == p.dim_x * p.dim_y and any(p.flora_layer):
        for v, rects in sorted(rectangles(p.flora_layer, p.dim_x, p.dim_y).items()):
            if not 0 < v <= len(p.flora_names):
                continue
            name = p.flora_names[v - 1]
            out.extend(FloraArea(name, rect_points(p, *r)) for r in rects)
    return out
