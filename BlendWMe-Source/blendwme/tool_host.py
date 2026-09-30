"""BlendWMe's tools for the outside brains (Claude Code and Codex).

The brains run as separate programs. They call BlendWMe's tools through the
MCP server (blendwme_mcp), which forwards each call over the local socket
(socket_bridge.py) to this module. Every call runs here, in the add-on:

    brain -> MCP server -> socket -> ToolHost.call() -> Blender (main thread)

The tools behave like the 1.0.5 agent's tools, so Auto / Work together
(Watch) / Teach, pause and take control, the plan checklist, the overlay and
the red pointers work the same whichever brain is active:

- run_python keeps the undo step, repair, pre-check and rollback (executor.py),
  the Teach gate, the Watch "Next" wait, the stuck guard and the art director
  reviews. Every step is signed with the brain that made it.
- look returns the picture itself: Claude and Codex can see.
- teach_step, ask_user and the Watch wait block until the user acts. A wait is
  capped (WAIT_CAP) so it stays inside the brains' tool-call timeouts.
- Messages typed while a brain works, and edits the user made since the last
  call, are attached to the next tool result.
"""

import base64
import json
import os
import re
import threading
import time
import traceback

import bpy

from . import bridge, executor, helpers, knowledge, look, prompts, scene_info, tools
from .state import ASKING, DONE, PAUSED, THINKING, WAIT_NEXT, WAIT_USER, WORKING, normalize_mode, session

WAIT_CAP = 45 * 60        # seconds one tool call may wait for the user (tool timeouts are 60 min)
REVIEW_FIRST = 3          # successful build steps before the first art director review
REVIEW_FIRST_WITH_REF = 1
REVIEW_EVERY = 4
REVIEW_MAX = 3
STUCK_FAILS = 4           # failures of the SAME step before the user gets the wheel
STUCK_PRECHECK = 6
MAX_IMAGE_BYTES = 4 * 1024 * 1024

BRAIN_LABELS = {"claude": "Claude", "codex": "Codex", "api": "API", "user": "You", "ai": "BlendWMe"}


def label(brain):
    return BRAIN_LABELS.get(brain, str(brain).title())


class Stopped(Exception):
    pass


class StillWaiting(Exception):
    """A wait reached WAIT_CAP. The message tells the brain what to do."""


class NullJournal:
    """Stands in until a project journal is attached (journal.py)."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _image(path):
    """{"data": base64, "mime": ...} for a picture file, or None."""
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) > MAX_IMAGE_BYTES:
            return None
        ext = os.path.splitext(path)[1].lower()
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
        with open(path, "rb") as f:
            return {"data": base64.b64encode(f.read()).decode("ascii"), "mime": mime}
    except OSError:
        return None


class ToolHost:
    def __init__(self):
        self.journal = NullJournal()
        self.lock = threading.Lock()     # one tool at a time, whichever brain calls
        self.active_brain = "claude"
        self.last_snapshot = None
        self.reset_task("", [])

    # ------------------------------------------------------------------
    # Called by the relay (engines) around each brain turn
    # ------------------------------------------------------------------
    def reset_task(self, brief, references):
        """A new task: forget per-task counters (the journal keeps the history)."""
        self.brief = brief or ""
        self.references = [p for p in references or [] if os.path.isfile(p)][:2]
        self.show_me_allowed = False
        self.review_pending = False
        self.ok_steps = self.reviewed_at = self.reviews = 0
        self.plan_done_noted = False
        self.task_started_at = time.time()
        self.looked_at_step = 0          # ok_steps when the brain last looked (plan done=[n] needs it)
        self.begin_turn()

    def begin_turn(self):
        """A new message from the user: a fresh failure count and one final check again."""
        self.step_fails, self.step_rejects, self.last_errors = {}, {}, {}
        self.final_reviewed = False
        self.finished = False
        self.retry_entry = None
        self.fail_streak = 0

    def take_snapshot(self):
        """Remember the scene, so the next call can report what the user changed (main thread)."""
        self.last_snapshot = scene_info.snapshot()

    def user_changes(self):
        """Lines describing what the user changed since the last snapshot ([] if nothing). Main thread."""
        current = scene_info.snapshot()
        if self.last_snapshot is None:
            self.last_snapshot = current
            return []
        lines, changed = scene_info.diff(self.last_snapshot, current)
        self.last_snapshot = current
        if lines:
            scene_info.tag_author(changed["added"] + changed["modified"], "user")
            self.journal.record_user_edits(lines)
        return lines

    # ------------------------------------------------------------------
    # The entry point (socket thread)
    # ------------------------------------------------------------------
    def call(self, name, args, brain="claude"):
        """Run one tool. Returns {"result": dict or str, "images": [{"data", "mime"}]}."""
        session.mode = normalize_mode(session.mode)
        args = args if isinstance(args, dict) else {}
        known = {t["name"] for t in tools.mcp_tools()}
        if name not in known:
            return {"result": {"error": f"There is no BlendWMe tool called {name!r}. Tools: "
                                        + ", ".join(sorted(known))}, "images": []}
        allowed = tools.names_for_mode(session.mode)
        if name not in allowed:
            return {"result": {"error": f"{name!r} cannot be used in {session.mode} mode. "
                                        f"Tools here: {', '.join(sorted(allowed))}."}, "images": []}
        args = _coerce_types(name, args)
        with self.lock:
            images = []
            try:
                self._check_stop()
                self._wait_if_paused()
                changes = bridge.call(self.user_changes)
                notes = self._read_inbox()
                session.tool_steps += 1
                if name not in ("ask_user", "teach_step"):
                    session.set_status(WORKING)
                result, images = getattr(self, "_tool_" + name)(args, brain)
                if isinstance(result, dict):
                    result = {k: v for k, v in result.items() if not k.startswith("_")}
                    if changes:
                        session.add("info", "I noticed your changes: " + "; ".join(changes))
                        result["user_changes"] = ("The user changed the scene themselves before this call. These "
                                                  "are intentional creative decisions: keep them and build around "
                                                  "them.\n" + "\n".join(changes[:40]))
                    if notes:
                        result["user_message"] = "Message from the user while you work: " + "\n".join(notes)
            except Stopped:
                result = {"stopped": "The user pressed Stop. End your turn now and do not call more tools. "
                                     "Everything already built stays in the scene."}
            except StillWaiting as e:
                result = {"still_waiting": str(e)}
            except Exception as e:   # a tool problem goes back to the brain; it never crashes Blender
                traceback.print_exc()
                result = {"error": f"{type(e).__name__}: {e}"}
            finally:
                if session.status == WORKING:
                    session.set_status(THINKING)
        return {"result": result, "images": [i for i in images if i]}

    # ------------------------------------------------------------------
    # Waiting for the user (always capped, always stoppable)
    # ------------------------------------------------------------------
    def _check_stop(self):
        if session.stop_flag.is_set():
            raise Stopped()

    def _wait_if_paused(self, card=None):
        if not session.pause_flag.is_set():
            return
        previous_card = session.card
        session.set_status(PAUSED)
        session.set_card(card or {"kind": "pause", "title": "You have control",
                                  "text": "Edit anything you like. Press Resume and I will continue from your version."})
        end = time.time() + WAIT_CAP
        while session.pause_flag.is_set():
            self._check_stop()
            if time.time() > end:
                raise StillWaiting("The user paused BlendWMe and has control. End your turn now. They will send "
                                   "a message when they want you to continue.")
            session.signal.wait(0.3)
            session.signal.clear()
        session.set_card(previous_card)
        session.set_status(THINKING)

    def _wait_signal(self, timeout=None):
        """Wait for Next / Done / an answer. Returns False when `timeout` passed first."""
        session.signal.clear()
        end = time.time() + min(timeout or WAIT_CAP, WAIT_CAP)
        while True:
            self._check_stop()
            if session.signal.wait(0.25):
                session.signal.clear()
                if session.pause_flag.is_set():
                    self._wait_if_paused()
                    continue
                return True
            if time.time() >= end:
                if timeout:
                    return False
                raise StillWaiting("The user has not answered for a long time. End your turn now and wait "
                                   "for their next message.")

    def _read_inbox(self):
        texts = []
        while not session.inbox.empty():
            texts.append(session.inbox.get_nowait())
        for t in texts:
            self.journal.record_user_message(t)
        return texts

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    def _tool_get_scene_info(self, args, brain):
        return bridge.call(scene_info.get_scene_info), []

    def _tool_get_object_info(self, args, brain):
        return bridge.call(scene_info.get_object_info, str(args.get("name", ""))), []

    def _tool_inspect_api(self, args, brain):
        return bridge.call(knowledge.inspect_api, str(args.get("path", ""))), []

    def _tool_load_skill(self, args, brain):
        name = str(args.get("name", ""))
        session.add("info", f"Reading skill card: {name}")
        return {"skill": name, "card": knowledge.load_skill(name)}, []

    def _tool_plan(self, args, brain):
        marks = args.get("done") or args.get("skip")
        parts = _plan_parts(args)
        if not parts and not marks:
            return {"error": "Give the parts as a list of short strings, e.g. "
                             "plan(parts=[\"<part> <size> m on <host>\", \"<next part> ...\"])."}, []
        result = {"ok": True}
        if parts:
            session.set_plan(parts)
            session.add("plan", "Plan: " + "; ".join(p.split(",")[0] for p in parts), brain=brain)
            self.journal.record_plan(brain, parts)
            result.update(parts=len(parts), note="Build them in order and pass part=<number> to run_python.")
        if marks:
            result.update(self._mark_plan(args, brain))
        return result, []

    def _mark_plan(self, args, brain):
        """plan(done=[n]) after a look, plan(skip=[{part, reason}]). Returns notes for the brain."""
        out = {}
        done = args.get("done") or []
        done = [done] if not isinstance(done, list) else done
        numbers = [int(n) for n in done if str(n).strip().isdigit()]
        if numbers:
            if self.ok_steps > self.looked_at_step:
                out["done_refused"] = ("Look at the result first (look), then mark the parts done. A part counts "
                                       "as done only after you have seen it.")
            else:
                for n in numbers:
                    session.tick_plan(n)
                    self.journal.record_plan_tick(n, brain)
                out["marked_done"] = numbers
        skips = args.get("skip") or []
        skips = [skips] if isinstance(skips, dict) else skips
        skipped = []
        for item in skips:
            if not isinstance(item, dict) or not str(item.get("part", "")).strip().isdigit():
                continue
            reason = str(item.get("reason") or "").strip()
            if not reason:
                out["skip_refused"] = "Give each skipped part a short reason the user can read."
                continue
            n = int(item["part"])
            session.skip_plan(n, reason)
            self.journal.record_plan_tick(n, brain, skipped=reason)
            skipped.append(n)
            if 1 <= n <= len(session.plan):
                session.add("info", f"Skipped \"{session.plan[n - 1]['name']}\": {reason}", brain=brain)
        if skipped:
            out["skipped"] = skipped
        return out

    def _plan_progress(self):
        if not session.plan:
            return {}
        done = sum(1 for p in session.plan if p["done"])
        return {"progress": f"PART {min(done + 1, len(session.plan))} OF {len(session.plan)}"}

    def _tool_run_python(self, args, brain):
        mode = session.mode
        if mode == "TEACH" and not self.show_me_allowed:
            return {"error": "In Teach mode the user does the work. Use teach_step. "
                             "run_python is only allowed right after teach_step returns show_me."}, []
        self.show_me_allowed = False
        title = str(args.get("title") or "Step")
        explain = str(args.get("explain") or "")
        code = str(args.get("code") or "")
        part = args.get("part")
        part = int(part) if isinstance(part, (int, float)) or str(part).isdigit() else None
        if _is_probe(title, code):
            return {"skipped": "Not run: this step only tests the environment. bpy, bmesh, math, Vector, C, D "
                               "and bwm are already defined in every step (import bwm also works). bwm has: "
                               + ", ".join(helpers.public_names()) + ". Build the next part of the plan now."}, []
        session.set_card({"kind": "work", "title": title, "text": explain,
                          "step": len(session.steps) + 1, **self._plan_progress()})
        result = bridge.call(executor.run, code, title)
        changed = result.pop("_changed", {}) or {}
        added = list(changed.get("added", []))
        modified = list(changed.get("modified", []))
        if result["ok"] and added:
            bridge.call(scene_info.tag_brain, added, brain)
        session.steps.append({"title": title, "explain": explain, "code": code, "ok": result["ok"], "brain": brain})
        self.last_snapshot = bridge.call(scene_info.snapshot)
        key = _step_key(title, part)
        self.journal.record_step(brain, title, explain, code, result["ok"], result.get("error", ""),
                                 added, modified, part)
        if result["ok"]:
            if self.fail_streak and self.retry_entry is not None:
                self.retry_entry["text"] = f"{title}: fixed a code error ({self.fail_streak + 1} tries)"
                self.retry_entry["detail"] = ""
                session.changed_flag = True
            self.retry_entry = None
            self.fail_streak = 0
            self.step_fails.pop(key, None)
            self.step_rejects.pop(key, None)
            self.journal.solve_struggle(brain, key)
            self.ok_steps += 1
            session.add("step", title, detail=explain, ok=True, brain=brain)
            if part:
                session.tick_plan(part)
                self.journal.record_plan_tick(part)
        else:
            self._step_failed(result, title, key, brain)

        targets = (added + modified)[:6]
        if mode == "AUTO" and session.build_style == "PIECE" and result["ok"] and targets and session.pace > 0:
            session.set_pointers([{"object": n, "building": True} for n in targets[:3]])
            time.sleep(min(session.pace, 10.0))
            session.set_pointers([])

        images = []
        if mode == "AUTO" and result["ok"]:
            review = self._maybe_review(brain)
            if review:
                result["art_director"], images = review
            note = self._wrap_up_note()
            if note:
                result["wrap_up"] = note
        if mode == "AUTO" and not session.plan and len(session.steps) == 1 and result["ok"]:
            result["reminder"] = "Call plan first so the user can follow the build."
        return result, images

    def _step_failed(self, result, title, key, brain):
        """Count a failure for THIS step, give sharper hints, and pause only when truly stuck."""
        error = result.get("error", "")
        short = error.strip().splitlines()[-1] if error.strip() else "unknown error"
        rejected = error.startswith("Pre-check found")
        counts = self.step_rejects if rejected else self.step_fails
        counts[key] = counts.get(key, 0) + 1
        n = counts[key]
        self.fail_streak = self.step_fails.get(key, 0) + self.step_rejects.get(key, 0)
        previous = self.last_errors.get(key)
        self.last_errors[key] = short
        self.journal.record_struggle(brain, key, f"{title}: {short}")
        text = f"{title}: fixing a code error"
        if self.retry_entry is None:
            self.retry_entry = session.add("retry", text, detail=short, brain=brain)
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
                                    "simplest way with bwm, even if it is less detailed. Do not reuse the code "
                                    "that failed.")
        elif n >= limit:
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

    def _capture_images(self, mode="angles", targets=None, views=None, shading=None, compare_reference=False):
        info = bridge.call(look.capture, mode, targets, views, shading)
        if info.get("error"):
            return info, []
        session.last_capture = info["path"]
        images = [_image(info["path"])]
        refs = self.references if compare_reference else []
        images += [_image(p) for p in refs]
        info["references_attached"] = len([r for r in refs if r])
        return info, images

    def _tool_look(self, args, brain):
        if session.mode == "AUTO" and self.plan_done_noted:
            return {"note": "The build is complete enough: you were asked to wrap up. Do not start new polish "
                            "rounds. Add only brief items that are still missing, delete leftovers, then call "
                            "finish."}, []
        mode = args.get("mode") or "angles"
        session.add("look", f"Looking at the scene ({mode})", brain=brain)
        self.looked_at_step = self.ok_steps
        info, images = self._capture_images(mode, args.get("targets"), args.get("views"), args.get("shading"),
                                            bool(args.get("compare_reference")))
        if info.get("error"):
            return info, []
        layout = ""
        if mode == "angles":
            layout = ("The first picture is a contact sheet of views (" + ", ".join(info.get("views", []))
                      + "), " + info.get("layout", "") + ". Front looks along +Y, right looks along -X.")
        refs = info.get("references_attached", 0)
        return {"view": mode, "views": info.get("views"), "layout": layout,
                "references": (f"The last {refs} picture(s) are the user's REFERENCE. Compare the scene to them."
                               if refs else "none attached"),
                "question": str(args.get("question", "")),
                "note": "Judge the picture yourself against the brief: what you see, problems, fixes."}, images

    # -- the art director (harness-driven reviews) ----------------------
    def _maybe_review(self, brain):
        first = REVIEW_FIRST_WITH_REF if self.references else REVIEW_FIRST
        if self.reviews >= REVIEW_MAX:
            return None
        if self.reviews == 0 and self.ok_steps < first:
            return None
        if self.reviews > 0 and self.ok_steps - self.reviewed_at < REVIEW_EVERY:
            return None
        return self._review(brain, f"after {self.ok_steps} build steps")

    def _review(self, brain, when):
        """(review text, images) from a multi-angle capture, or None."""
        info, images = self._capture_images("angles", compare_reference=bool(self.references))
        if info.get("error"):
            return None
        self.reviews += 1
        self.reviewed_at = self.ok_steps
        self.looked_at_step = self.ok_steps
        self.review_pending = True      # the brain's next words are its verdict (the relay records them)
        session.add("look", "Art director review " + when, brain=brain)
        stock = bridge.call(scene_info.inventory)
        refs = info.get("references_attached", 0)
        text = (f"Art director review {when}. BlendWMe captured the scene from several angles"
                + (" next to the user's reference" if refs else "") + ". THE BRIEF IS: \"" + self.brief[:400]
                + "\". The first picture is a contact sheet (" + ", ".join(info.get("views", [])) + "), "
                + info.get("layout", "") + ". Front looks along +Y, right looks along -X."
                + (f" The last {refs} picture(s) are the user's REFERENCE." if refs else "")
                + "\n" + prompts.REVIEW_RUBRIC
                + ("\nObjects made by the brains (exact sizes):\n" + "\n".join(stock)
                   + "\nDelete anything left over from an earlier attempt that is not part of the final design."
                   if stock else ""))
        self.journal.record_review(brain, when, stock)
        return text, images

    def _wrap_up_note(self):
        """Once every plan part is built, say so plainly, so the brain finishes instead of polishing forever."""
        if self.plan_done_noted:
            return ""
        if session.plan and all(p["done"] for p in session.plan):
            self.plan_done_noted = True
            return (f"All {len(session.plan)} plan parts are built. Fix only clear problems from the latest "
                    "review, then call finish. Do not start new polish rounds.")
        if self.ok_steps >= 20:
            self.plan_done_noted = True
            return (f"You have made {self.ok_steps} successful build steps. Wrap up now: add only items from the "
                    "brief that are still missing, delete leftovers, then call finish.")
        return ""

    # -- teaching and talking -------------------------------------------
    def _tool_point_at(self, args, brain):
        pointer = {k: args[k] for k in ("object", "location", "editor", "region", "tab", "label") if args.get(k)}
        if pointer.get("tab") and not pointer.get("editor"):
            pointer["editor"] = "PROPERTIES"
        if pointer.get("editor") == "PROPERTIES" and pointer.get("tab"):
            pointer["region"] = "NAVIGATION_BAR"
        session.set_pointers([pointer] if pointer else [])
        return {"ok": True}, []

    def _tool_teach_step(self, args, brain):
        title = str(args.get("title") or "Your turn")
        instruction = str(args.get("instruction") or "").strip()
        why = str(args.get("why") or "").strip()
        words = len(instruction.split())
        if words > TEACH_WORDS:
            return {"error": f"The instruction has {words} words. One action, under {TEACH_WORDS} words, on the "
                             "card; put the rest in why. Call teach_step again."}, []
        if not any(args.get(k) for k in ("point_object", "point_editor", "point_tab")):
            return {"error": "Add a pointer to the real control: point_tab (a Properties tab such as MODIFIER), "
                             "point_editor + point_region (a toolbar, sidebar or header), or point_object. For a "
                             "key press in the viewport use point_editor=VIEW_3D."}, []
        why = _numpad_note(instruction, why)
        session.set_card({"kind": "teach", "title": title, "text": instruction, "why": why})
        pointers = []
        if args.get("point_object"):
            pointers.append({"object": args["point_object"]})
        if args.get("point_editor") or args.get("point_tab"):
            pointer = {"editor": args.get("point_editor") or "PROPERTIES",
                       "region": args.get("point_region") or "WINDOW"}
            if args.get("point_tab"):
                pointer.update(tab=args["point_tab"], region="NAVIGATION_BAR")
            pointers.append(pointer)
        session.set_pointers(pointers)
        session.add("step", "Your turn: " + title, detail=args.get("instruction", ""), brain=brain)
        session.teach_choice = None
        session.set_status(WAIT_USER)
        check = str(args.get("check") or "").strip()
        passes = 0
        outcome = None
        end = time.time() + WAIT_CAP
        while outcome is None:
            if time.time() > end:
                session.set_pointers([])
                raise StillWaiting("The user has not finished this step yet. Call teach_step again with the same "
                                   "step to keep waiting, or end your turn.")
            if self._wait_signal(timeout=0.5):
                outcome = {"done": "user_said_done", "show_me": "show_me",
                           "skip": "skipped", "mode_changed": "mode_changed"}.get(session.teach_choice, "user_said_done")
                break
            if check:
                ok = bridge.call(_eval_check, check)
                passes = passes + 1 if ok else 0
                if passes >= 2:   # stable for about a second
                    outcome = "completed"
        session.set_pointers([])
        lines = bridge.call(self.user_changes)
        if outcome == "show_me":
            self.show_me_allowed = True
        session.add("info", {"completed": "Nice, step done.", "user_said_done": "Marked done.",
                             "show_me": "Showing you how.", "skipped": "Skipped.",
                             "mode_changed": "Lesson stopped because the mode changed."}[outcome])
        session.set_status(THINKING)
        result = {"result": outcome, "user_changes_in_this_step": lines[:30] or ["(no object-level changes)"]}
        if check:
            result["check_passed"] = bool(bridge.call(_eval_check, check))
        return result, []

    def _tool_ask_user(self, args, brain):
        question = str(args.get("question") or "")
        session.add("ai", question, brain=brain)
        session.set_card({"kind": "ask", "title": "Question", "text": question})
        session.set_status(ASKING)
        end = time.time() + WAIT_CAP
        while session.inbox.empty():
            if time.time() > end:
                raise StillWaiting("The user has not answered yet. End your turn; their answer will arrive as "
                                   "your next message.")
            self._wait_signal(timeout=1.0)
        answers = []
        while not session.inbox.empty():
            answers.append(session.inbox.get_nowait())
        for a in answers:
            self.journal.record_user_message(a, decision=True)
        session.set_card(None)
        session.set_status(THINKING)
        return {"answer": "\n".join(answers)}, []

    def _tool_finish(self, args, brain):
        summary = str(args.get("summary") or "Done.")
        # Done is never self-declared: every plan part is built, checked or skipped with a reason.
        still_open = session.open_parts()
        if still_open:
            listing = "; ".join(f"{n}. {name}" for n, name in still_open)
            return {"not_finished_yet": "These plan parts are still open: " + listing + ". Build them (run_python "
                                        "with part=<n>), or look and then plan(done=[n]) if they are already "
                                        "built, or plan(skip=[{\"part\": n, \"reason\": \"...\"}]) to leave "
                                        "one out. Then call finish again."}, []
        if _claims_render(summary) and not bridge.call(_render_exists, self.task_started_at):
            return {"not_finished_yet": "The summary says a render is done, but there is no render result or "
                                        "new rendered file. Render and save it (set scene.render.filepath, then "
                                        "bpy.ops.render.render(write_still=True)) or take the claim out of the "
                                        "summary."}, []
        # Quality gate: before the first finish, the art director looks at the final result once
        # (only if something changed since the last review). The brain then fixes or finishes again.
        if (session.mode == "AUTO" and not self.final_reviewed and self.ok_steps > 0
                and (self.reviews == 0 or self.ok_steps > self.reviewed_at)):
            self.final_reviewed = True
            review = self._review(brain, "before finishing")
            if review:
                text, images = review
                return {"not_finished_yet": "BlendWMe's art director checked the result before you finish. "
                                            "Judge the pictures: fix any real problems (missing parts from the "
                                            "brief, floating or intersecting parts, wrong proportions). If it is "
                                            "good, call finish again.",
                        "art_director": text}, images
        session.add("ai", summary, brain=brain)
        session.set_card({"kind": "done", "title": "Done", "text": summary})
        session.set_plan([])            # the plan card closes with the task (the journal keeps the plan)
        session.set_pointers([])
        session.set_status(DONE)
        self.finished = True
        self.journal.record_finish(brain, summary)
        return {"ok": True, "note": "Finished. End your turn now."}, []

    # -- the baton --------------------------------------------------------
    def _tool_handoff_note(self, args, brain):
        steps = args.get("next_steps") or []
        struggles = args.get("struggles") or []
        steps = [steps] if isinstance(steps, str) else [str(s) for s in steps]
        struggles = [struggles] if isinstance(struggles, str) else [str(s) for s in struggles]
        self.journal.record_handoff(brain, str(args.get("summary") or ""), steps, struggles)
        session.add("info", f"{label(brain)} wrote a handoff note.")
        return {"ok": True, "saved": "The note is in the project journal."}, []

    def _tool_read_journal(self, args, brain):
        section = str(args.get("section") or "brief")
        data = self.journal.section(section, brain, bridge.call(scene_info.makers))
        return (data if data is not None else {"journal": "No journal yet: this is a fresh project."}), []


# ----------------------------------------------------------------------
# Helpers for the tools (moved here from the 1.0.5 agent loop)
# ----------------------------------------------------------------------

TEACH_WORDS = 20      # a Teach cue card holds one action, under this many words

_NUMPAD = re.compile(r"\bnum\s?pad\b|\bnumpad\s?[0-9.]|\bnumpad\b", re.I)
_RENDER_CLAIM = re.compile(r"\b(rendered|render (is )?(done|finished|complete|saved|ready)|saved (the |a )?render|"
                           r"final render)\b", re.I)


def _numpad_note(instruction, why):
    """Many laptops have no numpad. Add the menu path unless Emulate Numpad is on."""
    if not _NUMPAD.search(instruction):
        return why
    try:
        emulated = bpy.context.preferences.inputs.use_emulate_numpad
    except Exception:
        emulated = False
    if emulated:
        return why
    note = ("No numpad? Use View > Viewpoint in the viewport header, or turn on Edit > Preferences > Input > "
            "Emulate Numpad to use the number row.")
    return (why + " " + note).strip()


def _claims_render(summary):
    return bool(_RENDER_CLAIM.search(summary or ""))


def _render_exists(since):
    """True when the render output file was written after `since` (the task's start).
    Blender does not tell Python whether its Render Result holds a picture, and an old
    file from an earlier project proves nothing, so only a fresh file counts."""
    render = bpy.context.scene.render
    # An animation frame is saved as <path>0001.png; a still (write_still) as <path>.png.
    candidates = [bpy.path.abspath(render.frame_path(frame=bpy.context.scene.frame_current))]
    still = bpy.path.abspath(render.filepath)
    if still and render.use_file_extension:
        still = bpy.path.ensure_ext(still, render.file_extension)
    candidates.append(still)
    return any(path and os.path.isfile(path) and os.path.getmtime(path) >= since - 1 for path in candidates)

# Steps that only probe the environment ("is bwm available?") instead of building.
_PROBE_CODE = re.compile(r"dir\(\s*(bwm|bpy|bmesh|bpy\.ops)\s*\)|hasattr\(\s*(bwm|bpy|bmesh)\s*,"
                         r"|sys\.modules|except\s+\(?\s*(ImportError|ModuleNotFoundError)|globals\(\)|locals\(\)")
_PROBE_TITLE = re.compile(r"\b(test|testing|availab|probe|debug|explore|environment)", re.I)
_BUILDS = re.compile(r"\bbwm\.(?!bounds|top\b|obj\b|col\b|mat\b)\w+\(|bpy\.ops\.|\.new\(|\.link\(|from_pydata|"
                     r"\.location|\.scale|\.rotation|modifiers|default_value")


def _plan_parts(args):
    """The plan's parts, however the model sent them.

    Models send the parts under different names and shapes, so parts, steps, items or plan are accepted, as a list, a JSON string
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


host = ToolHost()
