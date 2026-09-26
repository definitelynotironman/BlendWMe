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

OK, WARN, ERROR, RUNNING = "OK", "WARN", "ERROR", "RUNNING"

# {"claude" | "codex" | "mcp" | "install": (level, message)}, shown in the panel
results = {}


def _run(argv, timeout=30):
    """(exit code, output) of a program, or (None, reason)."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=clean_env(),
                             creationflags=no_window(), encoding="utf-8", errors="replace")
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "it did not answer in time"
    except OSError as e:
        return None, str(e)


def _missing_flags(help_text, flags):
    return [f for f in flags if f not in help_text]


# ----------------------------------------------------------------------
def check_claude(config=None):
    config = config or {}
    program = claude_code.find_program(config.get("claude_path", ""))
    if not program:
        return ERROR, ("Claude Code is not installed. Install it (claude.com/code), then open a terminal and run "
                       "`claude` once to sign in with your Claude account.")
    code, version = _run([program, "--version"])
    if code != 0:
        return ERROR, f"Claude Code did not start ({version.strip()[:120]})."
    version = version.strip().split()[0] if version.strip() else "?"
    code, help_text = _run([program, "--help"])
    missing = _missing_flags(help_text, claude_code.REQUIRED_FLAGS)
    if missing:
        return WARN, (f"Claude Code {version} does not list {', '.join(missing)}. Update it: open a terminal "
                      "and run `claude update`.")
    code, status = _run([program, "auth", "status", "--json"])
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


def check_codex(config=None):
    config = config or {}
    program = codex.find_program(config.get("codex_path", ""))
    if not program:
        return ERROR, ("Codex is not installed. Install the Codex CLI, then open a terminal and run `codex` to "
                       "sign in with ChatGPT.")
    code, version = _run([program, "--version"])
    if code != 0:
        return ERROR, f"Codex did not start ({version.strip()[:120]})."
    version = version.strip().split()[-1] if version.strip() else "?"
    code, help_text = _run([program, "exec", "--help"])
    missing = _missing_flags(help_text, codex.REQUIRED_EXEC_FLAGS)
    code_resume, _ = _run([program, "exec", "resume", "--help"])
    if code_resume != 0:
        missing.append("exec resume")
    if missing:
        return WARN, (f"Codex {version} does not list {', '.join(missing)}. Update Codex, then check again.")
    code, status = _run([program, "login", "status"])
    low = status.lower()
    if code != 0 or "not logged in" in low:
        return ERROR, "Codex is not signed in. Open a terminal and run `codex` to sign in with ChatGPT."
    if "api key" in low and "chatgpt" not in low:
        return WARN, ("Codex is signed in with an API key (pay per token). Open a terminal, run `codex logout`, "
                      "then `codex` and choose Sign in with ChatGPT.")
    return OK, f"Codex {version} is installed and signed in with ChatGPT."


def check_mcp():
    python = workspace.server_python()
    if not python:
        return ERROR, "No Python found for the MCP server. Press Install MCP server."
    if not workspace.server_script():
        return ERROR, "The MCP server files are missing from the add-on. Reinstall BlendWMe."
    code, out = _run([python, "-c", "import mcp"])
    if code != 0:
        return ERROR, "The MCP package is missing. Press Install MCP server (it needs the internet once)."
    if not socket_bridge.get().is_running():
        socket_bridge.start()
    ok, message, names = mcp_probe.check_server(workspace.server_command("claude"), cwd=workspace.folder(),
                                                env=clean_env())
    return (OK if ok else ERROR), message


def install_mcp():
    """Make <workspace>/venv and install the official mcp package into it."""
    base = workspace.system_python() or workspace.blender_python()
    if not base:
        return ERROR, "No Python found to make the environment. Install Python 3.10 or newer, then try again."
    venv_dir = workspace.path("venv")
    if not os.path.isfile(workspace.venv_python(venv_dir)):
        code, out = _run([base, "-m", "venv", venv_dir], timeout=300)
        if code != 0:
            return ERROR, f"Could not make the Python environment: {out.strip()[-200:]}"
    requirements = os.path.join(workspace.server_dir(), "requirements.txt")
    code, out = _run([workspace.venv_python(venv_dir), "-m", "pip", "install", "--disable-pip-version-check",
                      "-r", requirements], timeout=900)
    if code != 0:
        return ERROR, f"Installing the MCP package failed: {out.strip()[-240:]}"
    return OK, "The MCP server is installed in the workspace folder. Press Check MCP server."


# ----------------------------------------------------------------------
CHECKS = {"claude": check_claude, "codex": check_codex, "mcp": check_mcp, "install": install_mcp}
NAMES = {"claude": "Claude", "codex": "Codex", "mcp": "MCP server", "install": "Install"}


def run_in_background(names, config=None):
    """Run checks one after another in a thread; results appear in the panel."""
    for name in names:
        results[name] = (RUNNING, "Checking ..." if name != "install" else "Installing ...")
    session.changed_flag = True

    def work():
        for name in names:
            fn = CHECKS[name]
            try:
                level, message = fn(config) if name in ("claude", "codex") else fn()
            except Exception as e:      # a check must never break Blender
                level, message = ERROR, f"The check failed: {e}"
            results[name] = (level, message)
            session.add("info" if level == OK else "error", f"{NAMES[name]}: {message}")
    threading.Thread(target=work, name="BlendWMe setup check", daemon=True).start()
