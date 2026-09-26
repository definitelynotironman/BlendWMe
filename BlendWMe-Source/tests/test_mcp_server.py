"""Test the BlendWMe MCP server with a fake Blender socket (no Blender, no subscription).

Run with a Python that has the `mcp` package (see blendwme_mcp/requirements.txt):

    .venv/Scripts/python tests/test_mcp_server.py

The real server program (blendwme_mcp/run.py) is started over stdio by the
official MCP client, exactly as Claude Code or Codex start it. A fake add-on
socket answers like BlendWMe does and records what it was sent.
"""

import base64
import json
import os
import socket
import sys
import tempfile
import threading

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.join(os.path.dirname(HERE), "blendwme_mcp", "run.py")
TOKEN = "test-token-123"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()

TOOLS = [
    {"name": "run_python", "description": "Run Blender Python",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "code": {"type": "string"}},
                     "required": ["code"]}, "readOnly": False},
    {"name": "look", "description": "See the scene", "inputSchema": {"type": "object", "properties": {}},
     "readOnly": True},
]
SEEN = []
passed = []


def attr(obj, camel):
    """Read a field by its camelCase name (SDK 1.x) or snake_case name (SDK 2.x)."""
    snake = "".join("_" + c.lower() if c.isupper() else c for c in camel)
    return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, camel)


def check(name, cond, detail=""):
    assert cond, f"FAILED {name} {detail}"
    passed.append(name)
    print("ok -", name, flush=True)


class FakeAddon:
    """Answers like socket_bridge.py: one JSON line per request, token required."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        with conn:
            line = conn.makefile("rb").readline()
            if not line:
                return
            req = json.loads(line)
            SEEN.append(req)
            if req.get("token") != TOKEN:
                reply = {"id": req["id"], "ok": False, "error": "Wrong or missing token."}
            elif req["op"] == "list_tools":
                reply = {"id": req["id"], "ok": True, "result": TOOLS}
            elif req["op"] == "ping":
                reply = {"id": req["id"], "ok": True, "result": {"blendwme": True}}
            elif req["tool"] == "look":
                reply = {"id": req["id"], "ok": True, "result": {"view": "angles"},
                         "images": [{"data": PNG, "mime": "image/png"}]}
            elif req["tool"] == "run_python" and "boom" in req["args"].get("code", ""):
                reply = {"id": req["id"], "ok": True, "result": {"error": "NameError: boom"}}
            else:
                reply = {"id": req["id"], "ok": True, "result": {"ok": True, "changes": ["+ Cube"]}}
            conn.sendall(json.dumps(reply).encode() + b"\n")

    def close(self):
        self.sock.close()


def write_connection(folder, port, token=TOKEN):
    with open(os.path.join(folder, "connection.json"), "w", encoding="utf-8") as f:
        json.dump({"host": "127.0.0.1", "port": port, "token": token}, f)


async def session_for(folder, brain="claude"):
    params = StdioServerParameters(command=sys.executable,
                                   args=[RUN, "--brain", brain, "--workspace", folder])
    return params


async def run_tests():
    addon = FakeAddon()
    folder = tempfile.mkdtemp(prefix="bwm_mcp_test_")
    write_connection(folder, addon.port)
    params = await session_for(folder, "codex")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            init = await client.initialize()
            check("server introduces itself as blendwme", attr(init, "serverInfo").name == "blendwme")

            listed = await client.list_tools()
            names = [t.name for t in listed.tools]
            check("tool list comes from the add-on", names == ["run_python", "look"], names)
            look_tool = next(t for t in listed.tools if t.name == "look")
            check("read-only tools are marked read-only", attr(look_tool.annotations, "readOnlyHint") is True)
            check("schemas are passed through", attr(listed.tools[0], "inputSchema")["required"] == ["code"])

            result = await client.call_tool("run_python", {"title": "Cube", "code": "bwm.box('Cube', 1)"})
            check("tool call forwarded and answered", json.loads(result.content[0].text)["ok"] is True)
            call = [r for r in SEEN if r["op"] == "call"][-1]
            check("call carries the tool, args and token", call["tool"] == "run_python"
                  and call["args"]["code"] == "bwm.box('Cube', 1)" and call["token"] == TOKEN)
            check("call is signed with the brain", call["brain"] == "codex", call)

            result = await client.call_tool("look", {})
            kinds = [c.type for c in result.content]
            check("look returns the picture as an image", kinds == ["text", "image"]
                  and result.content[1].data == PNG and attr(result.content[1], "mimeType") == "image/png", kinds)

            result = await client.call_tool("run_python", {"code": "boom"})
            check("plain tool errors are flagged as errors", attr(result, "isError") is True)

            # The add-on restarts with a new token: the server reads the file again on every call.
            write_connection(folder, addon.port, "stale-token")
            result = await client.call_tool("run_python", {"code": "x = 1"})
            check("a wrong token is refused with a clear message",
                  attr(result, "isError") and "token" in result.content[0].text.lower(), result.content[0].text)
            write_connection(folder, addon.port)

            # Blender closed: the brain gets instructions, not a crash.
            os.remove(os.path.join(folder, "connection.json"))
            result = await client.call_tool("run_python", {"code": "x = 1"})
            check("Blender not running is explained", attr(result, "isError")
                  and "Open Blender" in result.content[0].text, result.content[0].text)

    # A server started while Blender is closed still starts, with a status tool.
    params = await session_for(folder)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            listed = await client.list_tools()
            check("without Blender the server offers a status tool", [t.name for t in listed.tools]
                  == ["blendwme_status"])
            result = await client.call_tool("blendwme_status", {})
            check("status tool explains how to fix it", "Open Blender" in result.content[0].text)
    addon.close()


if __name__ == "__main__":
    anyio.run(run_tests)
    print(f"ALL {len(passed)} MCP SERVER CHECKS PASSED")
