"""The BlendWMe panel: the approved Graphite Paper design, drawn inside the 3D Viewport.

Blender's sidebar cannot take custom fonts, colours or shapes, so BlendWMe draws
its own panel (gpu + blf) at the right edge of a 3D Viewport. A modal operator
(BLENDWME_OT_panel) handles clicks, scrolling and typing while the mouse is over
the panel, and lets every other event through to Blender.

Screens (docs/design/screens): "main" (Together / Teach / Auto, 10-23),
"settings" (24) and "journal" (25).

The drawing is immediate: each redraw lays the panel out again and records the
clickable rectangles (hits) for the modal operator. Nothing here keeps a Python
reference to Blender data between redraws.
"""

import os
import time
import traceback

import bpy

from . import draw2d as d
from . import theme
from .state import ASKING, DONE, ERROR, IDLE, PAUSED, THINKING, WAIT_NEXT, WAIT_USER, WORKING, session

PANEL_WIDTH = 372          # CSS px at 100 %
PAD = 16
GAP = 12
READ_MORE_LINES = 12       # longer messages show this many lines and "Read more"

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
MODES = (("WATCH", "Together"), ("TEACH", "Teach"), ("AUTO", "Auto"))


def label(brain):
    return {"claude": "Claude", "codex": "Codex"}.get(brain, "BlendWMe")


# ----------------------------------------------------------------------
# Panel state (one per Blender session; plain Python values only)
# ----------------------------------------------------------------------
class PanelState:
    def __init__(self):
        self.open = False
        self.area_ptr = 0              # as_pointer() of the 3D Viewport that hosts the panel
        self.screen = "main"           # main | settings | journal
        self.rect = (0, 0, 0, 0)       # x0, y0, x1, y1 in region pixels
        self.hits = []                 # [(x0, y0, x1, y1, action)]
        self.scroll = 0.0              # conversation: pixels scrolled up from the newest message
        self.max_scroll = 0.0
        self.scroll_rect = (0, 0, 0, 0)
        self.body_scroll = 0.0         # settings and journal screens
        self.body_max = 0.0
        self.text = ""                 # what the user is typing
        self.cursor = 0
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
    return state.open and area is not None and area.as_pointer() == state.area_ptr


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


def placement(area, region):
    """The panel rectangle in the 3D Viewport's main region: right side, clear of the
    sidebar and the header when they overlap the viewport."""
    s = d.ui()
    right_inset = 0
    top_inset = 0
    for r in area.regions:
        if r.width <= 1 or r.height <= 1 or r.type == "WINDOW":
            continue
        overlaps_x = region.x <= r.x < region.x + region.width
        overlaps_y = region.y <= r.y < region.y + region.height
        if r.type == "UI" and overlaps_x and overlaps_y:
            right_inset = max(right_inset, region.x + region.width - r.x)
        if r.type in ("HEADER", "TOOL_HEADER") and overlaps_y and r.y > region.y + region.height / 2:
            top_inset = max(top_inset, region.y + region.height - r.y)
    margin = 12 * s
    width = min(PANEL_WIDTH * s, region.width - right_inset - 2 * margin)
    x1 = region.width - right_inset - margin - 6 * s        # room for the hard shadow
    x0 = x1 - width
    y1 = region.height - top_inset - margin
    y0 = margin + 6 * s
    return x0, y0, x1, y1


# ----------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------
class Painter:
    """Draws one frame of the panel and records where the buttons are."""

    def __init__(self, area, region):
        self.area, self.region = area, region
        self.s = d.ui()
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
        return v * self.s

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
            d.dashed_path(d.rounded_points(x, y, w, h, self.px(theme.RADIUS)), border or p.ink,
                          self.px(width), self.px(7), self.px(5))
        else:
            d.box(x, y, w, h, fill=fill or p.card, border=border or p.ink, width=width, radius=theme.RADIUS)

    def label_caps(self, x, y, value, color=None, size=11):
        return d.tracked("BLACK", self.px(size), x, y, value.upper(), color or self.p.ink)

    def button(self, x, y, w, h, value, action, primary=False, hot=False, size=14):
        p = self.p
        fill = p.hot if hot else (p.accent if primary else p.card)
        ink = p.on_accent if (primary or hot) else p.ink
        d.box(x, y, w, h, fill=fill, border=p.ink if not hot else p.hot, width=theme.BORDER_CARD,
              radius=theme.RADIUS, shadow=p.shadow, shadow_offset=theme.SHADOW_BUTTON)
        tw = d.width_of("BLACK", self.px(size), value)
        d.text("BLACK", self.px(size), x + (w - tw) / 2, y + (h - self.px(size) * 0.72) / 2, value, ink)
        self.hit(x, y, w, h, action)

    def chip(self, x, y, w, h, value, action):
        p = self.p
        d.box(x, y, w, h, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=h / 2 / self.s)
        size = self.px(18)
        tw = d.width_of("HAND", size, value)
        d.text("HAND", size, x + (w - tw) / 2, y + (h - size * 0.62) / 2, value, p.ink)
        self.hit(x, y, w, h, action)

    def close_x(self, x1, y1, action="close_card"):
        size = self.px(10)
        cx, cy = x1 - self.px(16), y1 - self.px(16)
        d.cross(cx, cy, size, self.p.muted, self.px(2))
        self.hit(cx - self.px(12), cy - self.px(12), self.px(24), self.px(24), action)

    def mark(self, brain, cx, cy, r, color=None):
        color = color or self.p.ink
        if brain == "codex":
            d.diamond(cx, cy, r * 1.15, color, self.px(2.2))
        elif brain == "you":
            pass
        else:
            d.circle(cx, cy, r, color, self.px(2.2))

    def you_box(self, x, y, size):
        """The small boxed word "YOU"."""
        w = d.tracked_width("BLACK", size, "YOU") + self.px(6)
        d.box(x, y - self.px(3), w, size + self.px(5), border=self.p.ink, width=1.4, radius=3)
        d.tracked("BLACK", size, x + self.px(3), y, "YOU", self.p.ink)
        return w

    # -- the whole panel -----------------------------------------------
    def draw(self):
        import gpu
        x0, y0, x1, y1 = placement(self.area, self.region)
        if x1 - x0 < self.px(200) or y1 - y0 < self.px(240):
            return
        state.rect = (x0, y0, x1 + self.px(6), y1)
        gpu.state.blend_set("ALPHA")
        p = self.p
        d.box(x0, y0, x1 - x0, y1 - y0, fill=p.panel, border=p.ink, width=theme.BORDER_PANEL,
              radius=theme.RADIUS, shadow=p.shadow, shadow_offset=theme.SHADOW_PANEL)
        self.hit(x0, y0, x1 - x0, y1 - y0, "panel")          # swallow clicks on empty paper
        pad = self.px(PAD)
        inner = (x0 + pad, y0 + pad, x1 - pad, y1 - pad)
        if state.screen == "settings":
            self.settings_screen(*inner)
        elif state.screen == "journal":
            self.journal_screen(*inner)
        else:
            self.main_screen(*inner)
        gpu.state.blend_set("NONE")
        # Later hits are on top: the modal checks them last-first.
        state.hits = self.hits

    # -- header -----------------------------------------------------------
    def logo(self, x, top, size=30):
        """Blend + tilted WMe tag + square full stop (the full stop blinks on your turn)."""
        import blf
        p = self.p
        fs = self.px(size)
        base = top - fs * 0.92
        d.text("BLACK", fs, x, base, "Blend", p.ink)
        bx = x + d.width_of("BLACK", fs, "Blend") + self.px(4)
        tag_fs = fs * 0.92
        tw = d.width_of("BLACK", tag_fs, "WMe")
        w, h = tw + self.px(14), fs * 1.1
        cy = base + fs * 0.35
        angle = -0.044          # -2.5 degrees, like a sticker
        import math

        def rotated(pts, cx, cy_):
            ca, sa = math.cos(angle), math.sin(angle)
            return [(cx + (px_ - cx) * ca - (py - cy_) * sa, cy_ + (px_ - cx) * sa + (py - cy_) * ca) for px_, py in pts]
        cx = bx + w / 2
        box_pts = d.rounded_points(bx, cy - h / 2, w, h, self.px(5))
        off = self.px(4)
        d.fill_poly(rotated([(a + off, b - off) for a, b in box_pts], cx, cy), p.shadow)
        d.fill_poly(rotated(box_pts, cx, cy), p.accent)
        fid = d.font("BLACK")
        blf.enable(fid, blf.ROTATION)
        blf.rotation(fid, angle)
        tx, ty = bx + self.px(7), cy - tag_fs * 0.36
        rx, ry = rotated([(tx, ty)], cx, cy)[0]
        d.text("BLACK", tag_fs, rx, ry, "WMe", p.on_accent)
        blf.rotation(fid, 0)
        blf.disable(fid, blf.ROTATION)
        dot = fs * 0.24
        dot_alpha = 1.0 if (not your_turn() or self.blink(0.5)) else 0.15
        d.rect(bx + w + self.px(4), base, dot, dot, p.with_alpha(p.hot, dot_alpha))
        return fs * 1.15

    def status_pill(self, x1, top):
        p = self.p
        status = session.status
        card = session.card or {}
        running = self.relay.is_running()
        if card.get("kind") == "limit":
            words, icon = f"{label(card.get('brain'))} paused", "dot"
        elif status in (WAIT_USER, WAIT_NEXT, ASKING):
            words, icon = "Your turn", "dot"
        elif status == PAUSED:
            words, icon = "You have control", "none"
        elif running and status == WORKING:
            words, icon = "Building", "ticks"
        elif running:
            words, icon = "Thinking", "ticks"
        elif status == DONE:
            words, icon = "Done", "none"
        elif status == ERROR:
            words, icon = "Stopped", "none"
        else:
            return
        size = self.px(17)
        tw = d.width_of("HAND", size, words)
        icon_w = self.px(26) if icon != "none" else 0
        h = self.px(32)
        w = tw + icon_w + self.px(24)
        x = x1 - w
        y = top - h
        d.box(x, y, w, h, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=h / 2 / self.s)
        if icon == "ticks":
            phase = ((self.now % 1.2) / 1.2) if self.motion else None
            d.ticks(x + self.px(14), y + self.px(10), self.px(12), p.ink, phase)
        elif icon == "dot":
            alpha = 1.0 if self.blink(0.5) else 0.25
            d.disc(x + self.px(20), y + h / 2, self.px(6), p.with_alpha(p.hot, alpha))
        d.text("HAND", size, x + self.px(12) + icon_w, y + (h - size * 0.6) / 2, words, p.ink)

    def header(self, x0, top, x1, back=False):
        h = self.logo(x0, top)
        if back:
            self.back_pill(x1, top)
        else:
            self.status_pill(x1, top)
        return max(h, self.px(36))

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

    # -- main screen ------------------------------------------------------
    def main_screen(self, x0, y0, x1, y1):
        w = x1 - x0
        gap = self.px(GAP)
        top = y1
        top -= self.header(x0, top, x1) + gap
        first_view = not self.relay.has_conversation() and not session.log
        limit = (session.card or {}).get("kind") == "limit"
        # Measure the bottom stack first, so the brain card can step aside when space is short
        # (cards must never cover the mode tabs).
        input_h = self.input_height(w)
        card_h = self.current_cards(x0, 0, w, measure=True)
        stack = (self.px(24) + gap + (0 if limit else self.px(42) + gap) + input_h + gap
                 + self.action_height() + (card_h + gap if card_h else 0))
        brain_h = self.px(60) + gap
        tabs_h = self.px(46) + gap
        needed = self.px(170)
        if session.mode == "AUTO" and session.plan:
            needed += self.px(200)       # room for the plan checklist too
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
                self.chip(x0, top - h, w, h, name, "example:" + name)
                top -= h + self.px(10)
            return
        if not limit:
            bottom += self.chips_row(x0, bottom, w) + gap
        self.text_input(x0, bottom, w, input_h)
        bottom += input_h + gap
        bottom += self.action_buttons(x0, bottom, w)
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
        d.text("BLACK", self.px(15), x + self.px(46), y + h - self.px(26), title, p.ink)
        sub_w = w - self.px(46) - (self.px(140) if not limited else self.px(16))
        while sub and d.width_of("HAND", self.px(16), sub) > sub_w:
            sub = sub[:-2].rstrip() + "…"     # a short line beside the button; the full text is in Settings
        d.text("HAND", self.px(16), x + self.px(46), y + self.px(13), sub, p.muted)
        if not limited:
            bw, bh = self.px(116), self.px(34)
            bx, by = x + w - bw - self.px(12), y + (h - bh) / 2
            d.box(bx, by, bw, bh, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=8)
            ax, ay = bx + self.px(16), by + bh / 2
            d.polyline([(ax, ay + self.px(4)), (ax + self.px(14), ay + self.px(4)), (ax + self.px(10), ay + self.px(8))],
                       p.ink, self.px(1.8))
            d.polyline([(ax + self.px(14), ay - self.px(4)), (ax, ay - self.px(4)), (ax + self.px(4), ay - self.px(8))],
                       p.ink, self.px(1.8))
            d.text("BLACK", self.px(12), bx + self.px(40), by + (bh - self.px(9)) / 2, "Hand over", p.ink)
            self.hit(bx, by, bw, bh, "handover")
        return h

    def mode_tabs(self, x, top, w):
        p = self.p
        h = self.px(46)
        y = top - h
        d.box(x, y, w, h, fill=p.panel, border=p.ink, width=theme.BORDER_CARD, radius=theme.RADIUS)
        current = self.props.mode if self.props else session.mode
        seg = w / 3
        for i, (key, name) in enumerate(MODES):
            sx = x + i * seg
            if key == current:
                pts = d.rounded_points(sx, y, seg, h, self.px(theme.RADIUS))
                d.fill_poly(pts, p.accent)
                size = self.px(15)
                tw = d.width_of("BLACK", size, name)
                d.text("BLACK", size, sx + (seg - tw) / 2, y + (h - size * 0.72) / 2, name, p.on_accent)
            else:
                size = self.px(20)
                tw = d.width_of("HAND", size, name)
                d.text("HAND", size, sx + (seg - tw) / 2, y + (h - size * 0.6) / 2, name, p.ink)
            self.hit(sx, y, seg, h, "mode:" + key)
        d.polyline(d.rounded_points(x, y, w, h, self.px(theme.RADIUS)), p.ink, self.px(theme.BORDER_CARD), closed=True)
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
        if connected:
            d.disc(x + self.px(8), cy, self.px(6), p.ink)
            words = "Connected to Blender"
        else:
            d.circle(x + self.px(8), cy, self.px(6), p.hot, self.px(2))
            words = "Not connected. Press Settings"
        d.text("HAND", size, x + self.px(22), cy - size * 0.32, words, p.ink)
        right = x + w
        for name, action in (("Journal", "screen:journal"), ("Settings", "screen:settings")):
            if state.screen != "main" and action != "screen:main":
                continue
            tw = d.width_of("HAND", size, name)
            tx = right - tw
            d.text("HAND", size, tx, cy - size * 0.32, name, p.ink)
            d.polyline([(tx, cy - size * 0.42), (tx + tw, cy - size * 0.42)], p.ink, self.px(1.2))
            self.hit(tx - self.px(4), y, tw + self.px(8), h, action)
            right = tx - self.px(14)
        return h

    # -- input and chips ----------------------------------------------------
    def placeholder(self):
        brain = label(self.relay.active)
        if session.status == ASKING:
            return "Type your answer..."
        if not self.relay.has_conversation():
            return "What shall we make?"
        mode = self.props.mode if self.props else session.mode
        return {"TEACH": "Ask a question...", "AUTO": "Describe what to build..."}.get(
            mode, f"Tell {brain} what to change...")

    def input_lines(self, w):
        return d.wrap("HAND", self.px(20), state.text or " ", w - self.px(28))

    def input_height(self, w):
        lines = len(self.input_lines(w)) if state.text else 1
        return self.px(26) + d.line_height("HAND", self.px(20)) * min(max(1, lines), 6)

    def text_input(self, x, y, w, h):
        p = self.p
        size = self.px(20)
        border = p.ink
        d.box(x, y, w, h, fill=p.card, border=border, width=theme.BORDER_CARD + (0.5 if state.focused else 0),
              radius=theme.RADIUS)
        tx = x + self.px(14)
        top = y + h - self.px(13)
        self.hit(x, y, w, h, "focus_input")
        if not state.text:
            d.text("HAND", size, tx, top - size * 0.95, self.placeholder(), p.muted)
            if state.focused and self.blink(0.5):
                d.rect(tx, top - size * 1.05, self.px(2), size * 1.05, p.ink)
            return
        lines = self.input_lines(w)
        lh = d.line_height("HAND", size)
        visible = lines[-6:]
        with d.clip(x, y, w, h):
            # Find the caret line and column from the cursor position in the wrapped text.
            consumed = 0
            caret = None
            start_line = len(lines) - len(visible)
            for i, line in enumerate(lines):
                end = consumed + len(line)
                if caret is None and state.cursor <= end:
                    caret = (i, state.cursor - consumed)
                consumed = end + 1   # the space or newline the wrap removed
            for i, line in enumerate(visible):
                d.text("HAND", size, tx, top - size * 0.95 - i * lh, line, p.ink)
            if state.focused and self.blink(0.5) and caret and caret[0] >= start_line:
                row = caret[0] - start_line
                cx = tx + d.width_of("HAND", size, lines[caret[0]][:caret[1]])
                d.rect(cx, top - size * 1.05 - row * lh, self.px(2), size * 1.05, p.ink)

    def chips_row(self, x, y, w):
        mode = self.props.mode if self.props else session.mode
        h = self.px(42)
        if mode == "AUTO":
            return self.pace_slider(x, y, w, h)
        chips = CHIPS.get(mode, CHIPS["WATCH"])
        gap = self.px(10)
        cw = (w - gap * 2) / 3
        for i, (name, action) in enumerate(chips):
            self.chip(x + i * (cw + gap), y, cw, h, name, "chip:" + action)
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
        sx0 = x + d.width_of("HAND", size, "Pace") + self.px(16)
        sx1 = x + w - vw - self.px(18)
        cy = y + h / 2
        track_h = self.px(10)
        d.box(sx0, cy - track_h / 2, sx1 - sx0, track_h, fill=p.card, border=p.shadow, width=1.2, radius=5)
        t = max(0.0, min(1.0, pace / 10.0))
        kx = sx0 + (sx1 - sx0) * t
        d.box(sx0, cy - track_h / 2, max(track_h, kx - sx0), track_h, fill=p.accent, radius=5)
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
            buttons = (("Check my work", "teach:done", True), ("Show me", "teach:show_me", False))
        elif status == WAIT_NEXT:
            buttons = (("Next", "op:blendwme.next", True), ("Take control", "op:blendwme.pause", False))
        elif status == PAUSED:
            buttons = (("Resume", "op:blendwme.resume", True), ("Stop", "op:blendwme.stop", False))
        elif running and status in (THINKING, WORKING):
            buttons = (("Pause", "op:blendwme.pause", True), ("Take control", "op:blendwme.pause", False))
        if not buttons:
            return 0
        h = self.px(48)
        gap = self.px(14)
        bw = (w - gap) / 2
        for i, (name, action, primary) in enumerate(buttons):
            self.button(x + i * (bw + gap), y, bw - self.px(3), h, name, action, primary=primary, size=15)
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
                 "pause": "YOU HAVE CONTROL", "done": "DONE", "stuck": "STUCK"}
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
        h = self.px(18) + label_size + self.px(10) + d.paragraph_height("HAND", text_size, main, tw)
        if why:
            h += self.px(6) + d.paragraph_height("HAND", self.px(16), "Why: " + why, tw)
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
                  border=p.ink if on else None, width=1.5, radius=2)
        else:
            d.box(x + self.px(18), top - block, block, block, fill=p.with_alpha(p.ink, 0.12), radius=2)
        self.label_caps(tx, top - label_size * 0.85, self.card_label(card))
        cursor = top - label_size - self.px(10)
        cursor -= d.paragraph("HAND", text_size, tx, cursor, main, p.ink, tw)
        if why:
            cursor -= self.px(6)
            d.paragraph("HAND", self.px(16), tx, cursor, "Why: " + why, p.muted, tw)
        self.close_x(x + w, y + h)
        return h

    def pointer_row(self, x, y, w, h):
        p = self.p
        self.card_box(x, y, w, h)
        cx, cy = x + self.px(32), y + h / 2
        d.circle(cx, cy, self.px(16), p.hot, self.px(2.5))
        d.disc(cx, cy, self.px(9), p.hot)
        d.circle(cx, cy, self.px(9), p.ink, self.px(1.5))
        targets = []
        for ptr in session.pointers:
            targets.append(ptr.get("label") or ptr.get("tab") or ptr.get("object") or ptr.get("region")
                           or ptr.get("editor") or "")
        words = "Pointer on " + " then ".join(t.replace("_", " ").title() for t in targets if t)
        tx = x + self.px(60)
        bw = self.px(78)
        d.paragraph("HAND", self.px(18), tx, y + h - self.px(10), words, p.ink, w - (tx - x) - bw - self.px(24),
                    max_lines=2)
        bx, by = x + w - bw - self.px(14), y + (h - self.px(34)) / 2
        d.box(bx, by, bw, self.px(34), fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=8)
        tw = d.width_of("BLACK", self.px(12), "Move")
        d.text("BLACK", self.px(12), bx + (bw - tw) / 2, by + self.px(12), "Move", p.ink)
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
        baton_h = self.px(52) + sum(max(d.paragraph_height("HAND", self.px(18), v, tw), self.px(24)) + self.px(4)
                                    for _, v in rows)
        text = card.get("text") or ""
        urgent_h = self.px(52) + d.paragraph_height("HAND", self.px(21), text, w - self.px(90)) + self.px(8)
        total = check_h + gap + btn_h + gap + baton_h + gap + urgent_h
        if measure:
            return total
        cursor = y
        # "Next time, hand over by itself in Auto"
        auto = bool(self.prefs and self.prefs.auto_relay)
        self.checkbox(x + self.px(6), cursor + self.px(3), self.px(24), auto)
        d.text("HAND", self.px(19), x + self.px(44), cursor + self.px(8), "Next time, hand over by itself in Auto", p.ink)
        self.hit(x, cursor, w, check_h, "toggle:auto_relay")
        cursor += check_h + gap
        bw = (w - self.px(14)) * 0.64
        self.button(x, cursor, bw, btn_h, f"Hand over to {label(target)}", "handover:" + target, hot=True, size=15)
        self.button(x + bw + self.px(14), cursor, w - bw - self.px(17), btn_h, "Wait", "close_card", size=15)
        cursor += btn_h + gap
        # The baton
        self.card_box(x, cursor, w, baton_h)
        top = cursor + baton_h - self.px(18)
        name = os.path.basename(bpy.data.filepath) if bpy.data.filepath else "unsaved file"
        self.label_caps(x + self.px(18), top - self.px(10), "The baton · " + name)
        mx = x + w - self.px(66)
        self.mark(card.get("brain") or "claude", mx, top - self.px(6), self.px(7))
        d.polyline([(mx + self.px(14), top - self.px(6)), (mx + self.px(34), top - self.px(6))], p.ink, self.px(2))
        d.polyline([(mx + self.px(28), top), (mx + self.px(34), top - self.px(6)), (mx + self.px(28), top - self.px(12))],
                   p.ink, self.px(2))
        self.mark(target, mx + self.px(48), top - self.px(6), self.px(7))
        row_top = top - self.px(30)
        for key, value in rows:
            self.label_caps(x + self.px(18), row_top - self.px(15), key, size=10)
            used = d.paragraph("HAND", self.px(18), x + self.px(112), row_top, value, p.ink, tw)
            row_top -= max(used, self.px(24)) + self.px(4)
        cursor += baton_h + gap
        # The urgent card (hot border)
        self.card_box(x, cursor, w, urgent_h, border=p.hot, width=3.0)
        top = cursor + urgent_h - self.px(18)
        block = self.px(22)
        on = self.blink(1.0)
        d.box(x + self.px(18), top - block, block, block, fill=p.hot if on else p.with_alpha(p.hot, 0.18), radius=2)
        self.label_caps(x + self.px(58), top - self.px(10), card.get("title") or "Limit reached")
        d.paragraph("HAND", self.px(21), x + self.px(58), top - self.px(22), text, p.ink, w - self.px(90))
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
        d.box(x, y, size, size, fill=p.accent if on else p.card, border=p.ink, width=2.2, radius=3)
        if on:
            d.check_mark(x + size * 0.18, y + size * 0.2, size * 0.64, p.on_accent, self.px(2.6))

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
        h = height or h
        y = top - h
        self.card_box(x, y, w, h)
        done = sum(1 for i in items if i["done"])
        from .journal import journal
        brief = (journal.data.get("brief") or "").split(".")[0].split(",")[0]
        words = brief.split()[:3]
        title = "Plan" + (" · " + " ".join(words) if words else "")
        ty = y + h - self.px(30)
        self.label_caps(x + self.px(18), ty, title)
        count = f"{done} OF {len(items)}"
        cw = d.tracked_width("BLACK", self.px(11), count)
        self.label_caps(x + w - self.px(18) - cw, ty, count, color=p.muted)
        # The segmented progress bar
        n = max(1, len(items))
        gap = self.px(6)
        seg = (w - self.px(36) - gap * (n - 1)) / n
        by = ty - self.px(36)
        current = next((i for i, item in enumerate(items) if not item["done"]), None)
        for i, item in enumerate(items):
            sx = x + self.px(18) + i * (seg + gap)
            if item["done"]:
                d.box(sx, by, seg, self.px(22), fill=p.accent, radius=4)
            elif i == current and self.relay.is_running():
                d.hatch(sx, by, seg, self.px(22), p.ink, self.px(5), self.px(1.6))
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
                                   p.ink, self.px(2.4))
                name = item["name"]
                skipped = item.get("skipped")
                if skipped:
                    name += " (skipped)"
                if now:
                    d.text("BLACK", self.px(16), bx + self.px(34), cy - self.px(6), name, p.ink)
                else:
                    d.text("HAND", size, bx + self.px(34), cy - self.px(7), name, p.ink if item["done"] else p.muted)
                who = "now" if now else {"claude": "Claude", "codex": "Codex", "user": "moved by you"}.get(
                    item.get("by", ""), "")
                if who:
                    ww = d.width_of("HAND", self.px(17), who)
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
            body_h = d.paragraph_height("HAND", size, text, w * 0.86)
            if draw:
                lw = d.tracked_width("BLACK", label_size, "YOU")
                d.tracked("BLACK", label_size, x + w - lw, top - label_size * 0.9, "YOU", p.ink)
                d.paragraph("HAND", size, x + w * 0.14, top - h, text, p.ink, w * 0.86, align="right")
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
                d.paragraph("BODY", size, x, top - h, "\n".join(shown), p.ink, w + self.px(40))
                if long:
                    my = top - h - body_h - self.px(18)
                    d.text("HAND", self.px(17), x, my, "Read more", p.ink)
                    mw = d.width_of("HAND", self.px(17), "Read more")
                    d.polyline([(x, my - self.px(3)), (x + mw, my - self.px(3))], p.ink, self.px(1.2))
                    self.hit(x, my - self.px(6), mw, self.px(24), f"read_more:{eid}")
            return h + body_h + more_h
        if who in ("step", "look", "retry", "done", "plan"):
            size = self.px(14)
            indent = self.px(22)
            body_h = d.paragraph_height("BODY", size, text, w - indent)
            if draw:
                iy = top - size * 0.95
                if who == "look":
                    d.eye_icon(x + self.px(8), iy + size * 0.3, self.px(14), p.ink, self.px(1.6))
                elif who in ("step", "done") and e.get("ok", True):
                    d.check_mark(x + self.px(1), iy, self.px(13), p.ink, self.px(2))
                elif who == "retry":
                    d.pencil_icon(x + self.px(1), iy - self.px(1), self.px(12), p.muted)
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
            h += d.paragraph_height("BODY", size, e["text"], inner) + self.px(6)
        doing = ""
        if card.get("kind") == "work":
            doing = "Writing " + (card.get("progress") or "a step").lower() + ": " + (card.get("title") or "")
            h += d.paragraph_height("BODY", size, doing, inner) + self.px(6)
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
        words = f"{label(brain)} is {'building' if working else 'thinking'} · {seconds} s"
        self.label_caps(x + self.px(44), ty, words)
        progress = ((self.now % 2.6) / 2.6) if self.motion else 1.0
        d.scribble(x + self.px(18), ty - self.px(22), min(w * 0.5, self.px(180)), self.px(5), p.ink, progress,
                   "zigzag" if brain == "codex" else "wavy", self.px(2.4))
        cursor = ty - self.px(44)
        for e in trace:
            icon_y = cursor - size * 0.95
            if e["who"] == "retry":
                d.pencil_icon(x + self.px(18), icon_y - self.px(1), self.px(12), p.muted)
            elif e["who"] == "look":
                d.eye_icon(x + self.px(24), icon_y + size * 0.3, self.px(14), p.ink, self.px(1.6))
            else:
                d.check_mark(x + self.px(18), icon_y, self.px(13), p.ink, self.px(2))
            cursor -= d.paragraph("BODY", size, x + self.px(40), cursor, e["text"], p.ink, inner) + self.px(6)
        if doing:
            d.pencil_icon(x + self.px(18), cursor - size * 0.95 - self.px(1), self.px(12), p.ink)
            lines = d.wrap("BODY", size, doing, inner)
            used = d.paragraph("BODY", size, x + self.px(40), cursor, doing, p.ink, inner)
            if self.blink(0.5) and lines:
                lx = x + self.px(40) + d.width_of("BODY", size, lines[-1]) + self.px(4)
                ly = cursor - used + d.line_height("BODY", size) - size * 1.05
                d.rect(lx, ly, self.px(8), size * 1.1, p.with_alpha(p.ink, 0.15))
        return h

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
            top = view_bottom + content - state.scroll
        else:
            top = view_top
        with d.clip(x + self.px(3), view_bottom - self.px(4), w - self.px(6), view_h + self.px(8)):
            for e, eh in zip(log, heights):
                if top - eh < view_top + self.px(40) and top > view_bottom - self.px(40):
                    self.entry(e, inner_x, top, inner_w)
                top -= eh + gap
            if thinking:
                self.thinking_box(inner_x, top, inner_w)
        if state.max_scroll > 0:
            # A thin pencil scroll bar on the right.
            bar_h = max(self.px(24), view_h * view_h / content)
            t = 1.0 - state.scroll / state.max_scroll
            by = view_bottom + (view_h - bar_h) * t
            d.box(x + w - self.px(9), by, self.px(4), bar_h, fill=p.with_alpha(p.ink, 0.35), radius=2)

    # -- settings (screen 24) ------------------------------------------------
    def settings_screen(self, x0, y0, x1, y1):
        w = x1 - x0
        gap = self.px(GAP)
        top = y1 - self.header(x0, y1, x1, back=True) - gap
        bottom = y0 + self.footer(x0, y0, w) + gap
        self.scroll_body(x0, bottom, w, top - bottom, self.settings_blocks(w))

    def scroll_body(self, x, y, w, h, blocks):
        """Blocks [(height, draw(x, top, w))] in a scrollable column."""
        gap = self.px(GAP)
        content = sum(bh for bh, _ in blocks) + gap * max(0, len(blocks) - 1)
        state.body_max = max(0.0, content - h)
        state.body_scroll = max(0.0, min(state.body_scroll, state.body_max))
        state.scroll_rect = (x, y, x + w, y + h)
        top = y + h + state.body_scroll
        with d.clip(x - self.px(4), y, w + self.px(14), h + self.px(4)):
            for bh, fn in blocks:
                if top - bh < y + h + self.px(20) and top > y - self.px(20):
                    fn(x, top, w)
                top -= bh + gap

    def settings_blocks(self, w):
        from . import setup_checks
        p = self.p
        prefs = self.prefs
        props = self.props
        blocks = [(self.px(20), lambda x, top, w: self.label_caps(x, top - self.px(14), "Who works with you", size=12))]
        for brain, line in (("claude", "Claude Code · your Claude plan"), ("codex", "Codex CLI · your ChatGPT plan")):
            level, message = setup_checks.results.get(brain, (None, ""))
            if level == setup_checks.OK:
                status = "✓ Installed   ✓ Signed in"
            elif level == setup_checks.RUNNING:
                status = "Checking..."
            elif level is None:
                status = "Not checked yet"
            else:
                status = message
            sw = w - self.px(110)
            h = self.px(74) + max(0, d.paragraph_height("HAND", self.px(16), status, sw) - self.px(20))
            blocks.append((h, self._brain_choice(brain, line, status, h, sw)))
        # Theme
        blocks.append((self.px(132), self._theme_card))
        # Relay options, Auto pace and pointers
        options = (("offer_handover", "Offer a hand-over when a limit is reached"),
                   ("auto_relay", "Hand over by itself in Auto"),
                   ("returning_reviews", "Returning brain reviews the other's work first"),
                   ("show_pointers", "Show pointers in the viewport"))
        tw = w - self.px(80)
        rows_h = sum(max(self.px(34), d.paragraph_height("HAND", self.px(19), t, tw) + self.px(8)) for _, t in options)
        relay_h = self.px(52) + rows_h + self.px(56)
        blocks.append((relay_h, lambda x, top, w: self._relay_card(x, top, w, relay_h, options, tw)))
        # Checks
        checks = self._check_lines()
        cw = w - self.px(70)
        checks_h = self.px(72) + sum(d.paragraph_height("HAND", self.px(18), t, cw) + self.px(6) for _, t in checks)
        if self._needs_install():
            checks_h += self.px(52)
        blocks.append((checks_h, lambda x, top, w: self._checks_card(x, top, w, checks_h, checks, cw)))
        # The lock note and Advanced
        note = "You sign in inside Claude Code and Codex. BlendWMe never sees your login."
        note_h = d.paragraph_height("HAND", self.px(18), note, w - self.px(60))

        def lock_note(x, top, w):
            lx, ly = x + self.px(14), top - self.px(20)
            d.box(lx, ly - self.px(10), self.px(16), self.px(13), border=p.ink, width=1.8, radius=2)
            d.polyline(d.circle_points(lx + self.px(8), ly + self.px(4), self.px(5), 16)[0:9], p.ink, self.px(1.8))
            d.paragraph("HAND", self.px(18), x + self.px(46), top, note, p.ink, w - self.px(60))
        blocks.append((note_h, lock_note))

        def advanced(x, top, w):
            words = "Advanced settings"
            size = self.px(17)
            tw2 = d.width_of("HAND", size, words)
            d.text("HAND", size, x + self.px(46), top - size, words, p.ink)
            d.polyline([(x + self.px(46), top - size - self.px(4)), (x + self.px(46) + tw2, top - size - self.px(4))],
                       p.ink, self.px(1.2))
            self.hit(x + self.px(40), top - self.px(28), tw2 + self.px(12), self.px(28), "advanced")
        blocks.append((self.px(28), advanced))
        if props is None or prefs is None:
            return blocks[:1]
        return blocks

    def _brain_choice(self, brain, line, status, h, sw):
        def draw(x, top, w):
            p = self.p
            props = self.props
            active = (props.brain if props else "claude") == brain
            y = top - h
            if active:
                d.box(x, y, w, h, fill=p.card, border=p.ink, width=3, radius=theme.RADIUS, shadow=p.shadow,
                      shadow_offset=4)
            else:
                self.card_box(x, y, w, h)
            rx, ry = x + self.px(22), y + h / 2
            d.circle(rx, ry, self.px(10), p.ink, self.px(2))
            if active:
                d.disc(rx, ry, self.px(6), p.ink)
            self.mark(brain, x + self.px(56), ry, self.px(9))
            tx = x + self.px(80)
            d.text("BLACK", self.px(17), tx, top - self.px(28), label(brain), p.ink)
            if active:
                bw = d.width_of("BLACK", self.px(11), "Active") + self.px(14)
                bx = tx + d.width_of("BLACK", self.px(17), label(brain)) + self.px(10)
                d.box(bx, top - self.px(32), bw, self.px(20), fill=p.accent, radius=4)
                d.text("BLACK", self.px(11), bx + self.px(7), top - self.px(26), "Active", p.on_accent)
            d.text("HAND", self.px(17), tx, top - self.px(50), line, p.ink)
            d.paragraph("HAND", self.px(16), tx, top - self.px(54), status, p.muted, sw)
            self.hit(x, y, w, h, "brain:" + brain)
        return draw

    def _theme_card(self, x, top, w):
        p = self.p
        h = self.px(132)
        y = top - h
        self.card_box(x, y, w, h)
        self.label_caps(x + self.px(18), top - self.px(30), "Theme")
        current = self.prefs.theme if self.prefs else "GRAPHITE"
        gap = self.px(12)
        sw = (w - self.px(36) - 2 * gap) / 3
        for i, (key, name) in enumerate((("GRAPHITE", "Graphite"), ("RED", "Red"), ("BLENDER", "Blender"))):
            t = theme.Palette(key)
            sx = x + self.px(18) + i * (sw + gap)
            sy = y + self.px(46)
            d.box(sx, sy, sw, self.px(44), fill=t.panel if key != "RED" else t.card, border=p.ink,
                  width=3 if key == current else 2, radius=8)
            d.rect(sx + sw / 2 - self.px(22), sy + self.px(17), self.px(28), self.px(10), t.ink)
            d.rect(sx + sw / 2 + self.px(10), sy + self.px(17), self.px(10), self.px(10), t.hot)
            cx = sx + self.px(20)
            d.circle(cx, y + self.px(24), self.px(8), p.ink, self.px(1.8))
            if key == current:
                d.disc(cx, y + self.px(24), self.px(5), p.ink)
            d.text("HAND", self.px(18), cx + self.px(14), y + self.px(18), name, p.ink)
            self.hit(sx, y + self.px(8), sw, self.px(84), "theme:" + key)

    def _relay_card(self, x, top, w, h, options, tw):
        p = self.p
        y = top - h
        self.card_box(x, y, w, h)
        self.label_caps(x + self.px(18), top - self.px(30), "Relay and pace")
        cursor = top - self.px(48)
        for prop, words in options:
            on = bool(getattr(self.prefs, prop, False))
            row_h = max(self.px(34), d.paragraph_height("HAND", self.px(19), words, tw) + self.px(8))
            self.checkbox(x + self.px(18), cursor - self.px(28), self.px(26), on)
            d.paragraph("HAND", self.px(19), x + self.px(58), cursor, words, p.ink, tw)
            self.hit(x, cursor - row_h, w, row_h, "toggle:" + prop)
            cursor -= row_h
        self.pace_slider(x + self.px(18), y + self.px(10), w - self.px(36), self.px(40))

    def _check_lines(self):
        from . import setup_checks, socket_bridge
        lines = []
        try:
            connected = socket_bridge.get().is_running()
        except Exception:
            connected = False
        lines.append((connected, "Connected to Blender, this computer only" if connected
                      else "Not connected to Blender yet. Press Run again."))
        level, message = setup_checks.results.get("mcp", (None, ""))
        if level == setup_checks.OK:
            lines.append((True, "BlendWMe tools server is ready"))
        elif level == setup_checks.RUNNING:
            lines.append((None, "Checking the tools server..."))
        elif level is not None:
            lines.append((False, message))
        lines.append((True, "Coach instructions shared by both brains"))
        return lines

    def _needs_install(self):
        from . import setup_checks
        level = setup_checks.results.get("mcp", (None, ""))[0]
        busy = setup_checks.results.get("install", (None, ""))[0] == setup_checks.RUNNING
        return level in (setup_checks.ERROR, setup_checks.WARN) and not busy

    def _checks_card(self, x, top, w, h, checks, cw):
        p = self.p
        y = top - h
        self.card_box(x, y, w, h)
        self.label_caps(x + self.px(18), top - self.px(34), "Checks")
        bw, bh = self.px(108), self.px(38)
        bx, by = x + w - bw - self.px(16), top - bh - self.px(14)
        d.box(bx, by, bw, bh, fill=p.card, border=p.ink, width=theme.BORDER_CARD, radius=8)
        tw = d.width_of("BLACK", self.px(12), "Run again")
        d.text("BLACK", self.px(12), bx + (bw - tw) / 2, by + self.px(14), "Run again", p.ink)
        self.hit(bx, by, bw, bh, "checks")
        cursor = top - self.px(62)
        for ok, words in checks:
            iy = cursor - self.px(16)
            if ok:
                d.check_mark(x + self.px(18), iy, self.px(14), p.ink, self.px(2))
            elif ok is False:
                d.cross(x + self.px(25), iy + self.px(6), self.px(10), p.hot, self.px(2.2))
            cursor -= d.paragraph("HAND", self.px(18), x + self.px(44), cursor, words, p.ink, cw) + self.px(6)
        if self._needs_install():
            self.button(x + self.px(18), y + self.px(14), w - self.px(39), self.px(40), "Install the tools server",
                        "install", primary=True, size=13)

    # -- the baton journal (screen 25) ------------------------------------------
    def journal_screen(self, x0, y0, x1, y1):
        w = x1 - x0
        gap = self.px(GAP)
        top = y1 - self.header(x0, y1, x1, back=True) - gap
        bh = self.px(48)
        self.button(x0, y0, w * 0.64, bh, "Open as Blender text", "op:blendwme.open_journal", size=14)
        self.button(x0 + w * 0.64 + self.px(12), y0, w * 0.36 - self.px(15), bh, "Export", "op:blendwme.export_journal",
                    size=14)
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
            d.text("BLACK", self.px(24), x, top - self.px(46), name, p.ink)
            d.text("HAND", self.px(18), x, top - self.px(72), "Saved with your file. Both brains read it.", p.muted)
        blocks.append((self.px(80), title))
        tw = w - self.px(36)
        brief = data.get("brief") or "No brief yet. Type what you want to make."
        brief_h = self.px(48) + d.paragraph_height("HAND", self.px(19), brief, tw) + self.px(10)

        def brief_card(x, top, w):
            self.card_box(x, top - brief_h, w, brief_h)
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
            self.label_caps(x + self.px(18), top - self.px(28), "Steps, signed")
            cursor = top - self.px(46)
            if not rows:
                d.text("HAND", self.px(18), x + self.px(18), cursor - self.px(18), "Nothing built yet.", p.muted)
            for kind, by, t, words in rows:
                if kind == "switch":
                    cy = cursor - self.px(16)
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
                self.label_caps(x + self.px(18), top - self.px(28), f"Who made what · {total} objects")
                bx, by, bw, bh = x + self.px(18), top - self.px(68), w - self.px(36), self.px(26)
                cursor = bx
                for key in ("claude", "codex", "user"):
                    part = bw * counts[key] / total
                    if part <= 0:
                        continue
                    if key == "claude":
                        d.rect(cursor, by, part, bh, p.accent)
                    elif key == "codex":
                        d.hatch(cursor, by, part, bh, p.ink, self.px(6), self.px(1.6))
                    cursor += part
                d.box(bx, by, bw, bh, border=p.ink, width=2, radius=4)
                lx = x + self.px(18)
                for key, words in (("claude", "Claude"), ("codex", "Codex"), ("user", "You")):
                    sq = self.px(18)
                    sy = top - self.px(100)
                    if key == "claude":
                        d.rect(lx, sy, sq, sq, p.accent)
                    elif key == "codex":
                        d.hatch(lx, sy, sq, sq, p.ink, self.px(5), self.px(1.4))
                    d.box(lx, sy, sq, sq, border=p.ink, width=1.6, radius=1)
                    item = f"{words} {counts[key]}"
                    d.text("HAND", self.px(18), lx + sq + self.px(6), sy + self.px(3), item, p.ink)
                    lx += sq + self.px(20) + d.width_of("HAND", self.px(18), item)
            blocks.append((self.px(112), who_card))
        # Problems and next
        problems = [(s["text"], s.get("solved_by")) for s in data.get("struggles", [])[-6:]]
        nxt = (data.get("handoffs") or [{}])[-1].get("next_steps") or []
        if problems or nxt:
            pw = w - self.px(36)
            ph = self.px(48) + sum(d.paragraph_height("HAND", self.px(18), t + (f" · fixed by {label(b)}" if b else ""),
                                                      pw) + self.px(4) for t, b in problems)
            if nxt:
                ph += self.px(36) + d.paragraph_height("HAND", self.px(18), "; ".join(nxt), pw)
            ph += self.px(10)

            def problems_card(x, top, w):
                self.card_box(x, top - ph, w, ph)
                self.label_caps(x + self.px(18), top - self.px(28), "Problems")
                cursor = top - self.px(38)
                for words, solved in problems:
                    if solved:
                        line = words + f" · fixed by {label(solved)}"
                        used = d.paragraph("HAND", self.px(18), x + self.px(18), cursor, line, p.muted, pw)
                        first = d.wrap("HAND", self.px(18), line, pw)[0]
                        sw = min(d.width_of("HAND", self.px(18), words), d.width_of("HAND", self.px(18), first))
                        sy = cursor - self.px(18) * 0.6
                        d.polyline([(x + self.px(18), sy), (x + self.px(18) + sw, sy)], p.ink, self.px(1.3))
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
