"""BlendWMe 1.0 - an AI Blender artist that builds, demonstrates and teaches.

Architecture (one line each):
    llm.py        talks to Nebius with standard tool calling
    agent.py      the loop: think -> use tools -> look -> fix -> finish
    tools.py      the small, powerful tool list the model sees
    executor.py   run_python on the live scene, with undo steps
    look.py       viewport and multi-angle captures for the vision model
    scene_info.py scene summaries, snapshots and "what did the user change"
    knowledge.py  skill cards + live Blender API lookup
    overlay.py    Graphite Paper card and red pointers in the viewport
    ui.py         the sidebar panel and buttons
    bridge.py     runs Blender work safely on Blender's main thread
"""

from . import bridge, credentials, overlay, prefs, ui
from .agent import agent


def register():
    prefs.register()
    ui.register()
    bridge.register()
    overlay.register()


def unregister():
    agent.stop()
    overlay.unregister()
    bridge.unregister()
    ui.unregister()
    prefs.unregister()
    credentials.clear_memory()
