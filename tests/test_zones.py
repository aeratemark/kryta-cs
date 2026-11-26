"""The Zones (flora) codec, offline. Synthetic data only: no ArenaNet bytes live in this repo. When
tools/zones_113021/zones_stripped.bin exists (tools/zones_dump.bat writes it from your own Gw.dat),
the pre-Searing part is also round-tripped byte for byte."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs.zones import AREA_FLAGS, Area, FloraType, Zones, ZonesError     # noqa: E402

LOCAL = os.path.join(os.path.dirname(HERE), "tools", "zones_113021", "zones_stripped.bin")


def flora(tid, name, counts):
    g, m = len(counts), sum(counts)
    weights = []
    for n in counts:
        weights += [(i + 1) / n for i in range(n)]
    return FloraType(tid, name, [2] * g, [8.0] * g, [8.0] * g, [0.5] * g, [0.25] * g, [0] * g,
                     list(counts), weights, [4] * m)


def sample():
    return Zones(types=[flora(1, "Zones\\Wheat.ini", [3, 1]), flora(2, "Zones\\Flowers.ini", [2])],
                 areas=[Area(1, [(0.0, 0.0), (0.0, 96.0), (96.0, 96.0)]),
                        Area(2, [(10.0, 10.0), (10.0, 50.0), (50.0, 50.0), (50.0, 10.0)])],
                 record2=b"\x00")


def test_round_trip():
    z = sample()
    blob = z.encode()
    back = Zones.decode(blob)
    assert back.encode() == blob
    assert [t.short_name for t in back.types] == ["Wheat", "Flowers"]
    assert back.areas[1].points[2] == (50.0, 50.0)
    assert back.areas[0].flags == AREA_FLAGS


def test_empty_is_the_minimal_34_byte_form():
    assert len(Zones(record2=b"\x00").encode()) == 34


def test_model_ranges_follow_type_order():
    z = sample()
    assert z.model_ranges() == {1: (0, 4), 2: (4, 6)}
    assert z.model_count() == 6


def test_bad_parts_are_refused():
    with pytest.raises(ZonesError):
        Zones.decode(b"\x00\x00\x00\x00\x0a")
    z = sample()
    z.areas.append(Area(1, [(0.0, 0.0), (1.0, 1.0)]))
    with pytest.raises(ZonesError):
        z.encode()
    blob = sample().encode()
    with pytest.raises(ZonesError):
        Zones.decode(blob + b"\x00")


@pytest.mark.skipif(not os.path.isfile(LOCAL), reason="run tools/zones_dump.bat first")
def test_pre_searing_round_trips_byte_for_byte():
    with open(LOCAL, "rb") as fh:
        blob = fh.read()
    z = Zones.decode(blob)
    assert z.encode() == blob
    assert len(z.types) == 8 and z.model_count() == 191
    assert "LakesideWheat" in [t.short_name for t in z.types]
