"""Gameplay > Edit Scripts: the pack's Lua, in a window of its own, as the Construction Set's Script Edit.

Left, every Lua file the world reads, by what it is for: the runtime, the dialogs (one per NPC), the
behaviours and the skills. Right, the file, highlighted, with line numbers. Under it, the problems
Check found, each a double-click away from its line.

- The file on disk is the content. Nothing is kept anywhere else; Save writes the text you see, with
  the file's own line endings.
- Several files can be open and unsaved at once; each keeps its own undo history. Check reads the
  unsaved text in place of the file, so a dialog can be checked against a runtime change not yet saved.
- Syntax is checked as you type. Check (F7) runs the file the way the world loads it, when `lupa` is
  installed; Check All (Shift+F7) runs every kind. A file with a syntax error is not saved without
  asking, and a file changed on disk since it was opened is not overwritten without asking.
- `open_file(rel, line)` opens a file at a line: the Dialogue window and the NPC records use it for
  their "Edit script" buttons.

The world reads the pack once, at start-up: restart it after saving for the change to take effect.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QSplitter, QToolBar, QTreeWidget, QTreeWidgetItem)

from .. import paths
from ..lua import check as luacheck
from ..lua import pack as luapack
from ..lua.pack import read_script, write_script
from . import icons, theme
from .chrome import CaptionFrame
from .lua_editor import LuaEditor, new_document

ROLE_REL = Qt.UserRole


def default_pack(prefs: dict) -> str | None:
    """The pack the world serves (worldserver.json `WorldPack`), else the folder chosen before."""
    if paths.REPO_ROOT:
        try:
            from .. import kryta
            pack = kryta.pack_dir(paths.REPO_ROOT)
            if os.path.isdir(pack):
                return pack
        except Exception:
            pass
    chosen = prefs.get("script_pack")
    if chosen and os.path.isdir(chosen):
        return chosen
    return None


class Buffer:
    """One file as the window holds it."""

    def __init__(self, info: luapack.ScriptFile, text: str, newline: str = "\n", bom: bool = False,
                 mtime: float | None = None, is_new: bool = False):
        self.info = info
        self.doc = new_document(text)
        self.newline = newline
        self.bom = bom
        self.mtime = mtime
        self.is_new = is_new
        self.cursor = 0
        self.live: list[luacheck.Problem] = []      # syntax, as typed
        self.checked: list[luacheck.Problem] = []   # the last Check
        if is_new:
            self.doc.setModified(True)

    @property
    def rel(self) -> str:
        return self.info.rel

    @property
    def text(self) -> str:
        return self.doc.toPlainText()

    @property
    def modified(self) -> bool:
        return self.doc.isModified()


class ScriptWindow(QMainWindow):
    def __init__(self, prefs: dict, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.pack = default_pack(prefs)
        self.files: dict[str, luapack.ScriptFile] = {}
        self.buffers: dict[str, Buffer] = {}
        self.current: Buffer | None = None
        self._pack_problems: dict[str, list] = {}     # the last Check All, by file
        self.setWindowTitle("Script Edit")
        self.setWindowIcon(icons.icon("app"))
        self.resize(1200, 820)

        self._syntax_timer = QTimer(self)
        self._syntax_timer.setSingleShot(True)
        self._syntax_timer.setInterval(300)
        self._syntax_timer.timeout.connect(self._live_syntax)

        self._build()
        self.reload_list()

    # ------------------------------------------------------------------------------- layout
    def _act(self, text, slot, key=None, tip=None, icon=None) -> QAction:
        a = QAction(text, self)
        if icon:
            a.setIcon(icons.icon(icon))
        if key:
            a.setShortcut(QKeySequence(key))
            a.setShortcutContext(Qt.WindowShortcut)
        if tip:
            a.setToolTip(f"{tip} ({key})" if key else tip)
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build(self):
        self.a_new = self._act("&New Dialog...", self.new_dialog, "Ctrl+N",
                               "A new dialogs/<npc>.lua for an NPC on a map", icon="new")
        self.a_save = self._act("&Save", self.save_current, "Ctrl+S", "Write this file", icon="save")
        self.a_save_all = self._act("Save A&ll", self.save_all, "Ctrl+Shift+S", "Write every changed file")
        self.a_revert = self._act("&Revert", self.revert_current, None, "Read this file from disk again")
        self.a_check = self._act("&Check", self.check_current, "F7",
                                 "This file, as the world would load it")
        self.a_check_all = self._act("Check &All", self.check_all, "Shift+F7",
                                     "Every Lua file of the pack, as the world would load them")
        self.a_find = self._act("&Find", self.focus_find, "Ctrl+F", "Find in this file")
        self.a_find_next = self._act("Find Next", lambda: self.find(False), "F3")
        self.a_find_prev = self._act("Find Previous", lambda: self.find(True), "Shift+F3")
        self.a_goto = self._act("&Go to Line...", self.go_to_line, "Ctrl+G")
        self.a_comment = self._act("Comment Lines", lambda: self.editor.toggle_comment(), "Ctrl+/",
                                   "Comment or uncomment the selected lines")
        self.a_reload = self._act("Re&load List", self.reload_list, "F5", "Read the pack's file list again")
        self.a_pack = self._act("Choose &Pack Folder...", self.choose_pack, None,
                                "The world pack folder whose Lua to edit")

        bar = QToolBar("Script")
        bar.setMovable(False)
        for a in (self.a_new, self.a_save, self.a_save_all, self.a_revert):
            bar.addAction(a)
        bar.addSeparator()
        bar.addAction(self.a_check)
        bar.addAction(self.a_check_all)
        bar.addSeparator()
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find")
        self.find_box.setMaximumWidth(220)
        self.find_box.returnPressed.connect(lambda: self.find(False))
        bar.addWidget(self.find_box)
        self.addToolBar(bar)

        menu = self.menuBar()
        f = menu.addMenu("&File")
        for a in (self.a_new, self.a_save, self.a_save_all, self.a_revert):
            f.addAction(a)
        f.addSeparator()
        f.addAction(self.a_pack)
        f.addAction(self.a_reload)
        f.addSeparator()
        f.addAction(self._act("&Close", self.close, None))
        e = menu.addMenu("&Edit")
        for a in (self.a_find, self.a_find_next, self.a_find_prev, self.a_goto, self.a_comment):
            e.addAction(a)
        c = menu.addMenu("&Check")
        c.addAction(self.a_check)
        c.addAction(self.a_check_all)

        # left: the file list
        left = CaptionFrame("Scripts", compact=True)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter")
        self.filter.textChanged.connect(self._apply_filter)
        left.add(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.itemActivated.connect(self._tree_activated)
        self.tree.itemClicked.connect(self._tree_activated)
        left.add(self.tree, 1)

        # right: the text and the problems
        self.editor = LuaEditor()
        self.editor.textChanged.connect(self._text_changed)
        self.editor.cursor_moved.connect(self._cursor_moved)
        self.text_frame = CaptionFrame("", compact=True)
        self.text_frame.add(self.editor, 1)

        self.problems = QTreeWidget()
        self.problems.setHeaderLabels(["File", "Line", "Problem"])
        self.problems.setRootIsDecorated(False)
        self.problems.setColumnWidth(0, 220)
        self.problems.setColumnWidth(1, 50)
        self.problems.itemActivated.connect(self._problem_activated)
        prob_frame = CaptionFrame("Problems", compact=True)
        prob_frame.add(self.problems, 1)

        right = QSplitter(Qt.Vertical)
        right.addWidget(self.text_frame)
        right.addWidget(prob_frame)
        right.setStretchFactor(0, 4)
        right.setStretchFactor(1, 1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 940])
        self.setCentralWidget(split)

        self.pos_label = QLabel("")
        self.lua_label = QLabel("")
        self.statusBar().addPermanentWidget(self.pos_label)
        self.statusBar().addPermanentWidget(self.lua_label)
        ok, text = luacheck.lupa_status()
        self.lua_label.setText(("Lua 5.4 check" if ok else "Syntax check only"))
        self.lua_label.setToolTip(text)
        self._set_editing(False)

    def _set_editing(self, on: bool):
        self.editor.setEnabled(on)
        for a in (self.a_save, self.a_revert, self.a_check, self.a_find, self.a_find_next,
                  self.a_find_prev, self.a_goto, self.a_comment):
            a.setEnabled(on)
        self.a_check_all.setEnabled(self.pack is not None)
        self.a_new.setEnabled(self.pack is not None)

    # ----------------------------------------------------------------------------- the list
    def choose_pack(self):
        start = self.pack or paths.REPO_ROOT or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "The world pack folder (holds scripts/runtime.lua)", start)
        if not folder:
            return
        if not self._close_buffers():
            return
        self.pack = folder
        self.prefs["script_pack"] = folder
        try:
            paths.save_prefs(self.prefs)
        except OSError:
            pass
        self.reload_list()

    def reload_list(self):
        self.tree.clear()
        self.files.clear()
        if not self.pack:
            self.statusBar().showMessage("No world pack found: File > Choose Pack Folder.")
            self._set_editing(self.current is not None)
            return
        try:
            files = luapack.catalogue(self.pack)
        except (OSError, ValueError) as err:
            self.statusBar().showMessage(f"The pack's file list could not be read: {err}")
            files = []
        groups = {}
        for kind in luapack.KINDS:
            g = QTreeWidgetItem([luapack.KIND_TITLES[kind]])
            g.setFlags(Qt.ItemIsEnabled)
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            self.tree.addTopLevelItem(g)
            groups[kind] = g
        for info in files:
            self.files[info.rel] = info
            item = QTreeWidgetItem([info.label])
            item.setData(0, ROLE_REL, info.rel)
            tip = info.rel + (f"\n{info.note}" if info.note else "")
            if not info.exists:
                tip += "\nThe file does not exist yet."
            item.setToolTip(0, tip)
            groups[info.kind].addChild(item)
        for rel, buf in self.buffers.items():          # new, unsaved files
            if rel not in self.files:
                self.files[rel] = buf.info
                item = QTreeWidgetItem([buf.info.label])
                item.setData(0, ROLE_REL, rel)
                groups[buf.info.kind].addChild(item)
        for g in groups.values():
            g.setText(0, f"{luapack.KIND_TITLES[self._kind_of_group(g, groups)]} ({g.childCount()})")
            g.setExpanded(g.childCount() <= 60)
        self._refresh_items()
        self._apply_filter(self.filter.text())
        self.statusBar().showMessage(f"{len(files)} Lua file(s) in {self.pack}", 5000)
        self._set_editing(self.current is not None)

    @staticmethod
    def _kind_of_group(item, groups) -> str:
        for kind, g in groups.items():
            if g is item:
                return kind
        return "runtime"

    def _items(self):
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                yield g.child(j)

    def _refresh_items(self):
        """Bold and `*` for unsaved; red for a file with an error, brown for a warning."""
        c = theme.COLORS
        for item in self._items():
            rel = item.data(0, ROLE_REL)
            info = self.files.get(rel)
            buf = self.buffers.get(rel)
            label = info.label if info else rel
            unsaved = buf is not None and buf.modified
            item.setText(0, label + (" *" if unsaved else ""))
            f = item.font(0)
            f.setBold(unsaved or buf is self.current and buf is not None)
            item.setFont(0, f)
            problems = (buf.live + buf.checked) if buf else self._pack_problems.get(rel, [])
            if any(p.severity == "error" for p in problems):
                item.setForeground(0, QBrush(QColor(c["danger"])))
            elif problems:
                item.setForeground(0, QBrush(QColor(c["warn"])))
            elif info is not None and not info.exists and buf is None:
                item.setForeground(0, QBrush(QColor(c["muted"])))
            else:
                item.setForeground(0, QBrush(QColor(c["text"])))

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for item in self._items():
            rel = item.data(0, ROLE_REL) or ""
            item.setHidden(bool(needle) and needle not in item.text(0).lower() and needle not in rel.lower())

    def _tree_activated(self, item, _col=0):
        rel = item.data(0, ROLE_REL)
        if rel:
            self.open_file(rel)

    # ---------------------------------------------------------------------------- opening
    def open_file(self, rel: str, line: int | None = None, col: int | None = None) -> bool:
        """Show a pack file (pack-relative, forward slashes), at `line` if given."""
        rel = rel.replace("\\", "/")
        buf = self.buffers.get(rel)
        if buf is None:
            if not self.pack:
                return False
            info = self.files.get(rel) or luapack.ScriptFile(
                luacheck.kind_of(rel) or "runtime", rel, os.path.join(self.pack, *rel.split("/")),
                os.path.basename(rel)[:-4])
            if not os.path.isfile(info.path):
                QMessageBox.information(self, "Script Edit", f"{rel} does not exist.")
                return False
            try:
                text, newline, bom, mtime = read_script(info.path)
            except (OSError, UnicodeDecodeError) as err:
                QMessageBox.warning(self, "Script Edit", f"{rel} could not be read:\n{err}")
                return False
            buf = Buffer(info, text, newline, bom, mtime)
            buf.doc.modificationChanged.connect(lambda _m: self._refresh_items())
            self.buffers[rel] = buf
            buf.checked = [p for p in self._pack_problems.get(rel, [])]
        self._show(buf)
        if line:
            self.editor.go_to(line, col)
        return True

    def _show(self, buf: Buffer):
        if self.current is not None:
            self.current.cursor = self.editor.textCursor().position()
        self.current = buf
        self.editor.blockSignals(True)
        self.editor.set_document(buf.doc)
        self.editor.blockSignals(False)
        cursor = self.editor.textCursor()
        cursor.setPosition(min(buf.cursor, len(buf.text)))
        self.editor.setTextCursor(cursor)
        title = buf.rel + (f"  -  {buf.info.note}" if buf.info.note else "")
        self.text_frame.caption.setText(title)
        self.setWindowTitle(f"Script Edit - {buf.rel}")
        self._set_editing(True)
        self._live_syntax()
        self._show_problems()
        self._refresh_items()
        for item in self._items():
            if item.data(0, ROLE_REL) == buf.rel:
                self.tree.setCurrentItem(item)
                break

    # -------------------------------------------------------------------------- problems
    def _text_changed(self):
        if self.current is not None:
            self._syntax_timer.start()

    def _live_syntax(self):
        buf = self.current
        if buf is None:
            return
        buf.live = luacheck.syntax(buf.rel, buf.text)
        self._mark()
        if buf.live:
            self.statusBar().showMessage(f"Syntax: {buf.live[0]}")
        self._refresh_items()

    def _mark(self):
        buf = self.current
        if buf is None:
            return
        marks = {}
        for p in buf.checked + buf.live:
            if p.line and p.rel == buf.rel:
                if marks.get(p.line) != "error":
                    marks[p.line] = p.severity
        self.editor.set_marks(marks)

    def _show_problems(self, problems: list | None = None):
        """The Problems list: `problems` when given (a Check All), else the current file's."""
        self.problems.clear()
        if problems is None:
            buf = self.current
            problems = (buf.live + buf.checked) if buf else []
        c = theme.COLORS
        for p in problems:
            item = QTreeWidgetItem([p.rel or "(pack)", str(p.line or ""), p.message])
            item.setData(0, ROLE_REL, (p.rel, p.line, p.col))
            item.setToolTip(2, p.message)
            color = c["danger"] if p.severity == "error" else c["warn"]
            for col in range(3):
                item.setForeground(col, QBrush(QColor(color)))
            self.problems.addTopLevelItem(item)

    def _problem_activated(self, item, _col=0):
        rel, line, col = item.data(0, ROLE_REL)
        if rel and rel.endswith(".lua"):
            self.open_file(rel, line, col)

    def _overrides(self) -> dict:
        return {rel: b.text for rel, b in self.buffers.items() if b.modified}

    def check_current(self):
        buf = self.current
        if buf is None or not self.pack:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found = luacheck.check_file(self.pack, buf.rel, buf.text, self._overrides())
        except Exception as err:
            found = [luacheck.Problem(buf.rel, f"the check could not run: {err}")]
        finally:
            QApplication.restoreOverrideCursor()
        buf.checked = [p for p in found if p not in buf.live]
        buf.live = luacheck.syntax(buf.rel, buf.text)
        self._mark()
        self._show_problems()
        self._refresh_items()
        ok, how = luacheck.lupa_status()
        if not found:
            self.statusBar().showMessage(f"{buf.rel}: no problems" + ("" if ok else " (syntax only; " + how + ")"))
        else:
            self.statusBar().showMessage(f"{buf.rel}: {len(found)} problem(s)")

    def check_all(self):
        if not self.pack:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found = luacheck.check_pack(self.pack, self._overrides())
        except Exception as err:
            found = [luacheck.Problem("", f"the check could not run: {err}")]
        finally:
            QApplication.restoreOverrideCursor()
        by_file: dict[str, list] = {}
        for p in found:
            by_file.setdefault(p.rel, []).append(p)
        self._pack_problems = by_file
        for rel, buf in self.buffers.items():
            buf.checked = list(by_file.get(rel, []))
            buf.live = luacheck.syntax(rel, buf.text)
        self._mark()
        self._show_problems(found)
        self._refresh_items()
        errors = sum(1 for p in found if p.severity == "error")
        warnings = len(found) - errors
        ok, how = luacheck.lupa_status()
        note = "" if ok else f"  (syntax only: {how})"
        if found:
            self.statusBar().showMessage(f"Check All: {errors} error(s), {warnings} warning(s){note}")
        else:
            self.statusBar().showMessage(f"Check All: no problems{note}")

    # ---------------------------------------------------------------------------- saving
    def _save(self, buf: Buffer) -> bool:
        text = buf.text
        bad = luacheck.syntax(buf.rel, text)
        if bad:
            answer = QMessageBox.warning(
                self, "Save", f"{buf.rel} has a syntax error:\n\n{bad[0]}\n\n"
                "The world will refuse to start with it. Save anyway?",
                QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Save:
                return False
        path = buf.info.path
        if not buf.is_new and buf.mtime is not None and os.path.exists(path) \
                and abs(os.path.getmtime(path) - buf.mtime) > 1e-6:
            answer = QMessageBox.warning(
                self, "Save", f"{buf.rel} changed on disk since it was opened here "
                "(a git pull, or another editor).\n\nOverwrite it with this text?",
                QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Save:
                return False
        if buf.is_new and os.path.exists(path):
            QMessageBox.warning(self, "Save", f"{buf.rel} already exists; not overwritten.")
            return False
        try:
            buf.mtime = write_script(path, text, buf.newline, buf.bom)
        except OSError as err:
            QMessageBox.warning(self, "Save", f"{buf.rel} could not be written:\n{err}")
            return False
        was_new = buf.is_new
        buf.is_new = False
        buf.info.exists = True
        buf.doc.setModified(False)
        self.statusBar().showMessage(f"Saved {buf.rel}. Restart the world to load it.", 8000)
        if was_new:
            self.reload_list()
        self._refresh_items()
        return True

    def save_current(self):
        if self.current is not None:
            self._save(self.current)

    def save_all(self):
        for buf in list(self.buffers.values()):
            if buf.modified and not self._save(buf):
                return False
        return True

    def revert_current(self):
        buf = self.current
        if buf is None:
            return
        if buf.is_new:
            if QMessageBox.question(self, "Revert", f"Discard the new file {buf.rel}?") != QMessageBox.Yes:
                return
            del self.buffers[buf.rel]
            self.current = None
            self.editor.set_document(new_document())
            self.text_frame.caption.setText("")
            self._set_editing(False)
            self.reload_list()
            return
        if buf.modified and QMessageBox.question(
                self, "Revert", f"Discard your changes to {buf.rel} and read it from disk?") != QMessageBox.Yes:
            return
        try:
            text, newline, bom, mtime = read_script(buf.info.path)
        except (OSError, UnicodeDecodeError) as err:
            QMessageBox.warning(self, "Revert", f"{buf.rel} could not be read:\n{err}")
            return
        cursor = self.editor.textCursor().position()
        buf.doc.setPlainText(text)
        buf.doc.setModified(False)
        buf.newline, buf.bom, buf.mtime = newline, bom, mtime
        buf.cursor = cursor
        self._show(buf)

    def _close_buffers(self) -> bool:
        """Ask about unsaved files. False if the person cancelled."""
        unsaved = [b.rel for b in self.buffers.values() if b.modified]
        if unsaved:
            answer = QMessageBox.warning(
                self, "Script Edit", "Unsaved changes in:\n\n" + "\n".join(unsaved) + "\n\nSave them?",
                QMessageBox.SaveAll | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.SaveAll)
            if answer == QMessageBox.Cancel:
                return False
            if answer == QMessageBox.SaveAll and not self.save_all():
                return False
        self.buffers.clear()
        self.current = None
        self.editor.set_document(new_document())
        self.text_frame.caption.setText("")
        self._set_editing(False)
        return True

    def closeEvent(self, event):
        if self._close_buffers():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------------------------- new
    def new_dialog(self):
        if not self.pack:
            return
        npc, ok = QInputDialog.getText(self, "New Dialog", "The NPC's name, as its placement names it:")
        npc = npc.strip()
        if not ok or not npc:
            return
        map_id, ok = QInputDialog.getInt(self, "New Dialog", f"The map {npc} speaks on:", 148, 1, 100000)
        if not ok:
            return
        rel = f"{luapack.DIALOG_DIR}/{luapack.file_stem(npc)}.lua"
        path = os.path.join(self.pack, *rel.split("/"))
        if os.path.exists(path) or rel in self.buffers:
            QMessageBox.information(self, "New Dialog", f"{rel} already exists; it is opened instead. "
                                    f"Add map {map_id} to it as a new [{map_id}] = {{ ... }} entry.")
            self.open_file(rel)
            return
        info = luapack.ScriptFile("dialog", rel, path, rel.split("/")[-1][:-4], exists=False)
        buf = Buffer(info, luapack.new_dialog_text(npc, map_id), is_new=True)
        buf.doc.modificationChanged.connect(lambda _m: self._refresh_items())
        self.buffers[rel] = buf
        self.reload_list()
        self._show(buf)

    # ------------------------------------------------------------------------------ moving
    def focus_find(self):
        selected = self.editor.textCursor().selectedText()
        if selected and "\u2029" not in selected:
            self.find_box.setText(selected)
        self.find_box.setFocus()
        self.find_box.selectAll()

    def find(self, backward: bool):
        text = self.find_box.text()
        if not text or self.current is None:
            return
        if not self.editor.find_text(text, backward=backward):
            self.statusBar().showMessage(f"'{text}' is not in {self.current.rel}", 4000)
        else:
            self.editor.setFocus()

    def go_to_line(self):
        if self.current is None:
            return
        line, ok = QInputDialog.getInt(self, "Go to Line", "Line:", self.editor.textCursor().blockNumber() + 1,
                                       1, max(1, self.editor.blockCount()))
        if ok:
            self.editor.go_to(line)

    def _cursor_moved(self, line: int, col: int):
        self.pos_label.setText(f"Line {line}, Col {col}")

    def showEvent(self, event):
        super().showEvent(event)
        self.editor.refresh_theme()
        for buf in self.buffers.values():
            buf.doc.highlighter.refresh_colors()
