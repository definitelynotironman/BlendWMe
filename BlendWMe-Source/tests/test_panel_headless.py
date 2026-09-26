"""Headless tests of the BlendWMe panel's logic: themes, text wrapping, typing, history,
shortcuts, slash commands, closing cards and the Done card. Drawing needs a window, so
it is checked with screenshots instead; this file tests everything behind the pixels.

Run (no brain and no subscription is used):

    blender -b --factory-startup --python-exit-code 1 --python tests/test_panel_headless.py
"""

import os
import sys
import tempfile
import time

sys.stdout.reconfigure(line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["BLENDWME_LOG_FOLDER"] = tempfile.mkdtemp(prefix="bwm_panel_logs_")

import bpy  # noqa: E402

import addon_utils  # noqa: E402

addon_utils.enable("blendwme", default_set=True)      # enabled as an add-on, so it has its preferences

from blendwme import draw2d, panel, panel_ops, prefs, theme  # noqa: E402
from blendwme.relay import relay  # noqa: E402
from blendwme.state import session  # noqa: E402
import blendwme.state as st  # noqa: E402

passed = []


def check(name, cond, detail=""):
    assert cond, f"FAILED {name} {detail}"
    passed.append(name)
    print("ok -", name)


class Event:
    """A stand-in for bpy.types.Event."""

    def __init__(self, type, value="PRESS", unicode="", shift=False, ctrl=False, alt=False):
        self.type, self.value, self.unicode = type, value, unicode
        self.shift, self.ctrl, self.alt = shift, ctrl, alt


def typed(text):
    for ch in text:
        panel_ops.type_key(bpy.context, Event(ch.upper() if ch.isalpha() else "SPACE", unicode=ch))


def fresh():
    session.reset_conversation()
    relay.running = None
    panel.state.text, panel.state.cursor = "", 0
    panel.state.screen = "main"
    panel.state.focused = True


# ----------------------------------------------------------------------
# Themes and drawing helpers
# ----------------------------------------------------------------------
for name in ("GRAPHITE", "RED", "BLENDER"):
    pal = theme.Palette(name)
    check(f"theme {name} has every colour", all(len(getattr(pal, k)) == 4 for k in
                                                ("bg", "panel", "card", "ink", "accent", "on_accent", "shadow",
                                                 "hot", "muted")))
check("theme follows the setting", theme.current().name == prefs.addon_prefs().theme)
check("three fonts are bundled", all(os.path.exists(os.path.join(os.path.dirname(draw2d.__file__), "fonts", f))
                                     for f in ("ArchivoBlack-Regular.ttf", "Archivo-Regular.ttf",
                                               "PatrickHand-Regular.ttf")))
draw2d.load_fonts()
check("fonts load", all(draw2d.font(k) not in (None, -1) for k in ("BLACK", "HAND", "BODY")))
long_text = "word " * 120 + "Averyveryveryverylongwordthatmustbreaksomewhere" * 3
lines = draw2d.wrap("BODY", 16, long_text, 300)
check("wrap keeps every word (nothing is cut off)",
      "".join(lines).replace(" ", "") == long_text.replace(" ", ""), len(lines))
check("wrap keeps lines inside the width", all(draw2d.width_of("BODY", 16, ln) <= 300.5 for ln in lines))
check("wrap keeps blank lines between paragraphs", draw2d.wrap("BODY", 16, "a\n\nb", 300) == ["a", "", "b"])
pts = []
draw2d.segments = lambda pieces, *a, **k: pts.extend(pieces)
start = time.time()
draw2d.dashed_path([(0, 0), (1000.0000001, 0), (1000, 7.0000000001)], (0, 0, 0, 1), offset=time.time() * 24)
check("a dashed path always finishes, even with odd rounding", time.time() - start < 1.0 and pts)
check("reduce motion setting is read", theme.reduce_motion() in (True, False))
prefs.addon_prefs().reduce_motion = "REDUCE"
check("reduce motion can be forced on", theme.reduce_motion() is True)
prefs.addon_prefs().reduce_motion = "SYSTEM"

# ----------------------------------------------------------------------
# Typing, history and shortcuts
# ----------------------------------------------------------------------
fresh()
typed("hello world")
check("typing fills the input", panel.state.text == "hello world" and panel.state.cursor == 11)
panel_ops.type_key(bpy.context, Event("BACK_SPACE"))
check("backspace deletes one letter", panel.state.text == "hello worl")
panel_ops.type_key(bpy.context, Event("BACK_SPACE", ctrl=True))
check("ctrl+backspace deletes a word", panel.state.text == "hello ", repr(panel.state.text))
panel_ops.type_key(bpy.context, Event("HOME"))
typed(">")
check("home moves the caret to the start", panel.state.text.startswith(">"))
check("the shortcut table is read from the keymap or the defaults",
      panel_ops.match(Event("RET")) == "send" and panel_ops.match(Event("RET", shift=True)) == "newline")
check("Shift+Tab is next mode", panel_ops.match(Event("TAB", shift=True)) == "next_mode")
check("Ctrl+L clears the view", panel_ops.match(Event("L", ctrl=True)) == "clear_view")
check("plain Tab is not taken from Blender", panel_ops.match(Event("TAB")) is None)
props = bpy.context.window_manager.blendwme
props.mode = "WATCH"
panel_ops.next_mode(bpy.context)
check("next mode goes Together -> Teach", props.mode == "TEACH")
panel_ops.next_mode(bpy.context)
panel_ops.next_mode(bpy.context)
check("next mode wraps round", props.mode == "WATCH")

fresh()
panel.state.history = ["first", "second"]
panel.state.history_at = None
panel_ops.history(-1)
check("Up brings back the last message", panel.state.text == "second")
panel_ops.history(-1)
check("Up again goes further back", panel.state.text == "first")
panel_ops.history(1)
panel_ops.history(1)
check("Down past the newest gives an empty input", panel.state.text == "")

fresh()
panel_ops.type_key(bpy.context, Event("SLASH", unicode="?"))
check("? in an empty input shows the shortcuts", session.log and "Shift+Enter" in session.log[-1]["text"])
fresh()
panel_ops.send_text(bpy.context, "/help")
check("/help shows the shortcuts", "Esc twice" in session.log[-1]["text"])
panel_ops.send_text(bpy.context, "/journal")
check("/journal opens the baton journal", panel.state.screen == "journal")
panel_ops.send_text(bpy.context, "/settings")
check("/settings opens settings", panel.state.screen == "settings")

# ----------------------------------------------------------------------
# Cards: every card can be closed; Esc
# ----------------------------------------------------------------------
fresh()
session.set_card({"kind": "watch", "title": "Step", "text": "Press Next"})
check("a card gets the time it appeared", "t" in session.card)
session.status = st.WAIT_NEXT
check("close hides a card the AI still waits on", panel_ops.close_top_card() and session.card.get("hidden"))
check("the status still says it is your turn", panel.your_turn())
session.status = st.IDLE
session.set_card({"kind": "stuck", "title": "Stuck", "text": "x"})
panel_ops.close_top_card()
check("close removes a finished card", session.card is None)
session.set_card({"kind": "done", "title": "Done", "text": "Cottage built."})
panel_ops.close_top_card()
check("closing the Done card leaves one line", session.card is None and session.log[-1]["who"] == "done"
      and "Cottage built." in session.log[-1]["text"])
session.set_card({"kind": "done", "title": "Done", "text": "Room built."})
session.card["t"] = time.time() - 10
from blendwme import overlay  # noqa: E402
overlay._redraw_timer()
check("the Done card collapses by itself after 6 s", session.card is None and "Room built." in session.log[-1]["text"])

fresh()
session.set_card({"kind": "watch", "title": "x", "text": "y"})
session.status = st.IDLE
check("Esc closes the top card", panel_ops.escape(bpy.context) and session.card is None)
panel.state.screen = "journal"
panel_ops.escape(bpy.context)
check("Esc goes back from the journal", panel.state.screen == "main")
panel_ops.escape(bpy.context)
check("Esc leaves the input last", panel.state.focused is False)

# ----------------------------------------------------------------------
# Clicks
# ----------------------------------------------------------------------
fresh()
panel_ops.do_action(bpy.context, "mode:AUTO")
check("a mode tab click switches the mode", props.mode == "AUTO")
panel_ops.do_action(bpy.context, "example:A simple ice cream cone")
check("an example chip fills the input", "ice cream cone" in panel.state.text and panel.state.focused)
p = prefs.addon_prefs()
before = p.auto_relay
panel_ops.do_action(bpy.context, "toggle:auto_relay")
check("a checkbox click toggles the setting", p.auto_relay != before)
panel_ops.do_action(bpy.context, "theme:RED")
check("a theme click changes the theme", p.theme == "RED" and theme.current().name == "RED")
p.theme = "GRAPHITE"
panel_ops.do_action(bpy.context, "brain:codex")
check("a brain card click picks the brain", props.brain == "codex")
props.brain = "claude"
session.add("ai", "x " * 400, brain="claude")
entry = session.log[-1]
entry["_id"] = 99
panel_ops.do_action(bpy.context, "read_more:99")
check("Read more opens a long message", 99 in panel.state.expanded)
panel.state.cleared_at = 0.0
panel_ops.do_action(bpy.context, "screen:journal")
check("the footer opens the journal", panel.state.screen == "journal")

# ----------------------------------------------------------------------
# Behaviour: the plan cannot get stuck, done is never self-declared
# ----------------------------------------------------------------------
from blendwme.tool_host import host, _numpad_note, TEACH_WORDS  # noqa: E402
from blendwme import executor, knowledge  # noqa: E402
from blendwme.journal import journal as jr  # noqa: E402

fresh()
session.mode = "WATCH"
host.reset_task("A small table", [])
long_name = ("Table top " + "with a very long description " * 8).strip()
parts = [long_name] + [f"Leg {i}" for i in range(1, 46)]
host._tool_plan({"parts": parts}, "claude")
check("a long plan keeps every part and every word", len(session.plan) == 46 and session.plan[0]["name"] == long_name)
check("the journal keeps the full plan too", len(jr.data["plan"]) == 46 and jr.data["plan"][0]["name"] == long_name)
host._tool_plan({"parts": ["Top", "Legs", "Drawer"]}, "claude")
result, _ = host._tool_finish({"summary": "Table done."}, "claude")
check("finish is refused while plan parts are open", "not_finished_yet" in result and "1. Top" in result["not_finished_yet"])
check("a refused finish shows no Done card", (session.card or {}).get("kind") != "done")
session.tick_plan(1)
host.ok_steps, host.looked_at_step = 3, 0
result, _ = host._tool_plan({"done": [2]}, "claude")
check("marking a part done needs a look after building", "done_refused" in result and not session.plan[1]["done"])
host.looked_at_step = host.ok_steps
result, _ = host._tool_plan({"done": [2]}, "claude")
check("after a look a part can be marked done", result.get("marked_done") == [2] and session.plan[1]["done"])
result, _ = host._tool_plan({"skip": [{"part": 3}]}, "claude")
check("a skip needs a reason", "skip_refused" in result and not session.plan[2]["done"])
host._tool_plan({"skip": [{"part": 3, "reason": "The brief asked for a plain table."}]}, "claude")
check("a skipped part shows its reason", session.plan[2]["skipped"] and "plain table" in session.plan[2]["reason"]
      and "plain table" in session.log[-1]["text"])
check("the journal records the skip", jr.data["plan"][2]["skipped"] == "The brief asked for a plain table.")
host.ok_steps = 0
result, _ = host._tool_finish({"summary": "The table is done. Final render saved."}, "claude")
check("finish is refused when it claims a render that does not exist", "render" in result.get("not_finished_yet", ""))
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = scene.render.resolution_y = 32
scene.render.filepath = os.path.join(tempfile.mkdtemp(prefix="bwm_render_"), "shot")
bpy.ops.render.render(write_still=True)
result, _ = host._tool_finish({"summary": "The table is done. Final render saved."}, "claude")
check("finish works once every part is settled and the render exists", result.get("ok") is True, result)
check("the plan card closes when the task finishes", session.plan == [] and session.card["kind"] == "done")

session.set_plan(["A", "B"])
relay.running = "claude"
relay.stop()
relay.running = None
session.stop_flag.clear()
check("the plan card closes when the work is stopped", session.plan == [])
session.set_plan(["A", "B"])
relay._new_task("A new brief")
check("the plan card closes when a new brief starts", session.plan == [])

from blendwme.engines import claude_code, codex  # noqa: E402
long_result = "result " * 1500
check("Claude's long tool results are kept whole in the log", claude_code._result_text(long_result) == long_result)
check("Codex's long tool results are kept whole in the log",
      codex._result_text({"result": {"content": [{"type": "text", "text": long_result}]}}) == long_result)
jr.record_step("claude", "A step title that is quite a lot longer than eighty characters, to be sure it is whole",
               "", "", True)
check("long step titles are kept whole", jr.data["events"][-1]["title"].endswith("to be sure it is whole"))

# ----------------------------------------------------------------------
# Teaching is short and points at the real control
# ----------------------------------------------------------------------
session.mode = "TEACH"
result, _ = host._tool_teach_step({"title": "Bevel", "instruction": "word " * (TEACH_WORDS + 5),
                                   "point_tab": "MODIFIER"}, "claude")
check("a long Teach card is refused", "under 20 words" in result.get("error", ""))
result, _ = host._tool_teach_step({"title": "Bevel", "instruction": "Add a Bevel modifier."}, "claude")
check("a Teach card without a pointer is refused", "pointer" in result.get("error", ""))
bpy.context.preferences.inputs.use_emulate_numpad = False
why = _numpad_note("Press Numpad 1 for the front view.", "Front view shows the face.")
check("numpad steps get the menu path and the Emulate Numpad tip", "View > Viewpoint" in why and "Emulate Numpad" in why)
bpy.context.preferences.inputs.use_emulate_numpad = True
check("no numpad tip when Emulate Numpad is on", _numpad_note("Press Numpad 1.", "x") == "x")
bpy.context.preferences.inputs.use_emulate_numpad = False
check("other steps get no numpad tip", _numpad_note("Press Ctrl+B.", "x") == "x")
session.mode = "WATCH"

# ----------------------------------------------------------------------
# Camera help: the bwm helpers and the card's own code
# ----------------------------------------------------------------------
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from blendwme import helpers as bwm  # noqa: E402
from mathutils import Vector  # noqa: E402


def inside_frame(cam, objects):
    corners = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    co = [world_to_camera_view(bpy.context.scene, cam, p) for p in corners]
    return all(-0.001 <= c.x <= 1.001 and -0.001 <= c.y <= 1.001 and c.z > 0 for c in co)


bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
house = bpy.context.active_object
bpy.ops.mesh.primitive_cube_add(size=1, location=(6, 3, 0.5))
shed = bpy.context.active_object
cam = bwm.frame_camera()
check("frame_camera makes a camera when there is none", bpy.context.scene.camera is cam)
check("frame_camera fits every visible object", inside_frame(cam, [house, shed]))
cam = bwm.focal_length(85)
check("focal_length sets the lens and keeps the subject in frame", cam.data.lens == 85 and inside_frame(cam, [house, shed]))
bwm.frame_camera(house)
check("frame_camera can frame one subject", inside_frame(cam, [house]) and not inside_frame(cam, [shed]))
cam = bwm.camera_to_view()
check("camera_to_view without a viewport frames the scene", inside_frame(cam, [house, shed]))
card = knowledge.load_skill("camera-help")
check("the camera-help card loads", "Do it for me" in card and "Emulate Numpad" in card)
code = card.split("```python", 1)[1].split("```", 1)[0]
outcome = executor.run(code, "Camera from the card")
check("the camera-help card's code runs cleanly in Blender", outcome.get("ok"), outcome.get("error", ""))
check("the camera helpers are in the bwm kit list", {"camera_to_view", "frame_camera", "focal_length"}
      <= set(bwm.public_names()))

# ----------------------------------------------------------------------
# The panel's place and size: docked tight on the right, movable, resizable, minimizable
# ----------------------------------------------------------------------
class Reg:
    def __init__(self, type, x, y, width, height):
        self.type, self.x, self.y, self.width, self.height = type, x, y, width, height


class Area:
    def __init__(self, regions):
        self.regions = regions


class MouseEvent(Event):
    def __init__(self, type, value="PRESS", x=0, y=0):
        super().__init__(type, value)
        self.mouse_x, self.mouse_y = x, y


view = Reg("WINDOW", 0, 0, 1600, 900)
sidebar = Reg("UI", 1340, 0, 260, 870)          # the N sidebar overlapping the viewport
header = Reg("HEADER", 0, 870, 1600, 30)
fake_area = Area([view, sidebar, header])
panel_ops.host = lambda context: (fake_area, view)
p = prefs.addon_prefs()
panel_ops.reset_place(p)
p.panel_size, p.text_size, p.icon_size = 75, 100, 100
s = panel.scale()
x0, y0, x1, y1 = panel.placement(fake_area, view)
check("the panel is smaller by default (75 %)", abs((x1 - x0) - panel.PANEL_WIDTH * s) < 1 and s < draw2d.base_ui(),
      (x1 - x0, s))
check("docked, it sits tight against the sidebar (a few pixels for the shadow)",
      0 < 1340 - x1 <= (panel.EDGE + theme.SHADOW_PANEL) * s + 1, 1340 - x1)
check("docked, it starts right under the header", 0 < 870 - y1 <= panel.EDGE * s + 1, 870 - y1)
fake_area.regions = [view, header]               # sidebar closed: the panel takes the edge
check("with the sidebar closed it docks at the viewport's right edge",
      1600 - panel.placement(fake_area, view)[2] <= (panel.EDGE + theme.SHADOW_PANEL) * s + 1)
fake_area.regions = [view, sidebar, header]

p.panel_size = 100
wide = panel.placement(fake_area, view)
p.panel_size = 60
narrow = panel.placement(fake_area, view)
check("Panel size scales the whole panel", (narrow[2] - narrow[0]) < (wide[2] - wide[0]) * 0.65)
p.panel_size = 75

# Drag the logo row 300 px left and 100 px down.
x0, y0, x1, y1 = panel.placement(fake_area, view)
panel_ops.start_drag(bpy.context, "drag", MouseEvent("LEFTMOUSE", x=x1 - 50, y=y1 - 20))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=x1 - 350, y=y1 - 120))
panel_ops.end_drag(bpy.context)
m = panel.placement(fake_area, view)
check("dragging the logo row moves the panel", p.panel_dock == "FREE" and abs(m[2] - (x1 - 300)) < 2,
      (m, x1))
check("a full-height panel keeps its height while it moves (it cannot go below the viewport)",
      abs((m[3] - m[1]) - (y1 - y0)) < 2 and m[1] >= 0)
# Drag it back near the right edge: it docks again and gets its full height back.
panel_ops.start_drag(bpy.context, "drag", MouseEvent("LEFTMOUSE", x=m[2] - 50, y=m[3] - 20))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=m[2] - 50 + 290, y=m[3] - 20))
panel_ops.end_drag(bpy.context)
check("let go near the right edge and it docks there again",
      p.panel_dock == "RIGHT" and p.panel_height == 0.0 and panel.placement(fake_area, view) == (x0, y0, x1, y1),
      (p.panel_dock, p.panel_height, p.panel_gap_right, panel.placement(fake_area, view), (x0, y0, x1, y1)))
# A drag never takes the panel off the viewport.
panel_ops.start_drag(bpy.context, "drag", MouseEvent("LEFTMOUSE", x=1000, y=800))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=-5000, y=-5000))
panel_ops.end_drag(bpy.context)
far = panel.placement(fake_area, view)
check("the panel stays inside the viewport however far it is dragged", far[0] >= 0 and far[1] >= 0, far)
panel_ops.do_action(bpy.context, "panel_reset")
check("Put it back on the right resets place and size", p.panel_dock == "RIGHT" and not p.panel_width
      and panel.placement(fake_area, view) == (x0, y0, x1, y1))

# The corner grip: 80 px narrower, 200 px shorter (mouse right and up).
panel_ops.start_drag(bpy.context, "grip", MouseEvent("LEFTMOUSE", x=x0 + 5, y=y0 + 5))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=x0 + 85, y=y0 + 205))
panel_ops.end_drag(bpy.context)
g = panel.placement(fake_area, view)
check("the corner grip resizes the panel and keeps its top right corner",
      abs((g[2] - g[0]) - (x1 - x0 - 80)) < 2 and abs((g[3] - g[1]) - (y1 - y0 - 200)) < 2
      and abs(g[2] - x1) < 1 and abs(g[3] - y1) < 1, (g, (x0, y0, x1, y1)))
panel_ops.start_drag(bpy.context, "grip", MouseEvent("LEFTMOUSE", x=x0, y=y0))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=x0 + 5000, y=y0 + 5000))
panel_ops.end_drag(bpy.context)
g = panel.placement(fake_area, view)
check("the grip stops at a usable minimum size",
      abs((g[2] - g[0]) - panel.MIN_WIDTH * s) < 2 and abs((g[3] - g[1]) - panel.MIN_HEIGHT * s) < 2)
panel_ops.double_click(bpy.context, "grip")
check("double-click on the grip gives the normal size back", not p.panel_width and not p.panel_height)

# Size steppers in the settings
p.panel_size = 50
panel_ops.do_action(bpy.context, "size:panel_size:-5")
check("the size steppers stop at the smallest size", p.panel_size == 50)
panel_ops.do_action(bpy.context, "size:text_size:5")
panel_ops.do_action(bpy.context, "size:icon_size:-5")
check("Text size and Icon size steppers change their settings", p.text_size == 105 and p.icon_size == 95)
p.panel_size, p.text_size, p.icon_size = 75, 100, 100

# Text and icon size apply only while the panel draws
with draw2d.scaled(0.75, 1.2, 1.3):
    inside = (draw2d.FONT_SCALE, draw2d.ICON_SCALE, round(draw2d.ui() / draw2d.base_ui(), 2),
              round(draw2d.line_height("HAND", 10), 3))
check("the size settings are on while the panel draws", inside == (1.2, 1.3, 0.75, round(12.0 * 1.22, 3)), inside)
check("and off again for the viewport indicators", (draw2d.FONT_SCALE, draw2d.ICON_SCALE, draw2d.ZOOM) == (1, 1, 1))

# Minimize keeps the session; Take control minimizes
fresh()
panel.state.open = True
session.add("user", "Build a cottage")
log_before = list(session.log)
panel_ops.do_action(bpy.context, "minimize")
check("minimize folds the panel and keeps the conversation",
      panel.state.minimized and panel.state.open and session.log == log_before and not panel.state.focused)
panel_ops.start_drag(bpy.context, "mini", MouseEvent("LEFTMOUSE", x=1200, y=850))
panel_ops.end_drag(bpy.context)
check("a click on the minimized bar opens the panel again", not panel.state.minimized and panel.state.focused)
session.pause_flag.clear()
p.minimize_on_take_control = True
panel_ops.do_action(bpy.context, "take_control")
check("Take control pauses and gets the panel out of the way",
      session.pause_flag.is_set() and panel.state.minimized)
panel_ops.restore()
session.pause_flag.clear()
p.minimize_on_take_control = False
panel_ops.do_action(bpy.context, "take_control")
check("with the setting off, Take control only pauses", session.pause_flag.is_set() and not panel.state.minimized)
session.pause_flag.clear()
p.minimize_on_take_control = True
panel_ops.minimize()
panel_ops.close_panel()
check("closing the panel forgets the minimized state", not panel.state.minimized and not panel.state.open)
area_stub = type("A", (), {"as_pointer": lambda self: 7})()
panel.state.open, panel.state.area_ptr, panel.state.minimized = True, 7, True
check("a minimized panel counts as open but not expanded",
      panel.is_open_in(area_stub) and not panel.is_expanded_in(area_stub))
panel.state.open, panel.state.minimized = False, False

from blendwme.relay import addon_version  # noqa: E402
check("saved logs carry the manifest's version", addon_version() == "2.1.0", addon_version())

# ----------------------------------------------------------------------
# Export and the launcher
# ----------------------------------------------------------------------
before = set(os.listdir(os.environ["BLENDWME_LOG_FOLDER"]))
bpy.ops.wm.path_open = lambda **k: None      # do not open a file browser in a test
try:
    bpy.ops.blendwme.export_journal()
except Exception:
    pass
after = set(os.listdir(os.environ["BLENDWME_LOG_FOLDER"]))
check("Export writes the journal to the logs folder", any(n.startswith("journal_") for n in after - before),
      after - before)
check("the panel operators are registered", hasattr(bpy.types, "BLENDWME_OT_panel") and
      hasattr(bpy.types, "BLENDWME_OT_panel_key"))
check("the panel has a draw handler", len(panel._handles) == 1)
addon_utils.disable("blendwme", default_set=True)
check("unregister removes the panel", not panel._handles and not hasattr(bpy.types, "BLENDWME_OT_panel"))

print(f"ALL {len(passed)} PANEL CHECKS PASSED")
os._exit(0)
