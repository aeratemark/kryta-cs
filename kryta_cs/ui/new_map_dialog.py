"""File > New map: name, size, starting shape, and which client map id it takes over."""

from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QLabel, QLineEdit, QVBoxLayout)

from .. import kryta, paths, terrain
from ..model import MAP_TYPES, MapProject
from . import icons


class NewMapDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New map")
        self.setWindowIcon(icons.icon("app"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        title = QLabel("Author a new map")
        title.setProperty("heading", True)
        lay.addWidget(title)
        sub = QLabel("It gets a Gw.dat file of its own when you install it, so no retail map is touched.")
        sub.setProperty("muted", True)
        sub.setWordWrap(True)
        lay.addWidget(sub)

        ident = QGroupBox("Map")
        f = QFormLayout(ident)
        self.name = QLineEdit("New Map")
        self.map_id = QComboBox()
        for mid, label in kryta.map_id_choices(paths.REPO_ROOT):
            self.map_id.addItem(label, mid)
        self.map_id.setToolTip("/tp slots are reachable in game now: '/tp kamadan' then loads this map until "
                               "you take it off the world. Free ids displace nothing but have no travel yet.")
        self.map_type = QComboBox()
        for k, v in MAP_TYPES.items():
            self.map_type.addItem(v, k)
        f.addRow("Name", self.name)
        f.addRow("Client map id", self.map_id)
        f.addRow("Type", self.map_type)
        lay.addWidget(ident)

        terr = QGroupBox("Terrain")
        tf = QFormLayout(terr)
        self.size_x = QComboBox()
        self.size_y = QComboBox()
        for s in range(32, 257, 32):
            self.size_x.addItem(f"{s} cells ({s * 96:,} units)", s)
            self.size_y.addItem(f"{s} cells ({s * 96:,} units)", s)
        self.size_x.setCurrentIndex(1)
        self.size_y.setCurrentIndex(1)
        self.gen = QComboBox()
        self.gen.addItems(list(terrain.GENERATORS))
        self.gen.setCurrentIndex(2)
        self.base = QDoubleSpinBox()
        self.base.setRange(-10000, 10000)
        self.base.setDecimals(0)
        self.base.setSuffix(" units")
        tf.addRow("Width (x)", self.size_x)
        tf.addRow("Height (y)", self.size_y)
        tf.addRow("Start from", self.gen)
        tf.addRow("Base height", self.base)
        lay.addWidget(terr)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def project(self) -> MapProject:
        p = MapProject.new(self.name.text().strip() or "New Map", self.size_x.currentData(),
                           self.size_y.currentData(), self.base.value(), self.map_id.currentData(),
                           self.map_type.currentData())
        p.heights = terrain.GENERATORS[self.gen.currentText()](p, base=self.base.value())
        return p
