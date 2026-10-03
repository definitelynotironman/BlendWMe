# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""The MCP server's link to the BlendWMe add-on (standard library only).

Reads <workspace>/connection.json (port and token, written by the add-on) and
sends one JSON line per request over 127.0.0.1. A new connection is made for
every request, so restarting Blender never leaves a stale socket behind.
"""

import json
import os
import socket
import uuid
import time

__author__ = "definitelynotironman"

CONNECTION_FILE = "connection.json"
CONNECT_TIMEOUT = 5
CALL_TIMEOUT = 60 * 60        # a tool may wait for the user (teach_step); the add-on caps waits at 45 min
MAX_REPLY = 64 * 1024 * 1024

NOT_RUNNING = ("BlendWMe is not reachable. Open Blender with the BlendWMe add-on enabled "
               "(its bridge starts with Blender), then try again.")


class LinkError(Exception):
    """BlendWMe could not be reached, or refused the request; the message says what to do."""
    pass


class BlenderLink:
    """One MCP client's link to one Blender instance.

    The first successful read of connection.json pins that port and token (and
    the task and scene it was started for). If Blender restarts or another
    instance writes the file, later requests are refused instead of being
    silently redirected to a different scene.
    """
    def __init__(self, workspace, instance=None):
        """Remember the workspace and the expected instance; nothing is pinned yet."""
        self.workspace = workspace
        self.next_id = 0
        self.instance = instance
        self.pinned = None
        self.task = None
        self.scene = None
        self.client = str(uuid.uuid4())

    def _connection(self):
        """(host, port, token) from connection.json, pinning the identity on first use."""
        path = os.path.join(self.workspace, CONNECTION_FILE)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if self.instance and data.get("instance") != self.instance:
                raise LinkError("The Blender instance changed. Start a new client from the intended Blender window.")
            identity = (data["port"], data["token"])
            if self.pinned is not None and identity != self.pinned:
                raise LinkError("The Blender connection changed. Start a new client; this request was not redirected.")
            self.pinned = identity
            if self.task is None:
                self.task = data.get("task")
                self.scene = data.get("scene")
            return data.get("host") or "127.0.0.1", int(data["port"]), str(data["token"])
        except (OSError, ValueError, KeyError, TypeError):
            raise LinkError(NOT_RUNNING) from None

    def request(self, op, timeout=CALL_TIMEOUT, **fields):
        """Send one request and return the add-on's reply dict.

        Every request carries the token, the pinned task and scene, this client's id
        and an absolute deadline, so the add-on can refuse stale or redirected work.
        """
        host, port, token = self._connection()
        if host not in ("127.0.0.1", "localhost"):
            raise LinkError("The connection file points away from this computer. Refusing to connect.")
        self.next_id += 1
        message = dict(fields, op=op, id=self.next_id, token=token, task=self.task,
                       scene=self.scene, client=self.client, expires=time.time() + timeout)
        try:
            with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as sock:
                sock.settimeout(timeout)
                sock.sendall(json.dumps(message).encode("utf-8") + b"\n")
                reply = sock.makefile("rb").readline(MAX_REPLY)
        except socket.timeout:
            raise LinkError("BlendWMe did not answer in time.") from None
        except OSError:
            raise LinkError(NOT_RUNNING) from None
        if not reply:
            raise LinkError("BlendWMe closed the connection without an answer.")
        data = json.loads(reply.decode("utf-8"))
        if not data.get("ok"):
            raise LinkError(data.get("error") or "BlendWMe refused the request.")
        return data

    def ping(self):
        """The add-on's ping result (a quick reachability check)."""
        return self.request("ping", timeout=CONNECT_TIMEOUT)["result"]

    def list_tools(self):
        """The add-on's tool specs."""
        return self.request("list_tools", timeout=30)["result"]

    def call(self, tool, args, brain):
        """(result, images) for one tool call."""
        data = self.request("call", tool=tool, args=args or {}, brain=brain)
        return data.get("result"), data.get("images") or []
