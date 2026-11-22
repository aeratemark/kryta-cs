"""The CS's window chrome: captioned frames and the sunken view well.

The Morrowind Construction Set was a Windows 2000 MDI application. Its Render
Window and Cell View were child windows with a grey caption and a sunken client
area. The Object Window is a real Qt dock (so it can be moved), but it is dressed
the same way, and the two central windows use CaptionFrame directly."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout, QWidget

from .theme import COLORS, caption_font


class CaptionBar(QWidget):
    """A grey caption with a title, an optional icon and a right note."""

    def __init__(self, text: str = "", compact: bool = False, parent=None):
        super().__init__(parent)
        self._text = text
        self._detail = ""
        self._icon: QIcon | None = None
        self._compact = compact
        self.setFixedHeight(18 if compact else 22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setText(self, text: str):
        if text != self._text:
            self._text = text
            self.update()

    def text(self) -> str:
        return self._text

    def setDetail(self, text: str):
        if text != self._detail:
            self._detail = text
            self.update()

    def setIcon(self, icon: QIcon | None):
        self._icon = icon
        self.update()

    def paintEvent(self, _ev):
        c = COLORS
        qp = QPainter(self)
        w, h = self.width(), self.height()
        if "light" in c:
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0.0, QColor(c["title1"]))
            g.setColorAt(0.72, QColor(c["title2"]))
            g.setColorAt(1.0, QColor(c["title2"]))
        else:
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0.0, QColor(c["title1"]))
            g.setColorAt(1.0, QColor(c["title2"]))
        qp.fillRect(QRectF(0, 0, w, h), g)
        qp.setPen(QPen(QColor(c.get("light", c["border"])), 1))
        qp.drawLine(0, 0, w, 0)
        qp.setPen(QPen(QColor(c.get("shadow", c["border"])), 1))
        qp.drawLine(0, h - 1, w, h - 1)
        x = 4
        if self._icon is not None:
            size = 12 if self._compact else 14
            self._icon.paint(qp, QRect(int(x), int((h - size) / 2), size, size))
            x += size + 4
        qp.setFont(caption_font(self._compact))
        qp.setPen(QColor(c["title_text"]))
        metrics = qp.fontMetrics()
        title = metrics.elidedText(self._text, Qt.ElideRight, max(0, w - x - 90))
        qp.drawText(QRectF(x, 0, w - x - 8, h), Qt.AlignVCenter | Qt.AlignLeft, title)
        if self._detail:
            qp.setFont(QFont(self.font().family(), 7))
            qp.setPen(QColor(c["title_sub"]))
            qp.drawText(QRectF(x, 0, w - 8, h), Qt.AlignVCenter | Qt.AlignRight, self._detail)
        qp.end()


class CaptionFrame(QFrame):
    """A captioned, sunken client area, as an MDI child window looked in the CS."""

    def __init__(self, title: str = "", compact: bool = False, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.caption = CaptionBar(title, compact=compact)
        lay.addWidget(self.caption)
        self.well = QFrame()
        self.well.setFrameShape(QFrame.Panel)
        self.well.setFrameShadow(QFrame.Sunken)
        self.well.setLineWidth(2)
        self.body = QVBoxLayout(self.well)
        self.body.setContentsMargins(2, 2, 2, 2)
        self.body.setSpacing(0)
        lay.addWidget(self.well, 1)

    def add(self, widget: QWidget, stretch: int = 0):
        self.body.addWidget(widget, stretch)
        return widget


class Well(QFrame):
    """A Sunken well around a viewport, as the CS framed its render window."""

    def __init__(self, widget: QWidget, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Panel)
        self.setFrameShadow(QFrame.Sunken)
        self.setLineWidth(2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        lay.addWidget(widget)
