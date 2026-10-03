"""Fresh-task cancellation and view-only clearing keep the scene intact."""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BLENDWME_LOG_FOLDER"] = tempfile.mkdtemp(prefix="bwm_reset_logs_")
import bpy
import blendwme
from blendwme import bridge, panel, panel_ops, scene_info, workspace
from blendwme.journal import journal
from blendwme.relay import relay
from blendwme.state import session
from blendwme.tool_host import host

passed = 0


def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


workspace.set_folder(tempfile.mkdtemp(prefix="bwm_reset_workspace_"))
blendwme.register()
session.add("you", "Previous task sentinel")
journal.record_task("Previous task sentinel")
journal.set_session("claude", "old-client")
session.set_plan(["old requirement"])
session.set_card({"kind": "teach", "text": "Old lesson"})
session.set_pointers([{"object": "Cube"}])
scene_before = scene_info.snapshot()
journal_before = journal.to_text()
panel_ops.clear_conversation_view()
check("clear view retains the conversation source and exact journal", bool(session.log) and journal.to_text() == journal_before)
check("clear view leaves the current lesson and scene untouched", session.card["text"] == "Old lesson" and scene_info.snapshot() == scene_before)

effects, errors = [], []
def old_job():
    try:
        bridge.call(lambda: effects.append("obsolete mutation"), timeout=5)
    except RuntimeError as error:
        errors.append(str(error))
worker = threading.Thread(target=old_job)
worker.start()
end = time.monotonic() + 3
while not bridge.pending() and time.monotonic() < end:
    time.sleep(0.005)
assert bridge.pending()
old_task = host.task_id
panel_ops.set_text("old draft", undoable=False)
props = bpy.context.window_manager.blendwme
props.prompt = "old native draft"
bpy.ops.blendwme.new_task()
worker.join(3)
bridge._pump()
check("New task cancels queued old work", not effects and bool(errors) and not worker.is_alive())
check("New task creates a fresh task identity", host.task_id != old_task)
check("New task preserves scene objects, geometry and materials", scene_info.snapshot() == scene_before)
check("New task invalidates old cards, pointers and plan", session.card is None and not session.pointers and not session.plan)
check("New task archives history and clears active client context",
      any(e.get("text") == "Previous task sentinel" for e in journal.data["events"]) and
      not journal.data["brief"] and not journal.data["sessions"])
check("New task clears drawn and native drafts", not panel.state.text and not props.prompt)
panel_ops.run_command(bpy.context, "/new")
check("slash new uses the same fresh-task behavior", not relay.task_started and not session.log)
blendwme.unregister()
print(f"ALL {passed} RESET CHECKS PASSED", flush=True)
