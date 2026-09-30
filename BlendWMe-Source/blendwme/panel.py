"""The BlendWMe panel, drawn inside the 3D Viewport (the four drawn themes).

Blender's own panels cannot take custom fonts, colours or shapes, so BlendWMe draws
its panel with its one renderer (draw2d: hard pixels, whole pixels, Blender's scale).
A modal operator (panel_ops) handles clicks, typing and dragging while the mouse is
over the panel, and lets every other event through to Blender. With the "Blender UI"
theme nothing is drawn here: BlendWMe lives in the sidebar instead (ui.py).

Views (rule R10): Full, Compact (header, the current card, buttons and the input) and
Minimized (a small bar). Full turns Compact by itself when the viewport is too small.
Changing view animates only the frame; the content is laid out at its final size and
faded in, so text never reflows mid-animation.

Place and size (R9): the panel keeps a corner of the free viewport and a distance from
it, in design units, so it stays put when the viewport, the sidebar or the scale
changes. The free viewport ("safe area") leaves out the header, the toolbar, the
sidebar and the navigation gizmo. Its height follows the content up to the safe area.

Screens: "main" (Together / Teach / Auto) and "journal". Settings are a native popover.
The drawing is immediate: each redraw lays the panel out again and records the
clickable rectangles (hits) for the modal operator.
"""

import os
import time
import traceback

import bpy

from . import draw2d as d
from . import theme
from .state import ASKING, DONE, ERROR, IDLE, PAUSED, THINKING, WAIT_NEXT, WAIT_USER, WORKING, session

PANEL_WIDTH = 372          # design px
PAD = 16
GAP = 12
HEADER_H = 40
MIN_WIDTH = 300            # design px (rule R9)
MIN_HEIGHT = 220
WIDTH_GRID = 8             # the width snaps to this grid
EDGE = 3                   # design px between the panel and the edge it sits against
SNAP = 12                  # design px: magnetic snap to edges and corners while dragging
READ_MORE_LINES = 12       # longer messages show this many lines and "Read more"
EXPAND_TIME = 0.20         # seconds, ease-out
COLLAPSE_TIME = 0.15       # seconds, ease-in

EXAMPLES = ("A cosy low poly cottage", "A modern room with a bed", "A simple ice cream cone")
EXAMPLE_PROMPTS = {
    "A cosy low poly cottage": "A cosy low poly cottage with a red tiled roof, a chimney, a wooden door, four "
                               "windows, a stone base and a small path. Stylised, warm colours.",
    "A modern room with a bed": "Model me a modern room with two walls only, open on the viewer's side. In it a "
                                "bed, a PC setup and a closet.",
    "A simple ice cream cone": "Model me a simple ice cream cone.",
}
CHIPS = {
    "WATCH": (("Look again", "/look"), ("Undo step", "/undo"), ("Explain", "Explain what you just did and why.")),
    "TEACH": (("Hint", "Give me a hint for this step."), ("Skip", "teach:skip"), ("Do it for me", "teach:show_me")),
}
MODES = (("AUTO", "Auto"), ("TEACH", "Teach"))


def label(brain):
    return {"claude": "Claude", "codex": "Codex"}.get(brain, "BlendWMe")


# ----------------------------------------------------------------------
# Panel state (one per Blender session; plain Python values only)
# ----------------------------------------------------------------------
class PanelState:
    def __init__(self):
        self.open = False
        self.area_ptr = 0              # as_pointer() of the 3D Viewport that hosts the panel
        self.screen = "main"           # main | journal
        self.rect = (0, 0, 0, 0)       # x0, y0, x1, y1 in region pixels (with the shadow)
        self.frame = (0, 0, 0, 0)      # the panel box itself
        self.header = (0, 0, 0, 0)     # the drag handle
        self.input_box = (0, 0, 0, 0)  # where the input sits (click to place the caret)
        self.hits = []                 # [(x0, y0, x1, y1, action)]
        self.scroll = 0.0              # conversation: pixels scrolled up from the newest message
        self.max_scroll = 0.0
        self.scroll_rect = (0, 0, 0, 0)
        self.body_scroll = 0.0         # the journal screen
        self.body_max = 0.0
        self.text = ""                 # what the user is typing
        self.cursor = 0
        self.anchor = None             # selection start (None: no selection)
        self.undo = []                 # [(text, cursor)] for Ctrl+Z
        self.redo = []
        self.focused = False
        self.history = []
        self.history_at = None
        self.expanded = set()          # ids of messages opened with "Read more"
        self.slider = None             # (x0, x1) of the pace slider, while dragging
        self.turn_started = 0.0
        self.last_status = IDLE
        self.hover = None
        self.next_id = 1
        self.error_shown = False
        self.cleared_at = 0.0          # Ctrl+L: messages before this time are not shown
        self.minimized = False         # folded into the small bar; the session keeps going
        self.compact = False           # header, card, buttons and input only
        self.auto_compact = False      # Compact because the viewport is too small for Full
        self.anim = None               # the frame animation between views
        self.drag = None               # dragging the header, the minimized bar or the corner grip
        self.last_view = "full"        # what was drawn last (the start of the next animation)
        self.last_rect = (0, 0, 0, 0)
        self.last_content = None
        self.input_layout = None       # (text x, top, line height, size, first line, lines)
        self.hover_cursor = False      # the hand cursor is showing over the header

    def view(self):
        if self.minimized:
            return "mini"
        return "compact" if (self.compact or self.auto_compact) else "full"


state = PanelState()


def register():
    from . import panel_ops
    if _handles:
        unregister()
    d.load_fonts()
    for cls in panel_ops.classes:
        bpy.utils.register_class(cls)
    # Registered after the overlay's handler, so the panel is drawn on top.
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw_callback, (), "WINDOW", "POST_PIXEL"))


def unregister():
    from . import panel_ops
    for cls in reversed(panel_ops.classes):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
    for handle in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass
    _handles.clear()
    state.open = False
    d.unload_fonts()


_handles = []


def is_open_in(area):
    return state.open and area is not None and area.as_pointer() == state.area_ptr and not theme.native()


def is_expanded_in(area):
    """Open here and not folded into the minimized bar."""
    return is_open_in(area) and not state.minimized


def _prefs():
    from . import prefs
    try:
        return prefs.addon_prefs()
    except Exception:
        return None


def _wm_props():
    try:
        return bpy.context.window_manager.blendwme
    except AttributeError:
        return None


def your_turn():
    """True when the user is expected to act (the logo's full stop blinks)."""
    card = session.card or {}
    return session.status in (WAIT_USER, WAIT_NEXT, ASKING) or card.get("kind") == "limit"


def animates():
    """True when something in the panel moves on its own (blink, ticks, caret, the frame)."""
    if state.anim:
        return True
    if theme.reduce_motion():
        return False
    from .relay import relay
    return your_turn() or relay.is_running() or state.focused


def sizes():
    """(zoom, text, icon) from the Panel size, Text size and Icon size settings."""
    p = _prefs()
    if p is None:
        return 0.75, 1.0, 1.0
    return p.panel_size / 100.0, p.text_size / 100.0, p.icon_size / 100.0


def scale():
    """Pixels per design pixel: Blender's scale (Resolution Scale and DPI) times the panel size."""
    return d.system_scale() * sizes()[0]


# ----------------------------------------------------------------------
# Where the panel goes (rule R9)
# ----------------------------------------------------------------------
def free_area(area, region):
    """(left, bottom, right, top) of the viewport region that the toolbar, the sidebar
    and the header do not cover (they overlap the viewport when Region Overlap is on)."""
    left, bottom, right, top = 0.0, 0.0, float(region.width), float(region.height)
    for r in area.regions:
        if r.width <= 1 or r.height <= 1 or r.type == "WINDOW":
            continue
        overlaps_x = region.x <= r.x < region.x + region.width
        overlaps_y = region.y <= r.y < region.y + region.height
        if r.type == "UI" and overlaps_x and overlaps_y:
            right = min(right, r.x - region.x)
        if r.type == "TOOLS" and overlaps_y and r.x <= region.x + 2:
            left = max(left, r.x + r.width - region.x)
        if r.type in ("HEADER", "TOOL_HEADER") and overlaps_y and r.y > region.y + region.height / 2:
            top = min(top, r.y - region.y)
    return left, bottom, right, top


def gizmo_shown(area):
    try:
        space = area.spaces.active
        return bool(space.show_gizmo and space.show_gizmo_navigate)
    except Exception:
        return False


def gizmo_rect(area, region):
    """The navigation gizmo and its buttons in the top right of the viewport, or None."""
    p = _prefs()
    if (p is not None and not p.avoid_gizmo) or not gizmo_shown(area):
        return None
    left, bottom, right, top = free_area(area, region)
    s = d.system_scale()
    try:
        size = bpy.context.preferences.view.gizmo_size_navigate_v3d
    except Exception:
        size = 80
    w = round((size + 24) * s)
    h = round((size + 24 + 4 * 30) * s)      # the ball and the zoom, pan, camera and ortho buttons
    return right - w, top - h, right, top


def usable(area, region):
    """The safe area minus the edge gap and room for the hard shadow (right and down)."""
    s = scale()
    left, bottom, right, top = free_area(area, region)
    edge = round(EDGE * s)
    shadow = round(theme.SHADOW_PANEL * s)
    return left + edge, bottom + edge + shadow, right - edge - shadow, top - edge


def _avoid_gizmo(rect, area, region, bounds):
    """Move a rectangle out of the gizmo the shortest way: left, or down (shorter if needed)."""
    g = gizmo_rect(area, region)
    if g is None:
        return rect
    x0, y0, x1, y1 = rect
    gx0, gy0, gx1, gy1 = g
    if x1 <= gx0 or x0 >= gx1 or y1 <= gy0 or y0 >= gy1:
        return rect
    s = scale()
    ul, ub, ur, ut = bounds
    edge = round(EDGE * s) + round(theme.SHADOW_PANEL * s)
    options = []
    dx = x1 - (gx0 - edge)
    if x0 - dx >= ul:
        options.append((dx, (x0 - dx, y0, x1 - dx, y1)))
    dy = y1 - (gy0 - round(EDGE * s))
    if dy > 0:
        ny0, ny1 = y0 - dy, y1 - dy
        if ny0 < ub:                       # shorter, never below the minimum
            ny0 = ub
        if ny1 - ny0 >= min(MIN_HEIGHT * s, y1 - y0):
            options.append((dy + (y1 - y0) - (ny1 - ny0), (x0, ny0, x1, ny1)))
    if not options:
        return rect
    return min(options, key=lambda o: o[0])[1]


def panel_width(area, region):
    s = scale()
    p = _prefs()
    ul, ub, ur, ut = usable(area, region)
    width = (p.panel_width if p and p.panel_width else PANEL_WIDTH) * s
    return round(max(min(MIN_WIDTH * s, ur - ul), min(width, ur - ul)))


def placement(area, region, natural=None, width=None):
    """The panel rectangle (x0, y0, x1, y1) in the 3D Viewport's main region.

    `natural` is the height the content wants (None: as tall as the safe area). The
    corner grip sets a fixed height; maximize makes it full height. It always stays
    inside the safe area and keeps its corner and distance from it."""
    s = scale()
    p = _prefs()
    ul, ub, ur, ut = usable(area, region)
    w = width if width is not None else panel_width(area, region)
    avail = max(1.0, ut - ub)
    if p and p.panel_max:
        h = avail
    elif p and p.panel_height:
        h = p.panel_height * s
    elif natural is not None:
        h = natural
    else:
        h = avail
    h = round(max(min(MIN_HEIGHT * s, avail), min(h, avail)))
    corner = p.panel_corner if p else "TR"
    ox = round((p.panel_off_x if p else 0.0) * s)
    oy = round((p.panel_off_y if p else 0.0) * s)
    if corner in ("TR", "BR"):
        x1 = ur - ox
        x0 = x1 - w
    else:
        x0 = ul + ox
        x1 = x0 + w
    if corner in ("TR", "TL"):
        y1 = ut - oy
        y0 = y1 - h
    else:
        y0 = ub + oy
        y1 = y0 + h
    # Inside the safe area, however far the offsets say.
    shift_x = max(0, ul - x0) - max(0, x1 - ur)
    shift_y = max(0, ub - y0) - max(0, y1 - ut)
    rect = (x0 + shift_x, y0 + shift_y, x1 + shift_x, y1 + shift_y)
    rect = _avoid_gizmo(rect, area, region, (ul, ub, ur, ut))
    return tuple(round(v) for v in rect)


def bar_rect(area, region, w, h):
    """The minimized bar: at the panel's corner, the size of its content."""
    x0, y0, x1, y1 = placement(area, region, natural=h, width=w)
    p = _prefs()
    corner = p.panel_corner if p else "TR"
    if corner in ("TR", "TL"):
        return x0, y1 - h, x1, y1
    return x0, y0, x1, y0 + h


def to_corner(rect, bounds):
    """(corner, off_x, off_y) in pixels for a rectangle: the nearest corner of the safe area."""
    x0, y0, x1, y1 = rect
    ul, ub, ur, ut = bounds
    right = (x0 + x1) / 2 > (ul + ur) / 2
    top = (y0 + y1) / 2 > (ub + ut) / 2
    corner = ("T" if top else "B") + ("R" if right else "L")
    ox = (ur - x1) if right else (x0 - ul)
    oy = (ut - y1) if top else (y0 - ub)
    return corner, max(0.0, ox), max(0.0, oy)


def snap_rect(rect, bounds, area, region, s):
    """Magnetic snap to the safe area's edges and corners (and the gizmo's edges)."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    dist = SNAP * s
    ul, ub, ur, ut = bounds
    xs = [(ul, "x0"), (ur, "x1")]
    ys = [(ub, "y0"), (ut, "y1")]
    g = gizmo_rect(area, region)
    if g is not None:
        edge = round(EDGE * s) + round(theme.SHADOW_PANEL * s)
        xs.append((g[0] - edge, "x1"))
        ys.append((g[1] - round(EDGE * s), "y1"))
    best_x = min(((abs((x0 if k == "x0" else x1) - v), v, k) for v, k in xs), default=None)
    if best_x and best_x[0] < dist:
        x0 = best_x[1] if best_x[2] == "x0" else best_x[1] - w
    best_y = min(((abs((y0 if k == "y0" else y1) - v), v, k) for v, k in ys), default=None)
    if best_y and best_y[0] < dist:
        y0 = best_y[1] if best_y[2] == "y0" else best_y[1] - h
    return x0, y0, x0 + w, y0 + h


def clamp_rect(rect, bounds):
    x0, y0, x1, y1 = rect
    ul, ub, ur, ut = bounds
    w, h = x1 - x0, y1 - y0
    x0 = max(ul, min(x0, ur - w))
    y0 = max(ub, min(y0, ut - h))
    return x0, y0, x0 + w, y0 + h


def smooth_view_on():
    """Blender's own "Smooth View" time: 0 means the user turned view animation off."""
    try:
        return bpy.context.preferences.view.smooth_view > 0 and not theme.reduce_motion()
    except Exception:
        return True


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_in(t):
    return t * t


# ----------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------
class Painter:
    """Draws one frame of the panel and records where the buttons are."""

    def __init__(self, area, region):
        self.area, self.region = area, region
        self.zoom, self.text_scale, self.icon_scale = sizes()
        self.s = d.system_scale() * self.zoom
        self.p = theme.current()
        self.motion = not theme.reduce_motion()
        self.now = time.time()
        self.hits = []
        self.prefs = _prefs()
        self.props = _wm_props()
        from .relay import relay
        self.relay = relay

    # -- small helpers --------------------------------------------------
    def px(self, v):
        """A design length in whole pixels."""
        if not v:
            return 0
        return max(1, round(v * self.s)) if v > 0 else -max(1, round(-v * self.s))

    def hit(self, x, y, w, h, action):
        self.hits.append((x, y, x + w, y + h, action))

    def blink(self, period=1.0):
        """1 s on, 1 s off by default. Still (on) when motion is reduced."""
        if not self.motion:
            return True
        return (self.now % (2 * period)) < period

    def card_box(self, x, y, w, h, fill=None, border=None, width=theme.BORDER_CARD, dashed=False):
        p = self.p
        if dashed:
            d.box(x, y, w, h, fill=fill or p.card, border=None, radius=theme.RADIUS)
            d.dashed_path(d.rounded_points(x + self.px(1), y + self.px(1), w - self.px(2), h - self.px(2),
                                           self.px(theme.RADIUS)), border or p.ink,
                          self.px(width), self.px(7), self.px(5))
        else:
            d.box(x, y, w, h, fill=fill or p.card, border=border or p.ink, width=width, radius=theme.RADIUS)

    def label_caps(self, x, y, value, color=None, size=11, max_width=None):
        value = value.upper()
        if max_width is not None:
            value = d.tracked_fit("BLACK", self.px(size), value, max_width)
        return d.tracked("BLACK", self.px(size), x, y, value, color or self.p.ink)

    def centered(self, key, size, x, y, w, h, value, color, cap=0.72):
        """One line centred in a box, cut with "…" when it does not fit."""
        value = d.fit(key, size, value, w - self.px(12))
        tw = d.width_of(key, size, value)
        d.text(key, size, x + (w - tw) / 2, y + (h - size * cap) / 2, value, color)

    def button(self, x, y, w, h, value, action, primary=False, hot=False, size=14):
        p = self.p
        fill = p.hot if hot else (p.accent if primary else p.card)
        ink = p.on_accent if (primary or hot) else p.ink
        d.box(x, y, w, h, fill=fill, border=p.ink if not hot else p.hot, width=theme.BORDER_CARD,
              radius=theme.RADIUS, shadow=p.shadow, shadow_offset=theme.SHADOW_BUTTON)
        self.centered("BLACK", self.px(size), x, y, w, h, value, ink)
        self.hit(x, y, w, h, action)

    def chip(self, x, y, w, h, value, action):
        p = self.p
        d.box(x, y, w, h, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=h / 2 / self.s)
        self.centered("HAND", self.px(18), x, y, w, h, value, p.ink, cap=0.62)
        self.hit(x, y, w, h, action)

    def close_x(self, x1, y1, action="close_card"):
        size = self.px(10)
        cx, cy = x1 - self.px(16), y1 - self.px(16)
        d.cross(cx, cy, size, self.p.muted, self.px(2))
        self.hit(cx - self.px(12), cy - self.px(12), self.px(24), self.px(24), action)

    def mark(self, brain, cx, cy, r, color=None):
        color = color or self.p.ink
        if brain == "codex":
            d.diamond(cx, cy, r * 1.15, color, self.px(2))          # diamond() applies the icon size
        elif brain == "you":
            pass
        else:
            d.circle(cx, cy, r * d.ICON_SCALE, color, self.px(2))

    def you_box(self, x, y, size):
        """The small boxed word "YOU"."""
        w = d.tracked_width("BLACK", size, "YOU") + self.px(6)
        d.box(x, y - self.px(3), w, size + self.px(5), border=self.p.ink, width=1, radius=3)
        d.tracked("BLACK", size, x + self.px(3), y, "YOU", self.p.ink)
        return w

    # -- the whole panel -----------------------------------------------
    def draw(self):
        import gpu
        gpu.state.blend_set("ALPHA")
        try:
            with d.scaled(self.zoom, self.text_scale, self.icon_scale, self.p.roles):
                self.draw_views()
        finally:
            gpu.state.blend_set("NONE")
        # Later hits are on top: the modal checks them last-first. No clicks mid-animation.
        state.hits = [] if state.anim else self.hits

    def target(self, view):
        """(rect, draw) for a view: where it goes and how to draw its content there."""
        area, region = self.area, self.region
        if view == "mini":
            w, h = self.bar_size()
            rect = bar_rect(area, region, w, h)
            return rect, (lambda r: self.minimized_bar(*r))
        width = panel_width(area, region)
        inner_w = width - 2 * self.px(PAD)
        if view == "compact":
            natural = self.compact_height(inner_w) + 2 * self.px(PAD)
            rect = placement(area, region, natural=natural, width=width)
            return rect, (lambda r: self.full(*r, compact=True))
        natural = self.full_height(inner_w) + 2 * self.px(PAD)
        rect = placement(area, region, natural=natural, width=width)
        return rect, (lambda r: self.full(*r))

    def fits_full(self):
        """Is there room for the Full view? (Otherwise Compact, rule R12.)"""
        ul, ub, ur, ut = usable(self.area, self.region)
        width = panel_width(self.area, self.region)
        inner_w = width - 2 * self.px(PAD)
        return ut - ub >= self.full_minimum(inner_w) + 2 * self.px(PAD) and ur - ul >= MIN_WIDTH * self.s * 0.8

    def draw_views(self):
        state.auto_compact = not state.minimized and not state.compact and not self.fits_full()
        view = state.view()
        anim = state.anim
        if anim:
            t = (self.now - anim["t0"]) / max(0.001, anim["dur"])
            if t >= 1.0:
                state.anim = None
                anim = None
        rect, draw_content = self.target(view)
        if not anim:
            self.frame_and(rect, draw_content, view)
            return
        # Animate only the frame: lay the content out at its final size (expanding) or keep
        # the old layout (collapsing), clip it to the moving frame and fade it.
        t = min(1.0, max(0.0, (self.now - anim["t0"]) / anim["dur"]))
        src = anim["from"]
        expanding = (rect[2] - rect[0]) * (rect[3] - rect[1]) >= (src[2] - src[0]) * (src[3] - src[1])
        e = ease_out(t) if expanding else ease_in(t)
        frame = tuple(round(a + (b - a) * e) for a, b in zip(src, rect))
        p = self.p
        x0, y0, x1, y1 = frame
        d.box(x0, y0, x1 - x0, y1 - y0, fill=p.panel, border=p.ink, width=theme.BORDER_PANEL,
              radius=theme.RADIUS, shadow=p.shadow, shadow_offset=theme.SHADOW_PANEL)
        state.rect = (x0, y0 - self.px(theme.SHADOW_PANEL), x1 + self.px(theme.SHADOW_PANEL), y1)
        state.frame = frame
        if expanding:
            alpha = max(0.0, (e - 0.4) / 0.6)
            content_rect, content = rect, draw_content
        else:
            alpha = max(0.0, 1.0 - e / 0.6)
            content_rect, content = anim["from"], anim.get("content") or draw_content
        b = self.px(theme.BORDER_PANEL)
        with d.clip(x0 + b, y0 + b, x1 - x0 - 2 * b, y1 - y0 - 2 * b):
            with d.faded(alpha):
                content(content_rect)
        self.hits = []

    def frame_and(self, rect, draw_content, view):
        state.frame = rect
        draw_content(rect)
        state.last_view = view
        state.last_rect = rect
        state.last_content = draw_content

    def full(self, x0, y0, x1, y1, compact=False):
        s_sh = self.px(theme.SHADOW_PANEL)
        state.rect = (x0, y0 - s_sh, x1 + s_sh, y1)
        p = self.p
        d.box(x0, y0, x1 - x0, y1 - y0, fill=p.panel, border=p.ink, width=theme.BORDER_PANEL,
              radius=theme.RADIUS, shadow=p.shadow, shadow_offset=theme.SHADOW_PANEL)
        self.hit(x0, y0, x1 - x0, y1 - y0, "panel")          # swallow clicks on empty paper
        pad = self.px(PAD)
        if p.band is not None:
            self.title_band(x0, y1, x1, pad)
        b = self.px(theme.BORDER_PANEL)
        with d.clip(x0 + b, y0 + b, x1 - x0 - 2 * b, y1 - y0 - 2 * b):
            inner = (x0 + pad, y0 + pad, x1 - pad, y1 - pad)
            if compact:
                self.compact_screen(*inner)
            elif state.screen == "journal":
                self.journal_screen(*inner)
            else:
                self.main_screen(*inner)
        self.grip(x0, y0, x1, y1)

    def title_band(self, x0, y1, x1, pad):
        """The 2000s theme: a window title bar behind the header (hard pixels, no gradient)."""
        p = self.p
        b = self.px(theme.BORDER_PANEL)
        h = self.px(HEADER_H) + pad + self.px(GAP) // 2
        r = max(0, self.px(theme.RADIUS) - b)
        d.sdf_box(x0 + b, y1 - h, x1 - x0 - 2 * b, h - b, r, 0, p.band, p.band)
        d.rect(x0 + b, y1 - h, x1 - x0 - 2 * b, h // 2, p.band)          # square bottom corners
        d.rect(x0 + b, y1 - h - b, x1 - x0 - 2 * b, b, p.ink)

    def ink_on_band(self):
        return self.p.band_ink if self.p.band is not None else self.p.ink

    def grip(self, x0, y0, x1, y1):
        """The corner grip, opposite the corner the panel keeps: drag it to resize."""
        p = self.p
        corner = self.prefs.panel_corner if self.prefs else "TR"
        size = self.px(14)
        inset = self.px(5)
        left = corner in ("TR", "BR")
        bottom = corner in ("TR", "TL")
        gx = x0 + inset if left else x1 - inset
        gy = y0 + inset if bottom else y1 - inset
        sx = 1 if left else -1
        sy = 1 if bottom else -1
        for i in (1, 2, 3):
            k = size * i / 3
            d.polyline([(gx, gy + sy * k), (gx + sx * k, gy)], p.with_alpha(p.muted, 0.8), self.px(1))
        hx = gx if left else gx - size - self.px(4)
        hy = gy if bottom else gy - size - self.px(4)
        self.hit(hx - inset, hy - inset, size + self.px(8), size + self.px(8), "grip")

    def bar_size(self):
        words, icon = self.status_words()
        size = self.px(16)
        logo_w = d.width_of("BLACK", self.px(15), "BlendWMe")
        icon_w = self.px(22) if icon != "none" else 0
        w = self.px(14) + logo_w + self.px(12) + icon_w + d.width_of("HAND", size, words) + self.px(44)
        return round(w), self.px(38)

    def minimized_bar(self, x, y, x1, y1):
        """The panel folded into one small bar at its corner. The work goes on; the bar shows
        what is happening and blinks when it is your turn. Click it to open; drag it to move."""
        p = self.p
        words, icon = self.status_words()
        size = self.px(16)
        w, h = x1 - x, y1 - y
        fill = p.band if p.band is not None else p.panel
        ink = self.ink_on_band()
        d.box(x, y, w, h, fill=fill, border=p.ink, width=theme.BORDER_CARD, radius=theme.RADIUS,
              shadow=p.shadow, shadow_offset=theme.SHADOW_BUTTON)
        cy = y + h / 2
        tx = x + self.px(14)
        d.text("BLACK", self.px(15), tx, cy - self.px(5), "BlendWMe", ink)
        tx += d.width_of("BLACK", self.px(15), "BlendWMe") + self.px(12)
        if icon == "ticks":
            phase = ((self.now % 1.2) / 1.2) if self.motion else None
            d.ticks(tx, cy - self.px(6), self.px(11), ink, phase)
        elif icon == "dot":
            alpha = 1.0 if self.blink(0.5) else 0.25
            d.disc(tx + self.px(6), cy, self.px(5.5), p.with_alpha(p.hot, alpha))
        tx += self.px(22) if icon != "none" else 0
        d.text("HAND", size, tx, cy - size * 0.32, words, ink)
        # The "open" mark: a small square with an arrow out of it.
        bx = x + w - self.px(26)
        d.box(bx, cy - self.px(7), self.px(14), self.px(14), border=ink, width=1, radius=2)
        d.polyline([(bx + self.px(5), cy + self.px(2)), (bx + self.px(14), cy + self.px(11))], ink, self.px(1.5))
        d.polyline([(bx + self.px(8), cy + self.px(11)), (bx + self.px(14), cy + self.px(11)),
                    (bx + self.px(14), cy + self.px(5))], ink, self.px(1.5))
        state.rect = (x, y - self.px(theme.SHADOW_BUTTON), x1 + self.px(theme.SHADOW_BUTTON), y1)
        state.header = (x, y, x1, y1)
        self.hit(x, y, w, h, "mini")

    # -- header -----------------------------------------------------------
    def logo(self, x, top, size=30):
        """Blend + tilted WMe tag + square full stop (the full stop blinks on your turn)."""
        import blf
        import math
        p = self.p
        ink = self.ink_on_band()
        fs = self.px(size)
        base = top - round(fs * 0.92)
        d.text("BLACK", fs, x, base, "Blend", ink)
        bx = x + d.width_of("BLACK", fs, "Blend") + self.px(4)
        tag_fs = fs * 0.92
        tw = d.width_of("BLACK", tag_fs, "WMe")
        w, h = tw + self.px(14), fs * 1.1
        cy = base + fs * 0.35
        angle = -0.044          # -2.5 degrees, like a sticker

        def rotated(pts, cx, cy_):
            ca, sa = math.cos(angle), math.sin(angle)
            return [(cx + (px_ - cx) * ca - (py - cy_) * sa, cy_ + (px_ - cx) * sa + (py - cy_) * ca) for px_, py in pts]
        cx = bx + w / 2
        box_pts = [(bx, cy - h / 2), (bx + w, cy - h / 2), (bx + w, cy + h / 2), (bx, cy + h / 2)]
        off = self.px(4)
        d.fill_poly(rotated([(a + off, b - off) for a, b in box_pts], cx, cy), p.shadow)
        d.fill_poly(rotated(box_pts, cx, cy), p.tag)
        fid = d.font("BLACK")
        blf.enable(fid, blf.ROTATION)
        blf.rotation(fid, angle)
        tx, ty = bx + self.px(7), cy - tag_fs * 0.36
        rx, ry = rotated([(tx, ty)], cx, cy)[0]
        d.text("BLACK", tag_fs, rx, ry, "WMe", p.tag_ink)
        blf.rotation(fid, 0)
        blf.disable(fid, blf.ROTATION)
        dot = round(fs * 0.24)
        dot_alpha = 1.0 if (not your_turn() or self.blink(0.5)) else 0.15
        d.rect(bx + w + self.px(4), base, dot, dot, p.with_alpha(p.hot, dot_alpha))
        self.logo_right = bx + w + self.px(4) + dot
        return round(fs * 1.15)

    def status_words(self):
        """(words, icon) for the status pill and the minimized bar; ("Ready", "none") when idle."""
        status = session.status
        card = session.card or {}
        running = self.relay.is_running()
        if card.get("kind") == "limit":
            return f"{label(card.get('brain'))} paused", "dot"
        if status in (WAIT_USER, WAIT_NEXT, ASKING):
            return "Your turn", "dot"
        if status == PAUSED:
            return "You have control", "none"
        if running and status == WORKING:
            return "Building", "ticks"
        if running:
            return "Thinking", "ticks"
        if status == DONE:
            return "Done", "none"
        if status == ERROR:
            return "Stopped", "none"
        return "Ready", "none"

    def logo_width(self, size):
        fs = self.px(size)
        tag = d.width_of("BLACK", fs * 0.92, "WMe") + self.px(14)
        return d.width_of("BLACK", fs, "Blend") + self.px(4) + tag + self.px(4) + round(fs * 0.24)

    def pill_width(self, words, icon):
        if words == "Ready":
            return 0
        return d.width_of("HAND", self.px(17), words) + (self.px(26) if icon != "none" else 0) + self.px(24)

    def status_pill(self, x1, top, room=None):
        p = self.p
        words, icon = self.status_words()
        if words == "Ready":
            return
        if room is not None and self.pill_width(words, icon) > room:
            # Not enough room beside the logo: the icon alone, or nothing when there is no icon.
            if icon == "none" or room < self.px(40):
                return
            words = ""
        size = self.px(17)
        tw = d.width_of("HAND", size, words) if words else 0
        icon_w = self.px(26) if icon != "none" else 0
        h = self.px(32)
        w = tw + icon_w + self.px(24) - (self.px(10) if not words else 0)
        x = x1 - w
        y = top - h
        d.box(x, y, w, h, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=h / 2 / self.s)
        if icon == "ticks":
            phase = ((self.now % 1.2) / 1.2) if self.motion else None
            d.ticks(x + self.px(14), y + self.px(10), self.px(12), p.ink, phase)
        elif icon == "dot":
            alpha = 1.0 if self.blink(0.5) else 0.25
            d.disc(x + self.px(20), y + h / 2, self.px(6), p.with_alpha(p.hot, alpha))
        if words:
            d.text("HAND", size, x + self.px(12) + icon_w, y + (h - size * 0.6) / 2, words, p.ink)

    def header(self, x0, top, x1, back=False):
        """The logo row and the window buttons. Drag it to move the panel (both axes);
        double-click it to switch between Full and Compact."""
        hh = self.px(HEADER_H)
        pad = self.px(PAD)
        state.header = (x0 - pad, top - hh, x1 + pad, top + pad)
        self.hit(x0 - pad, top - hh, x1 - x0 + 2 * pad, hh + pad, "drag")
        right = self.window_buttons(x1, top) - self.px(8)
        words, icon = self.status_words()
        pill = self.pill_width(words, icon) if not back else d.width_of("HAND", self.px(17), "Back") + self.px(44)
        # The biggest logo that still leaves room for the whole status beside it.
        size = next((sz for sz in (30, 27, 24) if self.logo_width(sz) + pill + self.px(10) <= right - x0), 22)
        self.logo(x0, top, size=size)
        if back:
            self.back_pill(right, top)
        else:
            self.status_pill(right, top, room=right - self.logo_right - self.px(10))
        return hh

    def window_buttons(self, x1, top):
        """Maximize / restore and minimize. Returns the left x of the buttons."""
        p = self.p
        size = self.px(26)
        gap = self.px(6)
        y = top - self.px(29)
        # Minimize: a line at the bottom of a small square.
        x = x1 - size
        self.small_button(x, y, size, "minimize")
        d.rect(x + self.px(7), y + self.px(8), size - self.px(14), self.px(2), p.ink)
        # Maximize (a square) or restore (two squares); in Compact it opens Full.
        x -= size + gap
        self.small_button(x, y, size, "maximize")
        maxed = bool(self.prefs and self.prefs.panel_max) and state.view() == "full"
        if maxed:
            d.box(x + self.px(10), y + self.px(10), self.px(9), self.px(9), border=p.ink, width=1, radius=1)
            d.box(x + self.px(7), y + self.px(7), self.px(9), self.px(9), fill=p.card, border=p.ink, width=1, radius=1)
        else:
            d.box(x + self.px(7), y + self.px(7), size - self.px(14), size - self.px(14), border=p.ink, width=1, radius=1)
            d.rect(x + self.px(7), y + size - self.px(9), size - self.px(14), self.px(2), p.ink)
        return x

    def small_button(self, x, y, size, action):
        p = self.p
        d.box(x, y, size, size, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=6)
        self.hit(x, y, size, size, action)

    def back_pill(self, x1, top):
        p = self.p
        size = self.px(17)
        h = self.px(32)
        w = d.width_of("HAND", size, "Back") + self.px(44)
        x, y = x1 - w, top - h
        d.box(x, y, w, h, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=h / 2 / self.s)
        d.polyline([(x + self.px(22), y + h / 2 + self.px(6)), (x + self.px(16), y + h / 2),
                    (x + self.px(22), y + h / 2 - self.px(6))], p.ink, self.px(2))
        d.text("HAND", size, x + self.px(32), y + (h - size * 0.6) / 2, "Back", p.ink)
        self.hit(x, y, w, h, "screen:main")

    # -- heights (for content-following height and the Compact switch) ------
    def first_view(self):
        """Nothing to show yet (or after /new and Ctrl+L): the input and three examples."""
        return (not self.visible_log() and not self.relay.is_running() and not session.plan
                and not (session.card and not session.card.get("hidden")))

    def bottom_stack(self, w):
        """The fixed slots at the bottom: footer, chips, input, buttons, cards (R12)."""
        gap = self.px(GAP)
        limit = (session.card or {}).get("kind") == "limit"
        card_h = self.current_cards(0, 0, w, measure=True)
        return (self.px(24) + gap + (0 if limit else self.px(42) + gap) + self.input_height(w) + gap
                + self.action_height() + (card_h + gap if card_h else 0))

    def full_minimum(self, w):
        """The smallest height that shows Full without squeezing anything."""
        gap = self.px(GAP)
        if self.first_view():
            return self.px(HEADER_H) + gap + self.px(46) + gap + self.input_height(w) + gap + 3 * self.px(50)
        middle = self.px(110)
        if session.mode == "AUTO" and self.plan_items():
            middle += self.px(90)
        return self.px(HEADER_H) + gap + self.px(46) + gap + middle + gap + self.bottom_stack(w)

    def full_height(self, w):
        """The height the Full view wants: everything at its natural size, the conversation
        included, up to whatever the safe area allows (the log scrolls beyond that)."""
        gap = self.px(GAP)
        if state.screen == "journal":
            return 10 ** 6                     # the journal uses the whole height
        if self.first_view():
            return (self.px(HEADER_H) + gap + self.px(46) + gap + self.input_height(w) + gap
                    + 3 * (self.px(40) + self.px(10)) + self.px(4))
        top = self.px(HEADER_H) + gap + self.px(60) + gap + self.px(46) + gap
        middle = self.conversation_height(w) + 2 * self.px(16)
        plan = self.plan_items()
        if session.mode == "AUTO" and plan:
            middle += self.plan_card(0, 0, w, plan, measure=True) + gap
        return top + max(self.px(110), middle) + gap + self.bottom_stack(w)

    def compact_height(self, w):
        gap = self.px(GAP)
        h = self.px(HEADER_H) + gap
        if self.strip_text():
            h += self.px(44) + gap
        h += self.action_height() + self.limit_height()
        h += self.input_height(w, max_lines=3)
        return h

    def limit_height(self):
        """Compact keeps the limit card's two buttons (Hand over / Wait)."""
        if (session.card or {}).get("kind") == "limit" and not session.card.get("hidden"):
            return self.px(48) + self.px(GAP)
        return 0

    def limit_buttons(self, x, y, w):
        card = session.card
        target = card.get("target") or self.relay.other(card.get("brain") or self.relay.active)
        h = self.px(48)
        sh = self.px(theme.SHADOW_BUTTON)
        bw = round((w - self.px(14) - sh) * 0.64)
        self.button(x, y + sh, bw, h - sh, f"Hand over to {label(target)}", "handover:" + target, hot=True,
                    size=15)
        self.button(x + bw + self.px(14), y + sh, w - bw - self.px(14) - sh, h - sh, "Wait", "close_card",
                    size=15)

    # -- main screen ------------------------------------------------------
    def main_screen(self, x0, y0, x1, y1):
        w = x1 - x0
        gap = self.px(GAP)
        top = y1
        top -= self.header(x0, top, x1) + gap
        first_view = self.first_view()
        limit = (session.card or {}).get("kind") == "limit"
        stack = self.bottom_stack(w)
        brain_h = self.px(60) + gap
        tabs_h = self.px(46) + gap
        needed = self.px(110)
        if session.mode == "AUTO" and session.plan:
            needed += self.px(90)
        if not first_view and top - y0 - stack - tabs_h - brain_h >= needed:
            top -= self.brain_card(x0, top, w) + gap
        top -= self.mode_tabs(x0, top, w) + gap

        # Bottom up: footer, chips, input, buttons, cards.
        bottom = y0
        bottom += self.footer(x0, bottom, w) + gap
        if first_view:
            # One input, three example chips, the mode tabs. Nothing else.
            input_h = self.input_height(w)
            self.text_input(x0, top - input_h, w, input_h)
            top -= input_h + gap
            for name in EXAMPLES:
                h = self.px(40)
                if top - h < bottom:
                    break
                self.chip(x0, top - h, w, h, name, "example:" + name)
                top -= h + self.px(10)
            return
        if not limit:
            bottom += self.chips_row(x0, bottom, w) + gap
        input_h = self.input_height(w)
        self.text_input(x0, bottom, w, input_h)
        bottom += input_h + gap
        bottom += self.action_buttons(x0, bottom, w)
        card_h = self.current_cards(x0, 0, w, measure=True)
        if card_h:
            self.current_cards(x0, bottom, w)
            bottom += card_h + gap
        # The middle: the plan (Auto) and the conversation.
        middle_h = top - bottom
        if middle_h < self.px(60):
            return
        plan = self.plan_items()
        if session.mode == "AUTO" and plan:
            # The plan comes first in Auto; the conversation keeps at least a few lines.
            plan_h = min(self.plan_card(x0, top, w, plan, measure=True), max(middle_h * 0.62, middle_h - self.px(110)))
            self.plan_card(x0, top, w, plan, height=plan_h)
            top -= plan_h + gap
            middle_h = top - bottom
            if middle_h < self.px(60):
                return
        self.conversation(x0, bottom, w, middle_h)

    def compact_screen(self, x0, y0, x1, y1):
        """Compact: the header, one line about what is happening, the buttons and the input."""
        w = x1 - x0
        gap = self.px(GAP)
        top = y1 - self.header(x0, y1, x1) - gap
        strip = self.strip_text()
        if strip:
            h = self.px(44)
            self.cue_strip(x0, top - h, w, h, *strip)
            top -= h + gap
        actions = self.action_height()
        if actions:
            self.action_buttons(x0, top - actions, w)
            top -= actions
        limit = self.limit_height()
        if limit:
            self.limit_buttons(x0, top - limit + self.px(GAP), w)
            top -= limit
        input_h = self.input_height(w, max_lines=3)
        self.text_input(x0, max(y0, top - input_h), w, input_h, max_lines=3)

    def strip_text(self):
        """(label, words, yours) for the Compact strip, or None."""
        card = session.card or {}
        if card and not card.get("hidden") and card.get("kind") not in ("work",):
            kind = card.get("kind")
            words = card.get("text") or card.get("title") or ""
            if kind == "limit":
                words = card.get("title") or "Limit reached"
            return self.card_label(card), words, kind in ("watch", "teach", "ask", "pause", "limit")
        if self.relay.is_running() and session.status in (THINKING, WORKING):
            brain = label(self.relay.running or self.relay.active)
            seconds = int(self.now - (state.turn_started or self.now))
            verb = "building" if session.status == WORKING else "thinking"
            title = card.get("title") or ""
            return f"{brain} is {verb} · {seconds} s", title, False
        return None

    def cue_strip(self, x, y, w, h, head, words, yours):
        p = self.p
        self.card_box(x, y, w, h, border=p.hot if yours else None)
        block = self.px(16)
        bx = x + self.px(12)
        if yours:
            on = self.blink(1.0)
            d.box(bx, y + (h - block) / 2, block, block, fill=p.hot if on else p.with_alpha(p.hot, 0.18), radius=2)
        else:
            phase = ((self.now % 1.2) / 1.2) if self.motion else None
            d.ticks(bx, y + (h - block) / 2, block, p.ink, phase)
        tx = bx + block + self.px(10)
        room = x + w - self.px(12) - tx
        with d.clip(x, y, w, h):
            self.label_caps(tx, y + h - self.px(17), head, max_width=room, size=10)
            d.text("HAND", self.px(17), tx, y + self.px(8), d.fit("HAND", self.px(17), words, room), p.ink)
        self.hit(x, y, w, h, "expand")

    def brain_card(self, x, top, w):
        p = self.p
        props = self.props
        brain = (self.relay.running or self.relay.active or (props.brain if props else "claude"))
        card = session.card or {}
        limited = card.get("kind") == "limit" and card.get("brain") == brain
        name = label(brain)
        if limited:
            title = f"{name} · limit reached"
            sub = self.relay.limit.get("resets", "") if self.relay.limit else ""
            sub = f"Usage {sub}" if sub else "Usage resets later"
        elif session.status == WAIT_USER:
            title, sub = f"{name} · your coach", "Waiting for you. No rush."
        else:
            title = name
            sub = "Claude Code · your plan" if brain == "claude" else "Codex CLI · your plan"
        h = self.px(60)
        y = top - h
        self.card_box(x, y, w, h)
        self.mark(brain, x + self.px(24), y + h / 2, self.px(9), p.muted if limited else p.ink)
        bw = 0 if limited else self.px(112)
        room = w - self.px(46) - (bw + self.px(22) if bw else self.px(16))
        with d.clip(x, y, w, h):
            d.text("BLACK", self.px(15), x + self.px(46), y + h - self.px(26),
                   d.fit("BLACK", self.px(15), title, room), p.ink)
            d.text("HAND", self.px(16), x + self.px(46), y + self.px(13), d.fit("HAND", self.px(16), sub, room), p.muted)
        if not limited:
            bh = self.px(34)
            bx, by = x + w - bw - self.px(12), y + (h - bh) / 2
            d.box(bx, by, bw, bh, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=8)
            ax, ay = bx + self.px(14), by + bh / 2
            d.polyline([(ax, ay + self.px(4)), (ax + self.px(14), ay + self.px(4)), (ax + self.px(10), ay + self.px(8))],
                       p.ink, self.px(2))
            d.polyline([(ax + self.px(14), ay - self.px(4)), (ax, ay - self.px(4)), (ax + self.px(4), ay - self.px(8))],
                       p.ink, self.px(2))
            room_b = bw - self.px(40)
            d.text("BLACK", self.px(12), bx + self.px(36), by + (bh - self.px(9)) / 2,
                   d.fit("BLACK", self.px(12), "Hand over", room_b), p.ink)
            self.hit(bx, by, bw, bh, "handover")
        return h

    def mode_tabs(self, x, top, w):
        p = self.p
        h = self.px(46)
        y = top - h
        d.box(x, y, w, h, fill=p.panel, border=p.ink, width=theme.BORDER_CARD, radius=theme.RADIUS)
        current = self.props.mode if self.props else session.mode
        b = self.px(theme.BORDER_CARD)
        seg = round((w - 2 * b) / 3)
        for i, (key, name) in enumerate(MODES):
            sx = x + b + i * seg
            sw = seg if i < 2 else (x + w - b) - sx
            if key == current:
                r = max(0, self.px(theme.RADIUS) - b)
                d.sdf_box(sx, y + b, sw, h - 2 * b, r, 0, p.accent, p.accent)
                self.centered("BLACK", self.px(15), sx, y, sw, h, name, p.on_accent)
            else:
                self.centered("HAND", self.px(20), sx, y, sw, h, name, p.ink, cap=0.6)
            self.hit(sx, y, sw, h, "mode:" + key)
        return h

    def footer(self, x, y, w):
        p = self.p
        from . import socket_bridge
        try:
            connected = socket_bridge.get().is_running()
        except Exception:
            connected = False
        size = self.px(17)
        h = self.px(24)
        cy = y + h / 2
        right = x + w
        links = (("Settings", "settings"), ("Journal", "screen:journal"))
        for name, action in links:
            tw = d.width_of("HAND", size, name)
            tx = right - tw
            d.text("HAND", size, tx, cy - size * 0.32, name, p.ink)
            d.rect(tx, round(cy - size * 0.42), tw, self.px(1), p.ink)
            self.hit(tx - self.px(4), y, tw + self.px(8), h, action)
            right = tx - self.px(14)
        if connected:
            d.disc(x + self.px(8), cy, self.px(6), p.ink)
            words = "Connected to Blender"
        else:
            d.circle(x + self.px(8), cy, self.px(6), p.hot, self.px(2))
            words = "Not connected. Open Settings"
        d.text("HAND", size, x + self.px(22), cy - size * 0.32, d.fit("HAND", size, words, right - x - self.px(26)), p.ink)
        return h

    # -- input and chips ----------------------------------------------------
    def placeholder(self):
        brain = label(self.relay.active)
        if session.status == ASKING:
            return "Type your answer..."
        if self.first_view():
            return "What shall we make?"
        mode = self.props.mode if self.props else session.mode
        return {"TEACH": "Ask a question...", "AUTO": "Describe what to build..."}.get(
            mode, f"Tell {brain} what to change...")

    def input_lines(self, w):
        """[(start, end)] of each wrapped line of the input, as indices into state.text."""
        return input_layout(state.text, self.px(20), w - self.px(28))

    def input_height(self, w, max_lines=6):
        lines = len(self.input_lines(w)) if state.text else 1
        return self.px(26) + d.line_height("HAND", self.px(20)) * min(max(1, lines), max_lines)

    def text_input(self, x, y, w, h, max_lines=6):
        p = self.p
        size = self.px(20)
        focused = state.focused
        d.box(x, y, w, h, fill=p.card, border=p.accent if (focused and p.band is not None) else p.ink,
              width=theme.BORDER_CARD + (1 if focused else 0), radius=theme.RADIUS)
        tx = x + self.px(14)
        top = y + h - self.px(13)
        state.input_box = (x, y, x + w, y + h, tx, top)
        self.hit(x, y, w, h, "focus_input")
        lh = d.line_height("HAND", size)
        if not state.text:
            with d.clip(x, y, w, h):
                d.text("HAND", size, tx, top - size * 0.95, d.fit("HAND", size, self.placeholder(), w - self.px(28)),
                       p.muted)
            if focused and self.blink(0.5):
                d.rect(tx, top - round(size * 1.05), self.px(2), round(size * 1.05), p.ink)
            return
        lines = self.input_lines(w)
        first = max(0, len(lines) - max_lines)
        caret_line = next((i for i, (a, b) in enumerate(lines) if a <= state.cursor <= b), len(lines) - 1)
        if caret_line < first:
            first = caret_line
        visible = lines[first:first + max_lines]
        sel = selection()
        with d.clip(x, y, w, h):
            for i, (a, b) in enumerate(visible):
                ly = top - size * 0.95 - i * lh
                line = state.text[a:b]
                if sel and sel[0] < b and sel[1] > a:
                    s0, s1 = max(a, sel[0]), min(b, sel[1])
                    sx0 = tx + d.width_of("HAND", size, state.text[a:s0])
                    sx1 = tx + d.width_of("HAND", size, state.text[a:s1])
                    d.rect(sx0, top - (i + 1) * lh, max(self.px(2), sx1 - sx0), lh, p.with_alpha(p.accent, 0.25))
                d.text("HAND", size, tx, ly, line, p.ink)
            if focused and self.blink(0.5) and first <= caret_line < first + max_lines:
                a, _ = lines[caret_line]
                row = caret_line - first
                cx = tx + d.width_of("HAND", size, state.text[a:state.cursor])
                d.rect(cx, top - round(size * 1.05) - row * lh, self.px(2), round(size * 1.05), p.ink)
        state.input_layout = (tx, top, lh, size, first, lines)

    def chips_row(self, x, y, w):
        mode = self.props.mode if self.props else session.mode
        h = self.px(42)
        if mode == "AUTO":
            return self.pace_slider(x, y, w, h)
        chips = CHIPS.get(mode, CHIPS["WATCH"])
        gap = self.px(10)
        cw = round((w - gap * 2) / 3)
        for i, (name, action) in enumerate(chips):
            cx = x + i * (cw + gap)
            self.chip(cx, y, cw if i < 2 else x + w - cx, h, name, "chip:" + action)
        return h

    def pace_slider(self, x, y, w, h):
        p = self.p
        prefs = self.prefs
        pace = prefs.pace if prefs else 1.0
        size = self.px(19)
        d.text("HAND", size, x, y + (h - size * 0.6) / 2, "Pace", p.ink)
        value = f"{pace:.0f} s a step" if pace >= 1 or pace == 0 else f"{pace:.1f} s a step"
        vw = d.width_of("HAND", size, value)
        d.text("HAND", size, x + w - vw, y + (h - size * 0.6) / 2, value, p.ink)
        sx0 = round(x + d.width_of("HAND", size, "Pace") + self.px(16))
        sx1 = round(x + w - vw - self.px(18))
        cy = round(y + h / 2)
        track_h = self.px(10)
        d.box(sx0, cy - track_h // 2, sx1 - sx0, track_h, fill=p.card, border=p.shadow, width=1, radius=5)
        t = max(0.0, min(1.0, pace / 10.0))
        kx = sx0 + (sx1 - sx0) * t
        d.box(sx0, cy - track_h // 2, max(track_h, kx - sx0), track_h, fill=p.accent, radius=5)
        d.disc(kx, cy, self.px(11), p.accent)
        self.hit(sx0 - self.px(8), y, sx1 - sx0 + self.px(16), h, f"slider:{sx0}:{sx1}")
        return h

    # -- action buttons -------------------------------------------------------
    def action_height(self):
        """The height action_buttons() will use (0 when no buttons show)."""
        status = session.status
        if (session.card or {}).get("kind") == "limit":
            return 0
        if status in (WAIT_USER, WAIT_NEXT, PAUSED) or (self.relay.is_running() and status in (THINKING, WORKING)):
            return self.px(48) + self.px(GAP)
        return 0

    def action_buttons(self, x, y, w):
        status = session.status
        card = session.card or {}
        running = self.relay.is_running()
        if card.get("kind") == "limit":
            return 0          # the limit card has its own buttons
        buttons = None
        if status == WAIT_USER:
            buttons = (("Check my work", "teach:done", True), ("Do it for me", "teach:show_me", False))
        elif status == WAIT_NEXT:
            buttons = (("Next", "op:blendwme.next", True), ("Take control", "take_control", False))
        elif status == PAUSED:
            buttons = (("Resume", "op:blendwme.resume", True), ("Stop", "op:blendwme.stop", False))
        elif running and status in (THINKING, WORKING):
            buttons = (("Pause", "op:blendwme.pause", True), ("Take control", "take_control", False))
        if not buttons:
            return 0
        h = self.px(48)
        gap = self.px(14)
        sh = self.px(theme.SHADOW_BUTTON)
        bw = round((w - gap - sh) / 2)
        for i, (name, action, primary) in enumerate(buttons):
            self.button(x + i * (bw + gap), y + sh, bw, h - sh, name, action, primary=primary, size=15)
        return h + self.px(GAP)

    # -- cards ----------------------------------------------------------------
    def current_cards(self, x, y, w, measure=False):
        """The card for what is happening now (and the pointer row in Teach). Returns the height."""
        card = session.card
        if not card or card.get("hidden"):
            return 0
        if card.get("kind") == "work":
            return 0          # the thinking box in the conversation already shows the work
        total = 0
        kind = card.get("kind")
        if kind == "limit":
            return self.limit_cards(x, y, w, measure)
        if kind == "teach" and session.pointers:
            row_h = self.px(58)
            if not measure:
                self.pointer_row(x, y, w, row_h)
            total += row_h + self.px(GAP)
        h = self.cue_card(x, y + total, w, card, measure=True)
        if not measure:
            self.cue_card(x, y + total, w, card, height=h)
        return total + h

    def card_label(self, card):
        kind = card.get("kind")
        progress = card.get("progress") or ""
        step = card.get("step")
        step_text = progress.upper() if progress else (f"STEP {step}" if step else "")
        names = {"work": "WORKING", "watch": "WATCH", "teach": "YOUR TURN", "ask": f"{label(self.relay.active)} ASKS",
                 "pause": "YOU HAVE CONTROL", "done": "DONE", "stuck": "STUCK", "limit": "LIMIT REACHED"}
        text = names.get(kind, str(kind or "").upper())
        if step_text and kind in ("work", "watch", "teach"):
            text += " · " + step_text
        return text

    def cue_card(self, x, y, w, card, height=None, measure=False):
        p = self.p
        kind = card.get("kind")
        yours = kind in ("watch", "teach", "ask", "pause")
        block = self.px(22)
        tx = x + self.px(18) + (block + self.px(14))
        tw = x + w - self.px(34) - tx
        body = card.get("text") or ""
        title = card.get("title") or ""
        main = body if kind in ("teach", "ask") else (f"{title}. {body}" if body and title else (title or body))
        if kind == "work":
            main = (title or "Working") + ". Pause any time to take over."
        why = card.get("why") or ""
        label_size = self.px(11)
        text_size = self.px(20)
        # Very long instructions show their first lines; the conversation has the rest.
        max_lines = 6
        h = self.px(18) + label_size + self.px(10) + d.paragraph_height("HAND", text_size, main, tw, max_lines)
        if why:
            h += self.px(6) + d.paragraph_height("HAND", self.px(16), "Why: " + why, tw, 3)
        h += self.px(16)
        if measure:
            return h
        h = height or h
        self.card_box(x, y, w, h)
        top = y + h - self.px(18)
        # The block: hot and blinking when it is your turn; a quiet ink square while the AI works.
        if yours:
            on = self.blink(1.0)
            d.box(x + self.px(18), top - block, block, block, fill=p.hot if on else p.with_alpha(p.hot, 0.18),
                  border=p.ink if on else None, width=1, radius=2)
        else:
            d.box(x + self.px(18), top - block, block, block, fill=p.with_alpha(p.ink, 0.12), radius=2)
        with d.clip(x, y, w, h):
            self.label_caps(tx, top - label_size * 0.85, self.card_label(card), max_width=tw)
            cursor = top - label_size - self.px(10)
            cursor -= d.paragraph("HAND", text_size, tx, cursor, main, p.ink, tw, max_lines=max_lines)
            if why:
                cursor -= self.px(6)
                d.paragraph("HAND", self.px(16), tx, cursor, "Why: " + why, p.muted, tw, max_lines=3)
        self.close_x(x + w, y + h)
        return h

    def pointer_row(self, x, y, w, h):
        p = self.p
        self.card_box(x, y, w, h)
        cx, cy = x + self.px(32), y + h / 2
        d.circle(cx, cy, self.px(16), p.hot, self.px(2))
        d.disc(cx, cy, self.px(9), p.hot)
        d.circle(cx, cy, self.px(9), p.ink, self.px(1))
        targets = []
        for ptr in session.pointers:
            targets.append(ptr.get("label") or ptr.get("tab") or ptr.get("object") or ptr.get("region")
                           or ptr.get("editor") or "")
        words = "Pointer on " + " then ".join(t.replace("_", " ").title() for t in targets if t)
        tx = x + self.px(60)
        bw = self.px(78)
        with d.clip(x, y, w, h):
            d.paragraph("HAND", self.px(18), tx, y + h - self.px(10), words, p.ink, w - (tx - x) - bw - self.px(24),
                        max_lines=2)
        bx, by = x + w - bw - self.px(14), y + (h - self.px(34)) / 2
        d.box(bx, by, bw, self.px(34), fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=8)
        self.centered("BLACK", self.px(12), bx, by, bw, self.px(34), "Move", p.ink)
        self.hit(bx, by, bw, self.px(34), "pointers_again")

    def limit_cards(self, x, y, w, measure=False):
        """Screen 22: the urgent card, the baton summary and Hand over / Wait."""
        p = self.p
        card = session.card
        target = card.get("target") or self.relay.other(card.get("brain") or self.relay.active)
        gap = self.px(GAP)
        # Bottom up: checkbox, buttons, baton, urgent card.
        check_h = self.px(30)
        btn_h = self.px(48)
        rows = self.baton_rows()
        tw = w - self.px(130)
        baton_h = self.px(52) + sum(max(d.paragraph_height("HAND", self.px(18), v, tw, 3), self.px(24)) + self.px(4)
                                    for _, v in rows)
        text = card.get("text") or ""
        urgent_h = self.px(52) + d.paragraph_height("HAND", self.px(21), text, w - self.px(90), 4) + self.px(8)
        total = check_h + gap + btn_h + gap + baton_h + gap + urgent_h
        if measure:
            return total
        cursor = y
        # "Next time, hand over by itself in Auto"
        auto = bool(self.prefs and self.prefs.auto_relay)
        self.checkbox(x + self.px(6), cursor + self.px(3), self.px(24), auto)
        d.text("HAND", self.px(19), x + self.px(44), cursor + self.px(8),
               d.fit("HAND", self.px(19), "Next time, hand over by itself in Auto", w - self.px(48)), p.ink)
        self.hit(x, cursor, w, check_h, "toggle:auto_relay")
        cursor += check_h + gap
        sh = self.px(theme.SHADOW_BUTTON)
        bw = round((w - self.px(14) - sh) * 0.64)
        self.button(x, cursor + sh, bw, btn_h - sh, f"Hand over to {label(target)}", "handover:" + target, hot=True,
                    size=15)
        self.button(x + bw + self.px(14), cursor + sh, w - bw - self.px(14) - sh, btn_h - sh, "Wait", "close_card",
                    size=15)
        cursor += btn_h + gap
        # The baton
        self.card_box(x, cursor, w, baton_h)
        top = cursor + baton_h - self.px(18)
        name = os.path.basename(bpy.data.filepath) if bpy.data.filepath else "unsaved file"
        with d.clip(x, cursor, w, baton_h):
            self.label_caps(x + self.px(18), top - self.px(10), "The baton · " + name, max_width=w - self.px(110))
            mx = x + w - self.px(66)
            self.mark(card.get("brain") or "claude", mx, top - self.px(6), self.px(7))
            d.polyline([(mx + self.px(14), top - self.px(6)), (mx + self.px(34), top - self.px(6))], p.ink, self.px(2))
            d.polyline([(mx + self.px(28), top), (mx + self.px(34), top - self.px(6)),
                        (mx + self.px(28), top - self.px(12))], p.ink, self.px(2))
            self.mark(target, mx + self.px(48), top - self.px(6), self.px(7))
            row_top = top - self.px(30)
            for key, value in rows:
                self.label_caps(x + self.px(18), row_top - self.px(15), key, size=10, max_width=self.px(86))
                used = d.paragraph("HAND", self.px(18), x + self.px(112), row_top, value, p.ink, tw, max_lines=3)
                row_top -= max(used, self.px(24)) + self.px(4)
        cursor += baton_h + gap
        # The urgent card (hot border)
        self.card_box(x, cursor, w, urgent_h, border=p.hot, width=3.0)
        top = cursor + urgent_h - self.px(18)
        block = self.px(22)
        on = self.blink(1.0)
        d.box(x + self.px(18), top - block, block, block, fill=p.hot if on else p.with_alpha(p.hot, 0.18), radius=2)
        with d.clip(x, cursor, w, urgent_h):
            self.label_caps(x + self.px(58), top - self.px(10), card.get("title") or "Limit reached",
                            max_width=w - self.px(80))
            d.paragraph("HAND", self.px(21), x + self.px(58), top - self.px(22), text, p.ink, w - self.px(90),
                        max_lines=4)
        return total

    def baton_rows(self):
        from .journal import journal
        data = journal.data
        rows = []
        if data.get("brief"):
            rows.append(("Brief", data["brief"]))
        plan = data.get("plan") or []
        if plan:
            done = sum(1 for item in plan if item.get("done"))
            rows.append(("Plan", f"{done} of {len(plan)} done."))
        you = [e for e in data.get("events", []) if e.get("by") == "user" and e.get("kind") in ("user_edit",
                                                                                                "user_message")]
        if you:
            last = you[-1]
            rows.append(("You", last.get("text") or "; ".join(last.get("lines", [])[:2])))
        stuck = [s for s in data.get("struggles", []) if s.get("open")]
        if stuck:
            rows.append(("Stuck", stuck[-1]["text"]))
        if data.get("handoffs"):
            nxt = data["handoffs"][-1].get("next_steps") or []
            if nxt:
                rows.append(("Next", "; ".join(nxt[:2])))
        return rows

    def checkbox(self, x, y, size, on):
        p = self.p
        grown = round(size * d.ICON_SCALE)            # the Icon size setting, around the box's centre
        bx, by = x - (grown - size) / 2, y - (grown - size) / 2
        d.box(bx, by, grown, grown, fill=p.accent if on else p.card, border=p.ink, width=2, radius=3)
        if on:
            d.check_mark(x + size * 0.18, y + size * 0.2, size * 0.64, p.on_accent, self.px(2.5))

    # -- plan (Auto) ---------------------------------------------------------
    def plan_items(self):
        from .journal import journal
        items = [dict(item) for item in session.plan]
        jplan = journal.data.get("plan") or []
        if len(jplan) == len(items):
            for item, j in zip(items, jplan):
                item["by"] = j.get("by", "")
        return items

    def plan_card(self, x, top, w, items, height=None, measure=False):
        p = self.p
        row = self.px(30)
        header = self.px(84)
        h = header + row * len(items) + self.px(14)
        if measure:
            return h
        h = round(height or h)
        y = top - h
        self.card_box(x, y, w, h)
        done = sum(1 for i in items if i["done"])
        from .journal import journal
        brief = (journal.data.get("brief") or "").split(".")[0].split(",")[0]
        words = brief.split()[:3]
        title = "Plan" + (" · " + " ".join(words) if words else "")
        ty = y + h - self.px(30)
        count = f"{done} OF {len(items)}"
        # A fixed-width slot for the counter, so "9 OF 9" -> "10 OF 10" does not move the row.
        slot = d.tracked_width("BLACK", self.px(11), "00 OF 00")
        with d.clip(x, y, w, h):
            self.label_caps(x + self.px(18), ty, title, max_width=w - self.px(48) - slot)
            cw = d.tracked_width("BLACK", self.px(11), count)
            self.label_caps(x + w - self.px(18) - cw, ty, count, color=p.muted)
        # The segmented progress bar
        n = max(1, len(items))
        gap = self.px(6) if n < 14 else self.px(2)
        seg = max(self.px(2), (w - self.px(36) - gap * (n - 1)) // n)
        by = ty - self.px(36)
        current = next((i for i, item in enumerate(items) if not item["done"]), None)
        for i, item in enumerate(items):
            sx = x + self.px(18) + i * (seg + gap)
            if item["done"]:
                d.box(sx, by, seg, self.px(22), fill=p.accent, radius=4)
            elif i == current and self.relay.is_running():
                d.hatch(sx, by, seg, self.px(22), p.ink, self.px(5), self.px(1.5))
                d.box(sx, by, seg, self.px(22), border=p.ink, width=2, radius=4)
            else:
                d.box(sx, by, seg, self.px(22), fill=p.card, border=p.ink, width=2, radius=4)
        with d.clip(x, y + self.px(6), w, by - y - self.px(10)):
            # When the list does not fit, start a little before the current step.
            fits = max(1, int((by - y - self.px(14)) // row))
            first = 0
            if len(items) > fits and current is not None:
                first = max(0, min(current - fits // 2, len(items) - fits))
            cursor = by - self.px(14)
            for i, item in enumerate(items):
                if i < first:
                    continue
                if cursor - row < y + self.px(6):
                    break                    # only whole rows (rule R4)
                cy = cursor - row / 2
                bx = x + self.px(18)
                size = self.px(20)
                now = i == current and self.relay.is_running()
                if item["done"]:
                    self.checkbox(bx, cy - self.px(11), self.px(22), True)
                else:
                    d.box(bx, cy - self.px(11), self.px(22), self.px(22), fill=p.card,
                          border=p.ink if now else p.muted, width=2, radius=3)
                    if now:
                        d.polyline([(bx + self.px(8), cy - self.px(6)), (bx + self.px(13), cy + self.px(6))],
                                   p.ink, self.px(2.5))
                name = item["name"]
                if item.get("skipped"):
                    name += " (skipped)"
                who = "now" if now else {"claude": "Claude", "codex": "Codex", "user": "moved by you"}.get(
                    item.get("by", ""), "")
                ww = d.width_of("HAND", self.px(17), who) if who else 0
                room = w - self.px(34) - self.px(18) - (ww + self.px(28) if who else self.px(18))
                if now:
                    d.text("BLACK", self.px(16), bx + self.px(34), cy - self.px(6),
                           d.fit("BLACK", self.px(16), name, room), p.ink)
                else:
                    d.text("HAND", size, bx + self.px(34), cy - self.px(7), d.fit("HAND", size, name, room),
                           p.ink if item["done"] else p.muted)
                if who:
                    d.text("HAND", self.px(17), x + w - self.px(18) - ww, cy - self.px(6), who, p.muted)
                    if item.get("by") in ("claude", "codex") and not now:
                        self.mark(item["by"], x + w - self.px(30) - ww, cy, self.px(6), p.muted)
                cursor -= row
        return h

    # -- the conversation -------------------------------------------------------
    def entry_id(self, entry):
        if "_id" not in entry:
            entry["_id"] = state.next_id
            state.next_id += 1
        return entry["_id"]

    def visible_log(self):
        log = [e for e in session.log if e.get("t", 0) >= state.cleared_at]
        running = self.relay.is_running() and session.status in (THINKING, WORKING)
        if running:
            # The steps of the running turn are shown in the thinking box instead.
            last_you = max((i for i, e in enumerate(log) if e["who"] == "you"), default=-1)
            log = log[:last_you + 1] + [e for e in log[last_you + 1:] if e["who"] not in ("step", "look", "retry")]
        return log

    def entry(self, e, x, top, w, draw=True):
        """One message. Returns its height; draws it when draw is True."""
        p = self.p
        who = e.get("who")
        text = e.get("text") or ""
        if who == "you":
            label_size = self.px(10)
            size = self.px(21)
            h = label_size + self.px(6)
            body_w = round(w * 0.86)
            body_h = d.paragraph_height("HAND", size, text, body_w)
            if draw:
                lw = d.tracked_width("BLACK", label_size, "YOU")
                d.tracked("BLACK", label_size, x + w - lw, top - label_size * 0.9, "YOU", p.ink)
                d.paragraph("HAND", size, x + w - body_w, top - h, text, p.ink, body_w, align="right")
            return h + body_h
        if who == "ai":
            brain = e.get("brain") or "claude"
            label_size = self.px(11)
            size = self.px(16)
            h = label_size + self.px(8)
            lines = d.wrap("BODY", size, text, w)
            eid = self.entry_id(e)
            long = len(lines) > READ_MORE_LINES and eid not in state.expanded
            shown = lines[:READ_MORE_LINES - 2] if long else lines
            body_h = len(shown) * d.line_height("BODY", size)
            more_h = self.px(26) if long else 0
            if draw:
                self.mark(brain, x + self.px(6), top - label_size * 0.5, self.px(5))
                d.tracked("BLACK", label_size, x + self.px(18), top - label_size * 0.9, label(brain).upper(), p.ink)
                lh = d.line_height("BODY", size)
                ly = top - h - round(size * 0.95)
                for line in shown:
                    d.text("BODY", size, x, ly, line, p.ink)
                    ly -= lh
                if long:
                    my = top - h - body_h - self.px(18)
                    d.text("HAND", self.px(17), x, my, "Read more", p.ink)
                    mw = d.width_of("HAND", self.px(17), "Read more")
                    d.rect(x, my - self.px(3), mw, self.px(1), p.ink)
                    self.hit(x, my - self.px(6), mw, self.px(24), f"read_more:{eid}")
            return h + body_h + more_h
        if who in ("step", "look", "retry", "done", "plan"):
            size = self.px(14)
            indent = self.px(22)
            body_h = d.paragraph_height("BODY", size, text, w - indent)
            if draw:
                iy = top - size * 0.95
                if who == "look":
                    d.eye_icon(x + self.px(8), iy + size * 0.3, self.px(14), p.ink, self.px(1.5))
                elif who in ("step", "done") and e.get("ok", True):
                    d.check_mark(x + self.px(1), iy, self.px(13), p.ink, self.px(2))
                elif who == "retry":
                    d.pencil_icon(x + self.px(1), iy - self.px(1), self.px(12), p.muted, self.px(1.5))
                d.paragraph("BODY", size, x + indent, top, text, p.ink if who != "retry" else p.muted, w - indent)
            return body_h
        # info and error: system notes, muted (errors say what happened and what to do)
        size = self.px(14)
        body_h = d.paragraph_height("BODY", size, text, w)
        if draw:
            d.paragraph("BODY", size, x, top, text, p.muted if who != "error" else p.ink, w)
        return body_h

    def thinking_box(self, x, top, w, draw=True):
        """Claude or Codex is thinking: ticks, the scribble line and the latest steps."""
        p = self.p
        brain = self.relay.running or self.relay.active
        log = list(session.log)
        last_you = max((i for i, e in enumerate(log) if e["who"] == "you"), default=-1)
        trace = [e for e in log[last_you + 1:] if e["who"] in ("step", "look", "retry")][-4:]
        card = session.card or {}
        size = self.px(15)
        inner = w - self.px(52)
        h = self.px(70)
        for e in trace:
            h += d.paragraph_height("BODY", size, e["text"], inner, 3) + self.px(6)
        doing = ""
        if card.get("kind") == "work":
            doing = "Writing " + (card.get("progress") or "a step").lower() + ": " + (card.get("title") or "")
            h += d.paragraph_height("BODY", size, doing, inner, 3) + self.px(6)
        h += self.px(8)
        if not draw:
            return h
        y = top - h
        self.card_box(x, y, w, h, dashed=True)
        ty = top - self.px(26)
        phase = ((self.now % 1.2) / 1.2) if self.motion else None
        d.ticks(x + self.px(16), ty - self.px(2), self.px(13), p.ink, phase)
        working = session.status == WORKING
        seconds = int(self.now - (state.turn_started or self.now))
        words = f"{label(brain)} is {'building' if working else 'thinking'}"
        # A fixed-width slot for the timer, so the label does not jitter as the seconds grow.
        timer = f"{seconds} s"
        slot = d.tracked_width("BLACK", self.px(11), "000 S")
        self.label_caps(x + self.px(44), ty, words, max_width=w - self.px(60) - slot)
        self.label_caps(x + w - self.px(14) - slot, ty, timer, color=p.muted)
        progress = ((self.now % 2.6) / 2.6) if self.motion else 1.0
        d.scribble(x + self.px(18), ty - self.px(22), min(w * 0.5, self.px(180)), self.px(5), p.ink, progress,
                   "zigzag" if brain == "codex" else "wavy", self.px(2))
        cursor = ty - self.px(44)
        with d.clip(x, y, w, h):
            for e in trace:
                icon_y = cursor - size * 0.95
                if e["who"] == "retry":
                    d.pencil_icon(x + self.px(18), icon_y - self.px(1), self.px(12), p.muted, self.px(1.5))
                elif e["who"] == "look":
                    d.eye_icon(x + self.px(24), icon_y + size * 0.3, self.px(14), p.ink, self.px(1.5))
                else:
                    d.check_mark(x + self.px(18), icon_y, self.px(13), p.ink, self.px(2))
                cursor -= d.paragraph("BODY", size, x + self.px(40), cursor, e["text"], p.ink, inner,
                                      max_lines=3) + self.px(6)
            if doing:
                d.pencil_icon(x + self.px(18), cursor - size * 0.95 - self.px(1), self.px(12), p.ink, self.px(1.5))
                d.paragraph("BODY", size, x + self.px(40), cursor, doing, p.ink, inner, max_lines=3)
        return h

    def conversation_height(self, w):
        inner_w = w - 2 * self.px(16) - self.px(6)
        log = self.visible_log()
        gap = self.px(14)
        heights = [self.entry(e, 0, 0, inner_w, draw=False) for e in log]
        thinking = self.relay.is_running() and session.status in (THINKING, WORKING)
        think_h = self.thinking_box(0, 0, inner_w, draw=False) if thinking else 0
        return sum(heights) + gap * max(0, len(heights) - 1) + (think_h + gap if thinking else 0)

    def conversation(self, x, y, w, h):
        p = self.p
        self.card_box(x, y, w, h)
        pad = self.px(16)
        inner_x, inner_w = x + pad, w - 2 * pad - self.px(6)
        view_top, view_bottom = y + h - pad, y + pad
        view_h = view_top - view_bottom
        log = self.visible_log()
        gap = self.px(14)
        heights = [self.entry(e, inner_x, 0, inner_w, draw=False) for e in log]
        thinking = self.relay.is_running() and session.status in (THINKING, WORKING)
        think_h = self.thinking_box(inner_x, 0, inner_w, draw=False) if thinking else 0
        content = sum(heights) + gap * max(0, len(heights) - 1) + (think_h + gap if thinking else 0)
        state.max_scroll = max(0.0, content - view_h)
        state.scroll = max(0.0, min(state.scroll, state.max_scroll))
        state.scroll_rect = (x, y, x + w, y + h)
        # Content is laid out from the top; when it is taller than the view, the newest
        # message sits at the bottom unless the user scrolled up.
        if content > view_h:
            top = round(view_bottom + content - state.scroll)
        else:
            top = view_top
        b = self.px(theme.BORDER_CARD)
        with d.clip(x + b, y + b, w - 2 * b, h - 2 * b):
            for e, eh in zip(log, heights):
                if top - eh < view_top + self.px(40) and top > view_bottom - self.px(40):
                    self.entry(e, inner_x, top, inner_w)
                top -= eh + gap
            if thinking:
                self.thinking_box(inner_x, top, inner_w)
        if state.max_scroll > 0:
            # A thin scroll bar on the right.
            bar_h = max(self.px(24), view_h * view_h / content)
            t = 1.0 - state.scroll / state.max_scroll
            by = view_bottom + (view_h - bar_h) * t
            d.box(x + w - self.px(9), by, self.px(4), bar_h, fill=p.with_alpha(p.ink, 0.35), radius=2)

    # -- scrolling bodies --------------------------------------------------------
    def scroll_body(self, x, y, w, h, blocks):
        """Blocks [(height, draw(x, top, w))] in a scrollable column."""
        gap = self.px(GAP)
        content = sum(bh for bh, _ in blocks) + gap * max(0, len(blocks) - 1)
        state.body_max = max(0.0, content - h)
        state.body_scroll = max(0.0, min(state.body_scroll, state.body_max))
        state.scroll_rect = (x, y, x + w, y + h)
        top = round(y + h + state.body_scroll)
        with d.clip(x - self.px(4), y, w + self.px(14), h + self.px(4)):
            for bh, fn in blocks:
                if top - bh < y + h + self.px(20) and top > y - self.px(20):
                    fn(x, top, w)
                top -= bh + gap

    # -- the baton journal (screen 25) ------------------------------------------
    def journal_screen(self, x0, y0, x1, y1):
        w = x1 - x0
        gap = self.px(GAP)
        top = y1 - self.header(x0, y1, x1, back=True) - gap
        bh = self.px(48)
        sh = self.px(theme.SHADOW_BUTTON)
        bw = round((w - self.px(12) - sh) * 0.64)
        self.button(x0, y0 + sh, bw, bh - sh, "Open as Blender text", "op:blendwme.open_journal", size=14)
        self.button(x0 + bw + self.px(12), y0 + sh, w - bw - self.px(12) - sh, bh - sh, "Export",
                    "op:blendwme.export_journal", size=14)
        bottom = y0 + bh + gap
        self.scroll_body(x0, bottom, w, top - bottom, self.journal_blocks(w))

    def journal_blocks(self, w):
        from .journal import journal
        p = self.p
        data = journal.data
        blocks = []
        name = os.path.basename(bpy.data.filepath) if bpy.data.filepath else "Unsaved file"

        def title(x, top, w):
            self.label_caps(x, top - self.px(14), "The baton", size=12)
            d.text("BLACK", self.px(24), x, top - self.px(46), d.fit("BLACK", self.px(24), name, w), p.ink)
            d.text("HAND", self.px(18), x, top - self.px(72),
                   d.fit("HAND", self.px(18), "Saved with your file. Both brains read it.", w), p.muted)
        blocks.append((self.px(80), title))
        tw = w - self.px(36)
        brief = data.get("brief") or "No brief yet. Type what you want to make."
        brief_h = self.px(48) + d.paragraph_height("HAND", self.px(19), brief, tw) + self.px(10)

        def brief_card(x, top, w):
            self.card_box(x, top - brief_h, w, brief_h)
            with d.clip(x, top - brief_h, w, brief_h):
                self.label_caps(x + self.px(18), top - self.px(28), "Brief")
                d.paragraph("HAND", self.px(19), x + self.px(18), top - self.px(38), brief, p.ink, tw)
        blocks.append((brief_h, brief_card))
        # Steps, signed
        rows = []
        for e in data.get("events", [])[-60:]:
            kind = e.get("kind")
            if kind == "step" and e.get("ok"):
                rows.append(("step", e.get("by"), e["t"], e.get("title", "")))
            elif kind == "user_edit":
                rows.append(("step", "user", e["t"], "; ".join(e.get("lines", [])[:2])))
            elif kind == "switch":
                rows.append(("switch", e.get("by"), e["t"], f"{label(e.get('by'))} → {label(e.get('to'))}"
                             + (f" · {e.get('text')}" if e.get("text") else "")))
        text_w = w - self.px(130)
        steps_h = self.px(52) + sum(
            (self.px(34) if r[0] == "switch" else d.paragraph_height("HAND", self.px(18), r[3], text_w) + self.px(8))
            for r in rows) + (self.px(24) if not rows else 0)

        def steps_card(x, top, w):
            self.card_box(x, top - steps_h, w, steps_h)
            with d.clip(x, top - steps_h, w, steps_h):
                self.label_caps(x + self.px(18), top - self.px(28), "Steps, signed")
                cursor = top - self.px(46)
                if not rows:
                    d.text("HAND", self.px(18), x + self.px(18), cursor - self.px(18), "Nothing built yet.", p.muted)
                for kind, by, t, words in rows:
                    if kind == "switch":
                        cy = cursor - self.px(16)
                        words = d.fit("HAND", self.px(16), words, w - self.px(80))
                        tw2 = d.width_of("HAND", self.px(16), words)
                        mid = x + w / 2
                        d.dashed_path([(x + self.px(18), cy), (mid - tw2 / 2 - self.px(8), cy)], p.ink, self.px(2),
                                      self.px(8), self.px(5), closed=False)
                        d.dashed_path([(mid + tw2 / 2 + self.px(8), cy), (x + w - self.px(18), cy)], p.ink, self.px(2),
                                      self.px(8), self.px(5), closed=False)
                        d.text("HAND", self.px(16), mid - tw2 / 2, cy - self.px(5), words, p.ink)
                        cursor -= self.px(34)
                        continue
                    stamp = time.strftime("%H:%M", time.localtime(t))
                    d.text("HAND", self.px(16), x + self.px(18), cursor - self.px(17), stamp, p.muted)
                    if by == "user":
                        self.you_box(x + self.px(72), cursor - self.px(16), self.px(9))
                    else:
                        self.mark(by, x + self.px(86), cursor - self.px(11), self.px(7))
                    used = d.paragraph("HAND", self.px(18), x + self.px(112), cursor, words, p.ink, text_w)
                    cursor -= used + self.px(8)
        blocks.append((steps_h, steps_card))
        # Who made what
        counts = self._who_made_what()
        total = sum(counts.values())
        if total:
            def who_card(x, top, w):
                h = self.px(112)
                self.card_box(x, top - h, w, h)
                self.label_caps(x + self.px(18), top - self.px(28), f"Who made what · {total} objects",
                                max_width=w - self.px(36))
                bx, by, bw, bh = x + self.px(18), top - self.px(68), w - self.px(36), self.px(26)
                cursor = bx
                for key in ("claude", "codex", "user"):
                    part = round(bw * counts[key] / total)
                    if part <= 0:
                        continue
                    if key == "claude":
                        d.rect(cursor, by, part, bh, p.accent)
                    elif key == "codex":
                        d.hatch(cursor, by, part, bh, p.ink, self.px(6), self.px(1.5))
                    cursor += part
                d.box(bx, by, bw, bh, border=p.ink, width=2, radius=4)
                lx = x + self.px(18)
                with d.clip(x, top - h, w, h):
                    for key, words in (("claude", "Claude"), ("codex", "Codex"), ("user", "You")):
                        sq = self.px(18)
                        sy = top - self.px(100)
                        if key == "claude":
                            d.rect(lx, sy, sq, sq, p.accent)
                        elif key == "codex":
                            d.hatch(lx, sy, sq, sq, p.ink, self.px(5), self.px(1.5))
                        d.box(lx, sy, sq, sq, border=p.ink, width=1, radius=1)
                        item = f"{words} {counts[key]}"
                        d.text("HAND", self.px(18), lx + sq + self.px(6), sy + self.px(3), item, p.ink)
                        lx += sq + self.px(20) + d.width_of("HAND", self.px(18), item)
            blocks.append((self.px(112), who_card))
        # Problems and next
        problems = [(s["text"], s.get("solved_by")) for s in data.get("struggles", [])[-6:]]
        nxt = (data.get("handoffs") or [{}])[-1].get("next_steps") or []
        if problems or nxt:
            pw = w - self.px(36)
            ph = self.px(48) + sum(d.paragraph_height("HAND", self.px(18), t + (f" · fixed by {label(b)}" if b else
                                                                                " Open."), pw) + self.px(4)
                                   for t, b in problems)
            if nxt:
                ph += self.px(36) + d.paragraph_height("HAND", self.px(18), "; ".join(nxt), pw)
            ph += self.px(10)

            def problems_card(x, top, w):
                self.card_box(x, top - ph, w, ph)
                with d.clip(x, top - ph, w, ph):
                    self.label_caps(x + self.px(18), top - self.px(28), "Problems")
                    cursor = top - self.px(38)
                    for words, solved in problems:
                        if solved:
                            line = words + f" · fixed by {label(solved)}"
                            used = d.paragraph("HAND", self.px(18), x + self.px(18), cursor, line, p.muted, pw)
                            first = d.wrap("HAND", self.px(18), line, pw)[0]
                            sw = min(d.width_of("HAND", self.px(18), words), d.width_of("HAND", self.px(18), first))
                            sy = cursor - round(self.px(18) * 0.6)
                            d.rect(x + self.px(18), sy, sw, self.px(1), p.ink)
                        else:
                            used = d.paragraph("HAND", self.px(18), x + self.px(18), cursor, words + " Open.", p.ink, pw)
                        cursor -= used + self.px(4)
                    if nxt:
                        cursor -= self.px(8)
                        self.label_caps(x + self.px(18), cursor - self.px(14), "Next")
                        d.paragraph("HAND", self.px(18), x + self.px(18), cursor - self.px(24), "; ".join(nxt), p.ink, pw)
            blocks.append((ph, problems_card))
        return blocks

    _makers_cache = {"t": 0.0, "value": {}}

    def _who_made_what(self):
        cache = Painter._makers_cache
        if self.now - cache["t"] > 1.0:
            from . import scene_info
            counts = {"claude": 0, "codex": 0, "user": 0}
            try:
                makers = scene_info.makers()
                for obj in bpy.context.scene.objects:
                    if obj.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT", "GPENCIL", "GREASEPENCIL"):
                        continue
                    who = makers.get(obj.name, "user")
                    counts[who if who in counts else "claude"] += 1
            except Exception:
                pass
            cache.update(t=self.now, value=counts)
        return cache["value"]


# ----------------------------------------------------------------------
# The input's layout: [(start, end)] per wrapped line, as indices into the text
# ----------------------------------------------------------------------
_input_cache = {}


def input_layout(text, size, width):
    """Wrap the input at spaces (a long word breaks by characters). Each line is a range of
    the text, so the caret and the selection map straight onto it."""
    key = (text, round(size, 2), int(width), d.ROLES.get("HAND", "HAND"), round(d.FONT_SCALE, 3),
           d.ROLE_SIZE.get("HAND", 1.0))
    hit = _input_cache.get(key)
    if hit is None:
        if len(_input_cache) > 200:
            _input_cache.clear()
        hit = _input_cache[key] = _input_layout(text, size, width)
    return hit


def _input_layout(text, size, width):
    lines = []
    if not text:
        return [(0, 0)]
    start = 0
    for para in text.split("\n"):
        end_para = start + len(para)
        line_start = start
        i = start
        last_space = None
        while i < end_para:
            if text[i] == " ":
                last_space = i
            if d.width_of("HAND", size, text[line_start:i + 1]) > width and i > line_start:
                if last_space is not None and last_space > line_start:
                    lines.append((line_start, last_space))
                    line_start = last_space + 1
                else:
                    lines.append((line_start, i))
                    line_start = i
                last_space = None
                continue
            i += 1
        lines.append((line_start, end_para))
        start = end_para + 1
    return lines


def selection():
    """(start, end) of the selected text, or None."""
    if state.anchor is None or state.anchor == state.cursor:
        return None
    return min(state.anchor, state.cursor), max(state.anchor, state.cursor)


def draw_callback():
    """SpaceView3D WINDOW POST_PIXEL handler: draw the panel in its host viewport."""
    area = bpy.context.area
    if not is_open_in(area):
        return
    try:
        status = session.status
        if status in (THINKING, WORKING) and state.last_status not in (THINKING, WORKING):
            state.turn_started = time.time()
        state.last_status = status
        Painter(area, bpy.context.region).draw()
    except Exception:
        if not state.error_shown:          # print once, never break Blender's drawing
            state.error_shown = True
            traceback.print_exc()
