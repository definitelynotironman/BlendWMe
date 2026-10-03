"""Shared state between the agent thread, the panel and the viewport overlay."""

import queue
import threading
import time

# Status values shown in the panel
IDLE = "IDLE"
THINKING = "THINKING"        # waiting for the model
WORKING = "WORKING"          # running a tool in Blender
WAIT_NEXT = "WAIT_NEXT"      # Watch mode: waiting for Next
WAIT_USER = "WAIT_USER"      # Teach mode: waiting for you to do the step
ASKING = "ASKING"            # the AI asked you a question
PAUSED = "PAUSED"            # you have the wheel
DONE = "DONE"
ERROR = "ERROR"


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
    def __init__(self):
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
        self.teach_choice = None          # "done" | "show_me" | "skip"
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
        self.watch_auto = 0.0             # Together: seconds before the next step starts; 0 = wait for Next
        self.build_style = "PIECE"        # PIECE: part by part | FULL: big stages, fastest
        self.pace = 1.0                   # Auto: seconds each new part stays in focus
        self.show_pointers = True
        self.plan = []                    # [{"name", "done", "skipped", "reason"}] the build checklist
        self.sink = None                  # fn(who, text): the session log on disk (set at register)

    # -- log ---------------------------------------------------------------
    def add(self, who, text, **extra):
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
        with self.lock:
            if status in (THINKING, WORKING) and self.status not in (THINKING, WORKING):
                self.turn_started = time.time()
            self.status = status
            self.changed_flag = True

    def set_card(self, card):
        with self.lock:
            if card is not None and "t" not in card:
                card = dict(card, t=time.time())     # when it appeared (the Done card collapses after 6 s)
            self.card = card
            self.changed_flag = True

    def set_pointers(self, pointers):
        with self.lock:
            self.pointers = list(pointers or [])
            self.changed_flag = True

    def wake(self, choice=None):
        if choice:
            self.teach_choice = choice
        self.signal.set()

    def reset_conversation(self):
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
