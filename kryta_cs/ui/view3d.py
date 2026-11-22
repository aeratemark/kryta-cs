"""The Render Window's 3D view: the map as a surface you sculpt and place spawns on.

This is the editor the Morrowind Construction Set put in the middle of its screen:
a terrain you look at from an angle, a red circle that is the edit radius, and a
wireframe/cell-border view for judging the mesh. The 2D Top View is still there
for precise work (View > Top View), but this is the default.

Camera, as the Morrowind CS: hold Shift and move the mouse to rotate the view
around the selected object (or turn in place with nothing selected); middle
drag or Space + mouse move pans; the wheel zooms toward the point under the mouse; the arrow keys move the view, Shift+Up/Down raises and lowers it;
F fits the map. Shift or Alt + middle drag still orbits the point under the mouse. Landscape editing: left drag applies the brush at
the point under the cursor, right drag paints the donor texture selected in the
Object Window, Ctrl+wheel sizes the brush. Spawns and placed models: left
selects and drags, double-click opens a model's properties, right-click (Select
tool) removes it, Delete removes the selected one. Shift+left also orbits."""

from __future__ import annotations

import json
import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import QWidget

from .. import terrain, texlayers
from ..model import CELL, Spawn, prop_apply, prop_matrix
from .document import (HeightsCommand, PropsCommand, SpawnsCommand, TilesCommand, copy_heights,
                       copy_props, copy_spawns, copy_tiles)
from .heightmap_view import SCULPT, SELECT, SPAWN, ramp
from .images import qimage
from .theme import COLORS
from .uvmap import affine

MAX_SIDE = 72
TEXTURED_MIN_AREA = 260.0   # a triangle smaller than this many px^2 cannot show its texture
LOD_TRI_BUDGET = 700        # triangles a placed model may draw, however far you zoom in
TEXTURED_FRAME_BUDGET = 200 # textured (per-triangle) draws a whole frame may make
LIGHT = (0.45, 0.48, 0.75)  # z-up: the light mostly comes from above
MARKER_PX = 260.0
FOV = math.radians(60.0)        # the CS's render window is a perspective view
NEAR = 0.05                     # grid units; a point closer than this is clipped


def _clip_box(eye, d, lo, hi):
    """The (t0, t1) the ray spends inside an axis-aligned box, or (1, 0)."""
    t0, t1 = 0.0, 1e18
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if eye[i] < lo[i] or eye[i] > hi[i]:
                return 1.0, 0.0
            continue
        a = (lo[i] - eye[i]) / d[i]
        b = (hi[i] - eye[i]) / d[i]
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return 1.0, 0.0
    return t0, t1


def _ray_triangle(o, d, a, b, c):
    """Distance along ray (o, d) to triangle abc (Moller-Trumbore), or None.
    Two-sided: props are often single-sided panels."""
    e1x, e1y, e1z = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    e2x, e2y, e2z = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    px, py, pz = d[1] * e2z - d[2] * e2y, d[2] * e2x - d[0] * e2z, d[0] * e2y - d[1] * e2x
    det = e1x * px + e1y * py + e1z * pz
    if -1e-12 < det < 1e-12:
        return None
    inv = 1.0 / det
    tx, ty, tz = o[0] - a[0], o[1] - a[1], o[2] - a[2]
    u = (tx * px + ty * py + tz * pz) * inv
    if u < 0.0 or u > 1.0:
        return None
    qx, qy, qz = ty * e1z - tz * e1y, tz * e1x - tx * e1z, tx * e1y - ty * e1x
    v = (d[0] * qx + d[1] * qy + d[2] * qz) * inv
    if v < 0.0 or u + v > 1.0:
        return None
    t = (e2x * qx + e2y * qy + e2z * qz) * inv
    return t if t > 1e-6 else None


def _bilinear(p, gx: float, gy: float) -> float:
    """The height at a fractional grid point, between the height-field samples."""
    gx = min(max(gx, 0.0), p.dim_x - 1.0)
    gy = min(max(gy, 0.0), p.dim_y - 1.0)
    x0, y0 = int(math.floor(gx)), int(math.floor(gy))
    x1, y1 = min(x0 + 1, p.dim_x - 1), min(y0 + 1, p.dim_y - 1)
    fx, fy = gx - x0, gy - y0
    a, b = p.heights[y0][x0], p.heights[y0][x1]
    c, d = p.heights[y1][x0], p.heights[y1][x1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


class _View3DCore:
    """Camera, picking, editing and the software painter, shared by both
    Render Window implementations. The concrete classes add the Qt base and
    the signals; see SoftTerrain3DView and view3d_gl.GLTerrain3DView."""

    BUILD_LODS = True

    def _init_core(self, doc, brush):
        self.doc, self.brush = doc, brush
        self.tool = SCULPT
        self.yaw = math.radians(30)
        self.pitch = math.radians(45)
        self.zoom = 1.0
        self.exag = 1.0
        self.tx, self.ty, self.tz = 0.0, 0.0, 0.0
        self.base_dist = 100.0
        self.show_wireframe = False
        self.show_cells = True
        self.textures = []
        self.paint_tile = 0
        self.use_texture = True
        self.fast_texture = True
        self.show_radius = True
        self._tex_ready = []
        self._ground_image = None
        self._ground_dims = (0, 0)
        self._step = 1
        self._mesh_version = 0
        self.props = []
        self.prop_geo = {}
        self.prop_images = {}
        self.prop_mesh = {}
        self._props_version = 0
        self._pm = None
        self._cache_key = None
        self._quads = []
        self._mouse = None
        self._pick = None
        self._drag = None
        self._stroke_before = None
        self._tile_before = None
        self._last_dab = None
        self._spawns_before = None
        self._drag_spawn = None
        self._prop_drag = None
        self._prop_rot = None
        self._space = False
        self._look_last = None       # Shift + mouse move: the CS's camera rotation
        self._pan_last = None        # Space + mouse move: the CS's pan
        self._needs_fit = True
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._rebuild)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumSize(240, 200)
        self.setToolTip("Hold Shift and move the mouse to rotate the view (around the selected "
                        "object). Middle drag or Space pans. The wheel zooms. Arrows move. F fits.")
        doc.project_changed.connect(self._rebuild)
        doc.heights_changed.connect(lambda _r: self._timer.start())
        doc.analysis_changed.connect(self._timer.start)
        doc.textures_changed.connect(self._textures_changed)
        doc.spawns_changed.connect(self.update)
        doc.props_changed.connect(self.update)
        doc.selection_changed.connect(lambda _i: self.update())
        doc.prop_selection_changed.connect(lambda _i: self.update())

    # ------------------------------------------------------------ camera

    def _basis(self):
        """(forward, right, up) in grid space, z up, for the current angles."""
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        return ((sy * cp, cy * cp, -sp), (cy, -sy, 0.0), (sy * sp, cy * sp, cp))

    def _dist(self) -> float:
        return max(0.05, self.base_dist / max(1e-4, self.zoom))

    def _camera(self) -> tuple:
        """(centre x, centre y, focal, eye, right, up, forward).

        A real perspective camera, as the CS's render window was: the eye sits
        `dist` back along the view direction, and a point's screen position is
        its camera-space x/y divided by its depth. Heights are in grid units,
        so the model's world z is divided by CELL exactly as the old
        axonometric projection did."""
        p = self.doc.project
        fwd, right, up = self._basis()
        if p is None:
            return (self.width() / 2.0, self.height() / 2.0, 1.0,
                    (0.0, 0.0, 0.0), right, up, fwd)
        d = self._dist()
        tz = self.tz / CELL * self.exag
        eye = (self.tx - fwd[0] * d, self.ty - fwd[1] * d, tz - fwd[2] * d)
        focal = (self.height() / 2.0) / math.tan(FOV / 2.0)
        return self.width() / 2.0, self.height() / 2.0, focal, eye, right, up, fwd

    def _keep_above_ground(self):
        """Never let the eye go under the terrain.

        Panning and orbiting move the eye along the view plane, so a long drag
        can put it below the map - which reads as the world turning upside down,
        with the ground above you and the models hanging. The CS's camera never
        does that."""
        p = self.doc.project
        if p is None:
            return
        fwd = self._basis()[0]
        d = self._dist()
        gx = min(max(self.tx - fwd[0] * d, 0.0), p.dim_x - 1.0)
        gy = min(max(self.ty - fwd[1] * d, 0.0), p.dim_y - 1.0)
        eye = self.tz / CELL * self.exag - fwd[2] * d
        floor = _bilinear(p, gx, gy) / CELL * self.exag + 0.35
        if eye < floor:
            self.tz += (floor - eye) * CELL / self.exag

    def _selection_pivot(self):
        """(gx, gy, world z) of the selected prop or spawn, or None.

        The CS rotated the view about the selected object and zoomed onto it;
        with nothing selected it turned in place and zoomed on the centre."""
        p = self.doc.project
        if p is None:
            return None
        i = self.doc.selected_prop
        if 0 <= i < len(p.props):
            q = p.props[i]
            gx, gy = p.world_to_cell(q.x, q.y)
            return gx, gy, q.z
        i = self.doc.selected
        if 0 <= i < len(p.spawns):
            s = p.spawns[i]
            gx, gy = p.world_to_cell(s.x, s.y)
            return gx, gy, p.height_at_world(s.x, s.y)
        return None

    def _look(self, dx: float, dy: float):
        """Shift + mouse move: orbit the selection, or turn the camera in place."""
        pivot = self._selection_pivot()
        pitch = min(math.radians(88), max(math.radians(8), self.pitch + dy * 0.008))
        if pivot is not None:
            screen, depth = self._project(*pivot)
            if depth > 0.06:
                self.yaw += dx * 0.008
                self.pitch = pitch
                self._pin(screen, pivot)
                self._keep_above_ground()
                return
        eye = self._camera()[3]
        self.yaw += dx * 0.008
        self.pitch = pitch
        fwd = self._basis()[0]
        d = self._dist()
        self.tx = eye[0] + fwd[0] * d
        self.ty = eye[1] + fwd[1] * d
        self.tz = (eye[2] + fwd[2] * d) * CELL / self.exag

    def _step_view(self, forward: float, side: float, rise: float):
        """Arrow keys: move the view over the ground (Shift+Up/Down: up and down)."""
        step = max(0.5, self._dist() * 0.12)
        sy, cy = math.sin(self.yaw), math.cos(self.yaw)
        self.tx += (sy * forward + cy * side) * step
        self.ty += (cy * forward - sy * side) * step
        self.tz += rise * step * CELL / self.exag
        self._keep_above_ground()

    def _pan_pixels(self, dx: float, dy: float, depth: float | None = None):
        """Slide the view by a screen delta, one pixel on screen per pixel moved.

        Moving the content right means moving the camera (and its target) left;
        moving it down means moving the target up along the view plane."""
        cam = self._camera()
        focal, right, up = cam[2], cam[4], cam[5]
        k = (self._dist() if depth is None else depth) / max(1.0, focal)
        self.tx += (-right[0] * dx + up[0] * dy) * k
        self.ty += (-right[1] * dx + up[1] * dy) * k
        self.tz += (-right[2] * dx + up[2] * dy) * k * CELL / self.exag
        self._keep_above_ground()

    def _pin(self, screen, anchor):
        """Move the view until `anchor` (grid x, y, z) sits under `screen`.

        Zooming and orbiting both change where a ground point lands on screen.
        Pinning the point under the mouse is what makes the wheel feel like it
        zooms into the cursor and the orbit feel like it turns around what you
        are looking at, instead of the widget's centre."""
        if anchor is None:
            return
        for _ in range(3):
            pt, depth = self._project(anchor[0], anchor[1], anchor[2])
            dx, dy = screen.x() - pt.x(), screen.y() - pt.y()
            if abs(dx) < 0.25 and abs(dy) < 0.25:
                break
            self._pan_pixels(dx, dy, depth)

    def _project(self, gx: float, gy: float, z: float, cam=None) -> tuple:
        cam = self._camera() if cam is None else cam
        cx, cy, focal, eye, right, up, fwd = cam
        px, py = gx - eye[0], gy - eye[1]
        pz = z / CELL * self.exag - eye[2]
        zc = px * fwd[0] + py * fwd[1] + pz * fwd[2]
        if zc < NEAR:
            zc = NEAR
        xc = px * right[0] + py * right[1] + pz * right[2]
        yc = px * up[0] + py * up[1] + pz * up[2]
        return QPointF(cx + xc / zc * focal, cy - yc / zc * focal), zc

    def _ray(self, pos) -> tuple:
        """(eye, unit direction) through a screen point, in grid space."""
        cx, cy, focal, eye, right, up, fwd = self._camera()
        dx = (pos.x() - cx) / max(1.0, focal)
        dy = (cy - pos.y()) / max(1.0, focal)
        d = (fwd[0] + right[0] * dx + up[0] * dy,
             fwd[1] + right[1] * dx + up[1] * dy,
             fwd[2] + right[2] * dx + up[2] * dy)
        n = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) or 1.0
        return eye, (d[0] / n, d[1] / n, d[2] / n)

    def _pick_terrain(self, pos) -> tuple | None:
        """The terrain point under a screen position, by casting the view ray.

        A perspective view has no closed-form unprojection onto a heightfield,
        so the ray is clipped to the map's box, marched in 64 steps and then
        bisected: the same answer the fixed-point loop gave, for any camera."""
        p = self.doc.project
        if p is None:
            return None
        eye, d = self._ray(pos)
        lo, hi = terrain.height_range(p)
        t0, t1 = _clip_box(eye, d, (0.0, 0.0, lo / CELL * self.exag),
                           (p.dim_x - 1.0, p.dim_y - 1.0, hi / CELL * self.exag))
        if t1 <= t0:
            return None
        hit = None
        prev = t0
        for i in range(1, 65):
            t = t0 + (t1 - t0) * i / 64.0
            dz = (eye[2] + d[2] * t) - self._grid_height(p, eye[0] + d[0] * t,
                                                         eye[1] + d[1] * t)
            if dz <= 0.0:
                a, b = prev, t
                for _ in range(24):
                    m = (a + b) / 2.0
                    mz = (eye[2] + d[2] * m) - self._grid_height(
                        p, eye[0] + d[0] * m, eye[1] + d[1] * m)
                    if mz > 0.0:
                        a = m
                    else:
                        b = m
                hit = b
                break
            prev = t
        if hit is None:
            return None
        gx = min(max(eye[0] + d[0] * hit, 0.0), p.dim_x - 1.0)
        gy = min(max(eye[1] + d[1] * hit, 0.0), p.dim_y - 1.0)
        return gx, gy, _bilinear(p, gx, gy)

    def _grid_height(self, p, gx: float, gy: float) -> float:
        return _bilinear(p, gx, gy) / CELL * self.exag

    def _world_at(self, gx: float, gy: float) -> tuple:
        p = self.doc.project
        return (gx + 0.5) * CELL, (p.dim_y - 1 - gy + 0.5) * CELL

    def _focus_grid(self) -> tuple:
        p = self.doc.project
        if p is None:
            return 0.0, 0.0
        hit = self._pick_terrain(QPointF(self.width() / 2.0, self.height() / 2.0))
        if hit is None:
            return (p.dim_x - 1) / 2.0, (p.dim_y - 1) / 2.0
        return hit[0], hit[1]

    def focus_grid(self) -> tuple:
        return self._focus_grid()

    def center_on_grid(self, gx: float, gy: float):
        p = self.doc.project
        if p is None:
            return
        self.tx, self.ty = float(gx), float(gy)
        self.tz = _bilinear(p, gx, gy)
        self.update()
        self.view_changed.emit()
        self.user_moved.emit()

    def center_on_world(self, x: float, y: float):
        p = self.doc.project
        if p is None:
            return
        gx, gy = p.world_to_cell(x, y)
        self.center_on_grid(gx, gy)

    def visible_grid_rect(self):
        """No rectangular viewport in a perspective view; the Cell View shows the focus."""
        return None

    def zoom_text(self) -> str:
        return f"{self.zoom * 100:.0f}%"

    def camera_text(self) -> str:
        p = self.doc.project
        if p is None:
            return ""
        gx, gy = self._focus_grid()
        x, y = self._world_at(gx, gy)
        z = _bilinear(p, gx, gy)
        return (f"Camera {x:,.0f}, {y:,.0f}, {z:,.0f}   "
                f"cell {int(gx + 0.5)},{int(gy + 0.5)}   "
                f"view {math.degrees(self.yaw) % 360:.0f}/"
                f"{math.degrees(self.pitch):.0f} deg, {self.zoom * 100:.0f}%")

    def request_fit(self):
        self._needs_fit = True
        self.fit()

    def _screen_bounds(self, p, xs, ys):
        cam = self._camera()
        x0 = y0 = 1e18
        x1 = y1 = -1e18
        for x in xs:
            for y in ys:
                pt, _d = self._project(x, y, _bilinear(p, x, y), cam)
                x0, y0 = min(x0, pt.x()), min(y0, pt.y())
                x1, y1 = max(x1, pt.x()), max(y1, pt.y())
        return x0, y0, x1, y1

    def fit(self):
        p = self.doc.project
        if p is None or self.width() < 40 or self.height() < 40:
            self._needs_fit = True
            self.update()
            self.view_changed.emit()
            return
        lo, hi = terrain.height_range(p)
        self.zoom = 1.0
        self.tx = (p.dim_x - 1) / 2.0
        self.ty = (p.dim_y - 1) / 2.0
        self.tz = (lo + hi) / 2.0
        self.base_dist = max(p.dim_x, p.dim_y) * 1.15
        step = max(1, int(max(p.dim_x, p.dim_y) / 12))
        xs = list(range(0, p.dim_x, step)) + [p.dim_x - 1]
        ys = list(range(0, p.dim_y, step)) + [p.dim_y - 1]
        for _ in range(4):
            x0, y0, x1, y1 = self._screen_bounds(p, xs, ys)
            f = min((self.width() - 56.0) / max(1.0, x1 - x0),
                    (self.height() - 56.0) / max(1.0, y1 - y0))
            self.base_dist /= max(0.25, min(4.0, f))
        x0, y0, x1, y1 = self._screen_bounds(p, xs, ys)
        self._pan_pixels(self.width() / 2.0 - (x0 + x1) / 2.0,
                         self.height() / 2.0 - (y0 + y1) / 2.0)
        self._needs_fit = False
        self.update()
        self.view_changed.emit()

    def reset_camera(self):
        self.yaw = math.radians(30)
        self.pitch = math.radians(45)
        self.fit()

    def set_wireframe(self, on: bool):
        self.show_wireframe = bool(on)
        self._invalidate_cache()
        self.update()

    def set_cell_borders(self, on: bool):
        self.show_cells = bool(on)
        self.update()

    def set_tool(self, key: str):
        self.tool = key
        self.setCursor(Qt.CrossCursor if key != SELECT else Qt.ArrowCursor)
        self.update()

    # ------------------------------------------------------------ textures

    def set_textures(self, images):
        """The donor's texture table in tile order, prepared for the per-tile
        fallback while the blended ground image is being rebuilt. The base
        layer's alpha is not a mask, so this copy is RGB-only."""
        ready = []
        for img in images:
            if img is None or img.isNull():
                ready.append(None)
            else:
                ready.append(img.convertToFormat(QImage.Format_RGB32))
        self.textures = ready
        self._tex_ready = [img is not None for img in ready]
        self._invalidate_cache()
        self.update()

    def set_paint_texture(self, tile: int):
        self.paint_tile = max(0, int(tile))

    # the software painter draws a pre-blended albedo image; the OpenGL view
    # blends on the GPU from the layer table and does not need one
    needs_ground_image = True

    def set_ground_layers(self, layers, dim_x: int = 0, dim_y: int = 0, rect=None):
        """The per-cell texture layers (the OpenGL view blends from these)."""

    def set_ground_image(self, image, dim_x: int = 0, dim_y: int = 0):
        """The blended top-down albedo, or None to fall back to per-tile brushes."""
        self._ground_image = image
        self._ground_dims = (int(dim_x), int(dim_y))
        self._invalidate_cache()
        self.update()

    def set_props(self, props, geometries=None):
        self.props = props if props is not None else []
        self.prop_geo = dict(geometries or {})
        self.prop_images = {}
        self.prop_mesh = {}
        for fid, ma in self.prop_geo.items():
            imgs = []
            for t in getattr(ma, "textures", []) or []:
                imgs.append(qimage(t))
            self.prop_images[fid] = imgs
            self.prop_mesh[fid] = self._build_mesh(ma, imgs, self.BUILD_LODS)
        self._props_version += 1
        self._invalidate_cache()
        self.update()

    @staticmethod
    def _average_colour(img):
        """The colour a submodel reads as when its triangles are too small to
        show their texture: the mean of the texture's opaque pixels."""
        if img is None or img.isNull():
            return QColor(120, 116, 104)
        r = g = b = n = 0
        step = max(1, img.width() // 12)
        for y in range(0, img.height(), step):
            for x in range(0, img.width(), step):
                c = img.pixelColor(x, y)
                if c.alpha() < 32:
                    continue
                r += c.red()
                g += c.green()
                b += c.blue()
                n += 1
        if not n:
            return QColor(120, 116, 104)
        return QColor(r // n, g // n, b // n)

    @staticmethod
    def _build_mesh(ma, images, build_lods=True):
        """A prop model's submodels as data the Render Window can draw every
        frame without re-decoding the archive.

        Vertices stay in the model's own space. That space is the map's: x and
        y are the ground plane and **z is height, negative upward** (these
        walls run to z = -1100 with their base near 0, exactly like the map's
        own z, where a greater stored value is lower in the world). The paint
        step therefore flips z once, at the end."""
        geo = getattr(ma, "geometry", ma)
        subs = getattr(ma, "sub_textures", [])
        out = []
        for si, sm in enumerate(geo.submodels):
            verts = list(sm.positions())
            idx = list(sm.indices)
            tris = []
            for i in range(0, len(idx) - 2, 3):
                a, b, c = idx[i], idx[i + 1], idx[i + 2]
                if max(a, b, c) < len(verts):
                    tris.append((a, b, c))
            slot, uv_set = subs[si] if si < len(subs) else (None, 0)
            img = images[slot] if slot is not None and 0 <= slot < len(images) else None
            uvs = None
            if img is not None and not img.isNull():
                try:
                    uvs = sm.texcoords(int(uv_set))
                except Exception:                                    # noqa: BLE001
                    uvs = None
            normals = []
            for a, b, c in tris:
                pa, pb, pc = verts[a], verts[b], verts[c]
                ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
                vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
                nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
                n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                normals.append((nx / n, ny / n, nz / n))
            lods = [(verts, tris, normals, uvs)]
            # A model at map scale is a few pixels a side; its 2,000 triangles
            # buy nothing there. A clustering pass gives a coarser copy that is
            # used while the model is small on screen, and the full mesh comes
            # back as soon as you zoom in on it.
            if build_lods and len(tris) > 350:
                size = max(max(abs(v[0]) for v in verts), max(abs(v[1]) for v in verts)) or 1.0
                coarse = _View3DCore._cluster(verts, tris, uvs, normals, size / 14.0)
                if coarse and len(coarse[0][1]) < len(tris):
                    lods.append(coarse)
                if len(tris) > 1400:
                    finer = _View3DCore._cluster(verts, tris, uvs, normals, size / 26.0)
                    if finer and len(finer[0][1]) < len(lods[-1][0][1]):
                        lods.append(finer)
            out.append({"lods": lods, "image": img,
                        "colour": _View3DCore._average_colour(img),
                        "height": (max((v[2] for v in verts), default=0.0) -
                                   min((v[2] for v in verts), default=0.0)),
                        "radius": max((math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
                                       for v in verts), default=1.0)})
        return out

    @staticmethod
    def _cluster(verts, tris, uvs, normals, cell):
        """Merge vertices inside a cell and rebuild the triangles that survive.

        Quadric decimation would be prettier, but this is one pass, keeps the
        silhouette, and cannot open holes the way dropping every Nth triangle
        does: a triangle vanishes only when two of its corners land in the same
        cell."""
        remap = {}
        index = [0] * len(verts)
        out_verts = []
        out_uvs = [] if uvs is not None else None
        for i, v in enumerate(verts):
            key = (int(v[0] // cell), int(v[1] // cell), int(v[2] // cell))
            j = remap.get(key)
            if j is None:
                j = len(out_verts)
                remap[key] = j
                out_verts.append(v)
                if out_uvs is not None:
                    out_uvs.append(uvs[i])
            index[i] = j
        out_tris = []
        for a, b, c in tris:
            ia, ib, ic = index[a], index[b], index[c]
            if ia == ib or ib == ic or ia == ic:
                continue
            out_tris.append((ia, ib, ic))
        out_normals = []
        for a, b, c in out_tris:
            pa, pb, pc = out_verts[a], out_verts[b], out_verts[c]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            out_normals.append((nx / n, ny / n, nz / n))
        if not out_tris:
            return None
        return (out_verts, out_tris, out_normals, out_uvs)

    def _textures_changed(self, _rect=None):
        self._invalidate_cache()
        self.update()

    def _invalidate_cache(self):
        self._cache_key = None

    def _buffer(self):
        """The off-screen pixmap, reused across frames so a camera move does not
        allocate three megabytes every time."""
        if self._pm is None or self._pm.size() != self.size():
            self._pm = QPixmap(self.size())
        return self._pm

    def _texture(self, tile: int):
        if 0 <= tile < len(self.textures):
            return self.textures[tile]
        return None

    # ------------------------------------------------------------ mesh

    def _rebuild(self):
        p = self.doc.project
        self._quads = []
        self._mesh_version += 1
        self._invalidate_cache()
        if p is None:
            self.update()
            return
        textured = self.use_texture and any(self._tex_ready)
        step = max(1, math.ceil(max(p.dim_x, p.dim_y) / (48 if textured else MAX_SIDE)))
        self._step = step
        xs = list(range(0, p.dim_x, step))
        ys = list(range(0, p.dim_y, step))
        if xs[-1] != p.dim_x - 1:
            xs.append(p.dim_x - 1)
        if ys[-1] != p.dim_y - 1:
            ys.append(p.dim_y - 1)
        lo, hi = terrain.height_range(p)
        span = max(1.0, hi - lo)
        h = p.heights
        cl = self.doc.classes
        self._shade_shadow = QImage(max(1, len(xs) - 1), max(1, len(ys) - 1),
                                    QImage.Format_ARGB32_Premultiplied)
        self._shade_light = QImage(self._shade_shadow.size(), QImage.Format_ARGB32_Premultiplied)
        self._shade_shadow.fill(QColor(0, 0, 0, 0))
        self._shade_light.fill(QColor(0, 0, 0, 0))
        for j in range(len(ys) - 1):
            for i in range(len(xs) - 1):
                x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
                a, b, c, d = h[y0][x0], h[y0][x1], h[y1][x1], h[y1][x0]
                avg = (a + b + c + d) / 4
                dzdx = ((b + c) - (a + d)) / (2 * (x1 - x0) * CELL)
                dzdy = ((d + c) - (a + b)) / (2 * (y1 - y0) * CELL)
                n = math.sqrt(dzdx * dzdx + dzdy * dzdy + 1)
                shade = max(0.0, (-dzdx * -0.5 + -dzdy * -0.5 + 0.7071) / n)
                shade = 0.35 + 0.8 * shade
                r, g, bb = ramp((avg - lo) / span)
                klass = 0
                if cl is not None:
                    qy, qx = min(y0, len(cl) - 1), min(x0, len(cl[0]) - 1)
                    klass = cl[qy][qx]
                if klass == terrain.CLASS_STEEP:
                    r, g, bb = r * 0.55 + 220 * 0.45, g * 0.55 + 60 * 0.45, bb * 0.55 + 60 * 0.45
                elif klass == terrain.CLASS_RISKY:
                    r, g, bb = r * 0.55 + 240 * 0.45, g * 0.55 + 150 * 0.45, bb * 0.55 + 40 * 0.45
                col = QColor(min(255, int(r * shade)), min(255, int(g * shade)), min(255, int(bb * shade)))
                self._quads.append(((x0, y0, a), (x1, y0, b), (x1, y1, c), (x0, y1, d), col, shade))
                if shade < 1.0:
                    self._shade_shadow.setPixelColor(i, j, QColor(0, 0, 0, int(min(1.0, 1.0 - shade) * 150)))
                elif shade > 1.0:
                    self._shade_light.setPixelColor(i, j, QColor(255, 255, 255,
                                                                 int(min(1.0, shade - 1.0) * 90)))
        self.update()

    # ------------------------------------------------------------ painting

    def _soft_paint(self, qp):
        """The software painter (no depth buffer). Used only when OpenGL is
        unavailable or KRYTA_CS_SOFTWARE_3D is set."""
        p = self.doc.project
        if p is None or not self._quads:
            g = QLinearGradient(0, 0, 0, self.height())
            g.setColorAt(0, QColor(COLORS["sky_top"]))
            g.setColorAt(1, QColor(COLORS["sky_bottom"]))
            qp.fillRect(self.rect(), g)
            qp.setPen(QColor(COLORS["vtext"]))
            qp.drawText(self.rect(), Qt.AlignCenter, "Render Window")
            return
        key = (self.width(), self.height(), round(self.yaw, 6), round(self.pitch, 6),
               round(self.zoom, 6), round(self.tx, 3), round(self.ty, 3), round(self.tz, 3),
               round(self.base_dist, 3),
               round(self.exag, 6), self._mesh_version, self.show_wireframe,
               self.use_texture, len(self.textures), self._props_version,
               getattr(p, "water_level", None), self.doc.selected_prop)
        if key != self._cache_key:
            pm = self._buffer()
            pm.fill(Qt.transparent)
            cp = QPainter(pm)
            g = QLinearGradient(0, 0, 0, self.height())
            g.setColorAt(0, QColor(COLORS["sky_top"]))
            g.setColorAt(1, QColor(COLORS["sky_bottom"]))
            cp.fillRect(pm.rect(), g)
            cp.setRenderHint(QPainter.Antialiasing, True)
            cam = self._camera()
            self._paint_terrain(cp, cam)
            self._paint_water(cp, cam)
            self._paint_placed_models(cp, cam)
            cp.end()
            self._cache_key = key
        qp.drawPixmap(0, 0, self._pm)
        cam = self._camera()
        qp.setRenderHint(QPainter.Antialiasing, True)
        if self.show_cells:
            self._paint_cell_borders(qp, cam)
        self._paint_border(qp, cam)
        self._paint_spawns(qp, cam)
        self._paint_cursor(qp, cam)

    def _paint_placed_models(self, qp, cam, only=None):
        """The placed models, batched by shade so a building is a few fills, not
        a few thousand. `only` restricts the pass to a set of prop indices, so
        the water can sit between the models under it and the ones above."""
        if not self.props:
            return
        self._paint_props(qp, cam, only)

    def _paint_water(self, qp, cam):
        """The water plane, cell by cell, where the ground is below it.

        The client draws a plane at the environment's water height; a flat
        quad over the whole map would tint the hills too, so each cell whose
        ground sits under the level is filled. This is the same height the
        build writes into the water record, so the editor shows where water
        will be rather than inventing a sea level."""
        p = self.doc.project
        level = getattr(p, "water_level", None)
        if p is None or level is None or not self._quads:
            return
        qp.setPen(Qt.NoPen)
        qp.setBrush(QColor(46, 112, 150, 150))
        qp.setRenderHint(QPainter.Antialiasing, False)
        w, h = float(self.width()), float(self.height())
        depth = sorted(self._quads, key=lambda q: -sum(c[2] for c in q[:4]))
        for q in depth:
            # only a cell whose whole surface is under the level is water;
            # averaging would paint a translucent sheet up the bank it meets
            if max(c[2] for c in q[:4]) >= level:
                continue
            pts = []
            for (gx, gy, _z) in q[:4]:
                pt, _d = self._project(gx, gy, level, cam)
                pts.append(pt)
            xs = (pts[0].x(), pts[1].x(), pts[2].x(), pts[3].x())
            ys = (pts[0].y(), pts[1].y(), pts[2].y(), pts[3].y())
            if max(xs) < -2.0 or min(xs) > w + 2.0 or max(ys) < -2.0 or min(ys) > h + 2.0:
                continue
            qp.drawPolygon(QPolygonF(pts))

    def _paint_terrain(self, qp, cam):
        """The terrain.

        With a blended ground image there is nothing to project per quad: the
        whole albedo is one projective draw and its two shading images are two
        more. The quad list is only built for wireframe, for the per-tile
        fallback, and for the flat colour ramp before assets arrive."""
        textured = self.use_texture and any(self._tex_ready) and not self.show_wireframe
        if (textured and not self.show_wireframe and self._ground_image is not None
                and not self._ground_image.isNull()):
            self._paint_ground_images(qp, cam)
            return
        polys = []
        w, h = float(self.width()), float(self.height())
        for q in self._quads:
            pts, depth = [], 0.0
            for (gx, gy, z) in q[:4]:
                pt, d = self._project(gx, gy, z, cam)
                pts.append(pt)
                depth += d
            xs = (pts[0].x(), pts[1].x(), pts[2].x(), pts[3].x())
            ys = (pts[0].y(), pts[1].y(), pts[2].y(), pts[3].y())
            if max(xs) < -4.0 or min(xs) > w + 4.0 or max(ys) < -4.0 or min(ys) > h + 4.0:
                continue
            polys.append((depth, pts, q[4], q[5], q[0][0], q[0][1]))
        polys.sort(key=lambda t: -t[0])
        if self.show_wireframe:
            qp.setBrush(Qt.NoBrush)
            pen = QPen(QColor(150, 180, 150, 190), 0.7)
            qp.setPen(pen)
            for _d, pts, _col, _shade, _gx, _gy in polys:
                qp.drawPolygon(QPolygonF(pts))
            return
        if textured and self.fast_texture:
            self._paint_terrain_brush(qp, cam, polys)
            return
        for _d, pts, col, shade, gx0, gy0 in polys:
            img = self._texture(self.doc.project.tile_at(gx0, gy0)) if textured else None
            if img is not None and self._paint_textured_quad(qp, pts, img, gx0, gy0, shade):
                continue
            qp.setPen(QPen(col.darker(118), 0.5))
            qp.setBrush(col)
            qp.drawPolygon(QPolygonF(pts))

    def _paint_ground_images(self, qp, cam):
        """The blended albedo and its shading, three projective draws a frame."""
        p = self.doc.project
        lo, hi = terrain.height_range(p)
        z = (lo + hi) / 2.0
        ground = self._ground_image
        dim_x, dim_y = self._ground_dims if self._ground_dims[0] else (p.dim_x, p.dim_y)
        c0, _d = self._project(0, 0, z, cam)
        c1, _d = self._project(dim_x, 0, z, cam)
        c2, _d = self._project(dim_x, dim_y, z, cam)
        c3, _d = self._project(0, dim_y, z, cam)
        dst = QPolygonF([c0, c1, c2, c3])
        path = QPainterPath()
        path.addPolygon(dst)
        path.closeSubpath()
        self._draw_projected(qp, ground, dst, path)
        shadow = getattr(self, "_shade_shadow", None)
        light = getattr(self, "_shade_light", None)
        if shadow is not None and not shadow.isNull():
            self._draw_projected(qp, shadow, dst, path)
        if light is not None and not light.isNull():
            self._draw_projected(qp, light, dst, path)

    def _paint_terrain_brush(self, qp, cam, polys):
        """The per-tile fallback, used while a texture stroke is in progress or
        before the blended image is built."""
        p = self.doc.project
        lo, hi = terrain.height_range(p)
        z = (lo + hi) / 2.0
        width = height = 256.0
        p0, _d = self._project(0, 0, z, cam)
        pu, _d = self._project(2, 0, z, cam)
        pv, _d = self._project(0, 2, z, cam)
        m11 = (pu.x() - p0.x()) / width
        m12 = (pu.y() - p0.y()) / width
        m21 = (pv.x() - p0.x()) / height
        m22 = (pv.y() - p0.y()) / height
        t = QTransform(m11, m12, m21, m22, p0.x(), p0.y())
        brushes = []
        for img in self.textures:
            if img is None or img.isNull():
                brushes.append(None)
            else:
                b = QBrush(img)
                b.setTransform(t)
                brushes.append(b)
        for _d, pts, col, shade, gx0, gy0 in polys:
            tile = p.tile_at(gx0, gy0)
            brush = brushes[tile] if 0 <= tile < len(brushes) else None
            if brush is None:
                qp.setPen(QPen(col.darker(118), 0.5))
                qp.setBrush(col)
            else:
                qp.setPen(Qt.NoPen)
                qp.setBrush(brush)
            qp.drawPolygon(QPolygonF(pts))
            self._paint_shade(qp, pts, shade)

    @staticmethod
    def _draw_projected(qp, img, dst, clip_path):
        """Draw `img` onto the quad `dst`, perspective-correct, clipped to it."""
        src = QPolygonF([QPointF(0, 0), QPointF(img.width(), 0),
                         QPointF(img.width(), img.height()), QPointF(0, img.height())])
        t = QTransform()
        if not QTransform.quadToQuad(src, dst, t):
            return
        qp.save()
        qp.setClipPath(clip_path)
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        qp.setTransform(t, True)
        qp.drawImage(0, 0, img)
        qp.restore()

    @staticmethod
    def _paint_shade(qp, pts, shade):
        if shade < 1.0:
            qp.setPen(Qt.NoPen)
            qp.setBrush(QColor(0, 0, 0, int(min(1.0, 1.0 - shade) * 150)))
            qp.drawPolygon(QPolygonF(pts))
        elif shade > 1.0:
            qp.setPen(Qt.NoPen)
            qp.setBrush(QColor(255, 255, 255, int(min(1.0, shade - 1.0) * 90)))
            qp.drawPolygon(QPolygonF(pts))

    def _paint_props(self, qp, cam, only=None):
        """Draw the placed donor models.

        A triangle smaller than a few pixels cannot show its texture, and a
        model at map scale is made of thousands of them: they are batched into
        one path per shade step and filled once, which is what keeps three
        placed buildings interactive. A triangle that is large on screen still
        gets its own texture, so the model you are looking at stays textured."""
        p = self.doc.project
        if p is None or not self.props:
            return
        col_cache = {}
        selected_box = None
        lx, ly, lz = LIGHT
        inv_cell = 1.0 / CELL
        dim_y = p.dim_y
        # Only the model the user is looking at gets its texture: a textured
        # triangle is a whole Qt draw with its own brush transform, and at close
        # zoom three buildings turned that into two thousand of them a frame.
        focus = self.doc.selected_prop
        if not (0 <= focus < len(self.props)):
            best = 1e18
            for i, q in enumerate(self.props):
                _pt, d = self._project(p.world_to_cell(q.x, q.y)[0],
                                       p.world_to_cell(q.x, q.y)[1], q.z, cam)
                if d < best:
                    best, focus = d, i
        tex_left = TEXTURED_FRAME_BUDGET
        for prop_index, q in enumerate(self.props):
            if only is not None and prop_index not in only:
                continue
            meshes = self.prop_mesh.get(q.file_id)
            if not meshes:
                continue
            r0, r1, r2, r3, r4, r5, r6, r7, r8 = prop_matrix(q.rot)
            # the world light in the model's own space, whose z points down
            mlx = r0 * lx + r3 * ly - r6 * lz
            mly = r1 * lx + r4 * ly - r7 * lz
            mlz = r2 * lx + r5 * ly - r8 * lz
            scale = (q.scale * (255.0 / 128.0) / 256.0) + (1.0 / 128.0)
            sel = prop_index == self.doc.selected_prop
            box = None
            buckets = {}
            textured = []
            ox, oy = p.world_to_cell(q.x, q.y)
            _o, od = self._project(ox, oy, q.z, cam)
            ppu = cam[2] / max(1.0, od)          # pixels per grid unit at the prop
            radius = max(m["radius"] for m in meshes) * scale * ppu / CELL + 6.0
            if (_o.x() < -radius or _o.x() > self.width() + radius or
                    _o.y() < -radius or _o.y() > self.height() + radius):
                continue                        # off screen: do not touch its vertices
            # A model you are close to is drawn at full detail: clustering welds
            # thin walls and reads as torn geometry. Only a model small on
            # screen uses a coarse copy, where the damage is sub-pixel.
            screen_h = max((m["height"] for m in meshes), default=1.0) * scale * ppu / CELL
            if screen_h > 220.0:
                level = 0
            elif screen_h > 70.0:
                level = 1
            else:
                level = 2
            for mesh in meshes:
                lods = mesh["lods"]
                lod = lods[min(level, len(lods) - 1)]
                verts, tris, normals, uvs = lod
                img = mesh["image"]
                base = mesh["colour"]
                has_tex = img is not None and not img.isNull() and uvs is not None
                proj = []
                depth = []
                for (mx, my, mz) in verts:
                    sx, sy, sz = mx * scale, my * scale, mz * scale
                    # model space -> world: x and y are the ground, z is height
                    # and points down, so the world height is the negated one.
                    wx = q.x + r0 * sx + r1 * sy + r2 * sz
                    wy = q.y + r3 * sx + r4 * sy + r5 * sz
                    wz = q.z - (r6 * sx + r7 * sy + r8 * sz)
                    gx = wx * inv_cell - 0.5
                    gy = (dim_y - 1) - (wy * inv_cell - 0.5)
                    pt, d = self._project(gx, gy, wz, cam)
                    proj.append(pt)
                    depth.append(d)
                    if sel:
                        if box is None:
                            box = [pt.x(), pt.y(), pt.x(), pt.y()]
                        else:
                            box[0] = min(box[0], pt.x())
                            box[1] = min(box[1], pt.y())
                            box[2] = max(box[2], pt.x())
                            box[3] = max(box[3], pt.y())
                n = len(proj)
                px = [pt.x() for pt in proj]
                py = [pt.y() for pt in proj]
                w_img = img.width() if has_tex else 0
                h_img = img.height() if has_tex else 0
                ckey = (base.red(), base.green(), base.blue())
                for ti, (a, b, c) in enumerate(tris):
                    if a >= n or b >= n or c >= n:
                        continue
                    ax, ay = px[a], py[a]
                    bx, by = px[b], py[b]
                    cx, cy = px[c], py[c]
                    area = abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) * 0.5
                    nx, ny, nz = normals[ti]
                    shade = 0.55 + 0.45 * abs(nx * mlx + ny * mly + nz * mlz)
                    d = (depth[a] + depth[b] + depth[c]) * 0.333333
                    if has_tex and prop_index == focus and tex_left > 0 and area >= TEXTURED_MIN_AREA:
                        t = affine([(uvs[a][0] * w_img, uvs[a][1] * h_img),
                                    (uvs[b][0] * w_img, uvs[b][1] * h_img),
                                    (uvs[c][0] * w_img, uvs[c][1] * h_img)],
                                   [(ax, ay), (bx, by), (cx, cy)])
                        if t is not None:
                            brush = QBrush(img)
                            brush.setTransform(t)
                            textured.append((d, QPolygonF([proj[a], proj[b], proj[c]]),
                                             brush, shade))
                            tex_left -= 1
                            continue
                    level = int(shade * 10.0)
                    band = int(d / 3.0)
                    key = (band, level, ckey)
                    path = buckets.get(key)
                    if path is None:
                        path = buckets[key] = QPainterPath()
                    path.moveTo(ax, ay)
                    path.lineTo(bx, by)
                    path.lineTo(cx, cy)
                    path.closeSubpath()
            for (band, level, ckey), path in sorted(buckets.items(), key=lambda kv: -kv[0][0]):
                f = level / 10.0
                col = col_cache.get((ckey, level))
                if col is None:
                    col = QColor(min(255, int(ckey[0] * f)),
                                 min(255, int(ckey[1] * f)),
                                 min(255, int(ckey[2] * f)))
                    col_cache[(ckey, level)] = col
                qp.setRenderHint(QPainter.Antialiasing, False)
                qp.setPen(Qt.NoPen)
                qp.setBrush(col)
                qp.drawPath(path)
            if textured:
                textured.sort(key=lambda t: -t[0])
                for _d, poly, brush, _shade in textured:
                    # the polygon's own fill clips the brush, so no clip path
                    qp.setPen(Qt.NoPen)
                    qp.setBrush(brush)
                    qp.drawPolygon(poly)
            if box is not None:
                selected_box = box
        if selected_box is not None:
            qp.setPen(QPen(QColor("#ffe000"), 2))
            qp.setBrush(Qt.NoBrush)
            qp.drawRect(QRectF(selected_box[0] - 4, selected_box[1] - 4,
                               selected_box[2] - selected_box[0] + 8,
                               selected_box[3] - selected_box[1] + 8))

    def _paint_textured_quad(self, qp, pts, img, gx0, gy0, shade):
        span = self._step
        if span <= 1:
            sx = (int(gx0) % 2) * img.width() / 2.0
            sy = (int(gy0) % 2) * img.height() / 2.0
            sw, sh = img.width() / 2.0, img.height() / 2.0
        else:
            sx = sy = 0.0
            sw, sh = float(img.width()), float(img.height())
        src = QPolygonF([QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)])
        dst = QPolygonF(pts)
        t = QTransform()
        if not QTransform.quadToQuad(src, dst, t):
            return False
        path = QPainterPath()
        path.addPolygon(dst)
        path.closeSubpath()
        qp.save()
        qp.setClipPath(path)
        qp.setTransform(t)
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        qp.drawImage(QRectF(0, 0, 1, 1), img, QRectF(sx, sy, sw, sh))
        qp.restore()
        if shade < 1.0:
            qp.fillPath(path, QColor(0, 0, 0, int(min(1.0, 1.0 - shade) * 150)))
        elif shade > 1.0:
            qp.fillPath(path, QColor(255, 255, 255, int(min(1.0, shade - 1.0) * 90)))
        return True

    def _paint_cell_borders(self, qp, cam):
        p = self.doc.project
        step = max(1, int(max(p.dim_x, p.dim_y) / 160))
        qp.setRenderHint(QPainter.Antialiasing, True)
        for x in range(32, p.dim_x, 32):
            pts = []
            for y in range(0, p.dim_y, step):
                pts.append(self._project(x, y, _bilinear(p, x, y), cam)[0])
            pts.append(self._project(x, p.dim_y - 1, _bilinear(p, x, p.dim_y - 1), cam)[0])
            qp.setPen(QPen(QColor(0, 0, 0, 150), 2.4))
            qp.drawPolyline(QPolygonF(pts))
            qp.setPen(QPen(QColor(255, 230, 120, 190), 1.0))
            qp.drawPolyline(QPolygonF(pts))
        for y in range(32, p.dim_y, 32):
            pts = []
            for x in range(0, p.dim_x, step):
                pts.append(self._project(x, y, _bilinear(p, x, y), cam)[0])
            pts.append(self._project(p.dim_x - 1, y, _bilinear(p, p.dim_x - 1, y), cam)[0])
            qp.setPen(QPen(QColor(0, 0, 0, 150), 2.4))
            qp.drawPolyline(QPolygonF(pts))
            qp.setPen(QPen(QColor(255, 230, 120, 190), 1.0))
            qp.drawPolyline(QPolygonF(pts))

    def _paint_border(self, qp, cam):
        p = self.doc.project
        step = max(1, int(max(p.dim_x, p.dim_y) / 160))
        edges = []
        for x in range(0, p.dim_x, step):
            edges.append((x, 0))
        edges.append((p.dim_x - 1, 0))
        for y in range(0, p.dim_y, step):
            edges.append((p.dim_x - 1, y))
        edges.append((p.dim_x - 1, p.dim_y - 1))
        for x in range(p.dim_x - 1, -1, -step):
            edges.append((x, p.dim_y - 1))
        edges.append((0, p.dim_y - 1))
        for y in range(p.dim_y - 1, -1, -step):
            edges.append((0, y))
        pts = [self._project(x, y, _bilinear(p, x, y), cam)[0] for x, y in edges]
        qp.setPen(QPen(QColor(COLORS["marker"]), 1.6))
        qp.setBrush(Qt.NoBrush)
        qp.drawPolygon(QPolygonF(pts))

    def _spawn_points(self, i, cam):
        p = self.doc.project
        s = p.spawns[i]
        gx, gy = p.world_to_cell(s.x, s.y)
        z = p.height_at_world(s.x, s.y)
        base, _d = self._project(gx, gy, z, cam)
        top, _d = self._project(gx, gy, z + MARKER_PX, cam)
        return base, top, gx, gy, z

    def _paint_spawns(self, qp, cam):
        p = self.doc.project
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSize(max(7, self.font().pointSize()))
        qp.setFont(font)
        for i in range(len(p.spawns)):
            base, top, gx, gy, z = self._spawn_points(i, cam)
            if self._project(gx, gy, z, cam)[1] <= NEAR * 2:
                continue                    # behind the camera
            sel = i == self.doc.selected
            col = QColor(COLORS["marker"]) if i == 0 else QColor(COLORS["marker2"])
            pen = QPen(QColor("#ffffff") if sel else col.darker(150), 3 if sel else 1.5)
            qp.setPen(pen)
            qp.drawLine(base, top)
            qp.setBrush(col)
            qp.setPen(QPen(QColor("#ffffff") if sel else QColor("#101010"), 2 if sel else 1))
            qp.drawEllipse(top, 4.5 if sel else 3.5, 4.5 if sel else 3.5)
            ang = p.spawns[i].angle
            if ang is not None:
                ex, ey = gx + math.cos(ang) * 2.2, gy - math.sin(ang) * 2.2
                tip, _d = self._project(ex, ey, _bilinear(p, ex, ey) + 30, cam)
                qp.setPen(QPen(col, 2))
                qp.drawLine(top, tip)
            qp.setPen(QColor("#101010"))
            qp.drawText(QRectF(top.x() + 6, top.y() - 9, 26, 16), Qt.AlignVCenter | Qt.AlignLeft, str(i))

    def _paint_cursor(self, qp, cam):
        p = self.doc.project
        if self._mouse is None:
            return
        pick = self._pick_terrain(self._mouse)
        if pick is None:
            return
        gx, gy, z = pick
        self._pick = pick
        c, _d = self._project(gx, gy, z, cam)
        if self.tool != SCULPT:
            qp.setPen(QPen(QColor("#ffffff"), 2))
            qp.drawLine(QPointF(c.x() - 6, c.y()), QPointF(c.x() + 6, c.y()))
            qp.drawLine(QPointF(c.x(), c.y() - 6), QPointF(c.x(), c.y() + 6))
            return
        if not self.show_radius:
            return
        r = self.brush.radius
        for radius, pen in ((r, QPen(QColor("#d00000"), 2)),
                            (max(0.25, r * (1 - self.brush.falloff)),
                             QPen(QColor("#ff5050"), 1, Qt.DotLine))):
            pts = []
            n = max(24, int(radius * 12))
            for i in range(n + 1):
                a = 2 * math.pi * i / n
                x, y = gx + math.cos(a) * radius, gy + math.sin(a) * radius
                if not (0 <= x <= p.dim_x - 1 and 0 <= y <= p.dim_y - 1):
                    if pts:
                        qp.setPen(pen)
                        qp.drawPolyline(QPolygonF(pts))
                        pts = []
                    continue
                pts.append(self._project(x, y, _bilinear(p, x, y), cam)[0])
            if len(pts) > 1:
                qp.setPen(pen)
                qp.setBrush(Qt.NoBrush)
                qp.drawPolyline(QPolygonF(pts))
        qp.setPen(QPen(QColor("#ffffff"), 1))
        qp.drawLine(QPointF(c.x() - 4, c.y()), QPointF(c.x() + 4, c.y()))
        qp.drawLine(QPointF(c.x(), c.y() - 4), QPointF(c.x(), c.y() + 4))

    # ------------------------------------------------------------ editing

    def _spawn_at(self, pos) -> int:
        p = self.doc.project
        if p is None:
            return -1
        cam = self._camera()
        best, bd = -1, 1e9
        for i in range(len(p.spawns)):
            base, top, gx, gy, z = self._spawn_points(i, cam)
            if self._project(gx, gy, z, cam)[1] <= NEAR * 2:
                continue                    # behind the camera: not on screen at all
            d = min(math.hypot(base.x() - pos.x(), base.y() - pos.y()),
                    math.hypot(top.x() - pos.x(), top.y() - pos.y()))
            if d < bd:
                best, bd = i, d
        return best if bd <= 14.0 else -1

    def _prop_at(self, pos) -> int:
        """The placed model under a screen point: the first one the view ray hits.

        A ray from the eye through the mouse is intersected with each model's
        own triangles in grid space, so a tree is picked at its branches and
        nothing behind the camera can be picked. (The old test projected the
        triangles to the screen; a corner behind the eye was clamped onto the
        near plane, so a tree behind you became a screen-filling triangle with
        the smallest depth, and won.) A model hidden behind a hill is not
        picked through it: the ground hit has to be farther than the model's."""
        p = self.doc.project
        if p is None or not self.props:
            return -1
        eye, d = self._ray(pos)
        ground = self._pick_terrain(pos)
        t_ground = 1e18
        if ground is not None:
            gz = ground[2] / CELL * self.exag
            t_ground = ((ground[0] - eye[0]) * d[0] + (ground[1] - eye[1]) * d[1]
                        + (gz - eye[2]) * d[2])
        k = self.exag / CELL
        best, bt = -1, t_ground + 0.5          # a prop standing ON the ground still wins
        for qi, q in enumerate(self.props):
            meshes = self.prop_mesh.get(q.file_id)
            if not meshes:
                continue
            rot = prop_matrix(q.rot)
            scale = (q.scale * (255.0 / 128.0) / 256.0) + (1.0 / 128.0)
            # bounding sphere in grid space: skip the prop unless the ray passes it
            ox, oy = p.world_to_cell(q.x, q.y)
            oz = q.z * k
            radius = max(m["radius"] for m in meshes) * scale / CELL * max(1.0, self.exag)
            cx, cy, cz = ox - eye[0], oy - eye[1], oz - eye[2]
            along = cx * d[0] + cy * d[1] + cz * d[2]
            if along + radius < 0.0 or along - radius > bt:
                continue
            miss2 = cx * cx + cy * cy + cz * cz - along * along
            if miss2 > radius * radius:
                continue
            for mesh in meshes:
                verts, tris, _n, _uv = mesh["lods"][0]
                g = []
                for (mx, my, mz) in verts:
                    gx, gy, wz = self._prop_world(q, rot, scale, mx, my, mz)
                    g.append((gx, gy, wz * k))
                for a, b, c in tris:
                    t = _ray_triangle(eye, d, g[a], g[b], g[c])
                    if t is not None and t < bt:
                        best, bt = qi, t
        return best

    def _prop_world(self, q, rot, scale, mx, my, mz) -> tuple:
        """(gx, gy, world z) of a model-space vertex of a placed prop."""
        rx, ry, rz = prop_apply(rot, mx * scale, my * scale, mz * scale)
        gx, gy = self.doc.project.world_to_cell(q.x + rx, q.y + ry)
        return gx, gy, q.z - rz

    def _dab(self, pos):
        p = self.doc.project
        pick = self._pick_terrain(pos)
        if p is None or pick is None:
            return
        gx, gy, _z = pick
        rect = self.brush.apply(p, gx, gy)
        self._last_dab = pos
        self._pick = pick
        self.doc.heights_changed.emit(rect)
        self.view_changed.emit()

    def _texture_dab(self, pos):
        p = self.doc.project
        pick = self._pick_terrain(pos)
        if p is None or pick is None or not p.tiles:
            return
        if not self.doc.tile_types:
            # without the donor's tile-type table a stroke cannot be checked
            # against the client's three-layer limit, so it is not allowed
            self.doc.status.emit("Texture painting waits for the donor's texture table (Assets).")
            return
        gx, gy, _z = pick
        r = self.brush.radius
        x0, x1 = max(0, int(gx - r)), min(p.dim_x - 1, int(gx + r) + 1)
        y0, y1 = max(0, int(gy - r)), min(p.dim_y - 1, int(gy + r) + 1)
        points = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
                  if self.brush.weight(math.hypot(x - gx, y - gy)) > 0]
        # the new texture wins at junctions instead of the dab being refused,
        # and no cell ever needs a fourth layer (texlayers.paint)
        changed = texlayers.paint(p.tiles, p.dim_x, p.dim_y, self.doc.tile_types,
                                  points, self.paint_tile)
        hit = bool(changed)
        if hit:
            x0 = min(x0, min(x for x, _y in changed))
            y0 = min(y0, min(y for _x, y in changed))
            x1 = max(x1, max(x for x, _y in changed))
            y1 = max(y1, max(y for _x, y in changed))
        if hit:
            self.doc.textures_changed.emit((x0, y0, x1, y1))
        self.update()

    def _report(self, pos):
        p = self.doc.project
        if p is None:
            return
        pick = self._pick_terrain(pos)
        self._pick = pick
        if pick is None:
            self.cursor_info.emit("")
            return
        gx, gy, z = pick
        x, y = self._world_at(gx, gy)
        slope = ""
        if self.doc.slopes:
            qx = min(max(int(round(gx)), 0), len(self.doc.slopes[0]) - 1)
            qy = min(max(int(round(gy)), 0), len(self.doc.slopes) - 1)
            slope = f"   slope {self.doc.slopes[qy][qx]:.1f} deg"
        self.cursor_info.emit(f"x {x:,.0f}   y {y:,.0f}   z {z:,.0f}{slope}   "
                              f"cell {int(gx + 0.5)},{int(gy + 0.5)}")

    # ------------------------------------------------------------ input

    def mousePressEvent(self, ev):
        p = self.doc.project
        if p is None:
            return
        self.setFocus()
        pos = ev.position()
        self._mouse = pos
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.LeftButton and self._space):
            if ev.modifiers() & (Qt.ShiftModifier | Qt.AltModifier):
                self._drag = ("orbit", pos, self.yaw, self.pitch,
                              (self.tx, self.ty, self.tz), self._pick_terrain(pos))
            else:
                self._drag = ("pan", pos, self.yaw, self.pitch,
                              (self.tx, self.ty, self.tz), None)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if ev.button() == Qt.LeftButton and ev.modifiers() & Qt.ShiftModifier:
            self._drag = ("orbit", pos, self.yaw, self.pitch,
                          (self.tx, self.ty, self.tz), self._pick_terrain(pos))
            self.setCursor(Qt.ClosedHandCursor)
            return
        # Placed models are only selected, moved and turned in Select mode, so a
        # sculpt or texture stroke that starts on a building edits the ground.
        if self.tool == SELECT and ev.button() == Qt.LeftButton:
            hit = self._prop_at(pos)
            if hit >= 0:
                self.doc.select_prop(hit)
                self._prop_drag = (hit, copy_props(p))
                self.setCursor(Qt.SizeAllCursor)
                return
        if self.tool == SELECT and ev.button() == Qt.RightButton:
            # The CS rotated a selected object with a right drag (its default
            # axis is Z), so an object under the cursor takes the button before
            # landscape mode does.
            hit = self._prop_at(pos)
            if hit >= 0:
                self.doc.select_prop(hit)
                self._prop_rot = (hit, pos, tuple(p.props[hit].rot), copy_props(p))
                self.setCursor(Qt.SizeHorCursor)
                return
        if self.tool == SCULPT and ev.button() in (Qt.LeftButton, Qt.RightButton):
            pick = self._pick_terrain(pos)
            if pick is None:
                return
            if ev.button() == Qt.RightButton:
                self._tile_before = copy_tiles(p)
                self._texture_dab(pos)
                return
            self._stroke_before = copy_heights(p)
            self.brush.begin(p, pick[0], pick[1])
            self._last_dab = None
            self._dab(pos)
            return
        if self.tool in (SPAWN, SELECT) and ev.button() == Qt.LeftButton:
            hit = self._spawn_at(pos)
            if hit >= 0:
                self.doc.select(hit)
                self._drag_spawn = (hit, bool(ev.modifiers() & Qt.AltModifier))
                self._spawns_before = copy_spawns(p)
            elif self.tool == SPAWN:
                pick = self._pick_terrain(pos)
                if pick is not None:
                    x, y = self._world_at(pick[0], pick[1])
                    before = copy_spawns(p)
                    after = copy_spawns(p)
                    after.append(Spawn(x, y, None))
                    self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Add spawn"))
                    self.doc.select(len(after) - 1)
            else:
                self.doc.select(-1)
                if self.tool == SELECT:
                    self.doc.select_prop(-1)
            return
        if self.tool == SPAWN and ev.button() == Qt.RightButton:
            hit = self._spawn_at(pos)
            if hit >= 0:
                before = copy_spawns(p)
                after = [s for i, s in enumerate(before) if i != hit]
                self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Delete spawn"))
                self.doc.select(-1)
            return

    def mouseMoveEvent(self, ev):
        p = self.doc.project
        pos = ev.position()
        self._mouse = pos
        if self._drag is not None and not (ev.buttons() & (Qt.MiddleButton | Qt.LeftButton)):
            # A release that happened while the window was not focused (Alt-Tab,
            # a dialog) never reached mouseReleaseEvent; without this the view
            # would keep panning on every later hover.
            self._drag = None
            self.setCursor(Qt.CrossCursor if self.tool != SELECT else Qt.ArrowCursor)
        if p is None:
            return
        if not ev.buttons() and self._drag is None:
            if ev.modifiers() & Qt.ShiftModifier:
                if self._look_last is not None:
                    d = pos - self._look_last
                    self._look(d.x(), d.y())
                    self.view_changed.emit()
                    self.user_moved.emit()
                    self.update()
                self._look_last = pos
                return
            self._look_last = None
            if self._space:
                if self._pan_last is not None:
                    d = pos - self._pan_last
                    self._pan_pixels(d.x(), d.y())
                    self.view_changed.emit()
                    self.user_moved.emit()
                    self.update()
                self._pan_last = pos
                return
            self._pan_last = None
        if self._drag is not None:
            mode, start, yaw, pitch, target, anchor = self._drag
            d = pos - start
            if mode == "orbit":
                self.yaw = yaw + d.x() * 0.01
                self.pitch = min(math.radians(88), max(math.radians(8), pitch + d.y() * 0.01))
                self.tx, self.ty, self.tz = target
                self._pin(start, anchor)
                self._keep_above_ground()
            else:
                self.tx, self.ty, self.tz = target
                self._pan_pixels(d.x(), d.y())
            self._report(pos)
            self.view_changed.emit()
            self.user_moved.emit()
            self.update()
            return
        if self._prop_rot is not None and ev.buttons() & Qt.RightButton:
            idx, start, rot0, _before = self._prop_rot
            if 0 <= idx < len(p.props):
                q = p.props[idx]
                step = int(round((pos.x() - start.x()) * 0.36))
                q.rot = (rot0[0], rot0[1], (rot0[2] + step) % 256)
                self._invalidate_cache()
                self.update()
            return
        if self._prop_drag is not None and ev.buttons() & Qt.LeftButton:
            idx, _before = self._prop_drag
            pick = self._pick_terrain(pos)
            if pick is not None and 0 <= idx < len(p.props):
                q = p.props[idx]
                x, y = self._world_at(pick[0], pick[1])
                if q.grid_locked:
                    q.x, q.y, q.z = p.snap_world(x, y)
                else:
                    wx, wy = p.world_size()
                    q.x = min(max(x, 0.0), wx - 1)
                    q.y = min(max(y, 0.0), wy - 1)
                    q.z = float(pick[2])
                self._invalidate_cache()
                self._report(pos)
                self.update()
            return
        if self._tile_before is not None and ev.buttons() & Qt.RightButton:
            step = max(2.0, self.brush.radius * self._camera()[2] * 0.25)
            if self._last_dab is None or math.hypot(pos.x() - self._last_dab.x(),
                                                    pos.y() - self._last_dab.y()) >= step:
                self._texture_dab(pos)
            self._report(pos)
            self.update()
            return
        if self._stroke_before is not None and ev.buttons() & Qt.LeftButton:
            step = max(2.0, self.brush.radius * self._camera()[2] * 0.25)
            if self._last_dab is None or math.hypot(pos.x() - self._last_dab.x(),
                                                    pos.y() - self._last_dab.y()) >= step:
                self._dab(pos)
            self._report(pos)
            self.update()
            return
        if self._drag_spawn is not None and ev.buttons() & Qt.LeftButton:
            idx, turning = self._drag_spawn
            pick = self._pick_terrain(pos)
            if pick is not None:
                s = p.spawns[idx]
                x, y = self._world_at(pick[0], pick[1])
                if turning or ev.modifiers() & Qt.AltModifier:
                    s.angle = math.atan2(y - s.y, x - s.x)
                else:
                    wx, wy = p.world_size()
                    s.x = min(max(x, 0.0), wx - 1)
                    s.y = min(max(y, 0.0), wy - 1)
                self.doc.spawns_changed.emit()
            self._report(pos)
            self.update()
            return
        self._report(pos)
        self.update()

    def mouseReleaseEvent(self, ev):
        p = self.doc.project
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CrossCursor if self.tool != SELECT else Qt.ArrowCursor)
            return
        if self._prop_rot is not None and p is not None:
            _idx, _start, _rot0, before = self._prop_rot
            self._prop_rot = None
            self.setCursor(Qt.CrossCursor if self.tool != SELECT else Qt.ArrowCursor)
            after = copy_props(p)
            if any(a.rot != b.rot for a, b in zip(after, before)):
                p.props = before
                self.doc.undo.push(PropsCommand(self.doc, before, after, "Rotate prop"))
            return
        if self._prop_drag is not None and p is not None:
            idx, before = self._prop_drag
            self._prop_drag = None
            self.setCursor(Qt.CrossCursor if self.tool != SELECT else Qt.ArrowCursor)
            after = copy_props(p)
            moved = any((a.x, a.y, a.z) != (b.x, b.y, b.z)
                        for a, b in zip(after, before))
            if moved:
                p.props = before
                self.doc.undo.push(PropsCommand(self.doc, before, after, "Move prop"))
            return
        if self._tile_before is not None and p is not None:
            after = copy_tiles(p)
            before = self._tile_before
            self._tile_before = None
            self._last_dab = None
            if after != before:
                p.tiles = before
                self.doc.undo.push(TilesCommand(self.doc, before, after, "Paint texture"))
            return
        if self._stroke_before is not None and p is not None:
            after = copy_heights(p)
            before = self._stroke_before
            self._stroke_before = None
            self._last_dab = None
            if after != before:
                # the stroke already happened live, and HeightsCommand skips its
                # first redo for exactly that reason - so do NOT restore `before`
                # here or the stroke is thrown away
                self.doc.undo.push(HeightsCommand(self.doc, before, after,
                                                  f"Sculpt ({self.brush.tool})"))
            return
        if self._drag_spawn is not None and p is not None:
            after = copy_spawns(p)
            before = self._spawns_before
            self._drag_spawn = None
            if any((a.x, a.y, a.angle) != (b.x, b.y, b.angle) for a, b in zip(after, before)):
                p.spawns = before
                self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Move spawn"))

    def mouseDoubleClickEvent(self, ev):
        if self.doc.project is None or ev.button() != Qt.LeftButton or self.tool != SELECT:
            return
        hit = self._prop_at(ev.position())
        if hit >= 0:
            self.doc.select_prop(hit)
            self.prop_activated.emit(hit)

    def wheelEvent(self, ev):
        if self.doc.project is None:
            return
        delta = ev.angleDelta()
        # some platforms turn Shift/Alt+wheel into a horizontal scroll
        d = (delta.y() or delta.x()) / 120.0
        pos = ev.position()
        if ev.modifiers() & Qt.ControlModifier:
            self.brush.radius = max(0.5, min(64.0, self.brush.radius * (1.15 ** d)))
            self.update()
            self.view_changed.emit()
            return
        # Shift+wheel zooms too (it used to scale the heights).
        self._dolly(pos, d)
        self.update()
        self.view_changed.emit()
        self.user_moved.emit()

    def _dolly(self, pos, notches: float):
        """Move the camera toward (or away from) the point under the mouse.

        Each notch covers a fixed fraction of the remaining distance to that
        point, so the zoom slows down as you close in and never passes through
        it. The old wheel changed the distance to the camera's hidden orbit
        target instead, which after a pan or an orbit could sit in the air or
        behind the ground you were looking at: zooming then stalled, overshot,
        or got pushed back up by the ground clamp. The view direction is kept;
        the orbit target is re-seated on the new depth of the point."""
        p = self.doc.project
        cam = self._camera()
        eye, fwd = cam[3], cam[6]
        hit = self._pick_terrain(pos)
        if hit is not None:
            ax, ay, az = hit[0], hit[1], hit[2] / CELL * self.exag
        else:
            # over the sky: zoom along the ray under the mouse
            _e, ray = self._ray(pos)
            d0 = self._dist()
            ax, ay, az = eye[0] + ray[0] * d0, eye[1] + ray[1] * d0, eye[2] + ray[2] * d0
        vx, vy, vz = ax - eye[0], ay - eye[1], az - eye[2]
        dist = math.sqrt(vx * vx + vy * vy + vz * vz)
        if dist < 1e-6:
            return
        near, far = 0.5, max(p.dim_x, p.dim_y) * 6.0
        new = min(far, max(near, dist / (1.18 ** notches)))
        k = 1.0 - new / dist                     # fraction of the way to travel
        ex, ey, ez = eye[0] + vx * k, eye[1] + vy * k, eye[2] + vz * k
        depth = max(0.05, (ax - ex) * fwd[0] + (ay - ey) * fwd[1] + (az - ez) * fwd[2])
        self.tx = ex + fwd[0] * depth
        self.ty = ey + fwd[1] * depth
        self.tz = (ez + fwd[2] * depth) * CELL / self.exag
        self.zoom = self.base_dist / depth
        self._keep_above_ground()

    def keyPressEvent(self, ev):
        arrows = {Qt.Key_Up: (1, 0), Qt.Key_Down: (-1, 0), Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1)}
        if ev.key() in arrows and self.doc.project is not None:
            f, side = arrows[ev.key()]
            if ev.modifiers() & Qt.ShiftModifier and side == 0:
                self._step_view(0, 0, f)
            else:
                self._step_view(f, side, 0)
            self.view_changed.emit()
            self.user_moved.emit()
            self.update()
            return
        if ev.key() == Qt.Key_Space:
            if not ev.isAutoRepeat():
                self._space = True
                self._pan_last = None
                self.setCursor(Qt.OpenHandCursor)
        elif (ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.tool == SELECT
              and self.doc.selected_prop >= 0):
            p = self.doc.project
            i = self.doc.selected_prop
            if p is not None and 0 <= i < len(p.props):
                before = copy_props(p)
                after = [q for k, q in enumerate(before) if k != i]
                self.doc.undo.push(PropsCommand(self.doc, before, after, "Delete prop"))
                self.doc.select_prop(-1)
        elif ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.doc.selected >= 0:
            p = self.doc.project
            before = copy_spawns(p)
            after = [s for i, s in enumerate(before) if i != self.doc.selected]
            self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Delete spawn"))
            self.doc.select(-1)
        else:
            super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key_Shift:
            self._look_last = None
        if ev.key() == Qt.Key_Space and not ev.isAutoRepeat():
            self._space = False
            self._pan_last = None
            self.setCursor(Qt.CrossCursor if self.tool != SELECT else Qt.ArrowCursor)
        super().keyReleaseEvent(ev)

    def leaveEvent(self, _ev):
        self._mouse = None
        self._pick = None
        self.update()

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat("application/x-kryta-model"):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasFormat("application/x-kryta-model"):
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        md = ev.mimeData()
        if not md.hasFormat("application/x-kryta-model"):
            return
        try:
            data = json.loads(bytes(md.data("application/x-kryta-model")).decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return
        pick = self._pick_terrain(ev.position())
        if pick is None:
            return
        x, y = self._world_at(pick[0], pick[1])
        self.model_dropped.emit({"file_id": int(data["file_id"]),
                                 "model": int(data.get("model", 0)),
                                 "x": x, "y": y, "z": pick[2]})
        ev.acceptProposedAction()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._invalidate_cache()
        if self._needs_fit and self.width() > 40 and self.height() > 40:
            self.fit()
        self.view_changed.emit()



class SoftTerrain3DView(_View3DCore, QWidget):
    """The original QPainter renderer: a fallback when OpenGL cannot start."""
    cursor_info = Signal(str)
    view_changed = Signal()
    user_moved = Signal()
    tool_requested = Signal(str)
    model_dropped = Signal(object)
    prop_activated = Signal(int)

    def __init__(self, doc, brush, parent=None):
        QWidget.__init__(self, parent)
        self._init_core(doc, brush)

    def paintEvent(self, _ev):
        qp = QPainter(self)
        self._soft_paint(qp)
        qp.end()


def Terrain3DView(doc, brush, parent=None):
    """The Render Window's 3D view: the OpenGL renderer, depth-buffered, unless
    PySide6 has no OpenGL support or KRYTA_CS_SOFTWARE_3D=1 asks for the old one."""
    if not os.environ.get("KRYTA_CS_SOFTWARE_3D"):
        try:
            from .view3d_gl import GLTerrain3DView
        except ImportError as exc:                                   # noqa: F841
            pass
        else:
            return GLTerrain3DView(doc, brush, parent)
    return SoftTerrain3DView(doc, brush, parent)
