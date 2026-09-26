"""The add-on's end of the MCP bridge: a local socket the MCP server talks to.

    Claude Code / Codex  --stdio-->  blendwme_mcp (MCP server)  --socket-->  this module  ->  tool_host

Safety:
- It listens on 127.0.0.1 only, never on the network.
- Every request must carry a random token. The token is made fresh each time
  the bridge starts and written to <workspace>/connection.json, where the MCP
  server reads it. The file is deleted when the bridge stops.
- The token is compared in constant time and never logged.

Protocol: one JSON object per line, in both directions.
    request  {"token": ..., "id": 1, "op": "ping" | "list_tools" | "call",
              "tool": "run_python", "args": {...}, "brain": "claude"}
    reply    {"id": 1, "ok": true, "result": ..., "images": [{"data": base64, "mime": "image/png"}]}
             {"id": 1, "ok": false, "error": "..."}

Each connection gets its own thread, so a long wait (teach_step) never blocks
a ping. Tool calls themselves run one at a time (ToolHost.lock), and anything
that touches Blender data runs on Blender's main thread through bridge.py.
The idea of a socket between an MCP server and the Blender add-on follows
MCP for Blender (MIT); this code is BlendWMe's own.
"""

import hmac
import json
import secrets
import socket
import threading

from . import tools, workspace

MAX_LINE = 16 * 1024 * 1024


class SocketBridge:
    def __init__(self, host):
        self.host = host            # the ToolHost that runs the tools
        self.server = None
        self.token = ""
        self.port = 0
        self.thread = None
        self.running = False
        self.calls = 0

    def is_running(self):
        return self.running

    def start(self, port=0):
        """Listen on 127.0.0.1 (port 0 = any free port) and write the connection file."""
        if self.running:
            return self.port
        self.token = secrets.token_urlsafe(32)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", port))
        server.listen(8)
        self.server = server
        self.port = server.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._accept, name="BlendWMe socket", daemon=True)
        self.thread.start()
        workspace.write_connection(self.port, self.token)
        return self.port

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self.server.close()
        except OSError:
            pass
        workspace.remove_connection(self.token)
        self.server = None

    # ------------------------------------------------------------------
    def _accept(self):
        while self.running:
            try:
                conn, _addr = self.server.accept()
            except OSError:
                break           # closed by stop()
            threading.Thread(target=self._serve, args=(conn,), name="BlendWMe socket client",
                             daemon=True).start()

    def _serve(self, conn):
        with conn:
            reader = conn.makefile("rb")
            while self.running:
                try:
                    line = reader.readline(MAX_LINE)
                except OSError:
                    return
                if not line:
                    return
                reply = self._handle(line)
                try:
                    conn.sendall(json.dumps(reply, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
                except OSError:
                    return

    def _handle(self, line):
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"ok": False, "error": "Bad request: not one JSON object per line."}
        rid = request.get("id")
        token = str(request.get("token") or "")
        if not self.token or not hmac.compare_digest(token.encode(), self.token.encode()):
            return {"id": rid, "ok": False, "error": "Wrong or missing token. Restart the MCP server."}
        op = request.get("op")
        if op == "ping":
            return {"id": rid, "ok": True, "result": {"blendwme": True}}
        if op == "list_tools":
            return {"id": rid, "ok": True, "result": tools.mcp_tools()}
        if op == "call":
            self.calls += 1
            out = self.host.call(str(request.get("tool") or ""), request.get("args") or {},
                                 _brain(request.get("brain")))
            return {"id": rid, "ok": True, "result": out["result"], "images": out["images"]}
        return {"id": rid, "ok": False, "error": f"Unknown op {op!r}."}


def _brain(value):
    value = str(value or "").lower()
    return value if value in ("claude", "codex", "api") else "claude"


# ----------------------------------------------------------------------
# The one bridge of this Blender session
# ----------------------------------------------------------------------
_bridge = None


def get():
    global _bridge
    if _bridge is None:
        from .tool_host import host
        _bridge = SocketBridge(host)
    return _bridge


def prepare_workspace():
    """Write the shared instructions and Claude's MCP config into the workspace folder."""
    import bpy
    from . import knowledge, prompts
    workspace.write_instructions(prompts.brain_instructions(bpy.app.version_string, knowledge.skill_index()))
    workspace.write_mcp_config("claude")


def start():
    """Start the bridge and prepare the workspace. Returns a short status line."""
    b = get()
    try:
        port = b.start()
    except OSError as e:
        return f"The MCP bridge could not start: {e}"
    try:
        prepare_workspace()
    except OSError as e:
        return f"The bridge runs on port {port}, but the workspace could not be written: {e}"
    return f"The MCP bridge is running (127.0.0.1:{port})."


def stop():
    if _bridge is not None:
        _bridge.stop()
