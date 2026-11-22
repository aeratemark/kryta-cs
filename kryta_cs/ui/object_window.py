"""Object Window: the CS's record browser, the big pane on the left.

The CS put a row of category tabs over a record list. Ours has three records:
the map's spawn points, the map projects on disk, and the terrain record (size,
slope set, reachability, findings). The tabs have no icons and the list fills the
pane, as in the Construction Set."""

from __future__ import annotations

import datetime
import glob
import json
import os

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QMenu, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import paths, terrain
from ..model_names import name_for
from .model_properties import ModelPropertiesDialog
from .images import qimage
from .panels import SpawnPanel
from .theme import COLORS


class MapsPage(QWidget):
    open_requested = Signal(str)
    new_requested = Signal()

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Map", "Size", "ID", "Modified"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._open_item)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.setSpacing(3)
        openb = QPushButton("Open")
        openb.clicked.connect(self._open_selected)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        new = QPushButton("New map...")
        new.clicked.connect(self.new_requested.emit)
        for b in (openb, refresh, new):
            row.addWidget(b)
        lay.addLayout(row)
        doc.project_changed.connect(self.refresh)

    def refresh(self):
        current = self.doc.project.path if self.doc.project else None
        rows = []
        for path in glob.glob(os.path.join(paths.projects_dir(), "*.kmap.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                name = str(d.get("name", os.path.basename(path)))
                dx, dy = d.get("dim_x", 0), d.get("dim_y", 0)
                mid = d.get("map_id", "")
                when = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
                rows.append((path, name, f"{dx} x {dy}", str(mid), when))
            except (OSError, ValueError):
                rows.append((path, os.path.basename(path), "?", "?", "?"))
        rows.sort(key=lambda r: r[4], reverse=True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        mono = QFont(self.font())
        mono.setFamily("Courier New" if "light" in COLORS else "Consolas")
        for i, (path, name, size, mid, when) in enumerate(rows):
            a = QTableWidgetItem(name)
            a.setData(Qt.UserRole, path)
            if current and os.path.normcase(os.path.abspath(current)) == os.path.normcase(os.path.abspath(path)):
                f = QFont(self.font())
                f.setBold(True)
                a.setFont(f)
                a.setToolTip("the open map")
            for col, text in ((1, size), (2, mid), (3, when)):
                it = QTableWidgetItem(text)
                it.setForeground(QColor(COLORS["muted"]))
                it.setFont(mono)
                self.table.setItem(i, col, it)
            self.table.setItem(i, 0, a)
        if current:
            for i in range(self.table.rowCount()):
                it = self.table.item(i, 0)
                if it and os.path.normcase(os.path.abspath(it.data(Qt.UserRole))) == \
                        os.path.normcase(os.path.abspath(current)):
                    self.table.selectRow(i)
                    break

    def _open_item(self, item):
        row_item = self.table.item(item.row(), 0)
        path = row_item.data(Qt.UserRole) if row_item is not None else None
        if path:
            self.open_requested.emit(path)

    def _open_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            item = self.table.item(rows[0].row(), 0)
            if item:
                self.open_requested.emit(item.data(Qt.UserRole))


class TerrainPage(QWidget):
    """The terrain record: the same facts the CS put in its cell/climate pages."""

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Property", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table, 1)
        doc.project_changed.connect(self.refresh)
        doc.settings_changed.connect(self.refresh)
        doc.analysis_changed.connect(self.refresh)
        doc.spawns_changed.connect(self.refresh)

    def refresh(self):
        p = self.doc.project
        self.table.setRowCount(0)
        if p is None:
            return
        cut, name = terrain.slope_set(p.map_flags)
        reach = len(self.doc.reach) if self.doc.reach else 0
        errors = sum(f.level == "error" for f in self.doc.findings)
        warns = sum(f.level == "warning" for f in self.doc.findings)
        rows = [
            ("Name", p.name),
            ("Client map id", str(p.map_id)),
            ("Type", "Outpost" if p.map_type == 0 else "Explorable"),
            ("Size", f"{p.dim_x} x {p.dim_y} cells ({p.dim_x * 96:,} x {p.dim_y * 96:,} u)"),
            ("Slope set", f"{name}, walk cut {cut:.0f} deg"),
            ("Ground under water", "walkable" if p.map_flags & 1 else "not walkable"),
            ("Flood seed", "%.0f, %.0f" % p.flood_seed()),
            ("Reachable cells", f"{reach:,} of {p.dim_x * p.dim_y:,}"),
            ("Build findings", f"{errors} error(s), {warns} warning(s)"),
            ("Gw.dat file id", "not installed" if p.file_id is None else f"0x{p.file_id:X}"),
        ]
        self.table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(k))
            self.table.setItem(i, 1, QTableWidgetItem(v))


class TexturesPage(QWidget):
    texture_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.assets = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.note = QLabel("No donor assets loaded. Choose Gw.dat in Build & Deploy, then "
                           "View > Reload Donor Assets.")
        self.note.setWordWrap(True)
        self.note.setProperty("muted", True)
        lay.addWidget(self.note)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Tile", "File ID", "Used", "Size", "Status"])
        self.table.verticalHeader().setVisible(False)
        for c in range(5):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._picked)
        self.preview = QLabel("no texture")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFrameShape(QFrame.Panel)
        self.preview.setFrameShadow(QFrame.Sunken)
        self.preview.setMinimumSize(170, 170)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.preview)
        split.setSizes([480, 190])
        lay.addWidget(split, 1)
        self._textures = []
        self._sync = False

    def selected_tile(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return None if item is None else int(item.data(Qt.UserRole))

    def _show_preview(self, tile):
        tex = next((t for t in self._textures if t.tile == tile), None)
        if tex is None or not tex.png:
            self.preview.clear()
            self.preview.setText("not decoded" if tex is not None else "no texture")
            return False
        img = qimage(tex)
        if img is None or img.isNull():
            return False
        pix = QPixmap.fromImage(img)
        self.preview.setPixmap(pix.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.preview.setText("")
        return True

    def set_assets(self, assets, error=None):
        current = self.selected_tile()
        self.assets = assets
        self._textures = []
        self._sync = True
        self.table.setRowCount(0)
        self.preview.clear()
        self.preview.setText("no texture")
        if error:
            self.note.setText(error)
            self._sync = False
            return
        if assets is None:
            self.note.setText("No donor assets loaded. Choose Gw.dat in Build & Deploy, then "
                              "View > Reload Donor Assets.")
            self._sync = False
            return
        self._textures = list(assets.textures)
        self.table.setRowCount(len(self._textures))
        mono = QFont(self.font())
        mono.setFamily("Courier New" if "light" in COLORS else "Consolas")
        for i, t in enumerate(self._textures):
            a = QTableWidgetItem(str(t.tile))
            a.setData(Qt.UserRole, t.tile)
            if t.tile == 0:
                f = QFont(self.font())
                f.setBold(True)
                a.setFont(f)
            b = QTableWidgetItem(f"0x{t.file_id:X}")
            b.setFont(mono)
            c = QTableWidgetItem(f"{t.used:,}")
            c.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            d = QTableWidgetItem(f"{t.width} x {t.height}" if t.png else "-")
            e = QTableWidgetItem("base" if t.tile == 0 else (t.skipped or ""))
            if t.skipped:
                e.setForeground(QColor(COLORS["danger"]))
            for col, it in ((0, a), (1, b), (2, c), (3, d), (4, e)):
                self.table.setItem(i, col, it)
        n = sum(1 for t in self._textures if t.png)
        self.note.setText(f"Donor 0x{assets.donor_file_id:X}: {n} terrain texture(s) decoded. "
                          f"The build uses tile 0 for every cell until you paint. "
                          f"Right-drag paints the selected texture.")
        target = current if any(t.tile == current for t in self._textures) else 0
        row = next((i for i, t in enumerate(self._textures) if t.tile == target), -1)
        if row >= 0:
            self.table.blockSignals(True)
            self.table.selectRow(row)
            self.table.blockSignals(False)
            self._show_preview(target)
        self._sync = False
        if row >= 0 and (current is None or not any(t.tile == current for t in self._textures)):
            self.texture_selected.emit(target)

    def _picked(self):
        if self._sync:
            return
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), 0)
        if item is None:
            return
        tile = int(item.data(Qt.UserRole))
        if self._show_preview(tile):
            self.texture_selected.emit(tile)


class ModelTable(QTableWidget):
    """A record list whose rows can be dragged into the Render Window."""

    def __init__(self, rows, cols, parent=None):
        super().__init__(rows, cols, parent)
        self.models = []
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)

    def mimeData(self, items):
        if not items:
            return super().mimeData(items)
        row = items[0].row()
        if not (0 <= row < len(self.models)):
            return super().mimeData(items)
        m = self.models[row]
        payload = json.dumps({"file_id": int(m.file_id), "model": int(m.index)})
        md = QMimeData()
        md.setData("application/x-kryta-model", payload.encode("ascii"))
        md.setText(f"model {m.index} 0x{m.file_id:X}")
        return md


class ModelsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.assets = None
        self.archive = ""
        self._models = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.note = QLabel("No donor assets loaded.")
        self.note.setWordWrap(True)
        self.note.setProperty("muted", True)
        lay.addWidget(self.note)
        self.table = ModelTable(0, 7)
        self.table.setHorizontalHeaderLabels(["Model", "Name", "File ID", "Cells", "Height", "Tris", "Placed"])
        self.table.verticalHeader().setVisible(False)
        for c in range(7):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSortingEnabled(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemDoubleClicked.connect(lambda item: self._properties(item.row()))
        lay.addWidget(self.table, 1)

    def set_assets(self, assets, error=None):
        self.assets = assets
        self.archive = assets.archive if assets is not None else ""
        self.table.setRowCount(0)
        self.table.models = []
        self._models = list(assets.models) if assets is not None else []
        self.table.models = self._models
        if error:
            self.note.setText(error)
            return
        if assets is None:
            self.note.setText("No donor assets loaded.")
            return
        models = self._models
        self.table.setRowCount(len(models))
        mono = QFont(self.font())
        mono.setFamily("Courier New" if "light" in COLORS else "Consolas")
        for i, m in enumerate(models):
            a = QTableWidgetItem(str(m.index))
            name = QTableWidgetItem(name_for(m.file_id))
            b = QTableWidgetItem(f"0x{m.file_id:X}")
            b.setFont(mono)
            c = QTableWidgetItem("-" if m.cells is None else f"{m.cells:.1f}")
            c.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            d = QTableWidgetItem("-" if m.height is None else f"{m.height:,.0f}")
            d.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            e = QTableWidgetItem(f"{m.n_tris:,}" if m.extent else "-")
            e.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            f = QTableWidgetItem(str(m.placed))
            f.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            for col, it in ((0, a), (1, name), (2, b), (3, c), (4, d), (5, e), (6, f)):
                self.table.setItem(i, col, it)
        n = sum(1 for m in models if m.extent is not None)
        self.note.setText(f"Donor prop models: {n} measured of {len(models)}. "
                          f"Drag one into the Render Window to place it; right-click for its "
                          f"properties and preview.")

    def _menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        m = self._models[row] if 0 <= row < len(self._models) else None
        menu = QMenu(self)
        props = menu.addAction("Properties...")
        copy = menu.addAction("Copy File ID")
        act = menu.exec(self.table.viewport().mapToGlobal(pos))
        if act is props:
            self._properties(row)
        elif act is copy and m is not None:
            QApplication.clipboard().setText(f"0x{int(m.file_id):X}")

    def _properties(self, row):
        if not (0 <= row < len(self._models)):
            return
        ModelPropertiesDialog(self, self.archive, self._models[row]).exec()


class PropsPage(QWidget):
    go_requested = Signal(float, float)
    edit_requested = Signal(int)
    model_requested = Signal(int)

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.assets = None
        self._sync = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.note = QLabel("Drag a model from the Models tab into the Render Window to place it.")
        self.note.setWordWrap(True)
        self.note.setProperty("muted", True)
        lay.addWidget(self.note)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Object ID", "Model", "x", "y", "z", "Rot", "Scale", "Place"])
        self.table.verticalHeader().setVisible(False)
        for c in range(8):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _i: self._properties())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemSelectionChanged.connect(self._picked)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.setSpacing(3)
        props = QPushButton("Properties...")
        props.clicked.connect(self._properties)
        goto = QPushButton("Go To")
        goto.clicked.connect(self._goto)
        delete = QPushButton("Delete")
        delete.clicked.connect(self._delete)
        for b in (props, goto, delete):
            row.addWidget(b)
        lay.addLayout(row)
        doc.props_changed.connect(self.refresh)
        doc.project_changed.connect(self.refresh)
        doc.settings_changed.connect(self.refresh)
        doc.prop_selection_changed.connect(self._select_row)
        self.refresh()

    def set_assets(self, assets, error=None):
        self.assets = assets
        self.refresh()

    def refresh(self):
        p = self.doc.project
        props = list(p.props) if p is not None else []
        self._sync = True
        self.table.setRowCount(len(props))
        mono = QFont(self.font())
        mono.setFamily("Courier New" if "light" in COLORS else "Consolas")
        for i, q in enumerate(props):
            a = QTableWidgetItem(f"prop_{i}")
            a.setData(Qt.UserRole, i)
            label = name_for(q.file_id)
            b = QTableWidgetItem(f"{q.model} 0x{q.file_id:X}" + (f"  {label}" if label else ""))
            b.setFont(mono)
            c = QTableWidgetItem(f"{q.x:,.0f}")
            d = QTableWidgetItem(f"{q.y:,.0f}")
            e = QTableWidgetItem(f"{q.z:,.0f}")
            f = QTableWidgetItem(",".join(str(v) for v in q.rot))
            g = QTableWidgetItem(str(q.scale))
            h = QTableWidgetItem("grid" if q.grid_locked else "free")
            for col, it in ((0, a), (1, b), (2, c), (3, d), (4, e), (5, f), (6, g), (7, h)):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, col, it)
            a.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.note.setText(f"{len(props)} placed prop model(s). Drag a model from the Models tab "
                          f"into the Render Window to add one; right-click a row for properties. "
                          f"Props are grid locked unless their properties say free place.")
        self._sync = False
        self._select_row(self.doc.selected_prop)

    def _picked(self):
        if getattr(self, "_sync", False):
            return
        rows = self.table.selectionModel().selectedRows()
        self.doc.select_prop(rows[0].row() if rows else -1)

    def _select_row(self, i):
        self._sync = True
        if 0 <= i < self.table.rowCount():
            self.table.selectRow(i)
        else:
            self.table.clearSelection()
        self._sync = False

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return -1
        return rows[0].row()

    def _menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        menu = QMenu(self)
        props = menu.addAction("Properties...")
        model = menu.addAction("Model...")
        goto = menu.addAction("Go To")
        delete = menu.addAction("Delete")
        act = menu.exec(self.table.viewport().mapToGlobal(pos))
        if act is props:
            self._properties()
        elif act is model:
            self._model()
        elif act is goto:
            self._goto()
        elif act is delete:
            self._delete()

    def _properties(self):
        i = self._selected()
        if i >= 0:
            self.edit_requested.emit(i)

    def _model(self):
        i = self._selected()
        if i >= 0:
            self.model_requested.emit(i)

    def show_properties(self, i):
        p = self.doc.project
        if p is None or not (0 <= i < len(p.props)):
            return
        q = p.props[i]
        model = None
        if self.assets is not None:
            model = next((m for m in self.assets.models if int(m.file_id) == int(q.file_id)), None)
            if model is None and 0 <= q.model < len(self.assets.models):
                model = self.assets.models[q.model]
        if model is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Model Properties",
                                    f"This prop's model 0x{q.file_id:X} is not in the loaded donor "
                                    f"asset list.")
            return
        ModelPropertiesDialog(self, self.assets.archive if self.assets else "", model).exec()

    def _goto(self):
        i = self._selected()
        p = self.doc.project
        if p is not None and 0 <= i < len(p.props):
            q = p.props[i]
            self.go_requested.emit(q.x, q.y)

    def _delete(self):
        i = self._selected()
        p = self.doc.project
        if p is None or not (0 <= i < len(p.props)):
            return
        from .document import PropsCommand, copy_props
        before = copy_props(p)
        after = [q for k, q in enumerate(before) if k != i]
        self.doc.undo.push(PropsCommand(self.doc, before, after, "Delete prop"))
        self.doc.select_prop(-1)
        self.table.clearSelection()


class ObjectWindow(QWidget):
    open_project = Signal(str)
    new_map_requested = Signal()
    prop_go = Signal(float, float)
    edit_prop = Signal(int)

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.tabs = QTabWidget()
        self.spawns = SpawnPanel(doc)
        self.maps = MapsPage(doc)
        self.terrain = TerrainPage(doc)
        self.textures = TexturesPage()
        self.models = ModelsPage()
        self.props = PropsPage(doc)
        self.tabs.addTab(self.spawns, "Spawn Points")
        self.tabs.addTab(self.maps, "Maps")
        self.tabs.addTab(self.terrain, "Terrain")
        self.tabs.addTab(self.textures, "Textures")
        self.tabs.addTab(self.models, "Models")
        self.tabs.addTab(self.props, "Props")
        lay.addWidget(self.tabs, 1)
        self.maps.open_requested.connect(self.open_project.emit)
        self.maps.new_requested.connect(self.new_map_requested.emit)
        self.props.go_requested.connect(self.prop_go.emit)
        self.props.edit_requested.connect(self.edit_prop.emit)
        self.props.model_requested.connect(self.show_prop_properties)

    def show_spawns(self):
        self.tabs.setCurrentWidget(self.spawns)

    def show_maps(self):
        self.tabs.setCurrentWidget(self.maps)

    def show_terrain(self):
        self.tabs.setCurrentWidget(self.terrain)

    def show_textures(self):
        self.tabs.setCurrentWidget(self.textures)

    def show_models(self):
        self.tabs.setCurrentWidget(self.models)

    def show_props(self):
        self.tabs.setCurrentWidget(self.props)

    def show_prop_properties(self, i):
        self.show_props()
        self.doc.select_prop(i)
        self.props.show_properties(i)

    def set_assets(self, assets, error=None):
        self.textures.set_assets(assets, error)
        self.models.set_assets(assets, error)
        self.props.set_assets(assets, error)
