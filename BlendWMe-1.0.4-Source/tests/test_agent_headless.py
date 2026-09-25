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
        reply = dict(reply)
        finish = reply.pop("_finish", "stop")
        self._send({"choices": [{"message": reply, "finish_reason": finish}],
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

# A reply cut off by the length limit in a repetition loop is not kept in full (it primes more of it)
SCRIPT[:] = [{"role": "assistant", "content": "<tool_call>\n" + "<PARAMS>\n" * 3000, "_finish": "length"},
             call("finish", summary="ok")]
agent.send("Cut off", CONFIG)
pump_until(lambda: not agent.is_running())
check("cut-off repetition is trimmed from the history",
      max(len(m.get("content") or "") for m in agent.messages if m["role"] == "assistant") < 1000
      and session.status == state.DONE)

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

# Fix A6: smarter stuck guard
probe = call("run_python", title="Test bwm availability", explain="x", code="print(dir(bwm))\nprint(hasattr(bpy, 'ops'))")
SCRIPT[:] = [probe] * 5 + [call("run_python", title="Real part", explain="x", code="bwm.box('AfterProbe', (1, 1, 1))"),
                          call("finish", summary="ok")]
steps_before = len(session.steps)
agent.send("Probe test", CONFIG)
pump_until(lambda: not agent.is_running())
check("environment test steps are refused, not run", session.status == state.DONE and "AfterProbe" in bpy.data.objects
      and len(session.steps) == steps_before + 1)
check("refused probe gets the facts it was looking for", any("bwm has:" in m["content"] and "cylinder" in m["content"]
                                                              for m in agent.messages if m["role"] == "tool"))

reject = call("run_python", title="Roof", explain="x", code="bpy.ops.mesh.primitive_cube_add()\nr = bpy.not_a_thing")
SCRIPT[:] = [reject] * 5 + [call("run_python", title="Roof", explain="x", code="bwm.box('RoofOK', (1, 1, 1))"),
                           call("finish", summary="ok")]
agent.send("Pre-check test", CONFIG)
pump_until(lambda: not agent.is_running() or session.status == state.PAUSED)
check("pre-check rejections (nothing ran) do not trigger the stuck pause", session.status == state.DONE
      and "RoofOK" in bpy.data.objects, session.status)

fail_a = call("run_python", title="Door", explain="x", code="bwm.box('FA', (1, 1, 1))\nmissing_a()")
fail_b = call("run_python", title="Window", explain="x", code="bwm.box('FB', (1, 1, 1))\nmissing_b()")
SCRIPT[:] = [fail_a, fail_b] * 3 + [call("run_python", title="Door", explain="x", code="bwm.box('DoorOK', (1, 1, 1))"),
                                   call("finish", summary="ok")]
agent.send("Per-step test", CONFIG)
pump_until(lambda: not agent.is_running() or session.status == state.PAUSED)
check("failures are counted per step (3 + 3 different failures do not pause)", session.status == state.DONE, session.status)
check("repeated identical error is named in the hint", any("Same error again" in m["content"]
                                                            for m in agent.messages if m["role"] == "tool"))

SCRIPT[:] = [reject] * 6 + [call("finish", summary="ok")]
agent.send("Endless pre-check loop", CONFIG)
pump_until(lambda: session.status == state.PAUSED)
check("an endless pre-check loop still hands control to the user", "stuck" in session.card["title"].lower())
session.pause_flag.clear(); session.wake()
pump_until(lambda: not agent.is_running())

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

# 7c2. Fix A5: harness-driven vision. References become a spec before building, the art
# director reviews every few steps without being asked, and checks once before finishing.
VISION_OK.add("Qwen/Qwen3.8-27B")
VISION_SEEN.clear()
step = lambda i: call("run_python", title=f"Part {i}", explain="x", code=f"bwm.box('AD_{i}', (1, 1, 1), ({i * 2}, 0, 0.5))")  # noqa: E731
SCRIPT[:] = [step(i) for i in range(1, 7)] + [call("finish", summary="first try"), call("finish", summary="done")]
cfg_ref = dict(cfg, vision_model="Qwen/Qwen3.8-27B", references=[png])
agent.send("Build from my reference", cfg_ref, new_task=True)
pump_until(lambda: not agent.is_running())
users = [m["content"] for m in agent.messages if m["role"] == "user" and isinstance(m["content"], str)]
first_model_turn = next(i for i, m in enumerate(agent.messages) if m["role"] == "assistant")
check("reference turned into a spec before any building",
      any("[Reference spec" in str(m.get("content")) for m in agent.messages[:first_model_turn]))
reviews = [u for u in users if u.startswith("[Art director review")]
check("art director reviews without being asked (after step 1 with a reference, then every 4)",
      [r.split(".")[0] for r in reviews][:2] == ["[Art director review after 1 build steps",
                                                "[Art director review after 5 build steps"], reviews)
gate = [json.loads(m["content"]) for m in agent.messages if m["role"] == "tool" and "not_finished_yet" in m["content"]]
check("final check before the first finish, then finish is accepted",
      len(gate) == 1 and any("before finishing" in r for r in reviews) and session.status == state.DONE)
check("reviews compare against the reference image", len(VISION_SEEN) >= 4)
check("reviews restate the brief (pulls a drifting model back)", all('THE BRIEF IS: "Build from my reference"' in r for r in reviews))

SCRIPT[:] = [step(i) for i in range(11, 14)] + [call("finish", summary="done")]
agent.send("Build without a reference", dict(cfg_ref, references=[]), new_task=True)
pump_until(lambda: not agent.is_running())
users = [m["content"] for m in agent.messages if m["role"] == "user" and isinstance(m["content"], str)]
check("without references the first review comes after 3 steps",
      any(u.startswith("[Art director review after 3 build steps") for u in users)
      and not any("[Reference spec" in u for u in users))
SCRIPT[:] = ([call("plan", parts=["A", "B"])] + [call("run_python", title=f"P{i}", explain="x", part=min(i, 2),
                                                       code=f"bwm.box('CV_{i}', (1, 1, 1), ({i * 2}, 9, 0.5))") for i in range(1, 21)]
             + [call("finish", summary="x"), call("finish", summary="done")])
agent.send("Converge", dict(cfg_ref, references=[]), new_task=True)
pump_until(lambda: not agent.is_running())
users = [m["content"] for m in agent.messages if m["role"] == "user" and isinstance(m["content"], str)]
periodic = [u for u in users if u.startswith("[Art director review after")]
check("critique converges: at most 3 periodic reviews per task", len(periodic) == 3, len(periodic))
check("model told once when every plan part is built", sum(u.startswith("[All 2 plan parts are built") for u in users) == 1)
SCRIPT[:] = [step(i) for i in range(31, 34)] + [call("finish", summary="done")]
agent.send("Build without a reference again", dict(cfg_ref, references=[]), new_task=True)
pump_until(lambda: not agent.is_running())
check("no extra final check when nothing changed since the last review", session.status == state.DONE
      and not any("not_finished_yet" in m["content"] for m in agent.messages if m["role"] == "tool"))

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
md = open(os.path.join(LOGDIR, [f for f in files if f.endswith("look-at-it.md")][-1]), encoding="utf-8").read()
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
        {"id": "a3", "type": "function", "function": {"name": "get_scene_info", "arguments": "{}"}},
        {"id": "a4", "type": "function", "function": {"name": "bwm_collection", "arguments": json.dumps({"name": "RescuedCol"})}}]},
    call("make_house", size=3),
    call("blender_exec", code="bwm.box('ViaBlenderExec', (1, 1, 1))"),
    {"role": "assistant", "content": '\n{"tool": "execute_blender_code", "arguments": {"code": "bwm.box(\'ViaBareJson\', (1, 1, 1))"}}'},
    call("finish", summary="ok")]
agent.send("Rescue test", CONFIG, new_task=True)
pump_until(lambda: not agent.is_running())
check("python-as-tools rescued into one step", "R_Base" in bpy.data.objects and bpy.data.objects["R_Base"].material_slots[0].name == "R_Stone")
answered = {m["tool_call_id"] for m in agent.messages if m["role"] == "tool"}
check("every rescued call answered", {"a1", "a2", "a3", "a4"} <= answered)
check("bwm_name tool calls (underscore for dot) are rescued too", "RescuedCol" in bpy.data.collections)
check("code tools from elsewhere (blender_exec, MCP's execute_blender_code) run as run_python",
      "ViaBlenderExec" in bpy.data.objects and "ViaBareJson" in bpy.data.objects)
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

# Background Blender: transform macros used to crash Blender (borrowed viewport)
r = executor.run("bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1)\nC.active_object.name = 'Macro'\n"
                 "bpy.ops.object.mode_set(mode='EDIT')\nbpy.ops.mesh.select_all(action='SELECT')\n"
                 "bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={'value': (0, 0, 1)})\n"
                 "bpy.ops.object.mode_set(mode='OBJECT')\nbpy.ops.object.duplicate_move()")
check("extrude and duplicate macros run in background Blender", r["ok"] and "Macro" in bpy.data.objects, r)

r = executor.run("m = bwm.material('RepL', (1, 0, 0))\nm.diffuse_color = (1, 1, 1)\nundefined_after_repair()")
check("errors after a repair keep the model's line number", r["ok"] is False and "line 3" in r["error"]
      and "undefined_after_repair" in r["error"], r)
r = executor.run("o = C.scene.objects.get('Nope')\no.location = (1, 2, 3)")
check("real mistakes are not hidden by repairs", r["ok"] is False and "NoneType" in r["error"], r)
r = executor.run("o = bwm.box('RO', (1, 1, 1))\no.dimensions = (2, 2, 2)\nprint('dims', tuple(o.dimensions))")
check("normal property sets are untouched", r["ok"] and "auto_repaired" not in r, r)

# 7h. Values sent as text are converted using the tool schema; compaction never invents a code field
from blendwme.agent import _coerce_types  # noqa: E402
from blendwme.agent import _plan_parts  # noqa: E402
check("plan accepts steps= as a JSON string (live round 1: rejected 15 times)",
      _plan_parts({"title": "x", "steps": '["Ground plane", "Walls"]'}) == ["Ground plane", "Walls"])
check("plan accepts one part per line and dict parts",
      _plan_parts({"parts": "1. Base\n2. Roof"}) == ["Base", "Roof"]
      and _plan_parts({"parts": [{"name": "Body", "size": "2 m"}]}) == ["Body"])
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

# ----------------------------------------------------------------------
# 7i. Fix A3: the bwm modelling kit (shapes, placement, architecture, furniture)
# ----------------------------------------------------------------------
import math  # noqa: E402
from blendwme import helpers as bwm_h, knowledge  # noqa: E402
fresh_scene()
HOUSE = """
col = bwm.collection('Cottage')
bwm.box('Base', (4.4, 3.4, 0.25), (0, 0, 0.125), col, 'stone', bevel=0.03)
walls = bwm.box('Walls', (4, 3, 2.4), (0, 0, 1.45), col, '#e8d6b0')
roof = bwm.gable_roof('Roof', 4, 3, 1.3, (0, 0, 2.65), collection=col, gable_material='#e8d6b0')
ch = bwm.box('Chimney', (0.45, 0.45, 1.2), (1.2, 0.6, 4), col, (150, 60, 50))
bwm.place_on(ch, roof, sink=True)
bwm.door('Door', 0.9, 2.0, (0, -1.4, 0.25), wall=walls)
for i, x in enumerate((-1.3, 1.3)):
    bwm.window(f'Win_F{i}', 0.8, 1.0, (x, -1.2, 1.6), wall=walls)
    bwm.window(f'Win_B{i}', 0.8, 1.0, (x, 1.7, 1.6), wall='Walls')
bwm.path('Path', [(0, -1.9), (0.3, -3.5), (-0.2, -5)])
"""
r = executor.run(HOUSE, "house")
check("kit: cottage builds", r["ok"], r)
D_ = bpy.data
# Roof top surface under the chimney's low (front) edge: 2.65 + 1.3 - (1.3 / 1.5) * 0.825 + slab = 3.43
check("kit: chimney sunk into the roof slope", abs(bwm_h.bounds("Chimney").min.z - 3.43) < 0.03,
      bwm_h.bounds("Chimney"))
check("kit: windows snap onto the nearest face of a solid house body",
      abs(D_.objects["Win_F0"].location.y + 1.5) < 0.01 and abs(D_.objects["Win_B0"].location.y - 1.5) < 0.01)
check("kit: windows face outwards", abs(D_.objects["Win_F0"].rotation_euler.z) < 0.01
      and abs(abs(D_.objects["Win_B0"].rotation_euler.z) - math.pi) < 0.01)
check("kit: door and windows cut live holes", sum(1 for m in D_.objects["Walls"].modifiers if m.type == "BOOLEAN") == 5)
check("kit: gable roof underside meets the wall tops", abs(bwm_h.bounds("Roof_Gables").max.z - 3.95) < 0.01
      and abs(bwm_h.bounds("Roof_Gables").min.z - 2.65) < 0.01)

r = executor.run(HOUSE, "house again")
check("kit: re-running a step replaces the AI's earlier objects",
      r["ok"] and not [o.name for o in D_.objects if ".00" in o.name or ".replaced" in o.name]
      and any("Replaced" in n for n in r.get("helper_notes", [])), r)
r = executor.run(HOUSE, "house a third time")
check("kit: a replaced object is tagged as the AI's, so later re-runs replace it again",
      r["ok"] and D_.objects["Walls"].get("blendwme_author") == "ai"
      and not [o.name for o in D_.objects if ".00" in o.name or ".replaced" in o.name],
      [o.name for o in D_.objects if ".00" in o.name])
D_.objects["Base"]["blendwme_author"] = "user"           # the user takes ownership of the base
r = executor.run("bwm.box('Base', (1, 1, 1))\nbwm.box('Walls', (1, 1, 1))\nraise RuntimeError('late failure')")
check("kit: failed step restores the replaced objects", r["ok"] is False and tuple(round(v, 2) for v in D_.objects["Walls"].dimensions) == (4, 3, 2.4))
r = executor.run("bwm.box('Base', (1, 1, 1))")
check("kit: user objects are never replaced", "Base" in D_.objects and round(D_.objects["Base"].dimensions.x, 1) == 4.4
      and "Base.001" in D_.objects, [o.name for o in D_.objects if o.name.startswith("Base")])

ROOM = """
bwm.floor('Floor', (5, 4), (8, 0, 0))
bwm.wall('Wall_Back', (5.5, 2), (10.5, 2), 2.6)
bwm.wall('Wall_Left', (5.5, -2), (5.5, 2), 2.6)
bwm.bed('Bed', (1.6, 2.0), (6.6, 1, 0), against='Wall_Back')
bwm.desk('Desk', (1.4, 0.7, 0.75), (9.3, 1, 0), against='Wall_Back')
bwm.monitor('Monitor', on='Desk')
bwm.keyboard('Keyboard', on='Desk')
bwm.pc_tower('PC', (10.2, 1.0, 0), against='Wall_Back')
bwm.chair('Chair', at='Desk')
bwm.wardrobe('Closet', (1.2, 0.6, 2.0), (6, -1.0, 0), against='Wall_Left')
bwm.shelf('Shelf', (0.8, 0.3, 1.8), (8.1, 1, 0), against='Wall_Back')
bwm.rug('Rug', (2, 1.4), (8, 0, 0)); bwm.lamp('Lamp', (10.2, -1.5, 0)); bwm.plant('Plant', (7.5, -1.6, 0))
bwm.window('RoomWin', 1.2, 1.0, (8, 2, 1.5), wall='Wall_Back')
"""
r = executor.run(ROOM, "room")
check("kit: room with furniture builds", r["ok"], r)
check("kit: bed headboard against the back wall", abs(bwm_h.bounds("Bed").max.y - 1.915) < 0.03, bwm_h.bounds("Bed"))
check("kit: closet turned to face the room", abs(math.degrees(D_.objects["Closet"].rotation_euler.z) - 90) < 0.5
      and abs(bwm_h.bounds("Closet").min.x - 5.585) < 0.04, bwm_h.bounds("Closet"))
check("kit: monitor stands on the desk", abs(bwm_h.bounds("Monitor").min.z - bwm_h.bounds("Desk").max.z) < 0.02 or
      abs(D_.objects["Monitor"].location.z - 0.75) < 0.01)
check("kit: chair tucked in front of the desk, facing it",
      abs(abs(D_.objects["Chair"].rotation_euler.z - D_.objects["Desk"].rotation_euler.z) - math.pi) < 0.01
      and D_.objects["Chair"].location.y < D_.objects["Desk"].location.y)
check("kit: room window goes through the thin wall", abs(D_.objects["RoomWin"].location.y - 2) < 0.01)
# Live round 2: a window placed right after its wall (same step) stood in the middle of the room
r = executor.run("bwm.wall('FreshWall', (30, 2), (35, 2), 2.7)\n"
                 "bwm.window('FreshWin', 1.2, 1.1, (32, 2.0, 1.65), wall='FreshWall')\n"
                 "bwm.wall('FreshSide', (30, -2), (30, 2), 2.7)\nbwm.shelf('FreshShelf', (0.8, 0.3, 1.6), (30.5, 0, 0), against='FreshSide')")
check("kit: window right after its wall sits in the wall", r["ok"] and abs(D_.objects["FreshWin"].location.y - 2) < 0.01
      and abs(D_.objects["FreshWin"].location.x - 32) < 0.01, tuple(D_.objects["FreshWin"].location))
check("kit: against= a wall made in the same step", abs(bwm_h.bounds("FreshShelf").min.x - 30.085) < 0.03, bwm_h.bounds("FreshShelf"))

SHAPES = """
c = bwm.cone('IceCone', 0.35, 0.0, 1.2, (-6, 0, 0.6), axis='-Z', material=(0.8, 0.55, 0.25))
s = bwm.sphere('Scoop', 0.4, ('-6', 0, 1.3), material='pink')
bwm.place_on(bwm.sphere('Scoop2', (0.36, 0.36, 0.32), (-6, 0, 3)), s, sink=True)
bwm.lathe('Vase', [(0, 0), (0.3, 0.05), (0.35, 0.4), (0.15, 0.8), (0.2, 1.0)], (-6, 3, 0))
bwm.torus('Ring', 0.5, 0.1, (-6, -3, 0.1))
bwm.tube('Pipe', [(-7, -5, 0.2), (-6, -5, 1), (-5, -5, 0.4)], [0.08, 0.05, 0.03])
bwm.limb('Arm', (-7, 5, 1), (-5, 5, 1), 0.12, 0.06, bend=(0, -0.3, 0))
b = bwm.loft('Body', [(0, 0.0), (0.2, 0.4, 0.3), (0.8, 0.5, 0.35), (1.4, 0.3), (1.6, 0)], (-9, 0, 0), subdivide=1)
bwm.mirror(b); bwm.bevel('Ring', 0.01); bwm.array('Pipe', 2, (0, 1, 0)); bwm.solidify('Ring')
bwm.extrude_profile('Ell', [(0, 0), (1, 0), (1, 0.3), (0.3, 0.3), (0.3, 1), (0, 1)], 0.5, (-9, 4, 0))
bwm.prism('Prism', (2, 1, 1), (-9, -4, 0.5)); bwm.wedge('Ramp', (1, 2, 0.5), (-9, -6, 0.25))
g = bwm.group('Treats', 'IceCone', 'Scoop', 'Scoop2'); bwm.rotate(g, 10)
bwm.duplicate('IceCone', 'Cone2', offset=(0, -1.5, 0))
bwm.box('Tolerant', '1, 2, 3', ['0', '0', '1.5'], material='#ff0000')
bwm.cylinder('Cyl', '0.5', '2', (3, 3, 1), axis='x')
"""
r = executor.run(SHAPES, "shapes")
check("kit: every shape builds", r["ok"], r)
cb = bwm_h.bounds("IceCone")
check("kit: cone point-down with real size", abs(cb.size.z - 1.2) < 0.01 and abs(cb.size.x - 0.7) < 0.02)
check("kit: lathe profile becomes a closed body", D_.objects["Vase"].data.polygons and abs(bwm_h.bounds("Vase").size.z - 1.0) < 0.01)
check("kit: tolerant arguments", tuple(D_.objects["Tolerant"].dimensions) == (1, 2, 3)
      and D_.objects["Tolerant"].material_slots[0].material.diffuse_color[0] > 0.99
      and abs(D_.objects["Cyl"].dimensions.x - 2) < 0.01)
check("kit: all meshes valid (no loose or broken geometry)",
      all(not o.data.validate(verbose=False) for o in D_.objects if o.type == "MESH"))
# Live round 1: calls Nemotron really made, which must now work (with a note, not an error)
r = executor.run("col = bwm.collection('Live')\nstem = bwm.material('Stem', (0.2, 0.4, 0.1))\n"
                 "bwm.cylinder('Stem', 0.002, 0.015, (0, 0, 0.342), col, stem, segments=6)")
check("live: name, sizes, location, collection, material works for every shape", r["ok"] and "Stem" in D_.objects, r)
r = executor.run("bwm.box('LiveWall', (4, 3, 2.4), (20, 0, 1.2))\n"
                 "bwm.window('LiveWin', 0.8, 1.0, (20, -1.5, 1.5), wall='LiveWall', material=(1, 1, 1))\n"
                 "bwm.duplicate('Stem', 'Stem2', collection='Live')\nbwm.box('Red', (1, 1, 1), (25, 0, 0.5), color='red')\n"
                 "bwm.gable_roof('LiveRoof', 4, 3, height=1.2, location=(20, 0, 2.4))")
check("live: alias arguments are read, unknown extras ignored with a note", r["ok"]
      and any("frame_material" in n for n in r.get("helper_notes", []))
      and any("ignored unknown argument 'collection'" in n for n in r.get("helper_notes", []))
      and D_.objects["Red"].material_slots[0].material.diffuse_color[0] > 0.5
      and abs(bwm_h.bounds("LiveRoof_Gables").size.z - 1.2) < 0.01, r)
from blendwme import prompts as prompts_mod, tools as tools_mod  # noqa: E402
schema_text = json.dumps(tools_mod.ALL) + prompts_mod.CORE
check("tool schemas and core prompt carry no subject the model could copy",
      not any(w in schema_text for w in ("Stone base", "Cottage", "Gable roof", "four windows", "Walls 4 x 3")))
info = knowledge.inspect_api("bwm")
check("kit: inspect_api lists the new helpers", all(f"bwm.{n}" in " ".join(info["bwm_helpers"]) for n in ("cylinder", "gable_roof", "bed", "place_on")))
check("kit: inspect_api gives full help for one helper", "WALL TOP" in str(knowledge.inspect_api("bwm.gable_roof")).upper()
      or "top of the walls" in str(knowledge.inspect_api("bwm.gable_roof")))

# ----------------------------------------------------------------------
# 7j. Fix A4: worked example cards. A card is only kept if every step runs cleanly.
# ----------------------------------------------------------------------
import re  # noqa: E402
EXPECT = {"example-cottage": ["Ground", "Base", "Walls", "Roof", "Roof_Tiles", "Chimney", "Door", "Window_Left", "Path"],
          "example-room": ["Floor", "Wall_Back", "Wall_Left", "Bed", "Desk", "Monitor", "Chair", "Closet"],
          "example-ice-cream-cone": ["Cone", "Cone_Waffle", "Scoop_1", "Scoop_2", "Cherry", "Plate"]}
def floating_objects(tolerance=0.02):
    """Top-level visible meshes whose bottom touches neither the ground nor anything below them."""
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.hide_render and o.visible_get()]
    boxes = {o.name: bwm_h.bounds(o, children=False) for o in meshes}
    out = []
    for o in meshes:
        b = boxes[o.name]
        if b.min.z <= tolerance or o.parent is not None:
            continue
        supported = any(n != o.name and c.min.z - tolerance <= b.min.z <= c.max.z + tolerance
                        and c.min.x < b.max.x and c.max.x > b.min.x and c.min.y < b.max.y and c.max.y > b.min.y
                        for n, c in boxes.items())
        if not supported:
            out.append(o.name)
    return out


examples = [n for n, _ in knowledge.skill_index() if n.startswith("example-")]
check("example cards exist for the Stage 0 briefs", set(EXPECT) <= set(examples), examples)
for card in examples:
    fresh_scene()
    text = knowledge.load_skill(card)
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    results = [executor.run(code, f"{card} step {i + 1}") for i, code in enumerate(blocks)]
    bad = [(i + 1, r.get("error")) for i, r in enumerate(results) if not r["ok"]]
    check(f"{card}: all {len(blocks)} steps run cleanly", blocks and not bad, bad)
    repaired = [(i + 1, r["auto_repaired"]) for i, r in enumerate(results) if r.get("auto_repaired")]
    check(f"{card}: needs no auto-repair (it is the model's example)", not repaired, repaired)
    missing = [n for n in EXPECT.get(card, []) if n not in bpy.data.objects]
    check(f"{card}: builds every named part", not missing, missing)
    check(f"{card}: nothing floats", not floating_objects(), floating_objects())

# Blender's untouched default cube is flagged so the model can clear it; a moved one is the user's.
from blendwme import scene_info as si  # noqa: E402
bpy.ops.wm.read_factory_settings(use_empty=False)
check("default startup cube flagged", "default startup cube" in si.get_scene_info().get("default_startup_cube", ""))
bpy.data.objects["Cube"].location.x = 1.0
check("an edited cube is the user's, not flagged", "default_startup_cube" not in si.get_scene_info())

# 7k. Fix A7: inspect_api covers bmesh and mathutils
r = knowledge.inspect_api("bmesh")
check("inspect bmesh module", r.get("module") == "bmesh" and "ops" in r["members"] and "no bm.ops" in r["usage"], r)
r = knowledge.inspect_api("bmesh.ops")
check("inspect bmesh.ops lists the ops", "create_cone" in r.get("members", {}) and "extrude_face_region" in r["members"])
r = knowledge.inspect_api("bmesh.ops.create_cone")
check("inspect one bmesh op gives its arguments", {"radius1", "radius2", "depth", "segments"} <= set(r.get("arguments", {})), r)
r = knowledge.inspect_api("bmesh.types.BMVert")
check("inspect bmesh.types class", r.get("class") == "bmesh.types.BMVert" and "co" in r["members"], r)
check("inspect BMesh by its bare name", "verts" in knowledge.inspect_api("BMesh").get("members", {}))
r = knowledge.inspect_api("mathutils.Vector")
check("inspect mathutils.Vector", "normalized" in r.get("members", {}) and "cross" in r["members"], r)
check("inspect Vector by its bare name", "lerp" in knowledge.inspect_api("Vector").get("members", {}))
check("inspect mathutils.geometry", "intersect_line_line" in knowledge.inspect_api("mathutils.geometry").get("members", {}))
r = knowledge.inspect_api("bmesh.ops.create_cylinder")
check("unknown bmesh op suggests real ones", "create_circle" in r.get("did_you_mean", []) or "create_cone" in r.get("did_you_mean", []), r)
check("bpy types still inspected", knowledge.inspect_api("BevelModifier").get("type") == "BevelModifier")

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
