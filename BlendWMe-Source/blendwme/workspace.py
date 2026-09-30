"""The BlendWMe workspace folder: where the outside brains run.

Claude Code and Codex run as separate programs. They start in this folder,
so they find BlendWMe's instructions here (CLAUDE.md and AGENTS.md, the same
text) and nothing else of yours. The folder also holds:

    connection.json   port and random token of the add-on's local socket
                      (written when the bridge starts, removed when it stops)
    mcp-claude.json   MCP config that points Claude Code at the BlendWMe server
    venv/             optional Python environment with the `mcp` package,
                      made by the "Install MCP server" button

No login tokens or keys are ever written here. Sign-in stays inside the
official `claude` and `codex` programs.
"""

import json
import os
import shutil
import sys
import uuid

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
CONNECTION_FILE = "connection.json"
SERVER_PACKAGE = "blendwme_mcp"
INSTANCE_ID = str(uuid.uuid4())


def default_folder():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = docs if os.path.isdir(docs) else os.path.expanduser("~")
    return os.path.join(base, "BlendWMe Workspace")


def _prefs():
    try:
        from . import prefs
        return prefs.addon_prefs()
    except Exception:   # outside Blender, or the add-on is not registered (tests)
        return None


# A test (or the panel) can point everything at another folder.
_override = {"folder": ""}


def set_folder(path):
    _override["folder"] = path or ""


def root_folder(create=True):
    p = _prefs()
    path = _override["folder"] or (getattr(p, "workspace_folder", "") or "").strip() or default_folder()
    try:
        import bpy
        path = bpy.path.abspath(path)
    except Exception:
        pass
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def folder(create=True):
    """Per-process instructions/discovery prevent simultaneous Blender windows
    from replacing each other's MCP configuration before a client starts."""
    root = root_folder(create)
    target = root if _override["folder"] else os.path.join(root, "instances", INSTANCE_ID)
    if create:
        os.makedirs(target, exist_ok=True)
    return target


def path(name):
    if name == "venv":
        return os.path.join(root_folder(), name)
    return os.path.join(folder(), name)


# ----------------------------------------------------------------------
# Connection file (the MCP server reads it to find the add-on)
# ----------------------------------------------------------------------

def write_connection(port, token):
    import bpy
    from .tool_host import host
    if not bpy.context.scene.get("blendwme_scene_id"):
        bpy.context.scene["blendwme_scene_id"] = str(uuid.uuid4())
    data = {"host": "127.0.0.1", "port": port, "token": token, "pid": os.getpid(),
            "instance": INSTANCE_ID, "task": host.task_id,
            "scene": bpy.context.scene["blendwme_scene_id"]}
    target = path(CONNECTION_FILE)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, target)
    return target


def remove_connection(token):
    """Delete the connection file, but only if it is still ours (another Blender may own it now)."""
    target = path(CONNECTION_FILE)
    try:
        with open(target, encoding="utf-8") as f:
            if json.load(f).get("token") != token:
                return
        os.remove(target)
    except (OSError, ValueError):
        pass


# ----------------------------------------------------------------------
# The MCP server program and the Python that runs it
# ----------------------------------------------------------------------

def server_dir():
    """Folder of the blendwme_mcp package: inside the add-on (zip) or next to it (source)."""
    for candidate in (os.path.join(ADDON_DIR, SERVER_PACKAGE),
                      os.path.join(os.path.dirname(ADDON_DIR), SERVER_PACKAGE)):
        if os.path.isfile(os.path.join(candidate, "run.py")):
            return candidate
    return ""


def server_script():
    d = server_dir()
    return os.path.join(d, "run.py") if d else ""


def venv_python(base=None):
    base = base or path("venv")
    if os.name == "nt":
        return os.path.join(base, "Scripts", "python.exe")
    return os.path.join(base, "bin", "python")


def server_python():
    """The Python that runs the MCP server, or "".

    Order: the path set in preferences, the workspace venv (made by the
    install button), a developer .venv next to the source folder, then any
    Python on PATH. The setup check tells the user whether it has `mcp`.
    """
    p = _prefs()
    chosen = (getattr(p, "mcp_python", "") or "").strip()
    if chosen:
        return chosen
    for candidate in (venv_python(), venv_python(os.path.join(os.path.dirname(ADDON_DIR), ".venv"))):
        if os.path.isfile(candidate):
            return candidate
    return system_python()


def system_python():
    """A Python 3 on PATH (not Blender's own), or ""."""
    for name in ("python3", "python", "py"):
        found = shutil.which(name)
        if found and "WindowsApps" not in found:   # skip the Microsoft Store stub
            return found
    return ""


def blender_python():
    """Blender's bundled Python (can make a venv when no other Python is installed)."""
    return sys.executable if os.path.basename(sys.executable).lower().startswith("python") else ""


def server_command(brain):
    """[program, args...] that starts the MCP server for one brain."""
    return [server_python(), server_script(), "--instance", INSTANCE_ID,
            "--brain", brain, "--workspace", folder()]


def mcp_config(brain):
    """MCP config in Claude Code's --mcp-config JSON format."""
    cmd = server_command(brain)
    return {"mcpServers": {"blendwme": {"type": "stdio", "command": cmd[0], "args": cmd[1:]}}}


def write_mcp_config(brain="claude"):
    target = path(f"mcp-{brain}.json")
    with open(target, "w", encoding="utf-8") as f:
        json.dump(mcp_config(brain), f, indent=2)
    return target


def write_instructions(text):
    """The shared instructions every brain reads (CLAUDE.md for Claude, AGENTS.md for Codex)."""
    for name in ("CLAUDE.md", "AGENTS.md"):
        with open(path(name), "w", encoding="utf-8") as f:
            f.write(text)
