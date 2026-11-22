"""The Models tab's record dialog: the model's measurements and a preview."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout)

from .. import assets
from . import icons
from .model_preview import ModelPreview


class ModelPropertiesDialog(QDialog):
    def __init__(self, parent, archive: str, model):
        super().__init__(parent)
        self.setWindowTitle(f"Model Properties - 0x{int(model.file_id):X}")
        self.setWindowIcon(icons.icon("app"))
        self.setMinimumSize(760, 520)
        lay = QVBoxLayout(self)
        head = QLabel(f"Model {model.index}    file id 0x{int(model.file_id):X}")
        head.setProperty("heading", True)
        lay.addWidget(head)
        body = QHBoxLayout()
        frame = QFrame()
        frame.setFrameShape(QFrame.Panel)
        frame.setFrameShadow(QFrame.Sunken)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(2, 2, 2, 2)
        self.preview = ModelPreview()
        fl.addWidget(self.preview)
        body.addWidget(frame, 1)
        form_box = QFormLayout()
        form_box.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        def add(k, v):
            lab = QLabel(str(v))
            lab.setWordWrap(True)
            form_box.addRow(k, lab)
        add("Donor index", model.index)
        add("File id", f"0x{int(model.file_id):X}")
        add("Archive row", "-" if model.row is None else f"{model.row:,}")
        if model.extent:
            add("Extent", f"{model.extent[0]:,.0f} x {model.extent[1]:,.0f} x {model.extent[2]:,.0f} u")
        else:
            add("Extent", "-")
        add("Placement cells", "-" if model.cells is None else f"{model.cells:.2f}")
        add("Height", "-" if model.height is None else f"{model.height:,.0f} u")
        add("Vertices", f"{model.n_verts:,}")
        add("Triangles", f"{model.n_tris:,}")
        add("Submodels", model.n_submodels)
        add("Donor placements", model.placed)
        self.tex_label = QLabel("-")
        self.tex_label.setWordWrap(True)
        form_box.addRow("Textures", self.tex_label)
        if model.error:
            err = QLabel(model.error)
            err.setWordWrap(True)
            err.setStyleSheet("color: #c00000")
            form_box.addRow("Error", err)
        body.addLayout(form_box, 1)
        lay.addLayout(body, 1)
        note = QLabel("Drag this model from the Models tab into the Render Window to place it. "
                      "The right mouse button in that list opens this dialog.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        lay.addWidget(note)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._load(archive, model)

    def _load(self, archive, model):
        if model.error:
            self.preview.set_assets(None, model.error)
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            got = assets.load_model_assets(archive, model.file_id)
            self.preview.set_assets(got)
            n = sum(1 for t in got.textures if t is not None and t.png)
            self.tex_label.setText(f"{n} of {len(got.textures)} decoded")
        except Exception as exc:                                     # noqa: BLE001
            self.preview.set_assets(None, f"{type(exc).__name__}: {exc}")
            self.tex_label.setText("-")
        finally:
            QApplication.restoreOverrideCursor()
