# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
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

__author__ = "definitelynotironman"

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
CONNECTION_FILE = "connection.json"
SERVER_PACKAGE = "blendwme_mcp"
INSTANCE_ID = str(uuid.uuid4())


class Workspace:
    """Resolves and writes every file the outside brains and MCP server need.

    Layout::

        <root>/venv/                         shared tools-server Python (install button)
        <root>/instances/<instance id>/      one folder per running Blender process

    The per-instance folder matters: two Blender windows must never overwrite
    each other's connection.json or MCP config, or a brain could be redirected
    to the wrong project. A test override replaces the whole layout with one
    exact folder.
    """

    def __init__(self, instance_id=INSTANCE_ID):
        """Create a workspace for one Blender process, identified by instance_id."""
        self.instance_id = instance_id
        self.override = ""         # a test (or the panel) can point everything at another folder

    # -- folders -------------------------------------------------------------
    @staticmethod
    def default_folder():
        """Documents/BlendWMe Workspace, or ~/BlendWMe Workspace without a Documents folder."""
        docs = os.path.join(os.path.expanduser("~"), "Documents")
        base = docs if os.path.isdir(docs) else os.path.expanduser("~")
        return os.path.join(base, "BlendWMe Workspace")

    @staticmethod
    def _prefs():
        """The add-on preferences, or None outside Blender / before registration (tests)."""
        try:
            from . import prefs
            return prefs.addon_prefs()
        except Exception:
            return None

    def set_folder(self, path):
        """Use exactly this folder for everything (tests); "" returns to the normal layout."""
        self.override = path or ""

    def root_folder(self, create=True):
        """The workspace root: override, the preference, or the default folder."""
        p = self._prefs()
        path = self.override or (getattr(p, "workspace_folder", "") or "").strip() or self.default_folder()
        try:
            import bpy
            path = bpy.path.abspath(path)
        except Exception:
            pass
        if create:
            os.makedirs(path, exist_ok=True)
        return path

    def folder(self, create=True):
        """This process's own folder (instructions, discovery and MCP config).

        Per-process instructions/discovery prevent simultaneous Blender windows
        from replacing each other's MCP configuration before a client starts.
        """
        root = self.root_folder(create)
        target = root if self.override else os.path.join(root, "instances", self.instance_id)
        if create:
            os.makedirs(target, exist_ok=True)
        return target

    def path(self, name):
        """Full path of a workspace file. The shared venv lives at the root."""
        if name == "venv":
            return os.path.join(self.root_folder(), name)
        return os.path.join(self.folder(), name)

    # -- connection file (the MCP server reads it to find the add-on) -------
    def write_connection(self, port, token):
        """Atomically write host, port, token and the identities a request must match."""
        import bpy
        from .tool_host import host
        if not bpy.context.scene.get("blendwme_scene_id"):
            bpy.context.scene["blendwme_scene_id"] = str(uuid.uuid4())
        data = {"host": "127.0.0.1", "port": port, "token": token, "pid": os.getpid(),
                "instance": self.instance_id, "task": host.task_id,
                "scene": bpy.context.scene["blendwme_scene_id"]}
        target = self.path(CONNECTION_FILE)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, target)
        return target

    def remove_connection(self, token):
        """Delete the connection file, but only if it is still ours (another Blender may own it now)."""
        target = self.path(CONNECTION_FILE)
        try:
            with open(target, encoding="utf-8") as f:
                if json.load(f).get("token") != token:
                    return
            os.remove(target)
        except (OSError, ValueError):
            pass

    # -- the MCP server program and the Python that runs it -----------------
    @staticmethod
    def server_dir():
        """Folder of the blendwme_mcp package: inside the add-on (zip) or next to it (source)."""
        for candidate in (os.path.join(ADDON_DIR, SERVER_PACKAGE),
                          os.path.join(os.path.dirname(ADDON_DIR), SERVER_PACKAGE)):
            if os.path.isfile(os.path.join(candidate, "run.py")):
                return candidate
        return ""

    def server_script(self):
        """Path of blendwme_mcp/run.py, or "" when the server is missing."""
        d = self.server_dir()
        return os.path.join(d, "run.py") if d else ""

    def venv_python(self, base=None):
        """The python executable inside a venv (the workspace venv by default)."""
        base = base or self.path("venv")
        if os.name == "nt":
            return os.path.join(base, "Scripts", "python.exe")
        return os.path.join(base, "bin", "python")

    def server_python(self):
        """The Python that runs the MCP server, or "".

        Order: the path set in preferences, the workspace venv (made by the
        install button), a developer .venv next to the source folder, then any
        Python on PATH. The setup check tells the user whether it has `mcp`.
        """
        p = self._prefs()
        chosen = (getattr(p, "mcp_python", "") or "").strip()
        if chosen:
            return chosen
        for candidate in (self.venv_python(), self.venv_python(os.path.join(os.path.dirname(ADDON_DIR), ".venv"))):
            if os.path.isfile(candidate):
                return candidate
        return self.system_python()

    @staticmethod
    def system_python():
        """A Python 3 on PATH (not Blender's own), or ""."""
        for name in ("python3", "python", "py"):
            found = shutil.which(name)
            if found and "WindowsApps" not in found:   # skip the Microsoft Store stub
                return found
        return ""

    @staticmethod
    def blender_python():
        """Blender's bundled Python (can make a venv when no other Python is installed)."""
        return sys.executable if os.path.basename(sys.executable).lower().startswith("python") else ""

    def server_command(self, brain):
        """[program, args...] that starts the MCP server for one brain."""
        return [self.server_python(), self.server_script(), "--instance", self.instance_id,
                "--brain", brain, "--workspace", self.folder()]

    def mcp_config(self, brain):
        """MCP config in Claude Code's --mcp-config JSON format."""
        cmd = self.server_command(brain)
        return {"mcpServers": {"blendwme": {"type": "stdio", "command": cmd[0], "args": cmd[1:]}}}

    def write_mcp_config(self, brain="claude"):
        """Write mcp-<brain>.json into this process's folder and return its path."""
        target = self.path(f"mcp-{brain}.json")
        with open(target, "w", encoding="utf-8") as f:
            json.dump(self.mcp_config(brain), f, indent=2)
        return target

    def write_instructions(self, text):
        """The shared instructions every brain reads (CLAUDE.md for Claude, AGENTS.md for Codex)."""
        for name in ("CLAUDE.md", "AGENTS.md"):
            with open(self.path(name), "w", encoding="utf-8") as f:
                f.write(text)


# The add-on has one workspace per Blender process. The module functions below
# are the stable entry points used by the bridge, engines, setup checks and tests.
workspace = Workspace()
default_folder = Workspace.default_folder
set_folder = workspace.set_folder
root_folder = workspace.root_folder
folder = workspace.folder
path = workspace.path
write_connection = workspace.write_connection
remove_connection = workspace.remove_connection
server_dir = Workspace.server_dir
server_script = workspace.server_script
venv_python = workspace.venv_python
server_python = workspace.server_python
system_python = Workspace.system_python
blender_python = Workspace.blender_python
server_command = workspace.server_command
mcp_config = workspace.mcp_config
write_mcp_config = workspace.write_mcp_config
write_instructions = workspace.write_instructions
