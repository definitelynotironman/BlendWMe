"""The agent loop: the heart of BlendWMe.

    you -> brief
    loop:
        notice any changes YOU made since the last step
        ask the model what to do next (it may call several tools)
        run each tool in Blender, send results back
        Watch mode: wait for Next.  Teach mode: wait for you.
    until the model calls finish (or you Stop)

The conversation is kept between messages, so a follow-up like
"make the roof red" continues the same project with full memory.
"""

import json
import os
import re
import threading
import time
import traceback

from . import bridge, coerce, executor, helpers, knowledge, llm, logs, look, prefs, prompts, scene_info, tools
from .state import (ASKING, DONE, ERROR, IDLE, PAUSED, THINKING, WAIT_NEXT,
                    WAIT_USER, WORKING, session)

MAX_TOOL_RESULT = 6000
MAX_CALLS_PER_TURN = 8
KEEP_RECENT = 14          # newest messages kept in full
CONTEXT_BUDGET = 110_000  # characters before older results are compacted
REVIEW_FIRST = 3          # successful build steps before the first art director review
REVIEW_FIRST_WITH_REF = 1  # ... when the user gave reference images (match them early)
REVIEW_EVERY = 4          # successful build steps between reviews
REVIEW_MAX = 3            # periodic reviews per task (plus one final check): critique must converge
WRAP_UP_STEPS = 20        # successful build steps before a "time to finish" note (when there is no plan)
STUCK_FAILS = 4           # failures of the SAME step before handing the wheel to the user
STUCK_PRECHECK = 6        # pre-check rejections (nothing ran) of the same step before that

# Steps that only probe the environment ("is bwm available?") instead of building.
_PROBE_CODE = re.compile(r"dir\(\s*(bwm|bpy|bmesh|bpy\.ops)\s*\)|hasattr\(\s*(bwm|bpy|bmesh)\s*,"
                         r"|sys\.modules|except\s+\(?\s*(ImportError|ModuleNotFoundError)|globals\(\)|locals\(\)")
_PROBE_TITLE = re.compile(r"\b(test|testing|availab|probe|debug|explore|environment)", re.I)
_BUILDS = re.compile(r"\bbwm\.(?!bounds|top\b|obj\b|col\b|mat\b)\w+\(|bpy\.ops\.|\.new\(|\.link\(|from_pydata|"
                     r"\.location|\.scale|\.rotation|modifiers|default_value")


_SEES_IMAGES = {}   # model id -> True/False, filled from the provider's model list
_REQUIRED_REJECTED = set()   # models whose provider refused tool_choice="required"


class Stopped(Exception):
    pass


def _dump(obj):
    text = json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(text) > MAX_TOOL_RESULT:
        text = text[:MAX_TOOL_RESULT] + '..."[truncated]"'
    return text


class Agent:
    def __init__(self):
        self.messages = []
        self.thread = None
        self.config = {}
        self.brief = ""
        self.last_snapshot = None
        self.mode_in_prompt = None
        self.show_me_allowed = False
        self.budget_start = 0
        self.nudged = 0
        self.finished = False
        self.fail_streak = 0
        self.vision_broken = ""
        self.vision_retried = False
        self.trace = []          # timing and events for the full log
        self.step_fails = {}     # step key -> failures that ran and broke
        self.step_rejects = {}   # step key -> pre-check rejections (nothing ran)
        self.retry_entry = None  # the quiet "fixing a code error" log line
        self.rescued = 0         # Python calls sent as tools and rescued
        self.bad_tool_calls = 0
        self.pending_images = []  # images for a vision-capable agent, sent after tool replies
        self.agent_sees = False
        # Harness-driven vision: the art director looks without being asked.
        self.ok_steps = 0         # successful run_python steps in this task
        self.reviewed_at = 0      # ok_steps when the last review happened
        self.reviews = 0
        self.final_reviewed = False
        self.ref_spec = ""
        self.spec_pending = False
        self.plan_done_noted = False

    # ------------------------------------------------------------------
    # Called from the UI (main thread)
    # ------------------------------------------------------------------
    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def send(self, text, config, new_task=False):
        text = (text or "").strip()
        if not text:
            return
        session.add("you", text)
        if self.is_running():
            # The agent reads this between steps (steering or answering a question).
            session.inbox.put(text)
            session.wake()
            return
        self.config = config
        session.set_card(None)
        session.set_pointers([])
        if new_task or not self.messages:
            self._new_conversation(text)
            self.last_snapshot = scene_info.snapshot()
        else:
            # Keep the old snapshot: edits you made between messages get reported.
            self.messages.append({"role": "user", "content": text})
        self.budget_start = session.tool_steps
        self.step_fails, self.step_rejects = {}, {}
        self.final_reviewed = False
        self.finished = False
        self.fail_streak = 0
        self.vision_broken = ""
        self.vision_retried = False
        self.retry_entry = None
        self._trace("user", text=text[:200])
        session.stop_flag.clear()
        session.pause_flag.clear()
        session.signal.clear()
        self.thread = threading.Thread(target=self._run, name="BlendWMe agent", daemon=True)
        self.thread.start()

    def reset(self):
        self.stop()
        self.messages = []
        self.brief = ""
        session.reset_conversation()

    def stop(self):
        session.stop_flag.set()
        session.wake()

    def _new_conversation(self, text):
        self.brief = text
        self.trace = []
        self.mode_in_prompt = session.mode
        scene = scene_info.get_scene_info()
        refs = [p for p in self.config.get("references", []) if os.path.isfile(p)]
        intro = text + "\n\n[Current scene]\n" + _dump(scene)
        if refs:
            intro += f"\n\n[The user attached {len(refs)} reference image(s). Use look with compare_reference=true to see them.]"
        self.messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": intro},
        ]
        self.ok_steps = self.reviewed_at = self.reviews = 0
        self.plan_done_noted = False
        session.set_plan([])      # a new task never inherits the last task's checklist
        self.ref_spec = ""
        self.spec_pending = bool(refs)

    def _system_prompt(self):
        return prompts.build_system(session.mode, session.level,
                                    self.config.get("blender_version", "4.2"),
                                    knowledge.skill_index(), session.build_style)

    def _trace(self, kind, **data):
        self.trace.append(dict({"t": round(time.time(), 2), "type": kind}, **data))

    def save_log(self):
        """Write the full conversation to a file. Returns the path or ''."""
        if not self.messages:
            return ""
        info = {"title": self.brief[:60], "version": "1.0.5",
                "blender": self.config.get("blender_version"), "model": self.config.get("model"),
                "vision_model": self.config.get("vision_model") or "(auto)",
                "reasoning": self.config.get("reasoning"), "mode": session.mode, "level": session.level,
                "status": session.status, "model_calls": session.calls, "tokens": session.tokens,
                "steps": len(session.steps)}
        try:
            path = logs.save(self.messages, self.trace, info, self.config.get("log_folder", ""),
                             self.config.get("api_key", ""))
            session.last_log = path
            return path
        except OSError as e:
            session.add("error", f"Could not save the log: {e}")
            return ""

    # ------------------------------------------------------------------
    # The loop (background thread)
    # ------------------------------------------------------------------
    def _run(self):
        try:
            self._loop()
        finally:
            if self.config.get("auto_save_logs", True):
                self.save_log()

    def _loop(self):
        client = llm.Client(self.config.get("api_key"), self.config.get("base_url"))
        self.agent_sees = self._agent_sees_images(client)
        try:
            if self.spec_pending:
                self.spec_pending = False
                self._reference_spec(client)
            while not self.finished:
                self._check_stop()
                self._wait_if_paused()
                self._sync_mode()
                self._notice_user_changes()
                self._read_inbox()
                self._check_budget()

                session.set_status(THINKING)
                message = self._ask_model(client)
                finish_reason = message.pop("_finish_reason", None)
                if message.pop("_required_rejected", False):
                    _REQUIRED_REJECTED.add(self.config.get("model"))
                self.messages.append(message)
                if message.get("content"):
                    session.add("ai", message["content"])

                calls = message.get("tool_calls") or []
                if not calls:
                    if finish_reason == "length":
                        # A cut-off reply is often a repetition loop ("<PARAMS>" x 8000). Keeping it in the
                        # history primes the model to repeat it, so keep only a short marker.
                        if len(message.get("content") or "") > 1500:
                            message["content"] = (message["content"][:300]
                                                  + " [... reply cut off by the length limit ...]")
                        self.messages.append({"role": "user", "content":
                            "Your reply was cut off by the length limit. Continue with a smaller step."})
                        continue
                    code = _python_block(message.get("content")) if session.mode in ("AUTO", "WATCH") else ""
                    if code:
                        # Last resort: a tool call we could not parse, or code written as text.
                        # Run the code it wrote as a real step instead of ending the task.
                        calls = [{"id": "call_text_code_" + str(len(self.messages)), "type": "function",
                                  "function": {"name": "run_python", "arguments": json.dumps(
                                      {"title": "Step", "explain": "", "code": code})}}]
                        message["tool_calls"] = calls
                        # Keep the clean call, not the code-as-text, so the model does not imitate it.
                        message["content"] = (message.get("content") or "").split("```")[0].strip()[:400]
                        self._trace("text_code_run", lines=code.count("\n") + 1)
                    elif self.nudged < 2 and session.mode != "TEACH":
                        # Open models sometimes describe a plan and stop. Nudge (twice at most in a row).
                        self.nudged += 1
                        self.messages.append({"role": "user", "content":
                            "Continue the work using your tools: call the run_python TOOL with your code "
                            "(not code in the text). Call finish when the brief is complete, or ask_user if "
                            "you truly need an answer."})
                        continue
                    else:
                        break  # the model is waiting for the user
                self.nudged = 0

                # If you paused while the model was thinking, its plan may be stale.
                if session.pause_flag.is_set():
                    self._wait_if_paused()
                    if self._notice_user_changes(defer=True):
                        for call in calls:
                            self._tool_reply(call, {"skipped": "The user paused and edited the scene "
                                                    "before this ran. Re-plan from the current scene."})
                        self._flush_deferred()
                        continue

                calls = self._rescue_python_calls(calls, client)
                for n, call in enumerate(calls):
                    self._check_stop()
                    if n >= MAX_CALLS_PER_TURN:
                        self._tool_reply(call, {"skipped": f"Too many tool calls at once. Only the first "
                                                           f"{MAX_CALLS_PER_TURN} ran. Group work into run_python."})
                        continue
                    result = self._dispatch(call, client)
                    self._tool_reply(call, result)
                    if self.finished:
                        break
                if not self.finished:
                    self._maybe_review(client)
                    self._maybe_wrap_up()
                self._send_pending_images()
            if not self.finished and session.status not in (ERROR,):
                session.set_status(IDLE)
        except Stopped:
            session.set_card(None)
            session.set_pointers([])
            session.add("info", "Stopped. Everything already built stays in your scene.")
            session.set_status(IDLE)
            self._close_open_calls()
        except llm.LLMError as e:
            self._trace("error", error=str(e)[:500])
            session.last_error = str(e)
            session.add("error", str(e))
            session.set_status(ERROR)
            self._close_open_calls()
        except Exception as e:  # never crash Blender
            traceback.print_exc()
            session.last_error = f"{type(e).__name__}: {e}"
            self._trace("error", error=traceback.format_exc()[-1500:])
            session.add("error", session.last_error)
            session.set_status(ERROR)
            self._close_open_calls()

    # -- model ------------------------------------------------------------
    def _ask_model(self, client):
        self._compact()
        delays = [3, 8, 20]
        for attempt in range(len(delays) + 1):
            try:
                t0 = time.time()
                message, usage = client.chat(
                    self.config.get("model"), self.messages, tools.for_mode(session.mode),
                    temperature=self.config.get("temperature", 0.4),
                    max_tokens=self.config.get("max_tokens", 8192),
                    reasoning=self.config.get("reasoning", "LOW"),
                    tool_choice=self._tool_choice())
                session.calls += 1
                session.tokens += int(usage.get("total_tokens") or 0)
                session.last_latency = round(time.time() - t0, 1)
                self._trace("model", seconds=session.last_latency,
                            tokens=int(usage.get("total_tokens") or 0),
                            finish=message.get("_finish_reason"),
                            tools=[c["function"]["name"] for c in message.get("tool_calls") or []])
                return message
            except llm.LLMError as e:
                text = str(e).lower()
                if e.status == 400 and any(w in text for w in ("context", "too long", "maximum", "token")) \
                        and attempt == 0:
                    session.add("info", "The conversation got too long. Shortening older steps and retrying.")
                    self._compact(force=True)
                    continue
                retryable = e.status in (None, 429, 500, 502, 503, 504)
                if not retryable or attempt == len(delays):
                    raise
                self._trace("error", error=str(e)[:300])
                session.add("info", f"Model busy ({e.status or 'network'}). Retrying in {delays[attempt]}s.")
                self._sleep(delays[attempt])

    def _tool_choice(self):
        """Auto mode: every turn must be a tool call (finish, plan, ask_user are tools too).

        Watch and Teach keep "auto": there the model may answer the user in plain words.
        """
        if session.mode == "AUTO" and self.config.get("model") not in _REQUIRED_REJECTED:
            return "required"
        return "auto"

    def _tool_reply(self, call, result):
        if isinstance(result, dict):
            result = {k: v for k, v in result.items() if not k.startswith("_")}
        self.messages.append({"role": "tool", "tool_call_id": call["id"],
                              "content": result if isinstance(result, str) else _dump(result)})

    def _close_open_calls(self):
        """Every tool call needs an answer, or the next request is rejected."""
        if not self.messages:
            return
        last = self.messages[-1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            for call in last["tool_calls"]:
                self._tool_reply(call, {"skipped": "The task was stopped before this ran."})
        else:
            # Calls answered partly: find the last assistant call message.
            for i in range(len(self.messages) - 1, -1, -1):
                m = self.messages[i]
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    answered = {x.get("tool_call_id") for x in self.messages[i + 1:]}
                    for call in m["tool_calls"]:
                        if call["id"] not in answered:
                            self._tool_reply(call, {"skipped": "The task was stopped before this ran."})
                    break

    def _agent_sees_images(self, client):
        """True when the agent model itself accepts images (then it looks with its own eyes)."""
        model = (self.config.get("model") or "").lower()
        if not self.config.get("vision_enabled", True) or not self.config.get("agent_vision", True):
            return False
        if model not in _SEES_IMAGES:
            try:
                _SEES_IMAGES[model] = any(m["id"].lower() == model and m["vision"]
                                          for m in client.list_models_detailed())
            except llm.LLMError:
                return False
            if _SEES_IMAGES[model]:
                session.add("info", "This model can see, so it judges the pictures with its own eyes.")
        return _SEES_IMAGES[model]

    def _send_pending_images(self, review=True):
        if not self.pending_images:
            return
        content = []
        for item in self.pending_images:
            content.append({"type": "text", "text": item["caption"]})
            for path in item["paths"]:
                content.append({"type": "image_url", "image_url": {"url": llm.image_data_url(path)}})
        if review:
            content.append({"type": "text", "text": prompts.SELF_REVIEW.format(brief=self.brief[:600])})
        self.messages.append({"role": "user", "content": content})
        self.pending_images = []

    def _drop_old_images(self):
        """Keep only the newest picture in the conversation. Old ones become a short note."""
        seen_latest = False
        for m in reversed(self.messages):
            if m.get("role") != "user" or not isinstance(m.get("content"), list):
                continue
            if not seen_latest:
                seen_latest = True
                continue
            m["content"] = [p if p.get("type") == "text" else {"type": "text", "text": "[earlier image removed]"}
                            for p in m["content"]]

    @staticmethod
    def _size(message):
        content = message.get("content")
        if isinstance(content, list):
            return sum(len(p.get("text", "")) for p in content) + 2000 * sum(1 for p in content if p.get("type") == "image_url")
        return len(json.dumps(message, default=str))

    def _compact(self, force=False):
        """Keep long sessions inside the model's context window."""
        self._drop_old_images()
        total = sum(self._size(m) for m in self.messages)
        if total < CONTEXT_BUDGET and not force:
            return
        cutoff = len(self.messages) - (6 if force else KEEP_RECENT)
        for m in self.messages[2:max(2, cutoff)]:
            if m.get("role") == "tool" and len(m.get("content", "")) > 400:
                m["content"] = m["content"][:400] + '..."[older result shortened]"'
            if m.get("role") == "assistant":
                for call in m.get("tool_calls") or []:
                    fn = call["function"]
                    if len(fn["arguments"]) > 600:
                        try:
                            args = json.loads(fn["arguments"], strict=False)
                            if "code" in args:   # only shorten real code, never invent a code field
                                code = str(args["code"])
                                args["code"] = f"[{code.count(chr(10)) + 1} lines of earlier code omitted]"
                                fn["arguments"] = json.dumps(args)
                        except ValueError:
                            fn["arguments"] = json.dumps({"note": "earlier arguments omitted"})

    # -- user interaction -------------------------------------------------
    def _check_stop(self):
        if session.stop_flag.is_set():
            raise Stopped()

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self._check_stop()
            time.sleep(0.2)

    def _wait_if_paused(self, card=None):
        if not session.pause_flag.is_set():
            return
        previous = session.status
        previous_card = session.card
        session.set_status(PAUSED)
        session.set_card(card or {"kind": "pause", "title": "You have control",
                                  "text": "Edit anything you like. Press Resume and I will continue from your version."})
        while session.pause_flag.is_set():
            self._check_stop()
            session.signal.wait(0.3)
            session.signal.clear()
        session.set_card(previous_card)
        session.set_status(previous if previous != PAUSED else THINKING)

    def _notice_user_changes(self, defer=False):
        current = bridge.call(scene_info.snapshot)
        lines, changed = scene_info.diff(self.last_snapshot or {}, current)
        self.last_snapshot = current
        if not lines:
            return False
        bridge.call(scene_info.tag_author, changed["added"] + changed["modified"], "user")
        session.add("info", "I noticed your changes: " + "; ".join(lines[:4])
                    + (" ..." if len(lines) > 4 else ""))
        note = ("[The user changed the scene themselves. These are intentional creative decisions. "
                "Keep them and build around them.]\n" + "\n".join(lines[:40]))
        if defer:
            self._deferred = note
        else:
            self.messages.append({"role": "user", "content": note})
        return True

    def _flush_deferred(self):
        note = getattr(self, "_deferred", None)
        if note:
            self.messages.append({"role": "user", "content": note})
            self._deferred = None

    def _read_inbox(self):
        texts = []
        while not session.inbox.empty():
            texts.append(session.inbox.get_nowait())
        if texts:
            self.messages.append({"role": "user", "content":
                "[Message from the user while you work]\n" + "\n".join(texts)})

    def _sync_mode(self):
        if self.messages:
            self.messages[0]["content"] = self._system_prompt()
        if self.mode_in_prompt and self.mode_in_prompt != session.mode:
            self.messages.append({"role": "user", "content": prompts.mode_switch_note(session.mode)})
            session.add("info", f"Switched to {session.mode.title()} mode.")
        self.mode_in_prompt = session.mode

    def _check_budget(self):
        limit = int(self.config.get("max_steps", 60))
        if session.tool_steps - self.budget_start >= limit:
            session.add("info", f"Reached the {limit}-step limit. Press Resume to allow more.")
            session.pause_flag.set()
            self._wait_if_paused()
            self.budget_start = session.tool_steps

    def _wait_signal(self, timeout=None):
        """Wait for Next/Done/answer. Returns False on timeout."""
        session.signal.clear()
        end = None if timeout is None else time.time() + timeout
        while True:
            self._check_stop()
            if session.signal.wait(0.25):
                session.signal.clear()
                if session.pause_flag.is_set():
                    self._wait_if_paused()
                    continue
                return True
            if end is not None and time.time() >= end:
                return False

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    def _rescue_python_calls(self, calls, client):
        """Run Python-as-tool-calls (bwm.mesh, bpy.ops...) as ONE real step. Returns the other calls."""
        python = [c for c in calls if coerce.is_python_call(c["function"]["name"])]
        if not python:
            return calls
        parsed = []
        for c in python:
            try:
                parsed.append((coerce.normalise_name(c["function"]["name"]),
                               llm.parse_arguments(c["function"]["arguments"])))
            except ValueError:
                parsed.append((coerce.normalise_name(c["function"]["name"]), {}))
        import bpy
        code, notes = coerce.to_code(parsed, helpers, bpy)
        self.rescued += len(python)
        session.add("info", f"The model called {len(python)} Python function(s) as tools. Running them as one step.")
        self._trace("rescued_calls", count=len(python), names=sorted({n for n, _ in parsed})[:10])
        result = self._dispatch({"id": python[0]["id"], "function": {
            "name": "run_python",
            "arguments": json.dumps({"title": "Converted helper calls", "code": code,
                                     "explain": "Ran the helper calls the model sent as tools."})}}, client)
        lesson = ("IMPORTANT: bwm.*, bpy.* and bmesh.* are Python, not tools. BlendWMe ran these "
                  f"{len(python)} call(s) for you as this Python step:\n{code[:1500]}\n"
                  "From now on, put Python inside run_python(title, explain, code).")
        if notes:
            lesson += "\nNotes: " + "; ".join(notes)
        if isinstance(result, dict):
            result = dict(result, lesson=lesson)
        for i, c in enumerate(python):
            self._tool_reply(c, result if i == 0 else {"ran_together_with": python[0]["id"]})
        return [c for c in calls if c not in python]

    def _dispatch(self, call, client):
        name = call["function"]["name"]
        try:
            args = llm.parse_arguments(call["function"]["arguments"])
        except ValueError as e:
            return {"error": str(e) + " Send the tool call again with valid JSON arguments."}
        session.tool_steps += 1
        name, args = _code_tool_alias(name, args)
        args = _coerce_types(name, args)
        handler = getattr(self, "_tool_" + name, None)
        available = [t["function"]["name"] for t in tools.for_mode(session.mode)]
        if handler is None or name not in available:
            self.bad_tool_calls += 1
            if handler is None:
                return {"error": f"There is no tool called {name!r}. The only tools are: {', '.join(available)}. "
                                 "To run Python, call run_python with your code in its code argument, e.g. "
                                 "run_python(title='Main body', explain='The main block', "
                                 "code=\"bwm.box('Body', (2, 1, 1), (0, 0, 0.5))\")."}
            return {"error": f"{name!r} cannot be used in {session.mode} mode. Tools here: {', '.join(available)}."}
        session.set_status(WORKING)
        t0 = time.time()
        try:
            result = handler(args, client)
            ok = not (isinstance(result, dict) and (result.get("error") or result.get("ok") is False))
            self._trace("tool", tool=name, seconds=round(time.time() - t0, 2), ok=ok)
            return result
        except Stopped:
            raise
        except Exception as e:  # a tool problem goes back to the model; it never ends the task
            self._trace("tool_error", tool=name, error=f"{type(e).__name__}: {e}")
            return {"error": f"{type(e).__name__}: {e}"}

    def _tool_get_scene_info(self, args, client):
        return bridge.call(scene_info.get_scene_info)

    def _tool_get_object_info(self, args, client):
        return bridge.call(scene_info.get_object_info, str(args.get("name", "")))

    def _tool_inspect_api(self, args, client):
        return bridge.call(knowledge.inspect_api, str(args.get("path", "")))

    def _tool_load_skill(self, args, client):
        name = str(args.get("name", ""))
        session.add("info", f"Reading skill card: {name}")
        return knowledge.load_skill(name)

    def _tool_run_python(self, args, client):
        mode = session.mode
        if mode == "TEACH" and not self.show_me_allowed:
            return {"error": "In Teach mode the user does the work. Use teach_step. "
                             "run_python is only allowed right after teach_step returns show_me."}
        self.show_me_allowed = False
        title = str(args.get("title") or "Step")[:80]
        explain = str(args.get("explain") or "")
        code = str(args.get("code") or "")
        part = args.get("part")
        part = int(part) if isinstance(part, (int, float)) or str(part).isdigit() else None
        if _is_probe(title, code):
            # Do not run (or count) environment tests. Answer the question they were asking.
            self._trace("probe_refused", title=title)
            return {"skipped": "Not run: this step only tests the environment. Everything is ready: bpy, bmesh, "
                               "math, Vector, C, D and bwm are already defined in every step (import bwm also "
                               "works). bwm has: " + ", ".join(_bwm_names()) + ". Build the next part of the "
                               "plan now."}
        session.set_card({"kind": "work", "title": title, "text": explain,
                          "step": len(session.steps) + 1, **self._plan_progress()})
        result = bridge.call(executor.run, code, title)
        changed = result.get("_changed", {})
        session.steps.append({"title": title, "explain": explain, "code": code, "ok": result["ok"]})
        if result.get("auto_repaired"):
            self._trace("auto_repaired", title=title, fixes=result["auto_repaired"][:6])
        self.last_snapshot = bridge.call(scene_info.snapshot)
        key = _step_key(title, part)
        if result["ok"]:
            if self.fail_streak and self.retry_entry is not None:
                # Quietly close the earlier "fixing" note instead of leaving red errors around.
                self.retry_entry["text"] = f"{title}: fixed a code error ({self.fail_streak + 1} tries)"
                self.retry_entry["detail"] = ""
                session.changed_flag = True
            self.retry_entry = None
            self.fail_streak = 0
            self.step_fails.pop(key, None)
            self.step_rejects.pop(key, None)
            self.ok_steps += 1
            session.add("step", title, detail=explain, ok=True)
            if part:
                session.tick_plan(part)
        else:
            self._step_failed(result, title, key)
        targets = (changed.get("added", []) + changed.get("modified", []))[:6]
        if mode in ("WATCH", "TEACH") and result["ok"]:
            session.set_pointers([{"object": n} for n in targets])
            session.set_card({"kind": "watch", "title": title, "text": explain,
                              "step": len(session.steps), **self._plan_progress()})
            session.set_status(WAIT_NEXT)
            self._wait_signal(timeout=session.watch_auto or None)
            session.set_pointers([])
            result["user"] = "The user watched this step and pressed Next."
        elif mode == "AUTO" and session.build_style == "PIECE" and result["ok"] and targets:
            # Let the new part "land" visibly before the next one.
            session.set_pointers([{"object": n} for n in targets[:3]])
            self._sleep(0.9)
            session.set_pointers([])
        if self.plan_needed() and result["ok"]:
            result["reminder"] = "Call plan first so the user can follow the build."
        return result

    def _step_failed(self, result, title, key):
        """Count a failure for THIS step, give sharper hints, and pause only when truly stuck."""
        error = result.get("error", "")
        short = error.strip().splitlines()[-1][:160] if error.strip() else "unknown error"
        rejected = error.startswith("Pre-check found")     # nothing ran: cheap, not counted as a failure
        counts = self.step_rejects if rejected else self.step_fails
        counts[key] = counts.get(key, 0) + 1
        n = counts[key]
        self.fail_streak = self.step_fails.get(key, 0) + self.step_rejects.get(key, 0)
        self._trace("step_failed", title=title, error=error[:600], rejected=rejected, count=n)
        previous = getattr(self, "_last_error", {}).get(key)
        self._last_error = dict(getattr(self, "_last_error", {}), **{key: short})
        text = f"{title}: fixing a code error"
        if self.retry_entry is None:
            self.retry_entry = session.add("retry", text, detail=short)
        else:
            self.retry_entry.update(text=text + f" (try {self.fail_streak + 1})", detail=short)
            session.changed_flag = True
        limit = STUCK_PRECHECK if rejected else STUCK_FAILS
        if n == 2 or (rejected and n == 3):
            hint = ("This step failed twice. Change approach: split it into smaller steps, build it with bwm "
                    "(box, cylinder, cone, sphere, loft, extrude_profile, gable_roof, window, door...) instead of "
                    "bmesh or operators, or call inspect_api('bwm.<name>') to read a helper's arguments.")
            if previous and previous == short:
                hint = f"Same error again ({short}). That exact line is wrong; do not repeat it. " + hint
            result["stuck_hint"] = hint
        elif n == limit - 1:
            result["stuck_hint"] = ("Last try before BlendWMe hands control to the user. Rebuild this part in the "
                                    "simplest way with bwm (for example a few bwm.box / bwm.cylinder parts), even "
                                    "if it is less detailed. Do not reuse the code that failed.")
        elif n >= limit:
            # Do not burn tokens on a loop. Hand the wheel to the user.
            session.add("error", f"{title} keeps failing: {short}")
            session.add("info", "I'm stuck on this step, so I paused. Fix it yourself or press Resume "
                                "and I will try a different way.")
            session.pause_flag.set()
            self._wait_if_paused({"kind": "ask", "title": f"I'm stuck: {title}",
                                  "text": f"This step failed {n} times. Error: " + short
                                          + "  Edit the scene or tell me what to do, then press Resume."})
            counts[key] = 0
            self.fail_streak = 0
            self.retry_entry = None
            result["user"] = (f"You failed {n} times and were paused. The user has now resumed. Any message "
                              "or scene change they made follows. Try a clearly different approach.")

    def _plan_progress(self):
        if not session.plan:
            return {}
        done = sum(1 for p in session.plan if p["done"])
        return {"progress": f"PART {min(done + 1, len(session.plan))} OF {len(session.plan)}"}

    def plan_needed(self):
        return session.mode == "AUTO" and not session.plan and len(session.steps) == 1

    def _tool_plan(self, args, client):
        parts = _plan_parts(args)
        if not parts:
            return {"error": "Give the parts as a list of short strings, e.g. "
                             "plan(parts=[\"<part> <size> m on <host>\", \"<next part> ...\"])."}
        session.set_plan(parts)
        session.add("plan", "Plan: " + "; ".join(p.split(",")[0][:40] for p in parts[:12]))
        return {"ok": True, "parts": len(parts),
                "note": "Build them in order and pass part=<number> to run_python."}

    def _tool_look(self, args, client):
        if session.mode == "AUTO" and self.plan_done_noted:
            # After the wrap-up note, a fresh critique only starts another polish round
            # (live runs rebuilt the same cottage six times this way). Point at the finish.
            return {"note": "The build is complete enough: you were asked to wrap up. Do not start new polish "
                            "rounds. Add only brief items that are still missing, delete leftovers, then call finish."}
        if not self.config.get("vision_enabled", True):
            return {"error": "Vision is switched off. Check facts with get_scene_info (bounds, sizes)."}
        if self.vision_broken:
            return {"error": "Vision is unavailable this session (" + self.vision_broken + "). "
                             "Check contact, gaps and sizes with get_scene_info bounds instead."}
        mode = args.get("mode") or "angles"
        info = bridge.call(look.capture, mode, args.get("targets"), args.get("views"),
                           args.get("shading"))
        if info.get("error"):
            return info
        session.last_capture = info["path"]
        images = [info["path"]]
        refs = []
        if args.get("compare_reference"):
            refs = [p for p in self.config.get("references", []) if os.path.isfile(p)][:2]
            images += refs
        layout = ""
        if mode == "angles":
            layout = ("It is a contact sheet of views (" + ", ".join(info.get("views", []))
                      + "), " + info.get("layout", "") + ". Front looks along +Y, right looks along -X.")
        ref_text = (f"The next {len(refs)} image(s) are the user's REFERENCE. Compare the scene to them."
                    if refs else "")
        prompt = prompts.VISION.format(layout=layout, refs=ref_text, brief=self.brief[:1500],
                                       question=args.get("question", "Judge the result."))
        session.add("look", f"Looking at the scene ({mode})")
        if self.agent_sees:
            caption = (f"Picture from look ({mode}: " + ", ".join(info.get("views") or [mode]) + "). " + layout
                       + (" " + ref_text if refs else "") + " Question: " + str(args.get("question", "")))
            self.pending_images.append({"caption": caption, "paths": images})
            return {"view": mode, "views": info.get("views"),
                    "image": "The picture is attached in the next message. Judge it yourself."}
        model = self._vision_model(client)
        if not model:
            return {"error": "No vision model is available on this account. "
                             "Check contact, gaps and sizes with get_scene_info bounds instead."}
        t0 = time.time()
        try:
            description = client.describe_image(model, prompt, images)
        except llm.LLMError as e:
            if e.status in (400, 404) and not self.vision_retried:
                # The saved model does not exist or rejects images: find another one once.
                self.vision_retried = True
                self.config["vision_model"] = ""
                model = self._vision_model(client, exclude=model)
                if model:
                    try:
                        description = client.describe_image(model, prompt, images)
                    except llm.LLMError as e2:
                        return self._vision_failed(e2)
                else:
                    return self._vision_failed(e)
            else:
                return self._vision_failed(e)
        if not description and not self.vision_retried:
            self.vision_retried = True
            self.config["vision_model"] = ""
            other = self._vision_model(client, exclude=model)
            if other:
                try:
                    description, model = client.describe_image(other, prompt, images), other
                except llm.LLMError as e:
                    return self._vision_failed(e)
        self._trace("vision", model=model, seconds=round(time.time() - t0, 1))
        return {"view": mode, "views": info.get("views"), "vision_model": model,
                "vision_report": description or "(the vision model returned nothing)"}

    # ------------------------------------------------------------------
    # Harness-driven vision: references become a spec, the art director reviews
    # ------------------------------------------------------------------
    def _references(self):
        return [p for p in self.config.get("references", []) if os.path.isfile(p)][:2]

    def _vision_ready(self):
        return self.config.get("vision_enabled", True) and not self.vision_broken

    def _ask_vision(self, client, prompt, images):
        """Text from the vision model, or "" (vision trouble never stops the task)."""
        model = self._vision_model(client)
        if not model:
            return ""
        t0 = time.time()
        try:
            text = client.describe_image(model, prompt, images)
        except llm.LLMError as e:
            self._vision_failed(e)
            return ""
        self._trace("vision", model=model, seconds=round(time.time() - t0, 1))
        return (text or "").strip()

    def _reference_spec(self, client):
        """Turn the user's reference images into a structured build spec, before any building."""
        refs = self._references()
        if not refs or not self._vision_ready():
            return
        session.add("look", "Studying your reference image" + ("s" if len(refs) > 1 else ""))
        if self.agent_sees:
            self.pending_images.append({"caption": "The user's REFERENCE image(s). Build to match them.",
                                        "paths": refs})
            self._send_pending_images(review=False)
            return
        spec = self._ask_vision(client, prompts.REFERENCE_SPEC.format(brief=self.brief[:1200]), refs)
        if not spec:
            return
        self.ref_spec = spec[:3000]
        self.messages.append({"role": "user", "content":
            "[Reference spec: BlendWMe's eyes studied the user's reference image(s). Your build must "
            "clearly follow it: same parts, counts, proportions and colours.]\n" + self.ref_spec})
        self._trace("reference_spec", chars=len(self.ref_spec))

    def _maybe_review(self, client):
        """Every few successful build steps, the art director looks without being asked."""
        if session.mode != "AUTO" or not self._vision_ready() or self.ok_steps == 0:
            return
        first = REVIEW_FIRST_WITH_REF if self._references() else REVIEW_FIRST
        if self.reviews == 0 and self.ok_steps < first:
            return
        if self.reviews > 0 and self.ok_steps - self.reviewed_at < REVIEW_EVERY:
            return
        if self.reviews >= REVIEW_MAX:
            return          # enough critique; the final check before finish still happens
        self._review(client, f"after {self.ok_steps} build steps")

    def _maybe_wrap_up(self):
        """Once every plan part is built, say so plainly, so the model finishes instead of polishing forever."""
        if session.mode != "AUTO" or self.plan_done_noted:
            return
        if not session.plan or not all(p["done"] for p in session.plan):
            if self.ok_steps >= WRAP_UP_STEPS:
                # No plan to tick off (or it was never ticked): stop open-ended polishing.
                self.plan_done_noted = True
                self.messages.append({"role": "user", "content":
                    f"[You have made {self.ok_steps} successful build steps. Wrap up now: add only items from the "
                    "brief that are still missing, delete leftovers, then call finish.]"})
            return
        if all(p["done"] for p in session.plan):
            self.plan_done_noted = True
            self.messages.append({"role": "user", "content":
                f"[All {len(session.plan)} plan parts are built. Fix only clear problems from the latest review "
                "(missing parts, floating or intersecting parts), then call finish. Do not start new polish rounds.]"})

    def _review(self, client, when):
        """Capture the scene from several angles and add an art director review. Returns its text."""
        info = bridge.call(look.capture, "angles")
        if info.get("error"):
            return ""
        self.reviews += 1
        self.reviewed_at = self.ok_steps
        refs = self._references()
        session.last_capture = info["path"]
        session.add("look", "Art director review " + when)
        layout = ("The first image is a contact sheet of the scene (" + ", ".join(info.get("views", []))
                  + "), " + info.get("layout", "") + ". Front looks along +Y, right looks along -X.")
        ref_text = f"The next {len(refs)} image(s) are the user's REFERENCE." if refs else ""
        header = (f"[Art director review {when}. BlendWMe captured the scene from several angles"
                  + (" next to the user's reference" if refs else "") + " and judged it against the brief. "
                  + "THE BRIEF IS: \"" + self.brief[:400] + "\". Everything you build must serve this brief.]")
        stock = bridge.call(scene_info.inventory)
        stock_text = ("\nObjects you made (exact sizes):\n" + "\n".join(stock)
                      + "\nDelete anything left over from an earlier attempt that is not part of the final design."
                      if stock else "")
        if self.agent_sees:
            self.pending_images.append({"caption": header + " " + layout + " " + ref_text + stock_text,
                                        "paths": [info["path"]] + refs})
            self._trace("review", when=when, by="agent")
            return "attached"
        prompt = prompts.REVIEW.format(layout=layout, refs=ref_text, brief=self.brief[:1200],
                                       spec=("Reference spec: " + self.ref_spec[:1500]) if self.ref_spec else "",
                                       ref_word=" and reference" if refs else "")
        if stock:
            # The critic also gets the true names and sizes, so a huge leftover does not hide the real subject.
            prompt += ("\nThe objects in the scene, with exact sizes (use this to tell parts apart and to spot "
                       "leftovers or wrong scale):\n" + "\n".join(stock))
        text = self._ask_vision(client, prompt, [info["path"]] + refs)
        if not text:
            return ""
        self.messages.append({"role": "user", "content": header + "\n" + text[:2500] + stock_text
                              + "\nFix the most important problems first, then continue the plan."})
        self._trace("review", when=when, by="vision", chars=len(text))
        return text

    def _vision_model(self, client, exclude=""):
        """The configured vision model, or one discovered from the account's catalogue."""
        current = self.config.get("vision_model", "")
        if current and current != exclude and "nemotron" not in current.lower():
            return current
        try:
            models = client.list_models_detailed()
        except llm.LLMError:
            return ""
        models = [m for m in models if m["id"] != exclude]
        chosen = llm.pick_vision_model(models)
        if chosen:
            self.config["vision_model"] = chosen
            session.add("info", f"Using {chosen} as BlendWMe's eyes.")

            def save():
                p = prefs.addon_prefs()
                if p:
                    p.vision_model = chosen
            try:
                bridge.call(save)
            except Exception:
                pass
        return chosen

    def _vision_failed(self, error):
        """Vision trouble must never stop the build. Carry on without eyes."""
        self.vision_broken = str(error)[:120]
        session.add("error", "Vision failed, continuing without it: " + str(error)[:160])
        return {"error": "Vision is unavailable (" + str(error)[:160] + "). Continue the work. "
                         "Check contact, gaps and sizes with get_scene_info bounds instead."}

    def _tool_point_at(self, args, client):
        pointer = {k: args[k] for k in ("object", "location", "editor", "region", "label") if args.get(k)}
        session.set_pointers([pointer] if pointer else [])
        return {"ok": True}

    def _tool_teach_step(self, args, client):
        title = str(args.get("title") or "Your turn")
        session.set_card({"kind": "teach", "title": title,
                          "text": str(args.get("instruction") or ""),
                          "why": str(args.get("why") or "")})
        pointers = []
        if args.get("point_object"):
            pointers.append({"object": args["point_object"]})
        if args.get("point_editor"):
            pointers.append({"editor": args["point_editor"], "region": args.get("point_region") or "WINDOW"})
        session.set_pointers(pointers)
        session.add("step", "Your turn: " + title, detail=args.get("instruction", ""))
        session.teach_choice = None
        session.set_status(WAIT_USER)
        check = str(args.get("check") or "").strip()
        passes = 0
        outcome = None
        while outcome is None:
            if self._wait_signal(timeout=0.5):
                outcome = {"done": "user_said_done", "show_me": "show_me",
                           "skip": "skipped"}.get(session.teach_choice, "user_said_done")
                break
            if check:
                ok = bridge.call(_eval_check, check)
                passes = passes + 1 if ok else 0
                if passes >= 2:   # stable for about a second
                    outcome = "completed"
        session.set_pointers([])
        current = bridge.call(scene_info.snapshot)
        lines, changed = scene_info.diff(self.last_snapshot or {}, current)
        bridge.call(scene_info.tag_author, changed["added"] + changed["modified"], "user")
        self.last_snapshot = current
        if outcome == "show_me":
            self.show_me_allowed = True
        label = {"completed": "Nice, step done.", "user_said_done": "Marked done.",
                 "show_me": "Showing you how.", "skipped": "Skipped."}[outcome]
        session.add("info", label)
        result = {"result": outcome, "user_changes": lines[:30] or ["(no object-level changes)"]}
        if check:
            result["check_passed"] = bool(bridge.call(_eval_check, check))
        return result

    def _tool_ask_user(self, args, client):
        question = str(args.get("question") or "")
        session.add("ai", question)
        session.set_card({"kind": "ask", "title": "Question", "text": question})
        session.set_status(ASKING)
        while session.inbox.empty():
            self._wait_signal()
        answers = []
        while not session.inbox.empty():
            answers.append(session.inbox.get_nowait())
        session.set_card(None)
        return {"answer": "\n".join(answers)}

    def _tool_finish(self, args, client):
        # Quality gate: before the first finish, the art director looks at the final result once
        # (only if something changed since the last review). The model then fixes or finishes again.
        if (session.mode == "AUTO" and not self.final_reviewed and self.ok_steps > 0
                and self._vision_ready() and (self.reviews == 0 or self.ok_steps > self.reviewed_at)):
            self.final_reviewed = True
            review = self._review(client, "before finishing")
            if review:
                return {"not_finished_yet": "BlendWMe's art director checked the result before you finish "
                                            "(see the review). Fix any real problems (missing parts from the "
                                            "brief, floating or intersecting parts, wrong proportions). If it "
                                            "is good, call finish again."}
        summary = str(args.get("summary") or "Done.")
        session.add("ai", summary)
        session.set_card({"kind": "done", "title": "Done", "text": summary})
        session.set_pointers([])
        session.set_status(DONE)
        self.finished = True
        return {"ok": True}


def _python_block(text):
    """The Python code a model wrote in its message text (```python fences), or ""."""
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", text or "", re.S)
    if not blocks:
        # an unclosed fence at the end of a reply
        m = re.search(r"```(?:python|py)\s*\n(.*)$", text or "", re.S)
        blocks = [m.group(1)] if m else []
    code = "\n\n".join(b.strip("\n") for b in blocks).strip()
    return code if ("bpy" in code or "bwm" in code) else ""


def _code_tool_alias(name, args):
    """Tool names models know from elsewhere that carry Python code -> run_python.

    Nemotron 3 Ultra called 'blender_exec' in three live runs, and MCP for Blender's tool is
    'execute_blender_code'. Any unknown tool whose arguments hold code is run as run_python.
    """
    known = {t["function"]["name"] for t in tools.ALL}
    if name in known or not isinstance(args, dict):
        return name, args
    code = next((args[k] for k in ("code", "python", "script", "source") if isinstance(args.get(k), str)), None)
    if code is None:
        return name, args
    fixed = {"title": str(args.get("title") or "Step"), "explain": str(args.get("explain") or ""),
             "code": code}
    if args.get("part") is not None:
        fixed["part"] = args["part"]
    return "run_python", fixed


def _plan_parts(args):
    """The plan's parts, however the model sent them.

    Nemotron sent plan(title=..., style=..., steps="[\\"...\\", ...]") 15 times in one run and was
    rejected every time. parts, steps, items or plan are accepted, as a list, a JSON string
    or one part per line; dicts become their text.
    """
    value = None
    for key in ("parts", "steps", "items", "plan", "tasks"):
        if args.get(key):
            value = args[key]
            break
    if isinstance(value, str):
        text = value.strip()
        try:
            value = json.loads(text)
        except ValueError:
            value = [line.strip(" -*0123456789.)\t") for line in text.splitlines()]
    if isinstance(value, dict):
        value = list(value.values())
    out = []
    for p in value or []:
        if isinstance(p, dict):
            p = p.get("name") or p.get("part") or p.get("title") or " ".join(str(v) for v in p.values())
        p = str(p).strip()
        if p:
            out.append(p)
    return out


def _step_key(title, part):
    """Which plan step an attempt belongs to, so failures are counted per step."""
    if part:
        return f"part {part}"
    title = (title or "").strip().lower()
    return title if title and title != "step" else "(untitled)"


def _is_probe(title, code):
    """True for steps that only test the environment instead of building something."""
    if _PROBE_CODE.search(code):
        return not _BUILDS.search(_PROBE_CODE.sub("", code)) or "sys.modules" in code
    return bool(_PROBE_TITLE.search(title or "")) and not _BUILDS.search(code)


def _bwm_names():
    return helpers.public_names()


def _coerce_types(name, args):
    """Fix values sent as text ("2.0", "[1, 2]", "true") using the tool's schema.

    MCP servers get this for free from their schema validation. Open models on
    OpenAI-style endpoints sometimes send every value as a string.
    """
    schema = next((t["function"]["parameters"]["properties"] for t in tools.ALL
                   if t["function"]["name"] == name), None)
    if not schema or not isinstance(args, dict):
        return args
    fixed = dict(args)
    for key, value in args.items():
        want = (schema.get(key) or {}).get("type")
        if not isinstance(value, str) or want in (None, "string"):
            continue
        try:
            if want == "integer":
                fixed[key] = int(float(value))
            elif want == "number":
                fixed[key] = float(value)
            elif want == "boolean":
                fixed[key] = value.strip().lower() in ("true", "1", "yes")
            elif want in ("array", "object"):
                fixed[key] = json.loads(value)
        except (ValueError, TypeError):
            pass
    return fixed


def _eval_check(expression):
    import bpy
    try:
        return bool(eval(expression, {"bpy": bpy, "C": bpy.context, "D": bpy.data,
                                      "__builtins__": {"any": any, "all": all, "len": len,
                                                       "abs": abs, "round": round, "min": min,
                                                       "max": max, "sum": sum, "next": next,
                                                       "True": True, "False": False}}))
    except Exception:
        return False


agent = Agent()
