"""The open map, its undo history, and the signals every panel listens to."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from .. import terrain, texlayers
from ..model import MapProject, PlacedProp, Spawn


class Document(QObject):
    project_changed = Signal()        # a different project (new / open)
    heights_changed = Signal(object)  # dirty rect (x0, y0, x1, y1) or None for all
    textures_changed = Signal(object)  # per-cell texture rect (x0,y0,x1,y1) or None
    spawns_changed = Signal()
    props_changed = Signal()          # placed donor models changed
    settings_changed = Signal()       # name, ids, flags...
    analysis_changed = Signal()       # slopes / classes / findings recomputed
    selection_changed = Signal(int)   # spawn index or -1
    prop_selection_changed = Signal(int)  # placed prop index or -1
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self.project: MapProject | None = None
        self.undo = QUndoStack(self)
        self.slopes = None
        self.classes = None
        self.findings = []
        self.tile_types = None        # the donor's terrain tag 4, once assets load
        self.reach = set()
        self.selected = -1
        self.selected_prop = -1
        self.dirty = False
        self.undo.cleanChanged.connect(lambda clean: setattr(self, "dirty", not clean))

    # ------------------------------------------------------------ lifecycle

    def set_project(self, project: MapProject):
        self.project = project
        self.undo.clear()
        self.selected = 0 if project.spawns else -1
        self.selected_prop = -1
        self.analyse()
        self.project_changed.emit()
        self.selection_changed.emit(self.selected)
        self.prop_selection_changed.emit(-1)
        self.undo.setClean()

    def analyse(self):
        p = self.project
        if p is None:
            return
        self.slopes = terrain.slope_map(p)
        self.classes = terrain.classify(p, self.slopes)
        self.findings = terrain.validate(p)
        self.findings += self._texture_findings(p)
        seed = p.flood_seed()
        q = terrain.quad_of_world(p, *seed)
        q = (min(max(q[0], 0), p.dim_x - 2), min(max(q[1], 0), p.dim_y - 2))
        self.reach = terrain.reachable(p, self.classes, q)
        self.analysis_changed.emit()

    def _texture_findings(self, p) -> list:
        """Cells where four textures meet: the client asserts on them."""
        if not self.tile_types or not p.tiles:
            return []
        bad = texlayers.overflow_cells(p.tiles, p.dim_x, p.dim_y, self.tile_types)
        out = []
        for gx, gy, n in bad:
            wx, wy = p.cell_centre(gx, gy)
            out.append(terrain.Finding(
                terrain.Finding.ERROR,
                f"Cell {gx},{gy} needs {n} texture layers; the client draws at most 3 and "
                f"crashes. Reopen the map with its donor assets loaded to repair it.", (wx, wy)))
        return out

    def select(self, idx: int):
        self.selected = idx
        if idx >= 0:
            self.selected_prop = -1
            self.prop_selection_changed.emit(-1)
        self.selection_changed.emit(idx)

    def select_prop(self, idx: int):
        self.selected_prop = idx
        if idx >= 0:
            self.selected = -1
            self.selection_changed.emit(-1)
        self.prop_selection_changed.emit(idx)

    def touch_settings(self):
        self.analyse()
        self.settings_changed.emit()


class HeightsCommand(QUndoCommand):
    """One brush stroke (or generator, or quantize): the whole field before and after."""

    def __init__(self, doc: Document, before: list, after: list, text: str):
        super().__init__(text)
        self.doc, self.before, self.after = doc, before, after
        self.first = True

    def _put(self, src):
        p = self.doc.project
        p.heights = [row[:] for row in src]
        self.doc.analyse()
        self.doc.heights_changed.emit(None)

    def redo(self):
        if self.first:          # the stroke already happened live
            self.first = False
            self.doc.analyse()
            return
        self._put(self.after)

    def undo(self):
        self._put(self.before)


class SpawnsCommand(QUndoCommand):
    def __init__(self, doc: Document, before: list, after: list, text: str, seed_before=None, seed_after=None):
        super().__init__(text)
        self.doc = doc
        self.before = [Spawn(s.x, s.y, s.angle) for s in before]
        self.after = [Spawn(s.x, s.y, s.angle) for s in after]

    def _put(self, src):
        self.doc.project.spawns = [Spawn(s.x, s.y, s.angle) for s in src]
        if self.doc.selected >= len(src):
            self.doc.select(len(src) - 1)
        self.doc.analyse()
        self.doc.spawns_changed.emit()

    def redo(self):
        self._put(self.after)

    def undo(self):
        self._put(self.before)


class TilesCommand(QUndoCommand):
    """One texture stroke: the per-cell tile layer before and after."""

    def __init__(self, doc: Document, before: list, after: list, text: str):
        super().__init__(text)
        self.doc = doc
        self.before = list(before)
        self.after = list(after)

    def _put(self, src):
        self.doc.project.tiles = list(src)
        self.doc.textures_changed.emit(None)

    def redo(self):
        self._put(self.after)

    def undo(self):
        self._put(self.before)


class PropsCommand(QUndoCommand):
    """Place, move or delete placed donor models."""

    def __init__(self, doc: Document, before: list, after: list, text: str):
        super().__init__(text)
        self.doc = doc
        self.before = copy_props_list(before)
        self.after = copy_props_list(after)

    def _put(self, src):
        self.doc.project.props = copy_props_list(src)
        if self.doc.selected_prop >= len(src):
            self.doc.select_prop(-1)
        self.doc.props_changed.emit()

    def redo(self):
        self._put(self.after)

    def undo(self):
        self._put(self.before)


class SettingCommand(QUndoCommand):
    def __init__(self, doc: Document, attr: str, before, after, text: str):
        super().__init__(text)
        self.doc, self.attr, self.before, self.after = doc, attr, before, after

    def redo(self):
        setattr(self.doc.project, self.attr, self.after)
        self.doc.touch_settings()

    def undo(self):
        setattr(self.doc.project, self.attr, self.before)
        self.doc.touch_settings()

    def id(self):
        return 7000 + hash(self.attr) % 1000

    def mergeWith(self, other):
        if isinstance(other, SettingCommand) and other.attr == self.attr:
            self.after = other.after
            return True
        return False


def copy_heights(p: MapProject) -> list:
    return [row[:] for row in p.heights]


def copy_spawns(p: MapProject) -> list:
    return [Spawn(s.x, s.y, s.angle) for s in p.spawns]


def copy_tiles(p: MapProject) -> list:
    return list(p.tiles or [0] * (p.dim_x * p.dim_y))


def copy_props_list(props) -> list:
    return [PlacedProp(file_id=q.file_id, model=q.model, x=q.x, y=q.y, z=q.z,
                       rot=tuple(q.rot), scale=q.scale, flags=q.flags,
                       outline=[tuple(v) for v in q.outline],
                       grid_locked=bool(getattr(q, "grid_locked", True))) for q in props]


def copy_props(p: MapProject) -> list:
    return copy_props_list(p.props or [])
