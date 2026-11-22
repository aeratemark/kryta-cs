"""The OpenGL Render Window's matrices against the software view's projection.

Picking, spawns, cell borders and the brush circle are still projected on the
CPU by `_View3DCore._project`; the GPU draws terrain and models with
`glmath.view_proj`. These must agree to the pixel or the overlays drift off
the ground. No Qt needed."""
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from kryta_cs.model import CELL, prop_matrix                   # noqa: E402
from kryta_cs.ui import glmath                                 # noqa: E402

FOV = math.radians(60.0)


def _camera(yaw, pitch, dist, target, w, h, exag):
    """A copy of _View3DCore._basis/_camera (they need a widget)."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    fwd, right, up = (sy * cp, cy * cp, -sp), (cy, -sy, 0.0), (sy * sp, cy * sp, cp)
    tx, ty, tz = target
    tz = tz / CELL * exag
    eye = (tx - fwd[0] * dist, ty - fwd[1] * dist, tz - fwd[2] * dist)
    focal = (h / 2.0) / math.tan(FOV / 2.0)
    return w / 2.0, h / 2.0, focal, eye, right, up, fwd


def _project(cam, gx, gy, z, exag):
    """A copy of _View3DCore._project, without the depth clamp."""
    cx, cy, focal, eye, right, up, fwd = cam
    px, py = gx - eye[0], gy - eye[1]
    pz = z / CELL * exag - eye[2]
    zc = px * fwd[0] + py * fwd[1] + pz * fwd[2]
    xc = px * right[0] + py * right[1] + pz * right[2]
    yc = px * up[0] + py * up[1] + pz * up[2]
    return cx + xc / zc * focal, cy - yc / zc * focal, zc


def _gl_screen(m, gx, gy, gz, w, h):
    x, y, z, wc = glmath.transform(m, gx, gy, gz)
    return w / 2.0 + x / wc * w / 2.0, h / 2.0 - y / wc * h / 2.0, z / wc, wc


def test_view_proj_matches_software_projection():
    rnd = random.Random(7)
    for _ in range(200):
        w, h = rnd.uniform(300, 1600), rnd.uniform(200, 1000)
        exag = rnd.uniform(0.25, 6.0)
        cam = _camera(rnd.uniform(-6, 6), rnd.uniform(math.radians(8), math.radians(88)),
                      rnd.uniform(0.4, 300), (rnd.uniform(0, 256), rnd.uniform(0, 256),
                                              rnd.uniform(-2000, 2000)), w, h, exag)
        near, far = glmath.clip_planes(50.0, 400.0)
        m = glmath.view_proj(cam, w, h, near, far, FOV)
        for _ in range(20):
            gx, gy, z = rnd.uniform(-20, 270), rnd.uniform(-20, 270), rnd.uniform(-3000, 3000)
            sx, sy, depth = _project(cam, gx, gy, z, exag)
            if depth < 0.1:
                continue                     # behind the eye: the GPU clips it
            gx2, gy2, gz2, wc = _gl_screen(m, gx, gy, z / CELL * exag, w, h)
            assert abs(wc - depth) < 1e-6 * max(1.0, depth)
            assert abs(sx - gx2) < 1e-6 * max(1.0, abs(sx)), (sx, gx2)
            assert abs(sy - gy2) < 1e-6 * max(1.0, abs(sy)), (sy, gy2)


def test_depth_is_monotonic_inside_the_clip_range():
    cam = _camera(0.5, 0.8, 60.0, (32, 32, 0), 800, 600, 1.0)
    near, far = glmath.clip_planes(60.0, 120.0)
    m = glmath.view_proj(cam, 800, 600, near, far, FOV)
    eye, fwd = cam[3], cam[6]
    last = -2.0
    for k in range(1, 200):
        d = near + (far - near) * k / 200.0 * 0.999
        p = [eye[i] + fwd[i] * d for i in range(3)]
        _x, _y, z, wc = glmath.transform(m, *p)
        assert -1.0 <= z / wc <= 1.0
        assert z / wc > last
        last = z / wc


def test_prop_model_matches_per_vertex_placement():
    rnd = random.Random(3)
    dim_y = 96
    for _ in range(100):
        rot = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        sbyte = rnd.randrange(256)
        x, y, z = rnd.uniform(0, 9000), rnd.uniform(0, 9000), rnd.uniform(-500, 500)
        exag = rnd.uniform(0.25, 6.0)
        r = prop_matrix(rot)
        s = glmath.prop_scale(sbyte)
        m = glmath.prop_model(r, s, x, y, z, dim_y, CELL, exag)
        for _ in range(10):
            mx, my, mz = rnd.uniform(-900, 900), rnd.uniform(-900, 900), rnd.uniform(-1200, 50)
            sx, sy, sz = mx * s, my * s, mz * s
            # the placement the software view and the hit test use
            wx = x + r[0] * sx + r[1] * sy + r[2] * sz
            wy = y + r[3] * sx + r[4] * sy + r[5] * sz
            wz = z - (r[6] * sx + r[7] * sy + r[8] * sz)
            gx, gy = wx / CELL - 0.5, (dim_y - 1) - (wy / CELL - 0.5)
            ox, oy, oz, ow = glmath.transform(m, mx, my, mz)
            assert ow == 1.0
            assert abs(ox - gx) < 1e-6 and abs(oy - gy) < 1e-6
            assert abs(oz - wz / CELL * exag) < 1e-6


def test_texture_layers_follow_the_client_limit():
    """Four textures meeting at a cell crash the client; the brush guard and the
    repair must both keep every cell within three layers."""
    from kryta_cs import texlayers
    types = list(range(8))
    tiles = [0] * (32 * 32)
    tiles[5 * 32 + 6], tiles[6 * 32 + 5], tiles[6 * 32 + 6] = 1, 2, 3
    assert texlayers.overflow_cells(tiles, 32, 32, types) == [(5, 5, 4)]
    fixed, changed = texlayers.fix_overflows(tiles, 32, 32, types)
    assert changed and not texlayers.overflow_cells(fixed, 32, 32, types)
    assert not texlayers.can_paint(fixed, 32, 32, types, 6, 6, 3) or not \
        texlayers.overflow_cells([3 if i == 6 * 32 + 6 else t for i, t in enumerate(fixed)], 32, 32, types)


def test_build_writes_tiles_in_the_tiled_order():
    from kryta_cs.model import _tiled_index
    seen = sorted(_tiled_index(x, y, 64) for y in range(64) for x in range(64))
    assert seen == list(range(64 * 64))
    assert _tiled_index(32, 0, 64) == 1024 and _tiled_index(0, 1, 64) == 32


def test_built_props_stand_on_the_built_ground():
    """A prop's z goes into the file in the same (negated, z-down) convention
    as the terrain it stands on; otherwise it is sunk or floating in game."""
    import inspect
    from kryta_cs import pipeline
    src = inspect.getsource(pipeline._apply_authored_props)
    assert "z=-float(q.z)" in src
