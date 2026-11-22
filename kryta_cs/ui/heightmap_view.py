"""The top view: a shaded relief of the height field you sculpt and place spawns on.

Left drag sculpts with the current brush; right drag paints the donor texture selected
in the Object Window, as the Construction Set's landscape mode does. In Select mode
spawn points and props are objects: drag moves, Alt+drag turns a spawn; the wheel zooms, middle drag or
Space+drag pans, Ctrl+wheel sizes the brush. Overlays show what the client's mesh
compiler will make of the ground: orange where a slope sits in the 'only beside flat
ground' band, red where it is too steep, and a tint over what the flood fill reaches.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import QWidget

from .. import terrain, texlayers
from ..model import CELL
from .document import (HeightsCommand, PropsCommand, SpawnsCommand, TilesCommand, copy_heights,
                       copy_props, copy_spawns, copy_tiles)
from .theme import COLORS

# hypsometric ramp: (t, r, g, b)
RAMP = [(0.00, 38, 70, 52), (0.18, 62, 104, 62), (0.38, 112, 138, 78), (0.58, 160, 150, 100),
        (0.78, 140, 112, 82), (0.92, 190, 180, 170), (1.00, 238, 238, 240)]


def ramp(t: float) -> tuple:
    if t <= 0:
        return RAMP[0][1:]
    for (t0, r0, g0, b0), (t1, r1, g1, b1) in zip(RAMP, RAMP[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return (r0 + (r1 - r0) * f, g0 + (g1 - g0) * f, b0 + (b1 - b0) * f)
    return RAMP[-1][1:]


SELECT, SCULPT, SPAWN = "select", "sculpt", "spawn"


class HeightmapView(QWidget):
    cursor_info = Signal(str)
    view_changed = Signal()
    user_moved = Signal()

    def __init__(self, doc, brush, parent=None):
        super().__init__(parent)
        self.doc, self.brush = doc, brush
        self.tool = SCULPT
        self.show_slopes = True
        self.show_reach = True
        self.show_grid = True
        self.show_contours = False
        self.scale = 8.0
        self.off = QPointF(20, 20)
        self._img = None
        self._buf = None
        self._range = (0.0, 1.0)
        self._overlay = None
        self._mouse = None
        self.textures = []
        self._tex_colors = []
        self._ground_colors = []
        self.paint_tile = 0
        self.show_radius = True
        self._stroke_before = None
        self._tile_before = None
        self._last_dab = None
        self._pan_from = None
        self._space = False
        self._drag_spawn = None
        self._prop_drag = None
        self._spawns_before = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(300, 300)
        doc.project_changed.connect(self._on_project)
        doc.heights_changed.connect(self._on_heights)
        doc.analysis_changed.connect(self._on_analysis)
        doc.textures_changed.connect(self._on_textures)
        doc.spawns_changed.connect(self.update)
        doc.props_changed.connect(self.update)
        doc.prop_selection_changed.connect(lambda _i: self.update())
        doc.selection_changed.connect(lambda _i: self.update())

    # ------------------------------------------------------------ images

    def _on_project(self):
        self._rebuild_all()
        self.fit()

    def _on_heights(self, rect):
        if rect is None:
            self._rebuild_all()
        else:
            self._rebuild(rect)
        self.update()

    def _on_analysis(self):
        self._build_overlay()
        self.update()

    def _on_textures(self, rect=None):
        if rect is None:
            self._rebuild_all()
        else:
            self._rebuild(rect)
        self.update()

    def set_textures(self, images):
        """The donor's texture table; each cell shows one 128px quadrant of it."""
        self.textures = list(images)
        self._tex_colors = []
        for img in self.textures:
            if img is None or img.isNull():
                self._tex_colors.append(None)
                continue
            w, h = img.width(), img.height()
            quads = []
            for qy in (0, 1):
                for qx in (0, 1):
                    r = g = b = n = 0
                    for sy in range(6, max(7, h // 2), 24):
                        for sx in range(6, max(7, w // 2), 24):
                            c = img.pixelColor(qx * w // 2 + sx, qy * h // 2 + sy)
                            if c.alpha() > 32:
                                r += c.red()
                                g += c.green()
                                b += c.blue()
                                n += 1
                    quads.append((min(255, r // n + 28), min(255, g // n + 28),
                                  min(255, b // n + 28)) if n else (120, 110, 84))
            self._tex_colors.append(quads)
        self._rebuild_all()
        self.update()

    def set_ground_colors(self, colors, rect=None):
        """The client's blended base+overlay colour per cell, or None."""
        self._ground_colors = list(colors) if colors else []
        if rect is not None and self._buf is not None:
            self._rebuild(rect, check_range=False)
        else:
            self._rebuild_all()
        self.update()

    def _rebuild_all(self):
        p = self.doc.project
        if p is None:
            self._img = None
            return
        lo, hi = terrain.height_range(p)
        if hi - lo < 1:
            hi = lo + 1
        self._range = (lo, hi)
        self._buf = bytearray(p.dim_x * p.dim_y * 4)
        self._rebuild((0, 0, p.dim_x - 1, p.dim_y - 1), check_range=False)
        self._build_overlay()

    def _rebuild(self, rect, check_range=True):
        p = self.doc.project
        if p is None or self._buf is None:
            return
        x0, y0, x1, y1 = rect
        h = p.heights
        lo, hi = self._range
        if check_range:
            for y in range(max(0, y0), min(p.dim_y, y1 + 1)):
                row = h[y][max(0, x0):x1 + 1]
                if min(row) < lo or max(row) > hi:
                    lo2, hi2 = terrain.height_range(p)
                    span = hi2 - lo2
                    self._range = (lo2 - 0.1 * span, hi2 + 0.1 * span)
                    return self._rebuild((0, 0, p.dim_x - 1, p.dim_y - 1), check_range=False)
        span = hi - lo
        w, ht = p.dim_x, p.dim_y
        lx, ly, lz = -0.5, -0.5, 0.7071                   # light from the north-west, 45 degrees up
        buf = self._buf
        for y in range(max(0, y0 - 1), min(ht, y1 + 2)):
            row = h[y]
            up = h[y - 1] if y > 0 else row
            dn = h[y + 1] if y < ht - 1 else row
            base = y * w * 4
            for x in range(max(0, x0 - 1), min(w, x1 + 2)):
                v = row[x]
                dzdx = ((row[x + 1] if x < w - 1 else v) - (row[x - 1] if x > 0 else v)) / (2 * CELL)
                dzdy = (dn[x] - up[x]) / (2 * CELL)
                nx, ny, nz = -dzdx, -dzdy, 1.0
                n = math.sqrt(nx * nx + ny * ny + 1.0)
                shade = max(0.0, (nx * lx + ny * ly + nz * lz) / n)
                shade = 0.35 + 0.85 * shade
                r, g, b = ramp((v - lo) / span)
                if self._ground_colors and len(self._ground_colors) == w * ht:
                    r, g, b = self._ground_colors[y * w + x]
                elif self._tex_colors and p.tiles:
                    tile = p.tiles[y * w + x]
                    if 0 <= tile < len(self._tex_colors) and self._tex_colors[tile]:
                        r, g, b = self._tex_colors[tile][(y % 2) * 2 + (x % 2)]
                i = base + x * 4
                buf[i] = min(255, int(b * shade))
                buf[i + 1] = min(255, int(g * shade))
                buf[i + 2] = min(255, int(r * shade))
                buf[i + 3] = 255
        self._img_bytes = bytes(buf)
        self._img = QImage(self._img_bytes, w, ht, w * 4, QImage.Format_RGB32)

    def _build_overlay(self):
        p, cl = self.doc.project, self.doc.classes
        if p is None or cl is None:
            self._overlay = None
            return
        qw, qh = p.dim_x - 1, p.dim_y - 1
        buf = bytearray(qw * qh * 4)
        reach = self.doc.reach if self.show_reach else None
        for y in range(qh):
            row = cl[y]
            for x in range(qw):
                c = row[x]
                i = (y * qw + x) * 4
                if self.show_slopes and c == terrain.CLASS_STEEP:
                    buf[i:i + 4] = bytes((60, 60, 220, 120))
                elif self.show_slopes and c == terrain.CLASS_RISKY:
                    buf[i:i + 4] = bytes((40, 150, 240, 130))
                elif reach is not None and reach and (x, y) not in reach:
                    buf[i:i + 4] = bytes((40, 20, 20, 90))
        self._ov_bytes = bytes(buf)
        self._overlay = QImage(self._ov_bytes, qw, qh, qw * 4, QImage.Format_ARGB32)

    # ------------------------------------------------------------ geometry

    def fit(self):
        p = self.doc.project
        if p is None:
            return
        m = 24
        s = min((self.width() - 2 * m) / p.dim_x, (self.height() - 2 * m) / p.dim_y)
        self.scale = max(1.0, s)
        self.off = QPointF((self.width() - p.dim_x * self.scale) / 2, (self.height() - p.dim_y * self.scale) / 2)
        self.update()
        self.view_changed.emit()

    def center_on_grid(self, gx: float, gy: float):
        p = self.doc.project
        if p is None:
            return
        target = QPointF(self.off.x() + (gx + 0.5) * self.scale,
                         self.off.y() + (gy + 0.5) * self.scale)
        c = self.rect().center()
        self.off += QPointF(c.x() - target.x(), c.y() - target.y())
        self.update()
        self.view_changed.emit()
        self.user_moved.emit()

    def center_on_world(self, x: float, y: float):
        p = self.doc.project
        if p is None:
            return
        gx, gy = p.world_to_cell(x, y)
        self.center_on_grid(gx, gy)

    def focus_grid(self) -> tuple:
        p = self.doc.project
        if p is None:
            return 0.0, 0.0
        c = self.rect().center()
        gx, gy = self.to_grid(c)
        return gx, gy

    def visible_grid_rect(self):
        p = self.doc.project
        if p is None:
            return None
        gx0, gy0 = self.to_grid(QPointF(0, 0))
        gx1, gy1 = self.to_grid(QPointF(self.width(), self.height()))
        return gx0, gy0, gx1, gy1

    def zoom_text(self) -> str:
        return f"{self.scale / 8.0 * 100:.0f}%"

    def camera_text(self) -> str:
        p = self.doc.project
        if p is None:
            return ""
        c = self.rect().center()
        x, y = self.to_world(c)
        z = p.height_at_world(x, y)
        gx, gy = self.to_grid(c)
        return (f"Camera {x:,.0f}, {y:,.0f}, {z:,.0f}   "
                f"cell {int(gx + 0.5)},{int(gy + 0.5)}")

    def thumbnail(self):
        return self._img

    def to_grid(self, pos) -> tuple:
        return (pos.x() - self.off.x()) / self.scale - 0.5, (pos.y() - self.off.y()) / self.scale - 0.5

    def to_world(self, pos) -> tuple:
        p = self.doc.project
        gx = (pos.x() - self.off.x()) / self.scale
        gy = (pos.y() - self.off.y()) / self.scale
        return gx * CELL, (p.dim_y - gy) * CELL

    def from_world(self, x, y) -> QPointF:
        p = self.doc.project
        return QPointF(self.off.x() + x / CELL * self.scale, self.off.y() + (p.dim_y - y / CELL) * self.scale)

    # ------------------------------------------------------------ painting

    def paintEvent(self, _ev):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(COLORS["viewport"]))
        p = self.doc.project
        if p is None or self._img is None:
            qp.setPen(QColor(COLORS["vtext"]))
            qp.drawText(self.rect(), Qt.AlignCenter, "File > New map... to start")
            return
        target = QRectF(self.off.x(), self.off.y(), p.dim_x * self.scale, p.dim_y * self.scale)
        qp.setRenderHint(QPainter.SmoothPixmapTransform, self.scale < 4)
        qp.drawImage(target, self._img)
        if self._overlay is not None and (self.show_slopes or self.show_reach):
            ot = QRectF(self.off.x() + self.scale / 2, self.off.y() + self.scale / 2,
                        (p.dim_x - 1) * self.scale, (p.dim_y - 1) * self.scale)
            qp.setRenderHint(QPainter.SmoothPixmapTransform, False)
            qp.drawImage(ot, self._overlay)
        qp.setRenderHint(QPainter.Antialiasing, True)
        if self.show_contours:
            self._paint_contours(qp)
        if self.show_grid and self.scale >= 6:
            pen = QPen(QColor(255, 255, 255, 22))
            qp.setPen(pen)
            for i in range(0, p.dim_x + 1):
                x = self.off.x() + i * self.scale
                qp.drawLine(QPointF(x, target.top()), QPointF(x, target.bottom()))
            for j in range(0, p.dim_y + 1):
                y = self.off.y() + j * self.scale
                qp.drawLine(QPointF(target.left(), y), QPointF(target.right(), y))
        # 32-cell tile lines: the codec's own tiling, and a handy ruler (3,072 units)
        qp.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.DashLine))
        for i in range(32, p.dim_x, 32):
            x = self.off.x() + i * self.scale
            qp.drawLine(QPointF(x, target.top()), QPointF(x, target.bottom()))
        for j in range(32, p.dim_y, 32):
            y = self.off.y() + j * self.scale
            qp.drawLine(QPointF(target.left(), y), QPointF(target.right(), y))
        qp.setPen(QPen(QColor(COLORS["marker"]), 1.5))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(target)
        self._paint_spawns(qp)
        self._paint_props(qp)
        self._paint_cursor(qp)

    def _paint_props(self, qp):
        p = self.doc.project
        if p is None or not p.props:
            return
        r = max(3.0, min(8.0, self.scale * 0.6))
        for i, q in enumerate(p.props):
            c = self.from_world(q.x, q.y)
            sel = i == self.doc.selected_prop
            if sel:
                qp.setPen(QPen(QColor("#ffe000"), 2))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(QRectF(c.x() - r - 3, c.y() - r - 3, 2 * r + 6, 2 * r + 6))
            qp.setPen(QPen(QColor("#000000"), 1))
            qp.setBrush(QColor("#b08040"))
            qp.drawRect(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
            qp.setPen(QPen(QColor("#ffe0a0"), 1))
            qp.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
            qp.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))

    def _paint_contours(self, qp):
        p = self.doc.project
        lo, hi = self._range
        step = 96.0
        qp.setPen(QPen(QColor(0, 0, 0, 70), 1))
        h = p.heights
        for y in range(p.dim_y - 1):
            for x in range(p.dim_x - 1):
                a, b = h[y][x], h[y][x + 1]
                if math.floor(a / step) != math.floor(b / step):
                    px = self.off.x() + (x + 1) * self.scale
                    py = self.off.y() + (y + 0.5) * self.scale
                    qp.drawLine(QPointF(px, py - self.scale / 2), QPointF(px, py + self.scale / 2))
                c = h[y + 1][x]
                if math.floor(a / step) != math.floor(c / step):
                    px = self.off.x() + (x + 0.5) * self.scale
                    py = self.off.y() + (y + 1) * self.scale
                    qp.drawLine(QPointF(px - self.scale / 2, py), QPointF(px + self.scale / 2, py))

    def _paint_spawns(self, qp):
        p = self.doc.project
        r = max(6.0, min(14.0, self.scale * 1.2))
        font = QFont(self.font())
        font.setBold(True)
        qp.setFont(font)
        for i, s in enumerate(p.spawns):
            c = self.from_world(s.x, s.y)
            sel = i == self.doc.selected
            fill = QColor(COLORS["marker"]) if i == 0 else QColor(COLORS["marker2"])
            qp.setPen(QPen(QColor("#ffffff") if sel else QColor("#101010"), 2.5 if sel else 1.5))
            qp.setBrush(fill)
            qp.drawEllipse(c, r, r)
            ang = s.angle
            if ang is not None:
                # world facing: 0 = +x, counter-clockwise; screen y is flipped
                dx, dy = math.cos(ang), -math.sin(ang)
                tip = QPointF(c.x() + dx * r * 2.2, c.y() + dy * r * 2.2)
                qp.setPen(QPen(fill, 3))
                qp.drawLine(c, tip)
            qp.setPen(QColor("#101010"))
            qp.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter, str(i))
        if p.seed is not None:
            c = self.from_world(*p.seed)
            qp.setPen(QPen(QColor(COLORS["danger"]), 2))
            qp.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))
            qp.drawLine(QPointF(c.x(), c.y() - 8), QPointF(c.x(), c.y() + 8))

    def _paint_cursor(self, qp):
        if self._mouse is None or self.tool != SCULPT or not self.show_radius:
            return
        r = self.brush.radius * self.scale
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(QColor("#d00000"), 2))
        qp.drawEllipse(self._mouse, r, r)
        inner = r * (1 - self.brush.falloff)
        qp.setPen(QPen(QColor("#ff5050"), 1, Qt.DotLine))
        qp.drawEllipse(self._mouse, inner, inner)
        qp.setPen(QPen(QColor("#ffffff"), 1))
        qp.drawLine(QPointF(self._mouse.x() - 4, self._mouse.y()),
                    QPointF(self._mouse.x() + 4, self._mouse.y()))
        qp.drawLine(QPointF(self._mouse.x(), self._mouse.y() - 4),
                    QPointF(self._mouse.x(), self._mouse.y() + 4))

    # ------------------------------------------------------------ input

    def wheelEvent(self, ev):
        if self.doc.project is None:
            return
        d = ev.angleDelta().y() / 120.0
        if ev.modifiers() & Qt.ControlModifier:
            self.brush.radius = max(0.5, min(64.0, self.brush.radius * (1.15 ** d)))
            self.update()
            self.view_changed.emit()
            return
        pos = ev.position()
        f = 1.2 ** d
        new = max(0.5, min(80.0, self.scale * f))
        f = new / self.scale
        self.off = QPointF(pos.x() - (pos.x() - self.off.x()) * f, pos.y() - (pos.y() - self.off.y()) * f)
        self.scale = new
        self.update()
        self.view_changed.emit()
        self.user_moved.emit()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self._space = True
        elif ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.doc.selected >= 0:
            p = self.doc.project
            before = copy_spawns(p)
            after = [s for i, s in enumerate(before) if i != self.doc.selected]
            self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Delete spawn"))
            self.doc.select(-1)
        elif ev.key() == Qt.Key_F:
            self.fit()
        else:
            super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key_Space:
            self._space = False
        super().keyReleaseEvent(ev)

    def _spawn_at(self, pos):
        p = self.doc.project
        best, bd = -1, 1e9
        for i, s in enumerate(p.spawns):
            c = self.from_world(s.x, s.y)
            d = math.hypot(c.x() - pos.x(), c.y() - pos.y())
            if d < bd:
                best, bd = i, d
        return best if bd <= max(10.0, self.scale * 1.5) else -1

    def _prop_at(self, pos):
        p = self.doc.project
        best, bd = -1, 1e9
        for i, q in enumerate(p.props):
            c = self.from_world(q.x, q.y)
            d = math.hypot(c.x() - pos.x(), c.y() - pos.y())
            if d < bd:
                best, bd = i, d
        return best if bd <= max(10.0, self.scale * 1.5) else -1

    def mousePressEvent(self, ev):
        p = self.doc.project
        if p is None:
            return
        pos = ev.position()
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.LeftButton and self._space):
            self._pan_from = (pos, QPointF(self.off))
            return
        if self.tool == SELECT and ev.button() == Qt.LeftButton:
            hit = self._prop_at(pos)
            if hit >= 0:
                self.doc.select_prop(hit)
                self._prop_drag = (hit, copy_props(p))
                return
        if self.tool == SCULPT and ev.button() in (Qt.LeftButton, Qt.RightButton):
            if ev.button() == Qt.RightButton:
                self._tile_before = copy_tiles(p)
                self._texture_dab(pos)
                return
            self._stroke_before = copy_heights(p)
            gx, gy = self.to_grid(pos)
            self.brush.begin(p, gx, gy)
            self._dab(pos)
            return
        if self.tool in (SPAWN, SELECT) and ev.button() == Qt.LeftButton:
            hit = self._spawn_at(pos)
            if hit >= 0:
                self.doc.select(hit)
                self._drag_spawn = (hit, bool(ev.modifiers() & Qt.AltModifier))
                self._spawns_before = copy_spawns(p)
            elif self.tool == SPAWN:
                x, y = self.to_world(pos)
                wx, wy = p.world_size()
                if 0 <= x < wx and 0 <= y < wy:
                    before = copy_spawns(p)
                    after = copy_spawns(p)
                    from ..model import Spawn
                    after.append(Spawn(x, y, None))
                    self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Add spawn"))
                    self.doc.select(len(after) - 1)
            else:
                self.doc.select(-1)
        if ev.button() == Qt.RightButton and self.tool == SPAWN:
            hit = self._spawn_at(pos)
            if hit >= 0:
                before = copy_spawns(p)
                after = [s for i, s in enumerate(before) if i != hit]
                self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Delete spawn"))
                self.doc.select(-1)

    def _dab(self, pos):
        p = self.doc.project
        gx, gy = self.to_grid(pos)
        rect = self.brush.apply(p, gx, gy)
        self._last_dab = pos
        self.doc.heights_changed.emit(rect)
        self.update()

    def _texture_dab(self, pos):
        p = self.doc.project
        if p is None or not p.tiles:
            return
        if not self.doc.tile_types:
            # without the donor's tile-type table a stroke cannot be checked
            # against the client's three-layer limit, so it is not allowed
            self.doc.status.emit("Texture painting waits for the donor's texture table (Assets).")
            return
        gx, gy = self.to_grid(pos)
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
        self._last_dab = pos
        self.update()

    def mouseMoveEvent(self, ev):
        p = self.doc.project
        pos = ev.position()
        self._mouse = pos
        if self._pan_from is not None and not (ev.buttons() & (Qt.MiddleButton | Qt.LeftButton)):
            self._pan_from = None
            return
        if p is None:
            return
        if self._pan_from is not None:
            start, off = self._pan_from
            self.off = QPointF(off.x() + pos.x() - start.x(), off.y() + pos.y() - start.y())
            self.update()
            self.view_changed.emit()
            self.user_moved.emit()
            return
        x, y = self.to_world(pos)
        gx, gy = self.to_grid(pos)
        wx, wy = p.world_size()
        if 0 <= x < wx and 0 <= y < wy:
            z = p.height_at_world(x, y)
            qx, qy = int(math.floor(gx)), int(math.floor(gy))
            slope = ""
            if self.doc.slopes and 0 <= qy < len(self.doc.slopes) and 0 <= qx < len(self.doc.slopes[0]):
                slope = f"   slope {self.doc.slopes[qy][qx]:.1f} deg"
            self.cursor_info.emit(f"x {x:,.0f}   y {y:,.0f}   z {z:,.0f}{slope}   cell {int(gx + 0.5)},{int(gy + 0.5)}")
        if self._tile_before is not None and ev.buttons() & Qt.RightButton:
            step = max(1.0, self.brush.radius * self.scale * 0.25)
            if self._last_dab is None or math.hypot(pos.x() - self._last_dab.x(), pos.y() - self._last_dab.y()) >= step:
                self._texture_dab(pos)
            return
        if self._stroke_before is not None and ev.buttons() & Qt.LeftButton:
            step = max(1.0, self.brush.radius * self.scale * 0.25)
            if self._last_dab is None or math.hypot(pos.x() - self._last_dab.x(), pos.y() - self._last_dab.y()) >= step:
                self._dab(pos)
            return
        if self._prop_drag is not None and ev.buttons() & Qt.LeftButton:
            idx, before = self._prop_drag
            if 0 <= idx < len(p.props):
                q = p.props[idx]
                if q.grid_locked:
                    q.x, q.y, q.z = p.snap_world(x, y)
                else:
                    q.x = min(max(x, 0.0), wx - 1)
                    q.y = min(max(y, 0.0), wy - 1)
                    q.z = p.height_at_world(q.x, q.y)
                self.update()
            return
        if self._drag_spawn is not None and ev.buttons() & Qt.LeftButton:
            idx, turning = self._drag_spawn
            s = p.spawns[idx]
            if turning or ev.modifiers() & Qt.AltModifier:
                s.angle = math.atan2(y - s.y, x - s.x)
            else:
                s.x = min(max(x, 0.0), wx - 1)
                s.y = min(max(y, 0.0), wy - 1)
            self.doc.spawns_changed.emit()
        self.update()

    def mouseReleaseEvent(self, ev):
        p = self.doc.project
        if self._pan_from is not None:
            self._pan_from = None
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
        if self._stroke_before is not None:
            after = copy_heights(p)
            before = self._stroke_before
            self._stroke_before = None
            self._last_dab = None
            if after != before:
                self.doc.undo.push(HeightsCommand(self.doc, before, after,
                                                  f"Sculpt ({self.brush.tool})"))
            return
        if self._prop_drag is not None:
            idx, before = self._prop_drag
            self._prop_drag = None
            after = copy_props(p)
            moved = any((a.x, a.y, a.z) != (b.x, b.y, b.z) for a, b in zip(after, before))
            if moved:
                p.props = before
                self.doc.undo.push(PropsCommand(self.doc, before, after, "Move prop"))
            return
        if self._drag_spawn is not None:
            after = copy_spawns(p)
            before = self._spawns_before
            self._drag_spawn = None
            if any((a.x, a.y, a.angle) != (b.x, b.y, b.angle) for a, b in zip(after, before)):
                p.spawns = before
                self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Move spawn"))

    def leaveEvent(self, _ev):
        self._mouse = None
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.doc.project is not None and self._img is not None and ev.oldSize().width() <= 0:
            self.fit()
        self.view_changed.emit()

    def set_overlays(self, slopes=None, reach=None, grid=None, contours=None):
        if slopes is not None:
            self.show_slopes = slopes
        if reach is not None:
            self.show_reach = reach
        if grid is not None:
            self.show_grid = grid
        if contours is not None:
            self.show_contours = contours
        self._build_overlay()
        self.update()
