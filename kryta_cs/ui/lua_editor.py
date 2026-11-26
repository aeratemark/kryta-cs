"""The script text widget: Lua highlighting, line numbers, the current line, and marked problem lines.

Like the Construction Set's script editor it is a plain fixed-font text box; what it adds is what a
Lua file needs to be read: keywords, strings, comments and numbers in colour, the pack's own names
(`gw`, `mix`, `PLAYER`, `ctx`, `cast`, `op`) picked out, long strings and comments that span lines,
four-space indents kept on Enter, and a red band on a line Check found a problem on.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QKeyEvent, QPainter, QSyntaxHighlighter, QTextCharFormat,
                           QTextCursor, QTextFormat)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from . import theme
from ..lua.source import KEYWORDS

# Names the pack's Lua gives a meaning to (scripts/runtime.lua, skills/README.md, npcs/behaviors.lua).
PACK_NAMES = frozenset({"gw", "mix", "PLAYER", "ctx", "cast", "op", "Cast", "OP_WORDS"})
LUA_LIBS = frozenset({
    "table", "string", "math", "assert", "error", "ipairs", "next", "pairs", "pcall", "print",
    "rawequal", "rawget", "rawlen", "rawset", "select", "setmetatable", "getmetatable", "tonumber",
    "tostring", "type", "xpcall", "_G", "_VERSION", "self",
})

_STATE_NORMAL = -1
_STATE_COMMENT = 1000          # + the long bracket's level
_STATE_STRING = 2000

_TOKEN = re.compile(
    r"(?P<lcomment>--\[(?P<lcl>=*)\[)"
    r"|(?P<comment>--.*)"
    r"|(?P<lstring>\[(?P<lsl>=*)\[)"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\"?|'(?:\\.|[^'\\])*'?)"
    r"|(?P<number>0[xX][0-9a-fA-F.]+(?:[pP][+-]?\d+)?|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)


def _palette() -> dict:
    if theme.CURRENT == "dark":
        return {"keyword": "#c586c0", "string": "#ce9178", "comment": "#6a9955", "number": "#b5cea8",
                "pack": "#4fc1ff", "lib": "#dcdcaa", "current": "#2a2f38", "error": "#5a2a2a",
                "warning": "#4a4020", "gutter": "#1b1d22", "gutter_text": "#6b7380"}
    return {"keyword": "#000080", "string": "#a31515", "comment": "#008000", "number": "#098658",
            "pack": "#7a3e9d", "lib": "#795e26", "current": "#ffffe0", "error": "#ffd7d7",
            "warning": "#fff0c0", "gutter": "#ece9e2", "gutter_text": "#808080"}


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class LuaHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.refresh_colors()

    def refresh_colors(self):
        p = _palette()
        self.f = {
            "keyword": _fmt(p["keyword"], bold=True),
            "string": _fmt(p["string"]),
            "comment": _fmt(p["comment"], italic=True),
            "number": _fmt(p["number"]),
            "pack": _fmt(p["pack"], bold=True),
            "lib": _fmt(p["lib"]),
        }
        self.rehighlight()

    def _close_long(self, text: str, start: int, level: int, fmt: str, state_base: int) -> int:
        """From `start`, inside a long bracket of `level`: format to its close and return the index
        after it, or format to the end of the line, carry the state, and return -1."""
        close = "]" + "=" * level + "]"
        end = text.find(close, start)
        if end < 0:
            self.setFormat(start, len(text) - start, self.f[fmt])
            self.setCurrentBlockState(state_base + level)
            return -1
        end += len(close)
        self.setFormat(start, end - start, self.f[fmt])
        return end

    def highlightBlock(self, text: str):
        self.setCurrentBlockState(_STATE_NORMAL)
        i = 0
        prev = self.previousBlockState()
        if prev >= _STATE_STRING:
            i = self._close_long(text, 0, prev - _STATE_STRING, "string", _STATE_STRING)
        elif prev >= _STATE_COMMENT:
            i = self._close_long(text, 0, prev - _STATE_COMMENT, "comment", _STATE_COMMENT)
        if i < 0:
            return
        while i < len(text):
            m = _TOKEN.search(text, i)
            if not m:
                return
            kind = m.lastgroup
            s = m.start()
            if m.group("lcomment") is not None:
                i = self._close_long(text, m.end(), len(m.group("lcl")), "comment", _STATE_COMMENT)
                self.setFormat(s, m.end() - s, self.f["comment"])
                if i < 0:
                    return
                continue
            if m.group("lstring") is not None:
                i = self._close_long(text, m.end(), len(m.group("lsl")), "string", _STATE_STRING)
                self.setFormat(s, m.end() - s, self.f["string"])
                if i < 0:
                    return
                continue
            word = m.group()
            if kind == "comment":
                self.setFormat(s, len(word), self.f["comment"])
            elif kind == "string":
                self.setFormat(s, len(word), self.f["string"])
            elif kind == "number":
                self.setFormat(s, len(word), self.f["number"])
            elif kind == "name":
                if word in KEYWORDS:
                    self.setFormat(s, len(word), self.f["keyword"])
                elif word in PACK_NAMES:
                    self.setFormat(s, len(word), self.f["pack"])
                elif word in LUA_LIBS:
                    self.setFormat(s, len(word), self.f["lib"])
            i = m.end()


def new_document(text: str = ""):
    """A text document for one script, with its own highlighter (kept as `document.highlighter`)."""
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QPlainTextDocumentLayout
    document = QTextDocument()
    document.setDocumentLayout(QPlainTextDocumentLayout(document))
    document.setDefaultFont(theme.mono_font())
    document.setPlainText(text)
    document.highlighter = LuaHighlighter(document)
    document.setModified(False)
    document.clearUndoRedoStacks()
    return document


class _Gutter(QWidget):
    def __init__(self, editor: "LuaEditor"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class LuaEditor(QPlainTextEdit):
    """A Lua text box. `set_marks({line: "error" | "warning"})` bands problem lines (1-based)."""

    cursor_moved = Signal(int, int)       # line, column, both from 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setFont(theme.mono_font())
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._marks: dict[int, str] = {}
        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._cursor_changed)
        self.set_document(new_document())

    # -- documents
    def set_document(self, document):
        """Show another file's text. Each file keeps its own document, so its undo history and
        highlighting stay with it; `new_document()` makes one."""
        self.setDocument(document)
        document.setDefaultFont(theme.mono_font())
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._marks = {}
        self._update_gutter_width()
        self._cursor_changed()
        self._gutter.update()

    # -- appearance
    def refresh_theme(self):
        self.setFont(theme.mono_font())
        self.document().setDefaultFont(theme.mono_font())
        self._cursor_changed()
        self._gutter.update()

    def gutter_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _n=0):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        p = _palette()
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(p["gutter"]))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line = number + 1
                mark = self._marks.get(line)
                if mark:
                    painter.fillRect(0, top, self._gutter.width(), height,
                                     QColor(p["error"] if mark == "error" else p["warning"]))
                painter.setPen(QColor(p["gutter_text"]))
                painter.drawText(0, top, self._gutter.width() - 5, height, Qt.AlignRight, str(line))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1
        painter.end()

    # -- marks and the current line
    def set_marks(self, marks: dict):
        self._marks = dict(marks)
        self._cursor_changed()
        self._gutter.update()

    def _cursor_changed(self):
        p = _palette()
        selections = []
        for line, severity in sorted(self._marks.items()):
            block = self.document().findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(p["error"] if severity == "error" else p["warning"]))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = QTextCursor(block)
            selections.append(sel)
        cur = QTextEdit.ExtraSelection()
        line = self.textCursor().blockNumber() + 1
        if line not in self._marks:
            cur.format.setBackground(QColor(p["current"]))
            cur.format.setProperty(QTextFormat.FullWidthSelection, True)
            cur.cursor = self.textCursor()
            cur.cursor.clearSelection()
            selections.append(cur)
        self.setExtraSelections(selections)
        c = self.textCursor()
        self.cursor_moved.emit(c.blockNumber() + 1, c.positionInBlock() + 1)

    # -- moving
    def go_to(self, line: int, col: int | None = None):
        block = self.document().findBlockByNumber(max(0, line - 1))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        if col:
            cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, min(col - 1, block.length() - 1))
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    def find_text(self, text: str, backward: bool = False, whole: bool = False, case: bool = False) -> bool:
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindBackward
        if whole:
            flags |= QTextDocument.FindWholeWords
        if case:
            flags |= QTextDocument.FindCaseSensitively
        if self.find(text, flags):
            return True
        # wrap around once
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End if backward else QTextCursor.Start)
        self.setTextCursor(cursor)
        return self.find(text, flags)

    # -- typing
    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key_Tab and not event.modifiers():
            cursor = self.textCursor()
            if cursor.hasSelection():
                self._indent_selection(+1)
            else:
                col = cursor.positionInBlock()
                cursor.insertText(" " * (4 - col % 4))
            return
        if key == Qt.Key_Backtab:
            self._indent_selection(-1)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            # Every Enter is a plain newline: Shift+Enter would otherwise insert U+2028, which
            # toPlainText() keeps and Lua does not read as a line break.
            cursor = self.textCursor()
            line = cursor.block().text()
            indent = re.match(r"[ \t]*", line).group()
            before = line[:cursor.positionInBlock()].rstrip()
            if before.endswith(("{", "(", "then", "do", "else", "repeat")) or re.search(
                    r"\bfunction\b[^)]*\)\s*$", before):
                indent += "    "
            cursor.insertText("\n" + indent)
            self.ensureCursorVisible()
            return
        super().keyPressEvent(event)

    def _selected_blocks(self):
        cursor = self.textCursor()
        doc = self.document()
        first = doc.findBlock(cursor.selectionStart()).blockNumber()
        last = doc.findBlock(max(cursor.selectionStart(), cursor.selectionEnd() - 1)).blockNumber()
        return [doc.findBlockByNumber(n) for n in range(first, last + 1)]

    def _edit_lines(self, change):
        """Apply `change(text) -> (cut, insert)` at the start of each selected line, as one undo step.
        `cut` is how many characters to remove at the line's indent; `insert` the text to put there."""
        blocks = self._selected_blocks()
        texts = [b.text() for b in blocks]
        positions = [b.position() for b in blocks]
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        shift = 0
        for pos, text in zip(positions, texts):
            result = change(text)
            if result is None:
                continue
            at, cut, insert = result
            edit.setPosition(pos + shift + at)
            if cut:
                edit.setPosition(pos + shift + at + cut, QTextCursor.KeepAnchor)
                edit.removeSelectedText()
            if insert:
                edit.insertText(insert)
            shift += len(insert) - cut
        edit.endEditBlock()

    def _indent_selection(self, direction: int):
        if direction > 0:
            self._edit_lines(lambda text: (0, 0, "    "))
        else:
            def outdent(text):
                strip = min(4, len(text) - len(text.lstrip(" ")))
                return (0, strip, "") if strip else None
            self._edit_lines(outdent)

    def toggle_comment(self):
        """Ctrl+/: comment or uncomment the selected lines with `-- `."""
        texts = [b.text() for b in self._selected_blocks()]
        commented = all(t.lstrip().startswith("--") for t in texts if t.strip())

        def change(text):
            if not text.strip():
                return None
            lead = len(text) - len(text.lstrip())
            if commented:
                return (lead, 3 if text[lead:lead + 3] == "-- " else 2, "")
            return (lead, 0, "-- ")
        self._edit_lines(change)
