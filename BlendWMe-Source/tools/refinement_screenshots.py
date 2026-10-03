# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Disposable windowed Blender fixture. No subscription clients are started.

Run with --factory-startup --python tools/refinement_screenshots.py -- OUTPUT.
Use BLENDER_USER_RESOURCES pointing to a disposable profile.
"""
import os
import sys
import tempfile
import json
import traceback

import bpy
import addon_utils

__author__ = "definitelynotironman"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BLENDWME_LOG_FOLDER"] = tempfile.mkdtemp(prefix="bwm_gui_logs_")
output = os.path.abspath(sys.argv[sys.argv.index("--") + 1])
os.makedirs(output, exist_ok=True)
bpy.context.preferences.view.show_splash = False
if bpy.app.background:
    bpy.ops.wm.save_userpref()
    raise SystemExit
addon_utils.enable("blendwme", default_set=True)
from blendwme import draw2d, panel, panel_ops, prefs, theme, workspace
from blendwme.state import session


def trace(stage):
    """Keep failure evidence even when an assertion ends a Blender timer."""
    from blendwme import lifecycle
    details = {
        "stage": stage, "open": panel.state.open, "hits": len(panel.state.hits),
        "area": panel.state.area_ptr, "theme": theme.current_name(),
        "epoch": getattr(panel.state, "lifecycle_epoch", 0),
        "handles": len(panel._handles), "modal_alive": panel_ops.panel_alive(),
        "load_pre": [fn.__module__ + "." + fn.__name__ for fn in bpy.app.handlers.load_pre],
        "load_post": [fn.__module__ + "." + fn.__name__ for fn in bpy.app.handlers.load_post],
        "problems": lifecycle.problems,
    }
    with open(os.path.join(output, "trace.jsonl"), "a", encoding="utf-8") as file:
        file.write(json.dumps(details) + "\n")
    print("GUI TRACE", details, flush=True)


def failed():
    """Print the error, record it, take a failure screenshot and quit Blender."""
    traceback.print_exc()
    trace("failed")
    bpy.ops.screen.screenshot(filepath=os.path.join(output, "failed.png"))
    bpy.ops.wm.quit_blender()

workspace.set_folder(tempfile.mkdtemp(prefix="bwm_gui_workspace_"))
load_count = 0
draft_before_load = ""
if os.environ.get("BWM_BASELINE_TYPOGRAPHY"):
    theme.FONT_ROLES["Y2K"] = {"HAND": ("BODY", 0.8)}
if os.environ.get("BWM_BASELINE_GRAPHICS"):
    start = draw2d._FRAG.index("  float coverage")
    end = draw2d._FRAG.index("  if (c.a", start)
    draw2d._FRAG = draw2d._FRAG[:start] + "  if (d > 0.0) { discard; }\n  vec4 c = (d + border > 0.0) ? edge : fill;\n" + draw2d._FRAG[end:]
prefs.addon_prefs().start_bridge = False
prefs.addon_prefs().reduce_motion = "REDUCE"
session.add("you", "Teach me to select the cube and explain what happens.")
session.add("ai", "**What happens**\n\n- Select the cube.\n- Use `G` to move it.\n\nYour scene stays editable.")


def prepare():
    """Switch to the 2000s theme, open the panel and schedule the chosen scenario."""
    prefs.addon_prefs().theme = "Y2K"
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    panel_ops.open_panel(bpy.context, area)
    area.tag_redraw()
    action = os.environ.get("BWM_GUI_ACTION", "")
    bpy.app.timers.register(new_file if action == "new_file" else
                            settings_press if action == "settings" else capture, first_interval=1.0)


def new_file():
    """Remember the draft, then load the startup file (File > New)."""
    global load_count, draft_before_load
    load_count += 1
    draft_before_load = panel.state.text
    trace("before_new_file")
    bpy.ops.wm.read_homefile()
    bpy.app.timers.register(validate_new_file, first_interval=2.0)


def validate_new_file():
    """After a load: the panel must be open with hit targets, and typing must still work."""
    trace("after_new_file")
    try:
        assert panel.state.open and panel.state.hits, "New File did not restore the panel"
    except AssertionError:
        failed()
        return
    bpy.ops.screen.screenshot(filepath=os.path.join(output, "restored-panel.png"))
    window = bpy.context.window
    for char in "fresh draft":
        window.event_simulate(type="SPACE" if char == " " else char.upper(), value="PRESS", unicode=char)
    def check_input():
        """The typed text must reach the draft; repeat the load once, then test a second window."""
        try:
            assert panel.state.text == draft_before_load + "fresh draft", "New File lost typing/modal input"
        except AssertionError:
            failed()
            return
        print("GUI CHECK PASSED: New File restores drawing and draft typing", flush=True)
        if load_count < 2:
            new_file()
        else:
            check_second_window()
    bpy.app.timers.register(check_input, first_interval=0.5)


def check_second_window():
    """Unsupported panel transfer must preserve its original window owner."""
    original = bpy.context.window_manager.windows[0]
    owner = panel.state.window_ptr
    bpy.ops.wm.window_new()
    other = next(window for window in bpy.context.window_manager.windows if window != original)
    area = next(area for area in other.screen.areas if area.type == "VIEW_3D")
    with bpy.context.temp_override(window=other, area=area):
        assert not panel_ops.open_panel(bpy.context, area), "Second window stole panel ownership"
        assert panel.state.window_ptr == owner and panel_ops.panel_alive(), "Original panel lost ownership"
        bpy.ops.wm.window_close()
    print("GUI CHECK PASSED: second window preserves original panel ownership", flush=True)
    bpy.app.timers.register(settings_press, first_interval=0.5)


def settings_press():
    """Press the drawn Settings button with simulated mouse events."""
    hit = next(h for h in panel.state.hits if h[-1] == "settings")
    area = next(a for a in bpy.context.screen.areas if a.as_pointer() == panel.state.area_ptr)
    region = next(r for r in area.regions if r.type == "WINDOW")
    x = round((hit[0] + hit[2]) / 2 + region.x)
    y = round((hit[1] + hit[3]) / 2 + region.y)
    window = bpy.context.window
    window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
    window.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
    def release():
        """Screenshot the pressed state, then release over the button."""
        bpy.ops.screen.screenshot(filepath=os.path.join(output, "settings-pressed.png"))
        print("SETTINGS REQUESTED", panel_ops.settings_asked[0], "CLICK", x, y, flush=True)
        window.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
        bpy.app.timers.register(capture, first_interval=1.0)
    bpy.app.timers.register(release, first_interval=0.1)


def capture():
    """Check Settings was invoked (when expected), screenshot the panel and quit."""
    if os.environ.get("BWM_GUI_ACTION") in ("new_file", "settings"):
        assert panel_ops.settings_asked[0] > 0, "Settings click was not invoked on release"
    print("CAPTURE STATE", panel.state.open, panel.state.area_ptr, len(panel.state.hits),
          theme.current_name(), len(panel._handles), flush=True)
    bpy.ops.screen.screenshot(filepath=os.path.join(output, "y2k-full.png"))
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(prepare, first_interval=1.0)
