# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Shared state between the agent thread, the panel and the viewport overlay."""

import queue
import threading
import time

__author__ = "definitelynotironman"

# Status values shown in the panel
IDLE = "IDLE"
THINKING = "THINKING"        # waiting for the model
WORKING = "WORKING"          # running a tool in Blender
WAIT_NEXT = "WAIT_NEXT"      # legacy: no mode waits for Next now; kept so an old state still renders
WAIT_USER = "WAIT_USER"      # Teach mode: waiting for you to do the step
ASKING = "ASKING"            # the AI asked you a question
PAUSED = "PAUSED"            # you have the wheel
DONE = "DONE"
ERROR = "ERROR"

MAX_REFERENCES = 8          # reference pictures one task can carry


def normalize_mode(value):
    """Legacy Together sessions become Auto; Teach retains its identity."""
    return "TEACH" if value == "TEACH" else "AUTO"


STATUS_TEXT = {
    IDLE: "Ready",
    THINKING: "Thinking...",
    WORKING: "Working in your scene",
    WAIT_NEXT: "Press Next to continue",
    WAIT_USER: "Your turn",
    ASKING: "BlendWMe asked you something",
    PAUSED: "Paused. You have control",
    DONE: "Done",
    ERROR: "Stopped with an error",
}


class Session:
    """Everything the agent thread, the panel and the overlay share about the current task.

    The agent thread writes and the UI reads, so every change happens under
    `lock` and sets `changed_flag` so the panel knows to redraw. Events
    (`pause_flag`, `stop_flag`, `signal`) carry the user's controls to the agent.
    """
    def __init__(self):
        """An idle session with an empty log, plan and inbox."""
        self.lock = threading.RLock()
        self.status = IDLE
        self.turn_started = 0.0
        self.log = []            # [{"who": "you|ai|step|look|info|error", "text": str, "t": float}]
        self.card = None         # overlay card dict or None
        self.pointers = []       # overlay pointers
        self.inbox = queue.Queue()
        self.pause_flag = threading.Event()
        self.stop_flag = threading.Event()
        self.signal = threading.Event()   # Next / Done / Resume / answers wake the agent
        self.teach_choice = None          # "done", "hint", "show_me" or "skip"
        self.calls = 0
        self.tool_steps = 0
        self.tokens = 0
        self.last_error = ""
        self.last_capture = ""
        self.last_latency = 0.0
        self.last_log = ""
        self.changed_flag = True          # UI redraw hint
        self.steps = []                   # [{"title", "explain", "code", "ok"}] construction notebook
        # Live settings mirrored from the panel (read by the agent thread)
        self.mode = "AUTO"
        self.level = "BEGINNER"
        self.build_style = "PIECE"        # PIECE: part by part | FULL: big stages, fastest
        self.pace = 1.0                   # Auto: seconds each new part stays in focus
        self.show_pointers = True
        self.plan = []                    # [{"name", "done", "skipped", "reason"}] the build checklist
        self.sink = None                  # fn(who, text): the session log on disk (set at register)

    # -- log ---------------------------------------------------------------
    def add(self, who, text, **extra):
        """Append a log entry (trimmed to the last 300-400) and mirror it to the session log on disk."""
        with self.lock:
            entry = {"who": who, "text": str(text).strip(), "t": time.time()}
            entry.update(extra)
            self.log.append(entry)
            if len(self.log) > 400:
                del self.log[:100]
            self.changed_flag = True
        if self.sink is not None:
            self.sink(who, entry["text"])
        return entry

    def set_plan(self, parts):
        """A new or revised checklist. Parts that keep their name keep their state."""
        with self.lock:
            old = {p["name"]: p for p in self.plan}
            self.plan = []
            for n in parts:
                before = old.get(str(n), {})
                self.plan.append({"name": str(n), "done": before.get("done", False),
                                  "skipped": before.get("skipped", False), "reason": before.get("reason", "")})
            self.changed_flag = True

    def tick_plan(self, number):
        """Mark plan part `number` (1-based) as built."""
        with self.lock:
            if isinstance(number, int) and 1 <= number <= len(self.plan):
                self.plan[number - 1].update(done=True, skipped=False, reason="")
                self.changed_flag = True

    def skip_plan(self, number, reason):
        """A part left out on purpose. It counts as settled, and the reason is shown."""
        with self.lock:
            if isinstance(number, int) and 1 <= number <= len(self.plan):
                self.plan[number - 1].update(done=True, skipped=True, reason=str(reason))
                self.changed_flag = True

    def open_parts(self):
        """[(number, name)] of plan parts that are neither built nor skipped."""
        with self.lock:
            return [(i + 1, p["name"]) for i, p in enumerate(self.plan) if not p["done"]]

    def set_status(self, status):
        """Change the status; the turn timer starts when work begins after a pause or idle."""
        with self.lock:
            if status in (THINKING, WORKING) and self.status not in (THINKING, WORKING):
                self.turn_started = time.time()
            self.status = status
            self.changed_flag = True

    def set_card(self, card):
        """Show an overlay card (stamped with the time it appeared) or hide it with None."""
        with self.lock:
            if card is not None and "t" not in card:
                card = dict(card, t=time.time())     # when it appeared (the Done card collapses after 6 s)
            self.card = card
            self.changed_flag = True

    def set_pointers(self, pointers):
        """Replace the overlay pointers (rings around objects or controls)."""
        with self.lock:
            self.pointers = list(pointers or [])
            self.changed_flag = True

    def wake(self, choice=None):
        """Wake a waiting agent, optionally with the user's choice (done, hint, show_me, skip)."""
        if choice:
            self.teach_choice = choice
        self.signal.set()

    def reset_conversation(self):
        """Clear the log, cards, plan, counters and inbox for a new task."""
        with self.lock:
            self.log.clear()
            self.card = None
            self.pointers = []
            self.calls = self.tool_steps = self.tokens = 0
            self.last_error = ""
            self.steps = []
            self.plan = []
            self.status = IDLE
            while not self.inbox.empty():
                self.inbox.get_nowait()
            self.changed_flag = True


session = Session()
