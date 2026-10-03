"""Headless tests of BlendWMe's stability: register twice, file loads and undo while
working, a failing part at startup, the session log, crash reports.

Run (no brain and no subscription is used):

    blender -b --factory-startup --python-exit-code 1 --python tests/test_stability_headless.py
"""

import faulthandler
import os
import sys
import tempfile
import threading
import time
import zipfile

# Blender buffers stdout when it is piped, and os._exit() at the end skips the
# flush, so print each line straight away.
sys.stdout.reconfigure(line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

LOGDIR = tempfile.mkdtemp(prefix="bwm_stability_logs_")
os.environ["BLENDWME_LOG_FOLDER"] = LOGDIR      # never the real logs folder

import bpy  # noqa: E402

import blendwme  # noqa: E402
from blendwme import bridge, journal, lifecycle, logs, overlay, panel  # noqa: E402
from blendwme.relay import relay  # noqa: E402
from blendwme.state import session  # noqa: E402

passed = []


def check(name, cond, detail=""):
    assert cond, f"FAILED {name} {detail}"
    passed.append(name)
    print("ok -", name)


def ours(handlers):
    """BlendWMe's functions in one bpy.app.handlers list."""
    return [f for f in handlers if getattr(f, "__module__", "").startswith("blendwme")]


def handler_count():
    return sum(len(ours(getattr(bpy.app.handlers, n))) for n in ("load_pre", "load_post", "save_post",
                                                               "undo_post", "redo_post"))


TIMERS = (bridge._pump, overlay._redraw_timer, journal._flush_timer)

# ----------------------------------------------------------------------
# 1. register, unregister, register again: nothing left behind, nothing doubled
# ----------------------------------------------------------------------
blendwme.register()
first = handler_count()
check("register adds the load, save and undo handlers", first == 5, first)
check("every handler survives file loads (persistent)",
      all(hasattr(f, "_bpy_persistent") for n in ("load_pre", "load_post", "save_post", "undo_post")
          for f in ours(getattr(bpy.app.handlers, n))))
check("register starts the timers", all(bpy.app.timers.is_registered(t) for t in TIMERS))
check("nothing failed to start", not lifecycle.problems, lifecycle.problems)
check("the crash trace is on", faulthandler.is_enabled())
draw_handles = len(overlay._handles)

blendwme.unregister()
check("unregister removes every handler", handler_count() == 0, handler_count())
check("unregister stops every timer", not any(bpy.app.timers.is_registered(t) for t in TIMERS))
check("unregister removes the draw handlers", not overlay._handles)
check("unregister removes the panel properties", not hasattr(bpy.types.WindowManager, "blendwme"))
check("unregister turns the crash trace off", not faulthandler.is_enabled())

blendwme.register()
check("register again: the same handlers, not doubled", handler_count() == first, handler_count())
check("register again: the same draw handlers", len(overlay._handles) == draw_handles)
overlay.register()
check("a second overlay register does not double its draw handlers", len(overlay._handles) == draw_handles)

# ----------------------------------------------------------------------
# 2. One part failing at startup leaves the rest working, and is reported
# ----------------------------------------------------------------------
blendwme.unregister()
real_overlay_register = overlay.register


def broken():
    raise RuntimeError("no GPU here")


overlay.register = broken
try:
    blendwme.register()
    raised = False
except Exception:
    raised = True
overlay.register = real_overlay_register
check("register never raises, even when a part fails", not raised)
check("the failed part is named for the panel", lifecycle.problems
      and lifecycle.problems[0][0] == "Viewport overlay" and "no GPU here" in lifecycle.problems[0][1],
      lifecycle.problems)
check("the other parts still started", hasattr(bpy.types.WindowManager, "blendwme")
      and bpy.app.timers.is_registered(bridge._pump) and handler_count() == first)
blendwme.unregister()
blendwme.register()
check("a clean register clears the old problem", not lifecycle.problems, lifecycle.problems)

# ----------------------------------------------------------------------
# 3. A different file is opened while a brain works
# ----------------------------------------------------------------------
made = []


def make_ball():
    bpy.ops.mesh.primitive_uv_sphere_add()
    bpy.context.active_object.name = "LateBall"
    made.append(bpy.data.filepath)


box = {}


def worker():
    try:
        box["result"] = bridge.call(make_ball, timeout=20)
    except Exception as e:
        box["error"] = str(e)


relay.running = "claude"         # as if a brain's turn were running
session.set_card({"kind": "working", "title": "Building"})
session.set_pointers([{"object": "Cube"}])
thread = threading.Thread(target=worker)
thread.start()
end = time.time() + 5
while bridge.pending() == 0 and time.time() < end:
    time.sleep(0.01)
check("the brain's scene job is waiting", bridge.pending() == 1)
bpy.ops.wm.read_homefile(use_empty=True)     # opening another file, before the job could run
thread.join(10)
relay.running = None
check("the waiting job is cancelled, never run in the new file", not made and "different file" in box.get("error", ""),
      box)
check("the brain is told to stop", session.stop_flag.is_set())
check("cards and pointers are cleared", session.card is None and not session.pointers)
check("the user is told in plain words", any("opened another file" in e["text"] for e in session.log))
check("the handlers are still there after the load", handler_count() == first, handler_count())
check("the bridge runs again after the load", not bridge.dispatcher.held)

# Repeated loads must invalidate old modal ownership and interaction geometry.
for load_number in range(2):
    previous_epoch = getattr(panel.state, "lifecycle_epoch", 0)
    panel.state.open = True
    panel.state.hits = [(1, 2, 3, 4, "settings")]
    panel.state.settings_click = ("obsolete",)
    panel.state.input_layout = ("obsolete",)
    panel.state.drag = {"obsolete": True}
    panel.state.anim = {"obsolete": True}
    bpy.ops.wm.read_homefile(use_empty=True)
    check(f"load {load_number + 1} invalidates the old modal epoch",
          panel.state.lifecycle_epoch == previous_epoch + 1)
    check(f"load {load_number + 1} clears stale interaction state",
          not panel.state.hits and panel.state.settings_click is None and
          panel.state.input_layout is None and panel.state.drag is None and panel.state.anim is None)
    check(f"load {load_number + 1} retains the requested panel visibility", panel.state.open)
panel.state.open = False
session.stop_flag.clear()
done = {}
threading.Thread(target=lambda: done.update(r=bridge.call(lambda: len(bpy.data.objects), timeout=10))).start()
end = time.time() + 5
while "r" not in done and time.time() < end:
    bridge._pump()
    time.sleep(0.01)
check("scene work runs normally in the new file", done.get("r") == 0, done)

# ----------------------------------------------------------------------
# 4. Undo while working: a pointer to an object that is gone is dropped
# ----------------------------------------------------------------------
bpy.ops.mesh.primitive_cube_add()
bpy.context.active_object.name = "UndoCube"
bpy.ops.mesh.primitive_cube_add()
bpy.context.active_object.name = "StaysCube"
session.set_pointers([{"object": "UndoCube"}, {"object": "StaysCube"}, {"region": "PROPERTIES"}])
bpy.data.objects.remove(bpy.data.objects["UndoCube"])      # what an undo of its step does
lifecycle.on_undo_post(None)
check("undo: pointers to objects that are gone are dropped, the rest stay",
      session.pointers == [{"object": "StaysCube"}, {"region": "PROPERTIES"}], session.pointers)
if bpy.ops.ed.undo.poll():
    bpy.ops.ed.undo_push(message="test")
    bpy.ops.ed.undo()
    check("a real undo with BlendWMe running is fine", True)
else:
    print("skip - undo is not available in background Blender here")

# ----------------------------------------------------------------------
# 5. The bridge holds jobs while asked to (file load, the user's modal action)
# ----------------------------------------------------------------------
bridge.hold(True)
held = {}
threading.Thread(target=lambda: held.update(r=bridge.call(lambda: "ran", timeout=10))).start()
end = time.time() + 2
while bridge.pending() == 0 and time.time() < end:
    time.sleep(0.01)
for _ in range(10):
    bridge._pump()
check("a held bridge runs nothing", "r" not in held and bridge.pending() == 1)
bridge.hold(False)
end = time.time() + 5
while "r" not in held and time.time() < end:
    bridge._pump()
    time.sleep(0.01)
check("released, the job runs", held.get("r") == "ran")
check("no modal action in background Blender: not busy", bridge.user_busy() is False)

# ----------------------------------------------------------------------
# 6. Logs: one folder, the session log on disk, the crash report
# ----------------------------------------------------------------------
session.add("info", "A line for the session log")
path = logs.session_log.path
check("the session log is in the one log folder", os.path.dirname(path) == LOGDIR, path)
text = open(path, encoding="utf-8").read()
check("the session log is written as it happens (survives a crash)", "A line for the session log" in text
      and "opened another file" in text)
crash_dir = tempfile.mkdtemp(prefix="bwm_crash_")
with open(os.path.join(crash_dir, "blender.crash.txt"), "w") as f:
    f.write("# Blender 4.2.3, Commit date: ...\n# backtrace\n")
report = logs.make_crash_report(LOGDIR, [crash_dir])
names = zipfile.ZipFile(report).namelist()
check("the crash report has the session log, the Python trace and Blender's crash file",
      any(n.startswith("session_") for n in names) and "blender.crash.txt" in names
      and "python-crash-trace.txt" in names and "README.txt" in names, names)
for i in range(12):
    logs.session_log.open(LOGDIR)
check("old session logs are cleaned up", len([n for n in os.listdir(LOGDIR) if n.startswith("session_")])
      <= logs.SESSION_LOGS_KEPT)

# ----------------------------------------------------------------------
# 7. The Save Preferences note
# ----------------------------------------------------------------------
class FakePrefs:
    prefs_note_done = False


check("the note shows while the enabled add-on is not saved", lifecycle.needs_prefs_note(FakePrefs())
      == bool(bpy.context.preferences.is_dirty))
done_prefs = FakePrefs()
done_prefs.prefs_note_done = True
check("the note never shows again once handled", not lifecycle.needs_prefs_note(done_prefs))
check("no preferences: no note", not lifecycle.needs_prefs_note(None))

blendwme.unregister()
check("finally: nothing left behind", handler_count() == 0 and not any(bpy.app.timers.is_registered(t) for t in TIMERS))

print(f"\nALL {len(passed)} STABILITY CHECKS PASSED")
os._exit(0)
