"""Render the editor window to a PNG without a visible desktop (offscreen Qt).

    py -3.12 tools/shot_ui.py out.png [width height] [classic|dark] [3d|top] [hover] [tab] [full]

Used while working on the window chrome: it proves the layout builds and gives a
picture of it. Not part of the editor.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtCore import QPointF, QSettings, Qt                     # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from kryta_cs import paths                                      # noqa: E402
from kryta_cs.ui import theme                                   # noqa: E402
from kryta_cs.ui.main_window import MainWindow                  # noqa: E402
from kryta_cs.ui.view3d import _bilinear                        # noqa: E402


class Ev:
    def __init__(self, x, y, buttons=Qt.NoButton):
        self._p = QPointF(x, y)
        self._b = buttons

    def position(self):
        return self._p

    def button(self):
        return self._b

    def buttons(self):
        return Qt.NoButton

    def modifiers(self):
        return Qt.NoModifier


def main(argv):
    out = argv[1] if len(argv) > 1 else "shot.png"
    w = int(argv[2]) if len(argv) > 2 else 1600
    h = int(argv[3]) if len(argv) > 3 else 980
    theme_name = argv[4] if len(argv) > 4 else paths.load_prefs().get("theme", "classic")
    mode = argv[5] if len(argv) > 5 else "3d"
    hover = len(argv) > 6 and argv[6] == "hover"
    tab = int(argv[7]) if len(argv) > 7 else -1
    full = len(argv) > 8 and argv[8] == "full"
    app = QApplication([])
    theme.apply(app, theme_name)
    st = QSettings("Kryta", "ConstructionSet")
    for key in ("state", "centre", "layout"):
        st.remove(key)
    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.resize(w, h)
    win.show()
    win.render.set_mode(mode)
    deadline = time.time() + (35 if full else 12)
    while time.time() < deadline:
        app.processEvents()
        if full:
            if win.assets is not None and win.assets.models:
                break
        elif win.assets is not None:
            break
        time.sleep(0.05)
    for _ in range(30):
        app.processEvents()
    time.sleep(0.8)
    for _ in range(20):
        app.processEvents()
    if tab >= 0:
        win.objectwin.tabs.setCurrentIndex(tab)
        for _ in range(10):
            app.processEvents()
    if hover:
        p = win.doc.project
        if p is not None:
            gx, gy = p.dim_x / 2.0, p.dim_y / 2.0
            if mode == "top":
                v = win.render.view2d
                pt = QPointF(v.off.x() + (gx + 0.5) * v.scale, v.off.y() + (gy + 0.5) * v.scale)
                v.mouseMoveEvent(Ev(pt.x(), pt.y()))
            else:
                v = win.render.view3d
                z = _bilinear(p, gx, gy)
                pt, _d = v._project(gx, gy, z, v._camera())
                v.mouseMoveEvent(Ev(pt.x(), pt.y()))
            for _ in range(10):
                app.processEvents()
    win.grab().save(out)
    print(out)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
