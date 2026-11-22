"""A placed prop's own properties: where it stands and how it is anchored.

The CS put this on a reference: its position, its rotation and its scale. The
one thing that matters most here is Placement - Guild Wars authors props on the
terrain grid, so a prop is grid locked by default: it stands on a cell's centre
at that cell's height (which is exactly where Rurik's deploy puts one). Free
place keeps the position the user dragged to and follows the ground under it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout)

from ..model import CELL, PlacedProp
from . import icons
from .document import PropsCommand, copy_props


class PropPropertiesDialog(QDialog):
    def __init__(self, parent, doc, index: int, model_name: str = ""):
        super().__init__(parent)
        self.doc = doc
        self.index = int(index)
        p = doc.project
        self.prop = p.props[self.index] if p is not None and 0 <= self.index < len(p.props) else None
        if self.prop is None:
            raise ValueError("no such placed prop")
        self.setWindowTitle(f"Reference Data - prop_{self.index}")
        self.setWindowIcon(icons.icon("app"))
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        head = QLabel(f"prop_{self.index}    {model_name or ''}")
        head.setProperty("heading", True)
        lay.addWidget(head)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.mode = QComboBox()
        self.mode.addItem("Grid locked (2D) - stands on a cell", True)
        self.mode.addItem("Free place - follow the ground", False)
        self.mode.setCurrentIndex(0 if self.prop.grid_locked else 1)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        form.addRow("Placement", self.mode)

        self.x = self._spin(-1e6, 1e6, self.prop.x)
        self.y = self._spin(-1e6, 1e6, self.prop.y)
        self.z = self._spin(-1e6, 1e6, self.prop.z)
        form.addRow("X (world)", self.x)
        form.addRow("Y (world)", self.y)
        form.addRow("Z (height)", self.z)

        self.rot = QSpinBox()
        self.rot.setRange(0, 255)
        self.rot.setValue(int(self.prop.rot[2]))
        self.rot.setToolTip("Yaw, the prop chunk's third rotation byte: 256 steps per turn.")
        rot_row = QHBoxLayout()
        rot_row.addWidget(self.rot, 1)
        self.degrees = QLabel("")
        rot_row.addWidget(self.degrees, 1)
        self.rot.valueChanged.connect(self._update_degrees)
        self._update_degrees()
        form.addRow("Rotation", rot_row)

        self.scale = QSpinBox()
        self.scale.setRange(1, 255)
        self.scale.setValue(int(self.prop.scale))
        self.scale.setToolTip("The prop chunk's scale byte; 127 is 1.0.")
        form.addRow("Scale", self.scale)

        self.locked_note = QLabel("")
        self.locked_note.setWordWrap(True)
        self.locked_note.setProperty("muted", True)
        form.addRow("", self.locked_note)
        lay.addLayout(form)

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
        self._mode_changed()

    @staticmethod
    def _spin(lo, hi, value):
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setDecimals(2)
        box.setSingleStep(CELL / 2.0)
        box.setValue(float(value))
        return box

    def _update_degrees(self):
        self.degrees.setText(f"{self.rot.value() * 360.0 / 256.0:.1f} deg")

    def _mode_changed(self):
        locked = bool(self.mode.currentData())
        h = self.doc.project.height_at_world(self.prop.x, self.prop.y) if self.doc.project else 0.0
        self.locked_note.setText(
            "Grid locked: the prop stands on the nearest cell centre at that cell's "
            f"height ({h:,.0f} here). Dragging it in the Render Window moves it cell by cell."
            if locked else
            "Free place: the prop keeps the position you give it and follows the ground "
            "under the cursor when dragged.")

    def _accept(self):
        p = self.doc.project
        if p is None:
            self.reject()
            return
        q = self.prop
        grid_locked = bool(self.mode.currentData())
        if grid_locked:
            x, y, z = p.snap_world(self.x.value(), self.y.value())
        else:
            x, y, z = self.x.value(), self.y.value(), self.z.value()
        after = PlacedProp(file_id=q.file_id, model=q.model, x=x, y=y, z=z,
                           rot=(int(q.rot[0]), int(q.rot[1]), self.rot.value()),
                           scale=self.scale.value(), flags=q.flags,
                           outline=[tuple(v) for v in q.outline], grid_locked=grid_locked)
        if (after.x, after.y, after.z, after.rot, after.scale, after.grid_locked) == (
                q.x, q.y, q.z, tuple(q.rot), q.scale, q.grid_locked):
            self.accept()
            return
        before = copy_props(p)
        merged = list(before)
        merged[self.index] = after
        self.doc.undo.push(PropsCommand(self.doc, before, merged, "Edit prop"))
        self.accept()
