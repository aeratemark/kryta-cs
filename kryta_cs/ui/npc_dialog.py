"""A placed NPC's record: which type it is, what it is called, where it stands and faces.

The CS opened a reference's data on a double click: its base object, position and rotation.
This is that for one row of the map's NPCs (MapProject.npcs, written to the world pack's
npcs/placements/<map id>.json on Publish)."""

from __future__ import annotations

import math

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout)

from .. import npcs as npcs_mod
from . import icons
from .document import NpcsCommand, copy_npcs


class NpcDialog(QDialog):
    def __init__(self, parent, doc, index: int, types: list, behaviors: list):
        super().__init__(parent)
        self.doc = doc
        self.index = int(index)
        p = doc.project
        if p is None or not (0 <= self.index < len(p.npcs)):
            raise ValueError("no such placed NPC")
        self.npc = p.npcs[self.index]
        n = self.npc
        self.setWindowTitle(f"Reference Data - {n.id}")
        self.setWindowIcon(icons.icon("app"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        head = QLabel(n.id)
        head.setProperty("heading", True)
        lay.addWidget(head)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.type = QComboBox()
        self.type.setEditable(False)
        found = False
        for t in types:
            self.type.addItem(f"{t.label}   ({t.id}, {t.profession} L{t.level})", t.id)
        if n.definition not in {t.id for t in types}:
            self.type.addItem(f"{n.definition}   (not in the pack)", n.definition)
        for i in range(self.type.count()):
            if self.type.itemData(i) == n.definition:
                self.type.setCurrentIndex(i)
                found = True
                break
        if not found:
            self.type.setCurrentIndex(self.type.count() - 1)
        form.addRow("NPC type", self.type)

        self.name = QLineEdit(n.name or "")
        self.name.setMaxLength(npcs_mod.NAME_CHARS)
        self.name.setPlaceholderText("the type's own name")
        self.name.setToolTip("What the client shows over the NPC. A dialog in dialogs/*.lua finds its "
                             "NPC by this name (its `npc = ...` line).")
        form.addRow("Name", self.name)

        self.x = self._spin(n.x)
        self.y = self._spin(n.y)
        form.addRow("X (world)", self.x)
        form.addRow("Y (world)", self.y)

        face = QHBoxLayout()
        self.faces = QCheckBox("Faces")
        self.faces.setChecked(n.angle is not None)
        self.angle = QSpinBox()
        self.angle.setRange(0, 359)
        self.angle.setSuffix(" deg")
        self.angle.setValue(int(round(math.degrees(n.angle or 0.0))) % 360)
        self.angle.setToolTip("0 faces +x (east), 90 faces +y (north). Alt-drag the NPC in the Render "
                              "Window to turn it.")
        self.faces.toggled.connect(self.angle.setEnabled)
        self.angle.setEnabled(self.faces.isChecked())
        face.addWidget(self.faces)
        face.addWidget(self.angle, 1)
        form.addRow("Facing", face)

        lvl = QHBoxLayout()
        self.own_level = QCheckBox("Override")
        self.own_level.setChecked(n.level is not None)
        self.level = QSpinBox()
        self.level.setRange(0, 255)
        self.level.setValue(int(n.level) if n.level is not None else self._type_level(types))
        self.own_level.toggled.connect(self.level.setEnabled)
        self.level.setEnabled(self.own_level.isChecked())
        lvl.addWidget(self.own_level)
        lvl.addWidget(self.level, 1)
        form.addRow("Level", lvl)

        self.behavior = QComboBox()
        self.behavior.addItem("(none: stands)", "")
        for b in behaviors:
            self.behavior.addItem(b, b)
        if n.behavior and n.behavior not in behaviors:
            self.behavior.addItem(f"{n.behavior} (not in behaviors.lua)", n.behavior)
        i = self.behavior.findData(n.behavior or "")
        self.behavior.setCurrentIndex(max(0, i))
        self.behavior.setToolTip("An entry of npcs/behaviors.lua: what the NPC does on its own.")
        form.addRow("Behavior", self.behavior)

        shown = sorted(k for k in (n.extra or {}) if not k.startswith("_"))
        if shown:
            kept = QLabel("Also kept as read: " + ", ".join(shown))
            kept.setWordWrap(True)
            kept.setProperty("muted", True)
            form.addRow("", kept)
        lay.addLayout(form)

        note = QLabel("Saved with the map. Publish writes it to the world pack; restart the world "
                      "to see it.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        lay.addWidget(note)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("OK")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _type_level(self, types) -> int:
        t = next((t for t in types if t.id == self.npc.definition), None)
        return t.level if t is not None else 1

    @staticmethod
    def _spin(value):
        s = QDoubleSpinBox()
        s.setRange(-1e6, 1e6)
        s.setDecimals(2)
        s.setValue(float(value))
        return s

    def _accept(self):
        name = self.name.text().strip()
        if name:
            try:
                npcs_mod.encode_name(name)
            except npcs_mod.NpcError as exc:
                QMessageBox.warning(self, "Name", str(exc))
                return
        before = copy_npcs(self.doc.project)
        after = copy_npcs(self.doc.project)
        n = after[self.index]
        n.definition = str(self.type.currentData())
        n.name = name or None
        n.x = float(self.x.value())
        n.y = float(self.y.value())
        n.angle = math.radians(self.angle.value()) if self.faces.isChecked() else None
        n.level = int(self.level.value()) if self.own_level.isChecked() else None
        n.behavior = self.behavior.currentData() or None
        if n != before[self.index]:
            self.doc.undo.push(NpcsCommand(self.doc, before, after, f"Edit {n.id}"))
        self.accept()
