"""Keeping Blender safe while BlendWMe works: file loads, undo, crash traces, startup.

- A different file is opened while a brain works: before the load, the brain is
  stopped, waiting scene jobs are cancelled and cards and pointers are cleared,
  so nothing runs on a scene that is being freed. After the load the bridge runs
  again and the journal of the new file is picked up (journal.on_file_loaded).
- Undo while a brain works: nothing in BlendWMe keeps Python references to
  Blender data between steps (objects are always looked up by name), so an undo
  only means "the scene changed". Pointers to objects that are gone are dropped.
- Crashes: Python's faulthandler writes the Python stack of a crash into
  python-crash-trace.txt in the log folder. Blender writes its own crash file.
  The panel's "Make crash report" zips both with the session logs.
- Startup: every part registers on its own. A part that fails is reported in
  the panel instead of leaving the whole add-on switched off.

Every handler here is @persistent (it survives file loads) and is removed again
in unregister().
"""

import faulthandler
import traceback

import bpy
from bpy.app.handlers import persistent

from . import bridge, logs
from .state import session

problems = []            # [(part, message)] parts that failed to start
_trace_file = None       # the open python-crash-trace.txt
_load_note = ""          # said after a file load that stopped the brain


# ----------------------------------------------------------------------
# Startup and shutdown, one part at a time
# ----------------------------------------------------------------------
def safe_register(part, fn):
    """Run one part's register(); a failure is recorded, never raised."""
    try:
        fn()
        return True
    except Exception as e:
        traceback.print_exc()
        problems.append((part, f"{type(e).__name__}: {e}"))
        logs.session_log.write("error", f"{part} did not start: {e}")
        return False


def safe_unregister(part, fn):
    try:
        fn()
    except Exception:
        print(f"BlendWMe: {part} did not unregister cleanly")
        traceback.print_exc()


# ----------------------------------------------------------------------
# File loads and undo
# ----------------------------------------------------------------------
@persistent
def on_load_pre(*_args):
    bridge.hold(True)
    try:
        from .relay import relay
        working = relay.is_running()
        bridge.cancel_all("A different file was opened, so BlendWMe stopped this step.")
        if working:
            relay.stop()
        session.set_card(None)
        session.set_pointers([])
        global _load_note
        _load_note = ("You opened another file, so BlendWMe stopped. "
                      "The other file is exactly as you last saved it.") if working else ""
        logs.session_log.write("file", "load: " + (bpy.data.filepath or "unsaved file") + " -> new file")
    except Exception:
        traceback.print_exc()


@persistent
def on_load_post(*_args):
    global _load_note
    bridge.hold(False)
    logs.session_log.write("file", "loaded: " + (bpy.data.filepath or "new file"))
    from . import journal
    journal.on_file_loaded()            # the new file's journal (this resets the conversation view)
    if _load_note:
        session.add("info", _load_note)
        _load_note = ""
    # Blender ends modal operators when a file loads: start the panel again.
    from . import panel, panel_ops
    if panel.state.open and not bpy.app.background:
        bpy.app.timers.register(panel_ops.reopen_after_load, first_interval=0.3)


@persistent
def on_undo_post(*_args):
    try:
        names = set(bpy.data.objects.keys())
        kept = [p for p in session.pointers if "object" not in p or p["object"] in names]
        if len(kept) != len(session.pointers):
            session.set_pointers(kept)
        session.changed_flag = True
    except Exception:
        traceback.print_exc()


_HANDLERS = (("load_pre", on_load_pre), ("load_post", on_load_post),
             ("undo_post", on_undo_post), ("redo_post", on_undo_post))


# ----------------------------------------------------------------------
# Crash trace
# ----------------------------------------------------------------------
def enable_crash_trace(folder=""):
    """Write the Python stack of a hard crash into the log folder."""
    global _trace_file
    disable_crash_trace()
    try:
        _trace_file = open(logs.crash_trace_path(folder), "a", encoding="utf-8")
        faulthandler.enable(file=_trace_file, all_threads=True)
    except (OSError, RuntimeError, ValueError) as e:
        print("BlendWMe: crash trace is off:", e)
        _trace_file = None


def disable_crash_trace():
    global _trace_file
    if _trace_file is not None:
        try:
            faulthandler.disable()
            _trace_file.close()
        except (OSError, ValueError):
            pass
    _trace_file = None


# ----------------------------------------------------------------------
# "Save Preferences so BlendWMe stays on"
# ----------------------------------------------------------------------
def needs_prefs_note(prefs):
    """True when the enabled add-on is not saved in Blender's preferences yet.

    Blender keeps "this add-on is enabled" in its saved preferences. With
    auto-save on, they are saved only when Blender quits normally, so after a
    crash the add-on comes back switched off (and its shortcut with it).
    """
    if prefs is None or prefs.prefs_note_done:
        return False
    try:
        return bool(bpy.context.preferences.is_dirty)
    except AttributeError:
        return False


# ----------------------------------------------------------------------
# Register
# ----------------------------------------------------------------------
def register():
    from . import prefs
    folder = prefs.log_folder()
    logs.session_log.open(folder)
    logs.session_log.write("start", f"BlendWMe in Blender {bpy.app.version_string}")
    session.sink = logs.session_log.write
    enable_crash_trace(folder)
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)


def unregister():
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        while fn in handlers:
            handlers.remove(fn)
    session.sink = None
    disable_crash_trace()
    logs.session_log.write("stop", "BlendWMe switched off")
    logs.session_log.close()
