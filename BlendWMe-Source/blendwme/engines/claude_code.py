"""Claude Code as BlendWMe's main brain (signed in with the user's Claude subscription).

One turn = one run of the official `claude` program in print mode:

    claude -p "<message>"
        --output-format stream-json --verbose --include-partial-messages   live events, streamed text
        --mcp-config <workspace>/mcp-claude.json --strict-mcp-config       only BlendWMe's MCP server
        --tools ""                                                         no built-in tools (no Bash, no file edits)
        --allowedTools mcp__blendwme --permission-mode dontAsk             BlendWMe's tools only, no prompts
        --append-system-prompt "<short role line>"
        --session-id <uuid>   (first turn)   or   --resume <uuid>   (later turns)
        [--model <name>] [--effort <level>]

Every flag above was checked against `claude --help` (Claude Code 2.1). Never
`--bare`: it ignores the subscription sign-in. The program runs in the BlendWMe
workspace folder, where it also reads BlendWMe's instructions (CLAUDE.md).
Sign-in stays inside `claude`; BlendWMe never reads or passes credentials, and
ANTHROPIC_API_KEY is removed from its environment so it cannot fall back to
pay-per-token billing.
"""

import os
import shutil
import uuid

from . import limits
from .base import ProcessEngine

TOOL_PREFIX = "mcp__blendwme__"
ROLE = ("You are BlendWMe's main brain, working inside the user's live Blender scene through the blendwme "
        "MCP tools only. Follow CLAUDE.md in this folder.")

# Flags this adapter uses; the setup check confirms each one in `claude --help`.
REQUIRED_FLAGS = ["--print", "--output-format", "--verbose", "--include-partial-messages", "--mcp-config",
                  "--strict-mcp-config", "--tools", "--allowedTools", "--permission-mode",
                  "--append-system-prompt", "--session-id", "--resume", "--model", "--effort"]


def find_program(configured=""):
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe"),
                      os.path.join(os.path.expanduser("~"), ".local", "bin", "claude")):
        if os.path.isfile(candidate):
            return candidate
    return ""


class ClaudeCodeEngine(ProcessEngine):
    name = "claude"
    label = "Claude"

    def __init__(self):
        super().__init__()
        self.started_session = False      # True once a turn ran with --session-id
        self._streamed = set()            # message ids whose text already arrived as deltas
        self._current_message = None

    def reset(self):
        super().reset()
        self.started_session = False

    def build(self, message, config):
        program = find_program(config.get("claude_path", "")) or ("claude" if self.command_override else "")
        if not program:
            raise FileNotFoundError("Claude Code is not installed. Install it from claude.com/code, "
                                    "then open a terminal and run `claude` once to sign in.")
        argv = [program, "-p", message,
                "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--mcp-config", config["mcp_config"], "--strict-mcp-config",
                "--tools", "",
                "--allowedTools", "mcp__blendwme",
                "--permission-mode", "dontAsk",
                "--append-system-prompt", ROLE]
        if self.session_id and self.started_session:
            argv += ["--resume", self.session_id]
        else:
            self.session_id = self.session_id or str(uuid.uuid4())
            argv += ["--session-id", self.session_id]
            self.started_session = True
        if config.get("claude_model"):
            argv += ["--model", config["claude_model"]]
        if config.get("claude_effort"):
            argv += ["--effort", config["claude_effort"]]
        # Tool calls may wait for the user (Teach mode); BlendWMe caps a wait at 45 minutes.
        return argv, None, {"MCP_TOOL_TIMEOUT": "3600000", "MCP_TIMEOUT": "60000"}

    # -- events -----------------------------------------------------------
    def parse(self, obj):
        kind = obj.get("type")
        if obj.get("session_id"):
            self.session_id = obj["session_id"]
        if kind == "system" and obj.get("subtype") == "init":
            for server in obj.get("mcp_servers") or []:
                if server.get("name") == "blendwme" and server.get("status") in ("failed", "needs-auth"):
                    return [{"type": "error", "text": "BlendWMe's MCP server did not start for Claude "
                                                      f"({server.get('status')}). Run 'Check MCP server' in the panel."}]
            return []
        if kind == "stream_event":
            event = obj.get("event") or {}
            if event.get("type") == "message_start":
                self._current_message = (event.get("message") or {}).get("id")
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self._streamed.add(self._current_message)
                return [{"type": "text", "text": delta.get("text", ""), "delta": True}]
            return []
        if kind == "assistant":
            message = obj.get("message") or {}
            out = []
            for block in message.get("content") or []:
                if block.get("type") == "text" and message.get("id") not in self._streamed:
                    out.append({"type": "text", "text": block.get("text", ""), "delta": False})
                elif block.get("type") == "tool_use":
                    out.append({"type": "tool_call", "id": block.get("id"),
                                "tool": short_tool_name(block.get("name", "")), "args": block.get("input") or {}})
            return out
        if kind == "user":
            out = []
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append({"type": "tool_result", "id": block.get("tool_use_id"),
                                "ok": not block.get("is_error"), "text": _result_text(block.get("content"))})
            return out
        if kind == "rate_limit_event":
            info = obj.get("rate_limit_info") or {}
            if info.get("status") == "rejected":
                return [{"type": "limit_reached", "text": "Claude's usage limit is reached.",
                         "resets": _reset_from_epoch(info.get("resetsAt"))}]
            return []
        if kind == "result":
            text = obj.get("result") or "; ".join(str(e) for e in obj.get("errors") or [])
            if limits.is_limit(text, max_length=400):
                return [{"type": "limit_reached", "text": text, "resets": limits.reset_hint(text)}]
            if obj.get("is_error") or obj.get("subtype") not in (None, "success"):
                return [{"type": "error", "text": f"Claude stopped: {text or obj.get('subtype')}"}]
            return [{"type": "done", "text": text}]
        return []

    def parse_text_line(self, line):
        if limits.is_limit(line, max_length=400):
            return [{"type": "limit_reached", "text": line, "resets": limits.reset_hint(line)}]
        return []


def short_tool_name(name):
    return name[len(TOOL_PREFIX):] if name.startswith(TOOL_PREFIX) else name


def _result_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif isinstance(item, dict) and item.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts)


def _reset_from_epoch(value):
    import time
    try:
        return "resets " + time.strftime("%H:%M", time.localtime(int(value)))
    except (TypeError, ValueError):
        return ""
