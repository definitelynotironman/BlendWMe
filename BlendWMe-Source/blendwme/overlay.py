"""BlendWMe's indicators inside Blender (docs/design/UI_SPEC.md, section 6).

Colour rule: hot (red, orange in Blender Grey) means YOU: look here, do this.
Ink means the AI is working.

    Radar ring        two hot rings growing 0.3x -> 1.3x and fading, 1.8 s, the
                      second 0.9 s late: "look at this object / tab"
    Pencil circle     a hand-drawn hot loop and a short arrow around a control area
    Note bubble       a paper bubble with a dashed leader to what it talks about
    Building outline  a marching dashed ink box around what the AI is making now
    Working card      top of the viewport, only when the panel is not open there
    Cue card          your turn, at the bottom, only when the panel is not open there

With "reduce motion" everything is drawn in its still state.
Pointers are dicts from the brain's point_at / teach_step: object, location,
editor, region, tab, label; tool_host adds building=True for parts being made.
"""

import math
import time

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

from . import draw2d as d
from . import theme
from .state import ASKING, THINKING, WAIT_NEXT, WAIT_USER, WORKING, session

_handles = []
_pointer_start = [time.time()]
_tabs = {}            # Properties editor: as_pointer() -> the tab ids it shows, in order
DONE_SECONDS = 6.0

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

# Properties editor tab ids (4.2) and the names the brains may use for them.
TAB_ALIASES = {"MODIFIERS": "MODIFIER", "MATERIALS": "MATERIAL", "OBJECT_DATA": "DATA", "MESH": "DATA",
               "CONSTRAINTS": "CONSTRAINT", "PARTICLE": "PARTICLES", "TEXTURES": "TEXTURE",
               "WORLD_SETTINGS": "WORLD", "RENDER_SETTINGS": "RENDER"}


def restart_pointers():
    """'Move': run the pointer animation again so the eye finds it."""
    _pointer_start[0] = time.time()
    session.changed_flag = True


def _motion():
    return not theme.reduce_motion()


def _pointers_on():
    from . import prefs
    try:
        p = prefs.addon_prefs()
    except Exception:
        p = None
    return p.show_pointers if p else True


# ----------------------------------------------------------------------
# Indicators
# ----------------------------------------------------------------------
def radar(cx, cy, pal, size=34.0):
    """Two hot rings growing from 0.3x to 1.3x and fading over 1.8 s; the second 0.9 s late."""
    s = d.ui()
    r = size * s
    if _motion():
        t = time.time() - _pointer_start[0]
        for delay in (0.0, 0.9):
            phase = ((t - delay) % 1.8) / 1.8
            d.circle(cx, cy, r * (0.3 + phase), pal.with_alpha(pal.hot, 1.0 - phase), 2.5 * s)
    else:
        d.circle(cx, cy, r * 0.8, pal.hot, 2.5 * s)
        d.circle(cx, cy, r * 1.2, pal.with_alpha(pal.hot, 0.45), 2.0 * s)
    d.disc(cx, cy, 5 * s, pal.hot)


def pencil_circle(x, y, w, h, pal, arrow=True):
    """A hand-drawn hot loop around a control, overshooting a little, plus a short arrow."""
    s = d.ui()
    cx, cy = x + w / 2, y + h / 2
    rx, ry = w / 2 + 8 * s, h / 2 + 8 * s
    pts = []
    turns = 1.12
    for i in range(73):
        t = i / 72 * turns
        a = 2 * math.pi * t + 0.6
        wobble = 1.0 + 0.04 * math.sin(a * 3.0) + 0.05 * t
        pts.append((cx + rx * wobble * math.cos(a), cy + ry * wobble * math.sin(a)))
    d.polyline(pts, pal.hot, 3.0 * s)
    if arrow:
        ax, ay = cx - rx - 6 * s, cy - ry - 6 * s
        tip = (ax + 18 * s * 0.7, ay + 18 * s * 0.7)
        d.polyline([(ax - 26 * s, ay - 26 * s), tip], pal.hot, 3.0 * s)
        d.polyline([(tip[0] - 12 * s, tip[1]), tip, (tip[0], tip[1] - 12 * s)], pal.hot, 3.0 * s)


def bubble(x, y, words, pal, anchor=None, hand=False):
    """A paper note. Its bottom left is (x, y). A dashed leader goes to `anchor`."""
    s = d.ui()
    key, size = ("HAND", 17 * s) if hand else ("BODY", 14 * s)
    width = min(260 * s, d.width_of(key, size, words) + 24 * s)
    h = d.paragraph_height(key, size, words, width - 24 * s) + 18 * s
    if anchor is not None:
        d.dashed_path([(x + width, y + h / 2), anchor], pal.ink, 2 * s, 7 * s, 5 * s, closed=False)
    d.box(x, y, width, h, fill=pal.card, border=pal.ink, width=2.0, radius=8, shadow=pal.shadow,
          shadow_offset=3)
    d.paragraph(key, size, x + 12 * s, y + h - 9 * s, words, pal.ink, width - 24 * s)
    return width, h


def _screen_bounds(region, rv3d, obj):
    """The object's bounding box on screen, or None when it is not visible."""
    pts = []
    for corner in obj.bound_box:
        p2 = location_3d_to_region_2d(region, rv3d, obj.matrix_world @ Vector(corner))
        if p2 is not None:
            pts.append(p2)
    if not pts:
        return None
    x0, x1 = min(p.x for p in pts), max(p.x for p in pts)
    y0, y1 = min(p.y for p in pts), max(p.y for p in pts)
    if x1 < 0 or y1 < 0 or x0 > region.width or y0 > region.height:
        return None
    return x0, y0, x1, y1


def building_outline(region, rv3d, names, pal, brain):
    """A marching dashed ink box around what is being built, with a "Building now" tag."""
    s = d.ui()
    boxes = []
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            b = _screen_bounds(region, rv3d, obj)
            if b:
                boxes.append(b)
    if not boxes:
        return
    x0 = min(b[0] for b in boxes) - 14 * s
    y0 = min(b[1] for b in boxes) - 14 * s
    x1 = max(b[2] for b in boxes) + 14 * s
    y1 = max(b[3] for b in boxes) + 14 * s
    offset = ((time.time() % 10.0) * 24 * s) if _motion() else 0.0
    d.dashed_path(d.rounded_points(x0, y0, x1 - x0, y1 - y0, 4 * s), pal.ink, 2.2 * s, 9 * s, 6 * s, offset)
    words = "Building now"
    size = 16 * s
    w = d.width_of("HAND", size, words) + 40 * s
    h = 28 * s
    tx, ty = x0 + 10 * s, y1 - h / 2
    d.box(tx, ty, w, h, fill=pal.card, border=pal.ink, width=2.0, radius=h / 2 / s)
    if brain == "codex":
        d.diamond(tx + 15 * s, ty + h / 2, 6 * s, pal.ink, 1.8 * s)
    else:
        d.circle(tx + 15 * s, ty + h / 2, 5.5 * s, pal.ink, 1.8 * s)
    d.text("HAND", size, tx + 28 * s, ty + h / 2 - size * 0.32, words, pal.ink)


def working_card(region, pal):
    """Top of the viewport (screen 31): who is working, the step, the scribble line."""
    from .relay import relay
    s = d.ui()
    brain = relay.running or relay.active
    card = session.card or {}
    name = {"claude": "Claude", "codex": "Codex"}.get(brain, "BlendWMe")
    verb = "working" if session.status == WORKING else "thinking"
    progress = (card.get("progress") or "").upper()
    head = f"{name} is {verb}" .upper() + (f" · {progress}" if progress else "")
    title = card.get("title") or ""
    width = min(560 * s, region.width - 40 * s)
    if width < 240 * s:
        return
    h = (92 if title else 58) * s
    x = (region.width - width) / 2
    y = region.height - h - 60 * s
    d.box(x, y, width, h, fill=pal.panel, border=pal.ink, width=2.5, radius=8, shadow=pal.shadow, shadow_offset=4)
    top = y + h - 22 * s
    phase = ((time.time() % 1.2) / 1.2) if _motion() else None
    d.ticks(x + 14 * s, top - 6 * s, 12 * s, pal.ink, phase)
    if brain == "codex":
        d.diamond(x + 46 * s, top, 6 * s, pal.ink, 1.8 * s)
    else:
        d.circle(x + 46 * s, top, 5.5 * s, pal.ink, 1.8 * s)
    d.tracked("BLACK", 10.5 * s, x + 60 * s, top - 4 * s, head, pal.ink)
    hint = "Esc pauses"
    hw = d.width_of("HAND", 15 * s, hint)
    d.text("HAND", 15 * s, x + width - hw - 14 * s, top - 5 * s, hint, pal.muted)
    if title:
        d.text("HAND", 20 * s, x + 14 * s, top - 34 * s, title, pal.ink)
        prog = ((time.time() % 2.6) / 2.6) if _motion() else 1.0
        d.scribble(x + 16 * s, y + 14 * s, min(220 * s, width * 0.4), 3 * s, pal.ink, prog,
                   "zigzag" if brain == "codex" else "wavy", 2.2 * s)


def cue_card(region, card, pal):
    """Your turn, at the bottom of the viewport, when the panel is not open here."""
    s = d.ui()
    width = min(560 * s, region.width - 40 * s)
    if width < 240 * s:
        return
    words = card.get("text") or card.get("title") or ""
    tw = width - 80 * s
    h = 54 * s + d.paragraph_height("HAND", 20 * s, words, tw)
    x = (region.width - width) / 2
    y = 30 * s
    d.box(x, y, width, h, fill=pal.card, border=pal.ink, width=2.5, radius=10, shadow=pal.shadow, shadow_offset=6)
    top = y + h - 18 * s
    on = (not _motion()) or (time.time() % 2.0) < 1.0
    d.box(x + 18 * s, top - 22 * s, 22 * s, 22 * s, fill=pal.hot if on else pal.with_alpha(pal.hot, 0.18),
          border=pal.ink if on else None, width=1.5, radius=2)
    kind = card.get("kind")
    head = {"teach": "Your turn", "watch": "Press Next", "ask": "A question for you"}.get(kind, "Your turn")
    d.tracked("BLACK", 11 * s, x + 56 * s, top - 10 * s, head.upper(), pal.ink)
    d.paragraph("HAND", 20 * s, x + 56 * s, top - 18 * s, words, pal.ink, tw)


# ----------------------------------------------------------------------
# Where things are
# ----------------------------------------------------------------------
def properties_tabs(space):
    """The tab ids this Properties editor shows, in order (cached by the redraw timer)."""
    return _tabs.get(space.as_pointer(), [])


def _read_tabs():
    """Ask each Properties editor for its tabs. Blender lists the valid ones when an invalid
    one is set, which is the only way Python can learn which tabs are visible."""
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "PROPERTIES":
                    continue
                space = area.spaces.active
                try:
                    space.context = "__BLENDWME__"
                except TypeError as e:
                    text = str(e)
                    start, end = text.find("("), text.rfind(")")
                    if start != -1:
                        _tabs[space.as_pointer()] = [t.strip().strip("'") for t in text[start + 1:end].split(",")]
    except Exception:
        pass


def tab_rect(region, space, tab):
    """Where a tab sits in the Properties editor's tab column (NAVIGATION_BAR), or None."""
    tabs = properties_tabs(space)
    tab = TAB_ALIASES.get(tab.upper(), tab.upper())
    if tab not in tabs:
        return None
    # Blender draws the tab column as icon buttons 1.4 UI units tall (UI unit = 20 px at 100 %),
    # with a short separator between groups and a little padding at the top.
    unit = 20 * d.ui()
    tab_h = 1.4 * unit
    sep = 0.45 * unit
    groups = ({"TOOL"}, {"RENDER", "OUTPUT", "VIEW_LAYER", "SCENE", "WORLD"}, {"COLLECTION"},
              {"OBJECT", "MODIFIER", "SHADERFX", "PARTICLES", "PHYSICS", "CONSTRAINT", "DATA", "BONE",
               "BONE_CONSTRAINT", "MATERIAL"}, {"TEXTURE"})
    y = region.height - 0.4 * unit
    previous_group = None
    for t in tabs:
        group = next((i for i, g in enumerate(groups) if t in g), 3)
        if previous_group is not None and group != previous_group:
            y -= sep
        previous_group = group
        y -= tab_h
        if t == tab:
            return (region.width - tab_h) / 2, y, tab_h, tab_h
    return None


def _editor_matches(area, editor):
    if editor in ("TIMELINE", "DOPESHEET"):
        return area.ui_type == editor
    return area.type == editor


# ----------------------------------------------------------------------
# Draw handlers
# ----------------------------------------------------------------------
def _draw_view3d_main():
    from . import panel
    from .relay import relay
    region = bpy.context.region
    area = bpy.context.area
    rv3d = bpy.context.region_data
    try:
        import gpu
        gpu.state.blend_set("ALPHA")
        pal = theme.current()
        brain = relay.running or relay.active
        if rv3d is not None and _pointers_on():
            building = [p["object"] for p in session.pointers if p.get("building") and p.get("object")]
            if building:
                building_outline(region, rv3d, building, pal, brain)
            for p in list(session.pointers):
                if p.get("building") or p.get("editor"):
                    continue
                co = None
                if p.get("object") and p["object"] in bpy.data.objects:
                    obj = bpy.data.objects[p["object"]]
                    co = obj.matrix_world @ (sum((Vector(c) for c in obj.bound_box), Vector()) / 8.0)
                elif p.get("location") and len(p["location"]) == 3:
                    co = Vector(p["location"])
                if co is None:
                    continue
                xy = location_3d_to_region_2d(region, rv3d, co)
                if xy is None or not (0 <= xy.x <= region.width and 0 <= xy.y <= region.height):
                    continue
                radar(xy.x, xy.y, pal)
                if p.get("label"):
                    s = d.ui()
                    bubble(xy.x - 300 * s, xy.y + 40 * s, p["label"], pal, anchor=(xy.x - 12 * s, xy.y + 8 * s))
        _draw_region_pointer(region, area, pal)
        if not panel.is_open_in(area):
            card = session.card
            if card and not card.get("hidden") and session.status in (WAIT_USER, WAIT_NEXT, ASKING):
                cue_card(region, card, pal)
            elif relay.is_running() and session.status in (THINKING, WORKING):
                working_card(region, pal)
        gpu.state.blend_set("NONE")
    except Exception as e:  # never break Blender's drawing
        print("BlendWMe overlay:", e)


def _draw_region_pointer(region, area, pal):
    if area is None or not _pointers_on():
        return
    s = d.ui()
    for p in list(session.pointers):
        editor = p.get("editor")
        if not editor or not _editor_matches(area, editor):
            continue
        tab = p.get("tab")
        if tab and area.type == "PROPERTIES":
            if region.type != "NAVIGATION_BAR":
                continue
            rect = tab_rect(region, area.spaces.active, tab)
            if rect:
                x, y, w, h = rect
                radar(x + w / 2, y + h / 2, pal, size=26)
                continue
        if (p.get("region") or "WINDOW") != region.type:
            continue
        if editor == "VIEW_3D" and region.type == "WINDOW" and not p.get("label"):
            continue        # never circle the whole viewport without a reason
        # The pencil circle around the part of the editor, inset so it stays visible.
        inset = 10 * s
        pencil_circle(inset, inset, region.width - 2 * inset, region.height - 2 * inset, pal,
                      arrow=region.width > 120 * s and region.height > 120 * s)
        if p.get("label") and region.width > 160 * s and region.height > 50 * s:
            bubble(14 * s, region.height / 2, p["label"], pal, hand=True)


def _draw_other():
    try:
        import gpu
        gpu.state.blend_set("ALPHA")
        _draw_region_pointer(bpy.context.region, bpy.context.area, theme.current())
        gpu.state.blend_set("NONE")
    except Exception as e:
        print("BlendWMe overlay:", e)


def _redraw_timer():
    # The Done card collapses after 6 s into one line in the conversation.
    card = session.card
    if card and card.get("kind") == "done" and time.time() - card.get("t", time.time()) > DONE_SECONDS:
        from .panel_ops import collapse_done
        collapse_done(card)
    if any(p.get("tab") for p in session.pointers):
        _read_tabs()
    from . import panel
    animate = (bool(session.pointers) or bool(session.card) or session.status in (THINKING, WORKING)
               or panel.state.open)
    if animate and theme.reduce_motion():
        animate = panel.state.focused          # still keep the text caret alive
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
    if _handles:            # registered twice: start clean
        unregister()
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
