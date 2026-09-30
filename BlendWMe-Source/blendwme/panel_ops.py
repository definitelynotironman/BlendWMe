"""The panel's operators: open it, and the modal operator that handles clicks, scrolling,
typing, dragging and the keyboard shortcuts while the mouse is over the panel (or the
input has focus). Every other event goes on to Blender untouched.

Shortcuts. The editable ones live in Preferences > Keymap > Window as "BlendWMe Panel
Key" items (keymaps.py); this operator reads them from the user's keymap, so a change
there takes effect at once. They act only while the panel has focus, so they never take
a key away from Blender elsewhere.

Checked against Blender's default keymap: Enter, Shift+Enter, Up, Down, Shift+Tab,
Ctrl+L and / are used by Blender elsewhere (file browser, text editor, keyframe jump,
snapping, select linked, local view), which is why they act only on the focused panel.
Esc while the AI works is the one key taken outside the panel; it is left to Blender
while an animation plays (Esc cancels playback there). ? and Ctrl+Alt+M are free.

    Enter          send                 Shift+Enter   new line
    Esc            pause while working, otherwise close the top card
    Esc twice      stop                 Up / Down     prompt history
    Shift+Tab      next mode            Ctrl+L        clear the conversation view
    /              commands (in an empty input)       ?   all shortcuts (in an empty input)
    Ctrl+Alt+M     open BlendWMe; again: focus the input; again: minimize (anywhere)

Typing works like a Blender text field: Shift+arrows select, Ctrl+arrows jump words,
Ctrl+A selects all, Ctrl+C / X / V copy, cut and paste, Ctrl+Z undoes and Ctrl+Shift+Z
redoes. Click to place the caret, Shift+click to select, double-click to select a word.

Moving and sizing (rules R8 to R10): drag the header to move the panel in any direction.
It snaps to the edges and corners of the free viewport (hold Ctrl to place it freely);
Esc or right-click puts it back. It remembers the nearest corner and its distance from
it. The grip in the opposite corner resizes it. Double-click the header switches Full
and Compact; the square button maximizes; the line button folds it into a small bar.
"""

import time

import bpy

from . import draw2d as d
from . import panel
from .panel import EXAMPLE_PROMPTS, MIN_HEIGHT, MIN_WIDTH, MODES, WIDTH_GRID, state
from .state import PAUSED, THINKING, WORKING, session

DOUBLE_ESC = 0.6        # seconds between two Esc presses that stop the work
UNDO_LIMIT = 100

COMMANDS = {
    "/handover": "Hand the work to the other brain",
    "/look": "Look at the scene again",
    "/undo": "Undo the last step",
    "/journal": "Open the baton journal",
    "/logs": "Open the logs folder",
    "/settings": "Settings",
    "/new": "Start a new task (the scene stays)",
    "/stop": "Stop the current work",
    "/help": "Show all shortcuts",
}

HELP = ("Shortcuts: Enter sends. Shift+Enter makes a new line. Esc pauses while working, otherwise closes the "
        "top card. Esc twice stops. Up and Down bring back earlier messages. Shift+Tab changes the mode. "
        "Ctrl+L clears this view (the journal keeps everything). Type / for commands: "
        + ", ".join(COMMANDS) + ". Ctrl+Alt+M opens BlendWMe from anywhere (press it again to minimize). "
        "Drag the header to move the panel (Ctrl: no snapping, Esc: put it back); the corner grip resizes "
        "it; double-click the header for Compact. Change keys in Preferences > Keymap > Window > BlendWMe.")

# The editable panel keys: action -> default binding (keymaps.py registers these).
PANEL_KEYS = (
    ("send", {"type": "RET", "value": "PRESS"}),
    ("send", {"type": "NUMPAD_ENTER", "value": "PRESS"}),
    ("newline", {"type": "RET", "value": "PRESS", "shift": True}),
    ("escape", {"type": "ESC", "value": "PRESS"}),
    ("history_prev", {"type": "UP_ARROW", "value": "PRESS"}),
    ("history_next", {"type": "DOWN_ARROW", "value": "PRESS"}),
    ("next_mode", {"type": "TAB", "value": "PRESS", "shift": True}),
    ("clear_view", {"type": "L", "value": "PRESS", "ctrl": True}),
)

_last_esc = [0.0]


def _redraw():
    """Redraw the host viewport's main region only (rule R6)."""
    session.changed_flag = True
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.as_pointer() == state.area_ptr:
                    for region in area.regions:
                        if region.type == "WINDOW":
                            region.tag_redraw()
    except Exception:
        pass


def host(context):
    """The host 3D Viewport area and its main region in this window, or (None, None)."""
    for area in context.window.screen.areas:
        if area.as_pointer() == state.area_ptr and area.type == "VIEW_3D":
            for region in area.regions:
                if region.type == "WINDOW":
                    return area, region
    return None, None


def panel_alive():
    """True while the panel's modal operator runs in some window."""
    try:
        for window in bpy.context.window_manager.windows:
            for op in window.modal_operators:
                if "BLENDWME_OT_panel" in op.bl_idname or op.bl_idname == "blendwme.panel":
                    return True
    except Exception:
        pass
    return False


def user_bindings():
    """[(action, type, value, shift, ctrl, alt)] from the user's keymap (falls back to the defaults)."""
    out = []
    try:
        km = bpy.context.window_manager.keyconfigs.user.keymaps.get("Window")
        for kmi in (km.keymap_items if km else []):
            if kmi.idname == "blendwme.panel_key" and kmi.active:
                out.append((kmi.properties.action, kmi.type, kmi.value, kmi.shift, kmi.ctrl, kmi.alt))
    except Exception:
        out = []
    if not out:
        for action, key in PANEL_KEYS:
            out.append((action, key["type"], key["value"], key.get("shift", False), key.get("ctrl", False),
                        key.get("alt", False)))
    return out


def match(event):
    for action, kind, value, shift, ctrl, alt in user_bindings():
        if (event.type == kind and event.value == value and bool(event.shift) == bool(shift)
                and bool(event.ctrl) == bool(ctrl) and bool(event.alt) == bool(alt)):
            return action
    return None


# ----------------------------------------------------------------------
# Actions (clicks and keys end up here)
# ----------------------------------------------------------------------
def _config(context):
    from . import prefs
    return prefs.gather_config(context)


def send_text(context, text):
    from .relay import relay
    text = text.strip()
    if not text:
        return
    if text.startswith("/") and text.split()[0] in COMMANDS:
        run_command(context, text.split()[0])
        return
    if not state.history or state.history[-1] != text:
        state.history.append(text)
    state.history_at = None
    props = context.window_manager.blendwme
    props.prompt = text
    try:
        bpy.ops.blendwme.send()
    except RuntimeError as e:
        session.add("info", str(e))
    if not relay.is_running() and props.prompt:
        props.prompt = ""
    state.scroll = 0.0


settings_asked = [0.0]      # when Settings was last opened (tests read it)


def open_settings(context):
    """Settings are Blender's own widgets in a popover (rule R7). Returns True when shown;
    falls back to Preferences > Add-ons > BlendWMe."""
    settings_asked[0] = time.time()
    if bpy.app.background or getattr(context, "window", None) is None:
        return False            # no window to show a popover in (Blender crashes if asked)
    attempts = (lambda: bpy.ops.wm.call_panel(name="BWM_PT_settings", keep_open=True),
                lambda: bpy.ops.preferences.addon_show(module=__package__),
                lambda: bpy.ops.screen.userpref_show())
    for attempt in attempts:
        try:
            attempt()
            return True
        except Exception:
            continue
    session.add("info", "Settings could not open here. Find them in Edit > Preferences > Add-ons > BlendWMe.")
    return False


def run_command(context, name):
    from .relay import relay
    if name == "/handover":
        target = relay.other(relay.running or relay.active)
        bpy.ops.blendwme.hand_over(target=target)
    elif name == "/look":
        send_text(context, "Look at the scene again from a few angles and tell me briefly what you see.")
    elif name == "/undo":
        bpy.ops.blendwme.undo_step()
    elif name == "/journal":
        state.screen = "journal"
    elif name == "/logs":
        bpy.ops.blendwme.open_logs()
    elif name == "/settings":
        open_settings(context)
    elif name == "/new":
        relay.reset()
    elif name == "/stop":
        bpy.ops.blendwme.stop()
    elif name == "/help":
        session.add("info", HELP)


def close_top_card():
    """Esc or ✕: close the card on top. Returns True when something was closed."""
    card = session.card
    if card and not card.get("hidden"):
        kind = card.get("kind")
        if kind == "done":
            collapse_done(card)
        elif kind in ("limit", "stuck", "pause") or not _waiting():
            session.set_card(None)
        else:
            # The AI is still waiting for you: hide the card, the status keeps saying "Your turn".
            card["hidden"] = True
            session.changed_flag = True
        return True
    return False


def _waiting():
    from .state import ASKING, WAIT_NEXT, WAIT_USER
    return session.status in (WAIT_USER, WAIT_NEXT, ASKING)


def collapse_done(card=None):
    """The Done card becomes one line in the conversation: "✓ Done: <summary>"."""
    card = card or session.card
    if not card or card.get("kind") != "done":
        return
    summary = (card.get("text") or card.get("title") or "").strip()
    session.set_card(None)
    session.add("done", "Done" + (": " + summary if summary else ""))


# ----------------------------------------------------------------------
# Views: Full, Compact, Minimized, with the frame animation (rule R10)
# ----------------------------------------------------------------------
def set_view(minimized=None, compact=None):
    """Change the view. The frame animates from where it was (200 ms out, 150 ms in);
    with Smooth View at 0 or reduced motion it changes at once."""
    before = state.view()
    if minimized is not None:
        state.minimized = minimized
    if compact is not None:
        state.compact = compact
    after = state.view()
    if after != before and panel.smooth_view_on() and state.last_rect[2] > state.last_rect[0]:
        growing = before == "mini" or (before == "compact" and after == "full")
        state.anim = {"t0": time.time(), "dur": panel.EXPAND_TIME if growing else panel.COLLAPSE_TIME,
                      "from": state.last_rect, "content": state.last_content}
        start_anim_timer()
    else:
        state.anim = None
    _redraw()


def _anim_tick():
    """60 fps while the frame moves, then the timer stops (rule R10)."""
    _redraw()
    anim = state.anim
    if not anim:
        return None
    if time.time() - anim["t0"] > anim["dur"] + 0.05:
        state.anim = None
        _redraw()
        return None
    return 1 / 60


def start_anim_timer():
    if not bpy.app.timers.is_registered(_anim_tick):
        bpy.app.timers.register(_anim_tick, first_interval=0.0)


def minimize():
    """Fold the panel into the small bar. Nothing stops: the session, the brain and the plan go on."""
    state.focused = False
    state.drag = None
    set_view(minimized=True)


def restore(focus=True):
    state.focused = focus
    set_view(minimized=False)


def toggle_compact():
    if state.minimized:
        restore()
    elif state.auto_compact:
        state.compact = False           # Compact because of space; nothing to toggle
    else:
        set_view(compact=not state.compact)


def toggle_maximize(context):
    """The square button: full height, or back to the height before. In Compact: open Full."""
    from . import prefs
    p = prefs.addon_prefs(context)
    if state.view() == "compact":
        set_view(compact=False)
        return
    if p is not None:
        p.panel_max = not p.panel_max
        set_view()


def take_control(context):
    """Take control: pause after the current action, and get the panel out of the way."""
    from . import prefs
    bpy.ops.blendwme.pause()
    p = prefs.addon_prefs(context)
    if p is None or p.minimize_on_take_control:
        minimize()


def reset_place(p):
    """Back in the top right corner, tight against the edge, the design size."""
    p.panel_corner = "TR"
    p.panel_off_x = p.panel_off_y = 0.0
    p.panel_width = p.panel_height = 0.0
    p.panel_max = False


# ----------------------------------------------------------------------
# Dragging and resizing (rules R8, R9)
# ----------------------------------------------------------------------
def start_drag(context, kind, event):
    """Begin dragging: "drag" (the header), "mini" (the minimized bar) or "grip" (the corner)."""
    from . import prefs
    area, region = host(context)
    p = prefs.addon_prefs(context)
    if region is None or p is None:
        return
    rect = state.frame if state.frame[2] > state.frame[0] else panel.placement(area, region)
    if kind == "mini":
        rect = state.header if state.header[2] > state.header[0] else rect
    state.drag = {"kind": kind, "mx": event.mouse_x, "my": event.mouse_y, "moved": False, "rect": rect,
                  "saved": (p.panel_corner, p.panel_off_x, p.panel_off_y, p.panel_width, p.panel_height,
                            p.panel_max)}


def move_drag(context, event):
    from . import prefs
    drag = state.drag
    area, region = host(context)
    p = prefs.addon_prefs(context)
    if not (drag and region and p):
        return
    dx, dy = event.mouse_x - drag["mx"], event.mouse_y - drag["my"]
    if not drag["moved"] and abs(dx) + abs(dy) < 4:
        return                       # a click, not a drag (yet)
    drag["moved"] = True
    s = panel.scale()
    bounds = panel.usable(area, region)
    x0, y0, x1, y1 = drag["rect"]
    corner = drag["saved"][0]
    if drag["kind"] == "grip":
        # The panel's own corner stays; the opposite corner follows the mouse.
        right, top = corner in ("TR", "BR"), corner in ("TR", "TL")
        w = (x1 - x0) + (-dx if right else dx)
        h = (y1 - y0) + (-dy if top else dy)
        ul, ub, ur, ut = bounds
        w = max(MIN_WIDTH * s, min(w, ur - ul))
        h = max(MIN_HEIGHT * s, min(h, ut - ub))
        p.panel_width = round(w / s / WIDTH_GRID) * WIDTH_GRID         # an 8-unit grid
        p.panel_height = h / s
        p.panel_max = False
        return
    # Both axes, every event (R8).
    rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
    if not event.ctrl:
        rect = panel.snap_rect(rect, bounds, area, region, s)
    rect = panel.clamp_rect(rect, bounds)
    corner, ox, oy = panel.to_corner(rect, bounds)
    p.panel_corner = corner
    p.panel_off_x = ox / s
    p.panel_off_y = oy / s


def cancel_drag(context):
    """Esc or right-click while dragging: put the panel back where it started."""
    from . import prefs
    drag, state.drag = state.drag, None
    p = prefs.addon_prefs(context)
    if drag and p:
        (p.panel_corner, p.panel_off_x, p.panel_off_y, p.panel_width, p.panel_height,
         p.panel_max) = drag["saved"]
    _restore_cursor(context)


def end_drag(context):
    """Let go. A click (no movement) on the minimized bar opens the panel."""
    drag, state.drag = state.drag, None
    _restore_cursor(context)
    if not drag:
        return
    if not drag["moved"]:
        if drag["kind"] == "mini":
            restore()
        else:
            state.focused = False


def _restore_cursor(context):
    try:
        context.window.cursor_modal_restore()
    except Exception:
        pass
    state.hover_cursor = False


def double_click(context, action):
    """Double-click the header: Full or Compact. The grip: the design size. The bar: open."""
    from . import prefs
    p = prefs.addon_prefs(context)
    state.drag = None
    if action == "grip":
        if p is not None:
            p.panel_width = p.panel_height = 0.0
            p.panel_max = False
    elif action == "mini":
        restore()
    else:
        toggle_compact()


def next_mode(context):
    props = context.window_manager.blendwme
    keys = [k for k, _ in MODES]
    props.mode = keys[(keys.index(props.mode) + 1) % len(keys)] if props.mode in keys else keys[0]


def escape(context):
    """Esc: pause while working (twice: stop), otherwise close the top card or leave the input."""
    from .relay import relay
    now = time.time()
    working = relay.is_running() and session.status in (THINKING, WORKING, PAUSED)
    if working and now - _last_esc[0] < DOUBLE_ESC:
        _last_esc[0] = 0.0
        bpy.ops.blendwme.stop()
        return True
    _last_esc[0] = now
    if relay.is_running() and session.status in (THINKING, WORKING) and not session.pause_flag.is_set():
        bpy.ops.blendwme.pause()
        return True
    if close_top_card():
        return True
    if state.screen != "main":
        state.screen = "main"
        return True
    if state.focused:
        state.focused = False
        state.anchor = None
        return True
    return False


def do_action(context, action, event=None):
    from . import prefs
    from .relay import relay
    props = context.window_manager.blendwme
    p = prefs.addon_prefs(context)
    kind, _, arg = action.partition(":")
    if kind == "panel":
        state.focused = False
    elif kind == "focus_input":
        state.focused = True
        if event is not None and getattr(event, "mouse_x", None) is not None:
            place_caret(context, event)
        else:
            state.cursor = len(state.text)
            state.anchor = None
    elif kind == "mode":
        props.mode = arg
    elif kind == "screen":
        state.screen = arg if arg in ("main", "journal") else "main"
        state.body_scroll = 0.0
        if arg == "settings":
            open_settings(context)
    elif kind == "settings":
        from . import setup_checks
        if not setup_checks.results:
            bpy.ops.blendwme.setup_check(which="ALL")
        open_settings(context)
    elif kind == "example":
        set_text(EXAMPLE_PROMPTS.get(arg, arg))
        state.focused = True
    elif kind == "chip":
        if arg.startswith("/"):
            run_command(context, arg)
        elif arg.startswith("teach:"):
            bpy.ops.blendwme.teach(choice=arg.split(":", 1)[1])
        else:
            send_text(context, arg)
    elif kind == "teach":
        bpy.ops.blendwme.teach(choice=arg)
    elif kind == "op":
        getattr(getattr(bpy.ops, arg.split(".")[0]), arg.split(".")[1])()
    elif kind == "handover":
        target = arg or relay.other(relay.running or relay.active)
        bpy.ops.blendwme.hand_over(target=target)
        if session.card and session.card.get("kind") == "limit":
            session.set_card(None)
    elif kind == "close_card":
        close_top_card()
    elif kind == "minimize":
        minimize()
    elif kind == "maximize":
        toggle_maximize(context)
    elif kind == "expand":
        set_view(compact=False)
        if state.auto_compact:
            session.add("info", "The viewport is too small for the whole panel. Make the window bigger or "
                                "lower the Panel size in Settings.")
    elif kind == "take_control":
        take_control(context)
    elif kind == "panel_reset" and p:
        reset_place(p)
    elif kind == "size" and p:
        prop, step = arg.split(":")
        rna = p.bl_rna.properties[prop]
        setattr(p, prop, max(rna.hard_min, min(rna.hard_max, getattr(p, prop) + int(step))))
    elif kind == "read_more":
        state.expanded.add(int(arg))
    elif kind == "pointers_again":
        from . import overlay
        overlay.restart_pointers()
    elif kind == "brain":
        props.brain = arg
    elif kind == "theme" and p:
        p.theme = arg
    elif kind == "toggle" and p:
        setattr(p, arg, not getattr(p, arg))
        prefs.sync_session(context)
    elif kind == "checks":
        bpy.ops.blendwme.setup_check(which="ALL")
    elif kind == "install":
        from . import setup_checks
        setup_checks.run_in_background(["install", "mcp"], _config(context))
    elif kind == "advanced":
        try:
            bpy.ops.preferences.addon_show(module=__package__)
        except Exception:
            bpy.ops.screen.userpref_show()
    elif kind == "slider" and p and event is not None:
        x0, x1 = float(arg.split(":")[0]), float(arg.split(":")[1])
        state.slider = (x0, x1)
        set_slider(context, event)
    _redraw()


def set_slider(context, event):
    from . import prefs
    p = prefs.addon_prefs(context)
    area, region = host(context)
    if not (p and region and state.slider):
        return
    x = event.mouse_x - region.x
    x0, x1 = state.slider
    t = max(0.0, min(1.0, (x - x0) / max(1.0, x1 - x0)))
    p.pace = round(t * 10.0 * 2) / 2          # half-second steps
    prefs.sync_session(context)


# ----------------------------------------------------------------------
# Typing: a small text field that behaves like Blender's own (rule R7)
# ----------------------------------------------------------------------
_typing = [False]          # in the middle of typing a word (one undo step per word)


def _snapshot():
    state.undo.append((state.text, state.cursor))
    del state.undo[:-UNDO_LIMIT]
    state.redo.clear()


def set_text(text, cursor=None, undoable=True):
    if undoable and text != state.text:
        _snapshot()
    state.text = text
    state.cursor = len(text) if cursor is None else max(0, min(len(text), cursor))
    state.anchor = None


def _replace_selection(insert):
    """Replace the selection (or insert at the caret)."""
    t, c = state.text, state.cursor
    sel = panel.selection()
    a, b = sel if sel else (c, c)
    state.text = t[:a] + insert + t[b:]
    state.cursor = a + len(insert)
    state.anchor = None


def _word_left(t, c):
    i = c
    while i > 0 and t[i - 1] in " \n":
        i -= 1
    while i > 0 and t[i - 1] not in " \n":
        i -= 1
    return i


def _word_right(t, c):
    i = c
    while i < len(t) and t[i] in " \n":
        i += 1
    while i < len(t) and t[i] not in " \n":
        i += 1
    return i


def _move(to, extend):
    if extend:
        if state.anchor is None:
            state.anchor = state.cursor
    else:
        state.anchor = None
    state.cursor = max(0, min(len(state.text), to))


def type_key(context, event):
    """Edit the input. Returns True when the key was used."""
    t, c = state.text, state.cursor
    sel = panel.selection()
    shift = bool(event.shift)
    if not (event.unicode and not event.ctrl and not event.alt and event.unicode.isprintable()):
        _typing[0] = False
    if event.ctrl and event.type == "Z":
        if shift:
            if state.redo:
                state.undo.append((state.text, state.cursor))
                state.text, state.cursor = state.redo.pop()
                state.anchor = None
        elif state.undo:
            state.redo.append((state.text, state.cursor))
            state.text, state.cursor = state.undo.pop()
            state.anchor = None
        return True
    if event.ctrl and event.type == "Y":
        if state.redo:
            state.undo.append((state.text, state.cursor))
            state.text, state.cursor = state.redo.pop()
            state.anchor = None
        return True
    if event.type == "BACK_SPACE":
        _snapshot()
        if sel:
            _replace_selection("")
        elif event.ctrl:
            start = _word_left(t, c)
            state.text, state.cursor = t[:start] + t[c:], start
        elif c:
            state.text, state.cursor = t[:c - 1] + t[c:], c - 1
        return True
    if event.type == "DEL":
        _snapshot()
        if sel:
            _replace_selection("")
        elif event.ctrl:
            end = _word_right(t, c)
            state.text = t[:c] + t[end:]
        else:
            state.text = t[:c] + t[c + 1:]
        return True
    if event.type == "LEFT_ARROW":
        if sel and not shift:
            _move(sel[0], False)
        else:
            _move(_word_left(t, c) if event.ctrl else c - 1, shift)
        return True
    if event.type == "RIGHT_ARROW":
        if sel and not shift:
            _move(sel[1], False)
        else:
            _move(_word_right(t, c) if event.ctrl else c + 1, shift)
        return True
    if event.type == "HOME":
        _move(0 if event.ctrl else t.rfind("\n", 0, c) + 1, shift)
        return True
    if event.type == "END":
        nl = t.find("\n", c)
        _move(len(t) if (event.ctrl or nl == -1) else nl, shift)
        return True
    if event.ctrl and event.type == "A":
        state.anchor, state.cursor = 0, len(t)
        return True
    if event.ctrl and event.type in ("C", "X"):
        context.window_manager.clipboard = t[sel[0]:sel[1]] if sel else t
        if event.type == "X":
            _snapshot()
            if sel:
                _replace_selection("")
            else:
                state.text, state.cursor = "", 0
        return True
    if event.ctrl and event.type == "V":
        paste = context.window_manager.clipboard.replace("\r\n", "\n")
        _snapshot()
        _replace_selection(paste)
        return True
    if event.unicode and not event.ctrl and not event.alt and event.unicode.isprintable():
        if not t and event.unicode == "?":
            session.add("info", HELP)
            return True
        # Typing is one undo step per word.
        if sel or event.unicode == " " or not _typing[0]:
            _snapshot()
        _typing[0] = event.unicode != " "
        _replace_selection(event.unicode)
        return True
    return False


def caret_at(context, event):
    """The text index under the mouse in the input, or None."""
    layout = state.input_layout
    area, region = host(context)
    if not layout or region is None:
        return None
    tx, top, lh, size, first, lines = layout
    x = event.mouse_x - region.x
    y = event.mouse_y - region.y
    row = int((top - y) // lh) + first if lh else 0
    row = max(0, min(len(lines) - 1, row))
    a, b = lines[row]
    best, best_d = a, abs(x - tx)
    for i in range(a, b + 1):
        cx = tx + d.width_of("HAND", size, state.text[a:i])
        if abs(cx - x) < best_d:
            best, best_d = i, abs(cx - x)
    return best


def place_caret(context, event):
    i = caret_at(context, event)
    if i is None:
        state.cursor = len(state.text)
        state.anchor = None
        return
    if event.value == "DOUBLE_CLICK":
        t = state.text
        start, end = _word_left(t, min(len(t), i + 1)), _word_right(t, i)
        state.anchor, state.cursor = start, end
    elif event.shift:
        if state.anchor is None:
            state.anchor = state.cursor
        state.cursor = i
    else:
        state.anchor = None
        state.cursor = i


def history(step):
    if not state.history:
        return
    if state.history_at is None:
        state.history_at = len(state.history)
    state.history_at = max(0, min(len(state.history), state.history_at + step))
    set_text(state.history[state.history_at] if state.history_at < len(state.history) else "", undoable=False)


# ----------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------
def _in(rect, x, y):
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


class BWM_OT_panel(bpy.types.Operator):
    bl_idname = "blendwme.panel"
    bl_label = "BlendWMe Panel"
    bl_description = "The BlendWMe panel in the 3D Viewport"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        if panel_alive():
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        from . import theme
        if not state.open or theme.native():
            state.open = False
            _restore_cursor(context)
            _redraw()
            return {"FINISHED"}
        area, region = host(context)
        if region is None:
            return {"PASS_THROUGH"}
        x = event.mouse_x - region.x
        y = event.mouse_y - region.y
        inside = _in(state.rect, x, y) and 0 <= x <= region.width and 0 <= y <= region.height

        # Moving or resizing the panel
        if state.drag:
            if event.type in ("MOUSEMOVE", "LEFT_CTRL", "RIGHT_CTRL"):
                move_drag(context, event)
                _redraw()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                end_drag(context)
                _redraw()
                return {"RUNNING_MODAL"}
            if event.type in ("ESC", "RIGHTMOUSE") and event.value == "PRESS":
                cancel_drag(context)
                _redraw()
                return {"RUNNING_MODAL"}
            return {"RUNNING_MODAL"}

        # Dragging the pace slider
        if state.slider:
            if event.type == "MOUSEMOVE":
                set_slider(context, event)
                _redraw()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                state.slider = None
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            # The hand cursor over the header says "you can move this".
            over = inside and not state.anim and _in(state.header, x, y)
            if over and not state.hover_cursor:
                try:
                    context.window.cursor_modal_set("HAND")
                    state.hover_cursor = True
                except Exception:
                    pass
            elif not over and state.hover_cursor:
                _restore_cursor(context)
            return {"PASS_THROUGH"}

        if event.type in ("LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE"):
            if not inside:
                if event.value == "PRESS" and state.focused:
                    state.focused = False
                    state.anchor = None
                    _redraw()
                return {"PASS_THROUGH"}
            if event.type == "LEFTMOUSE" and event.value in ("PRESS", "DOUBLE_CLICK"):
                for hx0, hy0, hx1, hy1, action in reversed(state.hits):
                    if hx0 <= x <= hx1 and hy0 <= y <= hy1:
                        try:
                            if action in ("drag", "mini", "grip") and event.value == "DOUBLE_CLICK":
                                double_click(context, action)
                            elif action in ("drag", "mini", "grip"):
                                start_drag(context, action, event)
                                context.window.cursor_modal_set("SCROLL_XY")
                                state.hover_cursor = True
                            else:
                                do_action(context, action, event)
                        except Exception as e:
                            session.add("info", f"That did not work: {e}")
                        break
                _redraw()
            return {"RUNNING_MODAL"}

        if state.minimized and not relay_running_escape(context, event):
            return {"PASS_THROUGH"}         # the bar only takes clicks; keys belong to Blender

        if event.type in ("WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN") and inside:
            if event.type == "TRACKPADPAN":
                step = event.mouse_y - event.mouse_prev_y
            else:
                step = panel.scale() * 48 * (1 if event.type == "WHEELUPMOUSE" else -1)
            if state.screen == "main":
                state.scroll = max(0.0, min(state.max_scroll, state.scroll + step))
            else:
                state.body_scroll = max(0.0, min(state.body_max, state.body_scroll - step))
            _redraw()
            return {"RUNNING_MODAL"}

        if event.value not in ("PRESS", "CLICK"):
            return {"RUNNING_MODAL"} if (state.focused and event.type not in ("TIMER", "TIMER_REPORT",
                                                                              "WINDOW_DEACTIVATE")) else {"PASS_THROUGH"}

        from .relay import relay
        action = match(event)
        # Esc pauses the AI from anywhere in the viewport, except while an animation plays:
        # there Blender's Esc cancels the playback (the only clash in the default keymap).
        playing = bool(context.screen and context.screen.is_animation_playing)
        if action == "escape" and (state.focused or inside or (relay.is_running() and not playing)):
            if escape(context):
                _redraw()
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if not (state.focused or inside):
            return {"PASS_THROUGH"}

        used = True
        if action == "send" and state.focused:
            text = state.text
            set_text("", undoable=False)
            state.undo.clear()
            state.redo.clear()
            send_text(context, text)
        elif action == "newline" and state.focused:
            _snapshot()
            _replace_selection("\n")
        elif action == "history_prev" and state.focused and "\n" not in state.text:
            history(-1)
        elif action == "history_next" and state.focused and "\n" not in state.text:
            history(1)
        elif action == "next_mode":
            next_mode(context)
        elif action == "clear_view":
            state.cleared_at = time.time()
            state.scroll = 0.0
        elif state.focused:
            used = type_key(context, event)
        elif event.unicode and event.unicode.isprintable() and not event.ctrl and not event.alt:
            # Typing over the panel starts typing in the input.
            state.focused = True
            used = type_key(context, event)
        else:
            used = False
        if used:
            _redraw()
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def cancel(self, context):
        _restore_cursor(context)


def relay_running_escape(context, event):
    """While minimized only Esc still matters: it pauses the AI (unless an animation plays)."""
    from .relay import relay
    playing = bool(context.screen and context.screen.is_animation_playing)
    return match(event) == "escape" and relay.is_running() and not playing


class BWM_OT_panel_reset(bpy.types.Operator):
    bl_idname = "blendwme.panel_reset"
    bl_label = "Put the Panel Back on the Right"
    bl_description = "Put the BlendWMe panel back in the top right corner at its normal size"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from . import prefs
        p = prefs.addon_prefs(context)
        if p:
            reset_place(p)
        _redraw()
        return {"FINISHED"}


class BWM_OT_panel_key(bpy.types.Operator):
    """A panel shortcut. Listed in Preferences > Keymap > Window so it can be changed;
    the panel reads the binding from there and acts on it while it has focus."""
    bl_idname = "blendwme.panel_key"
    bl_label = "BlendWMe Panel Key"
    bl_options = {"INTERNAL"}

    action: bpy.props.EnumProperty(items=[
        ("send", "Send", "Send the message"),
        ("newline", "New Line", "A new line in the message"),
        ("escape", "Pause / Close", "Pause while working, otherwise close the top card. Twice: stop"),
        ("history_prev", "Earlier Message", "Bring back an earlier message"),
        ("history_next", "Later Message", "Go forward in the message history"),
        ("next_mode", "Next Mode", "Auto, Teach"),
        ("clear_view", "Clear View", "Clear the conversation view (the journal keeps everything)"),
    ])

    @classmethod
    def poll(cls, context):
        # The panel's modal operator handles the key; the keymap item only holds the binding.
        return False

    def execute(self, context):
        return {"CANCELLED"}


def open_panel(context, area=None):
    """Open the panel in a 3D Viewport (the one under the mouse, or the first) and start the modal."""
    if area is None or area.type != "VIEW_3D":
        area = context.area if context.area and context.area.type == "VIEW_3D" else None
    if area is None:
        area = next((a for a in context.window.screen.areas if a.type == "VIEW_3D"), None)
    if area is None:
        return False
    state.open = True
    state.area_ptr = area.as_pointer()
    state.focused = True
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if not panel_alive():
        with context.temp_override(window=context.window, area=area, region=region):
            bpy.ops.blendwme.panel("INVOKE_DEFAULT")
    area.tag_redraw()
    return True


def close_panel():
    state.open = False
    state.focused = False
    state.minimized = False
    state.compact = False
    state.drag = None
    state.anim = None
    _redraw()


def reopen_after_load():
    """Timer after a file load: Blender ended the modal operator; start it again."""
    if not state.open:
        return None
    try:
        context = bpy.context
        window = context.window_manager.windows[0]
        area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None)
        if area is None:
            state.open = False
            return None
        state.area_ptr = area.as_pointer()
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if not panel_alive():
            with context.temp_override(window=window, area=area, region=region):
                bpy.ops.blendwme.panel("INVOKE_DEFAULT")
    except Exception as e:
        print("BlendWMe: could not reopen the panel:", e)
    return None


classes = (BWM_OT_panel, BWM_OT_panel_key, BWM_OT_panel_reset)
