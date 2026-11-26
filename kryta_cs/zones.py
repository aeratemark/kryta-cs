"""A map's Zones part: where the client grows flora (grass, wheat, flowers, shrubs, ferns).

Guild Wars does not place its ground cover as props. A map's Zones part (chunk 0x10000003, Stripped)
lists FLORA TYPES - each a set of plant-model groups with sizes, densities and pick weights, named
after the artist's setup file (`...\\Zones\\LakesideWheat.ini`) - and AREAS, outlines in world
coordinates that say "this type grows inside here". The client's map compile reads it (Rurik:
`stripbuild.py`, "Terrain bloat reads `state->zones`") and grows the plants itself; the Bloated
copy's per-cell grids are the client's own output, so a build writes only this Stripped form.

The plant models are the Zones Dependencies list (chunk 0x11000003): every type's models in type
order, one entry per model (pre-Searing: 8 types, 16+38+25+50+19+12+16+15 = 191 entries, 191
dependencies). A build that keeps the donor's types keeps that list as the donor has it.

Layout, MEASURED on pre-Searing (map file 113021) and round-tripped byte for byte by `encode()`:

    u32  signature 0x59220320
    u8   version (10)
    records {u8 tag, u32 size, body}, ended by tag 0xFF with size 0:
      tag 1  flora types:  u32 count, then per type
                 u32 id (1-based), UTF-16 name NUL-terminated,
                 u32 G (groups), u32 M (models, the sum of the group counts),
                 u32[G] a, f32[G] b, f32[G] c, f32[G] d, f32[G] e, u8[G] f,
                 u32[G] group model counts, f32[M] cumulative pick weight per model (each group
                 runs to 1.0), u32[M] per-model value
      tag 2  NOT FULLY DECODED, carried as bytes: u8 n, u32[n], then n 4-bit values
             (pre-Searing: n 15, numbers 0-153, 69 bytes; the minimal part: n 0, 1 byte).
             The numbers are positions in the Zones Dependencies list, each the first
             appearance of a distinct plant model (15 of pre-Searing's 50); the 4-bit values
             (6-10) look like log2 texture sizes. Change the plant list and this goes stale:
             the client then stops at `Assertion: def`, ZnModel.cpp(86).
      tag 3  areas:  u32 count, then per area
                 u32 type id, u8[3] flags (00 00 80 on every pre-Searing area), u32 n,
                 n x (f32 x, f32 y)  - a polygon in world units

What a, b, c, d, e and f mean, and the per-model value, are not settled; they are kept as read.
Reading pre-Searing, b and c look like spacing in world units (8-42) and d, e like 0-1 fractions
(density, chance); a is 1 or 2 per group.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SIGNATURE = 0x59220320
VERSION = 10
STRIPPED_ZONES = 0x10000003
STRIPPED_ZONES_DEPS = 0x11000003
TAG_TYPES, TAG_TWO, TAG_AREAS, TAG_END = 1, 2, 3, 0xFF
AREA_FLAGS = b"\x00\x00\x80"
EMPTY_RECORD2 = b"\x00"     # record 2 with a zero count, as the minimal 34-byte Zones part has it


class ZonesError(ValueError):
    pass


@dataclass
class FloraType:
    id: int
    name: str
    a: list            # u32 per group
    b: list            # f32 per group
    c: list            # f32 per group
    d: list            # f32 per group
    e: list            # f32 per group
    f: list            # u8 per group
    counts: list       # u32 per group: how many models the group holds
    weights: list      # f32 per model: cumulative pick weight inside its group
    values: list       # u32 per model

    @property
    def short_name(self) -> str:
        """`LakesideWheat` from `Missions\\...\\Zones\\LakesideWheat.ini`."""
        base = self.name.replace("/", "\\").rsplit("\\", 1)[-1]
        return base[:-4] if base.lower().endswith(".ini") else base

    @property
    def model_count(self) -> int:
        return sum(self.counts)


@dataclass
class Area:
    type_id: int
    points: list                       # [(x, y), ...] world units
    flags: bytes = AREA_FLAGS


@dataclass
class Zones:
    types: list = field(default_factory=list)       # [FloraType]
    areas: list = field(default_factory=list)       # [Area]
    record2: bytes = b""                            # tag 2, carried
    version: int = VERSION
    order: tuple = (TAG_TYPES, TAG_TWO, TAG_AREAS)  # the records as the source had them

    # -- reading
    @classmethod
    def decode(cls, blob: bytes) -> "Zones":
        if len(blob) < 5:
            raise ZonesError(f"a Zones part is at least 5 bytes, this one is {len(blob)}")
        sig = struct.unpack_from("<I", blob, 0)[0]
        if sig != SIGNATURE:
            raise ZonesError(f"signature 0x{sig:08X}, expected 0x{SIGNATURE:08X}")
        z = cls(version=blob[4])
        off = 5
        order = []
        while True:
            if off + 5 > len(blob):
                raise ZonesError(f"the records run past the end at byte {off}")
            tag = blob[off]
            size = struct.unpack_from("<I", blob, off + 1)[0]
            body = blob[off + 5:off + 5 + size]
            if len(body) != size:
                raise ZonesError(f"record {tag} claims {size} bytes, {len(body)} are left")
            off += 5 + size
            if tag == TAG_END:
                break
            order.append(tag)
            if tag == TAG_TYPES:
                z.types = _decode_types(body)
            elif tag == TAG_AREAS:
                z.areas = _decode_areas(body)
            elif tag == TAG_TWO:
                z.record2 = bytes(body)
            else:
                raise ZonesError(f"unknown record tag {tag}")
        if off != len(blob):
            raise ZonesError(f"{len(blob) - off} byte(s) after the end record")
        z.order = tuple(order)
        return z

    # -- writing
    def encode(self) -> bytes:
        out = bytearray(struct.pack("<IB", SIGNATURE, self.version))
        bodies = {TAG_TYPES: _encode_types(self.types), TAG_TWO: bytes(self.record2),
                  TAG_AREAS: _encode_areas(self.areas)}
        for tag in self.order:
            body = bodies[tag]
            out += struct.pack("<BI", tag, len(body)) + body
        out += struct.pack("<BI", TAG_END, 0)
        return bytes(out)

    # -- queries
    def type_by_id(self, type_id: int) -> FloraType | None:
        return next((t for t in self.types if t.id == type_id), None)

    def model_ranges(self) -> dict:
        """{type id: (first, end)} indexes into the Zones Dependencies list, in type order."""
        out = {}
        at = 0
        for t in self.types:
            out[t.id] = (at, at + t.model_count)
            at += t.model_count
        return out

    def model_count(self) -> int:
        return sum(t.model_count for t in self.types)


def _read_wstring(body: bytes, off: int) -> tuple[str, int]:
    end = off
    while end + 1 < len(body) and (body[end] or body[end + 1]):
        end += 2
    if end + 1 >= len(body):
        raise ZonesError("a flora type's name has no end")
    return body[off:end].decode("utf-16-le"), end + 2


def _decode_types(body: bytes) -> list:
    (count,) = struct.unpack_from("<I", body, 0)
    off = 4
    out = []
    for _ in range(count):
        (tid,) = struct.unpack_from("<I", body, off)
        name, off = _read_wstring(body, off + 4)
        g, m = struct.unpack_from("<II", body, off)
        off += 8

        def take(fmt, n):
            nonlocal off
            vals = list(struct.unpack_from(f"<{n}{fmt}", body, off))
            off += struct.calcsize(f"<{n}{fmt}")
            return vals
        a = take("I", g)
        b = take("f", g)
        c = take("f", g)
        d = take("f", g)
        e = take("f", g)
        f = take("B", g)
        counts = take("I", g)
        if sum(counts) != m:
            raise ZonesError(f"flora type {tid}: its groups hold {sum(counts)} models, it says {m}")
        weights = take("f", m)
        values = take("I", m)
        out.append(FloraType(tid, name, a, b, c, d, e, f, counts, weights, values))
    if off != len(body):
        raise ZonesError(f"{len(body) - off} byte(s) after the last flora type")
    return out


def _encode_types(types: list) -> bytes:
    out = bytearray(struct.pack("<I", len(types)))
    for t in types:
        g = len(t.counts)
        m = sum(t.counts)
        for name, arr, want in (("a", t.a, g), ("b", t.b, g), ("c", t.c, g), ("d", t.d, g),
                                ("e", t.e, g), ("f", t.f, g), ("weights", t.weights, m),
                                ("values", t.values, m)):
            if len(arr) != want:
                raise ZonesError(f"flora type {t.id}: {name} has {len(arr)} entries, needs {want}")
        out += struct.pack("<I", t.id) + t.name.encode("utf-16-le") + b"\x00\x00"
        out += struct.pack("<II", g, m)
        out += struct.pack(f"<{g}I", *t.a) + struct.pack(f"<{g}f", *t.b)
        out += struct.pack(f"<{g}f", *t.c) + struct.pack(f"<{g}f", *t.d)
        out += struct.pack(f"<{g}f", *t.e) + struct.pack(f"<{g}B", *t.f)
        out += struct.pack(f"<{g}I", *t.counts)
        out += struct.pack(f"<{m}f", *t.weights) + struct.pack(f"<{m}I", *t.values)
    return bytes(out)


def _decode_areas(body: bytes) -> list:
    (count,) = struct.unpack_from("<I", body, 0)
    off = 4
    out = []
    for _ in range(count):
        (tid,) = struct.unpack_from("<I", body, off)
        flags = body[off + 4:off + 7]
        (n,) = struct.unpack_from("<I", body, off + 7)
        off += 11
        pts = struct.unpack_from(f"<{2 * n}f", body, off)
        off += 8 * n
        out.append(Area(tid, [(pts[i], pts[i + 1]) for i in range(0, 2 * n, 2)], bytes(flags)))
    if off != len(body):
        raise ZonesError(f"{len(body) - off} byte(s) after the last area")
    return out


def _encode_areas(areas: list) -> bytes:
    out = bytearray(struct.pack("<I", len(areas)))
    for a in areas:
        if len(a.points) < 3:
            raise ZonesError(f"an area of flora type {a.type_id} has {len(a.points)} point(s); "
                             "an outline needs at least 3")
        out += struct.pack("<I", a.type_id) + bytes(a.flags) + struct.pack("<I", len(a.points))
        for x, y in a.points:
            out += struct.pack("<ff", float(x), float(y))
    return bytes(out)
