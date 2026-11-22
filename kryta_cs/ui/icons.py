"""Small 16x16 icons in the flat, bevelled manner of a Windows 98/2000 toolbar.

Every icon is drawn here with QPainter so nothing ships as an image file. The
window icon is drawn at 16, 32 and 48 pixels: a little grey tool window over a
green hill, the Construction Set's own mix of window and landscape."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QStyle

_STD = {
    "new": QStyle.SP_FileIcon,
    "open": QStyle.SP_DirOpenIcon,
    "save": QStyle.SP_DialogSaveButton,
    "undo": QStyle.SP_ArrowBack,
    "redo": QStyle.SP_ArrowForward,
}


def _draw(fn, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing, False)
    fn(qp, size)
    qp.end()
    return QIcon(pm)


def _px(qp, x, y, w, h, color):
    qp.fillRect(QRectF(x, y, w, h), QColor(color))


def _outline(qp, x, y, w, h, color="#000000"):
    qp.setPen(QPen(QColor(color), 1))
    qp.setBrush(Qt.NoBrush)
    qp.drawRect(QRectF(x + 0.5, y + 0.5, w - 1, h - 1))


def _page(qp, size, fill="#ffffff"):
    _px(qp, 3, 1, 10, 14, fill)
    _px(qp, 3, 1, 10, 1, "#808080")
    _px(qp, 3, 14, 10, 1, "#808080")
    _px(qp, 3, 1, 1, 14, "#808080")
    _px(qp, 12, 1, 1, 14, "#808080")
    _px(qp, 10, 1, 3, 3, "#d4d0c8")
    _outline(qp, 10, 1, 3, 3)
    for y in (5, 7, 9, 11):
        _px(qp, 5, y, 6, 1, "#404080")


def _new(qp, size):
    _page(qp, size)
    _px(qp, 8, 5, 1, 5, "#000080")
    _px(qp, 6, 7, 5, 1, "#000080")


def _open(qp, size):
    _px(qp, 1, 4, 8, 2, "#e0c040")
    _px(qp, 2, 6, 12, 8, "#f0d060")
    _outline(qp, 1, 3, 9, 3)
    _outline(qp, 1, 5, 14, 9)
    _px(qp, 3, 7, 10, 1, "#fff0a0")


def _save(qp, size):
    _px(qp, 2, 1, 12, 14, "#c0c0c0")
    _outline(qp, 1, 0, 14, 15)
    _px(qp, 4, 2, 8, 4, "#000080")
    _px(qp, 5, 2, 3, 1, "#4fa3d9")
    _px(qp, 4, 8, 8, 6, "#ffffff")
    _outline(qp, 4, 8, 8, 6)
    _px(qp, 6, 10, 4, 1, "#808080")
    _px(qp, 6, 12, 4, 1, "#808080")


def _undo(qp, size):
    qp.setPen(QPen(QColor("#000080"), 2))
    qp.setBrush(Qt.NoBrush)
    qp.drawArc(QRectF(2, 3, 11, 9), 40 * 16, 220 * 16)
    qp.setBrush(QColor("#000080"))
    qp.setPen(Qt.NoPen)
    qp.drawPolygon(QPolygonF([QPointF(3, 3), QPointF(3, 9), QPointF(8, 6)]))


def _redo(qp, size):
    qp.setPen(QPen(QColor("#000080"), 2))
    qp.setBrush(Qt.NoBrush)
    qp.drawArc(QRectF(2, 3, 11, 9), -80 * 16, 220 * 16)
    qp.setBrush(QColor("#000080"))
    qp.setPen(Qt.NoPen)
    qp.drawPolygon(QPolygonF([QPointF(12, 3), QPointF(12, 9), QPointF(7, 6)]))


def _sculpt(qp, size):
    qp.setPen(Qt.NoPen)
    qp.setBrush(QColor("#2f7d32"))
    qp.drawPolygon(QPolygonF([QPointF(0, 15), QPointF(5, 6), QPointF(8, 10), QPointF(11, 4), QPointF(16, 15)]))
    qp.setBrush(QColor("#8bc34a"))
    qp.drawPolygon(QPolygonF([QPointF(5, 6), QPointF(8, 10), QPointF(6, 15), QPointF(2, 15)]))
    qp.setBrush(QColor("#ffffff"))
    qp.drawPolygon(QPolygonF([QPointF(11, 4), QPointF(9.5, 7), QPointF(12.5, 7)]))
    qp.setPen(QPen(QColor("#000000"), 1))
    qp.drawLine(0, 15, 15, 15)


def _spawn(qp, size):
    qp.setPen(QPen(QColor("#000000"), 1))
    qp.drawLine(4, 2, 4, 15)
    qp.setBrush(QColor("#c00000"))
    qp.drawPolygon(QPolygonF([QPointF(5, 2), QPointF(14, 5), QPointF(5, 8)]))
    qp.setBrush(QColor("#808080"))
    qp.drawRect(QRectF(1, 14, 7, 1))


def _select(qp, size):
    qp.setPen(QPen(QColor("#000000"), 1))
    qp.setBrush(QColor("#ffffff"))
    qp.drawPolygon(QPolygonF([QPointF(3, 1), QPointF(3, 13), QPointF(6, 10), QPointF(8, 15), QPointF(10, 14),
                              QPointF(8, 9), QPointF(12, 9)]))


def _slopes(qp, size):
    for i, c in enumerate(("#00a000", "#e0c000", "#c00000")):
        qp.fillRect(QRectF(1 + i * 5, 3, 4, 10), QColor(c))
    _outline(qp, 0, 2, 15, 12)


def _reach(qp, size):
    qp.setPen(QPen(QColor("#006000"), 2))
    qp.setBrush(Qt.NoBrush)
    qp.drawEllipse(QRectF(2, 2, 12, 12))
    qp.setPen(QPen(QColor("#c00000"), 2))
    qp.drawLine(4, 4, 12, 12)


def _contours(qp, size):
    qp.setPen(QPen(QColor("#804000"), 1))
    qp.setBrush(Qt.NoBrush)
    for r in (7, 5, 3, 1):
        qp.drawEllipse(QPointF(8, 8), r, r * 0.7)


def _grid(qp, size):
    _px(qp, 1, 2, 14, 12, "#ffffff")
    _outline(qp, 1, 2, 14, 12)
    qp.setPen(QPen(QColor("#606060"), 1))
    for i in range(1, 4):
        x = 1 + i * 14 // 4
        qp.drawLine(x, 2, x, 14)
        y = 2 + i * 12 // 4
        qp.drawLine(1, y, 15, y)
    _px(qp, 8, 8, 3, 3, "#000080")


def _deploy(qp, size):
    qp.setPen(QPen(QColor("#000000"), 1))
    qp.setBrush(QColor("#c0c0c0"))
    qp.drawRect(QRectF(1.5, 8.5, 13, 6))
    qp.setBrush(QColor("#008000"))
    qp.drawPolygon(QPolygonF([QPointF(8, 1), QPointF(13, 6), QPointF(10, 6), QPointF(10, 9), QPointF(6, 9),
                              QPointF(6, 6), QPointF(3, 6)]))


def _world(qp, size):
    qp.setPen(QPen(QColor("#000000"), 1))
    qp.setBrush(QColor("#4fa3d9"))
    qp.drawEllipse(QRectF(1.5, 1.5, 13, 13))
    qp.setBrush(QColor("#3d8b40"))
    qp.setPen(Qt.NoPen)
    qp.drawPolygon(QPolygonF([QPointF(5, 4), QPointF(10, 3), QPointF(12, 7), QPointF(9, 10),
                              QPointF(4, 9), QPointF(3, 6)]))
    qp.setPen(QPen(QColor("#ffffff"), 1))
    qp.drawArc(QRectF(4, 2, 8, 12), 90 * 16, 180 * 16)


def _objects(qp, size):
    _px(qp, 1, 2, 14, 12, "#ffffff")
    _outline(qp, 1, 2, 14, 12)
    _px(qp, 2, 3, 12, 3, "#0a246a")
    for y in (7, 9, 11):
        _px(qp, 3, y, 2, 1, "#c00000")
        _px(qp, 6, y, 7, 1, "#404040")


def _cell(qp, size):
    _px(qp, 1, 2, 14, 12, "#ffffff")
    _outline(qp, 1, 2, 14, 12)
    qp.setPen(QPen(QColor("#808080"), 1))
    for x in (5, 9, 13):
        qp.drawLine(x, 2, x, 14)
    for y in (6, 10):
        qp.drawLine(1, y, 15, y)
    qp.setPen(QPen(QColor("#ffb000"), 2))
    qp.setBrush(Qt.NoBrush)
    qp.drawRect(QRectF(4, 5, 5, 5))


def _fit(qp, size):
    qp.setPen(QPen(QColor("#000080"), 1))
    qp.drawLine(3, 3, 7, 3)
    qp.drawLine(3, 3, 3, 7)
    qp.drawLine(12, 3, 8, 3)
    qp.drawLine(12, 3, 12, 7)
    qp.drawLine(3, 12, 7, 12)
    qp.drawLine(3, 12, 3, 8)
    qp.drawLine(12, 12, 8, 12)
    qp.drawLine(12, 12, 12, 8)
    qp.setBrush(QColor("#e0c000"))
    qp.setPen(Qt.NoPen)
    qp.drawRect(QRectF(6, 6, 4, 4))


def _tube(qp, size):
    qp.setPen(QPen(QColor("#000000"), 1))
    qp.setBrush(QColor("#3a4a5a"))
    qp.drawPolygon(QPolygonF([QPointF(8, 1), QPointF(15, 4), QPointF(15, 11), QPointF(8, 14),
                              QPointF(1, 11), QPointF(1, 4)]))
    qp.setBrush(QColor("#6b8ea8"))
    qp.drawPolygon(QPolygonF([QPointF(8, 1), QPointF(15, 4), QPointF(8, 7), QPointF(1, 4)]))
    qp.setBrush(QColor("#2a3540"))
    qp.drawPolygon(QPolygonF([QPointF(1, 4), QPointF(8, 7), QPointF(8, 14), QPointF(1, 11)]))


def _wire(qp, size):
    qp.setPen(QPen(QColor("#008000"), 1))
    qp.setBrush(Qt.NoBrush)
    for i in range(4):
        qp.drawLine(1 + i * 4, 3, 1 + i * 4, 13)
        qp.drawLine(1, 3 + i * 3, 15, 3 + i * 3)
    qp.setPen(QPen(QColor("#000000"), 1))
    qp.drawRect(QRectF(0.5, 2.5, 15, 11))


def _cells(qp, size):
    _cell(qp, size)
    qp.setPen(QPen(QColor("#000080"), 1))
    qp.drawLine(0, 15, 15, 15)


def _app(qp, size):
    s = size / 16.0
    qp.scale(s, s)
    _px(qp, 1, 2, 14, 13, "#d4d0c8")
    _px(qp, 1, 2, 14, 3, "#0a246a")
    _px(qp, 1, 2, 14, 1, "#4fa3d9")
    qp.fillRect(QRectF(1, 5, 14, 10), QColor("#000000"))
    qp.setPen(Qt.NoPen)
    qp.setBrush(QColor("#2f7d32"))
    qp.drawPolygon(QPolygonF([QPointF(2, 15), QPointF(6, 7), QPointF(9, 11), QPointF(12, 6),
                              QPointF(15, 15)]))
    qp.setBrush(QColor("#8bc34a"))
    qp.drawPolygon(QPolygonF([QPointF(6, 7), QPointF(9, 11), QPointF(7, 15), QPointF(3, 15)]))
    qp.setPen(QPen(QColor("#808080"), 1))
    qp.setBrush(Qt.NoBrush)
    qp.drawRect(QRectF(0.5, 1.5, 15, 14))
    qp.setPen(QPen(QColor("#c0c0c0"), 1))
    qp.drawRect(QRectF(0.5, 4.5, 15, 11))


_DRAWN = {
    "new": _new, "open": _open, "save": _save, "undo": _undo, "redo": _redo,
    "sculpt": _sculpt, "spawn": _spawn, "select": _select,
    "slopes": _slopes, "reach": _reach, "contours": _contours, "grid": _grid,
    "deploy": _deploy, "world": _world, "objects": _objects, "cell": _cell,
    "fit": _fit, "tube": _tube, "wire": _wire, "cells": _cells,
}


def icon(name: str) -> QIcon:
    if name == "app":
        out = QIcon()
        for size in (16, 24, 32, 48, 64):
            out.addPixmap(_draw(_app, size).pixmap(size, size))
        return out
    fn = _DRAWN.get(name)
    if fn:
        return _draw(fn)
    std = _STD.get(name)
    return QApplication.style().standardIcon(std) if std else QIcon()
