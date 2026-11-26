"""File > Open Game Map: pick one of the world pack's maps to open from Gw.dat (kryta_cs/stock.py)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import icons

KINDS = {0: "outpost", 1: "explorable", 2: "mission", 4: "explorable", 6: "explorable",
         11: "creation"}


class GameMapDialog(QDialog):
    """The pack's maps that name a map file, with the other maps that share that file."""

    def __init__(self, parent, catalogue: dict):
        super().__init__(parent)
        self.setWindowTitle("Open Game Map")
        self.setWindowIcon(icons.icon("open"))
        self.resize(760, 540)
        self.rows = sorted((r for r in catalogue.values() if r.file_id),
                           key=lambda r: r.map_id)
        sharing = {}
        for r in self.rows:
            sharing.setdefault(r.file_id, []).append(r)
        self.sharing = sharing
        lay = QVBoxLayout(self)
        note = QLabel("Opens the map as it ships in Gw.dat. Its props, flora and NPCs can be "
                      "edited; its ground is shown but not reshaped. Build & Deploy writes the "
                      "props and flora into the map's own file, and the NPCs into the world "
                      "pack's npcs/placements/<map id>.json.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        lay.addWidget(note)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter (id, name, kind)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter)
        self.table = QTableWidget(len(self.rows), 5)
        self.table.setHorizontalHeaderLabels(["Map", "Name", "Kind", "Map file", "Also uses the file"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for c in range(5):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i, r in enumerate(self.rows):
            mid = QTableWidgetItem()
            mid.setData(Qt.DisplayRole, int(r.map_id))
            mid.setData(Qt.UserRole, i)
            others = [o for o in sharing[r.file_id] if o.map_id != r.map_id]
            cells = (mid, QTableWidgetItem(r.name or "(no name)"),
                     QTableWidgetItem(KINDS.get(r.map_type, "not hosted" if r.map_type is None
                                                else str(r.map_type))),
                     QTableWidgetItem(f"0x{r.file_id:X}"),
                     QTableWidgetItem(", ".join(f"{o.map_id} {o.name}" for o in others[:3])
                                      + (" ..." if len(others) > 3 else "")))
            for c, it in enumerate(cells):
                self.table.setItem(i, c, it)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0)
        self.table.itemDoubleClicked.connect(lambda _i: self.accept())
        self.table.itemSelectionChanged.connect(self._picked)
        lay.addWidget(self.table, 1)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Open")
        self.ok.setDefault(True)
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        lay.addLayout(row)

    def _apply_filter(self):
        words = self.filter.text().lower().split()
        for r in range(self.table.rowCount()):
            text = " ".join(self.table.item(r, c).text() for c in range(4)).lower()
            self.table.setRowHidden(r, not all(w in text for w in words))

    def chosen(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return self.rows[int(item.data(Qt.UserRole))]

    def _picked(self):
        r = self.chosen()
        self.ok.setEnabled(r is not None)
        if r is None:
            self.warn.setText("")
            return
        others = [o for o in self.sharing[r.file_id] if o.map_id != r.map_id]
        self.warn.setText(
            "" if not others else
            f"Map file 0x{r.file_id:X} is also " + ", ".join(f"{o.map_id} {o.name}" for o in others)
            + ": props and flora you change show in those maps too. NPCs are per map.")
