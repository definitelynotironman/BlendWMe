"""Disposable windowed Blender fixture. No subscription clients are started.

Run with --factory-startup --python tools/refinement_screenshots.py -- OUTPUT.
Use BLENDER_USER_RESOURCES pointing to a disposable profile.
"""
import os
import sys
import tempfile

import bpy
import addon_utils

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BLENDWME_LOG_FOLDER"] = tempfile.mkdtemp(prefix="bwm_gui_logs_")
output = os.path.abspath(sys.argv[sys.argv.index("--") + 1])
os.makedirs(output, exist_ok=True)
bpy.context.preferences.view.show_splash = False
if bpy.app.background:
    bpy.ops.wm.save_userpref()
    raise SystemExit
addon_utils.enable("blendwme", default_set=True)
from blendwme import panel, prefs, theme, workspace
from blendwme.state import session

workspace.set_folder(tempfile.mkdtemp(prefix="bwm_gui_workspace_"))
if os.environ.get("BWM_BASELINE_TYPOGRAPHY"):
    theme.FONT_ROLES["Y2K"] = {"HAND": ("BODY", 0.8)}
prefs.addon_prefs().start_bridge = False
prefs.addon_prefs().reduce_motion = "REDUCE"
session.add("you", "Teach me to select the cube and explain what happens.")
session.add("ai", "**What happens**\n\n- Select the cube.\n- Use `G` to move it.\n\nYour scene stays editable.")


def prepare():
    prefs.addon_prefs().theme = "Y2K"
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    panel.state.open = True
    panel.state.area_ptr = area.as_pointer()
    area.tag_redraw()
    bpy.app.timers.register(capture, first_interval=1.0)


def capture():
    bpy.ops.screen.screenshot(filepath=os.path.join(output, "y2k-full.png"))
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(prepare, first_interval=1.0)
