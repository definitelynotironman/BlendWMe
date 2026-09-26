"""Run Blender work on Blender's main thread.

Blender's Python API is not thread safe. The AI conversation runs in a
background thread so Blender never freezes while the model is thinking.
Whenever the agent needs to touch the scene, it hands a function to this
bridge. A Blender timer picks it up on the main thread, runs it, and hands
the result back.

Some moments are not safe for scene work, and the job waits for them to pass:
- while a file is loading (the old scene is being freed);
- while the user is in the middle of a modal action in Blender (moving an
  object, a sculpt stroke, knife, view navigation) or a render is running.
  Changing data under a running modal operator can crash Blender.
When a different file is opened, waiting jobs are cancelled, never run in it.
"""

import queue
import threading

import bpy

_jobs = queue.Queue()
_POLL = 0.05
_held = False          # True while a file loads

# Modal operators from these Blender areas edit or display the scene: wait for them.
# (Add-ons' own long-running modals have other prefixes and are not waited for.)
_RISKY_MODALS = {"TRANSFORM", "SCULPT", "PAINT", "PAINTCURVE", "VIEW3D", "MESH", "OBJECT", "CURVE", "CURVES",
                 "ARMATURE", "POSE", "GPENCIL", "GREASE_PENCIL", "PARTICLE", "UV", "SCREEN", "RENDER",
                 "SCULPT_CURVES", "ED", "WM"}


class _Job:
    def __init__(self, fn, args, kwargs):
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.done = threading.Event()
        self.result = None
        self.error = None


def call(fn, *args, timeout=600, **kwargs):
    """Run fn on the main thread and wait for its result (from a worker thread)."""
    if threading.current_thread() is threading.main_thread():
        return fn(*args, **kwargs)
    job = _Job(fn, args, kwargs)
    _jobs.put(job)
    if not job.done.wait(timeout):
        raise TimeoutError("Blender did not respond in time (is a modal operator running?)")
    if job.error is not None:
        raise job.error
    return job.result


def hold(on):
    """Hold every job (True while a file loads)."""
    global _held
    _held = bool(on)


def cancel_all(reason):
    """Answer every waiting job with an error instead of running it."""
    while True:
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            return
        job.error = RuntimeError(reason)
        job.done.set()


def pending():
    return _jobs.qsize()


def user_busy():
    """True while a render runs or the user is in the middle of a modal action."""
    try:
        if bpy.app.is_job_running("RENDER"):
            return True
        for window in bpy.context.window_manager.windows:
            for op in window.modal_operators:
                # "TRANSFORM_OT_translate" or "transform.translate": the part before is the area
                name = op.bl_idname
                area = (name.split("_OT_")[0] if "_OT_" in name else name.split(".")[0]).upper()
                if area in _RISKY_MODALS:
                    return True
    except Exception:
        return False
    return False


def _pump():
    if _held or _jobs.empty() or user_busy():
        return _POLL
    # Run at most a few jobs per tick so the UI stays responsive.
    for _ in range(4):
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            break
        try:
            job.result = job.fn(*job.args, **job.kwargs)
        except BaseException as e:  # hand every failure back to the agent
            job.error = e
        finally:
            job.done.set()
    return _POLL


def register():
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=_POLL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    cancel_all("BlendWMe was disabled")
