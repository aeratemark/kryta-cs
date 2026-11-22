"""Cell View: the CS's cell list and object list, under the Render Window.

The Construction Set listed cells on the left (Cell Name, Grid, Ref Co., Path) and
the objects in the selected cell on the right (Object ID, Type, Ownership). A map
here is one cell, so the left list is its 32-cell codec tiles, named after the map;
the right list is the spawn points in the selected tile. Clicking a tile moves the
Render Window there; clicking an object selects it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..model import TILE
from .chrome import CaptionFrame


class CellView(QWidget):
    def __init__(self, doc, render, parent=None):
        super().__init__(parent)
        self.doc, self.render = doc, render
        self._tile = (0, 0)
        self._sync = False
        self.frame = CaptionFrame("Cell View")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.frame)

        self.cells = QTableWidget(0, 4)
        self.cells.setHorizontalHeaderLabels(["Cell Name", "Grid", "Ref Co.", "Path"])
        self.cells.verticalHeader().setVisible(False)
        hh = self.cells.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.cells.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cells.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cells.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cells.cellClicked.connect(self._cell_clicked)

        self.objects = QTableWidget(0, 3)
        self.objects.setHorizontalHeaderLabels(["Object ID", "Type", "Ownership"])
        self.objects.verticalHeader().setVisible(False)
        self.objects.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.objects.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.objects.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.objects.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.objects.setSelectionMode(QAbstractItemView.SingleSelection)
        self.objects.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.objects.itemSelectionChanged.connect(self._object_picked)
        self.objects.itemDoubleClicked.connect(self._object_opened)
        objpane = QWidget()
        ol = QVBoxLayout(objpane)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(0)
        self.obj_header = QLabel("Objects")
        self.obj_header.setAlignment(Qt.AlignCenter)
        self.obj_header.setProperty("panelHeader", True)
        ol.addWidget(self.obj_header)
        ol.addWidget(self.objects, 1)

        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(self.cells)
        self.split.addWidget(objpane)
        self.split.setSizes([420, 300])
        self.frame.add(self.split, 1)

        doc.project_changed.connect(self._rebuild)
        doc.settings_changed.connect(self._rebuild)
        doc.spawns_changed.connect(self._spawns_changed)
        doc.props_changed.connect(self._props_changed)
        doc.selection_changed.connect(self._selection_changed)
        doc.prop_selection_changed.connect(self._prop_selected)
        self.render.user_moved.connect(self._follow_focus)
        self._rebuild()

    # ------------------------------------------------------------ cells

    def _rebuild(self):
        p = self.doc.project
        self._sync = True
        self.cells.setRowCount(0)
        if p is not None:
            cols = max(1, p.dim_x // TILE)
            rows_n = max(1, p.dim_y // TILE)
            self._tile = (min(max(self._tile[0], 0), cols - 1),
                          min(max(self._tile[1], 0), rows_n - 1))
            rows = []
            for ty in range(rows_n):
                for tx in range(cols):
                    rows.append((tx, ty))
            self.cells.setRowCount(len(rows))
            for row, (tx, ty) in enumerate(rows):
                a = QTableWidgetItem(p.name)
                a.setData(Qt.UserRole, (tx, ty))
                b = QTableWidgetItem(f"{tx}, {ty}")
                c = QTableWidgetItem(str(self._tile_count(tx, ty)))
                d = QTableWidgetItem("N")
                c.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.cells.setItem(row, 0, a)
                self.cells.setItem(row, 1, b)
                self.cells.setItem(row, 2, c)
                self.cells.setItem(row, 3, d)
        self._sync = False
        self._select_tile(self._tile)
        self._refresh_objects()

    def _tile_count(self, tx, ty):
        return len(self._objects_in(tx, ty))

    def _objects_in(self, tx, ty):
        p = self.doc.project
        if p is None:
            return []
        out = []
        for i, s in enumerate(p.spawns):
            gx, gy = p.world_to_cell(s.x, s.y)
            if int(gx // TILE) == tx and int(gy // TILE) == ty:
                out.append(("spawn", i))
        for i, q in enumerate(p.props):
            gx, gy = p.world_to_cell(q.x, q.y)
            if int(gx // TILE) == tx and int(gy // TILE) == ty:
                out.append(("prop", i))
        return out

    def _refresh_cell_counts(self):
        p = self.doc.project
        if p is None:
            return
        cols = max(1, p.dim_x // TILE)
        for row in range(self.cells.rowCount()):
            item = self.cells.item(row, 0)
            if item is None:
                continue
            tx, ty = item.data(Qt.UserRole)
            c = self.cells.item(row, 2)
            if c is not None:
                c.setText(str(self._tile_count(tx, ty)))
            self.cells.item(row, 0).setText(p.name)

    def _tile_of_focus(self):
        p = self.doc.project
        if p is None:
            return None
        gx, gy = self.render.focus_grid()
        tx = min(max(int(gx // TILE), 0), max(0, p.dim_x // TILE - 1))
        ty = min(max(int(gy // TILE), 0), max(0, p.dim_y // TILE - 1))
        return tx, ty

    def _select_tile(self, tile):
        p = self.doc.project
        if p is None:
            return
        cols = max(1, p.dim_x // TILE)
        row = tile[1] * cols + tile[0]
        if 0 <= row < self.cells.rowCount():
            self._sync = True
            self.cells.selectRow(row)
            self._sync = False
            self.cells.scrollToItem(self.cells.item(row, 0))

    def _follow_focus(self):
        tile = self._tile_of_focus()
        if tile is not None and tile != self._tile:
            self._tile = tile
            self._select_tile(tile)
            self._refresh_objects()

    def _cell_clicked(self, row, _col):
        if self._sync:
            return
        item = self.cells.item(row, 0)
        if item is None:
            return
        tx, ty = item.data(Qt.UserRole)
        self._tile = (tx, ty)
        self.render.center_on_grid(tx * TILE + (TILE - 1) / 2, ty * TILE + (TILE - 1) / 2)
        self._refresh_objects()

    # ------------------------------------------------------------ objects

    def _refresh_objects(self):
        p = self.doc.project
        self._sync = True
        self.objects.setRowCount(0)
        if p is not None:
            self.obj_header.setText(f"{p.name} ({self._tile[0]}, {self._tile[1]}) Objects")
            rows = self._objects_in(*self._tile)
            self.objects.setRowCount(len(rows))
            for row, (kind, i) in enumerate(rows):
                if kind == "spawn":
                    a = QTableWidgetItem(f"spawn_{i}")
                    b = QTableWidgetItem("Spawn Point")
                else:
                    a = QTableWidgetItem(f"prop_{i}")
                    b = QTableWidgetItem("Static")
                a.setData(Qt.UserRole, (kind, i))
                c = QTableWidgetItem("-")
                self.objects.setItem(row, 0, a)
                self.objects.setItem(row, 1, b)
                self.objects.setItem(row, 2, c)
            for row, (kind, i) in enumerate(rows):
                if (kind == "spawn" and i == self.doc.selected) or (
                        kind == "prop" and i == self.doc.selected_prop):
                    self.objects.selectRow(row)
                    break
        self._sync = False

    def _object_picked(self):
        if self._sync:
            return
        rows = self.objects.selectionModel().selectedRows()
        if not rows:
            return
        item = self.objects.item(rows[0].row(), 0)
        role = item.data(Qt.UserRole) if item is not None else None
        if role is not None:
            if role[0] == "spawn":
                self.doc.select(int(role[1]))
            elif role[0] == "prop":
                self.doc.select_prop(int(role[1]))

    def _object_opened(self, item):
        row_item = self.objects.item(item.row(), 0)
        role = row_item.data(Qt.UserRole) if row_item is not None else None
        p = self.doc.project
        if p is None or role is None:
            return
        kind, i = role
        if kind == "spawn":
            s = p.spawns[i]
            self.doc.select(i)
            gx, gy = p.world_to_cell(s.x, s.y)
        else:
            q = p.props[i]
            gx, gy = p.world_to_cell(q.x, q.y)
        self.render.center_on_grid(gx, gy)

    def _spawns_changed(self):
        self._refresh_cell_counts()
        self._refresh_objects()

    def _props_changed(self):
        self._refresh_cell_counts()
        self._refresh_objects()

    def _selection_changed(self, i):
        if i < 0:
            return
        p = self.doc.project
        if p is None or i >= len(p.spawns):
            return
        gx, gy = p.world_to_cell(p.spawns[i].x, p.spawns[i].y)
        tile = (int(gx // TILE), int(gy // TILE))
        if tile != self._tile:
            self._tile = tile
            self._select_tile(tile)
            self._refresh_objects()
        else:
            for row, (kind, idx) in enumerate(self._objects_in(*self._tile)):
                if kind == "spawn" and idx == i:
                    self._sync = True
                    self.objects.selectRow(row)
                    self._sync = False
                    break

    def _prop_selected(self, i):
        if i < 0:
            return
        p = self.doc.project
        if p is None or i >= len(p.props):
            return
        q = p.props[i]
        gx, gy = p.world_to_cell(q.x, q.y)
        tile = (int(gx // TILE), int(gy // TILE))
        if tile != self._tile:
            self._tile = tile
            self._select_tile(tile)
            self._refresh_objects()
        else:
            for row, (kind, idx) in enumerate(self._objects_in(*self._tile)):
                if kind == "prop" and idx == i:
                    self._sync = True
                    self.objects.selectRow(row)
                    self._sync = False
                    break
