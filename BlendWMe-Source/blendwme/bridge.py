# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
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

import contextlib
import queue
import threading
import time

import bpy

__author__ = "definitelynotironman"

_POLL = 0.05

# Modal operators from these Blender areas edit or display the scene: wait for them.
# (Add-ons' own long-running modals have other prefixes and are not waited for.)
_RISKY_MODALS = {"TRANSFORM", "SCULPT", "PAINT", "PAINTCURVE", "VIEW3D", "MESH", "OBJECT", "CURVE", "CURVES",
                 "ARMATURE", "POSE", "GPENCIL", "GREASE_PENCIL", "PARTICLE", "UV", "SCREEN", "RENDER",
                 "SCULPT_CURVES", "ED", "WM"}


class Job:
    """One function waiting to run on the main thread, with an explicit lifecycle.

    state moves pending -> running -> completed, or pending -> cancelled. Only a
    pending job can be cancelled: once native Blender work runs it cannot be
    interrupted, and the bridge never pretends otherwise.
    """

    def __init__(self, fn, args, kwargs, deadline, scope_state):
        """Capture the call and the caller's epoch, deadline and validator."""
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.done = threading.Event()
        self.result = None
        self.error = None
        self.lock = threading.Lock()
        self.state = "pending"
        self.epoch = scope_state.current_epoch()
        self.deadline = deadline
        self.validate = getattr(scope_state.local, "validate", None)

    def cancel(self, reason):
        """Cancel a pending job (its caller gets RuntimeError(reason)). False if it already started."""
        with self.lock:
            if self.state != "pending":
                return False
            self.state = "cancelled"
            self.error = RuntimeError(reason)
            self.done.set()
            return True


class MainThreadDispatcher:
    """The queue between worker threads and Blender's main thread.

    Every job is bound to the task *epoch* it was created in. ``cancel_all``
    advances the epoch (New task, file load, Stop), so a job queued for older
    work can never run against the new task or file, even if it was already
    waiting when the change happened. A thread-local *scope* carries the
    caller's epoch, deadline and a validator that is re-checked on the main
    thread immediately before dispatch.
    """

    def __init__(self):
        """Empty queue, not held, epoch 0."""
        self.jobs = queue.Queue()
        self.held = False          # True while a file loads
        self.epoch_value = 0
        self.local = threading.local()

    # -- scope ---------------------------------------------------------------
    @contextlib.contextmanager
    def scope(self, epoch=None, deadline=None, validate=None):
        """Run a block with a task epoch, an absolute deadline and a validator.

        Nested scopes keep the earliest deadline and inherit what they do not set.
        """
        previous = getattr(self.local, "epoch", None)
        previous_deadline = getattr(self.local, "deadline", None)
        previous_validate = getattr(self.local, "validate", None)
        self.local.epoch = (previous if previous is not None else self.epoch_value) if epoch is None else epoch
        if deadline is not None and previous_deadline is not None:
            self.local.deadline = min(deadline, previous_deadline)
        else:
            self.local.deadline = deadline if deadline is not None else previous_deadline
        self.local.validate = validate if validate is not None else previous_validate
        try:
            yield
        finally:
            self.local.epoch = previous
            self.local.deadline = previous_deadline
            self.local.validate = previous_validate

    def current_epoch(self):
        """The epoch of the current scope, or the global epoch outside any scope."""
        epoch = getattr(self.local, "epoch", None)
        return self.epoch_value if epoch is None else epoch

    def expired(self):
        """True when the current scope's deadline has passed."""
        deadline = getattr(self.local, "deadline", None)
        return deadline is not None and time.time() >= deadline

    def epoch(self):
        """The current global task epoch."""
        return self.epoch_value

    def current_valid(self):
        """True while this scope's epoch is current and its deadline has not passed."""
        expected = getattr(self.local, "epoch", None)
        return (expected is None or expected == self.epoch_value) and not self.expired()

    # -- calling ---------------------------------------------------------------
    def call(self, fn, *args, timeout=600, **kwargs):
        """Run fn on the main thread and wait for its result (from a worker thread).

        On the main thread fn runs directly, after the scope's validator.
        Raises RuntimeError for obsolete work and TimeoutError when the job
        expired before it could run.
        """
        expected = getattr(self.local, "epoch", None)
        if expected is not None and expected != self.epoch_value:
            raise RuntimeError("This task is obsolete; its pending work was cancelled.")
        deadline = getattr(self.local, "deadline", None)
        if deadline is not None:
            timeout = min(timeout, deadline - time.time())
            if timeout <= 0:
                raise TimeoutError("This request expired before scene work could run.")
        if threading.current_thread() is threading.main_thread():
            validate = getattr(self.local, "validate", None)
            if validate is not None and not validate():
                raise RuntimeError("The request belongs to an obsolete task, scene or connection.")
            return fn(*args, **kwargs)
        job = Job(fn, args, kwargs, time.time() + timeout, self)
        self.jobs.put(job)
        if not job.done.wait(timeout):
            cancelled = job.cancel("The pending Blender job expired before it ran.")
            raise TimeoutError("Pending Blender job cancelled." if cancelled else
                               "Blender job is already running; cancellation cannot interrupt native work.")
        if job.error is not None:
            raise job.error
        return job.result

    def hold(self, on):
        """Hold every job (True while a file loads)."""
        self.held = bool(on)

    def cancel_all(self, reason):
        """Advance the epoch and answer every waiting job with an error instead of running it."""
        self.epoch_value += 1
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            job.cancel(reason)

    def pending(self):
        """How many jobs are waiting."""
        return self.jobs.qsize()

    @staticmethod
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

    # -- the timer --------------------------------------------------------------
    def pump(self):
        """Blender timer callback: run up to four pending jobs, then yield to the UI.

        Returns the next interval. A job whose epoch is obsolete is cancelled
        instead of run; every other job is re-validated inside its own scope.
        """
        if self.held or self.jobs.empty() or self.user_busy():
            return _POLL
        for _ in range(4):
            if self.held or self.user_busy():
                break
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            if not self._start(job):
                continue
            started = time.monotonic()
            self._run(job)
            if time.monotonic() - started > _POLL:
                break
        return _POLL

    def _start(self, job):
        """Move a pending job to running; cancel it if its epoch is obsolete. True when it should run."""
        with job.lock:
            if job.state != "pending":
                return False
            if job.epoch != self.epoch_value:
                job.state = "cancelled"
                job.error = RuntimeError("The task changed before this job ran.")
                job.done.set()
                return False
            job.state = "running"
            return True

    def _run(self, job):
        """Run a started job in its own scope and hand back its result or error."""
        try:
            # The socket's earlier validation is not authority to mutate a later
            # task/scene. Validate on the main thread at every dispatch boundary.
            with self.scope(job.epoch, job.deadline, job.validate):
                if not self.current_valid():
                    raise RuntimeError("The request expired or was cancelled before dispatch.")
                job.result = self.call(job.fn, *job.args, **job.kwargs)
        except BaseException as e:  # hand every failure back to the agent
            job.error = e
        finally:
            job.state = "completed"
            job.done.set()


# One dispatcher per Blender process. ``_pump`` is stored once so Blender's
# timer API sees the same callable when it registers and unregisters it.
dispatcher = MainThreadDispatcher()
scope = dispatcher.scope
expired = dispatcher.expired
epoch = dispatcher.epoch
current_valid = dispatcher.current_valid
call = dispatcher.call
hold = dispatcher.hold
cancel_all = dispatcher.cancel_all
pending = dispatcher.pending
user_busy = dispatcher.user_busy
_pump = dispatcher.pump


def register():
    """Start the main-thread timer (persistent across file loads)."""
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=_POLL, persistent=True)


def unregister():
    """Stop the timer and cancel whatever is still waiting."""
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    cancel_all("BlendWMe was disabled")
