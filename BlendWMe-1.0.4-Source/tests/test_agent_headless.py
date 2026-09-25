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

# Blender buffers stdout when it is piped, and os._exit() at the end skips the
# flush, so print each line straight away.
sys.stdout.reconfigure(line_buffering=True)

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
VISION_SEEN = []
VISION_OK = {"Qwen/Qwen3.8-27B"}


def call(_tool, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": f"call_{_tool}_{len(SEEN)}", "type": "function",
        "function": {"name": _tool, "arguments": json.dumps(args)}}]}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send({"data": [
            {"id": "nvidia/nemotron-3-super-120b-a12b", "architecture": {"modality": "text->text"}},
            {"id": "Qwen/Qwen3.8-27B", "architecture": {"modality": "text+image->text"}},
            {"id": "moonshotai/Kimi-K2.6", "architecture": {"modality": "text+image->text"}},
        ]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        content = body["messages"][0].get("content")
        if isinstance(content, list):            # a vision request
            VISION_SEEN.append(body["model"])
            if body["model"] not in VISION_OK:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"detail":"model does not exist"}')
                return
            self._send({"choices": [{"message": {"content": "WHAT I SEE: a cottage. PROBLEMS: none."}}]})
            return
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
          "vision_enabled": False, "vision_model": "", "reasoning": "LOW", "temperature": 0.4, "max_tokens": 4096,
          "max_steps": 50, "references": [], "blender_version": bpy.app.version_string,
          # Never write test logs into the user's real Documents/BlendWMe Logs folder.
          "log_folder": os.path.join(__import__("tempfile").gettempdir(), "bwm_logs_test")}


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
check("look without vision handled", any(isinstance(r, dict) and "switched off" in r.get("error", "") for r in results))
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

# Fix A1: bwm can be imported like a module, only while a step runs
r = executor.run("import bwm\nfrom bwm import box, material\nbox('ImportedBox', (1, 1, 1), material=material('ImpMat', (1, 0, 0)))")
check("import bwm works", r["ok"] and "ImportedBox" in bpy.data.objects, r)
check("bwm module only exists during a step", "bwm" not in sys.modules)
r = executor.run("from bwm import made_up_helper")
check("unknown bwm import caught before running", r["ok"] is False and "made_up_helper" in r["error"], r)

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

# 7c. Vision: a missing model is replaced automatically; total failure never stops the task
import tempfile  # noqa: E402
from blendwme import look as look_mod, logs  # noqa: E402

# Background Blender has no viewport to draw: look renders the views instead.
engine_before, cam_before, n_before = bpy.context.scene.render.engine, bpy.context.scene.camera, len(bpy.data.objects)
shot = look_mod.capture("angles")
check("look works without a viewport", os.path.isfile(shot.get("path", "")) and shot.get("rendered"), shot)
check("render look leaves the scene as it was", bpy.context.scene.render.engine == engine_before
      and bpy.context.scene.camera == cam_before and len(bpy.data.objects) == n_before)
png = os.path.join(tempfile.gettempdir(), "bwm_fake.png")
img = bpy.data.images.new("fake", 4, 4); img.filepath_raw = png; img.file_format = "PNG"; img.save()
look_mod.capture = lambda *a, **k: {"path": png, "mode": "angles", "views": ["front"], "layout": "x"}
LOGDIR = os.path.join(tempfile.gettempdir(), "bwm_logs_test")
cfg = dict(CONFIG, vision_enabled=True, vision_model="nvidia/Nemotron-3-Nano-Omni", log_folder=LOGDIR)
SCRIPT[:] = [call("look", question="ok?"), call("look", question="again?"), call("finish", summary="seen")]
agent.send("Look at it", cfg, new_task=True)
pump_until(lambda: not agent.is_running())
reports = [json.loads(m["content"]) for m in agent.messages if m["role"] == "tool" and "vision" in m["content"]]
check("404 vision model replaced automatically", reports[0].get("vision_model") == "Qwen/Qwen3.8-27B", reports)
check("nemotron never used for images", "nvidia/Nemotron-3-Nano-Omni" not in VISION_SEEN, VISION_SEEN)
check("second look reuses the found model", reports[1].get("vision_model") == "Qwen/Qwen3.8-27B")
check("task finished normally", session.status == state.DONE)

VISION_OK.clear()
SCRIPT[:] = [call("look", question="ok?"), call("look", question="ok?"),
             call("run_python", title="Keep going", explain="x", code="print('still building')"),
             call("finish", summary="done without eyes")]
agent.send("Look again", dict(cfg, vision_model="Qwen/Qwen3.8-27B"))
pump_until(lambda: not agent.is_running())
check("vision failure does not stop the task", session.status == state.DONE, session.status)
check("work continues after vision failure", any("still building" in m["content"] for m in agent.messages if m["role"] == "tool"))
check("second look skips quickly", any("unavailable this session" in m["content"] for m in agent.messages if m["role"] == "tool"))

# 7d. Pre-check stops invented API names before anything runs
r = executor.run("bpy.ops.mesh.primitive_cube_add()\nslab = bpy.active_object\nslab.name = 'Slab'")
check("bpy.active_object repaired, not rejected", r["ok"] and "Slab" in bpy.data.objects
      and any("bpy.context" in n for n in r.get("auto_repaired", [])), r)
r = executor.run("bpy.ops.mesh.primitive_cube_add()\nx = bpy.not_a_real_thing")
check("unrepairable names still caught before running", "does not exist" in r["error"]
      and not any(o.name.startswith("Cube") for o in bpy.data.objects), r)
r = executor.run("import bmesh\nbm = bmesh.new()\nbm.new((0,0,0))")
check("pre-check catches bm.new", "bm.verts.new" in r["error"], r)

# 7e. Full logs are written automatically
files = sorted(os.listdir(LOGDIR))
check("log files saved", any(f.endswith(".md") for f in files) and any(f.endswith(".json") for f in files), files)
md = open(os.path.join(LOGDIR, [f for f in files if f.endswith(".md")][-1]), encoding="utf-8").read()
check("log has code, results and timeline", "still building" in md and "## Timeline" in md and "```python" in md)
check("log never contains the key", CONFIG["api_key"] == "test" or "test" not in md)
p = logs.save([{"role": "user", "content": "key is sk-SECRET-123456789"}], [], {"title": "x"}, LOGDIR, "sk-SECRET-123456789")
check("key scrubbed from logs", "sk-SECRET" not in open(p, encoding="utf-8").read())

# 7f. Plan checklist, quiet self-correction, legacy API pre-check, agent with its own eyes
SCRIPT[:] = [call("plan", parts=["Base slab", "Walls", "Roof"]),
             call("run_python", title="Base slab", explain="x", part=1, code="bwm.box('P_Base', (4, 3, 0.2), (0, 0, 0.1))"),
             call("run_python", title="Walls", explain="x", part=2, code="bwm.box('P_Walls', (3, 2, 2), (0, 0, 1.2))\nnot_defined()"),
             call("run_python", title="Walls", explain="x", part=2, code="bwm.box('P_Walls', (3, 2, 2), (0, 0, 1.2))"),
             call("finish", summary="ok")]
log_start = len(session.log)
agent.send("Plan test", CONFIG, new_task=True)
pump_until(lambda: not agent.is_running())
run_log = session.log[log_start:]
check("plan shown as checklist", [p["name"] for p in session.plan] == ["Base slab", "Walls", "Roof"])
check("plan ticks as parts are built", [p["done"] for p in session.plan] == [True, True, False], session.plan)
retry_lines = [e for e in run_log if e["who"] == "retry"]
check("failed try shown as quiet self-correction", len(retry_lines) == 1 and "fixed a code error" in retry_lines[0]["text"], retry_lines)
check("no red problem for a fixed step", not any(e["who"] == "error" for e in run_log), run_log)
r = executor.run("o = D.objects.new('L', D.meshes.new('L'))\nbpy.context.scene.objects.link(o)")
check("legacy 2.7 link repaired", r["ok"] and "L" in bpy.context.scene.objects, r)
r = executor.run("import bmesh\nbm = bmesh.new()\nbmesh.ops.create_cone(bm, radius=1, depth=1, segments=4)\n"
                 "me = D.meshes.new('ConeM')\nbm.to_mesh(me)\nprint(round(max(v.co.x for v in me.vertices), 2))")
check("create_cone radius repaired to radius1/radius2", r["ok"] and "1.0" in r.get("printed", ""), r)

SCRIPT[:] = [call("look", question="how is it?"), call("finish", summary="seen myself")]
VISION_SEEN.clear()
agent.send("Look yourself", dict(cfg, model="moonshotai/Kimi-K2.6"), new_task=True)
pump_until(lambda: not agent.is_running())
img_msgs = [m for m in agent.messages if m["role"] == "user" and isinstance(m["content"], list)]
check("vision-capable agent gets the picture itself", len(img_msgs) == 1 and any(p.get("type") == "image_url" for p in img_msgs[0]["content"]))
check("no separate vision model needed", VISION_SEEN == [], VISION_SEEN)
agent._drop_old_images()
check("image kept for the latest look", any(p.get("type") == "image_url" for p in img_msgs[0]["content"]))
md = open(agent.save_log() if agent.config.get("log_folder") else logs.save(agent.messages, agent.trace, {"title": "img"}, LOGDIR), encoding="utf-8").read()
check("logs never contain image data", "base64" not in md)

# 7g. Python sent as tool calls is rescued; unknown tools get a clear answer; hidden collections relink
SCRIPT[:] = [
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "a1", "type": "function", "function": {"name": "bwm.box", "arguments": json.dumps({"name": "R_Base", "size": [4, 3, 0.2], "location": [0, 0, 0.1], "material": "R_Stone", "title": "x"})}},
        {"id": "a2", "type": "function", "function": {"name": "bwm.material", "arguments": json.dumps({"name": "R_Stone", "color": [0.4, 0.4, 0.4]})}},
        {"id": "a3", "type": "function", "function": {"name": "get_scene_info", "arguments": "{}"}}]},
    call("make_house", size=3),
    call("finish", summary="ok")]
agent.send("Rescue test", CONFIG, new_task=True)
pump_until(lambda: not agent.is_running())
check("python-as-tools rescued into one step", "R_Base" in bpy.data.objects and bpy.data.objects["R_Base"].material_slots[0].name == "R_Stone")
answered = {m["tool_call_id"] for m in agent.messages if m["role"] == "tool"}
check("every rescued call answered", {"a1", "a2", "a3"} <= answered)
check("model told to use run_python", any("are Python, not tools" in m["content"] for m in agent.messages if m["role"] == "tool"))
check("unknown tool lists the real tools", any("There is no tool called 'make_house'" in m["content"] and "run_python" in m["content"]
                                               for m in agent.messages if m["role"] == "tool"))
hidden = bpy.data.collections.new("HiddenCottage")   # exists in the file, not in the scene
r = executor.run("c = bwm.collection('HiddenCottage')\nbwm.box('H1', (1, 1, 1), collection=c)")
check("hidden collection relinked so work is visible", "H1" in bpy.context.scene.objects and not r.get("warning"), r)
r = executor.run("o = bwm.box('H2', (1, 1, 1))\nC.scene.collection.objects.unlink(o)")
check("invisible new objects are reported", "NOT in the scene" in r.get("warning", ""), r)
r = executor.run("o = bwm.box('H3', (1,1,1))\no.modifiers.new('SOLIDIFY', 'Solidify')")
check("swapped modifier arguments repaired", r["ok"] and bpy.data.objects["H3"].modifiers[0].type == "SOLIDIFY"
      and any("swapped" in n for n in r["auto_repaired"]), r)

# Fix A2: more mechanical slips are repaired, run, and reported
REPAIRS = [
    ("diffuse_color with 3 values", "m = bwm.material('RepA', (1, 0, 0))\nm.diffuse_color = (0.2, 0.4, 0.6)",
     lambda: tuple(round(v, 1) for v in bpy.data.materials['RepA'].diffuse_color) == (0.2, 0.4, 0.6, 1.0)),
    ("socket colour with 3 values", "m = bwm.material('RepB')\nb = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')\n"
     "b.inputs['Base Color'].default_value = (0.1, 0.2, 0.3)",
     lambda: round(bpy.data.materials['RepB'].node_tree.nodes['Principled BSDF'].inputs[0].default_value[2], 1) == 0.3),
    ("renamed Principled input", "m = bwm.material('RepC')\nb = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')\n"
     "b.inputs['Specular'].default_value = 0.7\nb.inputs['Emission'].default_value = (1, 1, 0, 1)",
     lambda: round(bpy.data.materials['RepC'].node_tree.nodes['Principled BSDF'].inputs['Specular IOR Level'].default_value, 1) == 0.7),
    ("removed 2.7x property skipped", "w = C.scene.world or D.worlds.new('W')\nw.light_settings.use_ambient_occlusion = True\n"
     "bwm.box('AfterSkip', (1, 1, 1))", lambda: "AfterSkip" in bpy.data.objects),
    ("obj.select = True", "o = bwm.box('Sel', (1, 1, 1))\no.select = True", lambda: bpy.data.objects['Sel'].select_get()),
    ("engine id from 4.1", "C.scene.render.engine = 'BLENDER_EEVEE'", lambda: C_scene().render.engine == 'BLENDER_EEVEE_NEXT'),
    ("enum in lower case", "o = bwm.box('Wire', (1, 1, 1))\no.display_type = 'wire'", lambda: bpy.data.objects['Wire'].display_type == 'WIRE'),
    ("one number for a vector", "o = bwm.box('Sc', (1, 1, 1))\no.scale = 2", lambda: tuple(bpy.data.objects['Sc'].scale) == (2, 2, 2)),
    ("operator keywords and name", "bpy.ops.mesh.primitive_cylinder_add(radius=0.2, height=3, segments=12, name='Pole')",
     lambda: "Pole" in bpy.data.objects and round(bpy.data.objects['Pole'].dimensions.z, 1) == 3.0
     and len(bpy.data.objects['Pole'].data.vertices) == 24),
    ("modifier with type only, lower case", "o = bwm.box('ModT', (1, 1, 1))\no.modifiers.new(type='bevel')\no.modifiers.new('Subdivision')",
     lambda: [m.type for m in bpy.data.objects['ModT'].modifiers] == ['BEVEL', 'SUBSURF']),
    ("node by its label", "m = bwm.material('RepN')\nn = m.node_tree.nodes.new('Principled BSDF')\nn2 = m.node_tree.nodes.new('ShaderNodePrincipledBsdf')",
     lambda: sum(1 for n in bpy.data.materials['RepN'].node_tree.nodes if n.type == 'BSDF_PRINCIPLED') == 3),
    ("material_slots.append", "o = bwm.box('Slot', (1, 1, 1))\no.material_slots.append(bwm.material('RepS'))",
     lambda: bpy.data.objects['Slot'].material_slots[0].material.name == 'RepS'),
]


def C_scene():
    return bpy.context.scene


for label, code, ok in REPAIRS:
    r = executor.run(code)
    check("repaired: " + label, r["ok"] and r.get("auto_repaired") and ok(), r)

r = executor.run("m = bwm.material('RepL', (1, 0, 0))\nm.diffuse_color = (1, 1, 1)\nundefined_after_repair()")
check("errors after a repair keep the model's line number", r["ok"] is False and "line 3" in r["error"]
      and "undefined_after_repair" in r["error"], r)
r = executor.run("o = C.scene.objects.get('Nope')\no.location = (1, 2, 3)")
check("real mistakes are not hidden by repairs", r["ok"] is False and "NoneType" in r["error"], r)
r = executor.run("o = bwm.box('RO', (1, 1, 1))\no.dimensions = (2, 2, 2)\nprint('dims', tuple(o.dimensions))")
check("normal property sets are untouched", r["ok"] and "auto_repaired" not in r, r)

# 7h. Values sent as text are converted using the tool schema; compaction never invents a code field
from blendwme.agent import _coerce_types  # noqa: E402
fixed = _coerce_types("plan", {"parts": '["Base", "Walls"]'})
check("array sent as text is converted", fixed["parts"] == ["Base", "Walls"])
fixed = _coerce_types("run_python", {"title": "x", "code": "print(1)", "part": "3"})
check("integer sent as text is converted, code untouched", fixed["part"] == 3 and fixed["code"] == "print(1)")
fixed = _coerce_types("look", {"question": "q", "compare_reference": "True"})
check("boolean sent as text is converted", fixed["compare_reference"] is True)
agent.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                  {"role": "assistant", "content": "", "tool_calls": [{"id": "p1", "type": "function", "function": {
                      "name": "plan", "arguments": json.dumps({"parts": ["x" * 700]})}}]},
                  {"role": "tool", "tool_call_id": "p1", "content": "{}"}] + [{"role": "user", "content": "pad"}] * 20
agent._compact(force=True)
check("compaction never adds a fake code field", "code" not in json.loads(agent.messages[2]["tool_calls"][0]["function"]["arguments"]))

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
