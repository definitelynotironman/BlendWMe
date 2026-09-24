"""Run Blender work on Blender's main thread.

Blender's Python API is not thread safe. The AI conversation runs in a
background thread so Blender never freezes while the model is thinking.
Whenever the agent needs to touch the scene, it hands a function to this
bridge. A Blender timer picks it up on the main thread, runs it, and hands
the result back.
"""

import queue
import threading

import bpy

_jobs = queue.Queue()
_POLL = 0.05


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


def _pump():
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
    while not _jobs.empty():
        job = _jobs.get_nowait()
        job.error = RuntimeError("BlendWMe was disabled")
        job.done.set()
