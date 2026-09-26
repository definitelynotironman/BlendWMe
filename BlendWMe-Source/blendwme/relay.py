"""The relay: one collaborator, more than one brain.

The panel talks only to this module. It starts the active brain for each
message, turns the brain's standard events (engines/base.py) into the
conversation log, and keeps everything else the same whichever brain works:
the plan checklist, Auto / Watch / Teach, pause and take control, the overlay.

    you type in the panel -> relay.send()
        -> message = [BlendWMe] mode header + (first turn: scene summary)
                     + edits you made since the last turn + your words
        -> engine.start()  (Claude Code or Codex in the background, or the API agent)
        -> events: text (streamed into the panel), tool_call, tool_result, done / error / limit_reached
    the brain's tool calls arrive through the MCP server -> socket_bridge -> tool_host

Messages typed while a brain works are delivered with its next tool result;
anything still waiting when the brain ends its turn is sent as the next turn.
"""

import json
import threading
import time
import traceback

from . import bridge, logs, prompts, scene_info, socket_bridge, workspace
from .agent import agent
from .engines.api import ApiEngine
from .engines.base import TERMINAL
from .engines.claude_code import ClaudeCodeEngine
from .state import DONE, ERROR, IDLE, THINKING, session
from .tool_host import host, label

MAX_SCENE_CHARS = 5000


class Relay:
    def __init__(self):
        self.engines = {"claude": ClaudeCodeEngine(), "api": ApiEngine(agent)}
        self.active = "claude"
        self.running = None          # the brain working right now, or None
        self.entry = None            # the conversation entry being streamed into
        self.limit = None            # {"brain", "text", "resets"} after limit_reached
        self.told = set()            # brains that already got the scene summary in this task
        self.task_started = False
        self.config = {}
        self.last_text = ""
        self.resume_retried = False
        self.transcript = []         # the full log, in the 1.0.5 log format
        self.trace = []
        self.brief = ""

    # ------------------------------------------------------------------
    # Called from the panel (main thread)
    # ------------------------------------------------------------------
    def is_running(self):
        return self.running is not None

    def has_conversation(self):
        return self.task_started

    def engine(self, brain=None):
        return self.engines[brain or self.active]

    def send(self, text, config, new_task=False):
        text = (text or "").strip()
        if not text:
            return
        if self.is_running():
            # Steering or answering: the tool host hands it to the brain with its next tool result.
            session.add("you", text)
            session.inbox.put(text)
            session.wake()
            return
        brain = config.get("brain") or self.active
        if brain not in self.engines:
            session.add("error", f"{label(brain)} is not available in this version.")
            return
        self.active = brain
        self.config = dict(config)
        if new_task or not self.task_started:
            self._new_task(text)
        session.add("you", text)
        self._start(brain, text)

    def stop(self):
        session.stop_flag.set()
        session.wake()
        if self.running:
            self.engine(self.running).interrupt()
            session.set_card(None)
            session.set_pointers([])
            session.add("info", "Stopped. Everything already built stays in your scene.")

    def reset(self):
        """Forget the conversation (the scene and the project journal stay)."""
        self.stop()
        for e in self.engines.values():
            e.reset()
        session.reset_conversation()
        host.reset_task("", [])
        self.task_started = False
        self.told = set()
        self.limit = None
        self.transcript, self.trace = [], []

    # ------------------------------------------------------------------
    # Starting a turn
    # ------------------------------------------------------------------
    def _new_task(self, text):
        for e in self.engines.values():
            e.reset()
        self.told = set()
        self.brief = text
        self.transcript, self.trace = [], []
        self.task_started = True
        self.limit = None
        session.set_plan([])
        host.reset_task(text, self.config.get("references", []))
        host.last_snapshot = None

    def _engine_config(self, brain):
        if not socket_bridge.get().is_running():
            session.add("info", socket_bridge.start())
        else:
            socket_bridge.prepare_workspace()
        config = dict(self.config, workspace=workspace.folder(), new_task=brain not in self.told)
        if brain in ("claude", "codex"):
            config["mcp_config"] = workspace.write_mcp_config(brain)
        if brain == "api":
            config["token_budget"] = self.config.get("api_token_budget", 0)
            # The agent keeps its own conversation; the relay only reads the result.
        return config

    def compose(self, brain, text, handoff=""):
        """The message a brain gets: mode header, scene facts the first time, the user's edits, the text."""
        parts = [prompts.turn_header(session.mode, session.level, session.build_style)]
        if brain not in self.told:
            scene = json.dumps(bridge.call(scene_info.get_scene_info), ensure_ascii=False, default=str)
            if len(scene) > MAX_SCENE_CHARS:
                scene = scene[:MAX_SCENE_CHARS] + " ...(call get_scene_info for the rest)"
            parts.append("[Current scene]\n" + scene)
            refs = host.references
            if refs:
                parts.append(f"[The user attached {len(refs)} reference image(s). Call look with "
                             "compare_reference=true to see them before you build.]")
        changes = bridge.call(host.user_changes)
        if changes:
            parts.append("[The user changed the scene since your last turn. These are intentional creative "
                         "decisions: keep them and build around them.]\n" + "\n".join(changes[:40]))
        if handoff:
            parts.append(handoff)
        parts.append("[Message from the user]\n" + text)
        return "\n\n".join(parts)

    def _start(self, brain, text, handoff=""):
        engine = self.engine(brain)
        self.last_text = text
        try:
            config = self._engine_config(brain)
            message = text if getattr(engine, "raw_messages", False) else self.compose(brain, text, handoff)
            self.told.add(brain)
        except Exception as e:
            traceback.print_exc()
            self._fail(f"Could not prepare {label(brain)}: {e}")
            return
        host.active_brain = brain
        host.begin_turn()
        session.stop_flag.clear()
        session.pause_flag.clear()
        session.signal.clear()
        session.set_card(None)
        session.set_pointers([])
        session.set_status(THINKING)
        self.running = brain
        self.entry = None
        self.limit = None
        self.transcript.append({"role": "user", "content": message})
        self._trace("turn", brain=brain, chars=len(message))
        try:
            engine.start(message, self._on_event, config)
        except Exception as e:      # the program is missing, or similar
            self.running = None
            self._fail(str(e))

    def _later(self, brain, fn):
        """Run fn once the brain's current turn thread has ended (events arrive from inside it)."""
        engine = self.engine(brain)
        worker = engine.thread

        def wait_then_run():
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=30)
            fn()
        threading.Thread(target=wait_then_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Events from the brain (its own thread)
    # ------------------------------------------------------------------
    def _on_event(self, event):
        kind = event.get("type")
        brain = event.get("brain") or self.running or self.active
        try:
            if kind == "text":
                self._text(brain, event)
            elif kind == "tool_call":
                self.entry = None
                self._assistant()["tool_calls"].append({
                    "id": event.get("id") or f"call_{len(self.trace)}", "type": "function",
                    "function": {"name": event.get("tool", ""),
                                 "arguments": json.dumps(event.get("args") or {}, ensure_ascii=False)}})
                self._trace("tool", brain=brain, tool=event.get("tool"))
                if event.get("tool") not in _BLENDWME_TOOLS:
                    session.add("info", f"{label(brain)} used its own tool: {event.get('tool')}")
            elif kind == "tool_result":
                self.transcript.append({"role": "tool", "tool_call_id": event.get("id"),
                                        "content": event.get("text", "")})
            elif kind in TERMINAL:
                self._end_turn(brain, event)
        except Exception:
            traceback.print_exc()

    def _assistant(self):
        """The assistant message of the transcript that is being written."""
        last = self.transcript[-1] if self.transcript else None
        if not last or last.get("role") != "assistant":
            last = {"role": "assistant", "content": "", "tool_calls": []}
            self.transcript.append(last)
        return last

    def _text(self, brain, event):
        text = event.get("text") or ""
        if not text:
            return
        if event.get("delta"):
            if self.entry is None:
                self.entry = session.add("ai", text, brain=brain)
                self.entry["text"] = text      # keep leading spaces of a streamed reply as they come
            else:
                self.entry["text"] += text
                session.changed_flag = True
            message = self._assistant()
            if message["tool_calls"]:
                message = {"role": "assistant", "content": "", "tool_calls": []}
                self.transcript.append(message)
            message["content"] += text
            return
        if self.entry is not None and self.entry["text"].strip() == text.strip():
            return
        self.entry = session.add("ai", text, brain=brain)
        self._assistant()["content"] += text

    def _end_turn(self, brain, event):
        kind = event["type"]
        self.running = None
        self.entry = None
        self._trace(kind, brain=brain, text=(event.get("text") or "")[:300])
        if kind == "done":
            self.resume_retried = False
            if event.get("stopped"):
                session.set_status(IDLE)
            elif session.status != DONE:
                session.set_status(IDLE)
            if not event.get("stopped") and not session.inbox.empty():
                # The user typed after the brain's last tool call: that becomes the next turn.
                pending = []
                while not session.inbox.empty():
                    pending.append(session.inbox.get_nowait())
                self._later(brain, lambda: self._start(brain, "\n".join(pending)))
        elif kind == "error":
            text = event.get("text") or "unknown error"
            if "no conversation found" in text.lower() and not self.resume_retried:
                # The brain lost its saved conversation: start a fresh one with the full context.
                self.resume_retried = True
                self.engine(brain).reset()
                self.told.discard(brain)
                self._later(brain, lambda: self._start(brain, self.last_text))
                return
            self._fail(text)
        elif kind == "limit_reached":
            self.limit = {"brain": brain, "text": event.get("text", ""), "resets": event.get("resets", "")}
            when = f" It {self.limit['resets']}." if self.limit["resets"] else ""
            session.add("error", f"{label(brain)}'s usage limit is reached.{when}")
            session.set_card({"kind": "ask", "title": f"{label(brain)}'s limit is reached",
                              "text": "Everything so far is saved. Hand over to the other brain, or wait for "
                                      "the limit to reset and press Continue."})
            session.set_status(IDLE)
        if self.config.get("auto_save_logs", True):
            self.save_log()

    def _fail(self, text):
        session.last_error = text
        session.add("error", text)
        session.set_status(ERROR)

    def _trace(self, kind, **data):
        self.trace.append(dict({"t": round(time.time(), 2), "type": kind}, **data))

    # ------------------------------------------------------------------
    def save_log(self):
        """Write the full conversation of the outside brains (the API brain writes its own)."""
        if self.active == "api":
            return agent.save_log()
        if not self.transcript:
            return ""
        info = {"title": self.brief[:60], "version": "2.0.0", "blender": self.config.get("blender_version"),
                "model": ", ".join(sorted({t.get("brain", "") for t in self.trace if t.get("brain")})),
                "mode": session.mode, "level": session.level, "status": session.status,
                "steps": len(session.steps)}
        try:
            path = logs.save(self.transcript, self.trace, info, self.config.get("log_folder", ""))
            session.last_log = path
            return path
        except OSError as e:
            session.add("error", f"Could not save the log: {e}")
            return ""


_BLENDWME_TOOLS = {"run_python", "get_scene_info", "get_object_info", "look", "plan", "teach_step", "point_at",
                   "ask_user", "load_skill", "inspect_api", "finish", "handoff_note", "read_journal",
                   "blendwme_status"}

relay = Relay()
