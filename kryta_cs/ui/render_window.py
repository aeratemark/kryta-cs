"""The Render Window: the CS's 3D editing surface, with the Top View one click away.

This is the window the Construction Set put in the middle of the screen. The 3D
view is the editor: the terrain surface, the red edit-radius circle, wireframe and
cell borders, and spawn placement. The Top View is the same document from above,
for precise work; the View menu switches between them.

The window is deliberately bare, as the CS's was: a grey caption, a sunken black
viewport. The view buttons live on the main toolbar and the View menu, not in a
strip of their own."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from .chrome import CaptionFrame, Well
from .heightmap_view import SELECT, HeightmapView
from .view3d import Terrain3DView


class RenderWindow(QWidget):
    cursor_info = Signal(str)
    view_changed = Signal()
    user_moved = Signal()
    tool_requested = Signal(str)

    def __init__(self, doc, brush, parent=None):
        super().__init__(parent)
        self.doc, self.brush = doc, brush
        self.mode = "3d"
        self.frame = CaptionFrame("Render Window")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.frame)

        self.view3d = Terrain3DView(doc, brush)
        self.view2d = HeightmapView(doc, brush)
        self.well3d = Well(self.view3d)
        self.well2d = Well(self.view2d)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.well3d)
        self.stack.addWidget(self.well2d)
        self.frame.add(self.stack, 1)

        self.view3d.cursor_info.connect(self.cursor_info.emit)
        self.view2d.cursor_info.connect(self.cursor_info.emit)
        self.view3d.view_changed.connect(self._moved)
        self.view2d.view_changed.connect(self._moved)
        self.view3d.user_moved.connect(self.user_moved.emit)
        self.view2d.user_moved.connect(self.user_moved.emit)
        self.view3d.tool_requested.connect(self.tool_requested.emit)
        doc.project_changed.connect(self._project_changed)
        doc.settings_changed.connect(self._project)
        doc.analysis_changed.connect(self._moved)
        self._project()

    # ------------------------------------------------------------ views

    def active_view(self):
        return self.view2d if self.mode == "top" else self.view3d

    def set_mode(self, key: str):
        if key not in ("3d", "top"):
            return
        self.mode = key
        if key == "top":
            self.stack.setCurrentWidget(self.well2d)
            QTimer.singleShot(0, self.view2d.fit)
        else:
            self.stack.setCurrentWidget(self.well3d)
        self._project()
        self.view_changed.emit()

    def fit(self):
        self.active_view().fit()
        self._moved()

    def reset_camera(self):
        self.view3d.reset_camera()
        self._moved()

    def center_on_grid(self, gx: float, gy: float):
        self.active_view().center_on_grid(gx, gy)
        self._moved()

    def center_on_world(self, x: float, y: float):
        self.active_view().center_on_world(x, y)
        self._moved()

    def focus_grid(self) -> tuple:
        return self.active_view().focus_grid()

    def thumbnail(self):
        return self.view2d.thumbnail()

    def visible_grid_rect(self):
        return self.view2d.visible_grid_rect() if self.mode == "top" else None

    def zoom_text(self) -> str:
        return self.active_view().zoom_text()

    def camera_text(self) -> str:
        return self.active_view().camera_text()

    def set_tool(self, key: str):
        self.view3d.set_tool(key)
        self.view2d.tool = key
        self.view2d.setCursor(Qt.CrossCursor if key != SELECT else Qt.ArrowCursor)
        self.view2d.update()

    def set_overlays(self, **kwargs):
        self.view2d.set_overlays(**kwargs)

    def set_textures(self, images):
        self.view3d.set_textures(images)
        self.view2d.set_textures(images)

    def set_ground_surface(self, surface):
        if surface is None:
            self.view3d.set_ground_image(None)
            self.view3d.set_ground_layers(None)
            self.view2d.set_ground_colors(None)
            return
        self.view3d.set_ground_image(surface.image, surface.dim_x, surface.dim_y)
        self.view3d.set_ground_layers(surface.layers, surface.dim_x, surface.dim_y)
        self.view2d.set_ground_colors(surface.cell_colors)

    def update_ground(self, surface, rect):
        """A texture stroke changed `rect`'s cells of `surface` in place."""
        if surface.image is not None:
            self.view3d.set_ground_image(surface.image, surface.dim_x, surface.dim_y)
        self.view3d.set_ground_layers(surface.layers, surface.dim_x, surface.dim_y, rect)
        self.view2d.set_ground_colors(surface.cell_colors, rect)

    def clear_ground(self):
        self.set_ground_surface(None)

    def set_paint_texture(self, tile: int):
        self.view3d.set_paint_texture(tile)
        self.view2d.paint_tile = max(0, int(tile))

    def set_show_radius(self, on: bool):
        self.view3d.show_radius = bool(on)
        self.view2d.show_radius = bool(on)
        self.view3d.update()
        self.view2d.update()

    def set_wireframe(self, on: bool):
        self.view3d.set_wireframe(on)

    def set_cell_borders(self, on: bool):
        self.view3d.set_cell_borders(on)

    # ------------------------------------------------------------ caption

    def _project_changed(self):
        self.view3d.request_fit()
        QTimer.singleShot(0, self.view3d.request_fit)
        self._project()
        self.view_changed.emit()

    def _project(self):
        p = self.doc.project
        if p is None:
            self.frame.caption.setDetail("")
        else:
            gx, gy = self.focus_grid()
            tile = f"{int(gx // 32)},{int(gy // 32)}"
            self.frame.caption.setDetail(f"{p.name}  {tile}")

    def _moved(self):
        self.view_changed.emit()
