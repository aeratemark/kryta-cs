"""The client's three-layer limit on terrain textures, and how the editor keeps to it.

A terrain cell is drawn from the tiles at its FOUR CORNERS. The client groups
the corners by tile type and draws one layer per group: the base plus up to two
overlays, because its blend shader has three texture coordinate sets. A cell
whose corners need a fourth layer trips

    Assertion: varIndex < arrsize(tileVar)    (TrnTexBlendHi.cpp:369)

and Gw.exe stops. In practice that happens wherever four different textures meet
at one cell. Rurik's `trnblend.cell_layers` reproduces the client's grouping
(including its corner permutation) and caps the result at three; here it is
called uncapped so a cell that would overflow can be found.

Tiles are the editor's order: world row-major, row 0 at the top (`gy`), exactly
`MapProject.tiles`. The map file stores them TILED (32x32 blocks, like the
heights); `pipeline` converts on the way out.
"""

from __future__ import annotations

from . import paths

paths.bootstrap_rurik()

import trnblend                                                 # noqa: E402

MAX_LAYERS = trnblend.MAX_LAYERS        # 3


def _cell_corners(tiles, dim_x, dim_y, gx, gy):
    gx1 = min(gx + 1, dim_x - 1)
    gy1 = min(gy + 1, dim_y - 1)
    return (tiles[gy * dim_x + gx], tiles[gy * dim_x + gx1],
            tiles[gy1 * dim_x + gx], tiles[gy1 * dim_x + gx1])


def cell_layer_count(tiles, dim_x, dim_y, table_a, gx, gy) -> int:
    """How many layers the client draws for cell (gx, gy)."""
    corners = _cell_corners(tiles, dim_x, dim_y, gx, gy)
    if any(c >= len(table_a) for c in corners):
        return MAX_LAYERS + 1            # outside the table: the build refuses it anyway
    sel = trnblend.corner_selector(tuple(table_a[c] for c in corners))
    perm = tuple((sel >> (2 * k)) & 3 for k in range(4))
    ordered = tuple(corners[p] for p in perm)
    return len(trnblend.cell_layers(ordered, table_a, 0, max_layers=99, perm=perm))


def overflow_cells(tiles, dim_x, dim_y, table_a, rect=None) -> list:
    """[(gx, gy, layers)] for every cell the client would refuse to draw."""
    if not tiles or not table_a:
        return []
    x0, y0, x1, y1 = rect if rect is not None else (0, 0, dim_x - 1, dim_y - 1)
    out = []
    for gy in range(max(0, y0), min(dim_y - 1, y1) + 1):
        for gx in range(max(0, x0), min(dim_x - 1, x1) + 1):
            n = cell_layer_count(tiles, dim_x, dim_y, table_a, gx, gy)
            if n > MAX_LAYERS:
                out.append((gx, gy, n))
    return out


def can_paint(tiles, dim_x, dim_y, table_a, x, y, tile) -> bool:
    """Whether setting point (x, y) to `tile` keeps every cell it touches
    within the client's three layers. A point is a corner of up to four cells."""
    if not table_a:
        return True
    i = y * dim_x + x
    old = tiles[i]
    if old == tile:
        return True
    tiles[i] = tile
    try:
        for cy in (y - 1, y):
            for cx in (x - 1, x):
                if 0 <= cx < dim_x and 0 <= cy < dim_y:
                    if cell_layer_count(tiles, dim_x, dim_y, table_a, cx, cy) > MAX_LAYERS:
                        return False
        return True
    finally:
        tiles[i] = old


def fix_overflows(tiles, dim_x, dim_y, table_a, max_passes: int = 32):
    """A copy of `tiles` with every overflowing cell repaired, and the points changed.

    In each bad cell the corner tile whose type is least represented (never the
    cell's own corner 0) is replaced by the cell's own tile, until no cell needs
    more than three layers. Only corner points of offending cells change."""
    out = list(tiles)
    changed = set()
    for _ in range(max_passes):
        bad = overflow_cells(out, dim_x, dim_y, table_a)
        if not bad:
            break
        for gx, gy, _n in bad:
            if cell_layer_count(out, dim_x, dim_y, table_a, gx, gy) <= MAX_LAYERS:
                continue                 # an earlier repair in this pass fixed it
            gx1 = min(gx + 1, dim_x - 1)
            gy1 = min(gy + 1, dim_y - 1)
            points = [(gx, gy), (gx1, gy), (gx, gy1), (gx1, gy1)]
            own = out[gy * dim_x + gx]
            counts = {}
            for px, py in points:
                t = table_a[out[py * dim_x + px]]
                counts[t] = counts.get(t, 0) + 1
            for px, py in points[1:]:
                t = out[py * dim_x + px]
                if table_a[t] != table_a[own] and counts[table_a[t]] == min(
                        c for k, c in counts.items() if k != table_a[own]):
                    out[py * dim_x + px] = own
                    changed.add((px, py))
                    break
    return out, sorted(changed)


def paint(tiles, dim_x, dim_y, table_a, points, tile, max_passes: int = 64):
    """Paint `tile` at `points` IN PLACE without ever leaving a cell over the limit.

    Refusing points that would make a fourth layer (what the brush used to do)
    made some spots impossible to paint: where three textures already met,
    every dab was refused. Instead the new texture wins: in a cell that would
    overflow, the corner holding the least represented other texture is
    repainted with `tile` as well, so the stroke spills over by at most a
    point at such junctions. Every repair turns a corner into `tile`, so this
    always ends. Returns the set of (x, y) points changed."""
    changed = set()
    for x, y in points:
        i = y * dim_x + x
        if tiles[i] != tile:
            tiles[i] = tile
            changed.add((x, y))
    if not changed or not table_a:
        return changed
    want = table_a[tile] if tile < len(table_a) else None
    xs = [x for x, _y in changed]
    ys = [y for _x, y in changed]
    rect = [min(xs) - 1, min(ys) - 1, max(xs), max(ys)]
    for _ in range(max_passes):
        bad = overflow_cells(tiles, dim_x, dim_y, table_a, tuple(rect))
        if not bad:
            break
        for gx, gy, _n in bad:
            if cell_layer_count(tiles, dim_x, dim_y, table_a, gx, gy) <= MAX_LAYERS:
                continue
            gx1 = min(gx + 1, dim_x - 1)
            gy1 = min(gy + 1, dim_y - 1)
            corners = [(gx, gy), (gx1, gy), (gx, gy1), (gx1, gy1)]
            counts = {}
            for px, py in corners:
                t = table_a[tiles[py * dim_x + px]]
                counts[t] = counts.get(t, 0) + 1
            others = [(counts[table_a[tiles[py * dim_x + px]]], (px, py)) for px, py in corners
                      if table_a[tiles[py * dim_x + px]] != want]
            if not others:
                continue
            _c, (px, py) = min(others)
            tiles[py * dim_x + px] = tile
            changed.add((px, py))
            rect = [min(rect[0], px - 1), min(rect[1], py - 1), max(rect[2], px), max(rect[3], py)]
    return changed
