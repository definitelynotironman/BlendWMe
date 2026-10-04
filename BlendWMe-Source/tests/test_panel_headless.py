# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
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

__author__ = "definitelynotironman"

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
    """Assert one named check, count it and print an ok line (a failure stops the suite)."""
    assert cond, f"FAILED {name} {detail}"
    passed.append(name)
    print("ok -", name)


class Event:
    """A stand-in for bpy.types.Event."""

    def __init__(self, type, value="PRESS", unicode="", shift=False, ctrl=False, alt=False):
        """A key event with its type, value, character and modifiers."""
        self.type, self.value, self.unicode = type, value, unicode
        self.shift, self.ctrl, self.alt = shift, ctrl, alt


def typed(text):
    """Type text into the panel input one key event at a time."""
    for ch in text:
        panel_ops.type_key(bpy.context, Event(ch.upper() if ch.isalpha() else "SPACE", unicode=ch))


def fresh():
    """An empty conversation and draft on the main screen, with the input focused."""
    session.reset_conversation()
    relay.running = None
    panel.state.text, panel.state.cursor = "", 0
    panel.state.screen = "main"
    panel.state.focused = True


# ----------------------------------------------------------------------
# Themes and drawing helpers
# ----------------------------------------------------------------------
for name in ("GRAPHITE", "RED", "BLENDER", "Y2K", "NATIVE"):
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
joined = "".join(ln[:-1] if ln.endswith("-") else ln for ln in lines)
check("wrap keeps every word (nothing is cut off)", joined.replace(" ", "") == long_text.replace(" ", ""),
      len(lines))
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
props["mode"] = 0
prefs.sync_session()
check("legacy numeric mode becomes Auto", props.mode == "AUTO" and session.mode == "AUTO")
props["mode"] = 1
prefs.sync_session()
check("stored Teach keeps its identity", props.mode == "TEACH" and session.mode == "TEACH")
props.mode = "AUTO"
panel_ops.next_mode(bpy.context)
check("next mode goes Auto -> Teach", props.mode == "TEACH")
panel_ops.next_mode(bpy.context)
check("next mode wraps round", props.mode == "AUTO")

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
panel_ops.settings_asked[0] = 0.0
panel_ops.send_text(bpy.context, "/settings")
check("/settings opens the Settings popover", panel_ops.settings_asked[0] > 0)

# ----------------------------------------------------------------------
# The five themes (four drawn, one in Blender's own interface)
# ----------------------------------------------------------------------
check("there are five themes", [t[0] for t in prefs.THEMES] == ["GRAPHITE", "RED", "BLENDER", "Y2K", "NATIVE"])
y2k = theme.Palette("Y2K")
check("the 2000s theme has Theme 2000's blue title band and orange tag",
      y2k.band is not None and theme.TOKENS["Y2K"]["band"] == "5070D3" and theme.TOKENS["Y2K"]["tag"] == "F3963D")
check("the 2000s theme keeps handwriting", not y2k.roles.get("HAND"))
with draw2d.scaled(1.0, 1.0, 1.0, y2k.roles):
    role_w = draw2d.width_of("HAND", 20, "Tell Claude what to change")
    role_font = draw2d.font("HAND") != draw2d.font("BODY")
check("theme handwriting uses consistent metrics", role_font and
      abs(role_w - draw2d.width_of("HAND", 20, "Tell Claude what to change")) < 1, role_w)
saved_host = panel_ops.host
panel_ops.host = lambda context: (None, type("Region", (), {"x": 0, "y": 0})())
panel.state.text = "mixed handwriting"
for factor in (0.75, 1.0, 1.5, 2.0):
    with draw2d.scaled(1.0, factor):
        advances = [draw2d.width_of("HAND", 20, panel.state.text[:i])
                    for i in range(len(panel.state.text) + 1)]
    panel.state.input_layout = (10, 100, 30, 20, 0, [(0, len(panel.state.text))], [advances])
    event = type("Mouse", (), {"mouse_x": 10 + advances[6], "mouse_y": 90})()
    check(f"caret uses drawn advances at text scale {factor}", panel_ops.caret_at(None, event) == 6)
panel_ops.host = saved_host
native_pal = theme.Palette("NATIVE")
check("Blender UI takes its colours from Blender's own theme",
      all(len(getattr(native_pal, k)) == 4 for k in ("panel", "ink", "accent", "hot")))
check("drawn themes draw the panel, Blender UI does not", set(theme.DRAWN) == {"GRAPHITE", "RED", "BLENDER", "Y2K"})
from blendwme import ui  # noqa: E402
p0 = prefs.addon_prefs()
p0.theme = "GRAPHITE"
check("with a drawn theme the sidebar has no BlendWMe tab", not ui.BWM_PT_main.poll(bpy.context))
p0.theme = "NATIVE"
check("with Blender UI the sidebar is BlendWMe", ui.BWM_PT_main.poll(bpy.context) and theme.native())
area_stub = type("A", (), {"as_pointer": lambda self: 7})()
panel.state.open, panel.state.area_ptr = True, 7
check("with Blender UI the drawn panel is never open", not panel.is_open_in(area_stub))
panel.state.open = False
p0.theme = "GRAPHITE"
check("the Settings popover is registered", hasattr(bpy.types, "BWM_PT_settings"))

# ----------------------------------------------------------------------
# Typing like a Blender text field (rule R7)
# ----------------------------------------------------------------------
fresh()
typed("make a red roof")
panel_ops.type_key(bpy.context, Event("LEFT_ARROW", ctrl=True))
check("Ctrl+Left jumps a word", panel.state.cursor == len("make a red "))
panel_ops.type_key(bpy.context, Event("RIGHT_ARROW", shift=True, ctrl=True))
check("Ctrl+Shift+Right selects a word", panel.selection() == (len("make a red "), len("make a red roof")))
typed("tiled roof")
check("typing replaces the selection", panel.state.text == "make a red tiled roof", panel.state.text)
panel_ops.type_key(bpy.context, Event("Z", ctrl=True))
check("Ctrl+Z undoes a word at a time", panel.state.text == "make a red tiled ", panel.state.text)
panel_ops.type_key(bpy.context, Event("Z", ctrl=True, shift=True))
check("Ctrl+Shift+Z redoes", panel.state.text == "make a red tiled roof", panel.state.text)
panel_ops.type_key(bpy.context, Event("A", ctrl=True))
check("Ctrl+A selects everything", panel.selection() == (0, len(panel.state.text)))
clip = type("Ctx", (), {})()          # background Blender has no system clipboard
clip.window_manager = type("WM", (), {"clipboard": ""})()
panel_ops.type_key(clip, Event("C", ctrl=True))
check("Ctrl+C copies the selection", clip.window_manager.clipboard == "make a red tiled roof")
panel_ops.type_key(clip, Event("X", ctrl=True))
check("Ctrl+X cuts it", panel.state.text == "")
panel_ops.type_key(clip, Event("V", ctrl=True))
panel_ops.type_key(clip, Event("V", ctrl=True))
check("Ctrl+V pastes at the caret", panel.state.text == "make a red tiled roof" * 2)
panel_ops.type_key(bpy.context, Event("HOME"))
panel_ops.type_key(bpy.context, Event("RIGHT_ARROW", shift=True))
panel_ops.type_key(bpy.context, Event("RIGHT_ARROW", shift=True))
panel_ops.type_key(bpy.context, Event("BACK_SPACE"))
check("Shift+arrows select and Backspace deletes the selection", panel.state.text.startswith("ke a red"))
fresh()
lines = panel.input_layout("one two three four five six seven eight nine ten", 20, 120)
check("the input wraps into ranges of the text (for the caret and selection)",
      len(lines) > 1 and lines[0][0] == 0 and lines[-1][1] == len("one two three four five six seven eight nine ten"))

# ----------------------------------------------------------------------
# Text that never escapes its box (rules R4, R11)
# ----------------------------------------------------------------------
check("a value keeps its unit on the same line", draw2d.NBSP in draw2d.keep_together("wait 10 s please"))
check("'6 of 7' stays together", draw2d.keep_together("6 of 7").count(draw2d.NBSP) == 2)
check("a long word breaks with a hyphen",
      any(ln.endswith("-") for ln in draw2d.wrap("BODY", 16, "Supercalifragilisticexpialidocious" * 2, 120)))
orphan = draw2d.wrap("BODY", 16, "one two three four five six seven eight nine ten eleven twelve x", 170)
check("no one-word orphan on the last line", " " in orphan[-1].strip() or len(orphan) == 1, orphan)
cut = draw2d.fit("BODY", 16, "a sentence that is far too long for this little box", 120)
check("a line that does not fit ends with an ellipsis", cut.endswith("…") and draw2d.width_of("BODY", 16, cut) <= 120)
check("the line height is round(1.25 x size)", draw2d.line_height("BODY", 16) == 20 and draw2d.line_height("BODY", 13) == 16)
para_lines = draw2d.wrap("BODY", 16, "word " * 60, 200)
h_all = draw2d.paragraph_height("BODY", 16, "word " * 60, 200)
h_three = draw2d.paragraph_height("BODY", 16, "word " * 60, 200, max_lines=3)
check("max_lines limits a paragraph's height", h_three == 3 * draw2d.line_height("BODY", 16) < h_all, (h_three, h_all))

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
    """True when every bounding-box corner of the objects is inside the camera's view."""
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
# The panel's place and size (rules R8, R9): a corner of the free viewport and a
# distance from it, both-axis drag with snapping, the grip, the gizmo, the views
# ----------------------------------------------------------------------
class Reg:
    """A stand-in region with a type, position and size."""
    def __init__(self, type, x, y, width, height):
        """Store the region's type, position and size."""
        self.type, self.x, self.y, self.width, self.height = type, x, y, width, height


class Space:
    """A stand-in 3D Viewport space with its gizmo switches."""
    def __init__(self, gizmo=False):
        """Show or hide the navigation gizmo."""
        self.show_gizmo = self.show_gizmo_navigate = gizmo


class Spaces:
    """A stand-in for area.spaces with an active space."""
    def __init__(self, gizmo=False):
        """One active space."""
        self.active = Space(gizmo)


class Area:
    """A stand-in 3D Viewport area for the placement tests."""
    def __init__(self, regions, gizmo=False):
        """The regions and the space."""
        self.regions = regions
        self.spaces = Spaces(gizmo)

    def as_pointer(self):
        """A fixed fake pointer."""
        return 4242


class MouseEvent(Event):
    """A mouse event with a position."""
    def __init__(self, type, value="PRESS", x=0, y=0, ctrl=False, shift=False):
        """A mouse event at (x, y) with modifiers."""
        super().__init__(type, value, ctrl=ctrl, shift=shift)
        self.mouse_x, self.mouse_y = x, y


view = Reg("WINDOW", 0, 0, 1600, 900)
sidebar = Reg("UI", 1340, 0, 260, 870)          # the N sidebar overlapping the viewport
header = Reg("HEADER", 0, 870, 1600, 30)
toolbar = Reg("TOOLS", 0, 0, 48, 870)
fake_area = Area([view, sidebar, header])
panel_ops.host = lambda context: (fake_area, view)
p = prefs.addon_prefs()
panel_ops.reset_place(p)
p.panel_size, p.text_size, p.icon_size = 75, 100, 100
s = panel.scale()
check("the panel scale is Blender's scale times the panel size (R2)",
      abs(s - draw2d.system_scale() * 0.75) < 1e-6, s)
edge_x = (panel.EDGE + theme.SHADOW_PANEL) * s + 1
x0, y0, x1, y1 = panel.placement(fake_area, view)
check("the panel is smaller by default (75 %)", abs((x1 - x0) - panel.PANEL_WIDTH * s) < 1, (x1 - x0, s))
check("it sits tight against the sidebar (a few pixels for the shadow)", 0 < 1340 - x1 <= edge_x, 1340 - x1)
check("it starts right under the header", 0 < 870 - y1 <= panel.EDGE * s + 1, 870 - y1)
check("the rectangle is in whole pixels (R3)", all(v == int(v) for v in (x0, y0, x1, y1)))
fake_area.regions = [view, header]               # sidebar closed: the panel takes the edge
check("with the sidebar closed it sits at the viewport's right edge",
      1600 - panel.placement(fake_area, view)[2] <= edge_x)
fake_area.regions = [view, sidebar, header]

p.panel_size = 100
wide = panel.placement(fake_area, view)
p.panel_size = 60
narrow = panel.placement(fake_area, view)
check("Panel size scales the whole panel", (narrow[2] - narrow[0]) < (wide[2] - wide[0]) * 0.65)
p.panel_size = 75

# The height follows the content (R9), between the minimum and the free height.
h300 = panel.placement(fake_area, view, natural=300)
check("the height follows the content", h300[3] - h300[1] == 300 and h300[3] == y1, h300)
tall = panel.placement(fake_area, view, natural=10 ** 6)
check("a tall panel stops at the free viewport", tall[1] >= 0 and tall == (x0, y0, x1, y1))
tiny = panel.placement(fake_area, view, natural=10)
check("a short panel keeps the minimum height", abs((tiny[3] - tiny[1]) - round(panel.MIN_HEIGHT * s)) <= 1)

# Drag the header 300 px left and 100 px down: it moves on both axes (R8).
panel.state.frame = h300
panel_ops.start_drag(bpy.context, "drag", MouseEvent("LEFTMOUSE", x=h300[2] - 50, y=h300[3] - 20))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=h300[2] - 350, y=h300[3] - 120))
panel_ops.end_drag(bpy.context)
m = panel.placement(fake_area, view, natural=300)
check("dragging the header moves the panel left and down",
      abs(m[2] - (h300[2] - 300)) <= 1 and abs(m[3] - (h300[3] - 100)) <= 1, (m, h300))
check("it remembers the nearest corner and the distance in design units",
      p.panel_corner == "TR" and abs(p.panel_off_x * s - 300) <= 1 and abs(p.panel_off_y * s - 100) <= 1,
      (p.panel_corner, p.panel_off_x, p.panel_off_y))
# A wider viewport (or a closed sidebar) keeps that distance from the corner.
sidebar.x = 1300
moved = panel.placement(fake_area, view, natural=300)
check("the distance from the corner stays when the free area changes", abs((1300 - moved[2]) - (1340 - m[2])) <= 1)
sidebar.x = 1340

# Snapping: let go 5 px from the right edge and it snaps there; Ctrl places it freely.
bounds = panel.usable(fake_area, view)
panel.state.frame = m
panel_ops.start_drag(bpy.context, "drag", MouseEvent("LEFTMOUSE", x=1000, y=700))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=1000 + (bounds[2] - m[2]) - 5, y=700))
check("near an edge the panel snaps to it", p.panel_off_x == 0.0, p.panel_off_x)
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=1000 + (bounds[2] - m[2]) - 5, y=700, ctrl=True))
check("holding Ctrl turns snapping off", abs(p.panel_off_x * s - 5) <= 1, p.panel_off_x * s)
# Esc (or right-click) puts it back where the drag started.
panel_ops.cancel_drag(bpy.context)
check("Esc cancels a drag and puts the panel back", abs(p.panel_off_x * s - 300) <= 1 and not panel.state.drag)

# Drag it to the bottom left: the corner changes, the toolbar is never covered.
fake_area.regions = [view, sidebar, header, toolbar]
panel.state.frame = panel.placement(fake_area, view, natural=300)
f = panel.state.frame
panel_ops.start_drag(bpy.context, "drag", MouseEvent("LEFTMOUSE", x=f[0] + 40, y=f[3] - 10))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=f[0] + 40 - 5000, y=f[3] - 10 - 5000))
panel_ops.end_drag(bpy.context)
bl = panel.placement(fake_area, view, natural=300)
check("dragged far down and left it takes the bottom left corner", p.panel_corner == "BL", p.panel_corner)
check("it stays inside the free viewport, clear of the toolbar", bl[0] >= 48 and bl[1] >= 0, bl)
view.height, header.y, sidebar.height, toolbar.height = 700, 670, 670, 670
check("in a bottom corner it stays put when the viewport gets shorter",
      panel.placement(fake_area, view, natural=300)[1] == bl[1])
view.height, header.y, sidebar.height, toolbar.height = 900, 870, 870, 870
fake_area.regions = [view, sidebar, header]
check("the nearest corner is found from the centre", panel.to_corner((10, 10, 110, 60), (0, 0, 1000, 800))[0] == "BL"
      and panel.to_corner((900, 700, 990, 790), (0, 0, 1000, 800))[0] == "TR")
panel_ops.do_action(bpy.context, "panel_reset")
check("Put it back on the right resets place and size", p.panel_corner == "TR" and not p.panel_off_x
      and not p.panel_width and panel.placement(fake_area, view) == (x0, y0, x1, y1))

# The corner grip sits opposite the panel's corner: 40 px narrower, 205 px shorter.
panel.state.frame = (x0, y0, x1, y1)
panel_ops.start_drag(bpy.context, "grip", MouseEvent("LEFTMOUSE", x=x0 + 5, y=y0 + 5))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=x0 + 45, y=y0 + 210))
panel_ops.end_drag(bpy.context)
g = panel.placement(fake_area, view)
check("the grip width snaps to an 8-unit grid (R9)", p.panel_width % panel.WIDTH_GRID == 0, p.panel_width)
check("the grip resizes the panel and keeps its top right corner",
      abs((g[2] - g[0]) - (x1 - x0 - 40)) <= panel.WIDTH_GRID * s and abs((g[3] - g[1]) - (y1 - y0 - 205)) < 2
      and g[2] == x1 and g[3] == y1, (g, (x0, y0, x1, y1)))
panel.state.frame = g
panel_ops.start_drag(bpy.context, "grip", MouseEvent("LEFTMOUSE", x=g[0], y=g[1]))
panel_ops.move_drag(bpy.context, MouseEvent("MOUSEMOVE", x=g[0] + 5000, y=g[1] + 5000))
panel_ops.end_drag(bpy.context)
g = panel.placement(fake_area, view)
check("the grip stops at the minimum size (300 x 220)",
      abs((g[2] - g[0]) - panel.MIN_WIDTH * s) <= panel.WIDTH_GRID * s and abs((g[3] - g[1]) - panel.MIN_HEIGHT * s) < 2,
      (g[2] - g[0], g[3] - g[1]))
panel_ops.double_click(bpy.context, "grip")
check("double-click on the grip gives the normal size back", not p.panel_width and not p.panel_height)
panel_ops.toggle_maximize(bpy.context)
check("maximize makes the panel full height", p.panel_max and panel.placement(fake_area, view, natural=300) ==
      (x0, y0, x1, y1))
panel_ops.toggle_maximize(bpy.context)
check("and restore gives it back", not p.panel_max)

# The navigation gizmo: the panel moves out of its way by the shorter route.
gizmo_area = Area([view, sidebar, header], gizmo=True)
p.avoid_gizmo = True
gz = panel.gizmo_rect(gizmo_area, view)
pg = panel.placement(gizmo_area, view, natural=300)
check("the panel never covers the navigation gizmo", gz is not None and (pg[2] <= gz[0] or pg[3] <= gz[1]), (pg, gz))
p.avoid_gizmo = False
check("with the gizmo setting off it sits at the edge again", panel.placement(gizmo_area, view, natural=300) == h300)
p.avoid_gizmo = True
check("with the gizmo hidden there is nothing to avoid", panel.gizmo_rect(fake_area, view) is None)

# Views (R10): Full, Compact, Minimized, with the frame animation
fresh()
panel.state.open, panel.state.minimized, panel.state.compact = True, False, False
panel.state.last_rect = (x0, y0, x1, y1)
bpy.context.preferences.view.smooth_view = 200
p.reduce_motion = "FULL"
panel_ops.double_click(bpy.context, "drag")
check("double-click on the header switches to Compact", panel.state.view() == "compact")
check("changing view animates the frame", panel.state.anim is not None and panel.state.anim["dur"] == panel.COLLAPSE_TIME)
panel_ops.double_click(bpy.context, "drag")
check("and back to Full (expanding is 200 ms)", panel.state.view() == "full" and panel.state.anim["dur"] == panel.EXPAND_TIME)
bpy.context.preferences.view.smooth_view = 0
panel_ops.minimize()
check("with Smooth View off the view changes at once", panel.state.view() == "mini" and panel.state.anim is None)
panel_ops.restore()
bpy.context.preferences.view.smooth_view = 200
p.reduce_motion = "SYSTEM"
panel.state.anim = None
panel.state.compact = True
panel_ops.do_action(bpy.context, "maximize")
check("maximize in Compact opens Full", panel.state.view() == "full")

# Auto-Compact: a viewport too short for Full shows Compact (R12)
painter = panel.Painter(fake_area, view)
with draw2d.scaled(painter.zoom, 1.0, 1.0, painter.p.roles):
    session.add("you", "Build a cottage")
    session.add("ai", "On it.", brain="claude")
    big = painter.fits_full()
    view.height, header.y, sidebar.height = 260, 230, 230
    small = painter.fits_full()
    view.height, header.y, sidebar.height = 900, 870, 870
    inner = panel.panel_width(fake_area, view) - 2 * painter.px(panel.PAD)
    compact_h = painter.compact_height(inner)
    full_h = painter.full_height(inner)
check("Full fits a normal viewport", big)
check("a short viewport falls back to Compact", not small)
check("Compact is shorter than Full", compact_h < full_h, (compact_h, full_h))

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
check("the size settings are on while the panel draws", inside == (1.2, 1.3, 0.75, round(12.0 * 1.25)), inside)
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
check("saved logs carry the manifest's version", addon_version() == "2.3.0", addon_version())

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

# Settings opens after a complete click, and stale presses never open it.
rect = (10, 20, 90, 50)
panel.state.open = True
panel.state.area_ptr = 123
panel.state.hits = [(*rect, "settings")]
window_id = bpy.context.window.as_pointer()
epoch = getattr(panel.state, "lifecycle_epoch", 0)


def arm_settings():
    """Arm a Settings press on the current window, epoch and button rectangle."""
    panel.state.settings_click = (rect, window_id, 123, epoch)


arm_settings()
check("Settings accepts a release inside the pressed button", panel_ops.release_settings(bpy.context, 50, 30))
check("Settings consumes the pending click exactly once", not panel_ops.release_settings(bpy.context, 50, 30))
arm_settings()
check("Settings cancels a dragged-away release", not panel_ops.release_settings(bpy.context, 200, 30))
arm_settings()
panel.state.lifecycle_epoch = epoch + 1
check("Settings rejects a press from a previous file", not panel_ops.release_settings(bpy.context, 50, 30))
panel.state.lifecycle_epoch = epoch
arm_settings()
panel.state.area_ptr = 456
check("Settings rejects a changed viewport", not panel_ops.release_settings(bpy.context, 50, 30))
panel.state.area_ptr = 123
arm_settings()
panel_ops.close_panel()
check("closing the panel cancels Settings", panel.state.settings_click is None)
addon_utils.disable("blendwme", default_set=True)
check("unregister removes the panel", not panel._handles and not hasattr(bpy.types, "BLENDWME_OT_panel"))

print(f"ALL {len(passed)} PANEL CHECKS PASSED")
os._exit(0)
