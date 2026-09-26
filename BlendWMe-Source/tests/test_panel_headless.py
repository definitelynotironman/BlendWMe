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
import blendwme  # noqa: E402

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
