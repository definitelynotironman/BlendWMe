"""The project journal: "the baton".

BlendWMe, not the AI, holds the memory of a project. The journal records,
automatically and for every brain:

    - the brief and the user's decisions (answers, messages)
    - the plan checklist
    - every step, signed by the brain that made it (Claude / Codex / API), with code and result
    - who made each object (read live from the scene: Claude / Codex / API / the user)
    - struggles: failed attempts and problems nobody has solved yet
    - the latest art director review
    - handoff notes and next steps, and every switch between brains

From this it builds a compact HANDOFF BRIEF (with a size limit) for the brain
that takes over, including "what changed since you last worked here". So a
hand-over loses nothing, even when a brain hit its usage limit and could not
write a note.

Saved with each .blend project: a sidecar folder next to the file
(<name>_blendwme/journal.json) and a Text block ("BlendWMe Journal") inside the
.blend, so it travels with the file. When both exist, the newer one wins.
"""

import json
import os
import threading
import time

TEXT_NAME = "BlendWMe Journal"
SIDECAR_SUFFIX = "_blendwme"
FILE_NAME = "journal.json"
MAX_EVENTS = 600
MAX_CODE = 1500
BRIEF_LIMIT = 6000
LABELS = {"claude": "Claude", "codex": "Codex", "user": "the user", "ai": "BlendWMe"}


def _label(who):
    return LABELS.get(who, str(who))


def _empty():
    return {"version": 1, "seq": 0, "brief": "", "decisions": [], "plan": [], "events": [], "struggles": [],
            "review": None, "handoffs": [], "sessions": {}, "last_seen": {}, "active": "claude", "tasks": []}


class Journal:
    def __init__(self):
        self.lock = threading.RLock()
        self.data = _empty()
        self.dirty = False

    # ------------------------------------------------------------------
    # Recording (any thread)
    # ------------------------------------------------------------------
    def _add(self, kind, by, **fields):
        with self.lock:
            self.data["seq"] += 1
            event = {"n": self.data["seq"], "t": round(time.time()), "kind": kind, "by": by}
            event.update({k: v for k, v in fields.items() if v not in (None, "", [])})
            self.data["events"].append(event)
            if len(self.data["events"]) > MAX_EVENTS:
                del self.data["events"][:100]
            self.dirty = True
            return event

    def record_task(self, brief):
        with self.lock:
            if self.data["brief"]:
                self.data["tasks"].append({"brief": self.data["brief"][:300], "ended": round(time.time())})
                del self.data["tasks"][:-10]
            self.data["brief"] = brief
            self.data["decisions"] = []
            self.data["plan"] = []
            self.data["review"] = None
            for s in self.data["struggles"]:
                s["open"] = False
            self._add("task", "user", text=brief[:2000])

    def record_user_message(self, text, decision=False):
        with self.lock:
            if decision or len(self.data["decisions"]) < 30:
                self.data["decisions"].append(text[:400])
            self._add("user_message", "user", text=text[:1000])

    def record_user_edits(self, lines):
        self._add("user_edit", "user", lines=[str(x)[:200] for x in lines[:20]])

    def record_plan(self, brain, parts):
        with self.lock:
            old = {p["name"]: p for p in self.data["plan"]}
            self.data["plan"] = [{"name": p[:120], "done": old.get(p[:120], {}).get("done", False),
                                  "by": old.get(p[:120], {}).get("by", "")} for p in parts][:40]
            self._add("plan", brain, parts=[p[:120] for p in parts[:40]])

    def record_plan_tick(self, part, brain=""):
        with self.lock:
            if isinstance(part, int) and 1 <= part <= len(self.data["plan"]):
                self.data["plan"][part - 1]["done"] = True
                if brain:
                    self.data["plan"][part - 1]["by"] = brain
                self.dirty = True

    def record_step(self, brain, title, explain, code, ok, error="", added=(), modified=(), part=None):
        if ok and part:
            self.record_plan_tick(part, brain)
        self._add("step", brain, title=title[:80], explain=explain[:300], code=code[:MAX_CODE],
                  ok=bool(ok), error=(error or "")[:400], added=list(added)[:20], modified=list(modified)[:20],
                  part=part)

    def record_struggle(self, brain, key, text):
        with self.lock:
            for s in self.data["struggles"]:
                if s["open"] and s["key"] == key:
                    s.update(text=text[:300], by=brain, count=s.get("count", 1) + 1, t=round(time.time()))
                    self.dirty = True
                    return s
            s = {"key": key, "text": text[:300], "by": brain, "count": 1, "open": True, "t": round(time.time())}
            self.data["struggles"].append(s)
            del self.data["struggles"][:-30]
            self.dirty = True
            return s

    def solve_struggle(self, brain, key):
        with self.lock:
            for s in self.data["struggles"]:
                if s["open"] and s["key"] == key:
                    s["open"] = False
                    s["solved_by"] = brain
                    self._add("solved", brain, text=s["text"][:200])

    def record_review(self, brain, when, objects=()):
        with self.lock:
            self.data["review"] = {"by": brain, "when": when, "n": self.data["seq"] + 1,
                                   "objects": list(objects)[:30], "verdict": ""}
            self._add("review", brain, text=when)

    def set_review_verdict(self, brain, text):
        with self.lock:
            if self.data["review"] is not None:
                self.data["review"]["verdict"] = text[:800]
                self.dirty = True

    def record_said(self, brain, text):
        """A short note of what a brain told the user (context for the next brain)."""
        if text.strip():
            self._add("said", brain, text=text.strip()[:400])

    def record_handoff(self, brain, summary, next_steps=(), struggles=()):
        with self.lock:
            note = {"by": brain, "t": round(time.time()), "summary": summary[:800],
                    "next_steps": [s[:200] for s in next_steps][:10], "struggles": [s[:200] for s in struggles][:10]}
            self.data["handoffs"].append(note)
            del self.data["handoffs"][:-10]
            for text in note["struggles"]:
                self.record_struggle(brain, "note: " + text[:60], text)
            self._add("handoff_note", brain, text=summary[:400])

    def record_finish(self, brain, summary):
        self._add("finish", brain, text=summary[:400])

    def record_switch(self, source, target, reason):
        with self.lock:
            self.data["active"] = target
            self._add("switch", source, to=target, text=reason)

    def record_error(self, brain, text):
        self._add("error", brain, text=text[:300])

    def set_session(self, brain, session_id):
        with self.lock:
            if session_id and self.data["sessions"].get(brain) != session_id:
                self.data["sessions"][brain] = session_id
                self.dirty = True

    def mark_seen(self, brain):
        """The brain has now seen everything up to here ("since you last worked here" starts after it)."""
        with self.lock:
            self.data["last_seen"][brain] = self.data["seq"]
            self.dirty = True

    def has_history(self):
        return bool(self.data["events"])

    # ------------------------------------------------------------------
    # The handoff brief
    # ------------------------------------------------------------------
    def changes_since(self, brain):
        """Events by others since this brain last worked here, oldest first."""
        with self.lock:
            seen = self.data["last_seen"].get(brain, 0)
            return [e for e in self.data["events"] if e["n"] > seen and e["by"] != brain]

    @staticmethod
    def describe(event):
        who = _label(event["by"])
        kind = event["kind"]
        if kind == "step":
            made = (" -> made " + ", ".join(event["added"][:8])) if event.get("added") else ""
            changed = (" -> changed " + ", ".join(event["modified"][:8])) if event.get("modified") else ""
            if event.get("ok"):
                return f"{who}: step \"{event.get('title', '')}\"{made}{changed}"
            return f"{who}: step \"{event.get('title', '')}\" FAILED: {event.get('error', '')[-160:]}"
        if kind == "user_edit":
            return "The user edited the scene: " + "; ".join(event.get("lines", [])[:6])
        if kind == "user_message":
            return f"The user said: \"{event.get('text', '')[:200]}\""
        if kind == "plan":
            return f"{who} wrote the plan ({len(event.get('parts', []))} parts)"
        if kind == "handoff_note":
            return f"{who} left a handoff note"
        if kind == "switch":
            return f"{who} handed over to {_label(event.get('to'))} ({event.get('text', '')})"
        if kind == "said":
            return f"{who} told the user: \"{event.get('text', '')[:200]}\""
        if kind == "review":
            return f"Art director review for {who} ({event.get('text', '')})"
        if kind == "solved":
            return f"{who} solved: {event.get('text', '')[:120]}"
        if kind == "finish":
            return f"{who} finished: {event.get('text', '')[:160]}"
        if kind == "task":
            return f"New task: {event.get('text', '')[:200]}"
        return f"{who}: {kind} {event.get('text', '')[:120]}"

    def handoff_brief(self, brain, makers=None, limit=BRIEF_LIMIT, review_first=True):
        """The compact text a brain gets when it takes over or comes back.

        review_first (the "Returning brain reviews the other's work first" setting): the brain
        looks at the scene and fixes the open struggles before anything new.
        """
        with self.lock:
            d = self.data
            others = sorted({e["by"] for e in d["events"] if e["by"] not in (brain, "user")})
            came_back = d["last_seen"].get(brain, 0) > 0
            head = ["=== HANDOFF BRIEF (BlendWMe's project journal) ===",
                    f"You are {_label(brain)}. " + (f"You worked on this project before. Since then "
                                                    f"{', '.join(_label(o) for o in others) or 'nobody else'} "
                                                    "and the user worked on it." if came_back else
                                                    "You are taking over this project"
                                                    + (f" from {', '.join(_label(o) for o in others)}." if others
                                                       else "."))]
            sections = []   # (title, lines, may be shortened)
            if d["brief"]:
                sections.append(("THE BRIEF", [d["brief"][:1500]], False))
            if d["decisions"]:
                sections.append(("THE USER'S DECISIONS AND MESSAGES", ["- " + x for x in d["decisions"][-8:]], True))
            open_struggles = [s for s in d["struggles"] if s["open"]]
            if open_struggles:
                sections.append(("OPEN STRUGGLES (fix these first)",
                                 [f"- [{_label(s['by'])}] {s['text']}" + (f" (failed {s['count']}x)"
                                                                         if s.get("count", 1) > 1 else "")
                                  for s in open_struggles[-8:]], False))
            if d["handoffs"]:
                note = d["handoffs"][-1]
                lines = [f"From {_label(note['by'])}: {note['summary']}"]
                lines += ["Next: " + s for s in note["next_steps"]]
                lines += ["Struggled with: " + s for s in note["struggles"]]
                sections.append(("LATEST HANDOFF NOTE", lines, False))
            changes = [self.describe(e) for e in self.changes_since(brain) if e["kind"] != "task"]
            if changes:
                sections.append(("WHAT CHANGED SINCE YOU LAST WORKED HERE" if came_back
                                 else "WHAT HAS BEEN DONE SO FAR", ["- " + c for c in changes], True))
            if d["plan"]:
                sections.append(("THE PLAN", [f"[{'x' if p['done'] else ' '}] {i}. {p['name']}"
                                              + (f" (built by {_label(p['by'])})" if p.get("by") else "")
                                              for i, p in enumerate(d["plan"], 1)], True))
            if makers:
                by = {}
                for name, who in sorted(makers.items()):
                    by.setdefault(_label(who), []).append(name)
                sections.append(("WHO MADE EACH OBJECT", [f"- {who}: {', '.join(names[:25])}"
                                                          + (" ..." if len(names) > 25 else "")
                                                          for who, names in by.items()], True))
            review = d["review"]
            if review:
                lines = [f"By {_label(review['by'])}, {review['when']}."]
                if review.get("verdict"):
                    lines.append(review["verdict"])
                sections.append(("LATEST ART DIRECTOR REVIEW", lines, True))
            if review_first:
                rules = ["RULES: 1) Call look (mode angles) first and compare the scene with THE BRIEF. "
                         "2) Review what the others changed against the brief. 3) Fix the open struggles first. "
                         "4) Build on the other brain's and the user's work like a collaborator's edits: never "
                         "undo it unless the user asks. 5) Continue the plan. 6) Call handoff_note before you "
                         "stop with work left."]
            else:
                rules = ["RULES: 1) Continue with what the user asks next. 2) Build on the other brain's and the "
                         "user's work like a collaborator's edits: never undo it unless the user asks. 3) Call "
                         "handoff_note before you stop with work left."]
        return _fit(head, sections, rules, limit)

    # ------------------------------------------------------------------
    # read_journal
    # ------------------------------------------------------------------
    def section(self, name, brain, makers=None):
        with self.lock:
            d = self.data
            if not d["events"]:
                return None
            if name == "steps":
                return [self.describe(e) for e in d["events"] if e["kind"] == "step"][-40:]
            if name == "objects":
                return makers or {}
            if name == "struggles":
                return d["struggles"][-20:]
            if name == "handoffs":
                return d["handoffs"][-5:]
            if name == "everything":
                return {"brief": d["brief"], "decisions": d["decisions"], "plan": d["plan"],
                        "events": [self.describe(e) for e in d["events"][-80:]], "struggles": d["struggles"][-20:],
                        "review": d["review"], "handoffs": d["handoffs"][-5:], "objects": makers or {}}
        return self.handoff_brief(brain, makers)

    # ------------------------------------------------------------------
    # Saving and loading
    # ------------------------------------------------------------------
    def to_text(self):
        with self.lock:
            return json.dumps(self.data, ensure_ascii=False, indent=1, default=str)

    def load_text(self, text):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return False
        if not isinstance(data, dict) or "events" not in data:
            return False
        merged = _empty()
        merged.update(data)
        with self.lock:
            self.data = merged
            self.dirty = False
        return True

    def reset(self):
        with self.lock:
            self.data = _empty()
            self.dirty = True


def _fit(head, sections, rules, limit):
    """Join the sections, shortening the long lists (oldest lines first) until the brief fits."""
    def render():
        out = list(head)
        for title, lines, _ in sections:
            out.append("")
            out.append(title + ":")
            out += lines
        out.append("")
        out += rules
        return "\n".join(out)

    marker = "- ... (older lines left out; call read_journal for everything)"
    text = render()
    while len(text) > limit:
        # Shorten the longest list that can be shortened, dropping its oldest line.
        shrinkable = [(len("\n".join(lines)), i) for i, (_, lines, can) in enumerate(sections)
                      if can and [x for x in lines if x != marker]]
        if not shrinkable:
            break
        _, i = max(shrinkable)
        title, lines, can = sections[i]
        body = [x for x in lines if x != marker][1:]
        sections[i] = (title, [marker] + body, can)
        text = render()
    return text[:limit]


# ----------------------------------------------------------------------
# The journal of the open .blend file (Blender side)
# ----------------------------------------------------------------------
journal = Journal()


def sidecar_path(blend_path):
    folder = os.path.splitext(blend_path)[0] + SIDECAR_SUFFIX
    return os.path.join(folder, FILE_NAME)


def save():
    """Write the Text block (and the sidecar when the file is saved). Main thread."""
    import bpy
    if not journal.dirty:
        return
    text = journal.to_text()
    block = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
    block.from_string(text)
    if bpy.data.filepath:
        write_sidecar(bpy.data.filepath, text)
    journal.dirty = False


def write_sidecar(blend_path, text=None):
    path = sidecar_path(blend_path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(text or journal.to_text())
        os.replace(path + ".tmp", path)
    except OSError as e:
        print("BlendWMe: could not write the journal sidecar:", e)


def load_for_file():
    """Load the journal of the open .blend: the Text block or the sidecar, whichever is newer."""
    import bpy
    candidates = []
    block = bpy.data.texts.get(TEXT_NAME)
    if block is not None:
        candidates.append(block.as_string())
    if bpy.data.filepath and os.path.isfile(sidecar_path(bpy.data.filepath)):
        try:
            with open(sidecar_path(bpy.data.filepath), encoding="utf-8") as f:
                candidates.append(f.read())
        except OSError:
            pass
    best, best_seq = None, -1
    for text in candidates:
        probe = Journal()
        if probe.load_text(text) and probe.data["seq"] > best_seq:
            best, best_seq = text, probe.data["seq"]
    if best is None:
        journal.reset()
        journal.dirty = False
        return False
    return journal.load_text(best)


def _flush_timer():
    try:
        save()
    except Exception as e:      # never break Blender over the journal
        print("BlendWMe: journal save failed:", e)
    return 2.0


def _on_save_post(*_args):
    import bpy
    try:
        save()
        if bpy.data.filepath:
            write_sidecar(bpy.data.filepath)
    except Exception as e:
        print("BlendWMe: journal save failed:", e)


def on_file_loaded():
    """Called by lifecycle.py after a file is loaded: pick up that file's journal."""
    try:
        load_for_file()
        from .relay import relay
        relay.project_loaded()
    except Exception as e:
        print("BlendWMe: journal load failed:", e)


def _handlers():
    import bpy
    from bpy.app.handlers import persistent
    # persistent: the handler stays registered when a file is loaded.
    persistent(_on_save_post)
    return ((bpy.app.handlers.save_post, _on_save_post),)


def register():
    import bpy
    for handlers, fn in _handlers():
        if fn not in handlers:
            handlers.append(fn)
    if not bpy.app.timers.is_registered(_flush_timer):
        bpy.app.timers.register(_flush_timer, first_interval=2.0, persistent=True)


def unregister():
    import bpy
    for handlers, fn in _handlers():
        while fn in handlers:
            handlers.remove(fn)
    if bpy.app.timers.is_registered(_flush_timer):
        bpy.app.timers.unregister(_flush_timer)
