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
import time

from . import bridge, tools, workspace

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
        self.clients = set()
        self.client_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(16)
        self.receipts = {}
        self.receipt_lock = threading.Lock()

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
        with self.client_lock:
            for client in list(self.clients):
                try:
                    client.shutdown(socket.SHUT_RDWR)
                    client.close()
                except OSError:
                    pass
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
            if not self.slots.acquire(blocking=False):
                conn.close()
                continue
            conn.settimeout(15)
            with self.client_lock:
                self.clients.add(conn)
            threading.Thread(target=self._serve, args=(conn,), name="BlendWMe socket client",
                             daemon=True).start()

    def _serve(self, conn):
        try:
            self._serve_client(conn)
        finally:
            with self.client_lock:
                self.clients.discard(conn)
            self.slots.release()

    def _serve_client(self, conn):
        with conn:
            reader = conn.makefile("rb")
            while self.running:
                try:
                    line = reader.readline(MAX_LINE)
                except OSError:
                    return
                if not line:
                    return
                if len(line) >= MAX_LINE or not line.endswith(b"\n"):
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
        if not isinstance(request, dict):
            return {"ok": False, "error": "A request must be an object."}
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
            if not isinstance(rid, (int, str)) or not isinstance(request.get("client", ""), str):
                return {"id": None, "ok": False, "error": "Invalid request identity."}
            if not isinstance(request.get("expires", 0), (int, float)):
                return {"id": rid, "ok": False, "error": "Invalid deadline."}
            expected_task = request.get("task")
            if expected_task != self.host.task_id:
                return {"id": rid, "ok": False, "error": "This client belongs to an obsolete task. Start a new turn."}
            if request.get("expires", time.time() + 1) <= time.time():
                return {"id": rid, "ok": False, "error": "The request expired before execution."}
            def context_matches():
                import bpy
                return request.get("scene") == bpy.context.scene.get("blendwme_scene_id")
            if not bridge.call(context_matches):
                return {"id": rid, "ok": False, "error": "The request belongs to a different scene."}
            key = (request.get("client"), rid, expected_task)
            if key[0]:
                with self.receipt_lock:
                    if key in self.receipts:
                        receipt = self.receipts[key]
                        owner = False
                    else:
                        if len(self.receipts) >= 1024:
                            return {"id": rid, "ok": False, "error": "Request history is full; start a fresh task/client."}
                        receipt = {"done": threading.Event()}
                        self.receipts[key] = receipt
                        owner = True
                if not owner:
                    if receipt["done"].wait(45 * 60):
                        return receipt["reply"]
                    return {"id": rid, "ok": False, "error": "The original request is still running. It was not repeated."}
            self.calls += 1
            try:
                with bridge.scope(deadline=request.get("expires")):
                    out = self.host.call(str(request.get("tool") or ""), request.get("args") or {},
                                         _brain(request.get("brain")))
                reply = {"id": rid, "ok": True, "result": out["result"], "images": out["images"]}
            except Exception as error:
                reply = {"id": rid, "ok": False, "error": str(error)}
            if key[0]:
                receipt["reply"] = reply
                receipt["done"].set()
            return reply
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
    b = get()
    if b.running:
        workspace.write_connection(b.port, b.token)
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
