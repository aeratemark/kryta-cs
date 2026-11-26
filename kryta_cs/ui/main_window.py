"""The editor window, arranged like the Morrowind Construction Set.

The default screen is three panes and nothing else:

  left    Object Window (category tabs over a record list)
  right   Render Window (the 3D editor) over Cell View (cells and their objects)

Landscape Edit Settings, Map Properties, Problems, Messages and Undo History are
windows the View menu opens; the CS kept them out of the way until asked for. The
status bar is the CS's segmented one: the camera readout, counts, the selected
object and the map."""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QObject, QPoint, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QImage, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel, QMainWindow, QMessageBox,
                               QSplitter, QToolBar, QUndoView)

from .. import APP_NAME, __version__, assets, paths, terrain, texlayers
from ..model import MapProject, PlacedProp, Spawn
from . import icons, theme, terrain_texture
from .cell_view import CellView
from .deploy_window import DeployWindow
from .images import qimage
from .loading import LoadingPopup
from .document import (Document, NpcsCommand, PropsCommand, SpawnsCommand, TilesCommand, copy_npcs,
                       copy_props, copy_spawns, copy_tiles)
from .new_map_dialog import NewMapDialog
from .object_window import ObjectWindow
from .panels import BrushPanel, LogPanel, MapPanel, ProblemsPanel
from .render_window import RenderWindow

FILTER = "Kryta map project (*.kmap.json);;All files (*)"
LAYOUT_VERSION = 3


class _Cancelled(BaseException):
    """Raised inside a worker's progress report once the editor is closing.
    A BaseException, so the readers' own `except Exception` guards (which keep
    one bad model from failing a whole scan) do not swallow it."""


IN_USE = "IN_USE:"
IN_USE_TEXT = ("Gw.dat is in use (Guild Wars or the world server has it open); the editor "
               "reads it as soon as it is free.")
IN_USE_RETRY_MS = 4000


def _in_use(exc) -> bool:
    """Windows refuses to open a file another program holds exclusively, which
    is what Gw.exe does with Gw.dat while the game runs."""
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (32, 33)


class AssetJob(QObject):
    log = Signal(str)
    progress = Signal(int, int, str)
    textures = Signal(object)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, dat, donor_file_id):
        super().__init__()
        self.dat, self.donor_file_id = dat, donor_file_id
        self.cancelled = False

    def _check(self):
        if self.cancelled:
            raise _Cancelled()

    def _progress(self, done, total, text):
        self._check()
        self.progress.emit(done, total, text)

    def _log(self, text):
        self._check()
        self.log.emit(text)

    def run(self):
        try:
            a = assets.load_donor_assets(self.dat, self.donor_file_id, self._log,
                                         self.textures.emit, self._progress)
        except _Cancelled:
            return
        except Exception as exc:                                     # noqa: BLE001
            self.failed.emit((IN_USE if _in_use(exc) else "") + f"{type(exc).__name__}: {exc}")
            return
        self.done.emit(a)


class _Call(QObject):
    """Run one callable on a QThread; the result comes back on the GUI thread."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.cancelled = False

    def _report(self, done, total, text):
        if self.cancelled:
            raise _Cancelled()
        self.progress.emit(done, total, text)

    def run(self):
        try:
            result = self.fn(self._report)
        except _Cancelled:
            return
        except Exception as exc:                                     # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.done.emit(result)


class _GroundSnapshot:
    """What build_surface reads from a project, copied so the worker thread
    never sees a texture stroke half-applied."""

    def __init__(self, p):
        self.dim_x, self.dim_y = p.dim_x, p.dim_y
        self.tiles = bytes(p.tiles) if p.tiles else b""


class MainWindow(QMainWindow):
    def __init__(self, loading=None):
        super().__init__()
        self._threads = []
        self._models_job = None
        self._models_again = False
        self._ground_job = None
        self._ground_steps = None
        self._ground_again = False
        self.prefs = paths.load_prefs()
        self.doc = Document()
        self.brush = terrain.Brush()
        self.assets = None
        self.texture_images = []
        self.model_geometry = {}
        self._ground_surface = None
        self._ground_key = None
        self._asset_thread = None
        self._asset_job = None
        self._asset_stale = False
        self.resize(1600, 980)
        self.setDockNestingEnabled(True)
        self.setWindowIcon(icons.icon("app"))
        self._ground_timer = QTimer(self)
        self._ground_timer.setSingleShot(True)
        self._ground_timer.setInterval(450)
        self._ground_timer.timeout.connect(self._rebuild_ground)

        # ---------------------------------------------------------- three panes
        self.render = RenderWindow(self.doc, self.brush)
        self.cellview = CellView(self.doc, self.render)
        self.centre = QSplitter(Qt.Vertical)
        self.centre.setObjectName("centre")
        self.centre.addWidget(self.render)
        self.centre.addWidget(self.cellview)
        self.setCentralWidget(self.centre)

        self.brushes = BrushPanel(self.doc, self.brush)
        self.objectwin = ObjectWindow(self.doc)
        self.spawns = self.objectwin.spawns
        self.mappanel = MapPanel(self.doc)
        self.problems = ProblemsPanel(self.doc)
        self.logp = LogPanel()
        self.loading = loading if loading is not None else LoadingPopup(self)
        self.history = QUndoView(self.doc.undo)
        self.d_land = self._dock("Landscape Edit Settings", self.brushes, Qt.LeftDockWidgetArea)
        self.d_obj = self._dock("Object Window", self.objectwin, Qt.LeftDockWidgetArea)
        self.d_obj.setFeatures(QDockWidget.DockWidgetMovable)
        self.splitDockWidget(self.d_land, self.d_obj, Qt.Vertical)
        self.d_props = self._dock("Map Properties", self.mappanel, Qt.RightDockWidgetArea)
        self.d_prob = self._dock("Problems", self.problems, Qt.RightDockWidgetArea)
        self.splitDockWidget(self.d_props, self.d_prob, Qt.Vertical)
        self.d_log = self._dock("Messages", self.logp, Qt.BottomDockWidgetArea)
        self.d_hist = self._dock("Undo History", self.history, Qt.BottomDockWidgetArea)
        self.tabifyDockWidget(self.d_log, self.d_hist)
        self.d_log.raise_()
        for d in (self.d_land, self.d_props, self.d_prob, self.d_log, self.d_hist):
            d.hide()

        # ---------------------------------------------------------- status
        self.st_cursor = QLabel("")
        self.st_cursor.setFont(theme.mono_font())
        self.st_counts = QLabel("")
        self.st_sel = QLabel("")
        self.st_assets = QLabel("Assets: not loaded")
        self.st_map = QLabel("")
        self.statusBar().addWidget(self.st_cursor, 2)
        self.statusBar().addWidget(self.st_counts, 0)
        self.statusBar().addWidget(self.st_sel, 1)
        self.statusBar().addPermanentWidget(self.st_assets)
        self.statusBar().addPermanentWidget(self.st_map)

        self._actions()
        self.brushes.tool_chosen.connect(self.set_tool)
        self.brushes.closed.connect(lambda: self.show_landscape(False))
        self.brushes.choose_texture.connect(self._show_textures)
        self.spawns.add_requested.connect(self.place_spawn)
        self.brushes.show_radius.connect(self.render.set_show_radius)
        self.render.tool_requested.connect(self.set_tool)
        self.render.cursor_info.connect(self._cursor)
        self.render.view_changed.connect(self._camera_readout)
        self.render.view3d.model_dropped.connect(self._place_model)
        self.render.view3d.prop_activated.connect(self._prop_properties)
        self.objectwin.edit_prop.connect(self._prop_properties)
        self.render.view3d.npc_dropped.connect(self._place_npc)
        self.render.view3d.npc_activated.connect(self._npc_properties)
        self.objectwin.edit_npc.connect(self._npc_properties)
        self.objectwin.npc_go.connect(self._goto)
        self.doc.npcs_changed.connect(self._npcs_changed)
        self.doc.npcs_changed.connect(self._counts)
        self.doc.npcs_changed.connect(self._selection)
        self.doc.npc_selection_changed.connect(self._selection)
        self.doc.npc_selection_changed.connect(lambda _i: self.render.view3d.update())
        self.problems.goto.connect(self._goto)
        self.problems.recheck.connect(self._validate)
        self.objectwin.open_project.connect(self.open_map)
        self.objectwin.new_map_requested.connect(self.new_map)
        self.objectwin.prop_go.connect(self._goto)
        self.objectwin.textures.texture_selected.connect(self._texture_selected)
        self.objectwin.flora.flora_selected.connect(self._flora_selected)
        self.objectwin.flora.show_toggled.connect(self.render.view3d.set_show_flora)
        self.brushes.choose_flora.connect(self._show_flora)
        self.doc.flora_changed.connect(self.render.view3d.update)
        self.doc.flora_changed.connect(self.objectwin.flora.refresh)
        self.doc.project_changed.connect(self.objectwin.flora.refresh)
        self.doc.project_changed.connect(self._title)
        self.doc.project_changed.connect(self._counts)
        self.doc.project_changed.connect(self._selection)
        self.doc.project_changed.connect(self._queue_assets)
        self.doc.project_changed.connect(self._queue_ground)
        self.doc.project_changed.connect(self._ensure_prop_geometry)
        self.doc.settings_changed.connect(self._queue_ground)
        self.doc.textures_changed.connect(self._textures_changed_ground)
        self.doc.textures_changed.connect(self._textures_undone)
        self.doc.props_changed.connect(self._props_changed)
        self.doc.settings_changed.connect(self._title)
        self.doc.settings_changed.connect(self._counts)
        self.doc.settings_changed.connect(self._queue_assets)
        self.doc.spawns_changed.connect(self._counts)
        self.doc.spawns_changed.connect(self._selection)
        self.doc.analysis_changed.connect(self._counts)
        self.doc.selection_changed.connect(self._selection)
        self.doc.prop_selection_changed.connect(self._selection)
        self.doc.undo.cleanChanged.connect(self._clean_changed)
        self.doc.status.connect(self.logp.log)
        self.d_land.visibilityChanged.connect(self._landscape_visible)
        self._load_npc_types()
        self._title()
        self._counts()
        self._selection()
        if getattr(self, "_worldmap", None) is not None:
            self._worldmap.close()

        st = QSettings("Kryta", "ConstructionSet")
        restored = st.value("layout", 0) == LAYOUT_VERSION and bool(st.value("state"))
        if st.value("geometry"):
            self.restoreGeometry(st.value("geometry"))
        if restored:
            self.restoreState(st.value("state"))
        if restored and st.value("centre"):
            self.centre.restoreState(st.value("centre"))
        if not restored:
            QTimer.singleShot(0, self._default_sizes)
        self.d_land.setMaximumHeight(min(620, max(300, self.brushes.sizeHint().height() + 28)))
        self.logp.log(f"{APP_NAME} {__version__}. Kryta checkout: {paths.REPO_ROOT or 'not found'}")
        self.logp.log(f"Map projects are saved under {paths.projects_dir()}")
        if loading is None:
            QTimer.singleShot(0, self._startup)

    # ------------------------------------------------------------ chrome

    def _dock(self, title, widget, area):
        d = QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _default_sizes(self):
        h = min(620, max(300, self.brushes.sizeHint().height() + 28))
        self.d_land.setMaximumHeight(h)
        self.resizeDocks([self.d_obj], [700], Qt.Horizontal)
        self.resizeDocks([self.d_props], [360], Qt.Horizontal)
        self.resizeDocks([self.d_log], [140], Qt.Vertical)
        self.resizeDocks([self.d_land, self.d_obj], [h, max(320, self.height() - h - 260)], Qt.Vertical)
        self.centre.setSizes([max(420, int(self.height() * 0.66)), max(170, int(self.height() * 0.27))])

    def _act(self, text, slot, key=None, tip=None, checkable=False, icon=None):
        a = QAction(text, self)
        if icon:
            a.setIcon(icons.icon(icon))
        if key:
            a.setShortcut(QKeySequence(key))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        if checkable:
            a.toggled.connect(slot)
        else:
            a.triggered.connect(slot)
        return a

    def _actions(self):
        m = self.menuBar()
        # `toggled` fires for the tool switched OFF too; reacting to that put the
        # old tool straight back, so the buttons never seemed to change. Only
        # the tool switched ON is acted on.
        self.a_sculpt = self._act("Sculpt", lambda on: on and self.set_tool("sculpt"), "S",
                                  checkable=True, icon="sculpt")
        self.a_select = self._act("Select", lambda on: on and self.set_tool("select"), "V",
                                  checkable=True, icon="select")
        self.a_flora = self._act("Flora", lambda on: on and self.set_tool("flora"), "G",
                                 "Paint where the donor's flora grows: left drag paints the type "
                                 "chosen in Object Window > Flora, right drag clears",
                                 checkable=True, icon="flora")
        tgrp = QActionGroup(self)
        tgrp.setExclusive(True)
        for a in (self.a_sculpt, self.a_select, self.a_flora):
            tgrp.addAction(a)
        self.a_sculpt.setChecked(True)

        f = m.addMenu("&File")
        self.a_new = self._act("&New map...", self.new_map, "Ctrl+N", icon="new")
        self.a_open = self._act("&Open...", self.open_map, "Ctrl+O", icon="open")
        self.a_open_game = self._act("Open &Game Map...", self.open_game_map, "Ctrl+Shift+O")
        self.a_save = self._act("&Save", self.save, "Ctrl+S", icon="save")
        self.a_saveas = self._act("Save &as...", self.save_as, "Ctrl+Shift+S")
        f.addActions([self.a_new, self.a_open, self.a_open_game, self.a_save, self.a_saveas])
        self.recent_menu = f.addMenu("Open &recent")
        self._recent()
        f.addSeparator()
        f.addAction(self._act("E&xit", self.close, "Ctrl+Q"))

        e = m.addMenu("&Edit")
        undo = self.doc.undo.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.Undo)
        redo = self.doc.undo.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence.Redo)
        undo.setIcon(icons.icon("undo"))
        redo.setIcon(icons.icon("redo"))
        e.addActions([undo, redo])

        v = m.addMenu("&View")
        self.a_view3d = self._act("3D &Render", lambda _on=False: self.render.set_mode("3d"), "Ctrl+1",
                                  "The 3D render window, the editor", checkable=True, icon="tube")
        self.a_viewtop = self._act("&Top View", lambda _on=False: self.render.set_mode("top"), "Ctrl+2",
                                   "The same map from above, for precise work", checkable=True, icon="cell")
        self.a_view3d.setChecked(True)
        vgroup = QActionGroup(self)
        vgroup.addAction(self.a_view3d)
        vgroup.addAction(self.a_viewtop)
        v.addActions([self.a_view3d, self.a_viewtop])
        self.a_fit = self._act("&Fit to window", self.render.fit, "F", "Frame the map in the active view", icon="fit")
        self.a_wire = self._act("&Wireframe", self.render.set_wireframe, "W",
                                "Draw the 3D terrain as a wire mesh", checkable=True, icon="wire")
        self.a_cells = self._act("Cell &borders", self.render.set_cell_borders, "B",
                                 "Show the 32-cell codec tiles on the terrain", checkable=True, icon="cells")
        self.a_cells.setChecked(True)
        self.a_reset = self._act("Reset &camera", self.render.reset_camera)
        v.addActions([self.a_fit, self.a_wire, self.a_cells, self.a_reset])
        v.addSeparator()
        self.a_slopes = self._act("Slope classes", lambda on: self.render.set_overlays(slopes=on), "1",
                                  checkable=True, icon="slopes")
        self.a_reach = self._act("Unreachable ground", lambda on: self.render.set_overlays(reach=on), "2",
                                 checkable=True, icon="reach")
        self.a_grid = self._act("Cell grid", lambda on: self.render.set_overlays(grid=on), "3", checkable=True,
                                icon="grid")
        self.a_cont = self._act("Contours (96 u)", lambda on: self.render.set_overlays(contours=on), "4",
                                checkable=True, icon="contours")
        for a, on in ((self.a_slopes, True), (self.a_reach, True), (self.a_grid, True), (self.a_cont, False)):
            a.setChecked(on)
            v.addAction(a)
        v.addSeparator()
        self.a_land = self._act("&Landscape Edit Settings", self.show_landscape, "H",
                                "Show the brush and whole-map tools (H)", checkable=True)
        v.addAction(self.a_land)
        v.addAction(self.d_obj.toggleViewAction())
        v.addAction(self.d_props.toggleViewAction())
        self.a_problems = self.d_prob.toggleViewAction()
        v.addAction(self.a_problems)
        v.addAction(self.d_log.toggleViewAction())
        v.addAction(self.d_hist.toggleViewAction())
        v.addSeparator()
        self.a_renderwin = self._act("Render Window", lambda on: self.render.setVisible(on), checkable=True)
        self.a_cellwin = self._act("Cell View", lambda on: self.cellview.setVisible(on), checkable=True)
        self.a_renderwin.setChecked(True)
        self.a_cellwin.setChecked(True)
        v.addActions([self.a_renderwin, self.a_cellwin])
        v.addSeparator()
        v.addAction(self._act("Reload Donor &Assets", lambda: self.reload_assets(force=True), "Ctrl+R",
                              "Read the donor's textures and prop models out of Gw.dat again"))
        v.addAction(self._act("Reset window &layout", self.reset_layout))
        v.addSeparator()
        tm = v.addMenu("&Theme")
        tgrp = QActionGroup(self)
        for key, label in (("classic", "&Classic (Construction Set)"), ("dark", "&Dark")):
            a = self._act(label, lambda _on=False, k=key: self.set_theme(k), checkable=True)
            a.setChecked(theme.CURRENT == key)
            tgrp.addAction(a)
            tm.addAction(a)

        u = m.addMenu("&Universe")
        self.a_deploy = self._act("&Build && Deploy...", self.deploy, "F5",
                                  "Build the map into Gw.dat and publish it to the world", icon="deploy")
        u.addAction(self.a_deploy)
        u.addAction(self._act("&Maps", self._show_maps))
        u.addAction(self._act("&Terrain", self._show_terrain))
        u.addAction(self._act("T&extures", self._show_textures))
        u.addAction(self._act("&Models", self._show_models))
        u.addAction(self._act("&Props", self._show_props))
        u.addAction(self._act("&NPCs", self._show_npcs))
        u.addSeparator()
        u.addAction(self._act("World &Map...", self.world_map, "Ctrl+M",
                              "The client's area table over its world atlas (read-only)", icon="world"))

        c = m.addMenu("&Character")
        c.addAction(self._act("Spawn &Points", self._show_spawns))
        c.addAction(self._act("&Place Spawn Point", self.place_spawn, None,
                              "Put a spawn point where the Render Window is looking"))
        c.addSeparator()
        self.a_seed = self._act("Make Selected Spawn the &Seed", self._make_seed,
                                "Move the selected spawn to position 0, the flood seed")
        self.a_seed.setEnabled(False)
        c.addAction(self.a_seed)

        g = m.addMenu("&Gameplay")
        g.addAction(self._act("Edit &Scripts...", self.edit_scripts, "Ctrl+E",
                              "The pack's Lua: dialogs, behaviours, skills and the runtime", icon="open"))
        g.addSeparator()
        g.addAction(self._act("&Validate", self._validate, "F7", "Run the slope, reachability and build checks"))
        g.addAction(self.a_problems)

        h = m.addMenu("&Help")
        h.addAction(self._act("&About", self.about))

        tb = QToolBar("Main")
        tb.setObjectName("Main toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(tb)
        tb.addActions([self.a_new, self.a_open, self.a_save])
        tb.addSeparator()
        tb.addActions([undo, redo])
        tb.addSeparator()
        for a in (self.a_sculpt, self.a_select, self.a_flora):
            tb.addAction(a)
        tb.addSeparator()
        tb.addActions([self.a_view3d, self.a_viewtop])
        tb.addSeparator()
        tb.addActions([self.a_slopes, self.a_reach, self.a_grid, self.a_cont])
        tb.addSeparator()
        tb.addActions([self.a_fit, self.a_wire, self.a_cells])
        tb.addSeparator()
        tb.addActions([self.a_deploy])
        tb.addAction(self._act("World Map", self.world_map, icon="world"))

    # ------------------------------------------------------------ state

    def _show_object(self, page):
        self.d_obj.show()
        self.d_obj.raise_()
        page()

    def _show_spawns(self):
        self._show_object(self.objectwin.show_spawns)

    def _show_maps(self):
        self._show_object(self.objectwin.show_maps)

    def _show_terrain(self):
        self._show_object(self.objectwin.show_terrain)

    def _show_textures(self):
        self._show_object(self.objectwin.show_textures)

    def _show_models(self):
        self._show_object(self.objectwin.show_models)

    def _make_seed(self):
        p, i = self.doc.project, self.doc.selected
        if p is None or not (0 < i < len(p.spawns)):
            return
        before = copy_spawns(p)
        after = [before[i]] + [s for k, s in enumerate(before) if k != i]
        self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Make seed"))
        self.doc.select(0)

    def show_landscape(self, on):
        if on:
            self.d_land.setMaximumHeight(16777215)
            self.d_land.setFloating(True)
            self.d_land.resize(430, 600)
            self.d_land.move(self.mapToGlobal(QPoint(24, 80)))
            self.d_land.show()
            self.d_land.raise_()
            self.set_tool("sculpt")
        else:
            self.d_land.hide()

    def _landscape_visible(self, on):
        if self.a_land.isChecked() != bool(on):
            self.a_land.blockSignals(True)
            self.a_land.setChecked(bool(on))
            self.a_land.blockSignals(False)

    def set_theme(self, key):
        theme.apply(QApplication.instance(), key)
        self.prefs["theme"] = key
        paths.save_prefs(self.prefs)
        self.logp.apply_theme()
        self.st_cursor.setFont(theme.mono_font())
        for w in (self.render, self.render.view2d, self.render.view3d, self.cellview,
                  self.objectwin, self.mappanel, self.problems):
            w.update()

    def set_tool(self, key):
        if key not in ("sculpt", "select", "flora"):
            key = "select"
        p = self.doc.project
        if key == "sculpt" and p is not None and getattr(p, "stock", False):
            self.logp.log("a game map's ground and textures are not reshaped here; "
                          "Select (props, NPCs) and Flora are the tools for it")
            key = "select"
        self.render.set_tool(key)
        a = {"sculpt": self.a_sculpt, "select": self.a_select, "flora": self.a_flora}[key]
        if not a.isChecked():
            a.setChecked(True)
        self.brushes.set_mode(key)

    def place_spawn(self):
        """A new spawn point where the Render Window is looking, selected, in
        Select mode - spawns are objects you then move like a prop."""
        p = self.doc.project
        if p is None:
            return
        gx, gy = self.render.focus_grid()
        gx = min(max(int(round(gx)), 0), p.dim_x - 1)
        gy = min(max(int(round(gy)), 0), p.dim_y - 1)
        x, y = p.cell_centre(gx, gy)
        before = copy_spawns(p)
        after = copy_spawns(p)
        after.append(Spawn(x, y, None))
        self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Add spawn"))
        self.set_tool("select")
        self.doc.select(len(after) - 1)

    def _cursor(self, text):
        self.st_cursor.setText(text)

    def _camera_readout(self):
        try:
            if self.render.view3d.underMouse() or self.render.view2d.underMouse():
                return
        except RuntimeError:
            return
        text = self.render.camera_text()
        if text:
            self.st_cursor.setText(text)

    # ------------------------------------------------------------ donor assets

    def _queue_ground(self):
        p = self.doc.project
        if p is not None:
            self._ground_timer.start()

    def _textures_changed_ground(self, rect=None):
        """A texture stroke repaints its own cells; only undo/redo rebuilds all.

        The blend used to be thrown away on every dab so the per-tile fallback
        showed through - a flash of unblended, sharper textures that read as the
        window changing resolution. The blended image is now edited in place."""
        p, a, s = self.doc.project, self.assets, self._ground_surface
        if (rect is not None and s is not None and p is not None and a is not None
                and self.texture_images and a.table_a):
            try:
                terrain_texture.update_surface(s, p, self.texture_images, a.table_a, rect)
            except Exception as exc:                                 # noqa: BLE001
                self.logp.log(f"terrain blend: {type(exc).__name__}: {exc}")
                self._ground_timer.start()
                return
            self.render.update_ground(s, rect)
            self._ground_key = self._ground_signature()
            return
        self._ground_timer.start()

    def _ground_signature(self):
        p = self.doc.project
        if p is None:
            return None
        return (p.path or id(p), int(p.donor_file_id), p.dim_x, p.dim_y,
                hash(tuple(p.tiles or ())), id(self.assets), len(self.texture_images),
                self.render.view3d.needs_ground_image)

    def _rebuild_ground(self):
        p = self.doc.project
        a = self.assets
        images = self.texture_images
        if p is None or a is None or not images or not a.table_a:
            self._ground_surface = None
            self.render.clear_ground()
            return
        if len(images) < len(a.table_a):
            # the donor read hands over the base texture first and the rest
            # later; blending with one texture threw IndexError and left the
            # Render Window untextured. Wait for the whole table.
            return
        key = self._ground_signature()
        if self._ground_surface is not None and key == self._ground_key:
            return
        if self._ground_job is not None:
            self._ground_again = True           # rebuild once the running blend lands
            return
        snap = _GroundSnapshot(p)
        self._ground_job = (key, p)
        self._ground_steps = terrain_texture.build_surface_steps(
            snap, list(images), a.table_a,
            with_image=self.render.view3d.needs_ground_image)
        self._ground_rows = p.dim_y
        self.loading.begin("ground", "Blending the terrain textures ...", total=p.dim_y)
        QTimer.singleShot(0, self._ground_step)

    def _ground_step(self):
        """Advance the blend for ~25 ms, then give the event loop a turn."""
        steps = self._ground_steps
        if steps is None:
            return
        deadline = time.monotonic() + 0.025
        try:
            row = 0
            while time.monotonic() < deadline:
                row = next(steps)
            self.loading.step("ground", row + 1)
        except StopIteration as done:
            self._ground_steps = None
            self._ground_built(done.value)
            return
        except Exception as exc:                                     # noqa: BLE001
            self._ground_steps = None
            self._ground_failed(f"{type(exc).__name__}: {exc}")
            return
        QTimer.singleShot(0, self._ground_step)

    def _ground_done(self, surface, msg):
        """The running blend is over: keep its result if it is still wanted,
        then start the next one if the map changed while it ran."""
        key, p = self._ground_job
        self._ground_job = None
        again, self._ground_again = self._ground_again, False
        current = p is self.doc.project and key == self._ground_signature()
        if msg:
            self.logp.log(f"terrain blend: {msg}")
        if current:
            self._ground_key = key
            self._ground_surface = surface
            if surface is None:
                self.render.clear_ground()
            else:
                self.render.set_ground_surface(surface)
        if not current or again:
            self._rebuild_ground()
        if self._ground_job is None:
            self.loading.end("ground")

    def _ground_built(self, surface):
        self._ground_done(surface, "")

    def _ground_failed(self, msg):
        self._ground_done(None, msg)

    def _queue_assets(self):
        QTimer.singleShot(0, self.reload_assets)

    def reload_assets(self, force: bool = False):
        p = self.doc.project
        if p is None:
            self.objectwin.set_assets(None, "No map open.")
            self.st_assets.setText("Assets: no map")
            return
        dat = self.prefs.get("archive", "")
        if not dat:
            self.objectwin.set_assets(None, "No Gw.dat is chosen. Pick the archive in "
                                            "Universe > Build & Deploy, then View > Reload Donor Assets.")
            self.st_assets.setText("Assets: no archive")
            return
        if not force and self.assets is not None:
            if (int(self.assets.donor_file_id) == int(p.donor_file_id) and
                    os.path.normcase(os.path.abspath(self.assets.archive)) ==
                    os.path.normcase(os.path.abspath(dat))):
                return
        if self._asset_thread is not None and self._asset_thread.isRunning():
            return
        self.st_assets.setText("Assets: reading Gw.dat ...")
        self.loading.begin("assets", "Reading the donor map from Gw.dat ...")
        self.logp.log(f"reading donor 0x{p.donor_file_id:X} from {dat}")
        th = QThread(self)
        job = AssetJob(dat, p.donor_file_id)
        job.moveToThread(th)
        th.started.connect(job.run)
        job.log.connect(self.logp.log)
        job.progress.connect(self._assets_progress)
        job.textures.connect(self._assets_textures)
        job.done.connect(self._assets_ready)
        job.failed.connect(self._assets_failed)
        job.done.connect(th.quit)
        job.failed.connect(th.quit)
        th.finished.connect(job.deleteLater)
        th.finished.connect(self._asset_thread_done)
        self._asset_thread, self._asset_job = th, job
        th.start()

    def _assets_progress(self, done, total, text):
        self.loading.step("assets", done, total, text)

    def _asset_thread_done(self):
        self._asset_thread = None
        self._asset_job = None
        if self._asset_stale:
            self._asset_stale = False
            QTimer.singleShot(0, self._reload_stale_assets)

    def _reload_stale_assets(self):
        self.reload_assets()
        if self._asset_thread is None:
            self.loading.end("assets")

    def _assets_current(self, a):
        p = self.doc.project
        if p is None:
            return False
        dat = self.prefs.get("archive", "")
        if not dat:
            return False
        return (os.path.normcase(os.path.abspath(a.archive)) ==
                os.path.normcase(os.path.abspath(dat)) and int(a.donor_file_id) == int(p.donor_file_id))

    def _assets_textures(self, a):
        if not self._assets_current(a):
            self._asset_stale = True
            return
        self.assets = a
        self._set_tile_types(a)
        images = []
        for t in a.textures:
            images.append(qimage(t))
        self.texture_images = images
        self.objectwin.textures.set_assets(a)
        self.objectwin.models.note.setText("Scanning the donor's prop models ...")
        self.objectwin.models.table.setRowCount(0)
        self.render.set_textures(images)
        n = sum(1 for t in a.textures if t.png)
        self.st_assets.setText(f"Assets: {n} tex; scanning models ...")
        self._queue_ground()

    def _assets_ready(self, a):
        self._in_use_logged = False
        if not self._assets_current(a):
            self._asset_stale = True
            return
        self.assets = a
        self._set_tile_types(a)
        if not self.texture_images:
            images = []
            for t in a.textures:
                images.append(qimage(t))
            self.texture_images = images
            self.render.set_textures(images)
        self.loading.end("assets")
        self.objectwin.set_assets(a)
        n = sum(1 for t in a.textures if t.png)
        self.st_assets.setText(f"Assets: {n} tex / {len(a.models)} models")
        self.logp.log(f"donor assets: {n} texture(s), {len(a.models)} model(s), "
                      f"{a.props_measured} measured")
        self._queue_ground()
        self._ensure_prop_geometry()

    def _assets_failed(self, msg):
        self.loading.end("assets")
        if msg.startswith(IN_USE):
            # not a real failure: try again until the game lets go of the file
            self.objectwin.set_assets(None, IN_USE_TEXT)
            self.st_assets.setText("Assets: Gw.dat in use, waiting ...")
            if not getattr(self, "_in_use_logged", False):
                self.logp.log("donor assets: " + IN_USE_TEXT)
                self._in_use_logged = True
            QTimer.singleShot(IN_USE_RETRY_MS, lambda: self.reload_assets(force=True))
            return
        self._in_use_logged = False
        self.assets = None
        self._set_tile_types(None)
        self.texture_images = []
        self._ground_surface = None
        self._ground_key = None
        self.objectwin.set_assets(None, msg)
        self.render.set_textures([])
        self.render.clear_ground()
        self.st_assets.setText("Assets: unavailable")
        self.logp.log("donor assets: " + msg)

    def _set_tile_types(self, a):
        """Give the document the donor's tile-type table, so Problems can list
        cells the client cannot draw and the brushes can avoid making them."""
        types = list(a.table_a) if a is not None and a.table_a else None
        if types != self.doc.tile_types:
            self.doc.tile_types = types
            self._repair_texture_seams(automatic=True)
            self.doc.analyse()

    def _repair_texture_seams(self, automatic=False) -> int:
        """Make the map drawable: no cell may need a fourth texture layer.

        Runs by itself whenever the texture table becomes known for an open map,
        so a map painted before the brushes enforced the limit is repaired on
        open (one undoable step) rather than crashing the client later."""
        p = self.doc.project
        if p is None or not p.tiles or not self.doc.tile_types or getattr(p, "stock", False):
            return 0                    # a game map's textures are ArenaNet's and are not rewritten
        a = self.assets
        if a is None or int(a.donor_file_id) != int(p.donor_file_id):
            return 0                    # the loaded table belongs to another donor
        fixed, changed = texlayers.fix_overflows(p.tiles, p.dim_x, p.dim_y, self.doc.tile_types)
        if not changed:
            return 0
        self.doc.undo.push(TilesCommand(self.doc, copy_tiles(p), fixed,
                                        "Fix texture seams" + (" (automatic)" if automatic else "")))
        self.logp.log(f"texture seams: repainted {len(changed)} corner(s) where four textures "
                      f"met (the client draws three per cell): " +
                      ", ".join(f"{x},{y}" for x, y in changed[:12]) +
                      (" ..." if len(changed) > 12 else ""))
        return len(changed)

    def _textures_undone(self, rect):
        if rect is None and self.doc.tile_types:      # undo/redo/fix, not a brush dab
            self.doc.analyse()

    def _flora_selected(self, name):
        self.render.view3d.set_flora_type(name)
        self.brushes.set_flora(name)
        self.set_tool("flora")
        self.logp.log(f"flora brush: {name}")

    def _show_flora(self):
        self._show_object(self.objectwin.show_flora)

    def _texture_selected(self, tile):
        self.render.set_paint_texture(tile)
        self.brushes.set_texture(tile)
        self.logp.log(f"texture brush: tile {tile}")

    # ------------------------------------------------------------ placed props

    def _place_model(self, payload):
        p = self.doc.project
        if p is None:
            return
        x = float(payload["x"])
        y = float(payload["y"])
        z = float(payload["z"])
        x, y, z = p.snap_world(x, y)
        before = copy_props(p)
        q = PlacedProp(file_id=int(payload["file_id"]), model=int(payload.get("model", 0)),
                       x=x, y=y, z=z,
                       rot=(0, 0, 0), scale=0x7F, flags=0, outline=[], grid_locked=True)
        after = before + [q]
        self.doc.undo.push(PropsCommand(self.doc, before, after, "Place prop"))
        self.doc.select_prop(len(after) - 1)
        self.logp.log(f"placed model 0x{q.file_id:X} at {q.x:,.0f}, {q.y:,.0f}, {q.z:,.0f}")

    # ------------------------------------------------------------ NPCs

    def _load_npc_types(self):
        """The world pack's NPC types for the NPCs tab and the Render Window's colours."""
        from .script_window import default_pack
        page = self.objectwin.npcs
        page.pack_source = lambda: default_pack(self.prefs)
        page.types_loaded.connect(self._npc_types_loaded)
        page.reload()

    def _npc_types_loaded(self):
        page = self.objectwin.npcs
        view = self.render.view3d
        view.set_npc_hostile(t.id for t in page.types if t.hostile)
        view.set_npc_bodies({t.id: (t.bodies, t.scale) for t in page.types})
        self._npcs_changed()

    def _npcs_changed(self):
        """Load the meshes of NPC types just placed (with the prop models), else redraw."""
        view = self.render.view3d
        missing = view.npc_mesh_ids() - set(self.model_geometry) - self._models_failed_ids()
        if missing and self.doc.project is not None:
            self._ensure_prop_geometry()
        else:
            view.update()

    def _show_npcs(self):
        self._show_object(self.objectwin.show_npcs)

    def _place_npc(self, payload):
        from .. import npcs as npcs_mod
        from ..model import PlacedNpc
        p = self.doc.project
        if p is None:
            return
        before = copy_npcs(p)
        n = PlacedNpc(id=npcs_mod.next_id(p), definition=str(payload["definition"]),
                      x=float(payload["x"]), y=float(payload["y"]))
        after = before + [n]
        self.doc.undo.push(NpcsCommand(self.doc, before, after, f"Place {n.id}"))
        self.doc.select_npc(len(after) - 1)
        t = self.objectwin.npcs.type_by_id(n.definition)
        self.logp.log(f"placed NPC {n.id} ({t.label if t else n.definition}) at {n.x:,.0f}, {n.y:,.0f}")

    def _npc_properties(self, i):
        from .npc_dialog import NpcDialog
        p = self.doc.project
        if p is None or not (0 <= i < len(p.npcs)):
            return
        self.doc.select_npc(i)
        page = self.objectwin.npcs
        NpcDialog(self, self.doc, i, page.types, page.behaviors).exec()

    def _props_changed(self):
        self._ensure_prop_geometry()
        self._counts()

    def _prop_properties(self, i):
        from .prop_properties import PropPropertiesDialog
        p = self.doc.project
        if p is None or not (0 <= i < len(p.props)):
            return
        q = p.props[i]
        name = f"model {q.model}  0x{q.file_id:X}"
        self.doc.select_prop(i)
        PropPropertiesDialog(self, self.doc, i, name).exec()

    def _ensure_prop_geometry(self):
        p = self.doc.project
        if p is None:
            self.render.view3d.set_props([], {})
            return
        dat = self.prefs.get("archive", "")
        wanted = {q.file_id for q in p.props} | self.render.view3d.npc_mesh_ids()
        missing = sorted(wanted - set(self.model_geometry) - self._models_failed_ids())
        if dat and missing:
            if self._models_job is not None:
                self._models_again = True       # load the rest when this batch lands
            else:
                self._models_job = dat
                n = len(missing)
                self.loading.begin("models", f"Loading placed models (0 of {n}) ...", total=n)
                self._spawn(lambda report: self._load_models(dat, missing, report),
                            self._models_loaded, self._models_load_failed,
                            self._models_progress)
        self.render.view3d.set_props(p.props, self.model_geometry)

    @staticmethod
    def _load_models(dat, file_ids, report):
        """Worker thread: decode each model; failures are reported, not raised."""
        got, errors = {}, {}
        n = len(file_ids)
        for k, fid in enumerate(file_ids):
            report(k, n, f"Loading placed models ({k + 1} of {n}) ...")
            try:
                got[fid] = assets.load_model_assets(dat, fid)
            except Exception as exc:                                 # noqa: BLE001
                if _in_use(exc):
                    return dat, got, {}, True        # try the rest again later
                errors[fid] = f"{type(exc).__name__}: {exc}"
        return dat, got, errors, False

    def _models_progress(self, done, total, text):
        self.loading.step("models", done, total, text)

    def _models_failed_ids(self):
        return set(getattr(self, "_model_errors", {}))

    def _models_loaded(self, result):
        dat, got, errors, locked = result
        self._models_job = None
        if dat == self.prefs.get("archive", ""):
            self.model_geometry.update(got)
            if not hasattr(self, "_model_errors"):
                self._model_errors = {}
            for fid, msg in errors.items():
                self._model_errors[fid] = msg
                self.logp.log(f"model 0x{fid:X}: {msg}")
        if locked:
            # wait for the game to release Gw.dat instead of retrying at once
            self.logp.log("placed models: " + IN_USE_TEXT)
            self._models_again = False
            self.render.view3d.set_props(self.doc.project.props if self.doc.project else [],
                                         self.model_geometry)
            self.loading.end("models")
            QTimer.singleShot(IN_USE_RETRY_MS, self._ensure_prop_geometry)
            return
        again, self._models_again = self._models_again, False
        self._ensure_prop_geometry()
        if again and self._models_job is None:
            self._ensure_prop_geometry()
        if self._models_job is None:
            self.loading.end("models")

    def _models_load_failed(self, msg):
        self._models_job = None
        self._models_again = False
        self.logp.log(f"placed models: {msg}")
        self.loading.end("models")

    def _show_props(self):
        self._show_object(self.objectwin.show_props)

    def _clean_changed(self, _c):
        self._title()

    def _title(self):
        try:
            p = self.doc.project
            clean = self.doc.undo.isClean()
        except RuntimeError:
            return
        if p is None:
            self.setWindowTitle(APP_NAME)
            self.st_map.setText("")
            return
        star = " *" if not clean else ""
        where = os.path.basename(p.path) if p.path else "unsaved"
        self.setWindowTitle(f"{APP_NAME} - [{where}]{star}")
        fid = "no file id" if p.file_id is None else f"file 0x{p.file_id:X}"
        self.st_map.setText(f"map {p.map_id}  |  {p.dim_x}x{p.dim_y}  |  {fid}")

    def _counts(self):
        p = self.doc.project
        if p is None:
            self.st_counts.setText("")
            self.st_sel.setText("")
            return
        faces = max(0, (p.dim_x - 1) * (p.dim_y - 1))
        objects = len(p.spawns) + len(p.props) + len(p.npcs)
        self.st_counts.setText(f"Objects: {objects}   Faces: {faces:,}   Cells: {p.dim_x}x{p.dim_y}")

    def _selection(self, *_args):
        p = self.doc.project
        i = self.doc.selected
        pi = self.doc.selected_prop
        ni = self.doc.selected_npc
        if p is not None and 0 <= ni < len(p.npcs):
            self.st_sel.setText(f"[Sel Obj: {p.npcs[ni].id}]")
        elif p is not None and 0 <= pi < len(p.props):
            self.st_sel.setText(f"[Sel Obj: prop_{pi}]")
        elif p is not None and 0 <= i < len(p.spawns):
            seed = " (seed)" if i == 0 else ""
            self.st_sel.setText(f"[Sel Obj: spawn_{i}{seed}]")
        else:
            self.st_sel.setText("[Sel Obj: none]")
        if hasattr(self, "a_seed"):
            self.a_seed.setEnabled(p is not None and 0 < i < len(p.spawns))

    def _recent(self):
        self.recent_menu.clear()
        for path in self.prefs.get("recent", [])[:10]:
            self.recent_menu.addAction(path, lambda p=path: self.open_map(p))
        self.recent_menu.setEnabled(bool(self.prefs.get("recent")))

    def _remember(self, path):
        r = [x for x in self.prefs.get("recent", []) if os.path.normcase(x) != os.path.normcase(path)]
        self.prefs["recent"] = [path] + r[:9]
        paths.save_prefs(self.prefs)
        self._recent()

    def startup(self):
        """Open the last map (ui/app.py calls this before showing the window)."""
        self._startup()

    def _spawn(self, fn, on_done, on_failed, on_progress=None):
        """Run `fn(report)` on a worker thread; `report(done, total, text)` reaches
        `on_progress`, and `on_done(result)` / `on_failed(msg)` are called, on the
        GUI thread."""
        self._threads = [t for t in self._threads if not t.isFinished()]
        th = QThread(self)
        job = _Call(fn)
        job.moveToThread(th)
        th.started.connect(job.run)
        job.done.connect(on_done)
        job.failed.connect(on_failed)
        if on_progress is not None:
            job.progress.connect(on_progress)
        job.done.connect(th.quit)
        job.failed.connect(th.quit)
        th.finished.connect(job.deleteLater)
        self._threads.append(th)
        th._job = job                   # keep the Python wrapper alive with the thread
        th.start()
        return th

    def _startup(self):
        recent = [p for p in self.prefs.get("recent", []) if os.path.exists(p)]
        if recent:
            self.open_map(recent[0])
        else:
            self.doc.set_project(MapProject.new("New Map", 64, 64))
            self.doc.project.heights = terrain.gen_hills(self.doc.project)
            self.doc.set_project(self.doc.project)

    # ------------------------------------------------------------ files

    def _maybe_save(self) -> bool:
        if self.doc.project is None or self.doc.undo.isClean():
            return True
        r = QMessageBox.question(self, "Unsaved changes", f"Save {self.doc.project.name} first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save()
        return True

    def new_map(self):
        if not self._maybe_save():
            return
        dlg = NewMapDialog(self)
        if dlg.exec():
            p = dlg.project()
            p.path = os.path.join(paths.projects_dir(), p.slug + ".kmap.json")
            if os.path.exists(p.path):
                p.path = None
            self.doc.set_project(p)
            self.doc.undo.resetClean()
            self.objectwin.maps.refresh()
            self.logp.log(f"new map '{p.name}', {p.dim_x}x{p.dim_y}, client map id {p.map_id}")

    def open_map(self, path=None):
        if not self._maybe_save():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open map", paths.projects_dir(), FILTER)
            if not path:
                return
        try:
            p = MapProject.load(path)
        except Exception as exc:                                     # noqa: BLE001
            QMessageBox.warning(self, "Open", f"Could not open {path}:\n{exc}")
            return
        self.doc.set_project(p)
        self._repair_texture_seams(automatic=True)
        self._remember(path)
        self.objectwin.maps.refresh()
        self.logp.log(f"opened {path}")

    def open_game_map(self):
        """File > Open Game Map: one of the pack's maps, read out of Gw.dat (kryta_cs/stock.py)."""
        from .. import kryta
        from ..model import slugify
        from .game_map_dialog import GameMapDialog
        from .script_window import default_pack
        if not self._maybe_save():
            return
        repo = paths.REPO_ROOT
        if not repo:
            QMessageBox.warning(self, "Open Game Map", "No Kryta checkout was found around the editor, "
                                "so there is no list of maps.")
            return
        dat = self.prefs.get("archive") or kryta.server_archive(repo) or ""
        if not dat or not os.path.isfile(dat):
            QMessageBox.warning(self, "Open Game Map", "Choose your Gw.dat first (World > Build & "
                                "Deploy, the archive field).")
            return
        dlg = GameMapDialog(self, kryta.read_catalogue(repo))
        if not dlg.exec() or dlg.chosen() is None:
            return
        r = dlg.chosen()
        title = r.name or f"Map {r.map_id}"
        path = os.path.join(paths.projects_dir(), f"game-{r.map_id}-{slugify(title)}.kmap.json")
        if os.path.exists(path):
            ans = QMessageBox.question(
                self, "Open Game Map",
                f"You have saved edits of {title}:\n{path}\n\nOpen them? (No starts again from "
                f"the map as it ships.)", QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                return
            if ans == QMessageBox.Yes:
                self.open_map(path)
                return
            path = None
        pack = default_pack(self.prefs)
        self.loading.begin("game", f"Reading {title} out of Gw.dat ...")
        self.logp.log(f"opening game map {r.map_id} {title} (file 0x{r.file_id:X})")
        self._game_open = (title, path)

        def job(_report):
            from .. import stock
            return stock.open_map(dat, r.file_id, r.map_id, title, r.map_type or 1, pack)

        # The handlers are this window's own methods: a signal from the worker thread then runs
        # them on the GUI thread. A plain local function would run on the worker, and a message
        # box opened there takes the whole editor down (2026-11-25, Diessa Lowlands).
        self._spawn(job, self._game_map_opened, self._game_map_failed)

    def _game_map_opened(self, p):
        title, path = getattr(self, "_game_open", (p.name, None))
        self.loading.end("game")
        p.path = path
        self.doc.set_project(p)
        self.doc.undo.resetClean()
        self.objectwin.maps.refresh()
        self.set_tool("select")
        self.logp.log(f"{title}: {p.dim_x}x{p.dim_y} cells, {len(p.props)} prop(s), "
                      f"{len(p.flora)} flora area(s), {len(p.npcs)} NPC(s). Save to keep your "
                      f"edits; Build & Deploy writes them.")

    def _game_map_failed(self, msg):
        title, _path = getattr(self, "_game_open", ("the map", None))
        self.loading.end("game")
        self.logp.log(f"open game map: {msg}")
        QMessageBox.warning(self, "Open Game Map", f"Could not open {title}:\n\n{msg}")

    def save(self) -> bool:
        p = self.doc.project
        if p is None:
            return False
        if not p.path:
            return self.save_as()
        p.save()
        self.doc.undo.setClean()
        self._remember(p.path)
        self.objectwin.maps.refresh()
        self.logp.log(f"saved {p.path}")
        self._title()
        return True

    def save_as(self) -> bool:
        p = self.doc.project
        if p is None:
            return False
        start = p.path or os.path.join(paths.projects_dir(), p.slug + ".kmap.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save map", start, FILTER)
        if not path:
            return False
        if not path.endswith(".json"):
            path += ".kmap.json"
        p.path = path
        return self.save()

    # ------------------------------------------------------------ world

    def _validate(self):
        self.doc.analyse()
        self.d_prob.show()
        self.d_prob.raise_()
        errs = sum(f.level == "error" for f in self.doc.findings)
        self.logp.log(f"validate: {errs} error(s), {len(self.doc.findings) - errs} other finding(s)")

    def deploy(self):
        if self.doc.project is None:
            return
        if not self.doc.project.path or not self.doc.undo.isClean():
            if not self.save():
                return
        dlg = DeployWindow(self.doc, self.prefs, self)
        dlg.archive_chosen.connect(lambda _p: paths.save_prefs(self.prefs))
        dlg.exec()
        if self.doc.project.path:
            self.doc.project.save()
            self.doc.undo.setClean()
        self._title()
        self.reload_assets(force=True)

    def world_map(self):
        """World > World Map: one read-only window, kept and raised on the next open."""
        from .worldmap_window import WorldMapWindow
        win = getattr(self, "_worldmap", None)
        if win is None:
            win = self._worldmap = WorldMapWindow(self.prefs, None)
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    def edit_scripts(self, rel=None, line=None):
        """Gameplay > Edit Scripts: one Script Edit window, kept and raised on the next open.
        `rel` (pack-relative) opens that file, at `line` if given."""
        from .script_window import ScriptWindow
        win = getattr(self, "_scripts", None)
        if win is None:
            win = self._scripts = ScriptWindow(self.prefs, None)
        win.show()
        win.raise_()
        win.activateWindow()
        if isinstance(rel, str) and rel:
            win.open_file(rel, line)
        return win

    def _goto(self, x, y):
        p = self.doc.project
        if p is None:
            return
        gx, gy = p.world_to_cell(x, y)
        self.render.center_on_grid(gx, gy)

    def reset_layout(self):
        for d in (self.d_land, self.d_obj, self.d_props, self.d_prob, self.d_log, self.d_hist):
            self.addDockWidget(Qt.LeftDockWidgetArea, d)
            d.setFloating(False)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.d_land)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.d_obj)
        self.splitDockWidget(self.d_land, self.d_obj, Qt.Vertical)
        self.addDockWidget(Qt.RightDockWidgetArea, self.d_props)
        self.addDockWidget(Qt.RightDockWidgetArea, self.d_prob)
        self.splitDockWidget(self.d_props, self.d_prob, Qt.Vertical)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.d_log)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.d_hist)
        self.tabifyDockWidget(self.d_log, self.d_hist)
        self.d_log.raise_()
        self.d_obj.show()
        for d in (self.d_land, self.d_props, self.d_prob, self.d_log, self.d_hist):
            d.hide()
        self._landscape_visible(False)
        self._default_sizes()
        self.render.set_mode("3d")
        self.a_view3d.setChecked(True)
        self.a_renderwin.setChecked(True)
        self.a_cellwin.setChecked(True)
        self.centre.setSizes([max(420, int(self.height() * 0.66)), max(170, int(self.height() * 0.27))])

    def about(self):
        QMessageBox.about(self, APP_NAME, (
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>The world editor for the Kryta server, laid out like the Morrowind Construction Set: "
            "the Render Window is the 3D surface you sculpt, the Cell View is the overview under it, "
            "and the Object Window keeps the map's spawn points, projects and terrain record.</p>"
            "<p>Its first module authors new maps and builds them into Gw.dat under a file id of their own. "
            "The archive writer, terrain codec and the slope rules are "
            "<a href='https://github.com/GameJawnsInc/Rurik'>Rurik</a>'s (MIT), vendored under "
            "third_party/rurik. No ArenaNet data ships with this tool: the look of a map is borrowed from "
            "your own Gw.dat at build time.</p>"))

    def closeEvent(self, ev):
        scripts = getattr(self, "_scripts", None)
        if scripts is not None and not scripts._close_buffers():
            ev.ignore()
            return
        if not self._maybe_save():
            ev.ignore()
            return
        if scripts is not None:
            scripts.close()
        # Close at once: hide the popup, stop the terrain blend, and tell the
        # background readers to stop at their next texture or model instead of
        # waiting for a whole donor scan to finish.
        self.loading.stop()
        self._ground_steps = None
        if self._asset_job is not None:
            self._asset_job.cancelled = True
        for th in self._threads:
            job = getattr(th, "_job", None)
            if job is not None:
                job.cancelled = True
        st = QSettings("Kryta", "ConstructionSet")
        st.setValue("geometry", self.saveGeometry())
        st.setValue("state", self.saveState())
        st.setValue("centre", self.centre.saveState())
        st.setValue("layout", LAYOUT_VERSION)
        paths.save_prefs(self.prefs)
        self.hide()
        if self._asset_thread is not None and self._asset_thread.isRunning():
            self._asset_thread.quit()
            self._asset_thread.wait(5000)
        for th in self._threads:
            if th.isRunning():
                th.quit()
                th.wait(5000)
        ev.accept()


def main_entry(argv=None) -> int:
    from .app import main
    return main(argv)
