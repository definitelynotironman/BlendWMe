"""BlendWMe's visual identity inside Blender: the Graphite Paper overlay.

- A paper card at the bottom of the viewport shows the current step.
  White paper, graphite outline, hard shadow, big readable text.
- Red is used ONLY for indicators: a blinking red block when it is your
  turn, and radar rings around the object being talked about.
- Red outlines can point at other editors (Properties, Outliner, the
  toolbar...) when teaching.
"""

import math
import time

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .state import session

PAPER = (0.965, 0.962, 0.952, 0.97)
PAPER_DARK = (0.90, 0.895, 0.88, 1.0)
GRAPHITE = (0.16, 0.16, 0.17, 1.0)
PENCIL = (0.42, 0.42, 0.44, 1.0)
SHADOW = (0.05, 0.05, 0.06, 0.55)
RED = (0.86, 0.12, 0.12, 1.0)
YOU_BLUE = (0.16, 0.36, 0.62, 1.0)

FONT = 0
_handles = []

_SPACES = {
    "VIEW_3D": bpy.types.SpaceView3D,
    "PROPERTIES": bpy.types.SpaceProperties,
    "OUTLINER": bpy.types.SpaceOutliner,
    "NODE_EDITOR": bpy.types.SpaceNodeEditor,
    "DOPESHEET_EDITOR": bpy.types.SpaceDopeSheetEditor,
    "GRAPH_EDITOR": bpy.types.SpaceGraphEditor,
    "IMAGE_EDITOR": bpy.types.SpaceImageEditor,
    "SEQUENCE_EDITOR": bpy.types.SpaceSequenceEditor,
    "TEXT_EDITOR": bpy.types.SpaceTextEditor,
}
_REGIONS = ("WINDOW", "HEADER", "TOOLS", "UI", "NAVIGATION_BAR")


def _scale():
    try:
        return bpy.context.preferences.system.ui_scale
    except Exception:
        return 1.0


# ----------------------------------------------------------------------
# Drawing primitives
# ----------------------------------------------------------------------

def _rect(x, y, w, h, color):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]},
                             indices=[(0, 1, 2), (0, 2, 3)])
    shader.uniform_float("color", color)
    batch.draw(shader)


def _lines(points, color, width=2.0, closed=False):
    if closed:
        points = list(points) + [points[0]]
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    vp = gpu.state.viewport_get()
    shader.uniform_float("viewportSize", (vp[2], vp[3]))
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": [(p[0], p[1]) for p in points]})
    batch.draw(shader)


def _outline(x, y, w, h, color, width=2.0):
    _lines([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], color, width, closed=True)


def _circle(cx, cy, r, color, width=2.0, segments=48):
    pts = [(cx + r * math.cos(2 * math.pi * i / segments),
            cy + r * math.sin(2 * math.pi * i / segments)) for i in range(segments)]
    _lines(pts, color, width, closed=True)


def _text(x, y, text, size, color):
    blf.size(FONT, size)
    blf.color(FONT, *color)
    blf.position(FONT, x, y, 0)
    blf.draw(FONT, text)


def _wrap(text, size, max_width):
    blf.size(FONT, size)
    out = []
    for paragraph in str(text).split("\n"):
        line = ""
        for word in paragraph.split():
            trial = (line + " " + word).strip()
            if blf.dimensions(FONT, trial)[0] <= max_width or not line:
                line = trial
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def _blink(period=1.0):
    return (time.time() % period) < period * 0.6


# ----------------------------------------------------------------------
# The paper card
# ----------------------------------------------------------------------

_KIND_LABEL = {
    "work": ("BLENDWME IS WORKING", GRAPHITE, False),
    "watch": ("WATCH  -  PRESS NEXT", RED, True),
    "teach": ("YOUR TURN", RED, True),
    "ask": ("BLENDWME ASKS", RED, True),
    "pause": ("YOU HAVE CONTROL", YOU_BLUE, False),
    "done": ("DONE", GRAPHITE, False),
}


def _draw_card(region, card):
    s = _scale()
    pad = 16 * s
    width = min(600 * s, region.width - 40 * s)
    if width < 200 * s:
        return
    title_size = int(19 * s)
    body_size = int(15 * s)
    small = int(12 * s)
    inner = width - 2 * pad
    title_lines = _wrap(card.get("title", ""), title_size, inner)[:2]
    body_lines = _wrap(card.get("text", ""), body_size, inner)[:7]
    why_lines = _wrap(card.get("why", ""), int(13 * s), inner)[:2] if card.get("why") else []
    height = (pad * 2 + small + 10 * s + len(title_lines) * (title_size + 9 * s)
              + len(body_lines) * (body_size + 5 * s)
              + (len(why_lines) * (13 * s + 4 * s) + 6 * s if why_lines else 0))
    x = (region.width - width) / 2
    y = 24 * s
    gpu.state.blend_set("ALPHA")
    _rect(x + 6 * s, y - 6 * s, width, height, SHADOW)          # hard shadow
    _rect(x, y, width, height, PAPER)                            # paper
    _outline(x, y, width, height, GRAPHITE, 2.0 * s)             # graphite outline
    label, color, blinks = _KIND_LABEL.get(card.get("kind"), ("BLENDWME", GRAPHITE, False))
    top = y + height - pad
    block_on = (not blinks) or _blink()
    if block_on:
        _rect(x + pad, top - small, small * 1.4, small, RED if color is RED else color)
    step = card.get("step")
    label_text = label + (f"   STEP {step}" if step and card.get("kind") in ("work", "watch") else "")
    _text(x + pad + small * 2, top - small + 1 * s, label_text, small, color)
    cursor = top - small - 10 * s
    for line in title_lines:
        cursor -= title_size
        _text(x + pad, cursor, line, title_size, GRAPHITE)
        cursor -= 9 * s
    for line in body_lines:
        cursor -= body_size
        _text(x + pad, cursor, line, body_size, GRAPHITE)
        cursor -= 5 * s
    if why_lines:
        cursor -= 6 * s
        for line in why_lines:
            cursor -= 13 * s
            _text(x + pad, cursor, line, int(13 * s), PENCIL)
            cursor -= 4 * s
    gpu.state.blend_set("NONE")


def _draw_thinking(region):
    s = _scale()
    dots = "." * (1 + int(time.time() * 2) % 3)
    text = "BlendWMe is thinking" + dots
    size = int(14 * s)
    blf.size(FONT, size)
    w = blf.dimensions(FONT, "BlendWMe is thinking...")[0] + 40 * s
    h = size + 16 * s
    x = (region.width - w) / 2
    y = 24 * s
    gpu.state.blend_set("ALPHA")
    _rect(x + 4 * s, y - 4 * s, w, h, SHADOW)
    _rect(x, y, w, h, PAPER)
    _outline(x, y, w, h, GRAPHITE, 1.5 * s)
    _rect(x + 10 * s, y + 8 * s, size * 0.9, size * 0.9, RED if _blink(0.8) else PAPER_DARK)
    _text(x + 26 * s, y + 8 * s, text, size, GRAPHITE)
    gpu.state.blend_set("NONE")


# ----------------------------------------------------------------------
# Pointers
# ----------------------------------------------------------------------

def _radar(cx, cy, label=None):
    s = _scale()
    t = time.time()
    gpu.state.blend_set("ALPHA")
    for i in range(3):
        phase = ((t * 0.8) + i / 3.0) % 1.0
        r = (12 + 46 * phase) * s
        alpha = 1.0 - phase
        _circle(cx, cy, r, (RED[0], RED[1], RED[2], alpha), 2.5 * s)
    _circle(cx, cy, 6 * s, RED, 3 * s)
    if label:
        size = int(13 * s)
        blf.size(FONT, size)
        tw = blf.dimensions(FONT, label)[0]
        bx, by = cx + 22 * s, cy + 18 * s
        _rect(bx + 3 * s, by - 3 * s, tw + 14 * s, size + 10 * s, SHADOW)
        _rect(bx, by, tw + 14 * s, size + 10 * s, PAPER)
        _outline(bx, by, tw + 14 * s, size + 10 * s, GRAPHITE, 1.5 * s)
        _text(bx + 7 * s, by + 6 * s, label, size, GRAPHITE)
    gpu.state.blend_set("NONE")


def _object_point(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return sum(corners, Vector()) / 8.0


def _draw_viewport_pointers(region):
    rv3d = bpy.context.region_data
    if rv3d is None:
        return
    for p in list(session.pointers):
        co = None
        if p.get("object") and p["object"] in bpy.data.objects:
            co = _object_point(bpy.data.objects[p["object"]])
        elif p.get("location") and len(p["location"]) == 3:
            co = Vector(p["location"])
        if co is None:
            continue
        xy = location_3d_to_region_2d(region, rv3d, co)
        if xy is not None and 0 <= xy.x <= region.width and 0 <= xy.y <= region.height:
            _radar(xy.x, xy.y, p.get("label") or p.get("object"))


def _editor_matches(area, editor):
    if editor in ("TIMELINE", "DOPESHEET"):
        return area.ui_type == editor
    return area.type == editor


def _draw_region_outline(region):
    area = bpy.context.area
    if area is None:
        return
    for p in list(session.pointers):
        editor = p.get("editor")
        if not editor or not _editor_matches(area, editor):
            continue
        if (p.get("region") or "WINDOW") != region.type:
            continue
        if editor == "VIEW_3D" and region.type == "WINDOW" and not p.get("label"):
            continue  # do not frame the whole viewport without a reason
        s = _scale()
        if _blink(0.9):
            gpu.state.blend_set("ALPHA")
            _outline(3 * s, 3 * s, region.width - 6 * s, region.height - 6 * s, RED, 4 * s)
            gpu.state.blend_set("NONE")
        if p.get("label") and region.height > 30 * s:
            _radar(min(region.width - 30 * s, 30 * s), region.height / 2, p["label"])


# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------

def _draw_view3d_main():
    region = bpy.context.region
    try:
        _draw_viewport_pointers(region)
        _draw_region_outline(region)
        card = session.card
        if card:
            _draw_card(region, card)
        elif session.status == "THINKING":
            _draw_thinking(region)
    except Exception as e:  # never break Blender's drawing
        print("BlendWMe overlay:", e)


def _draw_other():
    try:
        _draw_region_outline(bpy.context.region)
    except Exception as e:
        print("BlendWMe overlay:", e)


def _redraw_timer():
    animate = bool(session.pointers) or bool(session.card) or session.status == "THINKING"
    changed = session.changed_flag
    if animate or changed:
        session.changed_flag = False
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
        except Exception:
            pass
    return 0.05 if animate else 0.25


def register():
    _handles.append((bpy.types.SpaceView3D,
                     bpy.types.SpaceView3D.draw_handler_add(_draw_view3d_main, (), "WINDOW", "POST_PIXEL"),
                     "WINDOW"))
    for name, space in _SPACES.items():
        for region in _REGIONS:
            if name == "VIEW_3D" and region == "WINDOW":
                continue
            try:
                handle = space.draw_handler_add(_draw_other, (), region, "POST_PIXEL")
                _handles.append((space, handle, region))
            except Exception:
                pass  # not every editor has every region
    if not bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.register(_redraw_timer, first_interval=0.2, persistent=True)


def unregister():
    for space, handle, region in _handles:
        try:
            space.draw_handler_remove(handle, region)
        except Exception:
            pass
    _handles.clear()
    if bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.unregister(_redraw_timer)
