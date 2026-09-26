"""The panel's operators: open it, and the modal operator that handles clicks, scrolling,
typing and the keyboard shortcuts while the mouse is over the panel (or the input
has focus). Every other event goes on to Blender untouched.

Shortcuts. The editable ones live in Preferences > Keymap >
Window as "BlendWMe Panel Key" items (keymaps.py); this operator reads them from the
user's keymap, so a change there takes effect at once. They act only while the panel
has focus, so they never take a key away from Blender elsewhere.

Checked against Blender 4.2's default keymap: Enter, Shift+Enter, Up, Down, Shift+Tab,
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

Moving and sizing: drag the logo row to move the panel; let go near the right edge
and it docks there again (double-click the logo row does the same). The grip in the
bottom left corner changes its width and height. The square button beside the
status folds it into a small bar; the work goes on, and a click on the bar opens it.
"""

import time

import bpy

from . import draw2d as d
from . import panel
from .panel import EXAMPLE_PROMPTS, MODES, MIN_HEIGHT, MIN_WIDTH, SNAP, state
from .state import PAUSED, THINKING, WORKING, session

DOUBLE_ESC = 0.6        # seconds between two Esc presses that stop the work

COMMANDS = {
    "/handover": "Hand the work to the other brain",
    "/look": "Look at the scene again",
    "/undo": "Undo the last step",
    "/journal": "Open the baton journal",
    "/logs": "Open the logs folder",
    "/settings": "Brains and settings",
    "/new": "Start a new task (the scene stays)",
    "/stop": "Stop the current work",
    "/help": "Show all shortcuts",
}

HELP = ("Shortcuts: Enter sends. Shift+Enter makes a new line. Esc pauses while working, otherwise closes the "
        "top card. Esc twice stops. Up and Down bring back earlier messages. Shift+Tab changes the mode. "
        "Ctrl+L clears this view (the journal keeps everything). Type / for commands: "
        + ", ".join(COMMANDS) + ". Ctrl+Alt+M opens BlendWMe from anywhere (press it again to minimize). "
        "Drag the logo row to move the panel; the corner grip resizes it. Change keys in Preferences > Keymap "
        "> Window > BlendWMe.")

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
    session.changed_flag = True
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.as_pointer() == state.area_ptr:
                    area.tag_redraw()
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
        state.screen = "settings"
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


def minimize():
    """Fold the panel into the small bar. Nothing stops: the session, the brain and the plan go on."""
    state.minimized = True
    state.focused = False
    state.drag = None
    _redraw()


def restore(focus=True):
    state.minimized = False
    state.focused = focus
    _redraw()


def take_control(context):
    """Take control: pause after the current action, and get the panel out of the way."""
    from . import prefs
    bpy.ops.blendwme.pause()
    p = prefs.addon_prefs(context)
    if p is None or p.minimize_on_take_control:
        minimize()


def reset_place(p):
    """Back on the right, full height, the design width."""
    p.panel_dock = "RIGHT"
    p.panel_gap_right = p.panel_gap_top = 0.0
    p.panel_width = p.panel_height = 0.0


def start_drag(context, kind, event):
    """Begin dragging: "drag" (the logo row), "mini" (the minimized bar) or "grip" (the corner)."""
    from . import prefs
    area, region = host(context)
    p = prefs.addon_prefs(context)
    if region is None or p is None:
        return
    x0, y0, x1, y1 = panel.placement(area, region)
    ax1, ay1, _, _ = panel.anchor(area, region)
    state.drag = {"kind": kind, "mx": event.mouse_x, "my": event.mouse_y, "moved": False,
                  "gap_x": ax1 - x1, "gap_y": ay1 - y1, "w": x1 - x0, "h": y1 - y0,
                  "auto_height": not p.panel_height}


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
    ax1, ay1, min_x, min_y = panel.anchor(area, region)
    if drag["kind"] == "grip":
        # The top right corner stays; the bottom left corner follows the mouse.
        w = max(MIN_WIDTH * s, min(drag["w"] - dx, ax1 - drag["gap_x"] - min_x))
        h = max(MIN_HEIGHT * s, min(drag["h"] - dy, ay1 - drag["gap_y"] - min_y))
        p.panel_width = w / s
        p.panel_height = h / s
        return
    if drag["auto_height"] and not p.panel_height:
        p.panel_height = drag["h"] / s           # a full-height panel keeps its height while it moves
    gap_x = max(0.0, min(drag["gap_x"] - dx, ax1 - min_x - drag["w"]))
    gap_y = max(0.0, min(drag["gap_y"] - dy, ay1 - min_y - drag["h"]))
    p.panel_dock = "FREE"
    p.panel_gap_right = gap_x / s
    p.panel_gap_top = gap_y / s


def end_drag(context):
    """Let go. Near the right edge it docks there; a click on the minimized bar opens the panel."""
    from . import prefs
    drag, state.drag = state.drag, None
    if not drag:
        return
    try:
        context.window.cursor_modal_restore()
    except Exception:
        pass
    p = prefs.addon_prefs(context)
    if not drag["moved"]:
        if drag["kind"] == "mini":
            restore()
        else:
            state.focused = False
        return
    if p and drag["kind"] in ("drag", "mini") and p.panel_dock == "FREE" and p.panel_gap_right < SNAP:
        p.panel_dock = "RIGHT"
        p.panel_gap_right = p.panel_gap_top = 0.0
        # Back to full height when it was full height before it moved (not when you resized it).
        area, region = host(context)
        if region is not None:
            _, ay1, _, min_y = panel.anchor(area, region)
            if drag["auto_height"] or p.panel_height * panel.scale() >= ay1 - min_y - 2:
                p.panel_height = 0.0


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
        state.cursor = len(state.text)
    elif kind == "mode":
        props.mode = arg
    elif kind == "screen":
        state.screen = arg
        state.body_scroll = 0.0
        if arg == "settings":
            from . import setup_checks
            if not setup_checks.results:
                bpy.ops.blendwme.setup_check(which="ALL")
    elif kind == "example":
        state.text = EXAMPLE_PROMPTS.get(arg, arg)
        state.cursor = len(state.text)
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
# Typing
# ----------------------------------------------------------------------
def type_key(context, event):
    """Edit the input. Returns True when the key was used."""
    t, c = state.text, state.cursor
    if event.type == "BACK_SPACE":
        if event.ctrl:
            start = t.rstrip()[:c].rfind(" ") + 1 if c else 0
            state.text, state.cursor = t[:start] + t[c:], start
        elif c:
            state.text, state.cursor = t[:c - 1] + t[c:], c - 1
        return True
    if event.type == "DEL":
        state.text = t[:c] + t[c + 1:]
        return True
    if event.type == "LEFT_ARROW":
        state.cursor = max(0, c - 1)
        return True
    if event.type == "RIGHT_ARROW":
        state.cursor = min(len(t), c + 1)
        return True
    if event.type == "HOME":
        state.cursor = 0
        return True
    if event.type == "END":
        state.cursor = len(t)
        return True
    if event.ctrl and event.type == "V":
        paste = context.window_manager.clipboard.replace("\r\n", "\n")
        state.text, state.cursor = t[:c] + paste + t[c:], c + len(paste)
        return True
    if event.ctrl and event.type == "A":
        state.cursor = len(t)
        return True
    if event.ctrl and event.type == "C":
        context.window_manager.clipboard = t
        return True
    if event.unicode and not event.ctrl and not event.alt and event.unicode.isprintable():
        if not t and event.unicode == "?":
            session.add("info", HELP)
            return True
        state.text, state.cursor = t[:c] + event.unicode + t[c:], c + len(event.unicode)
        return True
    return False


def history(step):
    if not state.history:
        return
    if state.history_at is None:
        state.history_at = len(state.history)
    state.history_at = max(0, min(len(state.history), state.history_at + step))
    state.text = state.history[state.history_at] if state.history_at < len(state.history) else ""
    state.cursor = len(state.text)


# ----------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------
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
        if not state.open:
            _redraw()
            return {"FINISHED"}
        area, region = host(context)
        if region is None:
            return {"PASS_THROUGH"}
        x = event.mouse_x - region.x
        y = event.mouse_y - region.y
        x0, y0, x1, y1 = state.rect
        inside = x0 <= x <= x1 and y0 <= y <= y1

        # Moving or resizing the panel
        if state.drag:
            if event.type == "MOUSEMOVE":
                move_drag(context, event)
                _redraw()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                end_drag(context)
                _redraw()
                return {"RUNNING_MODAL"}
            if event.type in ("ESC", "RIGHTMOUSE"):
                state.drag = None
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
            return {"PASS_THROUGH"}

        if event.type in ("LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE"):
            if not inside:
                if event.value == "PRESS" and state.focused:
                    state.focused = False
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
                                context.window.cursor_modal_set("SCROLL_XY" if action != "grip" else "SCROLL_X")
                            else:
                                do_action(context, action, event)
                        except Exception as e:
                            session.add("info", f"That did not work: {e}")
                        break
                _redraw()
            return {"RUNNING_MODAL"}

        if state.minimized and not (relay_running_escape(context, event)):
            return {"PASS_THROUGH"}         # the bar only takes clicks; keys belong to Blender

        if event.type in ("WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN") and inside:
            if event.type == "TRACKPADPAN":
                step = event.mouse_y - event.mouse_prev_y
            else:
                step = d.ui() * 48 * (1 if event.type == "WHEELUPMOUSE" else -1)
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
        # there Blender's Esc cancels the playback (the only clash in 4.2's default keymap).
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
            state.text, state.cursor = "", 0
            send_text(context, text)
        elif action == "newline" and state.focused:
            state.text = state.text[:state.cursor] + "\n" + state.text[state.cursor:]
            state.cursor += 1
        elif action == "history_prev" and state.focused:
            history(-1)
        elif action == "history_next" and state.focused:
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
        pass


def relay_running_escape(context, event):
    """While minimized only Esc still matters: it pauses the AI (unless an animation plays)."""
    from .relay import relay
    playing = bool(context.screen and context.screen.is_animation_playing)
    return match(event) == "escape" and relay.is_running() and not playing


def double_click(context, action):
    """Double-click the logo row: dock on the right again. The grip: the design size again."""
    from . import prefs
    p = prefs.addon_prefs(context)
    state.drag = None
    if p is None:
        return
    if action == "grip":
        p.panel_width = p.panel_height = 0.0
    elif action == "mini":
        restore()
    else:
        p.panel_dock = "RIGHT"
        p.panel_gap_right = p.panel_gap_top = 0.0


class BWM_OT_panel_reset(bpy.types.Operator):
    bl_idname = "blendwme.panel_reset"
    bl_label = "Put the Panel Back on the Right"
    bl_description = "Dock the BlendWMe panel on the right again, full height, at its normal width"
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
        ("next_mode", "Next Mode", "Together, Teach, Auto"),
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
    state.drag = None
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
