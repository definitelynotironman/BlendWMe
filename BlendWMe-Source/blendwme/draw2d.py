"""Shared GPU boxes, smooth curves/diagonals, crisp text and measured layouts.

SDF boundaries use pixel-sized coverage; solid colors retain the theme values.
Fonts are bundled under SIL OFL. Handwriting is shared by all drawn themes.
"""

import math
import os
import re

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_FILES = {"BLACK": "ArchivoBlack-Regular.ttf", "HAND": "PatrickHand-Regular.ttf", "BODY": "Archivo-Regular.ttf"}
_font_ids = {}

LINE = 1.25             # line height = round(1.25 x font size)  (R11)
NBSP = " "         # keeps a number with its unit: "10 s", "0.02 m", "6 of 7"

# Set by the panel while it draws (see scaled()) and put back after, so the viewport
# indicators keep Blender's own scale:
#   ZOOM        the Panel size setting (shapes that size themselves with ui() follow it)
#   FONT_SCALE  the Text size setting       ICON_SCALE  the Icon size setting
#   ALPHA       fades everything (the open and close animation)
#   ROLES       a theme's font roles, e.g. {"HAND": "BODY"}
ZOOM = 1.0
FONT_SCALE = 1.0
ICON_SCALE = 1.0
ALPHA = 1.0
ROLES = {}
ROLE_SIZE = {}          # a size factor per role, so a wider font sets the same words in the same room


class scaled:
    """with d.scaled(zoom, font, icon, roles): ... draws the panel at its size settings."""

    def __init__(self, zoom=1.0, font=1.0, icon=1.0, roles=None):
        self.zoom, self.font, self.icon, self.roles = zoom, font, icon, roles or {}

    def __enter__(self):
        global FONT_SCALE, ICON_SCALE, ZOOM, ROLES, ROLE_SIZE
        ZOOM, FONT_SCALE, ICON_SCALE = self.zoom, self.font, self.icon
        # A role is "BODY" or ("BODY", 0.84): the font to use and how big to set it.
        ROLES = {k: (v[0] if isinstance(v, tuple) else v) for k, v in self.roles.items()}
        ROLE_SIZE = {k: v[1] for k, v in self.roles.items() if isinstance(v, tuple)}
        return self

    def __exit__(self, *exc):
        global FONT_SCALE, ICON_SCALE, ZOOM, ALPHA, ROLES, ROLE_SIZE
        ZOOM, FONT_SCALE, ICON_SCALE, ALPHA, ROLES, ROLE_SIZE = 1.0, 1.0, 1.0, 1.0, {}, {}
        return False


class faded:
    """with d.faded(0.5): ... multiplies the opacity of everything drawn inside."""

    def __init__(self, alpha):
        self.alpha = max(0.0, min(1.0, alpha))

    def __enter__(self):
        global ALPHA
        self.prev = ALPHA
        ALPHA = ALPHA * self.alpha
        return self

    def __exit__(self, *exc):
        global ALPHA
        ALPHA = self.prev
        return False


def _fs(size, key=None):
    """A font size with the Text size setting (and the theme's role size) applied."""
    return size * FONT_SCALE * ROLE_SIZE.get(key, 1.0)


def _c(color):
    """A colour with the current fade applied."""
    if ALPHA >= 1.0:
        return tuple(color)
    return (color[0], color[1], color[2], color[3] * ALPHA)


# ----------------------------------------------------------------------
# Fonts and scale
# ----------------------------------------------------------------------
def load_fonts():
    for key, name in FONT_FILES.items():
        path = os.path.join(FONT_DIR, name)
        fid = blf.load(path) if os.path.isfile(path) else -1
        _font_ids[key] = fid if fid != -1 else 0      # 0 is Blender's own font


def unload_fonts():
    for key, name in FONT_FILES.items():
        if _font_ids.get(key):
            try:
                blf.unload(os.path.join(FONT_DIR, name))
            except Exception:
                pass
    _font_ids.clear()


def font(key):
    if not _font_ids:
        load_fonts()
    return _font_ids.get(ROLES.get(key, "BODY" if key == "CODE" else key), 0)


def system_scale():
    """Blender's scale for add-ons: Resolution Scale times the screen's DPI (R2)."""
    try:
        return float(bpy.context.preferences.system.ui_scale) or 1.0
    except Exception:
        return 1.0


base_ui = system_scale      # the name 2.1 used


def ui():
    """The scale to draw at: Blender's, times the panel size while the panel draws."""
    return system_scale() * ZOOM


def px(v):
    """A design length in whole pixels at the current scale (never below 1 when v > 0)."""
    if not v:
        return 0
    return max(1, round(v * ui())) if v > 0 else -max(1, round(-v * ui()))


# ----------------------------------------------------------------------
# The box shader (R1): a rounded box with a hard cutoff. Each pixel is fully
# inside or fully outside, so edges and corners have no in-between pixels.
# Blender sets `srgbTarget` for every shader, so the theme hex values stay exact.
# ----------------------------------------------------------------------
_BOX = {"shader": None, "failed": False}
_FRAG = """
vec4 to_framebuffer(vec4 c){
  if (!srgbTarget) { return c; }
  vec3 lo = c.rgb / 12.92;
  vec3 hi = pow((c.rgb + 0.055) / 1.055, vec3(2.4));
  return vec4(mix(lo, hi, step(vec3(0.04045), c.rgb)), c.a);
}
float sd_round_box(vec2 p, vec2 b, float r){
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
void main(){
  float d = sd_round_box(local - size * 0.5, size * 0.5, radius);
  float coverage = 1.0 - smoothstep(-0.5, 0.5, d);
  float stroke = border > 0.0 ? smoothstep(-border - 0.5, -border + 0.5, d) : 0.0;
  vec4 c = mix(vec4(fill.rgb * fill.a, fill.a), vec4(edge.rgb * edge.a, edge.a), stroke);
  c.rgb = c.a > 0.0 ? c.rgb / c.a : vec3(0.0);
  c.a *= coverage;
  if (c.a <= 0.0) { discard; }
  FragColor = to_framebuffer(c);
}
"""


def _box_shader():
    if _BOX["shader"] is None and not _BOX["failed"]:
        try:
            iface = gpu.types.GPUStageInterfaceInfo("bwm_box_iface")
            iface.smooth("VEC2", "local")              # pixel position inside the box
            info = gpu.types.GPUShaderCreateInfo()
            info.push_constant("MAT4", "mvp")
            info.push_constant("VEC2", "size")         # box width, height in whole pixels
            info.push_constant("FLOAT", "radius")
            info.push_constant("FLOAT", "border")
            info.push_constant("VEC4", "fill")
            info.push_constant("VEC4", "edge")
            info.push_constant("BOOL", "srgbTarget")
            info.vertex_in(0, "VEC2", "pos")
            info.vertex_in(1, "VEC2", "uv")            # 0..1 across the box
            info.vertex_out(iface)
            info.fragment_out(0, "VEC4", "FragColor")
            info.vertex_source("void main(){ local = uv * size; gl_Position = mvp * vec4(pos, 0.0, 1.0); }")
            info.fragment_source(_FRAG)
            _BOX["shader"] = gpu.shader.create_from_info(info)
        except Exception as e:      # no GPU (background) or an unusual backend: plain rectangles
            _BOX["failed"] = True
            print("BlendWMe: box shader unavailable, using plain boxes:", e)
    return _BOX["shader"]


_batches = {}


def _quad(shader, x, y, w, h):
    """A cached quad batch for a box at whole pixels (rebuilt only when the box changes)."""
    key = (x, y, w, h)
    batch = _batches.get(key)
    if batch is None:
        if len(_batches) > 2000:
            _batches.clear()
        pos = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        uv = ((0, 0), (1, 0), (1, 1), (0, 1))
        batch = batch_for_shader(shader, "TRIS", {"pos": pos, "uv": uv}, indices=((0, 1, 2), (0, 2, 3)))
        _batches[key] = batch
    return batch


def sdf_box(x, y, w, h, radius, border, fill, edge):
    """The one box primitive, in whole pixels: fill, a border `border` px wide, rounded corners."""
    x, y, w, h = round(x), round(y), round(w), round(h)
    if w <= 0 or h <= 0:
        return
    radius = max(0, min(round(radius), w // 2, h // 2))
    border = max(0, round(border))
    fill = _c(fill) if fill is not None else (0.0, 0.0, 0.0, 0.0)
    edge = _c(edge) if edge is not None else fill
    shader = _box_shader()
    if shader is None:
        _plain_box(x, y, w, h, border, fill, edge)
        return
    batch = _quad(shader, x, y, w, h)
    shader.bind()
    shader.uniform_float("mvp", gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
    shader.uniform_float("size", (float(w), float(h)))
    shader.uniform_float("radius", float(radius))
    shader.uniform_float("border", float(border))
    shader.uniform_float("fill", fill)
    shader.uniform_float("edge", edge)
    batch.draw(shader)


def _plain_box(x, y, w, h, border, fill, edge):
    if fill[3] > 0:
        _rect_px(x + border, y + border, w - 2 * border, h - 2 * border, fill)
    if border:
        _rect_px(x, y, w, border, edge)
        _rect_px(x, y + h - border, w, border, edge)
        _rect_px(x, y + border, border, h - 2 * border, edge)
        _rect_px(x + w - border, y + border, border, h - 2 * border, edge)


def _uniform():
    return gpu.shader.from_builtin("UNIFORM_COLOR")


def _rect_px(x, y, w, h, color):
    if w <= 0 or h <= 0:
        return
    shader = _uniform()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": ((x, y), (x + w, y), (x + w, y + h), (x, y + h))},
                     indices=((0, 1, 2), (0, 2, 3))).draw(shader)


def rect(x, y, w, h, color):
    """A plain rectangle at whole pixels."""
    x0, y0, x1, y1 = round(x), round(y), round(x + w), round(y + h)
    _rect_px(x0, y0, x1 - x0, y1 - y0, _c(color))


def box(x, y, w, h, fill=None, border=None, width=2.0, radius=10.0, shadow=None, shadow_offset=6.0):
    """A paper box: hard shadow (no blur), fill and border, all with the same rounded corners.
    x, y, w, h are pixels; width, radius and shadow_offset are design lengths (scaled here)."""
    s = ui()
    r = round(radius * s)
    bw = max(1, round(width * s)) if (border is not None and width) else 0
    if shadow is not None and shadow_offset:
        off = max(1, round(shadow_offset * s))
        sdf_box(round(x) + off, round(y) - off, round(w), round(h), r, 0, shadow, shadow)
    if fill is None and border is None:
        return
    sdf_box(x, y, w, h, r, bw, fill, border if bw else fill)


def disc(cx, cy, r, color):
    """A filled circle with hard pixels."""
    d = max(1, round(2 * r))
    sdf_box(round(cx - d / 2), round(cy - d / 2), d, d, d // 2 + 1, 0, color, color)


def circle(cx, cy, r, color, width=2.0):
    """A ring with hard pixels: `width` px wide, centred on radius r."""
    w = max(1, round(width))
    d = max(2, round(2 * r + w))
    sdf_box(round(cx - d / 2), round(cy - d / 2), d, d, d // 2 + 1, w, None, color)


# ----------------------------------------------------------------------
# Lines: POLYLINE with smoothing off, whole-pixel width (hard pixels on curves too)
# ----------------------------------------------------------------------
def _poly_shader(color, width):
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    vp = gpu.state.viewport_get()
    shader.uniform_float("viewportSize", (vp[2], vp[3]))
    shader.uniform_float("lineWidth", float(max(1, round(width))))
    shader.uniform_float("color", _c(color))
    try:
        shader.uniform_bool("lineSmooth", [True])
    except Exception:
        pass
    return shader


def _snap_points(points, width):
    """Whole pixels: odd widths sit on pixel centres, even widths on pixel edges."""
    half = 0.5 if max(1, round(width)) % 2 else 0.0
    return [(round(x) + half, round(y) + half) for x, y in points]


def polyline(points, color, width=2.0, closed=False):
    if len(points) < 2:
        return
    pts = list(points) + ([points[0]] if closed else [])
    shader = _poly_shader(color, width)
    batch_for_shader(shader, "LINE_STRIP", {"pos": pts}).draw(shader)


def segments(pairs, color, width=2.0):
    """Separate line pieces: [((x0, y0), (x1, y1)), ...]."""
    if not pairs:
        return
    pts = [p for pair in pairs for p in pair]
    shader = _poly_shader(color, width)
    batch_for_shader(shader, "LINES", {"pos": pts}).draw(shader)


def rounded_points(x, y, w, h, r, segments=6):
    """The outline of a rounded rectangle (for dashed paths), counter-clockwise from the bottom left."""
    r = max(0.0, min(r, w / 2, h / 2))
    pts = []
    corners = ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180))
    for cx, cy, start in corners:
        for i in range(segments + 1):
            a = math.radians(start + 90 * i / segments)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def fill_poly(points, color):
    """A convex polygon, filled (used only for the tilted logo tag)."""
    if len(points) < 3:
        return
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    verts = [(cx, cy)] + list(points)
    n = len(points)
    indices = [(0, 1 + i, 1 + (i + 1) % n) for i in range(n)]
    color = _c(color)
    colors = [color] * len(verts)
    # One-pixel coverage fringe, without rounding away the badge's tilt.
    for x, y in points:
        dx, dy = x - cx, y - cy
        distance = max(0.001, math.hypot(dx, dy))
        verts.append((x + dx / distance, y + dy / distance))
        colors.append(color[:3] + (0.0,))
    for i in range(n):
        j = (i + 1) % n
        indices.extend([(1 + i, 1 + j, 1 + n + j), (1 + i, 1 + n + j, 1 + n + i)])
    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    batch_for_shader(shader, "TRIS", {"pos": verts, "color": colors}, indices=indices).draw(shader)


def dashed_path(points, color, width=2.0, dash=7.0, gap=5.0, offset=0.0, closed=True):
    """A dashed line along a path. `offset` (pixels) makes it march."""
    pts = list(points) + ([points[0]] if closed else [])
    dash, gap = max(1.0, round(dash)), max(1.0, round(gap))
    period = dash + gap
    pieces = []
    travelled = period - (offset % period)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0:
            continue
        pos = 0.0
        while pos < length:
            phase = (travelled + pos) % period
            if phase < dash:
                run = min(dash - phase, length - pos)
                a, b = pos / length, (pos + run) / length
                pieces.append(((x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)))
            else:
                run = period - phase
            # Rounding can make a step vanish; always move on by at least a hair.
            pos += max(run, 0.01)
        travelled = (travelled + length) % period
    segments(pieces, color, width)


def circle_points(cx, cy, r, count=40):
    return [(cx + r * math.cos(2 * math.pi * i / count), cy + r * math.sin(2 * math.pi * i / count))
            for i in range(count)]


# ----------------------------------------------------------------------
# Icons (sizes follow the Icon size setting)
# ----------------------------------------------------------------------
def diamond(cx, cy, r, color, width=2.0, filled=False):
    r *= ICON_SCALE
    pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    if filled:
        fill_poly(pts, color)
    else:
        polyline(pts, color, width, closed=True)


def _grow(x, y, size):
    """An icon box at (x, y) scaled by the Icon size setting around its centre."""
    grown = size * ICON_SCALE
    return x - (grown - size) / 2, y - (grown - size) / 2, grown


def check_mark(x, y, size, color, width=2.0):
    """A tick whose bounding box starts at (x, y)."""
    x, y, size = _grow(x, y, size)
    polyline([(x, y + size * 0.5), (x + size * 0.38, y + size * 0.12), (x + size, y + size * 0.85)], color, width)


def cross(cx, cy, size, color, width=2.0):
    h = size * ICON_SCALE / 2
    segments([((cx - h, cy - h), (cx + h, cy + h)), ((cx - h, cy + h), (cx + h, cy - h))], color, width)


def pencil_icon(x, y, size, color, width=1.6):
    """A small pencil: a slanted body with a tip."""
    x, y, size = _grow(x, y, size)
    s = size
    polyline([(x, y), (x + s * 0.25, y + s * 0.08), (x + s, y + s * 0.83), (x + s * 0.83, y + s),
              (x + s * 0.08, y + s * 0.25), (x, y)], color, width)


def eye_icon(cx, cy, size, color, width=2.0):
    size *= ICON_SCALE
    w, h = size, size * 0.55
    top = [(cx - w / 2 + w * i / 12, cy + h / 2 * math.sin(math.pi * i / 12)) for i in range(13)]
    bottom = [(cx + w / 2 - w * i / 12, cy - h / 2 * math.sin(math.pi * i / 12)) for i in range(13)]
    polyline(top + bottom, color, width, closed=True)
    disc(cx, cy, size * 0.16, color)


def ticks(x, y, height, color, phase=None, width=None):
    """The thinking ticks: three slanted bars. `phase` (0..1) pulses them in turn; None is still."""
    width = width or max(2, round(3.0 * ui()))
    grown = height * ICON_SCALE
    y -= (grown - height) / 2
    height = grown
    gap = height * 0.42
    for i in range(3):
        alpha = 1.0
        if phase is not None:
            local = (phase - i / 3.0) % 1.0
            alpha = 0.35 + 0.65 * max(0.0, 1.0 - abs(local - 0.15) * 3.0)
        bx = x + i * gap
        polyline([(bx, y), (bx + height * 0.35, y + height)], color[:3] + (color[3] * alpha,), width)


def scribble(x, y, w, amplitude, color, progress=1.0, kind="wavy", width=2.0):
    """The thinking line drawing itself. Claude: wavy. Codex: zigzag. progress 0..1."""
    count = 48
    pts = []
    for i in range(count + 1):
        t = i / count
        if t > progress:
            break
        if kind == "zigzag":
            phase = (t * 7) % 1.0
            dy = amplitude * (4 * phase - 1 if phase < 0.5 else 3 - 4 * phase)
        else:
            dy = amplitude * math.sin(t * math.pi * 3.2) * (0.7 + 0.3 * math.sin(t * 9))
        pts.append((x + w * t, y + dy))
    polyline(pts, color, width)


def hatch(x, y, w, h, color, spacing=6.0, width=1.5):
    """Diagonal hatching inside a rectangle (clipped)."""
    with clip(x, y, w, h):
        pieces = []
        start = -h
        while start < w:
            pieces.append(((x + start, y), (x + start + h, y + h)))
            start += max(2.0, spacing)
        segments(pieces, color, width)


# ----------------------------------------------------------------------
# Clipping: nested clips intersect, so nothing escapes its card (R4)
# ----------------------------------------------------------------------
_clips = []


class clip:
    """`with clip(x, y, w, h):` limits drawing (shapes and text) to a rectangle."""

    def __init__(self, x, y, w, h):
        x0, y0 = math.floor(x), math.floor(y)
        x1, y1 = math.ceil(x + max(0, w)), math.ceil(y + max(0, h))
        if _clips:
            px0, py0, px1, py1 = _clips[-1]
            x0, y0, x1, y1 = max(x0, px0), max(y0, py0), min(x1, px1), min(y1, py1)
        self.r = (x0, y0, max(x0, x1), max(y0, y1))

    def __enter__(self):
        _clips.append(self.r)
        self._apply(self.r)
        return self

    def __exit__(self, *exc):
        _clips.pop()
        if _clips:
            self._apply(_clips[-1])
        else:
            try:
                gpu.state.scissor_test_set(False)
            except Exception:
                pass
        return False

    @staticmethod
    def _apply(r):
        try:
            gpu.state.scissor_test_set(True)
            gpu.state.scissor_set(r[0], r[1], r[2] - r[0], r[3] - r[1])
        except Exception:
            pass


# ----------------------------------------------------------------------
# Text: one pipeline, measure -> wrap -> place (R4, R5, R11)
# ----------------------------------------------------------------------
_UNIT = r"(?:s|ms|m|cm|mm|km|h|px|%|°|deg|x|fps|GB|MB|KB)"


def keep_together(value):
    """Non-breaking spaces where a line break would split a value: "10 s", "0.02 m",
    "6 of 7", "Step 3". They draw as normal spaces."""
    value = str(value)
    value = re.sub(r"(\d[\d.,]*) (of) (\d)", "\\1" + NBSP + "\\2" + NBSP + "\\3", value)
    value = re.sub(r"(\d[\d.,]*) (" + _UNIT + r")(?=\b|[\s.,;:)!?]|$)", "\\1" + NBSP + "\\2", value)
    value = re.sub(r"\b(Step|Part|Post|Steps|Parts|step|part|post) (\d)", "\\1" + NBSP + "\\2", value)
    return value


def _shown(value):
    return value.replace(NBSP, " ")


def _measure(fid, value):
    return blf.dimensions(fid, _shown(value))[0]


def text(key, size, x, y, value, color):
    """Draw one line with its baseline at y, at whole pixels (R5)."""
    fid = font(key)
    blf.size(fid, _fs(size, key))
    blf.color(fid, *_c(color))
    blf.position(fid, round(x), round(y), 0)
    blf.draw(fid, _shown(str(value)))


def width_of(key, size, value):
    fid = font(key)
    blf.size(fid, _fs(size, key))
    return _measure(fid, str(value))


def fit(key, size, value, max_width):
    """One line that fits: cut at a word and add "…" after measuring (never fades out)."""
    value = str(value)
    fid = font(key)
    blf.size(fid, _fs(size, key))
    if _measure(fid, value) <= max_width:
        return value
    words = value.split(" ")
    while len(words) > 1:
        words.pop()
        trial = " ".join(words).rstrip(",;:.-") + "…"
        if _measure(fid, trial) <= max_width:
            return trial
    word = words[0] if words else ""
    while word and _measure(fid, word + "…") > max_width:
        word = word[:-1]
    return word + "…" if word else ""


def tracked(key, size, x, y, value, color, tracking=0.08):
    """Letter-spaced text (labels in caps, 0.08 em). Returns the width drawn."""
    size = _fs(size, key)
    fid = font(key)
    blf.size(fid, size)
    blf.color(fid, *_c(color))
    cursor = x
    for ch in _shown(value):
        blf.position(fid, round(cursor), round(y), 0)
        blf.draw(fid, ch)
        cursor += blf.dimensions(fid, ch)[0] + size * tracking
    return cursor - x - (size * tracking if value else 0)


def tracked_width(key, size, value, tracking=0.08):
    size = _fs(size, key)
    fid = font(key)
    blf.size(fid, size)
    value = _shown(value)
    return sum(blf.dimensions(fid, ch)[0] for ch in value) + size * tracking * max(0, len(value) - 1)


def tracked_fit(key, size, value, max_width, tracking=0.08):
    """A tracked label that fits: drop words from the end and add "…"."""
    if tracked_width(key, size, value, tracking) <= max_width:
        return value
    words = value.split(" ")
    while len(words) > 1:
        words.pop()
        trial = " ".join(words).rstrip("·,") .rstrip() + "…"
        if tracked_width(key, size, trial, tracking) <= max_width:
            return trial
    return ""


_wrap_cache = {}


def _break_word(fid, word, max_width):
    """A word wider than the line: break it with a hyphen."""
    pieces = []
    while _measure(fid, word) > max_width and len(word) > 1:
        cut = len(word) - 1
        while cut > 1 and _measure(fid, word[:cut] + "-") > max_width:
            cut -= 1
        pieces.append(word[:cut] + "-")
        word = word[cut:]
    pieces.append(word)
    return pieces


def _wrap_paragraph(fid, paragraph, max_width):
    lines = []
    line = ""
    for word in paragraph.split(" "):
        trial = word if not line else line + " " + word
        if _measure(fid, trial) <= max_width:
            line = trial
            continue
        if line:
            lines.append(line)
        if _measure(fid, word) > max_width:
            pieces = _break_word(fid, word, max_width)
            lines.extend(pieces[:-1])
            word = pieces[-1]
        line = word
    lines.append(line)
    # No orphans: a last line of one short word takes a word from the line above (text-wrap: pretty).
    if len(lines) >= 2 and " " not in lines[-1].strip() and lines[-2].count(" ") >= 2:
        head, _, moved = lines[-2].rpartition(" ")
        candidate = moved + " " + lines[-1]
        if _measure(fid, candidate) <= max_width:
            lines[-2], lines[-1] = head, candidate
    return lines


def _balance(fid, lines, max_width):
    """Two lines of similar length (text-wrap: balance), for labels and headings."""
    if len(lines) != 2:
        return lines
    words = (lines[0] + " " + lines[1]).split(" ")
    best, best_w = lines, max(_measure(fid, lines[0]), _measure(fid, lines[1]))
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        wa, wb = _measure(fid, a), _measure(fid, b)
        if wa <= max_width and wb <= max_width and max(wa, wb) < best_w:
            best, best_w = [a, b], max(wa, wb)
    return best


def wrap(key, size, value, max_width, balance=False):
    """Split text into lines that fit max_width. Never cuts text off: wraps at spaces, keeps
    values with their units, breaks over-long words with a hyphen and avoids orphans."""
    size = _fs(size, key)
    max_width = max(1, int(max_width))
    cache_key = (ROLES.get(key, key), round(size, 2), value, max_width, balance)
    hit = _wrap_cache.get(cache_key)
    if hit is not None:
        return hit
    fid = font(key)
    blf.size(fid, size)
    lines = []
    for paragraph in keep_together(str(value)).split("\n"):
        wrapped = _wrap_paragraph(fid, paragraph, max_width)
        lines.extend(_balance(fid, wrapped, max_width) if balance else wrapped)
    if len(_wrap_cache) > 4000:
        _wrap_cache.clear()
    _wrap_cache[cache_key] = lines
    return lines


def line_height(key, size):
    return max(1, round(_fs(size, key) * LINE))


def paragraph(key, size, x, top, value, color, max_width, align="left", max_lines=None, balance=False):
    """Draw wrapped text with its first line's top at `top`. Returns the height used."""
    lines = wrap(key, size, value, max_width, balance)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit(key, size, lines[-1] + "…", max_width)
    lh = line_height(key, size)
    y = top - round(_fs(size, key) * 0.95)
    for line in lines:
        lx = x
        if align == "right":
            lx = x + max_width - width_of(key, size, line)
        elif align == "center":
            lx = x + (max_width - width_of(key, size, line)) / 2
        text(key, size, lx, y, line, color)
        y -= lh
    return len(lines) * lh


def paragraph_height(key, size, value, max_width, max_lines=None, balance=False):
    lines = wrap(key, size, value, max_width, balance)
    if max_lines is not None:
        lines = lines[:max_lines]
    return len(lines) * line_height(key, size)
