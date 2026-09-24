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
import threading
import time

from . import bridge, executor, knowledge, llm, look, prompts, scene_info, tools
from .state import (ASKING, DONE, ERROR, IDLE, PAUSED, THINKING, WAIT_NEXT,
                    WAIT_USER, WORKING, session)

MAX_TOOL_RESULT = 6000
KEEP_RECENT = 14          # newest messages kept in full
CONTEXT_BUDGET = 110_000  # characters before older results are compacted


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
        self.nudged = False
        self.finished = False
        self.fail_streak = 0

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
        self.finished = False
        self.fail_streak = 0
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

    def _system_prompt(self):
        return prompts.build_system(session.mode, session.level,
                                    self.config.get("blender_version", "4.2"),
                                    knowledge.skill_index())

    # ------------------------------------------------------------------
    # The loop (background thread)
    # ------------------------------------------------------------------
    def _run(self):
        client = llm.Client(self.config.get("api_key"), self.config.get("base_url"))
        try:
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
                self.messages.append(message)
                if message.get("content"):
                    session.add("ai", message["content"])

                calls = message.get("tool_calls") or []
                if not calls:
                    if finish_reason == "length":
                        self.messages.append({"role": "user", "content":
                            "Your reply was cut off by the length limit. Continue with a smaller step."})
                        continue
                    if not self.nudged and session.mode != "TEACH":
                        # Open models sometimes describe a plan and stop. Nudge once.
                        self.nudged = True
                        self.messages.append({"role": "user", "content":
                            "Continue the work using your tools. Call finish when the brief is "
                            "complete, or ask_user if you truly need an answer."})
                        continue
                    break  # the model is waiting for the user
                self.nudged = False

                # If you paused while the model was thinking, its plan may be stale.
                if session.pause_flag.is_set():
                    self._wait_if_paused()
                    if self._notice_user_changes(defer=True):
                        for call in calls:
                            self._tool_reply(call, {"skipped": "The user paused and edited the scene "
                                                    "before this ran. Re-plan from the current scene."})
                        self._flush_deferred()
                        continue

                for call in calls:
                    self._check_stop()
                    result = self._dispatch(call, client)
                    self._tool_reply(call, result)
                    if self.finished:
                        break
            if not self.finished and session.status not in (ERROR,):
                session.set_status(IDLE)
        except Stopped:
            session.set_card(None)
            session.set_pointers([])
            session.add("info", "Stopped. Everything already built stays in your scene.")
            session.set_status(IDLE)
            self._close_open_calls()
        except llm.LLMError as e:
            session.last_error = str(e)
            session.add("error", str(e))
            session.set_status(ERROR)
            self._close_open_calls()
        except Exception as e:  # never crash Blender
            import traceback
            traceback.print_exc()
            session.last_error = f"{type(e).__name__}: {e}"
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
                    reasoning=self.config.get("reasoning", "LOW"))
                session.calls += 1
                session.tokens += int(usage.get("total_tokens") or 0)
                session.last_latency = round(time.time() - t0, 1)
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
                session.add("info", f"Model busy ({e.status or 'network'}). Retrying in {delays[attempt]}s.")
                self._sleep(delays[attempt])

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

    def _compact(self, force=False):
        """Keep long sessions inside the model's context window."""
        total = sum(len(json.dumps(m, default=str)) for m in self.messages)
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
                            code = args.get("code", "")
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
    def _dispatch(self, call, client):
        name = call["function"]["name"]
        try:
            args = llm.parse_arguments(call["function"]["arguments"])
        except ValueError as e:
            return {"error": str(e) + " Send the tool call again with valid JSON arguments."}
        session.tool_steps += 1
        handler = getattr(self, "_tool_" + name, None)
        if handler is None or name not in {t["function"]["name"] for t in tools.for_mode(session.mode)}:
            return {"error": f"Tool {name!r} is not available in {session.mode} mode."}
        session.set_status(WORKING)
        try:
            return handler(args, client)
        except (Stopped, llm.LLMError):
            raise
        except Exception as e:
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
        session.set_card({"kind": "work", "title": title, "text": explain,
                          "step": len(session.steps) + 1})
        result = bridge.call(executor.run, code, title)
        changed = result.get("_changed", {})
        session.steps.append({"title": title, "explain": explain, "code": code, "ok": result["ok"]})
        self.last_snapshot = bridge.call(scene_info.snapshot)
        retry = self.fail_streak > 0
        session.add("step", title + (" (retry)" if retry and result["ok"] else ""), detail=explain,
                    ok=result["ok"])
        if result["ok"]:
            self.fail_streak = 0
        else:
            self.fail_streak += 1
            session.add("error", result.get("error", "")[:300]
                        + ("  Undone, trying again." if result.get("rolled_back") else ""))
            if self.fail_streak == 2:
                result["stuck_hint"] = ("This failed twice in a row. Change approach: split it into smaller "
                                        "steps, use bpy.data or bmesh instead of operators, or call inspect_api "
                                        "to check names before trying again.")
            elif self.fail_streak >= 4:
                # Do not burn tokens on a loop. Hand the wheel to the user.
                session.add("info", "I'm stuck on this step, so I paused. Fix it yourself or press Resume "
                                    "and I will try a different way.")
                session.pause_flag.set()
                self._wait_if_paused({"kind": "ask", "title": f"I'm stuck: {title}",
                                      "text": "This step failed 4 times. Error: " + result.get("error", "")[-160:]
                                              + "  Edit the scene or tell me what to do, then press Resume."})
                self.fail_streak = 0
                result["user"] = ("You failed 4 times and were paused. The user has now resumed. Any message "
                                  "or scene change they made follows. Try a clearly different approach.")
        targets = (changed.get("added", []) + changed.get("modified", []))[:6]
        if mode in ("WATCH", "TEACH") and result["ok"]:
            session.set_pointers([{"object": n} for n in targets])
            session.set_card({"kind": "watch", "title": title, "text": explain,
                              "step": len(session.steps)})
            session.set_status(WAIT_NEXT)
            self._wait_signal(timeout=session.watch_auto or None)
            session.set_pointers([])
            result["user"] = "The user watched this step and pressed Next."
        return result

    def _tool_look(self, args, client):
        if not self.config.get("vision_model"):
            return {"error": "Vision is off. Use get_scene_info and get_object_info for facts."}
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
        description = client.describe_image(self.config["vision_model"], prompt, images)
        return {"view": mode, "views": info.get("views"), "vision_report": description}

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
        summary = str(args.get("summary") or "Done.")
        session.add("ai", summary)
        session.set_card({"kind": "done", "title": "Done", "text": summary})
        session.set_pointers([])
        session.set_status(DONE)
        self.finished = True
        return {"ok": True}


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
