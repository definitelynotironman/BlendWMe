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
import time
import contextlib

import bpy

_jobs = queue.Queue()
_POLL = 0.05
_held = False          # True while a file loads
_epoch = 0
_scope = threading.local()


@contextlib.contextmanager
def scope(epoch=None, deadline=None):
    previous = getattr(_scope, "epoch", None)
    previous_deadline = getattr(_scope, "deadline", None)
    _scope.epoch = _epoch if epoch is None else epoch
    _scope.deadline = deadline if deadline is not None else previous_deadline
    try:
        yield
    finally:
        _scope.epoch = previous
        _scope.deadline = previous_deadline


def expired():
    deadline = getattr(_scope, "deadline", None)
    return deadline is not None and time.time() >= deadline


def epoch():
    return _epoch

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
        self.lock = threading.Lock()
        self.state = "pending"
        self.epoch = _epoch

    def cancel(self, reason):
        with self.lock:
            if self.state != "pending":
                return False
            self.state = "cancelled"
            self.error = RuntimeError(reason)
            self.done.set()
            return True


def call(fn, *args, timeout=600, **kwargs):
    """Run fn on the main thread and wait for its result (from a worker thread)."""
    expected = getattr(_scope, "epoch", None)
    if expected is not None and expected != _epoch:
        raise RuntimeError("This task is obsolete; its pending work was cancelled.")
    deadline = getattr(_scope, "deadline", None)
    if deadline is not None:
        timeout = min(timeout, deadline - time.time())
        if timeout <= 0:
            raise TimeoutError("This request expired before scene work could run.")
    if threading.current_thread() is threading.main_thread():
        return fn(*args, **kwargs)
    job = _Job(fn, args, kwargs)
    _jobs.put(job)
    if not job.done.wait(timeout):
        cancelled = job.cancel("The pending Blender job expired before it ran.")
        raise TimeoutError("Pending Blender job cancelled." if cancelled else
                           "Blender job is already running; cancellation cannot interrupt native work.")
    if job.error is not None:
        raise job.error
    return job.result


def hold(on):
    """Hold every job (True while a file loads)."""
    global _held
    _held = bool(on)


def cancel_all(reason):
    """Answer every waiting job with an error instead of running it."""
    global _epoch
    _epoch += 1
    while True:
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            return
        job.cancel(reason)


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
        if _held or user_busy():
            break
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            break
        with job.lock:
            if job.state != "pending":
                continue
            if job.epoch != _epoch:
                job.state = "cancelled"
                job.error = RuntimeError("The task changed before this job ran.")
                job.done.set()
                continue
            job.state = "running"
        started = time.monotonic()
        try:
            job.result = job.fn(*job.args, **job.kwargs)
        except BaseException as e:  # hand every failure back to the agent
            job.error = e
        finally:
            job.state = "completed"
            job.done.set()
        if time.monotonic() - started > _POLL:
            break
    return _POLL


def register():
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=_POLL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    cancel_all("BlendWMe was disabled")
