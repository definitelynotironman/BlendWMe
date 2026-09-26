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
from blendwme import bridge, mcp_probe, prefs, scene_info, socket_bridge, state, tool_host, workspace  # noqa: E402
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

# ----------------------------------------------------------------------
# M2: the engine layer and the claude_code adapter (a fake `claude` program)
# ----------------------------------------------------------------------
from blendwme.engines import limits  # noqa: E402
from blendwme.engines.claude_code import ClaudeCodeEngine  # noqa: E402
from blendwme.relay import relay  # noqa: E402

FAKE = os.path.join(HERE, "fake_brain.py")
CFG = {"brain": "claude", "references": [], "auto_save_logs": False, "blender_version": bpy.app.version_string,
       "log_folder": os.path.join(tempfile.gettempdir(), "bwm_logs_test")}


def script(*turns):
    with open(os.path.join(WORKSPACE, "fake_script.json"), "w", encoding="utf-8") as f:
        json.dump(list(turns), f)


def runs():
    path = os.path.join(WORKSPACE, "fake_runs.json")
    # The fake brain may be writing the file right now: try again on a half-written file.
    for _ in range(50):
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            time.sleep(0.02)
    raise AssertionError("fake_runs.json stayed unreadable")


def turn(text, brain="claude", **extra):
    relay.send(text, dict(CFG, brain=brain, **extra))
    pump_until(lambda: not relay.is_running(), 90)


def after(flag):
    argv = runs()[-1]["argv"]
    return argv[argv.index(flag) + 1] if flag in argv else None


def ai_texts(brain=None):
    return [e["text"] for e in session.log if e["who"] == "ai" and (brain is None or e.get("brain") == brain)]


fresh_scene()
reset_session()
relay.reset()
relay.engines["claude"].command_override = [sys.executable, FAKE]
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-never-forwarded"
script([{"say": "I will build a small table."},
        {"call": "plan", "args": {"parts": ["Top 1 x 1 x 0.1 m on the ground"]}},
        {"call": "run_python", "args": {"title": "Table top", "explain": "The top", "part": 1,
                                        "code": "bwm.box('TableTop', (1, 1, 0.1), (0, 0, 0.75))"}},
        {"say": "The table top is in place."},
        {"call": "finish", "args": {"summary": "A table top."}},
        {"call": "finish", "args": {"summary": "A table top."}}])
turn("Build a small table")
run = runs()[-1]
argv = run["argv"]
check("panel prompt reaches Claude in print mode", argv[0] == "-p" and argv[1].startswith("[BlendWMe] Mode: AUTO")
      and "[Message from the user]\nBuild a small table" in argv[1], argv[1][:200])
check("the first message carries the scene facts", "[Current scene]" in argv[1])
check("Claude streams JSON events with partial text", after("--output-format") == "stream-json"
      and "--verbose" in argv and "--include-partial-messages" in argv)
check("Claude gets BlendWMe's MCP server only", after("--mcp-config").endswith("mcp-claude.json")
      and "--strict-mcp-config" in argv)
check("no built-in tools (no Bash, no file edits)", after("--tools") == "")
check("only BlendWMe's tools are allowed, with no prompts", after("--allowedTools") == "mcp__blendwme"
      and after("--permission-mode") == "dontAsk")
check("never --bare (it ignores the subscription sign-in)", "--bare" not in argv)
first_session = after("--session-id")
check("the first turn names its own session", first_session and len(first_session) == 36)
check("Claude runs in the BlendWMe workspace", os.path.normcase(run["cwd"]) == os.path.normcase(WORKSPACE))
check("pay-per-token keys and Blender's Python settings are not passed on",
      run["env"]["ANTHROPIC_API_KEY"] is False and run["env"]["PYTHONPATH"] is False)
check("long tool waits are allowed (MCP_TOOL_TIMEOUT)", run["env"]["MCP_TOOL_TIMEOUT"] is True)
check("Claude built through BlendWMe's tools", "TableTop" in bpy.data.objects
      and bpy.data.objects["TableTop"].get("blendwme_brain") == "claude")
check("streamed text becomes one reply in the panel", ai_texts("claude")[:2]
      == ["I will build a small table.", "The table top is in place."], ai_texts())
check("the plan checklist works with Claude", session.plan and session.plan[0]["done"])
check("finish ends the task", session.status == state.DONE, session.status)

# Second message: the same Claude conversation, plus what the user changed in between.
bpy.data.objects["TableTop"].location.x = 2.0
session.mode = "WATCH"
script([{"call": "get_scene_info"}, {"say": "Noted."}])
turn("Make it taller")
argv = runs()[-1]["argv"]
check("follow-ups resume the same Claude session", after("--resume") == first_session and "--session-id" not in argv)
check("follow-ups skip the scene dump", "[Current scene]" not in argv[1])
check("edits between messages are reported to the brain",
      "changed the scene since your last turn" in argv[1] and "TableTop" in argv[1], argv[1][:400])
check("the mode header follows the panel", "Mode: WATCH" in argv[1])
session.mode = "AUTO"

# A message typed while Claude works arrives with its next tool result.
script([{"sleep": 1.5}, {"call": "get_scene_info"}, {"say": "ok"}])
relay.send("Add a lamp", dict(CFG))
pump_until(lambda: relay.engine("claude").proc is not None and relay.is_running())
relay.send("Also make it red", dict(CFG))
pump_until(lambda: not relay.is_running(), 60)
check("typing while it works steers it through the next tool result",
      any(m["role"] == "tool" and "Also make it red" in m["content"] for m in relay.transcript))

# ... and one typed after its last tool call becomes the next turn.
before = session.tool_steps
script([{"call": "get_scene_info"}, {"sleep": 1.5}, {"say": "done"}], [{"say": "Got your late message."}])
count = len(runs())
relay.send("Look at it", dict(CFG))
pump_until(lambda: session.tool_steps > before)
relay.send("One more thing: a lamp", dict(CFG))
pump_until(lambda: len(runs()) == count + 2 and not relay.is_running(), 60)
check("a late message is sent as the next turn", "One more thing: a lamp" in runs()[-1]["argv"][1]
      and "Got your late message." in ai_texts())

# Usage limits become limit_reached, never a crash.
script([{"say": "Working"}, {"line": {"type": "result", "subtype": "success", "is_error": False,
                                      "result": "Claude AI usage limit reached|1760000000"}}])
turn("Continue")
check("Claude's limit message becomes limit_reached", relay.limit and relay.limit["brain"] == "claude"
      and relay.limit["resets"].startswith("resets"), relay.limit)
check("the panel says the limit is reached and nothing is lost", "limit is reached" in session.card["title"]
      and "Nothing is lost" in session.card["text"] and session.card["kind"] == "limit"
      and session.status == state.IDLE)
engine = ClaudeCodeEngine()
check("a rejected rate_limit_event is a limit too", engine.parse({"type": "rate_limit_event", "rate_limit_info": {
    "status": "rejected", "resetsAt": 1760000000}})[0]["type"] == "limit_reached")
check("limit wording: 'hit your limit · resets 5pm'", limits.is_limit("You've hit your limit · resets 5pm")
      and limits.reset_hint("You've hit your limit · resets 5pm") == "resets 5pm")
check("limit wording: Codex 'usage limit ... try again in'",
      limits.is_limit("You've hit your usage limit. Upgrade to Pro or try again in 2 hours 13 minutes.")
      and "try again in 2 hours" in limits.reset_hint("try again in 2 hours 13 minutes."))
check("normal replies are never mistaken for a limit", not limits.is_limit("The roof is done.")
      and not limits.is_limit("the usage limit of a modifier " * 200))

# A message sent while the last turn's thread is still ending waits for it instead of failing.
ending = threading.Thread(target=time.sleep, args=(0.4,))
ending.start()
relay.engines["claude"].thread = ending
script([{"say": "Picked up right away."}])
turn("Next one")
check("a message right after a turn ends is not refused", session.status != state.ERROR
      and "Picked up right away." in ai_texts(), session.last_error)

# The program fails: a clear error, the scene stays.
script([{"stderr": "boom: something broke"}, {"exit": 1}])
turn("Continue")
check("a crashed brain becomes an error with its message", session.status == state.ERROR
      and "exit code 1" in session.last_error and "boom" in session.last_error, session.last_error)

# Stop kills the program at once.
script([{"sleep": 60}])
relay.send("Build a castle", dict(CFG))
pump_until(lambda: relay.engine("claude").proc is not None and relay.is_running())
t0 = time.time()
relay.stop()
pump_until(lambda: not relay.is_running(), 15)
check("Stop ends the brain's program quickly", time.time() - t0 < 10 and session.status == state.IDLE)

# A lost saved conversation: start fresh once, with the full context.
script([{"stderr": "No conversation found with session ID: x"}, {"exit": 1}], [{"say": "Fresh start."}])
count = len(runs())
relay.send("Continue", dict(CFG))
pump_until(lambda: len(runs()) == count + 2 and not relay.is_running(), 60)
argv = runs()[-1]["argv"]
check("a lost session is restarted fresh with the scene", "--session-id" in argv and "[Current scene]" in argv[1]
      and "Fresh start." in ai_texts() and session.status != state.ERROR)

# 2.1: only Claude and Codex ship. There is no pay-per-token brain and no API key anywhere.
check("only Claude and Codex are brains", sorted(relay.engines) == ["claude", "codex"])
check("no setting asks for an API key",
      not any("key" in p.identifier or "api" in p.identifier
              for p in prefs.BWM_Preferences.bl_rna.properties if p.identifier != "rna_type"))
relay.reset()
del os.environ["ANTHROPIC_API_KEY"]

# ----------------------------------------------------------------------
# M3: the journal, the codex adapter and the relay (fake `claude` and fake `codex`)
# ----------------------------------------------------------------------
import blendwme.journal as journal_module  # noqa: E402
from blendwme.engines.codex import CodexEngine  # noqa: E402
from blendwme.journal import Journal, journal  # noqa: E402


def section_text(text, title):
    """The lines of one section of a handoff brief."""
    if title + ":" not in text:
        return ""
    rest = text.split(title + ":", 1)[1]
    return rest.split("\n\n", 1)[0]


fresh_scene()
reset_session()
relay.reset()
journal.reset()
relay.engines["codex"].command_override = [sys.executable, FAKE]
os.environ["OPENAI_API_KEY"] = "sk-never-forwarded"

# 1. Claude builds part of a scene.
script([{"call": "plan", "args": {"parts": ["Base 2 x 2 x 0.2 m on the ground", "Tower 0.5 x 0.5 x 2 m on Base",
                                            "Flag 0.4 x 0.02 x 0.3 m on Tower"]}},
        {"call": "run_python", "args": {"title": "Base", "part": 1,
                                        "code": "bwm.box('Base', (2, 2, 0.2), (0, 0, 0.1))"}},
        {"say": "The base is done."}])
turn("Build a small tower with a flag")
claude_session = relay.engines["claude"].session_id
check("the journal records the brief and Claude's signed step", journal.data["brief"].startswith("Build a small tower")
      and any(e["kind"] == "step" and e["by"] == "claude" and e["title"] == "Base" for e in journal.data["events"]))

# 2. The user presses "Hand over to Codex": Claude writes a note, Codex continues from the journal.
count = len(runs())
script([{"call": "handoff_note", "args": {"summary": "Built the base.", "next_steps": ["Build the tower",
                                                                                     "Add the flag"]}}],
       [{"call": "look", "args": {"mode": "angles"}},
        {"call": "run_python", "args": {"title": "Tower", "part": 2,
                                        "code": "bwm.box('Tower', (0.5, 0.5, 2), (0, 0, 1.2))"}},
        {"call": "run_python", "args": {"title": "Flag", "part": 3,
                                        "code": "bwm.box('Flag', (0.4, 0.02, 0.3), (0.2, 0, 2.4))\nflag_cloth_sim()"}},
        {"say": "The tower stands. The flag failed."}])
relay.hand_over("codex", dict(CFG))
pump_until(lambda: len(runs()) == count + 2 and not relay.is_running(), 120)
note_run, codex_run = runs()[-2], runs()[-1]
check("the current brain is asked for a handoff note first", note_run["argv"][0] == "-p"
      and "handing this project to Codex" in note_run["argv"][1])
check("the note is saved in the journal", journal.data["handoffs"][-1]["by"] == "claude"
      and journal.data["handoffs"][-1]["next_steps"] == ["Build the tower", "Add the flag"])
argv = codex_run["argv"]
check("Codex runs non-interactively with JSON events", argv[:2] == ["exec", "--json"]
      and "--skip-git-repo-check" in argv and argv[argv.index("--cd") + 1] == WORKSPACE)
check("Codex runs in a read-only sandbox", argv[argv.index("--sandbox") + 1] == "read-only")
overrides = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
check("BlendWMe's MCP server is added for this run only (-c), signed for codex",
      any(o.startswith("mcp_servers.blendwme.command=") for o in overrides)
      and any(o.startswith("mcp_servers.blendwme.args=") and "'codex'" in o for o in overrides), overrides)
check("Codex's tool calls may wait for the user (tool_timeout_sec)",
      "mcp_servers.blendwme.tool_timeout_sec=3600" in overrides)
check("the message goes on stdin (no command-line limits)", argv[-1] == "-" and "resume" not in argv)
brief = codex_run["stdin"]
check("Codex gets the handoff brief with the brief, the note and Claude's work",
      "HANDOFF BRIEF" in brief and "THE BRIEF:\nBuild a small tower" in brief
      and "From Claude: Built the base." in brief and 'Claude: step "Base" -> made Base' in brief, brief[:600])
check("Codex gets fresh scene facts", "[Current scene]" in brief and "Base" in brief)
check("pay-per-token OpenAI keys are not passed to Codex", codex_run["env"]["OPENAI_API_KEY"] is False)
check("Codex builds through the same tools, signed as Codex", "Tower" in bpy.data.objects
      and bpy.data.objects["Tower"].get("blendwme_brain") == "codex")
check("Codex's failed step is rolled back and becomes an open struggle", "Flag" not in bpy.data.objects
      and any(s["open"] and s["by"] == "codex" and "Flag" in s["text"] for s in journal.data["struggles"]))
check("Codex's reply shows in the panel as Codex", "The tower stands. The flag failed." in ai_texts("codex"))
check("the journal keeps Codex's own session", journal.data["sessions"].get("codex") == "fake-thread-1"
      and journal.data["active"] == "codex")

# 3. Claude takes back over: it knows exactly what Codex did, and fixes Codex's struggle first.
count = len(runs())
script([{"call": "handoff_note", "args": {"summary": "Built the tower.",
                                          "struggles": ["The flag step called a function that does not exist"]}}],
       [{"call": "read_journal", "args": {}},
        {"call": "run_python", "args": {"title": "Flag", "part": 3,
                                        "code": "bwm.box('Flag', (0.4, 0.02, 0.3), (0.2, 0, 2.4))"}},
        {"say": "Fixed the flag first."}])
relay.hand_over("claude", dict(CFG))
pump_until(lambda: len(runs()) == count + 2 and not relay.is_running(), 120)
note_run, claude_run = runs()[-2], runs()[-1]
check("Codex is asked for its note in its own session", note_run["argv"][-3:] == ["resume", "fake-thread-1", "-"])
argv = claude_run["argv"]
check("Claude comes back to its own conversation", argv[argv.index("--resume") + 1] == claude_session)
prompt = argv[1]
changes = section_text(prompt, "WHAT CHANGED SINCE YOU LAST WORKED HERE")
check("Claude is told what changed since it last worked here",
      'Codex: step "Tower" -> made Tower' in changes and 'Codex: step "Flag" FAILED' in changes, prompt[:1500])
check("Claude's own earlier steps are not repeated as changes", 'Claude: step "Base"' not in changes)
check("Codex's open struggles come first, to be fixed", "[Codex]" in section_text(prompt, "OPEN STRUGGLES (fix these first)"))
check("Codex's note reaches Claude", "From Codex: Built the tower." in prompt)
makers = section_text(prompt, "WHO MADE EACH OBJECT")
check("Claude knows who made each object", "Codex: Tower" in makers and "Claude: Base" in makers, makers)
check("the rules say: build on the other brain's work, never undo it", "never undo" in prompt)
check("read_journal gives a brain the brief on demand",
      any(m["role"] == "tool" and "HANDOFF BRIEF" in m["content"] for m in relay.transcript))
check("Claude did not undo Codex's work", "Tower" in bpy.data.objects
      and bpy.data.objects["Tower"].get("blendwme_brain") == "codex")
check("Claude fixed Codex's struggle", "Flag" in bpy.data.objects
      and not any(s["open"] and s["key"] == "part 3" for s in journal.data["struggles"]))
check("the plan shows which brain built each part", [p.get("by") for p in journal.data["plan"]]
      == ["claude", "codex", "claude"], journal.data["plan"])
check("every switch is in the journal", [e["to"] for e in journal.data["events"] if e["kind"] == "switch"]
      == ["codex", "claude"])

# 4. The journal travels with the .blend (Text block + sidecar) and the brains resume after reopening.
journal_module.register()
journal_module.save()
check("the journal is mirrored into a Text block", journal_module.TEXT_NAME in bpy.data.texts
      and json.loads(bpy.data.texts[journal_module.TEXT_NAME].as_string())["seq"] == journal.data["seq"])
blend = os.path.join(tempfile.mkdtemp(prefix="bwm_blend_"), "tower.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
check("saving the .blend writes the sidecar journal", os.path.isfile(journal_module.sidecar_path(blend)))
seq = journal.data["seq"]
journal.reset()
bpy.ops.wm.open_mainfile(filepath=blend)
check("opening the .blend loads its journal", journal.data["seq"] == seq and journal.data["brief"].startswith("Build"))
check("each brain resumes its own session after reopening", relay.engines["claude"].session_id == claude_session
      and relay.engines["codex"].session_id == "fake-thread-1" and relay.has_conversation())
check("the plan checklist comes back with the file", len(session.plan) == 3 and all(p["done"] for p in session.plan))
script([{"say": "Back in this project."}])
turn("Add a door")
argv = runs()[-1]["argv"]
check("after reopening, the first message carries the handoff brief and the scene",
      argv[argv.index("--resume") + 1] == claude_session and "HANDOFF BRIEF" in argv[1] and "[Current scene]" in argv[1])
journal_module.unregister()

# 5. A usage limit in Auto mode: the other brain takes over by itself (no note: the limited brain cannot answer).
session.mode = "AUTO"
count = len(runs())
script([{"line": {"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "result": "You've hit your limit · resets 5pm"}}],
       [{"say": "Codex here, continuing."}])
relay.send("Keep going", dict(CFG, auto_relay=True))
pump_until(lambda: len(runs()) == count + 2 and not relay.is_running(), 60)
check("Auto mode: at Claude's limit Codex takes over by itself, without asking Claude for a note",
      runs()[-1]["argv"][0] == "exec" and "Codex here, continuing." in ai_texts("codex"))
check("the switch reason is in the journal", [e for e in journal.data["events"] if e["kind"] == "switch"][-1]["text"]
      == "usage limit reached")
check("Codex continues its own session", runs()[-1]["argv"][-3:-1] == ["resume", "fake-thread-1"])

# 6. In Watch mode the user is asked instead.
session.mode = "WATCH"
count = len(runs())
script([{"line": {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again in 3 hours."}}}])
relay.send("Show me the next step", dict(CFG, brain="codex", auto_relay=True))
pump_until(lambda: not relay.is_running(), 60)
time.sleep(0.5)
check("Watch mode: a limit asks before handing over", len(runs()) == count + 1 and relay.limit
      and relay.limit["brain"] == "codex" and session.card["target"] == "claude"
      and "Claude can carry on" in session.card["text"])
check("Codex's limit wording is understood", "Try again in 3 hours" in relay.limit["resets"], relay.limit)
session.mode = "AUTO"

# 7. Codex events, one at a time.
engine = CodexEngine()
check("a Codex 'error' event that is not a limit does not end the turn (Codex retries)",
      engine.parse({"type": "error", "message": "Reconnecting... 1/5"}) == [])
check("a Codex usage-limit error ends the turn as limit_reached",
      engine.parse({"type": "error", "message": "You've hit your usage limit."})[0]["type"] == "limit_reached")
check("a failed Codex turn is an error", engine.parse({"type": "turn.failed", "error": {"message": "x"}})[0]["type"]
      == "error")
check("Codex tool calls are standard events", engine.parse({"type": "item.started", "item": {
    "id": "1", "type": "mcp_tool_call", "server": "blendwme", "tool": "run_python", "arguments": {"code": "x"}}})[0]
      == {"type": "tool_call", "id": "1", "tool": "run_python", "args": {"code": "x"}})

# 8. The handoff brief always fits its size limit, keeping what matters most.
big = Journal()
big.record_task("A huge castle")
for i in range(300):
    big.record_step("codex", f"Part {i}", "", "x = 1", True, added=[f"Part{i}"])
big.record_struggle("codex", "part 7", "Part 7: the gate keeps failing")
text = big.handoff_brief("claude", {f"Part{i}": "codex" for i in range(300)})
check("a long project still gives a brief within the limit", len(text) <= journal_module.BRIEF_LIMIT
      and "older lines left out" in text and "THE BRIEF:\nA huge castle" in text
      and "the gate keeps failing" in text and "RULES" in text, len(text))

# 9. Settings: no hand-over card when it is switched off; the brief without "review first".
session.mode = "WATCH"
script([{"line": {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}}])
relay.send("Next", dict(CFG, brain="codex", offer_handover=False))
pump_until(lambda: not relay.is_running(), 60)
time.sleep(0.3)
check("'Offer a hand-over' off: the limit is said, but no card", relay.limit and session.card is None
      and any("limit is reached" in e["text"] for e in session.log if e["who"] == "error"))
plain = big.handoff_brief("claude", {}, review_first=False)
check("'Returning brain reviews first' off: the brief has no review-first rule",
      "Call look (mode angles) first" not in plain and "never undo" in plain)
session.mode = "AUTO"
relay.reset()
del os.environ["OPENAI_API_KEY"]

# ----------------------------------------------------------------------
# M4: the setup checks (a fake program that answers the status commands)
# ----------------------------------------------------------------------
from blendwme import setup_checks  # noqa: E402

STATUS = os.path.join(HERE, "fake_status.py")
fake_dir = tempfile.mkdtemp(prefix="bwm_fake_programs_")
if os.name == "nt":
    fake_program = os.path.join(fake_dir, "fakeprog.cmd")
    with open(fake_program, "w", encoding="utf-8") as f:
        f.write(f'@"{sys.executable}" "{STATUS}" %*\n')
else:
    fake_program = os.path.join(fake_dir, "fakeprog")
    with open(fake_program, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{STATUS}" "$@"\n')
    os.chmod(fake_program, 0o755)


def status_check(fn, key, **fake):
    os.environ["BWM_FAKE_STATUS"] = json.dumps(fake)
    return fn({key: fake_program})


SIGNED_IN = {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro", "email": "someone@example.com"}
level, message = status_check(setup_checks.check_claude, "claude_path", version="2.1.302 (Claude Code)", auth=SIGNED_IN)
check("Claude check: installed and signed in with the subscription", level == setup_checks.OK
      and "signed in with your Claude Pro subscription" in message, message)
check("the setup check never shows the account e-mail", "example.com" not in message)
level, message = status_check(setup_checks.check_claude, "claude_path", auth={"loggedIn": False})
check("Claude check: not signed in, with what to do", level == setup_checks.ERROR and "run `claude`" in message)
level, message = status_check(setup_checks.check_claude, "claude_path",
                              auth={"loggedIn": True, "authMethod": "api_key"})
check("Claude check: an API-key sign-in is flagged (pay per token)", level == setup_checks.WARN
      and "not your Claude subscription" in message, message)
level, message = status_check(setup_checks.check_claude, "claude_path", help="-p --print", auth=SIGNED_IN)
check("Claude check: a missing flag is named", level == setup_checks.WARN and "--mcp-config" in message, message)
saved_find = setup_checks.claude_code.find_program
setup_checks.claude_code.find_program = lambda configured="": ""
level, message = setup_checks.check_claude({})
setup_checks.claude_code.find_program = saved_find
check("Claude check: not installed says how to install and sign in", level == setup_checks.ERROR
      and "not installed" in message and "sign in" in message)

level, message = status_check(setup_checks.check_codex, "codex_path", version="codex-cli 0.50.0")
check("Codex check: installed and signed in with ChatGPT", level == setup_checks.OK
      and "signed in with ChatGPT" in message, message)
level, message = status_check(setup_checks.check_codex, "codex_path", login="Not logged in", login_code=1)
check("Codex check: not signed in says to run codex", level == setup_checks.ERROR
      and "run `codex` to sign in with ChatGPT" in message, message)
level, message = status_check(setup_checks.check_codex, "codex_path",
                              login="Logged in using an API key - sk-proj-***ABCDE")
check("Codex check: an API key is flagged, and the key is never shown", level == setup_checks.WARN
      and "sk-" not in message and "pay per token" in message, message)
level, message = status_check(setup_checks.check_codex, "codex_path", resume_code=2)
check("Codex check: a Codex without exec resume is flagged", level == setup_checks.WARN and "exec resume" in message)
del os.environ["BWM_FAKE_STATUS"]

if os.path.isfile(dev_python):
    saved = workspace.server_python
    workspace.server_python = lambda: dev_python
    level, message = wait(in_thread(setup_checks.check_mcp), 60)
    workspace.server_python = saved
    check("MCP check: the server starts and lists its tools", level == setup_checks.OK and "13 BlendWMe tools" in message,
          message)

socket_bridge.stop()
check("stopping the bridge removes the connection file",
      not os.path.exists(os.path.join(WORKSPACE, "connection.json")))

print(f"ALL {len(passed)} V2 CHECKS PASSED")
sys.stdout.flush()
os._exit(0)
