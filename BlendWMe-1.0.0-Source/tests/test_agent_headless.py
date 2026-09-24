"""End-to-end test of the agent loop in headless Blender with a fake model.

Run:  python tests/test_agent_headless.py   (needs the `bpy` module)

A local fake "Nebius" server replies with scripted tool calls, so we test the
real loop, real tool execution in Blender, error recovery, user-edit
detection, Watch gating and Teach checks without spending API credits.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bpy  # noqa: E402

import blendwme  # noqa: E402
from blendwme import bridge, prefs, state  # noqa: E402
from blendwme.agent import agent  # noqa: E402
from blendwme.state import session  # noqa: E402

# ----------------------------------------------------------------------
# Fake model server
# ----------------------------------------------------------------------
SCRIPT = []
SEEN = []


def call(_tool, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": f"call_{_tool}_{len(SEEN)}", "type": "function",
        "function": {"name": _tool, "arguments": json.dumps(args)}}]}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send({"data": [{"id": "nvidia/nemotron-3-super-120b-a12b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        # Every tool call must have an answer before the next request.
        msgs = body["messages"]
        pending = set()
        for m in msgs:
            for c in m.get("tool_calls") or []:
                pending.add(c["id"])
            if m.get("role") == "tool":
                pending.discard(m["tool_call_id"])
        assert not pending, f"unanswered tool calls: {pending}"
        reply = SCRIPT.pop(0) if SCRIPT else {"role": "assistant", "content": "All done."}
        if callable(reply):
            reply = reply(body)
        self._send({"choices": [{"message": reply, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 100}})

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{server.server_port}/v1"

CONFIG = {"api_key": "test", "base_url": URL, "model": "nvidia/nemotron-3-super-120b-a12b",
          "vision_model": "", "reasoning": "LOW", "temperature": 0.4, "max_tokens": 4096,
          "max_steps": 50, "references": [], "blender_version": bpy.app.version_string}


def pump_until(predicate, timeout=30, on_tick=None):
    end = time.time() + timeout
    while time.time() < end:
        bridge._pump()
        if on_tick:
            on_tick()
        if predicate():
            return True
        time.sleep(0.02)
    raise AssertionError("timeout; log tail: " + json.dumps(session.log[-6:], default=str))


def fresh_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


passed = []


def check(name, cond, detail=""):
    assert cond, f"FAILED {name} {detail}"
    passed.append(name)
    print("ok -", name)


# Register the add-on (UI classes, props). Overlay and timers need a UI; skip them.
prefs.register()
blendwme.ui.register()

# ----------------------------------------------------------------------
# 1. AUTO: build, hit an error, fix it, look without vision, finish
# ----------------------------------------------------------------------
fresh_scene()
session.mode = "AUTO"
SCRIPT[:] = [
    call("get_scene_info"),
    call("load_skill", name="low-poly-stylised-scenes"),
    call("run_python", title="Walls and base", explain="Main box and foundation.", code="""
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1))
walls = C.active_object; walls.name = "House_Walls"; walls.scale = (4, 3, 2)
bwm.apply_transforms(walls)
bwm.ground(walls, 0.2)
bwm.assign(walls, bwm.material("Cream", (0.85, 0.78, 0.62), 0.8))
base = bpy.data.objects.new("House_Base", bpy.data.meshes.new("base"))
C.scene.collection.objects.link(base)
print("walls dims", tuple(round(v, 2) for v in walls.dimensions))
"""),
    call("run_python", title="Roof", explain="Gable roof.", code="""
roof_mat = bwm.material("Terracotta", (0.62, 0.22, 0.12))
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 3))
roof = C.active_object; roof.name = "House_Roof"
roof.scale = (4.6, 3.6, 1.2)
undefined_name_here += 1
"""),
    call("run_python", title="Roof", explain="Gable roof, fixed.", code="""
roof_mat = bwm.material("Terracotta", (0.62, 0.22, 0.12))
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 3))
roof = C.active_object; roof.name = "House_Roof"
roof.scale = (4.6, 3.6, 1.2)
bwm.assign("House_Roof", "Terracotta")       # names work as well as objects
print("roof ok", len(roof.material_slots))
"""),
    call("look", question="Does the roof sit on the walls?", mode="angles"),
    call("inspect_api", path="BevelModifier"),
    call("inspect_api", path="bpy.ops.mesh.inset"),
    call("finish", summary="A small house."),
]
agent.send("Build a small low poly house", CONFIG, new_task=True)
pump_until(lambda: not agent.is_running())
check("auto finished", session.status == state.DONE, session.status)
check("walls built", "House_Walls" in bpy.data.objects)
w = bpy.data.objects["House_Walls"]
check("walls on ground at z=0.2", abs(min((w.matrix_world @ __import__('mathutils').Vector(c)).z for c in w.bound_box) - 0.2) < 1e-4)
check("ai authorship tagged", w.get("blendwme_author") == "ai")
tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
results = [json.loads(m["content"]) if m["content"].startswith("{") else m["content"] for m in tool_msgs]
err = next(r for r in results if isinstance(r, dict) and r.get("ok") is False)
check("error reported with line", "line 6" in err["error"] and "NameError" in err["error"], err)
check("failed step rolled back", "Removed" in err.get("rolled_back", ""), err)
check("no duplicate roof after retry", [o.name for o in bpy.data.objects if o.name.startswith("House_Roof")] == ["House_Roof"])
check("no orphan material duplicates", [m.name for m in bpy.data.materials if m.name.startswith("Terracotta")] == ["Terracotta"])
check("roof material fixed", len(bpy.data.objects["House_Roof"].material_slots) == 1)
check("printed output returned", any(isinstance(r, dict) and "walls dims" in r.get("printed", "") for r in results))
check("skill text returned", any(isinstance(r, str) and "Building recipe" in r for r in results))
check("look without vision handled", any(isinstance(r, dict) and "Vision is off" in r.get("error", "") for r in results))
check("api inspect works", any(isinstance(r, dict) and r.get("type") == "BevelModifier" and r["properties"] for r in results))
check("operator inspect works", any(isinstance(r, dict) and r.get("operator") == "bpy.ops.mesh.inset" for r in results))
check("system prompt has skills", "low-poly-stylised-scenes" in SEEN[0]["messages"][0]["content"])
check("tools sent natively", any(t["function"]["name"] == "run_python" for t in SEEN[0]["tools"]))
check("nemotron reasoning kwargs sent", SEEN[0].get("chat_template_kwargs", {}).get("low_effort") is True)
check("steps recorded", len(session.steps) == 3)

# ----------------------------------------------------------------------
# 2. Follow-up remembers the conversation and notices user edits
# ----------------------------------------------------------------------
w.location.x += 1.5            # the user moves the walls between tasks
SCRIPT[:] = [call("finish", summary="Kept your walls where you put them.")]
before = len(agent.messages)
agent.send("Add a chimney", CONFIG)
pump_until(lambda: not agent.is_running())
notes = [m["content"] for m in agent.messages[before:] if m["role"] == "user"]
check("follow-up keeps history", agent.messages[1]["content"].startswith("Build a small"))
check("user edit reported to model", any("House_Walls" in n and "moved" in n for n in notes), notes)
check("user edit tagged", w.get("blendwme_author") == "user")

# ----------------------------------------------------------------------
# 3. Text-only tool call fallback + nudge when the model only talks
# ----------------------------------------------------------------------
SCRIPT[:] = [
    {"role": "assistant", "content": "I will now add a door."},
    {"role": "assistant", "content": '<tool_call>{"name": "run_python", "arguments": {"title": "Door", "explain": "door", "code": "bpy.ops.mesh.primitive_cube_add(size=0.5)\\nC.active_object.name = \'Door\'"}}</tool_call>'},
    call("finish", summary="Door added."),
]
agent.send("Add a door", CONFIG)
pump_until(lambda: not agent.is_running())
check("nudge then text tool call recovered", "Door" in bpy.data.objects)

# ----------------------------------------------------------------------
# 4. Pause mid-task: user edits, model is told, stale plan skipped
# ----------------------------------------------------------------------
gate = threading.Event()


def slow_reply(body):
    gate.wait(10)
    return call("run_python", title="Window", explain="w", code="bpy.ops.mesh.primitive_cube_add(size=0.3)\nC.active_object.name='Window'")


SCRIPT[:] = [slow_reply, call("finish", summary="ok")]
agent.send("Add a window", CONFIG)
pump_until(lambda: session.status == state.THINKING)
session.pause_flag.set()                      # user presses Pause while the model thinks
gate.set()
pump_until(lambda: session.status == state.PAUSED)
bpy.data.objects["Door"].scale = (2, 2, 2)    # user edits
session.pause_flag.clear(); session.wake()    # Resume
pump_until(lambda: not agent.is_running())
skipped = [m for m in agent.messages if m["role"] == "tool" and "skipped" in m["content"]]
check("stale plan skipped after user edit", len(skipped) == 1)
check("window not built from stale plan", "Window" not in bpy.data.objects)
check("door edit reported", any(m["role"] == "user" and "Door" in m["content"] and "scaled" in m["content"] for m in agent.messages))

# ----------------------------------------------------------------------
# 5. WATCH waits for Next; TEACH waits for the user's action (check)
# ----------------------------------------------------------------------
session.mode = "WATCH"
SCRIPT[:] = [call("run_python", title="Add a plane", explain="Shift+A > Mesh > Plane",
                  code="bpy.ops.mesh.primitive_plane_add(size=4)\nC.active_object.name='Ground'"),
             call("finish", summary="done")]
agent.send("Show me how to add ground", CONFIG)
pump_until(lambda: session.status == state.WAIT_NEXT)
check("watch shows card", session.card and session.card["kind"] == "watch")
check("watch points at new object", session.pointers and session.pointers[0]["object"] == "Ground")
time.sleep(0.4)
check("watch really waits", session.status == state.WAIT_NEXT)
session.wake()
pump_until(lambda: not agent.is_running())
check("watch continues after Next", session.status == state.DONE)
check("mode switch announced", any("WATCH mode" in (m.get("content") or "") for m in agent.messages))

session.mode = "TEACH"
SCRIPT[:] = [call("run_python", title="cheat", explain="x", code="pass"),
             call("teach_step", title="Add a bevel", instruction="Add a Bevel modifier to Door.",
                  check="any(m.type=='BEVEL' for m in D.objects['Door'].modifiers)", point_object="Door"),
             call("finish", summary="You learned bevels.")]
agent.send("Teach me bevels", CONFIG)
pump_until(lambda: session.status == state.WAIT_USER)
check("teach blocks run_python", any("Teach mode the user does the work" in m["content"] for m in agent.messages if m["role"] == "tool"))
bpy.data.objects["Door"].modifiers.new("Bevel", "BEVEL")   # the user does it
pump_until(lambda: not agent.is_running())
last_teach = [json.loads(m["content"]) for m in agent.messages if m["role"] == "tool" and '"result"' in m["content"]][-1]
check("teach check detected completion", last_teach["result"] == "completed" and last_teach["check_passed"])
check("teach reports user change", any("modifiers changed" in l for l in last_teach["user_changes"]))

# ----------------------------------------------------------------------
# 6. Stop in the middle leaves a valid conversation
# ----------------------------------------------------------------------
session.mode = "WATCH"
SCRIPT[:] = [call("run_python", title="Cone", explain="x", code="bpy.ops.mesh.primitive_cone_add()"),
             call("finish", summary="never")]
agent.send("Add a cone", CONFIG)
pump_until(lambda: session.status == state.WAIT_NEXT)
agent.stop()
pump_until(lambda: not agent.is_running())
ids = {c["id"] for m in agent.messages for c in (m.get("tool_calls") or [])}
answered = {m["tool_call_id"] for m in agent.messages if m["role"] == "tool"}
check("stop leaves every tool call answered", ids == answered)
check("stopped status idle", session.status == state.IDLE)

# ----------------------------------------------------------------------
# 7. Safety blocklist
# ----------------------------------------------------------------------
from blendwme import executor  # noqa: E402
check("blocks os", executor.run("import os\nos.remove('x')")["ok"] is False)
check("blocks quitting", executor.run("bpy.ops.wm.quit_blender()")["ok"] is False)

check("blocks clipboard reads", executor.run("print(C.window_manager.clipboard)")["ok"] is False)
check("blocks importlib", executor.run("import importlib")["ok"] is False)
check("blocks reaching the add-on", executor.run("import bl_ext.user_default.blendwme.credentials")["ok"] is False)
check("allows environment textures", executor.run("n = 'ShaderNodeTexEnvironment'")["ok"] is True)

# 7b. Error hints, existing-object notes and the "stuck" guard
executor.run("bwm.collection('Props')")
r = executor.run("bwm.move_to('Door', 'prop')")
check("bad collection name explained", "No collection named" in r["error"] and "Similar" in r["error"], r)
r = executor.run("x = 'abc'\nx.objects")
check("str-instead-of-object hint", "needs the object itself" in r.get("hint", ""), r)
r = executor.run("D.objects['Door'].location.x += 0.5\nraise RuntimeError('boom')")
check("changes to existing objects reported", "Door" in r.get("note", ""), r)

session.mode = "AUTO"
bad = call("run_python", title="Broken", explain="x", code="bpy.ops.mesh.primitive_cube_add()\nundefined_thing()")
SCRIPT[:] = [bad, bad, bad, bad, call("finish", summary="ok after help")]
agent.send("Do the broken thing", CONFIG)
pump_until(lambda: session.status == state.PAUSED)
check("paused after 4 failures", session.card and "stuck" in session.card["title"].lower())
check("stuck hint given on 2nd failure", any("failed twice" in m["content"] for m in agent.messages if m["role"] == "tool"))
check("failed cubes never pile up", not any(o.name.startswith("Cube") for o in bpy.data.objects))
session.pause_flag.clear(); session.wake()
pump_until(lambda: not agent.is_running())
check("continues after resume", session.status == state.DONE)

# 8. Key handling: session mode keeps it in memory only, nothing in preferences
from blendwme import credentials  # noqa: E402
credentials.set_key("sk-test-" + "a" * 30, "SESSION")
check("session key in memory", credentials.get_key("SESSION").startswith("sk-test-"))
check("masked shows last 4 only", credentials.masked(credentials.get_key("SESSION")).endswith("aaaa") and "sk-" not in credentials.masked("sk-test-aaaa"))
check("no key property in preferences", "api_key" not in prefs.BWM_Preferences.__annotations__)
credentials.forget("SESSION")
check("forget clears it", credentials.get_key("SESSION") == "")

print(f"\nALL {len(passed)} CHECKS PASSED")
os._exit(0)
