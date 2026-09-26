"""A tiny MCP client (standard library only) to check the BlendWMe MCP server.

Blender's Python has no `mcp` package, so the setup check speaks the MCP
protocol directly: JSON-RPC 2.0, one message per line over the server's
stdin and stdout. It starts the server, says hello (initialize), lists the
tools and can make a tool call. Used by the "Check MCP server" button and by
the headless tests.
"""

import json
import os
import queue
import subprocess
import threading

PROTOCOL_VERSION = "2025-06-18"


class ProbeError(Exception):
    pass


def _no_window():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class Probe:
    def __init__(self, command, cwd=None, env=None):
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, cwd=cwd, env=env,
                                         creationflags=_no_window())
        except OSError as e:
            raise ProbeError(f"Could not start the MCP server: {e}") from None
        self.lines = queue.Queue()
        self.errors = []
        self.next_id = 0
        threading.Thread(target=self._read, args=(self.proc.stdout, self.lines), daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    @staticmethod
    def _read(stream, out):
        for line in iter(stream.readline, b""):
            out.put(line)
        out.put(None)

    def _read_err(self):
        for line in iter(self.proc.stderr.readline, b""):
            self.errors.append(line.decode("utf-8", "replace").rstrip())
            del self.errors[:-40]

    def _send(self, message):
        try:
            self.proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except OSError:
            raise ProbeError("The MCP server stopped. " + self.stderr_tail()) from None

    def request(self, method, params=None, timeout=20):
        self.next_id += 1
        rid = self.next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                raise ProbeError(f"The MCP server did not answer {method} within {timeout}s.") from None
            if line is None:
                raise ProbeError("The MCP server stopped. " + self.stderr_tail())
            try:
                message = json.loads(line)
            except ValueError:
                continue      # not a protocol line
            if message.get("id") != rid:
                continue      # a notification or another reply
            if "error" in message:
                raise ProbeError(f"{method} failed: {message['error'].get('message')}")
            return message.get("result") or {}

    def initialize(self):
        result = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                             "clientInfo": {"name": "blendwme-setup-check", "version": "2.0.0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self):
        return self.request("tools/list").get("tools", [])

    def call_tool(self, name, arguments=None, timeout=120):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)

    def stderr_tail(self):
        return " | ".join(self.errors[-4:])

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def check_server(command, cwd=None):
    """(ok, message, tool names). Starts the server, lists its tools, stops it."""
    try:
        probe = Probe(command, cwd=cwd)
    except ProbeError as e:
        return False, str(e), []
    try:
        info = probe.initialize()
        names = [t.get("name") for t in probe.list_tools()]
    except ProbeError as e:
        tail = probe.stderr_tail()
        message = str(e)
        if "No module named 'mcp'" in tail or "No module named mcp" in tail:
            message = "The MCP package is missing. Press 'Install MCP server' (it needs an internet connection)."
        return False, message, []
    finally:
        probe.close()
    name = (info.get("serverInfo") or {}).get("name", "?")
    if names == ["blendwme_status"]:
        return False, "The MCP server starts, but it cannot reach this Blender. Restart the bridge.", names
    return True, f"The MCP server '{name}' starts and offers {len(names)} BlendWMe tools.", names
