"""2D drawing for the BlendWMe panel and the viewport indicators (gpu + blf).

Everything is drawn in region pixels with the origin at the bottom left.
Sizes in the design are CSS pixels at 100 %; multiply them by `ui()`.

Fonts are bundled in fonts/ (SIL Open Font License) and loaded with blf.load():
    BLACK  Archivo Black   labels, buttons, the logo
    HAND   Patrick Hand    UI text, cue cards, your messages, the input
    BODY   Archivo         Claude's and Codex's messages, notes, traces
"""

import math
import os

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_FILES = {"BLACK": "ArchivoBlack-Regular.ttf", "HAND": "PatrickHand-Regular.ttf", "BODY": "Archivo-Regular.ttf"}
_font_ids = {}

# Line heights as a multiple of the font size.
LINE = {"BLACK": 1.25, "HAND": 1.22, "BODY": 1.38}


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
    return _font_ids.get(key, 0)


def ui():
    """Blender's interface scale (Resolution Scale times the screen's own scale)."""
    try:
        system = bpy.context.preferences.system
        return system.dpi / 72.0
    except Exception:
        return 1.0


# ----------------------------------------------------------------------
# Shapes
# ----------------------------------------------------------------------
def _uniform():
    return gpu.shader.from_builtin("UNIFORM_COLOR")


def _poly_shader(color, width):
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    vp = gpu.state.viewport_get()
    shader.uniform_float("viewportSize", (vp[2], vp[3]))
    shader.uniform_float("lineWidth", max(1.0, width))
    shader.uniform_float("color", color)
    return shader


def rounded_points(x, y, w, h, r, segments=6):
    """The outline of a rounded rectangle, counter-clockwise from the bottom left corner."""
    r = max(0.0, min(r, w / 2, h / 2))
    pts = []
    corners = ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180))
    for cx, cy, start in corners:
        for i in range(segments + 1):
            a = math.radians(start + 90 * i / segments)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def fill_poly(points, color):
    """A convex polygon, filled."""
    if len(points) < 3:
        return
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    verts = [(cx, cy)] + list(points)
    n = len(points)
    indices = [(0, 1 + i, 1 + (i + 1) % n) for i in range(n)]
    shader = _uniform()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices).draw(shader)


def rect(x, y, w, h, color):
    shader = _uniform()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]},
                     indices=[(0, 1, 2), (0, 2, 3)]).draw(shader)


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


def box(x, y, w, h, fill=None, border=None, width=2.5, radius=10.0, shadow=None, shadow_offset=6.0):
    """A paper box: hard shadow (no blur), fill and ink border, all with rounded corners."""
    s = ui()
    r = radius * s
    if shadow is not None and shadow_offset:
        off = shadow_offset * s
        fill_poly(rounded_points(x + off, y - off, w, h, r), shadow)
    if fill is not None:
        fill_poly(rounded_points(x, y, w, h, r), fill)
    if border is not None and width:
        polyline(rounded_points(x, y, w, h, r), border, width * s, closed=True)


def dashed_path(points, color, width=2.0, dash=7.0, gap=5.0, offset=0.0, closed=True):
    """A dashed line along a path. `offset` (pixels) makes it march."""
    pts = list(points) + ([points[0]] if closed else [])
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


def circle(cx, cy, r, color, width=2.0):
    polyline(circle_points(cx, cy, r), color, width, closed=True)


def disc(cx, cy, r, color):
    fill_poly(circle_points(cx, cy, r, 28), color)


def diamond(cx, cy, r, color, width=2.0, filled=False):
    pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    if filled:
        fill_poly(pts, color)
    else:
        polyline(pts, color, width, closed=True)


def check_mark(x, y, size, color, width=2.0):
    """A tick whose bounding box starts at (x, y)."""
    polyline([(x, y + size * 0.5), (x + size * 0.38, y + size * 0.12), (x + size, y + size * 0.85)], color, width)


def cross(cx, cy, size, color, width=2.0):
    h = size / 2
    segments([((cx - h, cy - h), (cx + h, cy + h)), ((cx - h, cy + h), (cx + h, cy - h))], color, width)


def pencil_icon(x, y, size, color, width=1.6):
    """A small pencil: a slanted body with a tip."""
    s = size
    polyline([(x, y), (x + s * 0.25, y + s * 0.08), (x + s, y + s * 0.83), (x + s * 0.83, y + s),
              (x + s * 0.08, y + s * 0.25), (x, y)], color, width)


def eye_icon(cx, cy, size, color, width=2.0):
    w, h = size, size * 0.55
    top = [(cx - w / 2 + w * i / 12, cy + h / 2 * math.sin(math.pi * i / 12)) for i in range(13)]
    bottom = [(cx + w / 2 - w * i / 12, cy - h / 2 * math.sin(math.pi * i / 12)) for i in range(13)]
    polyline(top + bottom, color, width, closed=True)
    disc(cx, cy, size * 0.16, color)


def ticks(x, y, height, color, phase=None, width=None):
    """The thinking ticks: three slanted bars. `phase` (0..1) pulses them in turn; None is still."""
    s = ui()
    width = width or 3.0 * s
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
    """Diagonal hatching inside a rectangle (clipped by scissor)."""
    with clip(x, y, w, h):
        pieces = []
        start = -h
        while start < w:
            pieces.append(((x + start, y), (x + start + h, y + h)))
            start += spacing
        segments(pieces, color, width)


class clip:
    """`with clip(x, y, w, h):` limits drawing (shapes and text) to a rectangle."""

    def __init__(self, x, y, w, h):
        self.r = (int(x), int(y), max(0, int(w)), max(0, int(h)))

    def __enter__(self):
        self.prev = gpu.state.scissor_get() if hasattr(gpu.state, "scissor_get") else None
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(*self.r)
        return self

    def __exit__(self, *exc):
        if self.prev:
            gpu.state.scissor_set(*self.prev)
        gpu.state.scissor_test_set(False)


# ----------------------------------------------------------------------
# Text
# ----------------------------------------------------------------------
def text(key, size, x, y, value, color):
    """Draw one line with its baseline at y."""
    fid = font(key)
    blf.size(fid, size)
    blf.color(fid, *color)
    blf.position(fid, x, y, 0)
    blf.draw(fid, value)


def width_of(key, size, value):
    fid = font(key)
    blf.size(fid, size)
    return blf.dimensions(fid, value)[0]


def tracked(key, size, x, y, value, color, tracking=0.08):
    """Letter-spaced text (labels in caps, 0.08 em). Returns the width drawn."""
    fid = font(key)
    blf.size(fid, size)
    blf.color(fid, *color)
    cursor = x
    for ch in value:
        blf.position(fid, cursor, y, 0)
        blf.draw(fid, ch)
        cursor += blf.dimensions(fid, ch)[0] + size * tracking
    return cursor - x - (size * tracking if value else 0)


def tracked_width(key, size, value, tracking=0.08):
    fid = font(key)
    blf.size(fid, size)
    return sum(blf.dimensions(fid, ch)[0] for ch in value) + size * tracking * max(0, len(value) - 1)


_wrap_cache = {}


def wrap(key, size, value, max_width):
    """Split text into lines that fit max_width. Never cuts text off: long words break."""
    cache_key = (key, round(size, 2), value, int(max_width))
    if cache_key in _wrap_cache:
        return _wrap_cache[cache_key]
    fid = font(key)
    blf.size(fid, size)
    lines = []
    for paragraph in str(value).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            trial = word if not line else line + " " + word
            if blf.dimensions(fid, trial)[0] <= max_width:
                line = trial
                continue
            if line:
                lines.append(line)
            # A word wider than the line is broken by characters.
            while blf.dimensions(fid, word)[0] > max_width and len(word) > 1:
                cut = len(word)
                while cut > 1 and blf.dimensions(fid, word[:cut])[0] > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    if len(_wrap_cache) > 3000:
        _wrap_cache.clear()
    _wrap_cache[cache_key] = lines
    return lines


def line_height(key, size):
    return size * LINE.get(key, 1.3)


def paragraph(key, size, x, top, value, color, max_width, align="left", max_lines=None):
    """Draw wrapped text with its first line's top at `top`. Returns the height used."""
    lines = wrap(key, size, value, max_width)
    if max_lines is not None:
        lines = lines[:max_lines]
    lh = line_height(key, size)
    y = top - size * 0.95
    for line in lines:
        lx = x
        if align == "right":
            lx = x + max_width - width_of(key, size, line)
        elif align == "center":
            lx = x + (max_width - width_of(key, size, line)) / 2
        text(key, size, lx, y, line, color)
        y -= lh
    return len(lines) * lh


def paragraph_height(key, size, value, max_width, max_lines=None):
    lines = wrap(key, size, value, max_width)
    if max_lines is not None:
        lines = lines[:max_lines]
    return len(lines) * line_height(key, size)
