"""Decoded textures as QImages, straight from their RGBA bytes.

Textures used to be PNG-encoded in Python after decoding and then PNG-decoded
again by Qt - a round trip that cost more than the decode itself. Now the pixels
go in as they are."""

from __future__ import annotations

from PySide6.QtGui import QImage


def qimage(tex):
    """A QImage owning a copy of a TextureAsset/ModelTexture's pixels, or None."""
    if tex is None:
        return None
    rgba = getattr(tex, "rgba", None)
    w, h = int(getattr(tex, "width", 0)), int(getattr(tex, "height", 0))
    if not rgba or w <= 0 or h <= 0 or len(rgba) != w * h * 4:
        return None
    return QImage(rgba, w, h, w * 4, QImage.Format_RGBA8888).copy()
