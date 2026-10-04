# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""BlendWMe's indicators inside Blender.

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

__author__ = "definitelynotironman"

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
    """True unless reduce motion is on (Blender, the OS or BlendWMe's setting)."""
    return not theme.reduce_motion()


def _pointers_on():
    """The Show pointers setting (on when the preferences are not available)."""
    from . import prefs
    try:
        p = prefs.addon_prefs()
    except Exception:
        p = None
    return p.show_pointers if p else True


# ----------------------------------------------------------------------
# Indicators (whole pixels, hard edges: rules R1 to R5)
# ----------------------------------------------------------------------
def _px(v, s):
    """A design length v at scale s in whole pixels, never below 1."""
    return max(1, round(v * s))


def radar(cx, cy, pal, size=34.0):
    """Two hot rings growing from 0.3x to 1.3x and fading over 1.8 s; the second 0.9 s late."""
    s = d.ui()
    r = size * s
    cx, cy = round(cx), round(cy)
    if _motion():
        t = time.time() - _pointer_start[0]
        for delay in (0.0, 0.9):
            phase = ((t - delay) % 1.8) / 1.8
            d.circle(cx, cy, round(r * (0.3 + phase)), pal.with_alpha(pal.hot, 1.0 - phase), _px(2.5, s))
    else:
        d.circle(cx, cy, round(r * 0.8), pal.hot, _px(2.5, s))
        d.circle(cx, cy, round(r * 1.2), pal.with_alpha(pal.hot, 0.45), _px(2.0, s))
    d.disc(cx, cy, _px(5, s), pal.hot)


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
    width = _px(3.0, s)
    d.polyline(pts, pal.hot, width)
    if arrow:
        ax, ay = cx - rx - 6 * s, cy - ry - 6 * s
        tip = (ax + 18 * s * 0.7, ay + 18 * s * 0.7)
        d.polyline([(ax - 26 * s, ay - 26 * s), tip], pal.hot, width)
        d.polyline([(tip[0] - 12 * s, tip[1]), tip, (tip[0], tip[1] - 12 * s)], pal.hot, width)


def _overlaps(a, b):
    """True when two rectangles (x0, y0, x1, y1) overlap."""
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def bubble_size(words, hand=False):
    """(font role, size, padding, width, height) of a label bubble for these words."""
    s = d.ui()
    key, size = ("HAND", round(17 * s)) if hand else ("BODY", round(14 * s))
    pad = round(12 * s)
    width = round(min(260 * s, d.width_of(key, size, words) + 2 * pad))
    h = round(d.paragraph_height(key, size, words, width - 2 * pad, max_lines=6) + 18 * s)
    return key, size, pad, width, h


def place_bubble(anchor, words, region, avoid=None, hand=False):
    """Where a note goes: left of what it talks about, or right, or below, so it never
    covers the panel (rule R12) and never leaves the region. Returns (x, y)."""
    s = d.ui()
    _, _, _, w, h = bubble_size(words, hand)
    ax, ay = anchor
    gap = round(40 * s)
    edge = round(6 * s)
    tries = [(ax - w - gap, ay + round(24 * s)), (ax + gap, ay + round(24 * s)),
             (ax - w / 2, ay - h - gap), (ax - w / 2, ay + gap)]
    best = None
    for x, y in tries:
        x = max(edge, min(x, region.width - w - edge))
        y = max(edge, min(y, region.height - h - edge))
        rect = (x, y, x + w, y + h)
        if avoid is None or not _overlaps(rect, avoid):
            return round(x), round(y)
        best = best or (round(x), round(y))
    return best


def bubble(x, y, words, pal, anchor=None, hand=False):
    """A paper note. Its bottom left is (x, y). A dashed leader goes to `anchor`."""
    s = d.ui()
    key, size, pad, width, h = bubble_size(words, hand)
    x, y = round(x), round(y)
    if anchor is not None:
        # The leader leaves from the side nearest the anchor.
        sx = x + width if anchor[0] > x + width / 2 else x
        d.dashed_path([(sx, y + h / 2), anchor], pal.ink, _px(2, s), 7 * s, 5 * s, closed=False)
    d.box(x, y, width, h, fill=pal.card, border=pal.ink, width=theme.BORDER_CARD, radius=8, shadow=pal.shadow,
          shadow_offset=theme.SHADOW_BUTTON)
    with d.clip(x, y, width, h):
        d.paragraph(key, size, x + pad, y + h - round(9 * s), words, pal.ink, width - 2 * pad, max_lines=6)
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
    m = round(14 * s)
    x0 = round(min(b[0] for b in boxes)) - m
    y0 = round(min(b[1] for b in boxes)) - m
    x1 = round(max(b[2] for b in boxes)) + m
    y1 = round(max(b[3] for b in boxes)) + m
    offset = ((time.time() % 10.0) * 24 * s) if _motion() else 0.0
    d.dashed_path(d.rounded_points(x0, y0, x1 - x0, y1 - y0, round(4 * s)), pal.ink, _px(2, s), 9 * s, 6 * s,
                  offset)
    words = "Building now"
    size = round(16 * s)
    w = round(d.width_of("HAND", size, words) + 40 * s)
    h = round(28 * s)
    tx, ty = x0 + round(10 * s), y1 - h // 2
    d.box(tx, ty, w, h, fill=pal.card, border=pal.ink, width=theme.BORDER_CARD, radius=h / 2 / s)
    if brain == "codex":
        d.diamond(tx + round(15 * s), ty + h / 2, 6 * s, pal.ink, _px(2, s))
    else:
        d.circle(tx + round(15 * s), ty + h // 2, round(5.5 * s), pal.ink, _px(2, s))
    d.text("HAND", size, tx + round(28 * s), ty + h / 2 - size * 0.32, words, pal.ink)


def _free(area, region):
    """(left, right) of the viewport that the toolbar and the sidebar do not cover."""
    try:
        from . import panel
        left, _, right, _ = panel.free_area(area, region)
        return left, right
    except Exception:
        return 0, region.width


def working_card(region, pal, area=None):
    """Top of the viewport (screen 31): who is working, the step, the scribble line."""
    from .relay import relay
    s = d.ui()
    brain = relay.running or relay.active
    card = session.card or {}
    name = {"claude": "Claude", "codex": "Codex"}.get(brain, "BlendWMe")
    verb = "working" if session.status == WORKING else "thinking"
    progress = (card.get("progress") or "").upper()
    head = f"{name} is {verb}".upper() + (f" · {progress}" if progress else "")
    title = card.get("title") or ""
    left, right = _free(area, region) if area is not None else (0, region.width)
    width = round(min(560 * s, right - left - 40 * s))
    if width < 240 * s:
        return
    h = round((92 if title else 58) * s)
    x = round(left + (right - left - width) / 2)
    y = round(region.height - h - 60 * s)
    d.box(x, y, width, h, fill=pal.panel, border=pal.ink, width=theme.BORDER_CARD, radius=8, shadow=pal.shadow,
          shadow_offset=4)
    top = y + h - round(22 * s)
    phase = ((time.time() % 1.2) / 1.2) if _motion() else None
    d.ticks(x + round(14 * s), top - round(6 * s), round(12 * s), pal.ink, phase)
    if brain == "codex":
        d.diamond(x + round(46 * s), top, 6 * s, pal.ink, _px(2, s))
    else:
        d.circle(x + round(46 * s), top, round(5.5 * s), pal.ink, _px(2, s))
    hint = "Esc pauses"
    hs = round(15 * s)
    hw = d.width_of("HAND", hs, hint)
    with d.clip(x, y, width, h):
        room = width - round(74 * s) - hw - round(14 * s)
        d.tracked("BLACK", round(10.5 * s), x + round(60 * s), top - round(4 * s),
                  d.tracked_fit("BLACK", round(10.5 * s), head, room), pal.ink)
        d.text("HAND", hs, x + width - hw - round(14 * s), top - round(5 * s), hint, pal.muted)
        if title:
            ts = round(20 * s)
            d.text("HAND", ts, x + round(14 * s), top - round(34 * s),
                   d.fit("HAND", ts, title, width - round(28 * s)), pal.ink)
            prog = ((time.time() % 2.6) / 2.6) if _motion() else 1.0
            d.scribble(x + round(16 * s), y + round(14 * s), min(220 * s, width * 0.4), 3 * s, pal.ink, prog,
                       "zigzag" if brain == "codex" else "wavy", _px(2, s))


def cue_card(region, card, pal, area=None):
    """Your turn, at the bottom of the viewport, when BlendWMe is not showing there."""
    s = d.ui()
    left, right = _free(area, region) if area is not None else (0, region.width)
    width = round(min(560 * s, right - left - 40 * s))
    if width < 240 * s:
        return
    words = card.get("text") or card.get("title") or ""
    tw = width - round(80 * s)
    size = round(20 * s)
    h = round(54 * s + d.paragraph_height("HAND", size, words, tw, max_lines=5))
    x = round(left + (right - left - width) / 2)
    y = round(30 * s)
    d.box(x, y, width, h, fill=pal.card, border=pal.ink, width=theme.BORDER_CARD, radius=10, shadow=pal.shadow,
          shadow_offset=theme.SHADOW_PANEL)
    top = y + h - round(18 * s)
    on = (not _motion()) or (time.time() % 2.0) < 1.0
    block = round(22 * s)
    d.box(x + round(18 * s), top - block, block, block, fill=pal.hot if on else pal.with_alpha(pal.hot, 0.18),
          border=pal.ink if on else None, width=1, radius=2)
    kind = card.get("kind")
    head = {"teach": "Your turn", "watch": "Press Next", "ask": "A question for you"}.get(kind, "Your turn")
    with d.clip(x, y, width, h):
        d.tracked("BLACK", round(11 * s), x + round(56 * s), top - round(10 * s), head.upper(), pal.ink)
        d.paragraph("HAND", size, x + round(56 * s), top - round(18 * s), words, pal.ink, tw, max_lines=5)


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
    """True when the area is the editor a pointer names (Timeline and Dope Sheet by ui_type)."""
    if editor in ("TIMELINE", "DOPESHEET"):
        return area.ui_type == editor
    return area.type == editor


# ----------------------------------------------------------------------
# Draw handlers
# ----------------------------------------------------------------------
def _blendwme_shows_in(area):
    """True when BlendWMe itself is visible in this viewport: the drawn panel (not
    minimized), or with "Blender UI" the sidebar open on the BlendWMe tab."""
    from . import panel
    if theme.native():
        from . import ui
        return ui.sidebar_shows_blendwme(area)
    return panel.is_expanded_in(area)


def _panel_rect(area):
    """The drawn panel's rectangle in this area, or None when it is not open there."""
    from . import panel
    if panel.is_open_in(area):
        return panel.state.rect
    return None


def _draw_view3d_main():
    """The 3D Viewport overlay: object rings with labels, the building outline,
    region pointers and the cue or working card when BlendWMe itself is not showing.

    It never raises: a drawing error is printed instead of breaking Blender's redraw.
    """
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
        avoid = _panel_rect(area)
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
                    anchor = (round(xy.x), round(xy.y + 8 * s))
                    bx, by = place_bubble(anchor, p["label"], region, avoid)
                    bubble(bx, by, p["label"], pal, anchor=anchor)
        _draw_region_pointer(region, area, pal)
        if not _blendwme_shows_in(area):
            # BlendWMe is closed or minimized here: a "your turn" cue still shows on the
            # viewport. The working card shows only when it is closed (the minimized bar
            # already says "Building").
            card = session.card
            if card and not card.get("hidden") and session.status in (WAIT_USER, WAIT_NEXT, ASKING):
                cue_card(region, card, pal, area)
            elif relay.is_running() and session.status in (THINKING, WORKING) and not panel.is_open_in(area):
                working_card(region, pal, area)
        gpu.state.blend_set("NONE")
    except Exception as e:  # never break Blender's drawing
        print("BlendWMe overlay:", e)


def _draw_region_pointer(region, area, pal):
    """Pointers to an editor region: a radar on a Properties tab, else a pencil loop.

    An uncalibrated region pointer circles the region honestly; it never claims
    to mark an exact control.
    """
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
        inset = round(10 * s)
        pencil_circle(inset, inset, region.width - 2 * inset, region.height - 2 * inset, pal,
                      arrow=region.width > 120 * s and region.height > 120 * s)
        if p.get("label") and region.width > 160 * s and region.height > 50 * s:
            avoid = _panel_rect(area) if (area.type == "VIEW_3D" and region.type == "WINDOW") else None
            bx, by = place_bubble((round(14 * s), round(region.height / 2)), p["label"], region, avoid, hand=True)
            bubble(bx, by, p["label"], pal, hand=True)


def _draw_other():
    """Overlay draw callback for every other editor and region (region pointers only)."""
    try:
        import gpu
        gpu.state.blend_set("ALPHA")
        _draw_region_pointer(bpy.context.region, bpy.context.area, theme.current())
        gpu.state.blend_set("NONE")
    except Exception as e:
        print("BlendWMe overlay:", e)


# ----------------------------------------------------------------------
# One timer for every redraw (rule R6): about 30 fps only while something moves,
# and only the regions that show it. Idle, it checks four times a second.
# ----------------------------------------------------------------------
FAST = 1 / 30
IDLE_EVERY = 0.25
SIDEBAR_EVERY = 0.5          # the "Blender UI" sidebar's live line (the seconds) updates twice a second
_last_sidebar = [0.0]


def _pointed_editors():
    """The editors the current pointers name."""
    return {p.get("editor") for p in session.pointers if p.get("editor")}


def _tag(area, kinds):
    """Tag the area's regions of the given kinds for redraw."""
    for region in area.regions:
        if region.type in kinds:
            region.tag_redraw()


def redraw_plan():
    """What to redraw now: (viewport overlays move, the panel moves, the sidebar is live)."""
    from . import panel
    from .relay import relay
    moving = _motion()
    working = relay.is_running() and session.status in (THINKING, WORKING)
    your_turn = session.status in (WAIT_USER, WAIT_NEXT, ASKING) and bool(session.card)
    overlays = moving and _pointers_on() and bool(session.pointers)
    # The cue and working cards on the viewport blink and tick when BlendWMe is not showing.
    cards = moving and (working or your_turn)
    panel_moves = panel.state.open and not theme.native() and panel.animates()
    sidebar = theme.native() and working
    return overlays, cards, panel_moves, sidebar


def _redraw_timer():
    """The one redraw timer (R6): fold the Done card, read Properties tabs and tag
    only the regions that change. Returns ~30 fps while something moves, else
    an idle interval.
    """
    # The Done card collapses after 6 s into one line in the conversation.
    card = session.card
    if card and card.get("kind") == "done" and time.time() - card.get("t", time.time()) > DONE_SECONDS:
        from .panel_ops import collapse_done
        collapse_done(card)
    if any(p.get("tab") for p in session.pointers):
        _read_tabs()
    from . import panel
    try:
        overlays, cards, panel_moves, sidebar = redraw_plan()
    except Exception:
        overlays = cards = panel_moves = sidebar = False
    changed = session.changed_flag
    session.changed_flag = False
    now = time.time()
    sidebar_due = sidebar and now - _last_sidebar[0] >= SIDEBAR_EVERY
    if sidebar_due:
        _last_sidebar[0] = now
    editors = _pointed_editors()
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    if changed:
                        _tag(area, ("WINDOW", "UI", "HEADER"))
                        continue
                    if overlays or (cards and not _blendwme_shows_in(area)):
                        _tag(area, ("WINDOW",))
                    elif panel_moves and area.as_pointer() == panel.state.area_ptr:
                        _tag(area, ("WINDOW",))
                    if sidebar_due:
                        _tag(area, ("UI",))
                elif editors and any(_editor_matches(area, e) for e in editors) and (changed or overlays):
                    area.tag_redraw()
    except Exception:
        pass
    return FAST if (overlays or cards or panel_moves) else (SIDEBAR_EVERY if sidebar else IDLE_EVERY)


def register():
    """Add the draw handlers for every editor and region, and the redraw timer."""
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
    """Remove every draw handler and the timer."""
    for space, handle, region in _handles:
        try:
            space.draw_handler_remove(handle, region)
        except Exception:
            pass
    _handles.clear()
    if bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.unregister(_redraw_timer)
