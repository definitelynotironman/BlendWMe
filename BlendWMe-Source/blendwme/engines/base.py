# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""The one small interface every brain implements.

    engine.start(message, on_event, config)   one turn in the background
    engine.interrupt()                        stop it now
    engine.is_running()
    engine.session_id                         resumes the brain's own conversation
    engine.reset()                            forget it (a new task)

A brain reports what happens as standard events, so the panel never needs to
know which brain is working:

    {"type": "text", "text": "...", "delta": True}   streamed reply text (delta) or a whole message
    {"type": "tool_call", "tool": "run_python", "args": {...}, "id": "..."}
    {"type": "tool_result", "id": "...", "ok": True, "text": "..."}
    {"type": "done", "text": "final reply"}
    {"type": "error", "text": "..."}
    {"type": "limit_reached", "text": "the brain's own message", "resets": "5pm" or ""}

Every turn ends with exactly one of done, error or limit_reached.
"""

import json
import os
import subprocess
import threading

from . import limits

__author__ = "definitelynotironman"

TERMINAL = ("done", "error", "limit_reached")

# Never forwarded to a brain program: it must use the user's subscription sign-in,
# never a pay-per-token key, and Blender's own Python settings would break the MCP server.
_DROP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
             "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONNOUSERSITE")


def clean_env(extra=None):
    """The current environment without pay-per-token keys or Blender's Python settings, plus extra."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in _DROP_ENV}
    env.update(extra or {})
    return env


def no_window():
    """Popen creation flags that stop a console window flashing up on Windows (0 elsewhere)."""
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class Engine:
    """A brain BlendWMe can talk to: start a turn, stream standard events, interrupt, reset.

    Events are dicts with a `type`: text, tool_call, tool_result and exactly one
    terminal event per turn (done, error or limit_reached).
    """
    name = "brain"
    label = "Brain"

    def __init__(self):
        """No session yet, no running turn."""
        self.session_id = None
        self.thread = None
        self._stop = threading.Event()

    def is_running(self):
        """True while a turn's thread is alive."""
        return self.thread is not None and self.thread.is_alive()

    def reset(self):
        """Forget the brain's own conversation (the next turn starts fresh)."""
        self.session_id = None

    def interrupt(self):
        """Ask the running turn to stop."""
        self._stop.set()

    def start(self, message, on_event, config):
        """Begin one turn with message; on_event receives the standard events."""
        raise NotImplementedError


class ProcessEngine(Engine):
    """A brain that runs as a program printing one JSON event per line (Claude Code, Codex)."""

    def __init__(self):
        """No program started yet; tests may set command_override."""
        super().__init__()
        self.proc = None
        self.command_override = None   # tests replace the program with a fake one
        self.last_command = []

    # -- what each brain defines -----------------------------------------
    def build(self, message, config):
        """(argv, stdin text or None, extra environment)."""
        raise NotImplementedError

    def parse(self, obj):
        """Standard events for one JSON line from the program."""
        raise NotImplementedError

    def parse_text_line(self, line):
        """Events for a stdout line that is not JSON (default: nothing)."""
        return []

    # -- the turn ---------------------------------------------------------
    def start(self, message, on_event, config):
        """Start the program for one turn in a background thread.

        The last turn sends "done" from inside its thread just before that thread ends;
        a message sent in that moment waits briefly instead of being refused.
        """
        if self.is_running() and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)
        if self.is_running():
            raise RuntimeError(f"{self.label} is already working.")
        self._stop.clear()
        self.proc = None
        argv, stdin_text, extra_env = self.build(message, config)
        if self.command_override:
            argv = list(self.command_override) + argv[1:]
        self.last_command = argv
        self.thread = threading.Thread(target=self._run, args=(argv, stdin_text, extra_env, on_event, config),
                                       name=f"BlendWMe {self.label}", daemon=True)
        self.thread.start()

    def _run(self, argv, stdin_text, extra_env, on_event, config):
        """Run the program, translate its output into events and end with exactly one terminal event.

        Malformed JSON events or parser failures end the turn with one error and stop
        the program, instead of being shown as text.
        """
        ended = []

        def emit(event):
            """Forward one event (tagged with the brain); drop anything after the terminal event."""
            if ended:
                return              # one terminal event per turn
            event = dict(event, brain=self.name)
            if event["type"] in TERMINAL:
                ended.append(event)
            on_event(event)

        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, cwd=config.get("workspace") or None,
                                         env=clean_env(extra_env), creationflags=no_window())
        except (OSError, ValueError, TypeError) as e:
            emit({"type": "error", "text": f"Could not start {self.label}: {e}. Run the setup check."})
            return
        if self._stop.is_set():
            self.interrupt()        # Stop was pressed while the program was starting
        errors = []
        err_thread = threading.Thread(target=self._read_stderr, args=(errors,), daemon=True)
        err_thread.start()
        try:
            if stdin_text is not None:
                self.proc.stdin.write(stdin_text.encode("utf-8"))
            self.proc.stdin.close()
        except OSError:
            pass
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                try:
                    events = self.parse_text_line(line)
                except Exception:
                    emit({"type": "error", "text": f"{self.label} sent an invalid text event."})
                    self.interrupt()
                    break
            else:
                try:
                    if not isinstance(obj, dict):
                        raise TypeError("An engine event must be an object")
                    events = self.parse(obj)
                except Exception as e:
                    emit({"type": "error", "text": f"{self.label} sent an invalid event ({type(e).__name__})."})
                    self.interrupt()
                    break
            try:
                for event in events or []:
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        raise ValueError("Malformed engine event")
                    emit(event)
            except Exception:
                emit({"type": "error", "text": f"{self.label} sent a malformed event."})
                self.interrupt()
                break
        self.proc.wait()
        err_thread.join(timeout=2)
        tail = "\n".join(errors[-8:]).strip()
        if self._stop.is_set():
            emit({"type": "done", "text": "", "stopped": True})
        elif limits.is_limit(tail):
            emit({"type": "limit_reached", "text": tail[-400:], "resets": limits.reset_hint(tail)})
        elif self.proc.returncode != 0:
            last = getattr(self, "last_error", "")
            emit({"type": "error", "text": f"{self.label} stopped (exit code {self.proc.returncode})."
                                           + (" " + last[:300] if last else "")
                                           + (" " + tail[-600:] if tail else "")})
        else:
            emit({"type": "done", "text": ""})

    def _read_stderr(self, errors):
        """Collect the last 60 stderr lines (for limit detection and error reports)."""
        for raw in iter(self.proc.stderr.readline, b""):
            errors.append(raw.decode("utf-8", "replace").rstrip())
            del errors[:-60]

    def interrupt(self):
        """Stop the program and everything it started (its MCP server too)."""
        self._stop.set()
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        if os.name == "nt":
            result = subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                                    creationflags=no_window())
            if result.returncode and proc.poll() is None:
                proc.kill()       # still stop our own child if tree termination is restricted
        else:
            proc.terminate()
