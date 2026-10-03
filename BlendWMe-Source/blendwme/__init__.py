# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""BlendWMe - an AI Blender artist that builds, demonstrates and teaches.

It runs on the plans you already have: Claude Code (main brain) and Codex CLI
(second brain). BlendWMe never sees your logins and needs no API key.

Architecture (one line each):
    relay.py         the panel's one entry point: active brain, hand-over, limits
    engines/         the brains: claude_code and codex, behind one small interface
    journal.py       the project journal ("the baton"), saved with the .blend
    socket_bridge.py the local socket the MCP server (blendwme_mcp, outside Blender) calls
    tool_host.py     BlendWMe's tools for the brains: run_python, look, teach_step ...
    executor.py      run_python on the live scene, with undo steps and rollback
    repair.py        repairs mechanical API slips; precheck.py rejects names that do not exist
    recovery.py      a saved .blend checkpoint before each step, and restoring a failed one
    lesson.py        typed, read-only checks of what a Teach step asked the user to do
    helpers.py       the bwm kit (with shapes.py and furniture.py) the brains model with
    look.py          viewport and multi-angle captures
    scene_info.py    scene summaries, snapshots, "what did the user change", who made what
    knowledge.py     skill cards + live Blender API lookup
    panel.py         the BlendWMe panel drawn in the 3D Viewport (the approved design)
    panel_ops.py     its modal operator: clicks, typing, shortcuts, commands
    draw2d.py        shapes, fonts and text for the panel and the indicators; theme.py the themes
    richtext.py      bold, code and lists in conversation text; reader.py the full-text reader
    overlay.py       the indicators in Blender: radar rings, pencil circles, building outline
    ui.py            operators and the small sidebar launcher; setup_checks.py the setup checks
    bridge.py        runs Blender work safely on Blender's main thread
    lifecycle.py     file loads, undo, crash trace, safe startup, the Save Preferences note
    keymaps.py       the shortcuts, in Blender's add-on keyconfig
    logs.py          one log folder: task logs, the session log, crash reports
"""

import bpy

from . import bridge, journal, keymaps, lifecycle, overlay, panel, prefs, socket_bridge, ui
from .relay import relay

__author__ = "definitelynotironman"


def _start_bridge():
    """Start the local socket the brains use (once, shortly after registration)."""
    # The brains reach Blender only through this local socket, so it always starts.
    try:
        print("BlendWMe:", socket_bridge.start())
    except Exception as e:
        lifecycle.problems.append(("Connection to the brains", str(e)))
    return None   # run once


# The parts, in order. Each registers on its own: one failing part is shown in the
# panel and the others keep working, instead of the whole add-on staying off.
_PARTS = (
    ("Settings", prefs),
    ("Safety handlers and logs", lifecycle),     # early, so later failures reach the session log
    ("Panel", ui),
    ("Main-thread bridge", bridge),
    ("Viewport overlay", overlay),
    ("BlendWMe panel", panel),
    ("Project journal", journal),
    ("Shortcuts", keymaps),
)


def register():
    """Register every part on its own, then start the bridge from a timer."""
    lifecycle.problems.clear()
    for name, part in _PARTS:
        lifecycle.safe_register(name, part.register)
    # Start after registration finished, when the preferences exist.
    if not bpy.app.timers.is_registered(_start_bridge):
        bpy.app.timers.register(_start_bridge, first_interval=0.5)


def unregister():
    """Stop the bridge and any brain, then unregister the parts in reverse order."""
    lifecycle.safe_unregister("Connection to the brains", socket_bridge.stop)
    lifecycle.safe_unregister("Brain", relay.stop)
    if bpy.app.timers.is_registered(_start_bridge):
        bpy.app.timers.unregister(_start_bridge)
    for name, part in reversed(_PARTS):
        lifecycle.safe_unregister(name, part.unregister)
