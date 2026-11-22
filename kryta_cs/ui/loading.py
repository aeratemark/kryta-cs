"""The little "loading" window the Construction Set showed for slow work.

A frameless window with the operation's name and an indeterminate bar. It is
also the startup splash: `ui/app.py` shows it before anything heavy is imported,
so launching the editor puts a window on screen at once instead of leaving you
to watch a frozen one. The main window then keeps using the same popup for its
background jobs (donor read, model loading, terrain blend).

Several jobs can be in flight; each has a key and, when it knows one, a count
of steps (textures, models, rows of the terrain blend). The popup shows the
newest job's text and how far it has got, and hides when the last one ends."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

from .chrome import CaptionFrame


class LoadingPopup(QWidget):
    def __init__(self, parent=None, title: str = "Working"):
        flags = Qt.FramelessWindowHint | (Qt.Tool if parent is not None
                                          else Qt.SplashScreen | Qt.WindowStaysOnTopHint)
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedWidth(380)
        self._anchor = parent
        self._jobs = {}                 # key -> [text, done, total], in start order
        self.frame = CaptionFrame(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.frame)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 10, 12, 12)
        self.label = QLabel("Loading ...")
        self.label.setWordWrap(True)
        bl.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")
        self.bar.setAlignment(Qt.AlignCenter)
        self.bar.setFixedHeight(16)
        bl.addWidget(self.bar)
        self.frame.add(body, 1)

    def set_anchor(self, widget):
        """Centre on this widget from now on (the main window, once it exists)."""
        self._anchor = widget

    def _place(self):
        self.adjustSize()
        a = self._anchor
        if a is not None and a.isVisible():
            centre = a.frameGeometry().center()
        else:
            screen = QGuiApplication.primaryScreen()
            centre = screen.availableGeometry().center() if screen is not None else None
        if centre is not None:
            self.move(centre.x() - self.width() // 2, centre.y() - self.height() // 2)

    def _refresh(self, pump: bool = False):
        if not self._jobs:
            self.hide()
            return
        text, done, total = list(self._jobs.values())[-1]
        self.label.setText(text)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(max(0, min(done, total)))
            self.bar.setTextVisible(True)
        else:
            # nothing counted yet: an empty bar, not the style's busy animation
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.bar.setTextVisible(False)
        self._place()
        self.show()
        self.raise_()
        if pump:
            # startup only: paint now, the caller is about to block for a while.
            # Never from a running editor - it would re-enter whatever called it.
            QApplication.processEvents()

    def begin(self, key: str, text: str, pump: bool = False, done: int = 0, total: int = 0):
        self._jobs.pop(key, None)
        self._jobs[key] = [text, int(done), int(total)]
        self._refresh(pump)

    def step(self, key: str, done: int, total: int | None = None, text: str | None = None):
        """Report progress on a running job (only repaints if it is the shown one)."""
        job = self._jobs.get(key)
        if job is None:
            return
        job[1] = int(done)
        if total is not None:
            job[2] = int(total)
        if text:
            job[0] = text
        if key == list(self._jobs)[-1]:
            text, done, total = job
            self.label.setText(text)
            if total > 0:
                if self.bar.maximum() != total:
                    self.bar.setRange(0, total)
                self.bar.setValue(max(0, min(done, total)))
                self.bar.setTextVisible(True)

    def end(self, key: str):
        if self._jobs.pop(key, None) is not None:
            self._refresh()

    def busy(self) -> bool:
        return bool(self._jobs)

    # the original one-job API
    def start(self, text: str):
        self.begin("_", text)

    def stop(self):
        self._jobs.clear()
        self.hide()
