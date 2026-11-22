"""Mapping one triangle of a model's texture onto its own triangle on screen."""

from __future__ import annotations

from PySide6.QtGui import QTransform


def affine(src, dst):
    """The affine transform mapping three source points onto three targets.

    A model's submodel carries a UV per vertex, so a triangle of the texture
    maps exactly onto the projected triangle; an affine map is enough because
    the triangle has three points. Returns None for a degenerate source
    triangle (zero area in the texture, which has no image to show)."""
    (u0, v0), (u1, v1), (u2, v2) = src
    (x0, y0), (x1, y1), (x2, y2) = dst
    det = (u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0)
    if abs(det) < 1e-9:
        return None
    m11 = ((x1 - x0) * (v2 - v0) - (x2 - x0) * (v1 - v0)) / det
    m21 = ((u1 - u0) * (x2 - x0) - (u2 - u0) * (x1 - x0)) / det
    m12 = ((y1 - y0) * (v2 - v0) - (y2 - y0) * (v1 - v0)) / det
    m22 = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / det
    return QTransform(m11, m12, m21, m22, x0 - m11 * u0 - m21 * v0, y0 - m12 * u0 - m22 * v0)
