"""BlendWMe 2.0 - an AI Blender artist that builds, demonstrates and teaches.

It runs on the plans you already have: Claude Code (main brain) and Codex CLI
(second brain). BlendWMe never sees your logins and needs no API key.

Architecture (one line each):
    relay.py         the panel's one entry point: active brain, hand-over, limits
    engines/         the brains: claude_code and codex, behind one small interface
    journal.py       the project journal ("the baton"), saved with the .blend
    socket_bridge.py the local socket the MCP server (blendwme_mcp, outside Blender) calls
    tool_host.py     BlendWMe's tools for the brains: run_python, look, teach_step ...
    executor.py      run_python on the live scene, with undo steps and rollback
    look.py          viewport and multi-angle captures
    scene_info.py    scene summaries, snapshots, "what did the user change", who made what
    knowledge.py     skill cards + live Blender API lookup
    overlay.py       Graphite Paper card and red pointers in the viewport
    ui.py            the sidebar panel and buttons; setup_checks.py the setup buttons
    bridge.py        runs Blender work safely on Blender's main thread
"""

import bpy

from . import bridge, journal, overlay, prefs, socket_bridge, ui
from .relay import relay


def _start_bridge():
    # The brains reach Blender only through this local socket, so it always starts.
    print("BlendWMe:", socket_bridge.start())
    return None   # run once


def register():
    prefs.register()
    ui.register()
    bridge.register()
    overlay.register()
    journal.register()
    # Start after registration finished, when the preferences exist.
    bpy.app.timers.register(_start_bridge, first_interval=0.5)


def unregister():
    socket_bridge.stop()
    journal.unregister()
    relay.stop()
    overlay.unregister()
    bridge.unregister()
    ui.unregister()
    prefs.unregister()
