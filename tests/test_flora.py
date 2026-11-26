"""The flora brush's data, offline: painting cells, the rectangles a build writes, and saving."""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs import flora                                     # noqa: E402
from kryta_cs.model import CELL, FloraArea, MapProject         # noqa: E402


def project(dim=64):
    return MapProject.new("Flora", dim_x=dim, dim_y=dim)


def test_paint_and_erase():
    p = project()
    changed = flora.paint(p, [(1, 1), (2, 1), (2, 1), (99, 99)], "LakesideWheat")
    assert changed == [(1, 1), (2, 1)]
    assert p.flora_names == ["LakesideWheat"]
    flora.paint(p, [(3, 3)], "lakesidewheat")               # names match without case
    assert p.flora_names == ["LakesideWheat"]
    flora.paint(p, [(4, 4)], "LakesideFlowers")
    assert flora.counts(p) == {"LakesideWheat": 3, "LakesideFlowers": 1}
    assert flora.paint(p, [(1, 1)], None) == [(1, 1)]
    assert flora.counts(p)["LakesideWheat"] == 2


def test_rectangles_cover_every_painted_cell_once():
    rnd = random.Random(7)
    dim = 40
    layer = [0] * (dim * dim)
    for _ in range(30):                                       # blobs of three types
        v = rnd.randint(1, 3)
        cx, cy, r = rnd.randrange(dim), rnd.randrange(dim), rnd.randint(1, 6)
        for y in range(max(0, cy - r), min(dim, cy + r)):
            for x in range(max(0, cx - r), min(dim, cx + r)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    layer[y * dim + x] = v
    covered = [0] * (dim * dim)
    for v, rects in flora.rectangles(layer, dim, dim).items():
        for gx, gy, w, h in rects:
            for y in range(gy, gy + h):
                for x in range(gx, gx + w):
                    assert layer[y * dim + x] == v
                    covered[y * dim + x] += 1
    assert covered == [1 if v else 0 for v in layer]


def test_a_painted_block_is_one_rectangle_in_world_units():
    p = project()
    flora.paint(p, [(x, y) for y in range(10, 14) for x in range(5, 9)], "LakesideWheat")
    areas = flora.all_areas(p)
    assert len(areas) == 1 and areas[0].type == "LakesideWheat"
    xs = [x for x, _y in areas[0].points]
    ys = [y for _x, y in areas[0].points]
    assert (min(xs), max(xs)) == (5 * CELL, 9 * CELL)
    # row 0 is the top: rows 10-13 are world y from (64-14)*96 to (64-10)*96
    assert (min(ys), max(ys)) == ((64 - 14) * CELL, (64 - 10) * CELL)
    # and the cell centre of a painted cell lies inside it
    cx, cy = p.cell_center(6, 12)
    assert min(xs) < cx < max(xs) and min(ys) < cy < max(ys)


def test_hand_typed_outlines_come_first():
    p = project()
    p.flora = [FloraArea("ScoutWheat", [(0, 0), (96, 0), (96, 96)])]
    flora.paint(p, [(0, 0)], "LakesideWheat")
    assert [a.type for a in flora.all_areas(p)] == ["ScoutWheat", "LakesideWheat"]


def test_the_layer_survives_a_save():
    p = project(32)
    flora.paint(p, [(2, 2), (3, 2)], "EdenHillsShrubs")
    q = MapProject.from_json(p.to_json())
    assert q.flora_names == ["EdenHillsShrubs"] and flora.counts(q) == {"EdenHillsShrubs": 2}
    empty = project(32).to_json()
    assert empty["flora_layer"] == []
    assert MapProject.from_json(empty).flora_layer == []
