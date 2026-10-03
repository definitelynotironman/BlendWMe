# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Logs: one folder (Documents/BlendWMe Logs, or the folder you choose) holds them all.

After every task (finished, stopped or failed) BlendWMe writes two files:

    2026-10-15_1552_cosy-low-poly-cottage.md    readable: every message, all code, results, timings
    2026-10-15_1552_cosy-low-poly-cottage.json  the exact messages, for debugging

While Blender runs, a session log is written line by line (session_<time>.log), so
something is on disk even if Blender crashes. python-crash-trace.txt holds the
Python stack of a crash (faulthandler). make_crash_report() zips these together
with Blender's own crash file.

BlendWMe holds no keys or logins. As an extra safety net, a secret passed to
save() is replaced with [SECRET] before writing.
"""

import glob
import json
import os
import re
import tempfile
import threading
import time
import zipfile

__author__ = "definitelynotironman"

SESSION_LOGS_KEPT = 10


def default_folder():
    """Documents/BlendWMe Logs, or ~/BlendWMe Logs without a Documents folder."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = docs if os.path.isdir(docs) else os.path.expanduser("~")
    return os.path.join(base, "BlendWMe Logs")


class TaskLog:
    """Writes one finished task as a readable Markdown file plus its exact JSON.

    ``messages`` use the chat format (role / content / tool_calls), ``trace`` is
    a list of timed events and ``info`` holds the header facts (title, version,
    mode ...). Images are replaced by "[image]" so logs stay small and private.
    """

    HEADER_KEYS = ("saved", "version", "blender", "model", "mode", "level",
                   "status", "model_calls", "tokens", "steps")

    @staticmethod
    def slug(text):
        """A short file-name-safe version of a task title."""
        text = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "task").lower()).strip("-")
        return text[:40] or "task"

    @staticmethod
    def scrub(text, secret):
        """Replace a secret (8+ characters) with [SECRET]."""
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[SECRET]")
        return text

    @staticmethod
    def pretty(content):
        """Indented JSON when content is JSON, otherwise the text as it is."""
        try:
            return json.dumps(json.loads(content), indent=2, ensure_ascii=False)
        except (ValueError, TypeError):
            return str(content)

    @staticmethod
    def without_images(m):
        """A copy of a message whose image parts are replaced by an "[image]" text part."""
        if isinstance(m.get("content"), list):
            m = dict(m, content=[p if p.get("type") == "text" else {"type": "text", "text": "[image]"}
                                 for p in m["content"]])
        return m

    def to_markdown(self, messages, trace, info):
        """The readable log: header, every message and step, errors and a timeline."""
        out = [f"# BlendWMe log: {info.get('title', 'task')}", ""]
        for key in self.HEADER_KEYS:
            if key in info:
                out.append(f"- **{key.replace('_', ' ')}**: {info[key]}")
        out.append("")
        calls = [t for t in trace if t["type"] == "model"]
        if calls:
            total = sum(t.get("seconds", 0) for t in calls)
            out.append(f"Model time: {total:.0f}s over {len(calls)} calls "
                       f"(slowest {max(t.get('seconds', 0) for t in calls):.0f}s).")
            out.append("")
        step = 0
        for m in messages:
            lines, step = self._message_lines(m, step)
            out += lines
        errors = [t for t in trace if t["type"] in ("tool_error", "error")]
        if errors:
            out += ["## Errors", ""] + [f"- {t.get('error', '')}" for t in errors] + [""]
        out += ["## Timeline", ""]
        start = trace[0]["t"] if trace else 0
        for t in trace:
            rest = ", ".join(f"{k}={v}" for k, v in t.items() if k not in ("t", "type"))
            out.append(f"- +{t['t'] - start:6.1f}s  {t['type']}  {rest}")
        return "\n".join(out) + "\n"

    def _message_lines(self, m, step):
        """Markdown lines for one chat message, and the updated run_python step counter."""
        role = m.get("role")
        if role == "system":
            return ["<details><summary>System prompt</summary>", "", "```", m["content"], "```",
                    "</details>", ""], step
        if role == "user":
            content = m["content"]
            if isinstance(content, list):
                content = "\n".join(p.get("text", "") if p.get("type") == "text" else "[image]" for p in content)
            return ["## YOU", "", content, ""], step
        if role == "tool":
            return ["**Result:**", "", "```json", self.pretty(m.get("content", "")), "```", ""], step
        out = []
        if role == "assistant":
            if m.get("content"):
                out += ["## BLENDWME", "", m["content"], ""]
            for call in m.get("tool_calls") or []:
                fn = call["function"]
                try:
                    args = json.loads(fn["arguments"], strict=False)
                except ValueError:
                    args = {"raw": fn["arguments"]}
                if fn["name"] == "run_python":
                    step += 1
                    out += [f"### Step {step}: {args.get('title', '')}", "",
                            f"_{args.get('explain', '')}_", "", "```python", args.get("code", ""), "```", ""]
                else:
                    out += [f"### Tool: {fn['name']}", "", "```json",
                            json.dumps(args, indent=2, ensure_ascii=False), "```", ""]
        return out, step

    def save(self, messages, trace, info, folder="", secret=""):
        """Write the .md and .json files. Returns the .md path."""
        folder = folder or default_folder()
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        base = os.path.join(folder, f"{stamp}_{self.slug(info.get('title'))}")
        info = dict(info, saved=time.strftime("%Y-%m-%d %H:%M:%S"))
        messages = [self.without_images(m) for m in messages]
        md = self.scrub(self.to_markdown(messages, trace, info), secret)
        raw = self.scrub(json.dumps({"info": info, "trace": trace, "messages": messages},
                                    indent=1, ensure_ascii=False, default=str), secret)
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(md)
        with open(base + ".json", "w", encoding="utf-8") as f:
            f.write(raw)
        return base + ".md"


class SessionLog:
    """The session log: written line by line, so it survives a crash.

    Safe to call from any thread; writing never raises, because a logging
    problem must never break the user's Blender session.
    """

    def __init__(self):
        """No file is open until open() is called."""
        self.lock = threading.Lock()
        self.file = None
        self.path = ""

    def open(self, folder=""):
        """Start a new session log in the log folder, keeping the newest few."""
        self.close()
        folder = folder or default_folder()
        try:
            os.makedirs(folder, exist_ok=True)
            old = sorted(glob.glob(os.path.join(folder, "session_*.log")))
            for path in old[:max(0, len(old) - (SESSION_LOGS_KEPT - 1))]:
                os.remove(path)
            self.path = os.path.join(folder, time.strftime("session_%Y-%m-%d_%H%M%S.log"))
            self.file = open(self.path, "a", encoding="utf-8")
        except OSError as e:
            print("BlendWMe: could not open the session log:", e)
            self.file = None
        return self.path

    def write(self, kind, text):
        """One line per event; safe from any thread. Never raises."""
        if self.file is None:
            return
        line = str(text).replace("\r", "").replace("\n", "\n    ")
        with self.lock:
            try:
                self.file.write(f"{time.strftime('%H:%M:%S')}  {kind:<6} {line}\n")
                self.file.flush()
            except (OSError, ValueError):
                pass

    def close(self):
        """Close the current file, if any."""
        with self.lock:
            if self.file is not None:
                try:
                    self.file.close()
                except OSError:
                    pass
            self.file = None


class CrashReporter:
    """Collects what is needed to diagnose a crash into one zip the user can share."""

    @staticmethod
    def trace_path(folder=""):
        """Where faulthandler writes the Python stack of a crash."""
        return os.path.join(folder or default_folder(), "python-crash-trace.txt")

    @staticmethod
    def blender_crash_files(temp_dirs=None):
        """Blender's own crash reports (<file name>.crash.txt in the temporary folder), newest first."""
        found = []
        for folder in temp_dirs or [tempfile.gettempdir()]:
            if folder and os.path.isdir(folder):
                found += glob.glob(os.path.join(folder, "*.crash.txt"))
        return sorted(set(found), key=os.path.getmtime, reverse=True)

    def make(self, folder="", temp_dirs=None):
        """Zip the newest session logs, the Python crash trace and Blender's crash file.

        Returns the zip's path. The two newest session logs go in: after a crash and a
        restart, the crashed session is the second newest.
        """
        folder = folder or default_folder()
        os.makedirs(folder, exist_ok=True)
        sessions = sorted(glob.glob(os.path.join(folder, "session_*.log")), reverse=True)[:2]
        tasks = sorted(glob.glob(os.path.join(folder, "20*.md")), key=os.path.getmtime, reverse=True)[:1]
        trace = [self.trace_path(folder)] if os.path.isfile(self.trace_path(folder)) else []
        crashes = self.blender_crash_files(temp_dirs)[:2]
        path = os.path.join(folder, time.strftime("BlendWMe-crash-report_%Y-%m-%d_%H%M%S.zip"))
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sessions + tasks + trace + crashes:
                try:
                    z.write(f, os.path.basename(f))
                except OSError:
                    pass
            z.writestr("README.txt", "\n".join([
                "BlendWMe crash report",
                "",
                f"Session logs: {len(sessions)}",
                f"Latest task log: {len(tasks)}",
                f"Python crash trace: {'yes' if trace else 'none'}",
                f"Blender crash files: {len(crashes)}",
                "",
                "These files can contain your file names and folder paths. Check them before sharing.",
                ""]))
        return path


# Shared instances and the module names the relay, lifecycle, UI and tests use.
task_log = TaskLog()
session_log = SessionLog()
crash_reporter = CrashReporter()
to_markdown = task_log.to_markdown
save = task_log.save
crash_trace_path = CrashReporter.trace_path
blender_crash_files = CrashReporter.blender_crash_files
make_crash_report = crash_reporter.make
