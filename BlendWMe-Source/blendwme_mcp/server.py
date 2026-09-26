"""BlendWMe MCP server: gives Claude Code and Codex the same BlendWMe tools.

It is a small, separate Python program (it does not run inside Blender). The
brain starts it over stdio. Every tool call is forwarded to the BlendWMe add-on
over a local socket (see link.py), and the add-on runs it in Blender.

    python run.py --brain claude --workspace "<BlendWMe workspace folder>"

The tool list comes from the add-on itself, so both brains always see exactly
the tools BlendWMe has. Uses the official `mcp` Python SDK (see requirements.txt).
"""

import argparse
import inspect
import json
import sys

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .link import BlenderLink, LinkError

SERVER_NAME = "blendwme"
VERSION = "2.0.0"

INSTRUCTIONS = ("BlendWMe's tools work inside the user's live Blender scene. Build with run_python, see "
                "with look, teach with teach_step and point_at, and finish with finish. Read the project "
                "journal with read_journal and leave a handoff_note before you stop with work left.")

STATUS_TOOL = {"name": "blendwme_status",
               "description": "BlendWMe was not reachable when the tools were listed. Call this to check "
                              "again and get instructions.",
               "inputSchema": {"type": "object", "properties": {}}, "readOnly": True}


def _log(text):
    # stdout carries the MCP protocol; anything for people goes to stderr.
    print("[blendwme_mcp] " + text, file=sys.stderr, flush=True)


class Bridge:
    """MCP-side behaviour, independent of the SDK version."""

    def __init__(self, brain, workspace):
        self.brain = brain
        self.link = BlenderLink(workspace)

    def tool_specs(self):
        try:
            return self.link.list_tools()
        except LinkError as e:
            _log(str(e))
            return [STATUS_TOOL]

    def tools(self):
        out = []
        for spec in self.tool_specs():
            out.append(types.Tool(name=spec["name"], description=spec.get("description", ""),
                                  inputSchema=spec.get("inputSchema") or {"type": "object", "properties": {}},
                                  annotations=types.ToolAnnotations(readOnlyHint=bool(spec.get("readOnly")))))
        return out

    def call(self, name, arguments):
        """(content list, is_error) for one call. Runs in a worker thread (it may wait a long time)."""
        if name == STATUS_TOOL["name"]:
            try:
                self.link.ping()
                return [types.TextContent(type="text", text="BlendWMe is running. Its tools are ready; if they "
                                                            "are missing from your list, restart this session.")], False
            except LinkError as e:
                return [types.TextContent(type="text", text=str(e))], True
        try:
            result, images = self.link.call(name, arguments or {}, self.brain)
        except LinkError as e:
            return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))], True
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        content = [types.TextContent(type="text", text=text)]
        for image in images:
            content.append(types.ImageContent(type="image", data=image["data"],
                                              mimeType=image.get("mime") or "image/png"))
        is_error = isinstance(result, dict) and bool(result.get("error")) and len(result) <= 2
        return content, is_error


def build_server(bridge):
    """An MCP Server for the installed SDK. SDK 2.x takes handlers in the constructor; 1.x uses decorators."""
    if "on_call_tool" in inspect.signature(Server.__init__).parameters:
        async def list_tools(ctx, params):
            specs = await anyio.to_thread.run_sync(bridge.tools)
            return types.ListToolsResult(tools=specs)

        async def call_tool(ctx, params):
            content, is_error = await anyio.to_thread.run_sync(bridge.call, params.name, params.arguments)
            return types.CallToolResult(content=content, isError=is_error)

        return Server(SERVER_NAME, version=VERSION, instructions=INSTRUCTIONS,
                      on_list_tools=list_tools, on_call_tool=call_tool)

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools_v1():
        return await anyio.to_thread.run_sync(bridge.tools)

    @server.call_tool()
    async def call_tool_v1(name, arguments):
        content, is_error = await anyio.to_thread.run_sync(bridge.call, name, arguments)
        if is_error:
            raise RuntimeError(content[0].text)
        return content

    return server


async def serve(brain, workspace):
    bridge = Bridge(brain, workspace)
    server = build_server(bridge)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv=None):
    parser = argparse.ArgumentParser(description="BlendWMe MCP server")
    parser.add_argument("--brain", default="claude", choices=["claude", "codex", "api"],
                        help="Which brain uses this server (signs every step it makes)")
    parser.add_argument("--workspace", required=True, help="The BlendWMe workspace folder")
    args = parser.parse_args(argv)
    _log(f"started for {args.brain}")
    anyio.run(serve, args.brain, args.workspace)


if __name__ == "__main__":
    main()
