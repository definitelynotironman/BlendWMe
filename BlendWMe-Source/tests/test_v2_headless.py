"""BlendWMe 2.0 headless tests: the MCP bridge, the tool host, the journal, the engines and the relay.

Run (no subscription is used; every brain here is fake):

    blender -b --factory-startup --python-exit-code 1 --python tests/test_v2_headless.py

When a Python with the `mcp` package exists (BlendWMe-Source/.venv), the real
MCP server program is started too, and a tool call travels the whole way:
MCP client -> blendwme_mcp -> socket -> add-on -> Blender.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bpy  # noqa: E402

import blendwme  # noqa: E402
from blendwme import bridge, mcp_probe, prefs, scene_info, socket_bridge, state, tool_host, tools, workspace  # noqa: E402
from blendwme.state import session  # noqa: E402

passed = []


def check(name, cond, detail=""):
    assert cond, f"FAILED {name} {detail}"
    passed.append(name)
    print("ok -", name)


def pump_until(predicate, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        bridge._pump()
        if predicate():
            return True
        time.sleep(0.02)
    raise AssertionError("timeout; log tail: " + json.dumps(session.log[-6:], default=str))


def in_thread(fn, *args):
    """Run fn in a worker thread (like the socket or a brain does). Returns a holder dict."""
    box = {"done": False}

    def work():
        try:
            box["value"] = fn(*args)
        except BaseException as e:
            box["error"] = e
        box["done"] = True
    threading.Thread(target=work, daemon=True).start()
    return box


def wait(box, timeout=60):
    pump_until(lambda: box["done"], timeout)
    if "error" in box:
        raise box["error"]
    return box["value"]


def fresh_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def reset_session(mode="AUTO"):
    session.reset_conversation()
    session.mode = mode
    session.build_style = "FULL"       # no 0.9 s "landing" pause between parts in tests
    session.stop_flag.clear()
    session.pause_flag.clear()
    tool_host.host.reset_task("Build a test scene", [])


prefs.register()
blendwme.ui.register()
WORKSPACE = tempfile.mkdtemp(prefix="bwm_v2_ws_")
workspace.set_folder(WORKSPACE)


# ----------------------------------------------------------------------
# M1: the socket bridge
# ----------------------------------------------------------------------

def raw(request, token=None):
    """One request over the socket, the way blendwme_mcp/link.py sends it."""
    with open(os.path.join(WORKSPACE, "connection.json"), encoding="utf-8") as f:
        conn = json.load(f)
    request = dict(request, token=conn["token"] if token is None else token, id=1)
    with socket.create_connection(("127.0.0.1", conn["port"]), timeout=5) as s:
        s.settimeout(120)
        s.sendall(json.dumps(request).encode() + b"\n")
        return json.loads(s.makefile("rb").readline())


def call(tool, brain="claude", **args):
    """A tool call over the real socket from a worker thread, while the main thread pumps."""
    reply = wait(in_thread(raw, {"op": "call", "tool": tool, "args": args, "brain": brain}), 120)
    assert reply["ok"], reply
    return reply["result"], reply["images"]


fresh_scene()
reset_session()
status = socket_bridge.start()
check("bridge starts on 127.0.0.1", "127.0.0.1" in status and socket_bridge.get().is_running(), status)
conn = json.load(open(os.path.join(WORKSPACE, "connection.json"), encoding="utf-8"))
check("connection file has port and a long random token", conn["port"] > 0 and len(conn["token"]) >= 32, conn.keys())
check("shared instructions written as CLAUDE.md and AGENTS.md",
      open(os.path.join(WORKSPACE, "CLAUDE.md"), encoding="utf-8").read()
      == open(os.path.join(WORKSPACE, "AGENTS.md"), encoding="utf-8").read()
      and "one of BlendWMe's two brains" in open(os.path.join(WORKSPACE, "AGENTS.md"), encoding="utf-8").read())
mcp_cfg = json.load(open(os.path.join(WORKSPACE, "mcp-claude.json"), encoding="utf-8"))
check("Claude's MCP config starts blendwme_mcp for claude",
      mcp_cfg["mcpServers"]["blendwme"]["args"][-3:] == ["claude", "--workspace", WORKSPACE]
      and mcp_cfg["mcpServers"]["blendwme"]["args"][0].endswith("run.py"), mcp_cfg)

check("ping answers", raw({"op": "ping"})["ok"])
bad = raw({"op": "ping"}, token="wrong")
check("wrong token refused", not bad["ok"] and "token" in bad["error"].lower(), bad)
check("missing token refused", not raw({"op": "ping"}, token="")["ok"])
listed = raw({"op": "list_tools"})["result"]
names = {t["name"] for t in listed}
check("every BlendWMe tool is listed, plus handoff_note and read_journal",
      {"run_python", "get_scene_info", "get_object_info", "look", "plan", "teach_step", "point_at", "ask_user",
       "load_skill", "inspect_api", "finish", "handoff_note", "read_journal"} <= names, names)
check("tools carry JSON schemas", all(t["inputSchema"]["type"] == "object" for t in listed))

# A brain builds through the socket.
result, _ = call("plan", parts=["Base 2 x 2 x 0.2 m on the ground", "Post 0.2 x 0.2 x 1 m on Base"])
check("plan sets the checklist", [p["name"][:4] for p in session.plan] == ["Base", "Post"], session.plan)
result, _ = call("run_python", brain="codex", title="Base", explain="A slab", part=1,
                 code="bwm.box('Base', (2, 2, 0.2), (0, 0, 0.1))")
check("run_python through the socket builds in Blender", result["ok"] and "Base" in bpy.data.objects, result)
base = bpy.data.objects["Base"]
check("objects are signed by the brain that made them", base.get("blendwme_author") == "ai"
      and base.get("blendwme_brain") == "codex")
check("steps are signed by the brain", session.steps[-1]["brain"] == "codex")
check("plan ticks from a brain's step", session.plan[0]["done"] is True)
check("scene info shows the maker", scene_info.object_summary(base).get("made_by_brain") == "codex")

result, _ = call("run_python", title="Post", part=2, code="bwm.box('Post', (0.2, 0.2, 1), (0, 0, 0.7))\nundefined_name")
check("failed step rolled back through the socket", not result["ok"] and "Post" not in bpy.data.objects
      and "line 2" in result["error"], result)

result, _ = call("teach_step", title="Bevel", instruction="Add a bevel")
check("tools of another mode are refused with the allowed list", "cannot be used in AUTO mode" in result["error"])
result, _ = call("made_up_tool")
check("unknown tools are refused", "no BlendWMe tool" in result["error"])

# What the user does while a brain works reaches the brain.
base.location.x = 3.0
session.inbox.put("Make it taller please")
result, _ = call("get_scene_info")
check("user edits are attached to the next tool result", "Base" in result.get("user_changes", ""), result.keys())
check("user edits are tagged as the user's", base.get("blendwme_author") == "user"
      and "blendwme_brain" not in base)
check("messages typed while working are attached", "Make it taller" in result.get("user_message", ""))

# look returns the picture itself.
result, images = call("look", mode="angles", question="Does it read?")
check("look returns a PNG image to the brain", len(images) == 1 and images[0]["mime"] == "image/png"
      and len(images[0]["data"]) > 1000, (result, len(images)))

# Pause: the next tool waits until Resume, then continues from the user's version.
session.pause_flag.set()
box = in_thread(raw, {"op": "call", "tool": "get_scene_info", "args": {}, "brain": "claude"})
pump_until(lambda: session.status == state.PAUSED)
check("a paused bridge holds the brain's next tool", not box["done"] and session.card["kind"] == "pause")
check("ping still answers while a tool waits", raw({"op": "ping"})["ok"])
bpy.data.objects["Base"].scale.z = 2.0
session.pause_flag.clear()
session.wake()
reply = wait(box)
check("after Resume the tool runs and reports the user's edit", "Base" in reply["result"].get("user_changes", ""))

# Stop releases a waiting tool at once.
session.pause_flag.set()
box = in_thread(raw, {"op": "call", "tool": "get_scene_info", "args": {}, "brain": "claude"})
pump_until(lambda: session.status == state.PAUSED)
session.stop_flag.set()
session.wake()
reply = wait(box)
check("Stop ends a waiting tool and tells the brain to stop", "stopped" in reply["result"], reply)
session.stop_flag.clear()
session.pause_flag.clear()

# Long waits are capped so they stay inside the brains' tool timeouts.
saved_cap = tool_host.WAIT_CAP
tool_host.WAIT_CAP = 1.0
result, _ = call("ask_user", question="Which colour?")
check("an unanswered question ends with still_waiting, not a timeout", "still_waiting" in result, result)
tool_host.WAIT_CAP = saved_cap
session.set_card(None)

# Teach mode: the brain waits for the user, the check detects completion.
reset_session("TEACH")
box = in_thread(raw, {"op": "call", "tool": "teach_step", "brain": "claude", "args": {
    "title": "Bevel the base", "instruction": "Add a Bevel modifier",
    "check": "any(m.type=='BEVEL' for m in D.objects['Base'].modifiers)"}})
pump_until(lambda: session.status == state.WAIT_USER)
bpy.data.objects["Base"].modifiers.new("Bevel", "BEVEL")
reply = wait(box)
check("teach_step through the socket detects the user's action", reply["result"]["result"] == "completed"
      and reply["result"]["check_passed"], reply)

# Watch mode: run_python waits for Next.
reset_session("WATCH")
box = in_thread(raw, {"op": "call", "tool": "run_python", "brain": "claude", "args": {
    "title": "Roof", "explain": "A cap", "code": "bwm.box('Cap', (1, 1, 0.1), (0, 0, 2))"}})
pump_until(lambda: session.status == state.WAIT_NEXT)
check("watch mode waits for Next", not box["done"] and session.card["kind"] == "watch")
session.wake()
reply = wait(box)
check("watch continues after Next", reply["result"]["ok"] and "pressed Next" in reply["result"]["user"])

# Auto mode: the art director looks without being asked, and checks once before finishing.
reset_session("AUTO")
reviews = []
for i in range(3):
    result, images = call("run_python", title=f"Part {i}", code=f"bwm.box('Part{i}', 0.5, ({i}, 3, 0.25))")
    if result.get("art_director"):
        reviews.append((result["art_director"], images))
check("art director review after 3 build steps, with pictures", len(reviews) == 1 and reviews[0][1]
      and "THE BRIEF IS" in reviews[0][0] and "Part0" in reviews[0][0], reviews and reviews[0][0][:200])
result, images = call("finish", summary="Done")
check("first finish triggers a final check only if something changed", result.get("ok") is True
      and session.status == state.DONE, result)
call("run_python", title="Extra", code="bwm.box('Extra', 0.3, (5, 5, 0.15))")
tool_host.host.begin_turn()
result, images = call("finish", summary="Done again")
check("a changed scene gets a final check with pictures before finishing",
      "not_finished_yet" in result and images, result.keys())
result, _ = call("finish", summary="Really done")
check("second finish is accepted", result.get("ok") is True)

# ----------------------------------------------------------------------
# M1: the whole way through the real MCP server program
# ----------------------------------------------------------------------
dev_python = workspace.venv_python(os.path.join(ROOT, ".venv"))
if os.path.isfile(dev_python):
    command = [dev_python, workspace.server_script(), "--brain", "claude", "--workspace", WORKSPACE]
    box = in_thread(mcp_probe.check_server, command)
    ok, message, names = wait(box, 60)
    check("setup check: the real MCP server starts and lists BlendWMe's tools", ok and "run_python" in names,
          message)

    def build_through_mcp():
        probe = mcp_probe.Probe(command)
        try:
            probe.initialize()
            return probe.call_tool("run_python", {"title": "Ball", "explain": "A sphere",
                                                  "code": "bwm.sphere('McpBall', 0.5, (0, -3, 0.5))"})
        finally:
            probe.close()
    reply = wait(in_thread(build_through_mcp), 90)
    text = reply["content"][0]["text"]
    check("a brain's MCP call builds in Blender (client -> server -> socket -> add-on)",
          "McpBall" in bpy.data.objects and json.loads(text)["ok"], text[:300])
    check("the MCP server signs the call with its brain", bpy.data.objects["McpBall"].get("blendwme_brain") == "claude")
else:
    print("skip - no .venv with the mcp package; the full MCP path was not tested")

socket_bridge.stop()
check("stopping the bridge removes the connection file",
      not os.path.exists(os.path.join(WORKSPACE, "connection.json")))

print(f"ALL {len(passed)} V2 CHECKS PASSED")
sys.stdout.flush()
os._exit(0)
