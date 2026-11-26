"""Build & Deploy: the map into Gw.dat, its folder into the world pack, and the panel at the archive.

Each step says what it is about to do and stops at the first refusal; nothing after a
refusal runs. The archive is backed up whole the first time this editor writes to it.
"""

from __future__ import annotations

import os
import traceback

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from .. import kryta, paths, pipeline
from . import icons, theme
from .theme import COLORS

STEPS = [
    ("check", "Check the map", "Spawn points, slopes and the flood seed"),
    ("build", "Build", "Assemble the map file and borrow the donor's look (reads Gw.dat, writes nothing)"),
    ("backup", "Back up Gw.dat", "A whole copy beside it, once, before the first write"),
    ("install", "Install into Gw.dat", "A new file id of its own, or an update of the one this map already has"),
    ("publish", "Publish into the world pack", "config/World/<pack>/maps/Custom/<id>/ and the map's NPCs, read when the world starts"),
    ("point", "Point the control panel at this archive",
     "manage.json MapArchiveFile, so the panel can add the map's walk graph to data/path-graphs.bin"),
]
ICON = {"todo": "\u25cb", "run": "\u25d4", "ok": "\u2714", "skip": "\u2013", "fail": "\u2716"}
ICOLOR = {"todo": "muted", "run": "accent2", "ok": "ok", "skip": "muted", "fail": "danger"}


class Worker(QObject):
    log = Signal(str)
    step = Signal(str, str, str)          # key, state, detail
    progress = Signal(int, int)
    finished = Signal(bool, str)

    def __init__(self, project, dat, repo, steps, findings):
        super().__init__()
        self.project, self.dat, self.repo, self.steps, self.findings = project, dat, repo, steps, findings

    def run(self):
        p, dat = self.project, self.dat
        try:
            if "check" in self.steps:
                self.step.emit("check", "run", "")
                errors = [f for f in self.findings if f.level == "error"]
                if errors:
                    for e in errors:
                        self.log.emit("ERROR: " + e.text)
                    self.step.emit("check", "fail", f"{len(errors)} error(s)")
                    return self.finished.emit(False, "The map has errors; see Problems.")
                warns = sum(f.level == "warning" for f in self.findings)
                self.step.emit("check", "ok", f"{warns} warning(s)" if warns else "clean")
            if "build" in self.steps:
                self.step.emit("build", "run", "")
                res = pipeline.build(p, dat, self.log.emit)
                self.step.emit("build", "ok", res.summary)
            if "backup" in self.steps:
                self.step.emit("backup", "run", "")
                made = pipeline.ensure_backup(dat, lambda a, b: self.progress.emit(a // 1048576, b // 1048576))
                self.step.emit("backup", "ok" if made else "skip",
                               os.path.basename(made) if made else "a backup already exists")
            if "install" in self.steps:
                self.step.emit("install", "run", "")
                r = pipeline.install(p, dat, self.log.emit)
                self.step.emit("install", "ok", f"file id 0x{r.file_id:X}, {r.verb} (rows {r.head_row}/{r.partner_row})")
                if p.path:
                    p.save()
            if "publish" in self.steps:
                self.step.emit("publish", "run", "")
                if not self.repo:
                    raise pipeline.PipelineError("No Kryta checkout found around the editor.")
                path = kryta.publish(self.repo, p)
                self.log.emit(f"wrote {path}")
                self.step.emit("publish", "ok", os.path.relpath(path, self.repo))
            if "point" in self.steps:
                self.step.emit("point", "run", "")
                if not self.repo:
                    raise pipeline.PipelineError("No Kryta checkout found around the editor.")
                if not dat:
                    self.step.emit("point", "skip", "no Gw.dat chosen")
                elif not os.path.exists(kryta.manage_config_path(self.repo)):
                    self.step.emit("point", "skip", "no tools/manage/manage.json yet; set MapArchiveFile by hand")
                else:
                    changed = kryta.point_manager_at(self.repo, dat)
                    for path in changed:
                        self.log.emit(f"updated {path} (a .kryta-cs.bak copy was kept)")
                    self.step.emit("point", "ok" if changed else "skip",
                                   "MapArchiveFile set; restart the control panel to use it" if changed
                                   else "the control panel already reads this archive")
            self.finished.emit(True, "")
        except pipeline.PipelineError as exc:
            self.log.emit("REFUSED: " + str(exc))
            self.finished.emit(False, str(exc))
        except Exception as exc:                                     # noqa: BLE001
            self.log.emit(traceback.format_exc())
            self.finished.emit(False, f"{type(exc).__name__}: {exc}")


class StepRow(QWidget):
    def __init__(self, title, desc):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.icon = QLabel(ICON["todo"])
        self.icon.setFixedWidth(22)
        f = QFont(self.font())
        f.setPointSize(f.pointSize() + 3)
        self.icon.setFont(f)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = QLabel(f"<b>{title}</b>")
        self.desc = QLabel(desc)
        self.desc.setProperty("muted", True)
        self.desc.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.desc)
        lay.addWidget(self.icon)
        lay.addLayout(col, 1)
        self.base_desc = desc
        self.set("todo", "")

    def set(self, state, detail):
        self.icon.setText(ICON[state])
        self.icon.setStyleSheet(f"color: {COLORS[ICOLOR[state]]}")
        self.desc.setText(detail if detail else self.base_desc)


class DeployWindow(QDialog):
    archive_chosen = Signal(str)

    def __init__(self, doc, prefs, parent=None):
        super().__init__(parent)
        self.doc, self.prefs = doc, prefs
        self.repo = paths.REPO_ROOT
        self.setWindowTitle("Build & Deploy")
        self.setWindowIcon(icons.icon("app"))
        self.setMinimumSize(760, 640)
        lay = QVBoxLayout(self)
        p = doc.project
        head = QLabel(f"{p.name}")
        head.setProperty("heading", True)
        lay.addWidget(head)
        self.sub = QLabel()
        self.sub.setProperty("muted", True)
        lay.addWidget(self.sub)

        arow = QHBoxLayout()
        arow.addWidget(QLabel("Gw.dat"))
        self.dat = QLineEdit(prefs.get("archive") or (kryta.server_archive(self.repo) if self.repo else "") or "")
        self.dat.setPlaceholderText("the archive your Kryta client runs from")
        self.dat.editingFinished.connect(self._inspect)
        b = QPushButton("Browse...")
        b.clicked.connect(self._browse)
        arow.addWidget(self.dat, 1)
        arow.addWidget(b)
        lay.addLayout(arow)
        self.info = QLabel()
        self.info.setProperty("muted", True)
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        fl = QVBoxLayout(frame)
        self.rows = {}
        for key, title, desc in STEPS:
            r = StepRow(title, desc)
            self.rows[key] = r
            fl.addWidget(r)
        lay.addWidget(frame)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        btns = QHBoxLayout()
        self.b_dry = QPushButton("Build only (dry run)")
        self.b_dry.clicked.connect(lambda: self._run(["check", "build"]))
        self.b_pub = QPushButton("Publish only")
        self.b_pub.setToolTip("Write the map's folder into the world pack (and point the control panel at the "
                              "archive) without building or installing again")
        self.b_pub.clicked.connect(lambda: self._run(["publish", "point"]))
        self.b_go = QPushButton("Build && Deploy")
        self.b_go.setProperty("accent", True)
        self.b_go.clicked.connect(self._deploy)
        self.b_verify = QPushButton("Verify compiled map")
        self.b_verify.setToolTip("After a client has loaded the map once: read back the mesh it compiled")
        self.b_verify.clicked.connect(self._verify)
        self.b_graph = QPushButton("Update walk graph")
        self.b_graph.setToolTip("After a client has loaded the map once: ask the control panel to copy the mesh "
                                "it compiled into data/path-graphs.bin, so the world can path on it")
        self.b_graph.clicked.connect(self._graph)
        self.b_unpub = QPushButton("Take off the world")
        self.b_unpub.setToolTip("Remove the map's folder from the world pack and put the pack's own map back; "
                                "the Gw.dat file stays")
        self.b_unpub.clicked.connect(self._unpublish)
        self.b_restore = QPushButton("Restore original Gw.dat")
        self.b_restore.clicked.connect(self._restore)
        for x in (self.b_dry, self.b_pub, self.b_go, self.b_verify, self.b_graph, self.b_unpub, self.b_restore):
            btns.addWidget(x)
        btns.addStretch(1)
        lay.addLayout(btns)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(theme.mono_font())
        lay.addWidget(self.out, 1)
        self.next = QLabel()
        self.next.setWordWrap(True)
        self.next.setTextFormat(Qt.RichText)
        lay.addWidget(self.next)
        self._thread = None
        self._inspect()
        self._show_next()

    # ------------------------------------------------------------ helpers

    def log(self, s):
        self.out.appendPlainText(s)
        self.out.verticalScrollBar().setValue(self.out.verticalScrollBar().maximum())

    def _browse(self):
        start = os.path.dirname(self.dat.text()) if self.dat.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "The Gw.dat your Kryta client runs from", start,
                                              "Guild Wars archive (Gw.dat *.dat);;All files (*)")
        if path:
            self.dat.setText(path)
            self._inspect()

    def _inspect(self):
        dat = self.dat.text().strip()
        p = self.doc.project
        self.sub.setText(f"client map id {p.map_id}, "
                         + ("no Gw.dat file yet" if p.file_id is None else f"Gw.dat file id 0x{p.file_id:X}"))
        if not dat:
            self.info.setText("Choose the Gw.dat your Kryta client runs from. The world will be pointed at the same one.")
            return
        try:
            info = pipeline.archive_info(dat)
        except Exception as exc:                                     # noqa: BLE001
            self.info.setText(f"<span style='color:{COLORS['danger']}'>{exc}</span>")
            return
        self.prefs["archive"] = dat
        self.archive_chosen.emit(dat)
        st = pipeline.status(p, dat)
        bk = os.path.exists(pipeline.backup_path(dat))
        srv = kryta.server_archive(self.repo) if self.repo else None
        srv_same = srv and os.path.normcase(os.path.abspath(srv)) == os.path.normcase(os.path.abspath(dat))
        pub = kryta.published_folder(self.repo, p) if self.repo else None
        self.info.setText(
            f"{info['bytes'] / 2**30:.2f} GiB, {info['entries']:,} entries, highest file id 0x{info['highest_plain_id']:X}; "
            f"next free id for a new map 0x{info['next_free_id']:X}.<br>"
            f"This map: <b>{st.text}</b>.<br>"
            f"Backup: {'present' if bk else 'none yet (made on the first install)'}.  "
            f"Control panel extracts from: {'this archive' if srv_same else (srv or 'not set')}.<br>"
            f"World pack: {'published (' + os.path.relpath(pub, self.repo) + ')' if pub else 'not published'}.")

    def _busy(self, on):
        for b in (self.b_dry, self.b_pub, self.b_go, self.b_verify, self.b_graph, self.b_unpub, self.b_restore):
            b.setEnabled(not on)

    def _run(self, steps):
        dat = self.dat.text().strip()
        for k, r in self.rows.items():
            r.set("todo" if k in steps else "skip", "" if k in steps else "not part of this run")
        self.out.clear()
        self._busy(True)
        self.doc.analyse()
        th = QThread(self)
        w = Worker(self.doc.project, dat, self.repo, steps, list(self.doc.findings))
        w.moveToThread(th)
        th.started.connect(w.run)
        w.log.connect(self.log)
        w.step.connect(lambda k, s, d: self.rows[k].set(s, d))
        w.progress.connect(self._progress)
        w.finished.connect(lambda ok, msg: self._done(ok, msg, th))
        w.finished.connect(th.quit)
        self._thread, self._worker = th, w
        th.start()

    def _progress(self, a, b):
        self.bar.setVisible(True)
        self.bar.setRange(0, b)
        self.bar.setValue(a)
        self.bar.setFormat(f"backing up Gw.dat: {a:,} / {b:,} MiB")

    def _done(self, ok, msg, th):
        self.bar.setVisible(False)
        self._busy(False)
        self._inspect()
        self.doc.settings_changed.emit()
        if not ok:
            QMessageBox.warning(self, "Stopped", msg)
        self._show_next()

    def _deploy(self):
        dat = self.dat.text().strip()
        if not dat:
            return QMessageBox.information(self, "Gw.dat", "Choose the Gw.dat first.")
        if not os.path.exists(pipeline.backup_path(dat)):
            size = os.path.getsize(dat) / 2**30 if os.path.exists(dat) else 0
            if QMessageBox.question(
                    self, "First write to this archive",
                    f"Before the first write, the editor copies the whole archive to\n"
                    f"{pipeline.backup_path(dat)}\n({size:.1f} GiB). Guild Wars must be closed.\n\nContinue?"
            ) != QMessageBox.Yes:
                return
        self._run([k for k, _t, _d in STEPS])

    def _verify(self):
        dat = self.dat.text().strip()
        self.out.clear()
        try:
            st = pipeline.status(self.doc.project, dat)
            self.log(st.text)
            if not st.installed or not st.compiled:
                self.log("Nothing to read back yet: load the map in the client once (it compiles the mesh then).")
                return
            lines, bad = pipeline.readback(self.doc.project, dat, self.log)
            for line in lines:
                self.log(line)
            self.log(f"\n{len(bad)} check(s) failed" if bad else "\nreadback: every check passed")
        except Exception as exc:                                     # noqa: BLE001
            self.log(f"{type(exc).__name__}: {exc}")

    def _graph(self):
        if not self.repo:
            return
        p = self.doc.project
        self.out.clear()
        if not kryta.published_folder(self.repo, p):
            self.log("Publish the map first: the control panel finds the map's archive file id in the world pack.")
            return
        self.log(f"asking the control panel ({kryta.manager_url(self.repo)}) to dump map {p.map_id} ...")
        try:
            r = kryta.dump_path_graph(self.repo, p.map_id)
        except RuntimeError as exc:
            self.log(f"refused: {exc}")
            if "absent" in str(exc) or "not path data" in str(exc):
                self.log("Load the map in the client once first: it compiles the walkable mesh into Gw.dat then.")
            return
        self.log(f"map {r.get('map_id')} graph from blob 0x{int(r.get('file_id', 0)):X} written to "
                 f"{r.get('cache')} ({r.get('graph_count')} graph(s), {r.get('bytes'):,} bytes).")
        self.log("Restart the world so it reads the updated cache.")

    def _unpublish(self):
        if not self.repo:
            return
        if QMessageBox.question(self, "Take off the world",
                                f"Remove map {self.doc.project.map_id}'s folder from the world pack and put the "
                                f"pack's own map {self.doc.project.map_id} back?") != QMessageBox.Yes:
            return
        try:
            what = kryta.unpublish(self.repo, self.doc.project)
        except Exception as exc:                                     # noqa: BLE001
            return self.log(f"{type(exc).__name__}: {exc}")
        self.log(f"{what}. The world reads it at its next start.")
        self._inspect()

    def _restore(self):
        dat = self.dat.text().strip()
        if not os.path.exists(pipeline.backup_path(dat)):
            return QMessageBox.information(self, "Restore", "There is no backup beside this archive.")
        if QMessageBox.question(self, "Restore original Gw.dat",
                                "Copy the backup over the archive? Every map installed since is removed from it.") \
                != QMessageBox.Yes:
            return
        try:
            pipeline.restore_backup(dat, lambda a, b: self._progress(a // 1048576, b // 1048576))
            self.log("restored")
        except Exception as exc:                                     # noqa: BLE001
            self.log(str(exc))
        self.bar.setVisible(False)
        self._inspect()

    def _show_next(self):
        p = self.doc.project
        aliases = kryta.tp_slots(self.repo).get(p.map_id, []) if self.repo else []
        if aliases:
            go = f"log in and type <code>/tp {aliases[0]}</code>"
            want = kryta.tp_slot_types(self.repo).get(p.map_id)
            if want is not None and want != p.map_type:
                go += (f" (note: that slot travels as map type {want}, this map says {p.map_type}; "
                       f"match them in the Map panel if the load misbehaves)")
        else:
            go = (f"log in and travel to map {p.map_id}. <code>/tp</code> only knows the fixed travel "
                  f"slots, so pick one (Kamadan is the default) in the Map panel to reach it with <code>/tp</code>")
        self.next.setText(
            f"<b>Then, to walk it:</b> start the stack (<code>python run_server.py</code>; the world reads the "
            f"map pack as it starts), {go}. The first load makes the client compile the walkable mesh into "
            f"Gw.dat. Close the client, click <b>Update walk graph</b> (the control panel must be running) and "
            f"restart the world so it paths on that mesh, then go back in. <b>Verify compiled map</b> checks what "
            f"the client built.")
