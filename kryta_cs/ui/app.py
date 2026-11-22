"""Start the editor the way Photoshop does: a loading window first, then the work.

Only Qt, the theme and the popup are imported before the popup is on screen.
Everything slow comes after it: importing the editor (Rurik's toolkit), building
the main window, opening the last map and analysing it. The main window is shown
once the map is open; the popup stays up while the donor textures, the placed
models and the terrain blend load in the background, and closes when the last of
them is done."""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME, paths
    from . import theme
    from .loading import LoadingPopup

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    theme.apply(app, paths.load_prefs().get("theme", "classic"))
    splash = LoadingPopup(None, APP_NAME)
    splash.begin("startup", "Starting the Kryta Construction Set ...", pump=True, done=0, total=4)

    splash.begin("startup", "Loading the editor ...", pump=True, done=1, total=4)
    from . import icons
    from .main_window import MainWindow
    app.setWindowIcon(icons.icon("app"))

    splash.begin("startup", "Building the window ...", pump=True, done=2, total=4)
    win = MainWindow(loading=splash)

    splash.begin("startup", "Opening the last map ...", pump=True, done=3, total=4)
    win.startup()
    win.show()
    splash.set_anchor(win)
    splash.end("startup")          # stays up if background jobs are still running
    return app.exec()
