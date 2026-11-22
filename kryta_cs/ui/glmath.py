"""Matrices for the OpenGL Render Window, with no Qt in them so they can be tested offline.

All matrices are 16 floats in row-major order (QMatrix4x4's constructor order).

The view/projection reproduces `_View3DCore._project` exactly: a point's screen
position is its camera-space x/y over its depth times the focal length, with
y growing downwards on screen. The only difference is that OpenGL clips at a
near plane instead of clamping depth, which is what made triangles behind the
camera explode across the old software view."""

from __future__ import annotations

import math


def view_proj(cam, width: float, height: float, near: float, far: float, fov: float) -> list:
    """Projection * view for the camera tuple `_View3DCore._camera()` returns."""
    _cx, _cy, _focal, eye, right, up, fwd = cam
    aspect = max(1e-6, width / max(1e-6, height))
    f = 1.0 / math.tan(fov / 2.0)
    ex, ey, ez = eye
    tr = -(right[0] * ex + right[1] * ey + right[2] * ez)
    tu = -(up[0] * ex + up[1] * ey + up[2] * ez)
    tf = fwd[0] * ex + fwd[1] * ey + fwd[2] * ez
    view = [right[0], right[1], right[2], tr,
            up[0], up[1], up[2], tu,
            -fwd[0], -fwd[1], -fwd[2], tf,
            0.0, 0.0, 0.0, 1.0]
    a = (far + near) / (near - far)
    b = 2.0 * far * near / (near - far)
    proj = [f / aspect, 0.0, 0.0, 0.0,
            0.0, f, 0.0, 0.0,
            0.0, 0.0, a, b,
            0.0, 0.0, -1.0, 0.0]
    return mul(proj, view)


def mul(a, b) -> list:
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = (a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] +
                              a[r * 4 + 2] * b[8 + c] + a[r * 4 + 3] * b[12 + c])
    return out


def prop_scale(scale_byte: int) -> float:
    return (scale_byte * (255.0 / 128.0) / 256.0) + (1.0 / 128.0)


def prop_model(rot3x3, scale: float, x: float, y: float, z: float,
               dim_y: int, cell: float, exag: float) -> list:
    """Model space -> the Render Window's grid space for one placed prop.

    Model x/y are the ground and model z is height pointing DOWN, as the map's:
    world = prop + R * (s * v) with z negated; grid x = wx / cell - 0.5,
    grid y = (dim_y - 1) - (wy / cell - 0.5), grid z = wz / cell * exag."""
    r0, r1, r2, r3, r4, r5, r6, r7, r8 = rot3x3
    k = scale / cell
    kz = scale * exag / cell
    return [r0 * k, r1 * k, r2 * k, x / cell - 0.5,
            -r3 * k, -r4 * k, -r5 * k, (dim_y - 1) - (y / cell - 0.5),
            -r6 * kz, -r7 * kz, -r8 * kz, z * exag / cell,
            0.0, 0.0, 0.0, 1.0]


def transform(m, x: float, y: float, z: float) -> tuple:
    """(x, y, z, w) of m * (x, y, z, 1)."""
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11],
            m[12] * x + m[13] * y + m[14] * z + m[15])


def clip_planes(dist: float, extent: float) -> tuple:
    """(near, far) for a camera `dist` grid units from its target over a map
    whose largest extent (including height) is `extent` grid units.

    Near scales with the distance so a 24-bit depth buffer keeps props apart
    from the ground they stand on, at any zoom."""
    near = max(0.03, dist * 0.01)
    far = dist + extent * 2.5 + 16.0
    return near, far
