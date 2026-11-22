"""The client's terrain texture blend, bound to Rurik's trnblend and trnvariation.

GuildWarsMapBrowser's `Terrain.cpp` and `TerrainRevPixelShader.hlsl` give the
rendering shape, and Rurik's `trnblend.py` gives the exact selection rule:

  * a cell samples the tile bytes of its four corners;
  * the corners are grouped by TILE TYPE (terrain tag 4), after the client's
    selection-sort permutation;
  * the first group is the opaque base and samples the cell's variation
    quadrant; later groups are overlays whose own authored alpha masks them;
  * the shader blends `t0`, `lerp(t1, t1.a)`, `lerp(t2, t2.a)`, then
    multiplies by 1.4 and the lighting colour.

The base's alpha is NOT a mask; its RGB is the ground. The overlays are drawn
with their alpha. This module builds one top-down albedo image (for the 3D
Render Window) and one average colour per cell (for the Top View)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF, QTransform

from .. import paths

paths.bootstrap_rurik()

import trnblend                                                 # noqa: E402
import trnvariation                                             # noqa: E402

BORDER = 8.5
QUADRANT = 128.0
OFFSETS = ((0.0, 0.0), (128.0, 0.0), (0.0, 128.0), (128.0, 128.0))
BRIGHTNESS = 1.4                    # TerrainRevPixelShader.hlsl: `result * 1.4`
FALLBACK = (92, 82, 58)


@dataclass
class GroundSurface:
    image: QImage | None       # None when the Render Window blends on the GPU
    cell_colors: list
    dim_x: int
    dim_y: int
    scale: int
    layers: list
    variation: list | None = None


MAX_SIDE_PX = 4096      # every GPU since ~2008 takes a 4096 texture
MAX_CELL_PX = 64        # a cell shows ~111 px of its source quadrant; 64 keeps most of it


def _scale_for(dim_x: int, dim_y: int) -> int:
    """Pixels per cell in the blended albedo.

    This used to be 1024 px for the whole map (16 px a cell on a 64x64 map),
    which is why the ground looked blurred next to the client. Now a cell gets
    up to 64 px, as long as the image stays within 4096 px a side: 64 px on a
    64-cell map, 32 on 128, 16 on 256."""
    return max(2, min(MAX_CELL_PX, MAX_SIDE_PX // max(dim_x, dim_y)))


def _opaque(img):
    return img.convertToFormat(QImage.Format_RGB32)


def _corner_px(layer, corner: int) -> tuple:
    effective = (3 - corner) if layer.rotated else corner
    cx, cy = effective % 2, effective // 2
    local_x = BORDER if cx == 0 else QUADRANT - BORDER
    local_y = BORDER if cy == 0 else QUADRANT - BORDER
    ox, oy = OFFSETS[layer.quadrant]
    return ox + local_x, oy + local_y


def build_layers(dim_x: int, dim_y: int, tiles, table_a: bytes, variation=None) -> list:
    """One `[Layer]` list per cell, world row-major, far edges replicated.

    `table_a` is terrain tag 4. `tiles` is the per-cell tile byte layer. The
    variation is the client's per-cell draw unless an authored tag-3 layer is
    supplied."""
    if variation is None:
        variation = trnvariation.map_variation(dim_x, dim_y, None)
    return trnblend.map_layers(dim_x, dim_y, tiles, table_a, variation)


def _sample(img, x: float, y: float) -> tuple:
    c = img.pixelColor(min(max(int(x), 0), img.width() - 1),
                       min(max(int(y), 0), img.height() - 1))
    return c.red(), c.green(), c.blue(), c.alpha()


def _brighten(rgb):
    return tuple(min(255, int(v * BRIGHTNESS)) for v in rgb)


def _cell_color(layers, images) -> tuple:
    base = layers[0]
    img = images[base.tile] if 0 <= base.tile < len(images) else None
    if img is None or img.isNull():
        return FALLBACK
    ox, oy = OFFSETS[base.quadrant]
    r, g, b, _a = _sample(img, ox + 64, oy + 64)
    for layer in layers[1:]:
        img = images[layer.tile] if 0 <= layer.tile < len(images) else None
        if img is None or img.isNull():
            continue
        ox, oy = OFFSETS[layer.quadrant]
        tr, tg, tb, ta = _sample(img, ox + 64, oy + 64)
        f = ta / 255.0
        r = int(tr * f + r * (1 - f))
        g = int(tg * f + g * (1 - f))
        b = int(tb * f + b * (1 - f))
    return _brighten((r, g, b))


def _brighten_image(img):
    """TerrainRevPixelShader.hlsl multiplies the blended result by 1.4. Add 40% of
    the image to itself; Plus clamps at 255."""
    boost = img.copy()
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode_Plus)
    p.setOpacity(0.4)
    p.drawImage(0, 0, boost)
    p.setOpacity(1.0)
    p.end()


def _draw_cell(painter, cell_layers, images, opaque, scale: int, dx: int, dy: int) -> None:
    """Draw one cell's layers into the rect `(dx, dy, scale, scale)`."""
    dst = QRectF(dx, dy, scale, scale)
    dst_poly = QPolygonF([dst.topLeft(), dst.topRight(), dst.bottomRight(), dst.bottomLeft()])
    drew_base = False
    for layer in cell_layers:
        if not 0 <= layer.tile < len(images):
            continue                # that texture is not decoded (yet)
        source = opaque[layer.tile] if layer.base else images[layer.tile]
        if source is None or source.isNull():
            continue
        pts = [_corner_px(layer, k) for k in range(4)]
        src_poly = QPolygonF([QPointF(*pts[0]), QPointF(*pts[1]),
                              QPointF(*pts[3]), QPointF(*pts[2])])
        t = QTransform()
        if not QTransform.quadToQuad(src_poly, dst_poly, t):
            continue
        painter.save()
        painter.setClipRect(dst)
        painter.setTransform(t)
        painter.drawImage(QPointF(0, 0), source)
        painter.restore()
        if layer.base:
            drew_base = True
    if not drew_base:
        painter.fillRect(dst, QColor(*FALLBACK))


def _tiles_of(project, cells: int):
    tiles = project.tiles if project.tiles else bytes(cells)
    if len(tiles) != cells:
        tiles = list(tiles)[:cells] + [0] * max(0, cells - len(tiles))
    return tiles


def cell_layers_at(dim_x, dim_y, tiles, table_a, variation, gx, gy):
    """`trnblend.map_layers` for one cell, so a stroke recomputes only its cells."""
    gy1 = min(gy + 1, dim_y - 1)
    gx1 = min(gx + 1, dim_x - 1)
    corners = (tiles[gy * dim_x + gx], tiles[gy * dim_x + gx1],
               tiles[gy1 * dim_x + gx], tiles[gy1 * dim_x + gx1])
    sel = trnblend.corner_selector(tuple(table_a[c] for c in corners))
    perm = tuple((sel >> (2 * k)) & 3 for k in range(4))
    corners = tuple(corners[p] for p in perm)
    return trnblend.cell_layers(corners, table_a, variation[gy * dim_x + gx], perm=perm)


def pack_layers(layers, dim_x: int, dim_y: int, out=None, rect=None) -> bytearray:
    """The per-cell layer table as an RGBA8 image `3*dim_x` wide, for the GPU.

    Texel (3*gx + k, gy) is layer k of cell (gx, gy): R = tile, G = quadrant +
    4*rotated, B = 255 when the layer exists. The Render Window's terrain shader
    reads it and samples the textures itself, as the client's does."""
    w = dim_x * 3
    if out is None or len(out) != w * dim_y * 4:
        out = bytearray(w * dim_y * 4)
        rect = None
    x0, y0, x1, y1 = rect if rect is not None else (0, 0, dim_x - 1, dim_y - 1)
    for gy in range(max(0, y0), min(dim_y - 1, y1) + 1):
        for gx in range(max(0, x0), min(dim_x - 1, x1) + 1):
            cell = layers[gy * dim_x + gx]
            o = (gy * w + gx * 3) * 4
            for k in range(3):
                if k < len(cell):
                    layer = cell[k]
                    out[o:o + 4] = bytes((layer.tile & 0xFF,
                                          (layer.quadrant & 3) | (4 if layer.rotated else 0),
                                          255, 255))
                else:
                    out[o:o + 4] = b"\0\0\0\0"
                o += 4
    return out


def build_surface(project, images, table_a: bytes, scale: int | None = None,
                  with_image: bool = True) -> GroundSurface:
    """The blended top-down albedo and one colour per cell for a map project."""
    steps = build_surface_steps(project, images, table_a, scale, with_image)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


def build_surface_steps(project, images, table_a: bytes, scale: int | None = None,
                        with_image: bool = True):
    """build_surface in slices: a generator that yields after each row of cells
    and returns the GroundSurface. The main window advances it from a timer so
    the window stays responsive while a large map's blend is drawn - on the GUI
    thread, because QPainter work on a worker thread crashed the editor."""
    dim_x, dim_y = project.dim_x, project.dim_y
    cells = dim_x * dim_y
    tiles = _tiles_of(project, cells)
    variation = trnvariation.map_variation(dim_x, dim_y, None)
    layers = trnblend.map_layers(dim_x, dim_y, tiles, table_a, variation)
    if not with_image:
        # the GPU blends from the layer table; only the Top View's colours here
        yield dim_y - 1
        colors = [_cell_color(layer, images) for layer in layers]
        return GroundSurface(None, colors, dim_x, dim_y, 0, layers, list(variation))
    scale = _scale_for(dim_x, dim_y) if scale is None else max(1, int(scale))
    opaque = [_opaque(img) if img is not None and not img.isNull() else None for img in images]
    out = QImage(dim_x * scale, dim_y * scale, QImage.Format_ARGB32_Premultiplied)
    out.fill(QColor(*FALLBACK))
    painter = QPainter(out)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    try:
        for gy in range(dim_y):
            for gx in range(dim_x):
                _draw_cell(painter, layers[gy * dim_x + gx], images, opaque, scale,
                           gx * scale, gy * scale)
            yield gy
    finally:
        painter.end()
    _brighten_image(out)
    colors = [_cell_color(layer, images) for layer in layers]
    return GroundSurface(out, colors, dim_x, dim_y, scale, layers, list(variation))


def update_surface(surface: GroundSurface, project, images, table_a: bytes, rect) -> None:
    """Repaint the cells a texture stroke touched, in place.

    A stroke is a few cells, so rebuilding the whole albedo for each dab is what
    made texture painting flash the unblended fallback. Only the touched rect is
    redrawn; each cell goes through a small image first so the shader's 1.4
    brightness is applied to the blend and not to its pieces."""
    dim_x, dim_y, scale = surface.dim_x, surface.dim_y, surface.scale
    if surface.image is None:
        # GPU blending: refresh the touched cells' layers and Top View colours
        tiles = _tiles_of(project, dim_x * dim_y)
        variation = surface.variation or trnvariation.map_variation(dim_x, dim_y, None)
        x0 = max(0, int(rect[0]) - 1)
        y0 = max(0, int(rect[1]) - 1)
        x1 = min(dim_x - 1, int(rect[2]) + 1)
        y1 = min(dim_y - 1, int(rect[3]) + 1)
        for gy in range(y0, y1 + 1):
            for gx in range(x0, x1 + 1):
                cell = cell_layers_at(dim_x, dim_y, tiles, table_a, variation, gx, gy)
                surface.layers[gy * dim_x + gx] = cell
                surface.cell_colors[gy * dim_x + gx] = _cell_color(cell, images)
        return
    if scale <= 0 or surface.image.isNull():
        return
    x0 = max(0, int(rect[0]) - 1)
    y0 = max(0, int(rect[1]) - 1)
    x1 = min(dim_x - 1, int(rect[2]) + 1)
    y1 = min(dim_y - 1, int(rect[3]) + 1)
    if x1 < x0 or y1 < y0:
        return
    tiles = _tiles_of(project, dim_x * dim_y)
    layers = build_layers(dim_x, dim_y, tiles, table_a)
    opaque = [_opaque(img) if img is not None and not img.isNull() else None for img in images]
    painter = QPainter(surface.image)
    for gy in range(y0, y1 + 1):
        for gx in range(x0, x1 + 1):
            cell = layers[gy * dim_x + gx]
            tmp = QImage(scale, scale, QImage.Format_ARGB32_Premultiplied)
            tmp.fill(QColor(*FALLBACK))
            tp = QPainter(tmp)
            tp.setRenderHint(QPainter.SmoothPixmapTransform, True)
            _draw_cell(tp, cell, images, opaque, scale, 0, 0)
            tp.end()
            _brighten_image(tmp)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.drawImage(gx * scale, gy * scale, tmp)
            surface.cell_colors[gy * dim_x + gx] = _cell_color(cell, images)
    painter.end()
    surface.layers = layers
