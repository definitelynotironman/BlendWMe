# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
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

import hashlib
import json
import os
import threading
import time
import uuid

__author__ = "definitelynotironman"

TEXT_NAME = "BlendWMe Journal"
SIDECAR_SUFFIX = "_blendwme"
FILE_NAME = "journal.json"
MAX_EVENTS = 600
MAX_CODE = 1500
BRIEF_LIMIT = 6000
LABELS = {"claude": "Claude", "codex": "Codex", "user": "the user", "ai": "BlendWMe"}


def _label(who):
    """A readable name for a brain or author ('claude' -> 'Claude')."""
    return LABELS.get(who, str(who))


def _empty():
    """A fresh journal: no brief, no events, Claude active."""
    return {"version": 1, "seq": 0, "brief": "", "decisions": [], "plan": [], "events": [], "struggles": [],
            "review": None, "handoffs": [], "sessions": {}, "last_seen": {}, "active": "claude", "tasks": [],
            "references": [], "task_start": 0}


class Journal:
    """The project's memory, held by BlendWMe rather than by any brain.

    Every change bumps `seq` (shown to brains as event numbers) and `revision`
    (an in-memory identity used to decide when a save is still current). All
    methods are safe to call from any thread.
    """
    def __init__(self):
        """An empty journal that is not yet dirty."""
        self.lock = threading.RLock()
        self.data = _empty()
        self.dirty = False
        self.revision = 0       # monotonic in-memory identity, including reset/load

    # ------------------------------------------------------------------
    # Recording (any thread)
    # ------------------------------------------------------------------
    def _add(self, kind, by, **fields):
        """Append one event (numbered, timestamped, signed by `by`) and mark the journal dirty."""
        with self.lock:
            self.data["seq"] += 1
            self.revision += 1
            event = {"n": self.data["seq"], "t": round(time.time()), "kind": kind, "by": by}
            event.update({k: v for k, v in fields.items() if v not in (None, "", [])})
            self.data["events"].append(event)
            self.dirty = True
            return event

    def record_task(self, brief):
        """Start a new task: archive the old brief and clear the active plan, sessions and references.

        Archived events stay; open struggles are closed because they belonged to the
        previous brief.
        """
        with self.lock:
            if self.data["brief"]:
                self.data["tasks"].append({"brief": self.data["brief"][:300], "ended": round(time.time())})
                del self.data["tasks"][:-10]
            self.data["brief"] = brief
            self.data["decisions"] = []
            self.data["plan"] = []
            self.data["review"] = None
            self.data["references"] = []
            self.data["handoffs"] = []
            self.data["sessions"] = {}
            self.data["last_seen"] = {}
            self.data["task_start"] = self.data["seq"] + 1
            for s in self.data["struggles"]:
                s["open"] = False
            self._add("task", "user", text=brief)

    def record_user_message(self, text, decision=False):
        """Record a message from the user as a decision the brains must respect."""
        with self.lock:
            self.data["decisions"].append(text)
            self._add("user_message", "user", text=text)

    def record_references(self, paths, blend_path=""):
        """Record reference images with portable relative paths, hashes and missing flags."""
        references = []
        for path in paths:
            entry = {"path": path, "name": os.path.basename(path), "missing": not os.path.isfile(path)}
            if blend_path:
                try:
                    entry["relative_path"] = os.path.relpath(path, os.path.dirname(blend_path))
                except ValueError:    # a reference on a different drive stays absolute
                    pass
            if not entry["missing"]:
                try:
                    with open(path, "rb") as stream:
                        entry["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                except OSError:
                    entry["missing"] = True
            references.append(entry)
        with self.lock:
            self.data["references"] = references
            self._add("references", "user", references=references)

    def resolve_references(self, blend_path=""):
        """Resolve portable copies without overwriting the original reference record."""
        with self.lock:
            entries = [dict(entry) for entry in self.data.get("references", [])]
        for entry in entries:
            relative = entry.get("relative_path")
            candidate = os.path.normpath(os.path.join(os.path.dirname(blend_path), relative)) if blend_path and relative else ""
            if candidate and os.path.isfile(candidate):
                entry["path"] = candidate
            path = entry["path"]
            entry["missing"] = not os.path.isfile(path)
            if not entry["missing"] and entry.get("sha256"):
                try:
                    with open(path, "rb") as stream:
                        entry["changed"] = hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]
                except OSError:
                    entry["missing"] = True
        return entries

    def record_user_edits(self, lines):
        """Record the user's own scene changes (at most 20 lines)."""
        self._add("user_edit", "user", lines=[str(x)[:200] for x in lines[:20]])

    def record_plan(self, brain, parts):
        """Set the plan; parts that keep their name keep their state and builder."""
        with self.lock:
            old = {p["name"]: p for p in self.data["plan"]}
            self.data["plan"] = [{"name": p, "done": old.get(p, {}).get("done", False),
                                  "by": old.get(p, {}).get("by", ""), "skipped": old.get(p, {}).get("skipped", ""),
                                  } for p in parts]
            self._add("plan", brain, parts=list(parts))

    def record_plan_tick(self, part, brain="", skipped=""):
        """Part `part` (1 = first) is built, or skipped when `skipped` holds the reason."""
        with self.lock:
            if isinstance(part, int) and 1 <= part <= len(self.data["plan"]):
                self.data["plan"][part - 1]["done"] = True
                self.data["plan"][part - 1]["skipped"] = skipped
                if brain:
                    self.data["plan"][part - 1]["by"] = brain
                self.dirty = True
                self._add("plan_tick", brain, part=part)
        if skipped:
            self._add("plan_skip", brain, part=part, text=skipped)

    def record_step(self, brain, title, explain, code, ok, error="", added=(), modified=(), part=None):
        """Record one run_python step.

        A successful step never ticks its plan part here: only the tool host
        knows whether the step changed the scene, and a no-op script must not
        mark a missing part as built. The host calls record_plan_tick itself.
        """
        self._add("step", brain, title=title, explain=explain, code=code,
                  ok=bool(ok), error=(error or "")[:400], added=list(added)[:20], modified=list(modified)[:20],
                  part=part)

    def record_struggle(self, brain, key, text):
        """Record a failed attempt; repeats of the same key count up instead of duplicating."""
        with self.lock:
            for s in self.data["struggles"]:
                if s["open"] and s["key"] == key:
                    s.update(text=text, by=brain, count=s.get("count", 1) + 1, t=round(time.time()))
                    self._add("struggle", brain, key=key, text=text)
                    return s
            s = {"key": key, "text": text, "by": brain, "count": 1, "open": True, "t": round(time.time())}
            self.data["struggles"].append(s)
            del self.data["struggles"][:-30]
            self._add("struggle", brain, key=key, text=text)
            return s

    def solve_struggle(self, brain, key):
        """Close the open struggle with this key and note which brain solved it."""
        with self.lock:
            for s in self.data["struggles"]:
                if s["open"] and s["key"] == key:
                    s["open"] = False
                    s["solved_by"] = brain
                    self._add("solved", brain, text=s["text"][:200])

    def record_review(self, brain, when, objects=()):
        """Start an art director review (its verdict follows from the brain's next words)."""
        with self.lock:
            self.data["review"] = {"by": brain, "when": when, "n": self.data["seq"] + 1,
                                   "objects": list(objects)[:30], "verdict": ""}
            self._add("review", brain, text=when)

    def set_review_verdict(self, brain, text):
        """Store the verdict of the latest review."""
        with self.lock:
            if self.data["review"] is not None:
                self.data["review"]["verdict"] = text[:800]
                self._add("review_verdict", brain, text=text)

    def record_said(self, brain, text):
        """A short note of what a brain told the user (context for the next brain)."""
        if text.strip():
            self._add("said", brain, text=text.strip())

    def record_handoff(self, brain, summary, next_steps=(), struggles=()):
        """Record a brain's handoff note; its struggles become open struggles."""
        with self.lock:
            note = {"by": brain, "t": round(time.time()), "summary": summary,
                    "next_steps": list(next_steps), "struggles": list(struggles)}
            self.data["handoffs"].append(note)
            del self.data["handoffs"][:-10]
            for text in note["struggles"]:
                self.record_struggle(brain, "note: " + text[:60], text)
            self._add("handoff_note", brain, text=summary, next_steps=list(next_steps), struggles=list(struggles))

    def record_finish(self, brain, summary):
        """Record that a brain finished the task, with its summary."""
        self._add("finish", brain, text=summary)

    def record_switch(self, source, target, reason):
        """Record a switch between brains and make the target active."""
        with self.lock:
            self.data["active"] = target
            self._add("switch", source, to=target, text=reason)

    def record_error(self, brain, text):
        """Record an error a brain hit."""
        self._add("error", brain, text=text[:300])

    def set_session(self, brain, session_id):
        """Remember a brain's own session id so its conversation can resume."""
        with self.lock:
            if session_id and self.data["sessions"].get(brain) != session_id:
                self.data["sessions"][brain] = session_id
                self._add("session", brain)

    def mark_seen(self, brain):
        """The brain has now seen everything up to here ("since you last worked here" starts after it)."""
        with self.lock:
            self.data["last_seen"][brain] = self.data["seq"]
            self._add("seen", brain)

    def has_history(self):
        """True when anything has been recorded."""
        return bool(self.data["events"])

    # ------------------------------------------------------------------
    # The handoff brief
    # ------------------------------------------------------------------
    def changes_since(self, brain):
        """Events by others since this brain last worked here, oldest first."""
        with self.lock:
            seen = max(self.data["last_seen"].get(brain, 0), self.data.get("task_start", 0) - 1)
            return [e for e in self.data["events"] if e["n"] > seen and e["by"] != brain]

    @staticmethod
    def describe(event):
        """One readable line for an event (used by the brief and read_journal)."""
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
            others = sorted({e["by"] for e in d["events"] if e["by"] not in (brain, "user") and
                             e["n"] >= d.get("task_start", 0)})
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
            if d.get("references"):
                sections.append(("REFERENCES", ["- " + entry["name"] +
                                 (" (recorded missing; check availability)" if entry.get("missing") else "")
                                 for entry in d["references"]], True))
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
        """One part of the journal for the read_journal tool (brief, steps, struggles ...)."""
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
                        "events": list(d["events"]), "struggles": d["struggles"][-20:],
                        "review": d["review"], "handoffs": d["handoffs"][-5:],
                        "references": d.get("references", []), "objects": makers or {}}
        return self.handoff_brief(brain, makers)

    # ------------------------------------------------------------------
    # Saving and loading
    # ------------------------------------------------------------------
    def to_text(self):
        """The whole journal as JSON text (for the Text block and the sidecar)."""
        with self.lock:
            return json.dumps(self.data, ensure_ascii=False, indent=1, default=str)

    def load_text(self, text):
        """Replace the journal with JSON text. False, and nothing changed, when it is invalid."""
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return False
        if (not isinstance(data, dict) or not isinstance(data.get("events"), list)
                or not isinstance(data.get("seq"), int) or data["seq"] < 0
                or data.get("version") != 1):
            return False
        merged = _empty()
        merged.update(data)
        if not _valid_schema(merged):
            return False
        with self.lock:
            self.data = merged
            self.dirty = False
            self.revision += 1
        return True

    def reset(self):
        """Forget everything (a fresh project)."""
        with self.lock:
            self.data = _empty()
            self.dirty = True
            self.revision += 1


def _valid_schema(data):
    """Validate the nested structures consumers rely on before replacing state."""
    if (isinstance(data["seq"], bool) or not isinstance(data["brief"], str) or
            not isinstance(data["active"], str)):
        return False
    for name in ("decisions", "plan", "struggles", "handoffs", "tasks", "references"):
        if not isinstance(data.get(name, []), list):
            return False
    if not all(isinstance(item, str) for item in data["decisions"]):
        return False
    for event in data["events"]:
        if (not isinstance(event, dict) or not isinstance(event.get("n"), int) or
                not 0 <= event["n"] <= data["seq"] or
                not all(isinstance(event.get(key), str) for key in ("by", "kind"))):
            return False
    for part in data["plan"]:
        if not isinstance(part, dict) or not isinstance(part.get("name"), str) or not isinstance(part.get("done"), bool):
            return False
    for item in data["struggles"]:
        if (not isinstance(item, dict) or not isinstance(item.get("open"), bool) or
                not all(isinstance(item.get(key), str) for key in ("key", "text", "by"))):
            return False
    for item in data["handoffs"]:
        if (not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("by", "summary")) or
                not all(isinstance(item.get(key), list) and all(isinstance(v, str) for v in item[key])
                        for key in ("next_steps", "struggles"))):
            return False
    review = data["review"]
    if review is not None and (not isinstance(review, dict) or
                               not all(isinstance(review.get(key), str) for key in ("by", "when", "verdict"))):
        return False
    for name in ("sessions", "last_seen"):
        if not isinstance(data[name], dict):
            return False
        expected = str if name == "sessions" else int
        if not all(isinstance(k, str) and isinstance(v, expected) for k, v in data[name].items()):
            return False
    for entry in data.get("references", []):
        if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("path", "name")):
            return False
        if any(key in entry and not isinstance(entry[key], str) for key in ("relative_path", "sha256")):
            return False
    return True


def _fit(head, sections, rules, limit):
    """Join the sections, shortening the long lists (oldest lines first) until the brief fits."""
    def render():
        """The brief text from the current (possibly shortened) sections."""
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


class JournalStore:
    """Saves and loads the open file's journal: a Text block in the .blend plus a sidecar file.

    The Text block travels inside the .blend; the sidecar
    (<name>_blendwme/journal.json) survives even when Blender closes before a
    save. On load the saved .blend is authoritative, so a newer sidecar that
    describes work the user deliberately discarded never resurrects it.
    Writes are atomic (unique temporary file, then os.replace), and the dirty
    flag is cleared only when the journal's in-memory revision did not change
    while it was being written.
    """

    def __init__(self, journal_):
        """Store the journal of the open file."""
        self.journal = journal_

    @staticmethod
    def sidecar_path(blend_path):
        """<folder>/<name>_blendwme/journal.json for a .blend path."""
        folder = os.path.splitext(blend_path)[0] + SIDECAR_SUFFIX
        return os.path.join(folder, FILE_NAME)

    def save(self):
        """Write the Text block (and the sidecar when the file is saved). Main thread."""
        import bpy
        with self.journal.lock:
            if not self.journal.dirty:
                return
            revision = self.journal.revision
            text = self.journal.to_text()
        block = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
        block.from_string(text)
        if bpy.data.filepath:
            # Through the module name, so tests can substitute the writer.
            if not write_sidecar(bpy.data.filepath, text):
                return
        with self.journal.lock:
            if self.journal.revision == revision:
                self.journal.dirty = False

    def write_sidecar(self, blend_path, text=None):
        """Atomically write the sidecar journal next to blend_path. False when it could not be written."""
        path = self.sidecar_path(blend_path)
        temporary = path + "." + uuid.uuid4().hex + ".tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as f:
                f.write(text or self.journal.to_text())
            os.replace(temporary, path)
            return True
        except OSError as e:
            print("BlendWMe: could not write the journal sidecar:", e)
            return False
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def load_for_file(self):
        """Load the journal of the open .blend: the Text block, else the sidecar."""
        import bpy
        block = bpy.data.texts.get(TEXT_NAME)
        if block is not None:
            # The saved blend is authoritative. A newer sidecar can describe work
            # the user deliberately discarded and must not resurrect that task.
            if self.journal.load_text(block.as_string()):
                return True
            self.journal.reset()
            self.journal.dirty = False
            print("BlendWMe: the saved journal is invalid; previous project context was cleared.")
            return False
        candidates = []
        if bpy.data.filepath and os.path.isfile(self.sidecar_path(bpy.data.filepath)):
            try:
                with open(self.sidecar_path(bpy.data.filepath), encoding="utf-8") as f:
                    candidates.append(f.read())
            except OSError:
                pass
        best, best_seq = None, -1
        for text in candidates:
            probe = Journal()
            if probe.load_text(text) and probe.data["seq"] > best_seq:
                best, best_seq = text, probe.data["seq"]
        if best is None:
            self.journal.reset()
            self.journal.dirty = False
            return False
        return self.journal.load_text(best)

    def on_file_loaded(self):
        """Called by lifecycle.py after a file is loaded: pick up that file's journal."""
        try:
            self.load_for_file()
            from .relay import relay
            relay.project_loaded()
        except Exception as e:
            print("BlendWMe: journal load failed:", e)


store = JournalStore(journal)
sidecar_path = JournalStore.sidecar_path
save = store.save
write_sidecar = store.write_sidecar
load_for_file = store.load_for_file
on_file_loaded = store.on_file_loaded


# Blender keeps handlers and timers by identity, so these stay plain module
# functions that Blender can register once and remove again.
def _flush_timer():
    """Every 2 seconds: save the journal if it changed. Never raises into Blender."""
    try:
        save()
    except Exception as e:      # never break Blender over the journal
        print("BlendWMe: journal save failed:", e)
    return 2.0


def _on_save_pre(*_args):
    """Before the .blend is written: put the latest journal into its Text block."""
    save()


def _on_save_post(*_args):
    """After the .blend is written: refresh the sidecar next to it."""
    import bpy
    try:
        save()
        if bpy.data.filepath:
            write_sidecar(bpy.data.filepath)
    except Exception as e:
        print("BlendWMe: journal save failed:", e)


def _handlers():
    """((handler list, function), ...) for save_pre and save_post, marked persistent."""
    import bpy
    from bpy.app.handlers import persistent
    # persistent: the handler stays registered when a file is loaded.
    persistent(_on_save_post)
    persistent(_on_save_pre)
    return ((bpy.app.handlers.save_pre, _on_save_pre), (bpy.app.handlers.save_post, _on_save_post))


def register():
    """Add the save handlers and the flush timer (once)."""
    import bpy
    for handlers, fn in _handlers():
        if fn not in handlers:
            handlers.append(fn)
    if not bpy.app.timers.is_registered(_flush_timer):
        bpy.app.timers.register(_flush_timer, first_interval=2.0, persistent=True)


def unregister():
    """Remove the save handlers and the flush timer."""
    import bpy
    for handlers, fn in _handlers():
        while fn in handlers:
            handlers.remove(fn)
    if bpy.app.timers.is_registered(_flush_timer):
        bpy.app.timers.unregister(_flush_timer)
