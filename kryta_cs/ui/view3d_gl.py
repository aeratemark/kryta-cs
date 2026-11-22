"""The Render Window on the GPU: a depth-buffered OpenGL renderer.

The software view drew everything with QPainter and sorted by hand. With no
depth buffer it could not decide what was in front: models were bucketed into
"depth bands", textured triangles went into a second pass on top, a level of
detail and a texture budget were chosen per frame from the camera, and points
behind the eye had their depth clamped instead of clipped. Turning the camera
reshuffled all of that, which is the flicker. The textured terrain was also a
single flat picture at the map's middle height, so props, spawns and the brush
sat on the real heights while the ground you saw did not.

Here the terrain is the real height field draped with the client's blended
albedo, models are their full meshes with their own textures, the GPU's depth
buffer decides visibility, and the near plane clips. Camera, picking and editing
are unchanged (they live in `_View3DCore`); spawns, cell borders and the brush
are still drawn on top with QPainter, using the same projection.

The shaders are GLSL 1.20 on a compatibility context, the most widely supported
combination on Windows drivers. Textures set their minification filter and
mipmaps explicitly: a texture left on the default mipmapped filter without
mipmaps is "incomplete" and samples black, which is what the earlier OpenGL
attempt hit (a 4x4 red texture sampling black).

If anything in the GL path fails, the view switches itself to the software
painter and says so in the corner; KRYTA_CS_SOFTWARE_3D=1 forces that."""

from __future__ import annotations

import math
import sys
import traceback
from array import array

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QMatrix4x4, QPainter, QPen, QSurfaceFormat,
                           QVector2D, QVector3D, QVector4D)
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .. import terrain
from ..model import CELL, prop_matrix
from .glmath import clip_planes, prop_model, prop_scale, view_proj
from . import terrain_texture
from .heightmap_view import ramp
from .theme import COLORS
from .view3d import FOV, LIGHT, _View3DCore

GL_LINES = 0x0001
GL_TRIANGLES = 0x0004
GL_FLOAT = 0x1406
GL_DEPTH_BUFFER_BIT = 0x0100
GL_DEPTH_TEST = 0x0B71
GL_CULL_FACE = 0x0B44
GL_BLEND = 0x0BE2
GL_LEQUAL = 0x0203
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_POLYGON_OFFSET_FILL = 0x8037
GL_TEXTURE0 = 0x84C0

TERRAIN_VS = """#version 120
attribute vec2 a_grid;
attribute float a_h;
uniform mat4 u_mvp;
uniform float u_hscale;
uniform vec2 u_uvscale;
varying vec3 v_pos;
varying vec2 v_uv;
void main() {
    vec3 p = vec3(a_grid, a_h * u_hscale);
    v_pos = p;
    v_uv = (a_grid + vec2(0.5)) * u_uvscale;
    gl_Position = u_mvp * vec4(p, 1.0);
}
"""

TERRAIN_FS = """#version 120
uniform sampler2D u_tex;
uniform vec3 u_light;
varying vec3 v_pos;
varying vec2 v_uv;
void main() {
    vec3 n = normalize(cross(dFdx(v_pos), dFdy(v_pos)));
    if (n.z < 0.0) n = -n;
    float shade = 0.42 + 0.70 * max(dot(n, u_light), 0.0);
    gl_FragColor = vec4(texture2D(u_tex, v_uv).rgb * shade, 1.0);
}
"""

# The client's terrain blend, per pixel (GuildWarsMapBrowser's
# TerrainRevPixelShader.hlsl via Rurik's trnblend): a cell draws up to three
# layers - an opaque base and two overlays whose own alpha masks them - each
# from one 128x128 quadrant of its 256x256 texture (8.5 px border, optionally
# turned 180 degrees), then the result is brightened by 1.4. The layer table
# is a small RGBA8 texture (3 texels per cell, see terrain_texture.pack_layers);
# the textures sit in one atlas, 256 px a slot. Explicit gradients keep the
# mip level continuous across cell seams where the atlas coordinate jumps.
LAYERED_VS = """#version 120
attribute vec2 a_grid;
attribute float a_h;
uniform mat4 u_mvp;
uniform float u_hscale;
varying vec3 v_pos;
varying vec2 v_grid;
void main() {
    vec3 p = vec3(a_grid, a_h * u_hscale);
    v_pos = p;
    v_grid = a_grid;
    gl_Position = u_mvp * vec4(p, 1.0);
}
"""

LAYERED_FS = """#version 120
#extension GL_ARB_shader_texture_lod : enable
uniform sampler2D u_atlas;
uniform sampler2D u_layers;
uniform vec2 u_dims;        // cells
uniform vec2 u_slots;       // atlas columns, rows (256 px each)
uniform vec3 u_light;
varying vec3 v_pos;
varying vec2 v_grid;

vec4 layer_at(vec2 cell, float k) {
    vec2 t = vec2((cell.x * 3.0 + k + 0.5) / (u_dims.x * 3.0), (cell.y + 0.5) / u_dims.y);
    return texture2D(u_layers, t);
}

vec4 sample_layer(vec4 L, vec2 local, vec2 gx, vec2 gy) {
    float tile = floor(L.r * 255.0 + 0.5);
    float g = floor(L.g * 255.0 + 0.5);
    float quad = mod(g, 4.0);
    vec2 l = (g >= 4.0) ? vec2(1.0) - local : local;
    vec2 px = vec2(mod(quad, 2.0), floor(quad / 2.0)) * 128.0 + 8.5 + l * 111.0;
    vec2 slot = vec2(mod(tile, u_slots.x), floor(tile / u_slots.x));
    vec2 size = u_slots * 256.0;
    vec2 uv = (slot * 256.0 + px) / size;
#ifdef GL_ARB_shader_texture_lod
    return texture2DGradARB(u_atlas, uv, gx * 111.0 / size, gy * 111.0 / size);
#else
    return texture2D(u_atlas, uv);
#endif
}

void main() {
    vec2 c = v_grid + vec2(0.5);
    vec2 cell = clamp(floor(c), vec2(0.0), u_dims - vec2(1.0));
    vec2 local = clamp(c - cell, 0.0, 1.0);
    vec2 gx = dFdx(v_grid);
    vec2 gy = dFdy(v_grid);
    vec4 L0 = layer_at(cell, 0.0);
    vec3 col = sample_layer(L0, local, gx, gy).rgb;
    vec4 L1 = layer_at(cell, 1.0);
    if (L1.b > 0.5) {
        vec4 t = sample_layer(L1, local, gx, gy);
        col = mix(col, t.rgb, t.a);
    }
    vec4 L2 = layer_at(cell, 2.0);
    if (L2.b > 0.5) {
        vec4 t = sample_layer(L2, local, gx, gy);
        col = mix(col, t.rgb, t.a);
    }
    col = min(col * 1.4, vec3(1.0));
    vec3 n = normalize(cross(dFdx(v_pos), dFdy(v_pos)));
    if (n.z < 0.0) n = -n;
    float shade = 0.42 + 0.70 * max(dot(n, u_light), 0.0);
    gl_FragColor = vec4(col * shade, 1.0);
}
"""

ATLAS_COLS = 8
ATLAS_SLOT = 256
ATLAS_MIP_MAX = 4               # 16 px a slot: lower mips would bleed between textures

MODEL_VS = """#version 120
attribute vec3 a_pos;
attribute vec2 a_uv;
uniform mat4 u_mvp;
uniform mat4 u_model;
varying vec3 v_pos;
varying vec2 v_uv;
void main() {
    vec4 w = u_model * vec4(a_pos, 1.0);
    v_pos = w.xyz;
    v_uv = a_uv;
    gl_Position = u_mvp * w;
}
"""

MODEL_FS = """#version 120
uniform sampler2D u_tex;
uniform float u_has_tex;
uniform float u_cutout;
uniform vec4 u_color;
uniform vec4 u_tint;
uniform vec3 u_light;
varying vec3 v_pos;
varying vec2 v_uv;
void main() {
    vec4 c = u_color;
    if (u_has_tex > 0.5) {
        c = texture2D(u_tex, v_uv);
        if (c.a < u_cutout) discard;
    }
    vec3 n = normalize(cross(dFdx(v_pos), dFdy(v_pos)));
    float shade = 0.55 + 0.45 * abs(dot(n, u_light));
    gl_FragColor = vec4(mix(c.rgb * shade, u_tint.rgb, u_tint.a), 1.0);
}
"""

FLAT_VS = """#version 120
attribute vec3 a_pos;
uniform mat4 u_mvp;
uniform float u_hscale;
void main() {
    gl_Position = u_mvp * vec4(a_pos.xy, a_pos.z * u_hscale, 1.0);
}
"""

FLAT_FS = """#version 120
uniform vec4 u_color;
void main() {
    gl_FragColor = u_color;
}
"""

MODEL_STRIDE = 5 * 4            # x y z u v, float32
ALPHA_CUTOUT = 0.25             # texels below this alpha are holes (foliage, fences)
SELECT_TINT = QVector4D(1.0, 0.88, 0.0, 0.22)
NO_TINT = QVector4D(0.0, 0.0, 0.0, 0.0)


def _vec4(c: QColor) -> QVector4D:
    return QVector4D(c.redF(), c.greenF(), c.blueF(), c.alphaF())


def _matrix(m) -> QMatrix4x4:
    return QMatrix4x4(*[float(v) for v in m])


class _Program:
    def __init__(self, vs: str, fs: str, attribs, uniforms):
        self.prog = QOpenGLShaderProgram()
        if not self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vs):
            raise RuntimeError("vertex shader: " + self.prog.log())
        if not self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fs):
            raise RuntimeError("fragment shader: " + self.prog.log())
        for i, name in enumerate(attribs):
            self.prog.bindAttributeLocation(name, i)
        if not self.prog.link():
            raise RuntimeError("shader link: " + self.prog.log())
        self.a = {n: self.prog.attributeLocation(n) for n in attribs}
        self.u = {n: self.prog.uniformLocation(n) for n in uniforms}

    def set(self, name: str, value):
        loc = self.u.get(name, -1)
        if loc < 0:
            return
        if isinstance(value, bool) or isinstance(value, int):
            self.prog.setUniformValue1i(loc, int(value))
        elif isinstance(value, float):
            self.prog.setUniformValue1f(loc, value)
        else:
            self.prog.setUniformValue(loc, value)


def _new_buffer(data: bytes, dynamic: bool = False, buf=None):
    if buf is None:
        buf = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        buf.create()
        buf.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw if dynamic
                            else QOpenGLBuffer.UsagePattern.StaticDraw)
    buf.bind()
    try:
        buf.allocate(data, len(data))
    except TypeError:
        from shiboken6 import VoidPtr
        buf.allocate(VoidPtr(data), len(data))
    buf.release()
    return buf


def _new_texture(img: QImage, repeat: bool):
    tex = QOpenGLTexture(img.convertToFormat(QImage.Format.Format_RGBA8888),
                         QOpenGLTexture.MipMapGeneration.GenerateMipMaps)
    tex.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)
    tex.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
    tex.setWrapMode(QOpenGLTexture.WrapMode.Repeat if repeat else QOpenGLTexture.WrapMode.ClampToEdge)
    try:
        # keeps the ground sharp when it is seen at a shallow angle
        tex.setMaximumAnisotropy(16.0)
    except Exception:                                                # noqa: BLE001
        pass
    return tex


class GLTerrain3DView(_View3DCore, QOpenGLWidget):
    cursor_info = Signal(str)
    view_changed = Signal()
    user_moved = Signal()
    tool_requested = Signal(str)
    model_dropped = Signal(object)
    prop_activated = Signal(int)

    BUILD_LODS = False              # the GPU draws full meshes; no clustered copies

    def __init__(self, doc, brush, parent=None):
        QOpenGLWidget.__init__(self, parent)
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self._gl = None
        self._gl_failed = ""
        self._tile_images = []
        self._layered_failed = False
        self._layers = None
        self._layer_dims = (0, 0)
        self._layer_bytes = None
        self._gl_reset_state()
        self._init_core(doc, brush)
        doc.settings_changed.connect(self.update)      # water level lives in Map Properties

    # ------------------------------------------------------------ GL lifetime

    def _gl_reset_state(self):
        """Forget every GPU object (a new context starts empty)."""
        self._progs = {}
        self._t_grid = None
        self._t_h = None
        self._t_count = 0
        self._t_dims = None
        self._t_idx = None
        self._heights_dirty = True
        self._ground_tex = None
        self._ground_uv = (1.0, 1.0)
        self._ground_dirty = True
        self._wire_buf = None
        self._wire_count = 0
        self._wire_key = None
        self._wire_idx = None
        self._water_buf = None
        self._water_count = 0
        self._water_key = None
        self._border_buf = None
        self._border_counts = (0, 0)
        self._border_key = None
        self._gpu_props = {}
        self._gpu_props_version = None
        self._atlas_tex = None
        self._atlas_slots = (1, 1)
        self._atlas_dirty = True
        self._layers_tex = None
        self._layers_dirty = True

    def initializeGL(self):
        self._gl_reset_state()
        ctx = self.context()
        ctx.aboutToBeDestroyed.connect(self._gl_cleanup)
        self._gl = ctx.functions()
        if self._gl_failed:
            return
        try:
            self._progs["terrain"] = _Program(TERRAIN_VS, TERRAIN_FS, ("a_grid", "a_h"),
                                              ("u_mvp", "u_hscale", "u_uvscale", "u_tex", "u_light"))
            self._progs["model"] = _Program(MODEL_VS, MODEL_FS, ("a_pos", "a_uv"),
                                            ("u_mvp", "u_model", "u_tex", "u_has_tex", "u_cutout",
                                             "u_color", "u_tint", "u_light"))
            self._progs["flat"] = _Program(FLAT_VS, FLAT_FS, ("a_pos",),
                                           ("u_mvp", "u_hscale", "u_color"))
        except Exception as exc:                                     # noqa: BLE001
            self._fail(exc)
            return
        try:
            self._progs["layered"] = _Program(LAYERED_VS, LAYERED_FS, ("a_grid", "a_h"),
                                              ("u_mvp", "u_hscale", "u_atlas", "u_layers",
                                               "u_dims", "u_slots", "u_light"))
        except Exception as exc:                                     # noqa: BLE001
            # only the GPU blend is lost: fall back to the pre-blended image,
            # which the main window builds once it sees needs_ground_image
            sys.stderr.write(f"Render Window: GPU terrain blend unavailable ({exc})\n")
            self._layered_failed = True
            self.doc.textures_changed.emit(None)

    def _gl_cleanup(self):
        """The context is going away (the window was re-parented or closed)."""
        if self.context() is None:
            self._gl_reset_state()
            return
        self.makeCurrent()
        try:
            for buf in (self._t_grid, self._t_h, self._wire_buf, self._water_buf, self._border_buf):
                if buf is not None:
                    buf.destroy()
            for tex in (self._ground_tex, self._atlas_tex, self._layers_tex):
                if tex is not None:
                    tex.destroy()
            self._free_props()
        finally:
            self.doneCurrent()
        self._gl_reset_state()

    def _fail(self, exc):
        """Fall back to the software painter for the rest of the session."""
        self._gl_failed = f"{type(exc).__name__}: {exc}"
        sys.stderr.write("Render Window: OpenGL renderer disabled, using the software painter\n")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        _View3DCore._rebuild(self)

    # ------------------------------------------------------------ core hooks

    def _rebuild(self):
        if self._gl_failed:
            _View3DCore._rebuild(self)
            return
        self._mesh_version += 1
        self._heights_dirty = True
        p = self.doc.project
        if not self._has_ground_image() and not (p is not None and self._layered_ready(p)):
            self._ground_dirty = True               # the colour ramp follows the heights
        self.update()

    def set_ground_image(self, image, dim_x: int = 0, dim_y: int = 0):
        _View3DCore.set_ground_image(self, image, dim_x, dim_y)
        self._ground_dirty = True                   # also when the same image was edited in place

    def set_textures(self, images):
        _View3DCore.set_textures(self, images)
        # the overlays need their alpha, which the software copies drop
        self._tile_images = list(images or [])
        self._atlas_dirty = True
        self._ground_dirty = True

    @property
    def needs_ground_image(self):
        return bool(self._gl_failed) or self._layered_failed

    def set_ground_layers(self, layers, dim_x: int = 0, dim_y: int = 0, rect=None):
        if layers is None:
            self._layers = None
            self._layer_bytes = None
        else:
            self._layers = layers
            self._layer_dims = (int(dim_x), int(dim_y))
            self._layer_bytes = terrain_texture.pack_layers(
                layers, int(dim_x), int(dim_y), self._layer_bytes, rect)
        self._layers_dirty = True
        self.update()

    def _layered_ready(self, p) -> bool:
        return ("layered" in self._progs and self.use_texture and self._layers is not None
                and self._layer_dims == (p.dim_x, p.dim_y)
                and any(img is not None and not img.isNull()
                        for img in self._tile_images))

    def set_props(self, props, geometries=None):
        _View3DCore.set_props(self, props, geometries)
        if self._gl_failed:
            # the software painter needs its level-of-detail copies
            for fid, ma in self.prop_geo.items():
                self.prop_mesh[fid] = self._build_mesh(ma, self.prop_images.get(fid, []), True)

    def _has_ground_image(self) -> bool:
        return (self.use_texture and any(self._tex_ready) and self._ground_image is not None
                and not self._ground_image.isNull())

    # ------------------------------------------------------------ GPU data

    def _sync_terrain(self, p):
        dims = (p.dim_x, p.dim_y)
        if self._t_dims != dims:
            dx, dy = dims
            idx = []
            for y in range(dy - 1):
                row = y * dx
                for x in range(dx - 1):
                    a = row + x
                    idx.extend((a, a + 1, a + dx + 1, a, a + dx + 1, a + dx))
            grid = array("f", [v for i in idx for v in (float(i % dx), float(i // dx))])
            self._t_grid = _new_buffer(grid.tobytes(), False, self._t_grid)
            self._t_idx = idx
            self._t_count = len(idx)
            self._t_dims = dims
            self._heights_dirty = True
            self._wire_key = None
            self._wire_idx = None
        if self._heights_dirty:
            flat = [h for row in p.heights for h in row]
            data = array("f", [flat[i] for i in self._t_idx]).tobytes()
            self._t_h = _new_buffer(data, True, self._t_h)
            self._heights_dirty = False

    def _ramp_image(self, p) -> QImage:
        """One colour per height sample, for a map with no donor textures yet:
        the Top View's height ramp, with steep and risky ground tinted."""
        lo, hi = terrain.height_range(p)
        span = max(1.0, hi - lo)
        cl = self.doc.classes
        dx, dy = p.dim_x, p.dim_y
        buf = bytearray(dx * dy * 4)
        o = 0
        for y in range(dy):
            hrow = p.heights[y]
            crow = cl[min(y, len(cl) - 1)] if cl else None
            for x in range(dx):
                r, g, b = ramp((hrow[x] - lo) / span)
                klass = crow[min(x, len(crow) - 1)] if crow else 0
                if klass == terrain.CLASS_STEEP:
                    r, g, b = r * 0.55 + 99.0, g * 0.55 + 27.0, b * 0.55 + 27.0
                elif klass == terrain.CLASS_RISKY:
                    r, g, b = r * 0.55 + 108.0, g * 0.55 + 67.5, b * 0.55 + 18.0
                buf[o] = min(255, int(r))
                buf[o + 1] = min(255, int(g))
                buf[o + 2] = min(255, int(b))
                buf[o + 3] = 255
                o += 4
        return QImage(bytes(buf), dx, dy, dx * 4, QImage.Format.Format_RGBA8888).copy()

    def _sync_ground(self, p):
        if not self._ground_dirty and self._ground_tex is not None:
            return
        if self._has_ground_image():
            img = self._ground_image
            gdx, gdy = self._ground_dims if self._ground_dims[0] else (p.dim_x, p.dim_y)
        else:
            img = self._ramp_image(p)
            gdx, gdy = p.dim_x, p.dim_y
        if self._ground_tex is not None:
            self._ground_tex.destroy()
        self._ground_tex = _new_texture(img, repeat=False)
        self._ground_uv = (1.0 / max(1, gdx), 1.0 / max(1, gdy))
        self._ground_dirty = False

    def _sync_layered(self, p):
        """Upload the texture atlas and the layer table when they changed."""
        if self._atlas_dirty or self._atlas_tex is None:
            imgs = self._tile_images
            n = max(1, len(imgs))
            cols = min(ATLAS_COLS, n)
            rows = (n + cols - 1) // cols
            atlas = QImage(cols * ATLAS_SLOT, rows * ATLAS_SLOT, QImage.Format.Format_RGBA8888)
            atlas.fill(QColor(*terrain_texture.FALLBACK))
            qp = QPainter(atlas)
            qp.setCompositionMode(QPainter.CompositionMode_Source)
            qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for i, img in enumerate(imgs):
                if img is None or img.isNull():
                    continue
                qp.drawImage(QRectF((i % cols) * ATLAS_SLOT, (i // cols) * ATLAS_SLOT,
                                    ATLAS_SLOT, ATLAS_SLOT), img)
            qp.end()
            if self._atlas_tex is not None:
                self._atlas_tex.destroy()
            tex = _new_texture(atlas, repeat=False)
            try:
                tex.setMipMaxLevel(ATLAS_MIP_MAX)
            except Exception:                                        # noqa: BLE001
                pass
            self._atlas_tex = tex
            self._atlas_slots = (cols, rows)
            self._atlas_dirty = False
        if self._layers_dirty or self._layers_tex is None:
            dx, dy = self._layer_dims
            img = QImage(bytes(self._layer_bytes), dx * 3, dy, dx * 3 * 4,
                         QImage.Format.Format_RGBA8888).copy()
            if self._layers_tex is not None:
                self._layers_tex.destroy()
            tex = QOpenGLTexture(img, QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
            tex.setMinificationFilter(QOpenGLTexture.Filter.Nearest)
            tex.setMagnificationFilter(QOpenGLTexture.Filter.Nearest)
            tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
            self._layers_tex = tex
            self._layers_dirty = False

    def _sync_wire(self, p):
        key = (self._mesh_version, self._t_dims)
        if self._wire_key == key:
            return
        dx, dy = p.dim_x, p.dim_y
        if self._wire_idx is None:
            idx = []
            for y in range(dy):
                for x in range(dx):
                    a = y * dx + x
                    if x + 1 < dx:
                        idx.extend((a, a + 1))
                    if y + 1 < dy:
                        idx.extend((a, a + dx))
            self._wire_idx = idx
        flat = [h for row in p.heights for h in row]
        data = array("f", [v for i in self._wire_idx
                           for v in (float(i % dx), float(i // dx), flat[i])])
        self._wire_buf = _new_buffer(data.tobytes(), True, self._wire_buf)
        self._wire_count = len(self._wire_idx)
        self._wire_key = key

    def _sync_borders(self, p):
        """The 32-cell borders and the map edge as lines lying on the ground.

        They used to be QPainter overlays drawn over everything, so a border
        behind a building showed through it and read as a line hanging in the
        air. Here they are geometry, depth-tested like the rest of the scene."""
        key = (self._mesh_version, p.dim_x, p.dim_y)
        if self._border_key == key:
            return
        self._border_key = key
        h = p.heights
        dx, dy = p.dim_x, p.dim_y
        out = array("f")

        def seg(x0, y0, x1, y1):
            out.extend((x0, y0, h[y0][x0], x1, y1, h[y1][x1]))

        for x in range(32, dx - 1, 32):
            for y in range(dy - 1):
                seg(x, y, x, y + 1)
        for y in range(32, dy - 1, 32):
            for x in range(dx - 1):
                seg(x, y, x + 1, y)
        cells = len(out) // 3
        for x in range(dx - 1):
            seg(x, 0, x + 1, 0)
            seg(x, dy - 1, x + 1, dy - 1)
        for y in range(dy - 1):
            seg(0, y, 0, y + 1)
            seg(dx - 1, y, dx - 1, y + 1)
        self._border_buf = _new_buffer(out.tobytes(), True, self._border_buf)
        self._border_counts = (cells, len(out) // 3 - cells)

    def _draw_borders(self, flat, mvp, hscale):
        f = self._gl
        cells, edge = self._border_counts
        flat.prog.bind()
        flat.set("u_mvp", mvp)
        flat.set("u_hscale", float(hscale))
        self._border_buf.bind()
        flat.prog.setAttributeBuffer(flat.a["a_pos"], GL_FLOAT, 0, 3, 0)
        flat.prog.enableAttributeArray(flat.a["a_pos"])
        marker = QColor(COLORS["marker"])
        passes = []
        if self.show_cells and cells:
            passes += [(0, cells, QVector4D(0.0, 0.0, 0.0, 0.6), 3.0),
                       (0, cells, QVector4D(1.0, 0.9, 0.47, 0.85), 1.0)]
        if edge:
            passes.append((cells, edge, _vec4(marker), 1.6))
        f.glEnable(GL_BLEND)
        f.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        for first, count, colour, width in passes:
            flat.set("u_color", colour)
            f.glLineWidth(width)
            f.glDrawArrays(GL_LINES, first, count)
        f.glLineWidth(1.0)
        f.glDisable(GL_BLEND)
        flat.prog.disableAttributeArray(flat.a["a_pos"])
        self._border_buf.release()
        flat.prog.release()

    def _sync_water(self, p):
        level = getattr(p, "water_level", None)
        key = (level, self._mesh_version, p.dim_x, p.dim_y)
        if self._water_key == key:
            return
        self._water_key = key
        self._water_count = 0
        if level is None:
            return
        # One plane over the whole map, as the client draws it: the depth test
        # hides it under higher ground, and where ground lies on the plane you
        # see the same patches and flicker the client shows. (It used to be
        # drawn only on fully submerged cells, which hid both.)
        lv = float(level)
        x1, y1 = float(p.dim_x - 1), float(p.dim_y - 1)
        out = array("f", (0.0, 0.0, lv, x1, 0.0, lv, x1, y1, lv,
                          0.0, 0.0, lv, x1, y1, lv, 0.0, y1, lv))
        if out:
            self._water_buf = _new_buffer(out.tobytes(), True, self._water_buf)
            self._water_count = len(out) // 3

    def _free_props(self):
        seen = set()
        for subs, _bbox in self._gpu_props.values():
            for buf, _n, tex, _col in subs:
                buf.destroy()
                if tex is not None and id(tex) not in seen:
                    seen.add(id(tex))
                    tex.destroy()
        self._gpu_props = {}

    def _sync_props(self):
        if self._gpu_props_version == self._props_version:
            return
        self._free_props()
        for fid, meshes in self.prop_mesh.items():
            subs = []
            lo = [1e18, 1e18, 1e18]
            hi = [-1e18, -1e18, -1e18]
            textures = {}
            for mesh in meshes:
                verts, tris, _normals, uvs = mesh["lods"][0]
                if not tris:
                    continue
                for v in verts:
                    for k in range(3):
                        lo[k] = min(lo[k], v[k])
                        hi[k] = max(hi[k], v[k])
                nuv = len(uvs) if uvs is not None else 0
                data = array("f")
                for a, b, c in tris:
                    for i in (a, b, c):
                        v = verts[i]
                        if i < nuv:
                            uv = uvs[i]
                            data.extend((v[0], v[1], v[2], uv[0], uv[1]))
                        else:
                            data.extend((v[0], v[1], v[2], 0.0, 0.0))
                img = mesh["image"]
                tex = None
                if img is not None and not img.isNull() and uvs is not None:
                    tex = textures.get(id(img))
                    if tex is None:
                        tex = textures[id(img)] = _new_texture(img, repeat=True)
                subs.append((_new_buffer(data.tobytes()), len(tris) * 3, tex, mesh["colour"]))
            if subs:
                self._gpu_props[fid] = (subs, (lo, hi))
        self._gpu_props_version = self._props_version

    # ------------------------------------------------------------ drawing

    def paintGL(self):
        qp = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(COLORS["sky_top"]))
        g.setColorAt(1, QColor(COLORS["sky_bottom"]))
        qp.fillRect(self.rect(), g)
        p = self.doc.project
        if self._gl_failed:
            self._soft_paint(qp)
            qp.setPen(QColor(COLORS["vtext"]))
            qp.drawText(QRectF(6, self.height() - 20, self.width() - 12, 16),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        "software renderer (OpenGL failed: " + self._gl_failed[:90] + ")")
            qp.end()
            return
        if p is None or p.dim_x < 2 or p.dim_y < 2 or self._gl is None:
            qp.setPen(QColor(COLORS["vtext"]))
            qp.drawText(self.rect(), Qt.AlignCenter, "Render Window")
            qp.end()
            return
        qp.beginNativePainting()
        ok = True
        try:
            self._render_scene(p)
        except Exception as exc:                                     # noqa: BLE001
            ok = False
            failure = exc
        finally:
            self._gl_restore()
            qp.endNativePainting()
        if not ok:
            qp.end()
            self._fail(failure)
            self.update()
            return
        cam = self._camera()
        qp.setRenderHint(QPainter.Antialiasing, True)
        self._paint_spawns(qp, cam)
        self._paint_selected_box(qp, cam)
        self._paint_cursor(qp, cam)
        qp.end()

    def _gl_restore(self):
        f = self._gl
        if f is None:
            return
        f.glDisable(GL_DEPTH_TEST)
        f.glDisable(GL_BLEND)
        f.glDisable(GL_POLYGON_OFFSET_FILL)
        f.glDepthMask(True)
        f.glActiveTexture(GL_TEXTURE0)

    def _render_scene(self, p):
        f = self._gl
        dpr = self.devicePixelRatioF()
        f.glViewport(0, 0, int(round(self.width() * dpr)), int(round(self.height() * dpr)))
        f.glDepthMask(True)
        f.glClearDepthf(1.0)
        f.glClear(GL_DEPTH_BUFFER_BIT)
        f.glEnable(GL_DEPTH_TEST)
        f.glDepthFunc(GL_LEQUAL)
        f.glDisable(GL_CULL_FACE)             # GW props are often single-sided panels
        f.glDisable(GL_BLEND)

        lo, hi = terrain.height_range(p)
        extent = math.hypot(p.dim_x, p.dim_y) + (hi - lo) / CELL * self.exag
        near, far = clip_planes(self._dist(), extent)
        cam = self._camera()
        mvp = _matrix(view_proj(cam, self.width(), self.height(), near, far, FOV))
        n = math.sqrt(sum(c * c for c in LIGHT))
        light = QVector3D(LIGHT[0] / n, LIGHT[1] / n, LIGHT[2] / n)
        hscale = self.exag / CELL

        self._sync_terrain(p)
        layered = self._layered_ready(p)
        if layered:
            self._sync_layered(p)
        else:
            self._sync_ground(p)
        self._sync_props()

        # terrain
        f.glEnable(GL_POLYGON_OFFSET_FILL)          # lines on the ground win the depth test
        f.glPolygonOffset(1.0, 1.0)
        if layered:
            t = self._progs["layered"]
            t.prog.bind()
            t.set("u_mvp", mvp)
            t.set("u_hscale", float(hscale))
            t.set("u_light", light)
            t.set("u_dims", QVector2D(float(p.dim_x), float(p.dim_y)))
            t.set("u_slots", QVector2D(float(self._atlas_slots[0]), float(self._atlas_slots[1])))
            t.set("u_atlas", 0)
            t.set("u_layers", 1)
            f.glActiveTexture(GL_TEXTURE0)
            self._atlas_tex.bind(0)
            self._layers_tex.bind(1)
            bound = (self._atlas_tex, 0), (self._layers_tex, 1)
        else:
            t = self._progs["terrain"]
            t.prog.bind()
            t.set("u_mvp", mvp)
            t.set("u_hscale", float(hscale))
            t.set("u_uvscale", QVector2D(*self._ground_uv))
            t.set("u_light", light)
            t.set("u_tex", 0)
            f.glActiveTexture(GL_TEXTURE0)
            self._ground_tex.bind(0)
            bound = ((self._ground_tex, 0),)
        self._t_grid.bind()
        t.prog.setAttributeBuffer(t.a["a_grid"], GL_FLOAT, 0, 2, 0)
        t.prog.enableAttributeArray(t.a["a_grid"])
        self._t_h.bind()
        t.prog.setAttributeBuffer(t.a["a_h"], GL_FLOAT, 0, 1, 0)
        t.prog.enableAttributeArray(t.a["a_h"])
        f.glDrawArrays(GL_TRIANGLES, 0, self._t_count)
        t.prog.disableAttributeArray(t.a["a_grid"])
        t.prog.disableAttributeArray(t.a["a_h"])
        self._t_h.release()
        for tex, unit in bound:
            tex.release(unit)
        f.glActiveTexture(GL_TEXTURE0)
        t.prog.release()
        f.glDisable(GL_POLYGON_OFFSET_FILL)

        # placed models
        if self.props and self._gpu_props:
            self._draw_props(p, mvp, light)

        flat = self._progs["flat"]
        self._sync_borders(p)
        self._draw_borders(flat, mvp, hscale)
        # wireframe over the ground
        if self.show_wireframe:
            self._sync_wire(p)
            if self._wire_count:
                flat.prog.bind()
                flat.set("u_mvp", mvp)
                flat.set("u_hscale", float(hscale))
                flat.set("u_color", QVector4D(0.60, 0.72, 0.60, 1.0))
                self._wire_buf.bind()
                flat.prog.setAttributeBuffer(flat.a["a_pos"], GL_FLOAT, 0, 3, 0)
                flat.prog.enableAttributeArray(flat.a["a_pos"])
                f.glDrawArrays(GL_LINES, 0, self._wire_count)
                flat.prog.disableAttributeArray(flat.a["a_pos"])
                self._wire_buf.release()
                flat.prog.release()

        # water last: translucent, tested against depth but not writing it
        self._sync_water(p)
        if self._water_count:
            f.glEnable(GL_BLEND)
            f.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            f.glDepthMask(False)
            flat.prog.bind()
            flat.set("u_mvp", mvp)
            flat.set("u_hscale", float(hscale))
            flat.set("u_color", QVector4D(46 / 255.0, 112 / 255.0, 150 / 255.0, 150 / 255.0))
            self._water_buf.bind()
            flat.prog.setAttributeBuffer(flat.a["a_pos"], GL_FLOAT, 0, 3, 0)
            flat.prog.enableAttributeArray(flat.a["a_pos"])
            f.glDrawArrays(GL_TRIANGLES, 0, self._water_count)
            flat.prog.disableAttributeArray(flat.a["a_pos"])
            self._water_buf.release()
            flat.prog.release()
            f.glDepthMask(True)
            f.glDisable(GL_BLEND)

    def _draw_props(self, p, mvp, light):
        f = self._gl
        m = self._progs["model"]
        m.prog.bind()
        m.set("u_mvp", mvp)
        m.set("u_light", light)
        m.set("u_tex", 0)
        m.set("u_cutout", float(ALPHA_CUTOUT))
        f.glActiveTexture(GL_TEXTURE0)
        a_pos, a_uv = m.a["a_pos"], m.a["a_uv"]
        m.prog.enableAttributeArray(a_pos)
        m.prog.enableAttributeArray(a_uv)
        selected = self.doc.selected_prop
        for i, q in enumerate(self.props):
            entry = self._gpu_props.get(q.file_id)
            if entry is None:
                continue
            model = prop_model(prop_matrix(q.rot), prop_scale(q.scale), q.x, q.y, q.z,
                               p.dim_y, CELL, self.exag)
            m.set("u_model", _matrix(model))
            m.set("u_tint", SELECT_TINT if i == selected else NO_TINT)
            for buf, count, tex, colour in entry[0]:
                if tex is not None:
                    tex.bind(0)
                    m.set("u_has_tex", 1.0)
                else:
                    m.set("u_has_tex", 0.0)
                    m.set("u_color", _vec4(colour))
                buf.bind()
                m.prog.setAttributeBuffer(a_pos, GL_FLOAT, 0, 3, MODEL_STRIDE)
                m.prog.setAttributeBuffer(a_uv, GL_FLOAT, 12, 2, MODEL_STRIDE)
                f.glDrawArrays(GL_TRIANGLES, 0, count)
                buf.release()
                if tex is not None:
                    tex.release(0)
        m.prog.disableAttributeArray(a_pos)
        m.prog.disableAttributeArray(a_uv)
        m.prog.release()

    def _paint_selected_box(self, qp, cam):
        """The CS's yellow box around the selected placed model."""
        p = self.doc.project
        i = self.doc.selected_prop
        if p is None or not (0 <= i < len(self.props)):
            return
        q = self.props[i]
        entry = self._gpu_props.get(q.file_id)
        if entry is None:
            return
        lo, hi = entry[1]
        rot = prop_matrix(q.rot)
        scale = prop_scale(q.scale)
        xs, ys = [], []
        for mx in (lo[0], hi[0]):
            for my in (lo[1], hi[1]):
                for mz in (lo[2], hi[2]):
                    gx, gy, wz = self._prop_world(q, rot, scale, mx, my, mz)
                    pt, _d = self._project(gx, gy, wz, cam)
                    xs.append(pt.x())
                    ys.append(pt.y())
        qp.setPen(QPen(QColor("#ffe000"), 2))
        qp.setBrush(Qt.NoBrush)
        qp.drawRect(QRectF(min(xs) - 4, min(ys) - 4, max(xs) - min(xs) + 8, max(ys) - min(ys) + 8))
