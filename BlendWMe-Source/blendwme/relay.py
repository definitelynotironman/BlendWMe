"""The relay: one collaborator, more than one brain.

The panel talks only to this module. It starts the active brain for each
message, turns the brain's standard events (engines/base.py) into the
conversation log, and keeps everything else the same whichever brain works:
the plan checklist, Auto / Watch / Teach, pause and take control, the overlay.

    you type in the panel -> relay.send()
        -> message = [BlendWMe] mode header + (first turn: scene summary)
                     + edits you made since the last turn + your words
        -> engine.start()  (Claude Code or Codex in the background)
        -> events: text (streamed into the panel), tool_call, tool_result, done / error / limit_reached
    the brain's tool calls arrive through the MCP server -> socket_bridge -> tool_host

Messages typed while a brain works are delivered with its next tool result;
anything still waiting when the brain ends its turn is sent as the next turn.

The relay (hand_over): the current brain is asked for a handoff note (if it
can still answer), the switch is written to the project journal, and the other
brain starts with the journal's handoff brief and fresh scene facts. On a
usage limit the panel offers the hand-over; in Auto mode a setting does it by
itself. Nothing is lost without a note, because the journal was written all along.
"""

import json
import os
import threading
import time
import traceback

from . import bridge, logs, prompts, scene_info, socket_bridge, workspace
from .engines.base import TERMINAL
from .engines.claude_code import ClaudeCodeEngine
from .engines.codex import CodexEngine
from .journal import journal
from .state import DONE, ERROR, IDLE, THINKING, session
from .tool_host import host, label

MAX_SCENE_CHARS = 5000
NOTE_TIMEOUT = 180          # seconds the old brain gets to write its handoff note


def _current_filepath():
    import bpy
    return bpy.data.filepath



def addon_version():
    """The version in blender_manifest.toml (one source of truth for logs)."""
    path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version"):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "unknown"

class Relay:
    def __init__(self):
        self.engines = {"claude": ClaudeCodeEngine(), "codex": CodexEngine()}
        self.active = "claude"
        self.running = None          # the brain working right now, or None
        self.entry = None            # the conversation entry being streamed into
        self.limit = None            # {"brain", "text", "resets"} after limit_reached
        self.told = set()            # brains that already got the scene summary in this task
        self.task_started = False
        self.resumed_project = False  # a reopened .blend: the first turn of each brain gets the brief
        self.handing_over = False
        self.turn_done = threading.Event()
        self.turn_end = None
        self.config = {}
        self.last_text = ""
        self.reply = ""              # the brain's last reply text in this turn
        self.text_since_tool = ""
        self.resume_retried = False
        self.transcript = []         # the full log, in the 1.0.5 log format
        self.trace = []
        self.brief = ""
        self.generation = 0
        host.journal = journal

    # ------------------------------------------------------------------
    # Called from the panel (main thread)
    # ------------------------------------------------------------------
    def is_running(self):
        return self.running is not None or self.handing_over

    def has_conversation(self):
        return self.task_started

    def engine(self, brain=None):
        return self.engines[brain or self.active]

    @staticmethod
    def other(brain):
        return "codex" if brain == "claude" else "claude"

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
        else:
            journal.record_user_message(text)
        session.add("you", text)
        self._start(brain, text)

    def stop(self):
        self.generation += 1
        bridge.cancel_all("This task was stopped before its pending job ran.")
        session.stop_flag.set()
        session.wake()
        if self.running:
            engine = self.engine(self.running)
            engine.interrupt()
            if engine.thread is not None and engine.thread is not threading.current_thread():
                engine.thread.join(timeout=5)
            session.set_card(None)
            session.set_pointers([])
            session.set_plan([])        # the plan card closes; the journal keeps the plan
            session.add("info", "Stopped. Everything already built stays in your scene.")
        self.running = None
        self.handing_over = False
        self.turn_done.set()
        session.set_status(IDLE)

    def reset(self):
        """Forget the conversation (the scene and the project journal stay)."""
        self.stop()
        for e in self.engines.values():
            e.reset()
        session.reset_conversation()
        host.reset_task("", [])
        self.task_started = False
        self.resumed_project = False
        self.told = set()
        self.limit = None
        self.transcript, self.trace = [], []

    def project_loaded(self):
        """A .blend was opened: pick up its journal (brains resume their own sessions, with the brief)."""
        self.reset()
        data = journal.data
        for brain, session_id in (data.get("sessions") or {}).items():
            engine = self.engines.get(brain)
            if engine is not None and session_id:
                engine.session_id = session_id
                if hasattr(engine, "started_session"):
                    engine.started_session = True
        if data.get("active") in self.engines:
            self.active = data["active"]
        if data.get("brief"):
            self.task_started = True
            self.resumed_project = True
            self.brief = data.get("brief", "")
            references = journal.resolve_references(bridge.call(_current_filepath))
            host.reset_task(self.brief, [r["path"] for r in references if not r["missing"]])
            for reference in references:
                if reference["missing"]:
                    session.add("info", "Reference is missing: " + reference.get("name", "unnamed image"))
                elif reference.get("changed"):
                    session.add("info", "Reference changed since it was recorded: " + reference["name"])
            session.set_plan([p["name"] for p in data.get("plan", [])])
            for i, p in enumerate(data.get("plan", []), 1):
                if p.get("done"):
                    session.tick_plan(i)
            session.add("info", "This project has a BlendWMe journal. The brains will pick up where they left off.")

    # ------------------------------------------------------------------
    # The relay: hand the work to the other brain
    # ------------------------------------------------------------------
    def hand_over(self, target, config, reason="the user pressed Hand over"):
        """Switch brains (runs in the background; the panel stays responsive)."""
        if self.handing_over:
            return
        source = self.running or self.active
        self.config = dict(config, brain=target)
        if target not in self.engines:
            session.add("error", f"{label(target)} is not available.")
            return
        if target == source and not self.running:
            self.active = target
            return
        self.handing_over = True
        threading.Thread(target=self._hand_over, args=(source, target, reason, self.generation), name="BlendWMe hand-over",
                         daemon=True).start()

    def _hand_over(self, source, target, reason, generation):
        try:
            if self.running:
                self._interrupt_and_wait(self.running)
            if generation != self.generation:
                return
            limited = bool(self.limit and self.limit["brain"] == source)
            pending = []
            while not session.inbox.empty():
                pending.append(session.inbox.get_nowait())
            if not limited and source in self.told and self.config.get("ask_handoff_note", True):
                session.add("info", f"Asking {label(source)} for a handoff note ...")
                end = self._quiet_turn(source, prompts.HANDOFF_REQUEST.format(target=label(target)))
                if generation != self.generation:
                    return
                if end != "done":
                    session.add("info", f"{label(source)} could not write a note. The journal has everything.")
            journal.record_switch(source, target, reason)
            session.add("info", f"Handing over to {label(target)}. It gets the journal's handoff brief and "
                                "a fresh look at the scene.")
            self.active = target
            self.told.discard(target)          # fresh scene facts for the brain taking over
            self.limit = None
            brief = self._brief(target)
            text = ("\n".join(pending) if pending else
                    f"{label(source)} handed the work to you. Take over and continue the task.")
        except Exception as e:
            if generation != self.generation:
                return
            traceback.print_exc()
            self.handing_over = False
            self._fail(f"The hand-over failed: {e}")
            return
        self.handing_over = False
        self._start(target, text, handoff=brief, generation=generation)

    def _interrupt_and_wait(self, brain):
        generation = self.generation
        engine = self.engine(brain)
        session.stop_flag.set()
        session.wake()
        engine.interrupt()
        if engine.thread is not None:
            engine.thread.join(timeout=30)
        if generation == self.generation:
            session.stop_flag.clear()

    def _quiet_turn(self, brain, text):
        """Run one short turn (the handoff note) and wait for its end. Returns done, error or limit_reached."""
        self.turn_done.clear()
        self.turn_end = None
        self._start(brain, text)
        if not self.turn_done.wait(NOTE_TIMEOUT):
            self._interrupt_and_wait(brain)
            self.turn_done.wait(10)
            return "timeout"
        return self.turn_end

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
        self.resumed_project = False
        self.limit = None
        session.set_plan([])
        host.reset_task(text, self.config.get("references", []))
        host.last_snapshot = None
        journal.record_task(text)
        journal.record_references(self.config.get("references", []), bridge.call(_current_filepath))

    def _engine_config(self, brain):
        if not socket_bridge.get().is_running():
            session.add("info", socket_bridge.start())
        else:
            socket_bridge.prepare_workspace()
        config = dict(self.config, workspace=workspace.folder(), new_task=brain not in self.told)
        config["mcp_config"] = workspace.write_mcp_config(brain)
        return config

    def _brief(self, brain):
        return journal.handoff_brief(brain, bridge.call(scene_info.makers),
                                     review_first=self.config.get("returning_reviews", True))

    def compose(self, brain, text, handoff=""):
        """The message a brain gets: mode header, scene facts the first time, the user's edits, the text."""
        parts = [prompts.turn_header(session.mode, session.level, session.build_style)]
        parts.append(prompts.MODES[session.mode if session.mode in prompts.MODES else "AUTO"])
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

    def _start(self, brain, text, handoff="", generation=None):
        generation = self.generation if generation is None else generation
        if generation != self.generation:
            return
        engine = self.engine(brain)
        self.last_text = text
        if not handoff and self.resumed_project and brain not in self.told and journal.has_history():
            handoff = self._brief(brain)
        try:
            config = bridge.call(self._engine_config, brain)
            message = self.compose(brain, text, handoff)
            self.told.add(brain)
        except Exception as e:
            traceback.print_exc()
            self._fail(f"Could not prepare {label(brain)}: {e}")
            return
        host.active_brain = brain
        if generation != self.generation:
            return
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
        self.reply = ""
        self.text_since_tool = ""
        self.transcript.append({"role": "user", "content": message})
        self._trace("turn", brain=brain, chars=len(message))
        try:
            engine.start(message, lambda event: self._on_event(event)
                         if generation == self.generation else None, config)
        except Exception as e:      # the program is missing, or similar
            self.running = None
            self._fail(str(e))
            self._signal_end("error")

    def _later(self, brain, fn):
        """Run fn once the brain's current turn thread has ended (events arrive from inside it)."""
        engine = self.engine(brain)
        worker = engine.thread
        generation = self.generation

        def wait_then_run():
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=30)
            if generation == self.generation:
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
                self._note_verdict(brain)
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
                self.reply = ""
            else:
                self.entry["text"] += text
                session.changed_flag = True
            self.reply += text
            self.text_since_tool += text
            message = self._assistant()
            if message["tool_calls"]:
                message = {"role": "assistant", "content": "", "tool_calls": []}
                self.transcript.append(message)
            message["content"] += text
            return
        if self.entry is not None and self.entry["text"].strip() == text.strip():
            return
        self.entry = session.add("ai", text, brain=brain)
        self.reply = text
        self.text_since_tool += text
        self._assistant()["content"] += text

    def _note_verdict(self, brain):
        """The brain's words right after an art director review are its verdict (kept in the journal)."""
        if host.review_pending and self.text_since_tool.strip():
            journal.set_review_verdict(brain, self.text_since_tool.strip())
            host.review_pending = False
        self.text_since_tool = ""

    def _end_turn(self, brain, event):
        kind = event["type"]
        self.running = None
        self.entry = None
        self._note_verdict(brain)
        self._trace(kind, brain=brain, text=(event.get("text") or "")[:300])
        engine = self.engine(brain)
        journal.set_session(brain, engine.session_id)
        if self.reply.strip():
            journal.record_said(brain, self.reply)
        journal.mark_seen(brain)
        quiet = self.handing_over
        if kind == "done":
            self.resume_retried = False
            if session.status != DONE:
                session.set_status(IDLE)
            if not quiet and not event.get("stopped") and not session.inbox.empty():
                # The user typed after the brain's last tool call: that becomes the next turn.
                pending = []
                while not session.inbox.empty():
                    pending.append(session.inbox.get_nowait())
                self._later(brain, lambda: self._start(brain, "\n".join(pending)))
        elif kind == "error":
            text = event.get("text") or "unknown error"
            if not quiet and "no conversation found" in text.lower() and not self.resume_retried:
                # The brain lost its saved conversation: start a fresh one with the full context.
                self.resume_retried = True
                engine.reset()
                self.told.discard(brain)
                self.running = brain       # preparing the retry is still active work
                self._later(brain, lambda: self._start(brain, self.last_text, handoff=self._brief(brain)))
                self._signal_end(kind)
                return
            journal.record_error(brain, text)
            if quiet:
                session.add("info", f"{label(brain)}: {text}")
            else:
                self._fail(text)
                session.set_card({"kind": "ask", "title": f"{label(brain)} stopped",
                                  "text": "Press Continue to try again, or hand the work to "
                                          f"{label(self.other(brain))}."})
        elif kind == "limit_reached":
            self.limit = {"brain": brain, "text": event.get("text", ""), "resets": event.get("resets", "")}
            journal.record_error(brain, "usage limit reached " + self.limit["resets"])
            when = f" It {self.limit['resets']}." if self.limit["resets"] else ""
            session.add("error", f"{label(brain)}'s usage limit is reached.{when}")
            if not quiet:
                target = self.other(brain)
                if self.config.get("offer_handover", True):
                    session.set_card({"kind": "limit", "title": f"{label(brain)}'s limit is reached",
                                      "text": f"{label(target)} can carry on right now. Nothing is lost.",
                                      "brain": brain, "target": target})
                session.set_status(IDLE)
                if self.config.get("auto_relay") and session.mode == "AUTO":
                    session.add("info", f"Auto mode: handing over to {label(target)} by itself.")
                    self._later(brain, lambda: self.hand_over(target, self.config, reason="usage limit reached"))
        self._signal_end(kind)
        if self.config.get("auto_save_logs", True) and not quiet:
            self.save_log()

    def _signal_end(self, kind):
        self.turn_end = kind
        self.turn_done.set()

    def _fail(self, text):
        session.last_error = text
        session.add("error", text)
        session.set_status(ERROR)

    def _trace(self, kind, **data):
        self.trace.append(dict({"t": round(time.time(), 2), "type": kind}, **data))

    # ------------------------------------------------------------------
    def save_log(self):
        """Write the full conversation (every message, all code and results) to the log folder."""
        if not self.transcript:
            return ""
        info = {"title": self.brief[:60], "version": addon_version(), "blender": self.config.get("blender_version"),
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
