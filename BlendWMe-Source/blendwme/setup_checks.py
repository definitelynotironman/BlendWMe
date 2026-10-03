# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Setup checks for the panel: are the brains installed and signed in, and does the MCP server start?

Each check runs the official programs' own status commands and turns the
answer into one clear sentence. They never read, show or store credentials:

    claude --version / claude --help / claude auth status --json
        (only loggedIn, authMethod and subscriptionType are used; the e-mail is never shown)
    codex --version / codex exec --help / codex exec resume --help / codex login status
        (only the exit code and whether it says ChatGPT or API key are used)
    the MCP server: started with its Python, asked for its tools (mcp_probe.py)

"Install MCP server" makes a Python environment in the workspace folder and
installs the official `mcp` package into it (this needs the internet once).
"""

import json
import os
import subprocess
import threading

from . import mcp_probe, socket_bridge, workspace
from .engines import claude_code, codex
from .engines.base import clean_env, no_window
from .state import session

__author__ = "definitelynotironman"

OK, WARN, ERROR, RUNNING = "OK", "WARN", "ERROR", "RUNNING"


class SetupChecker:
    """Runs the Brains panel checks and keeps their latest one-line results.

    ``results`` maps "claude" | "codex" | "mcp" | "install" to (level, message);
    the panel draws it. Checks run in a daemon thread so Blender never freezes
    while a program answers.
    """

    NAMES = {"claude": "Claude", "codex": "Codex", "mcp": "MCP server", "install": "Install"}

    def __init__(self):
        """Start with no results."""
        self.results = {}

    # -- running programs ----------------------------------------------------
    @staticmethod
    def run(argv, timeout=30):
        """(exit code, output) of a program, or (None, reason). Uses a key-free environment."""
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=clean_env(),
                                 creationflags=no_window(), encoding="utf-8", errors="replace")
            return out.returncode, (out.stdout or "") + (out.stderr or "")
        except subprocess.TimeoutExpired:
            return None, "it did not answer in time"
        except OSError as e:
            return None, str(e)

    @staticmethod
    def missing_flags(help_text, flags):
        """The required flags that a program's --help text does not mention."""
        return [f for f in flags if f not in help_text]

    # -- the four checks -----------------------------------------------------
    def check_claude(self, config=None):
        """Installed, starts, lists every required flag, signed in with a Claude subscription."""
        config = config or {}
        program = claude_code.find_program(config.get("claude_path", ""))
        if not program:
            return ERROR, ("Claude Code is not installed. Install it (claude.com/code), then open a terminal and run "
                           "`claude` once to sign in with your Claude account.")
        code, version = self.run([program, "--version"])
        if code != 0:
            return ERROR, f"Claude Code did not start ({version.strip()[:120]})."
        version = version.strip().split()[0] if version.strip() else "?"
        code, help_text = self.run([program, "--help"])
        missing = self.missing_flags(help_text, claude_code.REQUIRED_FLAGS)
        if missing:
            return WARN, (f"Claude Code {version} does not list {', '.join(missing)}. Update it: open a terminal "
                          "and run `claude update`.")
        code, status = self.run([program, "auth", "status", "--json"])
        try:
            info = json.loads(status[status.index("{"):])
        except ValueError:
            info = {}
        if not info.get("loggedIn"):
            return ERROR, ("Claude Code is not signed in. Open a terminal and run `claude` to sign in with your "
                           "Claude account.")
        if info.get("authMethod") != "claude.ai":
            return WARN, (f"Claude Code is signed in with {info.get('authMethod') or 'something else'}, not your "
                          "Claude subscription. Open a terminal, run `claude auth login` and pick your Claude account.")
        plan = info.get("subscriptionType")
        return OK, (f"Claude Code {version} is installed and signed in with your Claude"
                    + (f" {plan.title()}" if plan else "") + " subscription.")

    def check_codex(self, config=None):
        """Installed, starts, exec/resume flags present, signed in with ChatGPT (not an API key)."""
        config = config or {}
        program = codex.find_program(config.get("codex_path", ""))
        if not program:
            return ERROR, ("Codex is not installed. Install the Codex CLI, then open a terminal and run `codex` to "
                           "sign in with ChatGPT.")
        code, version = self.run([program, "--version"])
        if code != 0:
            return ERROR, f"Codex did not start ({version.strip()[:120]})."
        version = version.strip().split()[-1] if version.strip() else "?"
        code, help_text = self.run([program, "exec", "--help"])
        missing = self.missing_flags(help_text, codex.REQUIRED_EXEC_FLAGS)
        code_resume, _ = self.run([program, "exec", "resume", "--help"])
        if code_resume != 0:
            missing.append("exec resume")
        if missing:
            return WARN, (f"Codex {version} does not list {', '.join(missing)}. Update Codex, then check again.")
        code, status = self.run([program, "login", "status"])
        low = status.lower()
        if code != 0 or "not logged in" in low:
            return ERROR, "Codex is not signed in. Open a terminal and run `codex` to sign in with ChatGPT."
        if "api key" in low and "chatgpt" not in low:
            return WARN, ("Codex is signed in with an API key (pay per token). Open a terminal, run `codex logout`, "
                          "then `codex` and choose Sign in with ChatGPT.")
        return OK, f"Codex {version} is installed and signed in with ChatGPT."

    def check_mcp(self):
        """A Python with `mcp` exists and the real server starts and lists BlendWMe's tools."""
        python = workspace.server_python()
        if not python:
            return ERROR, "No Python found for the MCP server. Press Install MCP server."
        if not workspace.server_script():
            return ERROR, "The MCP server files are missing from the add-on. Reinstall BlendWMe."
        code, out = self.run([python, "-c", "import mcp"])
        if code != 0:
            return ERROR, "The MCP package is missing. Press Install MCP server (it needs the internet once)."
        if not socket_bridge.get().is_running():
            socket_bridge.start()
        ok, message, names = mcp_probe.check_server(workspace.server_command("claude"), cwd=workspace.folder(),
                                                    env=clean_env())
        return (OK if ok else ERROR), message

    def install_mcp(self):
        """Make <workspace>/venv and install the official mcp package into it."""
        base = workspace.system_python() or workspace.blender_python()
        if not base:
            return ERROR, "No Python found to make the environment. Install Python 3.10 or newer, then try again."
        venv_dir = workspace.path("venv")
        if not os.path.isfile(workspace.venv_python(venv_dir)):
            code, out = self.run([base, "-m", "venv", venv_dir], timeout=300)
            if code != 0:
                return ERROR, f"Could not make the Python environment: {out.strip()[-200:]}"
        requirements = os.path.join(workspace.server_dir(), "requirements.txt")
        code, out = self.run([workspace.venv_python(venv_dir), "-m", "pip", "install", "--disable-pip-version-check",
                              "-r", requirements], timeout=900)
        if code != 0:
            return ERROR, f"Installing the MCP package failed: {out.strip()[-240:]}"
        return OK, "The tools server is installed in the workspace folder."

    # -- running checks for the panel ---------------------------------------
    def check(self, name, config=None):
        """Run one named check and return (level, message)."""
        if name == "claude":
            return check_claude(config)
        if name == "codex":
            return check_codex(config)
        return {"mcp": check_mcp, "install": install_mcp}[name]()

    def run_in_background(self, names, config=None):
        """Run checks one after another in a thread; results appear in the panel."""
        for name in names:
            self.results[name] = (RUNNING, "Checking ..." if name != "install" else "Installing ...")
        session.changed_flag = True

        def work():
            """Thread body: each check's failure becomes an ERROR line, never an exception."""
            for name in names:
                try:
                    level, message = self.check(name, config)
                except Exception as e:      # a check must never break Blender
                    level, message = ERROR, f"The check failed: {e}"
                self.results[name] = (level, message)
                session.add("info" if level == OK else "error", f"{self.NAMES[name]}: {message}")
        threading.Thread(target=work, name="BlendWMe setup check", daemon=True).start()


# The panel and tests use these module names. The checks are looked up at call
# time (``check``), so a test can still replace one module function.
checker = SetupChecker()
results = checker.results
NAMES = SetupChecker.NAMES
_run = SetupChecker.run
check_claude = checker.check_claude
check_codex = checker.check_codex
check_mcp = checker.check_mcp
install_mcp = checker.install_mcp
run_in_background = checker.run_in_background
CHECKS = {"claude": check_claude, "codex": check_codex, "mcp": check_mcp, "install": install_mcp}
