"""A small 3D preview of one donor prop model, for its Properties dialog.

The geometry is Rurik's `modelfile` output: submodels with Y-up positions and
triangle indices. Each submodel is painted with the texture its material layer
names (the chain `submodel.unk -> material -> layer.texpath -> 0xFA5 slot`),
using the submodel's own UV set, and shaded by its face normal. That is the
same texture chain the Render Window uses, so the dialog shows the model the
client would draw.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from .images import qimage
from .theme import COLORS
from .uvmap import affine

YAW = math.radians(32)
PITCH = math.radians(24)
LIGHT = (0.4, 0.45, 0.8)


def _normalise(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def _sub_triangles(sm):
    pos = list(sm.positions())
    idx = list(sm.indices)
    for i in range(0, len(idx) - 2, 3):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        if max(a, b, c) >= len(pos):
            continue
        yield a, b, c


class ModelPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.assets = None
        self.error = ""
        self.setMinimumSize(320, 320)

    def set_assets(self, assets, error=""):
        self.assets = assets
        self.error = error
        self.update()

    def _tris(self):
        """Every triangle of the model as (points, uvs, image, normal)."""
        if self.assets is None:
            return []
        geo = self.assets.geometry
        images = [qimage(t)
                  for t in getattr(self.assets, "textures", [])]
        slots = getattr(self.assets, "sub_textures", [])
        out = []
        for si, sm in enumerate(geo.submodels):
            pos = list(sm.positions())
            slot, uv_set = slots[si] if si < len(slots) else (None, 0)
            img = images[slot] if slot is not None and 0 <= slot < len(images) else None
            uvs = None
            if img is not None and not img.isNull():
                try:
                    uvs = sm.texcoords(int(uv_set))
                except Exception:                                    # noqa: BLE001
                    uvs = None
            for a, b, c in _sub_triangles(sm):
                pa, pb, pc = pos[a], pos[b], pos[c]
                # a model's z is height, negative upward
                pa, pb, pc = (pa[0], pa[1], -pa[2]), (pb[0], pb[1], -pb[2]), (pc[0], pc[1], -pc[2])
                ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
                vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
                nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
                n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                normal = (nx / n, ny / n, nz / n)
                uv = None
                if uvs is not None and max(a, b, c) < len(uvs):
                    uv = (uvs[a], uvs[b], uvs[c])
                out.append(((pa, pb, pc), uv, img, normal))
        return out

    def _project(self, p, scale, cx, cy):
        """A z-up isometric point: a model's axes are the map's (x, y ground, z up)."""
        x, y, z = p
        cyaw, syaw = math.cos(YAW), math.sin(YAW)
        rx = x * cyaw - y * syaw
        ry = x * syaw + y * cyaw
        cp, sp = math.cos(PITCH), math.sin(PITCH)
        return QPointF(cx + rx * scale, cy + (ry * sp - z * cp) * scale), ry * cp + z * sp

    def paintEvent(self, _ev):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(COLORS["viewport"]))
        qp.setRenderHint(QPainter.Antialiasing, True)
        if self.assets is None:
            qp.setPen(QColor(COLORS["vtext"]))
            qp.drawText(self.rect(), Qt.AlignCenter, self.error or "no model")
            return
        tris = self._tris()
        if not tris:
            qp.setPen(QColor(COLORS["vtext"]))
            qp.drawText(self.rect(), Qt.AlignCenter, "the model has no triangles")
            return
        xs = [v[0] for t in tris for v in t[0]]
        ys = [v[1] for t in tris for v in t[0]]
        zs = [v[2] for t in tris for v in t[0]]
        cx3 = (min(xs) + max(xs)) / 2
        cy3 = (min(ys) + max(ys)) / 2
        cz3 = (min(zs) + max(zs)) / 2
        span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1.0)
        scale = min(self.width(), self.height()) * 0.72 / span
        cx, cy = self.width() / 2, self.height() / 2
        light = _normalise(LIGHT)
        out = []
        for (a, b, c), uv, img, normal in tris:
            pa, da = self._project((a[0] - cx3, a[1] - cy3, a[2] - cz3), scale, cx, cy)
            pb, db = self._project((b[0] - cx3, b[1] - cy3, b[2] - cz3), scale, cx, cy)
            pc, dc = self._project((c[0] - cx3, c[1] - cy3, c[2] - cz3), scale, cx, cy)
            shade = abs(normal[0] * light[0] + normal[1] * light[1] + normal[2] * light[2])
            col = QColor(int(84 + 100 * shade), int(94 + 105 * shade), int(64 + 76 * shade))
            brush = None
            if img is not None and uv is not None:
                w, h = img.width(), img.height()
                src = [(uv[0][0] * w, uv[0][1] * h), (uv[1][0] * w, uv[1][1] * h),
                       (uv[2][0] * w, uv[2][1] * h)]
                t = affine(src, [(pa.x(), pa.y()), (pb.x(), pb.y()), (pc.x(), pc.y())])
                if t is not None:
                    brush = QBrush(img)
                    brush.setTransform(t)
            out.append(((da + db + dc) / 3, QPolygonF([pa, pb, pc]), col, brush, shade))
        out.sort(key=lambda t: -t[0])
        for _d, poly, col, brush, shade in out:
            if brush is not None:
                path = QPainterPath()
                path.addPolygon(poly)
                path.closeSubpath()
                qp.save()
                qp.setClipPath(path)
                qp.setPen(Qt.NoPen)
                qp.setBrush(brush)
                qp.drawPolygon(poly)
                qp.restore()
            else:
                qp.setPen(QPen(col.darker(145), 0.5))
                qp.setBrush(col)
                qp.drawPolygon(poly)
            if shade < 1.0:
                qp.setPen(Qt.NoPen)
                qp.setBrush(QColor(0, 0, 0, int(min(1.0, 1.0 - shade) * 130)))
                qp.drawPolygon(poly)
        textured = sum(1 for t in out if t[3] is not None)
        qp.setPen(QColor(COLORS["vtext"]))
        qp.drawText(QRectF(6, self.height() - 22, self.width() - 12, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    f"{len(out):,} triangles, {textured:,} textured")
