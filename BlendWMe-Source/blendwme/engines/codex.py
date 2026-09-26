"""Codex CLI as BlendWMe's second brain (signed in with the user's ChatGPT account).

One turn = one run of the official `codex` program in non-interactive mode:

    codex exec --json --skip-git-repo-check --cd <workspace> --sandbox read-only
        -c mcp_servers.blendwme.command='<python>'
        -c mcp_servers.blendwme.args=['<run.py>', '--brain', 'codex', '--workspace', '<folder>']
        -c mcp_servers.blendwme.startup_timeout_sec=60
        -c mcp_servers.blendwme.tool_timeout_sec=3600
        -c mcp_servers.blendwme.default_tools_approval_mode='approve'
        [-m <model>]
        -                                  (first turn: the message comes on stdin)
        resume <thread id> -               (later turns: continue Codex's own conversation)

Codex is not installed on the machine this was written on, so these flags come
from the official Codex documentation (non-interactive mode, CLI reference and
MCP pages), not from `codex --help`. The setup check runs `codex exec --help`
and `codex exec resume --help` and says which flag is missing, if any.

- The MCP server is added with -c overrides for this run only: the user's own
  ~/.codex/config.toml is never edited.
- The instructions are AGENTS.md in the workspace folder (Codex reads it there).
- read-only sandbox: Codex's own shell commands cannot change files. BlendWMe's
  instructions tell it to use only BlendWMe's tools.
- Sign-in stays inside `codex`. OPENAI_API_KEY and CODEX_API_KEY are removed
  from its environment, so it uses the ChatGPT sign-in, never pay-per-token.
"""

import os
import shutil
import subprocess

from .. import workspace
from . import limits
from .base import ProcessEngine, clean_env, no_window

REQUIRED_EXEC_FLAGS = ["--json", "--skip-git-repo-check", "--cd", "--sandbox", "--config", "--model"]
MAX_ARGUMENT = 7000       # codex is a .cmd script on Windows: cmd.exe limits a command line to 8191 characters


def find_program(configured=""):
    if configured and os.path.isfile(configured):
        return configured
    return shutil.which("codex") or ""


def toml_string(text):
    """A TOML literal string ('...'): backslashes in Windows paths need no escaping."""
    text = str(text)
    if "'" in text or "\n" in text:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "'" + text + "'"


def toml_list(items):
    return "[" + ", ".join(toml_string(i) for i in items) + "]"


class CodexEngine(ProcessEngine):
    name = "codex"
    label = "Codex"

    def __init__(self):
        super().__init__()
        self.resume_reads_stdin = None    # learned from `codex exec resume --help`
        self.last_error = ""

    def _resume_reads_stdin(self, program):
        if self.resume_reads_stdin is None:
            try:
                out = subprocess.run([program, "exec", "resume", "--help"], capture_output=True, text=True,
                                     timeout=20, env=clean_env(), creationflags=no_window())
                self.resume_reads_stdin = "stdin" in (out.stdout + out.stderr).lower()
            except (OSError, subprocess.SubprocessError):
                self.resume_reads_stdin = False
        return self.resume_reads_stdin

    def build(self, message, config):
        program = find_program(config.get("codex_path", "")) or ("codex" if self.command_override else "")
        if not program:
            raise FileNotFoundError("Codex is not installed. Install the Codex CLI, then open a terminal and run "
                                    "`codex` once to sign in with ChatGPT.")
        self.last_error = ""
        server = workspace.server_command("codex")
        argv = [program, "exec", "--json", "--skip-git-repo-check", "--cd", config["workspace"],
                "--sandbox", "read-only"]
        for override in (f"mcp_servers.blendwme.command={toml_string(server[0])}",
                         f"mcp_servers.blendwme.args={toml_list(server[1:])}",
                         "mcp_servers.blendwme.startup_timeout_sec=60",
                         "mcp_servers.blendwme.tool_timeout_sec=3600",
                         "mcp_servers.blendwme.default_tools_approval_mode='approve'"):
            argv += ["-c", override]
        if config.get("codex_model"):
            argv += ["-m", config["codex_model"]]
        stdin_text = message
        if self.session_id:
            if self.command_override or self._resume_reads_stdin(program):
                argv += ["resume", self.session_id, "-"]
            elif len(message) <= MAX_ARGUMENT:
                argv += ["resume", self.session_id, message]
                stdin_text = None
            else:
                # Too long for a command line and no stdin for resume: a fresh Codex conversation.
                # Nothing is lost: the message carries the handoff brief from the journal.
                self.session_id = None
                argv.append("-")
        else:
            argv.append("-")
        return argv, stdin_text, {}

    # -- events -----------------------------------------------------------
    def parse(self, obj):
        kind = obj.get("type")
        if kind == "thread.started":
            self.session_id = obj.get("thread_id") or self.session_id
            return []
        item = obj.get("item") or {}
        itype = item.get("type")
        if kind == "item.started":
            if itype == "mcp_tool_call":
                return [{"type": "tool_call", "id": item.get("id"), "tool": item.get("tool", ""),
                         "args": item.get("arguments") or {}}]
            if itype == "command_execution":
                return [{"type": "tool_call", "id": item.get("id"), "tool": "shell",
                         "args": {"command": item.get("command", "")}}]
            return []
        if kind == "item.completed":
            if itype == "agent_message":
                return [{"type": "text", "text": item.get("text", ""), "delta": False}]
            if itype in ("mcp_tool_call", "command_execution"):
                failed = item.get("status") == "failed" or bool(item.get("error"))
                return [{"type": "tool_result", "id": item.get("id"), "ok": not failed,
                         "text": _result_text(item)}]
            if itype == "error":
                return self._problem(item.get("message", ""), final=False)
            return []
        if kind == "turn.completed":
            return [{"type": "done", "text": ""}]
        if kind == "turn.failed":
            return self._problem((obj.get("error") or {}).get("message", "") or self.last_error, final=True)
        if kind == "error":
            return self._problem(obj.get("message", ""), final=False)
        return []

    def _problem(self, text, final):
        """A limit always ends the turn; other errors end it only when the turn failed."""
        text = str(text or "")
        if limits.is_limit(text, max_length=600):
            return [{"type": "limit_reached", "text": text, "resets": limits.reset_hint(text)}]
        if final:
            return [{"type": "error", "text": f"Codex stopped: {text or 'the turn failed'}"}]
        self.last_error = text    # Codex may still recover (it retries); remember it for the end
        return []

    def parse_text_line(self, line):
        if limits.is_limit(line, max_length=600):
            return [{"type": "limit_reached", "text": line, "resets": limits.reset_hint(line)}]
        return []


def _result_text(item):
    result = item.get("result")
    if isinstance(result, dict):
        parts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)]
        return "\n".join(parts)
    if item.get("aggregated_output"):
        return str(item["aggregated_output"])
    return str(item.get("error") or result or "")
