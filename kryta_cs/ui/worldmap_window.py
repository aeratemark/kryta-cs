"""World Map: the client's area table over its world atlas. READ-ONLY.

Left, every s_missionClientData row (id, name, world, region, type, both footprints)
with a world filter, a text filter and a "free-looking" column that is a HEURISTIC
(kryta_cs.worldmap.free_reasons). Centre, the selected world's atlas, stitched from
Gw.dat's tiles, with every row's OUTPOST footprint (+0x48) outlined and its GAME
footprint (+0x58) dashed where it differs. Scene coordinates are terrain cells.

Reading Gw.exe / Gw.dat and stitching runs in a QThread; results are cached under
~/.kryta_cs/worldmap-cache/<stamp>/ so the second open is instant. Nothing here writes
to Gw.exe or Gw.dat.
"""

from __future__ import annotations

import os
import traceback

from PySide6.QtCore import QObject, QPointF, QRectF, QSortFilterProxyModel, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsItem,
                               QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QProgressBar,
                               QPushButton, QSplitter, QStackedWidget, QTableView, QVBoxLayout, QWidget)

from .. import kryta, paths
from .. import worldmap as wm
from . import icons
from .theme import COLORS

COLS = ["ID", "Name", "World", "Region", "Type", "Rect A (outpost)", "Rect B (game)", "Free-looking (heuristic)"]
ROW_ROLE = Qt.UserRole + 1
LABEL_MIN_PX = 48            # a footprint narrower than this on screen gets no name


def fmt_rect(r):
    return "-" if not wm.rect_ok(r) else f"{r[0]},{r[1]} - {r[2]},{r[3]}"


# ------------------------------------------------------------------ worker

class Job(QObject):
    """Runs one callable off the GUI thread. fn(progress, log) -> result."""
    progress = Signal(str, int, int)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            res = self.fn(lambda what, a, b: self.progress.emit(what, a, b), self.log.emit)
        except Exception as exc:            # reported in the window, never raised into Qt
            self.failed.emit(f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}")
            return
        self.done.emit(res)


# ------------------------------------------------------------------ map view

class FootprintItem(QGraphicsRectItem):
    """One distinct footprint rect, and every row that has it."""

    def __init__(self, rect, rows, dashed):
        x0, y0, x1, y1 = rect
        super().__init__(QRectF(x0, y0, x1 - x0, y1 - y0))
        self.cells = rect
        self.rows = rows
        self.dashed = dashed
        self.selected_row = None
        self.setAcceptHoverEvents(False)
        self.restyle()

    def restyle(self):
        sel = self.selected_row is not None
        color = QColor(COLORS["marker2"] if sel else COLORS["marker"])
        pen = QPen(color, 3 if sel else 1.5)
        pen.setCosmetic(True)
        if self.dashed:
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        fill = QColor(color)
        fill.setAlpha(50 if sel else 0)
        self.setBrush(QBrush(fill))
        self.setZValue(10 if sel else (2 if self.dashed else 3))

    def label(self):
        names = []
        for r in self.rows:
            n = r.name or f"#{r.id}"
            if n not in names:
                names.append(n)
        return " / ".join(names[:3]) + (" ..." if len(names) > 3 else "")


class LabelItem(QGraphicsItem):
    """A footprint's name, a fixed size on screen, on a dark plate so it reads over art."""

    def __init__(self, text, anchor):
        super().__init__()
        self.text = text
        self.setPos(anchor)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setZValue(20)
        self.font = QFont("Tahoma", 8)
        self.font.setBold(True)
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font)
        self._w = fm.horizontalAdvance(text) + 6
        self._h = fm.height() + 2

    def boundingRect(self):
        return QRectF(2, 2, self._w, self._h)

    def paint(self, p, _opt, _w=None):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 160))
        p.drawRect(self.boundingRect())
        p.setPen(QColor(COLORS["marker"]))
        p.setFont(self.font)
        p.drawText(self.boundingRect(), Qt.AlignCenter, self.text)


class AtlasView(QGraphicsView):
    hovered = Signal(float, float)
    clicked = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setBackgroundBrush(QColor(COLORS["viewport"]))
        self.labels = []
        self._press = None

    def wheelEvent(self, ev):
        f = 1.25 if ev.angleDelta().y() > 0 else 0.8
        z = self.transform().m11() * f
        if 0.01 <= z <= 32:
            self.scale(f, f)
            self.update_labels()

    def zoom(self):
        return self.transform().m11()

    def fit(self, rect: QRectF):
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.update_labels()

    def update_labels(self):
        z = self.zoom()
        for item, lab in self.labels:
            lab.setVisible(item.rect().width() * z >= LABEL_MIN_PX)

    def mouseMoveEvent(self, ev):
        p = self.mapToScene(ev.position().toPoint())
        self.hovered.emit(p.x(), p.y())
        super().mouseMoveEvent(ev)

    def mousePressEvent(self, ev):
        self._press = ev.position()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        if self._press is not None and (ev.position() - self._press).manhattanLength() < 4 \
                and ev.button() == Qt.LeftButton:
            p = self.mapToScene(ev.position().toPoint())
            self.clicked.emit(p.x(), p.y())
        self._press = None


# ------------------------------------------------------------------ the window

class WorldMapWindow(QMainWindow):
    def __init__(self, prefs: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("World Map (read-only)")
        self.setWindowIcon(icons.icon("world"))
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.resize(1500, 900)
        self.prefs = prefs
        self.rows = []
        self.index = None
        self.meta = None
        self.items = []
        self.selected_id = None
        self._threads = []
        self._cycle = (None, 0)
        self.catalogue = set(kryta.read_catalogue(paths.REPO_ROOT)) if paths.REPO_ROOT else set()

        # ---- left: the table
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        fl = QHBoxLayout()
        fl.addWidget(QLabel("World:"))
        self.f_world = QComboBox()
        self.f_world.addItem("All", None)
        fl.addWidget(self.f_world, 1)
        ll.addLayout(fl)
        self.f_text = QLineEdit()
        self.f_text.setPlaceholderText("Filter by id or name...")
        ll.addWidget(self.f_text)
        self.f_free = QCheckBox("Only free-looking rows (heuristic)")
        ll.addWidget(self.f_free)
        self.model = QStandardItemModel(0, len(COLS))
        self.model.setHorizontalHeaderLabels(COLS)
        self.proxy = RowFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(18)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        ll.addWidget(self.table, 1)
        self.count_label = QLabel("")
        self.count_label.setProperty("muted", True)
        ll.addWidget(self.count_label)

        # ---- centre: the atlas
        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Continent (world):"))
        self.c_world = QComboBox()
        self.c_world.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.c_world.setMinimumContentsLength(26)
        bar.addWidget(self.c_world)
        bar.addWidget(QLabel("Tier:"))
        self.c_tier = QComboBox()
        for t in wm.TIERS:
            self.c_tier.addItem(t, t)
        bar.addWidget(self.c_tier)
        self.b_fit = QPushButton("Fit")
        bar.addWidget(self.b_fit)
        bar.addStretch(1)
        cl.addLayout(bar)
        self.stack = QStackedWidget()
        self.view = AtlasView()
        frame = QFrame()
        frame.setFrameShape(QFrame.Panel)
        frame.setFrameShadow(QFrame.Sunken)
        frame.setLineWidth(2)
        fv = QVBoxLayout(frame)
        fv.setContentsMargins(2, 2, 2, 2)
        fv.addWidget(self.view)
        self.stack.addWidget(frame)
        self.message = QWidget()
        ml = QVBoxLayout(self.message)
        ml.addStretch(1)
        self.msg_label = QLabel("")
        self.msg_label.setWordWrap(True)
        self.msg_label.setAlignment(Qt.AlignCenter)
        ml.addWidget(self.msg_label)
        self.b_choose = QPushButton("Choose Gw.dat...")
        self.b_choose.setVisible(False)
        ml.addWidget(self.b_choose, 0, Qt.AlignCenter)
        ml.addStretch(1)
        self.stack.addWidget(self.message)
        cl.addWidget(self.stack, 1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(centre)
        split.setSizes([560, 940])
        self.setCentralWidget(split)

        # ---- status bar
        sb = self.statusBar()
        self.s_state = QLabel("")
        self.s_tiles = QLabel("")
        self.s_coord = QLabel("cell -")
        self.s_prog = QProgressBar()
        self.s_prog.setMaximumWidth(220)
        self.s_prog.setVisible(False)
        sb.addWidget(self.s_state, 1)
        sb.addPermanentWidget(self.s_tiles)
        sb.addPermanentWidget(self.s_coord)
        sb.addPermanentWidget(self.s_prog)

        # ---- wiring
        self.f_world.currentIndexChanged.connect(self._refilter)
        self.f_text.textChanged.connect(self._refilter)
        self.f_free.toggled.connect(self._refilter)
        self.table.selectionModel().selectionChanged.connect(self._table_selected)
        self.c_world.currentIndexChanged.connect(self._world_changed)
        self.c_tier.currentIndexChanged.connect(self._world_changed)
        self.b_fit.clicked.connect(self._fit)
        self.b_choose.clicked.connect(self._choose_archive)
        self.view.hovered.connect(self._hover)
        self.view.clicked.connect(self._map_clicked)

        self.start()

    # ------------------------------------------------------------ sources

    def sources(self):
        dat = self.prefs.get("archive") or ""
        exe = self.prefs.get("client_exe") or wm.default_exe_for(dat)
        return exe, dat

    def _show_message(self, text, choose=False):
        self.msg_label.setText(text)
        self.b_choose.setVisible(choose)
        self.stack.setCurrentWidget(self.message)

    def _choose_archive(self):
        start = os.path.dirname(self.prefs.get("archive") or "") or "C:\\"
        path, _ = QFileDialog.getOpenFileName(self, "The client's Gw.dat (read only)", start,
                                              "Guild Wars archive (Gw.dat);;All files (*)")
        if path:
            self.prefs["archive"] = path
            paths.save_prefs(self.prefs)
            self.start()

    def start(self):
        exe, dat = self.sources()
        if not dat or not os.path.isfile(dat):
            return self._show_message(
                "No Gw.dat is configured, so there is no world map to show.\n\n"
                "Choose the client's Gw.dat (the one Build & Deploy uses). The client exe is taken "
                "from the same folder. Both are only read.", choose=True)
        if not exe or not os.path.isfile(exe):
            return self._show_message(
                f"No client exe found beside {dat}.\n\nThe area table and the atlas index are read "
                "out of Gw.exe; put it next to Gw.dat, or set \"client_exe\" in "
                f"{os.path.join(paths.user_dir(), paths.PREFS_FILE)}.", choose=True)
        self._show_message(f"Reading the area table and atlas index...\n{exe}\n{dat}")
        self.s_state.setText(f"{exe}  |  {dat}")

        def load(progress, log):
            rows, info = wm.load_areas_cached(exe, dat, lambda a, b: progress("names", a, b))
            index = wm.load_atlas_cached(exe, dat)
            return rows, info, index
        self._run(load, self._loaded)

    def _run(self, fn, on_done):
        th = QThread(self)
        job = Job(fn)
        job.moveToThread(th)
        th.started.connect(job.run)
        job.progress.connect(self._progress)
        job.log.connect(lambda s: self.s_state.setText(s))
        job.done.connect(on_done)
        job.failed.connect(self._failed)
        job.done.connect(th.quit)
        job.failed.connect(th.quit)
        th.finished.connect(lambda: self._threads.remove((th, job)) if (th, job) in self._threads else None)
        self._threads.append((th, job))
        self.s_prog.setVisible(True)
        self.s_prog.setRange(0, 0)
        th.start()

    def busy(self):
        return bool(self._threads)

    def _progress(self, what, a, b):
        self.s_prog.setVisible(True)
        self.s_prog.setRange(0, max(1, b))
        self.s_prog.setValue(a)
        self.s_prog.setFormat(f"{what} %v/%m")

    def _failed(self, text):
        self.s_prog.setVisible(False)
        self._show_message("Could not read the world map:\n\n" + text.split("\n")[0], choose=True)
        self.s_state.setText(text.split("\n")[0])

    # ------------------------------------------------------------ loaded

    def _loaded(self, res):
        self.rows, info, self.index = res
        self.s_prog.setVisible(False)
        self.free = {r.id: wm.free_reasons(r, self.catalogue) for r in self.rows}
        self.model.setRowCount(0)
        for r in self.rows:
            cells = [r.id, r.name or "", r.continent, r.region, r.type, fmt_rect(r.rect_a),
                     fmt_rect(r.rect_b), ", ".join(self.free[r.id])]
            items = []
            for c in cells:
                it = QStandardItem()
                it.setData(c, Qt.DisplayRole)
                it.setData(r.id, ROW_ROLE)
                items.append(it)
            self.model.appendRow(items)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(1, 190)
        worlds = sorted({r.continent for r in self.rows} | set(self.index.worlds()))
        self.f_world.blockSignals(True)
        self.c_world.blockSignals(True)
        for w in worlds:
            self.f_world.addItem(wm.world_label(w), w)
        for w in self.index.worlds():
            n = sum(1 for r in self.rows if r.continent == w)
            self.c_world.addItem(f"{wm.world_label(w)}  ({n} rows)", w)
        self.f_world.blockSignals(False)
        self.c_world.blockSignals(False)
        self._refilter()
        self.s_state.setText(f"{len(self.rows)} area rows (table at VA 0x{info['base_va']:08X}, "
                             f"{'corroborated by code' if info['corroborated_by_code'] else 'structural only'}), "
                             f"client build {self.index.build}")
        self._world_changed()

    def _refilter(self, *_):
        self.proxy.world = self.f_world.currentData()
        self.proxy.text = self.f_text.text().strip().lower()
        self.proxy.free_only = self.f_free.isChecked()
        self.proxy.rows = {r.id: r for r in self.rows}
        self.proxy.free = getattr(self, "free", {})
        self.proxy.invalidateFilter()
        self.count_label.setText(f"{self.proxy.rowCount()} of {len(self.rows)} rows shown")

    # ------------------------------------------------------------ world

    def current_world(self):
        return self.c_world.currentData()

    def _world_changed(self, *_):
        w, tier = self.current_world(), self.c_tier.currentData()
        if w is None or self.index is None:
            return
        self._show_message(f"Stitching {wm.world_label(w)}, {tier} tier...\n(first time only, then cached)")
        exe, dat = self.sources()
        index = self.index

        def stitch(progress, log):
            return wm.stitch_world(exe, dat, index, tier, w, lambda a, b: progress("tiles", a, b), log)
        self._run(stitch, self._stitched)

    def _stitched(self, meta):
        if meta.get("world") != self.current_world() or meta.get("tier") != self.c_tier.currentData():
            return                      # the picker moved on while this was running
        self.s_prog.setVisible(False)
        self.meta = meta
        sc = self.view.scene()
        sc.clear()
        self.view.labels = []
        self.items = []
        pix = QPixmap(meta["png"])
        bg = QGraphicsPixmapItem(pix)
        bg.setScale(meta["scale"])
        bg.setTransformationMode(Qt.SmoothTransformation)
        bg.setZValue(0)
        sc.addItem(bg)
        border = sc.addRect(QRectF(0, 0, meta["width"], meta["height"]), QPen(QColor(COLORS["vtext"]), 0))
        border.setZValue(1)
        self._add_footprints(meta["world"])
        sc.setSceneRect(QRectF(-256, -256, meta["width"] + 512, meta["height"] + 512))
        self.stack.setCurrentIndex(0)
        miss = len(meta["missing"])
        bad = len(meta["undecodable"])
        self.s_tiles.setText(f"{meta['tier']} w{meta['world']}: {meta['tiles']} tiles, {meta['decoded']} drawn, "
                             f"{miss} not in this Gw.dat, {bad} undecodable  |  "
                             f"{meta['width']}x{meta['height']} cells, 1 px = {meta['scale']} cell(s)")
        self._fit()
        if self.selected_id is not None:
            self._highlight(self.selected_id, center=True)

    def _add_footprints(self, world):
        groups = {}
        for r in self.rows:
            if r.continent != world:
                continue
            if wm.rect_ok(r.rect_a):
                groups.setdefault((tuple(r.rect_a), False), []).append(r)
            if wm.rect_ok(r.rect_b) and tuple(r.rect_b) != tuple(r.rect_a):
                groups.setdefault((tuple(r.rect_b), True), []).append(r)
        sc = self.view.scene()
        for (rect, dashed), rows in sorted(groups.items(), key=lambda kv: -(kv[0][0][2] - kv[0][0][0])
                                           * (kv[0][0][3] - kv[0][0][1])):
            it = FootprintItem(rect, rows, dashed)
            sc.addItem(it)
            self.items.append(it)
            if not dashed:
                lab = LabelItem(it.label(), QPointF(rect[0], rect[1]))
                sc.addItem(lab)
                self.view.labels.append((it, lab))
        self.view.update_labels()

    def _fit(self):
        if self.meta:
            self.view.fit(QRectF(0, 0, self.meta["width"], self.meta["height"]))

    def _hover(self, x, y):
        if not self.meta:
            return
        cx, cy = int(x // 1), int(y // 1)
        inside = 0 <= cx < self.meta["width"] and 0 <= cy < self.meta["height"]
        chunk = f"  chunk ({cx // wm.CHUNK_SIZE},{cy // wm.CHUNK_SIZE})" if inside else ""
        self.s_coord.setText(f"cell {cx}, {cy}{chunk}" if inside else "cell -")

    # ------------------------------------------------------------ selection

    def _map_clicked(self, x, y):
        hits = [it for it in self.items if it.rect().contains(QPointF(x, y))]
        if not hits:
            return
        hits.sort(key=lambda it: it.rect().width() * it.rect().height())
        rows = [r for it in hits for r in it.rows]
        key = (int(x) // 16, int(y) // 16)
        last, n = self._cycle
        n = n + 1 if last == key else 0
        self._cycle = (key, n)
        self.select_row(rows[n % len(rows)].id, center=False)

    def select_row(self, row_id, center=True):
        """Select a row in the table (switching world if needed) and on the map."""
        self.selected_id = row_id
        for i in range(self.proxy.rowCount()):
            idx = self.proxy.index(i, 0)
            if idx.data(ROW_ROLE) == row_id:
                self.table.selectionModel().blockSignals(True)
                self.table.selectRow(i)
                self.table.selectionModel().blockSignals(False)
                self.table.scrollTo(idx)
                break
        self._highlight(row_id, center)

    def _table_selected(self, *_):
        idx = self.table.selectionModel().selectedRows()
        if not idx:
            return
        rid = idx[0].data(ROW_ROLE)
        self.selected_id = rid
        row = self.rows[rid] if rid < len(self.rows) and self.rows[rid].id == rid else None
        if row is not None and row.continent != self.current_world():
            i = self.c_world.findData(row.continent)
            if i >= 0:
                self.c_world.setCurrentIndex(i)     # re-stitches, then highlights
                return
        self._highlight(rid, True)

    def _highlight(self, rid, center):
        target = None
        for it in self.items:
            hit = any(r.id == rid for r in it.rows)
            it.selected_row = rid if hit else None
            it.restyle()
            if hit and (target is None or not it.dashed):
                target = it
        row = next((r for r in self.rows if r.id == rid), None)
        if row is not None:
            self.s_state.setText(f"#{row.id} {row.name}  world {row.continent}  region {row.region}  type {row.type}  "
                                 f"A {fmt_rect(row.rect_a)}  B {fmt_rect(row.rect_b)}"
                                 + (f"  [free-looking: {', '.join(self.free[row.id])}]" if self.free.get(row.id) else ""))
        if target is not None and center:
            self.view.centerOn(target)

    def zoom_to_cells(self, x0, y0, x1, y1):
        self.view.fit(QRectF(x0, y0, x1 - x0, y1 - y0))

    def closeEvent(self, ev):
        for th, _job in list(self._threads):
            th.quit()
            th.wait(2000)
        super().closeEvent(ev)


class RowFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.world = None
        self.text = ""
        self.free_only = False
        self.rows = {}
        self.free = {}

    def filterAcceptsRow(self, source_row, parent):
        m = self.sourceModel()
        rid = m.index(source_row, 0, parent).data(ROW_ROLE)
        r = self.rows.get(rid)
        if r is None:
            return True
        if self.world is not None and r.continent != self.world:
            return False
        if self.free_only and not self.free.get(rid):
            return False
        if self.text:
            return self.text in (r.name or "").lower() or self.text == str(r.id)
        return True
