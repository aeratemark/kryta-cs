"""The docks around the view: Brushes, Map properties, Spawn points, Problems, Log."""

from __future__ import annotations

import math
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
                               QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from .. import kryta, paths, terrain
from ..model import MAP_TYPES, Spawn
from . import theme
from .document import HeightsCommand, SettingCommand, SpawnsCommand, copy_heights, copy_spawns
from .theme import COLORS


def _label(text, muted=False, heading=False):
    lab = QLabel(text)
    lab.setWordWrap(True)
    if muted:
        lab.setProperty("muted", True)
    if heading:
        lab.setProperty("heading", True)
    return lab


class _SliderRow(QWidget):
    changed = Signal(float)

    def __init__(self, lo, hi, value, decimals=0, suffix=""):
        super().__init__()
        self.scale = 10 ** decimals
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(lo * self.scale), int(hi * self.scale))
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(lo, hi)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(84)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.set(value)
        self.slider.valueChanged.connect(lambda v: self._from_slider(v))
        self.spin.valueChanged.connect(lambda v: self._from_spin(v))

    def set(self, v):
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setValue(int(v * self.scale))
        self.spin.setValue(v)
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)

    def _from_slider(self, v):
        self.spin.blockSignals(True)
        self.spin.setValue(v / self.scale)
        self.spin.blockSignals(False)
        self.changed.emit(v / self.scale)

    def _from_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(v * self.scale))
        self.slider.blockSignals(False)
        self.changed.emit(v)


# ====================================================================== brushes

class BrushPanel(QWidget):
    tool_chosen = Signal(str)          # "sculpt", "select", "flora"
    closed = Signal()
    choose_texture = Signal()
    choose_flora = Signal()
    show_radius = Signal(bool)

    TOOL_LABELS = [("raise", "Raise"), ("lower", "Lower"), ("smooth", "Smooth"), ("flatten", "Flatten"),
                   ("level", "Level to"), ("noise", "Roughen")]

    def __init__(self, doc, brush):
        super().__init__()
        self.doc, self.brush = doc, brush
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        modes = QGroupBox("Tool")
        ml = QHBoxLayout(modes)
        ml.setContentsMargins(4, 6, 4, 4)
        ml.setSpacing(3)
        self.mode_group = QButtonGroup(self)
        for key, text, tip in (("sculpt", "Sculpt", "Paint the terrain with the brush (S)"),
                               ("select", "Select", "Pick, move and turn props and spawn points (V)"),
                               ("flora", "Flora", "Paint where the donor's flora grows: left drag "
                                                  "paints, right drag clears (G)")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setMinimumWidth(60)
            b.setChecked(key == "sculpt")
            b.clicked.connect(lambda _c=False, k=key: self.tool_chosen.emit(k))
            self.mode_group.addButton(b)
            ml.addWidget(b)
            setattr(self, f"btn_{key}", b)
        lay.addWidget(modes)

        box = QGroupBox("Brush")
        grid = QGridLayout(box)
        grid.setContentsMargins(4, 6, 4, 4)
        grid.setSpacing(2)
        self.tool_group = QButtonGroup(self)
        for i, (key, text) in enumerate(self.TOOL_LABELS):
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setMinimumWidth(70)
            b.setChecked(key == brush.tool)
            b.clicked.connect(lambda _c=False, k=key: self._set_tool(k))
            self.tool_group.addButton(b)
            grid.addWidget(b, i // 3, i % 3)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(2)
        self.radius = _SliderRow(0.5, 48, brush.radius, 1, " cells")
        self.radius.changed.connect(lambda v: setattr(brush, "radius", v))
        self.strength = _SliderRow(1, 200, brush.strength, 0, " u")
        self.strength.changed.connect(lambda v: setattr(brush, "strength", v))
        self.falloff = _SliderRow(0, 1, brush.falloff, 2)
        self.falloff.changed.connect(lambda v: setattr(brush, "falloff", v))
        self.level = QDoubleSpinBox()
        self.level.setRange(-20000, 20000)
        self.level.setDecimals(0)
        self.level.setSuffix(" u")
        self.level.setValue(brush.level)
        self.level.valueChanged.connect(lambda v: setattr(brush, "level", v))
        form.addRow("Edit radius", self.radius)
        form.addRow("Strength", self.strength)
        form.addRow("Edit falloff", self.falloff)
        form.addRow("Level height", self.level)
        grid.addLayout(form, 2, 0, 1, 3)
        self.show_circle = QCheckBox("Show edit radius")
        self.show_circle.setChecked(True)
        self.show_circle.toggled.connect(self.show_radius.emit)
        grid.addWidget(self.show_circle, 3, 0, 1, 3)
        lay.addWidget(box)

        whole = QGroupBox("Whole map")
        wl = QVBoxLayout(whole)
        wl.setContentsMargins(4, 6, 4, 4)
        wl.setSpacing(3)
        gen_row = QHBoxLayout()
        gen_row.setSpacing(3)
        self.gen = QComboBox()
        self.gen.addItems(list(terrain.GENERATORS))
        b = QPushButton("Generate")
        b.setToolTip("Replace the whole height field (undoable)")
        b.clicked.connect(self._generate)
        gen_row.addWidget(self.gen, 1)
        gen_row.addWidget(b)
        wl.addLayout(gen_row)
        q = QPushButton("Terrace to 48 units (Rurik's 'master rule')")
        q.setToolTip("Snap every height to a multiple of 48. With the 45 degree slope set every step is then "
                     "walkable or a wall, never the risky band (AUTHORING.md section 3).")
        q.clicked.connect(self._quantize)
        wl.addWidget(q)
        sm = QPushButton("Smooth everything")
        sm.clicked.connect(self._smooth_all)
        wl.addWidget(sm)
        lay.addWidget(whole)
        off = QPushButton("Turn Off Landscape Editing")
        off.clicked.connect(self.closed.emit)
        lay.addWidget(off)
        tex = QGroupBox("Texture")
        tl = QVBoxLayout(tex)
        tl.setSpacing(2)
        self.texture_label = _label("Selected: tile 0 (Object Window > Textures)", muted=True)
        tl.addWidget(self.texture_label)
        choose = QPushButton("Choose Texture...")
        choose.clicked.connect(self.choose_texture.emit)
        tl.addWidget(choose)
        lay.addWidget(tex)
        fl = QGroupBox("Flora")
        fll = QVBoxLayout(fl)
        fll.setSpacing(2)
        self.flora_label = _label("Selected: none (Object Window > Flora)", muted=True)
        fll.addWidget(self.flora_label)
        choose_fl = QPushButton("Choose Flora...")
        choose_fl.clicked.connect(self.choose_flora.emit)
        fll.addWidget(choose_fl)
        lay.addWidget(fl)
        lay.addWidget(_label("Sculpt: left drag sculpts, right drag paints the selected texture. "
                             "Flora: left drag paints the selected flora, right drag clears it; "
                             "the edit radius is the brush's.", muted=True))
        lay.addStretch(1)

    def set_texture(self, tile):
        self.texture_label.setText(f"Selected: tile {tile} (Object Window > Textures)")

    def set_flora(self, name):
        self.flora_label.setText(f"Selected: {name or 'none'} (Object Window > Flora)")

    def _set_tool(self, key):
        self.brush.tool = key
        self.tool_chosen.emit("sculpt")

    def set_mode(self, key):
        b = getattr(self, f"btn_{key}", None)
        if b is not None:
            b.setChecked(True)

    def _whole(self, text, fn):
        p = self.doc.project
        if p is None:
            return
        before = copy_heights(p)
        fn(p)
        self.doc.heights_changed.emit(None)
        self.doc.undo.push(HeightsCommand(self.doc, before, copy_heights(p), text))

    def _generate(self):
        name = self.gen.currentText()
        fn = terrain.GENERATORS[name]

        def run(p):
            p.heights = fn(p, base=0.0)
        self._whole(f"Generate: {name}", run)

    def _quantize(self):
        self._whole("Terrace to 48", lambda p: terrain.quantize(p, 48.0))

    def _smooth_all(self):
        def run(p):
            b = terrain.Brush("smooth", radius=1e6, strength=24.0, falloff=0.0)
            b.apply(p, p.dim_x / 2, p.dim_y / 2)
        self._whole("Smooth everything", run)


# ====================================================================== properties

class MapPanel(QWidget):
    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(6, 6, 6, 6)
        f = QFormLayout()
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._name)
        self.map_id = QComboBox()
        self.map_id.setEditable(False)
        self.map_id.currentIndexChanged.connect(self._map_id)
        self.map_type = QComboBox()
        for k, v in MAP_TYPES.items():
            self.map_type.addItem(v, k)
        self.map_type.currentIndexChanged.connect(self._map_type)
        self.size = QLabel()
        self.file_id = QLabel()
        self.water_has = QCheckBox("Water plane")
        self.water_has.toggled.connect(self._water_toggled)
        self.water_level = QDoubleSpinBox()
        self.water_level.setRange(-100000.0, 100000.0)
        self.water_level.setDecimals(0)
        self.water_level.setSingleStep(48.0)
        self.water_level.valueChanged.connect(self._water_level)
        water_row = QHBoxLayout()
        water_row.addWidget(self.water_has)
        water_row.addWidget(self.water_level, 1)
        f.addRow("Name", self.name)
        f.addRow("Client map id", self.map_id)
        f.addRow("Type", self.map_type)
        f.addRow("Water", water_row)
        f.addRow("Size", self.size)
        f.addRow("Gw.dat file id", self.file_id)
        pl.addLayout(f)
        pl.addWidget(_label("The water plane's height. The client draws a plane at the map's "
                            "environment level, so this is where the ground is under water; the "
                            "build writes it into the water record instead of inheriting another "
                            "map's.", muted=True))
        pl.addWidget(_label("The client's own map number. '/tp' only knows a fixed list of maps, so to walk "
                            "a custom map today publish it into one of those slots (Kamadan is the default: it "
                            "is outside pre-Searing). Taking it off the world puts the original map back.",
                            muted=True))
        pl.addStretch(1)
        self.tabs.addTab(page, "Map")

        mesh = QWidget()
        ml = QVBoxLayout(mesh)
        ml.setContentsMargins(6, 6, 6, 6)
        self.cut45 = QCheckBox("Steeper walkable slopes (45 degree cut, top byte 0x02)")
        self.cut45.toggled.connect(self._flags)
        self.water = QCheckBox("Keep ground under water walkable (bit 0)")
        self.water.toggled.connect(self._flags)
        self.flags_lab = _label("", muted=True)
        ml.addWidget(self.cut45)
        ml.addWidget(self.water)
        ml.addWidget(self.flags_lab)
        ml.addStretch(1)
        self.tabs.addTab(mesh, "Mesh")

        look = QWidget()
        lf = QFormLayout(look)
        lf.setContentsMargins(6, 6, 6, 6)
        self.donor = QLineEdit()
        self.donor.setToolTip("The file id of the retail map whose textures, sky, light and sound this map wears. "
                              "0x1B97D is pre-Searing Ascalon.")
        self.donor.editingFinished.connect(lambda: self._hex("donor_file_id", self.donor))
        self.constants = QLineEdit()
        self.constants.setToolTip("The file id of a small retail map whose structural chunks fit an authored "
                                  "map (Rurik's 0x22E2C).")
        self.constants.editingFinished.connect(lambda: self._hex("constants_file_id", self.constants))
        self.trees = QSpinBox()
        self.trees.setRange(0, 200)
        self.trees.setToolTip("Props from the donor's own model list, placed on flat cells away from the seed")
        self.trees.valueChanged.connect(lambda v: self._set("trees", int(v), "Props"))
        self.outline = QSpinBox()
        self.outline.setRange(0, 2000)
        self.outline.setSuffix(" u")
        self.outline.setToolTip("Half-width of a square collision footprint per prop; 0 = walk-through")
        self.outline.valueChanged.connect(lambda v: self._set("prop_outline", int(v), "Prop footprint"))
        lf.addRow("Biome donor", self.donor)
        lf.addRow("Constants donor", self.constants)
        lf.addRow("Props", self.trees)
        lf.addRow("Prop footprint", self.outline)
        self.tabs.addTab(look, "Look")

        notes = QWidget()
        nl = QVBoxLayout(notes)
        nl.setContentsMargins(6, 6, 6, 6)
        self.notes = QPlainTextEdit()
        self.notes.textChanged.connect(self._notes)
        nl.addWidget(self.notes)
        self.tabs.addTab(notes, "Notes")
        self.tabs.setTabToolTip(0, "Name, client map id, type and the Gw.dat file this map became")
        self.tabs.setTabToolTip(1, "What the client's mesh compiler will make of the slopes")
        self.tabs.setTabToolTip(2, "The retail map this map borrows its look from")
        self.tabs.setTabToolTip(3, "Author notes, saved with the project")
        doc.project_changed.connect(self.load)
        doc.settings_changed.connect(self.load)

    def load(self):
        p = self.doc.project
        self.setEnabled(p is not None)
        if p is None:
            return
        self._loading = True
        self.name.setText(p.name)
        self.map_id.clear()
        choices = kryta.map_id_choices(paths.REPO_ROOT, p.slug)
        if p.map_id not in {m for m, _l in choices}:
            self.map_id.addItem(str(p.map_id), p.map_id)
        for mid, label in choices:
            self.map_id.addItem(label, mid)
        idx = self.map_id.findData(p.map_id)
        self.map_id.setCurrentIndex(max(0, idx))
        self.map_type.setCurrentIndex(max(0, self.map_type.findData(p.map_type)))
        self.size.setText(f"{p.dim_x} x {p.dim_y} cells  ({p.dim_x * 96:,} x {p.dim_y * 96:,} units)")
        self.file_id.setText("not installed yet" if p.file_id is None else f"0x{p.file_id:X} ({p.file_id})")
        self.cut45.setChecked(((p.map_flags >> 24) & 0xFF) >= 2)
        self.water.setChecked(bool(p.map_flags & 1))
        self.flags_lab.setText(f"map_flags = 0x{p.map_flags:08X}   walk cut {terrain.slope_set(p.map_flags)[1]:.0f} deg")
        self.donor.setText(f"0x{p.donor_file_id:X}")
        self.constants.setText(f"0x{p.constants_file_id:X}")
        self.trees.setValue(p.trees)
        has_water = getattr(p, "water_level", None) is not None
        self.water_has.setChecked(has_water)
        self.water_level.setValue(float(getattr(p, "water_level", 0.0) or 0.0))
        self.water_level.setEnabled(has_water)
        self.outline.setValue(p.prop_outline)
        if self.notes.toPlainText() != p.notes:
            self.notes.setPlainText(p.notes)
        if getattr(p, "stock", False):
            # A game map's id, type, file, mesh flags, water and look are ArenaNet's own.
            for w in (self.map_id, self.map_type, self.water_has, self.water_level, self.cut45,
                      self.water, self.donor, self.constants, self.trees, self.outline):
                w.setEnabled(False)
            self.file_id.setText(f"0x{p.file_id:X} ({p.file_id}), the game's own map file")
        else:
            for w in (self.map_id, self.map_type, self.water_has, self.cut45, self.water,
                      self.donor, self.constants, self.trees, self.outline):
                w.setEnabled(True)
        self._loading = False

    def _set(self, attr, value, text):
        if self._loading or self.doc.project is None:
            return
        old = getattr(self.doc.project, attr)
        if old != value:
            self.doc.undo.push(SettingCommand(self.doc, attr, old, value, text))

    def _name(self):
        p = self.doc.project
        if p is None or self._loading:
            return
        name = self.name.text().strip() or "Unnamed"
        self._set("name", name, "Rename map")

    def _map_id(self, _i):
        v = self.map_id.currentData()
        if v is not None:
            self._set("map_id", int(v), "Change map id")

    def _map_type(self, _i):
        v = self.map_type.currentData()
        if v is not None:
            self._set("map_type", int(v), "Change map type")

    def _water_toggled(self, on):
        self.water_level.setEnabled(bool(on))
        self._set("water_level", float(self.water_level.value()) if on else None,
                  "Change the water plane")

    def _water_level(self, value):
        if self.water_has.isChecked():
            self._set("water_level", float(value), "Change water level")

    def _flags(self, _on):
        p = self.doc.project
        if p is None or self._loading:
            return
        flags = p.map_flags & 0x00FFFFFE
        flags |= (0x02 << 24) if self.cut45.isChecked() else 0
        flags |= 1 if self.water.isChecked() else 0
        self._set("map_flags", flags, "Change mesh flags")

    def _hex(self, attr, edit):
        try:
            v = int(edit.text().strip(), 0)
        except ValueError:
            self.load()
            return
        self._set(attr, v, "Change donor")

    def _notes(self):
        p = self.doc.project
        if p is not None and not self._loading:
            p.notes = self.notes.toPlainText()


# ====================================================================== spawns

class SpawnPanel(QWidget):
    add_requested = Signal()
    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Object ID", "x", "y", "Facing"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self._picked)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.setSpacing(4)
        add = QPushButton("Add")
        add.setToolTip("Add a spawn point where the Render Window is looking")
        add.clicked.connect(self.add_requested.emit)
        rem = QPushButton("Remove")
        rem.clicked.connect(self._remove)
        first = QPushButton("Make seed")
        first.setToolTip("Move the selected spawn to position 0, the flood seed")
        first.clicked.connect(self._make_first)
        for b in (add, rem, first):
            row.addWidget(b)
        lay.addLayout(row)
        self._loading = False
        doc.project_changed.connect(self.load)
        doc.spawns_changed.connect(self.load)
        doc.selection_changed.connect(self._select)

    def load(self):
        p = self.doc.project
        self._loading = True
        self.table.setRowCount(0)
        if p is not None:
            for i, s in enumerate(p.spawns):
                self.table.insertRow(i)
                it = QTableWidgetItem(f"spawn_{i}")
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, 0, it)
                self.table.setItem(i, 1, QTableWidgetItem(f"{s.x:.0f}"))
                self.table.setItem(i, 2, QTableWidgetItem(f"{s.y:.0f}"))
                self.table.setItem(i, 3, QTableWidgetItem("" if s.angle is None else f"{math.degrees(s.angle):.0f}"))
        self._loading = False
        self._select(self.doc.selected)

    def _select(self, idx):
        self._loading = True
        if 0 <= idx < self.table.rowCount():
            self.table.selectRow(idx)
        else:
            self.table.clearSelection()
        self._loading = False

    def _picked(self):
        if self._loading:
            return
        rows = self.table.selectionModel().selectedRows()
        self.doc.select(rows[0].row() if rows else -1)

    def _edited(self, item):
        if self._loading:
            return
        p = self.doc.project
        before = copy_spawns(p)
        after = copy_spawns(p)
        i, c = item.row(), item.column()
        try:
            txt = item.text().strip()
            if c == 1:
                after[i].x = float(txt)
            elif c == 2:
                after[i].y = float(txt)
            elif c == 3:
                after[i].angle = None if txt == "" else math.radians(float(txt))
        except ValueError:
            self.load()
            return
        self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Edit spawn"))

    def _add(self):
        p = self.doc.project
        if p is None:
            return
        before = copy_spawns(p)
        cx, cy = p.cell_center(p.dim_x // 2, p.dim_y // 2)
        self.doc.undo.push(SpawnsCommand(self.doc, before, before + [Spawn(cx, cy, None)], "Add spawn"))

    def _remove(self):
        p, i = self.doc.project, self.doc.selected
        if p is None or i < 0:
            return
        before = copy_spawns(p)
        self.doc.undo.push(SpawnsCommand(self.doc, before, [s for k, s in enumerate(before) if k != i],
                                         "Remove spawn"))
        self.doc.select(-1)

    def _make_first(self):
        p, i = self.doc.project, self.doc.selected
        if p is None or i <= 0:
            return
        before = copy_spawns(p)
        after = [before[i]] + [s for k, s in enumerate(before) if k != i]
        self.doc.undo.push(SpawnsCommand(self.doc, before, after, "Make seed"))
        self.doc.select(0)


# ====================================================================== problems

class ProblemsPanel(QWidget):
    goto = Signal(float, float)
    recheck = Signal()

    ICON = {"error": "\u2716", "warning": "\u26a0", "info": "\u2139"}
    COLOR = {"error": "danger", "warning": "warn", "info": "accent2"}

    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.summary = QLabel()
        top.addWidget(self.summary, 1)
        again = QPushButton("Re-check")
        again.setToolTip("Run the slope, reachability and build checks again (F7)")
        again.clicked.connect(self.recheck.emit)
        top.addWidget(again)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(self._go)
        lay.addWidget(self.list, 1)
        doc.analysis_changed.connect(self.load)

    def load(self):
        self.list.clear()
        errors = warnings = 0
        for f in self.doc.findings:
            it = QListWidgetItem(f"{self.ICON[f.level]}  {f.text}")
            it.setForeground(QColor(COLORS[self.COLOR[f.level]]))
            it.setData(Qt.UserRole, f.where)
            self.list.addItem(it)
            errors += f.level == "error"
            warnings += f.level == "warning"
        if errors:
            self.summary.setText(f"<b style='color:{COLORS['danger']}'>{errors} error(s)</b> - the build will refuse")
        elif warnings:
            self.summary.setText(f"<b style='color:{COLORS['warn']}'>{warnings} warning(s)</b> - buildable")
        else:
            self.summary.setText(f"<b style='color:{COLORS['ok']}'>Ready to build</b>")

    def _go(self, item):
        w = item.data(Qt.UserRole)
        if w:
            self.goto.emit(float(w[0]), float(w[1]))


# ====================================================================== log

class LogPanel(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.apply_theme()
        self.setMaximumBlockCount(5000)

    def apply_theme(self):
        self.setFont(theme.mono_font())

    _file = None

    def log(self, text):
        self.appendPlainText(text)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        # also kept in messages.log beside run_editor.py (replaced per launch),
        # so what the editor reported can be read after the fact
        if LogPanel._file is None:
            import os
            import time
            here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            try:
                LogPanel._file = open(os.path.join(here, "messages.log"), "w",
                                      encoding="ascii", errors="replace")
            except OSError:
                LogPanel._file = False
            LogPanel._t0 = time.monotonic()
        if LogPanel._file:
            import time
            LogPanel._file.write(f"{time.monotonic() - LogPanel._t0:8.2f}  {text}\n")
            LogPanel._file.flush()
