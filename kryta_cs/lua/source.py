"""Lua 5.4 source, read with every position kept.

Kryta's content is Lua: `dialogs/*.lua`, `npcs/behaviors.lua`, `scripts/runtime.lua` and
`skills/*.lua`. The world runs them in mlua (`game/src/Scripting/`); this module only *reads*
them, in plain Python, so the editor can

- tell a syntax error and its line without running anything,
- see the table a file returns as data (`literal`), and
- change one value in that table by replacing exactly its characters (`Edit`), so comments,
  ordering, layout and hand-written functions stay byte for byte as they were.

The grammar is the Lua 5.4 reference manual's section 9, complete: every statement and
expression, long strings and comments, attribs, goto and labels, integer division and the
bitwise operators. Nothing is evaluated: a parse is a tree of `Node`s, each with `start` and
`end`, character offsets into the source (`source[node.start:node.end]` is its text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KEYWORDS = frozenset(
    "and break do else elseif end false for function goto if in local nil not or repeat "
    "return then true until while".split()
)

# Longest first, so `...` is read before `..` and `.`.
SYMBOLS = sorted(
    "+ - * / % ^ # & ~ | << >> // == ~= <= >= < > = ( ) { } [ ] :: ; : , . .. ...".split(),
    key=len, reverse=True,
)

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HEX = re.compile(r"0[xX](?:[0-9a-fA-F]*\.?[0-9a-fA-F]*)(?:[pP][+-]?[0-9]+)?")
_DEC = re.compile(r"(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_LONG_OPEN = re.compile(r"\[(=*)\[")


class LuaSyntaxError(Exception):
    """A source that is not Lua. `line` and `col` count from 1, as Lua's own messages do."""

    def __init__(self, message: str, source: str, pos: int):
        self.pos = max(0, min(pos, len(source)))
        self.line = source.count("\n", 0, self.pos) + 1
        self.col = self.pos - (source.rfind("\n", 0, self.pos) + 1) + 1
        self.message = message
        super().__init__(f"{self.line}:{self.col}: {message}")


@dataclass
class Token:
    kind: str          # "name", "keyword", "number", "string", "symbol", "eof"
    value: object      # the name, keyword or symbol text; a number's value; a string's contents
    start: int
    end: int


@dataclass
class Comment:
    start: int
    end: int
    long: bool


def _long_bracket(src: str, pos: int, what: str) -> tuple[int, int, int] | None:
    """At `[==[`: (contents start, contents end, end after `]==]`); None if `pos` opens no long bracket."""
    m = _LONG_OPEN.match(src, pos)
    if not m:
        return None
    level = m.group(1)
    close = "]" + level + "]"
    body = m.end()
    stop = src.find(close, body)
    if stop < 0:
        raise LuaSyntaxError(f"unfinished long {what}", src, pos)
    return body, stop, stop + len(close)


_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
            "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


def _short_string(src: str, pos: int) -> tuple[str, int]:
    quote = src[pos]
    i = pos + 1
    out = []
    n = len(src)
    while True:
        if i >= n or src[i] == "\n":
            raise LuaSyntaxError("unfinished string", src, pos)
        c = src[i]
        if c == quote:
            return "".join(out), i + 1
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= n:
            raise LuaSyntaxError("unfinished string", src, pos)
        e = src[i]
        if e in _ESCAPES:
            out.append(_ESCAPES[e])
            i += 1
        elif e == "x":
            hexs = src[i + 1:i + 3]
            if len(hexs) != 2 or not all(h in "0123456789abcdefABCDEF" for h in hexs):
                raise LuaSyntaxError("hexadecimal digit expected", src, i)
            out.append(chr(int(hexs, 16)))
            i += 3
        elif e == "z":
            i += 1
            while i < n and src[i].isspace():
                i += 1
        elif e.isdigit():
            j = i
            while j < n and j < i + 3 and src[j].isdigit():
                j += 1
            value = int(src[i:j])
            if value > 255:
                raise LuaSyntaxError("decimal escape too large", src, i)
            out.append(chr(value))
            i = j
        elif e == "u":
            m = re.match(r"u\{([0-9a-fA-F]+)\}", src[i:])
            if not m:
                raise LuaSyntaxError("missing '{' or '}' in \\u{xxxx}", src, i)
            out.append(chr(int(m.group(1), 16)))
            i += m.end()
        elif e == "\r":
            out.append("\n")
            i += 2 if src[i + 1:i + 2] == "\n" else 1
        else:
            raise LuaSyntaxError("invalid escape sequence", src, i - 1)


def _number(text: str):
    if text[:2] in ("0x", "0X"):
        if any(c in text for c in ".pP"):
            return float.fromhex(text)
        return int(text, 16)
    if any(c in text for c in ".eE"):
        return float(text)
    return int(text)


def tokenize(src: str) -> tuple[list[Token], list[Comment]]:
    """Every token and every comment, in order. Raises `LuaSyntaxError` on a bad character."""
    tokens: list[Token] = []
    comments: list[Comment] = []
    i = 0
    n = len(src)
    if src.startswith("#"):                      # a shebang line, as lua.c skips it
        i = src.find("\n")
        i = n if i < 0 else i
    while i < n:
        c = src[i]
        if c in " \t\r\n\f\v":
            i += 1
            continue
        if src.startswith("--", i):
            long = _long_bracket(src, i + 2, "comment")
            if long:
                comments.append(Comment(i, long[2], True))
                i = long[2]
            else:
                j = src.find("\n", i)
                j = n if j < 0 else j
                comments.append(Comment(i, j, False))
                i = j
            continue
        if c.isalpha() or c == "_":
            m = _NAME.match(src, i)
            word = m.group()
            tokens.append(Token("keyword" if word in KEYWORDS else "name", word, i, m.end()))
            i = m.end()
            continue
        if c.isdigit() or (c == "." and src[i + 1:i + 2].isdigit()):
            m = _HEX.match(src, i) if src[i:i + 2] in ("0x", "0X") else _DEC.match(src, i)
            j = m.end()
            if j < n and (src[j].isalnum() or src[j] == "_"):
                raise LuaSyntaxError(f"malformed number near '{src[i:j + 1]}'", src, i)
            try:
                value = _number(m.group())
            except ValueError:
                raise LuaSyntaxError(f"malformed number near '{m.group()}'", src, i) from None
            tokens.append(Token("number", value, i, j))
            i = j
            continue
        if c in "\"'":
            value, j = _short_string(src, i)
            tokens.append(Token("string", value, i, j))
            i = j
            continue
        if c == "[":
            long = _long_bracket(src, i, "string")
            if long:
                body = src[long[0]:long[1]]
                if body.startswith("\r\n"):
                    body = body[2:]
                elif body.startswith("\n"):
                    body = body[1:]
                tokens.append(Token("string", body, i, long[2]))
                i = long[2]
                continue
        for sym in SYMBOLS:
            if src.startswith(sym, i):
                tokens.append(Token("symbol", sym, i, i + len(sym)))
                i += len(sym)
                break
        else:
            raise LuaSyntaxError(f"unexpected symbol near '{c}'", src, i)
    tokens.append(Token("eof", "<eof>", n, n))
    return tokens, comments


# ----------------------------------------------------------------------------------- the tree

@dataclass(eq=False)
class Node:
    """One piece of the tree. `kind` names the grammar rule; the rest is per kind (see `Parser`)."""

    kind: str
    start: int
    end: int
    value: object = None                 # Number, String, Name, Op: the value or operator
    children: list = field(default_factory=list)

    def text(self, source: str) -> str:
        return source[self.start:self.end]


@dataclass(eq=False)
class TableField:
    """One field of a table constructor: `[k] = v`, `name = v` or a positional `v`.

    `key` is a Node for `[k] = v`, a `str` for `name = v` and None for a positional field.
    `start`/`end` cover the whole field without its separator; `sep_end` is the end after the
    `,` or `;` that follows it, if one does."""

    key: object
    key_node: Node | None
    value: Node
    start: int
    end: int
    sep_end: int | None = None


@dataclass
class Chunk:
    source: str
    block: Node
    comments: list

    def returned(self) -> Node | None:
        """The first expression of the chunk's final top-level `return`, if it has one."""
        if self.block.children:
            last = self.block.children[-1]
            if last.kind == "Return" and last.children:
                return last.children[0]
        return None


# Binary operator precedence (left, right), Lua 5.4 manual 3.4.8; right > left is right associative.
_BINARY = {
    "or": (1, 1), "and": (2, 2),
    "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3), "==": (3, 3),
    "|": (4, 4), "~": (5, 5), "&": (6, 6), "<<": (7, 7), ">>": (7, 7),
    "..": (9, 8), "+": (10, 10), "-": (10, 10),
    "*": (11, 11), "/": (11, 11), "//": (11, 11), "%": (11, 11),
    "^": (14, 13),
}
_UNARY_PRIORITY = 12
_UNARY = ("not", "-", "#", "~")


class Parser:
    def __init__(self, source: str):
        self.src = source
        self.tokens, self.comments = tokenize(source)
        self.i = 0

    # -- tokens
    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def peek(self, ahead: int = 1) -> Token:
        return self.tokens[min(self.i + ahead, len(self.tokens) - 1)]

    def check(self, value: str) -> bool:
        t = self.tok
        return t.kind in ("symbol", "keyword") and t.value == value

    def accept(self, value: str) -> Token | None:
        if self.check(value):
            t = self.tok
            self.i += 1
            return t
        return None

    def expect(self, value: str, opener: Token | None = None) -> Token:
        t = self.accept(value)
        if t:
            return t
        near = self.tok.value if self.tok.kind != "string" else self.src[self.tok.start:self.tok.end]
        msg = f"'{value}' expected near '{near}'"
        if opener is not None:
            line = self.src.count("\n", 0, opener.start) + 1
            here = self.src.count("\n", 0, self.tok.start) + 1
            if line != here:
                msg = f"'{value}' expected (to close '{opener.value}' at line {line}) near '{near}'"
        raise LuaSyntaxError(msg, self.src, self.tok.start)

    def name(self) -> Token:
        t = self.tok
        if t.kind != "name":
            raise LuaSyntaxError(f"<name> expected near '{t.value}'", self.src, t.start)
        self.i += 1
        return t

    def prev_end(self) -> int:
        return self.tokens[self.i - 1].end

    # -- chunk and blocks
    def chunk(self) -> Chunk:
        block = self.block()
        if self.tok.kind != "eof":
            raise LuaSyntaxError(f"'<eof>' expected near '{self.tok.value}'", self.src, self.tok.start)
        return Chunk(self.src, block, self.comments)

    def _block_ends(self) -> bool:
        t = self.tok
        return t.kind == "eof" or (t.kind == "keyword" and t.value in ("end", "else", "elseif", "until"))

    def block(self) -> Node:
        start = self.tok.start
        stats = []
        while not self._block_ends():
            if self.check("return"):
                stats.append(self.retstat())
                break
            s = self.statement()
            if s is not None:
                stats.append(s)
        end = stats[-1].end if stats else start
        return Node("Block", start, end, children=stats)

    def retstat(self) -> Node:
        t = self.expect("return")
        exprs = []
        if not self._block_ends() and not self.check(";"):
            exprs = self.exprlist()
        self.accept(";")
        return Node("Return", t.start, self.prev_end(), children=exprs)

    def statement(self) -> Node | None:
        t = self.tok
        if self.accept(";"):
            return None
        if t.kind == "keyword":
            k = t.value
            if k == "if":
                return self.ifstat()
            if k == "while":
                self.i += 1
                cond = self.expr()
                self.expect("do")
                body = self.block()
                self.expect("end", t)
                return Node("While", t.start, self.prev_end(), children=[cond, body])
            if k == "do":
                self.i += 1
                body = self.block()
                self.expect("end", t)
                return Node("Do", t.start, self.prev_end(), children=[body])
            if k == "for":
                return self.forstat()
            if k == "repeat":
                self.i += 1
                body = self.block()
                self.expect("until", t)
                cond = self.expr()
                return Node("Repeat", t.start, cond.end, children=[body, cond])
            if k == "function":
                self.i += 1
                name = self.funcname()
                body = self.funcbody(t)
                return Node("Function", t.start, body.end, value=name, children=[body])
            if k == "local":
                self.i += 1
                if self.accept("function"):
                    n = self.name()
                    body = self.funcbody(t)
                    return Node("LocalFunction", t.start, body.end, value=n.value, children=[body])
                names = []
                while True:
                    n = self.name()
                    attrib = None
                    if self.accept("<"):
                        attrib = self.name().value
                        if attrib not in ("const", "close"):
                            raise LuaSyntaxError(f"unknown attribute '{attrib}'", self.src, n.start)
                        self.expect(">")
                    names.append((n.value, attrib))
                    if not self.accept(","):
                        break
                exprs = self.exprlist() if self.accept("=") else []
                return Node("Local", t.start, self.prev_end(), value=names, children=exprs)
            if k == "return":
                raise LuaSyntaxError("'return' must be the last statement of its block", self.src, t.start)
            if k == "break":
                self.i += 1
                return Node("Break", t.start, t.end)
            if k == "goto":
                self.i += 1
                n = self.name()
                return Node("Goto", t.start, n.end, value=n.value)
        if self.check("::"):
            self.i += 1
            n = self.name()
            self.expect("::")
            return Node("Label", t.start, self.prev_end(), value=n.value)
        # an assignment or a call
        first = self.suffixedexp()
        if self.check("=") or self.check(","):
            targets = [first]
            while self.accept(","):
                targets.append(self.suffixedexp())
            for target in targets:
                if target.kind not in ("Name", "Index"):
                    raise LuaSyntaxError("syntax error near '='", self.src, target.start)
            self.expect("=")
            exprs = self.exprlist()
            return Node("Assign", first.start, exprs[-1].end, value=targets, children=exprs)
        if first.kind not in ("Call", "Method"):
            raise LuaSyntaxError(f"syntax error near '{self.tok.value}'", self.src, self.tok.start)
        return Node("CallStat", first.start, first.end, children=[first])

    def ifstat(self) -> Node:
        t = self.expect("if")
        parts = []
        cond = self.expr()
        self.expect("then")
        parts.append(cond)
        parts.append(self.block())
        while self.check("elseif"):
            self.i += 1
            c = self.expr()
            self.expect("then")
            parts.append(c)
            parts.append(self.block())
        if self.accept("else"):
            parts.append(self.block())
        self.expect("end", t)
        return Node("If", t.start, self.prev_end(), children=parts)

    def forstat(self) -> Node:
        t = self.expect("for")
        first = self.name()
        if self.accept("="):
            exprs = [self.expr()]
            self.expect(",")
            exprs.append(self.expr())
            if self.accept(","):
                exprs.append(self.expr())
            self.expect("do")
            body = self.block()
            self.expect("end", t)
            return Node("ForNum", t.start, self.prev_end(), value=first.value, children=exprs + [body])
        names = [first.value]
        while self.accept(","):
            names.append(self.name().value)
        self.expect("in")
        exprs = self.exprlist()
        self.expect("do")
        body = self.block()
        self.expect("end", t)
        return Node("ForIn", t.start, self.prev_end(), value=names, children=exprs + [body])

    def funcname(self) -> str:
        parts = [self.name().value]
        while self.accept("."):
            parts.append(self.name().value)
        if self.accept(":"):
            parts.append(":" + self.name().value)
        return ".".join(parts)

    def funcbody(self, opener: Token) -> Node:
        start = self.tok.start
        self.expect("(")
        params = []
        if not self.check(")"):
            while True:
                if self.accept("..."):
                    params.append("...")
                    break
                params.append(self.name().value)
                if not self.accept(","):
                    break
        self.expect(")")
        body = self.block()
        self.expect("end", opener)
        return Node("FuncBody", start, self.prev_end(), value=params, children=[body])

    # -- expressions
    def exprlist(self) -> list:
        exprs = [self.expr()]
        while self.accept(","):
            exprs.append(self.expr())
        return exprs

    def expr(self, limit: int = 0) -> Node:
        t = self.tok
        if t.kind in ("keyword", "symbol") and t.value in _UNARY:
            self.i += 1
            operand = self.expr(_UNARY_PRIORITY)
            left = Node("UnOp", t.start, operand.end, value=t.value, children=[operand])
        else:
            left = self.simpleexp()
        while True:
            op = self.tok
            if op.kind not in ("keyword", "symbol") or op.value not in _BINARY:
                break
            lp, rp = _BINARY[op.value]
            if lp <= limit:
                break
            self.i += 1
            right = self.expr(rp)
            left = Node("BinOp", left.start, right.end, value=op.value, children=[left, right])
        return left

    def simpleexp(self) -> Node:
        t = self.tok
        if t.kind == "number":
            self.i += 1
            return Node("Number", t.start, t.end, value=t.value)
        if t.kind == "string":
            self.i += 1
            return Node("String", t.start, t.end, value=t.value)
        if t.kind == "keyword":
            if t.value == "nil":
                self.i += 1
                return Node("Nil", t.start, t.end)
            if t.value in ("true", "false"):
                self.i += 1
                return Node("Boolean", t.start, t.end, value=(t.value == "true"))
            if t.value == "function":
                self.i += 1
                body = self.funcbody(t)
                return Node("FunctionExpr", t.start, body.end, children=[body])
        if self.check("..."):
            self.i += 1
            return Node("Vararg", t.start, t.end)
        if self.check("{"):
            return self.table()
        return self.suffixedexp()

    def primaryexp(self) -> Node:
        t = self.tok
        if t.kind == "name":
            self.i += 1
            return Node("Name", t.start, t.end, value=t.value)
        if self.accept("("):
            inner = self.expr()
            self.expect(")", t)
            return Node("Paren", t.start, self.prev_end(), children=[inner])
        near = t.value if t.kind != "string" else self.src[t.start:t.end]
        raise LuaSyntaxError(f"unexpected symbol near '{near}'", self.src, t.start)

    def suffixedexp(self) -> Node:
        node = self.primaryexp()
        while True:
            t = self.tok
            if self.accept("."):
                n = self.name()
                key = Node("String", n.start, n.end, value=n.value)
                node = Node("Index", node.start, n.end, value="dot", children=[node, key])
            elif self.check("["):
                self.i += 1
                key = self.expr()
                self.expect("]", t)
                node = Node("Index", node.start, self.prev_end(), value="bracket", children=[node, key])
            elif self.accept(":"):
                n = self.name()
                args = self.callargs()
                node = Node("Method", node.start, self.prev_end(), value=n.value, children=[node] + args)
            elif self.check("(") or self.check("{") or t.kind == "string":
                args = self.callargs()
                node = Node("Call", node.start, self.prev_end(), children=[node] + args)
            else:
                return node

    def callargs(self) -> list:
        t = self.tok
        if t.kind == "string":
            self.i += 1
            return [Node("String", t.start, t.end, value=t.value)]
        if self.check("{"):
            return [self.table()]
        self.expect("(")
        args = [] if self.check(")") else self.exprlist()
        self.expect(")", t)
        return args

    def table(self) -> Node:
        t = self.expect("{")
        fields: list[TableField] = []
        while not self.check("}"):
            f_start = self.tok.start
            if self.check("["):
                self.i += 1
                key_node = self.expr()
                self.expect("]")
                self.expect("=")
                value = self.expr()
                key = key_node
            elif self.tok.kind == "name" and self.peek().kind == "symbol" and self.peek().value == "=":
                n = self.name()
                self.i += 1
                key_node = Node("String", n.start, n.end, value=n.value)
                key = n.value
                value = self.expr()
            else:
                key_node = None
                key = None
                value = self.expr()
            fld = TableField(key, key_node, value, f_start, value.end)
            sep = self.accept(",") or self.accept(";")
            if sep:
                fld.sep_end = sep.end
            fields.append(fld)
            if not sep:
                break
        self.expect("}", t)
        return Node("Table", t.start, self.prev_end(), value=fields)


def parse(source: str) -> Chunk:
    """The whole source as a tree. Raises `LuaSyntaxError` with the first error's line and column."""
    return Parser(source).chunk()


def check_syntax(source: str) -> LuaSyntaxError | None:
    """The first syntax error, or None for a source Lua would load."""
    try:
        parse(source)
    except LuaSyntaxError as err:
        return err
    return None


# ------------------------------------------------------------------------------ data and edits

class Script:
    """A value that is code, not data: a function, a call, a variable, an expression. The editor shows it
    as a script and leaves its text exactly as written."""

    def __init__(self, node: Node, source: str):
        self.node = node
        self.source = node.text(source)

    def __repr__(self):
        first = self.source.splitlines()[0] if self.source else ""
        more = "..." if "\n" in self.source else ""
        return f"Script({first!r}{more})"


class Call:
    """A call of a plain name with literal arguments, such as `gw(5896, 34250)` or `mix(gw(1), "x")`."""

    def __init__(self, name: str, args: list, node: Node):
        self.name = name
        self.args = args
        self.node = node

    def __repr__(self):
        return f"Call({self.name!r}, {self.args!r})"


class Global:
    """A bare global name used as a value, such as `PLAYER`."""

    def __init__(self, name: str, node: Node | None = None):
        self.name = name
        self.node = node

    def __eq__(self, other):
        return isinstance(other, Global) and other.name == self.name

    def __hash__(self):
        return hash(("Global", self.name))

    def __repr__(self):
        return f"Global({self.name!r})"


class LuaTable:
    """A table constructor read as data, in source order, with every field's node kept.

    `items` is a list of (key, value) where a positional field's key is its 1-based position.
    `table[key]` reads a value; `field(key)` gives the `TableField`, for editing."""

    def __init__(self, node: Node):
        self.node = node
        self.items: list = []
        self.fields: list[TableField] = []

    def keys(self):
        return [k for k, _ in self.items]

    def get(self, key, default=None):
        for k, v in self.items:
            if k == key:
                return v
        return default

    def __getitem__(self, key):
        for k, v in self.items:
            if k == key:
                return v
        raise KeyError(key)

    def __contains__(self, key):
        return any(k == key for k, _ in self.items)

    def field(self, key) -> TableField | None:
        for (k, _), f in zip(self.items, self.fields):
            if k == key:
                return f
        return None

    def sequence(self) -> list:
        """The positional values, 1..n, as Lua's `#` would see a constructor without holes."""
        out = []
        n = 1
        while self.get(n) is not None:
            out.append(self[n])
            n += 1
        return out

    def __repr__(self):
        return f"LuaTable({self.items!r})"


def literal(node: Node, source: str):
    """A node as data: numbers, strings, booleans and nil as Python values, tables as `LuaTable`,
    `name(...)` with literal arguments as `Call`, a bare name as `Global`, anything else as `Script`."""
    k = node.kind
    if k in ("Number", "String", "Boolean"):
        return node.value
    if k == "Nil":
        return None
    if k == "UnOp" and node.value == "-" and node.children[0].kind == "Number":
        return -node.children[0].value
    if k == "Name":
        return Global(node.value, node)
    if k == "Table":
        t = LuaTable(node)
        position = 0
        for f in node.value:
            if f.key is None:
                position += 1
                key = position
            elif isinstance(f.key, str):
                key = f.key
            else:
                key = literal(f.key, source)
                if isinstance(key, (LuaTable, Script, Call, Global)):
                    key = Script(f.key, source)
            t.items.append((key, literal(f.value, source)))
            t.fields.append(f)
        return t
    if k == "Call" and node.children[0].kind == "Name":
        args = [literal(a, source) for a in node.children[1:]]
        if not any(isinstance(a, Script) for a in args):
            return Call(node.children[0].value, args, node)
    return Script(node, source)


def returned_table(source: str) -> LuaTable | None:
    """The table a file's final `return` gives, as data; None if it returns something else."""
    value = parse(source).returned()
    if value is None or value.kind != "Table":
        return None
    return literal(value, source)


_SIMPLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def lua_string(text: str) -> str:
    """A Python string as a Lua short string literal, double quoted, as the pack writes them."""
    out = ['"']
    for c in text:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\{ord(c)}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def lua_number(value) -> str:
    if isinstance(value, bool):
        raise TypeError("a bool is not a number")
    if isinstance(value, int):
        return str(value)
    if value != value:
        return "(0/0)"
    if value in (float("inf"), float("-inf")):
        return "math.huge" if value > 0 else "-math.huge"
    text = repr(float(value))
    return text


def to_lua(value, indent: str = "", step: str = "    ") -> str:
    """A Python value as Lua source, in the pack's style: four-space indents, `key = value`, trailing
    commas. dicts and lists become tables; `Script`, `Call` and `Global` keep their own text."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return lua_number(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, Script):
        return value.source
    if isinstance(value, Global):
        return value.name
    if isinstance(value, Call):
        return f"{value.name}({', '.join(to_lua(a, indent, step) for a in value.args)})"
    if isinstance(value, Raw):
        return value.text
    if isinstance(value, LuaTable):
        value = {k: v for k, v in value.items}
    if isinstance(value, (list, tuple)):
        if not value:
            return "{}"
        inner = indent + step
        return "{\n" + "".join(f"{inner}{to_lua(v, inner, step)},\n" for v in value) + indent + "}"
    if isinstance(value, dict):
        if not value:
            return "{}"
        inner = indent + step
        lines = []
        for k, v in value.items():
            if isinstance(k, str) and _SIMPLE_KEY.match(k) and k not in KEYWORDS:
                key = k
            else:
                key = f"[{to_lua(k, inner, step)}]"
            lines.append(f"{inner}{key} = {to_lua(v, inner, step)},\n")
        return "{\n" + "".join(lines) + indent + "}"
    raise TypeError(f"no Lua form for {type(value).__name__}")


class Raw:
    """Lua source text to write as it is."""

    def __init__(self, text: str):
        self.text = text


def line_indent(source: str, pos: int) -> str:
    """The whitespace that opens the line `pos` is on."""
    start = source.rfind("\n", 0, pos) + 1
    m = re.match(r"[ \t]*", source[start:])
    return m.group() if m else ""


class Edit:
    """Changes to one source, each replacing exact characters, applied together.

    Everything outside the replaced ranges is kept byte for byte: comments, blank lines, the order
    of fields, functions written by hand. Ranges may not overlap. `apply()` gives the new source,
    which the caller should parse again before saving (`apply(check=True)` does)."""

    def __init__(self, source: str):
        self.source = source
        self.changes: list[tuple[int, int, str]] = []

    def replace(self, start: int, end: int, text: str) -> "Edit":
        for s, e, _ in self.changes:
            if start < e and s < end or (start == end == s):
                raise ValueError(f"edit {start}-{end} overlaps an earlier edit {s}-{e}")
        self.changes.append((start, end, text))
        return self

    def set_value(self, fld: TableField, value) -> "Edit":
        """Give a field a new value, written in the pack's style at the field's own indent."""
        indent = line_indent(self.source, fld.start)
        return self.replace(fld.value.start, fld.value.end, to_lua(value, indent))

    def set_node(self, node: Node, value) -> "Edit":
        indent = line_indent(self.source, node.start)
        return self.replace(node.start, node.end, to_lua(value, indent))

    def remove_field(self, table: Node, fld: TableField) -> "Edit":
        """Take a field out with its separator and, when it stands on lines of its own, those lines."""
        src = self.source
        start, end = fld.start, fld.sep_end or fld.end
        line_start = src.rfind("\n", 0, start) + 1
        nl = src.find("\n", end)
        line_end = len(src) if nl < 0 else nl + 1
        if not src[line_start:start].strip() and not src[end:line_end].strip():
            start, end = line_start, line_end
        elif fld.sep_end is None:
            # the last field without a separator: take the separator of the field before it
            fields = table.value
            i = fields.index(fld)
            if i > 0 and fields[i - 1].sep_end is not None:
                start = fields[i - 1].end
        return self.replace(start, end, "")

    def add_field(self, table: Node, key, value) -> "Edit":
        """Append `key = value` (or a positional `value` when `key` is None) as the table's last field."""
        src = self.source
        fields = table.value
        close = table.end - 1                       # the `}`
        if key is None:
            head = ""
        elif isinstance(key, str) and _SIMPLE_KEY.match(key) and key not in KEYWORDS:
            head = f"{key} = "
        else:
            head = f"[{to_lua(key)}] = "
        multiline = "\n" in src[table.start:table.end] or not fields
        if fields:
            last = fields[-1]
            indent = line_indent(src, last.start)
            body = head + to_lua(value, indent)
            if multiline and "\n" in src[table.start:table.end]:
                if last.sep_end is None:
                    return self.replace(last.end, last.end, f",\n{indent}{body},")
                return self.replace(last.sep_end, last.sep_end, f"\n{indent}{body},")
            if last.sep_end is None:
                return self.replace(last.end, last.end, f", {body}")
            return self.replace(last.sep_end, last.sep_end, f" {body},")
        outer = line_indent(src, table.start)
        indent = outer + "    "
        body = head + to_lua(value, indent)
        return self.replace(table.start + 1, close, f"\n{indent}{body},\n{outer}")

    def apply(self, check: bool = True) -> str:
        out = []
        at = 0
        for start, end, text in sorted(self.changes, key=lambda c: (c[0], c[1])):
            out.append(self.source[at:start])
            out.append(text)
            at = end
        out.append(self.source[at:])
        result = "".join(out)
        if check:
            parse(result)
        return result
